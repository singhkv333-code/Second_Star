"""yfinance fallback for company fundamentals.

The Moneycontrol scrape (`mc.*`) is primary, but it has gaps: banks store P&L
under bank-specific line items, and ~half the universe has no ratios or
balance-sheet rows at all (HDFC Bank, for example, has neither). This module
fills those gaps from yfinance so the stock page renders real numbers for
nearly any listed name.

Output is normalized to the SAME field keys + shape the FE already consumes
from `/api/financials/{symbol}` (see routers/financials.py), so the merge is
purely "fill where MC is null".

Unit conventions (must match MC so the FE's `fmtCrFromMC` is correct):
  - P&L / balance-sheet money lines are returned in ₹ Crore (yfinance gives
    absolute rupees → divide by 1e7).
  - Per-share lines (eps_basic, book_value_per_share) stay per-share.
  - Ratios match MC: ROE/ROA/margins in PERCENT, D/E and other multiples as
    plain multiples.

Everything is best-effort and never raises — a failed/empty fetch returns an
empty result and the caller falls back to "—".
"""
from __future__ import annotations

import concurrent.futures
import json
import logging
import math
from typing import Any, Optional

import pandas as pd
import yfinance as yf

from backend.cache import redis_client
from backend.market.yfinance_service import resolve_symbol

logger = logging.getLogger(__name__)

CACHE_TTL_SECONDS = 3600  # mirror fetch_price_history; fundamentals move slowly

# yfinance's underlying `requests` calls carry no default socket timeout, so a
# single stalled network call can occupy a worker thread forever. This module
# is now on the hot path for several callers (stock page, metric-series chart,
# screener fallback) — enough concurrent hung calls exhausts the app's shared
# thread pool and makes EVERY endpoint (including unrelated ones) unresponsive.
# Bounding the wait here (2026-07-01, observed live: the dev server went fully
# unresponsive, /docs included, after ~15min of concurrent yfinance-backed
# requests) can't kill the underlying blocked socket call, but it frees the
# calling request/thread after `_YF_TIMEOUT_S` instead of blocking indefinitely.
_YF_TIMEOUT_S = 12.0


def _fetch_ticker_bundle(yf_symbol: str) -> tuple[dict, Any, Any, Any]:
    t = yf.Ticker(yf_symbol)
    info = t.info or {}
    income = t.income_stmt
    balance = t.balance_sheet
    cashflow = t.cashflow
    return info, income, balance, cashflow

_CR = 1e7  # rupees per crore

# Money lines we report in ₹ Cr (divide the absolute yfinance value by 1e7).
_CR_FIELDS = frozenset(
    {
        "revenue",
        "operating_profit",
        "net_profit",
        "interest_expense",
        "cash_from_ops",
        "total_equity",
        "reserves",
        "total_debt",
        "ebitda",
        "cash",
    }
)

# income_stmt row → our field key. First matching index label wins.
_INCOME_MAP: dict[str, tuple[str, ...]] = {
    "revenue": ("Total Revenue", "Operating Revenue"),
    "operating_profit": ("Operating Income", "EBIT", "Total Operating Income As Reported"),
    "net_profit": ("Net Income", "Net Income Common Stockholders", "Net Income From Continuing Operation Net Minority Interest"),
    "interest_expense": ("Interest Expense", "Interest Expense Non Operating"),
    "eps_basic": ("Basic EPS",),
    # Used by the EV/EBITDA metric chart (not shown in the financials table).
    "ebitda": ("EBITDA", "Normalized EBITDA"),
}

# balance_sheet row → our field key.
_BALANCE_MAP: dict[str, tuple[str, ...]] = {
    "total_equity": ("Total Equity Gross Minority Interest", "Stockholders Equity", "Common Stock Equity"),
    "reserves": ("Retained Earnings",),
    "total_debt": ("Total Debt", "Total Debt And Capital Lease Obligation"),
    # For EV = mcap + net debt.
    "cash": ("Cash And Cash Equivalents", "Cash Cash Equivalents And Short Term Investments"),
}

