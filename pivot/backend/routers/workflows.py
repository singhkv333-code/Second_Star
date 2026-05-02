"""Agent System (Workflows v1) router.

Owns:
  - GET    /api/step-types                 (catalog; auth-required)
  - POST   /api/workflows                  (create)
  - GET    /api/workflows                  (list, omit steps/context)
  - GET    /api/workflows/{id}             (full shape)
  - PATCH  /api/workflows/{id}             (bumps version on step edit)
  - POST   /api/workflows/{id}/activate
  - POST   /api/workflows/{id}/pause
  - POST   /api/workflows/{id}/archive
  - POST   /api/workflows/{id}/run         (enqueue manual run)

Every endpoint is JWT-bearer authenticated and user-scoped. Cross-user
access returns 404 with the canonical envelope (NOT 403 — never leak
existence per API_CONTRACT.md §1).

Schema validation policy (ARCHITECTURE.md §7 invariant 7):
  - on POST: validate every step config against the registry's
    Pydantic model; reject unknown step_type; require step_index=0
    to be a `trigger.*`.
  - on PATCH: same validation; bump `workflow.version` if `steps`
    changed; refuse if status='active' (caller must pause first).
  - on activate: re-validate every step config.

The actual run scheduling (cron triggers, watcher arming) happens in
`backend/workflows/scheduler.py` Day 3+. For Day 2 the activate/pause
endpoints flip status only; manual `POST .../run` enqueues to the
in-process worker.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional, cast

from fastapi import APIRouter, Depends, Query
from pydantic import ValidationError
from sqlalchemy.orm import Session

from backend.database import get_db
from backend.models import (
    RunStatus,
    Workflow,
    WorkflowRun,
    WorkflowStatus,
    WorkflowStep,
)
from backend.routers._deps import require_user
from backend.routers._errors import (
    not_found,
    state_conflict,
    validation_error,
)
from backend.schemas import (
    RunCreatedResponse,
    StepOut,
    StepTypeCatalogResponse,
    WorkflowCreate,
    WorkflowListResponse,
    WorkflowOut,
    WorkflowPatch,
    WorkflowSummary,
)
from backend.workflows.engine import WorkflowEngine
from backend.workflows.registry import STEP_REGISTRY, get_catalog
from backend.workflows.scheduler import (
    InvalidCronError,
    upsert_workflow_schedule,
)

router = APIRouter(prefix="/api", tags=["Agents"])

logger = logging.getLogger(__name__)


# ── Helpers ──────────────────────────────────────────────────────────


def _validate_steps(
    steps: list[dict[str, object]],
) -> None:
    """Validate every step in a workflow body.

    Per API_CONTRACT.md §5.1:
      - reject unknown step_type → 422 validation_error with
        details.step_index and details.field='step_type'
      - reject step_index=0 that isn't a trigger.* → 400
        validation_error
      - reject any step whose config fails its Pydantic model → 422
        with details.step_index

    Steps come in as a list of dicts (already coerced from StepInput).
    Index in the list IS the step_index (per API_CONTRACT.md §5.1).
    """
    if not steps:
        # An empty workflow is allowed — chat-bot proposes a draft, user
        # may save with zero steps and add later.
        return

    for idx, step in enumerate(steps):
        step_type = step.get("step_type")
        if not isinstance(step_type, str):
            raise validation_error(
                f"step {idx} missing step_type",
                details={"step_index": idx, "field": "step_type"},
            )
        defn = STEP_REGISTRY.get(step_type)
        if defn is None:
            raise validation_error(
                f"unknown step_type {step_type!r}",
                details={
                    "step_index": idx,
                    "field": "step_type",
                    "reason": "unknown_step_type",
                    "known": sorted(STEP_REGISTRY.keys()),
                },
            )
        # step_index=0 must be a trigger.
        if idx == 0 and not defn.trigger_only:
            # 400 (per §5.1) — wrong shape, not a schema-validation
            # error.
            raise validation_error(
                f"step 0 must be a trigger.* (got {step_type!r})",
                details={
                    "step_index": 0,
                    "field": "step_type",
                    "reason": "step_0_must_be_trigger",
                },
            )
        # Subsequent steps must NOT be triggers (the engine handles
        # only step 0 as the trigger boundary).
        if idx > 0 and defn.trigger_only:
            raise validation_error(
                "trigger.* may only appear at step_index=0",
                details={
                    "step_index": idx,
                    "field": "step_type",
                    "reason": "trigger_must_be_step_0",
                },
            )

        cfg = step.get("config") or {}
        try:
            defn.config_model.model_validate(cfg)
        except ValidationError as e:
            first = e.errors()[0]
            raise validation_error(
                f"step {idx} config invalid: {first.get('msg', '')}",
                details={
                    "step_index": idx,
                    "field": ".".join(str(p) for p in first.get("loc", [])),
                    "reason": str(first.get("type", "")),
                },
            )


def _workflow_for_user(
    db: Session, user_id: int, workflow_id: str,
) -> Workflow:
    """Look up a workflow scoped to the user. Cross-user access → 404
    (per §1, never 403). Same shape used by every endpoint that takes
    `{id}`."""
    wf = (
        db.query(Workflow)
        .filter(Workflow.id == workflow_id, Workflow.user_id == user_id)
        .first()
    )
    if wf is None:
        raise not_found("workflow not found")
    return wf


def _replace_steps(
    db: Session, wf: Workflow, steps_in: list[dict[str, object]],
) -> None:
    """Replace the workflow's step list. Caller must have validated."""
    db.query(WorkflowStep).filter(WorkflowStep.workflow_id == wf.id).delete()
    db.flush()
    for idx, s in enumerate(steps_in):
        ws = WorkflowStep(
            workflow_id=wf.id,
            step_index=idx,
            step_type=cast(str, s["step_type"]),
            config=s.get("config") or {},
            label=cast(Optional[str], s.get("label")),
        )
        db.add(ws)
    db.flush()


