"""Tests for the admin synthetic-trigger endpoints.

Covered:
  - Production guard (app_env='production' → 404)
  - JWT auth required
  - Cross-user workflow access returns 404
  - Inactive workflow returns 409
  - Happy-path simulate-trigger creates a WorkflowRun
  - approve-all writes decisions on every pending approval row
  - approve-all with decision='rejected' cancels the run
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from backend.models import (
    RunStatus,
    Workflow,
    WorkflowApproval,
    WorkflowRun,
    WorkflowStatus,
)


def _seed_workflow(db, *, user_id: int, status=WorkflowStatus.active) -> Workflow:
    wf = Workflow(
        user_id=user_id,
        name=f"sim-wf-{uuid.uuid4().hex[:6]}",
        description="simulate-trigger test fixture",
        status=status,
        version=1,
    )
    db.add(wf)
    db.flush()
    return wf


def _seed_run(db, *, workflow: Workflow) -> WorkflowRun:
    run = WorkflowRun(
        workflow_id=workflow.id,
        workflow_version=int(workflow.version),
        triggered_by="manual",
        triggered_step_index=0,
        status=RunStatus.awaiting_approval,
    )
    db.add(run)
    db.flush()
    return run


def _seed_approval(db, *, run: WorkflowRun, step_index: int = 0) -> WorkflowApproval:
    appr = WorkflowApproval(
        run_id=run.id,
        step_index=step_index,
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        summary="Approve test trade",
    )
    db.add(appr)
    db.flush()
    return appr


def _user_id_from_headers(client: TestClient, headers: dict[str, str]) -> int:
    r = client.get("/auth/me", headers=headers)
    assert r.status_code == 200, r.text
    return int(r.json()["id"])


# ── Production guard ────────────────────────────────────────────────


def test_production_env_returns_404(client, auth_headers, db, monkeypatch):
    uid = _user_id_from_headers(client, auth_headers)
    wf = _seed_workflow(db, user_id=uid)
    db.commit()

    # Patch the settings reference inside the router module.
    monkeypatch.setattr(
        "backend.routers.admin_simulate.settings.app_env",
        "production",
        raising=False,
    )
    # require_admin reads settings.app_env fresh; flipping it to production
    # would make the admin gate fail-closed (403) BEFORE the router's own
    # production→404 guard runs. This test asserts the 404 guard, so grant
    # the caller admin for the patched-prod window (mirrors ADMIN_USER_IDS).
    monkeypatch.setattr(
        "backend.config.settings.admin_user_ids", str(uid), raising=False
    )

    r = client.post(
        f"/api/admin/workflows/{wf.id}/simulate-trigger",
        json={"triggered_by": "manual"},
        headers=auth_headers,
    )
    assert r.status_code == 404
    assert "production" in r.text.lower()


def test_unauthenticated_returns_401(client, db):
    wf = _seed_workflow(db, user_id=1)
    db.commit()

    r = client.post(
        f"/api/admin/workflows/{wf.id}/simulate-trigger",
        json={"triggered_by": "manual"},
    )
    assert r.status_code == 401


# ── Cross-user access ───────────────────────────────────────────────


def test_cross_user_workflow_returns_404(client, auth_headers, db):
    # Workflow belongs to user_id=999 (a different user).
    other_user_id = 999
    wf = _seed_workflow(db, user_id=other_user_id)
    db.commit()

    r = client.post(
        f"/api/admin/workflows/{wf.id}/simulate-trigger",
        json={"triggered_by": "manual"},
        headers=auth_headers,
    )
    assert r.status_code == 404


# ── Inactive workflow returns 409 ───────────────────────────────────


def test_inactive_workflow_returns_409(client, auth_headers, db):
    uid = _user_id_from_headers(client, auth_headers)
    wf = _seed_workflow(db, user_id=uid, status=WorkflowStatus.draft)
    db.commit()

    r = client.post(
        f"/api/admin/workflows/{wf.id}/simulate-trigger",
        json={"triggered_by": "manual"},
        headers=auth_headers,
    )
    assert r.status_code == 409
    body = r.json()
    assert body["error"]["details"]["current_status"] == "draft"


# ── Happy path: simulate-trigger ────────────────────────────────────


def test_simulate_trigger_creates_run(client, auth_headers, db, monkeypatch):
    uid = _user_id_from_headers(client, auth_headers)
    wf = _seed_workflow(db, user_id=uid, status=WorkflowStatus.active)
    db.commit()

    # Patch _fire_watch_run so we don't actually kick the engine
    # event loop (which would try to execute non-existent steps).
    fake_run_id = "fake-run-" + uuid.uuid4().hex[:8]
    captured: dict = {}

    async def fake_fire(**kwargs):
        captured.update(kwargs)
        return fake_run_id

    monkeypatch.setattr(
        "backend.workflows.scheduler._fire_watch_run", fake_fire
    )

    r = client.post(
        f"/api/admin/workflows/{wf.id}/simulate-trigger",
        json={
            "triggered_by": "price_alert",
            "triggered_step_index": 0,
            "audit_context": {"reason": "manual test"},
        },
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["run_id"] == fake_run_id
    assert body["started"] is True
    assert body["triggered_by"] == "price_alert"
    assert body["workflow_id"] == str(wf.id)

    # Right args reached _fire_watch_run.
    assert captured["workflow_id"] == str(wf.id)
    assert captured["triggered_by"] == "price_alert"
    assert captured["audit_context"] == {"reason": "manual test"}


def test_simulate_trigger_handles_inactive_workflow_at_engine_layer(
    client, auth_headers, db, monkeypatch
):
    """If the engine returns None (e.g. workflow was paused between our
    state-conflict check and the engine call), the response reflects
    started=False rather than 500."""
    uid = _user_id_from_headers(client, auth_headers)
    wf = _seed_workflow(db, user_id=uid, status=WorkflowStatus.active)
    db.commit()

    async def fake_fire(**kwargs):
        return None  # engine refused to start

    monkeypatch.setattr(
        "backend.workflows.scheduler._fire_watch_run", fake_fire
    )

    r = client.post(
        f"/api/admin/workflows/{wf.id}/simulate-trigger",
        json={"triggered_by": "manual"},
        headers=auth_headers,
    )
    assert r.status_code == 200
    assert r.json()["started"] is False
    assert r.json()["run_id"] is None


# ── approve-all ─────────────────────────────────────────────────────


def test_approve_all_happy_path(client, auth_headers, db):
    uid = _user_id_from_headers(client, auth_headers)
    wf = _seed_workflow(db, user_id=uid)
    run = _seed_run(db, workflow=wf)
    _seed_approval(db, run=run, step_index=2)
    _seed_approval(db, run=run, step_index=5)
    db.commit()

    r = client.post(
        f"/api/admin/workflows/{wf.id}/approve-all",
        json={"decision": "approved"},
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["decided_count"] == 2
    assert body["decision"] == "approved"
    assert body["run_id"] == str(run.id)

    # DB side effect — both approvals now have decision='approved'.
    db.expire_all()
    decisions = [
        a.decision for a in db.query(WorkflowApproval).filter_by(run_id=run.id).all()
    ]
    assert decisions == ["approved", "approved"]


def test_approve_all_rejected_also_cancels_run(client, auth_headers, db):
    uid = _user_id_from_headers(client, auth_headers)
    wf = _seed_workflow(db, user_id=uid)
    run = _seed_run(db, workflow=wf)
    _seed_approval(db, run=run, step_index=1)
    db.commit()

    r = client.post(
        f"/api/admin/workflows/{wf.id}/approve-all",
        json={"decision": "rejected"},
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text
    db.expire_all()

    run_after = db.query(WorkflowRun).filter_by(id=run.id).one()
    assert run_after.status == RunStatus.cancelled
    assert run_after.finished_at is not None
    assert "rejected" in (run_after.error_message or "").lower()


def test_approve_all_with_no_pending_returns_zero(client, auth_headers, db):
    uid = _user_id_from_headers(client, auth_headers)
    wf = _seed_workflow(db, user_id=uid)
    _seed_run(db, workflow=wf)  # no approvals attached
    db.commit()

    r = client.post(
        f"/api/admin/workflows/{wf.id}/approve-all",
        json={},
        headers=auth_headers,
    )
    assert r.status_code == 200
    assert r.json()["decided_count"] == 0


def test_approve_all_no_runs_returns_404(client, auth_headers, db):
    uid = _user_id_from_headers(client, auth_headers)
    wf = _seed_workflow(db, user_id=uid)
    db.commit()

    r = client.post(
        f"/api/admin/workflows/{wf.id}/approve-all",
        json={},
        headers=auth_headers,
    )
    assert r.status_code == 404


def test_approve_all_with_explicit_run_id(client, auth_headers, db):
    uid = _user_id_from_headers(client, auth_headers)
    wf = _seed_workflow(db, user_id=uid)
    old_run = _seed_run(db, workflow=wf)
    _seed_approval(db, run=old_run, step_index=0)
    # Newer run with its own approval
    new_run = _seed_run(db, workflow=wf)
    _seed_approval(db, run=new_run, step_index=0)
    db.commit()

    # Target the OLDER run explicitly.
    r = client.post(
        f"/api/admin/workflows/{wf.id}/approve-all",
        json={"run_id": str(old_run.id)},
        headers=auth_headers,
    )
    assert r.status_code == 200
    assert r.json()["run_id"] == str(old_run.id)
    db.expire_all()

    # Newer run's approval should NOT be touched.
    new_appr = db.query(WorkflowApproval).filter_by(run_id=new_run.id).one()
    assert new_appr.decision is None
