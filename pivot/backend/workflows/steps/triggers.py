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
    TriggerCompoundConfig,
    TriggerEventConfig,
    TriggerExitCompoundConfig,
    TriggerIndicatorConfig,
    TriggerExpiryDayConfig,
    TriggerIpoOpenConfig,
    TriggerManualConfig,
    TriggerMarketRelativeTimeConfig,
    TriggerPolymarketConfig,
    TriggerPriceConfig,
    TriggerScheduleConfig,
    TriggerWebhookConfig,
)


@register_step(
    step_type="trigger.schedule",
    category="trigger",
    label="On a schedule",
    description=(
        "Run on a repeating clock — e.g. every weekday 9:20 AM, or "
        "every 30 minutes."
    ),
    icon="clock",
    max_retries=0,
    trigger_only=True,
    config_model=TriggerScheduleConfig,
    output_schema=None,
    group="Schedule & time",
)
async def execute_trigger_schedule(ctx: Any) -> Optional[dict[str, Any]]:
    """No-op: the scheduler already decided this should fire. The
    workflow_runs row carries `triggered_by='schedule'` so the audit
    trail is complete."""
    return None


@register_step(
    step_type="trigger.price",
    category="trigger",
    label="When price crosses a level",
    description=(
        "Fire when a symbol's last price crosses above or below a "
        "level you set."
    ),
    icon="trending-up",
    max_retries=0,
    trigger_only=True,
    config_model=TriggerPriceConfig,
    output_schema=None,
    group="Price, indicators & exits",
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
    label="When an indicator crosses a level",
    description=(
        "Fire when a technical indicator (RSI, SMA, EMA, MACD…) "
        "crosses a threshold."
    ),
    icon="activity",
    max_retries=0,
    trigger_only=True,
    config_model=TriggerIndicatorConfig,
    output_schema=None,
    group="Price, indicators & exits",
)
async def execute_trigger_indicator(ctx: Any) -> Optional[dict[str, Any]]:
    """No-op: same reasoning as trigger.price. The watcher fires the
    run with `triggered_by='indicator_alert'`; this executor just
    acknowledges."""
    return None


@register_step(
    step_type="trigger.expiry_day",
    category="trigger",
    label="On option expiry day",
    description=(
        "Fire once on the morning of an underlying's option expiry, "
        "read live from the contract master — for rolls & expiry-day "
        "plays."
    ),
    icon="calendar-clock",
    max_retries=0,
    trigger_only=True,
    config_model=TriggerExpiryDayConfig,
    output_schema=None,
    group="Events & external",
)
async def execute_trigger_expiry_day(ctx: Any) -> Optional[dict[str, Any]]:
    """No-op: the option watcher in scheduler.py fires this trigger
    (triggered_by='event_alert') with a per-expiry fire-once latch
    persisted on the step config. The executor just acknowledges so
    step 1 runs."""
    return None


@register_step(
    step_type="trigger.compound",
    category="trigger",
    label="When multiple conditions are met",
    description=(
        "Fire when a combination of price, indicator, volume & option "
        "conditions (AND / OR / NOT) all hold — built visually, no "
        "extra steps."
    ),
    icon="git-merge",
    max_retries=0,
    trigger_only=True,
    config_model=TriggerCompoundConfig,
    output_schema=None,
    group="Price, indicators & exits",
)
async def execute_trigger_compound(ctx: Any) -> Optional[dict[str, Any]]:
    """No-op: the watcher (backend/workflows/scheduler.py) evaluates
    the tree on each tick. By the time the engine reaches this
    executor, the run row already carries an indicator_alert /
    price_alert triggered_by (the watcher picks the closest match)
    and the audit_context records which tree fired."""
    return None


@register_step(
    step_type="trigger.exit_compound",
    category="trigger",
    label="When exit conditions are met (open position)",
    description=(
        "For a position this workflow opened: fire on a mix of P&L, "
        "bars-held, drawdown & indicator conditions — e.g. down 2%, or "
        "held 10 bars and RSI > 70."
    ),
    icon="log-out",
    max_retries=0,
    trigger_only=True,
    config_model=TriggerExitCompoundConfig,
    output_schema=None,
    group="Price, indicators & exits",
)
async def execute_trigger_exit_compound(ctx: Any) -> Optional[dict[str, Any]]:
    """No-op: the scheduler's watcher
    (``backend/workflows/scheduler.py:_evaluate_exit_compound_trigger``)
    walks the tree on each tick. By the time the engine reaches this
    executor the run row already carries
    ``triggered_by='indicator_alert'``."""
    return None


