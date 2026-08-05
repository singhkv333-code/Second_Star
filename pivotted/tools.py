"""The tool table: Charto's read-only half, plus fundamentals, run in parallel.

Two jobs.

1. TRIM. Charto ships 25 tools written for a chat that sits beside a live
   chart the user can draw on. Seven of them exist only to put ink on that
   chart or to score ink already on it, and Pivotted has no chart:

     open_chart        opens a pane                          — no panes here
     get_anchors       mints ids for draw_shape to compose   — feeds ink only
     draw_shape        the ink itself
     evaluate_line     scores a line the USER DREW (drawing_id)
     evaluate_fib      scores a fib the USER DREW
     evaluate_drawing  scores a box/band/channel the USER DREW
     plan_position     entry/stop/target overlay + position sizing

   plan_position is the one that is dropped on principle rather than on
   plumbing: it is trade construction, and the split this product is built
   around is that research stops before the trade. The three evaluate_*
   drawing tools all take a `drawing_id` resolved out of the chart context
   envelope, so with no chart there is nothing for them to be about.

   The survivors are then stripped of their INK ARGUMENTS — `draw`,
   `mark_points`, `mark_levels`, `connect`, `remove`, `clear_marks` — which
   eight of them carry. Leaving those in the schema would invite a call that
   silently succeeds at nothing.

   Measured: 25 tools / ~10,282 tokens → 18 / ~7,064, a 31% cut of the
   dominant per-turn input cost. (The system prompt, by comparison, is 208.)

2. PARALLELISE. Charto executes a round's tool calls in a `for` loop, which
   was free when every call was ~10ms of local SQLite. Half of Pivotted's
   tools now cross the Pacific to Azure Postgres at ~1.4s each, so a turn
   that reads two companies' financials pays 2.8s sequentially and 1.4s
   together. The model already emits several calls per round; this just stops
   throwing that away.

   The catch is that Charto's request state — `_req.symbol`, and the scene
   buffers its detectors write through — lives in `threading.local()`. A
   worker thread inherits none of it, so `run_tool` would read the default
   symbol and quietly answer for RELIANCE under another company's name. Every
   worker therefore re-establishes that state before it dispatches; see
   `_call`.
"""
from __future__ import annotations

import copy
import json
import logging
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import fundamentals as fnd

HERE = Path(__file__).resolve().parent
CHARTO = HERE.parent / "charto" / "data"
if str(CHARTO) not in sys.path:
    sys.path.insert(0, str(CHARTO))

import dataserver as ds  # noqa: E402  — path must be set first


# Ink or trade construction. Neither survives the move off the chart.
DROPPED = frozenset({
    "open_chart", "get_anchors", "draw_shape",
    "evaluate_line", "evaluate_fib", "evaluate_drawing",
    "plan_position",
})

# Symbols the technical tools can reach. Every price tool is bounded by this;
# the fundamentals tools are not.
#
# Which list to ask is not obvious and getting it wrong under-serves the user.
# There are three, and they disagree:
#
#   sync_state       99  — only what this box has ALREADY hydrated locally
#   bars_1d         557  — daily folds, including symbols with no minute bars
#   symbols.json    500  — the universe the store is entitled to serve
#
# The right answer is symbols.json, because a symbol in it but absent locally
# is not unavailable — `ds._ensure_symbol` pulls it from blob storage on
# demand (~5.7s, once, then permanent). Gating on sync_state would refuse 400
# symbols the archive genuinely holds. The union with bars_1d picks up the
# macro and crypto series that were added to the store without going through
# symbols.json.
_universe_lock = threading.Lock()
_universe: dict = {}


def stored_symbols() -> set:
    with _universe_lock:
        if "set" not in _universe:
            try:
                known = set(ds._known_symbols())
                daily = {r[0] for r in ds._con.execute(
                    "SELECT symbol FROM bars_1d GROUP BY symbol")}
                _universe["set"] = known | daily
            except Exception:                       # noqa: BLE001
                _universe["set"] = set()
    return _universe["set"]


def _charto_tools() -> list[dict]:
    """Charto's table, minus the ink tools, minus the ink arguments."""
    out = []
    for spec in ds.TOOLS:
        if spec.get("name") in DROPPED:
            continue
        spec = copy.deepcopy(spec)
        props = spec["parameters"]["properties"]
        for arg in ds._INK_ARGS:
            props.pop(arg, None)
        spec["parameters"]["required"] = [
            r for r in spec["parameters"].get("required", [])
            if r not in ds._INK_ARGS]
        out.append(spec)
    return out


