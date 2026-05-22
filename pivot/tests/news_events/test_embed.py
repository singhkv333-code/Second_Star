"""Tests for backend.news_events.pipeline.embed.

We unit-test ``cosine_similarity`` directly, then exercise the
``ensure_*_embedding`` helpers with ``embed_text`` monkey-patched to
return a deterministic vector — that way the test doesn't depend on
an OPENAI_API_KEY at runtime.
"""
from __future__ import annotations

import asyncio
import math

import pytest

from backend.news_events.models import NewsArticle, NewsEventSpec
from backend.news_events.pipeline import embed


def test_cosine_orthogonal_is_zero():
    assert embed.cosine_similarity([1, 0], [0, 1]) == 0.0


def test_cosine_identical_is_one():
    assert math.isclose(embed.cosine_similarity([1, 2, 3], [1, 2, 3]), 1.0)


def test_cosine_opposite_is_minus_one():
    assert math.isclose(
        embed.cosine_similarity([1, 2], [-1, -2]), -1.0, abs_tol=1e-9
    )


def test_cosine_handles_zero_vector():
    assert embed.cosine_similarity([0, 0], [1, 2]) == 0.0


def test_cosine_length_mismatch_is_zero():
    assert embed.cosine_similarity([1, 0], [1, 0, 0]) == 0.0


def test_cosine_handles_empty():
    assert embed.cosine_similarity([], [1, 2, 3]) == 0.0


def _seed_spec(db, description="RBI cuts repo rate"):
    spec = NewsEventSpec(
        user_id=1,
        tier="tier1",
        description=description,
        resolution_criteria={"primary_sources": []},
        retraction_policy={"safety_window_minutes": 0, "action": "ignore"},
        keyword_set={"must_have_one": ["RBI"], "must_have_one_of": [], "must_not_have": []},
        state="active",
    )
    db.add(spec)
    db.flush()
    return spec


def _seed_article(db):
    article = NewsArticle(
        source_id="src_test",
        url="https://example.test/a/1",
        url_hash="hash1",
        title="RBI announces 25bps rate cut",
        title_hash="thash1",
        summary="...summary...",
        body_text="The MPC cut the repo rate to 5.75% with immediate effect.",
    )
    db.add(article)
    db.flush()
    return article


def test_ensure_spec_embedding_caches(monkeypatch, db):
    calls = {"n": 0}

    async def fake_embed(text: str):
        calls["n"] += 1
        return [0.1, 0.2, 0.3]

    monkeypatch.setattr(embed, "embed_text", fake_embed)
    spec = _seed_spec(db)
    db.commit()

    first = asyncio.run(embed.ensure_spec_embedding(db=db, spec=spec))
    second = asyncio.run(embed.ensure_spec_embedding(db=db, spec=spec))
    assert first == [0.1, 0.2, 0.3]
    assert second == [0.1, 0.2, 0.3]
    # Second call is served from the column — embed_text invoked once.
    assert calls["n"] == 1


def test_ensure_article_embedding_persists(monkeypatch, db):
    async def fake_embed(text: str):
        # Make the vector depend on the text length so the test is
        # robust to ordering changes.
        return [float(len(text)), 1.0, 2.0]

    monkeypatch.setattr(embed, "embed_text", fake_embed)
    article = _seed_article(db)
    db.commit()

    vec = asyncio.run(embed.ensure_article_embedding(db=db, article=article))
    assert vec[1:] == [1.0, 2.0]
    assert article.text_embedding == vec


def test_ensure_article_embedding_swallows_client_error(monkeypatch, db):
    async def fake_embed(text: str):
        raise embed.EmbeddingClientError("offline")

    monkeypatch.setattr(embed, "embed_text", fake_embed)
    article = _seed_article(db)
    db.commit()

    vec = asyncio.run(embed.ensure_article_embedding(db=db, article=article))
    assert vec is None
    # Column remains NULL — next tick re-tries.
    db.refresh(article)
    assert article.text_embedding is None
