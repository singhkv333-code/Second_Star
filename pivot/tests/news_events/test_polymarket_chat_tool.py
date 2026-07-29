"""Slice-2 tests: chat tool + endpoint + activation immediate-reconcile.

Covers:
  - propose_polymarket_trigger handler: matched (draft card) and
    unmatched (picker card) paths; bad input.
  - POST /api/news-events/specs/polymarket: persists a spec with the
    right resolution_criteria shape.
  - POST /api/news-events/specs/{id}/activate: pokes the WS supervisor
    when the spec carries polymarket_token_id.
"""
from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.agents.tool_executor import execute_tool
from backend.database import get_db
from backend.news_events.models import NewsEventSpec
from backend.news_events.parsing.polymarket_match import Candidate, MatchResult
from backend.news_events.router import router as news_events_router


def _build_client(db):
    app = FastAPI()
    app.include_router(news_events_router)

    def _override():
        yield db

    app.dependency_overrides[get_db] = _override
    return TestClient(app)


# ── chat tool handler ────────────────────────────────────────────────


def _match_matched():
    return MatchResult(
        matched=True,
        market_id="m1",
        token_id="ytok",
        side="YES",
        question="Will Bitcoin hit $150k by Dec 31, 2026?",
        confidence=0.92,
        reason="clean match on Bitcoin $150k market",
        candidates=[
            Candidate(
                market_id="m1", slug="btc-150k", question="Will Bitcoin hit $150k by Dec 31, 2026?",
                yes_price=0.05, yes_token_id="ytok", no_token_id="ntok", closed=False,
            ),
        ],
    )


def _match_unmatched():
    return MatchResult(
        matched=False,
        reason="low confidence pick",
        confidence=0.4,
        market_id="m1",
        token_id="ytok",
        side="YES",
        question="Will Eric Trump win 2028?",
        candidates=[
            Candidate(market_id="m1", slug="s1", question="Will Eric Trump win 2028?",
                      yes_price=0.01, yes_token_id="y1", no_token_id="n1", closed=False),
            Candidate(market_id="m2", slug="s2", question="Will JD Vance win 2028?",
                      yes_price=0.19, yes_token_id="y2", no_token_id="n2", closed=False),
        ],
    )


def test_chat_tool_matched_returns_draft_card():
    async def fake(desc):
        return _match_matched()

    with patch(
        "backend.news_events.parsing.polymarket_match.match_event_to_polymarket_contract",
        side_effect=fake,
    ):
        out = asyncio.run(execute_tool(
            "propose_polymarket_trigger",
            {"event_description": "Bitcoin $150k by year-end",
             "threshold": 0.3, "direction": "above"},
            "fake_kite_token", None, 42,
        ))
    assert out["success"] is True
    d = out["data"]
    assert d["_render_hint"] == "polymarket_trigger_draft"
    assert d["matched"] is True
    assert d["market_id"] == "m1"
    assert d["token_id"] == "ytok"
    assert d["side"] == "YES"
    assert d["threshold"] == 0.3
    assert d["direction"] == "above"
    assert d["confidence"] == pytest.approx(0.92)
    assert len(d["candidates"]) == 1


def test_chat_tool_unmatched_returns_picker_card():
    async def fake(desc):
        return _match_unmatched()

    with patch(
        "backend.news_events.parsing.polymarket_match.match_event_to_polymarket_contract",
        side_effect=fake,
    ):
        out = asyncio.run(execute_tool(
            "propose_polymarket_trigger",
            {"event_description": "Trump 2028", "threshold": 0.7},
            "fake_kite_token", None, 42,
        ))
    assert out["success"] is True
    d = out["data"]
    assert d["_render_hint"] == "polymarket_trigger_picker"
    assert d["matched"] is False
    # Best guess is surfaced so the picker can pre-highlight.
    assert d["best_guess_market_id"] == "m1"
    assert d["best_guess_side"] == "YES"
    assert len(d["candidates"]) == 2
    # Defaults applied: direction='above' from tool defaults registry.
    assert d["direction"] == "above"


def test_chat_tool_rejects_empty_description():
    out = asyncio.run(execute_tool(
        "propose_polymarket_trigger",
        {"event_description": "  ", "threshold": 0.5},
        "kt", None, 42,
    ))
    assert out["success"] is False
    assert "event_description" in out["error"]


def test_chat_tool_rejects_out_of_range_threshold():
    out = asyncio.run(execute_tool(
        "propose_polymarket_trigger",
        {"event_description": "anything", "threshold": 1.5},
        "kt", None, 42,
    ))
    assert out["success"] is False
    assert "1.5" in out["error"] or "out of" in out["error"]


