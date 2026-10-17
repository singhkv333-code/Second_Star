"""Tests for browse_events() source helper + browse_polymarket_markets chat tool.

httpx is mocked via MockTransport for the source layer so the tests
never touch the network. The chat tool's handler is tested against
the mocked source via the same execute_tool boundary the rest of
the suite uses.
"""
from __future__ import annotations

import asyncio
import json
from unittest.mock import patch

import httpx
import pytest

from backend.agents.tool_executor import execute_tool
from backend.news_events.sources import polymarket as pm


def _patched_async_client(monkeypatch, handler):
    real_init = httpx.AsyncClient.__init__

    def _patched_init(self, *args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        real_init(self, *args, **kwargs)

    monkeypatch.setattr(httpx.AsyncClient, "__init__", _patched_init)


def _event_payload(
    *,
    title="Test event",
    slug="test-event",
    volume=12345.0,
    tags=None,
    end_date="2026-12-31T00:00:00Z",
    markets=None,
    closed=False,
):
    return {
        "title": title,
        "slug": slug,
        "endDate": end_date,
        "volume24hr": volume,
        "tags": tags if tags is not None else [{"label": "Politics"}],
        "closed": closed,
        "archived": False,
        "active": True,
        "markets": markets if markets is not None else [],
    }


def _market_payload(
    *,
    mid="m-1",
    question="Will X happen?",
    yes=0.42,
    yes_tok="ytok",
    no_tok="ntok",
    volume=999.0,
    closed=False,
    active=True,
):
    return {
        "id": mid,
        "question": question,
        "outcomes": json.dumps(["Yes", "No"]),
        "outcomePrices": json.dumps([str(yes), str(round(1 - yes, 2))]),
        "clobTokenIds": json.dumps([yes_tok, no_tok]),
        "active": active,
        "closed": closed,
        "archived": False,
        "volume24hr": volume,
    }


# ── topic mode → /public-search?q= ───────────────────────────────────


def test_browse_events_with_topic_hits_public_search(monkeypatch):
    seen_url: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_url.append(request.url.path)
        assert request.url.params["q"] == "Bitcoin"
        body = {
            "events": [
                _event_payload(
                    title="When will Bitcoin hit $150k?",
                    tags=[{"label": "Bitcoin"}, {"label": "Crypto"}],
                    markets=[
                        _market_payload(
                            mid="m1",
                            question="Will Bitcoin hit $150k by Dec 31?",
                            yes=0.05, yes_tok="y1", no_tok="n1",
                            volume=500_000.0,
                        ),
                        _market_payload(
                            mid="m2",
                            question="Will Bitcoin hit $150k by June 30?",
                            yes=0.01, yes_tok="y2", no_tok="n2",
                            volume=200_000.0,
                        ),
                    ],
                ),
            ],
            "pagination": {},
        }
        return httpx.Response(200, json=body)

    _patched_async_client(monkeypatch, handler)
    events = asyncio.run(pm.browse_events("Bitcoin", limit=5))
    assert seen_url == ["/public-search"]
    assert len(events) == 1
    e = events[0]
    assert e["title"] == "When will Bitcoin hit $150k?"
    assert e["tags"] == ["Bitcoin", "Crypto"]
    assert len(e["markets"]) == 2
    # Markets sorted by volume desc within the event.
    assert e["markets"][0]["market_id"] == "m1"
    assert e["markets"][0]["yes_token_id"] == "y1"
    assert e["markets"][0]["volume_24h"] == pytest.approx(500_000.0)


def test_browse_events_no_topic_hits_events_endpoint(monkeypatch):
    seen_url: list[str] = []
    seen_params: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_url.append(request.url.path)
        seen_params.append(dict(request.url.params))
        # /events returns a bare list, not wrapped under "events".
        body = [
            _event_payload(
                title="2026 FIFA World Cup Winner",
                volume=23_000_000.0,
                tags=[{"label": "Soccer"}, {"label": "Sports"}],
                markets=[_market_payload(mid="brazil", question="Brazil?")],
            ),
            _event_payload(
                title="Top event 2",
                volume=11_000_000.0,
                markets=[_market_payload(mid="m_e2", question="E2?")],
            ),
        ]
        return httpx.Response(200, json=body)

    _patched_async_client(monkeypatch, handler)
    events = asyncio.run(pm.browse_events(None, limit=5))
    assert seen_url == ["/events"]
    assert seen_params[0]["closed"] == "false"
    assert seen_params[0]["order"] == "volume24hr"
    assert len(events) == 2
    assert events[0]["title"] == "2026 FIFA World Cup Winner"


# ── filters ──────────────────────────────────────────────────────────


def test_browse_events_drops_closed_event(monkeypatch):
    def handler(request):
        body = {
            "events": [
                _event_payload(title="closed one", closed=True,
                               markets=[_market_payload()]),
                _event_payload(title="open one",
                               markets=[_market_payload(mid="open_m")]),
            ],
        }
        return httpx.Response(200, json=body)

    _patched_async_client(monkeypatch, handler)
    events = asyncio.run(pm.browse_events("anything"))
    assert len(events) == 1
    assert events[0]["title"] == "open one"


def test_browse_events_drops_closed_markets_within_event(monkeypatch):
    def handler(request):
        body = {
            "events": [
                _event_payload(
                    title="mixed",
                    markets=[
                        _market_payload(mid="open_one"),
                        _market_payload(mid="closed_one", closed=True),
                        _market_payload(mid="inactive_one", active=False),
                    ],
                ),
            ],
        }
        return httpx.Response(200, json=body)

    _patched_async_client(monkeypatch, handler)
    events = asyncio.run(pm.browse_events("anything"))
    assert len(events) == 1
    assert len(events[0]["markets"]) == 1
    assert events[0]["markets"][0]["market_id"] == "open_one"


def test_browse_events_drops_event_with_no_open_markets(monkeypatch):
    def handler(request):
        body = {
            "events": [
                _event_payload(
                    title="all closed",
                    markets=[_market_payload(mid="c1", closed=True)],
                ),
                _event_payload(
                    title="has open",
                    markets=[_market_payload(mid="open")],
                ),
            ],
        }
        return httpx.Response(200, json=body)

    _patched_async_client(monkeypatch, handler)
    events = asyncio.run(pm.browse_events("anything"))
    assert {e["title"] for e in events} == {"has open"}


def test_browse_events_caps_markets_per_event(monkeypatch):
    def handler(request):
        markets = [
            _market_payload(mid=f"m{i}", question=f"q{i}", volume=10.0 * i)
            for i in range(8)
        ]
        return httpx.Response(200, json={"events": [_event_payload(markets=markets)]})

    _patched_async_client(monkeypatch, handler)
    events = asyncio.run(pm.browse_events("x", markets_per_event=3))
    assert len(events[0]["markets"]) == 3
    # Sorted by volume desc — m7 (highest) should be first.
    assert events[0]["markets"][0]["market_id"] == "m7"


# ── error paths ──────────────────────────────────────────────────────


def test_browse_events_returns_empty_on_5xx(monkeypatch):
    def handler(request):
        return httpx.Response(503, text="down")

    _patched_async_client(monkeypatch, handler)
    assert asyncio.run(pm.browse_events("Bitcoin")) == []


def test_browse_events_returns_empty_on_unexpected_shape(monkeypatch):
    def handler(request):
        return httpx.Response(200, json={"unexpected": "shape"})

    _patched_async_client(monkeypatch, handler)
    assert asyncio.run(pm.browse_events("Bitcoin")) == []


# ── chat tool wrapper ────────────────────────────────────────────────


def test_chat_tool_browse_topic_path():
    async def fake_browse(topic, *, limit, markets_per_event):
        return [{"title": "Event 1", "slug": "e1", "end_date": None,
                 "volume_24h": 1000.0, "tags": ["Bitcoin"],
                 "markets": [{"market_id": "m1", "question": "q1",
                              "yes_price": 0.5, "yes_token_id": "y",
                              "no_token_id": "n", "volume_24h": 100}]}]
    with patch(
        "backend.news_events.sources.polymarket.browse_events",
        side_effect=fake_browse,
    ):
        out = asyncio.run(execute_tool(
            "browse_polymarket_markets",
            {"topic": "Bitcoin", "limit": 5}, "kt", None, 42,
        ))
    assert out["success"] is True
    d = out["data"]
    assert d["_render_hint"] == "polymarket_market_browse_card"
    assert d["topic"] == "Bitcoin"
    assert d["result_count"] == 1
    assert d["events"][0]["title"] == "Event 1"


def test_chat_tool_browse_no_topic_returns_top_volume():
    async def fake_browse(topic, *, limit, markets_per_event):
        assert topic is None  # the handler converts "" / missing → None
        return [{"title": "FIFA WC", "slug": "fifa", "end_date": None,
                 "volume_24h": 20_000_000.0, "tags": ["Sports"],
                 "markets": []}]
    with patch(
        "backend.news_events.sources.polymarket.browse_events",
        side_effect=fake_browse,
    ):
        out = asyncio.run(execute_tool(
            "browse_polymarket_markets", {}, "kt", None, 42,
        ))
    assert out["success"] is True
    assert out["data"]["topic"] is None
    # Default limit comes from the tool's defaults registry.
    assert out["data"]["limit"] == 10


def test_chat_tool_browse_clamps_oversize_limit():
    async def fake_browse(topic, *, limit, markets_per_event):
        assert limit == 20  # clamped from 9999
        return []
    with patch(
        "backend.news_events.sources.polymarket.browse_events",
        side_effect=fake_browse,
    ):
        out = asyncio.run(execute_tool(
            "browse_polymarket_markets", {"limit": 9999}, "kt", None, 42,
        ))
    assert out["data"]["limit"] == 20


def test_chat_tool_browse_empty_result_carries_reason():
    async def fake_browse(topic, *, limit, markets_per_event):
        return []
    with patch(
        "backend.news_events.sources.polymarket.browse_events",
        side_effect=fake_browse,
    ):
        out = asyncio.run(execute_tool(
            "browse_polymarket_markets",
            {"topic": "very obscure topic"}, "kt", None, 42,
        ))
    assert out["success"] is True
    assert out["data"]["result_count"] == 0
    assert "very obscure topic" in out["data"]["empty_reason"]


def test_chat_tool_browse_handles_source_error():
    async def fake_browse(topic, *, limit, markets_per_event):
        raise RuntimeError("upstream blew up")
    with patch(
        "backend.news_events.sources.polymarket.browse_events",
        side_effect=fake_browse,
    ):
        out = asyncio.run(execute_tool(
            "browse_polymarket_markets", {"topic": "x"}, "kt", None, 42,
        ))
    assert out["success"] is False
    assert "browse failed" in out["error"]
    assert "upstream blew up" in out["error"]
