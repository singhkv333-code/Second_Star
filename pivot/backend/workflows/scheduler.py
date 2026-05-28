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
    logger.info(
        "[workflow-scheduler] registered poll job (%ss) + watcher (%ss)",
        _POLL_INTERVAL_SECONDS, _WATCHER_INTERVAL_SECONDS,
    )


# ── Price / indicator watcher ────────────────────────────────────────


# Price/indicator triggers store last_price under this key inside
# workflow_steps.config so the watcher can detect crossings on the
# next tick. Stored as a JSON-friendly float; absent on the first tick.
_LAST_PRICE_KEY = "_last_price"
_LAST_VALUE_KEY = "_last_value"  # for indicator triggers


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
        except Exception:
            logger.exception(
                "[watcher] failed to evaluate %s for workflow %s step %d",
                step_type, wf_id, step_idx,
            )


def _batch_fetch_prices(instruments: list[str]) -> dict[str, float]:
    """Fetch live quotes for a batch of instruments. Uses Kite-mock-mode
    when no key is configured. Returns {instrument: ltp} for every
    instrument that has a price."""
    from backend.kite.market_data import get_live_quote

    raw = get_live_quote("mock_token", instruments) or {}
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

    try:
        value = await asyncio.to_thread(
            _compute_indicator_sync, sym, indicator, period,
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
    path so crosses_above / crosses_below work across ticks."""
    entry_raw = cfg.get("entry")
    if not isinstance(entry_raw, dict):
        return

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
        await _fire_watch_run(
            workflow_id, step_index, "indicator_alert", fired_at,
        )


def _compute_indicator_sync(
    symbol: str, indicator: str, period: int,
) -> Optional[float]:
    """Sync version of the fetch.indicator computation, suitable for
    the watcher (which runs DB / network in worker threads). Returns
    the latest value or None on insufficient data.

    Delegates to ``backend.services.backtest_indicators`` so the live
    watcher and the backtest engine compute the same scalar for the
    same (indicator, period) pair — adding an indicator anywhere makes
    it instantly fire-able here."""
    import pandas as pd  # type: ignore[import-untyped]

    from backend.kite.market_data import get_historical_ohlcv
    from backend.services.backtest_indicators import latest_value

    bars = get_historical_ohlcv(symbol, period="6mo", interval="1d") or []
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
