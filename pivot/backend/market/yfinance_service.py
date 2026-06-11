"""
yfinance data layer for the Compare feature.

Resolves user-friendly symbols (INFY, NIFTY50, "reliance") to yfinance tickers,
fetches OHLCV history, aligns multi-symbol series on common trading days,
normalises to base 100, and computes return statistics.

Caches each (symbol, period, interval) result in Redis for 1 hour to avoid
hammering yfinance on every chat message; falls back to MockRedis when Redis
is unavailable.
"""
from __future__ import annotations

import json
import logging
import math
from datetime import datetime
from typing import Optional

import pandas as pd
import yfinance as yf

from backend.cache import redis_client

logger = logging.getLogger(__name__)

CACHE_TTL_SECONDS = 3600

INDEX_TICKERS: dict[str, str] = {
    "NIFTY": "^NSEI",
    "NIFTY50": "^NSEI",
    "NIFTY 50": "^NSEI",
    "SENSEX": "^BSESN",
    "BANKNIFTY": "^NSEBANK",
    "BANK NIFTY": "^NSEBANK",
    "NIFTY BANK": "^NSEBANK",
    "NIFTYBANK": "^NSEBANK",
    "FINNIFTY": "NIFTY_FIN_SERVICE.NS",
    "NIFTYIT": "^CNXIT",
    # GAN R4 F9/C5: India VIX — the volatility index. Wired so VIX-gated
    # thematic agents ("fire if India VIX > 20") and conflict-thesis
    # confirmation triggers resolve to a REAL quote instead of a dead
    # INDIAVIX.NS ticker. yfinance carries it as ^INDIAVIX.
    "INDIAVIX": "^INDIAVIX",
    "INDIA VIX": "^INDIAVIX",
    "VIX": "^INDIAVIX",
}

NAME_TO_TICKER: dict[str, str] = {
    "reliance": "RELIANCE",
    "infosys": "INFY",
    "infy": "INFY",
    "tcs": "TCS",
    "tata consultancy": "TCS",
    "hdfc bank": "HDFCBANK",
    "hdfcbank": "HDFCBANK",
    "icici bank": "ICICIBANK",
    "icicibank": "ICICIBANK",
    "axis bank": "AXISBANK",
    "axisbank": "AXISBANK",
    "sbi": "SBIN",
    "state bank": "SBIN",
    "kotak bank": "KOTAKBANK",
    "kotakbank": "KOTAKBANK",
    "wipro": "WIPRO",
    "hcl": "HCLTECH",
    "hcltech": "HCLTECH",
    "tech mahindra": "TECHM",
    "techm": "TECHM",
    "tata motors": "TATAMOTORS",
    "tatamotors": "TATAMOTORS",
    "maruti": "MARUTI",
    "ongc": "ONGC",
    "ntpc": "NTPC",
    "itc": "ITC",
    "hindustan unilever": "HINDUNILVR",
    "hul": "HINDUNILVR",
    "nestle": "NESTLEIND",
    "asian paints": "ASIANPAINT",
    "bajaj finance": "BAJFINANCE",
    "bajfinance": "BAJFINANCE",
    "larsen": "LT",
    "l&t": "LT",
    "adani enterprises": "ADANIENT",
    "adanient": "ADANIENT",
    # [C4] common shorthand aliases that previously resolved to dead
    # tickers (RIL.NS / NIFTY BANK.NS) and returned "no quote".
    "ril": "RELIANCE",
    "nifty bank": "BANKNIFTY",
    "nifty": "NIFTY50",
    "nifty50": "NIFTY50",
    "nifty 50": "NIFTY50",
    "sensex": "SENSEX",
    "banknifty": "BANKNIFTY",
    "bank nifty": "BANKNIFTY",
    "niftybees": "NIFTYBEES",
    "goldbees": "GOLDBEES",
    "gold": "GOLDBEES",
    # silver was missing → resolve_symbol('silver') hit the dead SILVER.NS.
    # SILVERBEES is the liquid NSE silver ETF (live on yfinance).
    "silver": "SILVERBEES",
    "silverbees": "SILVERBEES",
}

