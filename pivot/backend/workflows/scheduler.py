"""Workflow scheduler — fires `trigger.schedule` workflows on cron.

Plugs into the existing `backend/scheduler.py` `AsyncIOScheduler`
(don't add a parallel scheduler — see ARCHITECTURE.md §3 stack table).

Two surfaces:

  1. `upsert_workflow_schedule(db, workflow)` — called by the workflows
     router on activate / pause / archive / step edits. Computes
     `next_run_at` from the workflow's `trigger.schedule` step (if any)
     when status is `active`; otherwise clears it. **Does not** touch
     non-schedule trigger types (`trigger.price`, etc. are armed by the
     watcher subprocess, not here).

  2. `register_workflow_scheduler(scheduler)` — called once at app
     startup. Adds a recurring poll job that scans the workflows table
     every `_POLL_INTERVAL_SECONDS` and fires every active workflow
     whose `next_run_at <= now()`. After firing, recomputes `next_run_at`
     so the next tick is armed.

Why poll instead of register one APScheduler job per workflow:
  - Workflows can be activated / paused / patched at any time; a poll
    loop is naturally consistent with the DB without bookkeeping.
  - Cheaper at scale (one job vs N jobs).
  - The existing `check_strategy_triggers` job uses the same pattern.

Cron validation: `compute_next_run_at()` raises `InvalidCronError` when
the expression is malformed. The activate handler calls this before
flipping status, so an invalid cron fails 422 (not silently arms a dead
schedule — closes reviewer Day-2 edge case #1).
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional

from apscheduler.schedulers.asyncio import AsyncIOScheduler  # type: ignore[import-untyped]
from apscheduler.triggers.cron import CronTrigger  # type: ignore[import-untyped]
from pytz import timezone as pytz_timezone  # type: ignore[import-untyped]
from sqlalchemy.orm import Session

from backend.database import SessionLocal
from backend.models import (
    RunStatus,
    Workflow,
    WorkflowRun,
    WorkflowStatus,
    WorkflowStep,
)

logger = logging.getLogger(__name__)


# Polling cadence. Cron resolution is per-minute; 30s gives us at most
# 30s of jitter past the cron tick — acceptable for v1.
_POLL_INTERVAL_SECONDS = 30

# Price/indicator watcher cadence. Per ARCHITECTURE.md §8: every 60s
# during market hours. We register the job unconditionally (cheap when
# no watch-trigger workflows are active) and short-circuit inside the
# job when the market is closed.
_WATCHER_INTERVAL_SECONDS = 60
_WATCHER_JOB_ID = "pivot_workflows_watcher"

# IPO-open watcher cadence. IPO subscription windows move on a multi-hour
# cadence, not minutes — 30 minutes is fast enough to fire close to the
# real open time and slow enough to be a small fraction of the cached
# NSE feed's 45-minute TTL. Crucially this poller is NOT gated on
# market hours: IPO open-status is readable any time of day, and we
# want the trigger to fire even when the user activated the workflow
# overnight.
_IPO_OPEN_WATCHER_INTERVAL_SECONDS = 1800
_IPO_OPEN_WATCHER_JOB_ID = "pivot_workflows_ipo_open_watcher"

# IPO listing-credit poll cadence (P3.1). The poller is the bridge
# between an allotted PaperIpoAllocation and the paper book: when
# listing_date arrives we credit the allotted shares as a paper BUY at
# the issue price. Daily resolution is sufficient — listing happens once
# per IPO and we don't need minute-level precision. 1 hour is a
# defensive cadence that catches a same-day deploy / restart cleanly
# without hammering the DB. Like the IPO-open watcher, NOT gated on
# market hours: the credit is a paper-book write, no live feed needed.
_IPO_LISTING_CREDIT_INTERVAL_SECONDS = 3600
_IPO_LISTING_CREDIT_JOB_ID = "pivot_workflows_ipo_listing_credit"

# Scheduled-macro watcher cadence. Macro releases (RBI MPC ~10:00 IST,
# FOMC ~00:30 IST, CPI prints ~18:00/00:30 IST) fire on KNOWN dates and
# their verify windows span hours, so a 5-minute poll fires close to the
# release without hammering the official feeds. Like the IPO watcher,
# this is NOT gated on NSE market hours — FOMC / US-CPI land when the
# Indian market is closed and MUST still fire.
_MACRO_WATCHER_INTERVAL_SECONDS = 300
_MACRO_WATCHER_JOB_ID = "pivot_workflows_macro_watcher"

# Global-price watcher (trigger.global_price) cadence. Crypto / forex /
# global-commodity quotes come from public APIs OUTSIDE Kite (Kraken,
# CoinGecko, Twelve Data, Frankfurter, yfinance futures). The default
# poll interval is 60s but is settings-tunable via
# settings.global_price_poll_seconds so a deployer can dial it down to
# respect provider rate limits. Like the IPO / macro watchers, this is
# NOT gated on NSE market hours — crypto is 24/7 and forex sessions
# span the Indian overnight.
_GLOBAL_PRICE_WATCHER_JOB_ID = "pivot_workflows_global_price_watcher"

# Earnings watcher (trigger.earnings) cadence. Company earnings prints
# land at known dates with a multi-hour verify window (default 48h),
# so a 30-minute poll fires close to the release without hammering
# yfinance. NOT gated on NSE market hours: US ADR earnings (and the
# subsequent estimate update) can land overnight.
_EARNINGS_WATCHER_INTERVAL_SECONDS = 1800
_EARNINGS_WATCHER_JOB_ID = "pivot_workflows_earnings_watcher"

# APScheduler job id for the workflow poll job — keep stable across
# restarts so `replace_existing=True` works.
_POLL_JOB_ID = "pivot_workflows_poll"


class InvalidCronError(ValueError):
    """Raised when a `trigger.schedule` cron expression is malformed.

    Routers catch this and emit 422 with `validation_error` code.
    """


def _trigger_schedule_steps(workflow: Workflow) -> list[WorkflowStep]:
    """Return EVERY scheduled trigger step in the workflow.

    Includes both ``trigger.schedule`` (raw cron) and
    ``trigger.market_relative_time`` (anchor-relative). The latter is
    resolved to a concrete cron via `_resolve_market_relative_time` at
    arming time, then treated identically by the poller.

    Multi-trigger workflows can have several. Pre-multi-trigger
    workflows have either one (at index 0) or none.
    """
    return [
        step for step in workflow.steps
        if str(step.step_type) in {
            "trigger.schedule", "trigger.market_relative_time",
        }
    ]


# NSE regular session, IST. Hardcoded — these have been stable for years
# and a config knob would just invite mistakes. Special-day overrides
# (Diwali muhurat, etc.) come from the holiday calendar.
_NSE_OPEN_IST = "09:15"
_NSE_CLOSE_IST = "15:30"
_NSE_PRE_OPEN_IST = "09:00"
_NSE_POST_CLOSE_IST = "16:00"

_DOW_TO_CRON: dict[str, str] = {
    "monday": "1", "tuesday": "2", "wednesday": "3",
    "thursday": "4", "friday": "5",
}


def _resolve_market_relative_time(cfg: dict[str, object]) -> tuple[str, str]:
    """Convert a `trigger.market_relative_time` config to a
    (cron_expression, timezone) tuple the rest of the scheduler can
    treat identically to a raw `trigger.schedule`.

    `cfg` matches `TriggerMarketRelativeTimeConfig`:
      anchor:         'open' | 'close' | 'pre_open' | 'post_close'
      offset_minutes: int (signed; -5 = 5min before)
      days:           list of day names or ['weekday']
      timezone:       IANA tz, default Asia/Kolkata

    Raises `InvalidCronError` for unknown anchors / day names so the
    422 path bubbles cleanly.
    """
    anchor_raw = str(cfg.get("anchor", "")).lower()
    anchor_clock = {
        "open": _NSE_OPEN_IST,
        "close": _NSE_CLOSE_IST,
        "pre_open": _NSE_PRE_OPEN_IST,
        "post_close": _NSE_POST_CLOSE_IST,
    }.get(anchor_raw)
    if anchor_clock is None:
        raise InvalidCronError(
            f"trigger.market_relative_time: unknown anchor "
            f"{anchor_raw!r} (expected open/close/pre_open/post_close)"
        )

    try:
        offset_min = int(cfg.get("offset_minutes", 0))
    except (TypeError, ValueError) as e:
        raise InvalidCronError(
            f"trigger.market_relative_time: offset_minutes must be int"
        ) from e

    # Add the signed offset to the anchor wall-clock to land on the
    # actual fire time. Stay in 24h arithmetic so 09:15 + (-30) = 08:45,
    # 15:30 + 30 = 16:00, etc.
    h, m = map(int, anchor_clock.split(":"))
    total = h * 60 + m + offset_min
    if total < 0 or total >= 24 * 60:
        raise InvalidCronError(
            f"trigger.market_relative_time: offset {offset_min} pushes "
            f"fire time out of the 24h day from anchor {anchor_raw}"
        )
    fire_h, fire_m = divmod(total, 60)

    # Build the day-of-week cron field. 'weekday' shorthand expands to
    # 1-5 (mon-fri). Otherwise we union the listed days.
    days_raw = cfg.get("days") or ["weekday"]
    if not isinstance(days_raw, list):
        raise InvalidCronError(
            "trigger.market_relative_time: days must be a list"
        )
    if any(str(d).lower() == "weekday" for d in days_raw):
        dow_field = "1-5"
    else:
        nums = []
        for d in days_raw:
            num = _DOW_TO_CRON.get(str(d).lower())
            if num is None:
                raise InvalidCronError(
                    f"trigger.market_relative_time: unknown day {d!r}"
                )
            nums.append(num)
        dow_field = ",".join(sorted(set(nums)))

    cron = f"{fire_m} {fire_h} * * {dow_field}"
    tz = str(cfg.get("timezone", "Asia/Kolkata"))
    return cron, tz


def compute_next_run_at(
    cron: str,
    tz_str: str,
    *,
    after: Optional[datetime] = None,
) -> datetime:
    """Compute the next fire time for a cron expression in the given
    IANA timezone, returned as a UTC-aware datetime.

    Raises `InvalidCronError` if either the cron or timezone is bad.
    """
    try:
        tz = pytz_timezone(tz_str)
    except Exception as e:  # pytz.UnknownTimeZoneError, etc.
        raise InvalidCronError(f"unknown timezone: {tz_str}") from e
    try:
        trigger = CronTrigger.from_crontab(cron, timezone=tz)
    except Exception as e:  # ValueError on malformed cron
        raise InvalidCronError(f"invalid cron expression: {cron}") from e
    base = after or datetime.now(timezone.utc)
    # APScheduler returns the next fire time strictly *after* `base`.
    next_fire = trigger.get_next_fire_time(None, base.astimezone(tz))
    if next_fire is None:
        raise InvalidCronError(
            f"cron {cron!r} produces no future fire time"
        )
    utc: datetime = next_fire.astimezone(timezone.utc)
    return utc


def upsert_workflow_schedule(db: Session, workflow: Workflow) -> None:
    """Recompute per-step ``next_run_at`` for every ``trigger.schedule``
    step, plus the workflow-level ``next_run_at`` summary.

    Called by the workflows router on activate / pause / archive /
    PATCH-with-steps. Caller is responsible for ``db.commit()``.

    Behavior:
      - Status != active → clear every step's ``next_run_at`` plus
        the workflow's. Poller skips both.
      - Status == active → for each ``trigger.schedule`` step,
        compute ``next_run_at`` from its cron config. The
        workflow-level ``next_run_at`` is set to the EARLIEST of
        the per-step values (or None if no schedule triggers); used
        by the workflows list endpoint as a "next fire" summary.
      - Raises ``InvalidCronError`` if any cron is bad — caller lets
        it bubble so the router emits 422.
    """
    schedule_steps = _trigger_schedule_steps(workflow)

    if workflow.status != WorkflowStatus.active:
        for step in schedule_steps:
            step.next_run_at = None  # type: ignore[assignment]
        workflow.next_run_at = None  # type: ignore[assignment]
        return

    if not schedule_steps:
        # Active but no scheduled triggers — manual / webhook / price /
        # indicator workflow. Nothing for the poller to do.
        workflow.next_run_at = None  # type: ignore[assignment]
        return

    earliest: Optional[datetime] = None
    for step in schedule_steps:
        raw_cfg: dict[str, object] = step.config or {}  # type: ignore[assignment]
        cfg: dict[str, object] = dict(raw_cfg) if raw_cfg else {}
        # Two shapes flow through this loop today:
        #   trigger.schedule              → cfg has {cron, timezone}
        #   trigger.market_relative_time  → cfg has {anchor, offset_minutes,
        #                                            days, timezone}, which
        #                                   we resolve to the same
        #                                   (cron, tz) pair.
        if str(step.step_type) == "trigger.market_relative_time":
            cron, tz_str = _resolve_market_relative_time(cfg)
        else:
            cron = str(cfg.get("cron", ""))
            tz_str = str(cfg.get("timezone", "UTC"))
        nra = compute_next_run_at(cron, tz_str)
        step.next_run_at = nra  # type: ignore[assignment]
        if earliest is None or nra < earliest:
            earliest = nra
    workflow.next_run_at = earliest  # type: ignore[assignment]


async def _poll_due_workflows() -> None:
    """Polled job: find every (workflow, trigger.schedule step) pair
    whose ``WorkflowStep.next_run_at`` has passed, fire one run per
    pair with ``triggered_step_index`` set so the engine knows which
    branch to execute.

    Multi-trigger note: the poll keys off ``WorkflowStep.next_run_at``
    rather than ``Workflow.next_run_at`` so a workflow with two
    schedules at different times fires twice (one run per branch).

    All DB work via sync sessions inside ``asyncio.to_thread()`` so the
    APScheduler loop never blocks on I/O.
    """
    fired_at = datetime.now(timezone.utc)

    def _fetch_due() -> list[tuple[str, int]]:
        """Returns (workflow_id, step_index) pairs to fire. Runs in a
        worker thread.

        R4b: also deactivates any workflow whose ``expires_at`` has
        passed before harvesting due triggers, so an expired strategy
        never fires its next tick."""
        from sqlalchemy import or_
        db = SessionLocal()
        try:
            # Auto-deactivate expired workflows. Single update keeps
            # this cheap; the next poll cycle no longer sees them
            # because of the status filter below.
            expired = (
                db.query(Workflow)
                .filter(
                    Workflow.status == WorkflowStatus.active,
                    Workflow.expires_at.isnot(None),
                    Workflow.expires_at <= fired_at,
                )
                .all()
            )
            if expired:
                for wf in expired:
                    wf.status = WorkflowStatus.paused  # type: ignore[assignment]
                    wf.next_run_at = None  # type: ignore[assignment]
                    for st in wf.steps:
                        st.next_run_at = None  # type: ignore[assignment]
                db.commit()

            rows = (
                db.query(Workflow, WorkflowStep)
                .join(WorkflowStep, WorkflowStep.workflow_id == Workflow.id)
                .filter(
                    Workflow.status == WorkflowStatus.active,
                    or_(
                        Workflow.expires_at.is_(None),
                        Workflow.expires_at > fired_at,
                    ),
                    WorkflowStep.step_type.in_(
                        ["trigger.schedule", "trigger.market_relative_time"],
                    ),
                    WorkflowStep.next_run_at.isnot(None),
                    WorkflowStep.next_run_at <= fired_at,
                )
                .all()
            )
            return [(str(wf.id), int(st.step_index)) for wf, st in rows]
        finally:
            db.close()

    pairs = await asyncio.to_thread(_fetch_due)
    if not pairs:
        return

    logger.info(
        "[workflow-scheduler] firing %d due trigger(s) at %s",
        len(pairs),
        fired_at.isoformat(),
    )

    for wf_id, step_index in pairs:
        try:
            await _fire_one(wf_id, step_index, fired_at)
        except Exception:
            # Don't let one bad workflow kill the poll cycle.
            logger.exception(
                "[workflow-scheduler] failed to fire workflow %s step %d",
                wf_id, step_index,
            )


async def _fire_one(
    workflow_id: str, triggered_step_index: int, fired_at: datetime,
) -> None:
    """Create a scheduled run row tied to the firing trigger step,
    advance that step's ``next_run_at`` to the next cron tick, and
    hand the run to the engine.

    All DB work via to_thread; engine is async.
    """

    def _create_run_and_recompute() -> Optional[str]:
        """Returns the new run_id, or None if the workflow vanished /
        was paused between fetch and fire."""
        db = SessionLocal()
        try:
            wf = db.query(Workflow).filter(Workflow.id == workflow_id).first()
            if wf is None or wf.status != WorkflowStatus.active:
                return None
            # R4b: race-safe expiry check (the polling sweep already
            # deactivates, but a fire can fall between sweeps).
            if (
                getattr(wf, "expires_at", None) is not None
                and wf.expires_at <= fired_at  # type: ignore[operator]
            ):
                wf.status = WorkflowStatus.paused  # type: ignore[assignment]
                wf.next_run_at = None  # type: ignore[assignment]
                for st in wf.steps:
                    st.next_run_at = None  # type: ignore[assignment]
                db.commit()
                return None
            run = WorkflowRun(
                workflow_id=wf.id,
                workflow_version=int(wf.version),
                triggered_by="schedule",
                triggered_step_index=triggered_step_index,
                status=RunStatus.running,
                context={},
            )
            db.add(run)
            wf.last_run_at = fired_at  # type: ignore[assignment]
            # Advance EVERY trigger.schedule step so the workflow-level
            # next_run_at summary stays accurate. Cheap (handful of
            # steps per workflow). Falls back to clearing on bad cron.
            try:
                upsert_workflow_schedule(db, wf)
            except InvalidCronError:
                wf.next_run_at = None  # type: ignore[assignment]
                for st in _trigger_schedule_steps(wf):
                    st.next_run_at = None  # type: ignore[assignment]
            db.commit()
            db.refresh(run)
            return str(run.id)
        finally:
            db.close()

    run_id = await asyncio.to_thread(_create_run_and_recompute)
    if run_id is None:
        return

    # Engine is async; run it as a fire-and-forget task on the loop.
    # Imported lazily to avoid circular import at module load.
    from backend.workflows.engine import WorkflowEngine

    engine = WorkflowEngine()
    asyncio.create_task(engine.execute_run(run_id))


def register_workflow_scheduler(scheduler: AsyncIOScheduler) -> None:
    """Attach the workflow poll + watcher jobs to the existing scheduler.

    Idempotent: re-registering replaces the existing jobs.
    """
    scheduler.add_job(
        _poll_due_workflows,
        trigger="interval",
        seconds=_POLL_INTERVAL_SECONDS,
        id=_POLL_JOB_ID,
        name="Pivot Workflows — poll due schedules",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        _poll_watch_triggers,
        trigger="interval",
        seconds=_WATCHER_INTERVAL_SECONDS,
        id=_WATCHER_JOB_ID,
        name="Pivot Workflows — price / indicator watcher",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        _poll_ipo_open_triggers,
        trigger="interval",
        seconds=_IPO_OPEN_WATCHER_INTERVAL_SECONDS,
        id=_IPO_OPEN_WATCHER_JOB_ID,
        name="Pivot Workflows — IPO open watcher",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        _poll_ipo_listing_fills,
        trigger="interval",
        seconds=_IPO_LISTING_CREDIT_INTERVAL_SECONDS,
        id=_IPO_LISTING_CREDIT_JOB_ID,
        name="Pivot Workflows — IPO listing-credit",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )

    # Scheduled-macro watcher — only when the feature flag is on. Gated at
    # registration (not self-gated inside) so the job doesn't even exist
    # when disabled; tests call `_poll_scheduled_macro_triggers` directly.
    from backend.config import settings as _settings
    macro_on = bool(getattr(_settings, "macro_events_enabled", False))
    if macro_on:
        scheduler.add_job(
            _poll_scheduled_macro_triggers,
            trigger="interval",
            seconds=_MACRO_WATCHER_INTERVAL_SECONDS,
            id=_MACRO_WATCHER_JOB_ID,
            name="Pivot Workflows — scheduled-macro watcher",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )

    # Global-price watcher (trigger.global_price) — only when the feature
    # flag is on. Same registration-time gating pattern as the macro
    # watcher: tests call `_poll_global_price_triggers` directly.
    global_price_on = bool(
        getattr(_settings, "global_price_triggers_enabled", False)
    )
    global_price_seconds = int(
        getattr(_settings, "global_price_poll_seconds", 60) or 60
    )
    if global_price_on:
        scheduler.add_job(
            _poll_global_price_triggers,
            trigger="interval",
            seconds=global_price_seconds,
            id=_GLOBAL_PRICE_WATCHER_JOB_ID,
            name="Pivot Workflows — global-price watcher",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )

    # Earnings watcher (trigger.earnings) — feature-flag gated, mirrors
    # the macro watcher's per-occurrence latch pattern.
    earnings_on = bool(getattr(_settings, "earnings_events_enabled", False))
    if earnings_on:
        scheduler.add_job(
            _poll_earnings_triggers,
            trigger="interval",
            seconds=_EARNINGS_WATCHER_INTERVAL_SECONDS,
            id=_EARNINGS_WATCHER_JOB_ID,
            name="Pivot Workflows — earnings watcher",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )
    logger.info(
        "[workflow-scheduler] registered poll job (%ss) + watcher (%ss) "
        "+ ipo-open watcher (%ss) + ipo-listing-credit (%ss)%s%s%s",
        _POLL_INTERVAL_SECONDS, _WATCHER_INTERVAL_SECONDS,
        _IPO_OPEN_WATCHER_INTERVAL_SECONDS,
        _IPO_LISTING_CREDIT_INTERVAL_SECONDS,
        f" + macro watcher ({_MACRO_WATCHER_INTERVAL_SECONDS}s)" if macro_on else "",
        f" + global-price watcher ({global_price_seconds}s)" if global_price_on else "",
        f" + earnings watcher ({_EARNINGS_WATCHER_INTERVAL_SECONDS}s)" if earnings_on else "",
    )


# ── Price / indicator watcher ────────────────────────────────────────


# Price/indicator triggers store last_price under this key inside
# workflow_steps.config so the watcher can detect crossings on the
# next tick. Stored as a JSON-friendly float; absent on the first tick.
_LAST_PRICE_KEY = "_last_price"
_LAST_VALUE_KEY = "_last_value"  # for indicator triggers
_LAST_FIRED_EVENT_KEY = "_last_fired_event_guid"  # dedup for trigger.event
_RBI_RSS_SOURCE_ID = "rbi_press_releases"
_EVENT_GENERIC_ORG_TOKENS = frozenset({
    "rbi", "reserve bank", "reserve bank of india",
})


async def _poll_watch_triggers() -> None:
    """Polled every 60s. During NSE market hours, scans active workflows
    whose first step is `trigger.price` or `trigger.indicator`,
    batch-fetches the relevant quotes / computes indicators, and fires
    runs when conditions match.

    Crossing semantics (`crosses_above` / `crosses_below`) require
    knowing the previous tick's value — we persist it under
    `workflow_steps.config[_last_price]` (or `_last_value`) so the
    next tick can compare.
    """
    from backend.utils.time_utils import is_market_open, is_trading_day

    # Out of market hours / weekend → cheap no-op.
    try:
        if not (is_trading_day() and is_market_open()):
            return
    except Exception:  # pragma: no cover — defensive
        return

    fired_at = datetime.now(timezone.utc)

    def _scan_active_watch_triggers() -> list[tuple[str, int, str, dict[str, object]]]:
        """Returns list of (workflow_id, step_index, step_type,
        config_copy) tuples. Runs in a worker thread.

        Multi-trigger: scans EVERY trigger.price / trigger.indicator
        step, not just step 0, so a workflow's third trigger can also
        fire its own branch.
        """
        db = SessionLocal()
        try:
            rows = (
                db.query(Workflow, WorkflowStep)
                .join(WorkflowStep, WorkflowStep.workflow_id == Workflow.id)
                .filter(
                    Workflow.status == WorkflowStatus.active,
                    WorkflowStep.step_type.in_(
                        [
                            "trigger.price",
                            "trigger.indicator",
                            # Phase-D5: compound (DSL-tree) triggers
                            # evaluated via backend/workflows/dsl/.
                            "trigger.compound",
                            # Exit-tree triggers — same DSL evaluator
                            # but only fires when the workflow has an
                            # open position from a prior entry fire.
                            "trigger.exit_compound",
                            # RBI-autofire: news-event triggers fetch the
                            # RBI RSS feed and keyword-match per tick.
                            "trigger.event",
                            # F&O P3: expiry-day trigger — DTE from
                            # the instrument master, per-expiry
                            # fire-once latch.
                            "trigger.expiry_day",
                        ],
                    ),
                )
                .all()
            )
            return [
                (
                    str(wf.id),
                    int(step.step_index),
                    str(step.step_type),
                    dict(step.config or {}),
                )
                for wf, step in rows
            ]
        finally:
            db.close()

    triggers = await asyncio.to_thread(_scan_active_watch_triggers)
    if not triggers:
        return

    # Group price triggers by symbol so we batch-fetch quotes once per
    # tick instead of once per workflow.
    price_symbols: set[str] = set()
    for _wf_id, _step_idx, step_type, cfg in triggers:
        if step_type == "trigger.price":
            sym = str(cfg.get("symbol", "")).upper()
            exch = str(cfg.get("exchange", "NSE")).upper()
            if sym:
                price_symbols.add(f"{exch}:{sym}")

    quotes: dict[str, float] = {}
    if price_symbols:
        try:
            quotes = await asyncio.to_thread(_batch_fetch_prices, sorted(price_symbols))
        except Exception:
            logger.exception("[watcher] price batch fetch failed")
            quotes = {}

    for wf_id, step_idx, step_type, cfg in triggers:
        try:
            if step_type == "trigger.price":
                await _evaluate_price_trigger(wf_id, step_idx, cfg, quotes, fired_at)
            elif step_type == "trigger.indicator":
                await _evaluate_indicator_trigger(wf_id, step_idx, cfg, fired_at)
            elif step_type == "trigger.compound":
                await _evaluate_compound_trigger(wf_id, step_idx, cfg, fired_at)
            elif step_type == "trigger.exit_compound":
                await _evaluate_exit_compound_trigger(wf_id, step_idx, cfg, fired_at)
            elif step_type == "trigger.event":
                await _evaluate_event_trigger(wf_id, step_idx, cfg, fired_at)
            elif step_type == "trigger.expiry_day":
                await _evaluate_expiry_day_trigger(wf_id, step_idx, cfg, fired_at)
        except Exception:
            logger.exception(
                "[watcher] failed to evaluate %s for workflow %s step %d",
                step_type, wf_id, step_idx,
            )


async def _evaluate_expiry_day_trigger(
    workflow_id: str,
    step_index: int,
    cfg: dict[str, object],
    fired_at: datetime,
) -> None:
    """F&O P3: fire once on the morning of the underlying's option
    expiry day. DTE comes from the instrument master via the chain
    service (never a hardcoded weekday — exchanges reshuffled expiry
    days in 2025). Fire-once latch: ``_expiry_day_fired_for`` persists
    the expiry ISO the trigger last fired for, so it re-arms
    automatically for the NEXT expiry."""
    underlying = str(cfg.get("underlying", "")).strip().upper()
    if not underlying:
        return
    expiry_rule = str(cfg.get("expiry_rule", "nearest"))

    def _check_sync() -> tuple[bool, str]:
        from backend.database import SessionLocal
        from backend.market.instrument_master import list_expiries
        from backend.market.option_metrics import compute_dte

        db = SessionLocal()
        try:
            expiries = list_expiries(db, underlying)
            if not expiries:
                return False, ""
            if expiry_rule == "monthly":
                target = next(
                    (e["expiry"] for e in expiries if e["kind"] == "monthly"),
                    expiries[-1]["expiry"],
                )
            else:
                target = expiries[0]["expiry"]
            dte = compute_dte(db, underlying, expiry_rule=expiry_rule)
            # Expiry-day morning: DTE counts to the session close, so
            # the whole expiry day reads 0 < dte <= 1 (and the trigger
            # window opens at the 09:15 watcher tick).
            return (dte is not None and dte <= 1.0), target
        finally:
            db.close()

    try:
        is_expiry_day, target_expiry = await asyncio.to_thread(_check_sync)
    except Exception:
        logger.exception(
            "[watcher.expiry_day] check failed for %s", underlying,
        )
        return
    if not is_expiry_day or not target_expiry:
        return
    if str(cfg.get("_expiry_day_fired_for") or "") == target_expiry:
        return  # already fired for this expiry

    # Latch BEFORE firing (crash safety — same order the IPO-open
    # watcher uses) so a crash after the run insert can't double-fire.
    await asyncio.to_thread(
        _persist_last_value,
        workflow_id, step_index, "_expiry_day_fired_for", target_expiry,
    )
    logger.info(
        "[watcher.expiry_day] %s expiry %s — firing workflow %s",
        underlying, target_expiry, workflow_id,
    )
    await _fire_watch_run(workflow_id, step_index, "event_alert", fired_at)


def _resolve_market_token() -> str:
    """Return a usable Kite access token for market quotes, or the
    ``"mock_token"`` shim.

    The scheduler is system-wide and market quotes are not user-specific,
    so ANY active KiteSession token works. Previously this passed a
    hardcoded ``"mock_token"`` — fine in mock mode, but once real Kite
    credentials + a live session exist (mock mode OFF), Kite rejected it
    with ``TokenException`` and every price/indicator trigger silently
    errored. Resolve the most-recently-updated active session instead;
    fall back to ``"mock_token"`` (which get_live_quote serves from the
    mock store when KITE_MOCK_MODE is on)."""
    from backend.kite.auth import KITE_MOCK_MODE, read_kite_access_token
    if KITE_MOCK_MODE:
        return "mock_token"
    try:
        from backend.brokers.sessions import get_active_kite_session
        from backend.database import SessionLocal
        db = SessionLocal()
        try:
            row = get_active_kite_session(db)
            tok = read_kite_access_token(row)
            if tok and not tok.startswith("mock_") and len(tok) >= 20:
                return tok
            return "mock_token"
        finally:
            db.close()
    except Exception:  # noqa: BLE001 — never let token lookup crash a job
        return "mock_token"


def _batch_fetch_prices(instruments: list[str]) -> dict[str, float]:
    """Fetch live quotes for a batch of instruments. Resolves a live
    Kite session token (or the mock shim). Returns {instrument: ltp} for
    every instrument that has a price; returns {} on any fetch error so a
    dead/expired token (e.g. after the 6 AM IST daily expiry) degrades
    gracefully instead of throwing inside the scheduler job."""
    from backend.kite.market_data import get_live_quote

    try:
        raw = get_live_quote(_resolve_market_token(), instruments) or {}
    except Exception as exc:  # noqa: BLE001
        logger.warning("batch price fetch failed (%s): %s",
                       type(exc).__name__, str(exc)[:160])
        return {}
    out: dict[str, float] = {}
    for inst, payload in raw.items():
        if not isinstance(payload, dict):
            continue
        ltp = payload.get("last_price")
        if isinstance(ltp, (int, float)) and float(ltp) > 0:
            out[inst] = float(ltp)
    return out


def _matches_threshold(
    operator: str, current: float, threshold: float, last: Optional[float],
) -> bool:
    """Evaluate a price/indicator threshold operator. `last` is the
    previous tick's value, required for `crosses_*` operators."""
    if operator == ">":
        return current > threshold
    if operator == "<":
        return current < threshold
    if operator == "crosses_above":
        return last is not None and last <= threshold < current
    if operator == "crosses_below":
        return last is not None and last >= threshold > current
    return False


