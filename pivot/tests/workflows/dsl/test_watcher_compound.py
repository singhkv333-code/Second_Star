"""End-to-end test for the trigger.compound watcher branch.

Exercises the path the live watcher takes when a workflow has a
``trigger.compound`` step. The Live data accessor is monkey-patched
out so the test runs offline.
"""
from __future__ import annotations

import asyncio
from typing import Optional

import pytest

from backend import models
from backend.workflows import scheduler
from backend.workflows.dsl.data_accessor import LiveDataAccessor
from backend.workflows.scheduler import _evaluate_compound_trigger


# ── Fake accessor for the watcher integration test ─────────────────


class _FakeAccessor:
    """Returns the values set on the class attributes. Each test
    instance manipulates the class state directly because
    LiveDataAccessor is constructed fresh per evaluation."""

    prices: dict[str, float] = {}
    indicators: dict[tuple[str, str, int], float] = {}
    volumes: dict[tuple[str, int], float] = {}

    def __init__(self):
        # Empty constructor — class-level state used.
        pass

    def get_price(self, *, symbol, exchange="NSE"):
        return _FakeAccessor.prices.get(symbol)

    def get_indicator(self, *, symbol, indicator, period, exchange="NSE"):
        return _FakeAccessor.indicators.get((symbol, indicator, period))

    def get_volume(self, *, symbol, bars=1, exchange="NSE"):
        return _FakeAccessor.volumes.get((symbol, bars))


@pytest.fixture(autouse=True)
def reset_fake_accessor():
    _FakeAccessor.prices = {}
    _FakeAccessor.indicators = {}
    _FakeAccessor.volumes = {}
    yield
    _FakeAccessor.prices = {}
    _FakeAccessor.indicators = {}
    _FakeAccessor.volumes = {}


def _seed_workflow_with_compound_trigger(
    db, *, user_id: int, entry_tree: dict,
) -> tuple[models.Workflow, models.WorkflowStep]:
    """Create an active workflow with a trigger.compound step at index 0."""
    wf = models.Workflow(
        user_id=user_id,
        name="dsl test wf",
        status=models.WorkflowStatus.active,
        version=1,
    )
    db.add(wf)
    db.flush()
    step = models.WorkflowStep(
        workflow_id=wf.id,
        step_index=0,
        step_type="trigger.compound",
        config={"entry": entry_tree},
    )
    db.add(step)
    db.commit()
    return wf, step


# ── Tests ───────────────────────────────────────────────────────────


def test_compound_trigger_fires_when_tree_evaluates_true(
    db, monkeypatch, auth_headers,
):
    """A compound trigger with all conditions met creates a run."""
    # User from auth_headers
    from backend.routers._deps import require_user
    from backend.models import User
    # Pull any user out of the DB; auth_headers registered one for us.
    user = db.query(User).order_by(User.id.desc()).first()
    assert user is not None

    entry = {
        "type": "logic", "op": "and",
        "operands": [
            {"type": "comparison", "op": "<",
             "left": {"type": "indicator", "indicator": "rsi", "symbol": "TCS", "period": 14},
             "right": {"type": "constant", "value": 30}},
            {"type": "comparison", "op": ">",
             "left": {"type": "price", "symbol": "NIFTY"},
             "right": {"type": "constant", "value": 23000}},
        ],
    }
    wf, step = _seed_workflow_with_compound_trigger(
        db, user_id=user.id, entry_tree=entry,
    )

    # Both conditions met
    _FakeAccessor.indicators[("TCS", "rsi", 14)] = 27.0
    _FakeAccessor.prices["NIFTY"] = 23250.0

    # Patch LiveDataAccessor at module level so the watcher's
    # lazy import picks up the fake.
    monkeypatch.setattr(
        "backend.workflows.dsl.data_accessor.LiveDataAccessor",
        _FakeAccessor,
    )

    # Capture the _fire_watch_run call rather than letting it kick the engine.
    captured = {}

    async def _fake_fire(workflow_id, step_index, triggered_by, fired_at,
                          audit_context=None):
        captured["workflow_id"] = workflow_id
        captured["step_index"] = step_index
        captured["triggered_by"] = triggered_by
        return "fake-run-id"

    monkeypatch.setattr(scheduler, "_fire_watch_run", _fake_fire)

    # Drive one evaluation directly (bypass the scan-and-batch loop).
    from datetime import datetime, timezone
    asyncio.run(
        _evaluate_compound_trigger(
            workflow_id=str(wf.id),
            step_index=0,
            cfg={"entry": entry},
            fired_at=datetime.now(timezone.utc),
        )
    )

    assert captured.get("workflow_id") == str(wf.id)
    assert captured.get("triggered_by") == "indicator_alert"