DISPLAY_NAMES: dict[str, str] = {
    "INFY": "Infosys",
    "TCS": "Tata Consultancy",
    "RELIANCE": "Reliance",
    "HDFCBANK": "HDFC Bank",
    "ICICIBANK": "ICICI Bank",
    "AXISBANK": "Axis Bank",
    "SBIN": "State Bank of India",
    "KOTAKBANK": "Kotak Mahindra",
    "WIPRO": "Wipro",
    "HCLTECH": "HCL Technologies",
    "TECHM": "Tech Mahindra",
    "TATAMOTORS": "Tata Motors",
    "MARUTI": "Maruti Suzuki",
    "ONGC": "ONGC",
    "NTPC": "NTPC",
    "ITC": "ITC",
    "HINDUNILVR": "Hindustan Unilever",
    "NESTLEIND": "Nestle India",
    "ASIANPAINT": "Asian Paints",
    "BAJFINANCE": "Bajaj Finance",
    "LT": "Larsen & Toubro",
    "ADANIENT": "Adani Enterprises",
    "NIFTY50": "Nifty 50",
    "SENSEX": "Sensex",
    "BANKNIFTY": "Bank Nifty",
    "FINNIFTY": "Fin Nifty",
    "NIFTYIT": "Nifty IT",
    "NIFTYBEES": "Nifty BeES",
    "GOLDBEES": "Gold BeES",
    "SILVERBEES": "Silver BeES",
}

PERIOD_MAP: dict[str, tuple[str, str]] = {
    "1w": ("7d", "1d"),
    "1 week": ("7d", "1d"),
    "1m": ("1mo", "1d"),
    "1 month": ("1mo", "1d"),
    "3m": ("3mo", "1d"),
    "3 months": ("3mo", "1d"),
    "6m": ("6mo", "1d"),
    "6 months": ("6mo", "1d"),
    "1y": ("1y", "1wk"),
    "1 year": ("1y", "1wk"),
    "2y": ("2y", "1wk"),
    "2 years": ("2y", "1wk"),
    "5y": ("5y", "1mo"),
    "5 years": ("5y", "1mo"),
    "ytd": ("ytd", "1d"),
    "this year": ("ytd", "1d"),
    "max": ("max", "1mo"),
    "all time": ("max", "1mo"),
}

VALID_PERIODS = {"1w", "1m", "3m", "6m", "1y", "2y", "5y", "ytd", "max"}


def resolve_period(period: str) -> tuple[str, str]:
    """Map a user period string ('6m', '1 year', etc.) to (yf_period, yf_interval)."""
    key = (period or "").strip().lower()
    if key in PERIOD_MAP:
        return PERIOD_MAP[key]
    raise ValueError(f"Unsupported period: {period!r}")


def resolve_symbol(symbol: str) -> str:
    """Map a user-supplied symbol/name to a yfinance ticker."""
    if not symbol:
        return symbol
    raw = symbol.strip()
    upper = raw.upper()
    lower = raw.lower()

    if upper in INDEX_TICKERS:
        return INDEX_TICKERS[upper]
    if lower in NAME_TO_TICKER:
        canonical = NAME_TO_TICKER[lower]
        if canonical in INDEX_TICKERS:
            return INDEX_TICKERS[canonical]
        return f"{canonical}.NS"
    if upper.endswith(".NS") or upper.startswith("^"):
        return upper
    return f"{upper}.NS"


def canonical_symbol(symbol: str) -> str:
    """Return the user-facing canonical symbol (INFY, NIFTY50, ...) for display."""
    if not symbol:
        return symbol
    raw = symbol.strip()
    upper = raw.upper()
    lower = raw.lower()
    if upper in INDEX_TICKERS:
        return upper.replace(" ", "")
    if lower in NAME_TO_TICKER:
        return NAME_TO_TICKER[lower]
    return upper.replace(".NS", "")


