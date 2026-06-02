"""Paper IPO allocations — labelled-simulation IPO ledger (P3).

One additive table. No ALTER on any existing table. Mirrors the ORM
model added to backend/models.py (the §"IPO paper-mode simulated
allocations" block) 1:1, the same way 0013_paper_trading mirrors
PaperAccount + friends and 0014_ipo_applications mirrors IPOApplication.

  paper_ipo_allocations   simulated allotment outcome per IPO intent

Cross-dialect notes (same as 0013 / 0014): enum-like columns are String
+ CheckConstraint (not native PG ENUM); timestamp server defaults are
dialect-aware (now() vs CURRENT_TIMESTAMP).

Hard FKs: ``user_id -> users.id`` and ``paper_account_id ->
paper_accounts.id`` (same-domain, same pattern as paper_orders.account_id).

Soft references — no hard FK:
  - ipo_application_id (Integer): ipo_applications.id is Integer but lives
    in a separate domain; we mirror paper_orders.workflow_id's soft-ref
    pattern so the cross-domain link is value-only.
  - conversation_id (String): conversations.id is uuid in prod but plain
    String in the SQLite test DB. Same gotcha 0014 documents.
  - workflow_id (String(36)): not a hard FK for parity with ForwardIdea
    /paper_orders — prod workflows.id is uuid which doesn't FK cleanly
    across dialects.

DEPLOY ORDER: FKs users.id (a base table from 0001 / create_all) and
paper_accounts.id (from 0013). On a brand-new Postgres DB this means base
ORM tables must exist + 0013 must have applied before reaching 0015.

Revision ID: 0015_paper_ipo_allocation
Revises: 0014_ipo_applications
Create Date: 2026-06-02
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0015_paper_ipo_allocation"
down_revision: Union[str, None] = "0014_ipo_applications"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _ts(bind):
    """Dialect-aware server default for timestamp columns (mirrors 0013/0014)."""
    return (
        sa.text("now()") if bind.dialect.name == "postgresql"
        else sa.text("CURRENT_TIMESTAMP")
    )


def upgrade() -> None:
    bind = op.get_bind()
    TS = _ts(bind)

    op.create_table(
        "paper_ipo_allocations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "user_id", sa.Integer(), sa.ForeignKey("users.id"),
            nullable=False,
        ),
        sa.Column(
            "paper_account_id", sa.String(36),
            sa.ForeignKey("paper_accounts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        # SOFT reference (no FK) — see header.
        sa.Column("ipo_application_id", sa.Integer(), nullable=True),
        sa.Column("ipo_symbol", sa.String(50), nullable=False),
        sa.Column("ipo_name", sa.String(200), nullable=True),
        sa.Column("ipo_type", sa.String(16), nullable=False),
        sa.Column("lots_applied", sa.Integer(), nullable=False),
        sa.Column("quantity_applied", sa.Integer(), nullable=False),
        sa.Column("amount_applied", sa.Numeric(18, 4), nullable=False),
        sa.Column("issue_price", sa.Numeric(18, 4), nullable=False),
        sa.Column(
            "quantity_allotted", sa.Integer(), nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "allotment_status", sa.String(16), nullable=False,
            server_default=sa.text("'pending'"),
        ),
        sa.Column("allotment_date", sa.Date(), nullable=True),
        # P3.1 placeholders — NULL in P3.
        sa.Column("listing_date", sa.Date(), nullable=True),
        sa.Column("listing_price", sa.Numeric(18, 4), nullable=True),
        sa.Column("simulated_pnl", sa.Numeric(18, 4), nullable=True),
        # SOFT references — no FK. See file header.
        sa.Column("conversation_id", sa.String(64), nullable=True),
        sa.Column("workflow_id", sa.String(36), nullable=True),
        sa.Column("source", sa.String(50), nullable=False),
        sa.Column(
            "simulated", sa.Boolean(), nullable=False,
            server_default=sa.text("true"),
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            nullable=False, server_default=TS,
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True),
            nullable=False, server_default=TS,
        ),
        sa.CheckConstraint(
            "allotment_status IN ('allotted', 'not_allotted', 'pending')",
            name="ck_paper_ipo_allocations_status",
        ),
    )
    op.create_index(
        "ix_paper_ipo_allocations_user_id",
        "paper_ipo_allocations", ["user_id"],
    )
    op.create_index(
        "ix_paper_ipo_allocations_paper_account_id",
        "paper_ipo_allocations", ["paper_account_id"],
    )
    op.create_index(
        "ix_paper_ipo_allocations_ipo_symbol",
        "paper_ipo_allocations", ["ipo_symbol"],
    )
    op.create_index(
        "ix_paper_ipo_allocations_user_symbol",
        "paper_ipo_allocations", ["user_id", "ipo_symbol"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_paper_ipo_allocations_user_symbol",
        table_name="paper_ipo_allocations",
    )
    op.drop_index(
        "ix_paper_ipo_allocations_ipo_symbol",
        table_name="paper_ipo_allocations",
    )
    op.drop_index(
        "ix_paper_ipo_allocations_paper_account_id",
        table_name="paper_ipo_allocations",
    )
    op.drop_index(
        "ix_paper_ipo_allocations_user_id",
        table_name="paper_ipo_allocations",
    )
    op.drop_table("paper_ipo_allocations")
