"""Portfolio performance series — back the redesigned portfolio chart (#49).

Computes a historical portfolio value series by multiplying each holding's
quantity by its yfinance close-price history over the requested period,
then summing across holdings on each timestamp.

Holdings come from the existing kite portfolio source (mock-mode in tests),
via the shared `get_holdings_cached` (so this endpoint has no data
dependency on `/portfolio/holdings` having run first — both just read
through the same short-TTL cache independently). We fetch each holding's
price series **concurrently** via a small thread pool (yfinance has no
single-call "many symbols, aligned history" primitive that preserves our
per-holding partial-failure tolerance, so we mirror the
`ThreadPoolExecutor` batching pattern used in
`services/fundamentals_screen.py` rather than a serial per-symbol loop),
re-index to a common date axis, forward-fill any holes from non-trading
days, and sum weighted by quantity. The whole computed response is then
cached short-TTL per `(user_id, period)` — see `services/portfolio_cache.py`.

Endpoint:
  GET /api/portfolio/performance?period=1Y

Response:
  { period, points: [{t, v}], starting_value, ending_value,
    total_return, total_return_pct }
"""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Literal, Optional

import pandas as pd
import yfinance as yf  # type: ignore[import-untyped]
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend.database import get_db
from backend.routers._deps import require_user
from backend.routers._errors import http_error
from backend.routers.portfolio import get_kite_token
from backend.services.portfolio_cache import (
    cache_aside, performance_cache_key,
)


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


def _fetch_one_series(
    spec: tuple[float, str], yf_period: str, yf_interval: str,
) -> tuple[float, Optional[pd.Series]]:
    """Fetch one holding's close-price series. Returns `(qty, None)` on any
    failure (bad symbol, delisted, yfinance error, empty history) so the
    caller can drop it without failing the whole chart — same
    partial-failure tolerance as the old serial loop, just runnable
    concurrently in a thread pool. The caller collects results by walking
    its `Future` list in submission order (not completion order), so
    per-symbol ordering semantics match the old serial loop exactly."""
    qty, yf_sym = spec
    try:
        hist = yf.Ticker(yf_sym).history(period=yf_period, interval=yf_interval)
    except Exception as e:
        logger.warning(
            "[portfolio.performance] yfinance failed for %s: %s", yf_sym, e,
        )
        return qty, None
    if hist.empty:
        return qty, None
    return qty, hist["Close"].dropna()


def _flat_series(period: _PeriodLiteral, value: float) -> dict:
    """A flat portfolio-value line at ``value`` across ``period`` — the honest
    chart for an all-cash paper book (no positions to reconstruct). Uses a
    yfinance-free synthetic date axis at the period's native interval."""
    yf_period, yf_interval = _PERIOD_MAP[period]
    freq = {"1d": "D", "1wk": "W", "1mo": "MS"}.get(yf_interval, "W")
    span = {"1M": 31, "3M": 93, "6M": 186, "1Y": 366, "5Y": 1827}[period]
    end = pd.Timestamp.utcnow().normalize().tz_localize(None)
    idx = pd.date_range(start=end - pd.Timedelta(days=span), end=end, freq=freq)
    if len(idx) == 0:
        idx = pd.DatetimeIndex([end])
    points = [PerfPoint(t=ts.to_pydatetime(), v=round(float(value), 2)) for ts in idx]
    return PerformanceResponse(
        period=period,
        points=points,
        starting_value=round(float(value), 2),
        ending_value=round(float(value), 2),
        total_return=0.0,
        total_return_pct=0.0,
    ).model_dump(mode="json")


