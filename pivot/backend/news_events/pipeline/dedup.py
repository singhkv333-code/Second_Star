"""Stage 1 — cross-source de-duplication.

Stage 0 (ingest) already drops same-URL repeats via the
``news_articles.url_hash`` UNIQUE constraint. Stage 1 catches the
harder case: the same story re-published by a different feed under a
different URL but with the same headline. Those rows still get
persisted (so the audit trail is complete) but get their
``near_dup_of`` column set to the earlier article's id; downstream
stages then skip them with a single ``WHERE near_dup_of IS NULL``.

Algorithm — Phase 2 implementation:

  1. Compute ``title_hash`` over the lowercased, whitespace-collapsed
     title. (Already done by Stage 0; we reuse the column.)
  2. Look for any earlier ``news_articles`` row with the same
     ``title_hash`` whose ``fetched_at`` is within the configured
     dedup window. The earliest such row is the canonical one.
  3. Return that row's id to the caller, who writes it into the new
     row's ``near_dup_of``.

Phase 3+ may upgrade this to simhash for fuzzy matches (rephrased
headlines). The function signature stays the same so the upgrade is
swap-in.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy.orm import Session

from backend.news_events.models import NewsArticle


# Window inside which an earlier article with the same title_hash
# counts as a duplicate. 24h matches the rolling-window choice the
# health bookkeeping uses; same-news that re-surfaces 25h later is
# almost always a new angle worth re-evaluating.
DEDUP_WINDOW_HOURS: int = 24


def find_near_dup(
    db: Session,
    *,
    title_hash: str,
    now: Optional[datetime] = None,
    window_hours: int = DEDUP_WINDOW_HOURS,
) -> Optional[str]:
    """Return the id of the earliest article with the same
    ``title_hash`` within the window, or None if none exists.

    Picks the EARLIEST so a chain of three near-dups all point at the
    same canonical row — keeps the audit cluster easy to read.
    """
    if not title_hash:
        return None
    cutoff = (now or datetime.now(timezone.utc)) - timedelta(hours=window_hours)
    row = (
        db.query(NewsArticle.id)
        .filter(
            NewsArticle.title_hash == title_hash,
            NewsArticle.fetched_at >= cutoff,
            # The canonical row is itself NOT a near-dup. Without this
            # filter a chain of 3 near-dups could point at each other.
            NewsArticle.near_dup_of.is_(None),
        )
        .order_by(NewsArticle.fetched_at.asc(), NewsArticle.id.asc())
        .first()
    )
    return row.id if row is not None else None