def _to_workflow_out(wf: Workflow) -> WorkflowOut:
    """Build the canonical WorkflowOut shape with steps[]."""
    return WorkflowOut(
        id=str(wf.id),
        name=str(wf.name),
        description=wf.description,
        status=wf.status.value if hasattr(wf.status, "value")
        else str(wf.status),  # type: ignore[arg-type]
        version=int(wf.version),
        single_instance=bool(wf.single_instance),
        created_at=wf.created_at,
        updated_at=wf.updated_at,
        activated_at=wf.activated_at,
        last_run_at=wf.last_run_at,
        next_run_at=wf.next_run_at,
        steps=[
            StepOut(
                id=str(s.id),
                step_index=int(s.step_index),
                step_type=str(s.step_type),
                label=s.label,
                config=dict(s.config or {}),
            )
            for s in sorted(wf.steps, key=lambda s: int(s.step_index))
        ],
    )


def _to_workflow_summary(wf: Workflow) -> WorkflowSummary:
    return WorkflowSummary(
        id=str(wf.id),
        name=str(wf.name),
        description=wf.description,
        status=wf.status.value if hasattr(wf.status, "value")
        else str(wf.status),  # type: ignore[arg-type]
        version=int(wf.version),
        single_instance=bool(wf.single_instance),
        created_at=wf.created_at,
        updated_at=wf.updated_at,
        activated_at=wf.activated_at,
        last_run_at=wf.last_run_at,
        next_run_at=wf.next_run_at,
    )


# ── Catalog (Day 1 carryover) ─────────────────────────────────────────


@router.get(
    "/step-types",
    response_model=StepTypeCatalogResponse,
    summary="Step-type catalog",
    description=(
        "Returns the full catalog of supported workflow step types — their "
        "JSON Schema (draft 2020-12) for config validation, output schemas, "
        "and UI metadata. See docs/API_CONTRACT.md §8.1."
    ),
)
def get_step_types(
    _user_id: int = Depends(require_user),
) -> StepTypeCatalogResponse:
    catalog = get_catalog()
    return StepTypeCatalogResponse.model_validate(catalog)


