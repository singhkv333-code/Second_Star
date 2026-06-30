"""View Markets — Phase 3 cross-sectional rank engine + factor→smart-beta-ETF map.

Powers the factor-tilt within a theme (spec §3.3, §4.3) and the
factor-ETF-vs-index RELATIVE expression (R3). Textbook cross-sectional is
long-top-decile / short-bottom-decile, rank-demeaned to be dollar-neutral — but
the bottom-decile short is un-executable for retail, so the three honest
expressions (descending fidelity) are:

  1. smart-beta ETF long vs index-future short  → :data:`FACTOR_ETF_MAP`
  2. long top-decile basket + AVOID list        → ``honest_short.avoid_annotation``
  3. F&O-subset dollar-neutral (SSF names only)  → ``honest_short.short_leg_for``

This module provides the rank/decile helpers (pure numpy/stdlib) plus the
factor→ETF catalog. Signals: momentum from Kite OHLCV (12-1), value/quality from
the Moneycontrol fundamentals DB (the caller supplies the scores — same contract
as ``weighting._factor_weights``'s ``scores``).

Functions raise ``NotImplementedError`` in the skeleton; ``FACTOR_ETF_MAP`` is
real data.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, Optional

import numpy as np

if TYPE_CHECKING:  # pragma: no cover - typing only
    from collections.abc import Mapping, Sequence

logger = logging.getLogger(__name__)

FactorName = Literal["momentum", "quality", "value", "low_vol", "multi"]

# Price-derived factors (read off Kite OHLCV); the rest come from the
# fundamentals DB (caller-supplied, like ``weighting._factor_weights``'s scores).
_PRICE_FACTORS: frozenset[str] = frozenset({"momentum", "low_vol"})
_FUNDAMENTAL_FACTORS: frozenset[str] = frozenset({"value", "quality"})

# "multi" expands to the four single factors (composite = best-of-breed, §3.3).
_MULTI_EXPANSION: tuple[str, ...] = ("momentum", "quality", "value", "low_vol")

# 12-1 momentum: trailing 12-month return skipping the most recent month, in
# trading days (mirrors the academic cross-sectional momentum signal).
_MOM_LOOKBACK_DAYS: int = 252
_MOM_SKIP_DAYS: int = 21
_PRICE_PERIOD: str = "2y"
_TRADING_DAYS_YR: float = 252.0


@dataclass(frozen=True)
class FactorETF:
    """One smart-beta ETF mapping for the factor-ETF-vs-index relative leg."""

    factor: str
    index: str          # the underlying NSE factor index
    label: str
    note: str


# Factor → listed Indian smart-beta ETF / index (spec §3.3-#1). The ETF *ticker*
# is pinned to the live instrument master in INTEGRATE; the index + label are
# the stable mapping the card and the relative-pair builder read.
FACTOR_ETF_MAP: dict[str, FactorETF] = {
    "momentum": FactorETF(
        "momentum", "NIFTY200 Momentum 30",
        "Nifty200 Momentum 30",
        "12-1 momentum smart-beta; long vs short NIFTY future for the tilt.",
    ),
    "quality": FactorETF(
        "quality", "NIFTY100 Quality 30",
        "Nifty100 Quality 30",
        "Quality (ROE/leverage/earnings-stability) smart-beta long leg.",
    ),
    "value": FactorETF(
        "value", "NIFTY50 Value 20",
        "Nifty50 Value 20",
        "Value tilt smart-beta long leg.",
    ),
    "low_vol": FactorETF(
        "low_vol", "NIFTY Alpha Low-Volatility 30",
        "Nifty Alpha Low-Vol 30",
        "Low-volatility smart-beta long leg.",
    ),
    "multi": FactorETF(
        "multi", "NIFTY Multi-Factor",
        "Nifty Multi-Factor (value+momentum+quality+low-vol)",
        "Composite multi-factor — beats single-factor (spec §3.3/§4.3).",
    ),
}


def factor_etf(factor: str) -> Optional[FactorETF]:
    """Return the smart-beta ETF mapping for ``factor`` (or ``None``)."""
    return FACTOR_ETF_MAP.get(factor)


def rank_scores(scores: "Mapping[str, float]") -> dict[str, float]:
    """Cross-sectional rank (0..1) of each name's composite factor score.

    Higher score → higher rank. Returns ``{symbol: percentile_rank}``; ties share
    the average rank. Pure ordering — no look-ahead, no weighting.

    The percentile uses the Hazen plotting position ``(avg_rank - 0.5) / n`` so
    values fall strictly inside ``(0, 1)`` and a single name maps to ``0.5``
    (neutral) rather than an undefined ``0/0``. NaN/non-finite scores are dropped.
    """
    clean = {
        sym: float(val)
        for sym, val in scores.items()
        if val is not None and np.isfinite(float(val))
    }
    n = len(clean)
    if n == 0:
        return {}

    out: dict[str, float] = {}
    for sym, val in clean.items():
        n_less = sum(1 for other in clean.values() if other < val)
        n_equal = sum(1 for other in clean.values() if other == val)
        # 1-based average ordinal rank (ties share the mean of their block).
        avg_rank = n_less + (n_equal + 1) / 2.0
        out[sym] = (avg_rank - 0.5) / n
    return out


def decile_split(
    scores: "Mapping[str, float]",
    *,
    n_buckets: int = 10,
) -> dict[int, list[str]]:
    """Partition names into ``n_buckets`` deciles by score (1 = top, ``n`` = bottom).

    Top decile = the long basket; bottom decile = the AVOID list (or the SSF
    short leg in the advanced F&O-subset variant). Returns ``{bucket: [symbols]}``
    with every bucket ``1..n_buckets`` present (empty lists allowed) so callers
    can always read the top (``[1]``) and bottom (``[n_buckets]``) buckets.

    Names are sorted by descending score (ties broken by symbol for determinism)
    and split into contiguous, near-equal-size buckets.
    """
    if n_buckets < 1:
        raise ValueError("n_buckets must be >= 1")

    buckets: dict[int, list[str]] = {b: [] for b in range(1, n_buckets + 1)}

    clean = [
        (sym, float(val))
        for sym, val in scores.items()
        if val is not None and np.isfinite(float(val))
    ]
    n = len(clean)
    if n == 0:
        return buckets

    # Descending by score; symbol as a stable tiebreaker.
    ordered = sorted(clean, key=lambda kv: (-kv[1], kv[0]))
    for pos, (sym, _val) in enumerate(ordered):
        bucket = int(pos * n_buckets / n) + 1
        bucket = min(bucket, n_buckets)
        buckets[bucket].append(sym)
    return buckets


def composite_factor_scores(
    db: object,
    symbols: "Sequence[str]",
    *,
    factors: "Sequence[str]",
    fundamentals: Optional["Mapping[str, object]"] = None,
) -> dict[str, float]:
    """Build a z-scored composite factor score per name from the requested factors.

    Momentum / low-vol from Kite OHLCV (price-derived); value / quality from the
    fundamentals DB (caller-supplied, like ``weighting._factor_weights``'s
    ``scores``). Returns ``{symbol: composite_z}`` for ``rank_scores`` /
    ``decile_split`` and for ``weighting.compute_weights(scheme="factor",
    views=...)``.

    The composite is the equal blend of the z-scored factor columns that carry a
    signal; columns with no signal (e.g. ``value`` requested but no fundamentals
    supplied) are skipped, never fabricated. ``"multi"`` expands to all four base
    factors. When nothing has signal every name gets a neutral ``0.0`` (so the
    downstream weighting tilt is a no-op rather than an invented bet).
    """
    syms = list(symbols)
    if not syms:
        return {}

    requested = _expand_factors(factors)
    price_needed = bool(requested & _PRICE_FACTORS)

    closes: "Mapping[str, object]" = {}
    if price_needed:
        closes = _fetch_closes(syms)

    columns: dict[str, np.ndarray] = {}
    for factor in requested:
        if factor == "momentum":
            columns["momentum"] = _momentum_column(syms, closes)
        elif factor == "low_vol":
            columns["low_vol"] = _low_vol_column(syms, closes)
        elif factor in _FUNDAMENTAL_FACTORS:
            columns[factor] = _fundamental_column(syms, fundamentals, factor)

    composite = np.zeros(len(syms), dtype=float)
    used = 0.0
    for col in columns.values():
        z = _zscore(col)
        if z is None:
            continue
        composite += z
        used += 1.0

    if used > 0.0:
        composite /= used
    # used == 0.0 → composite stays all-zero (neutral, honest no-tilt).
    return {sym: float(composite[i]) for i, sym in enumerate(syms)}


# ── internal helpers ─────────────────────────────────────────────────────────


def _expand_factors(factors: "Sequence[str]") -> set[str]:
    """Normalise the requested factor list (lowercase, expand ``multi``)."""
    out: set[str] = set()
    for f in factors:
        key = str(f).strip().lower()
        if key == "multi":
            out.update(_MULTI_EXPANSION)
        elif key:
            out.add(key)
    return out


def _fetch_closes(symbols: "Sequence[str]") -> "Mapping[str, object]":
    """Fetch Close history (Kite primary, yfinance fallback) — honest ``{}`` on failure."""
    from backend.core.data import historical

    try:
        return dict(historical.get_close_dict(list(symbols), period=_PRICE_PERIOD))
    except Exception as exc:  # pragma: no cover - data layer failure → honest empty
        logger.info("composite_factor_scores: price fetch failed (%s)", exc)
        return {}


def _close_array(obj: object) -> Optional[np.ndarray]:
    """Coerce one symbol's Close history into a clean ascending float array."""
    if obj is None:
        return None
    try:
        import pandas as pd

        if isinstance(obj, pd.Series):
            ser = pd.to_numeric(obj, errors="coerce").dropna()
            if getattr(ser.index, "is_monotonic_increasing", True) is False:
                ser = ser.sort_index()
            arr = ser.to_numpy(dtype=float)
        elif isinstance(obj, pd.DataFrame):
            col = next((c for c in obj.columns if str(c).lower() == "close"), None)
            if col is None:
                return None
            arr = pd.to_numeric(obj[col], errors="coerce").dropna().to_numpy(dtype=float)
        elif isinstance(obj, (list, tuple)):
            vals = [
                rec.get("close", rec.get("Close"))
                for rec in obj
                if isinstance(rec, dict)
            ]
            arr = np.array([v for v in vals if v is not None], dtype=float)
        else:
            return None
    except Exception:  # pragma: no cover - defensive coercion guard
        return None

    arr = arr[np.isfinite(arr)]
    return arr if arr.size >= 2 else None


