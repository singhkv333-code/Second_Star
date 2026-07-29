"""ORM models for the news_events subsystem.

Six new tables, additive only. The DDL of record is migration 0007;
this file mirrors it so SQLAlchemy sessions can query through the ORM
and alembic --autogenerate stays in sync.

Cross-dialect conventions (mirrors backend/models.py §"Agent System"):

  - String(36) UUID PKs with a Python-side default via ``_uuid_str``.
    Same string lives in Postgres + SQLite test DBs.
  - SQLAlchemy ``JSON`` column type. Renders as JSONB on Postgres and
    JSON on SQLite — same dual-dialect choice the workflows tables
    made.
  - Soft FKs to ``workflows.id`` / ``workflow_runs.id`` are stored as
    plain String(36) without a ForeignKey constraint. Referential
    integrity is enforced in code (backend/news_events/integration.py)
    so this whole module stays additive and unable to corrupt the
    existing workflow tables.
"""
from __future__ import annotations

import uuid as _uuid

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.sql import func

from backend.database import Base


def _uuid_str() -> str:
    """Cross-dialect UUID v4 string default — matches the same helper
    in backend/models.py for the Agent System tables."""
    return str(_uuid.uuid4())


# ── Tier / state literals ────────────────────────────────────────────
#
# Kept as module-level frozensets so callers can validate before insert
# without re-importing the CheckConstraint values. Both the constraint
# and these sets must change together if we add a tier.
TIERS: frozenset[str] = frozenset({"tier1", "tier2", "tier3"})
EVENT_SPEC_STATES: frozenset[str] = frozenset(
    {
        "draft",
        "pending_disambiguation",
        "active",
        "fired",
        "expired",
        "cancelled",
    }
)
CLASSIFIER_VERDICTS: frozenset[str] = frozenset(
    {"YES", "NO", "AMBIGUOUS", "UNRELATED", "RETRACTION"}
)
RETRACTION_STATUSES: frozenset[str] = frozenset(
    {"none", "detected", "handled"}
)
DISAMBIGUATION_STATES: frozenset[str] = frozenset(
    {"open", "completed", "expired", "cancelled"}
)


class NewsEventSpec(Base):
    """A user-defined event automation.

    Wraps the natural-language description, the resolution criteria,
    the retraction policy, the deadline, the watch window, the keyword
    set used by the Stage 2 funnel filter, and a soft FK to the
    workflow that gets fired when resolution is reached.

    Phase 1 ships the table but no read/write paths yet — first user
    surface lands in Phase 4.
    """

    __tablename__ = "news_event_specs"

    id = Column(String(36), primary_key=True, default=_uuid_str)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    # Soft FK to workflows.id — no DB constraint. Code enforces.
    workflow_id = Column(String(36), nullable=True, index=True)
    tier = Column(String(8), nullable=False)
    description = Column(Text, nullable=False)
    resolution_criteria = Column(JSON, nullable=False)
    retraction_policy = Column(JSON, nullable=False)
    keyword_set = Column(JSON, nullable=False)
    deadline_at = Column(DateTime(timezone=True), nullable=True)
    watch_window_start_at = Column(DateTime(timezone=True), nullable=True)
    state = Column(String(32), nullable=False, default="draft", index=True)
    # Phase 3 Stage-4 cache: one-time embedding of ``description``.
    # Computed on the first funnel pass that needs it; persisted so
    # we never re-embed the same spec text.
    description_embedding = Column(JSON, nullable=True)
    created_at = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    __table_args__ = (
        CheckConstraint(
            "tier IN ('tier1', 'tier2', 'tier3')",
            name="ck_news_event_specs_tier",
        ),
        CheckConstraint(
            "state IN ('draft', 'pending_disambiguation', 'active', "
            "'fired', 'expired', 'cancelled')",
            name="ck_news_event_specs_state",
        ),
    )


class NewsArticle(Base):
    """Raw article ingested from a source.

    Deduplicated on ``url_hash`` — re-fetches of the same URL across
    feeds collapse into one row (Stage 1 funnel). The body_text column
    is intentionally NULL on insert; Stage 3 (Phase 3+) populates it
    only for survivors of the Stage 2 keyword filter.
    """

    __tablename__ = "news_articles"

    id = Column(String(36), primary_key=True, default=_uuid_str)
    source_id = Column(String(64), nullable=False, index=True)
    url = Column(Text, nullable=False)
    url_hash = Column(String(64), nullable=False, unique=True)
    title = Column(Text, nullable=False)
    title_hash = Column(String(64), nullable=False, index=True)
    summary = Column(Text, nullable=True)
    published_at = Column(DateTime(timezone=True), nullable=True)
    fetched_at = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    body_text = Column(Text, nullable=True)
    raw_metadata = Column(JSON, nullable=True)
    # Stage 1 cross-source dedup: when this row's ``title_hash`` matches
    # an article ingested within the dedup window, this column points at
    # the original. Stage 2 skips any row where this is non-NULL.
    near_dup_of = Column(String(36), nullable=True, index=True)
    # Stage 3 (Phase 3): full-article fetch bookkeeping.
    # ``body_fetch_status`` is one of 'ok' / 'robots_disallowed' /
    # 'http_error' / 'extract_failed'. ``body_text`` is populated only
    # on the 'ok' branch.
    body_fetched_at = Column(DateTime(timezone=True), nullable=True)
    body_fetch_status = Column(String(32), nullable=True)
    # Stage 4 (Phase 3): embedding of (title + summary + body) under
    # the configured embedding model. JSON list[float] of dimension
    # ``model_dim``. Phase 3 uses OpenAI text-embedding-3-small at
    # 1536 dims; future swap is a column re-fill rather than a schema
    # change.
    text_embedding = Column(JSON, nullable=True)

    __table_args__ = (
        UniqueConstraint("url_hash", name="uq_news_articles_url_hash"),
    )