# ── Workflow CRUD ─────────────────────────────────────────────────────


@router.post(
    "/workflows",
    response_model=WorkflowOut,
    status_code=201,
    summary="Create a workflow",
)
def create_workflow(
    body: WorkflowCreate,
    user_id: int = Depends(require_user),
    db: Session = Depends(get_db),
) -> WorkflowOut:
    steps_in = [s.model_dump() for s in body.steps]
    _validate_steps(steps_in)

    wf = Workflow(
        user_id=user_id,
        name=body.name,
        description=body.description,
        single_instance=body.single_instance,
        status=WorkflowStatus.draft,
    )
    db.add(wf)
    db.flush()
    _replace_steps(db, wf, steps_in)
    db.commit()
    db.refresh(wf)
    return _to_workflow_out(wf)


@router.get(
    "/workflows",
    response_model=WorkflowListResponse,
    summary="List workflows",
)
def list_workflows(
    user_id: int = Depends(require_user),
    db: Session = Depends(get_db),
    status: Optional[str] = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
    cursor: Optional[str] = Query(default=None),
) -> WorkflowListResponse:
    """Cursor-based pagination. Cursor is the previous page's last
    workflow_id; rows after it (ordered by created_at desc) are
    returned."""
    q = db.query(Workflow).filter(Workflow.user_id == user_id)
    if status:
        statuses = [s.strip() for s in status.split(",") if s.strip()]
        q = q.filter(Workflow.status.in_(statuses))
    q = q.order_by(Workflow.created_at.desc(), Workflow.id.desc())

    if cursor:
        cur_wf = db.query(Workflow).filter_by(id=cursor).first()
        if cur_wf is not None:
            q = q.filter(Workflow.created_at < cur_wf.created_at)

    rows = q.limit(limit + 1).all()
    has_more = len(rows) > limit
    items = [_to_workflow_summary(w) for w in rows[:limit]]
    next_cursor = str(rows[limit - 1].id) if has_more else None
    return WorkflowListResponse(items=items, next_cursor=next_cursor)


@router.get(
    "/workflows/{workflow_id}",
    response_model=WorkflowOut,
    summary="Get a workflow",
)
def get_workflow(
    workflow_id: str,
    user_id: int = Depends(require_user),
    db: Session = Depends(get_db),
) -> WorkflowOut:
    wf = _workflow_for_user(db, user_id, workflow_id)
    return _to_workflow_out(wf)


@router.patch(
    "/workflows/{workflow_id}",
    response_model=WorkflowOut,
    summary="Update a workflow",
)
def patch_workflow(
    workflow_id: str,
    body: WorkflowPatch,
    user_id: int = Depends(require_user),
    db: Session = Depends(get_db),
) -> WorkflowOut:
    wf = _workflow_for_user(db, user_id, workflow_id)

    # API_CONTRACT.md §5.4: editing is forbidden while active. Caller
    # must pause first.
    if wf.status == WorkflowStatus.active:
        raise state_conflict(
            "cannot edit an active workflow; pause first",
            details={"current_status": "active"},
        )
    if wf.status == WorkflowStatus.archived:
        raise state_conflict(
            "cannot edit an archived workflow",
            details={"current_status": "archived"},
        )

    if body.name is not None:
        wf.name = body.name
    if body.description is not None:
        wf.description = body.description
    if body.single_instance is not None:
        wf.single_instance = body.single_instance

    if body.steps is not None:
        steps_in = [s.model_dump() for s in body.steps]
        _validate_steps(steps_in)
        _replace_steps(db, wf, steps_in)
        # Bump version per §5.4 — runs reference the version at run
        # creation time so old run rows still join their original
        # step list.
        wf.version = int(wf.version) + 1

    db.commit()
    db.refresh(wf)
    return _to_workflow_out(wf)


# ── State transitions ────────────────────────────────────────────────


