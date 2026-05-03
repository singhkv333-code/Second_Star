"""Single-symbol indicator backtester (#55).

Backs the chat surface's "backtest <SYMBOL> when RSI drops below 50" /
"buying <SYMBOL> when it crossed 200 EMA" intents. Distinct from the
fundamentals expression backtester (which needs the financials Postgres
DB) — this one runs entirely off yfinance daily OHLCV data + pandas_ta.

Strategy semantics (long-only):
  - Enter long on the buy signal bar, full cash → shares (1% friction).
  - Exit on the next opposite signal, shares → cash.
  - Hold otherwise.

Returns:
  - price_curve:      [{t, v}]   close price series
  - equity_curve:     [{t, v}]   strategy portfolio value
  - indicator_curve:  [{t, v}]   indicator series (RSI/SMA/EMA)
  - signals:          [{t, side: "buy"|"sell", price, indicator_value}]
  - metrics:          {cagr_pct, total_return_pct, max_drawdown_pct,
                       hit_rate_pct, n_trades, n_wins, starting_capital,
                       ending_value}
  - bench_buy_hold_return_pct  (RELIANCE-only buy-and-hold over the same
                                window for comparison)
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

import pandas as pd
import pandas_ta_classic as ta  # type: ignore[import-untyped]
import yfinance as yf  # type: ignore[import-untyped]

logger = logging.getLogger(__name__)


_FRICTION = 0.001  # 10 bps slippage + brokerage per side
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


_OperatorLiteral = Literal[
    "<", ">", "<=", ">=", "crosses_below", "crosses_above",
]


def run_indicator_backtest(
    *,
    symbol: str,
    indicator: Literal["rsi", "sma", "ema"],
    indicator_period: int = 14,
    operator: _OperatorLiteral = "<",
    threshold: float = 50.0,
    period: str = "5y",
    exchange: str = "NSE",
) -> IndicatorBacktestResult:
    """Run the backtest. Raises ValueError on bad inputs / no data."""
    sym = symbol.upper().strip()
    suffix = ".NS" if exchange.upper() == "NSE" else ".BO"
    yf_sym = sym if sym.endswith((".NS", ".BO")) else f"{sym}{suffix}"

    hist = yf.Ticker(yf_sym).history(period=period, interval="1d")
    if hist.empty or len(hist) < max(indicator_period * 2, 30):
        raise ValueError(
            f"insufficient data for {sym} over {period} (got {len(hist)} bars)"
        )

    closes = hist["Close"].astype(float)

    # Compute the indicator series — for SMA/EMA we compare against PRICE,
    # not the indicator value, so the threshold field carries the period.
    if indicator == "rsi":
        ind_series = ta.rsi(closes, length=indicator_period)
        signal_basis = ind_series  # compare RSI to threshold
        threshold_value = float(threshold)
    elif indicator == "sma":
        ind_series = ta.sma(closes, length=indicator_period)
        signal_basis = closes  # compare PRICE to SMA value at each bar
        threshold_value = ind_series  # type: ignore[assignment]
    elif indicator == "ema":
        ind_series = ta.ema(closes, length=indicator_period)
        signal_basis = closes
        threshold_value = ind_series  # type: ignore[assignment]
    else:
        raise ValueError(f"unsupported indicator: {indicator}")

    if ind_series is None or ind_series.dropna().empty:
        raise ValueError(f"indicator {indicator}({indicator_period}) is empty")

    # Generate buy/sell signals on threshold crossings.
    signals = _detect_crossings(
        signal_basis, threshold_value, operator,
    )

    # Run the simulator.
    price_curve, equity_curve, enriched_signals, trades = _simulate(
        closes, signals, _STARTING_CAPITAL, _FRICTION,
    )
    metrics = _compute_metrics(equity_curve, trades, _STARTING_CAPITAL)
    bench_pct = (closes.iloc[-1] / closes.iloc[0] - 1) * 100

    # Summarise indicator series for the chart (drop NaNs at head).
    ind_clean = ind_series.dropna()
    indicator_curve = [
        {"t": ts.isoformat(), "v": round(float(v), 2)}
        for ts, v in ind_clean.items()
    ]

    summary_text = _format_summary(
        sym, indicator, indicator_period, operator, threshold,
        period, metrics, bench_pct,
    )

    return IndicatorBacktestResult(
        symbol=sym,
        indicator=indicator,
        indicator_period=indicator_period,
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


def _compute_metrics(
    equity_curve: list[dict], trades: list[dict], starting_capital: float,
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
    return {
        "total_return_pct": round(total_ret, 2),
        "cagr_pct": round(cagr, 2),
        "max_drawdown_pct": round(max_dd, 2),
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
    if indicator in ("sma", "ema"):
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
