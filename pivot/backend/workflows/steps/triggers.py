"""Trigger step executors.

Triggers always live at step_index=0 and have max_retries=0 (§7
invariant 3). For auto-fired runs, the scheduler/watcher already
confirmed the condition before creating the run row, so these
executors are mostly a no-op logging point.

RE-VERIFICATION (2026-07-06 live-test fix): a MANUAL run
(`POST /workflows/{id}/run`) creates the run WITHOUT any pre-check —
before this fix, the no-op trigger executors let it sail straight
through to the action steps regardless of whether the condition was
anywhere close to true (e.g. a "buy at 9:20 AM" schedule agent,
manually run at 3:35 PM, placed a real paper order). schedule/price/
indicator/compound triggers now RE-CHECK the live condition inside
their own executor — for EVERY run, not just manual ones (a single,
unconditional source of truth beats trusting every future caller to
pre-check correctly) — and raise `_ConditionFail` when it is
confirmed NOT currently true, so the engine halts the run cleanly
with `succeeded` + `halt_reason='condition_not_met'` (the same outcome
`condition.*` steps already use) instead of silently executing the
action. Data-fetch failures fail OPEN (proceed) — we only block on a
*confirmed* false, never on "couldn't tell right now".

The remaining event-driven triggers (event/webhook/scheduled_macro/
polymarket/kalshi/ipo_open/manual) have no live point-in-time
condition to re-derive this way and stay pass-through — manually
testing them without waiting for the real-world event is the whole
point of a manual run for that class.
"""
from __future__ import annotations

from typing import Any, Optional

from backend.workflows.engine import _ConditionFail
from backend.workflows.registry import register_step
from backend.workflows.schemas import (
    TriggerCompoundConfig,
    TriggerEarningsConfig,
    TriggerEventConfig,
    TriggerExitCompoundConfig,
    TriggerGlobalPriceConfig,
    TriggerIndicatorConfig,
    TriggerExpiryDayConfig,
    TriggerIpoOpenConfig,
    TriggerManualConfig,
    TriggerMarketRelativeTimeConfig,
    TriggerKalshiConfig,
    TriggerPolymarketConfig,
    TriggerScheduledMacroConfig,
    TriggerPriceConfig,
    TriggerScheduleConfig,
    TriggerWebhookConfig,
)


_SCHEDULE_TOLERANCE_SECONDS = 300  # ±5 min around the cron's exact minute


