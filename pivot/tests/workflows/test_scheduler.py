"""Tests for backend/workflows/scheduler.py — the cron-trigger fan-out.

Coverage:
  - compute_next_run_at: happy path, invalid cron, unknown timezone.
  - upsert_workflow_schedule: sets next_run_at for active+schedule,
    clears for paused / archived / non-schedule trigger.
  - _poll_due_workflows: creates a `triggered_by='schedule'` run for
    every active+due workflow, recomputes next_run_at past now.
  - InvalidCronError surfaced through the activate router → 422.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from backend.models import (
    RunStatus,
    Workflow,
    WorkflowRun,
    WorkflowStatus,
    WorkflowStep,
)
from backend.workflows import engine as engine_mod
from backend.workflows.scheduler import (
    InvalidCronError,
    _poll_due_workflows,
    compute_next_run_at,
    upsert_workflow_schedule,
)


# ── compute_next_run_at ──────────────────────────────────────────────


def test_compute_next_run_at_returns_utc_aware_datetime() -> None:
    """Cron `*/5 * * * *` at UTC always fires within the next 5 min."""
    now = datetime.now(timezone.utc)
    nxt = compute_next_run_at("*/5 * * * *", "UTC", after=now)
    assert nxt.tzinfo is not None
    assert nxt.utcoffset() == timedelta(0)
    assert now < nxt <= now + timedelta(minutes=5)


def test_compute_next_run_at_honors_timezone() -> None:
    """A cron expressed in IST should not fire at the same UTC instant
    as the same cron expressed in UTC (unless they happen to align)."""
    now = datetime(2026, 5, 2, 12, 0, 0, tzinfo=timezone.utc)
    nxt_utc = compute_next_run_at("0 9 * * *", "UTC", after=now)
    nxt_ist = compute_next_run_at("0 9 * * *", "Asia/Kolkata", after=now)
    # 09:00 IST is 03:30 UTC; 09:00 UTC is 09:00 UTC. Different instants.
    assert nxt_utc != nxt_ist


def test_compute_next_run_at_rejects_invalid_cron() -> None:
    with pytest.raises(InvalidCronError):
        compute_next_run_at("99 99 * * *", "UTC")


def test_compute_next_run_at_rejects_unknown_timezone() -> None:
    with pytest.raises(InvalidCronError):
        compute_next_run_at("0 9 * * *", "Mars/Olympus")


# ── upsert_workflow_schedule ─────────────────────────────────────────


def _make_workflow(
    db: Session,
    *,
    user_id: int = 1,
    status: WorkflowStatus = WorkflowStatus.draft,
    trigger_type: str = "trigger.schedule",
    cron: str = "*/5 * * * *",
    tz: str = "UTC",
) -> Workflow:
    """Insert a workflow + its trigger step. Returns the wf with steps
    eagerly loaded for upsert to walk."""
    wf = Workflow(
        user_id=user_id,
        name="t",
        status=status,
        version=1,
    )
    db.add(wf)
    db.flush()
    cfg: dict[str, object] = {}
    if trigger_type == "trigger.schedule":
        cfg = {"cron": cron, "timezone": tz}
    step = WorkflowStep(
        workflow_id=wf.id,
        step_index=0,
        step_type=trigger_type,
        config=cfg,
        label=None,
    )
    db.add(step)
    db.flush()
    db.refresh(wf)
    return wf


def test_upsert_sets_next_run_at_for_active_schedule(
    workflow_db: Session,
) -> None:
    wf = _make_workflow(
        workflow_db, status=WorkflowStatus.active, cron="*/1 * * * *"
    )
    upsert_workflow_schedule(workflow_db, wf)
    assert wf.next_run_at is not None
    assert wf.next_run_at > datetime.now(timezone.utc) - timedelta(seconds=1)
    assert wf.next_run_at <= datetime.now(timezone.utc) + timedelta(minutes=1)


def test_upsert_clears_next_run_at_when_paused(
    workflow_db: Session,
) -> None:
    wf = _make_workflow(workflow_db, status=WorkflowStatus.active)
    upsert_workflow_schedule(workflow_db, wf)
    assert wf.next_run_at is not None
    wf.status = WorkflowStatus.paused
    upsert_workflow_schedule(workflow_db, wf)
    assert wf.next_run_at is None


def test_upsert_clears_for_non_schedule_trigger(
    workflow_db: Session,
) -> None:
    wf = _make_workflow(
        workflow_db,
        status=WorkflowStatus.active,
        trigger_type="trigger.manual",
    )
    upsert_workflow_schedule(workflow_db, wf)
    assert wf.next_run_at is None


def test_upsert_raises_on_invalid_cron(workflow_db: Session) -> None:
    wf = _make_workflow(
        workflow_db, status=WorkflowStatus.active, cron="not a cron"
    )
    with pytest.raises(InvalidCronError):
        upsert_workflow_schedule(workflow_db, wf)


# ── _poll_due_workflows ──────────────────────────────────────────────


@pytest.fixture
def _scheduler_uses_test_db(
    monkeypatch: pytest.MonkeyPatch, workflow_db: Session,
) -> None:
    """Override the scheduler module's `SessionLocal` to return the
    test fixture's session itself, wrapped in a no-op close. This way
    `_fetch_due` and `_fire_one` see the same flushed-but-uncommitted
    rows the test just inserted — same identity map, same transaction.

    Production code is unchanged; this is purely a test-layer pivot
    around SQLite + StaticPool's cross-session visibility quirks
    (commits don't propagate cleanly inside a fixture-held outer
    transaction). Real PostgreSQL with proper isolation behaves
    differently and the production threading path works as written."""
    class _SharedSession:
        def __init__(self, real: Session) -> None:
            self._real = real
        def __enter__(self) -> Session:
            return self._real
        def __exit__(self, *a: object) -> None:
            pass
        def __getattr__(self, name: str) -> object:
            attr = getattr(self._real, name)
            # Intercept close() so the test session isn't torn down.
            if name == "close":
                return lambda: None
            return attr
    monkeypatch.setattr(
        "backend.workflows.scheduler.SessionLocal",
        lambda: _SharedSession(workflow_db),
    )

    async def _inline(func, *args, **kwargs):
        return func(*args, **kwargs)
    monkeypatch.setattr("asyncio.to_thread", _inline)


@pytest.mark.asyncio
async def test_poll_creates_run_for_due_workflow(
    workflow_db: Session,
    monkeypatch: pytest.MonkeyPatch,
    _scheduler_uses_test_db: None,
) -> None:
    """Active workflow with next_run_at in the past → poll creates a
    `triggered_by='schedule'` run and recomputes next_run_at past now."""
    wf = _make_workflow(
        workflow_db, status=WorkflowStatus.active, cron="*/5 * * * *"
    )
    # Force it past-due.
    wf.next_run_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    workflow_db.flush()
    wf_id = str(wf.id)

    # Stub the engine so the poll doesn't actually execute the run —
    # we're testing scheduling behavior, not execution.
    fired: list[str] = []

    class _StubEngine:
        async def execute_run(self, run_id: str) -> None:
            fired.append(run_id)

    monkeypatch.setattr(engine_mod, "WorkflowEngine", _StubEngine)

    await _poll_due_workflows()
    # Let any asyncio.create_task callbacks settle.
    await asyncio.sleep(0)

    workflow_db.expire_all()
    runs = (
        workflow_db.query(WorkflowRun)
        .filter(WorkflowRun.workflow_id == wf_id)
        .all()
    )
    assert len(runs) == 1
    assert runs[0].triggered_by == "schedule"
    assert runs[0].status == RunStatus.running

    # next_run_at should have advanced past now. SQLite drops tzinfo
    # on round-trip — normalise both sides to naive UTC for compare.
    refreshed = workflow_db.query(Workflow).filter(Workflow.id == wf_id).first()
    assert refreshed is not None
    assert refreshed.next_run_at is not None
    nra = refreshed.next_run_at
    if nra.tzinfo is None:
        nra = nra.replace(tzinfo=timezone.utc)
    assert nra > datetime.now(timezone.utc)

    # Engine got the new run id.
    assert fired == [str(runs[0].id)]


@pytest.mark.asyncio
async def test_poll_skips_paused_workflow(
    workflow_db: Session,
    monkeypatch: pytest.MonkeyPatch,
    _scheduler_uses_test_db: None,
) -> None:
    """Paused workflows must never fire from the poller, even if
    next_run_at is somehow in the past (defense in depth)."""
    wf = _make_workflow(workflow_db, status=WorkflowStatus.paused)
    wf.next_run_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    workflow_db.flush()

    class _StubEngine:
        async def execute_run(self, run_id: str) -> None:  # pragma: no cover
            raise AssertionError("paused workflow must not fire")

    monkeypatch.setattr(engine_mod, "WorkflowEngine", _StubEngine)
    await _poll_due_workflows()
    await asyncio.sleep(0)

    # Scope to this test's workflow only; the in-memory DB leaks rows
    # across tests because routers commit through the FastAPI dep
    # (a pre-existing fixture quirk, not our problem to solve here).
    runs = (
        workflow_db.query(WorkflowRun)
        .filter(WorkflowRun.workflow_id == str(wf.id))
        .all()
    )
    assert runs == []


# ── Activate router → 422 on bad cron ────────────────────────────────


def test_activate_rejects_invalid_cron_with_422(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """Closes reviewer Day-2 edge case #1: bad cron at activation → 422,
    not silently arming a dead schedule."""
    create_resp = client.post(
        "/api/workflows",
        headers=auth_headers,
        json={
            "name": "bad-cron",
            "description": None,
            "single_instance": True,
            "steps": [
                {
                    "step_type": "trigger.schedule",
                    "label": "bad",
                    "config": {"cron": "99 99 * * *", "timezone": "UTC"},
                },
                {
                    "step_type": "notify.log",
                    "label": None,
                    "config": {"message": "x"},
                },
            ],
        },
    )
    # Cron syntax isn't validated at create (only structural). So
    # create should pass; activate is where the cron gets parsed.
    assert create_resp.status_code in (201, 422), create_resp.text
    if create_resp.status_code == 422:
        # If the registry rejected at create, we already get the
        # canonical envelope — done.
        body = create_resp.json()
        assert body["error"]["code"] == "validation_error"
        return

    wf_id = create_resp.json()["id"]
    activate_resp = client.post(
        f"/api/workflows/{wf_id}/activate", headers=auth_headers
    )
    assert activate_resp.status_code == 422, activate_resp.text
    body = activate_resp.json()
    assert body["error"]["code"] == "validation_error"
    assert "cron" in body["error"]["message"].lower()
