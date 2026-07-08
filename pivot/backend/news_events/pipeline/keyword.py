"""Stage 2 — keyword / regex filter.

Pure function ``evaluate_keyword_set`` decides whether an article
passes a spec's keyword constraints. No DB, no LLM, microseconds.
The ``apply_stage_2`` helper persists one ``news_article_classifications``
row per (article, active spec) pair so the funnel has an audit trail.

Match semantics — chosen for the planner LLM's convenience, not raw
expressiveness:

  - ``must_have_one``: at least one term from this list must appear.
    Empty list ⇒ vacuously satisfied.
  - ``must_have_one_of``: list-of-lists. For every inner list, at
    least one term must appear. Empty outer list ⇒ vacuously
    satisfied. Models "must mention RBI AND (rate OR policy)".
  - ``must_not_have``: any hit rejects.

All matches are case-insensitive substring matches over the
concatenation of (title + " " + summary). Phase 3 may broaden to
regex if the planner needs it; today substring keeps the LLM-emitted
keyword set free of regex syntax surprises.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Iterable, Optional

from sqlalchemy.orm import Session

from backend.news_events.models import (
    EVENT_SPEC_STATES,
    NewsArticleClassification,
    NewsEventSpec,
)
from backend.news_events.schemas import KeywordSet

logger = logging.getLogger(__name__)


# ── Pure evaluation ──────────────────────────────────────────────────


def _haystack(title: str, summary: Optional[str]) -> str:
    """Build the case-folded text Stage 2 matches against."""
    if not summary:
        return (title or "").lower()
    return f"{title or ''} {summary}".lower()


def _any_hit(haystack: str, needles: Iterable[str]) -> bool:
    """True if any non-empty needle is a substring of ``haystack``."""
    for n in needles:
        n_norm = (n or "").strip().lower()
        if n_norm and n_norm in haystack:
            return True
    return False


def evaluate_keyword_set(
    *,
    title: str,
    summary: Optional[str],
    keyword_set: KeywordSet,
) -> bool:
    """Return True iff the article passes the keyword set's three
    rules. See module docstring for semantics. An empty ``KeywordSet``
    passes everything — callers should reject empty sets at parse
    time (Phase 4) rather than here.
    """
    haystack = _haystack(title, summary)

    if keyword_set.must_have_one:
        if not _any_hit(haystack, keyword_set.must_have_one):
            return False

    for inner in keyword_set.must_have_one_of:
        if inner and not _any_hit(haystack, inner):
            return False

    if keyword_set.must_not_have:
        if _any_hit(haystack, keyword_set.must_not_have):
            return False

    return True


# ── DB-bound evaluator ───────────────────────────────────────────────


@dataclass
class Stage2Outcome:
    """Per-article summary of the Stage-2 pass."""

    article_id: str
    specs_evaluated: int = 0
    specs_passed: int = 0


def _load_active_specs(db: Session) -> list[NewsEventSpec]:
    """All EventSpec rows in the 'active' state. Phase 4 lands the
    user surface that flips a draft into 'active'. Until then this
    list is populated only by test seeds or direct DB inserts."""
    return (
        db.query(NewsEventSpec)
        .filter(NewsEventSpec.state == "active")
        .all()
    )


def _keyword_set_from_row(spec: NewsEventSpec) -> KeywordSet:
    """Coerce the persisted JSON back into a KeywordSet. Tolerant of
    legacy rows that may have a partial shape (just must_have_one,
    say) because the Phase-4 parser hasn't shipped yet."""
    raw = spec.keyword_set or {}
    try:
        return KeywordSet.model_validate(raw)
    except Exception:  # noqa: BLE001 - rescue and log
        logger.warning(
            "[news_events.keyword] spec=%s has invalid keyword_set; "
            "treating as empty (vacuously matches all)",
            spec.id,
        )
        return KeywordSet()


def apply_stage_2_for_article(
    db: Session,
    *,
    article_id: str,
    title: str,
    summary: Optional[str],
) -> Stage2Outcome:
    """Evaluate one article against every active spec and persist a
    classification row per (article, spec) pair.

    Callers must pass an already-committed ``article_id`` so the FK is
    satisfied. ``commit`` is the caller's responsibility — we flush
    so the rows are visible inside the same transaction.
    """
    specs = _load_active_specs(db)
    outcome = Stage2Outcome(article_id=article_id)

    for spec in specs:
        ks = _keyword_set_from_row(spec)
        passed = evaluate_keyword_set(title=title, summary=summary, keyword_set=ks)
        row = NewsArticleClassification(
            article_id=article_id,
            event_spec_id=spec.id,
            stage_2_passed=passed,
        )
        db.add(row)
        outcome.specs_evaluated += 1
        if passed:
            outcome.specs_passed += 1

    if specs:
        db.flush()
    return outcome


# Public surface for backwards-compat reading by future stages.
__all__ = [
    "EVENT_SPEC_STATES",
    "Stage2Outcome",
    "apply_stage_2_for_article",
    "evaluate_keyword_set",
]
