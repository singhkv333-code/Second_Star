"""Consolidated chat tools — chat-kernel Phase 1 (2026-07-10).

Five view-enum tools that replace ~24 overlapping narrow tools, per the
GPT-5.4-mini probe findings (Markdowns/CHAT_KERNEL_PROBE_2026-07-10.md):
the model asked for ONE tool per family with an explicit `view`/`action`
enum plus "Best for / NOT for" description lines, and selected correctly
when given exactly that.

Design rules:
  - Schema and handler are CO-LOCATED here (the registry pattern going
    forward). Registration happens at import via `tools.tool(...)`;
    handlers are exported in CONSOLIDATED_HANDLERS and merged into
    tool_executor.HANDLERS, which makes them visible automatically.
  - Handlers are thin ADAPTERS over the existing battle-tested handlers
    — no business logic lives here, so behaviour per view is identical
    to the narrow tool it replaces.
  - Legacy handler signature (args, kite_token, db, user_id) because
    several targets need broker/db context. v2 targets (args-only) are
    wrapped where needed.
  - Errors follow the structured shape the model requested in the probe:
    unknown enum values return `field/received_value/allowed_values` so
    it can self-repair in one step.
"""
from __future__ import annotations

import logging
from typing import Any

from backend.agents.tools import tool

logger = logging.getLogger(__name__)


def _bad_enum(field: str, received: Any, allowed: list[str]) -> dict:
    """Structured one-step-repairable validation error (probe D3)."""
    return {
        "success": False,
        "error": (
            f"invalid {field}={received!r}; allowed_values={allowed}. "
            f"Re-call with a valid {field}."
        ),
        "data": {"field": field, "received_value": received,
                 "allowed_values": allowed, "retriable": True},
        "logiccard": None,
    }


def _wrap_v2(data: dict) -> dict:
    """Adapt a v2 handler's bare data dict to the legacy envelope."""
    return {"success": True, "data": data or {}, "logiccard": None}


# ── 1. get_market_data ───────────────────────────────────────────────

tool(
    "get_market_data",
    "Price data for one NSE/BSE stock or index. view=quote → the live "
    "price right now; view=ohlc → today's open/high/low/close; "
    "view=history → daily OHLCV series over `period` (charts, 'how has X "
    "done'); view=range52w → 52-week high/low and where price sits in the "
    "range. Best for: ANY price question. NOT for: fundamentals (use "
    "query_financials), indicators (use get_indicators), option chains "
    "(use get_option_chain).",
    {
        "symbol": {"type": "string", "description":
                   "NSE ticker uppercase, or index name (NIFTY, "
                   "BANKNIFTY, SENSEX)."},
        "view": {"type": "string",
                 "enum": ["quote", "ohlc", "history", "range52w"]},
        "period": {"type": "string",
                   "enum": ["1mo", "3mo", "6mo", "1y", "2y", "5y"],
                   "default": "1y",
                   "description": "history view only"},
    },
    ["symbol", "view"],
    defaults={"exchange": "NSE"},
)


async def _get_market_data(a: dict, kt: str, db, uid: int) -> dict:
    from backend.agents import tool_executor as tx

    view = (a.get("view") or "").strip().lower()
    if view == "quote":
        out = await tx._get_live_price(a, kt, db, uid)
        # 51-sweep: a transient feed miss on a VALID ticker was narrated
        # as "double-check the ticker". When the symbol is in the curated
        # universe, the ticker is fine — say the feed is down instead.
        if not out.get("success"):
            sym = str(a.get("symbol") or "").strip().upper()
            try:
                from backend.services.sector_universe import (
                    symbol_sector_map,
                )
                known = sym in symbol_sector_map()
            except Exception:
                known = False
            if known:
                out["error"] = (
                    f"quote feed temporarily unavailable for {sym}. "
                    f"{sym} IS a valid NSE symbol — tell the user the "
                    "feed is down right now and to retry shortly. Do "
                    "NOT tell them to double-check the ticker."
                )
        return out
    if view == "ohlc":
        return await tx._get_ohlc(a, kt, db, uid)
    if view == "history":
        from backend.services._v2_tools import get_price_history
        return _wrap_v2(await get_price_history(a))
    if view == "range52w":
        from backend.services._v2_tools import get_52wk_range
        return _wrap_v2(await get_52wk_range(a))
    return _bad_enum("view", a.get("view"),
                     ["quote", "ohlc", "history", "range52w"])


# ── 2. get_portfolio ─────────────────────────────────────────────────

