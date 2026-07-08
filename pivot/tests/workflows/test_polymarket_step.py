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
from unittest.mock import patch

import pytest

from backend.models import Workflow, WorkflowStatus, WorkflowStep
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


# ── supervisor scan ───────────────────────────────────────────────────


def _make_workflow(db, *, user_id: int, status: WorkflowStatus,
                   steps: list[tuple[str, dict]]) -> Workflow:
    wf = Workflow(
        user_id=user_id, name="t", description=None, status=status,
    )
    db.add(wf)
    db.flush()
    for idx, (st, cfg) in enumerate(steps):
        db.add(WorkflowStep(
            workflow_id=wf.id, step_index=idx, step_type=st, config=cfg,
        ))
    db.commit()
    db.refresh(wf)
    return wf


def test_supervisor_scan_picks_up_active_workflow_polymarket_step(
    db, auth_headers,
):
    from backend.auth.jwt_handler import get_user_id_from_token
    from backend.news_events.workers.polymarket_ws_worker import (
        _scan_active_workflow_steps,
    )
    user_id = get_user_id_from_token(
        auth_headers["Authorization"].split(" ", 1)[1]
    )
    assert user_id is not None
    wf = _make_workflow(db, user_id=user_id, status=WorkflowStatus.active,
                        steps=[
                            ("trigger.manual", {}),
                            ("action.place_order", {
                                "symbol": "RELIANCE", "side": "buy",
                                "quantity": 10, "order_type": "market",
                            }),
                            ("trigger.polymarket", {
                                "market_id": "m1", "token_id": "scan_tok",
                                "side": "YES", "threshold": 0.5,
                                "direction": "above",
                            }),
                        ])
    # Supervisor scan opens its own SessionLocal — patch it to our
    # in-test session so it sees the just-inserted workflow.
    with patch(
        "backend.news_events.workers.polymarket_ws_worker.SessionLocal",
        return_value=db,
    ), patch.object(db, "close", lambda: None):
        result = _scan_active_workflow_steps()

    key = (wf.id, 2)
    assert key in result
    assert result[key].asset_id == "scan_tok"
    assert result[key].mode == "threshold"
    assert result[key].threshold == pytest.approx(0.5)


def test_supervisor_scan_ignores_draft_workflow(db, auth_headers):
    from backend.auth.jwt_handler import get_user_id_from_token
    from backend.news_events.workers.polymarket_ws_worker import (
        _scan_active_workflow_steps,
    )
    user_id = get_user_id_from_token(
        auth_headers["Authorization"].split(" ", 1)[1]
    )
    wf = _make_workflow(db, user_id=user_id, status=WorkflowStatus.draft,
                        steps=[
                            ("trigger.polymarket", {
                                "market_id": "m1", "token_id": "tok",
                                "threshold": 0.5,
                            }),
                        ])
    with patch(
        "backend.news_events.workers.polymarket_ws_worker.SessionLocal",
        return_value=db,
    ), patch.object(db, "close", lambda: None):
        result = _scan_active_workflow_steps()
    assert (wf.id, 0) not in result


def test_supervisor_scan_skips_step_missing_token_id(db, auth_headers):
    from backend.auth.jwt_handler import get_user_id_from_token
    from backend.news_events.workers.polymarket_ws_worker import (
        _scan_active_workflow_steps,
    )
    user_id = get_user_id_from_token(
        auth_headers["Authorization"].split(" ", 1)[1]
    )
    # Bypass validation by inserting raw — a malformed-in-DB row should
    # be filtered by the scanner, not crash it.
    wf = Workflow(user_id=user_id, name="t", description=None,
                  status=WorkflowStatus.active)
    db.add(wf)
    db.flush()
    db.add(WorkflowStep(
        workflow_id=wf.id, step_index=0,
        step_type="trigger.polymarket",
        config={"market_id": "m1"},  # no token_id
    ))
    db.commit()
    db.refresh(wf)

    with patch(
        "backend.news_events.workers.polymarket_ws_worker.SessionLocal",
        return_value=db,
    ), patch.object(db, "close", lambda: None):
        result = _scan_active_workflow_steps()
    assert (wf.id, 0) not in result


def test_supervisor_scan_handles_resolution_mode(db, auth_headers):
    from backend.auth.jwt_handler import get_user_id_from_token
    from backend.news_events.workers.polymarket_ws_worker import (
        _scan_active_workflow_steps,
    )
    user_id = get_user_id_from_token(
        auth_headers["Authorization"].split(" ", 1)[1]
    )
    wf = _make_workflow(db, user_id=user_id, status=WorkflowStatus.active,
                        steps=[
                            ("trigger.polymarket", {
                                "market_id": "m1", "token_id": "res_tok",
                                "side": "YES", "mode": "resolution",
                                "resolve_on": "NO",
                            }),
                        ])
    with patch(
        "backend.news_events.workers.polymarket_ws_worker.SessionLocal",
        return_value=db,
    ), patch.object(db, "close", lambda: None):
        result = _scan_active_workflow_steps()
    key = (wf.id, 0)
    assert key in result
    reg = result[key]
    assert reg.mode == "resolution"
    assert reg.resolve_on == "NO"
    # threshold/direction don't matter in resolution mode but the
    # dataclass defaults to above/None.
    assert reg.threshold is None
