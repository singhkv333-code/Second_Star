"""Fundamentals over EVERY listed company, not the stored-price 500.

Charto's chat can already read a company's financials — but only for the 495
symbols whose Moneycontrol payloads were pre-baked into `charto_bars.db` by
`charto/data/sync_financials.py`. That sync exists because Charto is a chart:
its universe is bounded by what it holds bars for, so 495 was never a limit
worth breaking.

Research has the opposite shape. "Which listed companies earn above 20% on
equity with debt under half of equity" is not a question about the 500 names
we happen to store minute bars for — answering it off a 4% slice of the market
and calling it a screen would be a fabrication of a subtler kind than a made-up
number: every figure would be real and the CONCLUSION would be false.

So this module goes to the source the sync copies FROM — the `financials`
Postgres (schema `mc`, 11,256 companies, 18.3M statement lines) — through
Pivot's own `financials_db` / `fundamentals_screen`. Running Pivot's code
rather than re-deriving it is deliberate and is the same choice sync_financials
made: a second implementation of "what does ROE mean in this DB" would quietly
disagree with the stock page about the same company.

What Pivot's modules carry that a fresh reimplementation would take months to
rediscover, all of it audited in their docstrings:

  * line-item SYNONYMS — MC writes one concept under many strings across years
    and across bases; banks file their top line as "Total Interest Earned"
  * basis preference — consolidated, falling back to standalone only when
    consolidated has no row for that field (ROE is standalone-only in this DB)
  * a recency floor — latest-per-company alone surfaces dormant shells whose
    newest filing is from 2009 with absurd ratios
  * P/E from the `enrich` DB rather than 1/earnings_yield, which MC stores
    rounded to 2 dp and which therefore snaps onto a visible grid (25.00,
    16.67, 12.50…)

Cost of the reuse: SQLAlchemy + psycopg2, so the server runs on pivot/.venv.
Measured against Azure Central India: 37 fields + 3 history series for one
company in 1.39s, a company search in 1.64s. That is a tool call, not a
pipeline — the latency this product was built to escape was never the DB.
"""
from __future__ import annotations

import logging
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
PIVOT = HERE.parent / "pivot"

# Import-time state. The Postgres modules are loaded on FIRST USE, not at
# import: a research turn that only reads bars and indicators should not pay
# for an Azure connection, and — more importantly — a financials DB that is
# down must degrade to "fundamentals unavailable" instead of taking the whole
# server's technical half with it on boot.
_lock = threading.Lock()
_mods: dict = {}


def _pivot():
    """(financials_db, fundamentals_screen), imported once, or raise."""
    if "fdb" in _mods:
        return _mods["fdb"], _mods["fs"]
    with _lock:
        if "fdb" not in _mods:
            if str(PIVOT) not in sys.path:
                sys.path.insert(0, str(PIVOT))
            try:
                from dotenv import load_dotenv
                load_dotenv(PIVOT / ".env")
            except ImportError:      # env already exported by the caller
                pass
            from backend.market import financials_db as fdb
            from backend.services import fundamentals_screen as fs
            _mods["fdb"], _mods["fs"] = fdb, fs
            logging.info("pivotted: financials DB wired (%d metrics)",
                         len(fdb.FIELD_MAP))
    return _mods["fdb"], _mods["fs"]


def _unavailable(exc: Exception) -> dict:
    return {"error": f"the filings database is unreachable ({exc})",
            "_note": ("Say the fundamentals lookup failed and answer from "
                      "price/technical tools if the question allows it. Do "
                      "not fill the gap from memory.")}


# ── shaping ─────────────────────────────────────────────────────────────────
#
# The payload the model reads is NOT the payload the stock page reads. The
# page wants every field carrying its own line_item, unit, basis and source so
# it can render provenance in a tooltip; repeated 37 times that is ~1,800
# tokens of boilerplate for ~30 numbers, and the model pays it on every turn.
#
# So the shape here hoists what is shared (basis, the modal period) to the top
# and prints per-field provenance ONLY where it actually differs. A field
# whose latest filing is a year older than the rest is the one case where the
# detail changes the reading — and it is exactly the case a flat "as of Mar 26"
# would hide.

def _fv(v):
    return None if v is None or v.value_numeric is None else float(v.value_numeric)


def _period(v) -> str | None:
    return None if v is None else (v.period_label or
                                   (v.period_end.isoformat() if v.period_end else None))