class NewsArticleClassification(Base):
    """Per (article, event_spec) classifier verdict.

    Phase 2 onward populates one row per article-spec pair that reached
    Stage 2 or beyond. The verdict columns (``classifier_verdict``,
    ``confidence``, ``excerpt``) stay NULL until the row reaches Stage 6.
    Audit trail surface for ``GET /api/news-events/fired/{id}``.
    """

    __tablename__ = "news_article_classifications"

    id = Column(String(36), primary_key=True, default=_uuid_str)
    article_id = Column(
        String(36),
        ForeignKey("news_articles.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    event_spec_id = Column(
        String(36),
        ForeignKey("news_event_specs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    stage_2_passed = Column(Boolean, nullable=False, default=False)
    embedding_similarity = Column(Float, nullable=True)
    classifier_verdict = Column(String(16), nullable=True)
    confidence = Column(Float, nullable=True)
    excerpt = Column(Text, nullable=True)
    model = Column(String(64), nullable=True)
    created_at = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        CheckConstraint(
            "classifier_verdict IS NULL OR classifier_verdict IN "
            "('YES', 'NO', 'AMBIGUOUS', 'UNRELATED', 'RETRACTION')",
            name="ck_news_classifications_verdict",
        ),
    )


class NewsSourceHealth(Base):
    """One row per source_id. Drives /admin/sources and adaptive polling.

    Rows are upserted by the poller after every fetch attempt. The 24h
    counters are eventually consistent — recomputed by a separate
    rollover job (Phase 2) or lazily by the admin endpoint.
    """

    __tablename__ = "news_source_health"

    source_id = Column(String(64), primary_key=True)
    last_successful_fetch_at = Column(DateTime(timezone=True), nullable=True)
    last_error_at = Column(DateTime(timezone=True), nullable=True)
    last_error_message = Column(Text, nullable=True)
    consecutive_failures = Column(Integer, nullable=False, default=0)
    articles_seen_24h = Column(Integer, nullable=False, default=0)
    articles_passed_24h = Column(Integer, nullable=False, default=0)
    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class NewsFiredEvent(Base):
    """Audit + idempotency row for a fired event.

    The UNIQUE constraint on ``event_spec_id`` enforces "an event fires
    at most once" — paired with ``workflow_runs.client_request_id``
    idempotency on the broker side gives end-to-end exactly-once.
    Phase 6's retraction handler may extend the schema with a
    ``superseded_by`` column rather than relaxing this constraint.
    """

    __tablename__ = "news_fired_events"

    id = Column(String(36), primary_key=True, default=_uuid_str)
    event_spec_id = Column(
        String(36),
        ForeignKey("news_event_specs.id", ondelete="CASCADE"),
        nullable=False,
    )
    # Soft FK to workflow_runs.id — no DB constraint.
    workflow_run_id = Column(String(36), nullable=True)
    fired_at = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    tier = Column(String(8), nullable=False)
    aggregated_confidence = Column(Float, nullable=False)
    supporting_classification_ids = Column(JSON, nullable=False)
    prediction_market_snapshot = Column(JSON, nullable=True)
    retraction_window_ends_at = Column(DateTime(timezone=True), nullable=True)
    retraction_status = Column(String(16), nullable=False, default="none")
    # Phase 6 — populated by the retraction watcher when a RETRACTION
    # verdict lands inside the safety window. retraction_action_taken
    # records what the watcher did about it (cancel_pending_approvals /
    # cancel_and_alert / ignore / no_pending_approvals / workflow_run_missing).
    retraction_detected_at = Column(DateTime(timezone=True), nullable=True)
    retraction_classification_id = Column(String(36), nullable=True)
    retraction_action_taken = Column(String(48), nullable=True)

    __table_args__ = (
        CheckConstraint(
            "retraction_status IN ('none', 'detected', 'handled')",
            name="ck_news_fired_events_retraction_status",
        ),
        UniqueConstraint(
            "event_spec_id", name="uq_news_fired_events_event_spec_id"
        ),
    )


class NewsDisambiguationSession(Base):
    """Tier-3 multi-question state during spec creation.

    Lives in Postgres (not Redis) because the user may take minutes
    between answers and the chat session may rotate. ``expires_at`` is
    set by the parser (typically 30 min from creation).
    """

    __tablename__ = "news_disambiguation_sessions"

    id = Column(String(36), primary_key=True, default=_uuid_str)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    conversation_id = Column(String(64), nullable=True)
    pending_event_spec = Column(JSON, nullable=False)
    questions = Column(JSON, nullable=False)
    answers = Column(JSON, nullable=False, default=dict)
    state = Column(String(32), nullable=False, default="open")
    created_at = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    expires_at = Column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        CheckConstraint(
            "state IN ('open', 'completed', 'expired', 'cancelled')",
            name="ck_news_disambiguation_state",
        ),
    )
