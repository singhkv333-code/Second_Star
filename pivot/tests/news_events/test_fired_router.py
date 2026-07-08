"""HTTP-surface tests for GET /api/news-events/fired/{id}.

Spins up the small-app pattern from Phase 1. Seeds a NewsFiredEvent
+ supporting NewsArticleClassification rows directly.
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.auth.jwt_handler import create_access_token
from backend.database import get_db
from backend.news_events.models import (
    NewsArticle,
    NewsArticleClassification,
    NewsEventSpec,
    NewsFiredEvent,
)
from backend.news_events.router import router as news_events_router


def _build_client(db):
    app = FastAPI()
    app.include_router(news_events_router)

    def _override():
        yield db

    app.dependency_overrides[get_db] = _override
    return TestClient(app)


def _seed(db, *, user_id: int = 1) -> tuple[NewsEventSpec, NewsFiredEvent]:
    spec = NewsEventSpec(
        user_id=user_id,
        tier="tier1",
        description="RBI cuts repo rate",
        resolution_criteria={"primary_sources": ["rbi_press_releases"]},
        retraction_policy={"safety_window_minutes": 60, "action": "cancel_and_alert"},
        keyword_set={"must_have_one": ["RBI"], "must_have_one_of": [], "must_not_have": []},
        state="fired",
    )
    db.add(spec)
    db.flush()

    article = NewsArticle(
        source_id="rbi_press_releases",
        url="https://example.test/rbi/x",
        url_hash="u_x",
        title="RBI cuts repo rate",
        title_hash="t_x",
        summary=None,
    )
    db.add(article)
    db.flush()

    cls = NewsArticleClassification(
        article_id=article.id,
        event_spec_id=spec.id,
        stage_2_passed=True,
        classifier_verdict="YES",
        confidence=0.93,
        excerpt="The MPC cut the repo rate to 5.75%.",
        embedding_similarity=0.85,
    )
    db.add(cls)
    db.flush()

    fired = NewsFiredEvent(
        event_spec_id=spec.id,
        workflow_run_id="run-abc",
        fired_at=datetime.now(timezone.utc),
        tier="tier1",
        aggregated_confidence=0.93,
        supporting_classification_ids=[cls.id],
        retraction_status="none",
    )
    db.add(fired)
    db.flush()
    db.commit()
    return spec, fired


def _headers_for(user_id: int) -> dict:
    token = create_access_token(user_id=user_id, email=f"u{user_id}@pivot.com")
    return {"Authorization": f"Bearer {token}"}


def test_fired_audit_view_happy_path(db):
    spec, fired = _seed(db, user_id=1)
    client = _build_client(db)
    r = client.get(f"/api/news-events/fired/{fired.id}", headers=_headers_for(1))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["id"] == fired.id
    assert body["event_spec_id"] == spec.id
    assert body["spec_tier"] == "tier1"
    assert body["spec_description"] == "RBI cuts repo rate"
    assert body["workflow_run_id"] == "run-abc"
    assert body["aggregated_confidence"] == 0.93
    assert len(body["supporting"]) == 1
    s = body["supporting"][0]
    assert s["classifier_verdict"] == "YES"
    assert s["source_id"] == "rbi_press_releases"
    assert "5.75%" in s["excerpt"]


def test_fired_audit_cross_user_returns_404(db):
    spec, fired = _seed(db, user_id=1)
    client = _build_client(db)
    r = client.get(f"/api/news-events/fired/{fired.id}", headers=_headers_for(9999))
    assert r.status_code == 404


def test_fired_audit_unknown_id_returns_404(db):
    _seed(db, user_id=1)
    client = _build_client(db)
    r = client.get("/api/news-events/fired/nonexistent-id", headers=_headers_for(1))
    assert r.status_code == 404
