"""user_auth_beta: per-user beta tables — chat summary, settings, auth audit,
email-verify + password-reset tokens.

Backs the new models in backend/models.py:
  - conversation_summaries  — one rolling NL summary per conversation.
  - user_settings           — per-user preference blob (JSON).
  - auth_audit              — append-only signup/login/refresh/logout trail.
  - email_verification_tokens / password_reset_tokens — single-use token
    hashes for the (deferred-send) verify + reset flows.

Additive-only — no ALTER on existing tables. Integer PKs autoincrement on
Postgres (SERIAL) and SQLite alike, matching the broker_audit / trade_logs
pattern.

Revision ID: 0022_user_auth_beta
Revises: 0021_broker_audit
Create Date: 2026-06-21
"""
from __future__ import annotations

import logging
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0022_user_auth_beta"
down_revision: Union[str, None] = "0021_broker_audit"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

logger = logging.getLogger("alembic.runtime.migration")


def upgrade() -> None:
    bind = op.get_bind()

    # ── conversation_summaries ───────────────────────────────────────────
    op.create_table(
        "conversation_summaries",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("conversation_id", sa.String(36), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False, server_default=""),
        sa.Column("message_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("model", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"],
                                ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("conversation_id", name="uq_conv_summary_conv"),
    )
    op.create_index("ix_conversation_summaries_user_id",
                    "conversation_summaries", ["user_id"])

    # ── user_settings ────────────────────────────────────────────────────
    op.create_table(
        "user_settings",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("settings", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", name="uq_user_settings_user"),
    )

    # ── auth_audit ───────────────────────────────────────────────────────
    op.create_table(
        "auth_audit",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("email", sa.String(255), nullable=True),
        sa.Column("event", sa.String(40), nullable=False),
        sa.Column("success", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("ip_address", sa.String(64), nullable=True),
        sa.Column("user_agent", sa.String(512), nullable=True),
        sa.Column("detail", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_auth_audit_user_id", "auth_audit", ["user_id"])
    op.create_index("ix_auth_audit_email", "auth_audit", ["email"])
    op.create_index("ix_auth_audit_created_at", "auth_audit", ["created_at"])

    # ── email_verification_tokens / password_reset_tokens ────────────────
    for tbl in ("email_verification_tokens", "password_reset_tokens"):
        op.create_table(
            tbl,
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("user_id", sa.Integer(), nullable=False),
            sa.Column("token_hash", sa.String(128), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True),
                      server_default=sa.func.now(), nullable=False),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("token_hash", name=f"uq_{tbl}_hash"),
        )
        op.create_index(f"ix_{tbl}_user_id", tbl, ["user_id"])

    logger.info("0022_user_auth_beta: created 5 user tables (dialect=%s).",
                bind.dialect.name)


def downgrade() -> None:
    for tbl in ("password_reset_tokens", "email_verification_tokens"):
        op.drop_index(f"ix_{tbl}_user_id", table_name=tbl)
        op.drop_table(tbl)
    op.drop_index("ix_auth_audit_created_at", table_name="auth_audit")
    op.drop_index("ix_auth_audit_email", table_name="auth_audit")
    op.drop_index("ix_auth_audit_user_id", table_name="auth_audit")
    op.drop_table("auth_audit")
    op.drop_table("user_settings")
    op.drop_index("ix_conversation_summaries_user_id",
                  table_name="conversation_summaries")
    op.drop_table("conversation_summaries")