# ── the fundamentals half ───────────────────────────────────────────────────
#
# Descriptions carry the behaviour, because nothing else does — there is no
# router and the system prompt is 208 tokens. Two boundaries are stated here
# rather than left to be discovered: the filings are ANNUAL ONLY (all 18.3M
# rows are period_kind='annual' — there is no quarterly statement data at all),
# and the fundamentals universe is a superset of the price universe.

_FUNDAMENTAL_TOOLS = [
    {"type": "function", "name": "get_fundamentals",
     "description": (
         "Financial-statement metrics for ONE company — any listed company, "
         "not just the ones with stored bars. 37 fields: the ratio set (roe, "
         "roce, roa, roic, debt_to_equity, current_ratio, quick_ratio, "
         "interest_coverage, net_profit_margin, ebitda_margin, "
         "operating_margin, gross_margin, asset_turnover, inventory_turnover, "
         "receivables_turnover, price_to_book, ev_to_ebitda, earnings_yield, "
         "dividend_payout), the raw lines (revenue, net_profit, "
         "operating_profit, eps_basic, eps_diluted, interest_expense, "
         "total_debt, total_equity, reserves, cash_from_ops, "
         "book_value_per_share, enterprise_value_cr, sales_per_share, "
         "net_profit_per_share) and the bank-only set (gross_npa_pct, "
         "net_npa_pct, net_interest_margin, casa_pct). Omit `fields` for all "
         "of them — one call is the same cost as five. Pass `history` with "
         "the fields whose TREND matters; a level alone rarely answers a "
         "research question. FILINGS ARE ANNUAL: there is no quarterly "
         "statement data, so a question about last quarter cannot be answered "
         "from here. Fields the company does not publish come back named "
         "under not_published — say they are unavailable, never estimate "
         "them."),
     "parameters": {"type": "object", "properties": {
         "symbol": {"type": "string",
                    "description": "NSE symbol or company name."},
         "fields": {"type": "array", "items": {"type": "string"},
                    "description": "Omit for all 37."},
         "history": {"type": "array", "items": {"type": "string"},
                     "description": ("Fields to also return as a multi-year "
                                     "series, newest first.")},
         "history_years": {"type": "integer",
                           "description": "Default 6, max 12."},
         "basis": {"type": "string", "enum": ["consolidated", "standalone"],
                   "description": ("Default consolidated, falling back to "
                                   "standalone where consolidated has no "
                                   "row — the reply says which was served.")},
     }, "required": ["symbol"]}},

    {"type": "function", "name": "get_balance_sheet",
     "description": (
         "The full balance-sheet grid for one company, every line item as "
         "filed, with section headers and a multi-year column. Use when the "
         "question is about balance-sheet STRUCTURE — what the debt is made "
         "of, where the assets sit, how reserves moved — rather than about a "
         "ratio get_fundamentals already computes. Balance-sheet coverage is "
         "thinner than P&L coverage; an empty grid for a real company is a "
         "real answer."),
     "parameters": {"type": "object", "properties": {
         "symbol": {"type": "string"},
         "basis": {"type": "string", "enum": ["consolidated", "standalone"]},
         "years": {"type": "integer", "description": "Default 6, max 10."},
     }, "required": ["symbol"]}},

    {"type": "function", "name": "search_companies",
     "description": (
         "Resolve a name or partial ticker to listed companies. Use whenever "
         "the user names a company you cannot map to a symbol with certainty, "
         "and BEFORE answering for a guessed ticker — the universe is 11,256 "
         "companies and many share a first word. `has_fundamentals` says "
         "whether filings exist for that row."),
     "parameters": {"type": "object", "properties": {
         "query": {"type": "string"},
         "limit": {"type": "integer", "description": "Default 10, max 25."},
     }, "required": ["query"]}},

    {"type": "function", "name": "screen_fundamentals",
     "description": (
         "Rank EVERY listed company with recent filings against fundamental "
         "constraints — the counterpart to screen_universe, which screens the "
         "~550 stored symbols on price and technicals. Use this one whenever "
         "the constraint is financial (returns on capital, leverage, margins, "
         "growth, valuation) and screen_universe when it is about price "
         "behaviour. Filters are {field, op, value} with op in < <= > >= =; "
         "fields are the ratio set, the growth set (revenue_growth, "
         "net_profit_growth, eps_growth), the raw line items, or market_cap "
         "in rupees crore. A filter this DB cannot serve is DROPPED and "
         "disclosed in `note` — read it, because a screen that quietly lost a "
         "constraint answers a different question than the one asked."),
     "parameters": {"type": "object", "properties": {
         "filters": {"type": "array", "items": {"type": "object"},
                     "description": "[{field, op, value}], at least one."},
         "sector": {"type": "string",
                    "description": ("Coarse sector: pharma, bank, it, energy, "
                                    "auto, metal, finance, fmcg, cement, "
                                    "realty, chemical, textile, media…")},
         "sort_by": {"type": "object",
                     "description": "{field, dir: asc|desc}."},
         "market_cap_tier": {"type": "string",
                             "enum": ["large", "mid", "small", "micro"]},
         "growth_years": {"type": "integer",
                          "description": ("Years over which a *_growth field "
                                          "is measured; default 3.")},
         "exclude": {"type": "array", "items": {"type": "string"},
                     "description": "Names, sector words, or 'PSU'."},
         "limit": {"type": "integer", "description": "Default 15, max 50."},
     }, "required": ["filters"]}},

    {"type": "function", "name": "compare_fundamentals",
     "description": (
         "The same financial fields across 2-8 companies at once, fetched "
         "concurrently. Use for any 'X vs Y' or peer-set question instead of "
         "calling get_fundamentals repeatedly. Companies that cannot be "
         "resolved are returned under not_found rather than dropped."),
     "parameters": {"type": "object", "properties": {
         "symbols": {"type": "array", "items": {"type": "string"}},
         "fields": {"type": "array", "items": {"type": "string"},
                    "description": ("Omit for a sensible default set "
                                    "(revenue, net_profit, roe, roce, "
                                    "debt_to_equity, margin, price_to_book).")},
         "basis": {"type": "string", "enum": ["consolidated", "standalone"]},
     }, "required": ["symbols"]}},

    {"type": "function", "name": "search_web",
     "description": (
         "One isolated web lookup for things no local tool holds: management "
         "commentary, regulatory or legal developments, deal news, sector or "
         "macro context, anything after the last filing. Returns dated lines "
         "with source domains. Use search_news instead when the question is "
         "about a specific price move on a specific stored symbol — that one "
         "is windowed to the move. Never let a headline override a tool's "
         "number; the web supplies causes and dates, tools supply "
         "quantities."),
     "parameters": {"type": "object", "properties": {
         "query": {"type": "string",
                   "description": "What to find out, in a sentence."},
         "recent_only": {"type": "boolean",
                         "description": ("True to bias to the last few weeks. "
                                         "Default false.")},
     }, "required": ["query"]}},
]


