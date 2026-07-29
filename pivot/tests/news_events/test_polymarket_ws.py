"""Tests for the WS client + evaluator.

No network: the client's dispatch is tested by feeding it raw frame
dicts via ``_handle_frame``. The evaluator's edge logic is tested
directly. The fire path runs against an in-memory SQLite session
that mirrors the production conftest setup.
"""
from __future__ import annotations

import asyncio
import json

import pytest

from backend.news_events.pipeline import prediction_market_ws as eval_mod
from backend.news_events.pipeline.prediction_market_ws import (
    PolymarketWSEvaluator,
    _Registration,
)
from backend.news_events.sources.polymarket_ws import (
    PolymarketWSClient,
    _BookState,
    _apply_book,
    _apply_price_change,
)


# ── _BookState orderbook math ────────────────────────────────────────


def test_book_snapshot_replaces_levels():
    b = _BookState()
    _apply_book(b, {
        "bids": [{"price": "0.40", "size": "10"}, {"price": "0.39", "size": "5"}],
        "asks": [{"price": "0.42", "size": "8"}, {"price": "0.45", "size": "3"}],
    })
    assert b.best_bid() == pytest.approx(0.40)
    assert b.best_ask() == pytest.approx(0.42)
    assert b.mid() == pytest.approx(0.41)


def test_price_change_adds_and_removes_levels():
    b = _BookState()
    _apply_book(b, {
        "bids": [{"price": "0.40", "size": "10"}],
        "asks": [{"price": "0.42", "size": "8"}],
    })
    # New best bid
    _apply_price_change(b, {"price": "0.41", "size": "20", "side": "BUY"})
    assert b.mid() == pytest.approx(0.415)
    # Remove ask level (size 0)
    _apply_price_change(b, {"price": "0.42", "size": "0", "side": "SELL"})
    # Top of asks is now empty → mid is None
    assert b.best_ask() is None
    assert b.mid() is None
    # Add a new ask
    _apply_price_change(b, {"price": "0.43", "size": "5", "side": "SELL"})
    assert b.mid() == pytest.approx(0.42)


def test_price_change_with_unknown_side_is_ignored():
    b = _BookState()
    _apply_book(b, {
        "bids": [{"price": "0.40", "size": "10"}],
        "asks": [{"price": "0.42", "size": "8"}],
    })
    _apply_price_change(b, {"price": "0.99", "size": "1", "side": "WEIRD"})
    assert b.mid() == pytest.approx(0.41)


def test_price_change_with_bad_numbers_is_ignored():
    b = _BookState()
    _apply_book(b, {
        "bids": [{"price": "0.40", "size": "10"}],
        "asks": [{"price": "0.42", "size": "8"}],
    })
    _apply_price_change(b, {"price": "nope", "size": "1", "side": "BUY"})
    _apply_price_change(b, {"price": "0.41", "size": "abc", "side": "BUY"})
    assert b.mid() == pytest.approx(0.41)


# ── client dispatch ──────────────────────────────────────────────────


def test_client_dispatches_book_then_price_change():
    """Feed two raw frames through _handle_frame; verify on_tick fires
    with the correct midpoint at each step."""
    ticks: list[tuple[str, float]] = []

    async def on_tick(asset_id, mid, ts):
        ticks.append((asset_id, mid))

    client = PolymarketWSClient(on_tick=on_tick)
    asyncio.run(client.set_subscriptions({"tok_a"}))

    book_frame = json.dumps({
        "event_type": "book",
        "asset_id": "tok_a",
        "market": "0xabc",
        "bids": [{"price": "0.40", "size": "10"}],
        "asks": [{"price": "0.42", "size": "8"}],
    })
    asyncio.run(client._handle_frame(book_frame))
    assert ticks[-1] == ("tok_a", pytest.approx(0.41))

    pc_frame = json.dumps({
        "event_type": "price_change",
        "market": "0xabc",
        "price_changes": [
            {"asset_id": "tok_a", "price": "0.41", "size": "5", "side": "BUY"},
        ],
    })
    asyncio.run(client._handle_frame(pc_frame))
    assert ticks[-1] == ("tok_a", pytest.approx(0.415))


def test_client_ignores_unsubscribed_asset():
    """price_change frames carry events for BOTH YES and NO tokens
    of a market — we must filter on the subscribed asset only."""
    ticks: list[tuple[str, float]] = []

    async def on_tick(asset_id, mid, ts):
        ticks.append((asset_id, mid))

    client = PolymarketWSClient(on_tick=on_tick)
    asyncio.run(client.set_subscriptions({"tok_yes"}))

    pc_frame = json.dumps({
        "event_type": "price_change",
        "market": "0xabc",
        "price_changes": [
            {"asset_id": "tok_no", "price": "0.9", "size": "10", "side": "SELL"},
            {"asset_id": "tok_no", "price": "0.88", "size": "5", "side": "BUY"},
        ],
    })
    asyncio.run(client._handle_frame(pc_frame))
    assert ticks == []


