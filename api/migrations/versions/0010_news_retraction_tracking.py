"""News & Event Trigger — Phase 6 retraction-tracking columns.

Additive ALTERs on our own ``news_fired_events`` table — no
pre-news_events table is touched.

  + retraction_detected_at          TIMESTAMPTZ NULL
  + retraction_classification_id    String(36) NULL
                                    Soft FK to the RETRACTION verdict
                                    classification that triggered the
                                    retraction-policy action.
  + retraction_action_taken         VARCHAR(48) NULL
                                    One of:
                                      'cancel_pending_approvals'
                                      'cancel_and_alert'
                                      'ignore'
                                      'no_pending_approvals'
                                      'workflow_run_missing'
                                    (the audit "what we did about it").

Existing CHECK on ``retraction_status`` stays intact — these are pure
additions.

Revision ID: 0010_news_retraction_tracking
Revises: 0009_news_body_and_embeddings
Create Date: 2026-05-21
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0010_news_retraction_tracking"
down_revision: Union[str, None] = "0009_news_body_and_embeddings"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "news_fired_events",
        sa.Column(
            "retraction_detected_at", sa.DateTime(timezone=True), nullable=True
        ),
    )
    op.add_column(
        "news_fired_events",
        sa.Column(
            "retraction_classification_id", sa.String(length=36), nullable=True
        ),
    )
    op.add_column(
        "news_fired_events",
        sa.Column(
            "retraction_action_taken", sa.String(length=48), nullable=True
        ),
    )

    # The retraction watcher's hot query is
    #   WHERE retraction_status = 'none'
    #     AND retraction_window_ends_at > now()
    # Add a tiny index so the watcher's per-tick scan stays O(log N) as
    # fired events accumulate.
    op.create_index(
        "ix_news_fired_events_retraction_watch",
        "news_fired_events",
        ["retraction_status", "retraction_window_ends_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_news_fired_events_retraction_watch",
        table_name="news_fired_events",
    )
    op.drop_column("news_fired_events", "retraction_action_taken")
    op.drop_column("news_fired_events", "retraction_classification_id")
    op.drop_column("news_fired_events", "retraction_detected_at")
