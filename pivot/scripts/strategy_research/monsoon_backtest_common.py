"""Shared honest-backtest engine for the three Monsoon-view strategies.

NO backend LLM is touched anywhere. Data = yfinance only. All risk metrics come
from the REAL repo Trust Battery (forward_stats_block / monte_carlo_robustness /
sub_period_robustness / trust_verdict) and the REAL two-dial confidence scorer
(backend.view_markets.confidence). Costs come from backend.services.trading_costs.

Honesty rules baked in:
  * ONE-BAR LAG: the in-market mask is shifted +1 trading bar before it is used,
    so a position is taken on the bar AFTER the signal/window opens (next-bar
    fill, no look-ahead). Calendar windows are known in advance, but we still lag
    so the cost/fill timing is realistic.
  * Real STT + slippage charged via trading_costs.leg_bps on every entry and exit
    transition, per leg.
  * Beta hedge (S2): index-short beta estimated on an EXPANDING window of returns
    strictly BEFORE the season's entry date (no look-ahead), clamped to [0.4,1.6].
  * No fabricated option IV: S3 is an explicit underlying-delta PROXY, labelled.
"""
from __future__ import annotations

import json
import sys
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
import yfinance as yf

warnings.filterwarnings("ignore")
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # repo root for `backend`

from backend.services.forward_stats import forward_stats_block, max_drawdown_pct
from backend.services.backtest.validation.monte_carlo import monte_carlo_robustness
from backend.services.backtest.validation.sub_periods import sub_period_robustness
from backend.services.backtest.validation.verdict import trust_verdict
from backend.services.trading_costs import leg_bps, round_trip_bps
from backend.view_markets.confidence import (
    score_outcome_dial,
    score_expression_dial,
)

HERE = Path(__file__).resolve().parent
NIFTY = "^NSEI"
START, END = "2008-10-01", "2025-01-01"
YEARS = list(range(2009, 2025))

# IMD end-of-season rainfall as % of Long Period Average (the resolver).
LPA = {2009: 78, 2010: 102, 2011: 102, 2012: 93, 2013: 106, 2014: 88,
       2015: 86, 2016: 97, 2017: 95, 2018: 91, 2019: 110, 2020: 109,
       2021: 99, 2022: 106, 2023: 94, 2024: 108}
NORMAL_CUT = 96
NORMAL_YEARS = [y for y in YEARS if LPA[y] >= NORMAL_CUT]
DEFICIENT_YEARS = [y for y in YEARS if LPA[y] < NORMAL_CUT]

# Per-leg fractional cost (buy+sell averaged) from the real cost module.
LEG_RT = (leg_bps("buy") + leg_bps("sell"))  # round-trip fraction per leg

WIN = {
    "sowing":   (("06", "01"), ("08", "31")),
    "forecast": (("04", "15"), ("06", "15")),
    "s3":       (("04", "15"), ("09", "30")),
}


def fetch(t: str) -> pd.Series:
    df = yf.download(t, start=START, end=END, progress=False, auto_adjust=True)
    if df is None or len(df) == 0:
        raise RuntimeError(f"yfinance returned NOTHING for {t} — degrade, do not fabricate.")
    c = df["Close"]
    if isinstance(c, pd.DataFrame):
        c = c.iloc[:, 0]
    return c.dropna()


def in_window(idx: pd.DatetimeIndex, year: int, w: str) -> pd.Series:
    (sm, sd), (em, ed) = WIN[w]
    return (idx >= pd.Timestamp(f"{year}-{sm}-{sd}")) & (idx <= pd.Timestamp(f"{year}-{em}-{ed}"))


def simulate(port_ret: pd.Series, in_market: pd.Series, n_legs: int,
             *, leg_rt: float = LEG_RT, start_val: float = 100.0):
    """Honest daily simulator. ``in_market`` is LAGGED one bar inside (next-bar
    fill). Round-trip cost charged on every flat->in entry and in->flat exit, per
    leg. Returns (equity_list, held_daily_returns, per_trade_returns)."""
    im = in_market.reindex(port_ret.index).fillna(False).astype(bool)
    im = im.shift(1).fillna(False).astype(bool)  # ONE-BAR LAG (next-bar fill)
    eq = [start_val]
    held: list[float] = []
    trade_rets: list[float] = []
    prev = False
    cur_trade_mult = 1.0
    for dt, r in port_ret.items():
        now = bool(im.loc[dt])
        rr = float(r) if (now and np.isfinite(r)) else 0.0
        if now and not prev:                      # entry day
            rr -= leg_rt * n_legs
            cur_trade_mult = 1.0
        if prev and not now:                      # exit day (charge exit cost)
            exit_mult = (1.0 - leg_rt * n_legs)
            eq.append(eq[-1] * exit_mult)
            cur_trade_mult *= exit_mult
            trade_rets.append(cur_trade_mult - 1.0)
        if now:
            eq.append(eq[-1] * (1.0 + rr))
            held.append(rr)
            cur_trade_mult *= (1.0 + rr)
        prev = now
    if prev:  # still in-market at series end -> close the open trade record
        trade_rets.append(cur_trade_mult - 1.0)
    return eq, held, trade_rets


def cagr(equity: list[float], n_days: int) -> float:
    yrs = n_days / 252.0
    if yrs <= 0 or equity[0] <= 0:
        return 0.0
    return ((equity[-1] / equity[0]) ** (1.0 / yrs) - 1.0) * 100.0


