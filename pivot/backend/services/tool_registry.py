"""Single source of truth for which tools the LLM can see and call.

Replaces the old subset-based routing in ``agents/tools.py``. The LLM is shown
*every* tool on every turn and decides what to call (or not call). This is the
modern pattern; the classifier+subset approach was the second-largest source
of failures in the eval (wrong subset → right tool not in the prompt).

Tools that are stubs (return ``"Created"`` / placeholder text) are deliberately
excluded from the schema. They will be added back when their implementation
is real.
"""
from __future__ import annotations

import json
import logging
import math
import re
from collections import Counter
from dataclasses import dataclass, field
from functools import lru_cache

from backend.agents.tool_executor import execute_tool as _legacy_execute_tool
from backend.agents.tools import get_tool_defaults


logger = logging.getLogger(__name__)


# ---- Tool catalogue ----------------------------------------------------
#
# Each entry has the OpenAI function-calling shape (we reuse the existing
# tools.py definitions; this file simply curates the visible set).
#
# The visible set is DERIVED, not hand-maintained (chat-kernel Phase 0,
# 2026-07-10): a tool is visible iff it has a real handler — legacy
# handlers minus `_generic_confirm` stubs, plus the v2 handler table —
# and is not explicitly hidden below. The old hand-list drifted from the
# dispatcher twice; this derivation cannot.
#
# Doctrine notes that used to live on the hand-list (still true):
#   - Option strategy REGISTRATION happens on the card's POST
#     /option-strategies — there is deliberately NO place-options-order
#     chat tool.
#   - `run_backtest` (legacy single-indicator) stays out of chat: its
#     `trigger_condition` field trapped the LLM in clarification loops.
#     backtest_workflow / backtest_dsl_tree carry chat backtests.
#   - The propose_* macros exist for decode speed (~30x) — never fold
#     them back into propose_workflow.

# Handlers that exist but must NOT be shown to the model.
from backend.agents.consolidated_handlers import SUPERSEDED_BY_CONSOLIDATION

_HIDDEN_TOOLS: frozenset = frozenset({
    # Deterministic helper only meant to be invoked from inside a
    # compose_multistep plan (server resolves $step_id refs first).
    # Exposed directly, the LLM called it standalone with empty `from`,
    # which failed silently.
    "extract_winner_symbol",
    # Handler exists but returns placeholder data — stub by behaviour.
    "get_upcoming_events",
    # 2026-07-19 product decisions — handlers stay (cards/REST/legacy
    # flows), the LLM never sees them:
    # (a) SCRIPTED clarify questionnaires retired — under-specified
    #     builds go through the model's own judgment (build with stated
    #     assumptions or ONE model-authored ASK_USER question).
    "ask_user_dynamic",
    "ask_agent_clarify",
    # (b) IPO surface is info/analysis-only — applications and reminder
    #     workflows happen in the user's broker app.
    "propose_ipo_application",
    "propose_ipo_automation",
}) | SUPERSEDED_BY_CONSOLIDATION
# ^ Chat-kernel Phase 1: the 23 narrow tools replaced by the 5
# consolidated view-enum tools stay callable (cards, REST, macros,
# compose_multistep) but are no longer shown to the LLM — overlapping
# sibling tools were the probe's #1 misroute cause.


def _real_tools() -> set[str]:
    """The derived LLM-visible tool set. Requires v2 registration."""
    from backend.agents.tool_executor import HANDLERS, STUB_TOOLS
    _ensure_v2_tools_registered()
    return ((set(HANDLERS) - STUB_TOOLS) | set(_V2_HANDLERS)) - _HIDDEN_TOOLS


