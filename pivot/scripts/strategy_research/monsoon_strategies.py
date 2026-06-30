"""Monsoon strategies — build REAL seasonal equity curves from yfinance and run
the repo Trust Battery (forward_stats_block / monte_carlo_robustness /
sub_period_robustness / trust_verdict) on each. NO backend LLM. No fabrication.

The 3 designs (grounded in monsoon_event_study.py + monsoon_windows.py):

  S1  KIND_BASKET   CONFIRMATION  "Kharif Sowing Agri-Input Basket"
      Long EW [COROMANDEL, CHAMBLFERT, RALLIS, UPL, PIIND]; in-market the
      SOWING window [Jun01..Aug31] only (the window with the genuine, right-sign,
      LPA-conditional divergence: agri-input normal-minus-deficient +4.73%). Flat
      otherwise. Grounded: fertiliser/agrochem volumes physically track sown
      area, which tracks rainfall.

  S2  KIND_PAIR     PRE_POSITION  "Forecast Run-up: Tractor/2W vs NIFTY"
      Long EW [M&M, TVSMOTOR] minus NIFTY (beta-neutral) over the FORECAST window
      [Apr15..Jun15] only — the window where tractor/2W shows the strongest +
      most-significant seasonal pop (+7.46%, t=2.83). Isolates the genuine rural
      run-up over market beta. Armed pre-position (early April).

  S3  KIND_OPTION   HYBRID        "M&M Defined-Risk Monsoon Call (proxy)"
      Directional long M&M held [Apr15..Sep30] as the UNDERLYING driver of a
      hybrid-staged bull call SPREAD (defined risk). Battery runs on the
      underlying directional series scaled by ~0.5 to mimic spread delta; this is
      a PROXY for the option P&L (no historical IV in yfinance) and is labelled
      as such. M&M = most consistent positive monsoon-beta auto + most liquid
      tractor F&O proxy.

Costs: repo trading_costs round-trip applied at each seasonal entry/exit.
"""
from __future__ import annotations

import sys
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
import yfinance as yf

warnings.filterwarnings("ignore")
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # repo root for `backend`

from backend.services.forward_stats import forward_stats_block
from backend.services.backtest.validation.monte_carlo import monte_carlo_robustness
from backend.services.backtest.validation.sub_periods import sub_period_robustness
from backend.services.backtest.validation.verdict import trust_verdict
from backend.services.trading_costs import round_trip_bps

NIFTY = "^NSEI"
START, END = "2008-10-01", "2025-01-01"
YEARS = list(range(2009, 2025))

S1_BASKET = ["COROMANDEL.NS", "CHAMBLFERT.NS", "RALLIS.NS", "UPL.NS", "PIIND.NS"]
S2_LONG = ["M&M.NS", "TVSMOTOR.NS"]
S3_NAME = "M&M.NS"

WIN = {
    "sowing":   (("06", "01"), ("08", "31")),
    "forecast": (("04", "15"), ("06", "15")),
    "s3":       (("04", "15"), ("09", "30")),
}
RT = round_trip_bps() / 10_000.0  # fractional round-trip cost per leg


def fetch(t):
    df = yf.download(t, start=START, end=END, progress=False, auto_adjust=True)
    c = df["Close"]
    if isinstance(c, pd.DataFrame):
        c = c.iloc[:, 0]
    return c.dropna()


def rets(s):
    return s.pct_change()


def in_window(idx, year, w):
    (sm, sd), (em, ed) = WIN[w]
    return (idx >= pd.Timestamp(f"{year}-{sm}-{sd}")) & (idx <= pd.Timestamp(f"{year}-{em}-{ed}"))


def build_equity(daily_port_ret: pd.Series, in_market: pd.Series, n_legs: int, start_val=100.0):
    """Compound daily returns when in_market; charge round-trip cost on each
    entry day (transition flat->in) and exit day (in->flat). Returns equity list
    and the in-market daily return list (for monte_carlo)."""
    eq = [start_val]
    held = []  # daily returns actually experienced (in-market only)
    prev = False
    im = in_market.reindex(daily_port_ret.index).fillna(False)
    for dt, r in daily_port_ret.items():
        now = bool(im.loc[dt])
        rr = float(r) if (now and np.isfinite(r)) else 0.0
        # entry cost on first in-market day
        if now and not prev:
            rr -= RT * n_legs
        # exit cost on the day we go flat (charge on the transition day)
        if prev and not now:
            eq.append(eq[-1] * (1.0 - RT * n_legs))
        if now:
            eq.append(eq[-1] * (1.0 + rr))
            held.append(rr)
        prev = now
    return eq, held


