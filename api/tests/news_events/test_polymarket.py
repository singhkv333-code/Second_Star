"""Tests for backend.news_events.sources.polymarket.

httpx is mocked via MockTransport so we never touch the network.
"""
from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from backend.news_events.sources import polymarket as pm


def _patched_async_client(monkeypatch, handler):
    real_init = httpx.AsyncClient.__init__

    def _patched_init(self, *args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        real_init(self, *args, **kwargs)

    monkeypatch.setattr(httpx.AsyncClient, "__init__", _patched_init)


def _market_payload(*, mid="m-1", slug="trump-2028", yes=0.62, closed=False):
    return {
        "id": mid,
        "slug": slug,
        "question": "Will Trump win the 2028 US presidential election?",
        "outcomes": json.dumps(["Yes", "No"]),
        "outcomePrices": json.dumps([str(yes), str(round(1 - yes, 2))]),
        "closed": closed,
    }


# ── search_markets ──────────────────────────────────────────────────


def test_search_markets_returns_parsed_snapshots(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/markets"
        assert request.url.params["search"] == "trump 2028"
        body = [_market_payload(yes=0.62), _market_payload(mid="m-2", yes=0.31)]
        return httpx.Response(200, json=body)

    _patched_async_client(monkeypatch, handler)
    out = asyncio.run(pm.search_markets("trump 2028"))
    assert len(out) == 2
    assert out[0].yes_price == pytest.approx(0.62)
    assert out[0].market_id == "m-1"
    assert out[1].yes_price == pytest.approx(0.31)


def test_search_markets_returns_empty_on_5xx(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="upstream down")

    _patched_async_client(monkeypatch, handler)
    out = asyncio.run(pm.search_markets("xxx"))
    assert out == []


def test_search_markets_returns_empty_on_blank_query(monkeypatch):
    out = asyncio.run(pm.search_markets("   "))
    assert out == []


def test_search_markets_handles_wrapped_payload(monkeypatch):
    # Some Gamma endpoints wrap results in {"data": [...]}.
    def handler(request):
        return httpx.Response(200, json={"data": [_market_payload(yes=0.5)]})

    _patched_async_client(monkeypatch, handler)
    out = asyncio.run(pm.search_markets("x"))
    assert len(out) == 1
    assert out[0].yes_price == pytest.approx(0.5)


# ── get_market ──────────────────────────────────────────────────────


def test_get_market_success(monkeypatch):
    def handler(request):
        assert request.url.path == "/markets/m-1"
        return httpx.Response(200, json=_market_payload(yes=0.73))

    _patched_async_client(monkeypatch, handler)
    snap = asyncio.run(pm.get_market("m-1"))
    assert snap is not None
    assert snap.market_id == "m-1"
    assert snap.yes_price == pytest.approx(0.73)


def test_get_market_404_returns_none(monkeypatch):
    def handler(request):
        return httpx.Response(404, text="not found")

    _patched_async_client(monkeypatch, handler)
    snap = asyncio.run(pm.get_market("m-missing"))
    assert snap is None


def test_get_market_handles_tokens_array(monkeypatch):
    # Some markets carry a tokens array instead of outcomes/prices.
    payload = {
        "id": "m-2",
        "slug": "x",
        "question": "X?",
        "tokens": [
            {"outcome": "Yes", "price": 0.81},
            {"outcome": "No", "price": 0.19},
        ],
    }

    def handler(request):
        return httpx.Response(200, json=payload)

    _patched_async_client(monkeypatch, handler)
    snap = asyncio.run(pm.get_market("m-2"))
    assert snap is not None
    assert snap.yes_price == pytest.approx(0.81)


def test_get_market_unparseable_returns_none(monkeypatch):
    def handler(request):
        return httpx.Response(200, text="not json")

    _patched_async_client(monkeypatch, handler)
    snap = asyncio.run(pm.get_market("m-x"))
    assert snap is None


def test_yes_price_clamped_to_unit_interval(monkeypatch):
    def handler(request):
        return httpx.Response(200, json=_market_payload(yes=1.7))

    _patched_async_client(monkeypatch, handler)
    snap = asyncio.run(pm.get_market("m-1"))
    assert snap is not None
    assert snap.yes_price == 1.0  # clamped
