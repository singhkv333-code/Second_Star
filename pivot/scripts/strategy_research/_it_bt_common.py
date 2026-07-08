"""Shared honest-backtest plumbing for the IT-in-trouble strategies.

Used by it_trouble_backtest_a/b/c.py. Everything here is REAL: yfinance prices,
the repo Trust Battery, the repo cost model, and the repo two-dial confidence
scorer. No LLM, no Azure, no fabricated numbers.

Design choices (no look-ahead):
  * Signals are event-conditioned on a curated weak-print analog sample (the
    TCS-anchored result dates the event study identified). Each event has a
    fixed, pre-declared hold window relative to t0 — no peeking at the realised
    move to decide whether to trade.
  * Fills are NEXT-BAR: a position decided using data up to and including day d
    is entered at day d+1's close-to-close return (one-bar lag baked into the
    window offsets), and costs are charged on the entry bar and the exit bar.
  * Betas for the market-neutral pair are estimated on the PRE-event estimation
    window [t0-130, t0-11] only (strictly out-of-sample vs the hold window).
  * The equity curve moves ONLY on held event days (flat/cash otherwise); the
    Trust Battery then judges that concatenated event-conditioned return path.

Run any script with:  pivot/.venv/bin/python pivot/scripts/strategy_research/it_trouble_backtest_a.py
"""
from __future__ import annotations

import math
import os
import sys
import warnings
from typing import Callable, Optional

import numpy as np
import pandas as pd

warnings.simplefilter("ignore")

# Repo imports (real Trust Battery + costs + two-dial scorer).
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
from backend.services.forward_stats import forward_stats_block, max_drawdown_pct  # noqa: E402
from backend.services.backtest.validation.monte_carlo import monte_carlo_robustness  # noqa: E402
from backend.services.backtest.validation.sub_periods import sub_period_robustness  # noqa: E402
from backend.services.backtest.validation.verdict import trust_verdict  # noqa: E402
from backend.services.trading_costs import round_trip_bps, leg_bps  # noqa: E402
from backend.view_markets.confidence import (  # noqa: E402
    score_outcome_dial, score_expression_dial, letter_band,
)

import yfinance as yf  # noqa: E402

# Curated weak/guidance-cut TCS-anchored result dates (the event study's analog
# sample). t0 = first large-cap print of each weak quarter.
WEAK_ANALOGS = ["2022-04-11", "2022-07-08", "2023-01-09", "2023-04-12",
                "2023-07-12", "2023-10-11", "2024-04-12", "2025-01-09"]
BENCH = "^NSEI"

RT_COST = round_trip_bps() / 10_000.0          # fractional equity round-trip
LEG_BUY = leg_bps("buy")
LEG_SELL = leg_bps("sell")


def fetch(tickers: list[str], start: str = "2021-01-01", end: str = "2025-12-31") -> pd.DataFrame:
    """Adjusted daily closes for `tickers`; drops all-NaN columns, reports gaps."""
    uniq = list(dict.fromkeys(tickers))
    raw = yf.download(uniq, start=start, end=end, auto_adjust=True, progress=False)
    close = raw["Close"] if isinstance(raw.columns, pd.MultiIndex) else raw
    if isinstance(close, pd.Series):
        close = close.to_frame(uniq[0])
    have = [c for c in uniq if c in close.columns and close[c].notna().sum() > 50]
    missing = [c for c in uniq if c not in have]
    if missing:
        print(f"  [degraded] no usable yfinance history for: {missing}")
    return close[have].dropna(how="all")


def estimation_beta(stock: pd.Series, bench: pd.Series, t0_pos: int,
                    idx: pd.DatetimeIndex) -> Optional[float]:
    """OLS beta of `stock` on `bench` over the pre-event window [t0-130, t0-11].
    Strictly out-of-sample vs the hold window — no look-ahead."""
    lo, hi = t0_pos - 130, t0_pos - 11
    if lo < 1 or hi <= lo:
        return None
    s = stock.iloc[lo:hi].values
    b = bench.iloc[lo:hi].values
    m = np.isfinite(s) & np.isfinite(b)
    if m.sum() < 30 or np.std(b[m]) == 0:
        return None
    return float(np.cov(s[m], b[m])[0, 1] / np.var(b[m]))