# Pre-consolidation hand-maintained list (frozen 2026-07-10), kept ONLY
# to compute the tripwire snapshot below. Do not add new tools here —
# implement a real handler and they become visible.
_PRE_CONSOLIDATION_SNAPSHOT: set[str] = {
    # Trade execution
    "place_market_order", "place_limit_order",
    "create_gtt_order", "create_sl_order", "create_oco_order", "create_dip_buy",
    "place_basket_order",
    "cancel_order", "cancel_gtt", "list_pending_orders", "list_gtt_orders",
    "squareoff_all_intraday", "squareoff_symbol",
    # SIP
    "create_sip", "list_sips", "pause_sip", "resume_sip", "delete_sip",
    "pause_all_sips",
    # Strategies
    "create_strategy", "list_strategies", "pause_strategy", "resume_strategy",
    "delete_strategy",
    # Portfolio
    "get_portfolio_summary", "get_holdings", "get_sector_breakdown",
    "get_holding_detail", "get_tax_summary", "get_active_products",
    # Market data
    "get_live_price", "get_index_level", "get_ohlc", "get_market_status",
    "get_52wk_range", "get_price_history", "get_top_movers",
    # F&O P1: chain + strategy suggest/build/critique + portfolio greeks.
    # Registration happens on the card's POST /option-strategies — there
    # is deliberately NO place-options-order chat tool.
    "get_option_chain", "suggest_option_strategy", "build_option_strategy",
    "critique_option_strategy", "get_portfolio_greeks",
    # Track C #3: roll/adjust an existing option leg — close + reopen on
    # a later expiry / different strike, priced off the live chain.
    "roll_option_position",
    # Track C #1: chat-side workflow arming + grounded status readback.
    # register_workflow drives the same persist+activate path as the
    # card's Save & activate; get_workflow_status reads the REAL
    # scheduler facts (60s watcher cadence, next_run_at, current
    # indicator value). Register-not-execute throughout.
    "register_workflow", "get_workflow_status",
    # Retail capability tools (2026-05-29): fundamental screen,
    # single-stock fundamentals, company news, IPO feed. These MUST be
    # here (not just in agents/tools.py ALL_TOOLS) — get_tool_schema()
    # gates the per-hop routed surface on this set, so omitting them
    # meant the router selected them but they never reached the model
    # (only find_tool's lazy-load could surface them, wasting a hop).
    "screen_fundamentals", "fetch_fundamentals", "get_symbol_news",
    # Chat-kernel 2026-07-10: historical fundamentals (12y annual) with
    # series/max/min/cagr/yoy aggregation — the "which year did X have
    # max profit" class. Registered via _ensure_v2_tools_registered.
    "query_financials",
    # IPO surface is INFO/ANALYSIS-ONLY (2026-07-19 product decision):
    # propose_ipo_application + propose_ipo_automation removed from the
    # surface — applications happen in the user's broker app; Pivot
    # lists, details, and analyses IPOs only.
    "list_upcoming_ipos", "get_ipo_details",
    # IPO P4: post-listing performance ("how did TIKONA list?" /
    # "TIKONA listing gain"). Reads NSE past-issues + live price; the
    # FE renders the ipo_listed_card. Honest-on-failure (null + note),
    # NEVER fabricates the current price or gain.
    "get_ipo_listing",
    # /core/ analytics bridge — indicators / risk / comparison
    "get_indicator", "get_multiple_indicators", "get_performance_metrics",
    "compare_performance", "get_correlation_matrix", "get_returns",
    # Yields
    "compare_yields", "get_yield_recommendation",
    # Calculations
    "calculate_order_qty", "calculate_tax_impact", "calculate_sl_price",
    "calculate_dip_price", "calculate_margin",
    # Backtest. `run_backtest` is the legacy single-indicator tool —
    # excluded from the chat tool registry entirely because its
    # required `trigger_condition` field is too abstract for free-form
    # prompts and the LLM gets stuck in "Got it — what's the trigger
    # condition?" clarification loops. backtest_workflow uses the
    # same steps[] schema as propose_workflow, which the LLM handles
    # natively. The legacy tool is still importable from Python for
    # the REST `/api/backtest/run` endpoint and test scripts.
    "backtest_workflow",
    # DSL-tree backtester (Phase B+1+C.0). Preferred over
    # backtest_workflow for compound / cross-symbol / aggregator-based
    # conditions; LLM hands over the user's NL condition as one
    # string and the tool builds the DSL tree internally.
    "backtest_dsl_tree",
    # Pairs / stat-arb backtester (Phase 2.3): cointegration + spread strategy
    # for a named pair, and a pairwise cointegration scanner over a list.
    "backtest_pairs",
    "scan_pairs",
    "test_cointegration",
    # Multi-position portfolio backtester (Phase 2.4): constrained
    # cross-sectional momentum portfolio (max names / gross / sector caps, L/S).
    "backtest_portfolio",
    # Scheduler
    "get_scheduler_status", "list_upcoming_jobs",
    # New v2 tools
    "get_product_spec",
    # Agent System (Workflows v1)
    "propose_workflow",
    # DSL-tree workflow proposal — same role as propose_workflow but
    # the entry condition is a single DSL ``trigger.compound`` tree
    # rather than a flat ``steps[]`` list. Use when the entry has
    # multiple AND-ed / OR-ed conditions, cross-symbol filters, or
    # aggregators (highest-of-N, percentrank, barssince, ...).
    "propose_dsl_workflow",
    # Macro tools — narrow alternatives to propose_workflow that
    # hydrate the most common shapes server-side. ~30× faster decode.
    "propose_scheduled_order",
    "propose_threshold_order",
    "propose_basket_allocation",
    "propose_holding_action",
    # Strategy builder (Workstream A). build_strategy runs the DB-driven
    # equity+gold construction pipeline and emits a strategy_builder_card.
    # 2026-07-19: ask_user_dynamic + ask_agent_clarify REMOVED from the
    # surface — their SCRIPTED question sets (VOI templates with canned
    # example names, deterministic agent clarify) are gone by product
    # decision. Under-specified builds now go through the model's own
    # judgment: build with stated assumptions (the card lists them) or ask
    # ONE model-authored question via the generic ASK_USER card.
    "build_strategy",
    # L14: orchestrator + analytics helpers for multi-step compound intents.
    # `compose_multistep` resolves $step.field refs server-side between
    # sub-calls; `compare_backtests` runs 2-4 strategies in parallel.
    # `extract_winner_symbol` is INTENTIONALLY NOT in this set — it is
    # a deterministic helper only meant to be invoked from inside a
    # compose_multistep plan (where the server resolves $step_id refs
    # before dispatching). Exposing it directly led to the LLM calling
    # it standalone with empty `from`, which failed silently.
    "compose_multistep",
    "compare_backtests",
    # L14 T2: entity-grounding web lookup (DDG IA → Wikipedia fallback).
    # Use for "what is X" / "explain X" entity definitions where the
    # local fundamentals DB doesn't carry the info. Not for real-time
    # news (we don't have that feed wired).
    "web_search_brief",
    # L14 T4: pre/post-pivot regime comparison. Splits price history at
    # a date and computes risk + return metrics for each window.
    "regime_compare_metrics",
    # Meta tools — escape hatches for cases the regex router misses.
    "find_tool",
}


