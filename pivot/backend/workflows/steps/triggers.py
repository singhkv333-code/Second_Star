"""Trigger step stubs. Triggers always live at step_index=0.

Per ARCHITECTURE.md §7 invariant 3, triggers have max_retries=0: a
trigger either fires or it doesn't, retrying is meaningless.

Day-1 stubs raise NotImplementedError. Real executors land Day 2-4 in
this same module (no new imports from the engine — keep the boundary
clean)."""
from __future__ import annotations

from typing import Any, Optional

from backend.workflows.registry import register_step
from backend.workflows.schemas import (
    TriggerEventConfig,
    TriggerIndicatorConfig,
    TriggerManualConfig,
    TriggerPriceConfig,
    TriggerScheduleConfig,
    TriggerWebhookConfig,
)


@register_step(
    step_type="trigger.schedule",
    category="trigger",
    label="On schedule",
    description="Run on a cron schedule",
    icon="clock",
    max_retries=0,
    trigger_only=True,
    config_model=TriggerScheduleConfig,
    output_schema=None,
)
async def execute_trigger_schedule(*args: Any, **kwargs: Any) -> Optional[dict[str, Any]]:
    raise NotImplementedError("not yet implemented")


@register_step(
    step_type="trigger.price",
    category="trigger",
    label="On price",
    description="Fire when a symbol's price crosses a threshold",
    icon="trending-up",
    max_retries=0,
    trigger_only=True,
    config_model=TriggerPriceConfig,
    output_schema=None,
)
async def execute_trigger_price(*args: Any, **kwargs: Any) -> Optional[dict[str, Any]]:
    raise NotImplementedError("not yet implemented")


@register_step(
    step_type="trigger.indicator",
    category="trigger",
    label="On indicator",
    description="Fire when a technical indicator crosses a threshold",
    icon="activity",
    max_retries=0,
    trigger_only=True,
    config_model=TriggerIndicatorConfig,
    output_schema=None,
)
async def execute_trigger_indicator(*args: Any, **kwargs: Any) -> Optional[dict[str, Any]]:
    raise NotImplementedError("not yet implemented")


@register_step(
    step_type="trigger.event",
    category="trigger",
    label="On market event",
    description="Fire on an external event (RBI decision, results, FII flow)",
    icon="bell",
    max_retries=0,
    trigger_only=True,
    config_model=TriggerEventConfig,
    output_schema=None,
)
async def execute_trigger_event(*args: Any, **kwargs: Any) -> Optional[dict[str, Any]]:
    raise NotImplementedError("not yet implemented")


@register_step(
    step_type="trigger.manual",
    category="trigger",
    label="Manual",
    description="Only runs when you click Run now",
    icon="play",
    max_retries=0,
    trigger_only=True,
    config_model=TriggerManualConfig,
    output_schema=None,
)
async def execute_trigger_manual(*args: Any, **kwargs: Any) -> Optional[dict[str, Any]]:
    raise NotImplementedError("not yet implemented")


@register_step(
    step_type="trigger.webhook",
    category="trigger",
    label="Webhook",
    description="Fire when an external system POSTs to your unique URL",
    icon="webhook",
    max_retries=0,
    trigger_only=True,
    config_model=TriggerWebhookConfig,
    output_schema=None,
)
async def execute_trigger_webhook(*args: Any, **kwargs: Any) -> Optional[dict[str, Any]]:
    raise NotImplementedError("not yet implemented")
