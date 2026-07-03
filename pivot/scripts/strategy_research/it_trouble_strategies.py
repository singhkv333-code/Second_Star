"""IT-in-trouble — 3 strategy simulated equity curves + the REAL Trust Battery.

Builds an event-conditioned daily equity curve for each of the 3 designed
strategies from REAL yfinance returns across the weak-print analog sample, then
runs the repo's Trust Battery on each curve:
    forward_stats_block · monte_carlo_robustness · sub_period_robustness · trust_verdict

These are STYLIZED, event-window proxies (the full option/pair backtest is the
backtest stage's job) — labelled as such, no fabricated numbers. Real STT +
slippage from backend.services.trading_costs applied per round trip.

Run:  pivot/.venv/bin/python pivot/scripts/strategy_research/it_trouble_strategies.py
"""
from __future__ import annotations

import os
import sys
import warnings

import numpy as np
import pandas as pd

warnings.simplefilter("ignore")

# Repo imports (Trust Battery + costs).
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
from backend.services.forward_stats import forward_stats_block  # noqa: E402
from backend.services.backtest.validation.monte_carlo import monte_carlo_robustness  # noqa: E402
from backend.services.backtest.validation.sub_periods import sub_period_robustness  # noqa: E402
from backend.services.backtest.validation.verdict import trust_verdict  # noqa: E402
from backend.services.trading_costs import round_trip_bps  # noqa: E402

import yfinance as yf  # noqa: E402

WEAK_ANALOGS = ["2022-04-11", "2022-07-08", "2023-01-09", "2023-04-12",
                "2023-07-12", "2023-10-11", "2024-04-12", "2025-01-09"]
REACT_LO, DRIFT_HI = -1, 20   # held window per event: t0-1 .. t0+20
DEFENSIVES = ["NESTLEIND.NS", "HINDUNILVR.NS", "ITC.NS", "DABUR.NS"]
IT_BASKET = ["INFY.NS", "TCS.NS", "HCLTECH.NS", "TECHM.NS"]
BENCH = "^NSEI"
TICKERS = list(dict.fromkeys(DEFENSIVES + IT_BASKET + [BENCH]))

RT_COST = round_trip_bps() / 10_000.0   # fractional round-trip equity cost
print(f"Repo round-trip equity cost = {round_trip_bps():.1f} bps "
      f"({RT_COST*100:.3f}% per entry+exit)\n")


def fetch():
    raw = yf.download(TICKERS, start="2021-08-01", end="2025-12-31",
                      auto_adjust=True, progress=False)
    close = raw["Close"] if isinstance(raw.columns, pd.MultiIndex) else raw
    return close[[c for c in TICKERS if c in close.columns]].dropna(how="all")


def event_slices(idx: pd.DatetimeIndex):
    """Yield (lo, hi) positional slices for each analog event's held window."""
    for a in WEAK_ANALOGS:
        pos = idx.searchsorted(pd.Timestamp(a))
        lo, hi = pos + REACT_LO, pos + DRIFT_HI
        if lo >= 0 and hi < len(idx):
            yield lo, hi


def build_curve(daily_ret: pd.Series, *, cost_per_event: float) -> list[float]:
    """Concatenate the strategy's daily returns across event windows into an
    equity curve, debiting one round-trip cost at each event entry."""
    equity = [1.0]
    for k, (lo, hi) in enumerate(event_slices(daily_ret.index)):
        seg = daily_ret.iloc[lo:hi + 1].fillna(0.0).values
        first = True
        for r in seg:
            r_net = r - (cost_per_event if first else 0.0)
            equity.append(equity[-1] * (1.0 + r_net))
            first = False
    return equity


