"""Tests for the executors implemented in #25 (Path A).

Each executor is exercised via a minimal `_StubCtx` so we don't drag
the whole engine in — the engine integration tests in
`tests/workflows/test_engine.py` already cover the orchestration path.
We're testing the executor's local logic + its delegation to existing
services / Kite-mock-mode.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from backend.workflows.engine import _ConditionFail
from backend.workflows.steps import actions as actions_mod
from backend.workflows.steps.actions import (
    execute_action_cancel_orders,
    execute_action_set_stoploss,
    execute_action_update_watchlist,
)
from backend.workflows.steps.conditions import (
    execute_condition_market_status,
    execute_condition_position,
    execute_condition_time_window,
)
from backend.workflows.steps.control import (
    execute_control_skip_if,
    execute_wait_delay,
)
from backend.workflows.steps.fetches import (
    NotYetAvailableError,
    execute_fetch_quote,
)


class _StubCtx:
    """Minimal ctx object — only carries the fields the executors touch."""
    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config
        self.client_request_id = "test-crid-deadbeefdeadbeef"
        self.workflow = type("W", (), {"user_id": 1})()
        self.run = type("R", (), {"id": "run-id-test"})()
        self.step = type("S", (), {"step_index": 1})()
        self.db = None  # only fetch.portfolio / condition.position need it


def _run(coro: Any) -> Any:
    """Tiny async helper so each test stays sync-readable."""
    return asyncio.get_event_loop().run_until_complete(coro)


# ── wait.delay ───────────────────────────────────────────────────────


@pytest.fixture
def _no_sleep(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """Replace asyncio.sleep with a no-op + capture its argument so
    tests can verify the duration without actually waiting."""
    seen: list[float] = []

    async def _fake_sleep(n: float) -> None:
        seen.append(n)

    monkeypatch.setattr("asyncio.sleep", _fake_sleep)
    return seen


@pytest.mark.asyncio
async def test_wait_delay_duration_zero(_no_sleep: list[float]) -> None:
    out = await execute_wait_delay(_StubCtx({"duration_seconds": 0}))
    assert out == {"slept_seconds": 0}
    # Zero-duration short-circuit doesn't call sleep.
    assert _no_sleep == []


@pytest.mark.asyncio
async def test_wait_delay_duration_positive(_no_sleep: list[float]) -> None:
    out = await execute_wait_delay(_StubCtx({"duration_seconds": 30}))
    assert out == {"slept_seconds": 30}
    assert _no_sleep == [30.0]


@pytest.mark.asyncio
async def test_wait_delay_until_time_future(_no_sleep: list[float]) -> None:
    """until_time in the future → sleeps a small positive amount."""
    target = datetime.now(timezone.utc) + timedelta(minutes=2)
    hhmm = target.strftime("%H:%M")
    out = await execute_wait_delay(
        _StubCtx({"until_time": hhmm, "timezone": "UTC"})
    )
    assert out is not None
    assert 0 < out["slept_seconds"] <= 180


@pytest.mark.asyncio
async def test_wait_delay_rejects_neither_specified(
    _no_sleep: list[float],
) -> None:
    with pytest.raises(ValueError, match="duration_seconds or until_time"):
        await execute_wait_delay(_StubCtx({}))


@pytest.mark.asyncio
async def test_wait_delay_caps_at_max(_no_sleep: list[float]) -> None:
    """Absurd duration capped at 1 hour."""
    out = await execute_wait_delay(_StubCtx({"duration_seconds": 9999999}))
    assert _no_sleep == [3600.0]
    assert out == {"slept_seconds": 3600}


# ── control.skip_if ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_skip_if_numeric_holds() -> None:
    out = await execute_control_skip_if(_StubCtx({
        "condition": {"type": "numeric", "left": 5, "operator": ">", "right": 3},
    }))
    assert out == {"skipped_next": True}


@pytest.mark.asyncio
async def test_skip_if_numeric_does_not_hold() -> None:
    out = await execute_control_skip_if(_StubCtx({
        "condition": {"type": "numeric", "left": 1, "operator": ">", "right": 3},
    }))
    assert out == {"skipped_next": False}


@pytest.mark.asyncio
async def test_skip_if_unknown_type_raises() -> None:
    with pytest.raises(ValueError, match="unsupported skip_if condition type"):
        await execute_control_skip_if(_StubCtx({
            "condition": {"type": "magic"},
        }))


@pytest.mark.asyncio
async def test_skip_if_time_window_in_window(monkeypatch: pytest.MonkeyPatch) -> None:
    """Force `now` inside [00:00, 23:59] in UTC — always inside, so
    skipped_next=True."""
    out = await execute_control_skip_if(_StubCtx({
        "condition": {
            "type": "time_window",
            "start_time": "00:00",
            "end_time": "23:59",
            "timezone": "UTC",
        },
    }))
    assert out == {"skipped_next": True}


# ── condition.market_status ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_condition_market_status_open(monkeypatch: pytest.MonkeyPatch) -> None:
    """When market is open and it's a trading day, require=open passes."""
    monkeypatch.setattr(
        "backend.utils.time_utils.is_market_open", lambda: True,
    )
    monkeypatch.setattr(
        "backend.utils.time_utils.is_trading_day",
        lambda *_a, **_k: True,
    )
    out = await execute_condition_market_status(_StubCtx({"require": "open"}))
    assert out == {"passed": True}