def _schedule_condition_holds_now(cfg: dict[str, Any]) -> bool:
    """True iff `cfg`'s schedule (cron OR one-time run_at) is genuinely due
    within `_SCHEDULE_TOLERANCE_SECONDS` of right now. False only on a
    DEFINITIVE mismatch (e.g. a 9:20 AM cron checked at 3:35 PM) — a
    malformed/missing schedule fails OPEN (returns True) so a config the
    activate-time validator already accepted never blocks a legitimate run."""
    import datetime as _dt

    from backend.workflows.scheduler import InvalidCronError, compute_next_run_at

    tz_str = str(cfg.get("timezone") or "Asia/Kolkata")
    now = _dt.datetime.now(_dt.timezone.utc)
    run_at = cfg.get("run_at")
    if run_at:
        try:
            dt = _dt.datetime.fromisoformat(str(run_at).replace("Z", "+00:00"))
        except ValueError:
            return True
        if dt.tzinfo is None:
            from pytz import timezone as _pytz_tz
            tz = _pytz_tz(tz_str)
            dt = tz.localize(dt) if hasattr(tz, "localize") else dt.replace(tzinfo=tz)
        return abs((dt.astimezone(_dt.timezone.utc) - now).total_seconds()) \
            <= _SCHEDULE_TOLERANCE_SECONDS
    cron = cfg.get("cron")
    if not cron:
        return True
    try:
        nxt = compute_next_run_at(
            str(cron), tz_str,
            after=now - _dt.timedelta(seconds=_SCHEDULE_TOLERANCE_SECONDS + 1),
        )
    except InvalidCronError:
        return True
    return abs((nxt - now).total_seconds()) <= _SCHEDULE_TOLERANCE_SECONDS


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
    output_schema={
        "type": "object",
        "properties": {
            "confirmed_at": {"type": "string", "format": "date-time"},
            "cron": {"type": ["string", "null"]},
            "run_at": {"type": ["string", "null"]},
        },
    },
    group="Schedule & time",
)
async def execute_trigger_schedule(ctx: Any) -> Optional[dict[str, Any]]:
    """Re-checks the schedule is genuinely due right now (±5 min) before
    letting the run proceed — see the module docstring for why. An
    auto-fired run is always within tolerance (the poller just fired it);
    a manual run far outside the window is blocked instead of silently
    placing an order.

    Records the confirmation on WorkflowRunStep.output (2026-07-06 audit
    finding: trigger steps previously returned None, so a fired run carried
    NO forensic snapshot of what made it fire — auditing historical fires
    meant reading the step's current, since-mutated config instead of what
    was true at THIS run's moment)."""
    import datetime as _dt

    cfg = ctx.config or {}
    now_iso = _dt.datetime.now(_dt.timezone.utc).isoformat()
    if not _schedule_condition_holds_now(cfg):
        raise _ConditionFail({
            "checked_at": now_iso, "cron": cfg.get("cron"),
            "run_at": cfg.get("run_at"),
        })
    return {
        "confirmed_at": now_iso,
        "cron": cfg.get("cron"),
        "run_at": cfg.get("run_at"),
    }


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
    output_schema={
        "type": "object",
        "properties": {
            "observed_price": {"type": "number"},
            "threshold": {"type": "number"},
            "operator": {"type": "string"},
            "confirmed_at": {"type": "string", "format": "date-time"},
        },
    },
    group="Price, indicators & exits",
)
async def execute_trigger_price(ctx: Any) -> Optional[dict[str, Any]]:
    """Re-checks the price condition against a LIVE quote before letting the
    run proceed (see module docstring). Reuses the watcher's own
    `_matches_threshold` so "condition met" means the identical thing here
    and on the auto-fired path. A quote fetch failure fails OPEN (we
    couldn't tell, so don't block); a quote that clearly does NOT satisfy
    the operator/threshold raises `_ConditionFail`.

    On success, records {observed_price, threshold, operator, confirmed_at}
    on WorkflowRunStep.output — a permanent per-run snapshot of exactly what
    made this fire (2026-07-06 audit finding: the prior no-op left no
    forensic trail; auditing a historical fire meant reading the step's
    CURRENT, since-mutated config instead of the value at THIS run's moment)."""
    import datetime as _dt

    from backend.workflows.scheduler import _LAST_PRICE_KEY, _matches_threshold

    cfg = ctx.config or {}
    sym = str(cfg.get("symbol", "")).upper()
    exch = str(cfg.get("exchange", "NSE")).upper()
    operator = str(cfg.get("operator", ""))
    threshold = float(cfg.get("value", 0.0))
    if not sym or not operator:
        return None
    try:
        from backend.workflows.scheduler import _batch_fetch_prices
        quotes = _batch_fetch_prices([f"{exch}:{sym}"])
    except Exception:
        return None  # data unavailable — fail open
    current = quotes.get(f"{exch}:{sym}")
    if current is None:
        return None
    last_raw = cfg.get(_LAST_PRICE_KEY)
    last = float(last_raw) if isinstance(last_raw, (int, float)) else None
    if not _matches_threshold(operator, current, threshold, last):
        raise _ConditionFail({
            "observed_price": current, "threshold": threshold, "operator": operator,
            "checked_at": _dt.datetime.now(_dt.timezone.utc).isoformat(),
        })
    return {
        "observed_price": current,
        "threshold": threshold,
        "operator": operator,
        "confirmed_at": _dt.datetime.now(_dt.timezone.utc).isoformat(),
    }


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
    output_schema={
        "type": "object",
        "properties": {
            "observed_value": {"type": "number"},
            "threshold": {"type": "number"},
            "operator": {"type": "string"},
            "indicator": {"type": "string"},
            "confirmed_at": {"type": "string", "format": "date-time"},
        },
    },
    group="Price, indicators & exits",
)
async def execute_trigger_indicator(ctx: Any) -> Optional[dict[str, Any]]:
    """Re-checks the indicator condition against a freshly-computed value
    before letting the run proceed (see module docstring). Data/compute
    failure fails OPEN; a value that clearly does NOT satisfy the
    operator/threshold raises `_ConditionFail`.

    On success, records {observed_value, threshold, operator, indicator,
    confirmed_at} on WorkflowRunStep.output (2026-07-06 audit finding — see
    execute_trigger_price's docstring)."""
    import datetime as _dt

    from backend.workflows.scheduler import (
        _LAST_VALUE_KEY, _compute_indicator_sync, _matches_threshold,
    )

    cfg = ctx.config or {}
    sym = str(cfg.get("symbol", "")).upper()
    indicator = str(cfg.get("indicator", "")).lower()
    operator = str(cfg.get("operator", ""))
    if not sym or not indicator or not operator:
        return None
    period = int(cfg.get("period", 14))
    threshold = float(cfg.get("value", 0.0))
    timeframe = str(cfg.get("timeframe") or "daily").lower()
    try:
        value = _compute_indicator_sync(sym, indicator, period, timeframe)
    except Exception:
        return None  # data unavailable — fail open
    if value is None:
        return None
    last_raw = cfg.get(_LAST_VALUE_KEY)
    last = float(last_raw) if isinstance(last_raw, (int, float)) else None
    if not _matches_threshold(operator, value, threshold, last):
        raise _ConditionFail({
            "observed_value": value, "threshold": threshold,
            "operator": operator, "indicator": indicator,
            "checked_at": _dt.datetime.now(_dt.timezone.utc).isoformat(),
        })
    return {
        "observed_value": value,
        "threshold": threshold,
        "operator": operator,
        "indicator": indicator,
        "confirmed_at": _dt.datetime.now(_dt.timezone.utc).isoformat(),
    }


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
    output_schema={
        "type": "object",
        "properties": {
            "new_state": {"type": "object"},
            "confirmed_at": {"type": "string", "format": "date-time"},
        },
    },
    group="Price, indicators & exits",
)
async def execute_trigger_compound(ctx: Any) -> Optional[dict[str, Any]]:
    """Re-walks the DSL tree against LIVE data before letting the run
    proceed (see module docstring) — the identical evaluator the watcher
    uses. A parse/eval failure fails OPEN; a tree that resolves to
    anything other than TRUE raises `_ConditionFail`.

    On success, records the tree's resolved leaf values + confirmed_at on
    WorkflowRunStep.output (2026-07-06 audit finding — see
    execute_trigger_price's docstring)."""
    import datetime as _dt

    cfg = ctx.config or {}
    entry_raw = cfg.get("entry")
    if not isinstance(entry_raw, dict):
        return None
    last_values_raw = cfg.get("_last_values")
    prev_state: dict[str, float] = (
        {k: float(v) for k, v in last_values_raw.items()
         if isinstance(v, (int, float))}
        if isinstance(last_values_raw, dict) else {}
    )
    try:
        from pydantic import TypeAdapter

        from backend.workflows.dsl.data_accessor import LiveDataAccessor
        from backend.workflows.dsl.evaluator import Ternary, evaluate
        from backend.workflows.dsl.schema import Tree

        tree = TypeAdapter(Tree).validate_python(entry_raw)
        result = evaluate(tree, accessor=LiveDataAccessor(), prev_state=prev_state)
    except Exception:
        return None  # parse/data failure — fail open
    if result.value is not Ternary.TRUE:
        raise _ConditionFail({
            "new_state": result.new_state,
            "resolved_to": str(result.value),
            "checked_at": _dt.datetime.now(_dt.timezone.utc).isoformat(),
        })
    return {
        "new_state": result.new_state,
        "confirmed_at": _dt.datetime.now(_dt.timezone.utc).isoformat(),
    }


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
    step_type="trigger.scheduled_macro",
    category="trigger",
    label="On a scheduled macro event",
    description=(
        "Fire on a known-date macro release once its OUTCOME is verified "
        "against the official source — e.g. 'when RBI cuts the repo rate' "
        "or 'when US CPI prints above 3%'."
    ),
    icon="calendar-check",
    max_retries=0,
    trigger_only=True,
    config_model=TriggerScheduledMacroConfig,
    group="Events & external",
    output_schema={
        "type": "object",
        "properties": {
            "kind": {"type": "string"},
            "expected_outcome": {"type": "string"},
            "decision": {"type": ["string", "null"]},
            "matched": {"type": "boolean"},
            "tier": {"type": ["string", "null"]},
            "confidence": {"type": ["number", "null"]},
            "evidence": {"type": ["string", "null"]},
        },
        "required": ["kind", "expected_outcome"],
    },
)
async def execute_trigger_scheduled_macro(ctx: Any) -> Optional[dict[str, Any]]:
    """No-op: the macro watcher (``backend/workflows/scheduler.py
    :_poll_scheduled_macro_triggers``) opens the verify window around the
    calendar date, runs ``macro_events.verifier.verify_macro_outcome``,
    and fires this trigger out-of-band via ``fire_external_event`` only on
    a confident outcome match. By the time the engine reaches this
    executor the run row already carries ``triggered_by='event_alert'``
    and ``run.context['scheduled_macro']`` holds the verification
    snapshot. Same pattern as trigger.event / trigger.polymarket."""
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
    step_type="trigger.kalshi",
    category="trigger",
    label="On a Kalshi market",
    description=(
        "Fire when a Kalshi prediction market crosses a probability you "
        "set, or settles."
    ),
    icon="trending-up",
    max_retries=0,
    trigger_only=True,
    config_model=TriggerKalshiConfig,
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
async def execute_trigger_kalshi(ctx: Any) -> Optional[dict[str, Any]]:
    """No-op: the Kalshi REST poll worker
    (``backend/news_events/workers/kalshi_rest_worker.py``) drives the
    shared prediction-market evaluator and fires this trigger out-of-band
    via ``fire_external_event``. By the time the engine reaches this
    executor the run row already carries ``triggered_by='event_alert'``
    and ``run.context['kalshi']`` holds the firing snapshot. Same pattern
    as trigger.polymarket."""
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