async def _evaluate_price_trigger(
    workflow_id: str,
    step_index: int,
    cfg: dict[str, object],
    quotes: dict[str, float],
    fired_at: datetime,
) -> None:
    sym = str(cfg.get("symbol", "")).upper()
    exch = str(cfg.get("exchange", "NSE")).upper()
    operator = str(cfg.get("operator", ""))
    threshold = float(cfg.get("value", 0.0))  # type: ignore[arg-type]
    instrument = f"{exch}:{sym}"
    current = quotes.get(instrument)
    if current is None:
        return  # no quote available this tick

    last_raw = cfg.get(_LAST_PRICE_KEY)
    last = float(last_raw) if isinstance(last_raw, (int, float)) else None

    matched = _matches_threshold(operator, current, threshold, last)

    # Persist last_price so next tick's crosses_* logic works.
    await asyncio.to_thread(
        _persist_last_value, workflow_id, step_index, _LAST_PRICE_KEY, current,
    )

    if matched:
        await _fire_watch_run(workflow_id, step_index, "price_alert", fired_at)


async def _evaluate_indicator_trigger(
    workflow_id: str,
    step_index: int,
    cfg: dict[str, object],
    fired_at: datetime,
) -> None:
    """Compute the indicator inline and apply the same threshold logic
    as price triggers. This is more expensive than price evaluation
    (yfinance + indicator math) so we do it per-workflow rather than
    batching — N is expected to be small in v1."""
    sym = str(cfg.get("symbol", "")).upper()
    indicator = str(cfg.get("indicator", "")).lower()
    period = int(cfg.get("period", 14))  # type: ignore[call-overload]
    operator = str(cfg.get("operator", ""))
    threshold = float(cfg.get("value", 0.0))  # type: ignore[arg-type]
    # Track C #4: honored timeframe. 'weekly' evaluates the indicator on
    # W-FRI weekly closes (daily series resampled, lookback sized ×5) —
    # the card's timeframe field is REAL, never a silent daily downgrade.
    timeframe = str(cfg.get("timeframe") or "daily").lower()

    try:
        value = await asyncio.to_thread(
            _compute_indicator_sync, sym, indicator, period, timeframe,
        )
    except Exception:
        # Indicator data temporarily unavailable — try again next tick.
        return
    if value is None:
        return

    last_raw = cfg.get(_LAST_VALUE_KEY)
    last = float(last_raw) if isinstance(last_raw, (int, float)) else None
    matched = _matches_threshold(operator, value, threshold, last)

    await asyncio.to_thread(
        _persist_last_value, workflow_id, step_index, _LAST_VALUE_KEY, value,
    )

    if matched:
        await _fire_watch_run(workflow_id, step_index, "indicator_alert", fired_at)