def test_compound_trigger_does_not_fire_when_one_condition_misses(
    db, monkeypatch, auth_headers,
):
    from backend.models import User
    user = db.query(User).order_by(User.id.desc()).first()

    entry = {
        "type": "logic", "op": "and",
        "operands": [
            {"type": "comparison", "op": "<",
             "left": {"type": "indicator", "indicator": "rsi", "symbol": "TCS", "period": 14},
             "right": {"type": "constant", "value": 30}},
            {"type": "comparison", "op": ">",
             "left": {"type": "price", "symbol": "NIFTY"},
             "right": {"type": "constant", "value": 23000}},
        ],
    }
    wf, _ = _seed_workflow_with_compound_trigger(
        db, user_id=user.id, entry_tree=entry,
    )

    # NIFTY below threshold → AND fails
    _FakeAccessor.indicators[("TCS", "rsi", 14)] = 27.0
    _FakeAccessor.prices["NIFTY"] = 22500.0

    monkeypatch.setattr(
        "backend.workflows.dsl.data_accessor.LiveDataAccessor",
        _FakeAccessor,
    )

    fired = {"n": 0}

    async def _fake_fire(**_kwargs):
        fired["n"] += 1
        return "should-not-be-called"

    monkeypatch.setattr(scheduler, "_fire_watch_run", _fake_fire)

    from datetime import datetime, timezone
    asyncio.run(
        _evaluate_compound_trigger(
            workflow_id=str(wf.id),
            step_index=0,
            cfg={"entry": entry},
            fired_at=datetime.now(timezone.utc),
        )
    )

    assert fired["n"] == 0


def test_compound_trigger_with_missing_data_does_not_fire(
    db, monkeypatch, auth_headers,
):
    """If RSI is unavailable, the AND evaluates to UNKNOWN (Ternary) — must NOT fire."""
    from backend.models import User
    user = db.query(User).order_by(User.id.desc()).first()

    entry = {
        "type": "logic", "op": "and",
        "operands": [
            {"type": "comparison", "op": "<",
             "left": {"type": "indicator", "indicator": "rsi", "symbol": "TCS", "period": 14},
             "right": {"type": "constant", "value": 30}},
            {"type": "comparison", "op": ">",
             "left": {"type": "price", "symbol": "NIFTY"},
             "right": {"type": "constant", "value": 23000}},
        ],
    }
    wf, _ = _seed_workflow_with_compound_trigger(
        db, user_id=user.id, entry_tree=entry,
    )

    # RSI missing — NIFTY satisfies.
    _FakeAccessor.prices["NIFTY"] = 23250.0
    # No indicators registered.

    monkeypatch.setattr(
        "backend.workflows.dsl.data_accessor.LiveDataAccessor",
        _FakeAccessor,
    )

    fired = {"n": 0}

    async def _fake_fire(**_kwargs):
        fired["n"] += 1
        return "should-not-be-called"

    monkeypatch.setattr(scheduler, "_fire_watch_run", _fake_fire)

    from datetime import datetime, timezone
    asyncio.run(
        _evaluate_compound_trigger(
            workflow_id=str(wf.id),
            step_index=0,
            cfg={"entry": entry},
            fired_at=datetime.now(timezone.utc),
        )
    )

    assert fired["n"] == 0


def test_compound_trigger_registered_in_step_catalog():
    """The new step type should appear in the public /api/step-types
    catalog so the frontend picker can render it."""
    from backend.workflows.registry import STEP_REGISTRY
    assert "trigger.compound" in STEP_REGISTRY
    defn = STEP_REGISTRY["trigger.compound"]
    assert defn.category == "trigger"
    assert defn.trigger_only is True
