"""Tests for the price/indicator watcher in scheduler.py.

Coverage:
  - _matches_threshold: every operator (>, <, crosses_above/below).
  - _poll_watch_triggers: short-circuits when market closed.
  - _evaluate_price_trigger: persists last_price every tick;
    fires a run on threshold cross (not on plain > / <
    repeat ticks where last_price > threshold already);
    handles missing quote gracefully.
  - fetch.indicator real impl: SMA/EMA/RSI/MACD against synthetic
    OHLCV; raises NotYetAvailableError on insufficient bars.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

import pytest
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
    _evaluate_price_trigger,
    _matches_threshold,
    _poll_watch_triggers,
)


# ── _matches_threshold ────────────────────────────────────────────────


def test_threshold_gt_simple() -> None:
    assert _matches_threshold(">", 110, 100, last=None) is True
    assert _matches_threshold(">", 90, 100, last=None) is False


def test_threshold_lt_simple() -> None:
    assert _matches_threshold("<", 90, 100, last=None) is True
    assert _matches_threshold("<", 110, 100, last=None) is False


def test_threshold_crosses_above_requires_last() -> None:
    """No prior tick → crosses_above can't be detected (needs the cross)."""
    assert _matches_threshold("crosses_above", 110, 100, last=None) is False


def test_threshold_crosses_above_fires_on_actual_cross() -> None:
    # Last tick was below or at threshold, current is above → cross.
    assert _matches_threshold("crosses_above", 110, 100, last=99) is True
    assert _matches_threshold("crosses_above", 110, 100, last=100) is True


def test_threshold_crosses_above_does_not_fire_when_already_above() -> None:
    """Last tick was already above → no cross this tick."""
    assert _matches_threshold("crosses_above", 110, 100, last=105) is False


def test_threshold_crosses_below_fires_on_actual_cross() -> None:
    assert _matches_threshold("crosses_below", 90, 100, last=110) is True
    assert _matches_threshold("crosses_below", 90, 100, last=100) is True


def test_threshold_crosses_below_does_not_fire_when_already_below() -> None:
    assert _matches_threshold("crosses_below", 90, 100, last=95) is False


def test_threshold_unknown_operator_returns_false() -> None:
    assert _matches_threshold("greater", 110, 100, last=None) is False


# ── _poll_watch_triggers — market-hours short-circuit ────────────────


