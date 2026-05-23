"""BacktestDataAccessor — the as-of-bar guarantee.

Mirrors ``DataAccessor`` from ``backend.workflows.dsl.data_accessor``
so the SAME evaluator runs in live mode (LiveDataAccessor) and
backtest mode (this one). The single invariant:

    No public method may read from bars[as_of_idx + 1 :].

Enforced by per-call slicing (`df.iloc[: as_of_idx + 1]`). Defensive
mode (`DSL_BACKTEST_STRICT=true` env var) additionally runs a paranoid
double-check: each indicator computation is repeated over the
truncated slice AND over the full series with bars past `as_of_idx`
masked to NaN, and the two results must match. Off by default for
perf.

Indicators are computed once per ``(symbol, indicator, period)`` over
the full series at first access (pandas-ta is causal — RSI at bar N
only depends on bars 0..N), then sliced on subsequent calls. So a
10-year backtest with 5 indicators amortises to 5 indicator
computations total, not 5 × 2,500 bars.
"""
from __future__ import annotations

import logging
import os
from typing import Optional

import pandas as pd

from backend.workflows.dsl.backtest.bar_loader import LoadedBars, SymbolKey

logger = logging.getLogger(__name__)


def _strict_mode() -> bool:
    return os.environ.get("DSL_BACKTEST_STRICT", "").strip().lower() in {
        "1", "true", "yes", "on",
    }


