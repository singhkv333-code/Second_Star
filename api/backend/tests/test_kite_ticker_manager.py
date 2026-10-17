"""Unit tests for the KiteTickerManager singleton.

We can't reach Zerodha's WS in CI, so these tests focus on:
    - Symbol normalisation matches the Redis key convention.
    - Mock-mode `start()` is a no-op (no kiteconnect import attempted).
    - `add_symbols` / `remove_symbols` correctly maintain subscriber
      refcounts and never release a seed symbol.
    - Tick payload translation produces the contract shape.
"""
from __future__ import annotations

import json

import pytest

from backend.kite import ticker as ticker_mod
from backend.kite.ticker import (
    KiteTickerManager,
    cache_key,
    get_ticker_manager,
    normalize_symbol,
    reset_ticker_manager_for_tests,
)


@pytest.fixture(autouse=True)
def _reset_singleton():
    reset_ticker_manager_for_tests()
    yield
    reset_ticker_manager_for_tests()


def test_normalize_symbol_matches_redis_key():
    assert normalize_symbol("nifty 50") == "NIFTY_50"
    assert normalize_symbol(" reliance ") == "RELIANCE"
    assert normalize_symbol("nifty bank") == "NIFTY_BANK"
    assert cache_key("NIFTY 50") == "price:NIFTY_50"
    assert cache_key("reliance") == "price:RELIANCE"


def test_get_ticker_manager_returns_singleton():
    a = get_ticker_manager()
    b = get_ticker_manager()
    assert a is b
    assert isinstance(a, KiteTickerManager)


def test_start_is_noop_in_mock_mode(monkeypatch):
    monkeypatch.setattr(ticker_mod, "KITE_MOCK_MODE", True)
    mgr = get_ticker_manager()
    status = mgr.start(access_token="anything", user_id=1, seed_symbols=["RELIANCE"])
    assert status["running"] is False
    assert status["symbol_count"] == 0
    assert status["user_id"] is None


def test_start_is_noop_without_token(monkeypatch):
    monkeypatch.setattr(ticker_mod, "KITE_MOCK_MODE", False)
    mgr = get_ticker_manager()
    status = mgr.start(access_token="", user_id=1, seed_symbols=["RELIANCE"])
    assert status["running"] is False


def test_add_and_remove_symbols_refcount():
    """Test the refcount + seed protection logic directly on the dataclass.

    We bypass `start()` since that wires up kiteconnect; instead we
    seed `_state` directly the way `start()` would.
    """
    mgr = get_ticker_manager()
    mgr._state.running = False  # not running — add_symbols won't call subscribe
    mgr._state.seed_symbols = {"RELIANCE", "NIFTY_50"}
    mgr._state.universe = {"RELIANCE", "NIFTY_50"}
    mgr._state.subscriber_count = {"RELIANCE": 1, "NIFTY_50": 1}
    # Add the token dict so add_symbols can resolve INFY → an int.
    mgr._state.instrument_tokens = {
        "RELIANCE": 738561,
        "NIFTY_50": 256265,
        "INFY": 408065,
    }

    added = mgr.add_symbols(["INFY"])
    assert added == ["INFY"]
    assert "INFY" in mgr._state.universe
    assert mgr._state.subscriber_count["INFY"] == 1

    # Second subscriber for the same symbol: no re-add to universe,
    # but refcount goes up.
    added_again = mgr.add_symbols(["INFY"])
    assert added_again == []
    assert mgr._state.subscriber_count["INFY"] == 2

    # remove_symbols drops to 1 — still subscribed.
    mgr.remove_symbols(["INFY"])
    assert "INFY" in mgr._state.universe
    assert mgr._state.subscriber_count["INFY"] == 1

    # Final remove: zero refcount → released because INFY isn't a seed.
    mgr.remove_symbols(["INFY"])
    assert "INFY" not in mgr._state.universe

    # Removing a seed symbol must NOT release it from the universe.
    mgr.remove_symbols(["RELIANCE"])
    assert "RELIANCE" in mgr._state.universe


def test_tick_to_payload_shape():
    mgr = get_ticker_manager()
    mgr._state.instrument_tokens = {"RELIANCE": 738561}
    tick = {
        "instrument_token": 738561,
        "last_price": 2845.55,
        "ohlc": {
            "open": 2832.0,
            "high": 2850.10,
            "low": 2828.40,
            "close": 2833.65,
        },
        "volume_traded": 4128390,
    }
    payload = mgr._tick_to_payload(tick, ts_now=1747140330)
    assert payload is not None
    # Contract shape (PHASE2_CONTRACT.md §Layer 1)
    assert payload["symbol"] == "RELIANCE"
    assert payload["ltp"] == 2845.55
    assert payload["open"] == 2832.0
    assert payload["high"] == 2850.10
    assert payload["low"] == 2828.40
    assert payload["prev_close"] == 2833.65
    assert payload["volume"] == 4128390
    assert payload["ts"] == 1747140330
    assert payload["src"] == "kite_ws"
    # change_pct computed from prev_close → ltp
    assert payload["change_pct"] == pytest.approx(0.4196, abs=0.001)
    # Round-trippable
    json.dumps(payload)


def test_tick_to_payload_unknown_token_drops():
    mgr = get_ticker_manager()
    mgr._state.instrument_tokens = {"RELIANCE": 738561}
    assert mgr._tick_to_payload(
        {"instrument_token": 999999, "last_price": 100.0},
        ts_now=1,
    ) is None


def test_universe_cap_blocks_overgrowth(monkeypatch):
    monkeypatch.setattr(ticker_mod, "_MAX_UNIVERSE", 3)
    mgr = get_ticker_manager()
    mgr._state.instrument_tokens = {f"SYM{i}": 1000 + i for i in range(10)}
    mgr._state.seed_symbols = set()
    # Seed two symbols.
    mgr._state.universe = {"SYM0", "SYM1"}
    mgr._state.subscriber_count = {"SYM0": 1, "SYM1": 1}
    # Adding two more — only one should fit within the cap of 3.
    added = mgr.add_symbols(["SYM2", "SYM3"])
    assert added == ["SYM2"]
    assert len(mgr._state.universe) == 3
