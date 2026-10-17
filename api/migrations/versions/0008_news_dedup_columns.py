"""News & Event Trigger — Phase 2 dedup column.

Additive ALTER on our own Phase-1 ``news_articles`` table. Adds a
nullable ``near_dup_of`` column that the Stage-1 dedup pass populates
with the ``id`` of an earlier article carrying the same ``title_hash``.
The Stage-2 evaluator skips any row where this column is non-NULL —
that's the whole point.

No existing pre-Phase-1 table is touched. ``near_dup_of`` is a soft FK
without a DB-level constraint to keep behaviour identical to the other
news_events soft FKs (cascade is enforced in code).

Revision ID: 0008_news_dedup_columns
Revises: 0007_news_events
Create Date: 2026-05-21
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0008_news_dedup_columns"
down_revision: Union[str, None] = "0007_news_events"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "news_articles",
        sa.Column("near_dup_of", sa.String(length=36), nullable=True),
    )
    # Partial-style index — most rows will be NULL, so a vanilla index
    # is fine; the Stage-2 evaluator's hot path uses
    # ``WHERE near_dup_of IS NULL`` which the planner will satisfy via
    # an index-only scan on Postgres.
    op.create_index(
        "ix_news_articles_near_dup_of",
        "news_articles",
        ["near_dup_of"],
    )


def downgrade() -> None:
    op.drop_index("ix_news_articles_near_dup_of", table_name="news_articles")
    op.drop_column("news_articles", "near_dup_of")