tool(
    "get_portfolio",
    "The user's own portfolio. view=summary → totals + day/overall P&L; "
    "view=holdings → every position; view=sectors → sector breakdown; "
    "view=tax → realised/unrealised tax summary; view=products → active "
    "Pivot products; view=detail → one holding's full detail (requires "
    "`symbol`). Best for: any 'my portfolio / my holdings / my P&L / do "
    "I own X' question. NOT for: market-wide data or other people's "
    "stocks.",
    {
        "view": {"type": "string",
                 "enum": ["summary", "holdings", "sectors", "tax",
                          "products", "detail"]},
        "symbol": {"type": "string",
                   "description": "detail view only — which holding"},
        "sort_by": {"type": "string",
                    "enum": ["value", "pnl", "day_change"],
                    "description": "holdings view only"},
    },
    ["view"],
)


async def _get_portfolio(a: dict, kt: str, db, uid: int) -> dict:
    from backend.agents import tool_executor as tx

    view = (a.get("view") or "").strip().lower()
    dispatch = {
        "summary": tx._get_portfolio_summary,
        "holdings": tx._get_holdings,
        "sectors": tx._get_sector_breakdown,
        "tax": tx._get_tax_summary,
        "products": tx._get_active_products,
        "detail": tx._get_holding_detail,
    }
    handler = dispatch.get(view)
    if handler is None:
        return _bad_enum("view", a.get("view"), sorted(dispatch))
    if view == "detail" and not a.get("symbol"):
        return {
            "success": False,
            "error": "view='detail' requires `symbol` (which holding?). "
                     "Re-call with symbol, or use view='holdings' for all.",
            "data": {"field": "symbol", "retriable": True},
            "logiccard": None,
        }
    return await handler(a, kt, db, uid)


# ── 3. manage_automation ─────────────────────────────────────────────

tool(
    "manage_automation",
    "List / pause / resume / delete the user's existing SIPs and "
    "strategies (agents). action=list shows them with ids; "
    "pause/resume/delete act on one `id` (of the given `kind`); "
    "action=pause_all pauses every SIP. Best for: 'show/pause/stop/"
    "resume/delete my SIP/agent/strategy'. NOT for: creating anything "
    "new (use create_sip / propose_workflow) or reading the portfolio "
    "(use get_portfolio).",
    {
        "action": {"type": "string",
                   "enum": ["list", "pause", "resume", "delete",
                            "pause_all"]},
        "kind": {"type": "string", "enum": ["sip", "strategy"],
                 "description": "which family; required except for "
                                "pause_all (always SIPs)"},
        "id": {"type": "integer",
               "description": "target id from action='list' — required "
                              "for pause/resume/delete"},
    },
    ["action", "kind"],
)


async def _manage_automation(a: dict, kt: str, db, uid: int) -> dict:
    from backend.agents import tool_executor as tx

    action = (a.get("action") or "").strip().lower()
    kind = (a.get("kind") or "").strip().lower()
    if action == "pause_all":
        return await tx._pause_all_sips(a, kt, db, uid)
    dispatch = {
        ("list", "sip"): tx._list_sips,
        ("pause", "sip"): tx._pause_sip,
        ("resume", "sip"): tx._resume_sip,
        ("delete", "sip"): tx._delete_sip,
        ("list", "strategy"): tx._list_strategies,
        ("pause", "strategy"): tx._pause_strategy,
        ("resume", "strategy"): tx._resume_strategy,
        ("delete", "strategy"): tx._delete_strategy,
    }
    handler = dispatch.get((action, kind))
    if handler is None:
        if action not in {"list", "pause", "resume", "delete", "pause_all"}:
            return _bad_enum("action", a.get("action"),
                             ["list", "pause", "resume", "delete",
                              "pause_all"])
        return _bad_enum("kind", a.get("kind"), ["sip", "strategy"])
    if action in {"pause", "resume", "delete"} and a.get("id") is None:
        return {
            "success": False,
            "error": f"action='{action}' needs `id`. Call "
                     f"manage_automation(action='list', kind='{kind}') "
                     f"first to get ids.",
            "data": {"field": "id", "retriable": True},
            "logiccard": None,
        }
    # Narrow handlers expect their historical arg names.
    b = dict(a)
    if a.get("id") is not None:
        b.setdefault("sip_id", a["id"])
        b.setdefault("strategy_id", a["id"])
    return await handler(b, kt, db, uid)


# ── 4. get_indicators ────────────────────────────────────────────────

