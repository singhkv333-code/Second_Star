"""Portfolio performance series — back the redesigned portfolio chart (#49).

Computes a historical portfolio value series by multiplying each holding's
quantity by its yfinance close-price history over the requested period,
then summing across holdings on each timestamp.

Holdings come from the existing kite portfolio source (mock-mode in tests).
We fetch the per-symbol price series once with yfinance, re-index to a
common date axis, forward-fill any holes from non-trading days, and sum
weighted by quantity.

Endpoint:
  GET /api/portfolio/performance?period=1Y

Response:
  { period, points: [{t, v}], starting_value, ending_value,
    total_return, total_return_pct }
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Literal

import pandas as pd
import yfinance as yf  # type: ignore[import-untyped]
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend.database import get_db
from backend.kite.portfolio import get_holdings
from backend.routers._deps import require_user
from backend.routers._errors import http_error
from backend.routers.portfolio import get_kite_token


router = APIRouter(prefix="/api/portfolio", tags=["Portfolio (v1)"])
logger = logging.getLogger(__name__)


# ── Models ───────────────────────────────────────────────────────────


class PerfPoint(BaseModel):
    t: datetime
    v: float


class PerformanceResponse(BaseModel):
    period: str
    points: list[PerfPoint]
    starting_value: float
    ending_value: float
    total_return: float
    total_return_pct: float


_PeriodLiteral = Literal["1M", "3M", "6M", "1Y", "5Y"]


# yfinance period / interval pairs sized so each range yields ~80 points
# (smooth chart, low payload).
_PERIOD_MAP: dict[str, tuple[str, str]] = {
    "1M": ("1mo", "1d"),
    "3M": ("3mo", "1d"),
    "6M": ("6mo", "1d"),
    "1Y": ("1y", "1wk"),
    "5Y": ("5y", "1mo"),
}


# ── Endpoint ─────────────────────────────────────────────────────────


@router.get(
    "/performance",
    response_model=PerformanceResponse,
    summary="Historical portfolio value series for the chart",
)
def get_performance(
    period: _PeriodLiteral = Query("1Y"),
    user_id: int = Depends(require_user),
    db: Session = Depends(get_db),
) -> PerformanceResponse:
    token = get_kite_token(user_id, db)
    holdings = get_holdings(token)
    if not holdings:
        raise http_error(
            404, "not_found",
            "no holdings — performance chart needs at least one position",
        )

    yf_period, yf_interval = _PERIOD_MAP[period]

    # Pull each holding's close series; forgive per-symbol fetch failures
    # (a delisted ticker shouldn't 503 the whole chart).
    series_per_holding: list[tuple[float, pd.Series]] = []
    for h in holdings:
        sym = str(h.get("tradingsymbol", "")).strip()
        qty = float(h.get("quantity", 0) or 0)
        if not sym or qty <= 0:
            continue
        exchange = str(h.get("exchange") or "NSE").upper()
        suffix = ".BO" if exchange == "BSE" else ".NS"
        yf_sym = sym if sym.endswith((".NS", ".BO")) else f"{sym}{suffix}"
        try:
            hist = yf.Ticker(yf_sym).history(
                period=yf_period, interval=yf_interval,
            )
        except Exception as e:
            logger.warning(
                "[portfolio.performance] yfinance failed for %s: %s",
                yf_sym, e,
            )
            continue
        if hist.empty:
            continue
        series_per_holding.append((qty, hist["Close"].dropna()))

    if not series_per_holding:
        raise http_error(
            503, "not_yet_available",
            "no price history available for any holding (yfinance unreachable?)",
        )

    # Build a unioned date axis, forward-fill each series onto it, then
    # sum (qty × close). Forward-fill handles missing days for one symbol
    # by carrying its previous close, so a single missing print doesn't
    # zero out the portfolio value for that day.
    union_index = pd.Index([])
    for _, s in series_per_holding:
        union_index = union_index.union(s.index)
    union_index = union_index.sort_values()

    portfolio_value = pd.Series(0.0, index=union_index)
    for qty, s in series_per_holding:
        s_aligned = s.reindex(union_index).ffill().fillna(0.0)
        portfolio_value = portfolio_value + s_aligned * qty

    # Drop any leading zero values (period before any holding had data).
    nonzero = portfolio_value[portfolio_value > 0]
    if nonzero.empty:
        raise http_error(
            503, "not_yet_available",
            "computed portfolio value series is empty after alignment",
        )

    points = [
        PerfPoint(t=ts.to_pydatetime() if hasattr(ts, "to_pydatetime") else ts,
                  v=round(float(v), 2))
        for ts, v in nonzero.items()
    ]
    starting = float(nonzero.iloc[0])
    ending = float(nonzero.iloc[-1])
    total_return = ending - starting
    total_return_pct = (total_return / starting * 100) if starting > 0 else 0.0

    return PerformanceResponse(
        period=period,
        points=points,
        starting_value=round(starting, 2),
        ending_value=round(ending, 2),
        total_return=round(total_return, 2),
        total_return_pct=round(total_return_pct, 2),
    )


# Suppress unused-import warning for `datetime`/`timezone` reserved for
# future timestamp annotation.
_ = datetime, timezone
