"""Tests for backend.news_events.pipeline.aggregate.

Seeds NewsArticle + NewsArticleClassification rows directly, then
checks the per-tier firing rules return Fire / Hold as expected.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from backend.news_events.models import (
    NewsArticle,
    NewsArticleClassification,
    NewsEventSpec,
)
from backend.news_events.pipeline.aggregate import evaluate_firing


def _seed_spec(
    db,
    *,
    tier: str = "tier1",
    primary_sources: list[str] | None = None,
    min_secondary: int = 0,
    min_confidence: float = 0.85,
    conflict_policy: str = "hold",
    state: str = "active",
) -> NewsEventSpec:
    spec = NewsEventSpec(
        user_id=1,
        tier=tier,
        description="RBI cuts repo rate" if tier != "tier3" else "Trump wins 2028",
        resolution_criteria={
            "primary_sources": primary_sources or [],
            "min_secondary_confirmations": min_secondary,
            "min_confidence": min_confidence,
            "conflict_policy": conflict_policy,
        },
        retraction_policy={
            "safety_window_minutes": 60,
            "action": "cancel_and_alert",
        },
        keyword_set={"must_have_one": ["x"], "must_have_one_of": [], "must_not_have": []},
        state=state,
    )
    db.add(spec)
    db.flush()
    return spec


_counter = 0


def _seed_classification(
    db,
    *,
    spec_id: str,
    source_id: str,
    verdict: str,
    confidence: float = 0.9,
) -> NewsArticleClassification:
    global _counter
    _counter += 1
    article = NewsArticle(
        source_id=source_id,
        url=f"https://example.test/{_counter}",
        url_hash=f"u_{_counter}",
        title=f"Article {_counter}",
        title_hash=f"t_{_counter}",
        summary=None,
    )
    db.add(article)
    db.flush()

    cls = NewsArticleClassification(
        article_id=article.id,
        event_spec_id=spec_id,
        stage_2_passed=True,
        classifier_verdict=verdict,
        confidence=confidence,
    )
    db.add(cls)
    db.flush()
    return cls


# ── Tier 1 ───────────────────────────────────────────────────────────


def test_tier1_fires_on_primary_yes(db):
    spec = _seed_spec(
        db, tier="tier1", primary_sources=["rbi_press_releases"]
    )
    _seed_classification(
        db, spec_id=spec.id, source_id="rbi_press_releases", verdict="YES"
    )
    db.commit()

    out = evaluate_firing(db, spec=spec)
    assert out.status == "fire"
    assert len(out.supporting_classification_ids) == 1
    assert out.aggregated_confidence >= 0.85


def test_tier1_holds_when_only_secondary_yes(db):
    spec = _seed_spec(
        db, tier="tier1", primary_sources=["rbi_press_releases"]
    )
    _seed_classification(
        db, spec_id=spec.id, source_id="livemint", verdict="YES"
    )
    db.commit()
    out = evaluate_firing(db, spec=spec)
    assert out.status == "hold"


def test_tier1_holds_when_confidence_below_threshold(db):
    spec = _seed_spec(
        db, tier="tier1", primary_sources=["rbi_press_releases"],
        min_confidence=0.95,
    )
    _seed_classification(
        db, spec_id=spec.id, source_id="rbi_press_releases",
        verdict="YES", confidence=0.9,
    )
    db.commit()
    out = evaluate_firing(db, spec=spec)
    assert out.status == "hold"


# ── Tier 2 ───────────────────────────────────────────────────────────


def test_tier2_fires_on_primary_yes(db):
    spec = _seed_spec(
        db, tier="tier2", primary_sources=["business_standard"],
        min_secondary=1,
    )
    _seed_classification(
        db, spec_id=spec.id, source_id="business_standard", verdict="YES"
    )
    db.commit()
    out = evaluate_firing(db, spec=spec)
    assert out.status == "fire"


def test_tier2_fires_on_two_distinct_secondary_yes(db):
    spec = _seed_spec(
        db, tier="tier2", primary_sources=[], min_secondary=1
    )
    _seed_classification(db, spec_id=spec.id, source_id="livemint", verdict="YES")
    _seed_classification(db, spec_id=spec.id, source_id="bbc_world", verdict="YES")
    db.commit()
    out = evaluate_firing(db, spec=spec)
    assert out.status == "fire"


def test_tier2_holds_on_single_secondary(db):
    spec = _seed_spec(db, tier="tier2", primary_sources=[], min_secondary=1)
    _seed_classification(db, spec_id=spec.id, source_id="livemint", verdict="YES")
    db.commit()
    out = evaluate_firing(db, spec=spec)
    assert out.status == "hold"


def test_tier2_two_yes_from_same_source_does_not_fire(db):
    spec = _seed_spec(db, tier="tier2", primary_sources=[], min_secondary=1)
    _seed_classification(db, spec_id=spec.id, source_id="livemint", verdict="YES")
    _seed_classification(db, spec_id=spec.id, source_id="livemint", verdict="YES")
    db.commit()
    out = evaluate_firing(db, spec=spec)
    # Two YES from same source = 1 distinct source = not enough.
    assert out.status == "hold"


# ── Tier 3 ───────────────────────────────────────────────────────────


def test_tier3_fires_on_multi_source_consensus_no_primary(db):
    # primary_sources empty → need (min_secondary + 1) distinct YES.
    spec = _seed_spec(
        db, tier="tier3", primary_sources=[],
        min_secondary=1, conflict_policy="hold",
    )
    _seed_classification(db, spec_id=spec.id, source_id="reuters", verdict="YES")
    _seed_classification(db, spec_id=spec.id, source_id="ap_news", verdict="YES")
    db.commit()
    out = evaluate_firing(db, spec=spec)
    assert out.status == "fire"


def test_tier3_holds_on_conflicting_no(db):
    spec = _seed_spec(
        db, tier="tier3", primary_sources=[],
        min_secondary=1, conflict_policy="hold",
    )
    _seed_classification(db, spec_id=spec.id, source_id="reuters", verdict="YES")
    _seed_classification(db, spec_id=spec.id, source_id="ap_news", verdict="YES")
    _seed_classification(db, spec_id=spec.id, source_id="bbc_world", verdict="NO")
    db.commit()
    out = evaluate_firing(db, spec=spec)
    assert out.status == "hold"
    assert "conflict" in out.reason


def test_tier3_holds_on_retraction(db):
    spec = _seed_spec(
        db, tier="tier3", primary_sources=[],
        min_secondary=1, conflict_policy="hold",
    )
    _seed_classification(db, spec_id=spec.id, source_id="reuters", verdict="YES")
    _seed_classification(db, spec_id=spec.id, source_id="ap_news", verdict="RETRACTION")
    db.commit()
    out = evaluate_firing(db, spec=spec)
    assert out.status == "hold"


def test_tier3_with_primary_needs_primary_yes(db):
    spec = _seed_spec(
        db, tier="tier3", primary_sources=["official_ec"],
        min_secondary=1, conflict_policy="hold",
    )
    _seed_classification(db, spec_id=spec.id, source_id="reuters", verdict="YES")
    _seed_classification(db, spec_id=spec.id, source_id="ap_news", verdict="YES")
    db.commit()
    out = evaluate_firing(db, spec=spec)
    # No primary YES yet → hold even with two secondaries.
    assert out.status == "hold"
    assert "no primary" in out.reason.lower()


def test_tier3_with_primary_fires_when_complete(db):
    spec = _seed_spec(
        db, tier="tier3", primary_sources=["official_ec"],
        min_secondary=1, conflict_policy="hold",
    )
    _seed_classification(db, spec_id=spec.id, source_id="official_ec", verdict="YES")
    _seed_classification(db, spec_id=spec.id, source_id="reuters", verdict="YES")
    db.commit()
    out = evaluate_firing(db, spec=spec)
    assert out.status == "fire"


# ── Misc ─────────────────────────────────────────────────────────────


def test_holds_when_no_classifications(db):
    spec = _seed_spec(db, tier="tier1")
    db.commit()
    out = evaluate_firing(db, spec=spec)
    assert out.status == "hold"
    assert "no recent classifications" in out.reason
