"""Pairs / statistical-arbitrage backtester (Phase 2.3).

Endpoints
---------
POST /api/backtest/pairs/run    — cointegration + spread backtest for one pair
POST /api/backtest/pairs/scan   — pairwise cointegration scan over a universe

The spread signal is causal (trailing-window hedge ratio + z-score, position
lagged one bar) and the equity curve is judged through the same Phase-1 rigor
battery as every other engine. OHLCV comes from yfinance.

Auth: same Bearer-token pattern as the rest of the chat surface.
"""
from __future__ import annotations

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

from backend.auth.jwt_handler import get_user_id_from_token
from backend.services.backtest.pairs import (
    run_johansen,
    run_pairs_backtest,
    scan_pairs,
)
from backend.services.backtest.pairs.engine import PairsError

router = APIRouter(prefix="/api/backtest/pairs", tags=["Pairs backtester"])


def _auth(authorization: str) -> int:
    if not authorization:
        raise HTTPException(401, "Missing token")
    user_id = get_user_id_from_token(authorization.replace("Bearer ", ""))
    if not user_id:
        raise HTTPException(401, "Invalid token")
    return user_id


class PairsRunRequest(BaseModel):
    symbol_a: str
    symbol_b: str
    period: str = "2y"
    lookback: int = Field(60, ge=20, le=252)
    entry_z: float = Field(2.0, gt=0)
    exit_z: float = Field(0.5, ge=0)
    stop_z: float = Field(4.0, gt=0)
    hedge: str = Field("rolling", pattern="^(rolling|static)$")
    starting_capital: float = 1_000_000.0


class PairsScanRequest(BaseModel):
    symbols: list[str] = Field(..., min_length=2, max_length=40)
    period: str = "2y"
    min_level: str = Field("5%", pattern="^(1%|5%|10%)$")
    top: int = Field(20, ge=1, le=100)


class JohansenRequest(BaseModel):
    symbols: list[str] = Field(..., min_length=2, max_length=6)
    period: str = "2y"
    k_ar_diff: int = Field(1, ge=0, le=4)


@router.post("/run")
def run(req: PairsRunRequest, authorization: str = Header(None)):
    _auth(authorization)
    try:
        return run_pairs_backtest(
            req.symbol_a, req.symbol_b,
            period=req.period, lookback=req.lookback,
            entry_z=req.entry_z, exit_z=req.exit_z, stop_z=req.stop_z,
            hedge=req.hedge, starting_capital=req.starting_capital,
        )
    except PairsError as e:
        raise HTTPException(400, str(e))


@router.post("/scan")
def scan(req: PairsScanRequest, authorization: str = Header(None)):
    _auth(authorization)
    return scan_pairs(
        req.symbols, period=req.period, min_level=req.min_level, top=req.top
    )


@router.post("/johansen")
def johansen_basket(req: JohansenRequest, authorization: str = Header(None)):
    _auth(authorization)
    try:
        return run_johansen(req.symbols, period=req.period, k_ar_diff=req.k_ar_diff)
    except PairsError as e:
        raise HTTPException(400, str(e))
