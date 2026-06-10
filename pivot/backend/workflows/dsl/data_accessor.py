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


def resample_daily_bars_to_weekly(bars: list):
    """Resample a list of daily OHLCV dicts ({date, open, high, low,
    close, volume}) to W-FRI weekly bars. Returns a DataFrame with the
    same lowercase columns plus a ``date`` column (week-ending Friday),
    or None when the input can't be resampled.

    Shared by the live accessor and the watcher's
    ``_compute_indicator_sync`` so 'weekly RSI' means the same series
    everywhere. The trailing (in-progress) week is included — same
    convention as the daily path, whose last bar is the latest session.
    """
    try:
        import pandas as pd  # type: ignore[import-untyped]
    except ImportError:  # pragma: no cover
        return None
    if not bars:
        return None
    try:
        df = pd.DataFrame(bars)
        if "date" not in df.columns or "close" not in df.columns:
            return None
        df["date"] = pd.to_datetime(df["date"])
        df = df.set_index("date").sort_index()
        agg: dict = {"close": df["close"].resample("W-FRI").last()}
        if "open" in df.columns:
            agg["open"] = df["open"].resample("W-FRI").first()
        if "high" in df.columns:
            agg["high"] = df["high"].resample("W-FRI").max()
        if "low" in df.columns:
            agg["low"] = df["low"].resample("W-FRI").min()
        if "volume" in df.columns:
            agg["volume"] = df["volume"].resample("W-FRI").sum()
        wk = pd.DataFrame(agg).dropna(subset=["close"]).reset_index()
        wk["date"] = wk["date"].dt.strftime("%Y-%m-%d")
        return wk
    except Exception:  # noqa: BLE001 — honest None over a crash
        logger.info("[dsl.data_accessor] weekly resample failed", exc_info=True)
        return None


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
        timeframe: str = "daily",
    ) -> Optional[float]:
        comp_key = component.lower() if component else None
        tf = (timeframe or "daily").lower()
        cache_key = (
            "indicator", symbol.upper(), indicator.lower(),
            int(period), exchange.upper(), comp_key, int(offset), tf,
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

        # Weekly bars need ×5 the daily lookback so a period-N weekly
        # indicator clears the same min-history guard in WEEKLY bars.
        eff_period = int(period or 0) * (5 if tf == "weekly" else 1)
        try:
            # P0 parity: window sized to the indicator period + offset (was a
            # hardcoded "6mo" that silently starved any period > ~120 live).
            bars = get_historical_ohlcv(
                symbol,
                period=period_for_indicator(eff_period, offset=int(offset)),
                interval="1d",
            ) or []
        except Exception as exc:  # noqa: BLE001
            logger.info(
                "[dsl.data_accessor] historical fetch failed for %s: %s",
                symbol, exc,
            )
            self._call_cache[cache_key] = None
            return None

        if tf == "weekly":
            df = resample_daily_bars_to_weekly(bars)
            if df is None or len(df) < max(int(period or 0) + 5, 20) + int(offset):
                # Honest UNKNOWN — not enough WEEKLY bars; never serve a
                # daily value under a weekly label.
                self._call_cache[cache_key] = None
                return None
        else:
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

    # ── F&O P3: option leaves (OPTIONAL Protocol methods) ──
    #
    # Read through the 5s-cached chain service — never Kite directly.
    # Own short-lived session per call (the accessor has no db); the
    # per-walk cache keeps a tree with several option nodes at one
    # fetch per (underlying, metric). The BACKTEST accessor does not
    # implement these — the evaluator resolves them to None → UNKNOWN
    # until historical option data lands (F&O P4 vendor decision).

    def _with_db(self, fn):
        from backend.database import SessionLocal

        db = SessionLocal()
        try:
            return fn(db)
        except Exception:  # noqa: BLE001 — watcher must never crash
            return None
        finally:
            db.close()

    def get_option_metric(
        self, *, underlying: str, metric: str, expiry_rule: str = "nearest",
    ) -> Optional[float]:
        cache_key = ("opt_metric", underlying.upper(), metric, expiry_rule)
        if cache_key in self._call_cache:
            return self._call_cache[cache_key]
        from backend.market.option_metrics import compute_option_metric

        result = self._with_db(
            lambda db: compute_option_metric(
                db, underlying, metric, expiry_rule=expiry_rule,
            )
        )
        self._call_cache[cache_key] = result
        return result

    def get_option_greek(
        self,
        *,
        underlying: str,
        greek: str,
        option_type: str = "CE",
        strike: Optional[float] = None,
        expiry_rule: str = "nearest",
    ) -> Optional[float]:
        cache_key = (
            "opt_greek", underlying.upper(), greek, option_type,
            strike, expiry_rule,
        )
        if cache_key in self._call_cache:
            return self._call_cache[cache_key]
        from backend.market.option_metrics import compute_option_greek

        result = self._with_db(
            lambda db: compute_option_greek(
                db, underlying, greek, option_type=option_type,
                strike=strike, expiry_rule=expiry_rule,
            )
        )
        self._call_cache[cache_key] = result
        return result

    def get_dte(
        self, *, underlying: str, expiry_rule: str = "nearest",
    ) -> Optional[float]:
        cache_key = ("opt_dte", underlying.upper(), expiry_rule)
        if cache_key in self._call_cache:
            return self._call_cache[cache_key]
        from backend.market.option_metrics import compute_dte

        result = self._with_db(
            lambda db: compute_dte(db, underlying, expiry_rule=expiry_rule)
        )
        self._call_cache[cache_key] = result
        return result

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
