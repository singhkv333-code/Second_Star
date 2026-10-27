"""Run endpoints (API_CONTRACT.md §6).

  - GET    /api/workflows/{id}/runs   paginated history (RunSummary[])
  - GET    /api/runs/{id}             full Run shape
  - POST   /api/runs/{id}/cancel      flag the engine to stop

User-scoped: every query joins to workflows and filters by user_id;
cross-user access returns 404. RunSummary's `step_count` is derived
via a subquery against `workflow_steps` for the run's `workflow_version`
(API_CONTRACT.md §6.1 — the step list at run-time, not the workflow's
current step count).
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional, cast

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from backend.database import get_db
from backend.models import (
    RunStatus,
    Workflow,
    WorkflowRun,
    WorkflowRunStep,
    WorkflowStep,
)
from backend.routers._deps import require_user
from backend.routers._errors import not_found
from backend.schemas import (
    RunCancelResponse,
    RunListResponse,
    RunOut,
    RunStepOut,
    RunSummary,
)
from backend.workflows.engine import cancel_run

router = APIRouter(prefix="/api", tags=["Agents"])

logger = logging.getLogger(__name__)


# ── Helpers ──────────────────────────────────────────────────────────


def _ensure_workflow_for_user(
    db: Session, user_id: int, workflow_id: str,
) -> Workflow:
    wf = (
        db.query(Workflow)
        .filter(Workflow.id == workflow_id, Workflow.user_id == user_id)
        .first()
    )
    if wf is None:
        raise not_found("workflow not found")
    return wf


def _ensure_run_for_user(
    db: Session, user_id: int, run_id: str,
) -> WorkflowRun:
    """Look up a run + assert ownership via the parent workflow.
    Cross-user → 404 (per §1)."""
    run = (
        db.query(WorkflowRun)
        .join(Workflow, Workflow.id == WorkflowRun.workflow_id)
        .filter(WorkflowRun.id == run_id, Workflow.user_id == user_id)
        .first()
    )
    if run is None:
        raise not_found("run not found")
    return run


def _step_count_for_run(db: Session, run: WorkflowRun) -> int:
    """`step_count` is the workflow's step count at the version the run
    fired against. v1 has only one step list per workflow_id (PATCH
    rewrites it and bumps `workflow.version`), so the live count after
    a PATCH may differ from when the run was created. We approximate
    by counting current steps when run.workflow_version matches the
    workflow's version, else fall back to counting the run's
    workflow_run_steps (which records executed steps)."""
    wf_version = int(run.workflow_version)
    workflow = db.query(Workflow).filter_by(id=run.workflow_id).first()
    if workflow is not None and int(workflow.version) == wf_version:
        return cast(
            int,
            db.query(func.count(WorkflowStep.id))
            .filter(WorkflowStep.workflow_id == run.workflow_id)
            .scalar()
            or 0,
        )
    # Workflow has been edited since the run; the step list at run-time
    # is gone. Best-effort: use the run-step rows.
    return cast(
        int,
        db.query(func.count(WorkflowRunStep.id))
        .filter(WorkflowRunStep.run_id == run.id)
        .scalar()
        or 0,
    )


def _to_run_out(db: Session, run: WorkflowRun) -> RunOut:
    return RunOut(
        id=str(run.id),
        workflow_id=str(run.workflow_id),
        workflow_version=int(run.workflow_version),
        triggered_by=cast(str, run.triggered_by),  # type: ignore[arg-type]
        started_at=run.started_at,
        finished_at=run.finished_at,
        status=run.status.value if hasattr(run.status, "value")
        else str(run.status),  # type: ignore[arg-type]
        halt_reason=cast(Optional[str], run.halt_reason),  # type: ignore[arg-type]
        error_message=run.error_message,
        context=dict(run.context or {}),
        steps=[
            RunStepOut(
                step_index=int(s.step_index),
                step_type=str(s.step_type),
                status=s.status.value if hasattr(s.status, "value")
                else str(s.status),  # type: ignore[arg-type]
                started_at=s.started_at,
                finished_at=s.finished_at,
                output=dict(s.output) if s.output else None,
                error_message=s.error_message,
                attempts=int(s.attempts),
            )
            for s in sorted(run.steps, key=lambda s: int(s.step_index))
        ],
    )


def _to_run_summary(
    db: Session, run: WorkflowRun, step_count: int,
) -> RunSummary:
    return RunSummary(
        id=str(run.id),
        workflow_id=str(run.workflow_id),
        workflow_version=int(run.workflow_version),
        triggered_by=cast(str, run.triggered_by),  # type: ignore[arg-type]
        started_at=run.started_at,
        finished_at=run.finished_at,
        status=run.status.value if hasattr(run.status, "value")
        else str(run.status),  # type: ignore[arg-type]
        halt_reason=cast(Optional[str], run.halt_reason),  # type: ignore[arg-type]
        error_message=run.error_message,
        step_count=step_count,
    )


# ── Endpoints ────────────────────────────────────────────────────────


@router.get(
    "/workflows/{workflow_id}/runs",
    response_model=RunListResponse,
    summary="List runs for a workflow",
)
def list_runs(
    workflow_id: str,
    user_id: int = Depends(require_user),
    db: Session = Depends(get_db),
    status: Optional[str] = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
    cursor: Optional[str] = Query(default=None),
) -> RunListResponse:
    wf = _ensure_workflow_for_user(db, user_id, workflow_id)

    q = db.query(WorkflowRun).filter(WorkflowRun.workflow_id == wf.id)
    if status:
        statuses = [s.strip() for s in status.split(",") if s.strip()]
        q = q.filter(WorkflowRun.status.in_(statuses))
    q = q.order_by(WorkflowRun.started_at.desc(), WorkflowRun.id.desc())

    if cursor:
        cur_run = db.query(WorkflowRun).filter_by(id=cursor).first()
        if cur_run is not None:
            q = q.filter(WorkflowRun.started_at < cur_run.started_at)

    rows = q.limit(limit + 1).all()
    has_more = len(rows) > limit
    page = rows[:limit]

    # Compute step_count for each row in the page. We cache by
    # workflow_version to avoid O(n) duplicate queries when a workflow
    # has many runs at the same version.
    cache: dict[int, int] = {}
    items: list[RunSummary] = []
    for r in page:
        v = int(r.workflow_version)
        if v not in cache:
            cache[v] = _step_count_for_run(db, r)
        items.append(_to_run_summary(db, r, cache[v]))

    next_cursor = str(page[-1].id) if has_more else None
    return RunListResponse(items=items, next_cursor=next_cursor)


@router.get(
    "/runs/{run_id}",
    response_model=RunOut,
    summary="Get a run",
)
def get_run(
    run_id: str,
    user_id: int = Depends(require_user),
    db: Session = Depends(get_db),
) -> RunOut:
    run = _ensure_run_for_user(db, user_id, run_id)
    return _to_run_out(db, run)


@router.post(
    "/runs/{run_id}/cancel",
    response_model=RunCancelResponse,
    summary="Cancel a run",
)
def post_cancel_run(
    run_id: str,
    user_id: int = Depends(require_user),
    db: Session = Depends(get_db),
) -> RunCancelResponse:
    """Set the engine's cancel flag. The engine checks at every step
    boundary; if the run is already terminal this is a no-op (we
    return the current row unchanged)."""
    run = _ensure_run_for_user(db, user_id, run_id)
    if run.status in (
        RunStatus.succeeded,
        RunStatus.failed,
        RunStatus.cancelled,
    ):
        return RunCancelResponse(
            id=str(run.id),
            status=run.status.value if hasattr(run.status, "value")
            else str(run.status),  # type: ignore[arg-type]
            finished_at=run.finished_at,
        )

    cancel_run(str(run.id))

    # If the run is awaiting_approval (engine isn't actively looping),
    # we also need to flip it to cancelled directly — the engine won't
    # re-enter on its own. The cancel flag covers the active-loop case.
    if run.status == RunStatus.awaiting_approval:
        run.status = RunStatus.cancelled
        run.finished_at = datetime.now(timezone.utc)
        run.error_message = "cancelled by user"
        db.commit()
        db.refresh(run)

    return RunCancelResponse(
        id=str(run.id),
        status=run.status.value if hasattr(run.status, "value")
        else str(run.status),  # type: ignore[arg-type]
        finished_at=run.finished_at,
    )