def battery(name: str, equity: list[float], n_trades: int, num_trials: int):
    rets = pd.Series(equity).pct_change().dropna().tolist()
    fs = forward_stats_block(equity, num_trials=num_trials)
    mc = monte_carlo_robustness(rets, drawdown_tolerance_pct=-20.0)
    sp = sub_period_robustness(equity, n_periods=4)
    total_ret = (equity[-1] / equity[0] - 1.0) * 100.0
    verdict = trust_verdict(forward_stats=fs, monte_carlo=mc, sub_periods=sp,
                            total_return_pct=total_ret, n_trades=n_trades)
    print("=" * 78)
    print(f"STRATEGY — {name}")
    print("=" * 78)
    print(f"  events(trades)={n_trades}  obs={fs['n_obs']}  total_return={total_ret:+.2f}%")
    print(f"  forward_stats: obs_sharpe={fs['observed_sharpe']} skew={fs['skew']} "
          f"kurt={fs['kurtosis']} PSR={fs['psr']} DSR={fs['deflated_sharpe']} "
          f"minTRL={fs['min_trl']}")
    if mc:
        print(f"  monte_carlo:   dd_median={mc['dd_median_pct']}% "
              f"dd_p95={mc['dd_p95_severity_pct']}% prob_loss={mc['prob_loss']} "
              f"P(dd<-20%)={mc['prob_dd_worse_than_tol']}")
    if sp:
        print(f"  sub_periods:   per_period={sp['period_returns_pct']} "
              f"pos_frac={sp['positive_period_frac']} conc={sp['concentration']}")
    print(f"  VERDICT: {verdict['verdict'].upper()} ({verdict['label']}, "
          f"conf={verdict['confidence']}) flags={verdict['flags']}")
    print(f"    → {verdict['rationale']}\n")


def main():
    close = fetch()
    ret = close.pct_change()
    idx = ret.index
    bench = ret[BENCH]
    have = lambda lst: [c for c in lst if c in ret.columns]
    defs, its = have(DEFENSIVES), have(IT_BASKET)
    def_basket = ret[defs].mean(axis=1)
    it_basket = ret[its].mean(axis=1)
    infy = ret["INFY.NS"] if "INFY.NS" in ret.columns else it_basket
    n_ev = sum(1 for _ in event_slices(idx))
    print(f"Loaded {ret.shape[0]} days; {n_ev} weak-analog events held [{REACT_LO},+{DRIFT_HI}].\n")

    # (A) Bear put spread on INFY — defined-risk bearish. Stylized as a net
    # short-delta -0.40 position on INFY's abnormal (vs NIFTY) move, with the
    # defined-risk floor capping per-event downside at the net debit (~ -1.5%).
    infy_excess = (infy - bench).fillna(0.0)
    a_ret = (-0.40 * infy_excess).clip(lower=-0.015)   # cap = net debit risk
    a_curve = build_curve(a_ret.reindex(idx).fillna(0.0), cost_per_event=RT_COST)

    # (B) Market-neutral pair — long defensives basket vs short IT basket (SSF),
    # 50/50, beta-stripped (both legs ~beta 1 so the difference is ~neutral).
    b_ret = 0.5 * def_basket - 0.5 * it_basket
    b_curve = build_curve(b_ret.reindex(idx).fillna(0.0), cost_per_event=2 * RT_COST)

    # (C) Defensive rotation basket — long defensives EXCESS over NIFTY (the
    # long-only tilt; IT is AVOID-annotated, not shorted).
    c_ret = (def_basket - bench).fillna(0.0)
    c_curve = build_curve(c_ret.reindex(idx).fillna(0.0), cost_per_event=RT_COST)

    battery("A · INFY bear put spread (Pre-position, defined-risk option)",
            a_curve, n_trades=n_ev, num_trials=3)
    battery("B · Long FMCG vs short-IT-SSF pair (Hybrid, market-neutral)",
            b_curve, n_trades=n_ev, num_trials=3)
    battery("C · Defensive rotation basket, IT AVOID-annotated (Confirmation)",
            c_curve, n_trades=n_ev, num_trials=3)


if __name__ == "__main__":
    main()
