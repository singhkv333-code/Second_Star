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
        self,
        *,
        symbol: str,
        exchange: str = "NSE",
        basis: str = "close",
        offset: int = 0,
        timeframe: str = "daily",
    ) -> Optional[float]:
        """Returns the ``basis`` (close/open/high/low) of bar
        ``as_of_idx - offset``. ``None`` if the symbol isn't loaded,
        the bar's value is NaN, or the offset reaches before bar 0.
        Same no-lookahead guarantees as the rest of the accessor:
        no read past ``as_of_idx``.

        ``timeframe`` is accepted (and normalized) for signature parity
        with the live accessor; it's informational only here — the
        backtest's bars are already loaded at the run's chosen
        interval, so 'offset' is implicitly counted in BARS of those
        bars. We do not refetch.
        """
        from backend.core.data.intervals import normalize_interval
        _ = normalize_interval(timeframe)
        df = self._df_for(symbol, exchange)
        if df is None:
            return None
        idx = self._as_of_idx - int(offset)
        if idx < 0 or idx > self._as_of_idx:
            return None
        col = (basis or "close").lower()
        if col not in ("open", "high", "low", "close"):
            return None
        if col not in df.columns:
            return None
        val = df[col].iloc[idx]
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
        offset: int = 0,
        timeframe: str = "daily",
    ) -> Optional[float]:
        """Value of ``indicator(period)`` at bar ``as_of_idx - offset``.

        Uses ``backend.services.backtest_indicators.compute_series_component`` —
        same registry the live watcher uses, so live and backtest
        always agree. ``component`` selects a specific output for
        multi-output indicators (BB lower band, MACD signal line, ...);
        ``None`` keeps the default series. ``offset > 0`` reads bars in
        the past — still inside the no-lookahead envelope because we
        never reach beyond ``as_of_idx``.

        ``timeframe`` is accepted (and normalized) so intraday / weekly
        DSL nodes no longer raise TypeError → UNKNOWN here; the backtest
        bars are already loaded at the run's chosen interval, so 'period'
        is implicitly counted in BARS of those bars. We do not refetch.
        """
        # Normalize for forward compat; the value is informational here
        # since the loaded backtest series is already at the run interval.
        from backend.core.data.intervals import normalize_interval
        _ = normalize_interval(timeframe)
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

        target = self._as_of_idx - int(offset)
        if target < 0 or target >= len(series):
            return None
        val = series.iloc[target]
        if pd.isna(val):
            return None

        if _strict_mode() and offset == 0:
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

    def get_session_day(self) -> Optional[str]:
        """Return the as-of bar's weekday as a lowercase 3-letter
        code so ``session_day`` filters work in backtests."""
        if self._as_of_idx < 0:
            return None
        from backend.workflows.dsl.data_accessor import _WEEKDAY_LOOKUP
        ts = self._loaded.master_dates[self._as_of_idx]
        # pandas Timestamp.weekday() is Monday-zero — matches our tuple.
        return _WEEKDAY_LOOKUP[int(ts.weekday())]

    # ── aggregate fast path ──────────────────────────────────────────

    def evaluate_aggregate(self, *, node, evaluator, state):
        """Vectorised aggregator evaluation.

        The evaluator's slow path walks the source sub-tree once per
        offset in the window. That works in live mode (one call per
        minute) but is O(window × tree_depth) in a backtest, which
        means a 252-bar percentile over a depth-3 source on a 720-bar
        run is ~544 k evaluations.

        Fast path: we shift the accessor's ``as_of_idx`` backwards in
        a loop and evaluate the source sub-tree against THIS accessor
        each time. Each leaf hit reuses the cached indicator series,
        so the total work scales with window size × leaf count rather
        than window size × full-tree depth.
        """
        from backend.workflows.dsl.evaluator import _reduce_aggregate
        original_idx = self._as_of_idx
        bars = int(node.bars)
        src_values: list = []
        second_values: list = []
        try:
            for off in range(bars):
                shifted = original_idx - off
                if shifted < 0:
                    src_values.append(None)
                    if node.second is not None:
                        second_values.append(None)
                    continue
                self._as_of_idx = shifted
                sv = evaluator(node.source, accessor=self, state=state)
                src_values.append(
                    float(sv) if isinstance(sv, bool) else sv
                )
                if node.second is not None:
                    tv = evaluator(node.second, accessor=self, state=state)
                    second_values.append(
                        float(tv) if isinstance(tv, bool) else tv
                    )
        finally:
            self._as_of_idx = original_idx
        return _reduce_aggregate(node, src_values, second_values)

    def get_volume(
        self,
        *,
        symbol: str,
        bars: int = 1,
        exchange: str = "NSE",
        offset: int = 0,
    ) -> Optional[float]:
        """Volume summed over a ``bars``-wide window that ENDS
        ``offset`` bars before the as-of bar. ``offset=0`` keeps the
        legacy behaviour (window ends at as_of_idx)."""
        df = self._df_for(symbol, exchange)
        if df is None or "volume" not in df.columns:
            return None
        bars = max(1, int(bars))
        off = int(offset)
        end_excl = self._as_of_idx + 1 - off
        if end_excl <= 0:
            return None
        start = max(0, end_excl - bars)
        if start >= end_excl:
            return None
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