def build_event_curve(
    daily_leg_ret: Callable[[int, int, pd.DatetimeIndex], list[float]],
    idx: pd.DatetimeIndex,
    win_lo: int,
    win_hi: int,
    *,
    cost_per_event: float,
) -> tuple[list[float], int, list[float]]:
    """Concatenate per-event daily strategy returns into one equity curve.

    `daily_leg_ret(t0_pos, _, idx)` returns the list of daily strategy returns
    for the hold window [t0+win_lo, t0+win_hi]. One round-trip `cost_per_event`
    is debited on the first held bar (entry) of each event. Returns
    (equity_curve, n_events_traded, per_event_returns)."""
    equity = [1.0]
    per_event: list[float] = []
    n_ev = 0
    for a in WEAK_ANALOGS:
        pos = idx.searchsorted(pd.Timestamp(a))
        lo, hi = pos + win_lo, pos + win_hi
        if lo < 131 or hi >= len(idx):
            continue
        seg = daily_leg_ret(pos, hi, idx)
        if seg is None or not len(seg):
            continue
        n_ev += 1
        start_eq = equity[-1]
        first = True
        for r in seg:
            r_net = (r if np.isfinite(r) else 0.0) - (cost_per_event if first else 0.0)
            equity.append(equity[-1] * (1.0 + r_net))
            first = False
        per_event.append(equity[-1] / start_eq - 1.0)
    return equity, n_ev, per_event


def benchmark_curve(bench_ret: pd.Series, idx: pd.DatetimeIndex,
                    win_lo: int, win_hi: int) -> list[float]:
    """NIFTY buy-hold over the SAME concatenated event windows (no costs — it's
    the do-nothing yardstick)."""
    equity = [1.0]
    for a in WEAK_ANALOGS:
        pos = idx.searchsorted(pd.Timestamp(a))
        lo, hi = pos + win_lo, pos + win_hi
        if lo < 131 or hi >= len(idx):
            continue
        for r in bench_ret.iloc[lo:hi + 1].fillna(0.0).values:
            equity.append(equity[-1] * (1.0 + float(r)))
    return equity


def _p_from_t(t: float) -> float:
    """Two-sided p-value from a t/z stat via the normal approx (large-window)."""
    return 2.0 * (1.0 - 0.5 * (1.0 + math.erf(abs(t) / math.sqrt(2.0))))


def run_battery(equity: list[float], n_trades: int, num_trials: int) -> dict:
    rets = pd.Series(equity).pct_change().dropna().tolist()
    fs = forward_stats_block(equity, num_trials=num_trials)
    mc = monte_carlo_robustness(rets, drawdown_tolerance_pct=-20.0)
    sp = sub_period_robustness(equity, n_periods=4)
    total_ret = (equity[-1] / equity[0] - 1.0) * 100.0
    mdd = max_drawdown_pct(equity)
    verdict = trust_verdict(forward_stats=fs, monte_carlo=mc, sub_periods=sp,
                            total_return_pct=total_ret, n_trades=n_trades)
    win_rate = 100.0 * sum(1 for r in rets if r > 0) / len(rets) if rets else 0.0
    return {"fs": fs, "mc": mc, "sp": sp, "total_ret": total_ret, "mdd": mdd,
            "verdict": verdict, "win_rate": win_rate, "n_obs": fs["n_obs"]}


