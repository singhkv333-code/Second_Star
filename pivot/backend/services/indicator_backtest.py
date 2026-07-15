"""Single-symbol indicator backtester (#55).

Backs the chat surface's "backtest <SYMBOL> when RSI drops below 50" /
"buying <SYMBOL> when it crossed 200 EMA" intents. Distinct from the
fundamentals expression backtester (which needs the financials Postgres
DB) — this one runs entirely off yfinance daily OHLCV data + the unified
``backend.services.backtest_indicators`` registry.

Indicators supported are everything in the registry (rsi, sma, ema, wma,
macd, adx, supertrend, bollinger, stochastic, stoch_rsi, cci, mfi,
williams_r, atr, keltner, donchian, aroon, psar, roc, trix, obv, vwap,
…). Adding a new indicator there makes it instantly backtestable here.

Strategy semantics (long-only):
  - Enter long on the buy signal bar, full cash → shares (1% friction).
  - Exit on the next opposite signal, shares → cash.
  - Hold otherwise.

Returns:
  - price_curve:      [{t, v}]   close price series
  - equity_curve:     [{t, v}]   strategy portfolio value
  - indicator_curve:  [{t, v}]   indicator series
  - signals:          [{t, side: "buy"|"sell", price, indicator_value}]
  - metrics:          {cagr_pct, total_return_pct, max_drawdown_pct,
                       hit_rate_pct, n_trades, n_wins, starting_capital,
                       ending_value}
  - bench_buy_hold_return_pct  (buy-and-hold for the same window)
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Optional

import pandas as pd
import yfinance as yf  # type: ignore[import-untyped]

from backend.services.backtest_indicators import (
    compute_series as _ind_series,
    default_period_for as _ind_default_period,
    get_spec as _ind_spec,
    supported_indicators as _ind_supported,
)

logger = logging.getLogger(__name__)


# P1 cost convergence: per-leg average from the shared India delivery model
# (was a flat 10 bps that under-counted STT/GST/stamp). See trading_costs.py.
from backend.services.trading_costs import leg_bps as _leg_bps
_FRICTION = (_leg_bps("buy") + _leg_bps("sell")) / 2.0
_STARTING_CAPITAL = 1_000_000.0


@dataclass
class IndicatorBacktestResult:
    symbol: str
    indicator: str
    indicator_period: int
    operator: str
    threshold: float
    period_label: str
    price_curve: list[dict]
    equity_curve: list[dict]
    indicator_curve: list[dict]
    signals: list[dict]
    metrics: dict
    bench_buy_hold_return_pct: float
    summary_text: str
    # Methodology block (window / after-costs / basis / caveat) — see
    # backend/services/backtest_metrics.methodology_note. Optional so older
    # callers/tests that construct this result still work.
    methodology: Optional[dict] = None
    # Display metadata so the card names a BASKET backtest correctly instead of
    # masquerading it as one company's "scheduled buy" (the primary_symbol's
    # company name). strategy_kind ∈ {"indicator","basket"}; display_title /
    # display_subtitle override the FE's symbol→company-name derivation when set.
    strategy_kind: str = "indicator"
    display_title: Optional[str] = None
    display_subtitle: Optional[str] = None
    # Resolved test window so the reply/card can state EXACTLY what was
    # tested instead of a vague "5y". window_start/window_end are ISO
    # dates of the first/last simulated bar; n_bars is the bar count;
    # bar_interval is the resolved interval ('1d' etc.).
    window_start: Optional[str] = None
    window_end: Optional[str] = None
    n_bars: int = 0
    bar_interval: str = "1d"
    # What `bench_buy_hold_return_pct` actually measures — the primary
    # symbol, an explicit override (benchmark_symbol), or, for a basket,
    # "{n}-name basket (ideal weights)" when the benchmark is the basket's
    # own target-weight buy-and-hold rather than one arbitrary constituent.
    # The card falls back to `symbol` when this is unset (older callers).
    benchmark_label: Optional[str] = None


_OperatorLiteral = Literal[
    "<", ">", "<=", ">=", "crosses_below", "crosses_above",
]


def drop_partial_last_bar(hist: "pd.DataFrame") -> "pd.DataFrame":
    """Drop today's still-forming daily bar so (a) no trade is simulated on
    an unclosed bar and (b) same-day reruns are reproducible.

    The #1 cause of "buy-and-hold changes every run" is the last daily bar
    updating intraday (last price ticks), which moves ``Close.iloc[-1]``
    and thus the benchmark. A backtest is a study of COMPLETED sessions;
    a bar dated today (IST) hasn't closed, so it must not be traded on.
    """
    if hist is None or len(hist) == 0:
        return hist
    try:
        from zoneinfo import ZoneInfo
        today_ist = datetime.now(ZoneInfo("Asia/Kolkata")).date()
        last_date = pd.Timestamp(hist.index[-1]).date()
        if last_date >= today_ist:
            return hist.iloc[:-1]
    except Exception:  # noqa: BLE001 — never let this break a backtest
        pass
    return hist


def run_indicator_backtest(
    *,
    symbol: str,
    indicator: str,
    indicator_period: int = 14,
    operator: _OperatorLiteral = "<",
    threshold: float = 50.0,
    period: str = "5y",
    exchange: str = "NSE",
    interval: str = "1d",
) -> IndicatorBacktestResult:
    """Run the backtest. Raises ValueError on bad inputs / no data.

    ``indicator`` is validated against the registry — anything in
    ``backend.services.backtest_indicators.supported_indicators()`` is
    accepted. For ``basis="price"`` indicators (SMA/EMA/WMA/PSAR/VWAP/
    Keltner-mid/Donchian-mid) the comparison is close-vs-indicator and
    the user-supplied ``threshold`` is treated as an additive bias on
    the indicator series (0 for plain price-cross signals).

    ``interval`` is the bar interval the indicator runs on. Default
    '1d' (daily) keeps existing callers unchanged. Aliases like
    'daily'/'weekly'/'60m' are normalized. Intraday intervals are mapped
    to the matching yfinance string and refuse honestly (empty raise)
    when yfinance can't serve them — never silently downgrade.
    """
    sym = symbol.upper().strip()
    # [C4] shared resolver maps index aliases (NIFTY→^NSEI, …) and
    # shorthand (RIL→RELIANCE) to real yfinance tickers.
    from backend.market.yfinance_service import resolve_symbol
    from backend.core.data.intervals import (
        normalize_interval as _normalize_interval,
        to_yfinance as _to_yfinance,
    )
    yf_sym = resolve_symbol(sym)

    spec = _ind_spec(indicator)
    if spec is None:
        raise ValueError(
            f"unsupported indicator {indicator!r}; supported: "
            + ", ".join(_ind_supported())
        )
    period_n = (
        int(indicator_period)
        if indicator_period and int(indicator_period) > 0
        else (_ind_default_period(indicator) or 14)
    )

    _norm_interval = _normalize_interval(interval)
    _yf_iv = _to_yfinance(_norm_interval)
    if _yf_iv is None:
        raise ValueError(
            f"yfinance cannot serve interval {_norm_interval!r} for {sym}"
        )
    # auto_adjust=False → split-adjusted, dividend-unadjusted (price
    # returns), matching Kite bars used elsewhere. Keeps every engine on
    # one return basis.
    hist = yf.Ticker(yf_sym).history(
        period=period, interval=_yf_iv, auto_adjust=False,
    )
    # Drop today's unclosed bar (daily only) — reproducible + no trading on
    # a forming bar. Intraday intervals keep every bar (partial-bar concept
    # doesn't apply the same way and the window is explicit anyway).
    if _norm_interval in ("1d", "daily", "1day"):
        hist = drop_partial_last_bar(hist)
    if hist.empty or len(hist) < max(period_n * 2, 30):
        raise ValueError(
            f"insufficient data for {sym} over {period} (got {len(hist)} bars)"
        )

    closes = hist["Close"].astype(float)
    _win_start = pd.Timestamp(hist.index[0]).date().isoformat()
    _win_end = pd.Timestamp(hist.index[-1]).date().isoformat()
    _n_bars = int(len(hist))

    ind_series = _ind_series(hist, indicator, period_n)
    if ind_series is None:
        raise ValueError(
            f"indicator {indicator}({period_n}) returned an empty series"
        )

    if spec.basis == "price":
        signal_basis = closes
        threshold_value = (
            ind_series + float(threshold) if threshold else ind_series
        )
    else:
        signal_basis = ind_series
        threshold_value = float(threshold)

    # Generate buy/sell signals on threshold crossings.
    signals = _detect_crossings(
        signal_basis, threshold_value, operator,
    )
    # No-look-ahead: the crossing is only KNOWABLE after bar T's close, so
    # the earliest executable bar is T+1. Shift each signal's fill timestamp
    # to the next bar (mirrors workflow_backtester's next-bar-open rule).
    signals = _shift_signals_next_bar(signals, closes.index)

    # Run the simulator.
    price_curve, equity_curve, enriched_signals, trades = _simulate(
        closes, signals, _STARTING_CAPITAL, _FRICTION,
    )
    metrics = _compute_metrics(
        equity_curve, trades, _STARTING_CAPITAL,
        periods_per_year=_periods_per_year(_norm_interval),
    )
    bench_pct = (closes.iloc[-1] / closes.iloc[0] - 1) * 100

    # Summarise indicator series for the chart (drop NaNs at head).
    ind_clean = ind_series.dropna()
    indicator_curve = [
        {"t": ts.isoformat(), "v": round(float(v), 2)}
        for ts, v in ind_clean.items()
    ]

    summary_text = _format_summary(
        sym, indicator, period_n, operator, threshold,
        period, metrics, bench_pct,
    )
    # State the exact tested window so "what interval did you test?" is
    # answered in the reply and reruns are explainable.
    summary_text += (
        f"\n\n_Window: {_win_start} → {_win_end} · {_n_bars} daily bars "
        f"(period '{period}', partial-day bar excluded)._"
    )

    return IndicatorBacktestResult(
        symbol=sym,
        indicator=indicator,
        indicator_period=period_n,
        operator=operator,
        threshold=float(threshold),
        period_label=period,
        price_curve=price_curve,
        equity_curve=equity_curve,
        indicator_curve=indicator_curve,
        signals=enriched_signals,
        metrics=metrics,
        bench_buy_hold_return_pct=round(float(bench_pct), 2),
        summary_text=summary_text,
        window_start=_win_start,
        window_end=_win_end,
        n_bars=_n_bars,
        bar_interval=_norm_interval,
    )


def _detect_crossings(
    series: pd.Series,
    threshold: pd.Series | float,
    operator: str,
) -> list[dict]:
    """Walk the series. Emit a 'buy' signal when `operator` first triggers,
    a 'sell' on the opposite condition. Threshold can be scalar (RSI) or
    a series aligned to the basis (price vs SMA/EMA)."""
    is_series = isinstance(threshold, pd.Series)
    out: list[dict] = []
    in_position = False
    prev_basis: float | None = None
    prev_thr: float | None = None

    # Iterate using the series index so timestamps are preserved.
    for ts, basis_val in series.items():
        if pd.isna(basis_val):
            continue
        thr_val = (
            float(threshold.loc[ts])  # type: ignore[union-attr]
            if is_series and ts in threshold.index  # type: ignore[union-attr]
            else float(threshold)
            if not is_series else float("nan")
        )
        if pd.isna(thr_val):
            prev_basis = float(basis_val)
            continue

        # Crossing detection — needs a previous bar to compare against.
        if prev_basis is not None and prev_thr is not None:
            buy_cond = _eval_op(operator, prev_basis, prev_thr, basis_val, thr_val)
            sell_cond = _eval_op(_inverse_op(operator), prev_basis, prev_thr, basis_val, thr_val)
            if buy_cond and not in_position:
                out.append({
                    "t": ts.isoformat(),
                    "side": "buy",
                    "price": None,  # filled in by simulator
                    "indicator_value": round(float(basis_val), 2),
                })
                in_position = True
            elif sell_cond and in_position:
                out.append({
                    "t": ts.isoformat(),
                    "side": "sell",
                    "price": None,
                    "indicator_value": round(float(basis_val), 2),
                })
                in_position = False

        prev_basis = float(basis_val)
        prev_thr = thr_val if not pd.isna(thr_val) else prev_thr

    return out


def _eval_op(
    op: str, prev_basis: float, prev_thr: float, basis: float, thr: float,
) -> bool:
    """True if the *crossing* defined by `op` happens this bar."""
    if op in ("<", "<=", "crosses_below"):
        return prev_basis >= prev_thr and basis < thr
    if op in (">", ">=", "crosses_above"):
        return prev_basis <= prev_thr and basis > thr
    return False


def _inverse_op(op: str) -> str:
    return {
        "<": ">", "<=": ">=", "crosses_below": "crosses_above",
        ">": "<", ">=": "<=", "crosses_above": "crosses_below",
    }.get(op, op)


def _shift_signals_next_bar(
    signals: list[dict], index: "pd.DatetimeIndex",
) -> list[dict]:
    """Move every signal's fill timestamp to the NEXT bar so the crossing
    detected on bar T fills on T+1 (no look-ahead). A signal printing on
    the final bar has no T+1 and is dropped (can't be filled)."""
    if not signals:
        return signals
    iso_to_pos = {ts.isoformat(): i for i, ts in enumerate(index)}
    out: list[dict] = []
    for s in signals:
        pos = iso_to_pos.get(s["t"])
        if pos is None or pos + 1 >= len(index):
            continue
        s = dict(s)
        s["t"] = index[pos + 1].isoformat()
        out.append(s)
    return out


def _simulate(
    closes: pd.Series, signals: list[dict],
    starting_capital: float, friction: float,
) -> tuple[list[dict], list[dict], list[dict], list[dict]]:
    """Walk each signal in order. Buy = full cash → qty. Sell = qty → cash.
    At each bar in `closes`, the equity = cash + qty × close. Returns
    (price_curve, equity_curve, trades)."""
    cash = starting_capital
    qty = 0.0
    last_buy_value: float | None = None
    trades: list[dict] = []
    sig_iter = iter(signals)
    next_sig = next(sig_iter, None)

    price_curve: list[dict] = []
    equity_curve: list[dict] = []
    enriched_signals: list[dict] = []

    for ts, close in closes.items():
        close_f = float(close)
        ts_iso = ts.isoformat()
        # Fire any signal whose timestamp matches this bar.
        while next_sig is not None and next_sig["t"] == ts_iso:
            if next_sig["side"] == "buy" and qty == 0 and cash > 0:
                qty = (cash * (1 - friction)) / close_f
                last_buy_value = cash
                cash = 0.0
                trades.append({
                    "buy_t": ts_iso, "buy_price": round(close_f, 2),
                    "qty": round(qty, 4),
                })
            elif next_sig["side"] == "sell" and qty > 0:
                proceeds = qty * close_f * (1 - friction)
                if trades and "sell_t" not in trades[-1]:
                    trades[-1]["sell_t"] = ts_iso
                    trades[-1]["sell_price"] = round(close_f, 2)
                    trades[-1]["pnl"] = round(
                        proceeds - (last_buy_value or 0), 2,
                    )
                cash = proceeds
                qty = 0.0
                last_buy_value = None
            enriched_signals.append({
                "t": ts_iso, "side": next_sig["side"],
                "price": round(close_f, 2),
                "indicator_value": next_sig.get("indicator_value"),
            })
            next_sig = next(sig_iter, None)

        equity = cash + qty * close_f
        price_curve.append({"t": ts_iso, "v": round(close_f, 2)})
        equity_curve.append({"t": ts_iso, "v": round(equity, 2)})

    # If still long at the end, close the open trade at the last price
    # for accurate P&L reporting (don't move the equity, just record).
    if qty > 0 and trades and "sell_t" not in trades[-1]:
        last_close = float(closes.iloc[-1])
        proceeds = qty * last_close
        trades[-1]["sell_t"] = closes.index[-1].isoformat()
        trades[-1]["sell_price"] = round(last_close, 2)
        trades[-1]["pnl"] = round(proceeds - (last_buy_value or 0), 2)
        trades[-1]["open_at_end"] = True

    return price_curve, equity_curve, enriched_signals, trades


def _periods_per_year(interval: str) -> float:
    """Bars per year for the given interval, for correct Sharpe/Sortino
    annualization. A weekly series annualized at √252 (instead of √52)
    overstates Sharpe ~2.2×. NSE cash session ≈ 375 min."""
    iv = (interval or "1d").lower()
    if iv in ("1d", "daily", "1day", "d"):
        return 252.0
    if iv in ("1wk", "weekly", "1week", "w", "wk"):
        return 52.0
    if iv in ("1mo", "monthly", "1month", "mo"):
        return 12.0
    import re
    m = re.match(r"(\d+)\s*(m|min|h|hr|hour)", iv)
    if m:
        n = int(m.group(1))
        minutes = n * 60 if m.group(2) in ("h", "hr", "hour") else n
        if minutes > 0:
            return 252.0 * max(375.0 / minutes, 1.0)
    return 252.0


def _compute_metrics(
    equity_curve: list[dict], trades: list[dict], starting_capital: float,
    *, periods_per_year: float = 252.0,
) -> dict:
    if not equity_curve:
        return {
            "total_return_pct": 0.0, "cagr_pct": 0.0, "max_drawdown_pct": 0.0,
            "hit_rate_pct": 0.0, "n_trades": 0, "n_wins": 0,
            "starting_capital": starting_capital, "ending_value": starting_capital,
        }
    ending = equity_curve[-1]["v"]
    total_ret = (ending - starting_capital) / starting_capital * 100
    # CAGR.
    start_dt = datetime.fromisoformat(equity_curve[0]["t"].replace("Z", "+00:00").rstrip("Z"))
    end_dt = datetime.fromisoformat(equity_curve[-1]["t"].replace("Z", "+00:00").rstrip("Z"))
    days = max((end_dt - start_dt).days, 1)
    years = days / 365.25
    cagr = ((ending / starting_capital) ** (1 / years) - 1) * 100 if years > 0 else 0.0
    # Max drawdown.
    peak = equity_curve[0]["v"]
    max_dd = 0.0
    for p in equity_curve:
        peak = max(peak, p["v"])
        dd = (p["v"] - peak) / peak * 100
        max_dd = min(max_dd, dd)
    # Hit rate.
    closed = [t for t in trades if "pnl" in t]
    wins = [t for t in closed if t["pnl"] > 0]
    hit = (len(wins) / len(closed) * 100) if closed else 0.0
    from backend.services.backtest_metrics import (
        daily_returns_from_equity, sharpe_sortino,
    )
    _sharpe, _sortino = sharpe_sortino(
        daily_returns_from_equity([p["v"] for p in equity_curve]),
        periods_per_year=periods_per_year,
        # rf=0 — the sim holds idle capital in cash at 0%, so subtracting a
        # risk-free rate charges every flat day a −rf excess and drags Sharpe
        # deeply negative for any not-fully-invested strategy (see the twin
        # note in workflow_backtester). Measure raw risk-adjusted return.
        rf_annual=0.0,
    )
    return {
        "total_return_pct": round(total_ret, 2),
        "cagr_pct": round(cagr, 2),
        "max_drawdown_pct": round(max_dd, 2),
        "sharpe": _sharpe,
        "sortino": _sortino,
        "hit_rate_pct": round(hit, 2),
        "n_trades": len(closed),
        "n_wins": len(wins),
        "starting_capital": round(starting_capital, 2),
        "ending_value": round(ending, 2),
    }


def _format_summary(
    sym: str, indicator: str, indicator_period: int, operator: str,
    threshold: float, period: str, metrics: dict, bench_pct: float,
) -> str:
    op_word = {
        "<": "drops below", "<=": "drops to or below",
        ">": "rises above", ">=": "rises to or above",
        "crosses_below": "crosses below", "crosses_above": "crosses above",
    }.get(operator, operator)
    spec = _ind_spec(indicator)
    if spec is not None and spec.basis == "price":
        signal = f"price {op_word} {indicator.upper()}({indicator_period})"
    else:
        signal = f"{indicator.upper()}({indicator_period}) {op_word} {threshold:g}"
    # Plain prose — no markdown asterisks. The full chart + metrics row
    # render in the IndicatorBacktestCard, so this text only needs to
    # introduce the result, not duplicate the numbers.
    if metrics["n_trades"] == 0:
        return (
            f"Backtested {sym} entering long when {signal} over the {period} "
            f"window — no signal ever triggered. Buy-and-hold for the same "
            f"window returned {bench_pct:+.1f}%. Try a different threshold or period."
        )
    return (
        f"Here's the backtest for {sym} entering long when {signal} over {period}. "
        f"Strategy returned {metrics['total_return_pct']:+.1f}% across "
        f"{metrics['n_trades']} trades; buy-and-hold returned "
        f"{bench_pct:+.1f}%."
    )
