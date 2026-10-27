"""Tests for backend.news_events.parsing.polymarket_match.

Both the Polymarket search and the LLM client are mocked so the
tests never touch the network. The mocking pattern matches
test_event_spec_parser.py (FakeClient injected via monkeypatch on
get_llm_client).
"""
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass

import pytest

from backend.news_events.parsing import polymarket_match as m
from backend.news_events.parsing.polymarket_match import (
    _entity_fallback_query,
    _keyword_fallback_query,
    _search_query_from_description,
)
from backend.news_events.sources.polymarket import PolymarketSnapshot


@dataclass
class _FakeResponse:
    content: str


class _FakeClient:
    """Minimal LLM stub: returns canned content from each .complete()."""

    def __init__(self, contents):
        if isinstance(contents, str):
            contents = [contents]
        self._contents = list(contents)
        self.calls = 0
        self.last_messages: list = []

    async def complete(self, **kwargs):
        self.calls += 1
        self.last_messages = kwargs.get("messages") or []
        idx = min(self.calls - 1, len(self._contents) - 1)
        return _FakeResponse(self._contents[idx])


def _patch_llm(monkeypatch, fake: _FakeClient) -> None:
    monkeypatch.setattr(m, "get_llm_client", lambda: fake)


def _patch_search(monkeypatch, snapshots: list[PolymarketSnapshot]) -> None:
    async def _stub(query, *, limit=8):
        return snapshots

    monkeypatch.setattr(m, "search_via_public_search", _stub)


def _snap(*, mid: str, q: str, yes: float, yes_tok: str, no_tok: str,
          closed: bool = False) -> PolymarketSnapshot:
    return PolymarketSnapshot(
        market_id=mid,
        slug=mid,
        question=q,
        yes_price=yes,
        closed=closed,
        raw={
            "outcomes": json.dumps(["Yes", "No"]),
            "clobTokenIds": json.dumps([yes_tok, no_tok]),
        },
    )


# ── happy paths ──────────────────────────────────────────────────────


def test_match_returns_high_confidence_pick(monkeypatch):
    snaps = [
        _snap(mid="m1", q="Will Bitcoin hit $150k by June 2026?",
              yes=0.05, yes_tok="ytok1", no_tok="ntok1"),
        _snap(mid="m2", q="Will Trump be re-elected in 2028?",
              yes=0.52, yes_tok="ytok2", no_tok="ntok2"),
    ]
    _patch_search(monkeypatch, snaps)
    _patch_llm(monkeypatch, _FakeClient(json.dumps({
        "match_index": 1,
        "side": "YES",
        "confidence": 0.92,
        "reason": "the user asked about Trump 2028",
    })))

    out = asyncio.run(m.match_event_to_polymarket_contract(
        "alert me if Trump wins the 2028 election goes above 60%"
    ))
    assert out.matched is True
    assert out.market_id == "m2"
    assert out.token_id == "ytok2"
    assert out.side == "YES"
    assert out.confidence == pytest.approx(0.92)
    assert len(out.candidates) == 2


def test_match_no_side_picks_NO_token(monkeypatch):
    snaps = [_snap(mid="m1", q="Will Modi be PM by 2029?",
                   yes=0.7, yes_tok="ytok1", no_tok="ntok1")]
    _patch_search(monkeypatch, snaps)
    _patch_llm(monkeypatch, _FakeClient(json.dumps({
        "match_index": 0, "side": "NO", "confidence": 0.88, "reason": "negation",
    })))

    out = asyncio.run(m.match_event_to_polymarket_contract(
        "alert me if Modi won't be PM by 2029 goes above 40%"
    ))
    assert out.matched is True
    assert out.side == "NO"
    assert out.token_id == "ntok1"


# ── decline / low confidence ─────────────────────────────────────────


def test_no_candidates_returns_unmatched(monkeypatch):
    _patch_search(monkeypatch, [])
    _patch_llm(monkeypatch, _FakeClient(""))  # never called
    out = asyncio.run(m.match_event_to_polymarket_contract("something obscure"))
    assert out.matched is False
    assert "no open markets" in out.reason or "no candidates" in out.reason


def test_llm_declines_with_null_index(monkeypatch):
    snaps = [_snap(mid="m1", q="A", yes=0.2, yes_tok="y", no_tok="n")]
    _patch_search(monkeypatch, snaps)
    _patch_llm(monkeypatch, _FakeClient(json.dumps({
        "match_index": None, "side": "YES", "confidence": 0.0,
        "reason": "no clear match",
    })))
    out = asyncio.run(m.match_event_to_polymarket_contract("xyz"))
    assert out.matched is False
    assert "declined" in out.reason or "no clear match" in out.reason
    assert len(out.candidates) == 1


def test_low_confidence_returns_unmatched_but_surfaces_pick(monkeypatch):
    snaps = [_snap(mid="m1", q="A", yes=0.2, yes_tok="y", no_tok="n")]
    _patch_search(monkeypatch, snaps)
    _patch_llm(monkeypatch, _FakeClient(json.dumps({
        "match_index": 0, "side": "YES", "confidence": 0.40, "reason": "weak",
    })))
    out = asyncio.run(m.match_event_to_polymarket_contract("vague"))
    assert out.matched is False
    assert "low confidence" in out.reason
    # Low-confidence pick is still surfaced so the chat picker can
    # pre-highlight the LLM's best guess.
    assert out.market_id == "m1"
    assert out.token_id == "y"


