"""Stage 0 — source ingestion.

Inputs:  a ``SourceAdapter`` instance + its registry definition.
Output:  a ``IngestResult`` summarising what was persisted, plus
         the updated ``news_source_health`` row.

The function is intentionally synchronous on the DB side (matches
the rest of the backend, which uses sync SQLAlchemy + psycopg2) and
async on the fetch side (httpx). The poller in ``workers/poller.py``
calls ``ingest_one_source(...)`` per source per tick.

Idempotency: we dedup on ``url_hash`` before INSERT, so re-running the
same fetch is cheap and leaves no duplicate rows. A race between two
concurrent inserts is handled by the UNIQUE constraint on
``news_articles.url_hash`` — the loser falls into the
``IntegrityError`` branch and we count the article as "seen but not
new".
"""
from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.database import SessionLocal
from backend.news_events.models import NewsArticle, NewsSourceHealth
from backend.news_events.pipeline.dedup import find_near_dup
from backend.news_events.pipeline.keyword import apply_stage_2_for_article
from backend.news_events.sources.base import (
    FetchedItem,
    SourceAdapter,
    SourceFetchError,
)

if TYPE_CHECKING:  # pragma: no cover - type-only import
    from backend.news_events.config import SourceDef

logger = logging.getLogger(__name__)


@dataclass
class IngestResult:
    """Per-source ingest summary. Returned by ``ingest_one_source``."""

    source_id: str
    fetched_at: datetime
    items_seen: int = 0
    items_new: int = 0
    # Phase 2 funnel additions. ``items_after_stage1`` is the count
    # that survived cross-source dedup (i.e. ``near_dup_of IS NULL``).
    # ``items_after_stage2`` is the count of (article, spec) pairs
    # that the keyword filter let through; it can exceed
    # ``items_after_stage1`` when multiple active specs match.
    items_after_stage1: int = 0
    items_after_stage2: int = 0
    error: str | None = None
    new_article_ids: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.error is None


def _url_hash(url: str) -> str:
    """SHA-256 hex digest of the trimmed URL. We don't normalise
    further (no tracking-param stripping) on purpose: two feeds that
    publish the same article under slightly different URLs are kept
    as separate rows for now. Stage 1 (Phase 2) handles cross-feed
    near-dup via title simhash; Stage 0 only handles same-URL repeats.
    """
    return hashlib.sha256(url.strip().encode("utf-8")).hexdigest()


def _title_hash(title: str) -> str:
    """SHA-256 of the lowercased + whitespace-collapsed title. Phase 2
    will use this for cross-source dedup; in Phase 1 it's just persisted
    so the upgrade is free."""
    norm = " ".join(title.lower().split())
    return hashlib.sha256(norm.encode("utf-8")).hexdigest()


@dataclass
class _PersistOutcome:
    """Internal — returned by ``_persist_items``."""

    new_count: int = 0
    new_ids: list[str] = field(default_factory=list)
    after_stage1: int = 0  # rows with near_dup_of IS NULL
    after_stage2: int = 0  # classification rows with stage_2_passed=True


def _persist_items(db: Session, items: list[FetchedItem]) -> _PersistOutcome:
    """Insert each item if its url_hash isn't already present, then
    apply Stage-1 cross-source dedup (set ``near_dup_of`` for matches)
    and Stage-2 keyword filtering (persist classification rows for
    each active spec).

    Existing rows are skipped silently — that's the same-URL dedup
    the funnel relied on in Phase 1. New rows additionally trigger
    Phase-2 work BEFORE the commit so the Stage-2 audit trail and
    the article land in the same transaction.
    """
    out = _PersistOutcome()
    for item in items:
        url_hash = _url_hash(item.url)
        existing = (
            db.query(NewsArticle.id)
            .filter(NewsArticle.url_hash == url_hash)
            .first()
        )
        if existing is not None:
            continue

        title_h = _title_hash(item.title)
        # Stage 1 — does an earlier article in the dedup window share
        # the title? If yes, mark this row as a near-dup. The row is
        # still persisted (audit) but downstream stages skip it.
        near_dup_id = find_near_dup(db, title_hash=title_h)

        row = NewsArticle(
            source_id=item.source_id,
            url=item.url,
            url_hash=url_hash,
            title=item.title,
            title_hash=title_h,
            summary=item.summary,
            published_at=item.published_at,
            raw_metadata=dict(item.raw_metadata) if item.raw_metadata else None,
            near_dup_of=near_dup_id,
        )
        db.add(row)
        try:
            db.flush()
        except IntegrityError:
            # Concurrent poll inserted the same URL between SELECT
            # and INSERT. Roll back this row and continue — the
            # other path counted it.
            db.rollback()
            continue
        out.new_count += 1
        out.new_ids.append(row.id)

        # Stage 2 — keyword filter, only for non-dup articles. The
        # classifications table holds one row per (article, spec)
        # pair; specs_passed counts the True ones for the metrics.
        if near_dup_id is None:
            out.after_stage1 += 1
            stage_2 = apply_stage_2_for_article(
                db,
                article_id=row.id,
                title=item.title,
                summary=item.summary,
            )
            out.after_stage2 += stage_2.specs_passed
    db.commit()
    return out


