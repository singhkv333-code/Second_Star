"""Canonical bar-interval model — the single source of truth for every
indicator surface (analysis, triggers/DSL, backtest).

Before this module, indicators were daily-only: the analysis path locked
``interval="1d"``, the DSL ``timeframe`` was ``Literal["daily","weekly"]``,
and the backtest engines hardcoded ``"1d"``. The data layer can do much more
(Kite: minute…60minute…day; yfinance: 1m/5m/15m/30m/1h within rolling caps),
so this module defines the canonical interval set, the per-source string maps,
the honest per-interval lookback caps ("how far back data can be availed"),
and the annualisation factor needed when indicator/vol maths runs on intraday
bars instead of daily.

Design rules:
- ``period`` on an indicator is always counted in **bars of the chosen
  interval** (RSI(14) on 15m = 14 fifteen-minute bars), never silently
  converted to days.
- Legacy ``"daily"``/``"weekly"`` keep working as aliases for ``1d``/``1wk``.
- A source that cannot serve an interval returns ``None`` from its map — the
  caller degrades honestly rather than downgrading to the wrong timeframe.
"""

from __future__ import annotations

from typing import Optional

# Canonical user-facing interval strings, ordered fine → coarse.
CANONICAL_INTERVALS: tuple[str, ...] = (
    "1m", "3m", "5m", "10m", "15m", "30m", "1h", "1d", "1wk", "1mo",
)

# Intraday subset (sub-daily). These have shallow, rolling data windows.
_INTRADAY: frozenset[str] = frozenset({"1m", "3m", "5m", "10m", "15m", "30m", "1h"})

# Accepted aliases → canonical. Covers legacy DSL values ("daily"/"weekly"),
# yfinance/kite spellings ("60m"/"60minute"/"day"/"week"), and loose forms.
_ALIASES: dict[str, str] = {
    "1m": "1m", "1min": "1m", "1minute": "1m", "minute": "1m",
    "3m": "3m", "3min": "3m", "3minute": "3m",
    "5m": "5m", "5min": "5m", "5minute": "5m",
    "10m": "10m", "10min": "10m", "10minute": "10m",
    "15m": "15m", "15min": "15m", "15minute": "15m",
    "30m": "30m", "30min": "30m", "30minute": "30m",
    "1h": "1h", "60m": "1h", "60min": "1h", "60minute": "1h", "1hour": "1h",
    "1hr": "1h", "hr": "1h", "hrs": "1h", "hour": "1h", "hourly": "1h",
    "1d": "1d", "d": "1d", "day": "1d", "daily": "1d", "1day": "1d", "eod": "1d",
    "1wk": "1wk", "1w": "1wk", "wk": "1wk", "week": "1wk", "weekly": "1wk", "1week": "1wk",
    "1mo": "1mo", "mo": "1mo", "month": "1mo", "monthly": "1mo", "1month": "1mo",
}

# Canonical → yfinance interval string. ``None`` = yfinance cannot serve it.
_TO_YFINANCE: dict[str, Optional[str]] = {
    "1m": "1m", "3m": None, "5m": "5m", "10m": None, "15m": "15m",
    "30m": "30m", "1h": "60m", "1d": "1d", "1wk": "1wk", "1mo": "1mo",
}

# Canonical → Kite interval string. ``None`` = Kite cannot serve it.
_TO_KITE: dict[str, Optional[str]] = {
    "1m": "minute", "3m": "3minute", "5m": "5minute", "10m": "10minute",
    "15m": "15minute", "30m": "30minute", "1h": "60minute", "1d": "day",
    "1wk": "week", "1mo": None,
}

# Per-interval max single-request lookback in calendar days, per source.
# yfinance NSE: 1m→7d, 2-30m/90m→60d, 60m/1h→730d, daily+→full.
# Kite historical_data hard caps: minute 60d, 3-10min 100d, 15-30min 200d,
# 60min 400d, day 2000d. ``None`` = effectively unbounded (full history).
_YF_LOOKBACK_DAYS: dict[str, Optional[int]] = {
    "1m": 7, "3m": None, "5m": 60, "10m": None, "15m": 60,
    "30m": 60, "1h": 730, "1d": None, "1wk": None, "1mo": None,
}
_KITE_LOOKBACK_DAYS: dict[str, Optional[int]] = {
    "1m": 60, "3m": 100, "5m": 100, "10m": 100, "15m": 200,
    "30m": 200, "1h": 400, "1d": 2000, "1wk": 2000, "1mo": None,
}