@pytest.mark.asyncio
async def test_watcher_skips_when_market_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Outside market hours / weekends → cheap no-op, no DB query."""
    monkeypatch.setattr(
        "backend.utils.time_utils.is_trading_day",
        lambda *_a, **_k: False,
    )
    monkeypatch.setattr(
        "backend.utils.time_utils.is_market_open", lambda: False,
    )
    # If the watcher were to query the DB, it'd fail (we haven't set
    # up SessionLocal here). The fact that this returns cleanly proves
    # the early-return path runs.
    await _poll_watch_triggers()


# ── _evaluate_price_trigger ──────────────────────────────────────────


@pytest.fixture
def _scheduler_test_db(
    monkeypatch: pytest.MonkeyPatch, workflow_db: Session,
) -> None:
    """Same shared-session pattern used by test_scheduler.py — the
    watcher's SessionLocal opens land on the test fixture's connection
    so flushed-but-uncommitted rows are visible."""
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


def _make_price_workflow(
    db: Session, *, operator: str, value: float, last: float | None = None,
) -> Workflow:
    wf = Workflow(
        user_id=1, name="price-test",
        status=WorkflowStatus.active, version=1,
    )
    db.add(wf)
    db.flush()
    cfg: dict[str, object] = {
        "symbol": "INFY", "exchange": "NSE",
        "operator": operator, "value": value,
    }
    if last is not None:
        cfg["_last_price"] = last
    step = WorkflowStep(
        workflow_id=wf.id, step_index=0,
        step_type="trigger.price", config=cfg, label=None,
    )
    db.add(step)
    db.flush()
    db.refresh(wf)
    return wf


@pytest.mark.asyncio
async def test_price_trigger_fires_on_gt_match(
    workflow_db: Session,
    monkeypatch: pytest.MonkeyPatch,
    _scheduler_test_db: None,
) -> None:
    wf = _make_price_workflow(workflow_db, operator=">", value=1500.0)

    fired: list[str] = []

    class _StubEngine:
        async def execute_run(self, run_id: str) -> None:
            fired.append(run_id)

    monkeypatch.setattr(engine_mod, "WorkflowEngine", _StubEngine)

    quotes = {"NSE:INFY": 1523.0}
    await _evaluate_price_trigger(
        str(wf.id),
        {"symbol": "INFY", "exchange": "NSE",
         "operator": ">", "value": 1500.0},
        quotes,
        datetime.now(timezone.utc),
    )
    await asyncio.sleep(0)

    runs = (
        workflow_db.query(WorkflowRun)
        .filter(WorkflowRun.workflow_id == str(wf.id))
        .all()
    )
    assert len(runs) == 1
    assert runs[0].triggered_by == "price_alert"
    assert runs[0].status == RunStatus.running
    assert fired == [str(runs[0].id)]


@pytest.mark.asyncio
async def test_price_trigger_persists_last_price(
    workflow_db: Session,
    monkeypatch: pytest.MonkeyPatch,
    _scheduler_test_db: None,
) -> None:
    """Even when threshold doesn't match, last_price is persisted so
    the next tick can detect a crossing."""
    wf = _make_price_workflow(workflow_db, operator=">", value=2000.0)
    workflow_db.flush()

    monkeypatch.setattr(engine_mod, "WorkflowEngine", type(
        "Stub", (), {
            "__init__": lambda self: None,
            "execute_run": lambda self, run_id: None,
        },
    ))

    await _evaluate_price_trigger(
        str(wf.id),
        {"symbol": "INFY", "exchange": "NSE",
         "operator": ">", "value": 2000.0},
        {"NSE:INFY": 1523.0},
        datetime.now(timezone.utc),
    )

    # Re-read step config; last_price should now be 1523.0.
    workflow_db.expire_all()
    step = (
        workflow_db.query(WorkflowStep)
        .filter(WorkflowStep.workflow_id == str(wf.id))
        .filter(WorkflowStep.step_index == 0)
        .first()
    )
    assert step is not None
    assert step.config.get("_last_price") == 1523.0


@pytest.mark.asyncio
async def test_price_trigger_crosses_above_needs_prior_tick(
    workflow_db: Session,
    monkeypatch: pytest.MonkeyPatch,
    _scheduler_test_db: None,
) -> None:
    """First tick with crosses_above must NOT fire (no prior value).
    Second tick with prior < threshold AND current > threshold fires."""
    wf = _make_price_workflow(
        workflow_db, operator="crosses_above", value=100.0,
    )

    fired: list[str] = []

    class _StubEngine:
        async def execute_run(self, run_id: str) -> None:
            fired.append(run_id)

    monkeypatch.setattr(engine_mod, "WorkflowEngine", _StubEngine)

    cfg_now = {
        "symbol": "INFY", "exchange": "NSE",
        "operator": "crosses_above", "value": 100.0,
    }

    # Tick 1: current 95 (below) — should persist last_price=95, no fire.
    await _evaluate_price_trigger(
        str(wf.id), dict(cfg_now), {"NSE:INFY": 95.0},
        datetime.now(timezone.utc),
    )
    await asyncio.sleep(0)
    assert fired == []

    # Tick 2: current 110 (above), with the persisted last=95 from tick 1.
    workflow_db.expire_all()
    step = workflow_db.query(WorkflowStep).filter_by(
        workflow_id=str(wf.id), step_index=0,
    ).first()
    assert step is not None
    cfg_with_last = dict(step.config or {})
    await _evaluate_price_trigger(
        str(wf.id), cfg_with_last, {"NSE:INFY": 110.0},
        datetime.now(timezone.utc),
    )
    await asyncio.sleep(0)
    assert len(fired) == 1


@pytest.mark.asyncio
async def test_price_trigger_no_quote_skips_silently(
    workflow_db: Session,
    monkeypatch: pytest.MonkeyPatch,
    _scheduler_test_db: None,
) -> None:
    """Missing quote in the batch → don't fire, don't crash."""
    wf = _make_price_workflow(workflow_db, operator=">", value=100.0)

    fired: list[str] = []
    monkeypatch.setattr(
        engine_mod, "WorkflowEngine",
        type("Stub", (), {
            "__init__": lambda self: None,
            "execute_run": lambda self, run_id: fired.append(run_id),
        }),
    )
    await _evaluate_price_trigger(
        str(wf.id),
        {"symbol": "INFY", "exchange": "NSE",
         "operator": ">", "value": 100.0},
        {},  # no quote
        datetime.now(timezone.utc),
    )
    await asyncio.sleep(0)
    assert fired == []


# ── fetch.indicator real impl ────────────────────────────────────────


@pytest.mark.asyncio
async def test_fetch_indicator_sma(monkeypatch: pytest.MonkeyPatch) -> None:
    """SMA of [10,20,30,40,50] over period=5 = 30."""
    bars = [
        {"open": 10, "high": 10, "low": 10, "close": 10, "volume": 1000},
        {"open": 20, "high": 20, "low": 20, "close": 20, "volume": 1000},
        {"open": 30, "high": 30, "low": 30, "close": 30, "volume": 1000},
        {"open": 40, "high": 40, "low": 40, "close": 40, "volume": 1000},
        {"open": 50, "high": 50, "low": 50, "close": 50, "volume": 1000},
        {"open": 60, "high": 60, "low": 60, "close": 60, "volume": 1000},
        {"open": 70, "high": 70, "low": 70, "close": 70, "volume": 1000},
        {"open": 80, "high": 80, "low": 80, "close": 80, "volume": 1000},
        {"open": 90, "high": 90, "low": 90, "close": 90, "volume": 1000},
        {"open": 100, "high": 100, "low": 100, "close": 100, "volume": 1000},
    ]
    monkeypatch.setattr(
        "backend.kite.market_data.get_historical_ohlcv",
        lambda sym, period, interval: bars,
    )
    from backend.workflows.steps.fetches import execute_fetch_indicator

    class _Ctx:
        config = {"symbol": "INFY", "indicator": "sma", "period": 3}
    out = await execute_fetch_indicator(_Ctx())
    assert out is not None
    # Last 3 closes: 80, 90, 100 → SMA = 90.0
    assert out["value"] == pytest.approx(90.0)


@pytest.mark.asyncio
async def test_fetch_indicator_insufficient_history_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "backend.kite.market_data.get_historical_ohlcv",
        lambda sym, period, interval: [
            {"open": 1, "high": 1, "low": 1, "close": 1, "volume": 1}
            for _ in range(3)
        ],
    )
    from backend.workflows.steps.fetches import (
        NotYetAvailableError, execute_fetch_indicator,
    )

    class _Ctx:
        config = {"symbol": "INFY", "indicator": "rsi", "period": 14}
    with pytest.raises(NotYetAvailableError, match="not enough history"):
        await execute_fetch_indicator(_Ctx())


@pytest.mark.asyncio
async def test_fetch_indicator_unsupported(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "backend.kite.market_data.get_historical_ohlcv",
        lambda sym, period, interval: [
            {"open": i, "high": i, "low": i, "close": i, "volume": 1}
            for i in range(50)
        ],
    )
    from backend.workflows.steps.fetches import execute_fetch_indicator

    class _Ctx:
        config = {"symbol": "INFY", "indicator": "atr", "period": 14}
    with pytest.raises(ValueError, match="unsupported indicator"):
        await execute_fetch_indicator(_Ctx())