def _record_success(
    db: Session, source_id: str, fetched_at: datetime, seen: int, new: int
) -> None:
    """Upsert the source_health row after a successful fetch."""
    row = (
        db.query(NewsSourceHealth)
        .filter(NewsSourceHealth.source_id == source_id)
        .first()
    )
    if row is None:
        row = NewsSourceHealth(
            source_id=source_id,
            last_successful_fetch_at=fetched_at,
            articles_seen_24h=seen,
            articles_passed_24h=new,
            consecutive_failures=0,
        )
        db.add(row)
    else:
        row.last_successful_fetch_at = fetched_at
        # Phase 1 keeps the 24h counters monotonically increasing per
        # tick. A separate rollover job (Phase 2) will reset them on
        # the 24h boundary. Adequate for the smoke-metrics endpoint.
        row.articles_seen_24h = int(row.articles_seen_24h or 0) + seen
        row.articles_passed_24h = int(row.articles_passed_24h or 0) + new
        row.consecutive_failures = 0
    db.commit()


def _record_failure(
    db: Session, source_id: str, fetched_at: datetime, message: str
) -> None:
    """Upsert the source_health row after a failed fetch."""
    row = (
        db.query(NewsSourceHealth)
        .filter(NewsSourceHealth.source_id == source_id)
        .first()
    )
    if row is None:
        row = NewsSourceHealth(
            source_id=source_id,
            last_error_at=fetched_at,
            last_error_message=message[:1000],
            consecutive_failures=1,
        )
        db.add(row)
    else:
        row.last_error_at = fetched_at
        row.last_error_message = message[:1000]
        row.consecutive_failures = int(row.consecutive_failures or 0) + 1
    db.commit()


async def ingest_one_source(
    adapter: SourceAdapter,
    *,
    db_session: Session | None = None,
    db_session_factory=SessionLocal,
) -> IngestResult:
    """Fetch one source, dedup-persist, update health, return summary.

    Two session-management modes:

      - ``db_session`` provided (FastAPI route path): caller owns the
        session lifecycle; we never close it.
      - ``db_session=None`` (poller path): we open a fresh session via
        ``db_session_factory`` and close it before returning. Tests
        inject a sqlite-bound factory here.
    """
    fetched_at = datetime.now(timezone.utc)
    try:
        items = await adapter.fetch()
    except SourceFetchError as exc:
        message = str(exc)
        logger.warning(
            "[news_events.ingest] source=%s fetch_failed message=%s",
            adapter.source_id,
            message,
        )
        if db_session is not None:
            _record_failure(db_session, adapter.source_id, fetched_at, message)
        else:
            db = db_session_factory()
            try:
                _record_failure(db, adapter.source_id, fetched_at, message)
            finally:
                db.close()
        return IngestResult(
            source_id=adapter.source_id,
            fetched_at=fetched_at,
            error=message,
        )

    if db_session is not None:
        outcome = _persist_items(db_session, items)
        _record_success(
            db_session,
            adapter.source_id,
            fetched_at,
            seen=len(items),
            new=outcome.new_count,
        )
    else:
        db = db_session_factory()
        try:
            outcome = _persist_items(db, items)
            _record_success(
                db,
                adapter.source_id,
                fetched_at,
                seen=len(items),
                new=outcome.new_count,
            )
        finally:
            db.close()

    logger.info(
        "[news_events.ingest] source=%s seen=%d new=%d after_stage1=%d after_stage2=%d",
        adapter.source_id,
        len(items),
        outcome.new_count,
        outcome.after_stage1,
        outcome.after_stage2,
    )
    return IngestResult(
        source_id=adapter.source_id,
        fetched_at=fetched_at,
        items_seen=len(items),
        items_new=outcome.new_count,
        items_after_stage1=outcome.after_stage1,
        items_after_stage2=outcome.after_stage2,
        new_article_ids=outcome.new_ids,
    )


def build_adapter(source: "SourceDef") -> SourceAdapter:
    """Factory: pick the right adapter for a registry entry.

    Phase 1 only knows RSS; Phase 7 will add WebSub / n8n adapters and
    extend this switch.
    """
    from backend.news_events.sources.rss import RSSAdapter

    # All Phase 1 sources are RSS/Atom URLs. Future per-source types
    # (GDELT JSON, Polymarket Gamma) get a `kind` field on SourceDef.
    return RSSAdapter(source_id=source.source_id, feed_url=source.feed_url)


# ── Phase 7 — push-transport entry point ─────────────────────────────


def persist_pushed_items(
    db: Session,
    *,
    source_id: str,
    items: list[FetchedItem],
) -> _PersistOutcome:
    """Persist items pushed from a non-polling source (Telegram, Miniflux
    webhook). Runs the same Stage-0 dedup, Stage-1 cross-source dedup,
    and Stage-2 keyword evaluation as the in-process poller.

    Returns the same ``_PersistOutcome`` shape the poller produces.
    Callers are responsible for ``db.commit()`` — the function flushes
    so the rows are visible inside the caller's transaction but does
    not commit.

    Updates the source's health row so ``/admin/sources`` reflects
    push-driven activity the same way it reflects polled activity.
    """
    outcome = _persist_items(db, items)
    if items:
        # Treat the push like a successful "fetch" for health
        # bookkeeping — same code path the poller uses on success.
        _record_success(
            db,
            source_id,
            datetime.now(timezone.utc),
            seen=len(items),
            new=outcome.new_count,
        )
    return outcome