def test_client_dispatches_market_resolved_via_market_tracking():
    """market_resolved frames are keyed by market, not asset — the
    client tracks market→assets via book/price_change events and
    routes resolved callbacks back to subscribers under that market."""
    resolved_calls: list[tuple[str, dict]] = []

    async def on_tick(*args, **kwargs):
        return None

    async def on_resolved(asset_id, payload):
        resolved_calls.append((asset_id, payload))

    client = PolymarketWSClient(on_tick=on_tick, on_resolved=on_resolved)
    asyncio.run(client.set_subscriptions({"tok_a"}))

    asyncio.run(client._handle_frame(json.dumps({
        "event_type": "book",
        "asset_id": "tok_a",
        "market": "0xabc",
        "bids": [{"price": "0.40", "size": "10"}],
        "asks": [{"price": "0.42", "size": "8"}],
    })))

    asyncio.run(client._handle_frame(json.dumps({
        "event_type": "market_resolved",
        "market": "0xabc",
        "winner": "YES",
    })))
    assert len(resolved_calls) == 1
    assert resolved_calls[0][0] == "tok_a"
    assert resolved_calls[0][1]["winner"] == "YES"


def test_client_drops_new_market_firehose():
    """new_market events are global firehose noise — must NOT trigger
    any callback."""
    ticks = []
    resolved = []

    async def on_tick(*a, **kw):
        ticks.append(a)

    async def on_resolved(*a, **kw):
        resolved.append(a)

    client = PolymarketWSClient(on_tick=on_tick, on_resolved=on_resolved)
    asyncio.run(client.set_subscriptions({"tok_a"}))
    asyncio.run(client._handle_frame(json.dumps({
        "event_type": "new_market",
        "id": "9999",
        "question": "irrelevant",
    })))
    assert ticks == [] and resolved == []


# ── evaluator edge-trigger ───────────────────────────────────────────


async def _noop_handler(payload):  # pragma: no cover — never invoked in unit tests
    return None


def _reg(**kwargs) -> _Registration:
    """Helper: build a _Registration with a no-op fire_handler for
    the edge-trigger logic tests (those tests never invoke fire)."""
    return _Registration(fire_handler=_noop_handler, **kwargs)


def test_evaluator_fires_only_on_cross_above():
    sf = PolymarketWSEvaluator._should_fire
    reg = _reg(key="k", asset_id="a", threshold=0.7, direction="above")
    assert sf(reg, 0.65) is False  # no baseline yet
    reg.last_mid = 0.65
    assert sf(reg, 0.69) is False
    assert sf(reg, 0.70) is True
    reg.last_mid = 0.72
    assert sf(reg, 0.75) is False  # sustained above — already fired


def test_evaluator_fires_only_on_cross_below():
    sf = PolymarketWSEvaluator._should_fire
    reg = _reg(key="k", asset_id="a", threshold=0.30, direction="below")
    reg.last_mid = 0.35
    assert sf(reg, 0.31) is False
    assert sf(reg, 0.30) is True


def test_evaluator_no_fire_without_baseline():
    """The stale-already-above check: first tick after register can
    NOT fire even if it's already across, because we don't know
    whether the market crossed before or after we started watching."""
    sf = PolymarketWSEvaluator._should_fire
    reg = _reg(key="k", asset_id="a", threshold=0.5, direction="above")
    # No last_mid → cannot fire on first tick.
    assert sf(reg, 0.99) is False


def test_evaluator_register_unregister_bookkeeping():
    e = PolymarketWSEvaluator(session_factory=lambda: None)
    e.register(key="spec:s1", asset_id="ax", fire_handler=_noop_handler,
               threshold=0.7, direction="above")
    e.register(key="spec:s2", asset_id="ax", fire_handler=_noop_handler,
               threshold=0.8, direction="above")
    e.register(key="spec:s3", asset_id="ay", fire_handler=_noop_handler,
               threshold=0.5, direction="below")
    assert e.subscribed_asset_ids() == {"ax", "ay"}
    e.unregister("spec:s1")
    assert e.subscribed_asset_ids() == {"ax", "ay"}  # ax still has spec:s2
    e.unregister("spec:s2")
    assert e.subscribed_asset_ids() == {"ay"}  # ax drained
    e.unregister("spec:unknown")  # idempotent


