"""IPO applications — register-not-execute intent rows.

One additive table. No ALTER on any existing table. Mirrors the ORM
model added to backend/models.py (the §"IPO Applications" block) 1:1,
the same way 0013_paper_trading mirrors PaperAccount + friends.

  ipo_applications   register-not-execute IPO intents (P0: chat-confirm)

Cross-dialect notes (same as 0011 / 0013): enum-like columns are String
+ CheckConstraint (not native PG ENUM); timestamp server defaults are
dialect-aware (now() vs CURRENT_TIMESTAMP).

Soft references — no hard FK:
  - conversation_id (String): conversations.id is uuid in prod but the
    SQLite test DB writes it as plain String — a hard FK can fail to
    build across dialects. Same gotcha that motivated forward_ideas's
    soft refs in 0013.
  - workflow_id (Integer): workflows.id is uuid in prod, not Integer —
    keeping this Integer + soft means a row from a P2 reminder workflow
    can attach by value without breaking the schema across dialects.

DEPLOY ORDER: FKs users.id (a base table created by 0001 / create_all).
On a brand-new Postgres DB this means base ORM tables must exist before
`alembic upgrade head` reaches 0014.

Revision ID: 0014_ipo_applications
Revises: 0013_paper_trading
Create Date: 2026-06-02
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0014_ipo_applications"
down_revision: Union[str, None] = "0013_paper_trading"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _ts(bind):
    """Dialect-aware server default for timestamp columns (mirrors 0013)."""
    return (
        sa.text("now()") if bind.dialect.name == "postgresql"
        else sa.text("CURRENT_TIMESTAMP")
    )


def upgrade() -> None:
    bind = op.get_bind()
    TS = _ts(bind)

    op.create_table(
        "ipo_applications",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "user_id", sa.Integer(), sa.ForeignKey("users.id"),
            nullable=False,
        ),
        sa.Column("ipo_symbol", sa.String(50), nullable=False),
        sa.Column("ipo_name", sa.String(200), nullable=True),
        sa.Column("ipo_type", sa.String(16), nullable=False),
        sa.Column("category", sa.String(16), nullable=False),
        sa.Column("quantity_lots", sa.Integer(), nullable=False),
        sa.Column("lot_size", sa.Integer(), nullable=False),
        sa.Column("bid_price_mode", sa.String(8), nullable=False),
        sa.Column("bid_price", sa.Float(), nullable=True),
        sa.Column("amount_estimate", sa.Float(), nullable=False),
        sa.Column("upi_id_masked", sa.String(64), nullable=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("autonomous", sa.Boolean(), nullable=False),
        sa.Column("paper_mode", sa.Boolean(), nullable=False),
        sa.Column("stale", sa.Boolean(), nullable=False),
        # SOFT references — no FK. See file header.
        sa.Column("conversation_id", sa.String(64), nullable=True),
        sa.Column("workflow_id", sa.Integer(), nullable=True),
        sa.Column("source", sa.String(50), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            nullable=False, server_default=TS,
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True),
            nullable=False, server_default=TS,
        ),
        sa.CheckConstraint(
            "ipo_type IN ('mainboard', 'sme')",
            name="ck_ipo_applications_type",
        ),
        sa.CheckConstraint(
            "category IN ('retail', 'snii', 'bnii', 'shareholder', 'employee')",
            name="ck_ipo_applications_category",
        ),
        sa.CheckConstraint(
            "bid_price_mode IN ('cutoff', 'fixed')",
            name="ck_ipo_applications_bid_price_mode",
        ),
        sa.CheckConstraint(
            "status IN ('registered', 'withdrawn', 'intent_armed', "
            "'applied', 'blocked', 'allotted', 'not_allotted', 'rejected')",
            name="ck_ipo_applications_status",
        ),
    )
    op.create_index(
        "ix_ipo_applications_user_id", "ipo_applications", ["user_id"],
    )
    op.create_index(
        "ix_ipo_applications_ipo_symbol", "ipo_applications", ["ipo_symbol"],
    )
    op.create_index(
        "ix_ipo_applications_user_status", "ipo_applications",
        ["user_id", "status"],
    )


def downgrade() -> None:
    op.drop_index("ix_ipo_applications_user_status", table_name="ipo_applications")
    op.drop_index("ix_ipo_applications_ipo_symbol", table_name="ipo_applications")
    op.drop_index("ix_ipo_applications_user_id", table_name="ipo_applications")
    op.drop_table("ipo_applications")
