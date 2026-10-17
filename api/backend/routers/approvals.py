"""Approval endpoints (API_CONTRACT.md §7).

  - GET    /api/runs/{id}/approvals/pending
  - POST   /api/approvals/{id}/decision  body: {decision: 'approved'|'rejected'}

Decision flow:
  - approved:  set decision/decided_at; flip the run from
               `awaiting_approval` back to `running`; signal the engine
               to resume via WorkflowEngine.resume_run().
  - rejected:  set decision/decided_at; terminate the run as `cancelled`
               with `error_message='approval rejected at step <i>'`.
  - already-decided: 409 state_conflict.
  - expired (now > expires_at): 409 state_conflict with
               `details.reason='expired'`.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from backend.database import get_db
from backend.models import (
    RunStatus,
    Workflow,
    WorkflowApproval,
    WorkflowRun,
)
from backend.routers._deps import require_user
from backend.routers._errors import not_found, state_conflict
from backend.schemas import (
    ApprovalDecisionRequest,
    ApprovalDecisionResponse,
    ApprovalListResponse,
    ApprovalOut,
)
from backend.workflows.engine import WorkflowEngine

router = APIRouter(prefix="/api", tags=["Agents"])

logger = logging.getLogger(__name__)


def _ensure_run_for_user(
    db: Session, user_id: int, run_id: str,
) -> WorkflowRun:
    run = (
        db.query(WorkflowRun)
        .join(Workflow, Workflow.id == WorkflowRun.workflow_id)
        .filter(WorkflowRun.id == run_id, Workflow.user_id == user_id)
        .first()
    )
    if run is None:
        raise not_found("run not found")
    return run


def _ensure_approval_for_user(
    db: Session, user_id: int, approval_id: str,
) -> WorkflowApproval:
    """Look up an approval and assert ownership via run → workflow →
    user. Cross-user → 404."""
    appr = (
        db.query(WorkflowApproval)
        .join(WorkflowRun, WorkflowRun.id == WorkflowApproval.run_id)
        .join(Workflow, Workflow.id == WorkflowRun.workflow_id)
        .filter(
            WorkflowApproval.id == approval_id,
            Workflow.user_id == user_id,
        )
        .first()
    )
    if appr is None:
        raise not_found("approval not found")
    return appr


def _approval_to_out(appr: WorkflowApproval) -> ApprovalOut:
    return ApprovalOut(
        id=str(appr.id),
        run_id=str(appr.run_id),
        step_index=int(appr.step_index),
        summary=str(appr.summary),
        requested_at=appr.requested_at,
        expires_at=appr.expires_at,
        decision=appr.decision,  # type: ignore[arg-type]
        decided_at=appr.decided_at,
    )


@router.get(
    "/runs/{run_id}/approvals/pending",
    response_model=ApprovalListResponse,
    summary="List pending approvals for a run",
)
def list_pending_approvals(
    run_id: str,
    user_id: int = Depends(require_user),
    db: Session = Depends(get_db),
) -> ApprovalListResponse:
    _ensure_run_for_user(db, user_id, run_id)
    rows = (
        db.query(WorkflowApproval)
        .filter(
            WorkflowApproval.run_id == run_id,
            WorkflowApproval.decision.is_(None),
        )
        .order_by(WorkflowApproval.requested_at)
        .all()
    )
    return ApprovalListResponse(
        items=[_approval_to_out(a) for a in rows],
    )


@router.post(
    "/approvals/{approval_id}/decision",
    response_model=ApprovalDecisionResponse,
    summary="Decide an approval",
)
async def decide_approval(
    approval_id: str,
    body: ApprovalDecisionRequest,
    user_id: int = Depends(require_user),
    db: Session = Depends(get_db),
) -> ApprovalDecisionResponse:
    appr = _ensure_approval_for_user(db, user_id, approval_id)

    if appr.decision is not None:
        raise state_conflict(
            "approval already decided",
            details={"current_decision": appr.decision},
        )
    # Naive check (SQLite stores DateTime without timezone awareness in
    # tests). Coerce to UTC-aware before comparing.
    expires_at = appr.expires_at
    if expires_at is not None and expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if expires_at is not None and expires_at < datetime.now(timezone.utc):
        raise state_conflict(
            "approval expired",
            details={"reason": "expired"},
        )

    decided_at = datetime.now(timezone.utc)
    appr.decision = body.decision
    appr.decided_at = decided_at

    run = (
        db.query(WorkflowRun).filter_by(id=appr.run_id).first()
    )
    if run is None:
        # Should be unreachable — FK ensures existence.
        raise not_found("run not found")

    if body.decision == "rejected":
        # Terminate the run as cancelled, citing the step.
        run.status = RunStatus.cancelled
        run.finished_at = decided_at
        run.error_message = (
            f"approval rejected at step {int(appr.step_index)}"
        )
        db.commit()
        db.refresh(appr)
    else:
        # Approved — flip run back to running and let the engine
        # resume. Resume runs in the background; the response returns
        # immediately.
        run.status = RunStatus.running
        db.commit()
        db.refresh(appr)
        engine = WorkflowEngine()
        asyncio.create_task(engine.resume_run(str(run.id)))

    return ApprovalDecisionResponse(
        id=str(appr.id),
        decision=body.decision,
        decided_at=decided_at,
    )
