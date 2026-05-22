"""Stages 3-6 orchestrator.

Selects pending ``news_article_classifications`` rows
(``stage_2_passed=TRUE AND classifier_verdict IS NULL``), then for
each (article, spec) pair:

  Stage 3 — fetch the body if we haven't yet
  Stage 4 — embed article + spec, drop pair if cosine < threshold
  Stage 5 — LLM excerpt extraction
  Stage 6 — LLM classification with retraction flag

Writes the verdict back to the same classification row. Bounded per
tick by ``DEFAULT_BATCH_SIZE`` to keep wall-clock and LLM spend
predictable.

This is NOT inside the ingest tick — Stage 3-6 are
network/LLM-bound and would block the per-source poll. Wired up as a
separate APScheduler job in ``backend/news_events/workers/funnel.py``.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy.orm import Session

from backend.database import SessionLocal
from backend.news_events.models import (
    NewsArticle,
    NewsArticleClassification,
    NewsEventSpec,
)
from backend.news_events.pipeline.aggregate import evaluate_firing
from backend.news_events.pipeline.classify import classify_excerpt
from backend.news_events.pipeline.embed import (
    SIM_THRESHOLD,
    cosine_similarity,
    ensure_article_embedding,
    ensure_spec_embedding,
)
from backend.news_events.pipeline.excerpt import extract_excerpt
from backend.news_events.pipeline.fetch_body import fetch_article_body
from backend.news_events.pipeline.prediction_market import (
    evaluate_prediction_market_signal,
)
from backend.news_events.pipeline.propose import fire_spec

logger = logging.getLogger(__name__)


# Conservative batch — the wall-clock budget is dominated by Stages
# 3, 5, 6. At ~3-5s per (article, spec) end-to-end, 5 keeps a tick
# under 30s comfortably.
DEFAULT_BATCH_SIZE: int = 5


@dataclass
class FunnelTickResult:
    """Per-tick summary returned by ``process_pending``."""

    candidates_seen: int = 0
    stage3_attempted: int = 0
    stage3_failed: int = 0
    stage4_rejected: int = 0
    stage5_excerpts: int = 0
    stage6_completed: int = 0
    verdicts: dict[str, int] = field(default_factory=dict)
    # Phase 5 — aggregator + firing.
    specs_evaluated: int = 0
    specs_fired: int = 0
    fired_event_ids: list[str] = field(default_factory=list)


async def _ensure_body(db: Session, article: NewsArticle) -> bool:
    """Stage 3 — fetch the article body if not already attempted.

    Returns True iff we now have a usable ``body_text``. False on
    failure (the funnel still progresses on title+summary alone).
    """
    if article.body_text:
        return True
    if article.body_fetch_status == "ok":
        return bool(article.body_text)
    if article.body_fetch_status in {"robots_disallowed", "http_error", "extract_failed"}:
        # Already tried and failed. Don't retry inside one process —
        # the row stays in this state until manual intervention or
        # a future re-poll.
        return False
    result = await fetch_article_body(article.url)
    article.body_fetched_at = result.fetched_at
    article.body_fetch_status = result.status
    if result.status == "ok" and result.body_text:
        article.body_text = result.body_text
        db.flush()
        return True
    db.flush()
    return False


async def _process_one(
    db: Session,
    *,
    classification: NewsArticleClassification,
    article: NewsArticle,
    spec: NewsEventSpec,
    result: FunnelTickResult,
) -> None:
    # Stage 3 — body fetch
    result.stage3_attempted += 1
    body_ok = await _ensure_body(db, article)
    if not body_ok:
        result.stage3_failed += 1
        # We still try to classify on title+summary alone — the same
        # fallback Phase-2 lived with.

    # Stage 4 — embedding similarity
    spec_vec = await ensure_spec_embedding(db=db, spec=spec)
    art_vec = await ensure_article_embedding(db=db, article=article)
    if not spec_vec or not art_vec:
        # Embedding unavailable (no API key or transient outage).
        # Persist a marker and move on; next tick re-checks.
        classification.embedding_similarity = None
        return
    sim = cosine_similarity(spec_vec, art_vec)
    classification.embedding_similarity = sim
    if sim < SIM_THRESHOLD:
        result.stage4_rejected += 1
        # Below threshold → record UNRELATED so the row leaves the
        # pending queue and doesn't churn the worker.
        classification.classifier_verdict = "UNRELATED"
        classification.confidence = 0.0
        classification.excerpt = ""
        classification.model = "stage4_threshold"
        db.flush()
        return

    # Stage 5 — excerpt extraction. Skipped if body is empty.
    excerpt = ""
    if article.body_text:
        excerpt = await extract_excerpt(
            event_description=spec.description,
            article_title=article.title,
            article_body=article.body_text,
        )
        if excerpt:
            result.stage5_excerpts += 1

    # Stage 6 — classifier
    verdict = await classify_excerpt(
        event_description=spec.description,
        excerpt=excerpt,
        article_title=article.title,
    )
    classification.classifier_verdict = verdict.verdict
    classification.confidence = verdict.confidence
    classification.excerpt = excerpt
    classification.model = verdict.model
    db.flush()
    result.stage6_completed += 1
    result.verdicts[verdict.verdict] = result.verdicts.get(verdict.verdict, 0) + 1

    # Stage 7 + 8 — aggregator decides; firing path persists audit
    # + (optionally) starts a workflow run. Only attempted when the
    # spec is still active (a prior YES from this same tick may
    # have already fired it).
    if spec.state == "active":
        # Phase 6 — for Tier-3 specs with a prediction-market
        # threshold, consult Polymarket FIRST so the aggregator
        # gets the boolean signal. Tier-1/2 specs and Tier-3 specs
        # without a threshold short-circuit to signal=None inside
        # the helper.
        pm_signal = await evaluate_prediction_market_signal(db, spec=spec)

        decision = evaluate_firing(
            db,
            spec=spec,
            prediction_market_signal=pm_signal.above_threshold,
        )
        result.specs_evaluated += 1
        if decision.status == "fire":
            # Snapshot for the audit row, if we have one.
            pm_snapshot = None
            if pm_signal.snapshot is not None:
                pm_snapshot = {
                    "market_id": pm_signal.snapshot.market_id,
                    "slug": pm_signal.snapshot.slug,
                    "question": pm_signal.snapshot.question,
                    "yes_price": pm_signal.snapshot.yes_price,
                    "threshold": pm_signal.threshold,
                    "closed": pm_signal.snapshot.closed,
                }
            outcome = await fire_spec(
                db,
                spec=spec,
                decision=decision,
                prediction_market_snapshot=pm_snapshot,
            )
            if outcome.fired_event_id and not outcome.duplicate:
                result.specs_fired += 1
                result.fired_event_ids.append(outcome.fired_event_id)


async def process_pending(
    *,
    batch_size: int = DEFAULT_BATCH_SIZE,
    db: Optional[Session] = None,
) -> FunnelTickResult:
    """Run Stages 3-6 against ``batch_size`` pending classifications.

    ``db`` may be passed in (FastAPI route path); otherwise we open
    and close our own session like the poller does.
    """
    owns_session = db is None
    session = db if db is not None else SessionLocal()
    summary = FunnelTickResult()
    try:
        # Fetch pending classifications joined to article + spec.
        pending = (
            session.query(NewsArticleClassification, NewsArticle, NewsEventSpec)
            .join(NewsArticle, NewsArticle.id == NewsArticleClassification.article_id)
            .join(NewsEventSpec, NewsEventSpec.id == NewsArticleClassification.event_spec_id)
            .filter(
                NewsArticleClassification.stage_2_passed.is_(True),
                NewsArticleClassification.classifier_verdict.is_(None),
                # Stage 1 dedup — only operate on canonical articles.
                NewsArticle.near_dup_of.is_(None),
                # Only active specs participate.
                NewsEventSpec.state == "active",
            )
            .order_by(NewsArticleClassification.created_at.asc())
            .limit(batch_size)
            .all()
        )
        summary.candidates_seen = len(pending)

        for classification, article, spec in pending:
            try:
                await _process_one(
                    session,
                    classification=classification,
                    article=article,
                    spec=spec,
                    result=summary,
                )
            except Exception as exc:  # noqa: BLE001 — one row never kills the batch
                logger.exception(
                    "[news_events.funnel] row_failed classification_id=%s err=%s",
                    classification.id,
                    exc,
                )
                # Mark as UNRELATED with 0 confidence so the row exits
                # the pending queue; next ingest re-evaluates.
                classification.classifier_verdict = "UNRELATED"
                classification.confidence = 0.0
                classification.excerpt = f"funnel_error: {exc}"[:500]
                session.flush()

        session.commit()
    finally:
        if owns_session:
            session.close()

    logger.info(
        "[news_events.funnel] tick "
        "candidates=%d stage3_failed=%d stage4_rejected=%d "
        "stage5_excerpts=%d stage6_completed=%d verdicts=%s "
        "specs_evaluated=%d specs_fired=%d",
        summary.candidates_seen,
        summary.stage3_failed,
        summary.stage4_rejected,
        summary.stage5_excerpts,
        summary.stage6_completed,
        summary.verdicts,
        summary.specs_evaluated,
        summary.specs_fired,
    )
    return summary