def tool_search_web(query: str = "", recent_only: bool = False) -> dict:
    """A generic browse on Charto's clerk transport.

    Charto's own web access is `search_news`, whose three prompts are welded
    to a symbol and a date window because it exists to explain a move. This
    keeps the isolation that matters — the hosted web-search tool costs ~4.3k
    tokens merely by being ATTACHED to a request, so it is never a schema on
    the main loop, only a separate sub-call — and drops the framing.
    """
    if not query:
        return {"error": "query is required"}
    key = f"web:{'r' if recent_only else 'a'}:{query.strip().lower()[:200]}"
    hit = ds._news_cache_get(key, 900 if recent_only else 86400)
    if hit:
        return {**hit, "cached": True}
    window = ("Prefer sources from the last few weeks."
              if recent_only else "Any date, but every line must carry one.")
    prompt = (
        "You are a research clerk for an Indian equities analyst. Search the "
        "web once — twice only if the first search returns nothing — and "
        f"answer this: {query}\n{window} Reply with 2-6 lines, each "
        "'date · what happened or what is true · source domain'. Do NOT "
        "report prices, returns, percentages or price targets — the caller "
        "holds exact figures and yours would be stale. If you find nothing "
        "usable, reply exactly: nothing found.")
    got = ds._news_browse(prompt)
    if isinstance(got, dict):
        return {"error": got["error"],
                "_note": "Say the web lookup failed; do not answer from memory."}
    body, sources, searched = got
    out = {"query": query, "findings": body or "nothing found",
           "sources": sources}
    if searched and body:
        ds._news_cache_put(key, recent_only, out)
    return out


