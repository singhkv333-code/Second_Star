"""Unified indicator registry for backtesting and live evaluation.

Single source of truth for every technical indicator the workflow
backtester, the single-symbol indicator backtester, the trigger.indicator
watcher, and the fetch.indicator step can compute. Adding a new indicator
means one ``register()`` call here — all five sites pick it up.

Design
======

Each indicator exposes:

  ``compute(bars, period) -> pd.Series``
    Returns a single canonical scalar series aligned to ``bars.index``.
    For composite indicators (MACD, Bollinger, Supertrend, etc.) we pick
    the one most useful component for threshold-based gating:
      * MACD       → histogram (macd − signal); 0 = crossover.
      * Bollinger  → %B = (close − lower) / (upper − lower); 0/1 = bands.
      * Supertrend → direction (+1 long / −1 short); 0 = the flip.
      * Stochastic → %K (0–100).
      * Aroon      → oscillator (up − down, −100…+100).
      * PSAR       → the active stop level (compare against close).
      * Keltner    → midline (basis EMA).
      * Donchian   → midline.
      * VWAP       → daily VWAP.

  ``basis: 'value' | 'price'``
    'value' → trigger semantics compare the series directly to the
              user's threshold (RSI < 30 → series < 30).
    'price' → trigger semantics compare the day's CLOSE to the series
              (close < SMA(50) → close vs the series at that bar).

  ``default_period: int``
    Used by the chat heuristic when the user omits a period.

Bars contract
=============
Callers MUST pass a DataFrame with columns ``Open, High, Low, Close,
Volume`` (title-case, the yfinance native shape). The thin
``_normalise_bars`` helper accepts the lowercase Kite shape too —
downstream callers that fetch from Kite call it before computing.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Literal, Optional

import pandas as pd  # type: ignore[import-untyped]
import pandas_ta_classic as ta  # type: ignore[import-untyped]


Basis = Literal["value", "price"]


@dataclass(frozen=True)
class IndicatorSpec:
    key: str
    label: str
    basis: Basis
    default_period: int
    compute: Callable[[pd.DataFrame, int], Optional[pd.Series]]


_REGISTRY: dict[str, IndicatorSpec] = {}


def _register(spec: IndicatorSpec) -> IndicatorSpec:
    _REGISTRY[spec.key] = spec
    return spec


def _close(bars: pd.DataFrame) -> pd.Series:
    return bars["Close"].astype(float)


def _hlc(bars: pd.DataFrame) -> tuple[pd.Series, pd.Series, pd.Series]:
    return (
        bars["High"].astype(float),
        bars["Low"].astype(float),
        bars["Close"].astype(float),
    )


def _hlcv(
    bars: pd.DataFrame,
) -> tuple[pd.Series, pd.Series, pd.Series, pd.Series]:
    return (
        bars["High"].astype(float),
        bars["Low"].astype(float),
        bars["Close"].astype(float),
        bars["Volume"].astype(float),
    )


# ── Trend ────────────────────────────────────────────────────────────


_register(IndicatorSpec(
    key="sma",
    label="Simple Moving Average",
    basis="price",
    default_period=50,
    compute=lambda bars, n: ta.sma(_close(bars), length=n),
))


_register(IndicatorSpec(
    key="ema",
    label="Exponential Moving Average",
    basis="price",
    default_period=50,
    compute=lambda bars, n: ta.ema(_close(bars), length=n),
))


_register(IndicatorSpec(
    key="wma",
    label="Weighted Moving Average",
    basis="price",
    default_period=20,
    compute=lambda bars, n: ta.wma(_close(bars), length=n),
))


def _macd_hist(bars: pd.DataFrame, n: int) -> Optional[pd.Series]:
    """Return the MACD histogram (macd − signal). The user's `period`
    is treated as the slow EMA length so 'macd > 0' on default 26 still
    works without exposing fast/slow/signal as separate config."""
    df = ta.macd(
        _close(bars),
        fast=12,
        slow=max(int(n), 13),
        signal=9,
    )
    if df is None or df.empty:
        return None
    col = next((c for c in df.columns if c.startswith("MACDh_")), None)
    return df[col] if col else None


_register(IndicatorSpec(
    key="macd",
    label="MACD Histogram",
    basis="value",
    default_period=26,
    compute=_macd_hist,
))


def _adx(bars: pd.DataFrame, n: int) -> Optional[pd.Series]:
    h, l, c = _hlc(bars)
    df = ta.adx(h, l, c, length=n)
    if df is None or df.empty:
        return None
    col = next((c for c in df.columns if c.startswith("ADX_")), None)
    return df[col] if col else None


_register(IndicatorSpec(
    key="adx",
    label="Average Directional Index",
    basis="value",
    default_period=14,
    compute=_adx,
))


def _supertrend(bars: pd.DataFrame, n: int) -> Optional[pd.Series]:
    """Return the trend direction: +1 (long), −1 (short). Threshold 0
    catches every flip."""
    h, l, c = _hlc(bars)
    df = ta.supertrend(h, l, c, length=n, multiplier=3.0)
    if df is None or df.empty:
        return None
    col = next((c for c in df.columns if c.startswith("SUPERTd_")), None)
    return df[col] if col else None


_register(IndicatorSpec(
    key="supertrend",
    label="Supertrend Direction",
    basis="value",
    default_period=10,
    compute=_supertrend,
))


def _psar(bars: pd.DataFrame, n: int) -> Optional[pd.Series]:
    """Return the active PSAR stop level (l on long, s on short). Compare
    against close: close > PSAR ⇒ uptrend."""
    h, l, c = _hlc(bars)
    df = ta.psar(h, l, c)
    if df is None or df.empty:
        return None
    long_col = next((c for c in df.columns if c.startswith("PSARl_")), None)
    short_col = next((c for c in df.columns if c.startswith("PSARs_")), None)
    if long_col is None or short_col is None:
        return None
    return df[long_col].combine_first(df[short_col])


_register(IndicatorSpec(
    key="psar",
    label="Parabolic SAR",
    basis="price",
    default_period=0,
    compute=_psar,
))


def _aroon(bars: pd.DataFrame, n: int) -> Optional[pd.Series]:
    h, l, _ = _hlc(bars)
    df = ta.aroon(h, l, length=n)
    if df is None or df.empty:
        return None
    col = next((c for c in df.columns if c.startswith("AROONOSC_")), None)
    return df[col] if col else None


_register(IndicatorSpec(
    key="aroon",
    label="Aroon Oscillator",
    basis="value",
    default_period=14,
    compute=_aroon,
))


# ── Momentum ─────────────────────────────────────────────────────────


_register(IndicatorSpec(
    key="rsi",
    label="Relative Strength Index",
    basis="value",
    default_period=14,
    compute=lambda bars, n: ta.rsi(_close(bars), length=n),
))


def _stoch_k(bars: pd.DataFrame, n: int) -> Optional[pd.Series]:
    h, l, c = _hlc(bars)
    df = ta.stoch(h, l, c, k=n, d=3)
    if df is None or df.empty:
        return None
    col = next((c for c in df.columns if c.startswith("STOCHk_")), None)
    return df[col] if col else None


_register(IndicatorSpec(
    key="stoch",
    label="Stochastic %K",
    basis="value",
    default_period=14,
    compute=_stoch_k,
))


def _stoch_rsi_k(bars: pd.DataFrame, n: int) -> Optional[pd.Series]:
    df = ta.stochrsi(_close(bars), length=n)
    if df is None or df.empty:
        return None
    col = next((c for c in df.columns if c.startswith("STOCHRSIk_")), None)
    return df[col] if col else None


_register(IndicatorSpec(
    key="stoch_rsi",
    label="Stochastic RSI %K",
    basis="value",
    default_period=14,
    compute=_stoch_rsi_k,
))


_register(IndicatorSpec(
    key="cci",
    label="Commodity Channel Index",
    basis="value",
    default_period=20,
    compute=lambda bars, n: ta.cci(*_hlc(bars), length=n),
))


_register(IndicatorSpec(
    key="williams_r",
    label="Williams %R",
    basis="value",
    default_period=14,
    compute=lambda bars, n: ta.willr(*_hlc(bars), length=n),
))


_register(IndicatorSpec(
    key="mfi",
    label="Money Flow Index",
    basis="value",
    default_period=14,
    compute=lambda bars, n: ta.mfi(*_hlcv(bars), length=n),
))


_register(IndicatorSpec(
    key="roc",
    label="Rate of Change",
    basis="value",
    default_period=10,
    compute=lambda bars, n: ta.roc(_close(bars), length=n),
))


def _trix(bars: pd.DataFrame, n: int) -> Optional[pd.Series]:
    df = ta.trix(_close(bars), length=n)
    if df is None or df.empty:
        return None
    col = next((c for c in df.columns if c.startswith("TRIX_")), None)
    return df[col] if col else None


_register(IndicatorSpec(
    key="trix",
    label="TRIX",
    basis="value",
    default_period=18,
    compute=_trix,
))


# ── Volatility ───────────────────────────────────────────────────────


def _bb_pctb(bars: pd.DataFrame, n: int) -> Optional[pd.Series]:
    """Return Bollinger %B: (close − lower) / (upper − lower). 0 = at
    lower band, 1 = at upper band, <0 / >1 = breakout."""
    df = ta.bbands(_close(bars), length=n, std=2.0)
    if df is None or df.empty:
        return None
    col = next((c for c in df.columns if c.startswith("BBP_")), None)
    return df[col] if col else None


_register(IndicatorSpec(
    key="bollinger",
    label="Bollinger %B",
    basis="value",
    default_period=20,
    compute=_bb_pctb,
))
# Common alias.
_REGISTRY["bb"] = _REGISTRY["bollinger"]


_register(IndicatorSpec(
    key="atr",
    label="Average True Range",
    basis="value",
    default_period=14,
    compute=lambda bars, n: ta.atr(*_hlc(bars), length=n),
))


def _kc_mid(bars: pd.DataFrame, n: int) -> Optional[pd.Series]:
    df = ta.kc(*_hlc(bars), length=n)
    if df is None or df.empty:
        return None
    col = next((c for c in df.columns if c.startswith("KCBe_") or c.startswith("KCMe_")), None)
    return df[col] if col else None


_register(IndicatorSpec(
    key="keltner",
    label="Keltner Channel midline",
    basis="price",
    default_period=20,
    compute=_kc_mid,
))


def _donchian_mid(bars: pd.DataFrame, n: int) -> Optional[pd.Series]:
    h, l, _ = _hlc(bars)
    df = ta.donchian(h, l, lower_length=n, upper_length=n)
    if df is None or df.empty:
        return None
    col = next((c for c in df.columns if c.startswith("DCM_")), None)
    return df[col] if col else None


_register(IndicatorSpec(
    key="donchian",
    label="Donchian Channel midline",
    basis="price",
    default_period=20,
    compute=_donchian_mid,
))


# ── Volume ───────────────────────────────────────────────────────────


_register(IndicatorSpec(
    key="obv",
    label="On-Balance Volume",
    basis="value",
    default_period=0,
    compute=lambda bars, n: ta.obv(_close(bars), bars["Volume"].astype(float)),
))


def _volume_ma(bars: pd.DataFrame, n: int) -> Optional[pd.Series]:
    """Rolling mean of daily volume. Compared against today's volume to
    detect spikes ('volume above 20-day average')."""
    return bars["Volume"].astype(float).rolling(window=int(n)).mean()


_register(IndicatorSpec(
    key="volume_ma",
    label="Volume Moving Average",
    basis="value",
    default_period=20,
    compute=_volume_ma,
))


def _volume_roc(bars: pd.DataFrame, n: int) -> Optional[pd.Series]:
    """Volume rate of change — % change in volume over n bars."""
    return ta.roc(bars["Volume"].astype(float), length=int(n))


_register(IndicatorSpec(
    key="volume_roc",
    label="Volume Rate of Change",
    basis="value",
    default_period=10,
    compute=_volume_roc,
))


def _volume(bars: pd.DataFrame, n: int) -> Optional[pd.Series]:
    """Raw daily volume — the comparison series the user typically
    means by 'volume' on its own. Compared against volume_ma in the
    trigger.indicator + fetch.indicator chain."""
    return bars["Volume"].astype(float)


_register(IndicatorSpec(
    key="volume",
    label="Daily Volume",
    basis="value",
    default_period=0,
    compute=_volume,
))


def _vwap(bars: pd.DataFrame, n: int) -> Optional[pd.Series]:
    """VWAP needs a DatetimeIndex with a date component for resets.
    pandas_ta_classic's vwap groups by day automatically when the index
    is daily. Returns NaNs on weekly/monthly bars; that's fine."""
    h, l, c, v = _hlcv(bars)
    return ta.vwap(h, l, c, v)