def battery(name, equity, held, n_trades, num_trials):
    fs = forward_stats_block(equity, num_trials=num_trials)
    mc = monte_carlo_robustness(held, n_sims=2000, drawdown_tolerance_pct=-20.0)
    sp = sub_period_robustness(equity, n_periods=4)
    total = (equity[-1] / equity[0] - 1.0) * 100.0
    v = trust_verdict(forward_stats=fs, monte_carlo=mc, sub_periods=sp,
                      total_return_pct=total, n_trades=n_trades)
    print(f"\n========== {name} ==========")
    print(f"  in-market days={len(held)}  seasonal trades={n_trades}  "
          f"total return={total:.1f}%  terminal equity={equity[-1]:.1f}")
    print(f"  forward_stats: {fs}")
    print(f"  monte_carlo : dd_median={mc['dd_median_pct']}%  dd_p95={mc['dd_p95_severity_pct']}%  "
          f"term_median={mc['terminal_median_pct']}%  term_p05={mc['terminal_p05_pct']}%  "
          f"prob_loss={mc['prob_loss']}  P(dd<-20%)={mc['prob_dd_worse_than_tol']}")
    print(f"  sub_periods : {sp}")
    print(f"  VERDICT     : {v['verdict']}  conf={v['confidence']}  flags={v['flags']}")
    print(f"                {v['rationale']}")
    return {"forward_stats": fs, "monte_carlo": mc, "sub_periods": sp, "verdict": v,
            "total_return_pct": round(total, 2)}


def main():
    nifty = fetch(NIFTY)
    rn = rets(nifty)

    # ── S1: agri-input EW basket, sowing window ──
    s1c = {t: fetch(t) for t in S1_BASKET}
    s1r = pd.DataFrame({t: rets(s) for t, s in s1c.items()})
    idx = s1r.index.intersection(rn.index)
    s1r = s1r.loc[idx]
    s1_port = s1r.mean(axis=1)  # equal-weight
    s1_im = pd.Series(False, index=s1_port.index)
    for y in YEARS:
        s1_im |= in_window(s1_port.index, y, "sowing")
    eq1, held1 = build_equity(s1_port, s1_im, n_legs=len(S1_BASKET))
    r1 = battery("S1  Kharif Sowing Agri-Input Basket (BASKET/CONFIRMATION)",
                 eq1, held1, n_trades=len(YEARS), num_trials=3)

    # ── S2: long tractor/2W EW minus NIFTY, forecast window ──
    s2c = {t: fetch(t) for t in S2_LONG}
    s2r = pd.DataFrame({t: rets(s) for t, s in s2c.items()})
    idx2 = s2r.index.intersection(rn.index)
    s2_long = s2r.loc[idx2].mean(axis=1)
    s2_port = s2_long - rn.loc[idx2]  # beta~1 neutral excess (long stocks / short index)
    s2_im = pd.Series(False, index=s2_port.index)
    for y in YEARS:
        s2_im |= in_window(s2_port.index, y, "forecast")
    eq2, held2 = build_equity(s2_port, s2_im, n_legs=3)  # 2 long legs + 1 index short
    r2 = battery("S2  Forecast Run-up Tractor/2W vs NIFTY (PAIR/PRE_POSITION)",
                 eq2, held2, n_trades=len(YEARS), num_trials=3)

    # ── S3: M&M directional, Apr15-Sep30, scaled 0.5 = bull-call-spread delta proxy ──
    s3 = fetch(S3_NAME)
    s3r = rets(s3) * 0.5  # ~spread delta; defined-risk proxy
    idx3 = s3r.index.intersection(rn.index)
    s3r = s3r.loc[idx3]
    s3_im = pd.Series(False, index=s3r.index)
    for y in YEARS:
        s3_im |= in_window(s3r.index, y, "s3")
    eq3, held3 = build_equity(s3r, s3_im, n_legs=2)  # debit spread = 2 option legs
    r3 = battery("S3  M&M Defined-Risk Monsoon Call spread PROXY (OPTION/HYBRID)",
                 eq3, held3, n_trades=len(YEARS), num_trials=3)

    print("\nNote S3 is an UNDERLYING-delta PROXY (yfinance has no historical option IV);"
          " true premium P&L is computed at the backtest stage with the option engine.")


if __name__ == "__main__":
    main()