def test_chat_tool_rejects_non_numeric_threshold():
    out = asyncio.run(execute_tool(
        "propose_polymarket_trigger",
        {"event_description": "anything", "threshold": "not-a-number"},
        "kt", None, 42,
    ))
    assert out["success"] is False
    assert "threshold" in out["error"]


# ── slice-4 smart-approval — threshold-omitted preset derivation ─────


def _patch_get_market(monkeypatch, end_date: str = "2026-08-30T00:00:00Z"):
    from backend.news_events.sources.polymarket import PolymarketSnapshot

    async def stub(mid):
        return PolymarketSnapshot(
            market_id="m1", slug="s", question="Q?",
            yes_price=0.14, closed=False,
            raw={"endDate": end_date,
                 "outcomes": '["Yes","No"]',
                 "clobTokenIds": '["ytok","ntok"]'},
        )

    monkeypatch.setattr(
        "backend.news_events.sources.polymarket.get_market", stub,
    )


def test_chat_tool_smart_default_when_threshold_omitted(monkeypatch):
    async def fake(desc):
        return MatchResult(
            matched=True, market_id="m1", token_id="ytok", side="YES",
            question="Will Iran ceasefire continue?", confidence=0.94,
            reason="strong",
            candidates=[Candidate(
                market_id="m1", slug="s",
                question="Will Iran ceasefire continue?",
                yes_price=0.14, yes_token_id="ytok", no_token_id="ntok",
                closed=False,
            )],
        )
    monkeypatch.setattr(
        "backend.news_events.parsing.polymarket_match.match_event_to_polymarket_contract",
        fake,
    )
    _patch_get_market(monkeypatch)

    out = asyncio.run(execute_tool(
        "propose_polymarket_trigger",
        {"event_description": "alert me if Iran ceasefire breaks down"},
        "kt", None, 42,
    ))
    d = out["data"]
    assert d["mode"] == "threshold"
    assert d["_render_hint"] == "polymarket_trigger_draft"
    assert d["current_yes_price"] == pytest.approx(0.14)
    assert len(d["threshold_presets"]) == 3
    assert d["threshold_was_assumed"] is True
    # Middle chip is pre-selected as effective threshold.
    assert d["threshold"] == d["threshold_preselected"]
    assert d["timeline_default"] == "2026-08-30T00:00:00Z"


def test_chat_tool_user_supplied_threshold_skips_assumption(monkeypatch):
    async def fake(desc):
        return MatchResult(
            matched=True, market_id="m1", token_id="ytok", side="YES",
            question="Q", confidence=0.92, reason="ok",
            candidates=[Candidate(
                market_id="m1", slug="s", question="Q",
                yes_price=0.14, yes_token_id="ytok", no_token_id="ntok",
                closed=False,
            )],
        )
    monkeypatch.setattr(
        "backend.news_events.parsing.polymarket_match.match_event_to_polymarket_contract",
        fake,
    )
    _patch_get_market(monkeypatch)

    out = asyncio.run(execute_tool(
        "propose_polymarket_trigger",
        {"event_description": "alert me at 25%", "threshold": 0.25},
        "kt", None, 42,
    ))
    d = out["data"]
    assert d["threshold"] == pytest.approx(0.25)
    assert d["threshold_was_assumed"] is False


def test_chat_tool_resolution_mode_suppresses_threshold_ui(monkeypatch):
    async def fake(desc):
        return MatchResult(
            matched=True, market_id="m1", token_id="ytok", side="YES",
            question="Will Iran ceasefire continue?", confidence=0.95,
            reason="strong",
            candidates=[Candidate(
                market_id="m1", slug="s",
                question="Will Iran ceasefire continue?",
                yes_price=0.14, yes_token_id="ytok", no_token_id="ntok",
                closed=False,
            )],
        )
    monkeypatch.setattr(
        "backend.news_events.parsing.polymarket_match.match_event_to_polymarket_contract",
        fake,
    )
    _patch_get_market(monkeypatch)

    out = asyncio.run(execute_tool(
        "propose_polymarket_trigger",
        {"event_description": "execute when Iran ceasefire actually breaks down",
         "mode": "resolution", "resolve_on": "NO"},
        "kt", None, 42,
    ))
    d = out["data"]
    assert d["mode"] == "resolution"
    assert d["resolve_on"] == "NO"
    assert d["threshold"] is None
    assert d["threshold_presets"] == []
    assert d["direction"] is None
    # Timeline still shown so user knows when watching stops.
    assert d["timeline_default"] == "2026-08-30T00:00:00Z"


# ── POST /api/news-events/specs/polymarket ───────────────────────────