def _shape(latest: dict, history: dict, company, basis: str,
           pe: float | None) -> dict:
    """{field: number} plus the periods, with nulls NAMED rather than dropped."""
    vals, periods, missing = {}, {}, []
    for k, v in latest.items():
        n = _fv(v)
        if n is None:
            missing.append(k)
            continue
        vals[k] = round(n, 4)
        p = _period(v)
        if p:
            periods.setdefault(p, []).append(k)
    # the modal period is "as of"; anything else is called out by name
    as_of, stale = None, {}
    if periods:
        as_of = max(periods, key=lambda p: len(periods[p]))
        stale = {p: fields for p, fields in periods.items() if p != as_of}
    if pe is not None:
        vals["pe"] = round(pe, 2)
    out = {
        "company": {"name": company.name, "symbol": company.nse_symbol
                    or company.ticker or company.sc_id, "sc_id": company.sc_id},
        "basis": basis,
        "as_of": as_of,
        "values": vals,
    }
    if stale:
        out["other_periods"] = stale
    if missing:
        # Named, not silent. "Moneycontrol does not publish this for this
        # company" is a fact the reply can state; an absent key is one the
        # model will try to fill.
        out["not_published"] = sorted(missing)
    if history:
        out["history"] = {
            k: [{"p": _period(x), "v": round(float(x.value_numeric), 4)}
                for x in series if x.value_numeric is not None]
            for k, series in history.items()}
    if "pe" in vals:
        out["_note"] = ("pe is trailing (live price / TTM EPS) and is the only "
                        "price-derived figure here; every other value is from "
                        "the filing named in as_of.")
    return out


# ── tools ───────────────────────────────────────────────────────────────────

def tool_get_fundamentals(symbol: str = "", fields: list | None = None,
                          history: list | None = None,
                          basis: str = "consolidated",
                          history_years: int = 6) -> dict:
    """Statement metrics for one company, any listed company."""
    if not symbol:
        return {"error": "symbol is required"}
    try:
        fdb, fs = _pivot()
    except Exception as exc:                      # noqa: BLE001
        return _unavailable(exc)
    try:
        sc_id = fdb.resolve_symbol(symbol)
        if sc_id is None:
            return {"error": f"no listed company matches {symbol!r}",
                    "_note": ("Call search_companies to find the right name — "
                              "do not guess a ticker.")}
        company = fdb.get_company(sc_id)
        want = [f for f in (fields or fdb.FIELD_MAP) if f in fdb.FIELD_MAP]
        bad = [f for f in (fields or []) if f not in fdb.FIELD_MAP]
        if not want:
            return {"error": "no known fields requested",
                    "fields": sorted(fdb.FIELD_MAP)}
        hist = [f for f in (history or []) if f in fdb.FIELD_MAP]
        latest, series = fdb.get_company_fundamentals_bulk(
            sc_id, fields=want, history_fields=hist,
            history_limit=max(1, min(int(history_years or 6), 12)), basis=basis)
        # Prefer enrich's real trailing P/E; 1/earnings_yield is quantized by
        # MC's 2-dp rounding and shows up as a grid of repeated values.
        pe = None
        try:
            pe = fs._load_trailing_pe().get(sc_id)
        except Exception:                          # noqa: BLE001
            pass
        if pe is None:
            ey = _fv(latest.get("earnings_yield"))
            pe = (1 / ey) if ey else None
        out = _shape(latest, series, company, basis, pe)
        if bad:
            out["unknown_fields"] = bad
        return out
    except Exception as exc:                       # noqa: BLE001
        logging.exception("pivotted: get_fundamentals failed")
        return _unavailable(exc)


def tool_get_balance_sheet(symbol: str = "", basis: str = "consolidated",
                           years: int = 6) -> dict:
    """The full balance-sheet grid as MC publishes it, section headers intact."""
    if not symbol:
        return {"error": "symbol is required"}
    try:
        fdb, _ = _pivot()
    except Exception as exc:                       # noqa: BLE001
        return _unavailable(exc)
    try:
        got = fdb.get_balance_sheet_statement(
            symbol, basis=basis, years=max(1, min(int(years or 6), 10)))
        if got is None:
            return {"error": f"no listed company matches {symbol!r}"}
        if not got.get("rows"):
            # A resolved company with nothing scraped is a real state, not an
            # error — MC's balance-sheet coverage is thinner than its P&L.
            return {"symbol": symbol, "basis": got.get("basis", basis),
                    "rows": [], "_note": ("No balance sheet is published for "
                                          "this company in the filings "
                                          "database. Say so.")}
        return got
    except Exception as exc:                       # noqa: BLE001
        logging.exception("pivotted: get_balance_sheet failed")
        return _unavailable(exc)


def tool_search_companies(query: str = "", limit: int = 10) -> dict:
    """Name/ticker → the companies that match, across all 11,256."""
    if not query:
        return {"error": "query is required"}
    try:
        fdb, _ = _pivot()
    except Exception as exc:                       # noqa: BLE001
        return _unavailable(exc)
    try:
        hits = fdb.search_companies(query, limit=max(1, min(int(limit or 10), 25)))
        return {"query": query, "count": len(hits),
                "results": [{"symbol": h.symbol, "name": h.name,
                             "sc_id": h.sc_id,
                             "has_fundamentals": bool(h.has_fundamentals)}
                            for h in hits],
                **({} if hits else {"_note": (
                    "Nothing matched. Say so rather than picking a "
                    "similar-sounding company.")})}
    except Exception as exc:                       # noqa: BLE001
        logging.exception("pivotted: search_companies failed")
        return _unavailable(exc)


