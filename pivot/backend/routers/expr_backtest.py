"""Expression-based fundamentals backtester / screener.

Wraps the `pivot-backtester` package so the chat (and any direct API caller)
can run flexible point-in-time queries over the financials DB.

Endpoints
---------
GET  /api/backtest/expr/fields              — list available fields
POST /api/backtest/expr/validate            — validate an expression, no DB hit
POST /api/backtest/expr/screen              — universe at a single date (fast — no engine)
POST /api/backtest/expr/run                 — full backtest with equity curve + metrics

Auth: same Bearer-token pattern as the rest of the chat surface.
"""
from __future__ import annotations

import asyncpg
from datetime import date as _date
from typing import Optional

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

from backend.auth.jwt_handler import get_user_id_from_token
from backend.config import settings


router = APIRouter(prefix="/api/backtest/expr", tags=["Expression backtester"])


# ---- Helpers -----------------------------------------------------------


def _financials_dsn() -> str:
    """Derive the financials DB DSN from the maintenance DSN.

    Pivot's ``settings.database_url`` already points at the app DB; the
    backtester reads from ``financials`` on the same Postgres instance.
    """
    base = settings.database_url
    # SQLAlchemy DSNs sometimes use ``postgresql+psycopg2://``; strip that.
    base = base.replace("postgresql+psycopg2://", "postgresql://")
    base = base.replace("postgresql+asyncpg://", "postgresql://")
    if "/financials" in base:
        return base
    # Replace the path component with /financials.
    head, _, _ = base.rpartition("/")
    return f"{head}/financials"


def _auth(authorization: str) -> int:
    if not authorization:
        raise HTTPException(401, "Missing token")
    token = authorization.replace("Bearer ", "")
    user_id = get_user_id_from_token(token)
    if not user_id:
        raise HTTPException(401, "Invalid token")
    return user_id


# ---- Models ------------------------------------------------------------


class ValidateRequest(BaseModel):
    expression: str


class ScreenRequest(BaseModel):
    expression: str
    as_of: Optional[str] = None         # YYYY-MM-DD; default today
    basis: str = "consolidated"
    limit: int = 50


class RunRequest(BaseModel):
    expression: str
    start: str                          # YYYY-MM-DD
    end: str                            # YYYY-MM-DD
    rebalance: str = Field("Q", pattern="^[DWMQYdwmqy]$")
    starting_capital: float = 1_000_000.0
    benchmark_sc_id: Optional[str] = None
    basis: str = "consolidated"
    auto_map_symbols: bool = True       # call the LLM to fill in nse_symbol
                                        # for the universe at start_date


class MapSymbolsRequest(BaseModel):
    sc_ids: list[str]
    force: bool = False                 # re-map even if nse_symbol already set
    skip_verify: bool = False           # trust the LLM without yfinance round-trip


# ---- Endpoints ---------------------------------------------------------


@router.get("/fields")
def list_fields(authorization: str = Header(None)):
    _auth(authorization)
    from backtester.fields import load_default_registry

    reg = load_default_registry()
    base = [
        {"name": n, "kind": "base", "statement": s.statement,
         "ttm_eligible": s.ttm_eligible, "unit": s.unit,
         "description": s.description}
        for n, s in sorted(reg.base.items())
    ]
    computed = [
        {"name": n, "kind": "computed", "expr": s.expr_text, "unit": s.unit,
         "description": s.description}
        for n, s in sorted(reg.computed.items())
    ]
    return {
        "base_fields": base,
        "computed_fields": computed,
        "specials": ["price"],
        "ttm_suffix_note": "Append _ttm to any TTM-eligible base field for trailing-12-month sum.",
    }


@router.post("/validate")
def validate_expr(req: ValidateRequest, authorization: str = Header(None)):
    _auth(authorization)
    from backtester.expr import parse_expression
    from backtester.expr.validator import validate, ValidationError
    from backtester.fields import load_default_registry

    reg = load_default_registry()
    try:
        ast = parse_expression(req.expression)
        result = validate(ast, reg)
    except ValidationError as e:
        return {"ok": False, "error": str(e), "suggestions": e.suggestions}
    except Exception as e:
        return {"ok": False, "error": f"parse error: {e}"}
    return {
        "ok": True,
        "referenced_fields": result.referenced_fields,
        "warnings": result.warnings,
    }


