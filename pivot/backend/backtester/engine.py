"""
Backtester orchestrator (wide-primitive system).

Single public entry point: ``run_backtest(strategy_def)`` (async). It pulls
OHLCV from yfinance, builds entry/exit signals via the composer using the
SIGNAL_REGISTRY / EXIT_REGISTRY, runs a chronological day-by-day simulation
with realistic Indian costs (Zerodha-style brokerage, slippage, STT, exchange,
SEBI, stamp duty), force-closes any positions still open at the end of the
test window, computes metrics against an NSEI buy-and-hold benchmark and
returns a frontend-ready result dict.

Simulation contract (no look-ahead):
  - Signals on day ``i-1`` (yesterday's close) trigger ENTRIES at day ``i``'s
    OPEN. EXITS triggered intraday by stop / target / trailing-stop fire at
    the trigger price using the bar's high/low; indicator-driven exits (and
    the n-day exit) fire at today's OPEN. The peak_price for trailing exits
    is updated at the start of the bar from today's high. Mark-to-market
    uses today's CLOSE.
  - The first ``warmup_days`` bars (computed from the indicators in use, plus
    a 50-bar buffer with a hard floor of 300) are discarded from the equity
    curve and trade log so reported returns reflect only the test window.
"""
from __future__ import annotations

import asyncio
import logging
import math
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Optional

import numpy as np
import pandas as pd
import yfinance as yf

from backend.backtester.composer import build_entry_signal, build_exit_check
from backend.backtester.metrics import calculate_metrics
from backend.backtester.portfolio import PortfolioSnapshot, Trade
from backend.backtester.primitives import SIGNAL_REGISTRY, WARMUP
from backend.market.yfinance_service import resolve_symbol

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Indian costs — single source of truth now lives in services/trading_costs.py
# (2026-05-29 audit: STT was sell-only + GST was missing here; the converged
# model adds STT on both legs + GST). Re-exported so existing importers
# (`dsl/backtest/engine.py` imports buy_cost/sell_cost from here) keep working.
# ---------------------------------------------------------------------------
from backend.services.trading_costs import (  # noqa: E402
    BROKERAGE_PER_ORDER,
    EXCHANGE_PCT,
    SEBI_PCT,
    SLIPPAGE_PCT,
    STAMP_BUY_PCT,
    STT_SELL_PCT,
    buy_cost,
    sell_cost,
)


# ---------------------------------------------------------------------------
# Warmup
# ---------------------------------------------------------------------------
DEFAULT_WARMUP_FLOOR = 300
WARMUP_BUFFER = 50

