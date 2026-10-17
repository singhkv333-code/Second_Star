"""Where a stock's P/E sits within its own history.

P/E on each trading day = market cap / trailing-twelve-month net profit, with

  * market cap  = adjusted close x the latest filed share count. Adjusted
    prices are already restated to today's share base, so this stays right
    through bonuses and splits, where a per-share EPS history does not (MC
    carries HDFC Bank's pre-bonus EPS of 93 beside a post-bonus 50, and
    rounds EPS to whole rupees, so Eternal's reads 0).
  * TTM profit  = `quarterly_metrics.np_ttm`, counted from 45 days after the
    quarter ends, when the results are out. No look-ahead.

Days with zero or negative TTM profit have no P/E and are left out.
"""
from __future__ import annotations

import bisect
import logging
from datetime import timedelta
from statistics import median
from typing import Any

from sqlalchemy import text

from backend.services.chart_series import _thin

logger = logging.getLogger(__name__)

RESULTS_LAG = timedelta(days=45)


def _quantile(sorted_vals: list[float], q: float) -> float:
    i = (len(sorted_vals) - 1) * q
    lo, hi = int(i), min(int(i) + 1, len(sorted_vals) - 1)
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (i - lo)


def _earnings(who: dict, since) -> tuple[list[tuple[Any, float]], str]:
    """[(date counted from, trailing net profit in Rs Cr)] ascending, and the
    basis. TTM from the quarterly table, preferring the consolidated series when
    it covers the window; annual reported profit when no quarters are on record
    (HDFC Bank and Infosys have none)."""
    from backend.database import SessionLocal
    from backend.market import financials_db as fdb

    found = []
    with SessionLocal() as db:
        for basis in ("consolidated", "standalone"):
            rows = db.execute(text("""
                SELECT period_end, np_ttm FROM quarterly_metrics
                 WHERE isin = :i AND basis = :b AND np_ttm IS NOT NULL AND n_ttm = 4
              ORDER BY period_end"""), {"i": who["isin"], "b": basis}).fetchall()
            if rows:
                steps = [(r[0] + RESULTS_LAG, float(r[1])) for r in rows]
                if steps[0][0] <= since:
                    return steps, f"{basis} TTM"
                found.append((steps, f"{basis} TTM"))
    if found:
        return max(found, key=lambda f: len(f[0]))
    try:
        rows = fdb.get_fundamental_history(who["symbol"], "net_profit", limit=12)
    except Exception:  # noqa: BLE001
        rows = []
    steps = sorted(
        (r.availability_date or r.period_end + timedelta(days=60), float(r.value_numeric))
        for r in rows if r.value_numeric is not None and r.period_end)
    return steps, ("annual reported (no quarterly results on record)" if steps else "")


def _identity(symbol: str) -> dict | None:
    """ISIN and name, by the stock page's own rule (primary MC mapping first)."""
    from backend.database import SessionLocal

    with SessionLocal() as db:
        row = db.execute(text("""
            SELECT isin, verified_name FROM company_identity
             WHERE verified_symbol = :s
          ORDER BY mc_is_primary DESC, mc_metric_count DESC NULLS LAST, mc_sc_id
             LIMIT 1"""), {"s": symbol}).first()
    return {"symbol": symbol, "isin": row[0], "name": row[1]} if row else None


def _shares(symbol: str, _isin: str | None = None) -> float | None:
    from backend.market.financials_db import FinancialsSessionLocal

    with FinancialsSessionLocal() as db:
        row = db.execute(text("""
            SELECT total_shares FROM shp.filings
             WHERE symbol = :s AND total_shares > 0
          ORDER BY quarter_end DESC LIMIT 1"""), {"s": symbol}).first()
    return float(row[0]) if row else None


def pe_band(symbol: str, years: int = 5) -> dict:
    from concurrent.futures import ThreadPoolExecutor

    from backend.core.data.historical import get_close_series

    sym = (symbol or "").strip().upper()
    years = max(1, min(int(years or 5), 10))
    # Prices, identity and share count are independent reads: pay the slowest.
    # Not a `with` block: an unknown symbol must not wait out its price fetch.
    pool = ThreadPoolExecutor(3)
    try:
        f_closes = pool.submit(get_close_series, sym, period=f"{years}y")
        f_shares = pool.submit(_shares, sym)
        who = pool.submit(_identity, sym).result()
        if who is None:
            return {"available": False, "error": f"unknown symbol {sym}"}
        try:
            closes = f_closes.result()
        except Exception:  # noqa: BLE001 — DataUnavailableError and feed errors
            return {"available": False, "symbol": sym, "error": "no price history"}
        shares = f_shares.result()
    finally:
        pool.shutdown(wait=False)
    earnings, basis = _earnings(who, closes.index[0].date())
    if not earnings or not shares:
        return {"available": False, "symbol": sym,
                "error": "no earnings history or share count on record"}

    starts = [d for d, _ in earnings]
    pts = []
    for ts, close in zip(closes.index, closes.values):
        d = ts.date()
        i = bisect.bisect_right(starts, d) - 1
        if i < 0 or earnings[i][1] <= 0:
            continue
        pts.append({"t": d.isoformat(),
                    "v": round(float(close) * shares / 1e7 / earnings[i][1], 2)})
    if len(pts) < 20:
        return {"available": False, "symbol": sym,
                "error": "too little history with positive earnings for a band"}

    vals = sorted(p["v"] for p in pts)
    cur = pts[-1]["v"]
    stats = {
        "current": cur,
        "median": round(median(vals), 2),
        "p25": round(_quantile(vals, 0.25), 2),
        "p75": round(_quantile(vals, 0.75), 2),
        "low": vals[0],
        "high": vals[-1],
        "percentile": round(100 * bisect.bisect_left(vals, cur) / len(vals)),
    }
    since = pts[0]["t"]
    return {
        "available": True,
        "symbol": sym,
        "name": who.get("name") or sym,
        "metric": "P/E (trailing 12 months)",
        "as_of": pts[-1]["t"],
        "window": f"{since} to {pts[-1]['t']} ({len(pts)} trading days)",
        **stats,
        "earnings_basis": basis,
        "method": "market cap (adjusted close x latest filed shares) / trailing net profit, "
                  "counted from when results were out; loss-making days excluded",
        "_charts": [{
            "_render_hint": "valuation_band",
            "title": f"{sym} · P/E",
            "symbol": sym,
            "years": years,
            **stats,
            "points": _thin(pts),
        }],
    }
