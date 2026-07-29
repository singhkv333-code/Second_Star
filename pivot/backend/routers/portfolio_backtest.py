"""Multi-position portfolio backtester (Phase 2.4).

POST /api/backtest/portfolio/run — cross-sectional momentum portfolio over a
universe with max-names / gross / net / sector-cap constraints, long-only or
dollar-neutral long/short, judged through the Phase-1 rigor battery. OHLCV from
yfinance. Auth: same Bearer-token pattern as the rest of the chat surface.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

from backend.auth.jwt_handler import get_user_id_from_token
from backend.services.backtest.portfolio import run_portfolio_backtest
from backend.services.backtest.portfolio.engine import PortfolioError

router = APIRouter(prefix="/api/backtest/portfolio", tags=["Portfolio backtester"])


def _auth(authorization: str) -> int:
    if not authorization:
        raise HTTPException(401, "Missing token")
    user_id = get_user_id_from_token(authorization.replace("Bearer ", ""))
    if not user_id:
        raise HTTPException(401, "Invalid token")
    return user_id


class PortfolioRunRequest(BaseModel):
    symbols: list[str] = Field(..., min_length=3, max_length=100)
    period: str = "5y"
    signal: str = Field("momentum", pattern="^(momentum)$")
    lookback: int = Field(252, ge=20, le=756)
    skip: int = Field(21, ge=0, le=63)
    top_n: int = Field(5, ge=1, le=50)
    rebalance: str = Field("M", pattern="^[WMQwmq]$")
    long_short: bool = False
    gross: float = Field(1.0, gt=0, le=2.0)
    sector_cap: Optional[float] = Field(None, gt=0, le=1.0)
    starting_capital: float = 1_000_000.0


@router.post("/run")
def run(req: PortfolioRunRequest, authorization: str = Header(None)):
    _auth(authorization)
    try:
        return run_portfolio_backtest(
            req.symbols, period=req.period, signal=req.signal,
            lookback=req.lookback, skip=req.skip, top_n=req.top_n,
            rebalance=req.rebalance.upper(), long_short=req.long_short,
            gross=req.gross, sector_cap=req.sector_cap,
            starting_capital=req.starting_capital,
        )
    except PortfolioError as e:
        raise HTTPException(400, str(e))
