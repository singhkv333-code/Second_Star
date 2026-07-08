"""News & Event Trigger — additive tables for Phase 1.

Adds the isolated news_events subsystem's storage. Strictly additive:
no ALTER on any existing table. The subsystem is gated by the
``news_events_enabled`` setting; with the flag off the tables exist but
stay empty.

Soft FKs (`news_event_specs.workflow_id`, `news_fired_events.workflow_run_id`,
`news_fired_events.event_spec_id`) reference rows in `workflows` /
`workflow_runs` by UUID without a DB-level constraint, to keep this
migration 100 % additive and unable to break existing tables. Code in
backend/news_events/ enforces referential integrity.

Six new tables. Postgres types where possible; SQLite fallback works for
the test DB (JSON instead of JSONB, TEXT instead of UUID, no partial
indexes that require a Postgres-only WHERE).

Revision ID: 0007_news_events
Revises: 0006_llm_usage_cached_tokens
Create Date: 2026-05-21
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "0007_news_events"
down_revision: Union[str, None] = "0006_llm_usage_cached_tokens"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _json_type(bind) -> sa.types.TypeEngine:
    """JSONB on Postgres, JSON on SQLite. Matches the dual-dialect choice
    made for workflow_steps.config."""
    if bind.dialect.name == "postgresql":
        return postgresql.JSONB(astext_type=sa.Text())
    return sa.JSON()


def upgrade() -> None:
    bind = op.get_bind()
    JSON_T = _json_type(bind)

    # ── news_event_specs ────────────────────────────────────────────────
    # One row per user-defined event automation. Tier metadata, resolution
    # criteria, retraction policy, deadline, watch window, keyword set,
    # link to a workflow (soft FK).
    op.create_table(
        "news_event_specs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        # Soft FK to workflows.id — no DB-level constraint to keep this
        # migration additive. Enforced in code.
        sa.Column("workflow_id", sa.String(36), nullable=True),
        sa.Column("tier", sa.String(8), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("resolution_criteria", JSON_T, nullable=False),
        sa.Column("retraction_policy", JSON_T, nullable=False),
        sa.Column("keyword_set", JSON_T, nullable=False),
        sa.Column("deadline_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("watch_window_start_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("state", sa.String(32), nullable=False, server_default=sa.text("'draft'")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()") if bind.dialect.name == "postgresql" else sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()") if bind.dialect.name == "postgresql" else sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.CheckConstraint(
            "tier IN ('tier1', 'tier2', 'tier3')",
            name="ck_news_event_specs_tier",
        ),
        sa.CheckConstraint(
            "state IN ('draft', 'pending_disambiguation', 'active', "
            "'fired', 'expired', 'cancelled')",
            name="ck_news_event_specs_state",
        ),
    )
    op.create_index("ix_news_event_specs_user_id", "news_event_specs", ["user_id"])
    op.create_index("ix_news_event_specs_state", "news_event_specs", ["state"])
    op.create_index("ix_news_event_specs_workflow_id", "news_event_specs", ["workflow_id"])

    # ── news_articles ───────────────────────────────────────────────────
    # Raw articles ingested from sources. Deduped on url_hash so two
    # feeds republishing the same URL collapse to one row.
    op.create_table(
        "news_articles",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("source_id", sa.String(64), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("url_hash", sa.String(64), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("title_hash", sa.String(64), nullable=False),
        sa.Column("summary", sa.Text(), nullable=True),
        # Published_at as reported by the feed; nullable because some
        # feeds omit it. fetched_at is when WE saw it.
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "fetched_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()") if bind.dialect.name == "postgresql" else sa.text("CURRENT_TIMESTAMP"),
        ),
        # Body text — populated only for survivors of Stage 2 keyword
        # filter (Phase 2+). NULL in Phase 1.
        sa.Column("body_text", sa.Text(), nullable=True),
        sa.Column("raw_metadata", JSON_T, nullable=True),
        sa.UniqueConstraint("url_hash", name="uq_news_articles_url_hash"),
    )
    op.create_index("ix_news_articles_source_id", "news_articles", ["source_id"])
    op.create_index("ix_news_articles_title_hash", "news_articles", ["title_hash"])
    op.create_index(
        "ix_news_articles_published_at",
        "news_articles",
        [sa.text("published_at DESC")],
    )

    # ── news_article_classifications ────────────────────────────────────
    # Per (article, event_spec) classifier verdict — Phases 2-6 populate.
    # Phase 1 ships the table empty.
    op.create_table(
        "news_article_classifications",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "article_id",
            sa.String(36),
            sa.ForeignKey("news_articles.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "event_spec_id",
            sa.String(36),
            sa.ForeignKey("news_event_specs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("stage_2_passed", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("embedding_similarity", sa.Float(), nullable=True),
        sa.Column("classifier_verdict", sa.String(16), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("excerpt", sa.Text(), nullable=True),
        sa.Column("model", sa.String(64), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()") if bind.dialect.name == "postgresql" else sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.CheckConstraint(
            "classifier_verdict IS NULL OR classifier_verdict IN "
            "('YES', 'NO', 'AMBIGUOUS', 'UNRELATED', 'RETRACTION')",
            name="ck_news_classifications_verdict",
        ),
    )
    op.create_index(
        "ix_news_classifications_article_id",
        "news_article_classifications",
        ["article_id"],
    )
    op.create_index(
        "ix_news_classifications_event_spec_id",
        "news_article_classifications",
        ["event_spec_id"],
    )

    # ── news_source_health ──────────────────────────────────────────────
    # One row per source_id. Drives /admin/sources and adaptive polling.
    op.create_table(
        "news_source_health",
        sa.Column("source_id", sa.String(64), primary_key=True),
        sa.Column("last_successful_fetch_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_message", sa.Text(), nullable=True),
        sa.Column("consecutive_failures", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("articles_seen_24h", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("articles_passed_24h", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()") if bind.dialect.name == "postgresql" else sa.text("CURRENT_TIMESTAMP"),
        ),
    )

    # ── news_fired_events ───────────────────────────────────────────────
    # Audit + idempotency. One row per fired event. Holds the "why we
    # fired" payload for user trust + regulatory defensibility.
    op.create_table(
        "news_fired_events",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "event_spec_id",
            sa.String(36),
            sa.ForeignKey("news_event_specs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        # Soft FK to workflow_runs.id — no DB-level constraint.
        sa.Column("workflow_run_id", sa.String(36), nullable=True),
        sa.Column(
            "fired_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()") if bind.dialect.name == "postgresql" else sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column("tier", sa.String(8), nullable=False),
        sa.Column("aggregated_confidence", sa.Float(), nullable=False),
        sa.Column("supporting_classification_ids", JSON_T, nullable=False),
        sa.Column("prediction_market_snapshot", JSON_T, nullable=True),
        sa.Column("retraction_window_ends_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "retraction_status",
            sa.String(16),
            nullable=False,
            server_default=sa.text("'none'"),
        ),
        sa.CheckConstraint(
            "retraction_status IN ('none', 'detected', 'handled')",
            name="ck_news_fired_events_retraction_status",
        ),
        # Idempotency: an event spec can fire at most once. Phase 6's
        # retraction handler may relax this by adding a `superseded_by`
        # column rather than removing the constraint.
        sa.UniqueConstraint("event_spec_id", name="uq_news_fired_events_event_spec_id"),
    )
    op.create_index(
        "ix_news_fired_events_fired_at",
        "news_fired_events",
        [sa.text("fired_at DESC")],
    )

    # ── news_disambiguation_sessions ────────────────────────────────────
    # Tier-3 multi-question state during spec creation. Lives in Postgres
    # (not Redis) because the user may take minutes to answer between
    # turns and the chat session may rotate.
    op.create_table(
        "news_disambiguation_sessions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("conversation_id", sa.String(64), nullable=True),
        sa.Column("pending_event_spec", JSON_T, nullable=False),
        sa.Column("questions", JSON_T, nullable=False),
        sa.Column("answers", JSON_T, nullable=False, server_default=sa.text("'{}'") if bind.dialect.name == "postgresql" else sa.text("'{}'")),
        sa.Column("state", sa.String(32), nullable=False, server_default=sa.text("'open'")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()") if bind.dialect.name == "postgresql" else sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "state IN ('open', 'completed', 'expired', 'cancelled')",
            name="ck_news_disambiguation_state",
        ),
    )
    op.create_index(
        "ix_news_disambiguation_user_id",
        "news_disambiguation_sessions",
        ["user_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_news_disambiguation_user_id",
        table_name="news_disambiguation_sessions",
    )
    op.drop_table("news_disambiguation_sessions")

    op.drop_index("ix_news_fired_events_fired_at", table_name="news_fired_events")
    op.drop_table("news_fired_events")

    op.drop_table("news_source_health")

    op.drop_index(
        "ix_news_classifications_event_spec_id",
        table_name="news_article_classifications",
    )
    op.drop_index(
        "ix_news_classifications_article_id",
        table_name="news_article_classifications",
    )
    op.drop_table("news_article_classifications")

    op.drop_index("ix_news_articles_published_at", table_name="news_articles")
    op.drop_index("ix_news_articles_title_hash", table_name="news_articles")
    op.drop_index("ix_news_articles_source_id", table_name="news_articles")
    op.drop_table("news_articles")

    op.drop_index("ix_news_event_specs_workflow_id", table_name="news_event_specs")
    op.drop_index("ix_news_event_specs_state", table_name="news_event_specs")
    op.drop_index("ix_news_event_specs_user_id", table_name="news_event_specs")
    op.drop_table("news_event_specs")
