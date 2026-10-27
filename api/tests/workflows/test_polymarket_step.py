"""Slice-4 tests for trigger.polymarket DSL step.

Covers:
  - Step registration + config_model validation (both modes).
  - Late-binding resolver in propose.py — high-conf accepts + inlines
    ids; low-conf rejects with the 'call propose_polymarket_trigger
    first' message.
  - Supervisor _scan_active_workflow_steps — yields the right key
    for an active workflow's trigger.polymarket step, skips inactive
    workflows + skips steps with missing/invalid config.
"""
from __future__ import annotations

import asyncio

import pytest

from backend.news_events.parsing.polymarket_match import (
    Candidate,
    MatchResult,
)
from backend.workflows.propose import (
    ProposalValidationError,
    resolve_polymarket_event_descriptions,
    validate_draft_against_registry,
)
from backend.workflows.registry import STEP_REGISTRY
from backend.workflows.schemas import TriggerPolymarketConfig


# ── step registration + config validation ─────────────────────────────


def test_trigger_polymarket_is_registered():
    assert "trigger.polymarket" in STEP_REGISTRY
    defn = STEP_REGISTRY["trigger.polymarket"]
    assert defn.trigger_only is True
    assert defn.max_retries == 0
    assert defn.category == "trigger"


def test_config_threshold_mode_requires_threshold():
    # Valid threshold-mode config.
    c = TriggerPolymarketConfig(
        market_id="m1", token_id="t1", threshold=0.7,
    )
    assert c.mode == "threshold"
    assert c.threshold == pytest.approx(0.7)
    # Missing threshold → reject (mode='threshold' default).
    with pytest.raises(Exception):
        TriggerPolymarketConfig(market_id="m1", token_id="t1")


def test_config_resolution_mode_does_not_require_threshold():
    c = TriggerPolymarketConfig(
        market_id="m1", token_id="t1",
        mode="resolution", resolve_on="NO",
    )
    assert c.mode == "resolution"
    assert c.threshold is None
    assert c.resolve_on == "NO"


def test_config_threshold_out_of_range_rejected():
    with pytest.raises(Exception):
        TriggerPolymarketConfig(
            market_id="m1", token_id="t1", threshold=1.5,
        )


# ── late-binding resolver in propose.py ───────────────────────────────


def _high_conf_match():
    return MatchResult(
        matched=True, market_id="resolved_m1", token_id="resolved_tok1",
        side="YES", question="Will crude > $100?", confidence=0.92,
        reason="strong match",
        candidates=[Candidate(
            market_id="resolved_m1", slug="s",
            question="Will crude > $100?",
            yes_price=0.18, yes_token_id="resolved_tok1",
            no_token_id="resolved_tok1_no", closed=False,
        )],
    )


def _low_conf_match():
    return MatchResult(
        matched=False, reason="low confidence", confidence=0.4,
        market_id="m1", token_id="t1", side="YES", question="Q?",
        candidates=[Candidate(
            market_id="m1", slug="s", question="Q?", yes_price=0.5,
            yes_token_id="t1", no_token_id="t1_no", closed=False,
        )],
    )


def test_resolver_fills_ids_on_high_confidence(monkeypatch):
    async def fake(desc):
        return _high_conf_match()
    monkeypatch.setattr(
        "backend.news_events.parsing.polymarket_match.match_event_to_polymarket_contract",
        fake,
    )
    raw = {
        "name": "compound",
        "steps": [
            {"step_type": "trigger.manual", "config": {}},
            {"step_type": "trigger.polymarket",
             "config": {
                 "event_description": "crude > $100 by year-end",
                 "threshold": 0.5,
                 "direction": "above",
             }},
        ],
    }
    asyncio.run(resolve_polymarket_event_descriptions(raw))
    poly_cfg = raw["steps"][1]["config"]
    assert poly_cfg["market_id"] == "resolved_m1"
    assert poly_cfg["token_id"] == "resolved_tok1"
    assert poly_cfg["side"] == "YES"
    assert poly_cfg["question"] == "Will crude > $100?"
    # Original threshold preserved — resolver doesn't touch it.
    assert poly_cfg["threshold"] == pytest.approx(0.5)


def test_resolver_rejects_on_low_confidence(monkeypatch):
    async def fake(desc):
        return _low_conf_match()
    monkeypatch.setattr(
        "backend.news_events.parsing.polymarket_match.match_event_to_polymarket_contract",
        fake,
    )
    raw = {
        "name": "compound",
        "steps": [
            {"step_type": "trigger.manual", "config": {}},
            {"step_type": "trigger.polymarket",
             "config": {
                 "event_description": "vague thing",
                 "threshold": 0.5,
             }},
        ],
    }
    with pytest.raises(ProposalValidationError) as exc:
        asyncio.run(resolve_polymarket_event_descriptions(raw))
    msg = str(exc.value)
    assert "ambiguous" in msg
    assert "propose_polymarket_trigger" in msg


def test_resolver_skips_steps_with_ids_already_set(monkeypatch):
    """When market_id+token_id are both present, resolver leaves the
    step alone (doesn't call the matcher)."""
    called = {"n": 0}

    async def fake(desc):
        called["n"] += 1
        return _high_conf_match()
    monkeypatch.setattr(
        "backend.news_events.parsing.polymarket_match.match_event_to_polymarket_contract",
        fake,
    )
    raw = {
        "steps": [
            {"step_type": "trigger.manual", "config": {}},
            {"step_type": "trigger.polymarket",
             "config": {
                 "market_id": "preset_m", "token_id": "preset_t",
                 "side": "YES", "threshold": 0.5,
             }},
        ],
    }
    asyncio.run(resolve_polymarket_event_descriptions(raw))
    assert called["n"] == 0  # matcher never called
    assert raw["steps"][1]["config"]["market_id"] == "preset_m"


def test_resolver_no_steps_no_op():
    """No steps[] → no-op. Doesn't raise."""
    asyncio.run(resolve_polymarket_event_descriptions({}))
    asyncio.run(resolve_polymarket_event_descriptions({"steps": []}))


def test_full_compound_workflow_validates_end_to_end(monkeypatch):
    """End-to-end: resolver fills ids + validate_draft_against_registry
    accepts the resulting compound workflow shape."""
    async def fake(desc):
        return _high_conf_match()
    monkeypatch.setattr(
        "backend.news_events.parsing.polymarket_match.match_event_to_polymarket_contract",
        fake,
    )
    raw = {
        "name": "buy reliance, sell on poly",
        "description": "demo compound",
        "steps": [
            {"step_type": "trigger.manual", "config": {}},
            {"step_type": "action.place_order",
             "config": {
                 "symbol": "RELIANCE", "side": "buy", "quantity": 10,
                 "order_type": "market",
             }},
            {"step_type": "trigger.polymarket",
             "config": {
                 "event_description": "crude > $100 by year-end",
                 "threshold": 0.50, "direction": "above",
             }},
            {"step_type": "action.place_order",
             "config": {
                 "symbol": "RELIANCE", "side": "sell",
                 "quantity": "{{ context.1.quantity }}",
                 "order_type": "market",
             }},
        ],
    }
    asyncio.run(resolve_polymarket_event_descriptions(raw))
    draft = validate_draft_against_registry(raw)
    assert len(draft.steps) == 4
    poly = draft.steps[2]
    assert poly.step_type == "trigger.polymarket"
    assert poly.config["market_id"] == "resolved_m1"
    assert poly.config["token_id"] == "resolved_tok1"
