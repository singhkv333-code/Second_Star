"""Save / load / list / cancel for DslBacktestRun rows.

All helpers take a Session — the router opens one per request via
``Depends(get_db)``. Cross-user access returns ``None`` (the router
maps that to 404 — the Agent System convention).
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy.orm import Session

from backend.workflows.dsl.backtest.models import DslBacktestRun
from backend.workflows.dsl.backtest.schema import BacktestRequest, BacktestResult

logger = logging.getLogger(__name__)


def save_run(
    db: Session,
    *,
    user_id: int,
    request: BacktestRequest,
    tree_summary: str,
) -> DslBacktestRun:
    """Insert a fresh row in status='running'. Returns the row.
    Caller updates result + status when the engine returns."""
    row = DslBacktestRun(
        user_id=user_id,
        tree=request.tree,
        request=request.model_dump(mode="json"),
        result=None,
        tree_summary=tree_summary,
        primary_symbol=request.primary_symbol,
        start_date=request.start_date,
        end_date=request.end_date,
        status="running",
    )
    db.add(row)
    db.flush()
    return row


def finalise_succeeded(
    db: Session, *, row: DslBacktestRun, result: BacktestResult,
) -> DslBacktestRun:
    row.result = result.model_dump(mode="json")
    row.status = "succeeded"
    row.finished_at = datetime.now(timezone.utc)
    row.total_return_pct = float(result.metrics.total_return_pct)
    row.total_trades = int(result.metrics.total_trades)
    db.flush()
    return row


def finalise_failed(
    db: Session, *, row: DslBacktestRun, error_message: str,
) -> DslBacktestRun:
    row.status = "failed"
    row.error_message = str(error_message)[:2000]
    row.finished_at = datetime.now(timezone.utc)
    db.flush()
    return row


def get_run_for_user(
    db: Session, *, run_id: str, user_id: int,
) -> Optional[DslBacktestRun]:
    row = db.query(DslBacktestRun).filter(DslBacktestRun.id == run_id).first()
    if row is None or int(row.user_id) != int(user_id):
        return None
    return row


def list_user_runs(
    db: Session, *, user_id: int, limit: int = 50,
) -> list[DslBacktestRun]:
    return (
        db.query(DslBacktestRun)
        .filter(DslBacktestRun.user_id == user_id)
        .order_by(DslBacktestRun.started_at.desc())
        .limit(max(1, min(int(limit), 200)))
        .all()
    )


def cancel_run(
    db: Session, *, run_id: str, user_id: int,
) -> Optional[DslBacktestRun]:
    row = get_run_for_user(db, run_id=run_id, user_id=user_id)
    if row is None:
        return None
    if row.status in {"succeeded", "failed", "cancelled"}:
        return row   # idempotent on terminal states
    row.status = "cancelled"
    row.finished_at = datetime.now(timezone.utc)
    db.flush()
    return row