def test_create_polymarket_spec_persists_with_correct_resolution_criteria(
    db, auth_headers,
):
    client = _build_client(db)
    r = client.post(
        "/api/news-events/specs/polymarket",
        headers=auth_headers,
        json={
            "event_description": "Bitcoin hits $150k by Dec 31, 2026",
            "market_id": "m1",
            "token_id": "ytok_btc_150k",
            "side": "YES",
            "threshold": 0.3,
            "direction": "above",
            "question": "Will Bitcoin hit $150k by Dec 31, 2026?",
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["state"] == "draft"
    assert body["tier"] == "tier3"
    rc = body["resolution_criteria"]
    assert rc["polymarket_market_id"] == "m1"
    assert rc["polymarket_token_id"] == "ytok_btc_150k"
    assert rc["polymarket_side"] == "YES"
    assert rc["prediction_market_threshold"] == pytest.approx(0.3)
    assert rc["polymarket_threshold_direction"] == "above"
    assert rc["polymarket_question"] == "Will Bitcoin hit $150k by Dec 31, 2026?"
    # Verify it actually landed in the DB.
    spec = db.query(NewsEventSpec).filter(NewsEventSpec.id == body["id"]).first()
    assert spec is not None
    assert spec.state == "draft"
    assert (spec.resolution_criteria or {})["polymarket_token_id"] == "ytok_btc_150k"


def test_create_polymarket_spec_rejects_out_of_range_threshold(db, auth_headers):
    client = _build_client(db)
    r = client.post(
        "/api/news-events/specs/polymarket",
        headers=auth_headers,
        json={
            "event_description": "anything goes here",
            "market_id": "m1",
            "token_id": "tok",
            "side": "YES",
            "threshold": 1.5,
        },
    )
    assert r.status_code == 422


def test_create_polymarket_spec_requires_auth(db):
    client = _build_client(db)
    r = client.post(
        "/api/news-events/specs/polymarket",
        json={
            "event_description": "x" * 10,
            "market_id": "m1",
            "token_id": "tok",
            "side": "YES",
            "threshold": 0.3,
        },
    )
    assert r.status_code == 401


# ── /activate immediate-reconcile hook ───────────────────────────────


def test_activate_pokes_reconcile_when_token_present(db, auth_headers):
    """Activating a WS-mode spec calls request_immediate_reconcile()
    so the supervisor picks it up on the next event-loop tick.
    Worker module is mocked — we only check the call happens."""
    client = _build_client(db)
    # Create a WS-mode draft spec.
    r = client.post(
        "/api/news-events/specs/polymarket",
        headers=auth_headers,
        json={
            "event_description": "alert me if X probability above 70%",
            "market_id": "m1",
            "token_id": "tok_ws",
            "side": "YES",
            "threshold": 0.7,
            "direction": "above",
        },
    )
    assert r.status_code == 200, r.text
    spec_id = r.json()["id"]

    with patch(
        "backend.news_events.workers.polymarket_ws_worker.request_immediate_reconcile",
    ) as mock_reconcile:
        r2 = client.post(
            f"/api/news-events/specs/{spec_id}/activate",
            headers=auth_headers,
        )
    assert r2.status_code == 200, r2.text
    assert r2.json()["state"] == "active"
    mock_reconcile.assert_called_once()


def test_activate_does_not_poke_reconcile_for_non_ws_spec(db, auth_headers):
    """An ordinary news-driven spec without polymarket_token_id must
    NOT trigger the reconcile call."""
    client = _build_client(db)
    # Insert a draft spec directly (no polymarket token).
    from backend.auth.jwt_handler import get_user_id_from_token
    user_id = get_user_id_from_token(
        auth_headers["Authorization"].split(" ", 1)[1]
    )
    assert user_id is not None
    spec = NewsEventSpec(
        user_id=user_id,
        workflow_id=None,
        tier="tier1",
        description="RBI cuts the repo rate",
        resolution_criteria={
            "primary_sources": ["rbi_press_releases"],
            "min_secondary_confirmations": 0,
            "min_confidence": 0.85,
            "conflict_policy": "fire",
        },
        retraction_policy={"safety_window_minutes": 60, "action": "ignore"},
        keyword_set={"must_have_one": ["RBI"], "must_have_one_of": [], "must_not_have": []},
        state="draft",
    )
    db.add(spec)
    db.commit()
    db.refresh(spec)

    with patch(
        "backend.news_events.workers.polymarket_ws_worker.request_immediate_reconcile",
    ) as mock_reconcile:
        r = client.post(
            f"/api/news-events/specs/{spec.id}/activate",
            headers=auth_headers,
        )
    assert r.status_code == 200, r.text
    assert r.json()["state"] == "active"
    mock_reconcile.assert_not_called()