# cash_flow row → our field key.
_CASHFLOW_MAP: dict[str, tuple[str, ...]] = {
    "cash_from_ops": ("Operating Cash Flow", "Cash Flow From Continuing Operating Activities"),
}


def _safe(v: Any) -> Optional[float]:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if math.isnan(f) or math.isinf(f):
        return None
    return f


def _fy_label(period_end: str) -> str:
    """ISO 'YYYY-MM-DD' → 'FYxx' matching the FE's yearLabel()."""
    y = period_end[:4]
    return f"FY{y[2:]}"


def _row_series(df: Optional[pd.DataFrame], names: tuple[str, ...]) -> Optional[pd.Series]:
    """Pull the first matching row (by index label) from a yfinance statement
    DataFrame. Columns are period-end Timestamps (newest first)."""
    if df is None or getattr(df, "empty", True):
        return None
    for name in names:
        if name in df.index:
            return df.loc[name]
    return None


def _history_from(df: Optional[pd.DataFrame], field_map: dict[str, tuple[str, ...]]) -> dict[str, list[dict]]:
    """Build {field: [{period_end, period_label, value, unit}, ...]} (newest
    first) from a statement DataFrame."""
    out: dict[str, list[dict]] = {}
    if df is None or getattr(df, "empty", True):
        return out
    for field, names in field_map.items():
        series = _row_series(df, names)
        if series is None:
            continue
        pts: list[dict] = []
        for col, raw in series.items():
            val = _safe(raw)
            if val is None:
                continue
            try:
                period_end = pd.Timestamp(col).strftime("%Y-%m-%d")
            except Exception:
                continue
            if field in _CR_FIELDS:
                val = val / _CR
            pts.append(
                {
                    "period_end": period_end,
                    "period_label": _fy_label(period_end),
                    "value": round(val, 4),
                    "unit": "₹ Cr" if field in _CR_FIELDS else None,
                    "source": "yfinance",
                }
            )
        if pts:
            pts.sort(key=lambda p: p["period_end"], reverse=True)
            out[field] = pts
    return out


def _ratios_from_info(info: dict) -> dict[str, Optional[float]]:
    """Latest ratios from `Ticker.info`, scaled to MC conventions."""
    def pct(key: str) -> Optional[float]:
        v = _safe(info.get(key))
        return round(v * 100, 4) if v is not None else None

    def mult(key: str) -> Optional[float]:
        v = _safe(info.get(key))
        return round(v, 4) if v is not None else None

    d2e = _safe(info.get("debtToEquity"))  # yfinance reports as a percent number
    return {
        "pe": mult("trailingPE"),
        "roe": pct("returnOnEquity"),
        "roa": pct("returnOnAssets"),
        "net_profit_margin": pct("profitMargins"),
        "price_to_book": mult("priceToBook"),
        "ev_to_ebitda": mult("enterpriseToEbitda"),
        "ev_to_sales": mult("enterpriseToRevenue"),
        "current_ratio": mult("currentRatio"),
        "debt_to_equity": round(d2e / 100, 4) if d2e is not None else None,
        "book_value_per_share": mult("bookValue"),
    }