# The tripwire snapshot the derivation is checked against
# (tests/test_tool_registry_derivation.py). Every deliberate visibility
# change is an explicit term here — reviewable in the diff — while
# accidental drift still trips the test.
_REAL_TOOLS_LEGACY_SNAPSHOT: set[str] = (
    (
        _PRE_CONSOLIDATION_SNAPSHOT  # (already includes query_financials)
        # Chat-kernel Phase 1 + round 2: consolidated view-enum tools...
        | {"get_market_data", "get_portfolio", "manage_automation",
           "get_indicators", "place_order", "calculate", "get_ipo"}
        # Reasoning-lane (2026-07-12): `compute` is the free-form
        # deterministic sandbox for in-context maths (percentile ranks,
        # P&L what-ifs…). Distinct from `calculate`, which is the 5-way
        # DOMAIN-formula dispatcher (order_qty/tax/SL/dip/margin).
        | {"compute"}
    )
    # ...replacing the 23 narrow tools they supersede. (Parenthesised:
    # `-` binds tighter than `|`, so without the parens the subtraction
    # only applied to the 5-name union term.)
    - SUPERSEDED_BY_CONSOLIDATION
)


# Tools intentionally excluded because their implementation is a stub:
#   modify_order, place_futures_order, place_options_order,
#   place_multileg_options, roll_futures_position, get_margin_required,
#   create_cash_sweep, create_rebalancing_rule, create_drawdown_protection,
#   get_upcoming_events
# (get_option_chain went REAL in F&O P1 along with the suggest/build/
# critique/portfolio-greeks surface; get_option_greeks was folded into
# the chain card and its schema removed.)
# When their handlers stop returning placeholder text they get added here.