async def _evaluate_compound_trigger(
    workflow_id: str,
    step_index: int,
    cfg: dict[str, object],
    fired_at: datetime,
) -> None:
    """Phase-D6: walk the DSL tree on the step's config; fire when
    it returns Ternary.TRUE.

    State plumbing for ``crosses_above`` / ``crosses_below`` lives in
    ``cfg["_last_values"]`` — the watcher reads it before walking,
    writes it back after. Same persistence pattern as
    ``_evaluate_indicator_trigger``'s ``_LAST_VALUE_KEY``.

    Data accessor + tree evaluator are both wrapped in
    ``asyncio.to_thread`` because the evaluator's leaf calls
    (yfinance, indicator math) are blocking — same convention the
    existing price/indicator branches use.
    """
    entry_raw = cfg.get("entry")
    if not isinstance(entry_raw, dict):
        # Config invalid; the registry validator would have caught
        # this on activate, but be defensive.
        return

    last_values_raw = cfg.get("_last_values")
    prev_state: dict[str, float] = (
        {k: float(v) for k, v in last_values_raw.items()
         if isinstance(v, (int, float))}
        if isinstance(last_values_raw, dict) else {}
    )

    def _evaluate_sync() -> tuple[bool, dict[str, float]]:
        # Lazy imports keep watcher startup cheap.
        from pydantic import TypeAdapter
        from backend.workflows.dsl.data_accessor import LiveDataAccessor
        from backend.workflows.dsl.evaluator import Ternary, evaluate
        from backend.workflows.dsl.schema import Tree

        try:
            tree = TypeAdapter(Tree).validate_python(entry_raw)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "[watcher.compound] step %d workflow %s — tree parse failed: %s",
                step_index, workflow_id, exc,
            )
            return False, prev_state

        result = evaluate(tree, accessor=LiveDataAccessor(), prev_state=prev_state)
        fired = result.value is Ternary.TRUE
        return fired, result.new_state

    try:
        matched, new_state = await asyncio.to_thread(_evaluate_sync)
    except Exception:
        logger.exception(
            "[watcher.compound] eval crashed; step %d workflow %s",
            step_index, workflow_id,
        )
        return

    # Persist updated state so the next tick sees the previous values.
    # Same dance as price/indicator triggers — single key, but the
    # value is a dict instead of a scalar.
    if new_state != prev_state:
        await asyncio.to_thread(
            _persist_last_value,
            workflow_id, step_index, "_last_values", new_state,
        )

    if matched:
        # Pick the most-specific triggered_by value we can. The CHECK
        # constraint allows price_alert / indicator_alert / event_alert;
        # compound triggers most resemble indicator_alert (heavy use
        # of indicator nodes), so use that. A future schema rev could
        # add 'compound_alert' if the audit distinction matters.
        await _fire_watch_run(
            workflow_id, step_index, "indicator_alert", fired_at,
        )