@register_step(
    step_type="trigger.global_price",
    category="trigger",
    label="When a global asset's price crosses a level",
    description=(
        "Fire when a non-Kite asset (crypto like BTC/ETH, forex like "
        "EURUSD/USDINR, or USD-denominated commodities like WTI/Brent/"
        "gold) crosses a price level. For INR-denominated NSE/MCX "
        "instruments use trigger.price (Kite path) instead."
    ),
    icon="coins",
    max_retries=0,
    trigger_only=True,
    config_model=TriggerGlobalPriceConfig,
    output_schema=None,
    group="Price, indicators & exits",
)
async def execute_trigger_global_price(ctx: Any) -> Optional[dict[str, Any]]:
    """No-op: the global-price watcher (``backend/workflows/scheduler.py
    :_poll_global_price_triggers``) polls
    :func:`backend.market.global_quotes.get_global_quote` on the configured
    asset_class+symbol, applies the same ``_matches_threshold`` semantics as
    ``trigger.price`` against a persisted last-price latch, and fires this
    trigger out-of-band via ``fire_external_event``. By the time the engine
    reaches this executor the run row already carries
    ``triggered_by='price_alert'`` and ``run.context`` records the firing
    snapshot. Same pattern as trigger.price — the executor exists purely
    so the engine can advance to step 1 without a special trigger-skip
    path. Crypto runs 24/7 so the poll loop is NOT gated on NSE hours."""
    return None


