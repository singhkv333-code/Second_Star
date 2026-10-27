"""Top-level alias routes for the expression backtester.

Day 8 (#51). The Phase 2 frontend brief consumes the backtester at
`/api/backtest/{fields,validate,run}`, but the existing handlers live
at `/api/backtest/expr/{fields,validate,run}` (different scope). This
module mounts the shorter alias paths and delegates to the same
handler callables — zero duplication, no behavioural drift.

We deliberately don't move the `/expr` routes: they're documented and
referenced by other surfaces. The aliases are additive.
"""
from __future__ import annotations

from fastapi import APIRouter, Header

from backend.routers.expr_backtest import (
    RunRequest,
    ValidateRequest,
    list_fields as _list_fields,
    run_expr_backtest as _run_expr_backtest,
    validate_expr as _validate_expr,
)


router = APIRouter(prefix="/api/backtest", tags=["Backtester (alias)"])


@router.get("/fields", summary="Top-level alias for /api/backtest/expr/fields")
def list_fields_alias(authorization: str = Header(None)):
    return _list_fields(authorization)


@router.post(
    "/validate",
    summary="Top-level alias for /api/backtest/expr/validate",
)
def validate_alias(req: ValidateRequest, authorization: str = Header(None)):
    return _validate_expr(req, authorization)


@router.post(
    "/run",
    summary="Top-level alias for /api/backtest/expr/run",
)
async def run_alias(req: RunRequest, authorization: str = Header(None)):
    return await _run_expr_backtest(req, authorization)
