"""Trigger executors re-verify their condition against LIVE data before
letting a run proceed (2026-07-06 live-test regression).

BUG this fixes: every trigger.* executor was an unconditional no-op — a
MANUAL run (POST /workflows/{id}/run) creates a run row with NO pre-check,
so a "buy at 9:20 AM" schedule agent manually run at 3:35 PM placed a real
order. Fixed by having the trigger executors themselves re-check the
condition (schedule window / price / indicator / compound tree) and raise
_ConditionFail when it is DEFINITIVELY not currently true — the engine's
existing condition_fail path (RunStatus.succeeded, halt_reason=
'condition_not_met') then halts the run before any action step runs.

Mirrors tests/workflows/test_engine.py's fixtures/helpers.
"""
from __future__ import annotations

import asyncio
import datetime as dt
from typing import Any

import pytest
from sqlalchemy.orm import Session

from backend import models
from backend.workflows import engine as engine_mod
from backend.workflows.engine import WorkflowEngine


@pytest.fixture(autouse=True)
def _stub_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(engine_mod, "_engine_sleep", lambda s: None)


@pytest.fixture
def session_factory(monkeypatch: pytest.MonkeyPatch, db: Session) -> None:
    from tests.conftest import TestSessionLocal
    monkeypatch.setattr(engine_mod, "SessionLocal", TestSessionLocal)


def _make_user(db: Session, email: str = "trig_recheck@pivot.test") -> models.User:
    user = models.User(email=email, hashed_password="x", full_name="Trig Test")
    db.add(user)
    db.flush()
    return user


def _make_workflow(db: Session, user_id: int) -> models.Workflow:
    wf = models.Workflow(
        user_id=user_id, name="Trigger recheck test",
        single_instance=True, status=models.WorkflowStatus.active,
    )
    db.add(wf)
    db.flush()
    return wf


def _add_step(
    db: Session, wf: models.Workflow, idx: int, step_type: str,
    config: dict[str, Any],
) -> models.WorkflowStep:
    step = models.WorkflowStep(
        workflow_id=wf.id, step_index=idx, step_type=step_type, config=config,
    )
    db.add(step)
    db.flush()
    return step


def _make_run(db: Session, wf: models.Workflow, *, triggered_by: str = "manual") -> models.WorkflowRun:
    run = models.WorkflowRun(
        workflow_id=wf.id, workflow_version=wf.version,
        triggered_by=triggered_by, status=models.RunStatus.running, context={},
    )
    db.add(run)
    db.commit()
    return run


def _order_step(db, wf, idx):
    return _add_step(db, wf, idx, "action.place_order", {
        "symbol": "RELIANCE", "side": "buy", "quantity": 1,
        "order_type": "market", "requires_approval": False,
    })


# ── trigger.schedule ─────────────────────────────────────────────────


def test_manual_run_of_schedule_far_outside_window_does_not_place_order(
    session_factory: None, db: Session,
) -> None:
    """The exact bug: a 9:20 AM cron manually run at an arbitrary other
    time must NOT execute the action step."""
    user = _make_user(db, "schedule_outside@pivot.test")
    wf = _make_workflow(db, user.id)
    # A cron that will not match "right now" in any CI timezone: fixed to
    # a specific minute far in the past relative to any run.
    _add_step(db, wf, 0, "trigger.schedule", {
        "cron": "20 9 * * 1-5", "timezone": "Asia/Kolkata",
    })
    _order_step(db, wf, 1)
    db.commit()
    run = _make_run(db, wf, triggered_by="manual")

    asyncio.run(WorkflowEngine().execute_run(run.id))

    db.expire_all()
    final = db.query(models.WorkflowRun).filter_by(id=run.id).one()
    # Skip this assertion in the vanishingly rare case the test itself runs
    # within the tolerance window of 9:20 IST.
    now_ist = dt.datetime.now(dt.timezone.utc).astimezone(
        dt.timezone(dt.timedelta(hours=5, minutes=30))
    )
    if abs((now_ist.replace(hour=9, minute=20, second=0, microsecond=0) - now_ist).total_seconds()) < 300:
        pytest.skip("test coincidentally running within the cron's tolerance window")
    assert final.status == models.RunStatus.succeeded
    assert final.halt_reason == "condition_not_met"
    order_step = (
        db.query(models.WorkflowRunStep)
        .filter_by(run_id=run.id, step_index=1)
        .one_or_none()
    )
    assert order_step is None  # never reached — no order was placed


