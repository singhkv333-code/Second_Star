"""Condition step stubs.

Conditions gate continuation. Failing a condition is NOT an error
(ARCHITECTURE.md §5.3): the run completes successfully with
halt_reason='condition_not_met'. max_retries=0 — re-evaluating the same
condition with the same context can't change the outcome."""
from __future__ import annotations

from typing import Any, Optional

from backend.workflows.registry import register_step
from backend.workflows.schemas import (
    ConditionMarketStatusConfig,
    ConditionNumericConfig,
    ConditionPositionConfig,
    ConditionTimeWindowConfig,
)


# Conditions all share the same output shape: {"passed": bool}.
_CONDITION_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"passed": {"type": "boolean"}},
    "required": ["passed"],
}


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
async def execute_condition_numeric(*args: Any, **kwargs: Any) -> Optional[dict[str, Any]]:
    raise NotImplementedError("not yet implemented")


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
async def execute_condition_market_status(*args: Any, **kwargs: Any) -> Optional[dict[str, Any]]:
    raise NotImplementedError("not yet implemented")


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
async def execute_condition_position(*args: Any, **kwargs: Any) -> Optional[dict[str, Any]]:
    raise NotImplementedError("not yet implemented")


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
async def execute_condition_time_window(*args: Any, **kwargs: Any) -> Optional[dict[str, Any]]:
    raise NotImplementedError("not yet implemented")
