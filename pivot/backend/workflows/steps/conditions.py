"""Condition step executors.

Conditions gate continuation. A failed condition is NOT an error
(ARCHITECTURE.md §5.3): the run completes successfully, the engine
sets `halt_reason='condition_not_met'`, and downstream steps are
skipped. max_retries=0 — re-evaluating the same condition with the
same context can't change the outcome.

For Day 2 we ship `condition.numeric`. The other condition types stay
as NotImplementedError until their dependencies (market hours service,
position service, time-window helper) land Day 3-4.
"""
from __future__ import annotations

from typing import Any, Optional

from backend.workflows.engine import _ConditionFail
from backend.workflows.registry import register_step
from backend.workflows.schemas import (
    ConditionBooleanConfig,
    ConditionMarketStatusConfig,
    ConditionNumericConfig,
    ConditionPositionConfig,
    ConditionTimeWindowConfig,
)


_CONDITION_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"passed": {"type": "boolean"}},
    "required": ["passed"],
}


def _coerce_number(v: Any, side: str) -> float:
    """Refs may resolve to numbers, numeric strings, or other JSON
    primitives. The engine has already resolved refs by the time we
    run, so the value here is concrete. We accept int/float/numeric
    string, and raise a clear ValueError otherwise (caught by the
    engine and surfaced as a step error)."""
    if isinstance(v, bool):
        # bool is a subclass of int — reject explicitly so True/False
        # don't compare numerically.
        raise ValueError(
            f"condition.numeric.{side} resolved to a boolean; "
            f"expected number"
        )
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        try:
            return float(v)
        except ValueError as e:
            raise ValueError(
                f"condition.numeric.{side} resolved to {v!r} — "
                f"not a number"
            ) from e
    raise ValueError(
        f"condition.numeric.{side} resolved to "
        f"{type(v).__name__} — expected number"
    )


def _evaluate(left: float, op: str, right: float) -> bool:
    if op == "==":
        return left == right
    if op == "!=":
        return left != right
    if op == ">":
        return left > right
    if op == "<":
        return left < right
    if op == ">=":
        return left >= right
    if op == "<=":
        return left <= right
    raise ValueError(f"unknown condition operator {op!r}")


@register_step(
    step_type="condition.numeric",
    category="condition",
    label="Numeric check",
    description="Compare two numbers (or refs) with an operator",
    icon="equal",
    max_retries=0,
    trigger_only=False,
    config_model=ConditionNumericConfig,
    output_schema=_CONDITION_OUTPUT_SCHEMA,
)
async def execute_condition_numeric(ctx: Any) -> Optional[dict[str, Any]]:
    """Refs in `left`/`right` have already been resolved by the engine.
    We coerce to floats, evaluate, and either return `{passed: True}`
    or raise `_ConditionFail` so the engine halts the run with
    `succeeded` + `halt_reason='condition_not_met'`."""
    cfg = ctx.config
    left = _coerce_number(cfg["left"], "left")
    right = _coerce_number(cfg["right"], "right")
    op = cfg["operator"]
    if _evaluate(left, op, right):
        return {"passed": True}
    raise _ConditionFail


def _coerce_bool(v: Any) -> bool:
    """Refs may resolve to a Python bool, a "true"/"false" string, or
    a 0/1 int. Coerce all three to the canonical bool; raise otherwise."""
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return bool(v)
    if isinstance(v, str):
        s = v.strip().lower()
        if s in ("true", "1", "yes", "y", "on"):
            return True
        if s in ("false", "0", "no", "n", "off", ""):
            return False
        raise ValueError(
            f"condition.boolean.left resolved to {v!r} — expected boolean"
        )
    raise ValueError(
        f"condition.boolean.left resolved to {type(v).__name__} — "
        f"expected boolean"
    )


@register_step(
    step_type="condition.boolean",
    category="condition",
    label="Boolean check",
    description=(
        "Pass when a boolean ref equals the expected value "
        "(e.g. fetch.news's `matched`)"
    ),
    icon="check-circle",
    max_retries=0,
    trigger_only=False,
    config_model=ConditionBooleanConfig,
    output_schema=_CONDITION_OUTPUT_SCHEMA,
)
async def execute_condition_boolean(ctx: Any) -> Optional[dict[str, Any]]:
    """Boolean equality gate. Pass when ``coerce_bool(left) == value``,
    otherwise raise ``_ConditionFail`` so the engine completes the run
    with ``succeeded`` + ``halt_reason='condition_not_met'``."""
    cfg = ctx.config
    left = _coerce_bool(cfg["left"])
    expected = bool(cfg.get("value", True))
    if left == expected:
        return {"passed": True}
    raise _ConditionFail


@register_step(
    step_type="condition.market_status",
    category="condition",
    label="Market is open / closed",
    description="Pass when the NSE market is in the chosen state",
    icon="calendar-clock",
    max_retries=0,
    trigger_only=False,
    config_model=ConditionMarketStatusConfig,
    output_schema=_CONDITION_OUTPUT_SCHEMA,
)
async def execute_condition_market_status(ctx: Any) -> Optional[dict[str, Any]]:
    """Pass when NSE market is in the chosen state. Reuses the shared
    matcher in `steps.control` to keep the two state-machine surfaces
    in sync (skip_if can also check market_status)."""
    from backend.utils.time_utils import is_market_open, is_trading_day
    from backend.workflows.steps.control import _market_status_matches

    require = str(ctx.config["require"])
    if _market_status_matches(require, is_market_open, is_trading_day):
        return {"passed": True}
    raise _ConditionFail


@register_step(
    step_type="condition.position",
    category="condition",
    label="Position held / not held",
    description="Pass when the symbol is (or isn't) in your portfolio",
    icon="briefcase",
    max_retries=0,
    trigger_only=False,
    config_model=ConditionPositionConfig,
    output_schema=_CONDITION_OUTPUT_SCHEMA,
)
async def execute_condition_position(ctx: Any) -> Optional[dict[str, Any]]:
    """Pass when the symbol is (or isn't) in the user's holdings.
    Delegates to the same portfolio service fetch.portfolio uses, so
    both step types see the same canonical holdings shape."""
    from backend.services.portfolio import get_user_portfolio

    cfg = ctx.config
    symbol = str(cfg["symbol"]).upper()
    require = str(cfg["require"])  # 'held' | 'not_held'

    portfolio = get_user_portfolio(int(ctx.workflow.user_id), ctx.db)
    holdings = portfolio.get("holdings", []) if isinstance(portfolio, dict) else []
    held = any(
        str(h.get("tradingsymbol", h.get("symbol", ""))).upper() == symbol
        for h in holdings
    )
    holds_condition = held if require == "held" else not held
    if holds_condition:
        return {"passed": True}
    raise _ConditionFail


@register_step(
    step_type="condition.time_window",
    category="condition",
    label="Time window",
    description="Pass when the current time is inside the configured window",
    icon="hourglass",
    max_retries=0,
    trigger_only=False,
    config_model=ConditionTimeWindowConfig,
    output_schema=_CONDITION_OUTPUT_SCHEMA,
)
async def execute_condition_time_window(ctx: Any) -> Optional[dict[str, Any]]:
    """Pass when current time in the configured timezone is within
    [start_time, end_time] (HH:MM, inclusive). Reuses the shared
    helper in steps.control so skip_if and condition share semantics."""
    from backend.workflows.steps.control import _time_in_window

    cfg = ctx.config
    if _time_in_window(
        str(cfg["start_time"]),
        str(cfg["end_time"]),
        str(cfg.get("timezone", "Asia/Kolkata")),
    ):
        return {"passed": True}
    raise _ConditionFail
