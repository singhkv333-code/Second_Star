"""F&O P1: option strategies + legs (register-not-execute, paper-first).

Two NEW tables, no ALTER on anything existing (additive-only).

  option_strategies
      One row per user-registered multi-leg option strategy INTENT.
      book='paper' rows get auto-executed by the paper broker in P2;
      book='live' rows are REGISTER-NOT-EXECUTE forever — Pivot never
      places a live F&O order. Decision-quad columns (net_premium /
      max_loss / max_profit / pop / capital / margin) are the SERVER's
      recomputation at registration; client numbers are discarded
      (IPO-application pattern). NULL max_loss/max_profit = unlimited.

  option_legs
      Child rows (NOT a JSONB column): each leg becomes an independent
      per-symbol paper position once filled, so legs must be
      addressable rows that P2 paper fills reference via
      client_request_id "optstrat:{strategy_id}:leg{n}".

NO data backfill. DEPLOY ORDER: chains off 0017_instrument_master
(needs nothing from it at DDL level, but the registration path reads
instrument_master at runtime).

Revision ID: 0018_option_strategies
Revises: 0017_instrument_master
Create Date: 2026-06-04
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0018_option_strategies"
down_revision: Union[str, None] = "0017_instrument_master"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "option_strategies",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "user_id", sa.Integer(),
            sa.ForeignKey("users.id"), nullable=False,
        ),
        sa.Column("underlying", sa.String(40), nullable=False),
        sa.Column("segment", sa.String(16), nullable=False),
        sa.Column("exchange", sa.String(8), nullable=False),
        sa.Column("template", sa.String(40), nullable=False),
        sa.Column("expiry", sa.Date(), nullable=False),
        sa.Column(
            "book", sa.String(8), nullable=False,
            server_default=sa.text("'paper'"),
        ),
        sa.Column(
            "status", sa.String(16), nullable=False,
            server_default=sa.text("'registered'"),
        ),
        sa.Column(
            "qty_lots", sa.Integer(), nullable=False,
            server_default=sa.text("1"),
        ),
        sa.Column("lot_size", sa.Integer(), nullable=False),
        sa.Column("net_premium", sa.Numeric(18, 4), nullable=True),
        sa.Column("max_loss", sa.Numeric(18, 4), nullable=True),
        sa.Column("max_profit", sa.Numeric(18, 4), nullable=True),
        sa.Column("pop", sa.Float(), nullable=True),
        sa.Column("capital_required", sa.Numeric(18, 4), nullable=True),
        sa.Column("margin_estimate", sa.Numeric(18, 4), nullable=True),
        sa.Column("net_greeks_json", sa.JSON(), nullable=True),
        sa.Column("critique_verdict", sa.String(12), nullable=True),
        sa.Column("conversation_id", sa.String(64), nullable=True),
        sa.Column("workflow_id", sa.String(36), nullable=True),
        sa.Column("source", sa.String(50), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
        sa.CheckConstraint(
            "book IN ('paper', 'live')",
            name="ck_option_strategies_book",
        ),
        sa.CheckConstraint(
            "status IN ('registered', 'withdrawn', 'intent_armed', "
            "'active', 'closed', 'rejected', 'blocked')",
            name="ck_option_strategies_status",
        ),
    )
    op.create_index(
        "ix_option_strategies_user_id", "option_strategies", ["user_id"],
    )
    op.create_index(
        "ix_option_strategies_underlying", "option_strategies", ["underlying"],
    )
    op.create_index(
        "ix_option_strategies_user_status",
        "option_strategies", ["user_id", "status"],
    )

    op.create_table(
        "option_legs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "strategy_id", sa.String(36),
            sa.ForeignKey("option_strategies.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "leg_index", sa.Integer(), nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column("instrument_token", sa.BigInteger(), nullable=True),
        sa.Column("tradingsymbol", sa.String(64), nullable=True),
        sa.Column("option_type", sa.String(2), nullable=False),
        sa.Column("side", sa.String(4), nullable=False),
        sa.Column("strike", sa.Numeric(14, 4), nullable=False),
        sa.Column(
            "qty_lots", sa.Integer(), nullable=False,
            server_default=sa.text("1"),
        ),
        sa.Column("lot_size", sa.Integer(), nullable=False),
        sa.Column("entry_mid", sa.Float(), nullable=True),
        sa.Column("entry_iv", sa.Float(), nullable=True),
        sa.Column("entry_delta", sa.Float(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
        sa.CheckConstraint(
            "option_type IN ('CE', 'PE')", name="ck_option_legs_type",
        ),
        sa.CheckConstraint(
            "side IN ('BUY', 'SELL')", name="ck_option_legs_side",
        ),
    )
    op.create_index("ix_option_legs_strategy_id", "option_legs", ["strategy_id"])


def downgrade() -> None:
    op.drop_index("ix_option_legs_strategy_id", "option_legs")
    op.drop_table("option_legs")
    op.drop_index("ix_option_strategies_user_status", "option_strategies")
    op.drop_index("ix_option_strategies_underlying", "option_strategies")
    op.drop_index("ix_option_strategies_user_id", "option_strategies")
    op.drop_table("option_strategies")