@router.post(
    "/workflows/{workflow_id}/activate",
    response_model=WorkflowOut,
    summary="Activate a workflow",
)
def activate_workflow(
    workflow_id: str,
    user_id: int = Depends(require_user),
    db: Session = Depends(get_db),
) -> WorkflowOut:
    wf = _workflow_for_user(db, user_id, workflow_id)
    if wf.status == WorkflowStatus.active:
        raise state_conflict(
            "workflow already active",
            details={"current_status": "active"},
        )
    if wf.status == WorkflowStatus.archived:
        raise state_conflict(
            "cannot activate an archived workflow",
            details={"current_status": "archived"},
        )

    # Re-validate every step on activate (defense in depth, §7.7).
    steps_in = [
        {
            "step_type": s.step_type,
            "config": s.config or {},
            "label": s.label,
        }
        for s in sorted(wf.steps, key=lambda s: int(s.step_index))
    ]
    _validate_steps(steps_in)

    wf.status = WorkflowStatus.active
    wf.activated_at = datetime.now(timezone.utc)
    # Compute `next_run_at` for trigger.schedule workflows. Invalid
    # cron / timezone fails the activation 422 (closes reviewer
    # Day-2 edge case #1 — never silently arm a dead schedule).
    try:
        upsert_workflow_schedule(db, wf)
    except InvalidCronError as e:
        raise validation_error(
            str(e),
            details={"step_index": 0, "field": "config.cron"},
        )
    db.commit()
    db.refresh(wf)
    return _to_workflow_out(wf)


@router.post(
    "/workflows/{workflow_id}/pause",
    response_model=WorkflowOut,
    summary="Pause a workflow",
)
def pause_workflow(
    workflow_id: str,
    user_id: int = Depends(require_user),
    db: Session = Depends(get_db),
) -> WorkflowOut:
    wf = _workflow_for_user(db, user_id, workflow_id)
    if wf.status == WorkflowStatus.archived:
        raise state_conflict(
            "cannot pause an archived workflow",
            details={"current_status": "archived"},
        )
    wf.status = WorkflowStatus.paused
    upsert_workflow_schedule(db, wf)  # clears next_run_at
    db.commit()
    db.refresh(wf)
    return _to_workflow_out(wf)


@router.post(
    "/workflows/{workflow_id}/archive",
    response_model=WorkflowOut,
    summary="Archive a workflow",
)
def archive_workflow(
    workflow_id: str,
    user_id: int = Depends(require_user),
    db: Session = Depends(get_db),
) -> WorkflowOut:
    wf = _workflow_for_user(db, user_id, workflow_id)
    wf.status = WorkflowStatus.archived
    upsert_workflow_schedule(db, wf)  # clears next_run_at
    db.commit()
    db.refresh(wf)
    return _to_workflow_out(wf)


@router.post(
    "/workflows/{workflow_id}/run",
    response_model=RunCreatedResponse,
    status_code=201,
    summary="Manually run a workflow",
)
async def run_workflow(
    workflow_id: str,
    user_id: int = Depends(require_user),
    db: Session = Depends(get_db),
) -> RunCreatedResponse:
    """Create a `triggered_by='manual'` run row and enqueue it on the
    in-process worker. Allowed for any non-archived status — including
    paused (so users can test before activating)."""
    wf = _workflow_for_user(db, user_id, workflow_id)
    if wf.status == WorkflowStatus.archived:
        raise state_conflict(
            "cannot run an archived workflow",
            details={"current_status": "archived"},
        )
    run = WorkflowRun(
        workflow_id=wf.id,
        workflow_version=int(wf.version),
        triggered_by="manual",
        status=RunStatus.running,
        context={},
    )
    db.add(run)
    wf.last_run_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(run)

    # Enqueue on the loop. The engine takes care of acquiring the
    # single-instance lock; if held, the run terminates as cancelled.
    engine = WorkflowEngine()
    asyncio.create_task(engine.execute_run(str(run.id)))

    return RunCreatedResponse(run_id=str(run.id))
