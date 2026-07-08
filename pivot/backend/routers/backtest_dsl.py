"""DSL-tree backtester HTTP surface.

  POST   /api/backtest/dsl/run         run a backtest, optionally persist
  GET    /api/backtest/dsl/runs        list this user's runs (newest first)
  GET    /api/backtest/dsl/runs/{id}   one run with the full result
  POST   /api/backtest/dsl/runs/{id}/cancel  soft-cancel an in-flight run

The new namespace is parallel to the legacy ``/backtest/*`` paths —
that engine and contract stay untouched.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Path
from pydantic import TypeAdapter, ValidationError
from sqlalchemy.orm import Session

from backend.database import get_db
from backend.routers._deps import require_user
from backend.workflows.dsl.backtest import persistence
from backend.workflows.dsl.backtest.engine import run_backtest
from backend.workflows.dsl.backtest.schema import (
    BacktestRequest,
    BacktestResult,
    RunListItem,
    RunListResponse,
)
from backend.workflows.dsl.readback import tree_to_english
from backend.workflows.dsl.schema import Tree
from backend.workflows.dsl.validators import DSLValidationError, semantic_validate

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/backtest/dsl", tags=["BacktestDSL"])


_TREE_ADAPTER = TypeAdapter(Tree)


# ── POST /run ───────────────────────────────────────────────────────


@router.post(
    "/run",
    response_model=BacktestResult,
    summary="Run a DSL-tree backtest (synchronous)",
    description=(
        "Validates the tree (Pydantic + semantic), loads OHLCV for every "
        "symbol the tree references, and runs the engine. When "
        "request.save=true the result is also persisted to "
        "dsl_backtest_runs."
    ),
)
async def run(
    payload: BacktestRequest,
    user_id: int = Depends(require_user),
    db: Session = Depends(get_db),
) -> BacktestResult:
    # ── Validate the tree before we even consider running the engine.
    #    Same validators the registry uses on workflow activate, so
    #    the error envelope matches what the chat layer already knows.
    try:
        tree = _TREE_ADAPTER.validate_python(payload.tree)
    except ValidationError as exc:
        first = exc.errors()[0] if exc.errors() else {"msg": str(exc)}
        raise HTTPException(
            status_code=422,
            detail=f"tree parse failed: {first.get('msg')} at "
                   f"{'/'.join(str(p) for p in first.get('loc', []))}",
        )
    try:
        semantic_validate(tree)
    except DSLValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    summary = tree_to_english(tree)

    # ── Persist a 'running' row before kicking the engine so a crash
    #    mid-run still leaves an audit trail.
    saved_row = (
        persistence.save_run(
            db, user_id=user_id, request=payload, tree_summary=summary,
        )
        if payload.save else None
    )
    if saved_row is not None:
        db.commit()
        db.refresh(saved_row)

    # ── Run the engine on a worker thread — the loop is blocking
    #    (pandas + yfinance) and we don't want to occupy the event
    #    loop.
    try:
        result = await asyncio.to_thread(
            run_backtest, request=payload, user_id=user_id, fetcher=None,
        )
    except ValueError as exc:
        if saved_row is not None:
            persistence.finalise_failed(
                db, row=saved_row, error_message=str(exc),
            )
            db.commit()
        raise HTTPException(status_code=422, detail=str(exc))
    except Exception as exc:  # noqa: BLE001 — turn any engine crash into a clean 500
        logger.exception("[backtest.dsl] engine crashed: %s", exc)
        if saved_row is not None:
            persistence.finalise_failed(
                db, row=saved_row,
                error_message=f"{type(exc).__name__}: {exc}",
            )
            db.commit()
        raise HTTPException(status_code=500, detail="backtest engine failed")

    if saved_row is not None:
        persistence.finalise_succeeded(db, row=saved_row, result=result)
        # Surface the persisted id on the response so the FE can
        # link to the audit page.
        result_with_id = result.model_copy(update={"request_id": str(saved_row.id)})
        db.commit()
        return result_with_id

    return result


# ── POST /validate (walk-forward + no-skill permutation) ─────────────


class ValidateRequest(BacktestRequest):
    n_perm: int = 200
    n_folds: int = 4
    warmup: int = 200


@router.post(
    "/validate",
    summary="Deep validation: walk-forward + no-skill permutation test",
    description=(
        "Re-runs the single-symbol tree strategy many times to answer two "
        "questions in-sample metrics can't: (1) does it hold up out-of-sample "
        "across sequential walk-forward folds (warmup-padded so indicators stay "
        "warm), and (2) does it beat a no-skill null — the same strategy on "
        "shuffled returns (a Monte-Carlo permutation p-value). EXPENSIVE: "
        "n_perm + n_folds engine re-runs."
    ),
)
async def validate(
    payload: ValidateRequest,
    user_id: int = Depends(require_user),
) -> dict:
    try:
        tree = _TREE_ADAPTER.validate_python(payload.tree)
        semantic_validate(tree)
    except (ValidationError, DSLValidationError) as exc:
        raise HTTPException(status_code=422, detail=f"tree invalid: {exc}")

    from backend.backtester.engine import _fetch_ohlcv
    from backend.services.backtest.validation.walkforward import deep_validate_engine2b

    try:
        bars = await asyncio.to_thread(
            _fetch_ohlcv, payload.primary_symbol, payload.start_date, payload.end_date,
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"price fetch failed: {exc}")
    if bars is None or bars.empty:
        raise HTTPException(status_code=400, detail=f"no bars for {payload.primary_symbol}")
    bars = bars.rename(columns={c: str(c).lower() for c in bars.columns})
    if len(bars) < payload.warmup + 2 * payload.n_folds:
        raise HTTPException(
            status_code=400,
            detail=f"need >= {payload.warmup + 2 * payload.n_folds} bars for warmup "
                   f"{payload.warmup} + {payload.n_folds} folds; got {len(bars)}.",
        )

    out = await asyncio.to_thread(
        deep_validate_engine2b,
        tree=payload.tree, primary_symbol=payload.primary_symbol, bars=bars,
        exit_policy=payload.exit_policy,
        starting_capital=payload.starting_capital, quantity=payload.quantity,
        n_perm=payload.n_perm, n_folds=payload.n_folds, warmup=payload.warmup,
    )
    return out


# ── GET /runs (list) ─────────────────────────────────────────────────


@router.get(
    "/runs",
    response_model=RunListResponse,
    summary="List the current user's backtest runs (newest first)",
)
async def list_runs(
    user_id: int = Depends(require_user),
    db: Session = Depends(get_db),
) -> RunListResponse:
    rows = persistence.list_user_runs(db, user_id=user_id)
    items: list[RunListItem] = []
    for r in rows:
        items.append(RunListItem(
            id=r.id,
            primary_symbol=r.primary_symbol,
            start_date=r.start_date,
            end_date=r.end_date,
            tree_summary=r.tree_summary,
            status=r.status,  # type: ignore[arg-type]
            total_return_pct=r.total_return_pct,
            total_trades=r.total_trades,
            started_at=r.started_at,
            finished_at=r.finished_at,
        ))
    return RunListResponse(runs=items)


# ── GET /runs/{id} ──────────────────────────────────────────────────


@router.get(
    "/runs/{run_id}",
    summary="Fetch a single run with its full result JSON",
)
async def get_run(
    run_id: str = Path(..., min_length=1, max_length=64),
    user_id: int = Depends(require_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    row = persistence.get_run_for_user(db, run_id=run_id, user_id=user_id)
    if row is None:
        raise HTTPException(status_code=404, detail="run not found")
    return {
        "id": row.id,
        "status": row.status,
        "tree_summary": row.tree_summary,
        "started_at": row.started_at,
        "finished_at": row.finished_at,
        "request": row.request,
        "result": row.result,
        "error_message": row.error_message,
    }


# ── POST /runs/{id}/cancel ─────────────────────────────────────────


@router.post(
    "/runs/{run_id}/cancel",
    summary="Soft-cancel a run (idempotent on terminal states)",
)
async def cancel_run(
    run_id: str = Path(..., min_length=1, max_length=64),
    user_id: int = Depends(require_user),
    db: Session = Depends(get_db),
) -> dict[str, str]:
    row = persistence.cancel_run(db, run_id=run_id, user_id=user_id)
    if row is None:
        raise HTTPException(status_code=404, detail="run not found")
    db.commit()
    return {"id": row.id, "status": row.status}
