"""Tests for the Stage 1 cross-source dedup helper.

Inserts NewsArticle rows directly so we exercise ``find_near_dup``
without running the whole ingest pipeline. The integration test in
``test_stage1_stage2_integration.py`` covers the end-to-end behaviour.
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone

import pytest

from backend.news_events.models import NewsArticle
from backend.news_events.pipeline.dedup import find_near_dup


def _title_hash(title: str) -> str:
    norm = " ".join(title.lower().split())
    return hashlib.sha256(norm.encode("utf-8")).hexdigest()


def _make_article(
    db,
    *,
    source_id: str,
    url: str,
    title: str,
    fetched_at: datetime | None = None,
    near_dup_of: str | None = None,
) -> NewsArticle:
    article = NewsArticle(
        source_id=source_id,
        url=url,
        url_hash=hashlib.sha256(url.encode("utf-8")).hexdigest(),
        title=title,
        title_hash=_title_hash(title),
        summary=None,
        fetched_at=fetched_at or datetime.now(timezone.utc),
        near_dup_of=near_dup_of,
    )
    db.add(article)
    db.flush()
    return article


def test_find_near_dup_returns_earlier_id_for_matching_title(db):
    a = _make_article(
        db,
        source_id="src_a",
        url="https://a.test/article/1",
        title="RBI cuts repo rate",
    )
    db.commit()

    hit = find_near_dup(db, title_hash=_title_hash("RBI cuts repo rate"))
    assert hit == a.id


def test_find_near_dup_ignores_self_chain(db):
    # Persist canonical row, then a near-dup pointing at it. The
    # helper must skip the near-dup (its near_dup_of is set) and only
    # ever return the canonical row's id.
    a = _make_article(
        db,
        source_id="src_a",
        url="https://a.test/article/1",
        title="Some headline",
    )
    _make_article(
        db,
        source_id="src_b",
        url="https://b.test/article/1",
        title="Some headline",
        near_dup_of=a.id,
    )
    db.commit()

    hit = find_near_dup(db, title_hash=_title_hash("Some headline"))
    assert hit == a.id


def test_find_near_dup_returns_none_outside_window(db):
    old_fetched = datetime.now(timezone.utc) - timedelta(hours=72)
    _make_article(
        db,
        source_id="src_a",
        url="https://a.test/article/old",
        title="An old story",
        fetched_at=old_fetched,
    )
    db.commit()

    hit = find_near_dup(
        db,
        title_hash=_title_hash("An old story"),
        window_hours=24,
    )
    assert hit is None


def test_find_near_dup_returns_none_for_empty_hash(db):
    assert find_near_dup(db, title_hash="") is None
