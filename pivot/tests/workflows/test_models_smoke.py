"""Smoke-test the workflow SQLAlchemy models.

Pure import + metadata sanity. End-to-end CRUD coverage lives in the
endpoint test files (test_workflows_api.py etc.) once the routers ship.
This file's only job is to fail loudly if a model goes missing or its
schema drifts from docs/ARCHITECTURE.md §4.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from backend.database import Base
from backend import models


# Names locked by ARCHITECTURE.md §4. Bumping any of these is a contract
# change and requires updating both this list and the API contract.
EXPECTED_TABLES = {
    "workflows",
    "workflow_steps",
    "workflow_runs",
    "workflow_run_steps",
    "workflow_approvals",
    "workflow_webhook_tokens",
}


def test_all_workflow_tables_registered() -> None:
    """Every workflow table from §4 must be in Base.metadata."""
    registered = {t.name for t in Base.metadata.sorted_tables}
    missing = EXPECTED_TABLES - registered
    assert not missing, f"Missing workflow tables: {sorted(missing)}"


def test_workflow_models_importable() -> None:
    """The model classes must be exported from backend.models."""
    assert models.Workflow.__tablename__ == "workflows"
    assert models.WorkflowStep.__tablename__ == "workflow_steps"
    assert models.WorkflowRun.__tablename__ == "workflow_runs"
    assert models.WorkflowRunStep.__tablename__ == "workflow_run_steps"
    assert models.WorkflowApproval.__tablename__ == "workflow_approvals"
    assert models.WorkflowWebhookToken.__tablename__ == "workflow_webhook_tokens"


def test_workflow_status_enum_values() -> None:
    """Locked status enum values per ARCHITECTURE.md §4."""
    assert {s.value for s in models.WorkflowStatus} == {
        "draft", "active", "paused", "archived",
    }
    assert {s.value for s in models.RunStatus} == {
        "running", "succeeded", "failed", "cancelled", "awaiting_approval",
    }
    assert {s.value for s in models.StepStatus} == {
        "pending", "running", "succeeded", "failed", "skipped", "awaiting_approval",
    }


def test_create_workflow_with_steps_round_trips(workflow_db: Session) -> None:
    """End-to-end: insert a workflow + steps + a run + a run step, read
    them back. Catches column-name typos, FK wiring, default mistakes."""
    user = models.User(
        email="wf_smoke@pivot.test",
        hashed_password="x",
        full_name="Smoke",
    )
    workflow_db.add(user)
    workflow_db.flush()

    wf = models.Workflow(
        user_id=user.id,
        name="Test agent",
        description="smoke test",
    )
    workflow_db.add(wf)
    workflow_db.flush()

    s0 = models.WorkflowStep(
        workflow_id=wf.id,
        step_index=0,
        step_type="trigger.schedule",
        config={"cron": "55 15 * * 1-5", "timezone": "Asia/Kolkata"},
        label="On schedule",
    )
    s1 = models.WorkflowStep(
        workflow_id=wf.id,
        step_index=1,
        step_type="fetch.portfolio",
        config={},
    )
    workflow_db.add_all([s0, s1])
    workflow_db.flush()

    run = models.WorkflowRun(
        workflow_id=wf.id,
        workflow_version=1,
        triggered_by="manual",
        status=models.RunStatus.running,
        context={},
    )
    workflow_db.add(run)
    workflow_db.flush()

    run_step = models.WorkflowRunStep(
        run_id=run.id,
        step_index=0,
        step_type="trigger.schedule",
        status=models.StepStatus.succeeded,
        attempts=1,
    )
    workflow_db.add(run_step)
    workflow_db.flush()

    approval = models.WorkflowApproval(
        run_id=run.id,
        step_index=3,
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=15),
        summary="Buy 10 RELIANCE at market",
    )
    workflow_db.add(approval)
    workflow_db.flush()

    token = models.WorkflowWebhookToken(
        token="tok_smoke_123",
        workflow_id=wf.id,
        step_index=0,
    )
    workflow_db.add(token)
    workflow_db.flush()

    # Re-read and verify relationships.
    fetched = workflow_db.query(models.Workflow).filter_by(id=wf.id).one()
    assert fetched.name == "Test agent"
    assert len(fetched.steps) == 2
    assert fetched.steps[0].step_type == "trigger.schedule"
    assert fetched.steps[0].config["cron"] == "55 15 * * 1-5"
    assert fetched.steps[1].step_type == "fetch.portfolio"

    fetched_run = workflow_db.query(models.WorkflowRun).filter_by(id=run.id).one()
    assert fetched_run.status == models.RunStatus.running
    assert len(fetched_run.steps) == 1
    assert len(fetched_run.approvals) == 1
    assert fetched_run.approvals[0].summary.startswith("Buy 10 RELIANCE")

    fetched_token = (
        workflow_db.query(models.WorkflowWebhookToken)
        .filter_by(token="tok_smoke_123")
        .one()
    )
    assert fetched_token.workflow_id == wf.id


def test_unique_step_index_per_workflow(workflow_db: Session) -> None:
    """The (workflow_id, step_index) UNIQUE constraint is enforced."""
    import sqlalchemy.exc as sa_exc
    import pytest

    user = models.User(
        email="wf_unique@pivot.test",
        hashed_password="x",
    )
    workflow_db.add(user)
    workflow_db.flush()
    wf = models.Workflow(user_id=user.id, name="dup test")
    workflow_db.add(wf)
    workflow_db.flush()

    workflow_db.add(
        models.WorkflowStep(
            workflow_id=wf.id, step_index=0,
            step_type="trigger.manual", config={},
        )
    )
    workflow_db.flush()

    workflow_db.add(
        models.WorkflowStep(
            workflow_id=wf.id, step_index=0,
            step_type="trigger.manual", config={},
        )
    )
    with pytest.raises(sa_exc.IntegrityError):
        workflow_db.flush()
    workflow_db.rollback()
