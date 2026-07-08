"""resolve_kalshi_event_descriptions — the propose-time single-shot
matcher resolver for trigger.kalshi (sibling of the polymarket resolver).
"""
from __future__ import annotations

import pytest

from backend.news_events.parsing.polymarket_match import MatchResult
from backend.workflows import propose as propose_mod
from backend.workflows.propose import (
    ProposalValidationError,
    resolve_kalshi_event_descriptions,
)

pytestmark = pytest.mark.asyncio


def _patch_matcher(monkeypatch, result: MatchResult) -> None:
    async def _match(desc, **kw):
        return result
    monkeypatch.setattr(
        "backend.news_events.parsing.kalshi_match."
        "match_event_to_kalshi_contract",
        _match,
    )


async def test_resolver_fills_ids_on_high_confidence(monkeypatch) -> None:
    _patch_matcher(monkeypatch, MatchResult(
        matched=True, market_id="KXFED-26JAN-H",
        token_id="KXFED-26JAN-H:YES", side="YES",
        question="Will the Fed cut in January?", confidence=0.93,
    ))
    raw = {"steps": [{
        "step_type": "trigger.kalshi",
        "config": {"event_description": "fire when the Fed cuts in January"},
    }]}
    await resolve_kalshi_event_descriptions(raw)
    cfg = raw["steps"][0]["config"]
    assert cfg["market_id"] == "KXFED-26JAN-H"
    assert cfg["token_id"] == "KXFED-26JAN-H:YES"
    assert cfg["side"] == "YES"
    assert cfg["question"] == "Will the Fed cut in January?"


async def test_resolver_raises_on_low_confidence(monkeypatch) -> None:
    _patch_matcher(monkeypatch, MatchResult(
        matched=False, confidence=0.4, reason="ambiguous"))
    raw = {"steps": [{
        "step_type": "trigger.kalshi",
        "config": {"event_description": "something vague"},
    }]}
    with pytest.raises(ProposalValidationError) as exc:
        await resolve_kalshi_event_descriptions(raw)
    assert "propose_kalshi_trigger" in str(exc.value)


async def test_resolver_skips_already_resolved(monkeypatch) -> None:
    """A step that already has ids must not invoke the matcher."""
    called = {"n": 0}

    async def _match(desc, **kw):  # pragma: no cover
        called["n"] += 1
        return MatchResult(matched=True)

    monkeypatch.setattr(
        "backend.news_events.parsing.kalshi_match."
        "match_event_to_kalshi_contract", _match)
    raw = {"steps": [{
        "step_type": "trigger.kalshi",
        "config": {"market_id": "T", "token_id": "T:YES",
                   "event_description": "ignored"},
    }]}
    await resolve_kalshi_event_descriptions(raw)
    assert called["n"] == 0


async def test_resolver_ignores_non_kalshi_steps(monkeypatch) -> None:
    raw = {"steps": [{"step_type": "trigger.price",
                      "config": {"symbol": "RELIANCE"}}]}
    await resolve_kalshi_event_descriptions(raw)  # must not raise
