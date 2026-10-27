"""Kalshi contract matcher — stubbed search + LLM (no network)."""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from backend.news_events.parsing import kalshi_match
from backend.news_events.sources.kalshi import KalshiSnapshot

pytestmark = pytest.mark.asyncio


def _snap(ticker: str, question: str, yes: float = 0.4) -> KalshiSnapshot:
    return KalshiSnapshot(
        market_id=ticker, slug=None, question=question, yes_price=yes,
        closed=False, raw={"ticker": ticker},
    )


def _patch(monkeypatch, *, candidates, llm_json) -> None:
    async def _search(query, *, limit=8):
        return candidates

    monkeypatch.setattr(kalshi_match, "search_via_public_search", _search)

    class _Client:
        async def complete(self, **kw):
            return SimpleNamespace(content=json.dumps(llm_json), finish_reason="stop")

    monkeypatch.setattr(kalshi_match, "get_llm_client", lambda: _Client())


async def test_high_confidence_yes_match(monkeypatch) -> None:
    _patch(
        monkeypatch,
        candidates=[_snap("KXFED-26JAN-H", "Will the Fed cut in January?")],
        llm_json={"match_index": 0, "side": "YES", "confidence": 0.92,
                  "reason": "direct match"},
    )
    res = await kalshi_match.match_event_to_kalshi_contract(
        "fire when the Fed cuts rates in January")
    assert res.matched is True
    assert res.market_id == "KXFED-26JAN-H"
    assert res.token_id == "KXFED-26JAN-H:YES"
    assert res.side == "YES"


async def test_no_side_maps_to_no_asset(monkeypatch) -> None:
    _patch(
        monkeypatch,
        candidates=[_snap("KXFED-26JAN-H", "Will the Fed cut in January?")],
        llm_json={"match_index": 0, "side": "NO", "confidence": 0.9,
                  "reason": "negation"},
    )
    res = await kalshi_match.match_event_to_kalshi_contract(
        "fire when the Fed does NOT cut in January")
    assert res.matched is True
    assert res.token_id == "KXFED-26JAN-H:NO"
    assert res.side == "NO"


async def test_low_confidence_returns_candidates(monkeypatch) -> None:
    _patch(
        monkeypatch,
        candidates=[_snap("A", "Market A"), _snap("B", "Market B")],
        llm_json={"match_index": 0, "side": "YES", "confidence": 0.4,
                  "reason": "ambiguous"},
    )
    res = await kalshi_match.match_event_to_kalshi_contract("vague ask")
    assert res.matched is False
    assert len(res.candidates) == 2


async def test_no_candidates_found(monkeypatch) -> None:
    _patch(monkeypatch, candidates=[], llm_json={})
    res = await kalshi_match.match_event_to_kalshi_contract("nothing matches this")
    assert res.matched is False
    assert "no open kalshi markets" in res.reason
