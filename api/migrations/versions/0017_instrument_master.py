"""F&O P0: instrument master + dynamic option universe.

Two NEW tables, no ALTER on anything existing (additive-only convention).

  instrument_master
      One row per tradable contract from the daily Kite instruments dump
      (NFO-OPT / NFO-FUT / BFO-OPT / MCX-OPT …). THE single source of
      truth for strikes, expiries and LOT SIZES — lot sizes changed
      Dec'25/Jan'26 (NIFTY 75→65, BANKNIFTY 35→30) so hardcoding one
      anywhere is a bug; every consumer reads this table. Repopulated
      daily ~08:35 IST; disappeared contracts keep their row with a
      stale ``last_seen`` (audit/backtest resolution) rather than being
      deleted. PK is Kite's instrument_token (BigInteger, NOT
      autoincrement — it's an exchange-assigned id).

  option_universe
      One row per (underlying, as_of): the liquidity evidence + verdict
      of the dynamic universe selector. ``selected`` rows are surfaced
      in chat; ``research_only`` rows (all MCX-OPT in v1) are quotable
      and screenable but execution-blocked at the registration gate.
      Percentile-based selection — NO hardcoded underlying lists.

NO data backfill: both tables fill on the first scheduler run of
``refresh_instrument_master`` (or the POST /admin/options/refresh
endpoint in dev).

DEPLOY ORDER: chains off 0016_ipo_listing_credit; no cross-table deps.

Revision ID: 0017_instrument_master
Revises: 0016_ipo_listing_credit
Create Date: 2026-06-04
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0017_instrument_master"
down_revision: Union[str, None] = "0016_ipo_listing_credit"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "instrument_master",
        sa.Column(
            "instrument_token", sa.BigInteger(), primary_key=True,
            autoincrement=False,
        ),
        sa.Column("exchange_token", sa.BigInteger(), nullable=True),
        sa.Column("tradingsymbol", sa.String(64), nullable=False),
        sa.Column("name", sa.String(64), nullable=True),
        sa.Column("underlying", sa.String(40), nullable=False),
        sa.Column("exchange", sa.String(8), nullable=False),
        sa.Column("segment", sa.String(16), nullable=False),
        sa.Column("instrument_type", sa.String(4), nullable=False),
        sa.Column("strike", sa.Numeric(14, 4), nullable=True),
        sa.Column("expiry", sa.Date(), nullable=True),
        sa.Column("expiry_kind", sa.String(12), nullable=True),
        sa.Column("lot_size", sa.Integer(), nullable=True),
        sa.Column("tick_size", sa.Float(), nullable=True),
        sa.Column("last_price", sa.Float(), nullable=True),
        sa.Column("first_seen", sa.Date(), nullable=False),
        sa.Column("last_seen", sa.Date(), nullable=False),
        sa.Column("refreshed_on", sa.Date(), nullable=False),
    )
    op.create_index(
        "ix_instrument_master_tradingsymbol",
        "instrument_master", ["tradingsymbol"],
    )
    op.create_index(
        "ix_instrument_master_underlying", "instrument_master", ["underlying"],
    )
    op.create_index("ix_instrument_master_expiry", "instrument_master", ["expiry"])
    op.create_index(
        "ix_instrument_master_last_seen", "instrument_master", ["last_seen"],
    )
    op.create_index(
        "ix_instrument_master_refreshed_on",
        "instrument_master", ["refreshed_on"],
    )
    op.create_index(
        "ix_instrument_master_chain",
        "instrument_master",
        ["underlying", "expiry", "instrument_type", "strike"],
    )
    op.create_index(
        "ix_instrument_master_segment_expiry",
        "instrument_master", ["segment", "expiry"],
    )

    op.create_table(
        "option_universe",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("underlying", sa.String(40), nullable=False),
        sa.Column("as_of", sa.Date(), nullable=False),
        sa.Column("segment", sa.String(16), nullable=False),
        sa.Column("exchange", sa.String(8), nullable=False),
        sa.Column("avg_oi", sa.Float(), nullable=True),
        sa.Column("avg_volume", sa.Float(), nullable=True),
        sa.Column("spread_pct_atm", sa.Float(), nullable=True),
        sa.Column("liquidity_score", sa.Float(), nullable=True),
        sa.Column(
            "selected", sa.Boolean(), nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column(
            "research_only", sa.Boolean(), nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column("reason", sa.String(120), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
        sa.UniqueConstraint(
            "underlying", "as_of", name="uq_option_universe_underlying_asof",
        ),
    )
    op.create_index(
        "ix_option_universe_underlying", "option_universe", ["underlying"],
    )
    op.create_index(
        "ix_option_universe_asof_selected",
        "option_universe", ["as_of", "selected"],
    )


def downgrade() -> None:
    op.drop_index("ix_option_universe_asof_selected", "option_universe")
    op.drop_index("ix_option_universe_underlying", "option_universe")
    op.drop_table("option_universe")
    op.drop_index("ix_instrument_master_segment_expiry", "instrument_master")
    op.drop_index("ix_instrument_master_chain", "instrument_master")
    op.drop_index("ix_instrument_master_refreshed_on", "instrument_master")
    op.drop_index("ix_instrument_master_last_seen", "instrument_master")
    op.drop_index("ix_instrument_master_expiry", "instrument_master")
    op.drop_index("ix_instrument_master_underlying", "instrument_master")
    op.drop_index("ix_instrument_master_tradingsymbol", "instrument_master")
    op.drop_table("instrument_master")