def _momentum_column(
    symbols: "Sequence[str]", closes: "Mapping[str, object]"
) -> np.ndarray:
    """12-1 momentum (trailing 12m return skipping the last month); NaN where unknown."""
    col = np.full(len(symbols), np.nan)
    for i, sym in enumerate(symbols):
        arr = _close_array(closes.get(sym))
        if arr is None:
            continue
        if arr.size >= _MOM_LOOKBACK_DAYS + 1:
            start = arr[-(_MOM_LOOKBACK_DAYS + 1)]
            end = arr[-(_MOM_SKIP_DAYS + 1)]
        else:  # short history → plain trailing return over what we have
            start = arr[0]
            end = arr[-1]
        if start > 0:
            col[i] = float(end) / float(start) - 1.0
    return col


def _low_vol_column(
    symbols: "Sequence[str]", closes: "Mapping[str, object]"
) -> np.ndarray:
    """Low-volatility factor = negative annualised daily-return vol; NaN where unknown."""
    col = np.full(len(symbols), np.nan)
    for i, sym in enumerate(symbols):
        arr = _close_array(closes.get(sym))
        if arr is None:
            continue
        rets = np.diff(arr) / arr[:-1]
        rets = rets[np.isfinite(rets)]
        if rets.size >= 2:
            col[i] = -float(np.std(rets)) * np.sqrt(_TRADING_DAYS_YR)
    return col