tool(
    "get_indicators",
    "Compute technical indicator values for ONE symbol: rsi, sma, ema, "
    "macd, bollinger, atr, adx, supertrend, vwap (pass one or several in "
    "`indicators`). Best for: 'RSI of X', 'is X above its 200 DMA', "
    "multi-indicator technical reads. NOT for: raw prices (use "
    "get_market_data) or backtesting a rule (use backtest_dsl_tree).",
    {
        "symbol": {"type": "string"},
        "indicators": {"type": "array", "items": {"type": "string"},
                       "description": "e.g. ['rsi'] or ['rsi','sma']"},
        "period": {"type": "integer", "minimum": 2, "maximum": 250,
                   "description": "lookback length, default per-indicator"},
        "interval": {"type": "string", "enum": ["day", "week"],
                     "default": "day"},
    },
    ["symbol", "indicators"],
)


async def _get_indicators(a: dict, kt: str, db, uid: int) -> dict:
    from backend.agents import tool_executor as tx

    inds = a.get("indicators")
    if isinstance(inds, str):  # tolerate a bare string
        a = {**a, "indicators": [inds]}
    return await tx._get_multiple_indicators(a, kt, db, uid)


# ── 5. place_order ───────────────────────────────────────────────────

tool(
    "place_order",
    "REGISTER a buy/sell order for the user to confirm in their broker "
    "app (never auto-executed). Market order when `price` is omitted; "
    "limit order at `price` when given ('buy INFY at 1450'). Best for: "
    "immediate, unconditional order intent ONLY. NOT for: conditional "
    "('if/when it falls to…' → create_gtt_order), recurring ('every "
    "week' → create_sip), alerts ('tell me when' → propose_workflow "
    "notify), or anything with a trigger. ASK_USER first when the "
    "company name is genuinely ambiguous (bare 'Tata', 'HDFC', 'Adani') "
    "or '100 of X' doesn't say shares vs lots.",
    {
        "symbol": {"type": "string", "description":
                   "NSE ticker, uppercase. Infer from company name: "
                   "Swiggy→SWIGGY, Zomato/Eternal→ETERNAL, Infosys→INFY, "
                   "HDFC Bank→HDFCBANK, SBI→SBIN."},
        "side": {"type": "string", "enum": ["buy", "sell"]},
        "quantity": {"type": "integer", "minimum": 1,
                     "description": "number of shares (positive integer)"},
        "price": {"type": "number",
                  "description": "limit price in INR; OMIT for market"},
        "product": {"type": "string", "enum": ["CNC", "MIS"],
                    "default": "CNC",
                    "description": "CNC=delivery, MIS=intraday"},
    },
    ["symbol", "side", "quantity"],
    defaults={"exchange": "NSE", "product": "CNC"},
)


async def _place_order(a: dict, kt: str, db, uid: int) -> dict:
    from backend.agents import tool_executor as tx

    side = (a.get("side") or "").strip().lower()
    if side not in {"buy", "sell"}:
        return _bad_enum("side", a.get("side"), ["buy", "sell"])
    b = {**a, "transaction_type": side.upper()}
    if b.get("price") is not None:
        b.setdefault("order_type", "LIMIT")
        return await tx._place_limit_order(b, kt, db, uid)
    b.setdefault("order_type", "MARKET")
    return await tx._place_market_order(b, kt, db, uid)


# ── 6. calculate (round 2) ───────────────────────────────────────────

tool(
    "calculate",
    "Deterministic trading calculators, picked by `kind`: order_qty → "
    "shares a rupee budget buys (budget_inr, price? or symbol); "
    "tax_impact → STCG/LTCG estimate on a sale (symbol, quantity, "
    "tax_slab?); sl_price → stop price from a % (entry_price, stop_pct); "
    "dip_price → the price a dip% implies + shares a budget buys "
    "(symbol, dip_pct, budget_inr); margin → margin needed for an order "
    "(symbol, quantity, product CNC|MIS|NRML). Best for: any 'how "
    "many shares / what price / what tax / what margin' arithmetic. "
    "NOT for: placing anything (place_order) or market data.",
    {
        "kind": {"type": "string",
                 "enum": ["order_qty", "tax_impact", "sl_price",
                          "dip_price", "margin"]},
        "symbol": {"type": "string"},
        "quantity": {"type": "integer"},
        "budget_inr": {"type": "number"},
        "price": {"type": "number"},
        "entry_price": {"type": "number"},
        "stop_pct": {"type": "number"},
        "dip_pct": {"type": "number"},
        "tax_slab": {"type": "number"},
        "product": {"type": "string", "enum": ["CNC", "MIS", "NRML"]},
    },
    ["kind"],
)

