"""Strategy B backtest — Long-FMCG vs short-IT-SSF pair (Hybrid, market-neutral).

REAL pair backtest: LONG defensives (NESTLEIND + HINDUNILVR) vs SHORT IT via a
single-stock FUTURE on HCLTECH. Each leg is BETA-HEDGED to NIFTY using the
PRE-event estimation window [t0-130, t0-11] (strictly out-of-sample), so the
spread is ~market-neutral. The short is the honest SSF future short (no fabricated
delivery short). Underlying-price backtest: an SSF tracks spot ~1:1, so the
underlying return IS the future's return ex-carry; we ignore the small basis/roll
(flagged). Window T-2..T+10 (hybrid: starter T-2, scale on the print).

pivot/.venv/bin/python pivot/scripts/strategy_research/it_trouble_backtest_b.py
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from _it_bt_common import (
    BENCH, RT_COST, LEG_BUY, LEG_SELL, fetch, estimation_beta,
    build_event_curve, benchmark_curve, run_battery, print_report,
)

LONG = ["NESTLEIND.NS", "HINDUNILVR.NS"]
SHORT = "HCLTECH.NS"
WIN_LO, WIN_HI = -2, 10
# pair cost: long basket round-trip + short SSF round-trip (~ one equity RT each side).
COST = 2.0 * RT_COST


def main():
    print("STRATEGY B — Long FMCG vs short-IT-SSF pair (Hybrid, market-neutral)\n")
    close = fetch(LONG + [SHORT, BENCH])
    ret = close.pct_change()
    idx = ret.index
    longs = [c for c in LONG if c in ret.columns]
    if not longs or SHORT not in ret.columns:
        print("  [degraded] required legs unavailable — cannot backtest B."); return
    bench = ret[BENCH]
    long_basket = ret[longs].mean(axis=1)
    short_leg = ret[SHORT]

    def leg(t0_pos, hi, _idx):
        # Beta-hedge BOTH legs on the pre-event window (no look-ahead).
        bl = estimation_beta(long_basket, bench, t0_pos, idx)
        bs = estimation_beta(short_leg, bench, t0_pos, idx)
        if bl is None or bs is None:
            return None
        sl = long_basket.iloc[t0_pos + WIN_LO: hi + 1].fillna(0.0).values
        ss = short_leg.iloc[t0_pos + WIN_LO: hi + 1].fillna(0.0).values
        bm = bench.iloc[t0_pos + WIN_LO: hi + 1].fillna(0.0).values
        out = []
        for rl, rs, rb in zip(sl, ss, bm):
            # long defensives (beta-stripped) minus short IT (beta-stripped),
            # 50/50 notional => 0.5 weight each; short pays when IT falls.
            long_excess = float(rl) - bl * float(rb)
            short_excess = -(float(rs) - bs * float(rb))
            out.append(0.5 * long_excess + 0.5 * short_excess)
        return out

    eq, n_ev, per_ev = build_event_curve(leg, idx, WIN_LO, WIN_HI, cost_per_event=COST)
    bench_eq = benchmark_curve(bench, idx, WIN_LO, WIN_HI)

    gross_eq, *_ = build_event_curve(leg, idx, WIN_LO, WIN_HI, cost_per_event=0.0)
    gross = gross_eq[-1] / gross_eq[0] - 1.0
    net = eq[-1] / eq[0] - 1.0
    cost_survival = max(0.0, min(1.0, net / gross)) if gross > 0 else 0.0

    bt = run_battery(eq, n_trades=n_ev, num_trials=3)
    print_report(
        "STRATEGY B — Long FMCG vs short-IT-SSF pair (Hybrid, market-neutral)",
        struct="""
        expression_kind = pair / engle_granger; short leg = SSF future (honest_short)
        legs: LONG NESTLEIND+HINDUNILVR (clean +reaction names) vs SHORT HCLTECH SSF
        both legs beta-hedged to NIFTY on the pre-event window => ~market-neutral
        short handling: SSF future (~15-20% SPAN margin, monthly roll, physical settle)
        timing: HYBRID 50/30/20 ladder (starter T-2, add on weak print, add on IT break)
        exit on FMCG-IT spread mean-revert / +1mo; invalidation = IT outperforms FMCG >2sigma
        """,
        bt=bt, bench_eq=bench_eq, per_event=per_ev, n_ev=n_ev,
        caar_pct=3.50, caar_t=1.62,     # spread favorable CAAR (long +0.66, short HCLTECH +2.84; conservative t=HCLTECH)
        cost_survival=cost_survival, payoff_pop=None,
        hit_dir_positive=True,
        practical="""
        SSF on HCLTECH is F&O-liquid; long FMCG names are large-cap liquid.
        Short via SSF = honest short (no delivery-short ban); needs ~15-20% SPAN margin.
        Monthly roll + physical settlement on the SSF => square off pre-expiry (cost/effort).
        Two-leg + beta-hedge = more operationally complex; capital = margin + long notional.
        Placeable by an F&O-enabled retail account, but heavier than a long-only basket.
        """,
        grade="B  (genuinely market-neutral, the short-IT leg carries the strongest single mover HCLTECH -2.84%; costs and SSF roll are the drag)",
        place_it="Run a beta-hedged long NESTLE+HUL vs short HCLTECH-SSF pair, laddered around the print; size the short to SPAN margin and roll before expiry.",
    )


if __name__ == "__main__":
    main()