def fetch_fundamentals(symbol: str) -> dict:
    """Return normalized fundamentals from yfinance, or an empty dict.

    Shape:
      {
        "latest": { <field>: {value, period_end, period_label, unit, source} },
        "history": { <field>: [ {period_end, period_label, value, unit} ] },
        "profile": { blurb, sector, industry, website, ceo },
      }
    """
    if not symbol:
        return {}
    cache_key = f"fund:{symbol.upper()}"
    try:
        cached = redis_client.get(cache_key)
        if cached:
            return json.loads(cached)
    except Exception:
        pass

    yf_symbol = resolve_symbol(symbol)
    executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    try:
        future = executor.submit(_fetch_ticker_bundle, yf_symbol)
        info, income, balance, cashflow = future.result(timeout=_YF_TIMEOUT_S)
    except concurrent.futures.TimeoutError:
        logger.warning(
            "yfinance fundamentals TIMED OUT for %s after %.0fs — returning empty, not blocking the caller",
            yf_symbol, _YF_TIMEOUT_S,
        )
        executor.shutdown(wait=False)  # let the stuck call die in the background, don't wait on it
        return {}
    except Exception as e:  # noqa: BLE001 — yfinance throws assorted errors
        logger.warning("yfinance fundamentals failed for %s: %s", yf_symbol, str(e)[:160])
        executor.shutdown(wait=False)
        return {}
    executor.shutdown(wait=False)

    history: dict[str, list[dict]] = {}
    history.update(_history_from(income, _INCOME_MAP))
    history.update(_history_from(balance, _BALANCE_MAP))
    history.update(_history_from(cashflow, _CASHFLOW_MAP))

    # Scale sanity. Some yfinance tickers (INFY.NS is the canonical offender)
    # report their whole statement set ~100x off; even info.totalRevenue is
    # wrong, but info.marketCap is reliable. Cross-check the implied P/S — if
    # it's absurd, the statements are untrustworthy, so drop them rather than
    # render fabricated numbers. info-derived ratios (next block) are ratios of
    # two same-scaled figures, so they survive a uniform mis-scale.
    mcap = _safe(info.get("marketCap"))
    rev_pts = history.get("revenue")
    stmt_rev = (rev_pts[0]["value"] or 0) * _CR if rev_pts else None
    if mcap and stmt_rev and stmt_rev > 0:
        ps = mcap / stmt_rev
        if ps > 80 or ps < 0.02:
            logger.warning(
                "yfinance statements for %s look mis-scaled (implied P/S=%.0f) — dropping history",
                yf_symbol, ps,
            )
            history = {}

    # Book value per share: history from balance-sheet equity / shares.
    shares = _safe(info.get("sharesOutstanding"))
    if shares and "total_equity" in history:
        bvps = []
        for p in history["total_equity"]:
            # total_equity is in ₹ Cr → back to rupees for per-share math.
            eq_rupees = (p["value"] or 0) * _CR
            bvps.append(
                {
                    "period_end": p["period_end"],
                    "period_label": p["period_label"],
                    "value": round(eq_rupees / shares, 4),
                    "unit": "₹",
                    "source": "yfinance",
                }
            )
        if bvps:
            history["book_value_per_share"] = bvps

    # Latest snapshot: newest history point per money/per-share field + ratios.
    latest: dict[str, dict] = {}
    for field, pts in history.items():
        if pts:
            top = pts[0]
            latest[field] = {
                "value": top["value"],
                "period_end": top["period_end"],
                "period_label": top["period_label"],
                "line_item": None,
                "unit": top["unit"],
                "basis": None,
                "source": "yfinance",
            }

    ratios = _ratios_from_info(info)
    # Use the most recent fiscal period we saw for ratio labelling, else None.
    ratio_period = None
    ratio_label = None
    for f in ("net_profit", "total_equity", "revenue"):
        if history.get(f):
            ratio_period = history[f][0]["period_end"]
            ratio_label = history[f][0]["period_label"]
            break
    for field, val in ratios.items():
        if val is None:
            continue
        latest.setdefault(
            field,
            {
                "value": val,
                "period_end": ratio_period,
                "period_label": ratio_label,
                "line_item": None,
                "unit": None,
                "basis": None,
                "source": "yfinance",
            },
        )

    profile = {
        "name": info.get("longName") or info.get("shortName"),
        "blurb": info.get("longBusinessSummary"),
        "sector": info.get("sector"),
        "industry": info.get("industry"),
        "website": info.get("website"),
        "ceo": _first_officer(info.get("companyOfficers")),
    }

    result = {
        "latest": latest,
        "history": history,
        "profile": profile,
        "shares": shares,
    }
    try:
        redis_client.setex(cache_key, CACHE_TTL_SECONDS, json.dumps(result))
    except Exception:
        pass
    return result


def _first_officer(officers: Any) -> Optional[str]:
    """Best-effort CEO/MD name from yfinance companyOfficers."""
    if not isinstance(officers, list):
        return None
    for o in officers:
        title = str(o.get("title", "")).lower() if isinstance(o, dict) else ""
        if any(k in title for k in ("ceo", "chief executive", "managing director", "md & ")):
            name = o.get("name")
            if name:
                return str(name)
    return None
