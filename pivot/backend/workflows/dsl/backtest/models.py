"""ORM model for the dsl_backtest_runs table.

Mirrors migration 0011_dsl_backtest_runs.py. Same dual-dialect
conventions as the workflow ORM (String(36) UUID PKs,
SQLAlchemy JSON column type which renders as JSONB on Postgres
and JSON on SQLite).
"""
from __future__ import annotations

import uuid as _uuid

from sqlalchemy import (
    CheckConstraint,
    Column,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    JSON,
    String,
    Text,
)
from sqlalchemy.sql import func

from backend.database import Base


def _uuid_str() -> str:
    return str(_uuid.uuid4())


# Allowed values for the ``status`` column. Kept as a frozenset so
# callers can validate before INSERT without re-importing the
# constraint.
RUN_STATUSES: frozenset[str] = frozenset(
    {"running", "succeeded", "failed", "cancelled"}
)


class DslBacktestRun(Base):
    """One backtest invocation. Tree + request + result all live in
    this row — no join table needed."""

    __tablename__ = "dsl_backtest_runs"

    id = Column(String(36), primary_key=True, default=_uuid_str)
    user_id = Column(
        Integer, ForeignKey("users.id"), nullable=False, index=True,
    )
    tree = Column(JSON, nullable=False)
    request = Column(JSON, nullable=False)
    result = Column(JSON, nullable=True)
    tree_summary = Column(Text, nullable=False)
    primary_symbol = Column(String(32), nullable=False, index=True)
    start_date = Column(Date, nullable=False)
    end_date = Column(Date, nullable=False)
    status = Column(String(16), nullable=False, default="running")
    error_message = Column(Text, nullable=True)
    started_at = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
    )
    finished_at = Column(DateTime(timezone=True), nullable=True)
    total_return_pct = Column(Float, nullable=True)
    total_trades = Column(Integer, nullable=True)

    __table_args__ = (
        CheckConstraint(
            "status IN ('running', 'succeeded', 'failed', 'cancelled')",
            name="ck_dsl_backtest_runs_status",
        ),
    )
