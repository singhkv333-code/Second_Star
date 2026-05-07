"""Per-state policy: tool palette, system prompt block, tool_choice,
output budget, cache key.

The pipeline calls `policy_for(ctx)` to get the StatePolicy for the
LLM hop. Tools the LLM sees are TIGHT — usually 1-5, never the full
48-tool catalog. The system prompt is base + state-specific block,
total ~150 lines compared to v1's 750.

Adding a new state? Add a tuple to STATE_POLICIES and a markdown
file under backend/prompts/states/.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Optional, Union

from backend.chat_v2.state import (
    ConvContext,
    ConvState,
    MacroKind,
)


# ─────────────── Static tool palette per state / kind ──────────────


# Read-only tools always available where data fetches make sense.
READ_TOOLS = (
    "get_live_price", "get_stock_quote", "get_stock_history", "get_ohlc",
    "get_index_level", "get_top_movers", "get_market_status",
    "get_portfolio_summary", "get_holdings", "get_holding_detail",
    "get_sector_breakdown",
    "get_indicator", "get_multiple_indicators", "get_returns",
    "get_performance_metrics", "compare_performance",
    "get_correlation_matrix",
    "list_pending_orders", "list_gtt_orders", "list_strategies",
    "list_workflows", "list_sips",
)

# Order-family tools, used in DRAFTING(order).
ORDER_TOOLS = (
    "place_market_order", "place_limit_order", "create_gtt_order",
    "create_sl_order", "create_oco_order", "create_sip",
    "calculate_order_qty", "calculate_sl_price",
)

# Workflow-builder tool — DRAFTING(workflow) sees ONLY this.
WORKFLOW_TOOLS = ("propose_workflow",)

# Threshold / scheduled / basket / holding macros, when those kinds
# are the active draft.
THRESHOLD_TOOLS = ("propose_threshold_order",)
SCHEDULED_TOOLS = ("propose_scheduled_order",)
BASKET_TOOLS = ("propose_basket_allocation",)
HOLDING_TOOLS = ("propose_holding_action",)
BACKTEST_TOOLS = ("run_workflow_backtest",)

# ASK_USER is synthetic — always present so the model can clarify.
SYNTHETIC = ("ASK_USER",)


# ─────────────── Prompt-block loader (cached at import) ────────────


_PROMPT_DIR = Path(__file__).resolve().parents[2] / "prompts" / "states"
_PROMPT_CACHE: dict[str, str] = {}


def _load(name: str) -> str:
    if name not in _PROMPT_CACHE:
        path = _PROMPT_DIR / f"{name}.md"
        try:
            _PROMPT_CACHE[name] = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            _PROMPT_CACHE[name] = ""
    return _PROMPT_CACHE[name]


# ─────────────── StatePolicy dataclass ─────────────────────────────


@dataclass
class StatePolicy:
    """The policy applied to one LLM hop given the current state."""
    state_label: str
    tools: tuple[str, ...]
    tool_choice: Union[Literal["auto", "required", "none"], dict] = "auto"
    system_block: str = ""
    max_output_tokens: int = 600
    reasoning_effort: Literal["minimal", "low", "medium"] = "low"
    cache_key: str = "pivot-chat-v2-default"


# ─────────────── Resolver ──────────────────────────────────────────


_BASE: Optional[str] = None


def _base_block() -> str:
    global _BASE
    if _BASE is None:
        _BASE = _load("_base")
    return _BASE


def _drafting_macro_block(kind: Optional[MacroKind]) -> str:
    """Pick the right macro-specific block under DRAFTING."""
    if kind is None:
        return _load("drafting_workflow")  # safe default
    name_map = {
        MacroKind.WORKFLOW: "drafting_workflow",
        MacroKind.ORDER: "drafting_order",
        MacroKind.BASKET: "drafting_basket",
        MacroKind.HOLDING: "drafting_holding",
        MacroKind.THRESHOLD: "drafting_workflow",   # share workflow rules
        MacroKind.SCHEDULED: "drafting_workflow",
        MacroKind.BACKTEST: "drafting_workflow",
    }
    return _load(name_map.get(kind, "drafting_workflow"))


def _macro_tools_for(kind: MacroKind) -> tuple[str, ...]:
    """Tool palette for a given macro kind in DRAFTING state."""
    if kind == MacroKind.WORKFLOW:
        return WORKFLOW_TOOLS
    if kind == MacroKind.THRESHOLD:
        return THRESHOLD_TOOLS
    if kind == MacroKind.SCHEDULED:
        return SCHEDULED_TOOLS
    if kind == MacroKind.BASKET:
        return BASKET_TOOLS
    if kind == MacroKind.HOLDING:
        return HOLDING_TOOLS
    if kind == MacroKind.BACKTEST:
        return BACKTEST_TOOLS
    if kind == MacroKind.ORDER:
        return ORDER_TOOLS
    return WORKFLOW_TOOLS


def policy_for(ctx: ConvContext) -> StatePolicy:
    """Compute the StatePolicy for the next LLM hop given ctx."""

    base = _base_block()

    if ctx.state == ConvState.IDLE:
        return StatePolicy(
            state_label="idle",
            tools=READ_TOOLS + SYNTHETIC,
            tool_choice="auto",
            system_block=base + "\n\n" + _load("idle"),
            max_output_tokens=400,
            reasoning_effort="minimal",
            cache_key="pivot-chat-v2-idle",
        )

    if ctx.state == ConvState.EXPLORING:
        return StatePolicy(
            state_label="exploring",
            tools=READ_TOOLS + SYNTHETIC,
            tool_choice="auto",
            system_block=base + "\n\n" + _load("exploring"),
            max_output_tokens=600,
            reasoning_effort="low",
            cache_key="pivot-chat-v2-exploring",
        )

    if ctx.state == ConvState.DRAFTING:
        kind = ctx.macro_kind or MacroKind.WORKFLOW
        macro_tools = _macro_tools_for(kind)
        # If an order draft of a specific tool is on screen, pin
        # tool_choice to that tool so amendments re-emit the right shape.
        if kind == MacroKind.ORDER and ctx.macro_tool:
            tool_choice: Union[str, dict] = {
                "type": "function",
                "function": {"name": ctx.macro_tool},
            }
        else:
            tool_choice = "auto"

        return StatePolicy(
            state_label=f"drafting_{kind.value}",
            tools=macro_tools + READ_TOOLS + SYNTHETIC,
            tool_choice=tool_choice,
            system_block=base + "\n\n" + _drafting_macro_block(kind),
            max_output_tokens=1500,
            reasoning_effort="low",
            cache_key=f"pivot-chat-v2-drafting-{kind.value}",
        )

    if ctx.state == ConvState.AWAITING_CLARIFICATION:
        return StatePolicy(
            state_label="clarifying",
            tools=(
                WORKFLOW_TOOLS + THRESHOLD_TOOLS + SCHEDULED_TOOLS
                + BASKET_TOOLS + HOLDING_TOOLS + ORDER_TOOLS
                + READ_TOOLS + SYNTHETIC
            ),
            tool_choice="auto",
            system_block=base + "\n\n" + _load("clarifying"),
            max_output_tokens=1200,
            reasoning_effort="low",
            cache_key="pivot-chat-v2-clarifying",
        )

    if ctx.state == ConvState.ACTIVATED:
        return StatePolicy(
            state_label="activated",
            tools=READ_TOOLS + (
                "pause_strategy", "resume_strategy", "delete_strategy",
                "pause_sip", "resume_sip", "delete_sip",
            ) + SYNTHETIC,
            tool_choice="auto",
            system_block=base + "\n\n" + _load("activated"),
            max_output_tokens=400,
            reasoning_effort="minimal",
            cache_key="pivot-chat-v2-activated",
        )

    if ctx.state == ConvState.CANCELLED:
        return StatePolicy(
            state_label="cancelled",
            tools=READ_TOOLS + SYNTHETIC,
            tool_choice="auto",
            system_block=base + "\n\n" + _load("cancelled"),
            max_output_tokens=200,
            reasoning_effort="minimal",
            cache_key="pivot-chat-v2-cancelled",
        )

    # Fallback — should never hit.
    return StatePolicy(
        state_label="default",
        tools=READ_TOOLS + SYNTHETIC,
        tool_choice="auto",
        system_block=base,
        max_output_tokens=600,
        cache_key="pivot-chat-v2-default",
    )
