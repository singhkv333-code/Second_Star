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
    # Route through the shared price engine, which is now Kite-primary
    # (broker-grade daily bars that work on cloud IPs) with yfinance as the
    # backup — so the portfolio curve no longer depends on Yahoo. Weekly/monthly
    # ranges (1Y/5Y) that Kite can't serve still fall to yfinance inside it.
    try:
        from backend.market.yfinance_service import fetch_price_history
        records = fetch_price_history(yf_sym, yf_period, yf_interval)
    except Exception as e:
        logger.warning(
            "[portfolio.performance] price fetch failed for %s: %s", yf_sym, e,
        )
        return qty, None
    if not records:
        return qty, None
    try:
        series = pd.Series(
            [float(r["close"]) for r in records],
            index=pd.to_datetime([r["date"] for r in records]),
        ).dropna()
    except Exception:  # noqa: BLE001 — malformed record → drop this holding
        return qty, None
    if series.empty:
        return qty, None
    return qty, series


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


_PERIOD_SPAN_DAYS: dict[str, int] = {
    "1M": 31, "3M": 93, "6M": 186, "1Y": 366, "5Y": 1827,
}


def _paper_performance(
    db, user_id: int, period: _PeriodLiteral,
) -> Optional[dict]:
    """Real paper-book equity curve = NAV (cash + positions) over time.

    This is the honest "value of my portfolio over time": it anchors at the
    account's **starting capital** (the all-cash NAV before any trade), replays
    every fill in order to know the exact holdings + cash on each date, marks
    those holdings at their real historical close each day, and ends at the live
    NAV. So the curve begins at the initial capital and its slope reflects the
    book's actual P&L — a book in the red slopes DOWN, never up.

    Value identity at each date d:
        value(d) = cash(d) + Σ_sym qty(sym, d) · mark(sym, d)
    where ``cash(d) = starting_capital + Σ net_cashflow(fills ≤ d)`` (buys
    debit, sells credit, both charge-inclusive) — i.e. exactly NAV = cash +
    positions_mv. Before a symbol's first fill its qty is 0, so the pre-trade
    span honestly shows the all-cash starting capital (NOT a backward projection
    of today's book). Quantities are kept FRACTIONAL (crypto/US positions are
    sub-unit); ``mark`` is the historical close for NSE names and the position's
    average cost for US/crypto (whose INR history we don't reconstruct) so those
    legs sit flat at cost until the live-NAV endpoint reflects their real move.
    The final point is pinned to the live NAV so the chart end == the header.
    """
    from backend.models import PaperAccount, PaperFill, PaperPosition
    from backend.services.portfolio_source import paper_cash_and_nav
    from backend.view_markets.security_meta import is_us_or_crypto_fast

    account = (
        db.query(PaperAccount)
        .filter(PaperAccount.user_id == user_id)
        .first()
    )
    paper = paper_cash_and_nav(db, user_id)  # (cash, nav) | None
    nav_now = (
        float(paper[1]) if paper is not None
        else (float(account.cash_available) if account is not None else 0.0)
    )
    if account is None:
        # No paper book at all — nothing of our own to show. Defer to the
        # caller's legacy holdings path (which 404s on an empty book), rather
        # than fabricating a flat ₹0 line.
        return None

    seed = float(account.starting_capital or 0.0)

    fills = (
        db.query(PaperFill)
        .filter(PaperFill.account_id == account.id)
        .order_by(PaperFill.filled_at.asc())
        .all()
    )
    if not fills:
        # All-cash book — honest flat NAV line at the starting capital.
        return _flat_series(period, nav_now)

    yf_period, yf_interval = _PERIOD_MAP[period]
    end = pd.Timestamp.utcnow().tz_localize(None).normalize()
    period_start = end - pd.Timedelta(days=_PERIOD_SPAN_DAYS[period])
    first_fill = pd.Timestamp(fills[0].filled_at.date())
    # Start ONE bar before the first trade so the chart opens on the all-cash
    # starting-capital baseline (the "start at ₹5L" the value identity gives for
    # free), then moves as positions are opened. For a book older than the
    # window we start at period_start instead (mid-hold), never before it.
    pre_trade = first_fill - pd.Timedelta(days=1)
    axis_start = pre_trade if pre_trade > period_start else period_start

    # The REAL held span can be much shorter than the selected range's calendar
    # span (e.g. a 1Y view on a 2-day-old book). Bucketing at the range's native
    # interval (1wk/1mo) over so short a span collapses the window into ~1 bar;
    # force the finer daily interval so the recent movement is actually visible.
    _BAR_DAYS = {"1d": 1, "1wk": 7, "1mo": 30}
    real_span_days = max((end - axis_start).days, 1)
    if real_span_days < _BAR_DAYS.get(yf_interval, 1) * 3:
        yf_interval = "1d"

    # Per-symbol average cost (charge-inclusive) — the flat historical mark for
    # legs whose historical close we don't fetch (US/crypto) or can't fetch.
    avg_cost: dict[str, float] = {
        str(p.symbol): float(p.avg_cost or 0.0)
        for p in db.query(PaperPosition)
        .filter(PaperPosition.account_id == account.id)
        .all()
    }
    for f in fills:  # closed-lot / missing fallback: last fill price
        avg_cost.setdefault(str(f.symbol), 0.0)
        if not avg_cost[str(f.symbol)]:
            try:
                avg_cost[str(f.symbol)] = float(f.fill_price)
            except (TypeError, ValueError):
                pass

    # Historical closes — NSE equities only. US/crypto have no INR daily-close
    # path here (appending ".NS" to "SOL-USD" just fails), so they mark at cost.
    symbols = sorted({str(f.symbol) for f in fills})
    indian = [s for s in symbols if not is_us_or_crypto_fast(s)]
    specs = [
        (1.0, s if s.endswith((".NS", ".BO")) else f"{s}.NS")
        for s in indian
    ]
    closes: dict[str, pd.Series] = {}
    if specs:
        with ThreadPoolExecutor(max_workers=min(8, len(specs))) as pool:
            futures = {
                sym: pool.submit(_fetch_one_series, spec, yf_period, yf_interval)
                for sym, spec in zip(indian, specs)
            }
            for sym, fut in futures.items():
                _, series = fut.result()
                if series is None:
                    continue
                idx = series.index
                if getattr(idx, "tz", None) is not None:
                    series = series.copy()
                    series.index = idx.tz_localize(None)
                series.index = series.index.normalize()
                # Intraday history collapses to duplicate dates after
                # normalize(); keep one row per date (last close wins) so a
                # duplicate index can't make `s.get(ts)` return a Series.
                series = series[~series.index.duplicated(keep="last")]
                closes[sym] = series

    # Naive date axis over [axis_start, end]: a regular grid at the interval,
    # plus the key anchors (start, first-fill day, end) and every real close
    # date, so the flat all-cash baseline AND the post-trade movement both draw.
    freq = {"1d": "D", "1wk": "W", "1mo": "MS"}.get(yf_interval, "W")
    grid = pd.date_range(start=axis_start, end=end, freq=freq)
    union = grid.union(pd.DatetimeIndex([axis_start, first_fill, end]))
    for s in closes.values():
        union = union.union(s.index)
    union = union[(union >= axis_start) & (union <= end)].unique().sort_values()
    if len(union) == 0:
        union = pd.DatetimeIndex([axis_start, end])
    aligned = {sym: s.reindex(union).ffill().bfill() for sym, s in closes.items()}

    # Sweep the axis, folding in each fill as its date is reached.
    i = 0
    held: dict[str, float] = {}
    cash = seed
    points: list[PerfPoint] = []
    for ts in union:
        while i < len(fills) and pd.Timestamp(fills[i].filled_at.date()) <= ts:
            f = fills[i]
            qty = float(f.quantity or 0.0)  # fractional for crypto/US lots
            signed = qty if str(f.transaction_type).upper() == "BUY" else -qty
            sym = str(f.symbol)
            held[sym] = held.get(sym, 0.0) + signed
            cash += float(f.net_cashflow)
            i += 1
        val = cash
        for sym, q in held.items():
            if q == 0:
                continue
            px: Optional[float] = None
            s = aligned.get(sym)
            if s is not None:
                raw = s.get(ts)
                if isinstance(raw, pd.Series):
                    raw = raw.iloc[-1] if not raw.empty else None
                if raw is not None and not pd.isna(raw):
                    px = float(raw)
            if px is None:  # US/crypto or unfetched NSE → flat at average cost
                px = avg_cost.get(sym)
            if px is not None:
                val += q * px
        points.append(PerfPoint(t=ts.to_pydatetime(), v=round(val, 2)))

    # Pin the last point to the live NAV so the chart end == header value.
    if points:
        points[-1] = PerfPoint(t=points[-1].t, v=round(nav_now, 2))
    else:
        points = [PerfPoint(t=end.to_pydatetime(), v=round(nav_now, 2))]

    # Report return against the STARTING CAPITAL (the curve's anchor), so the
    # chart's headline return agrees with the portfolio's Total P&L. When the
    # window opens mid-hold (older book), the first plotted point is the anchor.
    starting = seed if axis_start <= first_fill else float(points[0].v)
    ending = float(points[-1].v)
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


def _compute_performance(
    db, user_id: int, token: str, period: _PeriodLiteral,
) -> dict:
    """Build the performance payload as a plain JSON-safe dict (not a
    `PerformanceResponse`) so it round-trips cleanly through the Redis
    cache-aside layer — see `services/portfolio_cache.cache_aside`.

    PAPER mode reconstructs the curve from the account's ACTUAL fills forward
    (``_paper_performance``) — real portfolio movement AFTER the trades took
    place, never a backward projection of today's book. LIVE / broker mode has
    no fill history to anchor to, so it falls back to the holdings×history
    projection below (the only option when we don't know purchase dates).
    """
    from backend.paper.routing import should_use_paper

    if should_use_paper(db, user_id):
        paper_result = _paper_performance(db, user_id, period)
        if paper_result is not None:
            return paper_result
        # No paper account yet — fall through to the legacy holdings path.

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
