"""Integration test for backend.news_events.pipeline.ingest.

Uses a stub SourceAdapter that returns a hard-coded list of items —
exercises the persistence, dedup-on-url_hash, and source_health update
path without any network. Hits the shared in-memory test DB via the
``db`` fixture from the top-level conftest.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest

from backend.news_events.models import NewsArticle, NewsSourceHealth
from backend.news_events.pipeline.ingest import ingest_one_source
from backend.news_events.sources.base import (
    FetchedItem,
    SourceAdapter,
    SourceFetchError,
)


class _StubAdapter(SourceAdapter):
    """Returns a fixed list of items; second call returns the same set
    plus one new item — tests the dedup branch."""

    def __init__(self, source_id: str, batches: list[list[FetchedItem]]):
        self._source_id = source_id
        self._batches = list(batches)
        self._call = 0

    @property
    def source_id(self) -> str:
        return self._source_id

    async def fetch(self) -> list[FetchedItem]:
        idx = min(self._call, len(self._batches) - 1)
        self._call += 1
        return list(self._batches[idx])


class _FailingAdapter(SourceAdapter):
    @property
    def source_id(self) -> str:
        return "broken"

    async def fetch(self) -> list[FetchedItem]:
        raise SourceFetchError("boom")


def _make_item(source_id: str, idx: int) -> FetchedItem:
    return FetchedItem(
        source_id=source_id,
        url=f"https://example.test/articles/{idx}",
        title=f"Test article {idx}",
        summary=f"Summary for article {idx}",
        published_at=datetime.now(timezone.utc),
        raw_metadata={},
    )


@pytest.fixture
def session_factory(db):
    """Wrap the per-test ``db`` fixture as a no-arg session factory so
    the ingest code can call ``factory()`` and ``.close()`` without
    actually closing the shared in-memory connection."""

    class _Wrapper:
        def __init__(self, session):
            self._session = session

        def close(self) -> None:
            # Tests roll back via the outer fixture; we don't want a
            # premature .close() to detach the session.
            pass

        def __getattr__(self, name):
            return getattr(self._session, name)

    def factory():
        return _Wrapper(db)

    return factory


def test_ingest_persists_new_articles(db, session_factory):
    batch = [_make_item("src_a", 1), _make_item("src_a", 2)]
    adapter = _StubAdapter("src_a", [batch])
    result = asyncio.run(
        ingest_one_source(adapter, db_session_factory=session_factory)
    )

    assert result.ok
    assert result.items_seen == 2
    assert result.items_new == 2

    rows = db.query(NewsArticle).filter(NewsArticle.source_id == "src_a").all()
    assert len(rows) == 2

    health = (
        db.query(NewsSourceHealth)
        .filter(NewsSourceHealth.source_id == "src_a")
        .first()
    )
    assert health is not None
    assert health.articles_seen_24h == 2
    assert health.articles_passed_24h == 2
    assert health.consecutive_failures == 0
    assert health.last_successful_fetch_at is not None


def test_ingest_dedups_on_repeat_url(db, session_factory):
    batch1 = [_make_item("src_b", 1)]
    batch2 = [_make_item("src_b", 1), _make_item("src_b", 2)]
    adapter = _StubAdapter("src_b", [batch1, batch2])

    r1 = asyncio.run(ingest_one_source(adapter, db_session_factory=session_factory))
    assert r1.items_new == 1

    r2 = asyncio.run(ingest_one_source(adapter, db_session_factory=session_factory))
    assert r2.items_seen == 2
    # The repeat URL is deduped; only the new item counts.
    assert r2.items_new == 1

    rows = db.query(NewsArticle).filter(NewsArticle.source_id == "src_b").all()
    assert len(rows) == 2


def test_ingest_records_failure_on_source_error(db, session_factory):
    adapter = _FailingAdapter()
    result = asyncio.run(
        ingest_one_source(adapter, db_session_factory=session_factory)
    )

    assert not result.ok
    assert "boom" in (result.error or "")
    health = (
        db.query(NewsSourceHealth)
        .filter(NewsSourceHealth.source_id == "broken")
        .first()
    )
    assert health is not None
    assert health.consecutive_failures == 1
    assert "boom" in (health.last_error_message or "")
    assert health.last_successful_fetch_at is None


def test_ingest_health_resets_failures_on_success(db, session_factory):
    # Pre-seed a health row with a stale failure to make sure success
    # clears it.
    health = NewsSourceHealth(
        source_id="src_c",
        consecutive_failures=3,
        last_error_message="prior outage",
    )
    db.add(health)
    db.commit()

    adapter = _StubAdapter("src_c", [[_make_item("src_c", 9)]])
    result = asyncio.run(
        ingest_one_source(adapter, db_session_factory=session_factory)
    )
    assert result.ok

    refreshed = (
        db.query(NewsSourceHealth)
        .filter(NewsSourceHealth.source_id == "src_c")
        .first()
    )
    assert refreshed.consecutive_failures == 0
    assert refreshed.last_successful_fetch_at is not None
