"""Event-trigger calendar endpoint — back the FE Calendar tab's
event-driven entries (#48).

Phase 1 of the redesign asks the calendar to render BOTH scheduled-runs
(cron-driven) AND event-driven entries side-by-side. Scheduled-runs
already have an endpoint (`/api/workflows/scheduled-runs`); this is the
event side.

Source of truth for "what events exist" is a small static calendar of
known 2026 macro events (RBI MPC dates, FII flow windows) plus an
"next-likely-results-window" heuristic for company_results triggers.
We surface ONE upcoming event per active `trigger.event` workflow
within [from, to], filtered by the workflow's `event_type` + `filter`.

Endpoint:
  GET /api/events/calendar?from=<ISO>&to=<ISO>

Response shape mirrors `/api/workflows/scheduled-runs` so the FE can
union both lists by sort key on the same component.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Iterable

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend.database import get_db
from backend.models import Workflow, WorkflowStatus, WorkflowStep
from backend.routers._deps import require_user
from backend.routers._errors import validation_error


router = APIRouter(prefix="/api/events", tags=["Events"])
logger = logging.getLogger(__name__)


_MAX_ITEMS = 500
_MAX_WINDOW_DAYS = 90


# ── Static calendar (2026) ───────────────────────────────────────────
#
# Tradeoff: a real "events DB" requires a feed integration we don't
# have wired. A static calendar lets the FE render real entries today
# and is easy to update when the next year's MPC dates publish. Each
# upstream source's official site is the right SoT to refresh from.

_RBI_MPC_DATES_2026: list[tuple[datetime, str]] = [
    # MPC outcome days announced by RBI; each at ~10:00 IST.
    (datetime(2026, 2, 6, 10, 0, tzinfo=timezone.utc), "RBI MPC Outcome"),
    (datetime(2026, 4, 8, 10, 0, tzinfo=timezone.utc), "RBI MPC Outcome"),
    (datetime(2026, 6, 6, 10, 0, tzinfo=timezone.utc), "RBI MPC Outcome"),
    (datetime(2026, 8, 7, 10, 0, tzinfo=timezone.utc), "RBI MPC Outcome"),
    (datetime(2026, 10, 1, 10, 0, tzinfo=timezone.utc), "RBI MPC Outcome"),
    (datetime(2026, 12, 5, 10, 0, tzinfo=timezone.utc), "RBI MPC Outcome"),
]

# Approximate quarterly results windows. Companies report on assorted
# days inside; we surface the window-start so the FE shows an icon.
_RESULTS_WINDOWS_2026: list[tuple[datetime, str]] = [
    (datetime(2026, 1, 15, 9, 0, tzinfo=timezone.utc), "Q3 FY26 results window opens"),
    (datetime(2026, 4, 15, 9, 0, tzinfo=timezone.utc), "Q4 FY26 results window opens"),
    (datetime(2026, 7, 15, 9, 0, tzinfo=timezone.utc), "Q1 FY27 results window opens"),
    (datetime(2026, 10, 15, 9, 0, tzinfo=timezone.utc), "Q2 FY27 results window opens"),
]


def _fii_flow_estimates(
    from_: datetime, to: datetime,
) -> Iterable[tuple[datetime, str]]:
    """FII/DII net-flow data is published every market evening.
    Yield one entry per upcoming weekday in [from, to] at 18:30 IST."""
    cur = from_.replace(hour=13, minute=0, second=0, microsecond=0)
    while cur <= to:
        # Mon–Fri only.
        if cur.weekday() < 5:
            yield cur, "FII / DII net-flow published"
        cur += timedelta(days=1)


# ── Response models ──────────────────────────────────────────────────


class EventCalendarItem(BaseModel):
    workflow_id: str
    workflow_name: str
    trigger_type: str               # always "trigger.event" here
    event_type: str                 # rbi_rate_decision | company_results | fii_flow
    fire_time: datetime             # UTC
    fire_time_local: str            # human-readable IST string
    label: str                      # e.g. "RBI MPC Outcome"


class EventCalendarResponse(BaseModel):
    items: list[EventCalendarItem]


# ── Endpoint ─────────────────────────────────────────────────────────


@router.get(
    "/calendar",
    response_model=EventCalendarResponse,
    summary="Upcoming event-trigger fires for the user's active workflows",
)
def get_event_calendar(
    from_: datetime = Query(..., alias="from"),
    to: datetime = Query(...),
    user_id: int = Depends(require_user),
    db: Session = Depends(get_db),
) -> EventCalendarResponse:
    if to <= from_:
        raise validation_error(
            "`to` must be strictly after `from`",
            details={"field": "to", "reason": "to_must_exceed_from"},
        )
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
            WorkflowStep.step_type == "trigger.event",
        )
        .all()
    )

    items: list[EventCalendarItem] = []
    for wf, step in rows:
        if len(items) >= _MAX_ITEMS:
            break
        cfg = dict(step.config or {})
        # TriggerEventConfig carries keywords/event_description, NOT an
        # event_type field — derive the calendar bucket from them (the old
        # cfg.get("event_type") read was always "" so the calendar was
        # permanently empty).
        _hay = " ".join([
            str(cfg.get("event_description", "")),
            " ".join(str(k) for k in (cfg.get("keywords") or []) if isinstance(k, str)),
        ]).lower()
        if any(t in _hay for t in ("repo rate", "rate cut", "mpc", "monetary policy", "rbi")):
            event_type = "rbi_rate_decision"
        elif any(t in _hay for t in ("result", "earnings", "quarterly")):
            event_type = "company_results"
        elif any(t in _hay for t in ("fii", "dii", "net flow", "net-flow")):
            event_type = "fii_flow"
        else:
            event_type = ""
        if event_type == "rbi_rate_decision":
            sources: list[tuple[datetime, str]] = list(_RBI_MPC_DATES_2026)
        elif event_type == "company_results":
            sources = list(_RESULTS_WINDOWS_2026)
        elif event_type == "fii_flow":
            sources = list(_fii_flow_estimates(from_, to))
        else:
            continue

        for fire_time, label in sources:
            if fire_time < from_ or fire_time > to:
                continue
            tz_label = "IST"
            local_str = (
                fire_time.strftime("%I:%M %p").lstrip("0") + " " + tz_label
            )
            items.append(EventCalendarItem(
                workflow_id=str(wf.id),
                workflow_name=str(wf.name),
                trigger_type="trigger.event",
                event_type=event_type,
                fire_time=fire_time,
                fire_time_local=local_str,
                label=label,
            ))
            if len(items) >= _MAX_ITEMS:
                break

    items.sort(key=lambda x: x.fire_time)
    return EventCalendarResponse(items=items[:_MAX_ITEMS])