class _OpenPosition:
    """A minimal in-memory snapshot of one open position, threaded into
    a position-aware DataAccessor so the DSL evaluator's PositionNode
    leaves resolve to real numbers.

    The watcher builds this from the workflow's run history: the most
    recent successful ``action.place_order`` (buy side) on
    ``target_symbol`` that is NOT followed by a closing sell.
    """

    __slots__ = ("symbol", "entry_price", "fill_ts", "peak_close")

    def __init__(
        self, symbol: str, entry_price: float,
        fill_ts: datetime, peak_close: Optional[float] = None,
    ) -> None:
        self.symbol = symbol.upper()
        self.entry_price = float(entry_price)
        self.fill_ts = fill_ts
        self.peak_close = float(peak_close) if peak_close is not None else None


class _PositionAwareAccessor:
    """Wraps a LiveDataAccessor and answers PositionNode reads against
    a single open position. Every market-data method delegates to the
    inner accessor; only ``get_position_field`` is overridden."""

    def __init__(self, inner, position: _OpenPosition) -> None:
        self._inner = inner
        self._position = position
        # bars_held is computed once per accessor lifetime to avoid
        # walking the calendar on every leaf access inside the same
        # tree walk.
        self._bars_held_cache: Optional[int] = None

    # Delegate everything market-data-shaped.
    def get_price(self, **kw):
        return self._inner.get_price(**kw)

    def get_indicator(self, **kw):
        return self._inner.get_indicator(**kw)

    def get_volume(self, **kw):
        return self._inner.get_volume(**kw)

    def get_session_day(self):
        return self._inner.get_session_day()

    def evaluate_aggregate(self, *a, **kw):
        if hasattr(self._inner, "evaluate_aggregate"):
            return self._inner.evaluate_aggregate(*a, **kw)
        return None

    # The override that matters.
    def get_position_field(
        self, *, field: str, basis: Optional[str] = None,
    ) -> Optional[float]:
        p = self._position
        if field == "entry_price":
            return p.entry_price
        if field in ("unrealised_pct", "unrealised_abs"):
            current = self._current_price(basis)
            if current is None or p.entry_price == 0.0:
                return None
            if field == "unrealised_abs":
                return current - p.entry_price
            return (current - p.entry_price) / p.entry_price
        if field == "bars_held":
            return float(self._bars_held())
        if field == "peak_unrealised_pct":
            peak = p.peak_close
            if peak is None or p.entry_price == 0.0:
                return None
            return (peak - p.entry_price) / p.entry_price
        if field == "drawdown_from_peak_pct":
            peak = p.peak_close
            current = self._current_price(basis="close")
            if peak is None or current is None or p.entry_price == 0.0:
                return None
            peak_pct = (peak - p.entry_price) / p.entry_price
            cur_pct = (current - p.entry_price) / p.entry_price
            dd = peak_pct - cur_pct
            return max(0.0, dd)
        return None

    def _current_price(self, basis: Optional[str]) -> Optional[float]:
        b = (basis or "close").lower()
        return self._inner.get_price(
            symbol=self._position.symbol, basis=b, offset=0,
        )

    def _bars_held(self) -> int:
        if self._bars_held_cache is not None:
            return self._bars_held_cache
        # Naive: count weekdays between fill_ts and now. Good enough for
        # daily-bar workflows. A future revision can swap in the real
        # NSE trading calendar.
        now = datetime.now(timezone.utc)
        fill = self._position.fill_ts
        if fill.tzinfo is None:
            fill = fill.replace(tzinfo=timezone.utc)
        delta_days = max(0, (now.date() - fill.date()).days)
        bars = 0
        for d in range(delta_days + 1):
            from datetime import timedelta
            day = (fill.date() + timedelta(days=d))
            if day.weekday() < 5:  # Mon..Fri
                bars += 1
        # bars_held = number of completed bars AFTER entry, so subtract 1
        # for the entry bar itself.
        self._bars_held_cache = max(0, bars - 1)
        return self._bars_held_cache


def _resolve_open_position(
    workflow_id: str, target_symbol: Optional[str],
) -> Optional[_OpenPosition]:
    """Walk the workflow's run history (most recent first) for the
    latest buy-side ``action.place_order`` whose symbol has NOT been
    closed by a subsequent sell of equal-or-greater quantity.

    Returns None when no open position is found — the exit-tree
    trigger then no-ops for this tick. Cheap query: bounded by
    ``_OPEN_POSITION_RUN_LIMIT`` recent runs (defaults to 50)."""
    from backend.models import StepStatus, WorkflowRunStep

    target = target_symbol.upper().strip() if target_symbol else None

    db = SessionLocal()
    try:
        # Pull recent run-step rows for action.place_order in this
        # workflow. Order by run finish desc so we see latest fills
        # first. Limit is a sanity cap; v1 workflows rarely run >50
        # times in the lookback window.
        rows = (
            db.query(WorkflowRunStep, WorkflowRun)
            .join(WorkflowRun, WorkflowRunStep.run_id == WorkflowRun.id)
            .filter(
                WorkflowRun.workflow_id == workflow_id,
                WorkflowRunStep.step_type == "action.place_order",
                WorkflowRunStep.status == StepStatus.succeeded,
            )
            .order_by(WorkflowRun.started_at.desc())
            .limit(_OPEN_POSITION_RUN_LIMIT)
            .all()
        )

        # Track net signed quantity per symbol across the recent runs.
        # First scan: latest-buy-not-yet-fully-closed-by-sells.
        net_qty: dict[str, int] = {}
        latest_buy: dict[str, tuple[float, datetime]] = {}
        peak_close: dict[str, float] = {}
        for step, run in rows:
            out = step.output or {}
            if not isinstance(out, dict):
                continue
            sym = str(out.get("symbol") or "").upper()
            if not sym:
                continue
            if target and sym != target:
                continue
            qty_raw = out.get("quantity") or out.get("filled_quantity") or 0
            try:
                qty = int(qty_raw)
            except (TypeError, ValueError):
                continue
            side = str(out.get("side") or "").lower()
            signed = qty if side == "buy" else -qty if side == "sell" else 0
            if signed == 0:
                continue
            net_qty[sym] = net_qty.get(sym, 0) + signed
            if signed > 0 and sym not in latest_buy:
                # Record the first buy we see (latest by descending sort).
                fill_price = (
                    out.get("executed_price") or out.get("price") or out.get("fill_price")
                )
                try:
                    fp = float(fill_price) if fill_price is not None else None
                except (TypeError, ValueError):
                    fp = None
                if fp is not None:
                    latest_buy[sym] = (fp, run.started_at)

        # An "open" position has net_qty > 0 AND a recorded buy.
        for sym, qty in net_qty.items():
            if qty <= 0:
                continue
            if sym not in latest_buy:
                continue
            fp, fill_ts = latest_buy[sym]
            return _OpenPosition(
                symbol=sym, entry_price=fp, fill_ts=fill_ts,
                peak_close=peak_close.get(sym),
            )
        return None
    finally:
        db.close()


_OPEN_POSITION_RUN_LIMIT = 50


