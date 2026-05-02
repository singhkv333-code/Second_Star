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

# APScheduler job id for the workflow poll job — keep stable across
# restarts so `replace_existing=True` works.
_POLL_JOB_ID = "pivot_workflows_poll"


class InvalidCronError(ValueError):
    """Raised when a `trigger.schedule` cron expression is malformed.

    Routers catch this and emit 422 with `validation_error` code.
    """


def _trigger_schedule_step(workflow: Workflow) -> Optional[WorkflowStep]:
    """Return the `trigger.schedule` step at index 0, or None if the
    workflow's trigger is a different type (manual, price, etc.)."""
    for step in workflow.steps:
        if int(step.step_index) == 0:
            return step if str(step.step_type) == "trigger.schedule" else None
    return None


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
    """Set or clear `workflow.next_run_at` based on current state.

    Called by the workflows router on activate / pause / archive /
    PATCH-with-steps. Caller is responsible for `db.commit()`.

    Behavior:
      - If status == active AND step 0 is `trigger.schedule` →
        recompute `next_run_at` from the cron config. Raises
        `InvalidCronError` if the cron is bad — caller should let it
        bubble so the router emits 422.
      - Otherwise (paused / archived / draft / non-schedule trigger) →
        clear `next_run_at` so the poller skips this workflow.
    """
    if workflow.status != WorkflowStatus.active:
        workflow.next_run_at = None  # type: ignore[assignment]
        return

    step = _trigger_schedule_step(workflow)
    if step is None:
        # Active but trigger isn't schedule (manual, price, etc.) —
        # not our problem; clear `next_run_at` so the poller skips.
        workflow.next_run_at = None  # type: ignore[assignment]
        return

    raw_cfg: dict[str, object] = step.config or {}  # type: ignore[assignment]
    cfg: dict[str, object] = dict(raw_cfg) if raw_cfg else {}
    cron = str(cfg.get("cron", ""))
    tz_str = str(cfg.get("timezone", "UTC"))
    workflow.next_run_at = compute_next_run_at(cron, tz_str)  # type: ignore[assignment]


async def _poll_due_workflows() -> None:
    """Polled job: find every active workflow whose `next_run_at`
    has passed, create a `triggered_by='schedule'` run, hand it to the
    engine, and recompute `next_run_at` for the next tick.

    All DB work via sync sessions inside `asyncio.to_thread()` so the
    APScheduler loop never blocks on I/O.
    """
    fired_at = datetime.now(timezone.utc)

    def _fetch_due() -> list[str]:
        """Returns workflow IDs to fire. Runs in a worker thread."""
        db = SessionLocal()
        try:
            due = (
                db.query(Workflow)
                .filter(
                    Workflow.status == WorkflowStatus.active,
                    Workflow.next_run_at.isnot(None),
                    Workflow.next_run_at <= fired_at,
                )
                .all()
            )
            return [str(wf.id) for wf in due]
        finally:
            db.close()

    workflow_ids = await asyncio.to_thread(_fetch_due)
    if not workflow_ids:
        return

    logger.info(
        "[workflow-scheduler] firing %d due workflow(s) at %s",
        len(workflow_ids),
        fired_at.isoformat(),
    )

    for wf_id in workflow_ids:
        try:
            await _fire_one(wf_id, fired_at)
        except Exception:
            # Don't let one bad workflow kill the poll cycle.
            logger.exception(
                "[workflow-scheduler] failed to fire workflow %s", wf_id
            )


async def _fire_one(workflow_id: str, fired_at: datetime) -> None:
    """Create a scheduled run row, recompute next_run_at, hand to
    engine. All DB work via to_thread; engine is async."""

    def _create_run_and_recompute() -> Optional[str]:
        """Returns the new run_id, or None if the workflow vanished /
        was paused between fetch and fire."""
        db = SessionLocal()
        try:
            wf = db.query(Workflow).filter(Workflow.id == workflow_id).first()
            if wf is None or wf.status != WorkflowStatus.active:
                return None
            run = WorkflowRun(
                workflow_id=wf.id,
                workflow_version=int(wf.version),
                triggered_by="schedule",
                status=RunStatus.running,
                context={},
            )
            db.add(run)
            wf.last_run_at = fired_at  # type: ignore[assignment]
            try:
                upsert_workflow_schedule(db, wf)
            except InvalidCronError:
                # Cron became invalid since activation (e.g. step
                # patched after activation through some future path).
                # Clear so we don't retry forever.
                wf.next_run_at = None  # type: ignore[assignment]
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
    """Attach the workflow poll job to the existing AsyncIOScheduler.

    Idempotent: re-registering replaces the existing job.
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
    logger.info(
        "[workflow-scheduler] registered poll job (every %ss)",
        _POLL_INTERVAL_SECONDS,
    )
