"""HTTP-surface tests for the news_events admin router.

We mount the router on a minimal FastAPI app instead of the real
``backend.main:app`` so the global ``settings.news_events_enabled``
flag doesn't have to be flipped for tests — the production main.py
keeps its conditional include working as designed.

The auth path (``require_user``) is exercised through the same
``auth_headers`` fixture the rest of the suite uses, so the JWT
contract stays consistent.
"""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.database import get_db
from backend.news_events.router import router as news_events_router


def _build_client(db):
    """Spin up a minimal app that mounts ONLY the news_events router,
    with the same get_db override the global ``client`` fixture uses."""
    app = FastAPI()
    app.include_router(news_events_router)

    def _override():
        yield db

    app.dependency_overrides[get_db] = _override
    return TestClient(app)


def test_admin_sources_requires_auth(db):
    client = _build_client(db)
    r = client.get("/api/news-events/admin/sources")
    assert r.status_code == 401


def test_admin_sources_returns_registry(db, auth_headers):
    client = _build_client(db)
    r = client.get("/api/news-events/admin/sources", headers=auth_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    sources = body["sources"]
    # Phase 1 shipped 5 sources (RBI ×3, BBC, Google News); Phase 7
    # added Telegram channels. Assert the original five are still in
    # the registry, and that the count grew (rather than shrank).
    assert len(sources) >= 5
    source_ids = {s["source_id"] for s in sources}
    for must in ("rbi_press_releases", "rbi_notifications", "rbi_speeches",
                 "bbc_world", "google_news_search_india_markets"):
        assert must in source_ids
    # Each row carries the static registry fields and zero health.
    # Phase 7 added Telegram channels, four of which ship in the
    # registry as enabled=False until their usernames are verified
    # (tg_ani_news, tg_pib_india, tg_reuters_india, tg_etmarkets).
    # So we only assert that the original five RSS rows are enabled.
    REQUIRED_ENABLED = {
        "rbi_press_releases", "rbi_notifications", "rbi_speeches",
        "bbc_world", "google_news_search_india_markets",
    }
    for s in sources:
        assert s["feed_url"].startswith("http")
        assert s["consecutive_failures"] == 0
        if s["source_id"] in REQUIRED_ENABLED:
            assert s["enabled"] is True


def test_admin_metrics_zero_when_no_articles(db, auth_headers):
    client = _build_client(db)
    r = client.get(
        "/api/news-events/admin/metrics?window_hours=24",
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["window_hours"] == 24
    assert body["articles_ingested"] == 0
    assert body["events_fired"] == 0


def test_force_poll_unknown_source_returns_404(db, auth_headers):
    client = _build_client(db)
    r = client.post(
        "/api/news-events/admin/sources/no_such_source/poll",
        headers=auth_headers,
    )
    assert r.status_code == 404
