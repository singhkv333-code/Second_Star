"""Stage 7 — confidence aggregator.

Given a spec and the recent classifications it has accumulated,
decide whether the spec's firing rule is satisfied. Output is a
``FiringDecision`` — either ``Fire`` (with the supporting
classification ids for the audit row) or ``Hold`` (with a one-line
reason for the audit log).

Per-tier rules (from docs/news_events_phase0_plan.md):

  Tier 1 — one primary-source YES (confidence ≥ min_confidence) fires.
  Tier 2 — one primary YES OR ≥(min_secondary_confirmations+1)
           secondary YES inside the lookback window fires.
  Tier 3 — primary YES + ≥ min_secondary_confirmations secondary YES,
           BUT if any NO / RETRACTION classification has landed inside
           the same window AND ``conflict_policy`` is "hold", we hold.
           When ``primary_sources`` is empty (the parser's default for
           Tier 3, since there's no authoritative wire service), we
           require ≥(min_secondary_confirmations + 1) distinct-source
           YES classifications.

Retraction detection runs separately in Phase 6 (the safety-window
watcher). For Phase 5, a RETRACTION verdict on its own counts as a
"conflicting signal" for Tier 3's hold logic, but doesn't actively
retract a prior fire.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Literal, Optional

from sqlalchemy.orm import Session

from backend.news_events.models import (
    NewsArticle,
    NewsArticleClassification,
    NewsEventSpec,
)

logger = logging.getLogger(__name__)


# Lookback window in minutes — how far back classifications still
# count toward the firing rule. Tunable per spec in Phase 6; for now
# a single global default that covers the common cases.
DEFAULT_LOOKBACK_MINUTES: int = 120


FiringStatus = Literal["fire", "hold"]


@dataclass(frozen=True)
class _ClassRow:
    """Internal shape passed around the aggregator. Pulled from the
    join in ``_load_recent_classifications``."""

    classification_id: str
    article_id: str
    source_id: str
    verdict: str
    confidence: Optional[float]
    is_retraction: bool


@dataclass
class FiringDecision:
    """Aggregator output.

    ``fire`` is the headline boolean. ``supporting_classification_ids``
    is the audit-trail list (only populated when ``fire=True``).
    ``aggregated_confidence`` is the max confidence across the
    supporting YES classifications.
    """

    spec_id: str
    status: FiringStatus
    reason: str
    supporting_classification_ids: list[str] = field(default_factory=list)
    aggregated_confidence: float = 0.0


def _load_recent_classifications(
    db: Session,
    *,
    spec_id: str,
    cutoff: datetime,
) -> list[_ClassRow]:
    rows = (
        db.query(
            NewsArticleClassification.id,
            NewsArticleClassification.article_id,
            NewsArticleClassification.classifier_verdict,
            NewsArticleClassification.confidence,
            NewsArticle.source_id,
        )
        .join(NewsArticle, NewsArticle.id == NewsArticleClassification.article_id)
        .filter(
            NewsArticleClassification.event_spec_id == spec_id,
            NewsArticleClassification.classifier_verdict.is_not(None),
            NewsArticle.fetched_at >= cutoff,
            # Stage-1 dedup — only canonical articles count.
            NewsArticle.near_dup_of.is_(None),
        )
        .all()
    )
    return [
        _ClassRow(
            classification_id=row[0],
            article_id=row[1],
            source_id=row[4],
            verdict=row[2],
            confidence=row[3],
            is_retraction=row[2] == "RETRACTION",
        )
        for row in rows
    ]


def _filter_yes(rows: list[_ClassRow], *, min_confidence: float) -> list[_ClassRow]:
    """Keep only YES rows whose confidence clears the bar."""
    return [
        r for r in rows
        if r.verdict == "YES" and (r.confidence or 0.0) >= min_confidence
    ]


def _partition_by_source(
    rows: list[_ClassRow], primary_sources: set[str]
) -> tuple[list[_ClassRow], list[_ClassRow]]:
    """(primary_yes, secondary_yes)."""
    primary = [r for r in rows if r.source_id in primary_sources]
    secondary = [r for r in rows if r.source_id not in primary_sources]
    return primary, secondary


def _distinct_source_count(rows: list[_ClassRow]) -> int:
    return len({r.source_id for r in rows})


def _max_confidence(rows: list[_ClassRow]) -> float:
    return max((r.confidence or 0.0) for r in rows) if rows else 0.0


def _real_ids(rows: list[_ClassRow]) -> list[str]:
    """Drop synthetic classification ids (the ``__pm__`` Phase-6
    prediction-market marker, anything else prefixed with ``__``)
    so the audit row only carries real classification UUIDs."""
    return [
        r.classification_id
        for r in rows
        if not (r.classification_id or "").startswith("__")
    ]


def evaluate_firing(
    db: Session,
    *,
    spec: NewsEventSpec,
    now: Optional[datetime] = None,
    lookback_minutes: int = DEFAULT_LOOKBACK_MINUTES,
    prediction_market_signal: Optional[bool] = None,
) -> FiringDecision:
    """Apply the per-tier firing rule for one spec.

    Idempotency: callers MUST also check ``spec.state``. A spec
    already in state 'fired' should not be re-evaluated by the
    caller; the aggregator itself is stateless and will happily
    return a second Fire decision on the same data.

    ``prediction_market_signal`` (Phase 6, Tier-3 only): when the
    spec carries a ``prediction_market_threshold``, the caller
    should pre-compute this via
    ``pipeline.prediction_market.evaluate_prediction_market_signal``
    and pass it here. ``True`` means the market YES price ≥ threshold
    (counts as one synthetic secondary YES from a virtual
    ``prediction_market`` source). ``False`` means the market is
    below threshold (counts as a conflicting signal under
    ``conflict_policy='hold'``). ``None`` (default) means no market
    is configured — same behaviour as Phase 5.
    """
    cutoff = (now or datetime.now(timezone.utc)) - timedelta(minutes=lookback_minutes)

    rc = dict(spec.resolution_criteria or {})
    primary_sources = set(rc.get("primary_sources", []) or [])
    min_confidence = float(rc.get("min_confidence", 0.85) or 0.85)
    min_secondary = int(rc.get("min_secondary_confirmations", 0) or 0)
    conflict_policy = rc.get("conflict_policy", "hold")

    rows = _load_recent_classifications(db, spec_id=spec.id, cutoff=cutoff)

    # Phase 6 — splice the prediction-market signal in as a synthetic
    # classification row, then re-use the existing partition logic.
    # The virtual source_id 'prediction_market' is never in
    # primary_sources (the parser never includes it there), so it
    # always counts as a secondary YES.
    if prediction_market_signal is True:
        rows.append(
            _ClassRow(
                classification_id="__pm__",
                article_id="__pm__",
                source_id="prediction_market",
                verdict="YES",
                confidence=1.0,
                is_retraction=False,
            )
        )
    elif prediction_market_signal is False:
        rows.append(
            _ClassRow(
                classification_id="__pm__",
                article_id="__pm__",
                source_id="prediction_market",
                verdict="NO",
                confidence=1.0,
                is_retraction=False,
            )
        )

    if not rows:
        return FiringDecision(
            spec_id=spec.id,
            status="hold",
            reason="no recent classifications",
        )

    yes_rows = _filter_yes(rows, min_confidence=min_confidence)
    primary_yes, secondary_yes = _partition_by_source(yes_rows, primary_sources)

    # Tier-3 conflict check — applied across all verdicts, not just YES.
    no_or_retract = [
        r for r in rows
        if r.verdict in {"NO", "RETRACTION", "AMBIGUOUS"} or r.is_retraction
    ]

    tier = spec.tier

    if tier == "tier1":
        if primary_yes:
            return FiringDecision(
                spec_id=spec.id,
                status="fire",
                reason="tier1 primary YES landed",
                supporting_classification_ids=_real_ids(primary_yes[:1]),
                aggregated_confidence=_max_confidence(primary_yes[:1]),
            )
        return FiringDecision(
            spec_id=spec.id,
            status="hold",
            reason="tier1: no primary YES yet",
        )

    if tier == "tier2":
        # Path A — one primary YES.
        if primary_yes:
            return FiringDecision(
                spec_id=spec.id,
                status="fire",
                reason="tier2 primary YES landed",
                supporting_classification_ids=_real_ids(primary_yes[:1]),
                aggregated_confidence=_max_confidence(primary_yes[:1]),
            )
        # Path B — ≥(min_secondary + 1) secondary YES from distinct sources.
        required = max(2, min_secondary + 1)
        if _distinct_source_count(secondary_yes) >= required:
            return FiringDecision(
                spec_id=spec.id,
                status="fire",
                reason=(
                    f"tier2 secondary consensus: "
                    f"{_distinct_source_count(secondary_yes)} sources YES"
                ),
                supporting_classification_ids=_real_ids(secondary_yes[:required]),
                aggregated_confidence=_max_confidence(secondary_yes[:required]),
            )
        return FiringDecision(
            spec_id=spec.id,
            status="hold",
            reason=(
                f"tier2: need primary YES or {required} secondary YES; "
                f"have primary={len(primary_yes)} secondary_sources="
                f"{_distinct_source_count(secondary_yes)}"
            ),
        )

    if tier == "tier3":
        # Conflict policy gate (per Phase-0 plan): when conflicting
        # signals exist and policy is "hold", we hold and alert.
        if no_or_retract and conflict_policy == "hold":
            return FiringDecision(
                spec_id=spec.id,
                status="hold",
                reason=(
                    f"tier3 conflict: {len(no_or_retract)} non-YES "
                    f"classifications present; policy=hold"
                ),
            )

        if primary_sources:
            # Need 1 primary YES + min_secondary secondary YES.
            if not primary_yes:
                return FiringDecision(
                    spec_id=spec.id,
                    status="hold",
                    reason="tier3: no primary YES yet",
                )
            if _distinct_source_count(secondary_yes) < min_secondary:
                return FiringDecision(
                    spec_id=spec.id,
                    status="hold",
                    reason=(
                        f"tier3: have primary YES but need "
                        f"{min_secondary} secondary; "
                        f"have {_distinct_source_count(secondary_yes)}"
                    ),
                )
            supporting = primary_yes[:1] + secondary_yes[:min_secondary]
            return FiringDecision(
                spec_id=spec.id,
                status="fire",
                reason="tier3: primary + secondary consensus",
                supporting_classification_ids=_real_ids(supporting),
                aggregated_confidence=_max_confidence(supporting),
            )

        # No primary source defined — multi-source secondary consensus.
        required = max(2, min_secondary + 1)
        if _distinct_source_count(secondary_yes) >= required:
            return FiringDecision(
                spec_id=spec.id,
                status="fire",
                reason=(
                    f"tier3 multi-source consensus: "
                    f"{_distinct_source_count(secondary_yes)} sources YES"
                ),
                supporting_classification_ids=_real_ids(secondary_yes[:required]),
                aggregated_confidence=_max_confidence(secondary_yes[:required]),
            )
        return FiringDecision(
            spec_id=spec.id,
            status="hold",
            reason=(
                f"tier3 multi-source: need {required} distinct YES "
                f"sources; have {_distinct_source_count(secondary_yes)}"
            ),
        )

    # Unknown tier — shouldn't happen given the model CHECK constraint.
    return FiringDecision(
        spec_id=spec.id,
        status="hold",
        reason=f"unknown tier {tier!r}",
    )
