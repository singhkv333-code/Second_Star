"""Paper trading & forward-testing — simulated-broker portfolio.

Eight additive tables. No ALTER on any existing table. Mirrors the ORM
models added to backend/models.py (the §"Paper Trading & Forward-Testing"
block) 1:1, the same way 0011_dsl_backtest_runs mirrors DslBacktestRun.

  paper_accounts            one simulated book per user (cash ledger head)
  forward_ideas             the forward-test unit (idea attribution)
  paper_orders              order lifecycle incl. resting LIMIT/GTT/SL/TP
  paper_fills               immutable executions — source of truth
  paper_positions           derived open-lot cache (unique account+symbol)
  paper_ledger              append-only cash transactions
  paper_nav_snapshots       account-grain daily equity curve
  paper_idea_nav_snapshots  idea-grain daily curve (scorecard series)

Tables are created in FK-dependency order (forward_ideas before
paper_orders/paper_fills, which reference it). create_all topo-sorts for
the SQLite test DB; a hand-written migration must order them explicitly
so Postgres CREATE TABLE doesn't reference a not-yet-created table.

Cross-dialect notes (same as 0011): String(36) UUID PKs; enum-like
columns are String + CheckConstraint (not native PG ENUM); scorecard_cache
renders JSONB on Postgres / JSON on SQLite; timestamp server defaults are
dialect-aware. Only columns whose ORM definition carries
server_default=func.now() get a DB default here — the rest use Python-side
defaults (no DDL default), matching Base.metadata.create_all in tests.

forward_ideas.backtest_run_id is a SOFT reference (plain String, no FK):
the dsl_backtest_runs table is owned by a model outside backend.models.

Reconciled-money columns are Numeric(18,4) (paise precision, crore
headroom); market prices (fill_price/last_price/limit/trigger/intended/
nifty_close) and ratios (slippage_bps) stay Float. See the models.py
header for the rationale.

DEPLOY ORDER: like every migration since 0001 (which FKs users.id), this
revision FKs base tables it does NOT create — users, strategies,
conversations, trade_logs. 0013 is the first to reference the latter
three. The deploy contract is therefore: create the base ORM tables via
Base.metadata.create_all (or the prior chain), THEN `alembic upgrade
head`. On a brand-new Postgres DB with no create_all first, 0013's FKs
to strategies/conversations/trade_logs would fail.

Revision ID: 0013_paper_trading
Revises: 0012_workflow_expires_at
Create Date: 2026-05-30
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "0013_paper_trading"
down_revision: Union[str, None] = "0012_workflow_expires_at"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _json_type(bind):
    if bind.dialect.name == "postgresql":
        return postgresql.JSONB(astext_type=sa.Text())
    return sa.JSON()


def _ts(bind):
    """Dialect-aware server default for timestamp columns (mirrors 0011)."""
    return (
        sa.text("now()") if bind.dialect.name == "postgresql"
        else sa.text("CURRENT_TIMESTAMP")
    )


def upgrade() -> None:
    bind = op.get_bind()
    JSON_T = _json_type(bind)
    TS = _ts(bind)

    # ── paper_accounts ────────────────────────────────────────────────
    op.create_table(
        "paper_accounts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "user_id", sa.Integer(), sa.ForeignKey("users.id"),
            nullable=False,
        ),
        sa.Column("label", sa.String(64), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("starting_capital", sa.Numeric(18, 4), nullable=False),
        sa.Column("cash_settled", sa.Numeric(18, 4), nullable=False),
        sa.Column("cash_available", sa.Numeric(18, 4), nullable=False),
        sa.Column("cash_reserved", sa.Numeric(18, 4), nullable=False),
        sa.Column("mode", sa.String(8), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            nullable=False, server_default=TS,
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True),
            nullable=False, server_default=TS,
        ),
        sa.CheckConstraint(
            "mode IN ('paper', 'live')", name="ck_paper_accounts_mode",
        ),
    )
    op.create_index(
        "ix_paper_accounts_user_id", "paper_accounts", ["user_id"],
        unique=True,
    )

    # ── forward_ideas (before paper_orders/paper_fills which FK it) ────
    op.create_table(
        "forward_ideas",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "user_id", sa.Integer(), sa.ForeignKey("users.id"),
            nullable=False,
        ),
        sa.Column(
            "account_id", sa.String(36),
            sa.ForeignKey("paper_accounts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("origin_kind", sa.String(16), nullable=False),
        sa.Column(
            "workflow_id", sa.String(36),
            sa.ForeignKey("workflows.id"), nullable=True,
        ),
        sa.Column(
            "conversation_id", sa.String(36),
            sa.ForeignKey("conversations.id"), nullable=True,
        ),
        sa.Column(
            "strategy_id", sa.Integer(),
            sa.ForeignKey("strategies.id"), nullable=True,
        ),
        sa.Column("label", sa.String(140), nullable=False),
        sa.Column("inception_date", sa.Date(), nullable=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("status_changed_at", sa.DateTime(timezone=True), nullable=True),
        # SOFT reference to dsl_backtest_runs.id — intentionally NO FK.
        sa.Column("backtest_run_id", sa.String(36), nullable=True),
        sa.Column("cohort_trial_count", sa.Integer(), nullable=False),
        sa.Column("scorecard_cache", JSON_T, nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            nullable=False, server_default=TS,
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True),
            nullable=False, server_default=TS,
        ),
        sa.CheckConstraint(
            "status IN ('paper', 'candidate', 'promoted', 'retired')",
            name="ck_forward_ideas_status",
        ),
    )
    op.create_index("ix_forward_ideas_user_id", "forward_ideas", ["user_id"])
    op.create_index("ix_forward_ideas_account_id", "forward_ideas", ["account_id"])
    op.create_index("ix_forward_ideas_workflow_id", "forward_ideas", ["workflow_id"])

    # ── paper_orders ──────────────────────────────────────────────────
    op.create_table(
        "paper_orders",
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
        sa.Column("client_request_id", sa.String(120), nullable=True),
        sa.Column("symbol", sa.String(50), nullable=False),
        sa.Column("exchange", sa.String(10), nullable=False),
        sa.Column("transaction_type", sa.String(10), nullable=False),
        sa.Column("order_type", sa.String(16), nullable=False),
        sa.Column("product", sa.String(8), nullable=False),
        sa.Column("variety", sa.String(16), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("limit_price", sa.Float(), nullable=True),
        sa.Column("trigger_price", sa.Float(), nullable=True),
        sa.Column("intended_price", sa.Float(), nullable=True),
        sa.Column("intended_quote_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("reserved_cash", sa.Numeric(18, 4), nullable=False),
        sa.Column("filled_quantity", sa.Integer(), nullable=False),
        sa.Column("reject_reason", sa.String(200), nullable=True),
        sa.Column("gtt_oco_group", sa.String(36), nullable=True),
        sa.Column(
            "parent_order_id", sa.String(36),
            sa.ForeignKey("paper_orders.id"), nullable=True,
        ),
        sa.Column("source", sa.String(50), nullable=True),
        sa.Column("origin_kind", sa.String(16), nullable=True),
        sa.Column(
            "workflow_id", sa.String(36),
            sa.ForeignKey("workflows.id"), nullable=True,
        ),
        sa.Column(
            "workflow_run_id", sa.String(36),
            sa.ForeignKey("workflow_runs.id"), nullable=True,
        ),
        sa.Column(
            "conversation_id", sa.String(36),
            sa.ForeignKey("conversations.id"), nullable=True,
        ),
        sa.Column(
            "strategy_id", sa.Integer(),
            sa.ForeignKey("strategies.id"), nullable=True,
        ),
        sa.Column(
            "idea_id", sa.String(36),
            sa.ForeignKey("forward_ideas.id"), nullable=True,
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
            "status IN ('pending', 'queued', 'resting', "
            "'partially_filled', 'filled', 'cancelled', 'rejected')",
            name="ck_paper_orders_status",
        ),
    )
    op.create_index(
        "ix_paper_orders_client_request_id", "paper_orders",
        ["client_request_id"], unique=True,
    )
    op.create_index("ix_paper_orders_account_id", "paper_orders", ["account_id"])
    op.create_index("ix_paper_orders_user_id", "paper_orders", ["user_id"])
    op.create_index("ix_paper_orders_symbol", "paper_orders", ["symbol"])
    op.create_index("ix_paper_orders_status", "paper_orders", ["status"])
    op.create_index("ix_paper_orders_gtt_oco_group", "paper_orders", ["gtt_oco_group"])
    op.create_index("ix_paper_orders_source", "paper_orders", ["source"])
    op.create_index("ix_paper_orders_idea_id", "paper_orders", ["idea_id"])
    # Hot path: resting-order drain + open-orders blotter filter by
    # (account_id, status).
    op.create_index(
        "ix_paper_orders_account_status", "paper_orders",
        ["account_id", "status"],
    )

    # ── paper_fills ───────────────────────────────────────────────────
    op.create_table(
        "paper_fills",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "order_id", sa.String(36),
            sa.ForeignKey("paper_orders.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "account_id", sa.String(36),
            sa.ForeignKey("paper_accounts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id", sa.Integer(), sa.ForeignKey("users.id"),
            nullable=False,
        ),
        sa.Column("symbol", sa.String(50), nullable=False),
        sa.Column("transaction_type", sa.String(10), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("fill_price", sa.Float(), nullable=False),
        sa.Column("gross_value", sa.Numeric(18, 4), nullable=False),
        sa.Column("charges", sa.Numeric(18, 4), nullable=False),
        sa.Column("net_cashflow", sa.Numeric(18, 4), nullable=False),
        sa.Column("slippage_bps", sa.Float(), nullable=True),
        sa.Column("realized_pnl", sa.Numeric(18, 4), nullable=True),
        sa.Column("settles_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "idea_id", sa.String(36),
            sa.ForeignKey("forward_ideas.id"), nullable=True,
        ),
        sa.Column(
            "trade_log_id", sa.Integer(),
            sa.ForeignKey("trade_logs.id"), nullable=True,
        ),
        sa.Column(
            "filled_at", sa.DateTime(timezone=True),
            nullable=False, server_default=TS,
        ),
    )
    op.create_index("ix_paper_fills_order_id", "paper_fills", ["order_id"])
    op.create_index("ix_paper_fills_account_id", "paper_fills", ["account_id"])
    op.create_index("ix_paper_fills_user_id", "paper_fills", ["user_id"])
    op.create_index("ix_paper_fills_symbol", "paper_fills", ["symbol"])
    op.create_index("ix_paper_fills_idea_id", "paper_fills", ["idea_id"])
    op.create_index("ix_paper_fills_filled_at", "paper_fills", ["filled_at"])

    # ── paper_positions ───────────────────────────────────────────────
    op.create_table(
        "paper_positions",
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
        sa.Column("symbol", sa.String(50), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("avg_cost", sa.Numeric(18, 4), nullable=False),
        sa.Column("realized_pnl", sa.Numeric(18, 4), nullable=False),
        sa.Column("last_price", sa.Float(), nullable=True),
        sa.Column("last_mark_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("prev_close", sa.Float(), nullable=True),
        sa.Column("stale", sa.Boolean(), nullable=False),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True),
            nullable=False, server_default=TS,
        ),
        sa.UniqueConstraint(
            "account_id", "symbol",
            name="uq_paper_positions_account_symbol",
        ),
    )
    op.create_index("ix_paper_positions_account_id", "paper_positions", ["account_id"])
    op.create_index("ix_paper_positions_user_id", "paper_positions", ["user_id"])

    # ── paper_ledger ──────────────────────────────────────────────────
    op.create_table(
        "paper_ledger",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "account_id", sa.String(36),
            sa.ForeignKey("paper_accounts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "fill_id", sa.String(36),
            sa.ForeignKey("paper_fills.id"), nullable=True,
        ),
        sa.Column("kind", sa.String(24), nullable=False),
        sa.Column("amount", sa.Numeric(18, 4), nullable=False),
        sa.Column("balance_after", sa.Numeric(18, 4), nullable=False),
        sa.Column("note", sa.String(200), nullable=True),
        sa.Column(
            "recorded_at", sa.DateTime(timezone=True),
            nullable=False, server_default=TS,
        ),
        sa.CheckConstraint(
            "kind IN ('seed', 'buy_debit', 'sell_credit', "
            "'reserve', 'release', 'settlement')",
            name="ck_paper_ledger_kind",
        ),
    )
    op.create_index("ix_paper_ledger_account_id", "paper_ledger", ["account_id"])
    op.create_index("ix_paper_ledger_recorded_at", "paper_ledger", ["recorded_at"])

    # ── paper_nav_snapshots ───────────────────────────────────────────
    op.create_table(
        "paper_nav_snapshots",
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
        sa.Column("as_of_date", sa.Date(), nullable=False),
        sa.Column("cash_available", sa.Numeric(18, 4), nullable=False),
        sa.Column("cash_settled", sa.Numeric(18, 4), nullable=False),
        sa.Column("positions_mv", sa.Numeric(18, 4), nullable=False),
        sa.Column("nav", sa.Numeric(18, 4), nullable=False),
        sa.Column("realized_pnl_cum", sa.Numeric(18, 4), nullable=False),
        sa.Column("unrealized_pnl", sa.Numeric(18, 4), nullable=False),
        sa.Column("nifty_close", sa.Float(), nullable=True),
        sa.Column("is_stale", sa.Boolean(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            nullable=False, server_default=TS,
        ),
        sa.UniqueConstraint(
            "account_id", "as_of_date",
            name="uq_paper_nav_snapshots_account_date",
        ),
    )
    op.create_index("ix_paper_nav_snapshots_account_id", "paper_nav_snapshots", ["account_id"])
    op.create_index("ix_paper_nav_snapshots_user_id", "paper_nav_snapshots", ["user_id"])
    op.create_index("ix_paper_nav_snapshots_as_of_date", "paper_nav_snapshots", ["as_of_date"])

    # ── paper_idea_nav_snapshots ──────────────────────────────────────
    op.create_table(
        "paper_idea_nav_snapshots",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "idea_id", sa.String(36),
            sa.ForeignKey("forward_ideas.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "account_id", sa.String(36),
            sa.ForeignKey("paper_accounts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("as_of_date", sa.Date(), nullable=False),
        sa.Column("committed_capital", sa.Numeric(18, 4), nullable=False),
        sa.Column("positions_mv", sa.Numeric(18, 4), nullable=False),
        sa.Column("idea_nav", sa.Numeric(18, 4), nullable=False),
        sa.Column("realized_pnl", sa.Numeric(18, 4), nullable=False),
        sa.Column("unrealized_pnl", sa.Numeric(18, 4), nullable=False),
        sa.Column("nifty_close", sa.Float(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            nullable=False, server_default=TS,
        ),
        sa.UniqueConstraint(
            "idea_id", "as_of_date",
            name="uq_paper_idea_nav_snapshots_idea_date",
        ),
    )
    op.create_index("ix_paper_idea_nav_snapshots_idea_id", "paper_idea_nav_snapshots", ["idea_id"])
    op.create_index("ix_paper_idea_nav_snapshots_account_id", "paper_idea_nav_snapshots", ["account_id"])
    op.create_index("ix_paper_idea_nav_snapshots_as_of_date", "paper_idea_nav_snapshots", ["as_of_date"])


def downgrade() -> None:
    # Drop in reverse FK-dependency order.
    op.drop_table("paper_idea_nav_snapshots")
    op.drop_table("paper_nav_snapshots")
    op.drop_table("paper_ledger")
    op.drop_table("paper_positions")
    op.drop_table("paper_fills")
    op.drop_table("paper_orders")
    op.drop_table("forward_ideas")
    op.drop_table("paper_accounts")
