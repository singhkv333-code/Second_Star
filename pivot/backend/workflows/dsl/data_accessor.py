"""DataAccessor — single abstraction over market data lookups.

The Protocol below is what the tree evaluator calls. Two concrete
implementations:

  - ``LiveDataAccessor``    — wraps backend.kite.market_data +
                              backend.services.backtest_indicators.
                              Used by the watcher (Phase D6).
  - ``BacktestDataAccessor`` — a thin stub for now; bar-strict
                               version lands in the backtester PR.

Every method returns ``Optional[float]``. None means "data not yet
available" and propagates through the evaluator as ``Ternary.UNKNOWN``
rather than firing or holding spuriously.

Why a per-walk cache:
  A tree like ``RSI(TCS, 14) > 30 AND RSI(TCS, 14) < 70`` references
  RSI(TCS, 14) twice. Without caching, the watcher would compute
  the same indicator twice in a single tick. The cache is the
  ``_call_cache`` dict on the live accessor — keyed by
  ``(method, symbol, indicator?, period?, exchange)``. Lifetime is one
  tree walk; the accessor is constructed fresh per evaluation in the
  watcher, so cross-tick caching falls back to whatever Redis layer
  the underlying market-data clients have.
"""
from __future__ import annotations

import logging
from typing import Optional, Protocol, runtime_checkable

logger = logging.getLogger(__name__)


# ── Protocol ─────────────────────────────────────────────────────────


_WEEKDAY_LOOKUP: tuple[str, ...] = (
    "mon", "tue", "wed", "thu", "fri", "sat", "sun",
)


@runtime_checkable
class DataAccessor(Protocol):
    """Surface every DSL leaf-node ultimately resolves through.

    Methods accept an ``offset`` (default 0 = current bar). For live
    accessors, offset > 0 reads from cached history; for backtest
    accessors it reads from the bar series at ``as_of_idx - offset``.
    """

    def get_price(
        self,
        *,
        symbol: str,
        exchange: str = "NSE",
        basis: str = "close",
        offset: int = 0,
    ) -> Optional[float]:
        ...

    def get_indicator(
        self,
        *,
        symbol: str,
        indicator: str,
        period: int,
        exchange: str = "NSE",
        component: Optional[str] = None,
        offset: int = 0,
    ) -> Optional[float]:
        ...

    def get_volume(
        self,
        *,
        symbol: str,
        bars: int = 1,
        exchange: str = "NSE",
        offset: int = 0,
    ) -> Optional[float]:
        ...

    def get_position_field(
        self,
        *,
        field: str,
        basis: Optional[str] = None,
    ) -> Optional[float]:
        """Return a property of the currently-open position, or
        ``None`` when no position is open (entry-tree context).

        The default implementation in non-position-aware accessors
        returns None so the Kleene UNKNOWN behaviour kicks in
        automatically — entry trees with a stray ``position`` leaf
        evaluate to UNKNOWN rather than crashing.
        """
        return None

    def get_session_day(self) -> Optional[str]:
        """Return the as-of bar's weekday as a lowercase 3-letter
        code (``mon`` .. ``sun``), or ``None`` when the accessor
        can't determine it. Used by the ``session_day`` leaf."""
        return None


# ── Live implementation ─────────────────────────────────────────────