def nifty_buyhold(nifty: pd.Series, index: pd.DatetimeIndex):
    """Buy-hold NIFTY over the same calendar span for benchmarking."""
    s = nifty.reindex(index).dropna()
    if len(s) < 2:
        return None
    total = (s.iloc[-1] / s.iloc[0] - 1.0) * 100.0
    eq = (s / s.iloc[0] * 100.0).tolist()
    return {
        "total_return_pct": round(total, 1),
        "cagr_pct": round(cagr(eq, len(s)), 2),
        "max_dd_pct": round(max_drawdown_pct(eq) or 0.0, 1),
    }


def t_to_p_twosided(t: float, dof: int) -> float:
    """Two-sided p-value for a t-stat (normal approx for the dial; dof large)."""
    from backend.services.forward_stats import _norm_cdf
    z = abs(float(t))
    return float(2.0 * (1.0 - _norm_cdf(z)))


def run_battery(name, equity, held, trade_rets, n_trades, num_trials,
                bench, dd_tol=-20.0):
    fs = forward_stats_block(equity, num_trials=num_trials)
    mc = monte_carlo_robustness(held, n_sims=2000, drawdown_tolerance_pct=dd_tol)
    sp = sub_period_robustness(equity, n_periods=4)
    total = (equity[-1] / equity[0] - 1.0) * 100.0
    mdd = max_drawdown_pct(equity)
    verdict = trust_verdict(forward_stats=fs, monte_carlo=mc, sub_periods=sp,
                            total_return_pct=total, n_trades=n_trades)
    wins = sum(1 for r in trade_rets if r > 0)
    win_rate = (wins / len(trade_rets) * 100.0) if trade_rets else 0.0
    cg = cagr(equity, len(held) if held else 1)
    out = {
        "name": name,
        "total_return_pct": round(total, 1),
        "cagr_in_market_pct": round(cg, 2),
        "max_dd_pct": round(mdd or 0.0, 1),
        "win_rate_pct": round(win_rate, 1),
        "n_trades": n_trades,
        "wins": wins,
        "in_market_days": len(held),
        "terminal_equity": round(equity[-1], 1),
        "forward_stats": fs,
        "monte_carlo": mc,
        "sub_periods": sp,
        "verdict": verdict,
        "benchmark_nifty": bench,
    }
    return out


def print_battery(out):
    fs, mc, sp, v = out["forward_stats"], out["monte_carlo"], out["sub_periods"], out["verdict"]
    print(f"\n===== {out['name']} =====")
    print(f"  total={out['total_return_pct']}%  CAGR(in-mkt)={out['cagr_in_market_pct']}%  "
          f"maxDD={out['max_dd_pct']}%  win={out['win_rate_pct']}% ({out['wins']}/{out['n_trades']})  "
          f"in-mkt days={out['in_market_days']}")
    b = out["benchmark_nifty"]
    if b:
        print(f"  NIFTY buy-hold (same span): total={b['total_return_pct']}%  "
              f"CAGR={b['cagr_pct']}%  maxDD={b['max_dd_pct']}%")
    print(f"  forward_stats: obsSharpe={fs['observed_sharpe']} skew={fs['skew']} kurt={fs['kurtosis']} "
          f"n_obs={fs['n_obs']} PSR={fs['psr']} DSR={fs['deflated_sharpe']} MinTRL={fs['min_trl']} "
          f"trials={fs['num_trials']}")
    if mc:
        print(f"  monte_carlo : dd_median={mc['dd_median_pct']}% dd_p95={mc['dd_p95_severity_pct']}% "
              f"dd_worst={mc['dd_worst_pct']}% term_med={mc['terminal_median_pct']}% "
              f"prob_loss={mc['prob_loss']} P(dd<{mc['drawdown_tolerance_pct']}%)={mc['prob_dd_worse_than_tol']}")
    print(f"  sub_periods : returns={sp['period_returns_pct']} pos_frac={sp['positive_period_frac']} "
          f"concentration={sp['concentration']}")
    print(f"  VERDICT     : {v['verdict']}  conf={v['confidence']}  flags={v['flags']}")
    print(f"                {v['rationale']}")


def two_dials(out, *, hit_rate, sample_n, relationship_strength,
              caar_pct, sig_t, dof=14):
    """Score the two SEPARATE dials with grounded inputs. cost_survival is
    measured (did the strategy stay net-positive after real costs?)."""
    v = out["verdict"]["verdict"]
    fs = out["forward_stats"]
    caar_frac = caar_pct / 100.0
    sig_p = t_to_p_twosided(sig_t, dof)
    # cost survival: net total return positive => survives; scale by margin.
    cost_survival = float(np.clip(0.5 + (out["total_return_pct"] / 100.0) / 4.0, 0.0, 1.0))
    od = score_outcome_dial(
        hit_rate=hit_rate, relationship_strength=relationship_strength,
        sample_n=sample_n, verdict=v,
    )
    ed = score_expression_dial(
        caar_bhar_alignment=float(np.clip(0.5 + caar_frac * 20.0, 0.0, 1.0)),
        significance_p=sig_p, cost_survival=cost_survival, verdict=v,
        deflated_sharpe=fs["deflated_sharpe"], n_obs=fs["n_obs"], min_trl=fs["min_trl"],
    )
    print(f"  --- TWO-DIAL ALIGNMENT ---")
    print(f"  OUTCOME    : {od.letter} {od.score}  | {od.rationale}")
    print(f"  EXPRESSION : {ed.letter} {ed.score}  | {ed.rationale}")
    return {"outcome": {"letter": od.letter, "score": od.score, "rationale": od.rationale},
            "expression": {"letter": ed.letter, "score": ed.score, "rationale": ed.rationale}}


def save(out, dials, path_name):
    out = dict(out)
    out["two_dial"] = dials
    p = HERE / "_out" / path_name
    p.parent.mkdir(exist_ok=True)
    p.write_text(json.dumps(out, indent=1, default=str))
    print(f"  saved -> {p}")