def display_name(symbol: str) -> str:
    canon = canonical_symbol(symbol)
    return DISPLAY_NAMES.get(canon, canon)


def _cache_key(symbol: str, period: str, interval: str) -> str:
    return f"chart:{symbol.upper()}:{period}:{interval}"


def _records_from_df(df: pd.DataFrame) -> list[dict]:
    if df is None or df.empty:
        return []
    out = []
    for idx, row in df.iterrows():
        try:
            close = float(row["Close"])
        except (KeyError, TypeError, ValueError):
            continue
        if math.isnan(close):
            continue
        out.append(
            {
                "date": idx.strftime("%Y-%m-%d"),
                "open": round(float(row.get("Open", close)), 4),
                "high": round(float(row.get("High", close)), 4),
                "low": round(float(row.get("Low", close)), 4),
                "close": round(close, 4),
                "volume": int(row["Volume"]) if "Volume" in row and not math.isnan(row["Volume"]) else 0,
            }
        )
    return out


def fetch_price_history(symbol: str, period: str, interval: str) -> list[dict]:
    """
    Fetch OHLCV history for a single symbol. Returns [] on failure (never raises).

    Tries the resolved ticker first (e.g. INFY → INFY.NS, NIFTY50 → ^NSEI).
    If that returns empty and the resolved form had a .NS suffix, retries with
    the raw symbol — covers indices and ETFs that don't take the suffix.
    """
    if not symbol:
        return []

    # Normalize the period string to a yfinance-valid period. RESPECT the
    # caller's interval — only adopt the chart-oriented downsample
    # (PERIOD_MAP's 1y→1wk / 5y→1mo) when the caller didn't specify one.
    # get_ohlcv passes interval='1d' for return/Sharpe/volatility metrics
    # and MUST get DAILY bars; the old unconditional override silently
    # returned weekly (1y/2y) or monthly (5y) data, corrupting CAGR and
    # overstating volatility by ~sqrt(5) (2026-05-29 audit). Chart callers
    # (routers/compare.py) pass their own resolved interval, so they are
    # unaffected.
    _key = (period or "").strip().lower()
    if _key in PERIOD_MAP:
        mapped_period, mapped_interval = PERIOD_MAP[_key]
        period = mapped_period
        if not interval:
            interval = mapped_interval

    resolved = resolve_symbol(symbol)
    cache_key = _cache_key(resolved, period, interval)

    try:
        cached = redis_client.get(cache_key)
        if cached:
            raw = cached.decode() if isinstance(cached, (bytes, bytearray)) else cached
            return json.loads(raw)
    except Exception as e:
        logger.debug(f"Cache read miss for {cache_key}: {e}")

    records: list[dict] = []
    try:
        df = yf.Ticker(resolved).history(period=period, interval=interval, auto_adjust=False)
        records = _records_from_df(df)
    except Exception as e:
        logger.warning(f"yfinance fetch failed for {resolved}: {e}")

    if not records and resolved.endswith(".NS"):
        bare = resolved[:-3]
        try:
            df = yf.Ticker(bare).history(period=period, interval=interval, auto_adjust=False)
            records = _records_from_df(df)
        except Exception as e:
            logger.warning(f"yfinance fallback fetch failed for {bare}: {e}")

    if records:
        try:
            redis_client.set(cache_key, json.dumps(records), ex=CACHE_TTL_SECONDS)
        except Exception as e:
            logger.debug(f"Cache write failed for {cache_key}: {e}")

    return records