# Approx number of completed bars per NSE trading session (6h15m = 375 min)
# at each intraday interval, used to annualise intraday returns/vol.
_BARS_PER_SESSION: dict[str, float] = {
    "1m": 375.0, "3m": 125.0, "5m": 75.0, "10m": 37.5,
    "15m": 25.0, "30m": 12.5, "1h": 6.25,
}
_TRADING_DAYS_PER_YEAR = 252.0


def normalize_interval(value: Optional[str]) -> str:
    """Map any accepted spelling/alias to a canonical interval.

    Empty/unknown values fall back to ``"1d"`` (daily) — the historical
    default — so existing daily callers and legacy ``"daily"``/``"weekly"``
    workflows are unchanged.
    """
    if not value:
        return "1d"
    key = str(value).strip().lower().replace(" ", "").replace("-", "").replace("_", "")
    if key in _ALIASES:
        return _ALIASES[key]
    if key in CANONICAL_INTERVALS:
        return key
    return "1d"


def is_intraday(interval: str) -> bool:
    """True for sub-daily intervals (shallow rolling data windows)."""
    return normalize_interval(interval) in _INTRADAY


def to_yfinance(interval: str) -> Optional[str]:
    """Canonical → yfinance interval string, or ``None`` if yfinance can't
    serve it (3m / 10m have no yfinance equivalent)."""
    return _TO_YFINANCE.get(normalize_interval(interval))


def to_kite(interval: str) -> Optional[str]:
    """Canonical → Kite interval string, or ``None`` if Kite can't serve it
    (1mo has no Kite equivalent)."""
    return _TO_KITE.get(normalize_interval(interval))


def max_lookback_days(interval: str, *, has_kite: bool = False) -> Optional[int]:
    """Honest max single-request lookback in calendar days for ``interval``.

    Picks the more generous of the available sources: Kite's deeper intraday
    caps when a session exists, else yfinance. ``None`` = full history
    (daily/weekly/monthly). Used to default the lookback window and to clamp
    over-long requests rather than silently fabricating data.
    """
    norm = normalize_interval(interval)
    yf = _YF_LOOKBACK_DAYS.get(norm)
    if not has_kite:
        # yfinance only. If yfinance can't serve it at all, fall back to the
        # Kite cap so a Kite-only interval still reports a finite window.
        return yf if yf is not None or _TO_YFINANCE.get(norm) is not None \
            else _KITE_LOOKBACK_DAYS.get(norm)
    kite = _KITE_LOOKBACK_DAYS.get(norm)
    caps = [c for c in (yf, kite) if c is not None]
    # If either source serves it with unbounded history, treat as unbounded.
    serves_unbounded = (
        (_TO_YFINANCE.get(norm) is not None and yf is None)
        or (_TO_KITE.get(norm) is not None and kite is None)
    )
    if serves_unbounded:
        return None
    return max(caps) if caps else None


def kite_lookback_days(interval: str) -> Optional[int]:
    """Kite's OWN per-interval single-request cap in days (``None`` = full
    history). Distinct from :func:`max_lookback_days`, which returns the most
    generous cap across sources — when clamping a span for an actual Kite
    request you must use Kite's own cap, not the cross-source maximum."""
    return _KITE_LOOKBACK_DAYS.get(normalize_interval(interval))


def default_period_for(interval: str, *, has_kite: bool = False) -> str:
    """Period string sized to grab ``interval``'s full available window when
    the caller gives no explicit lookback ("as far back as data can be
    availed"). Daily/weekly/monthly default to a generous multi-year window.
    """
    norm = normalize_interval(interval)
    cap = max_lookback_days(norm, has_kite=has_kite)
    if cap is None:
        # Unbounded source → sensible defaults by coarseness.
        if norm == "1mo":
            return "max"
        if norm == "1wk":
            return "5y"
        return "2y"
    # Intraday: request the whole rolling window (e.g. 60d of 15-min bars).
    return f"{int(cap)}d"


def bars_per_year(interval: str) -> float:
    """Annualisation factor (number of bars per year) for ``interval``.

    Daily 252, weekly 52, monthly 12; intraday = 252 × bars-per-session.
    Used to replace the hardcoded ``252**0.5`` so vol/Sharpe maths stays
    meaningful on intraday bars.
    """
    norm = normalize_interval(interval)
    if norm in _BARS_PER_SESSION:
        return _TRADING_DAYS_PER_YEAR * _BARS_PER_SESSION[norm]
    if norm == "1wk":
        return 52.0
    if norm == "1mo":
        return 12.0
    return _TRADING_DAYS_PER_YEAR