@register_step(
    step_type="trigger.earnings",
    category="trigger",
    label="When a company reports earnings",
    description=(
        "Fire after a company's results are out, when EPS (or revenue) "
        "beats / misses / meets the consensus estimate — e.g. 'alert me "
        "when INFY beats EPS estimate'."
    ),
    icon="bar-chart-3",
    max_retries=0,
    trigger_only=True,
    config_model=TriggerEarningsConfig,
    output_schema=None,
    group="Events & external",
)
async def execute_trigger_earnings(ctx: Any) -> Optional[dict[str, Any]]:
    """No-op: the earnings watcher (``backend/workflows/scheduler.py
    :_poll_earnings_triggers``) opens the verify window around the
    announced report date (see
    :func:`backend.earnings_events.due_event`), runs
    :func:`backend.earnings_events.verify_earnings_outcome`, and fires
    this trigger out-of-band via ``fire_external_event`` ONLY when
    ``outcome.matched`` is True (fail-safe: missing data never fires).
    By the time the engine reaches this executor the run row already
    carries ``triggered_by='event_alert'`` (the value
    ``fire_external_event`` sets — the only external-fire class allowed by
    the workflow_runs CHECK constraint) and ``run.context`` holds the
    verification snapshot (reported/estimate/surprise/evidence). Same
    no-op pattern as trigger.scheduled_macro / trigger.polymarket."""
    return None