# Map signal name → list of WARMUP keys it depends on. Signals not listed
# default to the calendar/no-warmup bucket (0 days).
_SIGNAL_TO_WARMUP_KEYS: dict[str, list[str]] = {
    # Moving Averages (key="sma" or "ema" — both depend on period)
    "price_cross_above_sma": ["sma"],
    "price_cross_below_sma": ["sma"],
    "price_above_sma": ["sma"],
    "price_below_sma": ["sma"],
    "golden_cross_sma": ["sma"],
    "death_cross_sma": ["sma"],
    "golden_cross_ema": ["ema"],
    "death_cross_ema": ["ema"],
    "price_above_vwma": ["sma"],
    "hma_turn_up": ["sma"],
    # RSI
    "rsi_cross_below": ["rsi"],
    "rsi_cross_above": ["rsi"],
    "rsi_in_range": ["rsi"],
    "rsi_below_level": ["rsi"],
    "rsi_divergence_bullish": ["rsi"],
    "rsi_divergence_bearish": ["rsi"],
    # Stoch / StochRSI
    "stoch_cross_above": ["stoch"],
    "stoch_cross_below": ["stoch"],
    "stochrsi_cross_above": ["stoch", "rsi"],
    # CCI / Williams / MFI
    "cci_cross_above": ["cci"],
    "cci_cross_below": ["cci"],
    "williams_r_cross_above": ["williams_r"],
    "mfi_oversold": ["mfi"],
    "mfi_cross_below": ["mfi"],
    # ROC / Momentum / AO
    "roc_cross_zero_up": ["roc"],
    "momentum_cross_zero_up": ["momentum"],
    "ao_cross_zero_up": ["sma"],
    "ao_cross_zero_down": ["sma"],
    # MACD
    "macd_cross_above_signal": ["macd"],
    "macd_cross_below_signal": ["macd"],
    "macd_histogram_cross_zero_up": ["macd"],
    "macd_histogram_cross_zero_down": ["macd"],
    "macd_line_cross_zero_up": ["macd"],
    "macd_line_cross_zero_down": ["macd"],
    "macd_divergence_bullish": ["macd"],
    "macd_histogram_expanding_bullish": ["macd"],
    # Bollinger
    "bb_lower_touch": ["bb"],
    "bb_upper_touch": ["bb"],
    "bb_breakout_above": ["bb"],
    "bb_breakout_below": ["bb"],
    "bb_squeeze": ["bb"],
    "bb_squeeze_breakout_up": ["bb"],
    "bb_w_pattern": ["bb"],
    "bb_mean_reversion": ["bb"],
    # Volatility
    "supertrend_flip_bullish": ["supertrend"],
    "supertrend_flip_bearish": ["supertrend"],
    "price_above_supertrend": ["supertrend"],
    "keltner_breakout_above": ["keltner"],
    "keltner_breakout_below": ["keltner"],
    "donchian_breakout_above": ["donchian"],
    "donchian_breakout_below": ["donchian"],
    "atr_expansion": ["atr"],
    "atr_contraction": ["atr"],
    "low_volatility_period": ["atr"],
    # Trend strength
    "adx_strong_trend": ["adx"],
    "adx_weak_trend": ["adx"],
    "di_cross_bullish": ["adx"],
    "di_cross_bearish": ["adx"],
    "adx_rising_with_trend": ["adx"],
    "aroon_bullish_cross": ["aroon"],
    "aroon_bearish_cross": ["aroon"],
    "vortex_bullish_cross": ["adx"],
    # Volume
    "volume_spike": ["sma"],
    "volume_price_confirm_up": ["sma"],
    "volume_price_diverge_up": ["sma"],
    "obv_cross_above_sma": ["obv", "sma"],
    "cmf_cross_zero_up": ["cmf"],
    "cmf_positive": ["cmf"],
    "high_volume_breakout": ["sma"],
    "accumulation_day": ["obv"],
    "distribution_day": ["obv"],
    # Price action
    "52wk_high_breakout": ["52wk"],
    "52wk_low_breakdown": ["52wk"],
    "n_period_high_breakout": ["donchian"],
    "n_period_low_breakdown": ["donchian"],
    "pct_below_52wk_high": ["52wk"],
    "gap_up": [],
    "gap_down": [],
    "pct_dip_from_yesterday": [],
    "pct_rally_from_yesterday": [],
    "psar_flip_bullish": ["psar"],
    "psar_flip_bearish": ["psar"],
    "price_above_psar": ["psar"],
    "inside_day_breakout": [],
    "hammer_candle": [],
    "shooting_star_candle": [],
    "engulfing_bullish": [],
    "engulfing_bearish": [],
    # Ichimoku
    "ichimoku_tk_cross_bullish": ["ichimoku"],
    "ichimoku_tk_cross_bearish": ["ichimoku"],
    "ichimoku_price_above_cloud": ["ichimoku"],
    "ichimoku_price_below_cloud": ["ichimoku"],
    "ichimoku_cloud_breakout_up": ["ichimoku"],
    "ichimoku_cloud_breakout_down": ["ichimoku"],
    "ichimoku_bullish_cloud": ["ichimoku"],
    "ichimoku_chikou_above_price": ["ichimoku"],
    "ichimoku_full_bullish": ["ichimoku"],
    # Squeeze
    "squeeze_fire_up": ["squeeze"],
    "squeeze_fire_down": ["squeeze"],
    # Pivots
    "price_cross_above_pivot": [],
    "price_at_support": [],
    "price_at_resistance": [],
    # Calendar primitives — no warmup
    "monday": [], "tuesday": [], "wednesday": [], "thursday": [], "friday": [],
    "weekday": [], "first_day_of_month": [], "last_day_of_month": [],
    "first_day_of_quarter": [], "last_day_of_quarter": [],
    "day_of_month": [], "month_of_year": [], "days_before_fno_expiry": [],
}


def _warmup_for_signal(signal_name: str, params: dict) -> int:
    """Compute warmup days for a single signal call. Returns 0 if unknown."""
    keys = _SIGNAL_TO_WARMUP_KEYS.get(signal_name, [])
    if not keys:
        return 0
    p = params or {}
    out = 0
    for key in keys:
        fn = WARMUP.get(key)
        if fn is None:
            continue
        try:
            if key == "rsi":
                out = max(out, int(fn(p.get("period", 14))))
            elif key == "macd":
                out = max(out, int(fn(
                    p.get("fast", 12),
                    p.get("slow", 26),
                    p.get("signal", 9),
                )))
            elif key == "stoch":
                out = max(out, int(fn(
                    p.get("k_period", p.get("k", 14)),
                    p.get("d_period", p.get("d", 3)),
                )))
            elif key in ("sma", "ema", "bb", "atr", "cci", "mfi", "adx",
                          "supertrend", "keltner", "donchian", "cmf",
                          "aroon", "roc", "momentum", "williams_r"):
                period = (p.get("period")
                          or p.get("slow_period")
                          or p.get("vol_period")
                          or p.get("price_period")
                          or p.get("sma_period")
                          or 20)
                out = max(out, int(fn(int(period))))
            elif key in ("obv", "vwap", "psar", "squeeze", "ichimoku",
                          "52wk", "sma200"):
                out = max(out, int(fn()))
        except Exception as exc:
            logger.debug("warmup calc failed for %s/%s: %s",
                         signal_name, key, exc)
    return out