_EXTRA_DISPATCH = {
    "get_fundamentals": fnd.tool_get_fundamentals,
    "get_balance_sheet": fnd.tool_get_balance_sheet,
    "search_companies": fnd.tool_search_companies,
    "screen_fundamentals": fnd.tool_screen_fundamentals,
    "compare_fundamentals": fnd.tool_compare_fundamentals,
    "search_web": tool_search_web,
}

TOOLS = _charto_tools() + _FUNDAMENTAL_TOOLS
_CHARTO_NAMES = {t["name"] for t in TOOLS} - set(_EXTRA_DISPATCH)


def _call(name: str, args: dict, symbol: str) -> dict:
    """Dispatch one call. Runs in a worker thread — establish state first.

    `ds._req` and the scene buffers are `threading.local()`. In a fresh
    thread `_req.symbol` is unset, and `run_tool` falls back to a hardcoded
    RELIANCE — which is the single worst failure this product can have, since
    every number would be real and would belong to the wrong company. So the
    working symbol is set explicitly here, per call, from the call's own
    argument.
    """
    if name in _EXTRA_DISPATCH:
        try:
            return _EXTRA_DISPATCH[name](**(args or {}))
        except TypeError as exc:
            return {"error": f"bad arguments for {name}: {exc}"}
        except Exception as exc:                    # noqa: BLE001
            logging.exception("pivotted: %s failed", name)
            return {"error": f"{name} failed: {exc}"}
    if name not in _CHARTO_NAMES:
        return {"error": f"unknown tool {name}"}

    args = dict(args or {})
    want = str(args.get("symbol") or symbol or "").upper().strip()
    if not want:
        return {"error": f"{name} needs a symbol",
                "_note": ("There is no chart in focus here — every "
                          "price/technical tool takes the symbol "
                          "explicitly.")}
    if want not in stored_symbols():
        # The honest boundary, stated where the model can act on it. A
        # company can be perfectly real, and screenable on fundamentals, and
        # still have no bars in this archive.
        return {"error": f"no stored price history for {want}",
                "_note": ("The bar archive is ~560 symbols; the filings "
                          "database is every listed company. Fundamentals "
                          "tools will still work for this company — say the "
                          "price-based part is unavailable rather than "
                          "substituting an index or a peer.")}
    # In the universe but not yet on this box: pull it. Charto does this once
    # per turn before the loop starts because a tool aimed mid-round cannot
    # wait ~6s; here it happens inside the worker, which is fine because the
    # workers are already concurrent and `_ensure_symbol` holds a per-symbol
    # lock, so two calls for the same cold symbol hydrate it once.
    if not ds._symbol_ready(want):
        err = ds._ensure_symbol(want)
        if err:
            return {**err, "_note": ("This symbol is in the universe but its "
                                     "bars could not be loaded. Say the "
                                     "price data is unavailable right now.")}
    ds._req.symbol = want
    ds._req.drawable = False          # no chart: refuses anything ink-shaped
    ds._req.charts = [want]
    ds._scene_reset()                 # initialise the threadlocal buffers
    ds._drawings_set(None)
    args["symbol"] = want
    return ds.run_tool(name, args)


def run_round(calls: list[dict], symbol: str = "") -> list[dict]:
    """Execute one round's calls together. Order of results matches `calls`.

    Bounded at 8 workers: the model rarely emits more, and the Azure
    financials DB is a shared read pool that a wide fan-out would be rude to.
    """
    if not calls:
        return []
    if len(calls) == 1:
        c = calls[0]
        return [_call(c["name"], c["args"], symbol)]
    with ThreadPoolExecutor(max_workers=min(len(calls), 8)) as pool:
        return list(pool.map(
            lambda c: _call(c["name"], c["args"], symbol), calls))


def parse_args(raw) -> dict:
    if isinstance(raw, dict):
        return raw
    try:
        return json.loads(raw or "{}")
    except (json.JSONDecodeError, TypeError):
        return {}