@pytest.mark.asyncio
async def test_condition_market_status_closed_when_open_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "backend.utils.time_utils.is_market_open", lambda: True,
    )
    monkeypatch.setattr(
        "backend.utils.time_utils.is_trading_day",
        lambda *_a, **_k: True,
    )
    with pytest.raises(_ConditionFail):
        await execute_condition_market_status(_StubCtx({"require": "closed"}))


# ── condition.time_window ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_condition_time_window_passes_when_inside() -> None:
    """[00:00, 23:59] always covers `now` in UTC → passes."""
    out = await execute_condition_time_window(_StubCtx({
        "start_time": "00:00",
        "end_time": "23:59",
        "timezone": "UTC",
    }))
    assert out == {"passed": True}


@pytest.mark.asyncio
async def test_condition_time_window_fails_when_outside() -> None:
    """A 1-minute window in the past → fails."""
    past = (datetime.now(timezone.utc) - timedelta(hours=1)).strftime("%H:%M")
    past_end = (datetime.now(timezone.utc) - timedelta(minutes=59)).strftime("%H:%M")
    if past >= past_end:
        # Around midnight wrap; skip rather than test cross-midnight v1 limit.
        pytest.skip("test would cross midnight in UTC; skip")
    with pytest.raises(_ConditionFail):
        await execute_condition_time_window(_StubCtx({
            "start_time": past,
            "end_time": past_end,
            "timezone": "UTC",
        }))


# ── condition.position ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_condition_position_held(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stub get_user_portfolio to report INFY held."""
    monkeypatch.setattr(
        "backend.services.portfolio.get_user_portfolio",
        lambda uid, db: {
            "holdings": [
                {"tradingsymbol": "INFY", "quantity": 10},
                {"tradingsymbol": "TCS", "quantity": 5},
            ],
            "buying_power": 50000, "total_value": 200000,
        },
    )
    out = await execute_condition_position(
        _StubCtx({"symbol": "INFY", "require": "held"})
    )
    assert out == {"passed": True}


@pytest.mark.asyncio
async def test_condition_position_not_held_passes_when_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "backend.services.portfolio.get_user_portfolio",
        lambda uid, db: {"holdings": [], "buying_power": 0, "total_value": 0},
    )
    out = await execute_condition_position(
        _StubCtx({"symbol": "RELIANCE", "require": "not_held"})
    )
    assert out == {"passed": True}


@pytest.mark.asyncio
async def test_condition_position_held_fails_when_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "backend.services.portfolio.get_user_portfolio",
        lambda uid, db: {"holdings": [], "buying_power": 0, "total_value": 0},
    )
    with pytest.raises(_ConditionFail):
        await execute_condition_position(
            _StubCtx({"symbol": "RELIANCE", "require": "held"})
        )


# ── action.cancel_orders ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_cancel_orders_no_filter_cancels_all_pending(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two pending orders; both cancelled."""
    monkeypatch.setattr(actions_mod, "_kite_token_for_run", lambda ctx: "tok")
    monkeypatch.setattr(actions_mod, "get_orders", lambda tok: [
        {"order_id": "o1", "tradingsymbol": "INFY",
         "transaction_type": "BUY", "status": "OPEN"},
        {"order_id": "o2", "tradingsymbol": "TCS",
         "transaction_type": "SELL", "status": "PENDING"},
        {"order_id": "o3", "tradingsymbol": "HDFC",
         "transaction_type": "BUY", "status": "COMPLETE"},  # filtered out
    ])
    cancelled: list[str] = []
    monkeypatch.setattr(
        actions_mod, "cancel_order",
        lambda tok, oid: cancelled.append(oid) or {"order_id": oid, "status": "CANCELLED"},
    )

    out = await execute_action_cancel_orders(_StubCtx({}))
    assert out == {"cancelled_count": 2, "order_ids": ["o1", "o2"]}
    assert sorted(cancelled) == ["o1", "o2"]


@pytest.mark.asyncio
async def test_cancel_orders_with_symbol_filter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(actions_mod, "_kite_token_for_run", lambda ctx: "tok")
    monkeypatch.setattr(actions_mod, "get_orders", lambda tok: [
        {"order_id": "o1", "tradingsymbol": "INFY",
         "transaction_type": "BUY", "status": "OPEN"},
        {"order_id": "o2", "tradingsymbol": "TCS",
         "transaction_type": "BUY", "status": "OPEN"},
    ])
    monkeypatch.setattr(actions_mod, "cancel_order", lambda tok, oid: None)

    out = await execute_action_cancel_orders(_StubCtx({"symbol_filter": "INFY"}))
    assert out["cancelled_count"] == 1
    assert out["order_ids"] == ["o1"]


