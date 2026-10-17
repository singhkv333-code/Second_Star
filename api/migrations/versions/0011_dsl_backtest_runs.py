"""DSL backtester — Phase B run persistence.

One additive table. No ALTER on any existing table.

  dsl_backtest_runs
    id                      String(36)   PK (UUID v4)
    user_id                 Integer      FK → users.id, indexed
    tree                    JSON/JSONB   the tree the engine ran
    request                 JSON/JSONB   the full BacktestRequest
    result                  JSON/JSONB   nullable; populated on success
    tree_summary            Text         tree_to_english(tree) for list views
    primary_symbol          String(32)   indexed (filter by symbol)
    start_date              Date
    end_date                Date
    status                  String(16)   running | succeeded | failed | cancelled
    error_message           Text         populated on failed
    started_at              TIMESTAMPTZ
    finished_at             TIMESTAMPTZ  nullable
    total_return_pct        Float        nullable (list-view convenience copy)
    total_trades            Integer      nullable (list-view convenience copy)

Storage caveat: the ``result`` column can be sizeable for long
backtests (5-year daily curves ≈ 1,300 EquityPoint rows + trade
list). The plan caps each row at ~50 KB JSON; the engine doesn't
enforce this today — Phase B+1 adds compression / equity-curve
downsampling.

Revision ID: 0011_dsl_backtest_runs
Revises: 0010_news_retraction_tracking
Create Date: 2026-05-22
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "0011_dsl_backtest_runs"
down_revision: Union[str, None] = "0010_news_retraction_tracking"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _json_type(bind):
    if bind.dialect.name == "postgresql":
        return postgresql.JSONB(astext_type=sa.Text())
    return sa.JSON()


def upgrade() -> None:
    bind = op.get_bind()
    JSON_T = _json_type(bind)

    op.create_table(
        "dsl_backtest_runs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "user_id", sa.Integer(), sa.ForeignKey("users.id"),
            nullable=False, index=True,
        ),
        sa.Column("tree", JSON_T, nullable=False),
        sa.Column("request", JSON_T, nullable=False),
        sa.Column("result", JSON_T, nullable=True),
        sa.Column("tree_summary", sa.Text(), nullable=False),
        sa.Column(
            "primary_symbol", sa.String(32), nullable=False, index=True,
        ),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=False),
        sa.Column(
            "status", sa.String(16), nullable=False,
            server_default=sa.text("'running'"),
        ),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column(
            "started_at", sa.DateTime(timezone=True),
            nullable=False,
            server_default=(
                sa.text("now()") if bind.dialect.name == "postgresql"
                else sa.text("CURRENT_TIMESTAMP")
            ),
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("total_return_pct", sa.Float(), nullable=True),
        sa.Column("total_trades", sa.Integer(), nullable=True),
        sa.CheckConstraint(
            "status IN ('running', 'succeeded', 'failed', 'cancelled')",
            name="ck_dsl_backtest_runs_status",
        ),
    )
    op.create_index(
        "ix_dsl_backtest_runs_user_started",
        "dsl_backtest_runs",
        ["user_id", sa.text("started_at DESC")],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_dsl_backtest_runs_user_started",
        table_name="dsl_backtest_runs",
    )
    op.drop_table("dsl_backtest_runs")