async def _evaluate_exit_compound_trigger(
    workflow_id: str,
    step_index: int,
    cfg: dict[str, object],
    fired_at: datetime,
) -> None:
    """Watcher path for trigger.exit_compound.

    Pre-check: resolve the workflow's open position via
    ``_resolve_open_position``. No position → no-op (nothing to exit).
    With a position, walk the DSL tree under a
    ``_PositionAwareAccessor`` so ``PositionNode`` leaves resolve to
    real numbers. Same _last_values plumbing as the entry-compound
    path so crosses_above / crosses_below work across ticks.

    Track C #5 (staged scale-out exits): a branch with ``one_shot:
    true`` fires AT MOST ONCE — the ``_exit_branch_fired`` latch is
    persisted on the step config before the run is created, so a
    partial-exit branch ("sell 5 at +3%") can't re-register the same
    sell every tick while the condition stays true. Each staged branch
    carries its own latch; the remaining branches keep evaluating
    against the (now smaller) net position."""
    entry_raw = cfg.get("entry")
    if not isinstance(entry_raw, dict):
        return
    if bool(cfg.get("one_shot")) and str(cfg.get(_EXIT_FIRED_KEY) or ""):
        return  # this staged branch already fired — stay quiet

    target_symbol_raw = cfg.get("target_symbol")
    target_symbol = (
        str(target_symbol_raw).upper().strip()
        if isinstance(target_symbol_raw, str) and target_symbol_raw.strip()
        else None
    )

    position = await asyncio.to_thread(
        _resolve_open_position, workflow_id, target_symbol,
    )
    if position is None:
        return  # Nothing to exit — quietly skip.

    last_values_raw = cfg.get("_last_values")
    prev_state: dict[str, float] = (
        {k: float(v) for k, v in last_values_raw.items()
         if isinstance(v, (int, float))}
        if isinstance(last_values_raw, dict) else {}
    )

    def _evaluate_sync() -> tuple[bool, dict[str, float]]:
        from pydantic import TypeAdapter
        from backend.workflows.dsl.data_accessor import LiveDataAccessor
        from backend.workflows.dsl.evaluator import Ternary, evaluate
        from backend.workflows.dsl.schema import Tree

        try:
            tree = TypeAdapter(Tree).validate_python(entry_raw)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "[watcher.exit_compound] step %d workflow %s — tree parse failed: %s",
                step_index, workflow_id, exc,
            )
            return False, prev_state

        accessor = _PositionAwareAccessor(LiveDataAccessor(), position)
        result = evaluate(tree, accessor=accessor, prev_state=prev_state)
        fired = result.value is Ternary.TRUE
        return fired, result.new_state

    try:
        matched, new_state = await asyncio.to_thread(_evaluate_sync)
    except Exception:
        logger.exception(
            "[watcher.exit_compound] eval crashed; step %d workflow %s",
            step_index, workflow_id,
        )
        return

    if new_state != prev_state:
        await asyncio.to_thread(
            _persist_last_value,
            workflow_id, step_index, "_last_values", new_state,
        )

    if matched:
        if bool(cfg.get("one_shot")):
            # Latch BEFORE firing (crash-safe at-most-once, same order
            # the IPO-open watcher uses).
            await asyncio.to_thread(
                _persist_config_str,
                workflow_id, step_index, _EXIT_FIRED_KEY,
                fired_at.isoformat(),
            )
        await _fire_watch_run(
            workflow_id, step_index, "indicator_alert", fired_at,
        )


# Track C #5: per-branch fire-once latch for staged scale-out exits.
_EXIT_FIRED_KEY = "_exit_branch_fired"


def _persist_config_str(
    workflow_id: str, step_index: int, key: str, value: str,
) -> None:
    """Persist an arbitrary string key on a step's config (copy-and-
    reassign so SQLA tracks the JSON change). Generic sibling of
    ``_persist_event_guid`` for fire-once latches."""
    db = SessionLocal()
    try:
        step = (
            db.query(WorkflowStep)
            .filter(
                WorkflowStep.workflow_id == workflow_id,
                WorkflowStep.step_index == step_index,
            )
            .first()
        )
        if step is None:
            return
        cfg = dict(step.config or {})
        cfg[key] = str(value)
        step.config = cfg  # type: ignore[assignment]
        db.commit()
    finally:
        db.close()


def _compute_indicator_sync(
    symbol: str, indicator: str, period: int, timeframe: str = "daily",
) -> Optional[float]:
    """Sync version of the fetch.indicator computation, suitable for
    the watcher (which runs DB / network in worker threads). Returns
    the latest value or None on insufficient data.

    Delegates to ``backend.services.backtest_indicators`` so the live
    watcher and the backtest engine compute the same scalar for the
    same (indicator, period) pair — adding an indicator anywhere makes
    it instantly fire-able here.

    Track C #4: ``timeframe='weekly'`` evaluates on W-FRI weekly closes
    (daily series resampled via the shared helper in
    ``dsl.data_accessor``), with the lookback window sized ×5 so a
    period-N weekly indicator has ≥N weekly bars. Intraday timeframes
    (1m/3m/5m/10m/15m/30m/1h) fetch native intraday bars at the
    requested interval (Kite primary, yfinance fallback) — 'period' is
    counted in BARS of the chosen interval (RSI(14, 15m) = 14
    fifteen-minute bars). The 60s watcher recomputes the latest closed
    bar each poll, so intraday triggers evaluate on the most recent
    completed bar of the chosen interval. Insufficient history (weekly
    or intraday) returns None — never a silently-daily value."""
    import pandas as pd  # type: ignore[import-untyped]

    from backend.core.data.intervals import (
        default_period_for, is_intraday, normalize_interval,
    )
    from backend.kite.market_data import get_historical_ohlcv, period_for_indicator
    from backend.services.backtest_indicators import latest_value

    tf = normalize_interval(timeframe)
    intraday = is_intraday(tf)
    # P0 parity: size the window to the indicator period (was hardcoded "6mo",
    # which silently starved any period > ~120, e.g. a 200-EMA, → returned None
    # and the agent never fired live despite backtesting fine). Weekly needs
    # ×5 the daily bars for the same indicator period; intraday fetches the
    # source's full rolling window at the native interval.
    eff_period = int(period or 0) * (5 if tf == "1wk" else 1)
    if intraday:
        bars = get_historical_ohlcv(
            symbol,
            period=default_period_for(tf, has_kite=True),
            interval=tf,
        ) or []
    else:
        bars = get_historical_ohlcv(
            symbol, period=period_for_indicator(eff_period), interval="1d",
        ) or []
    if tf == "1wk":
        from backend.workflows.dsl.data_accessor import (
            resample_daily_bars_to_weekly,
        )
        df = resample_daily_bars_to_weekly(bars)
        if df is None or len(df) < max(int(period or 0) + 5, 20):
            return None
        return latest_value(df, indicator, period)
    if len(bars) < max(int(period or 0) + 5, 20):
        return None
    df = pd.DataFrame(bars)
    return latest_value(df, indicator, period)


def _persist_last_value(
    workflow_id: str, step_index: int, key: str, value,
) -> None:
    """Update the firing step's config with the latest observed value
    so the next tick can detect a crossing. Uses a fresh SessionLocal
    because we're in a worker thread.

    ``value`` accepts:
      - a number (float / int) — for price + indicator triggers'
        ``_last_price`` / ``_last_value`` scalar state, OR
      - a dict[str, float] — for compound triggers' ``_last_values``
        per-comparison crossing state.

    Multi-trigger: writes to the specific step that fired, not just
    step 0 — different triggers in the same workflow keep their
    own state under their own keys.
    """
    db = SessionLocal()
    try:
        step = (
            db.query(WorkflowStep)
            .filter(
                WorkflowStep.workflow_id == workflow_id,
                WorkflowStep.step_index == step_index,
            )
            .first()
        )
        if step is None:
            return
        # JSON column update — copy and reassign so SQLA detects the
        # change (in-place mutation of a JSON dict isn't auto-tracked).
        cfg = dict(step.config or {})
        if isinstance(value, (int, float)):
            cfg[key] = float(value)
        elif isinstance(value, dict):
            # Coerce all keys to str + all values to float so the
            # JSON column stays serialisable across Postgres ↔ SQLite.
            cfg[key] = {
                str(k): float(v) for k, v in value.items()
                if isinstance(v, (int, float))
            }
        else:
            # Unknown shape — drop silently rather than 500 the loop.
            return
        step.config = cfg  # type: ignore[assignment]
        db.commit()
    finally:
        db.close()


async def _fire_watch_run(
    workflow_id: str,
    triggered_step_index: int,
    triggered_by: str,
    fired_at: datetime,
    audit_context: Optional[dict] = None,
) -> Optional[str]:
    """Create the workflow_run row and hand to the engine. Mirrors
    `_fire_one` but with the watch-specific ``triggered_by`` value
    and the firing step's index so the engine runs the right branch.

    ``audit_context`` is an opt-in dict that callers (Phase-5 news
    event firing — see ``fire_external_event`` below) can pass to
    seed ``workflow_run.context["news_event"]``. Default behaviour
    (``None``) is unchanged from before — an empty context dict,
    matching every pre-Phase-5 call site.

    Returns the newly-created ``workflow_run.id`` as a string, or
    None if the workflow was missing / inactive at fire time.
    """

    def _create() -> Optional[str]:
        db = SessionLocal()
        try:
            wf = db.query(Workflow).filter(Workflow.id == workflow_id).first()
            if wf is None or wf.status != WorkflowStatus.active:
                return None
            # R4b: expiry guard — same race-safe check as _fire_one.
            if (
                getattr(wf, "expires_at", None) is not None
                and wf.expires_at <= fired_at  # type: ignore[operator]
            ):
                wf.status = WorkflowStatus.paused  # type: ignore[assignment]
                wf.next_run_at = None  # type: ignore[assignment]
                for st in wf.steps:
                    st.next_run_at = None  # type: ignore[assignment]
                db.commit()
                return None
            context: dict = {}
            if audit_context:
                context["news_event"] = audit_context
            run = WorkflowRun(
                workflow_id=wf.id,
                workflow_version=int(wf.version),
                triggered_by=triggered_by,
                triggered_step_index=triggered_step_index,
                status=RunStatus.running,
                context=context,
            )
            db.add(run)
            wf.last_run_at = fired_at  # type: ignore[assignment]
            db.commit()
            db.refresh(run)
            return str(run.id)
        finally:
            db.close()

    run_id = await asyncio.to_thread(_create)
    if run_id is None:
        return None

    from backend.workflows.engine import WorkflowEngine

    engine = WorkflowEngine()
    asyncio.create_task(engine.execute_run(run_id))
    return run_id


def _persist_event_guid(
    workflow_id: str, step_index: int, guid: str,
) -> None:
    """Persist the last-fired event guid (a string) on the firing step so
    the next tick dedups. _persist_last_value only accepts float/dict, so
    trigger.event needs its own string-capable writer."""
    db = SessionLocal()
    try:
        step = (
            db.query(WorkflowStep)
            .filter(
                WorkflowStep.workflow_id == workflow_id,
                WorkflowStep.step_index == step_index,
            )
            .first()
        )
        if step is None:
            return
        cfg = dict(step.config or {})
        cfg[_LAST_FIRED_EVENT_KEY] = str(guid)
        step.config = cfg  # type: ignore[assignment]
        db.commit()
    finally:
        db.close()


