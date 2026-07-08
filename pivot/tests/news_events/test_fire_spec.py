"""Tests for backend.news_events.pipeline.propose.fire_spec.

Exercises the audit-row write, the state flip, idempotency via the
UNIQUE constraint, and the workflow handoff via the Touch-1 seam
(``fire_external_event`` is monkey-patched).
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest

from backend.news_events.models import (
    NewsEventSpec,
    NewsFiredEvent,
)
from backend.news_events.pipeline.aggregate import FiringDecision
from backend.news_events.pipeline import propose as propose_mod


def _seed_spec(
    db,
    *,
    state: str = "active",
    workflow_id: str | None = None,
) -> NewsEventSpec:
    spec = NewsEventSpec(
        user_id=1,
        tier="tier1",
        description="RBI cuts repo rate",
        resolution_criteria={"primary_sources": ["rbi_press_releases"]},
        retraction_policy={"safety_window_minutes": 60, "action": "cancel_and_alert"},
        keyword_set={"must_have_one": ["RBI"], "must_have_one_of": [], "must_not_have": []},
        state=state,
        workflow_id=workflow_id,
    )
    db.add(spec)
    db.flush()
    return spec


def _fire_decision(spec_id: str) -> FiringDecision:
    return FiringDecision(
        spec_id=spec_id,
        status="fire",
        reason="tier1 primary YES landed",
        supporting_classification_ids=["c-1", "c-2"],
        aggregated_confidence=0.92,
    )


def _hold_decision(spec_id: str) -> FiringDecision:
    return FiringDecision(
        spec_id=spec_id,
        status="hold",
        reason="no primary YES yet",
    )


def test_fire_persists_audit_row_and_flips_state(db, monkeypatch):
    called = {"n": 0}

    async def fake_fire(**kwargs):
        called["n"] += 1
        return "wf-run-id-123"

    # No workflow attached → fire_external_event must NOT be called.
    monkeypatch.setattr(propose_mod, "_minutes",
                        propose_mod._minutes)  # touch-test the import
    # Patch the module reference used by the function (imported lazily).
    monkeypatch.setattr(
        "backend.workflows.scheduler.fire_external_event", fake_fire
    )

    spec = _seed_spec(db, workflow_id=None)
    db.commit()
    outcome = asyncio.run(
        propose_mod.fire_spec(db, spec=spec, decision=_fire_decision(spec.id))
    )
    assert outcome.fired_event_id is not None
    assert outcome.workflow_run_id is None
    assert outcome.workflow_attached is False
    assert called["n"] == 0

    db.refresh(spec)
    assert spec.state == "fired"

    rows = db.query(NewsFiredEvent).filter(
        NewsFiredEvent.event_spec_id == spec.id
    ).all()
    assert len(rows) == 1
    assert rows[0].aggregated_confidence == pytest.approx(0.92)
    assert rows[0].supporting_classification_ids == ["c-1", "c-2"]


def test_fire_with_workflow_calls_seam_and_links_run(db, monkeypatch):
    called = {"audit_context": None}

    async def fake_fire(*, workflow_id, triggered_step_index, fired_at, audit_context):
        called["audit_context"] = dict(audit_context)
        return "wf-run-id-abc"

    monkeypatch.setattr(
        "backend.workflows.scheduler.fire_external_event", fake_fire
    )

    spec = _seed_spec(db, workflow_id="some-workflow-id")
    db.commit()
    outcome = asyncio.run(
        propose_mod.fire_spec(db, spec=spec, decision=_fire_decision(spec.id))
    )
    assert outcome.workflow_attached is True
    assert outcome.workflow_run_id == "wf-run-id-abc"

    db.refresh(spec)
    assert spec.state == "fired"
    # Audit row has the workflow_run_id linked back.
    row = db.query(NewsFiredEvent).filter(
        NewsFiredEvent.event_spec_id == spec.id
    ).one()
    assert row.workflow_run_id == "wf-run-id-abc"

    # The audit_context the seam saw carries the right shape.
    ctx = called["audit_context"]
    assert ctx is not None
    assert ctx["spec_id"] == spec.id
    assert ctx["tier"] == "tier1"
    assert "fired_event_id" in ctx
    assert ctx["supporting_classification_ids"] == ["c-1", "c-2"]


def test_hold_decision_is_a_noop(db, monkeypatch):
    spec = _seed_spec(db)
    db.commit()
    outcome = asyncio.run(
        propose_mod.fire_spec(db, spec=spec, decision=_hold_decision(spec.id))
    )
    assert outcome.fired_event_id is None
    db.refresh(spec)
    assert spec.state == "active"  # untouched
    assert db.query(NewsFiredEvent).count() == 0


def test_already_fired_short_circuits(db, monkeypatch):
    async def fake_fire(**kwargs):
        raise AssertionError("must not be called")

    monkeypatch.setattr(
        "backend.workflows.scheduler.fire_external_event", fake_fire
    )

    spec = _seed_spec(db, state="fired", workflow_id="wf-x")
    db.commit()
    outcome = asyncio.run(
        propose_mod.fire_spec(db, spec=spec, decision=_fire_decision(spec.id))
    )
    assert outcome.duplicate is True
    assert outcome.fired_event_id is None


def test_workflow_handoff_failure_does_not_corrupt_audit(db, monkeypatch):
    async def boom(**kwargs):
        raise RuntimeError("engine offline")

    monkeypatch.setattr(
        "backend.workflows.scheduler.fire_external_event", boom
    )

    spec = _seed_spec(db, workflow_id="wf-x")
    db.commit()
    outcome = asyncio.run(
        propose_mod.fire_spec(db, spec=spec, decision=_fire_decision(spec.id))
    )
    # Audit row landed, state flipped, but workflow_run_id stayed None.
    assert outcome.fired_event_id is not None
    assert outcome.workflow_run_id is None
    db.refresh(spec)
    assert spec.state == "fired"
    row = db.query(NewsFiredEvent).filter(
        NewsFiredEvent.event_spec_id == spec.id
    ).one()
    assert row.workflow_run_id is None


def test_concurrent_double_fire_is_idempotent(db, monkeypatch):
    """Simulates two near-simultaneous fire decisions on the same
    spec — the second hits the UNIQUE(event_spec_id) and returns
    duplicate=True instead of writing a second row."""

    async def fake_fire(**kwargs):
        return "run-1"

    monkeypatch.setattr(
        "backend.workflows.scheduler.fire_external_event", fake_fire
    )

    spec = _seed_spec(db, workflow_id="wf-x")
    db.commit()
    out1 = asyncio.run(
        propose_mod.fire_spec(db, spec=spec, decision=_fire_decision(spec.id))
    )
    # Re-load spec — state should be fired now. Force state back to
    # active to bypass the early short-circuit and stress the
    # UNIQUE branch directly.
    spec.state = "active"
    db.commit()
    out2 = asyncio.run(
        propose_mod.fire_spec(db, spec=spec, decision=_fire_decision(spec.id))
    )
    assert out1.fired_event_id is not None
    assert out2.duplicate is True
    # Only one audit row.
    assert (
        db.query(NewsFiredEvent)
        .filter(NewsFiredEvent.event_spec_id == spec.id)
        .count()
        == 1
    )