def _calculate_warmup(entry_def: dict, exit_def: dict) -> int:
    """Walk all conditions in entry/exit defs, take max signal warmup,
    add WARMUP_BUFFER buffer, floor at DEFAULT_WARMUP_FLOOR."""
    max_w = 0
    for cond in (entry_def.get("conditions") or []):
        sn = cond.get("signal")
        if sn:
            max_w = max(max_w, _warmup_for_signal(sn, cond.get("params") or {}))
    for cond in (exit_def.get("conditions") or []):
        if cond.get("exit_type") == "indicator_signal":
            params = cond.get("params") or {}
            sn = params.get("signal")
            if sn:
                max_w = max(max_w, _warmup_for_signal(
                    sn, params.get("params") or {}))
    return max(DEFAULT_WARMUP_FLOOR, max_w + WARMUP_BUFFER)


# ---------------------------------------------------------------------------
# Period parsing
# ---------------------------------------------------------------------------
PERIOD_TO_YEARS: dict[str, float] = {
    "1mo": 1 / 12, "3mo": 0.25, "6mo": 0.5,
    "1y": 1.0, "2y": 2.0, "3y": 3.0, "5y": 5.0, "10y": 10.0,
    "max": 20.0, "ytd": 1.0,
}


def _parse_period(period: Optional[str], start_date: Optional[str],
                   end_date: Optional[str]) -> tuple[date, date]:
    end = (datetime.strptime(end_date, "%Y-%m-%d").date()
           if end_date else date.today())
    if start_date:
        start = datetime.strptime(start_date, "%Y-%m-%d").date()
        return start, end
    key = (period or "2y").lower()
    if key == "ytd":
        start = date(end.year, 1, 1)
    elif key == "max":
        start = end - timedelta(days=int(20 * 365.25))
    else:
        years = PERIOD_TO_YEARS.get(key, 2.0)
        start = end - timedelta(days=int(years * 365.25))
    return start, end


# ---------------------------------------------------------------------------
# Data fetching
# ---------------------------------------------------------------------------
def _fetch_ohlcv(
    symbol: str, start: date, end: date, *, interval: str = "1d",
) -> pd.DataFrame:
    """OHLCV for the inclusive [start, end] window at ``interval`` with
    lowercase columns [open, high, low, close, volume].

    Daily (``interval='1d'``) is unchanged. For intraday intervals the
    canonical value is mapped via ``to_yfinance`` (returns ``None`` if
    yfinance can't serve it — honest empty raise rather than silently
    downgrading to a different timeframe) and the start date is clamped
    to yfinance's rolling intraday cap so the request stays valid.
    """
    from backend.core.data.intervals import (
        max_lookback_days,
        normalize_interval,
        to_yfinance,
    )

    norm = normalize_interval(interval)
    yf_interval = to_yfinance(norm)
    if yf_interval is None:
        # Honest boundary — yfinance doesn't serve 3m / 10m; refuse rather
        # than silently downgrade. Engine surfaces the error.
        raise ValueError(
            f"yfinance cannot serve interval {norm!r} for {symbol}"
        )

    resolved = resolve_symbol(symbol)
    yf_end = end + timedelta(days=1)

    # Clamp the start date for intraday so we stay inside yfinance's rolling
    # window (e.g. 60d for 15m bars). yfinance rejects any intraday range
    # WIDER than the cap, and ``yf_end`` is the exclusive end + 1 day, so we
    # measure the window from ``yf_end`` and leave a 1-day margin to keep the
    # span strictly under the cap. Daily/weekly/monthly are unbounded and
    # unchanged.
    fetch_start = start
    cap_days = max_lookback_days(norm, has_kite=False)
    if cap_days is not None:
        earliest = yf_end - timedelta(days=int(cap_days) - 1)
        if fetch_start < earliest:
            fetch_start = earliest
    df = yf.Ticker(resolved).history(
        start=fetch_start.isoformat(), end=yf_end.isoformat(),
        interval=yf_interval, auto_adjust=True,
    )
    if (df is None or df.empty) and resolved.endswith(".NS"):
        df = yf.Ticker(resolved[:-3]).history(
            start=fetch_start.isoformat(), end=yf_end.isoformat(),
            interval=yf_interval, auto_adjust=True,
        )
    if df is None or df.empty:
        raise ValueError(f"No historical data for {symbol} ({resolved})")

    df = df.rename(columns={c: c.lower() for c in df.columns})
    keep = [c for c in ("open", "high", "low", "close", "volume") if c in df.columns]
    df = df[keep].copy().dropna(subset=["close"])
    if df.empty:
        raise ValueError(f"All rows had null close for {symbol}")
    if df.index.tz is not None:
        df.index = df.index.tz_localize(None)
    return df


