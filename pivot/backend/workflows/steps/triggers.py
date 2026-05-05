"""Trigger step executors.

Triggers always live at step_index=0 and have max_retries=0 (§7
invariant 3). For the v1 demo path we ship `trigger.manual` and
`trigger.schedule` as no-ops: by the time the engine reaches them, the
trigger has already fired (the scheduler / "Run now" handler created
the run row). The executor's job is purely to log the fire and return
None so the engine moves on to step 1.

The remaining triggers (price/indicator/event/webhook) stay as
NotImplementedError stubs — they are wired Day 3-4 once the watcher
exists. The catalog still publishes them so the frontend renders them
in the picker, but trying to *execute* one will fail the run.
"""
from __future__ import annotations

from typing import Any, Optional

from backend.workflows.registry import register_step
from backend.workflows.schemas import (
    TriggerEventConfig,
    TriggerIndicatorConfig,
    TriggerManualConfig,
    TriggerMarketRelativeTimeConfig,
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
async def execute_trigger_schedule(ctx: Any) -> Optional[dict[str, Any]]:
    """No-op: the scheduler already decided this should fire. The
    workflow_runs row carries `triggered_by='schedule'` so the audit
    trail is complete."""
    return None


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
async def execute_trigger_price(ctx: Any) -> Optional[dict[str, Any]]:
    """No-op: the watcher (backend/workflows/scheduler.py:_poll_watch_triggers)
    is what actually fires this trigger. By the time the engine reaches
    this executor, the run row already carries `triggered_by='price_alert'`.
    The executor's only job is to log + return None so step 1 runs."""
    return None


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
async def execute_trigger_indicator(ctx: Any) -> Optional[dict[str, Any]]:
    """No-op: same reasoning as trigger.price. The watcher fires the
    run with `triggered_by='indicator_alert'`; this executor just
    acknowledges."""
    return None


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
async def execute_trigger_event(ctx: Any) -> Optional[dict[str, Any]]:
    raise NotImplementedError("trigger.event executor lands Day 4 with event sources")


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
async def execute_trigger_manual(ctx: Any) -> Optional[dict[str, Any]]:
    """No-op: the user clicked Run now. The run row carries
    `triggered_by='manual'`."""
    return None


@register_step(
    step_type="trigger.market_relative_time",
    category="trigger",
    label="At market open/close",
    description="Fire at a fixed offset from the NSE open or close",
    icon="clock",
    max_retries=0,
    trigger_only=True,
    config_model=TriggerMarketRelativeTimeConfig,
    output_schema=None,
)
async def execute_trigger_market_relative_time(ctx: Any) -> Optional[dict[str, Any]]:
    """No-op: by the time the engine reaches this executor, the
    scheduler has already fired the run (same lifecycle as
    `trigger.schedule`). The scheduler resolves the relative anchor to
    a concrete cron at job-arming time — see
    backend/workflows/scheduler.py:_arm_market_relative_time."""
    return None


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
async def execute_trigger_webhook(ctx: Any) -> Optional[dict[str, Any]]:
    """No-op at execute time. The webhook router writes the inbound
    body into `run.context["webhook_payload"]` BEFORE the engine starts,
    so downstream `{{context.webhook_payload.<path>}}` refs resolve
    correctly."""
    return None
