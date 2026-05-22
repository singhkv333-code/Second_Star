"""News & Event Trigger — Phase 3 body fetch + embedding columns.

Additive ALTERs on our own news_events tables. No pre-news_events
table is touched.

Adds:

  news_articles
    + body_fetched_at      TIMESTAMPTZ NULL
    + body_fetch_status    VARCHAR(32) NULL
                           one of 'ok' / 'robots_disallowed' /
                           'http_error' / 'extract_failed'
    + text_embedding       JSON / JSONB NULL
                           list[float], dimension is
                           ``len(...) == model_dim``; populated by
                           Stage 4.

  news_event_specs
    + description_embedding JSON / JSONB NULL
                           one-time computed embedding of the spec
                           description; cached on first Stage-4 pass.

The existing ``body_text`` column (added in 0007 but unused in
Phase 1) gets its first real population in Phase 3.

Revision ID: 0009_news_body_and_embeddings
Revises: 0008_news_dedup_columns
Create Date: 2026-05-21
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "0009_news_body_and_embeddings"
down_revision: Union[str, None] = "0008_news_dedup_columns"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _json_type(bind):
    if bind.dialect.name == "postgresql":
        return postgresql.JSONB(astext_type=sa.Text())
    return sa.JSON()


def upgrade() -> None:
    bind = op.get_bind()
    JSON_T = _json_type(bind)

    op.add_column(
        "news_articles",
        sa.Column("body_fetched_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "news_articles",
        sa.Column("body_fetch_status", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "news_articles",
        sa.Column("text_embedding", JSON_T, nullable=True),
    )

    op.add_column(
        "news_event_specs",
        sa.Column("description_embedding", JSON_T, nullable=True),
    )

    # The funnel scans pending classifications by
    # (stage_2_passed=true AND classifier_verdict IS NULL). Add a
    # covering index so the worker's hot query is O(log N).
    op.create_index(
        "ix_news_classifications_pending",
        "news_article_classifications",
        ["stage_2_passed", "classifier_verdict"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_news_classifications_pending",
        table_name="news_article_classifications",
    )
    op.drop_column("news_event_specs", "description_embedding")
    op.drop_column("news_articles", "text_embedding")
    op.drop_column("news_articles", "body_fetch_status")
    op.drop_column("news_articles", "body_fetched_at")