# ── robustness ───────────────────────────────────────────────────────


def test_llm_returns_invalid_json(monkeypatch):
    snaps = [_snap(mid="m1", q="A", yes=0.2, yes_tok="y", no_tok="n")]
    _patch_search(monkeypatch, snaps)
    _patch_llm(monkeypatch, _FakeClient("not json at all"))
    out = asyncio.run(m.match_event_to_polymarket_contract("x"))
    assert out.matched is False
    assert "JSON" in out.reason


def test_llm_returns_out_of_range_index(monkeypatch):
    snaps = [_snap(mid="m1", q="A", yes=0.2, yes_tok="y", no_tok="n")]
    _patch_search(monkeypatch, snaps)
    _patch_llm(monkeypatch, _FakeClient(json.dumps({
        "match_index": 5, "side": "YES", "confidence": 0.9, "reason": "x",
    })))
    out = asyncio.run(m.match_event_to_polymarket_contract("x"))
    assert out.matched is False
    assert "out-of-range" in out.reason


def test_candidate_without_token_ids_is_dropped(monkeypatch):
    # raw payload with mismatched array lengths → no tokens extracted
    bad = PolymarketSnapshot(
        market_id="m1", slug="s", question="q", yes_price=0.5, closed=False,
        raw={"outcomes": '["Yes", "No"]', "clobTokenIds": '["only_one"]'},
    )
    _patch_search(monkeypatch, [bad])
    _patch_llm(monkeypatch, _FakeClient(""))
    out = asyncio.run(m.match_event_to_polymarket_contract("x"))
    assert out.matched is False
    assert "token ids" in out.reason or "candidates" in out.reason


def test_empty_query_returns_unmatched(monkeypatch):
    _patch_llm(monkeypatch, _FakeClient(""))
    out = asyncio.run(m.match_event_to_polymarket_contract("   "))
    assert out.matched is False
    assert "empty" in out.reason


# ── query extraction helpers ─────────────────────────────────────────


@pytest.mark.parametrize("raw, want", [
    ("alert me if Trump wins the 2028 US presidential election",
     "Trump wins the 2028 US presidential election"),
    ("tell me when Bitcoin hits $150k probability above 30%",
     "Bitcoin hits $150k"),
    ("Fed cuts rates at the June 2026 meeting",
     "Fed cuts rates at the June 2026 meeting"),
    ("the election goes well", "the election goes well"),  # 'goes' at idx 13
    ("above zero", "above zero"),  # marker at idx 0 → not stripped
])
def test_search_query_strips_chat_prefixes_and_threshold_clauses(raw, want):
    assert _search_query_from_description(raw) == want


@pytest.mark.parametrize("raw, want", [
    ("alert me if Trump wins the 2028 US presidential election", "Trump 2028 US"),
    ("alert me if Trump does NOT win the 2028 US election", "Trump 2028 US"),
    ("tell me when Bitcoin hits $150k probability above 30%", "Bitcoin $150k"),
    ("Fed cuts rates at the June 2026 meeting", "Fed June 2026"),
    ("India wins the next T20 World Cup", "India T20 World Cup"),
])
def test_entity_fallback_drops_stopwords_and_negations(raw, want):
    assert _entity_fallback_query(raw) == want


@pytest.mark.parametrize("raw, want", [
    ("India wins the next T20 World Cup", "India wins T20 World Cup"),
    ("Fed cuts rates at the June 2026 meeting", "Fed cuts rates June 2026 meeting"),
])
def test_keyword_fallback_drops_stopwords_only(raw, want):
    assert _keyword_fallback_query(raw) == want


def test_search_chain_fans_out_until_match(monkeypatch):
    """Verify the four-tier fallback: cleaned → keyword → entity → raw.
    The stub returns empty for the first two, candidates for the entity
    query — we expect search to be called multiple times until success."""
    calls: list[str] = []
    candidates = [
        PolymarketSnapshot(
            market_id="m1", slug="s1", question="Will Donald Trump win 2028?",
            yes_price=0.42, closed=False,
            raw={"outcomes": '["Yes","No"]', "clobTokenIds": '["y","n"]'},
        ),
    ]

    async def stub_search(q, *, limit=8):
        calls.append(q)
        # Returns candidates only for the entity-query shape (short
        # all-caps tokens). The primary cleaned query is the long form
        # and gets no hits.
        if q == "Trump 2028 US":
            return candidates
        return []

    monkeypatch.setattr(m, "search_via_public_search", stub_search)
    _patch_llm(monkeypatch, _FakeClient(json.dumps({
        "match_index": 0, "side": "YES", "confidence": 0.91, "reason": "ok",
    })))
    out = asyncio.run(m.match_event_to_polymarket_contract(
        "alert me if Trump wins the 2028 US presidential election"
    ))
    # Entity-fallback runs FIRST in the chain (it's the most aggressive
    # noise-stripper); the cleaned-phrase + keyword + raw forms are
    # tried only if entity returns empty.
    assert calls[0] == "Trump 2028 US"
    assert out.matched is True
    assert out.market_id == "m1"
