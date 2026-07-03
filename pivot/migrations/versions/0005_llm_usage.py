"""Per-call LLM usage + cost ledger.

Phase 0 — Wave 3 of the observability rollout. Every call into our
LLM clients (OpenAI Responses + Sarvam-m) now persists a row here on
close of trace, recording token counts, latency, computed USD cost,
and the request / user / conversation context that issued the call.

Why a dedicated table rather than piggy-backing on ``llm_traces`` or
``conversation_messages``:

  - The trace-file output is gated behind ``PIVOT_LLM_TRACE`` and is
    PII-bearing (raw prompts land in it). The cost ledger must always
    record, and must NEVER carry prompt or response text.
  - The ledger is the source-of-truth for cost dashboards / billing
    alerts. Putting it next to chat_messages would couple "did the
    chat path render?" to "did we get billed?", which we want to keep
    decoupled (e.g. agentic / scheduler calls bill too).

Indexes:

  - ``(user_id, created_at)`` for "spend by user over the last N days".
  - ``(created_at)`` for "spend across all users in the last 24h" and
    for the per-day rollup we'll add in a later migration.

``cost_usd`` is Numeric(12, 6) — six decimal places of USD covers
sub-penny per-call costs without rounding loss on aggregation; twelve
total digits handles up to $999,999.999999 per row, which is a
comfortable safety margin against any single runaway request.

Revision ID: 0005_llm_usage
Revises: 0004_encrypt_kite_tokens
Create Date: 2026-05-12
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0005_llm_usage"
down_revision: Union[str, None] = "0004_encrypt_kite_tokens"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "llm_usage",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id"),
            nullable=True,
        ),
        sa.Column("conversation_id", sa.String(length=64), nullable=True),
        sa.Column("turn_id", sa.String(length=64), nullable=True),
        sa.Column("request_id", sa.String(length=64), nullable=True),
        sa.Column("endpoint", sa.String(length=64), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("model", sa.String(length=64), nullable=False),
        sa.Column(
            "input_tokens",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "output_tokens",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "reasoning_tokens",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "total_tokens",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "cost_usd",
            sa.Numeric(precision=12, scale=6),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column("latency_ms", sa.Float(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_llm_usage_user_id",
        "llm_usage",
        ["user_id"],
    )
    op.create_index(
        "ix_llm_usage_conversation_id",
        "llm_usage",
        ["conversation_id"],
    )
    op.create_index(
        "ix_llm_usage_request_id",
        "llm_usage",
        ["request_id"],
    )
    op.create_index(
        "ix_llm_usage_created_at",
        "llm_usage",
        ["created_at"],
    )
    # Composite for the "spend by user in the last 24h" query — the
    # planner will use this in preference to scanning by user_id then
    # filtering by created_at.
    op.create_index(
        "ix_llm_usage_user_created",
        "llm_usage",
        ["user_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_llm_usage_user_created", table_name="llm_usage")
    op.drop_index("ix_llm_usage_created_at", table_name="llm_usage")
    op.drop_index("ix_llm_usage_request_id", table_name="llm_usage")
    op.drop_index("ix_llm_usage_conversation_id", table_name="llm_usage")
    op.drop_index("ix_llm_usage_user_id", table_name="llm_usage")
    op.drop_table("llm_usage")