def _fundamental_column(
    symbols: "Sequence[str]",
    fundamentals: Optional["Mapping[str, object]"],
    factor: str,
) -> np.ndarray:
    """Pull a per-name fundamental factor score; NaN where the caller gave none.

    Accepts the caller's ``fundamentals`` as either ``{symbol: score}`` (a blended
    value+quality number reused for both, matching ``weighting._factor_weights``)
    or ``{symbol: {factor: score, ...}}`` (per-factor breakdown).
    """
    col = np.full(len(symbols), np.nan)
    if not fundamentals:
        return col
    for i, sym in enumerate(symbols):
        raw = fundamentals.get(sym)
        if raw is None:
            continue
        if isinstance(raw, dict):
            val = raw.get(factor, raw.get("score", raw.get("value")))
        else:
            val = raw
        try:
            num = float(val)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            continue
        if np.isfinite(num):
            col[i] = num
    return col


def _zscore(col: np.ndarray) -> Optional[np.ndarray]:
    """Z-score a factor column, filling NaNs to the column mean (z=0).

    Returns ``None`` when the column has no finite values or zero spread — i.e.
    no usable signal — so the caller skips it rather than inventing a tilt.
    """
    finite = np.isfinite(col)
    if not finite.any():
        return None
    mean = float(col[finite].mean())
    filled = np.where(finite, col, mean)
    std = float(filled.std())
    if std <= 0.0:
        return None
    return (filled - mean) / std


__all__ = [
    "FactorName",
    "FactorETF",
    "FACTOR_ETF_MAP",
    "factor_etf",
    "rank_scores",
    "decile_split",
    "composite_factor_scores",
]