class LiveDataAccessor:
    """Real-time accessor for the watcher tick.

    All three methods catch exceptions from the underlying market-data
    layer and return None, so a transient yfinance / Kite outage
    doesn't crash the evaluator. The structured log line on failure
    is the operator's signal to investigate.
    """

    def __init__(self) -> None:
        # Per-walk cache so the same indicator isn't computed twice
        # inside a single tree (e.g. RSI > 30 AND RSI < 70).
        self._call_cache: dict[tuple, Optional[float]] = {}

    # ── price ──

    def get_price(
        self,
        *,
        symbol: str,
        exchange: str = "NSE",
        basis: str = "close",
        offset: int = 0,
    ) -> Optional[float]:
        cache_key = (
            "price", symbol.upper(), exchange.upper(),
            basis.lower(), int(offset),
        )
        if cache_key in self._call_cache:
            return self._call_cache[cache_key]

        # offset==0 + basis==close → fast path via the live quote.
        if offset == 0 and basis.lower() == "close":
            result = self._live_close(symbol, exchange)
            self._call_cache[cache_key] = result
            return result

        # offset > 0 or non-close basis → fall through to historical
        # daily OHLCV.
        result = self._historical_bar_price(
            symbol, basis=basis.lower(), offset=int(offset),
        )
        self._call_cache[cache_key] = result
        return result

    def _live_close(
        self, symbol: str, exchange: str,
    ) -> Optional[float]:
        try:
            from backend.kite.market_data import get_live_quote
        except ImportError as exc:  # pragma: no cover — would mean broken install
            logger.warning("[dsl.data_accessor] market_data import failed: %s", exc)
            return None
        try:
            quote = get_live_quote(symbol, exchange=exchange)
        except Exception as exc:  # noqa: BLE001
            logger.info(
                "[dsl.data_accessor] get_live_quote failed for %s: %s",
                symbol, exc,
            )
            return None
        if not quote:
            return None
        for k in ("last_price", "ltp", "price", "close"):
            v = quote.get(k) if isinstance(quote, dict) else None
            if v is None:
                continue
            try:
                return float(v)
            except (TypeError, ValueError):
                continue
        return None

    def _historical_bar_price(
        self, symbol: str, *, basis: str, offset: int,
    ) -> Optional[float]:
        """Pull the OHLC at (-1 - offset) from the cached historical
        OHLCV. Used for any non-zero offset or non-close basis."""
        try:
            from backend.kite.market_data import (
                get_historical_ohlcv, period_for_bars,
            )
        except ImportError:  # pragma: no cover
            return None
        try:
            # Price offsets are small; size the window to the offset (+ margin).
            bars = get_historical_ohlcv(
                symbol, period=period_for_bars(int(offset) + 5, cap="1y"),
                interval="1d",
            ) or []
        except Exception as exc:  # noqa: BLE001
            logger.info(
                "[dsl.data_accessor] historical fetch (price) failed for %s: %s",
                symbol, exc,
            )
            return None
        if not bars or len(bars) <= offset:
            return None
        bar = bars[-1 - offset]
        if not isinstance(bar, dict):
            return None
        key_map = {"open": "open", "high": "high", "low": "low", "close": "close"}
        v = bar.get(key_map.get(basis, "close"))
        if v is None:
            return None
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    # ── indicator ──

    def get_indicator(
        self,
        *,
        symbol: str,
        indicator: str,
        period: int,
        exchange: str = "NSE",
        component: Optional[str] = None,
        offset: int = 0,
    ) -> Optional[float]:
        comp_key = component.lower() if component else None
        cache_key = (
            "indicator", symbol.upper(), indicator.lower(),
            int(period), exchange.upper(), comp_key, int(offset),
        )
        if cache_key in self._call_cache:
            return self._call_cache[cache_key]

        try:
            import pandas as pd  # type: ignore[import-untyped]
            from backend.kite.market_data import (
                get_historical_ohlcv, period_for_indicator,
            )
            from backend.services.backtest_indicators import (
                compute_series_component,
            )
        except ImportError as exc:  # pragma: no cover
            logger.warning("[dsl.data_accessor] indicator deps missing: %s", exc)
            self._call_cache[cache_key] = None
            return None

        try:
            # P0 parity: window sized to the indicator period + offset (was a
            # hardcoded "6mo" that silently starved any period > ~120 live).
            bars = get_historical_ohlcv(
                symbol,
                period=period_for_indicator(int(period or 0), offset=int(offset)),
                interval="1d",
            ) or []
        except Exception as exc:  # noqa: BLE001
            logger.info(
                "[dsl.data_accessor] historical fetch failed for %s: %s",
                symbol, exc,
            )
            self._call_cache[cache_key] = None
            return None

        # Same minimum-history guard the watcher's
        # _compute_indicator_sync uses. Below this floor the indicator
        # would either be NaN (some series) or misleading (volatile
        # rolling-window numbers).
        if len(bars) < max(int(period or 0) + 5, 20) + int(offset):
            self._call_cache[cache_key] = None
            return None

        df = pd.DataFrame(bars)
        try:
            series = compute_series_component(
                df, indicator, period, component=comp_key,
            )
        except Exception as exc:  # noqa: BLE001
            logger.info(
                "[dsl.data_accessor] compute_series(%s, %d, comp=%s) on %s "
                "failed: %s",
                indicator, period, comp_key, symbol, exc,
            )
            series = None

        if series is None:
            self._call_cache[cache_key] = None
            return None
        cleaned = series.dropna()
        if cleaned.empty or len(cleaned) <= int(offset):
            self._call_cache[cache_key] = None
            return None
        value = cleaned.iloc[-1 - int(offset)]
        result = None if value is None else float(value)
        self._call_cache[cache_key] = result
        return result

    # ── position (entry-tree default — always None) ──

    def get_position_field(
        self, *, field: str, basis: Optional[str] = None,
    ) -> Optional[float]:
        """The live watcher's entry-tree evaluation never has an
        open position context — return None so the Kleene UNKNOWN
        path applies. Position-aware exit-tree evaluation uses a
        wrapper accessor that overrides this."""
        return None

    # ── session_day ──

    def get_session_day(self) -> Optional[str]:
        """Live evaluation runs once per minute; the relevant day is
        today's IST date. Returns ``mon`` .. ``sun``."""
        from datetime import datetime
        try:
            from zoneinfo import ZoneInfo
            now = datetime.now(ZoneInfo("Asia/Kolkata"))
        except Exception:  # noqa: BLE001 — pre-Py3.9 or missing tzdata
            now = datetime.now()
        return _WEEKDAY_LOOKUP[now.weekday()]

    # ── volume ──

    def get_volume(
        self,
        *,
        symbol: str,
        bars: int = 1,
        exchange: str = "NSE",
        offset: int = 0,
    ) -> Optional[float]:
        cache_key = (
            "volume", symbol.upper(), int(bars), exchange.upper(),
            int(offset),
        )
        if cache_key in self._call_cache:
            return self._call_cache[cache_key]

        try:
            from backend.kite.market_data import (
                get_historical_ohlcv, period_for_bars,
            )
        except ImportError:  # pragma: no cover
            self._call_cache[cache_key] = None
            return None

        try:
            # P0 parity: size to the volume window (+offset+margin) so a
            # "volume above 50-day average" rule fires live (was hardcoded
            # "3mo" ≈ 63 bars, which starved windows > ~50).
            ohlcv = get_historical_ohlcv(
                symbol,
                period=period_for_bars(int(bars) + int(offset) + 5, cap="2y"),
                interval="1d",
            ) or []
        except Exception as exc:  # noqa: BLE001
            logger.info(
                "[dsl.data_accessor] historical fetch (volume) failed for %s: %s",
                symbol, exc,
            )
            self._call_cache[cache_key] = None
            return None
        if not ohlcv:
            self._call_cache[cache_key] = None
            return None
        n = int(bars)
        off = int(offset)
        # Window ends ``offset`` bars BEFORE the latest bar.
        end_excl = len(ohlcv) - off
        start = max(0, end_excl - n)
        if end_excl <= 0 or start >= end_excl:
            self._call_cache[cache_key] = None
            return None
        recent = ohlcv[start:end_excl]
        total = 0.0
        for bar in recent:
            v = bar.get("volume") if isinstance(bar, dict) else None
            if v is None:
                self._call_cache[cache_key] = None
                return None
            try:
                total += float(v)
            except (TypeError, ValueError):
                self._call_cache[cache_key] = None
                return None
        self._call_cache[cache_key] = total
        return total