def test_evaluator_register_rejects_out_of_range_threshold():
    e = PolymarketWSEvaluator(session_factory=lambda: None)
    e.register(key="spec:s1", asset_id="a", fire_handler=_noop_handler,
               threshold=1.5)
    assert e.subscribed_asset_ids() == set()
    e.register(key="spec:s2", asset_id="a", fire_handler=_noop_handler,
               threshold=-0.1)
    assert e.subscribed_asset_ids() == set()


def test_evaluator_re_register_updates_in_place_preserves_baseline():
    e = PolymarketWSEvaluator(session_factory=lambda: None)
    e.register(key="spec:s", asset_id="a", fire_handler=_noop_handler,
               threshold=0.5, direction="above")
    # Tick to set baseline.
    asyncio.run(e.on_tick("a", 0.4, 0.0))
    # Re-register with new threshold — baseline must NOT reset.
    e.register(key="spec:s", asset_id="a", fire_handler=_noop_handler,
               threshold=0.8, direction="above")
    reg = e._by_asset["a"]["spec:s"]
    assert reg.threshold == 0.8
    assert reg.last_mid == 0.4  # baseline preserved


def test_evaluator_threshold_mode_ignores_resolution_path():
    """A threshold-mode registration never fires on a market_resolved
    event — only resolution-mode regs are dispatched there."""
    fired = []

    async def cap(payload):
        fired.append(payload)

    e = PolymarketWSEvaluator(session_factory=lambda: None)
    e.register(key="spec:thr", asset_id="a", fire_handler=cap,
               mode="threshold", threshold=0.5, direction="above")
    asyncio.run(e.on_resolved("a", {"market": "0xabc", "winner": "YES"}))
    assert fired == []


def test_evaluator_resolution_mode_fires_when_winner_matches():
    fired = []

    async def cap(payload):
        fired.append(payload)

    e = PolymarketWSEvaluator(session_factory=lambda: None)
    e.register(key="wf:w1:2", asset_id="a", fire_handler=cap,
               mode="resolution", resolve_on="YES")
    asyncio.run(e.on_resolved("a", {"market": "0xabc", "winner": "YES"}))
    assert len(fired) == 1
    assert fired[0]["mode"] == "resolution"
    assert fired[0]["winner"] == "YES"
    # Registration was dropped after firing — second resolved event no-ops.
    asyncio.run(e.on_resolved("a", {"market": "0xabc", "winner": "YES"}))
    assert len(fired) == 1


def test_evaluator_resolution_mode_skips_wrong_winner():
    fired = []

    async def cap(payload):
        fired.append(payload)

    e = PolymarketWSEvaluator(session_factory=lambda: None)
    e.register(key="wf:w1:2", asset_id="a", fire_handler=cap,
               mode="resolution", resolve_on="NO")
    # Winner = YES, resolve_on = NO → no fire, registration stays.
    asyncio.run(e.on_resolved("a", {"market": "0xabc", "winner": "YES"}))
    assert fired == []
    assert "wf:w1:2" in e.registered_keys()


def test_evaluator_resolution_mode_any_fires_on_either():
    fired = []

    async def cap(payload):
        fired.append(payload)

    e = PolymarketWSEvaluator(session_factory=lambda: None)
    e.register(key="wf:w1:2", asset_id="a", fire_handler=cap,
               mode="resolution", resolve_on="ANY")
    asyncio.run(e.on_resolved("a", {"market": "0xabc", "winner": "NO"}))
    assert len(fired) == 1
    assert fired[0]["winner"] == "NO"


def test_evaluator_routes_to_correct_handler_under_same_asset():
    """Two registrations on the same asset — a threshold one and a
    resolution one — fire INDEPENDENTLY on the appropriate event."""
    thr_fires = []
    res_fires = []

    async def thr_h(payload):
        thr_fires.append(payload)

    async def res_h(payload):
        res_fires.append(payload)

    e = PolymarketWSEvaluator(session_factory=lambda: None)
    e.register(key="spec:thr", asset_id="a", fire_handler=thr_h,
               mode="threshold", threshold=0.5, direction="above")
    e.register(key="wf:w:2", asset_id="a", fire_handler=res_h,
               mode="resolution", resolve_on="YES")

    # baseline tick then a cross → only thr_h fires.
    asyncio.run(e.on_tick("a", 0.40, 0.0))
    asyncio.run(e.on_tick("a", 0.55, 1.0))
    assert len(thr_fires) == 1 and res_fires == []

    # market resolves YES → only res_h fires.
    asyncio.run(e.on_resolved("a", {"market": "0xabc", "winner": "YES"}))
    assert len(thr_fires) == 1
    assert len(res_fires) == 1