@dataclass
class ToolResult:
    name: str
    args: dict
    success: bool
    data: dict
    error: str | None = None
    logiccard: dict | None = None
    # Structured route hint (chat-kernel 2026-07-10): set when the tool
    # raised ToolRedirect — the chat loop prefers this over regex-scanning
    # the error prose for "use <tool>".
    redirect_to: str | None = None

    def to_llm_string(self) -> str:
        """Compact JSON string the model sees as the tool result."""
        if not self.success:
            return json.dumps({"error": self.error or "tool failed"})
        return json.dumps(self.data, default=str)[:6000]


def get_tool_schema() -> list[dict]:
    """The full tool list shown to the LLM on every turn."""
    from backend.agents.tools import ALL_TOOLS
    real = _real_tools()  # registers v2 tools as a side effect
    return [defn for name, defn in ALL_TOOLS.items() if name in real]


async def execute(name: str, args: dict, *, kite_token: str, db, user_id: int) -> ToolResult:
    """Dispatch a tool call to its handler. Wraps the legacy executor + new v2 tools."""
    _ensure_v2_tools_registered()

    # Merge declarative defaults so v2 handlers also get optional fields
    # auto-filled (exchange, etc.). User-supplied values win.
    merged = {**get_tool_defaults(name), **(args or {})}

    # Deterministic repair pass: catch numeric strings ("ten", "10 shares"),
    # non-push channels ("email" → "push"), and other minor LLM mistakes
    # before Pydantic validation. Saves an LLM hop per repaired failure.
    # Notes are logged inside repair_tool_args; we don't persist them on
    # `merged` because tools with strict Pydantic schemas would reject
    # the extra key.
    from backend.services.arg_repair import repair_tool_args
    merged, _repair_notes = repair_tool_args(name, merged)

    if name in _V2_HANDLERS:
        try:
            data = await _V2_HANDLERS[name](merged)
        except Exception as e:
            from backend.services.tool_errors import ToolRedirect
            if isinstance(e, ToolRedirect):
                # Typed route hint — the chat loop reads redirect_to
                # directly instead of regex-scanning the prose (which a
                # truncation once severed). Prose still goes to the LLM.
                return ToolResult(
                    name=name, args=merged, success=False, data={},
                    error=str(e)[:600], redirect_to=e.redirect_to,
                )
            logger.exception("v2 tool %s failed: %s", name, e)
            # Cap is generous (600, not 200) because several tools append a
            # ROUTE HINT at the END of long explanatory errors ("...Use
            # propose_workflow with one branch per time-anchored leg"). The
            # chat loop's route-redirect regex scans this string for
            # `use <tool>`; a 200-char cap severed the hint on the longer
            # DSL refusals, so the redirect never fired and the turn fell
            # through to a fake-success clarifier with no card. The error is
            # internal only (LLM tool-result + redirect regex + trace), never
            # user-facing, so 600 is safe.
            return ToolResult(name=name, args=merged, success=False, data={}, error=str(e)[:600])
        return ToolResult(name=name, args=merged, success=True, data=data)

    if name not in _real_tools():
        return ToolResult(
            name=name, args=args, success=False, data={},
            error=f"tool '{name}' is not currently available",
        )

    # Legacy executor performs its own merge; pass the original args so
    # there's a single point-of-truth for the merged payload.
    raw = await _legacy_execute_tool(name, args, kite_token, db, user_id)
    return ToolResult(
        name=name, args=args,
        success=bool(raw.get("success")),
        data=raw.get("data") or {},
        error=raw.get("error"),
        logiccard=raw.get("logiccard"),
    )


# ---- find_tool: lazy-load search over the full tool catalog ------------
#
# When the regex router misses, the LLM can call `find_tool` with a
# free-form intent. We compute a tiny BM25-style ranking over each tool's
# (name + description) corpus and return the top matches. The chat hop
# loop then lazy-loads the schemas of any tool the model names on the
# next hop. Pure stdlib (Counter + math.log), no external deps.