def print_report(name: str, struct: str, bt: dict, bench_eq: list[float],
                 per_event: list[float], n_ev: int,
                 *, caar_pct: float, caar_t: float, cost_survival: float,
                 payoff_pop: Optional[float], hit_dir_positive: bool,
                 practical: str, grade: str, place_it: str,
                 option_proxy_note: Optional[str] = None) -> None:
    fs, mc, sp, v = bt["fs"], bt["mc"], bt["sp"], bt["verdict"]
    bench_ret = (bench_eq[-1] / bench_eq[0] - 1.0) * 100.0
    # Event-level hit rate (direction the thesis predicts).
    hits = sum(1 for r in per_event if (r > 0) == hit_dir_positive)
    hit_rate = hits / len(per_event) if per_event else 0.0
    sig_p = _p_from_t(caar_t)

    print("=" * 80)
    print(name)
    print("=" * 80)
    print("STRUCTURE")
    for line in struct.strip().splitlines():
        print("  " + line.strip())
    if option_proxy_note:
        print("  LIMITATION: " + option_proxy_note)
    print()
    print("REAL BACKTEST METRICS (event-conditioned, next-bar fills, repo costs)")
    print(f"  events traded            : {n_ev}")
    print(f"  return observations(n)   : {bt['n_obs']}")
    print(f"  total return             : {bt['total_ret']:+.2f}%")
    print(f"  NIFTY buy-hold (same win): {bench_ret:+.2f}%   "
          f"=> excess {bt['total_ret']-bench_ret:+.2f}%")
    print(f"  max drawdown             : {bt['mdd']:+.2f}%")
    print(f"  daily win rate           : {bt['win_rate']:.1f}%")
    print(f"  per-event returns %      : {[round(r*100,2) for r in per_event]}")
    print()
    print("TRUST BATTERY (real forward_stats / monte_carlo / sub_periods / verdict)")
    print(f"  observed Sharpe (per-bar): {fs['observed_sharpe']}")
    print(f"  skew / kurtosis          : {fs['skew']} / {fs['kurtosis']}")
    print(f"  PSR                      : {fs['psr']}")
    print(f"  Deflated Sharpe (trials) : {fs['deflated_sharpe']} (num_trials={fs['num_trials']})")
    print(f"  MinTRL (obs needed)      : {fs['min_trl']}  vs n_obs={fs['n_obs']}")
    if mc:
        print(f"  MC dd_median / dd_p95    : {mc['dd_median_pct']}% / {mc['dd_p95_severity_pct']}%")
        print(f"  MC prob_loss / P(dd<-20) : {mc['prob_loss']} / {mc['prob_dd_worse_than_tol']}")
    if sp:
        print(f"  sub-period returns %     : {sp['period_returns_pct']}")
        print(f"  positive-period frac     : {sp['positive_period_frac']}   concentration={sp['concentration']}")
    print(f"  VERDICT                  : {v['verdict'].upper()} ({v['label']}, conf={v['confidence']})")
    print(f"  flags                    : {v['flags']}")
    print(f"    -> {v['rationale']}")
    print()

    # ── Two-dial alignment (real repo scorer) ──
    verdict_str = v["verdict"]
    out = score_outcome_dial(
        hit_rate=hit_rate, relationship_strength=None,
        sample_n=n_ev, min_trl=None, verdict=verdict_str,
    )
    expr = score_expression_dial(
        caar_bhar_alignment=_align(caar_pct), significance_p=sig_p,
        cost_survival=cost_survival, payoff_pop=payoff_pop,
        verdict=verdict_str, deflated_sharpe=fs["deflated_sharpe"],
        n_obs=fs["n_obs"], min_trl=fs["min_trl"],
    )
    print("TWO-DIAL ALIGNMENT (separate dials, never averaged)")
    print(f"  event-level hit-rate     : {hit_rate:.0%} ({hits}/{len(per_event)})  "
          f"CAAR={caar_pct:+.2f}% t={caar_t:+.2f} (p={sig_p:.3f})")
    if out.suppressed:
        print(f"  OUTCOME dial             : SUPPRESSED — {out.rationale}")
    else:
        print(f"  OUTCOME dial             : {out.letter} ({out.score}/100)")
        print(f"    -> {out.rationale}")
    if expr.suppressed:
        print(f"  EXPRESSION dial          : SUPPRESSED — {expr.rationale}")
    else:
        print(f"  EXPRESSION dial          : {expr.letter} ({expr.score}/100)")
        print(f"    -> {expr.rationale}")
    print()
    print("PRACTICALNESS")
    for line in practical.strip().splitlines():
        print("  " + line.strip())
    print()
    print(f"GRADE: {grade}")
    print(f"PLACE-IT: {place_it}")
    print()
    return {"outcome": out, "expression": expr, "hit_rate": hit_rate,
            "verdict": verdict_str, "total_ret": bt["total_ret"],
            "bench_ret": bench_ret, "psr": fs["psr"]}


def _align(caar_pct: float) -> float:
    """CAAR% (window) -> 0..1 alignment, mirroring confidence._alignment_from_events
    (_CAAR_SCALE=20 on a FRACTION). Bearish thesis: a negative IT CAAR (or a
    positive defensive CAAR) is aligned, so we pass the magnitude in the thesis
    direction as a positive fraction."""
    return max(0.0, min(1.0, 0.5 + (abs(caar_pct) / 100.0) * 20.0))
