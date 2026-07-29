"""Phase-5 end-to-end: classification arrives → aggregator fires → audit
row written.

Externals (body fetch / embedding / excerpt / classify / Touch-1 seam)
are stubbed. We seed a spec + a stage_2_passed classification with the
verdict deliberately left NULL so the funnel completes it via the
mocked classifier and the aggregator fires on the resulting YES.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from backend.news_events.models import (
    NewsArticle,
    NewsArticleClassification,
    NewsEventSpec,
    NewsFiredEvent,
)
from backend.news_events.pipeline import classify as classify_mod
from backend.news_events.pipeline import embed as embed_mod
from backend.news_events.pipeline import excerpt as excerpt_mod
from backend.news_events.pipeline import fetch_body as fb_mod
from backend.news_events.pipeline import funnel as funnel_mod
from backend.news_events.pipeline.classify import ClassificationResult
from backend.news_events.pipeline.fetch_body import BodyFetchResult


def _seed_spec(db, *, workflow_id=None):
    spec = NewsEventSpec(
        user_id=1,
        tier="tier1",
        description="RBI cuts repo rate",
        resolution_criteria={
            "primary_sources": ["rbi_press_releases"],
            "min_secondary_confirmations": 0,
            "min_confidence": 0.85,
            "conflict_policy": "fire",
        },
        retraction_policy={
            "safety_window_minutes": 60, "action": "cancel_and_alert",
        },
        keyword_set={"must_have_one": ["RBI"], "must_have_one_of": [], "must_not_have": []},
        state="active",
        workflow_id=workflow_id,
    )
    db.add(spec)
    db.flush()
    return spec


def _seed_article(db, *, source_id="rbi_press_releases"):
    a = NewsArticle(
        source_id=source_id,
        url=f"https://example.test/rbi/1",
        url_hash="u_rbi_1",
        title="RBI cuts repo rate by 25 bps",
        title_hash="t_rbi_1",
        summary="MPC reduces repo rate to 5.75%",
    )
    db.add(a)
    db.flush()
    return a


def test_funnel_aggregates_and_fires_tier1(db, monkeypatch):
    spec = _seed_spec(db)
    article = _seed_article(db)
    cls = NewsArticleClassification(
        article_id=article.id,
        event_spec_id=spec.id,
        stage_2_passed=True,
    )
    db.add(cls)
    db.commit()

    # Stub Stages 3-6 to land a high-confidence YES.
    async def fake_fetch(url):
        return BodyFetchResult(
            status="ok",
            body_text="The MPC cut the repo rate to 5.75%.",
            http_status=200,
            fetched_at=datetime.now(timezone.utc),
        )

    async def fake_embed(text):
        return [1.0, 0.5, 0.25]

    async def fake_excerpt(*, event_description, article_title, article_body, max_body_chars=12000):
        return "The MPC cut the repo rate to 5.75%."

    async def fake_classify(*, event_description, excerpt, article_title):
        return ClassificationResult(
            verdict="YES",
            confidence=0.93,
            is_retraction=False,
            reason="confirmed",
            model="fake",
        )

    async def fake_fire_external_event(**kwargs):
        return "wf-run-xyz"

    monkeypatch.setattr(fb_mod, "fetch_article_body", fake_fetch)
    monkeypatch.setattr(embed_mod, "embed_text", fake_embed)
    monkeypatch.setattr(excerpt_mod, "extract_excerpt", fake_excerpt)
    monkeypatch.setattr(classify_mod, "classify_excerpt", fake_classify)
    monkeypatch.setattr(funnel_mod, "fetch_article_body", fake_fetch)
    monkeypatch.setattr(funnel_mod, "extract_excerpt", fake_excerpt)
    monkeypatch.setattr(funnel_mod, "classify_excerpt", fake_classify)
    monkeypatch.setattr(
        "backend.workflows.scheduler.fire_external_event",
        fake_fire_external_event,
    )

    result = asyncio.run(funnel_mod.process_pending(db=db, batch_size=5))
    assert result.candidates_seen == 1
    assert result.stage6_completed == 1
    assert result.verdicts.get("YES") == 1
    assert result.specs_evaluated == 1
    assert result.specs_fired == 1
    assert len(result.fired_event_ids) == 1

    db.refresh(spec)
    assert spec.state == "fired"

    fired = db.query(NewsFiredEvent).all()
    assert len(fired) == 1
    assert fired[0].event_spec_id == spec.id
    assert fired[0].tier == "tier1"
    assert fired[0].aggregated_confidence >= 0.85


def test_funnel_no_fire_when_below_confidence(db, monkeypatch):
    spec = _seed_spec(db)
    # Bump min_confidence so the verdict 0.7 doesn't make the bar.
    spec.resolution_criteria = {
        **spec.resolution_criteria,
        "min_confidence": 0.95,
    }
    db.flush()
    article = _seed_article(db)
    cls = NewsArticleClassification(
        article_id=article.id,
        event_spec_id=spec.id,
        stage_2_passed=True,
    )
    db.add(cls)
    db.commit()

    async def fake_fetch(url):
        return BodyFetchResult(status="ok", body_text="x", http_status=200,
                               fetched_at=datetime.now(timezone.utc))

    async def fake_embed(text): return [1.0, 0.5]
    async def fake_excerpt(**kw): return "x"
    async def fake_classify(**kw):
        return ClassificationResult(
            verdict="YES", confidence=0.70, is_retraction=False,
            reason="weak", model="fake",
        )

    monkeypatch.setattr(fb_mod, "fetch_article_body", fake_fetch)
    monkeypatch.setattr(embed_mod, "embed_text", fake_embed)
    monkeypatch.setattr(excerpt_mod, "extract_excerpt", fake_excerpt)
    monkeypatch.setattr(classify_mod, "classify_excerpt", fake_classify)
    monkeypatch.setattr(funnel_mod, "fetch_article_body", fake_fetch)
    monkeypatch.setattr(funnel_mod, "extract_excerpt", fake_excerpt)
    monkeypatch.setattr(funnel_mod, "classify_excerpt", fake_classify)

    result = asyncio.run(funnel_mod.process_pending(db=db, batch_size=5))
    assert result.specs_evaluated == 1
    assert result.specs_fired == 0
    db.refresh(spec)
    assert spec.state == "active"
    assert db.query(NewsFiredEvent).count() == 0
