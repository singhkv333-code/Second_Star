"""End-to-end integration test for Stage 1 + Stage 2 inside
``ingest_one_source``.

We seed a NewsEventSpec row in the 'active' state (the Phase-4 user
surface for spec creation isn't shipped yet, but the table already
supports direct inserts), feed a stub adapter, and inspect the
persisted state.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest

from backend.news_events.models import (
    NewsArticle,
    NewsArticleClassification,
    NewsEventSpec,
)
from backend.news_events.pipeline.ingest import ingest_one_source
from backend.news_events.sources.base import FetchedItem, SourceAdapter


class _StubAdapter(SourceAdapter):
    def __init__(self, source_id: str, items: list[FetchedItem]):
        self._source_id = source_id
        self._items = list(items)

    @property
    def source_id(self) -> str:
        return self._source_id

    async def fetch(self) -> list[FetchedItem]:
        return list(self._items)


@pytest.fixture
def session_factory(db):
    class _Wrapper:
        def __init__(self, s):
            self._s = s

        def close(self):  # don't tear down the shared in-memory session
            pass

        def __getattr__(self, name):
            return getattr(self._s, name)

    return lambda: _Wrapper(db)


def _seed_spec(
    db,
    *,
    user_id: int = 1,
    must_have_one: list[str],
    must_not_have: list[str] | None = None,
    state: str = "active",
) -> NewsEventSpec:
    spec = NewsEventSpec(
        user_id=user_id,
        tier="tier1",
        description="RBI cuts repo rate",
        resolution_criteria={
            "primary_sources": ["rbi_press_releases"],
            "min_secondary_confirmations": 0,
            "min_confidence": 0.85,
            "conflict_policy": "hold",
        },
        retraction_policy={
            "safety_window_minutes": 120,
            "action": "cancel_and_alert",
        },
        keyword_set={
            "must_have_one": must_have_one,
            "must_have_one_of": [],
            "must_not_have": must_not_have or [],
        },
        state=state,
    )
    db.add(spec)
    db.flush()
    return spec


def _item(
    source_id: str,
    idx: int,
    title: str,
    summary: str | None = None,
) -> FetchedItem:
    return FetchedItem(
        source_id=source_id,
        url=f"https://example.test/{source_id}/{idx}",
        title=title,
        summary=summary,
        published_at=datetime.now(timezone.utc),
        raw_metadata={},
    )


def test_stage1_marks_cross_source_dup_and_stage2_skips_it(db, session_factory):
    spec = _seed_spec(db, must_have_one=["RBI", "repo"])
    db.commit()

    # First poll from source_a — should land cleanly, pass keyword.
    adapter_a = _StubAdapter(
        "source_a",
        [_item("source_a", 1, "RBI cuts repo rate by 25 bps")],
    )
    result_a = asyncio.run(
        ingest_one_source(adapter_a, db_session_factory=session_factory)
    )
    assert result_a.items_new == 1
    assert result_a.items_after_stage1 == 1
    assert result_a.items_after_stage2 == 1

    # Second poll from a DIFFERENT source, same title — Stage 1 marks
    # it as a near-dup and Stage 2 skips the keyword evaluation.
    adapter_b = _StubAdapter(
        "source_b",
        [_item("source_b", 1, "RBI cuts repo rate by 25 bps")],
    )
    result_b = asyncio.run(
        ingest_one_source(adapter_b, db_session_factory=session_factory)
    )
    assert result_b.items_new == 1
    assert result_b.items_after_stage1 == 0  # dropped by Stage 1
    assert result_b.items_after_stage2 == 0

    # DB state — both rows exist; lookup by state (the SQLite test DB
    # has 1s timestamp resolution, so ordering by fetched_at is
    # unreliable across same-second inserts).
    rows = db.query(NewsArticle).all()
    assert len(rows) == 2
    canonical = next(r for r in rows if r.near_dup_of is None)
    dup = next(r for r in rows if r.near_dup_of is not None)
    assert dup.near_dup_of == canonical.id

    # Only the canonical row gets a classification.
    classifications = (
        db.query(NewsArticleClassification)
        .filter(NewsArticleClassification.event_spec_id == spec.id)
        .all()
    )
    assert len(classifications) == 1
    assert classifications[0].article_id == canonical.id
    assert classifications[0].stage_2_passed is True


def test_stage2_rejects_when_keyword_misses(db, session_factory):
    _seed_spec(db, must_have_one=["RBI"])
    db.commit()

    adapter = _StubAdapter(
        "source_x",
        [
            _item("source_x", 1, "RBI keeps rate unchanged"),
            _item("source_x", 2, "Sensex rallies on global cues"),
        ],
    )
    result = asyncio.run(
        ingest_one_source(adapter, db_session_factory=session_factory)
    )
    assert result.items_new == 2
    assert result.items_after_stage1 == 2
    # Only the first article hits "RBI" in the keyword set.
    assert result.items_after_stage2 == 1

    passed = (
        db.query(NewsArticleClassification)
        .filter(NewsArticleClassification.stage_2_passed.is_(True))
        .all()
    )
    rejected = (
        db.query(NewsArticleClassification)
        .filter(NewsArticleClassification.stage_2_passed.is_(False))
        .all()
    )
    assert len(passed) == 1
    assert len(rejected) == 1


def test_inactive_spec_is_ignored(db, session_factory):
    _seed_spec(db, must_have_one=["RBI"], state="draft")
    db.commit()

    adapter = _StubAdapter(
        "source_y",
        [_item("source_y", 1, "RBI cuts repo rate by 25 bps")],
    )
    result = asyncio.run(
        ingest_one_source(adapter, db_session_factory=session_factory)
    )
    assert result.items_new == 1
    assert result.items_after_stage1 == 1
    # Draft specs are not evaluated — no classifications written.
    assert result.items_after_stage2 == 0
    classifications = db.query(NewsArticleClassification).all()
    assert classifications == []