def test_manual_run_of_schedule_within_window_proceeds(
    session_factory: None, db: Session, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A schedule check within tolerance of "now" (the normal auto-fire
    case, and a manual run at the right moment) still proceeds."""
    import backend.workflows.steps.triggers as triggers_mod
    monkeypatch.setattr(triggers_mod, "_schedule_condition_holds_now", lambda cfg: True)

    user = _make_user(db, "schedule_inside@pivot.test")
    wf = _make_workflow(db, user.id)
    _add_step(db, wf, 0, "trigger.schedule", {
        "cron": "20 9 * * 1-5", "timezone": "Asia/Kolkata",
    })
    _order_step(db, wf, 1)
    db.commit()
    run = _make_run(db, wf, triggered_by="schedule")

    asyncio.run(WorkflowEngine().execute_run(run.id))

    db.expire_all()
    final = db.query(models.WorkflowRun).filter_by(id=run.id).one()
    assert final.status == models.RunStatus.succeeded
    assert final.halt_reason is None
    # 2026-07-06 audit finding: the trigger step now records a forensic
    # snapshot of what confirmed it, instead of a bare no-op None.
    trigger_step = (
        db.query(models.WorkflowRunStep)
        .filter_by(run_id=run.id, step_index=0)
        .one()
    )
    assert trigger_step.output is not None
    assert trigger_step.output.get("cron") == "20 9 * * 1-5"
    order_step = (
        db.query(models.WorkflowRunStep)
        .filter_by(run_id=run.id, step_index=1)
        .one()
    )
    assert order_step.status == models.StepStatus.succeeded


# ── trigger.price ────────────────────────────────────────────────────


def test_manual_run_of_price_trigger_not_crossed_does_not_place_order(
    session_factory: None, db: Session, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # triggers.py imports _batch_fetch_prices lazily inside the function
    # body, so patch it where it's looked up (the scheduler module).
    import backend.workflows.scheduler as sched_mod
    monkeypatch.setattr(sched_mod, "_batch_fetch_prices", lambda instruments: {"NSE:RELIANCE": 1300.0})

    user = _make_user(db, "price_not_crossed@pivot.test")
    wf = _make_workflow(db, user.id)
    _add_step(db, wf, 0, "trigger.price", {
        "symbol": "RELIANCE", "exchange": "NSE",
        "operator": "crosses_above", "value": 1350.0,
    })
    _order_step(db, wf, 1)
    db.commit()
    run = _make_run(db, wf, triggered_by="manual")

    asyncio.run(WorkflowEngine().execute_run(run.id))

    db.expire_all()
    final = db.query(models.WorkflowRun).filter_by(id=run.id).one()
    assert final.status == models.RunStatus.succeeded
    assert final.halt_reason == "condition_not_met"


def test_manual_run_of_price_trigger_data_unavailable_fails_open(
    session_factory: None, db: Session, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No quote available -> can't confirm false -> proceed (fail open),
    matching the existing "never block on missing data" convention."""
    import backend.workflows.scheduler as sched_mod
    monkeypatch.setattr(sched_mod, "_batch_fetch_prices", lambda instruments: {})

    user = _make_user(db, "price_no_data@pivot.test")
    wf = _make_workflow(db, user.id)
    _add_step(db, wf, 0, "trigger.price", {
        "symbol": "RELIANCE", "exchange": "NSE",
        "operator": "crosses_above", "value": 1350.0,
    })
    _order_step(db, wf, 1)
    db.commit()
    run = _make_run(db, wf, triggered_by="manual")

    asyncio.run(WorkflowEngine().execute_run(run.id))

    db.expire_all()
    final = db.query(models.WorkflowRun).filter_by(id=run.id).one()
    assert final.status == models.RunStatus.succeeded
    assert final.halt_reason is None


# ── trigger.indicator ────────────────────────────────────────────────


def test_manual_run_of_indicator_trigger_not_met_does_not_place_order(
    session_factory: None, db: Session, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import backend.workflows.scheduler as sched_mod
    monkeypatch.setattr(sched_mod, "_compute_indicator_sync", lambda *a, **k: 66.6)

    user = _make_user(db, "indicator_not_met@pivot.test")
    wf = _make_workflow(db, user.id)
    _add_step(db, wf, 0, "trigger.indicator", {
        "symbol": "NIFTYBEES", "indicator": "rsi", "period": 14,
        "operator": "<", "value": 30.0,
    })
    _order_step(db, wf, 1)
    db.commit()
    run = _make_run(db, wf, triggered_by="manual")

    asyncio.run(WorkflowEngine().execute_run(run.id))

    db.expire_all()
    final = db.query(models.WorkflowRun).filter_by(id=run.id).one()
    assert final.status == models.RunStatus.succeeded
    assert final.halt_reason == "condition_not_met"
    # A blocked fire still records WHY (2026-07-06 trade-log completeness
    # fix) — not just the bare {"passed": False} it used to.
    trigger_step = (
        db.query(models.WorkflowRunStep)
        .filter_by(run_id=run.id, step_index=0)
        .one()
    )
    assert trigger_step.output["passed"] is False
    assert trigger_step.output["observed_value"] == 66.6
    assert trigger_step.output["threshold"] == 30.0


def test_manual_run_of_indicator_trigger_met_proceeds(
    session_factory: None, db: Session, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import backend.workflows.scheduler as sched_mod
    monkeypatch.setattr(sched_mod, "_compute_indicator_sync", lambda *a, **k: 22.0)

    user = _make_user(db, "indicator_met@pivot.test")
    wf = _make_workflow(db, user.id)
    _add_step(db, wf, 0, "trigger.indicator", {
        "symbol": "NIFTYBEES", "indicator": "rsi", "period": 14,
        "operator": "<", "value": 30.0,
    })
    _order_step(db, wf, 1)
    db.commit()
    run = _make_run(db, wf, triggered_by="manual")

    asyncio.run(WorkflowEngine().execute_run(run.id))

    db.expire_all()
    final = db.query(models.WorkflowRun).filter_by(id=run.id).one()
    assert final.status == models.RunStatus.succeeded
    assert final.halt_reason is None
    trigger_step = (
        db.query(models.WorkflowRunStep)
        .filter_by(run_id=run.id, step_index=0)
        .one()
    )
    assert trigger_step.output["observed_value"] == 22.0
    assert trigger_step.output["threshold"] == 30.0
    assert trigger_step.output["indicator"] == "rsi"
