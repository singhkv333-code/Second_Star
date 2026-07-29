"""Strategy C backtest — Defensive rotation basket, IT AVOID-annotated (Confirmation).

REAL long-only backtest: an equal-weighted defensive basket (NESTLEIND,
HINDUNILVR, ITC, DABUR) measured as EXCESS return over NIFTY (the long-only tilt;
IT names are AVOID/underweight annotations, NOT shorted). CONFIRMATION timing:
enter T+1 (one bar AFTER the weak print is confirmed — no look-ahead), hold to
T+20. Real equity round-trip cost per event.

pivot/.venv/bin/python pivot/scripts/strategy_research/it_trouble_backtest_c.py
"""
from __future__ import annotations

import numpy as np

from _it_bt_common import (
    BENCH, RT_COST, fetch, build_event_curve, benchmark_curve,
    run_battery, print_report,
)

BASKET = ["NESTLEIND.NS", "HINDUNILVR.NS", "ITC.NS", "DABUR.NS"]
WIN_LO, WIN_HI = 1, 20   # confirmation: enter T+1 (after the print), hold to +20


def main():
    print("STRATEGY C — Defensive rotation basket, IT AVOID-annotated (Confirmation)\n")
    close = fetch(BASKET + [BENCH])
    ret = close.pct_change()
    idx = ret.index
    have = [c for c in BASKET if c in ret.columns]
    if not have:
        print("  [degraded] basket names unavailable — cannot backtest C."); return
    bench = ret[BENCH]
    basket = ret[have].mean(axis=1)

    def leg(t0_pos, hi, _idx):
        b = basket.iloc[t0_pos + WIN_LO: hi + 1].fillna(0.0).values
        m = bench.iloc[t0_pos + WIN_LO: hi + 1].fillna(0.0).values
        return [float(rb) - float(rm) for rb, rm in zip(b, m)]  # excess over NIFTY

    eq, n_ev, per_ev = build_event_curve(leg, idx, WIN_LO, WIN_HI, cost_per_event=RT_COST)
    bench_eq = benchmark_curve(bench, idx, WIN_LO, WIN_HI)

    gross_eq, *_ = build_event_curve(leg, idx, WIN_LO, WIN_HI, cost_per_event=0.0)
    gross = gross_eq[-1] / gross_eq[0] - 1.0
    net = eq[-1] / eq[0] - 1.0
    cost_survival = max(0.0, min(1.0, net / gross)) if gross > 0 else 0.0

    bt = run_battery(eq, n_trades=n_ev, num_trials=3)
    print_report(
        "STRATEGY C — Defensive rotation basket, IT AVOID-annotated (Confirmation)",
        struct="""
        expression_kind = basket / risk-parity-conviction (long-only delivery equity)
        legs: LONG NESTLEIND + HINDUNILVR + ITC + DABUR; IT = 0% AVOID annotation (no short)
        short handling: NONE — underperform leg expressed as honest 0% underweight
        timing: CONFIRMATION (0% until weak IT guidance confirmed, then rotate in T+1)
        hold the defensive tilt 4-8wk, rebalance monthly; invalidation = IT guidance RAISED
        """,
        bt=bt, bench_eq=bench_eq, per_event=per_ev, n_ev=n_ev,
        caar_pct=0.60, caar_t=2.00,    # defensive basket positive reaction (NESTLE +0.75 t2.43 leads)
        cost_survival=cost_survival, payoff_pop=None,
        hit_dir_positive=True,
        practical="""
        All four are large-cap, fully liquid delivery names — no shortability/margin friction.
        Long-only, CNC delivery: capital = basket notional only; cheapest to run.
        No lot-size constraint (cash equity); rebalance monthly is trivial.
        The most retail-friendly, register-not-execute-clean expression of the rotation.
        """,
        grade="A-  (the cleanest placeable expression: long-only, lowest cost-drag, built on the two statistically-clean +reaction defensives; capped to UNPROVEN only by the 8-event sample)",
        place_it="On a confirmed weak IT-guidance print, rotate into an equal-weight NESTLE/HUL/ITC/DABUR basket (long-only CNC), hold 4-8 weeks, rebalance monthly.",
    )


if __name__ == "__main__":
    main()