def _compute_performance(
    db, user_id: int, token: str, period: _PeriodLiteral,
) -> dict:
    """Build the performance payload as a plain JSON-safe dict (not a
    `PerformanceResponse`) so it round-trips cleanly through the Redis
    cache-aside layer — see `services/portfolio_cache.cache_aside`.

    Holdings resolve through ``portfolio_source.resolve_holdings`` so a
    paper-mode account reconstructs from its SIMULATED positions (making the
    chart reactive to real activity). In paper mode the account's cash is added
    as a flat baseline so the curve ends at the true NAV, and an all-cash book
    draws a flat NAV line instead of erroring on "no holdings".
    """
    from backend.services.portfolio_source import (
        paper_cash_and_nav, resolve_holdings,
    )

    holdings = resolve_holdings(db, user_id, token)
    paper = paper_cash_and_nav(db, user_id)  # (cash, nav) | None

    if not holdings:
        if paper is not None:
            return _flat_series(period, paper[1])  # flat line at NAV (all cash)
        raise http_error(
            404, "not_found",
            "no holdings — performance chart needs at least one position",
        )

    yf_period, yf_interval = _PERIOD_MAP[period]

    fetch_specs: list[tuple[float, str]] = []
    for h in holdings:
        sym = str(h.get("tradingsymbol", "")).strip()
        qty = float(h.get("quantity", 0) or 0)
        if not sym or qty <= 0:
            continue
        exchange = str(h.get("exchange") or "NSE").upper()
        suffix = ".BO" if exchange == "BSE" else ".NS"
        yf_sym = sym if sym.endswith((".NS", ".BO")) else f"{sym}{suffix}"
        fetch_specs.append((qty, yf_sym))

    # Pull each holding's close series concurrently — was a serial
    # `for h in holdings: yf.Ticker(...).history(...)` loop (one blocking
    # network round-trip per holding). A small thread pool cuts wall-clock
    # roughly by `min(len(holdings), max_workers)`x; mirrors the
    # `ThreadPoolExecutor` batching pattern in
    # `services/fundamentals_screen.py` (yfinance has no single batched
    # call that preserves per-symbol partial-failure tolerance the way
    # `yf.download` with a ticker list would silently mask).
    series_per_holding: list[tuple[float, pd.Series]] = []
    if fetch_specs:
        with ThreadPoolExecutor(max_workers=min(8, len(fetch_specs))) as pool:
            futures = [
                pool.submit(_fetch_one_series, spec, yf_period, yf_interval)
                for spec in fetch_specs
            ]
            for future in futures:
                qty, series = future.result()
                if series is not None:
                    series_per_holding.append((qty, series))

    if not series_per_holding:
        if paper is not None:
            # No price history for any paper position → still honest to show
            # the flat NAV line rather than erroring.
            return _flat_series(period, paper[1])
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
        if paper is not None:
            return _flat_series(period, paper[1])
        raise http_error(
            503, "not_yet_available",
            "computed portfolio value series is empty after alignment",
        )

    # Paper: add the account's cash as a flat baseline so the reconstructed
    # positions curve ends at the true NAV (cash + positions), matching the
    # header value. Cash is treated as constant over the window (an honest
    # approximation for a "value of my current book over time" chart).
    if paper is not None:
        nonzero = nonzero + float(paper[0])

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
    ).model_dump(mode="json")


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
    """Historical portfolio value series for the chart.

    No dependency on `/portfolio/summary` or `/portfolio/holdings` having
    run first — it derives its own holdings via the shared
    `get_holdings_cached`, so callers should fire all four portfolio
    endpoints concurrently. Short-TTL Redis cached per `(user_id, period)`
    (see `services/portfolio_cache.py`) so repeated chart loads/period
    switches within the TTL window skip both the holdings walk and the
    per-symbol yfinance fetch entirely.
    """
    token = get_kite_token(user_id, db)
    # Fold the source (paper vs live) into the cache key so a mode flip never
    # serves the other book's series for a TTL window.
    from backend.paper.routing import should_use_paper
    book = "paper" if should_use_paper(db, user_id) else "live"
    data = cache_aside(
        performance_cache_key(user_id, f"{book}:{period}"),
        lambda: _compute_performance(db, user_id, token, period),
    )
    return PerformanceResponse.model_validate(data)


# Suppress unused-import warning for `datetime`/`timezone` reserved for
# future timestamp annotation.
_ = datetime, timezone