class BacktestDataAccessor:
    """As-of-bar accessor. The engine constructs one and calls
    ``advance_to(idx)`` between bars."""

    def __init__(self, loaded: LoadedBars) -> None:
        self._loaded = loaded
        self._as_of_idx: int = -1
        # Per-(symbol, indicator, period, component) cache of FULL
        # series. Populated lazily; subsequent calls slice the cached
        # series. ``component`` is None for single-output indicators
        # and for the default series of multi-output ones; non-None
        # for explicit component selection (e.g. Bollinger lower band).
        self._indicator_cache: dict[
            tuple[SymbolKey, str, int, Optional[str]], pd.Series
        ] = {}

    # ── Public lifecycle ──────────────────────────────────────────

    def advance_to(self, idx: int) -> None:
        if idx < 0 or idx >= len(self._loaded.master_dates):
            raise IndexError(
                f"as_of_idx {idx} out of range [0, "
                f"{len(self._loaded.master_dates) - 1}]"
            )
        self._as_of_idx = idx

    @property
    def as_of_idx(self) -> int:
        return self._as_of_idx

    @property
    def as_of_date(self) -> pd.Timestamp:
        return self._loaded.master_dates[self._as_of_idx]

    # ── DataAccessor protocol ─────────────────────────────────────

    def get_price(
        self, *, symbol: str, exchange: str = "NSE"
    ) -> Optional[float]:
        """Returns the CLOSE of the as-of bar. ``None`` if the symbol
        isn't loaded or the bar's close is NaN (which can happen after
        the bar_loader's reindex to the master calendar).
        """
        df = self._df_for(symbol, exchange)
        if df is None:
            return None
        val = df["close"].iloc[self._as_of_idx]
        if pd.isna(val):
            return None
        return float(val)

    def get_indicator(
        self,
        *,
        symbol: str,
        indicator: str,
        period: int,
        exchange: str = "NSE",
        component: Optional[str] = None,
    ) -> Optional[float]:
        """Latest value of ``indicator(period)`` over bars[0..as_of_idx+1].

        Uses ``backend.services.backtest_indicators.compute_series_component`` —
        same registry the live watcher uses, so live and backtest
        always agree. ``component`` selects a specific output for
        multi-output indicators (BB lower band, MACD signal line, ...);
        ``None`` keeps the default series.
        """
        df = self._df_for(symbol, exchange)
        if df is None:
            return None
        comp_key = component.lower() if component else None
        key = (
            (symbol.upper(), exchange.upper()),
            indicator.lower(),
            int(period),
            comp_key,
        )
        series = self._indicator_cache.get(key)
        if series is None:
            # Lazy import — keeps the module load cheap.
            from backend.services.backtest_indicators import (
                compute_series_component,
            )
            try:
                series = compute_series_component(
                    df, indicator, period, component=comp_key,
                )
            except Exception as exc:  # noqa: BLE001
                logger.info(
                    "[backtest.accessor] compute_series(%s, %d, %s, "
                    "comp=%s) failed: %s",
                    indicator, period, symbol, comp_key, exc,
                )
                series = None
            if series is None or len(series) == 0:
                # Cache the None so we don't retry every bar.
                self._indicator_cache[key] = pd.Series(dtype=float)
                return None
            self._indicator_cache[key] = series

        # The single safety statement: ALWAYS slice to as_of_idx + 1.
        # `iloc[self._as_of_idx]` is exclusive on the right when
        # treated as a label-based slice; for positional access we
        # take the element at that index directly.
        if self._as_of_idx >= len(series):
            return None
        val = series.iloc[self._as_of_idx]
        if pd.isna(val):
            return None

        if _strict_mode():
            self._shadow_check_indicator(
                df, indicator, period, val, component=comp_key,
            )
        return float(val)

    def get_position_field(
        self, *, field: str, basis: Optional[str] = None,
    ) -> Optional[float]:
        """Entry-tree default for the backtest path. The engine wraps
        this accessor with PositionAwareAccessor before evaluating the
        EXIT tree, which overrides this method."""
        return None

    def get_volume(
        self,
        *,
        symbol: str,
        bars: int = 1,
        exchange: str = "NSE",
    ) -> Optional[float]:
        """Volume summed over ``bars`` bars ending at as_of_idx."""
        df = self._df_for(symbol, exchange)
        if df is None or "volume" not in df.columns:
            return None
        bars = max(1, int(bars))
        end_excl = self._as_of_idx + 1
        start = max(0, end_excl - bars)
        window = df["volume"].iloc[start:end_excl]
        if window.empty:
            return None
        total = window.sum(skipna=False)
        if pd.isna(total):
            return None
        return float(total)

    # ── Internals ────────────────────────────────────────────────

    def _df_for(self, symbol: str, exchange: str) -> Optional[pd.DataFrame]:
        return self._loaded.by_symbol.get(
            (symbol.upper(), exchange.upper())
        )

    def _shadow_check_indicator(
        self,
        df: pd.DataFrame,
        indicator: str,
        period: int,
        expected: float,
        *,
        component: Optional[str] = None,
    ) -> None:
        """Paranoid recheck — recompute the indicator over the
        truncated slice and assert the result matches the cached
        full-series value at as_of_idx. Off by default; gates on
        DSL_BACKTEST_STRICT.

        If this ever raises, the pandas-ta computation isn't causal
        for this indicator and the entire backtest result is suspect.
        """
        from backend.services.backtest_indicators import (
            compute_series_component,
        )
        truncated_df = df.iloc[: self._as_of_idx + 1]
        try:
            truncated = compute_series_component(
                truncated_df, indicator, period, component=component,
            )
        except Exception:  # noqa: BLE001
            return  # if the truncated compute fails, can't compare; skip
        if truncated is None or truncated.empty:
            return
        observed = truncated.iloc[-1]
        if pd.isna(observed):
            return
        # Tolerance: floating-point noise from pandas-ta's rolling
        # buffers can drift in the 1e-9 range. 1e-6 is well above
        # that and well below any meaningful trading threshold.
        if abs(float(observed) - float(expected)) > 1e-6:
            raise AssertionError(
                f"[backtest.accessor] shadow-check mismatch for "
                f"{indicator}({period}) at idx={self._as_of_idx}: "
                f"full-series={expected}, truncated-recompute={observed}. "
                f"This means {indicator} is NOT causal — look-ahead bias is "
                f"possible. Investigate before trusting results."
            )