async def _evaluate_event_trigger(
    workflow_id: str,
    step_index: int,
    cfg: dict,
    fired_at: datetime,
) -> None:
    """RBI-autofire: fetch the RBI press-release RSS feed and fire when a
    real rate-decision headline matches the step's keywords.

    Detection uses the live RBI RSS adapter (NOT the keyless NewsAPI path
    in execute_fetch_news) so it works with NEWSAPI_KEY empty and
    news_events_enabled=False — the adapter/registry import fine with the
    master flag off; that flag only gates the news_events router/jobs.

    Specificity guard: a bare org-name token ("RBI") alone never fires —
    at least one specific policy keyword (repo rate / MPC / rate cut / ...)
    must hit. Verified against the live feed: 0/10 false fires on today's
    money-market / penalty / annual-report noise, fires on a real
    rate-cut headline.

    Dedup: the fired item's guid/url is persisted under
    _LAST_FIRED_EVENT_KEY so a press release that stays in the feed for
    many ticks fires exactly once.
    """
    keywords_raw = cfg.get("keywords") or []
    if not isinstance(keywords_raw, list):
        return
    keywords = [str(k) for k in keywords_raw if isinstance(k, str) and k.strip()]
    if not keywords:
        return

    # Lazy imports keep watcher startup cheap and avoid a hard dependency
    # on the news_events package at module load.
    try:
        from backend.news_events.config import get_source
        from backend.news_events.sources.rss import RSSAdapter
    except Exception:  # pragma: no cover — defensive
        return

    src = get_source(_RBI_RSS_SOURCE_ID)
    if src is None:
        return
    try:
        items = await RSSAdapter(
            source_id=src.source_id, feed_url=src.feed_url,
        ).fetch()
    except Exception:
        # Transient fetch/parse error — try again next tick.
        return
    if not items:
        return

    kw_lower = [k.lower() for k in keywords]
    last_fired_raw = cfg.get(_LAST_FIRED_EVENT_KEY)
    last_fired_guid = str(last_fired_raw) if isinstance(last_fired_raw, str) else ""

    for item in items:  # RSS feed is newest-first
        hay = ((item.title or "") + " " + (item.summary or "")).lower()
        hits = [k for k in kw_lower if k in hay]
        specific = [k for k in hits if k not in _EVENT_GENERIC_ORG_TOKENS]
        if not specific:
            continue
        meta = item.raw_metadata or {}
        guid = str(meta.get("guid") or item.url or item.title or "")
        if guid and guid == last_fired_guid:
            # Same press release we already fired on — dedup.
            return
        # Persist guid BEFORE firing so a crash between fire and persist
        # re-fires (at-least-once) rather than silently dropping.
        await asyncio.to_thread(
            _persist_event_guid, workflow_id, step_index, guid,
        )
        await fire_external_event(
            workflow_id=workflow_id,
            triggered_step_index=step_index,
            fired_at=fired_at,
            audit_context={
                "source": _RBI_RSS_SOURCE_ID,
                "title": item.title,
                "url": item.url,
                "matched_keywords": specific,
                "published_at": (
                    item.published_at.isoformat()
                    if item.published_at else None
                ),
            },
        )
        return  # one fire per tick


async def fire_external_event(
    *,
    workflow_id: str,
    triggered_step_index: int,
    fired_at: datetime,
    audit_context: dict,
) -> Optional[str]:
    """Public seam for the Phase-5 news_events firing path.

    Thin wrapper around ``_fire_watch_run`` that always uses
    ``triggered_by='event_alert'`` (already allowed by the
    workflow_runs CHECK constraint) and forces ``audit_context``
    to non-empty. Returns the newly-created ``workflow_run.id`` so
    the caller can persist the link in ``news_fired_events.workflow_run_id``.

    This is the ONLY new public function added to the workflows
    package in the entire news_events build — every other touch
    is inside ``backend/news_events/``. See
    ``docs/news_events_phase0_plan.md`` §3.5 Touch 1.
    """
    if not audit_context:
        raise ValueError("audit_context must be non-empty for external events")
    return await _fire_watch_run(
        workflow_id=workflow_id,
        triggered_step_index=triggered_step_index,
        triggered_by="event_alert",
        fired_at=fired_at,
        audit_context=audit_context,
    )


# ── Scheduled-macro watcher (trigger.scheduled_macro) ────────────────


# Per-occurrence fire-once latch. Stores the event instance key
# (e.g. "rbi_mpc:2026-06-06") so the workflow fires once per release and
# re-arms automatically for the NEXT calendar occurrence.
_MACRO_FIRED_KEY = "_macro_fired_for"


def _persist_macro_fired(
    workflow_id: str, step_index: int, instance_key: str,
) -> None:
    """Persist the per-occurrence latch on a trigger.scheduled_macro step.
    Mirrors _persist_ipo_fired (string-capable JSON writer). Runs in a
    worker thread."""
    db = SessionLocal()
    try:
        step = (
            db.query(WorkflowStep)
            .filter(
                WorkflowStep.workflow_id == workflow_id,
                WorkflowStep.step_index == step_index,
            )
            .first()
        )
        if step is None:
            return
        cfg = dict(step.config or {})
        cfg[_MACRO_FIRED_KEY] = str(instance_key)
        step.config = cfg  # type: ignore[assignment]
        db.commit()
    finally:
        db.close()


def _clear_macro_fired(workflow_id: str, step_index: int) -> None:
    """Remove the per-occurrence latch. Called when a fire was persisted
    but ``fire_external_event`` did NOT create a run (e.g. the workflow
    was paused/deactivated in the tiny window between persist and fire),
    so the occurrence stays re-armable instead of being silently skipped
    forever."""
    db = SessionLocal()
    try:
        step = (
            db.query(WorkflowStep)
            .filter(
                WorkflowStep.workflow_id == workflow_id,
                WorkflowStep.step_index == step_index,
            )
            .first()
        )
        if step is None:
            return
        cfg = dict(step.config or {})
        if cfg.pop(_MACRO_FIRED_KEY, None) is not None:
            step.config = cfg  # type: ignore[assignment]
            db.commit()
    finally:
        db.close()


def _scan_active_macro_triggers() -> list[tuple[str, int, dict[str, object]]]:
    """Return (workflow_id, step_index, config_copy) for every active
    workflow with a trigger.scheduled_macro step. Multi-trigger: every
    such step is read independently (own latch)."""
    db = SessionLocal()
    try:
        rows = (
            db.query(Workflow, WorkflowStep)
            .join(WorkflowStep, WorkflowStep.workflow_id == Workflow.id)
            .filter(
                Workflow.status == WorkflowStatus.active,
                WorkflowStep.step_type == "trigger.scheduled_macro",
            )
            .all()
        )
        return [
            (str(wf.id), int(step.step_index), dict(step.config or {}))
            for wf, step in rows
        ]
    finally:
        db.close()


async def _poll_scheduled_macro_triggers() -> None:
    """Polled every 5 minutes. For each active trigger.scheduled_macro
    step whose calendar occurrence is currently inside its verify window,
    run the layered outcome verifier and fire ONCE on a confident match.

    NOT gated on NSE market hours: FOMC (~00:30 IST) and US CPI
    (~18:00 IST) land when the Indian market is closed and must still
    fire. Fail-safe: the verifier returns ``unknown`` on any uncertainty
    (no headline / low confidence / evidence-guard tripped / feed down),
    and we only fire when ``result.matched`` is True — a stale calendar
    date therefore causes a missed/late fire, never a false one.

    Fire-once is per-occurrence: the latch stores the event instance key
    (kind:date), persisted BEFORE firing so a crash re-fires at-most-once
    (engine runs are idempotent), and the workflow re-arms for the next
    occurrence automatically.
    """
    fired_at = datetime.now(timezone.utc)

    try:
        triggers = await asyncio.to_thread(_scan_active_macro_triggers)
    except Exception:
        logger.exception("[watcher.macro] scan failed")
        return
    if not triggers:
        return

    from backend.config import settings
    from backend.macro_events.calendar import due_event
    from backend.macro_events.verifier import verify_macro_outcome

    global_floor = float(getattr(settings, "macro_verifier_min_confidence", 0.85))

    for wf_id, step_idx, cfg in triggers:
        try:
            kind = str(cfg.get("kind", "")).strip()
            expected = str(cfg.get("expected_outcome", "")).strip()
            if not kind or not expected:
                continue

            # Only act while a known occurrence is inside its verify window.
            due = due_event(kind, fired_at)
            if due is None:
                continue
            # Already fired for THIS occurrence?
            if str(cfg.get(_MACRO_FIRED_KEY, "")) == due.instance_key():
                continue

            try:
                step_min = float(cfg.get("min_confidence", 0.85))
            except (TypeError, ValueError):
                step_min = 0.85
            eff_min = max(step_min, global_floor)

            comparison = cfg.get("comparison")
            threshold = cfg.get("threshold")
            allow_pm = bool(cfg.get("allow_prediction_market_fallback", True))

            result = await verify_macro_outcome(
                kind, expected,
                min_confidence=eff_min,
                comparison=str(comparison) if comparison is not None else None,
                threshold=float(threshold) if threshold is not None else None,
                allow_prediction_market_fallback=allow_pm,
            )
            if not result.matched:
                logger.info(
                    "[watcher.macro] no fire wf=%s step=%d kind=%s "
                    "expected=%s decision=%s tier=%s reason=%s",
                    wf_id, step_idx, kind, expected,
                    result.decision, result.tier,
                    (result.audit or {}).get("reason", ""),
                )
                continue

            # Fire-once: persist the per-occurrence latch BEFORE firing
            # (at-most-once — a real order must never double-register).
            await asyncio.to_thread(
                _persist_macro_fired, wf_id, step_idx, due.instance_key(),
            )
            run_id = await fire_external_event(
                workflow_id=wf_id,
                triggered_step_index=step_idx,
                fired_at=fired_at,
                audit_context={
                    "source": "scheduled_macro_watcher",
                    "kind": kind,
                    "expected_outcome": expected,
                    "decision": result.decision,
                    "tier": result.tier,
                    "confidence": result.confidence,
                    "evidence": result.evidence,
                    "event_instance": due.instance_key(),
                    "label": due.label,
                    **(result.audit or {}),
                },
            )
            if run_id is None:
                # The fire didn't create a run (workflow paused/deactivated
                # in the persist→fire window). Re-arm so the occurrence
                # isn't silently lost.
                await asyncio.to_thread(_clear_macro_fired, wf_id, step_idx)
                logger.info(
                    "[watcher.macro] fire produced no run wf=%s step=%d — "
                    "latch cleared, will retry", wf_id, step_idx,
                )
                continue
            logger.info(
                "[watcher.macro] fired wf=%s step=%d kind=%s outcome=%s "
                "tier=%s run=%s",
                wf_id, step_idx, kind, result.decision, result.tier, run_id,
            )
        except Exception:
            logger.exception(
                "[watcher.macro] failed to evaluate wf=%s step=%d",
                wf_id, step_idx,
            )


# ── IPO-open watcher ─────────────────────────────────────────────────


# Fire-once latch for trigger.ipo_open. Stored on the step's config as
# the string "1" once the watcher has fired. _persist_last_value can
# only carry float/dict, so we mirror _persist_event_guid's string-
# capable writer.
_IPO_OPEN_FIRED_KEY = "_ipo_open_fired"


def _persist_ipo_fired(workflow_id: str, step_index: int) -> None:
    """Persist the fire-once latch on a trigger.ipo_open step.

    Mirrors _persist_event_guid's shape: copy-and-reassign the JSON dict
    so SQLA tracks the change. Runs in a worker thread via to_thread.
    """
    db = SessionLocal()
    try:
        step = (
            db.query(WorkflowStep)
            .filter(
                WorkflowStep.workflow_id == workflow_id,
                WorkflowStep.step_index == step_index,
            )
            .first()
        )
        if step is None:
            return
        cfg = dict(step.config or {})
        cfg[_IPO_OPEN_FIRED_KEY] = "1"
        step.config = cfg  # type: ignore[assignment]
        db.commit()
    finally:
        db.close()


def _scan_active_ipo_open_triggers() -> list[tuple[str, int, dict[str, object]]]:
    """Return (workflow_id, step_index, config_copy) for every active
    workflow whose has a `trigger.ipo_open` step.

    Mirrors _scan_active_watch_triggers but narrowed to one step type.
    Multi-trigger: the watcher reads every ipo_open step (not just step
    0) — a workflow with two IPO triggers fires each one independently
    (with its own fire-once latch).
    """
    db = SessionLocal()
    try:
        rows = (
            db.query(Workflow, WorkflowStep)
            .join(WorkflowStep, WorkflowStep.workflow_id == Workflow.id)
            .filter(
                Workflow.status == WorkflowStatus.active,
                WorkflowStep.step_type == "trigger.ipo_open",
            )
            .all()
        )
        return [
            (str(wf.id), int(step.step_index), dict(step.config or {}))
            for wf, step in rows
        ]
    finally:
        db.close()