# Category bucketing: derived from the tool name. Used by find_tool's
# response so the model has a coarse signal for what each match does.
# Kept as ordered tuples so the FIRST match wins (e.g. `propose_*` lands
# in `workflow` before any other prefix sneaks in).
_FIND_TOOL_CATEGORIES: tuple[tuple[str, str], ...] = (
    # Explicit overrides for special tools — handled first.
    ("find_tool", "meta"),
    ("ASK_USER", "meta"),
    # Workflows / proposals.
    ("propose_", "workflow"),
    # Backtesting. (`run_backtest` retired 2026-06-01 — diverged from the
    # shared cost/CAGR model + rigor battery; use backtest_workflow / _dsl_tree.)
    ("backtest_", "backtest"),
    ("scan_pairs", "backtest"),
    ("test_cointegration", "backtest"),
    # News.
    ("news_", "news"),
    ("get_news", "news"),
    # Account / user / watchlist.
    ("get_user_", "account"),
    ("watchlist_", "account"),
    # Order placement / management.
    ("place_", "order"),
    ("create_gtt", "order"),
    ("create_sl", "order"),
    ("create_oco", "order"),
    ("create_dip", "order"),
    ("create_sip", "order"),
    ("create_strategy", "order"),
    ("create_cash_sweep", "order"),
    ("create_rebalancing", "order"),
    ("create_drawdown", "order"),
    ("cancel_", "order"),
    ("modify_", "order"),
    ("list_pending", "order"),
    ("list_gtt", "order"),
    ("list_sips", "order"),
    ("list_strategies", "order"),
    ("list_upcoming", "order"),
    ("pause_", "order"),
    ("resume_", "order"),
    ("delete_", "order"),
    ("squareoff_", "order"),
    ("roll_", "order"),
    # Indicators / analytics.
    ("get_indicator", "indicator"),
    ("get_multiple_indicators", "indicator"),
    ("get_performance_metrics", "indicator"),
    ("compare_performance", "indicator"),
    ("get_correlation_matrix", "indicator"),
    ("get_returns", "indicator"),
    # Portfolio.
    ("get_portfolio", "portfolio"),
    ("get_holdings", "portfolio"),
    ("get_holding", "portfolio"),
    ("get_sector_breakdown", "portfolio"),
    ("get_tax_summary", "portfolio"),
    ("get_active_products", "portfolio"),
    ("calculate_tax_impact", "portfolio"),
    # Market data (generic — catch what's left).
    ("get_live_price", "market_data"),
    ("get_index_level", "market_data"),
    ("get_ohlc", "market_data"),
    ("get_market_status", "market_data"),
    ("get_52wk_range", "market_data"),
    ("get_price_history", "market_data"),
    ("get_top_movers", "market_data"),
    ("get_upcoming_events", "market_data"),
    ("get_option_chain", "market_data"),
    ("get_option_greeks", "market_data"),
    ("get_margin_required", "market_data"),
    ("get_product_spec", "portfolio"),
    # Yields.
    ("compare_yields", "market_data"),
    ("get_yield_recommendation", "market_data"),
    # Calculations.
    ("calculate_", "indicator"),
    # Scheduler.
    ("get_scheduler_status", "meta"),
)


def _category_for(tool_name: str) -> str:
    """Map a tool name → coarse category. First matching prefix wins."""
    for prefix, cat in _FIND_TOOL_CATEGORIES:
        if tool_name == prefix or tool_name.startswith(prefix):
            return cat
    return "meta"


_TOKEN_RE = re.compile(r"[\W_]+")


def _tokenize(text: str) -> list[str]:
    """Lowercase, split on non-word chars, drop short tokens."""
    if not text:
        return []
    return [t for t in _TOKEN_RE.split(text.lower()) if len(t) > 2]


@dataclass
class _FindToolIndex:
    """In-memory inverted index over (name + description) per tool.

    docs:           tool_name → list[tokens]
    doc_token_freq: tool_name → Counter(token → tf)
    doc_len:        tool_name → len(tokens)
    avg_doc_len:    average doc length (for BM25 length norm)
    idf:            token → idf weight
    descriptions:   tool_name → full description (used for response trim)
    """
    docs: dict[str, list[str]] = field(default_factory=dict)
    doc_token_freq: dict[str, Counter] = field(default_factory=dict)
    doc_len: dict[str, int] = field(default_factory=dict)
    avg_doc_len: float = 0.0
    idf: dict[str, float] = field(default_factory=dict)
    descriptions: dict[str, str] = field(default_factory=dict)