# Which args each calculator actually requires — checked here so the
# model gets one structured, repairable error instead of a deep failure.
_CALC_REQUIRED: dict = {
    "order_qty": ["budget_inr"],
    "tax_impact": ["symbol", "quantity"],
    "sl_price": ["entry_price", "stop_pct"],
    "dip_price": ["symbol", "dip_pct", "budget_inr"],
    "margin": ["symbol", "quantity", "product"],
}


async def _calculate(a: dict, kt: str, db, uid: int) -> dict:
    from backend.agents import tool_executor as tx

    kind = (a.get("kind") or "").strip().lower()
    dispatch = {
        "order_qty": tx._calculate_order_qty,
        "tax_impact": tx._calculate_tax_impact,
        "sl_price": tx._calculate_sl_price,
        "dip_price": tx._calculate_dip_price,
        "margin": tx._calculate_margin,
    }
    handler = dispatch.get(kind)
    if handler is None:
        return _bad_enum("kind", a.get("kind"), sorted(dispatch))
    missing = [f for f in _CALC_REQUIRED[kind] if a.get(f) is None]
    if missing:
        return {
            "success": False,
            "error": (f"kind='{kind}' requires {missing}. Re-call with "
                      f"those fields filled from the user's words."),
            "data": {"field": missing[0], "missing_fields": missing,
                     "retriable": True},
            "logiccard": None,
        }
    return await handler(a, kt, db, uid)


# ── 7. get_ipo (round 2) ─────────────────────────────────────────────

tool(
    "get_ipo",
    "One IPO by name/symbol, picked by `view`: details → price band, "
    "dates, lot/issue size, subscription breakdown (retail/HNI/QIB x), "
    "RHP, allotment ('tell me about the X IPO', 'how subscribed is X'); "
    "listing → post-listing performance: issue price, listing-day pop, "
    "current return ('how did X list', 'X listing gain'). Best for: one "
    "named IPO. NOT for: the upcoming-IPO list (list_upcoming_ipos), "
    "applying (propose_ipo_application), or reminders "
    "(propose_ipo_automation). NEVER fabricate IPO details — unavailable "
    "fields are null with an honest note.",
    {
        "name_or_symbol": {"type": "string",
                           "description": "IPO company name or NSE "
                                          "symbol, case-insensitive"},
        "view": {"type": "string", "enum": ["details", "listing"]},
    },
    ["name_or_symbol", "view"],
)


async def _get_ipo(a: dict, kt: str, db, uid: int) -> dict:
    from backend.agents import tool_executor as tx

    view = (a.get("view") or "").strip().lower()
    if view == "details":
        return await tx._get_ipo_details(a, kt, db, uid)
    if view == "listing":
        return await tx._get_ipo_listing(a, kt, db, uid)
    return _bad_enum("view", a.get("view"), ["details", "listing"])


# ── Export ───────────────────────────────────────────────────────────

CONSOLIDATED_HANDLERS: dict = {
    "calculate": _calculate,
    "get_ipo": _get_ipo,
    "get_market_data": _get_market_data,
    "get_portfolio": _get_portfolio,
    "manage_automation": _manage_automation,
    "get_indicators": _get_indicators,
    "place_order": _place_order,
}

# Narrow tools each consolidated tool supersedes — hidden from the LLM
# once the swap is on (handlers stay callable; cards/REST unaffected).
SUPERSEDED_BY_CONSOLIDATION: frozenset = frozenset({
    # get_market_data
    "get_live_price", "get_ohlc", "get_price_history", "get_52wk_range",
    # get_portfolio
    "get_portfolio_summary", "get_holdings", "get_sector_breakdown",
    "get_tax_summary", "get_active_products", "get_holding_detail",
    # manage_automation
    "list_sips", "pause_sip", "resume_sip", "delete_sip", "pause_all_sips",
    "list_strategies", "pause_strategy", "resume_strategy",
    "delete_strategy",
    # get_indicators
    "get_indicator", "get_multiple_indicators",
    # place_order
    "place_market_order", "place_limit_order",
    # calculate (round 2)
    "calculate_order_qty", "calculate_tax_impact", "calculate_sl_price",
    "calculate_dip_price", "calculate_margin",
    # get_ipo (round 2)
    "get_ipo_details", "get_ipo_listing",
    # folded into compare_performance (round 2): single-symbol subsets
    "get_performance_metrics", "get_returns",
})
