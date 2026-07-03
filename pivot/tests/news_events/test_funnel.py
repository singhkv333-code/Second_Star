"""End-to-end test for the Stage 3-6 funnel orchestrator.

All four externals (body fetcher, embedding API, excerpt LLM,
classifier LLM) are stubbed so the test runs offline and
deterministically. Verifies:

  - candidates are picked up from news_article_classifications
  - body fetch result is persisted
  - embedding similarity gates Stage 5
  - classifier verdict + confidence land on the row
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
from backend.news_events.pipeline import classify as classify_mod
from backend.news_events.pipeline import embed as embed_mod
from backend.news_events.pipeline import excerpt as excerpt_mod
from backend.news_events.pipeline import fetch_body as fb_mod
from backend.news_events.pipeline import funnel as funnel_mod
from backend.news_events.pipeline.classify import ClassificationResult
from backend.news_events.pipeline.fetch_body import BodyFetchResult


def _seed_spec(db, description="RBI cuts repo rate", state="active"):
    spec = NewsEventSpec(
        user_id=1,
        tier="tier1",
        description=description,
        resolution_criteria={"primary_sources": []},
        retraction_policy={"safety_window_minutes": 0, "action": "ignore"},
        keyword_set={"must_have_one": ["RBI"], "must_have_one_of": [], "must_not_have": []},
        state=state,
    )
    db.add(spec)
    db.flush()
    return spec


def _seed_article(
    db,
    *,
    source_id="src_test",
    title="RBI announces 25bps rate cut",
    summary="MPC cuts repo rate",
    body_text=None,
):
    a = NewsArticle(
        source_id=source_id,
        url=f"https://example.test/{source_id}/{title}",
        url_hash=f"u_{source_id}_{title}",
        title=title,
        title_hash=f"t_{title}",
        summary=summary,
        body_text=body_text,
    )
    db.add(a)
    db.flush()
    return a


def _seed_classification(db, *, article_id, spec_id, stage_2_passed=True):
    c = NewsArticleClassification(
        article_id=article_id,
        event_spec_id=spec_id,
        stage_2_passed=stage_2_passed,
    )
    db.add(c)
    db.flush()
    return c


@pytest.fixture
def stub_externals(monkeypatch):
    """Patch all four external calls used by the funnel."""

    async def fake_fetch(url: str):
        return BodyFetchResult(
            status="ok",
            body_text="The MPC cut the repo rate to 5.75% with immediate effect.",
            http_status=200,
            fetched_at=datetime.now(timezone.utc),
        )

    async def fake_embed(text: str):
        # Two near-identical vectors so cosine ~= 1.0 — well above the
        # SIM_THRESHOLD=0.20 gate.
        return [1.0, 0.5, 0.25]

    async def fake_excerpt(*, event_description, article_title, article_body, max_body_chars=12000):
        return "The MPC cut the repo rate to 5.75%."

    async def fake_classify(*, event_description, excerpt, article_title):
        return ClassificationResult(
            verdict="YES",
            confidence=0.91,
            is_retraction=False,
            reason="Article confirms the cut.",
            model="fake-model",
        )

    monkeypatch.setattr(fb_mod, "fetch_article_body", fake_fetch)
    monkeypatch.setattr(embed_mod, "embed_text", fake_embed)
    monkeypatch.setattr(excerpt_mod, "extract_excerpt", fake_excerpt)
    monkeypatch.setattr(classify_mod, "classify_excerpt", fake_classify)
    # The funnel imports the patched targets by name from these
    # modules; rebind on the funnel module too so the patch lands.
    monkeypatch.setattr(funnel_mod, "fetch_article_body", fake_fetch)
    monkeypatch.setattr(funnel_mod, "extract_excerpt", fake_excerpt)
    monkeypatch.setattr(funnel_mod, "classify_excerpt", fake_classify)


def test_funnel_processes_pending_to_yes_verdict(db, stub_externals):
    spec = _seed_spec(db)
    article = _seed_article(db)
    cls = _seed_classification(db, article_id=article.id, spec_id=spec.id)
    db.commit()

    result = asyncio.run(funnel_mod.process_pending(db=db))
    assert result.candidates_seen == 1
    assert result.stage6_completed == 1
    assert result.verdicts.get("YES") == 1

    db.refresh(cls)
    assert cls.classifier_verdict == "YES"
    assert cls.confidence == pytest.approx(0.91)
    assert cls.embedding_similarity is not None
    assert cls.embedding_similarity >= 0.99  # identical vectors → ~1.0
    assert cls.excerpt and "5.75%" in cls.excerpt

    db.refresh(article)
    assert article.body_text and "5.75%" in article.body_text
    assert article.body_fetch_status == "ok"


def test_funnel_rejects_below_similarity_threshold(monkeypatch, db, stub_externals):
    # Spec embedding vs article embedding: orthogonal → cosine = 0 < threshold.
    state = {"calls": 0}

    async def alternating_embed(text: str):
        state["calls"] += 1
        if state["calls"] == 1:
            return [1.0, 0.0, 0.0]
        return [0.0, 1.0, 0.0]

    monkeypatch.setattr(embed_mod, "embed_text", alternating_embed)

    spec = _seed_spec(db)
    article = _seed_article(db)
    cls = _seed_classification(db, article_id=article.id, spec_id=spec.id)
    db.commit()

    result = asyncio.run(funnel_mod.process_pending(db=db))
    assert result.candidates_seen == 1
    assert result.stage4_rejected == 1
    assert result.stage6_completed == 0

    db.refresh(cls)
    assert cls.classifier_verdict == "UNRELATED"
    assert cls.confidence == 0.0
    assert cls.model == "stage4_threshold"


def test_funnel_skips_inactive_specs(db, stub_externals):
    spec = _seed_spec(db, state="draft")
    article = _seed_article(db)
    _seed_classification(db, article_id=article.id, spec_id=spec.id)
    db.commit()

    result = asyncio.run(funnel_mod.process_pending(db=db))
    assert result.candidates_seen == 0


def test_funnel_skips_already_classified(db, stub_externals):
    spec = _seed_spec(db)
    article = _seed_article(db)
    cls = _seed_classification(db, article_id=article.id, spec_id=spec.id)
    cls.classifier_verdict = "YES"
    db.commit()

    result = asyncio.run(funnel_mod.process_pending(db=db))
    assert result.candidates_seen == 0