# ---------------------------------------------------------------------------
# Legacy → new strategy_def adapter
# ---------------------------------------------------------------------------
_LEGACY_ENTRY_ALIASES = {
    "calendar": ("monday", {}),  # default — overridden below
    "sip_monthly": ("first_day_of_month", {}),
    "price_52wk_high": ("52wk_high_breakout", {}),
    "price_52wk_low": ("52wk_low_breakdown", {}),
}


def _legacy_to_new(strategy_def: dict) -> dict:
    """Translate the old engine input shape to the new entry/exit dict shape.
    A new-format input (already has 'entry' dict with 'conditions') is
    returned unchanged."""
    if isinstance(strategy_def.get("entry"), dict) and \
            "conditions" in strategy_def["entry"]:
        return strategy_def

    out = dict(strategy_def)
    entry_name = strategy_def.get("entry_signal", "")
    entry_params = strategy_def.get("entry_params") or {}
    calendar_filter = strategy_def.get("calendar_filter") or {}

    conditions: list[dict] = []
    if entry_name == "calendar":
        weekday = entry_params.get("weekday")
        if weekday is not None:
            wd_map = {0: "monday", 1: "tuesday", 2: "wednesday",
                      3: "thursday", 4: "friday"}
            conditions.append({"signal": wd_map.get(int(weekday), "weekday"),
                                "params": ({} if int(weekday) in wd_map
                                           else {"weekday": int(weekday)})})
        elif entry_params.get("day_of_month") is not None:
            conditions.append({"signal": "day_of_month",
                                "params": {"day": int(entry_params["day_of_month"])}})
        cond = (entry_params.get("price_condition") or "").lower()
        sma_period = (entry_params.get("sma_period")
                      or entry_params.get("price_level_period"))
        if cond == "above" and sma_period:
            conditions.append({"signal": "price_above_sma",
                                "params": {"period": int(sma_period)}})
        elif cond == "below" and sma_period:
            conditions.append({"signal": "price_below_sma",
                                "params": {"period": int(sma_period)}})
    else:
        mapped_name, mapped_params = _LEGACY_ENTRY_ALIASES.get(
            entry_name, (entry_name, {}))
        merged = {**mapped_params, **entry_params}
        if mapped_name in SIGNAL_REGISTRY:
            conditions.append({"signal": mapped_name, "params": merged})
        elif entry_name:
            # Fall through — composer will raise a clear error if unknown
            conditions.append({"signal": entry_name, "params": entry_params})

        if calendar_filter:
            wd = calendar_filter.get("weekday")
            if wd is not None:
                conditions.append({"signal": "weekday",
                                    "params": {"weekday": int(wd)}})

    if not conditions:
        raise ValueError(
            "Strategy must define an entry signal — got empty/unknown "
            f"entry_signal {entry_name!r}"
        )
    operator = "single" if len(conditions) == 1 else "and"
    out["entry"] = {"operator": operator, "conditions": conditions}

    # Build exit
    exit_name = (strategy_def.get("exit_signal") or "hold").lower()
    exit_params = strategy_def.get("exit_params") or {}
    exit_conditions: list[dict] = []

    sl_pct = strategy_def.get("stop_loss_pct")
    tp_pct = strategy_def.get("take_profit_pct")
    if sl_pct is not None and tp_pct is not None:
        exit_conditions.append({"exit_type": "stop_and_target",
                                 "params": {"stop_pct": float(sl_pct),
                                            "target_pct": float(tp_pct)}})
    elif sl_pct is not None:
        exit_conditions.append({"exit_type": "stop_loss",
                                 "params": {"stop_pct": float(sl_pct)}})
    elif tp_pct is not None:
        exit_conditions.append({"exit_type": "take_profit",
                                 "params": {"target_pct": float(tp_pct)}})

    if exit_name in ("hold", "stop_and_target"):
        pass  # handled by stop_and_target above (or no extra exit at all)
    elif exit_name == "n_days":
        exit_conditions.append({"exit_type": "after_n_days",
                                 "params": {"n_days": int(exit_params.get("n_days", 30))}})
    elif exit_name in SIGNAL_REGISTRY:
        exit_conditions.append({"exit_type": "indicator_signal",
                                 "params": {"signal": exit_name,
                                            "params": exit_params}})
    elif exit_name and exit_name != "hold":
        # Unknown exit signal name — raise as composer would
        exit_conditions.append({"exit_type": "indicator_signal",
                                 "params": {"signal": exit_name,
                                            "params": exit_params}})

    # Always add end_of_period as a safety net
    exit_conditions.append({"exit_type": "end_of_period", "params": {}})
    out["exit"] = {"operator": "first_of", "conditions": exit_conditions}
    return out


