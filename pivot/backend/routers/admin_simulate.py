"""Admin-only synthetic-trigger endpoints for testing workflows.

These exist so engineers can drive workflows through the engine
WITHOUT waiting for live market data, real news, or market hours.
Targeted use cases:

  - Quickly test a freshly-drafted workflow end-to-end before
    activating it for real (typing one prompt → simulating its
    trigger → watching the engine + approval + broker-mock path).
  - Reproduce a production bug locally with a known input.
  - Drive Layer-1 of the test harness (scenario YAML files) — see
    the upcoming docs/test_harness.md.

Hard guards (every endpoint):

  1. ``settings.app_env`` must NOT be ``"production"``. In production
     these endpoints return 404 — they never exist for end users.
  2. JWT bearer auth via ``require_user``; the caller must own the
     target workflow (cross-user calls return 404, never 403, matching
     the Agent System convention).

Endpoints:

  POST /api/admin/workflows/{id}/simulate-trigger
       Synthetically fire the workflow's first trigger step (or a
       specified ``triggered_step_index`` for multi-trigger workflows)
       as if the live data path had matched. Reuses the existing
       ``_fire_watch_run`` so the engine sees a real WorkflowRun.

  POST /api/admin/workflows/{id}/approve-all
       Bulk-approve every pending WorkflowApproval row for the
       workflow's most recent run (or a specified ``run_id``).
       Bypasses the human-in-the-loop step for tests.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Path
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from backend.config import settings
from backend.database import get_db
from backend.models import (
    RunStatus,
    Workflow,
    WorkflowApproval,
    WorkflowRun,
    WorkflowStatus,
)
from backend.routers._deps import require_admin
from backend.routers._errors import not_found, state_conflict

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin", tags=["Admin"])


# ── Production guard ────────────────────────────────────────────────


def _production_guard() -> None:
    """Refuse to operate in production. Raises 404 so the endpoint
    looks like it doesn't exist."""
    if (settings.app_env or "").strip().lower() == "production":
        raise HTTPException(
            status_code=404,
            detail=(
                "admin simulate endpoints are not available in production"
            ),
        )


# ── Schemas ──────────────────────────────────────────────────────────


_ALLOWED_TRIGGERED_BY = (
    "schedule", "manual", "webhook",
    "price_alert", "indicator_alert", "event_alert",
)


class SimulateTriggerRequest(BaseModel):
    triggered_by: Literal[
        "schedule", "manual", "webhook",
        "price_alert", "indicator_alert", "event_alert",
    ] = Field(
        default="manual",
        description=(
            "Mirrors the CHECK constraint on workflow_runs.triggered_by. "
            "Pick the value that matches the trigger type you're simulating "
            "so the audit trail reads correctly."
        ),
    )
    triggered_step_index: int = Field(
        default=0, ge=0, le=63,
        description=(
            "Which trigger step to fire from in a multi-trigger workflow. "
            "Defaults to 0 (the workflow's first trigger)."
        ),
    )
    audit_context: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Free-form payload merged into workflow_runs.context. Useful "
            "for handing the engine the same shape fire_external_event "
            "would have written, e.g. {'news_event': {...}}. Empty by default."
        ),
    )


class SimulateTriggerResponse(BaseModel):
    workflow_id: str
    run_id: Optional[str]
    triggered_by: str
    triggered_step_index: int
    started: bool = Field(
        ...,
        description=(
            "False iff the workflow is not active (status != 'active') "
            "and the engine refused to start a run."
        ),
    )


class ApproveAllRequest(BaseModel):
    run_id: Optional[str] = Field(
        default=None,
        description=(
            "Specific run to approve. Defaults to the workflow's most "
            "recent run (any status)."
        ),
    )
    decision: Literal["approved", "rejected"] = Field(
        default="approved",
        description=(
            "What decision to apply to every pending approval on the run. "
            "Defaults to approved; pass 'rejected' to stress the cancel "
            "path."
        ),
    )


class ApproveAllResponse(BaseModel):
    workflow_id: str
    run_id: str
    decided_count: int
    decision: str


# ── Helpers ──────────────────────────────────────────────────────────


def _load_workflow_for_user(
    db: Session, *, workflow_id: str, user_id: int
) -> Workflow:
    wf = db.query(Workflow).filter(Workflow.id == workflow_id).first()
    if wf is None or int(wf.user_id) != int(user_id):
        # Convention: cross-user → 404, never 403.
        raise not_found("workflow not found")
    return wf


# ── Endpoints ────────────────────────────────────────────────────────


