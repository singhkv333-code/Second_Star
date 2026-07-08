"""Phase-6 aggregator behaviour with the prediction_market_signal kwarg.

Re-uses the seeding helpers from test_aggregate.py shape but lives in
its own file so the Phase-6 additions are isolated and don't dilute
the Phase-5 regression set.
"""
from __future__ import annotations

import hashlib

from backend.news_events.models import (
    NewsArticle,
    NewsArticleClassification,
    NewsEventSpec,
)
from backend.news_events.pipeline.aggregate import evaluate_firing


_n = 0


def _seed_spec_tier3(
    db,
    *,
    primary_sources=None,
    min_secondary=1,
    min_confidence=0.85,
    conflict_policy="hold",
    prediction_market_threshold=None,
) -> NewsEventSpec:
    rc = {
        "primary_sources": primary_sources or [],
        "min_secondary_confirmations": min_secondary,
        "min_confidence": min_confidence,
        "conflict_policy": conflict_policy,
    }
    if prediction_market_threshold is not None:
        rc["prediction_market_threshold"] = prediction_market_threshold
    spec = NewsEventSpec(
        user_id=1,
        tier="tier3",
        description="Trump wins 2028",
        resolution_criteria=rc,
        retraction_policy={
            "safety_window_minutes": 240,
            "action": "cancel_and_alert",
        },
        keyword_set={"must_have_one": ["Trump"], "must_have_one_of": [], "must_not_have": []},
        state="active",
    )
    db.add(spec)
    db.flush()
    return spec


def _seed_classification(
    db, *, spec_id, source_id, verdict="YES", confidence=0.92
) -> NewsArticleClassification:
    global _n
    _n += 1
    art = NewsArticle(
        source_id=source_id,
        url=f"https://example.test/{_n}",
        url_hash=f"u_{_n}",
        title=f"Article {_n}",
        title_hash=hashlib.sha256(str(_n).encode()).hexdigest(),
        summary=None,
    )
    db.add(art)
    db.flush()
    cls = NewsArticleClassification(
        article_id=art.id,
        event_spec_id=spec_id,
        stage_2_passed=True,
        classifier_verdict=verdict,
        confidence=confidence,
    )
    db.add(cls)
    db.flush()
    return cls


# ── Tier 3 with prediction-market signal ─────────────────────────────


def test_tier3_pm_yes_counts_as_secondary_and_fires(db):
    spec = _seed_spec_tier3(
        db,
        primary_sources=[],
        min_secondary=1,
        prediction_market_threshold=0.85,
    )
    # One single-source secondary YES PLUS a True PM signal → 2 distinct
    # sources (the synthetic "prediction_market" source) → fires.
    _seed_classification(db, spec_id=spec.id, source_id="reuters", verdict="YES")
    db.commit()

    out = evaluate_firing(db, spec=spec, prediction_market_signal=True)
    assert out.status == "fire"
    # Synthetic PM marker MUST NOT leak into supporting ids.
    assert all(not cid.startswith("__") for cid in out.supporting_classification_ids)
    # The single real classification is in the list.
    assert len(out.supporting_classification_ids) >= 1


def test_tier3_pm_no_creates_conflict_and_holds(db):
    spec = _seed_spec_tier3(
        db,
        primary_sources=[],
        min_secondary=1,
        prediction_market_threshold=0.85,
        conflict_policy="hold",
    )
    _seed_classification(db, spec_id=spec.id, source_id="reuters", verdict="YES")
    _seed_classification(db, spec_id=spec.id, source_id="ap_news", verdict="YES")
    db.commit()

    # With conflict_policy=hold and a False PM signal (which the
    # aggregator splices in as a synthetic NO), we should hold.
    out = evaluate_firing(db, spec=spec, prediction_market_signal=False)
    assert out.status == "hold"
    assert "conflict" in out.reason


def test_tier3_pm_none_is_phase5_behaviour(db):
    # Spec has a threshold but the caller passed signal=None (e.g. the
    # market lookup failed). Behaviour should be identical to Phase 5
    # with no PM threshold configured.
    spec = _seed_spec_tier3(
        db,
        primary_sources=[],
        min_secondary=1,
        prediction_market_threshold=0.85,
    )
    _seed_classification(db, spec_id=spec.id, source_id="reuters", verdict="YES")
    db.commit()

    out = evaluate_firing(db, spec=spec, prediction_market_signal=None)
    # Only 1 distinct source — below the 2-distinct threshold.
    assert out.status == "hold"


def test_tier3_pm_yes_pushes_below_threshold_set_over_line(db):
    """A Tier-3 spec with min_secondary=2 needs 3 distinct sources by
    default. Two real secondary YES + a PM YES = 3 → fires."""
    spec = _seed_spec_tier3(
        db,
        primary_sources=[],
        min_secondary=2,
        prediction_market_threshold=0.85,
    )
    _seed_classification(db, spec_id=spec.id, source_id="reuters", verdict="YES")
    _seed_classification(db, spec_id=spec.id, source_id="ap_news", verdict="YES")
    db.commit()

    out_without_pm = evaluate_firing(db, spec=spec, prediction_market_signal=None)
    assert out_without_pm.status == "hold"

    out_with_pm = evaluate_firing(db, spec=spec, prediction_market_signal=True)
    assert out_with_pm.status == "fire"
    assert "3 sources YES" in out_with_pm.reason


def test_synthetic_pm_marker_never_appears_in_supporting_ids(db):
    spec = _seed_spec_tier3(
        db,
        primary_sources=[],
        min_secondary=1,
        prediction_market_threshold=0.85,
    )
    _seed_classification(db, spec_id=spec.id, source_id="reuters", verdict="YES")
    db.commit()

    out = evaluate_firing(db, spec=spec, prediction_market_signal=True)
    assert out.status == "fire"
    for cid in out.supporting_classification_ids:
        assert not cid.startswith("__")