def _ipo_close_plus_one_trading_day(close_date_str: str) -> Optional[datetime]:
    """Parse an IPO close_date and return close + 1 trading day at UTC
    end-of-day. Used to set Workflow.expires_at so the workflow auto-
    deactivates after the close-day handoff window.

    Trading-day skipping uses is_trading_day (currently weekend-only;
    the holiday table is a TODO — see backend/utils/time_utils.py:83).
    For v1 this is acceptable: a workflow that bleeds one extra calendar
    day past a Diwali holiday is harmless (it won't re-fire — the latch
    is set).

    Returns None when the date can't be parsed honestly.
    """
    from backend.utils.time_utils import is_trading_day

    if not close_date_str:
        return None
    s = str(close_date_str).strip()
    for fmt in ("%Y-%m-%d", "%d-%b-%Y", "%d-%B-%Y", "%d-%m-%Y", "%d/%m/%Y"):
        try:
            parsed = datetime.strptime(s, fmt)
            break
        except ValueError:
            continue
    else:
        return None

    # Walk forward at least one calendar day, then keep advancing until
    # we land on a trading day. is_trading_day takes a tz-aware datetime;
    # convert to IST then ask. We only need date-level resolution here.
    from datetime import timedelta as _td
    candidate = parsed + _td(days=1)
    for _ in range(7):  # cap the loop at one week
        # is_trading_day accepts a naive dt → it calls to_ist for us.
        if is_trading_day(candidate):
            break
        candidate += _td(days=1)

    # Anchor expires_at at end-of-trading-day IST = 15:30 IST.
    # is_trading_day returned True for candidate; build a UTC dt at that
    # day's 15:30 IST (≈ 10:00 UTC). We don't need exact precision —
    # the engine just consults expires_at <= now() before firing.
    return candidate.replace(hour=10, minute=0, second=0, microsecond=0)


def _persist_workflow_expires_at(
    workflow_id: str, expires_at: datetime,
) -> None:
    """Set Workflow.expires_at if it's currently NULL or in the future
    of the proposed value (we never shorten an existing tighter expiry).
    """
    db = SessionLocal()
    try:
        wf = db.query(Workflow).filter(Workflow.id == workflow_id).first()
        if wf is None:
            return
        current = getattr(wf, "expires_at", None)
        if current is None or current > expires_at:
            wf.expires_at = expires_at  # type: ignore[assignment]
            db.commit()
    finally:
        db.close()


async def _poll_ipo_open_triggers() -> None:
    """Polled every 30 minutes. Fire workflows whose `trigger.ipo_open`
    step's symbol matches an IPO that flipped to status='open' in the
    live NSE feed.

    NOT gated on market hours: IPO open-status is readable any time of
    day. Fires ONCE per (workflow_id, step_index) — the latch
    `_ipo_open_fired` is persisted on the step config BEFORE the
    workflow runs so a crash between fire and persist re-fires at-most-
    once on the next tick (engine retries are idempotent).

    On a successful fire, also sets `Workflow.expires_at` to close_date
    + 1 trading day so the workflow auto-deactivates after the close-day
    handoff window (the watcher's expiry sweep in `_poll_due_workflows`
    handles the eventual transition to paused).
    """
    fired_at = datetime.now(timezone.utc)

    try:
        triggers = await asyncio.to_thread(_scan_active_ipo_open_triggers)
    except Exception:
        logger.exception("[watcher.ipo_open] scan failed")
        return
    if not triggers:
        return

    # Filter out already-fired triggers BEFORE the (potentially slow)
    # IPO feed call — if every trigger has already fired we have nothing
    # to do this tick.
    pending = [t for t in triggers if not t[2].get(_IPO_OPEN_FIRED_KEY)]
    if not pending:
        return

    # One feed call per tick (the feed is Redis-cached for 45 minutes
    # so this is typically a cache hit anyway).
    try:
        from backend.services.ipo_feed import list_upcoming_ipos
        listing = await asyncio.to_thread(list_upcoming_ipos)
    except Exception:
        logger.exception("[watcher.ipo_open] feed call crashed")
        return

    if listing.get("source") == "unreachable":
        # Honest: feed unreachable. Log + return (try next tick).
        # Do NOT fire, do NOT fabricate.
        logger.info(
            "[watcher.ipo_open] feed unreachable; skipping tick (note=%s)",
            (listing.get("note") or "")[:120],
        )
        return

    # Build a (symbol -> ipo_record) lookup so per-trigger evaluation is O(1).
    ipos = listing.get("ipos") or []
    by_symbol: dict[str, dict[str, object]] = {}
    for r in ipos:
        if not isinstance(r, dict):
            continue
        sym = str(r.get("symbol") or "").upper()
        if sym:
            by_symbol[sym] = r

    for wf_id, step_idx, cfg in pending:
        try:
            target_symbol = str(cfg.get("symbol", "")).upper()
            if not target_symbol:
                continue
            ipo = by_symbol.get(target_symbol)
            if ipo is None:
                # Not in the feed yet (still pre-announcement) or no
                # match — wait for next tick.
                continue
            status_ = str(ipo.get("status") or "").lower()
            if status_ != "open":
                # upcoming / closed → not the open edge. Skip.
                continue

            # Fire-once: persist the latch BEFORE the actual fire so a
            # crash between the two re-fires at-most-once (the next
            # poll re-evaluates the same trigger; engine idempotency
            # handles double-runs).
            await asyncio.to_thread(_persist_ipo_fired, wf_id, step_idx)

            run_id = await _fire_watch_run(
                wf_id, step_idx, "event_alert", fired_at,
                audit_context={
                    "source": "ipo_open_watcher",
                    "ipo_symbol": target_symbol,
                    "ipo_name": ipo.get("name"),
                    "open_date": ipo.get("open_date"),
                    "close_date": ipo.get("close_date"),
                },
            )

            # Best-effort: set Workflow.expires_at = close_date + 1
            # trading day so the workflow doesn't fire forever if a
            # future IPO with the same symbol re-uses NSE conventions.
            close_dt = _ipo_close_plus_one_trading_day(
                str(ipo.get("close_date") or ""),
            )
            if close_dt is not None:
                expires_utc = close_dt.replace(tzinfo=timezone.utc)
                await asyncio.to_thread(
                    _persist_workflow_expires_at, wf_id, expires_utc,
                )

            logger.info(
                "[watcher.ipo_open] fired workflow %s step %d "
                "(ipo=%s run=%s)",
                wf_id, step_idx, target_symbol, run_id,
            )
        except Exception:
            logger.exception(
                "[watcher.ipo_open] failed to evaluate workflow %s step %d",
                wf_id, step_idx,
            )


# ── IPO listing-credit poller (P3.1) ─────────────────────────────────


def _scan_due_listing_allocations() -> list[str]:
    """Return the ids of every PaperIpoAllocation that needs a listing
    credit on this tick.

    Criteria:
      * allotment_status == 'allotted'
      * book_credited is False (not yet credited / terminally skipped)
      * listing_date IS NOT NULL
      * listing_date <= today (IST)

    Returns ids (not row instances) so the per-row processing loop can
    open a fresh transaction per allocation, isolating partial failures.
    Synchronous sync-SQLA scan inside the worker thread; caller wraps
    this in asyncio.to_thread.
    """
    from backend.models import PaperIpoAllocation
    from backend.utils.time_utils import now_ist

    today = now_ist().date()
    db = SessionLocal()
    try:
        rows = (
            db.query(PaperIpoAllocation.id)
            .filter(
                PaperIpoAllocation.allotment_status == "allotted",
                PaperIpoAllocation.book_credited.is_(False),
                PaperIpoAllocation.listing_date.isnot(None),
                PaperIpoAllocation.listing_date <= today,
            )
            .order_by(PaperIpoAllocation.created_at.asc())
            .all()
        )
        return [str(r[0]) for r in rows]
    finally:
        db.close()


def _credit_one_allocation(allocation_id: str) -> None:
    """Credit a single allocation, opening + committing its own session.

    Runs in a worker thread. Per-row try/except + commit isolates one
    allocation's failure from the rest of the tick.
    """
    from backend.models import PaperIpoAllocation
    from backend.paper.ipo_fills import credit_listed_allotment

    db = SessionLocal()
    try:
        alloc = db.get(PaperIpoAllocation, allocation_id)
        if alloc is None:
            return
        credit_listed_allotment(db, alloc)
        db.commit()
    except Exception:
        db.rollback()
        logger.exception(
            "[ipo-listing-credit] failed to credit allocation %s",
            allocation_id,
        )
    finally:
        db.close()


async def _poll_ipo_listing_fills() -> None:
    """Polled hourly. Credit every allotted PaperIpoAllocation whose
    ``listing_date`` has arrived into the user's paper book.

    NOT gated on market hours: the credit is a paper-book write (no live
    trade), and ``listing_price`` is honestly None when the feed has no
    quote yet. ``credit_listed_allotment`` is idempotent — the
    ``book_credited`` latch + UNIQUE
    ``paper_orders.client_request_id='ipo-listing-{alloc.id}'`` together
    guarantee at-most-once credit per allocation, even across crashes /
    overlapping ticks.

    One sync scan inside ``asyncio.to_thread`` for the candidate ids,
    then one per-row thread per allocation with its own SessionLocal +
    commit. Per-row isolation means a bad row never blocks the rest of
    the tick.
    """
    try:
        allocation_ids = await asyncio.to_thread(_scan_due_listing_allocations)
    except Exception:
        logger.exception("[ipo-listing-credit] scan failed")
        return
    if not allocation_ids:
        return

    logger.info(
        "[ipo-listing-credit] processing %d due allocation(s)",
        len(allocation_ids),
    )
    for alloc_id in allocation_ids:
        try:
            await asyncio.to_thread(_credit_one_allocation, alloc_id)
        except Exception:
            logger.exception(
                "[ipo-listing-credit] worker failed for %s", alloc_id,
            )


# ── Global-price watcher (trigger.global_price) ──────────────────────


# Per-step crossing-detection key. Mirrors `_LAST_PRICE_KEY` /
# `_LAST_VALUE_KEY` but lives under its own slot so a workflow with
# BOTH a `trigger.price` (Kite NSE/NFO/MCX) AND a `trigger.global_price`
# (Kraken / Twelve Data / etc.) keeps independent crossing state per
# step. `_persist_last_value` accepts (key, float) so we re-use it.
_GLOBAL_PRICE_LAST_KEY = "_global_last_price"


def _scan_active_global_price_triggers() -> list[tuple[str, int, dict[str, object]]]:
    """Return (workflow_id, step_index, config_copy) for every active
    workflow with a ``trigger.global_price`` step.

    Multi-trigger: every such step is scanned independently (own last-
    price state under ``_GLOBAL_PRICE_LAST_KEY``). Mirrors
    ``_scan_active_macro_triggers`` / ``_scan_active_ipo_open_triggers``.
    """
    db = SessionLocal()
    try:
        rows = (
            db.query(Workflow, WorkflowStep)
            .join(WorkflowStep, WorkflowStep.workflow_id == Workflow.id)
            .filter(
                Workflow.status == WorkflowStatus.active,
                WorkflowStep.step_type == "trigger.global_price",
            )
            .all()
        )
        return [
            (str(wf.id), int(step.step_index), dict(step.config or {}))
            for wf, step in rows
        ]
    finally:
        db.close()


