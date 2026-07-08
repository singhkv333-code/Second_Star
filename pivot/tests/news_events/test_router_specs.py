"""HTTP-surface tests for the Phase 4 spec endpoints.

Same pattern as Phase 1's ``test_router.py``: spin up a minimal app
that mounts only the news_events router and exercise via a real
TestClient. The parser is monkey-patched at the router import
boundary so the tests don't touch the live LLM surface.
"""
from __future__ import annotations

from dataclasses import dataclass

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.database import get_db
from backend.news_events.parsing import event_spec_parser as parser_mod
from backend.news_events.parsing.event_spec_parser import ParsedSpec
from backend.news_events.router import router as news_events_router
from backend.news_events.schemas import (
    KeywordSet,
    ResolutionCriteria,
    RetractionPolicy,
)


def _build_client(db):
    app = FastAPI()
    app.include_router(news_events_router)

    def _override():
        yield db

    app.dependency_overrides[get_db] = _override
    return TestClient(app)


def _tier1_parsed() -> ParsedSpec:
    return ParsedSpec(
        description="RBI cuts repo rate",
        tier="tier1",
        keyword_set=KeywordSet(must_have_one=["RBI", "repo"]),
        resolution_criteria=ResolutionCriteria(
            primary_sources=["rbi_press_releases"],
            conflict_policy="fire",
        ),
        retraction_policy=RetractionPolicy(
            safety_window_minutes=60, action="cancel_and_alert"
        ),
        needs_disambiguation=False,
    )


def _tier3_parsed() -> ParsedSpec:
    return ParsedSpec(
        description="Trump wins the 2028 election",
        tier="tier3",
        keyword_set=KeywordSet(must_have_one=["Trump", "wins"]),
        resolution_criteria=ResolutionCriteria(
            min_secondary_confirmations=1, conflict_policy="hold"
        ),
        retraction_policy=RetractionPolicy(
            safety_window_minutes=240, action="cancel_pending_approvals"
        ),
        needs_disambiguation=True,
    )


def test_create_tier1_returns_draft_spec(monkeypatch, db, auth_headers):
    async def fake_parse(text: str):
        return _tier1_parsed()

    monkeypatch.setattr(parser_mod, "parse_event_spec", fake_parse)
    # Rebind on the router module too — the import was `from ... import parse_event_spec`.
    from backend.news_events import router as router_mod
    monkeypatch.setattr(router_mod, "parse_event_spec", fake_parse)

    client = _build_client(db)
    r = client.post(
        "/api/news-events/specs",
        json={"text": "If RBI cuts repo rate buy SBI"},
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["spec"] is not None
    assert body["disambiguation"] is None
    assert body["spec"]["state"] == "draft"
    assert body["spec"]["tier"] == "tier1"

    spec_id = body["spec"]["id"]
    # Activate.
    r = client.post(
        f"/api/news-events/specs/{spec_id}/activate", headers=auth_headers
    )
    assert r.status_code == 200, r.text
    assert r.json()["state"] == "active"


def test_create_tier3_returns_disambiguation(monkeypatch, db, auth_headers):
    async def fake_parse(text: str):
        return _tier3_parsed()

    from backend.news_events import router as router_mod
    monkeypatch.setattr(router_mod, "parse_event_spec", fake_parse)

    client = _build_client(db)
    r = client.post(
        "/api/news-events/specs",
        json={"text": "If Trump wins 2028 buy defense"},
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["spec"] is None
    assert body["disambiguation"] is not None
    questions = body["disambiguation"]["questions"]
    assert len(questions) >= 1
    spec_id = body["disambiguation"]["spec_id"]

    # Activate before answering → 409.
    r = client.post(
        f"/api/news-events/specs/{spec_id}/activate", headers=auth_headers
    )
    assert r.status_code == 409

    # Answer the first question.
    r = client.post(
        f"/api/news-events/specs/{spec_id}/disambiguate",
        json={
            "question_id": questions[0]["id"],
            "option_id": questions[0]["options"][1]["id"],
        },
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text
    # Still in disambiguation — one more question to go.
    assert r.json()["spec"] is None
    assert r.json()["disambiguation"] is not None

    # Answer the second question — spec flips to draft.
    r = client.post(
        f"/api/news-events/specs/{spec_id}/disambiguate",
        json={
            "question_id": questions[1]["id"],
            "option_id": questions[1]["options"][0]["id"],
        },
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["spec"] is not None
    assert body["spec"]["state"] == "draft"
    assert body["spec"]["id"] == spec_id

    # Now activate.
    r = client.post(
        f"/api/news-events/specs/{spec_id}/activate", headers=auth_headers
    )
    assert r.status_code == 200, r.text
    assert r.json()["state"] == "active"


def test_list_and_get_spec(monkeypatch, db, auth_headers):
    async def fake_parse(text: str):
        return _tier1_parsed()

    from backend.news_events import router as router_mod
    monkeypatch.setattr(router_mod, "parse_event_spec", fake_parse)

    client = _build_client(db)
    r = client.post(
        "/api/news-events/specs",
        json={"text": "RBI cuts rate"},
        headers=auth_headers,
    )
    spec_id = r.json()["spec"]["id"]

    r = client.get("/api/news-events/specs", headers=auth_headers)
    assert r.status_code == 200
    assert any(s["id"] == spec_id for s in r.json()["specs"])

    r = client.get(f"/api/news-events/specs/{spec_id}", headers=auth_headers)
    assert r.status_code == 200
    assert r.json()["id"] == spec_id


def test_cancel_endpoint(monkeypatch, db, auth_headers):
    async def fake_parse(text: str):
        return _tier1_parsed()

    from backend.news_events import router as router_mod
    monkeypatch.setattr(router_mod, "parse_event_spec", fake_parse)

    client = _build_client(db)
    r = client.post(
        "/api/news-events/specs",
        json={"text": "RBI cuts rate"},
        headers=auth_headers,
    )
    spec_id = r.json()["spec"]["id"]
    r = client.post(
        f"/api/news-events/specs/{spec_id}/cancel", headers=auth_headers
    )
    assert r.status_code == 200
    assert r.json()["state"] == "cancelled"


def test_cross_user_returns_404(monkeypatch, db, auth_headers):
    async def fake_parse(text: str):
        return _tier1_parsed()

    from backend.news_events import router as router_mod
    monkeypatch.setattr(router_mod, "parse_event_spec", fake_parse)

    client = _build_client(db)
    # User A creates a spec.
    r = client.post(
        "/api/news-events/specs",
        json={"text": "RBI cuts rate"},
        headers=auth_headers,
    )
    spec_id = r.json()["spec"]["id"]

    # User B — mint a JWT for a different user_id directly. The
    # small test app doesn't include /auth/register, so we bypass.
    from backend.auth.jwt_handler import create_access_token
    other_token = create_access_token(user_id=9999, email="other@pivot.com")
    other_headers = {"Authorization": f"Bearer {other_token}"}
    r = client.get(
        f"/api/news-events/specs/{spec_id}", headers=other_headers
    )
    assert r.status_code == 404


def test_parser_failure_returns_422(monkeypatch, db, auth_headers):
    async def fake_parse(text: str):
        raise parser_mod.ParserError("boom")

    from backend.news_events import router as router_mod
    monkeypatch.setattr(router_mod, "parse_event_spec", fake_parse)

    client = _build_client(db)
    r = client.post(
        "/api/news-events/specs",
        json={"text": "anything"},
        headers=auth_headers,
    )
    assert r.status_code == 422
    assert "boom" in r.text