@router.post("/screen")
async def screen(req: ScreenRequest, authorization: str = Header(None)):
    _auth(authorization)
    from backtester.universe import universe_at
    from backtester.expr.validator import ValidationError

    as_of = _date.fromisoformat(req.as_of) if req.as_of else _date.today()
    try:
        conn = await asyncpg.connect(dsn=_financials_dsn())
    except Exception as e:
        raise HTTPException(503, f"financials DB unreachable: {e}")
    try:
        try:
            snap = await universe_at(conn, req.expression, as_of, basis=req.basis)
        except ValidationError as e:
            raise HTTPException(400, f"invalid expression: {e}")
    finally:
        await conn.close()

    rows = snap.rows[: req.limit]
    return {
        "as_of": str(as_of),
        "expression": req.expression,
        "n_total": len(snap.rows),
        "n_returned": len(rows),
        "leaf_fields": snap.leaf_fields,
        "referenced_fields": snap.referenced_fields,
        "rows": [_to_jsonable(r) for r in rows],
        "truncated": len(snap.rows) > req.limit,
    }


@router.post("/map-symbols")
async def map_symbols(req: MapSymbolsRequest, authorization: str = Header(None)):
    """Resolve a list of sc_ids to NSE tickers via the LLM, verify with yfinance,
    persist to ``mc.companies.nse_symbol``. Idempotent."""
    _auth(authorization)
    from backend.agents.symbol_mapper import map_and_persist
    import asyncpg as _ap

    pool = await _ap.create_pool(dsn=_financials_dsn(), min_size=1, max_size=2)
    try:
        return await map_and_persist(
            pool, req.sc_ids,
            force=req.force, skip_verify=req.skip_verify,
        )
    finally:
        await pool.close()


@router.post("/run")
async def run_expr_backtest(req: RunRequest, authorization: str = Header(None)):
    _auth(authorization)
    from backtester.engine import BacktestConfig, run_backtest as _run
    from backtester.metrics import compute_metrics
    from backtester.universe import universe_at
    from backtester.expr.validator import ValidationError
    import asyncpg as _ap

    cfg = BacktestConfig(
        expression=req.expression,
        start=_date.fromisoformat(req.start),
        end=_date.fromisoformat(req.end),
        rebalance=req.rebalance.upper(),
        starting_capital=req.starting_capital,
        benchmark_sc_id=req.benchmark_sc_id,
        basis=req.basis,
    )

    mapping_summary = None
    price_summary = None
    if req.auto_map_symbols:
        # Pre-map: run universe at start to find candidates, map any without nse_symbol,
        # then backfill prices for the full window so the engine has a curve to read.
        try:
            conn = await _ap.connect(dsn=_financials_dsn())
            try:
                snap = await universe_at(conn, req.expression, cfg.start, basis=cfg.basis)
            finally:
                await conn.close()
            if snap.rows:
                sc_ids = [r["sc_id"] for r in snap.rows]
                from backend.agents.symbol_mapper import map_and_persist
                from backtester.data.prices import backfill_prices
                pool = await _ap.create_pool(dsn=_financials_dsn(), min_size=1, max_size=4)
                try:
                    mapping_summary = await map_and_persist(pool, sc_ids)
                    price_summary = await backfill_prices(
                        pool, since=cfg.start, until=cfg.end,
                        sc_ids=sc_ids, sleep_between=0.05,
                    )
                finally:
                    await pool.close()
        except ValidationError as e:
            raise HTTPException(400, f"invalid expression: {e}")
        except Exception as e:
            # Mapping failure shouldn't block the backtest — proceed without it,
            # the engine will skip companies without prices.
            mapping_summary = {"error": str(e)[:200]}

    try:
        result = await _run(_financials_dsn(), cfg)
    except ValidationError as e:
        raise HTTPException(400, f"invalid expression: {e}")
    except Exception as e:
        raise HTTPException(500, f"backtest failed: {e}")

    metrics = compute_metrics(
        result.equity_curve,
        benchmark_curve=result.benchmark_curve,
        trades=result.trades,
    ).to_dict()
    return {
        "expression": req.expression,
        "start": req.start, "end": req.end, "rebalance": req.rebalance.upper(),
        "metrics": metrics,
        "equity_curve": result.equity_curve,
        "benchmark_curve": result.benchmark_curve,
        "rebalances": result.rebalances,
        "n_trades": len(result.trades),
        "trades_sample": result.trades[:50],
        "universe_audit": [_to_jsonable(r) for r in result.universe_audit][:20],
        "leaf_fields": result.leaf_fields,
        "referenced_fields": result.referenced_fields,
        "warnings": result.warnings[:10],
        "symbol_mapping": mapping_summary,
        "price_backfill": price_summary,
    }


def _to_jsonable(row: dict) -> dict:
    """Cast Decimal etc. to plain floats so FastAPI's JSON encoder is happy."""
    out = {}
    for k, v in row.items():
        if v is None:
            out[k] = None
        elif isinstance(v, (int, str, float)):
            out[k] = v
        else:
            try:
                out[k] = float(v)
            except (TypeError, ValueError):
                out[k] = str(v)
    return out