@lru_cache(maxsize=1)
def _find_tool_index() -> _FindToolIndex:
    """Build the search index once over ALL_TOOLS.

    Excludes the `find_tool` entry itself from the searchable corpus —
    no point ranking it against its own queries.
    """
    _ensure_v2_tools_registered()
    from backend.agents.tools import ALL_TOOLS

    idx = _FindToolIndex()
    df: Counter = Counter()
    total_len = 0
    n_docs = 0

    for name, defn in ALL_TOOLS.items():
        if name == "find_tool":
            continue
        fn = (defn.get("function") or {}) if isinstance(defn, dict) else {}
        desc = fn.get("description") or ""
        tokens = _tokenize(name) + _tokenize(desc)
        if not tokens:
            continue
        idx.docs[name] = tokens
        tf = Counter(tokens)
        idx.doc_token_freq[name] = tf
        idx.doc_len[name] = len(tokens)
        idx.descriptions[name] = desc
        for term in tf.keys():
            df[term] += 1
        total_len += len(tokens)
        n_docs += 1

    idx.avg_doc_len = (total_len / n_docs) if n_docs else 0.0
    # BM25-style smoothed idf: log((N - df + 0.5) / (df + 0.5) + 1)
    # The +1 inside the log keeps idf non-negative even for ubiquitous
    # tokens — common short stopwords still have ~0 weight without
    # going negative and inverting the score.
    for term, count in df.items():
        idx.idf[term] = math.log(((n_docs - count + 0.5) / (count + 0.5)) + 1.0)

    return idx


def _bm25_score(
    idx: _FindToolIndex,
    name: str,
    query_tokens: list[str],
    *,
    k1: float = 1.5,
    b: float = 0.75,
) -> float:
    """Compute a BM25 score for one (doc, query) pair."""
    tf = idx.doc_token_freq.get(name)
    if not tf:
        return 0.0
    dl = idx.doc_len.get(name, 0)
    adl = idx.avg_doc_len or 1.0
    score = 0.0
    for term in query_tokens:
        idf = idx.idf.get(term)
        if not idf:
            continue
        f = tf.get(term, 0)
        if not f:
            continue
        denom = f + k1 * (1 - b + b * (dl / adl))
        score += idf * (f * (k1 + 1) / denom)
    return score


def _truncate_description(desc: str, *, cap: int = 240) -> str:
    """Return the first sentence of `desc`, capped at `cap` chars."""
    if not desc:
        return ""
    # Split on ". " — keep just the first sentence's content.
    first = desc.split(". ", 1)[0].strip()
    # Some descriptions end without a trailing period; re-add for clarity.
    if not first.endswith("."):
        first = first + "."
    if len(first) > cap:
        first = first[: cap - 1].rstrip() + "…"
    return first


async def _find_tool_handler(args: dict) -> dict:
    """Handler for the `find_tool` meta-tool.

    Args:
        query: free-form intent string.
        top_k: max matches to return (1..10, default 5).

    Returns:
        {"matches": [{"name", "description", "category"}, ...]}
    """
    query = (args.get("query") or "").strip()
    if not query:
        return {"matches": [], "note": "empty query"}
    try:
        top_k = int(args.get("top_k", 5) or 5)
    except (TypeError, ValueError):
        top_k = 5
    top_k = max(1, min(top_k, 10))

    idx = _find_tool_index()
    query_tokens = _tokenize(query)
    if not query_tokens:
        return {"matches": [], "note": "no scorable tokens in query"}

    scored: list[tuple[float, str]] = []
    for name in idx.docs:
        s = _bm25_score(idx, name, query_tokens)
        if s > 0:
            scored.append((s, name))
    scored.sort(key=lambda p: (-p[0], p[1]))

    matches: list[dict] = []
    for _score, name in scored[:top_k]:
        matches.append({
            "name": name,
            "description": _truncate_description(idx.descriptions.get(name, "")),
            "category": _category_for(name),
        })
    return {"matches": matches}


# ---- v2 tool definitions ----------------------------------------------
#
# These are registered into ALL_TOOLS lazily. We declare them here rather than
# in tools.py so the v2 surface is clearly separated from the legacy pile.

_V2_REGISTERED = False
_V2_HANDLERS: dict = {}


