"""
POST /compare — single endpoint for the Compare feature.

Drives both the dedicated Compare page and chat-bubble inline charts. Accepts
1–5 user-facing symbols (INFY, NIFTY50, ...) and a period key, fetches aligned
yfinance series, and returns chart-ready data plus per-symbol stats.

chart_type:
  - "comparison": all series rebased to 100 (default for >1 symbol)
  - "single":    raw close prices (default for 1 symbol when normalise=false)
  - "backtest":  monthly SIP simulation, series is portfolio value over time
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

from backend.auth.jwt_handler import get_user_id_from_token
from backend.market.yfinance_service import (
    calculate_returns,
    canonical_symbol,
    display_name,
    fetch_multi_symbol,
    fetch_price_history,
    normalise_to_base100,
    resolve_period,
    thin_series,
)

router = APIRouter(prefix="/compare", tags=["Compare"])

MAX_SYMBOLS = 5
VALID_CHART_TYPES = {"comparison", "single", "backtest"}


class CompareRequest(BaseModel):
    symbols: list[str] = Field(default_factory=list)
    period: str = "1y"
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    chart_type: str = "comparison"
    normalise: bool = True
    sip_amount: Optional[float] = None


class StatsModel(BaseModel):
    total_return_pct: float
    max_drawdown_pct: float
    best_day_pct: float
    worst_day_pct: float
    volatility_annualised: float
    cagr_pct: Optional[float] = None
    total_invested: Optional[float] = None
    final_value: Optional[float] = None


class SeriesPointModel(BaseModel):
    date: str
    value: float


class SeriesModel(BaseModel):
    symbol: str
    display_name: str
    data: list[SeriesPointModel]
    stats: StatsModel
    color_index: int
    note: Optional[str] = None


class DateRangeModel(BaseModel):
    from_: Optional[str] = Field(default=None, alias="from")
    to: Optional[str] = None

    class Config:
        populate_by_name = True


class CompareResponse(BaseModel):
    chart_type: str
    symbols: list[str]
    period: str
    series: list[SeriesModel]
    date_range: DateRangeModel
    data_source: str = "yfinance (15-min delayed)"
    disclaimer: str = "Past performance does not guarantee future results."


def _require_user(authorization: Optional[str]) -> int:
    if not authorization:
        raise HTTPException(status_code=401, detail="Missing token")
    uid = get_user_id_from_token(authorization.replace("Bearer ", ""))
    if not uid:
        raise HTTPException(status_code=401, detail="Invalid token")
    return uid


def _simulate_sip(price_series: list[dict], monthly_amount: float) -> tuple[list[dict], dict]:
    """
    Given a price series (list of {date, close}) and a monthly SIP amount,
    simulate buying once per month at the first available trading day of each
    new calendar month. Returns (portfolio_value_series, stats).
    """
    if not price_series or monthly_amount <= 0:
        return [], {
            "total_return_pct": 0.0, "max_drawdown_pct": 0.0,
            "best_day_pct": 0.0, "worst_day_pct": 0.0,
            "volatility_annualised": 0.0, "cagr_pct": None,
            "total_invested": 0.0, "final_value": 0.0,
        }

    units = 0.0
    invested = 0.0
    last_month: Optional[str] = None
    portfolio_series: list[dict] = []
    portfolio_closes: list[dict] = []

    for point in price_series:
        date = point["date"]
        close = float(point["close"])
        if close <= 0:
            continue
        ym = date[:7]
        if ym != last_month:
            units += monthly_amount / close
            invested += monthly_amount
            last_month = ym
        value = round(units * close, 2)
        portfolio_series.append({"date": date, "value": value})
        portfolio_closes.append({"date": date, "close": value})

    base_stats = calculate_returns(portfolio_closes)
    final_value = portfolio_series[-1]["value"] if portfolio_series else 0.0
    total_return_pct = (
        round((final_value - invested) / invested * 100, 4) if invested > 0 else 0.0
    )
    base_stats["total_return_pct"] = total_return_pct
    base_stats["total_invested"] = round(invested, 2)
    base_stats["final_value"] = final_value
    return portfolio_series, base_stats


def _build_series(
    canon: str,
    points: list[dict],
    chart_type: str,
    normalise: bool,
    sip_amount: Optional[float],
    color_index: int,
) -> SeriesModel:
    note: Optional[str] = None
    if not points:
        note = f"No data found for {canon}"
        empty_stats = StatsModel(
            total_return_pct=0.0, max_drawdown_pct=0.0,
            best_day_pct=0.0, worst_day_pct=0.0,
            volatility_annualised=0.0, cagr_pct=None,
        )
        return SeriesModel(
            symbol=canon, display_name=display_name(canon),
            data=[], stats=empty_stats, color_index=color_index, note=note,
        )

    if chart_type == "backtest" and sip_amount and sip_amount > 0:
        series_points, stats = _simulate_sip(points, sip_amount)
    else:
        if normalise:
            series_points = normalise_to_base100(points)
        else:
            series_points = [{"date": p["date"], "value": round(float(p["close"]), 4)} for p in points]
        stats = calculate_returns(points)

    series_points = thin_series(series_points, max_points=200)
    return SeriesModel(
        symbol=canon,
        display_name=display_name(canon),
        data=[SeriesPointModel(**p) for p in series_points],
        stats=StatsModel(**stats),
        color_index=color_index,
        note=note,
    )


def run_compare(req: CompareRequest) -> CompareResponse:
    """Pure compute path — usable from the chat router without HTTP overhead."""
    if not req.symbols:
        raise HTTPException(status_code=422, detail="At least one symbol required")
    if len(req.symbols) > MAX_SYMBOLS:
        raise HTTPException(status_code=422, detail=f"Maximum {MAX_SYMBOLS} symbols")
    if req.chart_type not in VALID_CHART_TYPES:
        raise HTTPException(status_code=422, detail=f"chart_type must be one of {sorted(VALID_CHART_TYPES)}")
    try:
        yf_period, yf_interval = resolve_period(req.period)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))

    canonical_syms = [canonical_symbol(s) for s in req.symbols]

    if len(canonical_syms) >= 2 and req.chart_type != "backtest":
        aligned = fetch_multi_symbol(req.symbols, yf_period, yf_interval)
        per_symbol_points = [aligned.get(s, []) for s in canonical_syms]
    else:
        per_symbol_points = [fetch_price_history(s, yf_period, yf_interval) for s in req.symbols]
        per_symbol_points = [
            [{"date": p["date"], "close": p["close"]} for p in pts]
            for pts in per_symbol_points
        ]

    series: list[SeriesModel] = []
    for idx, (canon, points) in enumerate(zip(canonical_syms, per_symbol_points)):
        series.append(_build_series(
            canon=canon,
            points=points,
            chart_type=req.chart_type,
            normalise=req.normalise,
            sip_amount=req.sip_amount,
            color_index=idx,
        ))

    populated = [s for s in series if s.data]
    if populated:
        first_dates = [s.data[0].date for s in populated]
        last_dates = [s.data[-1].date for s in populated]
        date_range = DateRangeModel(**{"from": f"{min(first_dates)} IST", "to": f"{max(last_dates)} IST"})
    else:
        date_range = DateRangeModel(**{"from": None, "to": None})

    return CompareResponse(
        chart_type=req.chart_type,
        symbols=canonical_syms,
        period=req.period,
        series=series,
        date_range=date_range,
    )


@router.post("", response_model=CompareResponse)
def compare(req: CompareRequest, authorization: Optional[str] = Header(None)) -> CompareResponse:
    _require_user(authorization)
    return run_compare(req)
