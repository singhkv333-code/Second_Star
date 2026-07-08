"""End-to-end dry-run for the scheduled-macro watcher.

Proves the "RBI cuts → register buy" path without waiting for a real MPC
date: the calendar's ``due_event`` and the layered ``verify_macro_outcome``
are monkeypatched, so one ``_poll_scheduled_macro_triggers`` tick fires
exactly once on a confident match, persists the per-occurrence latch, and
does NOT re-fire on the next tick. The negative path (outcome doesn't
match) fires nothing.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

import pytest
from sqlalchemy.orm import Session

from backend.macro_events.calendar import MacroEventDef
from backend.macro_events.outcomes import OutcomeResult
from backend.models import (
    RunStatus,
    Workflow,
    WorkflowRun,
    WorkflowStatus,
    WorkflowStep,
)
from backend.workflows import engine as engine_mod
from backend.workflows.scheduler import _poll_scheduled_macro_triggers


@pytest.fixture
def _scheduler_test_db(
    monkeypatch: pytest.MonkeyPatch, workflow_db: Session,
) -> None:
    """Repoint the watcher's SessionLocal at the test session + inline
    asyncio.to_thread (same shape as test_watcher's fixture)."""
    class _SharedSession:
        def __init__(self, real: Session) -> None:
            self._real = real

        def __enter__(self) -> Session:
            return self._real

        def __exit__(self, *a: object) -> None:
            pass

        def __getattr__(self, name: str) -> object:
            attr = getattr(self._real, name)
            if name == "close":
                return lambda: None
            return attr

    monkeypatch.setattr(
        "backend.workflows.scheduler.SessionLocal",
        lambda: _SharedSession(workflow_db),
    )

    async def _inline(func: Any, *args: Any, **kwargs: Any) -> Any:
        return func(*args, **kwargs)

    monkeypatch.setattr("asyncio.to_thread", _inline)


_DUE = MacroEventDef(
    kind="rbi_mpc",
    fire_at_utc=datetime(2026, 6, 6, 4, 30, tzinfo=timezone.utc),
    verify_window_minutes=240,
    source_of_truth_id="rbi_mpc",
    label="RBI MPC Outcome",
)


def _seed_macro_workflow(db: Session) -> Workflow:
    wf = Workflow(
        user_id=1, name="rbi-cut-buy",
        status=WorkflowStatus.active, version=1,
    )
    db.add(wf)
    db.flush()
    db.add(WorkflowStep(
        workflow_id=wf.id, step_index=0,
        step_type="trigger.scheduled_macro",
        config={"kind": "rbi_mpc", "expected_outcome": "cut"},
        label=None,
    ))
    db.add(WorkflowStep(
        workflow_id=wf.id, step_index=1,
        step_type="action.place_order",
        config={"symbol": "NIFTYBEES", "side": "buy",
                "quantity": 1, "order_type": "market"},
        label=None,
    ))
    db.flush()
    db.refresh(wf)
    return wf


def _patch_calendar_and_verifier(
    monkeypatch: pytest.MonkeyPatch, *, matched: bool, decision: str = "cut",
) -> None:
    monkeypatch.setattr(
        "backend.macro_events.calendar.due_event",
        lambda kind, now: _DUE if kind == "rbi_mpc" else None,
    )

    async def _fake_verify(kind: str, expected: str, **kw: Any) -> OutcomeResult:
        return OutcomeResult(
            matched=matched,
            decision=decision,  # type: ignore[arg-type]
            confidence=0.97 if matched else 0.0,
            tier="official",
            evidence="RBI cuts repo rate by 25 bps",
        )

    monkeypatch.setattr(
        "backend.macro_events.verifier.verify_macro_outcome", _fake_verify,
    )


@pytest.mark.asyncio
async def test_macro_trigger_fires_once_on_confident_cut(
    workflow_db: Session,
    monkeypatch: pytest.MonkeyPatch,
    _scheduler_test_db: None,
) -> None:
    wf = _seed_macro_workflow(workflow_db)
    _patch_calendar_and_verifier(monkeypatch, matched=True, decision="cut")

    fired: list[str] = []

    class _StubEngine:
        async def execute_run(self, run_id: str) -> None:
            fired.append(run_id)

    monkeypatch.setattr(engine_mod, "WorkflowEngine", _StubEngine)

    # Tick 1 → fires.
    await _poll_scheduled_macro_triggers()
    await asyncio.sleep(0)

    runs = (
        workflow_db.query(WorkflowRun)
        .filter(WorkflowRun.workflow_id == str(wf.id))
        .all()
    )
    assert len(runs) == 1
    assert runs[0].triggered_by == "event_alert"
    assert runs[0].status == RunStatus.running
    assert fired == [str(runs[0].id)]

    # Latch persisted on the trigger step, keyed to the occurrence.
    step0 = (
        workflow_db.query(WorkflowStep)
        .filter(WorkflowStep.workflow_id == str(wf.id),
                WorkflowStep.step_index == 0)
        .first()
    )
    assert step0.config.get("_macro_fired_for") == _DUE.instance_key()

    # Tick 2 → dedup, no second run.
    await _poll_scheduled_macro_triggers()
    await asyncio.sleep(0)
    runs2 = (
        workflow_db.query(WorkflowRun)
        .filter(WorkflowRun.workflow_id == str(wf.id))
        .all()
    )
    assert len(runs2) == 1, "must not re-fire for the same occurrence"


@pytest.mark.asyncio
async def test_macro_trigger_does_not_fire_on_hold(
    workflow_db: Session,
    monkeypatch: pytest.MonkeyPatch,
    _scheduler_test_db: None,
) -> None:
    """Expected 'cut' but the verifier did not confirm a match → no fire,
    no latch."""
    wf = _seed_macro_workflow(workflow_db)
    _patch_calendar_and_verifier(monkeypatch, matched=False, decision="hold")

    class _StubEngine:
        async def execute_run(self, run_id: str) -> None:  # pragma: no cover
            raise AssertionError("must not fire")

    monkeypatch.setattr(engine_mod, "WorkflowEngine", _StubEngine)

    await _poll_scheduled_macro_triggers()
    await asyncio.sleep(0)

    runs = (
        workflow_db.query(WorkflowRun)
        .filter(WorkflowRun.workflow_id == str(wf.id))
        .all()
    )
    assert runs == []
    step0 = (
        workflow_db.query(WorkflowStep)
        .filter(WorkflowStep.workflow_id == str(wf.id),
                WorkflowStep.step_index == 0)
        .first()
    )
    assert "_macro_fired_for" not in (step0.config or {})


@pytest.mark.asyncio
async def test_macro_trigger_skips_outside_verify_window(
    workflow_db: Session,
    monkeypatch: pytest.MonkeyPatch,
    _scheduler_test_db: None,
) -> None:
    """No occurrence currently due → nothing fires (verifier never even
    called)."""
    wf = _seed_macro_workflow(workflow_db)
    monkeypatch.setattr(
        "backend.macro_events.calendar.due_event",
        lambda kind, now: None,
    )

    async def _boom(*a: Any, **k: Any) -> OutcomeResult:  # pragma: no cover
        raise AssertionError("verifier must not run outside the window")

    monkeypatch.setattr(
        "backend.macro_events.verifier.verify_macro_outcome", _boom,
    )

    class _StubEngine:
        async def execute_run(self, run_id: str) -> None:  # pragma: no cover
            raise AssertionError("must not fire")

    monkeypatch.setattr(engine_mod, "WorkflowEngine", _StubEngine)

    await _poll_scheduled_macro_triggers()
    await asyncio.sleep(0)
    assert (
        workflow_db.query(WorkflowRun)
        .filter(WorkflowRun.workflow_id == str(wf.id)).all() == []
    )