@register_step(
    step_type="trigger.event",
    category="trigger",
    label="When a news event happens",
    description=(
        "Fire when a news article confirms an event you describe — "
        "e.g. 'RBI announces a repo-rate cut'."
    ),
    icon="newspaper",
    max_retries=0,
    trigger_only=True,
    config_model=TriggerEventConfig,
    group="Events & external",
    output_schema={
        "type": "object",
        "properties": {
            "articles": {"type": "array"},
            "matched": {"type": "boolean"},
            "max_confidence": {"type": "number"},
            "matched_count": {"type": "integer"},
            "top_article": {"type": ["object", "null"]},
            "event_description": {"type": "string"},
        },
        "required": [
            "articles", "matched", "max_confidence",
            "matched_count", "event_description",
        ],
    },
)
async def execute_trigger_event(ctx: Any) -> Optional[dict[str, Any]]:
    """Single-shot news-event check.

    Design note: ``poll_seconds`` / ``max_runtime_minutes`` are part of
    the trigger config so the LLM can express *"watch for up to 2 hours"*
    intent, but this executor does NOT block-poll inside one call. The
    existing scheduler watcher (``backend/workflows/scheduler.py
    :_poll_watch_triggers``) is only wired for ``trigger.price`` and
    ``trigger.indicator`` today; arming a per-event polling loop in the
    scheduler is a bigger change than this slice can absorb without
    breaking the watcher contract.

    Until that wiring lands, this executor performs ONE fetch+classify
    pass on each scheduler invocation. The downstream steps see the
    standard aggregate (``matched``, ``top_article``, etc.). A workflow
    author who wants polling semantics today should pair this with a
    ``trigger.schedule`` cron that re-fires the run every N minutes.

    Reuses the same fetch+classify pipeline as ``fetch.news`` — see
    ``backend.workflows.steps.fetches.execute_fetch_news`` for the
    full flow. Trigger executors carry max_retries=0 so any raise
    fails the run immediately (ARCHITECTURE.md §7 invariant 3).
    """
    from backend.workflows.steps.fetches import execute_fetch_news

    # The two configs intentionally share the keyword / event_description
    # / sources / min_confidence / hours_back fields, so we can delegate
    # to fetch.news's executor wholesale. The extra trigger-only knobs
    # (poll_seconds, max_runtime_minutes) are picked up by the scheduler,
    # not the executor — silently ignored here is the correct shape.
    return await execute_fetch_news(ctx)


@register_step(
    step_type="trigger.manual",
    category="trigger",
    label="Manual (Run now)",
    description="Never fires on its own — runs only when you press Run now.",
    icon="play",
    max_retries=0,
    trigger_only=True,
    config_model=TriggerManualConfig,
    output_schema=None,
    group="Events & external",
)
async def execute_trigger_manual(ctx: Any) -> Optional[dict[str, Any]]:
    """No-op: the user clicked Run now. The run row carries
    `triggered_by='manual'`."""
    return None


@register_step(
    step_type="trigger.polymarket",
    category="trigger",
    label="On a Polymarket market",
    description=(
        "Fire when a Polymarket prediction market crosses a probability "
        "you set, or resolves."
    ),
    icon="trending-up",
    max_retries=0,
    trigger_only=True,
    config_model=TriggerPolymarketConfig,
    group="Events & external",
    output_schema={
        "type": "object",
        "properties": {
            "market_id": {"type": "string"},
            "token_id": {"type": "string"},
            "side": {"type": "string"},
            "mode": {"type": "string"},
            "fired_at_price": {"type": ["number", "null"]},
            "fired_on_resolution_winner": {"type": ["string", "null"]},
        },
        "required": ["market_id", "token_id", "side", "mode"],
    },
)
async def execute_trigger_polymarket(ctx: Any) -> Optional[dict[str, Any]]:
    """No-op: the Polymarket WS supervisor / evaluator (see
    backend/news_events/workers/polymarket_ws_worker.py +
    backend/news_events/pipeline/prediction_market_ws.py) fires this
    trigger from out-of-band by calling fire_external_event(). By the
    time the engine reaches this executor the run row already carries
    triggered_by='event_alert' and the audit_context dict in
    run.context["polymarket"] has the firing snapshot.

    Same pattern as trigger.price / trigger.event — the executor
    exists purely so the engine can advance to step N+1 without
    needing a special trigger-step skip path.
    """
    return None


@register_step(
    step_type="trigger.market_relative_time",
    category="trigger",
    label="At market open / close",
    description=(
        "Fire at a fixed offset from the NSE open or close — e.g. "
        "5 min after open."
    ),
    icon="clock",
    max_retries=0,
    trigger_only=True,
    config_model=TriggerMarketRelativeTimeConfig,
    output_schema=None,
    group="Schedule & time",
)
async def execute_trigger_market_relative_time(ctx: Any) -> Optional[dict[str, Any]]:
    """No-op: by the time the engine reaches this executor, the
    scheduler has already fired the run (same lifecycle as
    `trigger.schedule`). The scheduler resolves the relative anchor to
    a concrete cron at job-arming time — see
    backend/workflows/scheduler.py:_arm_market_relative_time."""
    return None


@register_step(
    step_type="trigger.ipo_open",
    category="trigger",
    label="When an IPO opens",
    description="Fire when an IPO's subscription window opens.",
    icon="rocket",
    max_retries=0,
    trigger_only=True,
    config_model=TriggerIpoOpenConfig,
    output_schema=None,
    group="Events & external",
)
async def execute_trigger_ipo_open(ctx: Any) -> Optional[dict[str, Any]]:
    """No-op: the IPO open watcher (``backend/workflows/scheduler.py
    :_poll_ipo_open_triggers``) is what actually fires this trigger. By
    the time the engine reaches this executor, the run row already
    exists. The executor's job is purely to return None so step 1 runs."""
    return None


@register_step(
    step_type="trigger.webhook",
    category="trigger",
    label="On a webhook",
    description=(
        "Fire when an external system POSTs to this workflow's unique "
        "URL; the payload is available to later steps."
    ),
    icon="webhook",
    max_retries=0,
    trigger_only=True,
    config_model=TriggerWebhookConfig,
    output_schema=None,
    group="Events & external",
)
async def execute_trigger_webhook(ctx: Any) -> Optional[dict[str, Any]]:
    """No-op at execute time. The webhook router writes the inbound
    body into `run.context["webhook_payload"]` BEFORE the engine starts,
    so downstream `{{context.webhook_payload.<path>}}` refs resolve
    correctly."""
    return None