def fetch_multi_symbol(symbols: list[str], period: str, interval: str) -> dict[str, list[dict]]:
    """
    Fetch multiple symbols and align them on common trading days.

    Returns {canonical_symbol: [{date, close}, ...], ...}. Symbols with no data
    are returned with an empty list and not used for alignment.
    """
    raw_series: dict[str, list[dict]] = {}
    for sym in symbols:
        canon = canonical_symbol(sym)
        raw_series[canon] = fetch_price_history(sym, period, interval)

    non_empty = {s: rec for s, rec in raw_series.items() if rec}
    if len(non_empty) < 2:
        return {
            s: [{"date": r["date"], "close": r["close"]} for r in rec]
            for s, rec in raw_series.items()
        }

    frames = []
    for sym, rec in non_empty.items():
        df = pd.DataFrame(rec)[["date", "close"]].rename(columns={"close": sym})
        frames.append(df)

    merged = frames[0]
    for f in frames[1:]:
        merged = merged.merge(f, on="date", how="inner")
    merged = merged.sort_values("date")

    aligned: dict[str, list[dict]] = {}
    for sym in raw_series:
        if sym in non_empty:
            aligned[sym] = [
                {"date": row["date"], "close": float(row[sym])}
                for _, row in merged.iterrows()
            ]
        else:
            aligned[sym] = []
    return aligned


def normalise_to_base100(series: list[dict]) -> list[dict]:
    """Rebase a price series so the first close = 100. Returns [{date, value}]."""
    if not series:
        return []
    base = series[0].get("close")
    if not base or base <= 0:
        return []
    return [
        {"date": p["date"], "value": round((p["close"] / base) * 100, 4)}
        for p in series
    ]


def _years_between(first_date: str, last_date: str) -> float:
    try:
        a = datetime.strptime(first_date, "%Y-%m-%d")
        b = datetime.strptime(last_date, "%Y-%m-%d")
    except ValueError:
        return 0.0
    return max((b - a).days / 365.25, 0.0)


def calculate_returns(series: list[dict]) -> dict:
    """
    Compute total return, max drawdown, best/worst single-day move, annualised
    volatility, and CAGR (if span >= 1 year) from a list of {date, close}.
    """
    blank = {
        "total_return_pct": 0.0,
        "max_drawdown_pct": 0.0,
        "best_day_pct": 0.0,
        "worst_day_pct": 0.0,
        "volatility_annualised": 0.0,
        "cagr_pct": None,
    }
    if not series or len(series) < 2:
        return blank

    closes = [p["close"] for p in series if p.get("close")]
    if len(closes) < 2 or closes[0] <= 0:
        return blank

    first, last = closes[0], closes[-1]
    total_return_pct = ((last - first) / first) * 100

    peak = closes[0]
    max_dd = 0.0
    for c in closes:
        if c > peak:
            peak = c
        dd = ((c - peak) / peak) * 100 if peak > 0 else 0.0
        if dd < max_dd:
            max_dd = dd

    daily_returns = [
        (closes[i] / closes[i - 1] - 1) * 100
        for i in range(1, len(closes))
        if closes[i - 1] > 0
    ]
    best_day = max(daily_returns) if daily_returns else 0.0
    worst_day = min(daily_returns) if daily_returns else 0.0

    if len(daily_returns) >= 2:
        mean = sum(daily_returns) / len(daily_returns)
        variance = sum((r - mean) ** 2 for r in daily_returns) / (len(daily_returns) - 1)
        vol_annualised = math.sqrt(variance) * math.sqrt(252)
    else:
        vol_annualised = 0.0

    years = _years_between(series[0]["date"], series[-1]["date"])
    cagr_pct: Optional[float] = None
    if years >= 1.0 and first > 0:
        cagr_pct = round(((last / first) ** (1 / years) - 1) * 100, 4)

    return {
        "total_return_pct": round(total_return_pct, 4),
        "max_drawdown_pct": round(max_dd, 4),
        "best_day_pct": round(best_day, 4),
        "worst_day_pct": round(worst_day, 4),
        "volatility_annualised": round(vol_annualised, 4),
        "cagr_pct": cagr_pct,
    }


def thin_series(series: list[dict], max_points: int = 200) -> list[dict]:
    """Down-sample a series to at most max_points by uniform stride, keeping the last point."""
    if not series or len(series) <= max_points:
        return series
    stride = max(1, len(series) // max_points)
    sampled = series[::stride]
    if sampled[-1] is not series[-1]:
        sampled.append(series[-1])
    return sampled