def tool_screen_fundamentals(filters: list | None = None, sector: str = "",
                             sort_by: dict | None = None, limit: int = 15,
                             market_cap_tier: str = "",
                             growth_years: int = 0,
                             exclude: list | None = None,
                             enrich_fields: list | None = None) -> dict:
    """Cross-sectional screen over every company with recent filings.

    `enrich_fields` exists to kill a round. A screen returns only the metrics
    it filtered or sorted on, so "screen on ROE, then tell me their margins"
    was always two LLM hops: screen, read the names back, fetch each one. The
    tool call itself is ~1.3s; the hop around it is ~6s. Fetching the extra
    fields for the returned rows HERE — concurrently, inside the same call —
    collapses the most common two-round pattern in this product into one.
    """
    if not filters:
        return {"error": "filters is required",
                "_note": ("Give at least one {field, op, value}. Use "
                          "screen_universe instead for a purely technical "
                          "screen (price, RSI, moving averages).")}
    try:
        _, fs = _pivot()
    except Exception as exc:                       # noqa: BLE001
        return _unavailable(exc)
    try:
        got = fs.screen_by_fundamentals(
            filters, sector=sector or None, sort_by=sort_by or None,
            limit=max(1, min(int(limit or 15), 50)),
            market_cap_tier=market_cap_tier or None,
            growth_years=growth_years or None,
            exclude=exclude or None)
        # screen_by_fundamentals already discloses dropped filters in `note`;
        # surfacing the count keeps a zero-row screen from reading as an error.
        got.setdefault("_note", (
            "Every row is a real filing. A filter the DB cannot serve is "
            "dropped and disclosed in `note` — read it before answering."))
        rows = got.get("results") or []
        want = [f for f in (enrich_fields or []) if f]
        if want and rows:
            syms = [r.get("symbol") for r in rows[:12] if r.get("symbol")]
            with ThreadPoolExecutor(max_workers=min(len(syms), 8)) as pool:
                extra = dict(zip(syms, pool.map(
                    lambda s: tool_get_fundamentals(s, fields=want), syms)))
            for row in rows:
                got_one = extra.get(row.get("symbol")) or {}
                vals = got_one.get("values") or {}
                # Only fields the screen did not already carry, so an enriched
                # column never silently overwrites the one that was filtered on
                # (they can differ: the screen applies its own recency floor).
                for k, v in vals.items():
                    row.setdefault(k, v)
                if got_one.get("as_of"):
                    row.setdefault("as_of", got_one["as_of"])
            got["enriched_with"] = want
        return got
    except Exception as exc:                       # noqa: BLE001
        logging.exception("pivotted: screen_fundamentals failed")
        return _unavailable(exc)


def tool_compare_fundamentals(symbols: list | None = None,
                              fields: list | None = None,
                              basis: str = "consolidated",
                              history: list | None = None,
                              history_years: int = 6) -> dict:
    """The same fields across several companies — fetched concurrently.

    Sequentially this is N × ~1.4s of Azure round-trip, which is the whole
    latency budget for a four-company comparison. Each company is an
    independent query against a read-only DB, so they go together.

    `history` is here because leaving it out cost a whole round. Without it,
    "how has A's ROCE trended, and how does B compare" could only be answered
    by comparing the LEVELS in one round and then re-fetching both companies
    with history in the next — the model reaching for get_fundamentals twice
    because the comparison tool could not carry a series. Observed doing
    exactly that before this argument existed.
    """
    syms = [str(s).strip() for s in (symbols or []) if str(s).strip()]
    if len(syms) < 2:
        return {"error": "give at least two symbols"}
    syms = syms[:8]
    want = fields or ["revenue", "net_profit", "roe", "roce",
                      "debt_to_equity", "net_profit_margin", "price_to_book"]
    hist = [f for f in (history or []) if f]
    with ThreadPoolExecutor(max_workers=len(syms)) as pool:
        got = list(pool.map(
            lambda s: (s, tool_get_fundamentals(
                s, fields=want, basis=basis, history=hist,
                history_years=history_years)),
            syms))
    rows, failed = {}, {}
    for sym, res in got:
        if "error" in res:
            failed[sym] = res["error"]
            continue
        rows[res["company"]["symbol"]] = {
            "name": res["company"]["name"], "as_of": res.get("as_of"),
            "basis": res.get("basis"), **res["values"],
            **({"history": res["history"]} if res.get("history") else {})}
    out = {"fields": want, "companies": rows}
    if failed:
        # A comparison missing a leg is a different answer, not a smaller one.
        out["not_found"] = failed
        out["_note"] = ("Name the companies that could not be read; a "
                        "comparison silently missing one is misleading.")
    return out
