"""Regression: LiveDataAccessor's live-close fast path must actually
reach a working quote function.

Found while investigating a live report (2026-07-15) that a GOLD-on-MCX
automation got tagged exchange="NSE". Root cause turned out to be a
separate, more severe bug one level down: `_live_close` imported
`backend.kite.market_data.get_live_quote` — a function whose real
signature is `(access_token, instruments_list)` — and called it as
`get_live_quote(symbol, exchange=exchange)`. Every call raised
TypeError, silently caught, so `get_price(offset=0, basis="close")`
returned None for EVERY symbol on EVERY live evaluation, not just MCX
ones. Fixed by calling `backend.kite.live_quote.get_kite_quote(symbol,
exchange=...)` — the single-quote helper every other "live quote by
symbol" call site in the codebase already uses.
"""
from __future__ import annotations

from backend.workflows.dsl.data_accessor import LiveDataAccessor


def test_live_close_reaches_a_working_quote_function(monkeypatch):
    calls = []

    def fake_get_kite_quote(symbol, exchange="NSE"):
        calls.append((symbol, exchange))
        return {"last_price": 1234.5}

    monkeypatch.setattr(
        "backend.kite.live_quote.get_kite_quote", fake_get_kite_quote,
    )
    acc = LiveDataAccessor()
    price = acc.get_price(
        symbol="RELIANCE", exchange="NSE", basis="close", offset=0,
    )
    assert price == 1234.5
    assert calls == [("RELIANCE", "NSE")]


def test_live_close_passes_through_the_requested_exchange(monkeypatch):
    calls = []

    def fake_get_kite_quote(symbol, exchange="NSE"):
        calls.append((symbol, exchange))
        return {"last_price": 71500.0}

    monkeypatch.setattr(
        "backend.kite.live_quote.get_kite_quote", fake_get_kite_quote,
    )
    acc = LiveDataAccessor()
    price = acc.get_price(
        symbol="GOLD", exchange="MCX", basis="close", offset=0,
    )
    assert price == 71500.0
    assert calls == [("GOLD", "MCX")]


def test_live_close_returns_none_on_quote_failure_not_raise(monkeypatch):
    def raising_get_kite_quote(symbol, exchange="NSE"):
        raise RuntimeError("kite session expired")

    monkeypatch.setattr(
        "backend.kite.live_quote.get_kite_quote", raising_get_kite_quote,
    )
    acc = LiveDataAccessor()
    price = acc.get_price(
        symbol="RELIANCE", exchange="NSE", basis="close", offset=0,
    )
    assert price is None