def _ensure_v2_tools_registered() -> None:
    global _V2_REGISTERED
    if _V2_REGISTERED:
        return
    _V2_REGISTERED = True

    from backend.agents.tools import tool

    tool(
        "get_price_history",
        "Returns daily OHLCV for a stock or index over a period. "
        "Use when the user asks 'show me X', 'X chart', 'how has X done', "
        "'price history of X', or wants to see a stock visually. Returns "
        "actual price data the assistant uses to summarise; do NOT call this "
        "if the user is asking for a single point-in-time quote (use "
        "get_live_price for that).",
        {
            "symbol": {"type": "string"},
            "period": {"type": "string",
                       "enum": ["1mo", "3mo", "6mo", "1y", "2y", "5y"],
                       "default": "1y"},
        },
        ["symbol"],
        defaults={"exchange": "NSE"},
    )

    tool(
        "get_52wk_range",
        "Returns the 52-week high and low (and current price relative to range) "
        "for a stock. Real values from price history — never a placeholder.",
        {"symbol": {"type": "string"}},
        ["symbol"],
        defaults={"exchange": "NSE"},
    )

    tool(
        "get_product_spec",
        "Returns the spec (allocation, legs, tenor, notes) of a Pivot product. "
        "ONLY call when the user explicitly asks about Pivot's offerings "
        "(e.g. 'what is SafeGrow', 'explain EarnMore', 'show StormShield'). "
        "Never call as a reflexive answer to 'what should I invest in'.",
        {"product": {"type": "string",
                     "enum": ["safegrow", "earnmore", "stormshield"]}},
        ["product"],
    )

    # Historical fundamentals query (chat-kernel Phase 0.5, 2026-07-10):
    # one general tool over the mc.* DB — 26 ratios + statement lines x
    # 12 annual years with latest/series/max/min/cagr/yoy aggregation.
    from backend.services.financials_query import (
        TOOL_DESCRIPTION as _FQ_DESC,
        TOOL_NAME as _FQ_NAME,
        TOOL_PROPERTIES as _FQ_PROPS,
        TOOL_REQUIRED as _FQ_REQ,
        query_financials as _query_financials,
    )
    tool(_FQ_NAME, _FQ_DESC, _FQ_PROPS, _FQ_REQ)

    # Register handlers
    from backend.services._v2_tools import (
        get_price_history, get_52wk_range, get_product_spec,
    )
    from backend.services._dsl_chat_tools import (
        backtest_dsl_tree, propose_dsl_workflow,
    )
    from backend.services._pairs_chat_tools import (
        backtest_pairs, scan_pairs, test_cointegration,
    )
    from backend.services._portfolio_chat_tools import backtest_portfolio
    from backend.services._orchestrator_chat_tools import (
        compose_multistep, compare_backtests, extract_winner_symbol,
    )
    from backend.agents.web_tools import web_search_brief
    from backend.core.calculations.regime import regime_compare_metrics

    async def _regime_compare_async(args: dict) -> dict:
        return regime_compare_metrics(
            symbol=args.get("symbol"),
            pivot_date=args.get("pivot_date"),
            period=args.get("period") or "5y",
        )

    async def _extract_winner_sync(args: dict) -> dict:
        # extract_winner_symbol is sync but the dispatcher expects an
        # async handler. Thin wrap.
        return extract_winner_symbol(args)

    _V2_HANDLERS.update({
        "get_price_history": get_price_history,
        "get_52wk_range": get_52wk_range,
        "get_product_spec": get_product_spec,
        # DSL-tree chat tools (Phase B+1+C.0)
        "backtest_dsl_tree": backtest_dsl_tree,
        "propose_dsl_workflow": propose_dsl_workflow,
        # Pairs / stat-arb chat tools (Phase 2.3)
        "backtest_pairs": backtest_pairs,
        "scan_pairs": scan_pairs,
        "test_cointegration": test_cointegration,
        # Portfolio backtester (Phase 2.4)
        "backtest_portfolio": backtest_portfolio,
        # L14 orchestrator + helpers.
        "compose_multistep": compose_multistep,
        "compare_backtests": compare_backtests,
        "extract_winner_symbol": _extract_winner_sync,
        "web_search_brief": web_search_brief,
        "regime_compare_metrics": _regime_compare_async,
        "query_financials": _query_financials,
        # find_tool's schema is registered in agents/tools.py; the
        # handler lives here next to the search index.
        "find_tool": _find_tool_handler,
    })