# ── action.set_stoploss ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_set_stoploss_uses_explicit_quantity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(actions_mod, "_kite_token_for_run", lambda ctx: "tok")
    seen: dict[str, Any] = {}

    def _fake_gtt(**kwargs: Any) -> dict[str, Any]:
        seen.update(kwargs)
        return {"trigger_id": 999, "status": "active"}

    monkeypatch.setattr(actions_mod, "place_gtt_order", _fake_gtt)
    out = await execute_action_set_stoploss(_StubCtx({
        "symbol": "INFY", "trigger_price": 1400.0, "quantity": 10,
    }))
    assert out["trigger_id"] == "999"
    assert seen["quantity"] == 10
    assert seen["transaction_type"] == "SELL"
    assert seen["trigger_price"] == 1400.0


@pytest.mark.asyncio
async def test_set_stoploss_defaults_to_holding_qty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No quantity specified → reads from portfolio."""
    monkeypatch.setattr(actions_mod, "_kite_token_for_run", lambda ctx: "tok")
    monkeypatch.setattr(
        "backend.services.portfolio.get_user_portfolio",
        lambda uid, db: {"holdings": [
            {"tradingsymbol": "INFY", "quantity": 25},
        ], "buying_power": 0, "total_value": 0},
    )
    seen: dict[str, Any] = {}
    monkeypatch.setattr(
        actions_mod, "place_gtt_order",
        lambda **kwargs: seen.update(kwargs) or {"trigger_id": 1, "status": "active"},
    )
    await execute_action_set_stoploss(_StubCtx({
        "symbol": "INFY", "trigger_price": 1400.0,
    }))
    assert seen["quantity"] == 25


@pytest.mark.asyncio
async def test_set_stoploss_no_qty_no_holding_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(actions_mod, "_kite_token_for_run", lambda ctx: "tok")
    monkeypatch.setattr(
        "backend.services.portfolio.get_user_portfolio",
        lambda uid, db: {"holdings": [], "buying_power": 0, "total_value": 0},
    )
    with pytest.raises(ValueError, match="no quantity specified and no holding"):
        await execute_action_set_stoploss(_StubCtx({
            "symbol": "RELIANCE", "trigger_price": 2500.0,
        }))


# ── action.update_watchlist ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_update_watchlist_raises_not_yet_available() -> None:
    """Per spec: never fake data when the source isn't ready."""
    with pytest.raises(NotYetAvailableError, match="Watchlist data model"):
        await execute_action_update_watchlist(_StubCtx({
            "action": "add", "symbol": "INFY",
        }))


# ── fetch.quote ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_fetch_quote_kite_path_returns_ltp(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Kite returns a populated quote → use directly."""
    monkeypatch.setattr(actions_mod, "_kite_token_for_run", lambda ctx: "tok")
    monkeypatch.setattr(
        "backend.kite.market_data.get_live_quote",
        lambda tok, ins: {ins[0]: {
            "last_price": 1523.0,
            "ohlc": {"open": 1500.0, "high": 1530.0, "low": 1495.0, "close": 1518.0},
            "volume": 1234567,
        }},
    )

    out = await execute_fetch_quote(_StubCtx({"symbol": "INFY"}))
    assert out["ltp"] == 1523.0
    assert out["open"] == 1500.0
    assert out["volume"] == 1234567
    assert "asof" in out


@pytest.mark.asyncio
async def test_fetch_quote_yfinance_backfill(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Kite returns only `last_price` (mock mode); yfinance fills OHLC."""
    monkeypatch.setattr(actions_mod, "_kite_token_for_run", lambda ctx: "tok")
    monkeypatch.setattr(
        "backend.kite.market_data.get_live_quote",
        lambda tok, ins: {ins[0]: {"last_price": 100.0}},
    )
    monkeypatch.setattr(
        "backend.kite.market_data.get_historical_ohlcv",
        lambda sym, period, interval: [
            {"open": 95, "high": 102, "low": 94, "close": 101, "volume": 50000},
        ],
    )
    out = await execute_fetch_quote(_StubCtx({"symbol": "RELIANCE"}))
    assert out["ltp"] == 100.0
    assert out["open"] == 95.0
    assert out["volume"] == 50000.0


@pytest.mark.asyncio
async def test_fetch_quote_no_data_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Both Kite and yfinance return nothing → NotYetAvailableError."""
    monkeypatch.setattr(actions_mod, "_kite_token_for_run", lambda ctx: "tok")
    monkeypatch.setattr(
        "backend.kite.market_data.get_live_quote",
        lambda tok, ins: {ins[0]: {"last_price": 0}},
    )
    monkeypatch.setattr(
        "backend.kite.market_data.get_historical_ohlcv",
        lambda sym, period, interval: [],
    )
    with pytest.raises(NotYetAvailableError, match="no quote available"):
        await execute_fetch_quote(_StubCtx({"symbol": "GHOST"}))
