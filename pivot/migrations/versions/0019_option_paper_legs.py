"""F&O P2: option legs in the paper book + portfolio-Greeks snapshots.

Additive only:
  paper_orders.option_strategy_id   VARCHAR(36) NULL + index
      Soft ref to option_strategies.id — one PaperOrder per LEG of a
      multi-leg strategy. Idempotency key shape:
      "optstrat:{option_strategy_id}:leg{n}".
  paper_positions.is_option         BOOLEAN NOT NULL DEFAULT FALSE
  paper_positions.segment           VARCHAR(16) NULL
      Option positions are SIGNED (short legs go negative). The equity
      fill engine still clamps >= 0 — only paper/options_routing writes
      negative quantities, and only on is_option rows.
  paper_fills.iv_at_fill            FLOAT NULL
      IV at fill from the chain solve — P&L attribution input.
  paper_greeks_snapshots            NEW TABLE
      Daily (account, date) net Greeks + FutEq delta-notional snapshot,
      written at close alongside the NAV snapshot.

NO data backfill: all defaults are FALSE/NULL and every pre-existing row
is an equity row by definition.

DEPLOY ORDER: chains off 0018_option_strategies (the soft ref's target
table should exist for sanity, though nothing enforces it at DDL level).

Revision ID: 0019_option_paper_legs
Revises: 0018_option_strategies
Create Date: 2026-06-04
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0019_option_paper_legs"
down_revision: Union[str, None] = "0018_option_strategies"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "paper_orders",
        sa.Column("option_strategy_id", sa.String(36), nullable=True),
    )
    op.create_index(
        "ix_paper_orders_option_strategy_id",
        "paper_orders", ["option_strategy_id"],
    )
    op.add_column(
        "paper_positions",
        sa.Column(
            "is_option", sa.Boolean(), nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.add_column(
        "paper_positions",
        sa.Column("segment", sa.String(16), nullable=True),
    )
    op.add_column(
        "paper_fills",
        sa.Column("iv_at_fill", sa.Float(), nullable=True),
    )

    op.create_table(
        "paper_greeks_snapshots",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "account_id", sa.String(36),
            sa.ForeignKey("paper_accounts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id", sa.Integer(), sa.ForeignKey("users.id"),
            nullable=False,
        ),
        sa.Column("as_of", sa.Date(), nullable=False),
        sa.Column(
            "net_delta", sa.Float(), nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "net_gamma", sa.Float(), nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "net_theta", sa.Float(), nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "net_vega", sa.Float(), nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column("delta_notional", sa.Numeric(18, 2), nullable=True),
        sa.Column(
            "position_count", sa.Integer(), nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column("breakdown_json", sa.JSON(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
        sa.UniqueConstraint(
            "account_id", "as_of",
            name="uq_paper_greeks_snapshots_account_asof",
        ),
    )
    op.create_index(
        "ix_paper_greeks_snapshots_account_id",
        "paper_greeks_snapshots", ["account_id"],
    )
    op.create_index(
        "ix_paper_greeks_snapshots_user_id",
        "paper_greeks_snapshots", ["user_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_paper_greeks_snapshots_user_id", "paper_greeks_snapshots",
    )
    op.drop_index(
        "ix_paper_greeks_snapshots_account_id", "paper_greeks_snapshots",
    )
    op.drop_table("paper_greeks_snapshots")
    op.drop_column("paper_fills", "iv_at_fill")
    op.drop_column("paper_positions", "segment")
    op.drop_column("paper_positions", "is_option")
    op.drop_index("ix_paper_orders_option_strategy_id", "paper_orders")
    op.drop_column("paper_orders", "option_strategy_id")