async def _poll_global_price_triggers() -> None:
    """Polled every ``settings.global_price_poll_seconds`` (default 60s).
    Scans active workflows with a ``trigger.global_price`` step, fetches
    the current price from the external provider chain
    (``backend.market.global_quotes.get_global_quote``), evaluates the
    threshold using the same ``_matches_threshold`` semantics as
    ``trigger.price`` (with persisted last-price state for
    ``crosses_*``), and fires on a match.

    NOT gated on NSE market hours: crypto is 24/7 and forex sessions
    span the Indian overnight. The provider chain is best-effort with
    a Redis cache; ``get_global_quote`` returns None when every provider
    fails, in which case we simply skip that step on this tick (no fire,
    no false alarm) — same fail-safe posture as the macro watcher.
    """
    fired_at = datetime.now(timezone.utc)

    try:
        triggers = await asyncio.to_thread(_scan_active_global_price_triggers)
    except Exception:
        logger.exception("[watcher.global_price] scan failed")
        return
    if not triggers:
        return

    # Lazy import — keeps watcher startup cheap and lets the module load
    # even if the global_quotes config fields aren't populated yet.
    try:
        from backend.market.global_quotes import get_global_quote
    except Exception:
        logger.exception("[watcher.global_price] import failed")
        return

    for wf_id, step_idx, cfg in triggers:
        try:
            asset_class = str(cfg.get("asset_class", "")).strip().lower()
            symbol = str(cfg.get("symbol", "")).strip().upper()
            operator = str(cfg.get("operator", "")).strip()
            if not asset_class or not symbol or not operator:
                continue
            try:
                threshold = float(cfg.get("value", 0.0))  # type: ignore[arg-type]
            except (TypeError, ValueError):
                continue

            quote_currency_raw = cfg.get("quote_currency")
            quote_currency: Optional[str] = (
                str(quote_currency_raw).strip().upper()
                if isinstance(quote_currency_raw, str) and quote_currency_raw.strip()
                else None
            )

            # Provider chain runs in a worker thread because the default
            # httpx-based implementation is sync. None on every provider
            # failure → skip this tick (fail-safe; no false fires).
            quote = await asyncio.to_thread(
                get_global_quote, asset_class, symbol,
                quote_currency=quote_currency,
            )
            if quote is None:
                continue

            current = float(quote.price)
            last_raw = cfg.get(_GLOBAL_PRICE_LAST_KEY)
            last = float(last_raw) if isinstance(last_raw, (int, float)) else None

            matched = _matches_threshold(operator, current, threshold, last)

            # Persist last_price so the NEXT tick's crosses_* logic
            # works — same pattern as `_evaluate_price_trigger`.
            await asyncio.to_thread(
                _persist_last_value,
                wf_id, step_idx, _GLOBAL_PRICE_LAST_KEY, current,
            )

            if not matched:
                continue

            audit_context = {
                "source": "global_price_watcher",
                "asset_class": asset_class,
                "symbol": symbol,
                "operator": operator,
                "threshold": threshold,
                "price": current,
                "quote_currency": quote.quote_currency,
                "provider": quote.source,
                "as_of": quote.as_of,
            }
            run_id = await _fire_watch_run(
                wf_id, step_idx, "price_alert", fired_at,
                audit_context=audit_context,
            )
            logger.info(
                "[watcher.global_price] fired wf=%s step=%d "
                "asset=%s sym=%s op=%s thr=%s price=%s src=%s run=%s",
                wf_id, step_idx, asset_class, symbol, operator,
                threshold, current, quote.source, run_id,
            )
        except Exception:
            logger.exception(
                "[watcher.global_price] failed to evaluate wf=%s step=%d",
                wf_id, step_idx,
            )


# ── Earnings watcher (trigger.earnings) ──────────────────────────────


# Per-occurrence fire-once latch for trigger.earnings. Mirrors
# ``_MACRO_FIRED_KEY``: stores the event instance key (e.g.
# "INFY:2026-07-15") so the workflow fires once per quarter and re-arms
# automatically for the NEXT scheduled earnings date.
_EARNINGS_FIRED_KEY = "_earnings_fired_for"


def _persist_earnings_fired(
    workflow_id: str, step_index: int, instance_key: str,
) -> None:
    """Persist the per-occurrence latch on a ``trigger.earnings`` step.

    Mirrors ``_persist_macro_fired`` — copy-and-reassign the JSON dict
    so SQLA tracks the change. Runs in a worker thread via
    ``asyncio.to_thread``.
    """
    db = SessionLocal()
    try:
        step = (
            db.query(WorkflowStep)
            .filter(
                WorkflowStep.workflow_id == workflow_id,
                WorkflowStep.step_index == step_index,
            )
            .first()
        )
        if step is None:
            return
        cfg = dict(step.config or {})
        cfg[_EARNINGS_FIRED_KEY] = str(instance_key)
        step.config = cfg  # type: ignore[assignment]
        db.commit()
    finally:
        db.close()


def _clear_earnings_fired(workflow_id: str, step_index: int) -> None:
    """Remove the per-occurrence earnings latch. Called when the latch
    was persisted but ``fire_external_event`` did NOT create a run (e.g.
    the workflow was paused in the persist→fire window), so the
    occurrence stays re-armable instead of being silently skipped
    forever. Mirrors ``_clear_macro_fired``.
    """
    db = SessionLocal()
    try:
        step = (
            db.query(WorkflowStep)
            .filter(
                WorkflowStep.workflow_id == workflow_id,
                WorkflowStep.step_index == step_index,
            )
            .first()
        )
        if step is None:
            return
        cfg = dict(step.config or {})
        if cfg.pop(_EARNINGS_FIRED_KEY, None) is not None:
            step.config = cfg  # type: ignore[assignment]
            db.commit()
    finally:
        db.close()


def _scan_active_earnings_triggers() -> list[tuple[str, int, dict[str, object]]]:
    """Return (workflow_id, step_index, config_copy) for every active
    workflow with a ``trigger.earnings`` step. Multi-trigger: every such
    step is read independently (own per-occurrence latch). Mirrors
    ``_scan_active_macro_triggers``.
    """
    db = SessionLocal()
    try:
        rows = (
            db.query(Workflow, WorkflowStep)
            .join(WorkflowStep, WorkflowStep.workflow_id == Workflow.id)
            .filter(
                Workflow.status == WorkflowStatus.active,
                WorkflowStep.step_type == "trigger.earnings",
            )
            .all()
        )
        return [
            (str(wf.id), int(step.step_index), dict(step.config or {}))
            for wf, step in rows
        ]
    finally:
        db.close()


async def _poll_earnings_triggers() -> None:
    """Polled every 30 minutes. For each active ``trigger.earnings`` step
    whose calendar occurrence is currently inside its verify window, run
    the EPS/revenue outcome verifier and fire ONCE on a confident match.

    Mirrors ``_poll_scheduled_macro_triggers`` exactly in shape:

      * NOT gated on NSE market hours — US ADR earnings (and yfinance's
        post-print estimate updates) can land overnight.
      * Fail-safe: ``verify_earnings_outcome`` returns ``unknown``
        whenever the reported number isn't in yet (or the metric isn't
        supported, e.g. revenue today). We fire ONLY when
        ``outcome.matched`` is True — a not-yet-reported quarter causes
        a missed/late fire, never a false one.
      * Fire-once is per-occurrence: the latch stores the event
        instance key (``SYMBOL:YYYY-MM-DD``), persisted BEFORE firing so
        a crash re-fires at-most-once (engine runs are idempotent), and
        the workflow re-arms for the next quarter automatically.
      * If ``fire_external_event`` returns None (workflow paused /
        deactivated in the persist→fire window) the latch is cleared so
        the occurrence isn't silently lost.
    """
    fired_at = datetime.now(timezone.utc)

    try:
        triggers = await asyncio.to_thread(_scan_active_earnings_triggers)
    except Exception:
        logger.exception("[watcher.earnings] scan failed")
        return
    if not triggers:
        return

    # Lazy imports keep watcher startup cheap and avoid a hard dependency
    # on the earnings_events package at module load.
    try:
        from backend.config import settings
        from backend.earnings_events import due_event, verify_earnings_outcome
    except Exception:
        logger.exception("[watcher.earnings] import failed")
        return

    global_floor = float(
        getattr(settings, "earnings_verifier_min_confidence", 0.85)
    )

    for wf_id, step_idx, cfg in triggers:
        try:
            symbol = str(cfg.get("symbol", "")).strip().upper()
            condition = str(cfg.get("condition", "")).strip().lower()
            if not symbol or not condition:
                continue
            metric = str(cfg.get("metric", "eps")).strip().lower() or "eps"

            # Only act while a known occurrence is inside its verify window.
            ev = due_event(symbol, fired_at)
            if ev is None:
                continue
            # Already fired for THIS occurrence?
            if str(cfg.get(_EARNINGS_FIRED_KEY, "")) == ev.instance_key():
                continue

            try:
                step_min = float(cfg.get("min_confidence", 0.85))
            except (TypeError, ValueError):
                step_min = 0.85
            eff_min = max(step_min, global_floor)

            surprise_threshold_raw = cfg.get("surprise_threshold_pct")
            surprise_threshold: Optional[float]
            if surprise_threshold_raw is None:
                surprise_threshold = None
            else:
                try:
                    surprise_threshold = float(surprise_threshold_raw)
                except (TypeError, ValueError):
                    surprise_threshold = None

            outcome = await verify_earnings_outcome(
                symbol, metric, condition,
                surprise_threshold_pct=surprise_threshold,
                min_confidence=eff_min,
            )
            if not outcome.matched:
                logger.info(
                    "[watcher.earnings] no fire wf=%s step=%d sym=%s "
                    "metric=%s cond=%s decision=%s reason=%s",
                    wf_id, step_idx, symbol, metric, condition,
                    outcome.decision,
                    (outcome.audit or {}).get("reason", ""),
                )
                continue

            # Fire-once: persist the per-occurrence latch BEFORE firing
            # (at-most-once — a real order must never double-register).
            await asyncio.to_thread(
                _persist_earnings_fired, wf_id, step_idx, ev.instance_key(),
            )
            run_id = await fire_external_event(
                workflow_id=wf_id,
                triggered_step_index=step_idx,
                fired_at=fired_at,
                audit_context={
                    **(outcome.audit or {}),
                    "source": "earnings_watcher",
                    "symbol": symbol,
                    "metric": metric,
                    "condition": condition,
                    "decision": outcome.decision,
                    "reported": outcome.reported,
                    "estimate": outcome.estimate,
                    "surprise_pct": outcome.surprise_pct,
                    "confidence": outcome.confidence,
                    "evidence": outcome.evidence,
                    "instance_key": ev.instance_key(),
                    "label": ev.label,
                },
            )
            if run_id is None:
                # The fire didn't create a run (workflow paused/deactivated
                # in the persist→fire window). Re-arm so the occurrence
                # isn't silently lost — mirrors the macro watcher.
                await asyncio.to_thread(
                    _clear_earnings_fired, wf_id, step_idx,
                )
                logger.info(
                    "[watcher.earnings] fire produced no run wf=%s "
                    "step=%d — latch cleared, will retry",
                    wf_id, step_idx,
                )
                continue
            logger.info(
                "[watcher.earnings] fired wf=%s step=%d sym=%s "
                "metric=%s decision=%s run=%s",
                wf_id, step_idx, symbol, metric, outcome.decision, run_id,
            )
        except Exception:
            logger.exception(
                "[watcher.earnings] failed to evaluate wf=%s step=%d",
                wf_id, step_idx,
            )