_register(IndicatorSpec(
    key="vwap",
    label="Volume-Weighted Average Price",
    basis="price",
    default_period=0,
    compute=_vwap,
))


# ── Public surface ───────────────────────────────────────────────────


def supported_indicators() -> tuple[str, ...]:
    """Stable-sorted tuple of every indicator key (incl. aliases). Use
    this for schema validation so newly-registered indicators are
    accepted without code edits in the validator."""
    return tuple(sorted(_REGISTRY.keys()))


def get_spec(indicator: str) -> Optional[IndicatorSpec]:
    """Lookup helper. Returns None for unknown keys (caller decides
    whether that's a 400 / a no-op / a warning)."""
    return _REGISTRY.get((indicator or "").strip().lower())


def normalise_bars(bars: pd.DataFrame) -> pd.DataFrame:
    """Accept either the yfinance shape (Open/High/Low/Close/Volume,
    title-case) or the Kite shape (lowercase). Returns a frame with the
    title-case columns the registry expects."""
    if bars is None or bars.empty:
        return bars
    cols = {str(c).lower(): c for c in bars.columns}
    rename: dict[str, str] = {}
    for canonical in ("open", "high", "low", "close", "volume"):
        src = cols.get(canonical)
        if src is None:
            continue
        target = canonical.capitalize()
        if src != target:
            rename[src] = target
    return bars.rename(columns=rename) if rename else bars


def compute_series(
    bars: pd.DataFrame, indicator: str, period: Optional[int] = None,
) -> Optional[pd.Series]:
    """Compute the canonical comparison series for ``indicator`` at the
    given ``period``. Returns None when the indicator is unknown or the
    series is entirely NaN."""
    spec = get_spec(indicator)
    if spec is None:
        return None
    n = int(period) if period and period > 0 else spec.default_period
    bars_norm = normalise_bars(bars)
    series = spec.compute(bars_norm, n)
    if series is None or series.dropna().empty:
        return None
    return series


def latest_value(
    bars: pd.DataFrame, indicator: str, period: Optional[int] = None,
) -> Optional[float]:
    """Convenience for live paths (watcher / fetch.indicator step):
    return the most recent non-NaN scalar of the canonical series, or
    None when unavailable."""
    s = compute_series(bars, indicator, period)
    if s is None:
        return None
    s = s.dropna()
    return float(s.iloc[-1]) if not s.empty else None


def basis_for(indicator: str) -> Optional[Basis]:
    spec = get_spec(indicator)
    return spec.basis if spec else None


def default_period_for(indicator: str) -> Optional[int]:
    spec = get_spec(indicator)
    return spec.default_period if spec else None
