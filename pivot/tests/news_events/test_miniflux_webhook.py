"""Tests for backend.news_events.webhooks.miniflux + the FastAPI route.

Two layers:

  - Pure helpers: ``verify_signature``, ``parse_payload``,
    ``_source_id_for`` (table-driven, no HTTP).
  - HTTP surface: real POST to the router via TestClient, with
    HMAC-signed bodies — exercises the 401/400/200 paths.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.database import get_db
from backend.news_events.models import NewsArticle, NewsSourceHealth
from backend.news_events.router import router as news_events_router
from backend.news_events.webhooks.miniflux import (
    parse_payload,
    verify_signature,
)


SECRET = "supersecret-shared-with-miniflux"


def _build_client(db, monkeypatch):
    """Stand up the news_events router on a minimal app with the
    real get_db override + the configured miniflux secret."""
    monkeypatch.setattr(
        "backend.news_events.router.settings.miniflux_webhook_secret",
        SECRET,
        raising=False,
    )
    # The route reads settings.miniflux_webhook_secret directly; the
    # monkeypatch above replaces it inside the router module's
    # imported settings object.
    app = FastAPI()
    app.include_router(news_events_router)

    def _override():
        yield db

    app.dependency_overrides[get_db] = _override
    return TestClient(app)


def _sign(body: bytes, secret: str = SECRET) -> str:
    return "sha256=" + hmac.new(
        secret.encode("utf-8"), body, hashlib.sha256
    ).hexdigest()


def _payload(feed_id=17, entries=None) -> dict:
    return {
        "event_type": "new_entries",
        "feed": {
            "id": feed_id,
            "title": "RBI Press Releases",
            "site_url": "https://www.rbi.org.in/",
        },
        "entries": entries or [
            {
                "id": 142,
                "title": "RBI cuts repo rate by 25 bps",
                "url": f"https://rbi.org.in/article/{uuid.uuid4().hex[:8]}",
                "published_at": "2026-05-21T04:00:00Z",
                "content": "MPC reduced the repo rate to 5.75% with immediate effect.",
            }
        ],
    }


# ── Pure helpers ─────────────────────────────────────────────────────


def test_verify_signature_accepts_prefixed_and_bare_hex():
    body = b'{"x":1}'
    expected = hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()
    assert verify_signature(
        secret=SECRET, raw_body=body, signature_header=f"sha256={expected}"
    )
    assert verify_signature(
        secret=SECRET, raw_body=body, signature_header=expected
    )


def test_verify_signature_rejects_tampered_body():
    body = b'{"x":1}'
    sig = _sign(body)
    assert not verify_signature(
        secret=SECRET, raw_body=b'{"x":2}', signature_header=sig
    )


def test_verify_signature_rejects_bad_format():
    assert not verify_signature(
        secret=SECRET, raw_body=b"x", signature_header="not-a-hex-string"
    )
    assert not verify_signature(
        secret=SECRET, raw_body=b"x", signature_header=""
    )


def test_verify_signature_rejects_empty_secret():
    assert not verify_signature(
        secret="", raw_body=b"x", signature_header=_sign(b"x")
    )


def test_parse_payload_extracts_items():
    src_id, items = parse_payload(_payload(feed_id=17))
    assert src_id == "miniflux_feed_17"
    assert len(items) == 1
    item = items[0]
    assert "5.75%" in (item.summary or "")
    assert item.url.startswith("https://rbi.org.in/article/")
    assert item.published_at is not None


def test_parse_payload_handles_non_new_entries_event():
    payload = {"event_type": "saved_entry", "feed": {"id": 1}}
    src_id, items = parse_payload(payload)
    assert items == []


def test_parse_payload_skips_malformed_entries():
    p = _payload(
        entries=[
            {"id": 1, "title": "ok", "url": "https://x.test/a"},
            {"id": 2, "title": "", "url": "https://x.test/b"},  # bad
            {"id": 3, "title": "good 2", "url": ""},  # bad
            {"id": 4, "title": "good 3", "url": "https://x.test/c"},
        ]
    )
    _, items = parse_payload(p)
    assert len(items) == 2
    assert items[0].title == "ok"
    assert items[1].title == "good 3"


def test_parse_payload_source_id_fallback_uses_title():
    p = _payload()
    p["feed"] = {"title": "Some Random Feed"}  # no id
    src_id, _ = parse_payload(p)
    assert src_id.startswith("miniflux_feed_")
    assert "some_random_feed" in src_id


# ── HTTP route ───────────────────────────────────────────────────────


def test_webhook_401_when_secret_unset(db, monkeypatch):
    monkeypatch.setattr(
        "backend.news_events.router.settings.miniflux_webhook_secret",
        "",
        raising=False,
    )
    app = FastAPI()
    app.include_router(news_events_router)

    def _override(): yield db
    app.dependency_overrides[get_db] = _override
    client = TestClient(app)

    r = client.post("/api/news-events/webhook/miniflux", json=_payload())
    assert r.status_code == 401


def test_webhook_401_on_bad_signature(db, monkeypatch):
    client = _build_client(db, monkeypatch)
    body = json.dumps(_payload()).encode("utf-8")
    r = client.post(
        "/api/news-events/webhook/miniflux",
        content=body,
        headers={"X-Miniflux-Signature": "sha256=" + ("0" * 64),
                 "Content-Type": "application/json"},
    )
    assert r.status_code == 401


def test_webhook_happy_path_persists_article(db, monkeypatch):
    client = _build_client(db, monkeypatch)
    payload = _payload()
    body = json.dumps(payload).encode("utf-8")
    r = client.post(
        "/api/news-events/webhook/miniflux",
        content=body,
        headers={"X-Miniflux-Signature": _sign(body),
                 "Content-Type": "application/json"},
    )
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["status"] == "ok"
    assert out["items_new"] == 1
    assert out["source_id"] == "miniflux_feed_17"

    # Verify the article actually landed in the test DB.
    rows = db.query(NewsArticle).filter(
        NewsArticle.source_id == "miniflux_feed_17"
    ).all()
    assert len(rows) == 1
    assert rows[0].title == "RBI cuts repo rate by 25 bps"

    # Source health was upserted by persist_pushed_items.
    health = db.query(NewsSourceHealth).filter(
        NewsSourceHealth.source_id == "miniflux_feed_17"
    ).first()
    assert health is not None
    assert health.last_successful_fetch_at is not None


def test_webhook_idempotent_on_repeat_post(db, monkeypatch):
    client = _build_client(db, monkeypatch)
    payload = _payload()
    body = json.dumps(payload).encode("utf-8")
    sig = _sign(body)

    r1 = client.post(
        "/api/news-events/webhook/miniflux",
        content=body,
        headers={"X-Miniflux-Signature": sig,
                 "Content-Type": "application/json"},
    )
    r2 = client.post(
        "/api/news-events/webhook/miniflux",
        content=body,
        headers={"X-Miniflux-Signature": sig,
                 "Content-Type": "application/json"},
    )
    assert r1.status_code == 200
    assert r2.status_code == 200
    assert r1.json()["items_new"] == 1
    # Second call dedups on url_hash — items_seen=1, items_new=0.
    assert r2.json()["items_seen"] == 1
    assert r2.json()["items_new"] == 0


def test_webhook_400_on_malformed_json(db, monkeypatch):
    client = _build_client(db, monkeypatch)
    body = b"not json {{{"
    r = client.post(
        "/api/news-events/webhook/miniflux",
        content=body,
        headers={"X-Miniflux-Signature": _sign(body),
                 "Content-Type": "application/json"},
    )
    assert r.status_code == 400


def test_webhook_200_on_other_event_type(db, monkeypatch):
    client = _build_client(db, monkeypatch)
    payload = {"event_type": "saved_entry", "feed": {"id": 5}}
    body = json.dumps(payload).encode("utf-8")
    r = client.post(
        "/api/news-events/webhook/miniflux",
        content=body,
        headers={"X-Miniflux-Signature": _sign(body),
                 "Content-Type": "application/json"},
    )
    assert r.status_code == 200
    assert r.json()["items_seen"] == 0