@router.post(
    "/workflows/{workflow_id}/simulate-trigger",
    response_model=SimulateTriggerResponse,
    summary="Synthetically fire a workflow trigger (non-production only)",
)
async def simulate_trigger(
    payload: SimulateTriggerRequest,
    workflow_id: str = Path(..., min_length=1, max_length=64),
    user_id: int = Depends(require_admin),
    db: Session = Depends(get_db),
) -> SimulateTriggerResponse:
    """Drive the engine to execute the workflow as if its trigger had
    fired live. Returns the created ``run_id`` (or None if the
    workflow isn't active)."""
    _production_guard()
    wf = _load_workflow_for_user(db, workflow_id=workflow_id, user_id=user_id)

    if wf.status != WorkflowStatus.active:
        # Be explicit: ``_fire_watch_run`` silently no-ops on
        # inactive workflows, which is confusing for a test endpoint.
        # Surface the precondition.
        raise state_conflict(
            "workflow must be in state 'active' to simulate a trigger",
            details={"current_status": wf.status.value},
        )

    fired_at = datetime.now(timezone.utc)
    # Lazy import to keep this router cheap to import at app boot.
    from backend.workflows.scheduler import _fire_watch_run

    run_id = await _fire_watch_run(
        workflow_id=str(wf.id),
        triggered_step_index=int(payload.triggered_step_index),
        triggered_by=payload.triggered_by,
        fired_at=fired_at,
        audit_context=dict(payload.audit_context) or None,
    )

    logger.info(
        "[admin.simulate] simulate-trigger workflow_id=%s triggered_by=%s "
        "run_id=%s requester_user_id=%s",
        wf.id, payload.triggered_by, run_id, user_id,
    )
    return SimulateTriggerResponse(
        workflow_id=str(wf.id),
        run_id=run_id,
        triggered_by=payload.triggered_by,
        triggered_step_index=int(payload.triggered_step_index),
        started=run_id is not None,
    )


@router.post(
    "/workflows/{workflow_id}/approve-all",
    response_model=ApproveAllResponse,
    summary="Bulk-decide every pending approval on a workflow run (non-production only)",
)
async def approve_all(
    payload: ApproveAllRequest,
    workflow_id: str = Path(..., min_length=1, max_length=64),
    user_id: int = Depends(require_admin),
    db: Session = Depends(get_db),
) -> ApproveAllResponse:
    """Stamp ``decision`` on every undecided WorkflowApproval for the
    chosen run. Lets tests skip the human-in-the-loop step.

    NOTE: this writes ``decided_at`` and ``decision`` but does NOT
    re-enter the engine. That's a deliberate scope choice — the
    engine's approval-resume path already polls for decided rows
    on its next tick. If you need an immediate re-enter, call
    ``simulate-trigger`` again after this.
    """
    _production_guard()
    wf = _load_workflow_for_user(db, workflow_id=workflow_id, user_id=user_id)

    if payload.run_id is not None:
        run = (
            db.query(WorkflowRun)
            .filter(
                WorkflowRun.id == payload.run_id,
                WorkflowRun.workflow_id == wf.id,
            )
            .first()
        )
        if run is None:
            raise not_found("run not found for this workflow")
    else:
        run = (
            db.query(WorkflowRun)
            .filter(WorkflowRun.workflow_id == wf.id)
            .order_by(WorkflowRun.started_at.desc())
            .first()
        )
        if run is None:
            raise not_found("workflow has no runs to approve")

    pending = (
        db.query(WorkflowApproval)
        .filter(
            WorkflowApproval.run_id == run.id,
            WorkflowApproval.decision.is_(None),
        )
        .all()
    )

    now = datetime.now(timezone.utc)
    decided = 0
    for appr in pending:
        appr.decision = payload.decision
        appr.decided_at = now
        decided += 1

    # Mirror approvals.py: on rejected, also terminate the run as
    # cancelled. Skip for ``approved`` since the engine will re-enter
    # and progress the run naturally.
    if payload.decision == "rejected" and decided > 0:
        run.status = RunStatus.cancelled
        run.finished_at = now
        run.error_message = (
            f"all approvals rejected via admin simulate "
            f"(decided={decided})"
        )

    db.commit()

    logger.info(
        "[admin.simulate] approve-all workflow_id=%s run_id=%s decided=%d "
        "decision=%s requester_user_id=%s",
        wf.id, run.id, decided, payload.decision, user_id,
    )
    return ApproveAllResponse(
        workflow_id=str(wf.id),
        run_id=str(run.id),
        decided_count=decided,
        decision=payload.decision,
    )
