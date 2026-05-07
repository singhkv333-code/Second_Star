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
from dataclasses import dataclass, field

from backend.agents.tool_executor import execute_tool as _legacy_execute_tool
from backend.agents.tools import get_tool_defaults


logger = logging.getLogger(__name__)


# ---- Tool catalogue ----------------------------------------------------
#
# Each entry has the OpenAI function-calling shape Sarvam expects (we reuse
# the existing tools.py definitions; this file simply curates the visible set).

# Tools that have a real backing implementation in tool_executor.py.
# Keep this list aligned with the dispatcher in tool_executor.py:18.
_REAL_TOOLS: set[str] = {
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
    # Yields
    "compare_yields", "get_yield_recommendation",
    # Calculations
    "calculate_order_qty", "calculate_tax_impact", "calculate_sl_price",
    "calculate_dip_price", "calculate_margin",
    # Backtest
    "run_backtest",
    # Scheduler
    "get_scheduler_status", "list_upcoming_jobs",
    # New v2 tools
    "get_product_spec",
    # Agent System (Workflows v1)
    "propose_workflow",
    # Macro tools — narrow alternatives to propose_workflow that
    # hydrate the most common shapes server-side. ~30× faster decode.
    "propose_scheduled_order",
    "propose_threshold_order",
    "propose_basket_allocation",
    "propose_holding_action",
}


# Tools intentionally excluded because their implementation is a stub:
#   modify_order, place_futures_order, place_options_order,
#   place_multileg_options, roll_futures_position,
#   get_option_chain, get_option_greeks, get_margin_required,
#   create_cash_sweep, create_rebalancing_rule, create_drawdown_protection,
#   get_upcoming_events
# When their handlers stop returning placeholder text they get added here.


@dataclass
class ToolResult:
    name: str
    args: dict
    success: bool
    data: dict
    error: str | None = None
    logiccard: dict | None = None

    def to_llm_string(self) -> str:
        """Compact JSON string the model sees as the tool result."""
        if not self.success:
            return json.dumps({"error": self.error or "tool failed"})
        return json.dumps(self.data, default=str)[:6000]


def get_tool_schema() -> list[dict]:
    """The full tool list shown to the LLM on every turn."""
    from backend.agents.tools import ALL_TOOLS
    # Make sure the v2-only tools are registered as soon as we ask for the schema.
    _ensure_v2_tools_registered()
    return [defn for name, defn in ALL_TOOLS.items() if name in _REAL_TOOLS]


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
            logger.exception("v2 tool %s failed: %s", name, e)
            return ToolResult(name=name, args=merged, success=False, data={}, error=str(e)[:200])
        return ToolResult(name=name, args=merged, success=True, data=data)

    if name not in _REAL_TOOLS:
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

    # Register handlers
    from backend.services._v2_tools import (
        get_price_history, get_52wk_range, get_product_spec,
    )
    _V2_HANDLERS.update({
        "get_price_history": get_price_history,
        "get_52wk_range": get_52wk_range,
        "get_product_spec": get_product_spec,
    })