# ---------------------------------------------------------------------------
# Inline simulation (Indian costs)
# ---------------------------------------------------------------------------
@dataclass
class _OpenPos:
    trade: Trade
    entry_price: float
    qty: int
    peak_price: float
    pending_exit: bool = False


# ---------------------------------------------------------------------------
# Public entry
# ---------------------------------------------------------------------------
DEFAULT_DISCLAIMER = (
    "Past performance does not guarantee future results. "
    "This simulation includes realistic Indian retail costs "
    "(brokerage ₹20/order, slippage 0.05%, STT 0.1% on sells, "
    "exchange/SEBI charges, stamp 0.015% on buys). "
    "Results assume execution at next-day OPEN on signal."
)


async def run_backtest(strategy_def: dict) -> dict:
    """Run a strategy backtest end-to-end. Async wrapper around the blocking
    yfinance fetch and pandas-heavy simulation."""
    return await asyncio.to_thread(_run_backtest_sync, strategy_def)


def _run_backtest_sync(strategy_def: dict) -> dict:
    # ------------------------------------------------------------------
    # 1. Validate
    # ------------------------------------------------------------------
    if not isinstance(strategy_def, dict):
        raise ValueError("strategy_def must be a dict")
    symbol = strategy_def.get("symbol")
    if not symbol:
        raise ValueError("strategy_def.symbol is required")
    starting_capital = float(strategy_def.get("starting_capital", 500_000))

    period = strategy_def.get("period")
    start_date_in = strategy_def.get("start_date")
    end_date_in = strategy_def.get("end_date")
    if not period and not (start_date_in and end_date_in):
        period = "2y"

    # Translate legacy → new shape if needed
    sdef = _legacy_to_new(strategy_def)
    entry_def = sdef["entry"]
    exit_def = sdef["exit"]

    # ------------------------------------------------------------------
    # 2. Warmup
    # ------------------------------------------------------------------
    warmup_days = _calculate_warmup(entry_def, exit_def)

    test_start, test_end = _parse_period(period, start_date_in, end_date_in)
    fetch_start = test_start - timedelta(days=warmup_days)

    # ------------------------------------------------------------------
    # 3. Fetch OHLCV (full window incl. warmup) and benchmark
    # ------------------------------------------------------------------
    df_full = _fetch_ohlcv(symbol, fetch_start, test_end)
    benchmark_symbol = strategy_def.get("benchmark", "^NSEI")
    try:
        bench_full = _fetch_ohlcv(benchmark_symbol, fetch_start, test_end)
    except ValueError:
        # Fall back to the test symbol itself if benchmark is unavailable
        logger.warning("Benchmark %s unavailable — using test symbol",
                       benchmark_symbol)
        bench_full = df_full.copy()

    # ------------------------------------------------------------------
    # 4-5. Build entry signal series (over full warmup+test data) and
    #      exit check closure.
    # ------------------------------------------------------------------
    entry_series_full = build_entry_signal(df_full, entry_def)
    exit_check = build_exit_check(exit_def)
    exit_signal_cache: dict = {}

    # ------------------------------------------------------------------
    # 6. Trim warmup
    # ------------------------------------------------------------------
    test_mask = df_full.index >= pd.Timestamp(test_start)
    df = df_full.loc[test_mask].copy()
    if df.empty:
        raise ValueError(f"No trading days in test window for {symbol}")
    entry_series = entry_series_full.loc[test_mask]

    # ------------------------------------------------------------------
    # 7. Strategy parameters
    # ------------------------------------------------------------------
    position_size_inr = strategy_def.get("position_size_inr")
    position_size_pct = strategy_def.get("position_size_pct")
    max_positions = int(strategy_def.get("max_positions", 10))
    allow_averaging = bool(strategy_def.get("allow_averaging", True))

    open_positions: list[_OpenPos] = []
    closed_trades: list[Trade] = []
    snapshots: list[PortfolioSnapshot] = []
    cash = float(starting_capital)
    next_trade_id = 1
    skipped_count = 0

    def _default_sized() -> float:
        if position_size_inr is not None:
            return float(position_size_inr)
        if position_size_pct is not None:
            return cash * (float(position_size_pct) / 100.0)
        return float(starting_capital) / max(1, max_positions)

    # ------------------------------------------------------------------
    # 8. Simulation loop
    # ------------------------------------------------------------------
    n = len(df)
    closes = df["close"].to_numpy(dtype=float)
    opens = df["open"].to_numpy(dtype=float)
    highs = df["high"].to_numpy(dtype=float)
    lows = df["low"].to_numpy(dtype=float)
    timestamps = df.index
    entry_arr = entry_series.to_numpy(dtype=bool)

    for i in range(n):
        ts = timestamps[i]
        d_today = ts.date() if hasattr(ts, "date") else date.fromisoformat(str(ts)[:10])
        o, h, lo, c = float(opens[i]), float(highs[i]), float(lows[i]), float(closes[i])
        is_last_day = (i == n - 1)

        # 8a. Update peak prices using today's high (so trailing-stop sees it)
        for pos in open_positions:
            if h > pos.peak_price:
                pos.peak_price = h

        # 8b. Exit checks on each open position. For composer-driven hits,
        #     execute at today's OPEN. Stop / target / trailing exits use
        #     the trigger price.
        if i > 0 and open_positions:
            for pos in list(open_positions):
                hit, reason = exit_check(
                    entry_price=pos.entry_price,
                    peak_price=pos.peak_price,
                    entry_date=pos.trade.entry_date,
                    current_date=d_today,
                    current_price=c,
                    current_high=h,
                    current_low=lo,
                    df=df_full,
                    current_idx=int(df_full.index.get_indexer([ts])[0]),
                    is_last_day=is_last_day,
                    signal_series_cache=exit_signal_cache,
                )
                if not hit:
                    continue

                # Pick exit price by reason
                if reason == "stop_loss":
                    stop_pct = _find_param(exit_def, "stop_loss", "stop_pct")
                    exit_price = pos.entry_price * (1 - (stop_pct or 0) / 100.0)
                elif reason == "trailing_stop":
                    trail_pct = _find_param(exit_def, "trailing_stop", "trail_pct")
                    exit_price = pos.peak_price * (1 - (trail_pct or 0) / 100.0)
                elif reason == "take_profit":
                    tgt_pct = _find_param(exit_def, "take_profit", "target_pct")
                    exit_price = pos.entry_price * (1 + (tgt_pct or 0) / 100.0)
                elif reason == "stop_and_target":
                    sp = _find_param(exit_def, "stop_and_target", "stop_pct") or 0
                    tp = _find_param(exit_def, "stop_and_target", "target_pct") or 0
                    stop_price = pos.entry_price * (1 - sp / 100.0)
                    tgt_price = pos.entry_price * (1 + tp / 100.0)
                    if lo <= stop_price:
                        exit_price = stop_price
                        reason = "stop_loss"
                    else:
                        exit_price = tgt_price
                        reason = "take_profit"
                else:
                    # indicator_signal, after_n_days, end_of_period → exit at OPEN
                    exit_price = o

                _close_position(pos, d_today, exit_price, reason)
                cash_credit, costs = sell_cost(exit_price, pos.qty)
                pos.trade.brokerage_sell = BROKERAGE_PER_ORDER
                pos.trade.slippage_sell = exit_price * pos.qty * SLIPPAGE_PCT
                pos.trade.stt_sell = exit_price * pos.qty * STT_SELL_PCT
                cash += cash_credit
                # Final pnl
                buy_total_costs = (pos.trade.brokerage + pos.trade.slippage
                                   + pos.trade.stt_buy)
                pos.trade.gross_pnl = (exit_price - pos.entry_price) * pos.qty
                pos.trade.net_pnl = (pos.trade.gross_pnl
                                     - buy_total_costs - costs)
                pos.trade.return_pct = (
                    pos.trade.net_pnl / pos.trade.position_size_inr * 100.0
                    if pos.trade.position_size_inr > 0 else 0.0
                )
                if pos.trade.entry_date is not None:
                    pos.trade.holding_days = (d_today - pos.trade.entry_date).days
                closed_trades.append(pos.trade)
                open_positions.remove(pos)

        # 8c. Entry check — yesterday's signal fires entry at today's open.
        if i >= 1 and bool(entry_arr[i - 1]):
            if len(open_positions) >= max_positions:
                skipped_count += 1
                closed_trades.append(_skipped_trade(
                    next_trade_id, symbol, "max_positions_reached"))
                next_trade_id += 1
            elif not allow_averaging and open_positions:
                skipped_count += 1
                closed_trades.append(_skipped_trade(
                    next_trade_id, symbol, "averaging_disabled"))
                next_trade_id += 1
            elif o <= 0:
                skipped_count += 1
                closed_trades.append(_skipped_trade(
                    next_trade_id, symbol, "invalid_open_price"))
                next_trade_id += 1
            else:
                target_value = _default_sized()
                qty = int(target_value // o)
                if qty <= 0:
                    skipped_count += 1
                    closed_trades.append(_skipped_trade(
                        next_trade_id, symbol, "position_size_below_one_share"))
                    next_trade_id += 1
                else:
                    net_debit, costs = buy_cost(o, qty)
                    if net_debit > cash:
                        # try to scale down
                        qty = max(0, int((cash - BROKERAGE_PER_ORDER)
                                         / (o * (1 + SLIPPAGE_PCT
                                                 + EXCHANGE_PCT + SEBI_PCT
                                                 + STAMP_BUY_PCT))))
                        if qty <= 0:
                            skipped_count += 1
                            closed_trades.append(_skipped_trade(
                                next_trade_id, symbol, "insufficient_cash"))
                            next_trade_id += 1
                        else:
                            net_debit, costs = buy_cost(o, qty)
                            cash -= net_debit
                            tr = _new_trade(next_trade_id, symbol, d_today,
                                            o, qty, costs)
                            next_trade_id += 1
                            open_positions.append(_OpenPos(
                                trade=tr, entry_price=o, qty=qty,
                                peak_price=h))
                    else:
                        cash -= net_debit
                        tr = _new_trade(next_trade_id, symbol, d_today,
                                        o, qty, costs)
                        next_trade_id += 1
                        open_positions.append(_OpenPos(
                            trade=tr, entry_price=o, qty=qty, peak_price=h))

        # 8d. Mark to market at today's close
        holdings_value = sum(p.qty * c for p in open_positions)
        total_value = cash + holdings_value
        snapshots.append(PortfolioSnapshot(
            date=d_today,
            cash=cash,
            holdings_value=holdings_value,
            total_value=total_value,
            open_positions=len(open_positions),
        ))

    # ------------------------------------------------------------------
    # 9. Force-close any positions remaining at last close
    # ------------------------------------------------------------------
    if open_positions:
        last_ts = timestamps[-1]
        last_close = float(closes[-1])
        last_d = (last_ts.date() if hasattr(last_ts, "date")
                  else date.fromisoformat(str(last_ts)[:10]))
        for pos in list(open_positions):
            cash_credit, costs = sell_cost(last_close, pos.qty)
            cash += cash_credit
            pos.trade.exit_date = last_d
            pos.trade.exit_price = last_close
            pos.trade.exit_reason = "end_of_period"
            pos.trade.brokerage_sell = BROKERAGE_PER_ORDER
            pos.trade.slippage_sell = last_close * pos.qty * SLIPPAGE_PCT
            pos.trade.stt_sell = last_close * pos.qty * STT_SELL_PCT
            buy_total_costs = (pos.trade.brokerage + pos.trade.slippage
                               + pos.trade.stt_buy)
            pos.trade.gross_pnl = (last_close - pos.entry_price) * pos.qty
            pos.trade.net_pnl = pos.trade.gross_pnl - buy_total_costs - costs
            pos.trade.return_pct = (
                pos.trade.net_pnl / pos.trade.position_size_inr * 100.0
                if pos.trade.position_size_inr > 0 else 0.0)
            if pos.trade.entry_date is not None:
                pos.trade.holding_days = (last_d - pos.trade.entry_date).days
            closed_trades.append(pos.trade)
            open_positions.remove(pos)

        # Update final snapshot with new cash and zero holdings
        if snapshots:
            snapshots[-1] = PortfolioSnapshot(
                date=snapshots[-1].date,
                cash=cash,
                holdings_value=0.0,
                total_value=cash,
                open_positions=0,
            )

    # ------------------------------------------------------------------
    # 10. Benchmark buy-and-hold over the test window
    # ------------------------------------------------------------------
    benchmark_curve = _build_benchmark_curve(
        bench_full, df.index, starting_capital)

    # ------------------------------------------------------------------
    # 11. Metrics
    # ------------------------------------------------------------------
    metrics = calculate_metrics(
        equity_curve=snapshots,
        trades=closed_trades,
        benchmark_curve=benchmark_curve,
        starting_capital=starting_capital,
    )

    # ------------------------------------------------------------------
    # 12. Build result payload
    # ------------------------------------------------------------------
    dd_by_date = {r["date"]: r["drawdown_pct"]
                  for r in metrics.get("drawdown_series", [])}
    equity_payload = []
    for s in snapshots:
        date_str = (s.date.isoformat() if hasattr(s.date, "isoformat")
                    else str(s.date))
        equity_payload.append({
            "date": date_str,
            "value": round(s.total_value, 2),
            "cash": round(s.cash, 2),
            "holdings_value": round(s.holdings_value, 2),
            "open_positions": s.open_positions,
            "drawdown_pct": dd_by_date.get(date_str, 0.0),
        })

    benchmark_payload = [
        {"date": (s.date.isoformat() if hasattr(s.date, "isoformat")
                  else str(s.date)),
         "value": round(s.total_value, 2)}
        for s in benchmark_curve
    ]

    trades_payload = [t.to_dict() for t in closed_trades]
    drawdown_payload = list(metrics.get("drawdown_series", []))

    warnings = _build_warnings(metrics, closed_trades, df.index)

    return {
        "strategy_definition": strategy_def,
        "strategy": strategy_def,  # alias for spec compatibility
        "equity_curve": equity_payload,
        "benchmark_curve": benchmark_payload,
        "trades": trades_payload,
        "metrics": metrics,
        "drawdown_series": drawdown_payload,
        "data_source": "yfinance (adjusted closes)",
        "disclaimer": DEFAULT_DISCLAIMER,
        "warnings": warnings,
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _new_trade(trade_id: int, symbol: str, d: date, price: float,
               qty: int, total_buy_costs: float) -> Trade:
    notional = price * qty
    slippage = notional * SLIPPAGE_PCT
    return Trade(
        trade_id=trade_id,
        symbol=symbol,
        entry_date=d,
        entry_price=float(price),
        quantity=int(qty),
        position_size_inr=float(notional),
        brokerage=BROKERAGE_PER_ORDER,
        slippage=slippage,
        stt_buy=0.0,  # no STT on buy side for delivery
        peak_price=float(price),
    )


def _skipped_trade(trade_id: int, symbol: str, reason: str) -> Trade:
    return Trade(
        trade_id=trade_id,
        symbol=symbol,
        entry_date=None,
        entry_price=None,
        quantity=0,
        position_size_inr=0.0,
        skipped=True,
        skip_reason=reason,
    )


def _close_position(pos: _OpenPos, d: date, exit_price: float,
                     reason: str) -> None:
    pos.trade.exit_date = d
    pos.trade.exit_price = float(exit_price)
    pos.trade.exit_reason = reason


def _find_param(exit_def: dict, exit_type: str, param: str):
    for c in (exit_def.get("conditions") or []):
        if c.get("exit_type") == exit_type:
            return (c.get("params") or {}).get(param)
    return None


def _build_benchmark_curve(bench_df: pd.DataFrame, dates: pd.DatetimeIndex,
                            starting_capital: float) -> list[PortfolioSnapshot]:
    """Buy-and-hold the benchmark with starting_capital on day 1 at open;
    mark to close each day. Aligns to the supplied date index."""
    if bench_df.empty or len(dates) == 0:
        return []
    bench_aligned = bench_df.reindex(dates).ffill().bfill()
    open_p = float(bench_aligned["open"].iloc[0])
    if open_p <= 0:
        return []
    qty = starting_capital / open_p  # fractional — theoretical benchmark
    out: list[PortfolioSnapshot] = []
    for ts in dates:
        close_p = float(bench_aligned.loc[ts, "close"])
        value = qty * close_p
        d_obj = ts.date() if hasattr(ts, "date") else date.fromisoformat(str(ts)[:10])
        out.append(PortfolioSnapshot(
            date=d_obj,
            cash=0.0,
            holdings_value=value,
            total_value=value,
            open_positions=1,
        ))
    return out


def _build_warnings(metrics: dict, trades: list[Trade],
                     test_index: pd.DatetimeIndex) -> list[str]:
    warnings: list[str] = []
    total_trades = metrics.get("total_trades", 0)
    skipped = metrics.get("skipped_trades", 0)
    if total_trades < 5:
        warnings.append(
            f"Strategy fired fewer than 5 trades ({total_trades}). "
            "Results may not be statistically meaningful."
        )
    if total_trades > 0 and skipped > total_trades * 0.2:
        warnings.append(
            "More than 20% of signals were skipped due to insufficient capital."
        )
    if _has_long_inactive_period(trades, test_index):
        warnings.append(
            "Strategy was inactive for more than 6 months at a stretch."
        )
    return warnings


def _has_long_inactive_period(trades: list[Trade],
                               test_index: pd.DatetimeIndex,
                               threshold_days: int = 180) -> bool:
    completed = [t for t in trades if not t.skipped and t.entry_date is not None]
    if not completed or len(test_index) < 2:
        return False
    entry_dates = sorted({t.entry_date for t in completed})
    prev = test_index[0].date()
    for d in entry_dates:
        if (d - prev).days > threshold_days:
            return True
        prev = d
    if (test_index[-1].date() - prev).days > threshold_days:
        return True
    return False
