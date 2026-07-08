"""Scheduled-runs endpoint — backs the FE Calendar tab.

Enumerates upcoming fire times for the authenticated user's active
workflows in a [from, to] date range. v1 covers `trigger.schedule`
only; `trigger.event` is cut to v2 (no event source wired) and
returns nothing.

Capped at `_MAX_ITEMS` to bound the response size — a 1-min cron over
30 days is 43,200 fire times, which would saturate the wire and the
client. The cap is documented in API_CONTRACT.md §6.5.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from apscheduler.triggers.cron import CronTrigger  # type: ignore[import-untyped]
from fastapi import APIRouter, Depends, Query
from pytz import timezone as pytz_timezone  # type: ignore[import-untyped]
from sqlalchemy.orm import Session

from backend.database import get_db
from backend.models import Workflow, WorkflowStatus, WorkflowStep
from backend.routers._deps import require_user
from backend.routers._errors import validation_error
from backend.workflows.scheduler import _normalize_cron_dow
from backend.schemas import ScheduledRunItem, ScheduledRunsResponse

router = APIRouter(prefix="/api", tags=["Agents"])
logger = logging.getLogger(__name__)


# Hard cap on returned items. A user with 10 active 1-min crons over a
# 30-day window would otherwise get 432,000 items. Cap covers the
# realistic Calendar-tab use case (months ahead, daily/weekly crons).
_MAX_ITEMS = 500

# Hard cap on the lookahead window. > 90 days is almost always a UI
# bug — Calendar's month view is at most 6 weeks.
_MAX_WINDOW_DAYS = 90


@router.get(
    "/workflows/scheduled-runs",
    response_model=ScheduledRunsResponse,
    summary="List upcoming scheduled fires for the user's active workflows",
)
def get_scheduled_runs(
    from_: datetime = Query(
        ..., alias="from",
        description="Window start (ISO 8601 UTC).",
    ),
    to: datetime = Query(
        ..., description="Window end (ISO 8601 UTC).",
    ),
    user_id: int = Depends(require_user),
    db: Session = Depends(get_db),
) -> ScheduledRunsResponse:
    if to <= from_:
        raise validation_error(
            "`to` must be strictly after `from`",
            details={"field": "to", "reason": "to_must_exceed_from"},
        )
    # Normalise to UTC so APScheduler's get_next_fire_time gets a
    # consistent timezone-aware comparison point.
    if from_.tzinfo is None:
        from_ = from_.replace(tzinfo=timezone.utc)
    if to.tzinfo is None:
        to = to.replace(tzinfo=timezone.utc)
    window_days = (to - from_).total_seconds() / 86400
    if window_days > _MAX_WINDOW_DAYS:
        raise validation_error(
            f"window must be <= {_MAX_WINDOW_DAYS} days "
            f"(got {window_days:.1f})",
            details={"field": "to", "reason": "window_too_large"},
        )

    rows = (
        db.query(Workflow, WorkflowStep)
        .join(WorkflowStep, WorkflowStep.workflow_id == Workflow.id)
        .filter(
            Workflow.user_id == user_id,
            Workflow.status == WorkflowStatus.active,
            WorkflowStep.step_index == 0,
            WorkflowStep.step_type == "trigger.schedule",
        )
        .all()
    )

    items: list[ScheduledRunItem] = []
    for wf, step in rows:
        if len(items) >= _MAX_ITEMS:
            break
        cfg = dict(step.config or {})
        cron = str(cfg.get("cron", ""))
        tz_str = str(cfg.get("timezone", "UTC"))
        if not cron:
            continue
        try:
            tz = pytz_timezone(tz_str)
            # Day-of-week digits in a stored cron are POSIX-convention
            # (0/7=Sun,1=Mon..6=Sat); from_crontab needs the translated,
            # unambiguous day-name form (see workflows/scheduler.py).
            trig = CronTrigger.from_crontab(_normalize_cron_dow(cron), timezone=tz)
        except Exception:
            # Malformed cron / tz on a stored workflow step — should
            # have been blocked at activate time. Skip rather than 500.
            logger.warning(
                "[scheduled-runs] workflow %s has invalid cron/tz; skipping",
                wf.id,
            )
            continue

        # Iterate fire times. APScheduler's get_next_fire_time(prev,
        # now) returns the next fire strictly after `prev` (or after
        # `now` when prev is None); feed each result back to walk
        # forward.
        next_fire = trig.get_next_fire_time(None, from_.astimezone(tz))
        while next_fire is not None and next_fire <= to.astimezone(tz):
            fire_utc: datetime = next_fire.astimezone(timezone.utc)
            # Format like "3:55 PM IST" — pytz tz string can be
            # "Asia/Kolkata"; we abbreviate the city to make it terse.
            tz_label = tz_str.split("/")[-1].upper().replace("KOLKATA", "IST")
            local_str = next_fire.strftime("%I:%M %p").lstrip("0") + " " + tz_label
            items.append(ScheduledRunItem(
                workflow_id=str(wf.id),
                workflow_name=str(wf.name),
                trigger_type="trigger.schedule",
                fire_time=fire_utc,
                fire_time_local=local_str,
            ))
            if len(items) >= _MAX_ITEMS:
                break
            next_fire = trig.get_next_fire_time(next_fire, next_fire)

    items.sort(key=lambda r: r.fire_time)
    return ScheduledRunsResponse(items=items[:_MAX_ITEMS])
