"""REFINED R2 — Defence + Auto "domestic-demand" long-only rotation basket.

Top-gainer grounding: the prior rotation basket (C) was FMCG-ONLY and largely
earned market-beta drift. The connectedness scan shows the two sectors that
GENUINELY load against the IT factor are AUTO (TVSMOTOR/EICHERMOT b_it~-0.22,
t<-5.7) and DEFENCE (BEL b_it=-0.20, t=-4.2). This version concentrates capital
there, with FMCG (MARICO genuine event-abnormal abn_CAAR +3.89%, COLPAL) as
ballast. Long-only delivery; IT is AVOID-annotated (no short). Spurious raw
top-15 names (ONGC/VEDL/COALINDIA/COCHINSHIP) are deliberately EXCLUDED despite
their raw appearance.

Weights: 40% Auto (TVSMOTOR/EICHERMOT/M&M) + 35% Defence (BEL/HAL) + 25% FMCG
(MARICO/COLPAL). Measured as EXCESS return over NIFTY (the long-only tilt).
Timing: scale in T-2..T+1, hold to T+20.

pivot/.venv/bin/python pivot/scripts/strategy_research/it_trouble_refined_bt_r2.py
"""
from __future__ import annotations

import numpy as np

from _it_bt_common import (
    BENCH, RT_COST, fetch, build_event_curve, benchmark_curve,
    run_battery, print_report,
)

# sub-basket -> weight ; within each sub-basket equal-weight
SUBS = {
    0.40: ["TVSMOTOR.NS", "EICHERMOT.NS", "M&M.NS"],   # Auto (genuine -IT loaders)
    0.35: ["BEL.NS", "HAL.NS"],                         # Defence (BEL genuine; HAL satellite)
    0.25: ["MARICO.NS", "COLPAL.NS"],                   # FMCG ballast
}
WIN_LO, WIN_HI = -2, 20


def _caar(per_ev):
    a = np.array(per_ev) * 100.0
    t = a.mean() / (a.std(ddof=1) / np.sqrt(len(a))) if len(a) > 1 and a.std(ddof=1) > 0 else 0.0
    return float(a.mean()), float(t)


def main():
    print("REFINED R2 — Defence+Auto domestic-demand long-only basket\n")
    names = [n for sub in SUBS.values() for n in sub]
    close = fetch(names + [BENCH])
    ret = close.pct_change()
    idx = ret.index
    bench = ret[BENCH]

    # Weighted daily basket return (re-normalise weights over available names).
    parts, wsum = [], 0.0
    used = []
    for w, sub in SUBS.items():
        have = [c for c in sub if c in ret.columns]
        if not have:
            print(f"  [degraded] sub-basket {sub} entirely unavailable")
            continue
        parts.append(w * ret[have].mean(axis=1))
        wsum += w
        used += [c.replace(".NS", "") for c in have]
    if not parts:
        print("  [degraded] no basket names — cannot backtest R2."); return
    basket = sum(parts) / wsum
    print(f"  basket (re-normalised weights): {used}")

    def leg(t0_pos, hi, _idx):
        b = basket.iloc[t0_pos + WIN_LO: hi + 1].fillna(0.0).values
        m = bench.iloc[t0_pos + WIN_LO: hi + 1].fillna(0.0).values
        return [float(rb) - float(rm) for rb, rm in zip(b, m)]   # excess over NIFTY

    eq, n_ev, per_ev = build_event_curve(leg, idx, WIN_LO, WIN_HI, cost_per_event=RT_COST)
    bench_eq = benchmark_curve(bench, idx, WIN_LO, WIN_HI)

    gross_eq, *_ = build_event_curve(leg, idx, WIN_LO, WIN_HI, cost_per_event=0.0)
    gross = gross_eq[-1] / gross_eq[0] - 1.0
    net = eq[-1] / eq[0] - 1.0
    cost_survival = max(0.0, min(1.0, net / gross)) if gross > 0 else 0.0

    caar_pct, caar_t = _caar(per_ev)
    bt = run_battery(eq, n_trades=n_ev, num_trials=3)
    print_report(
        "REFINED R2 — Defence+Auto domestic-demand long-only basket (Confirmation)",
        struct="""
        expression_kind = basket / long_only (delivery CNC equity); IT = 0% AVOID annotation (no short)
        legs: 40% Auto (TVSMOTOR/EICHERMOT/M&M) + 35% Defence (BEL/HAL) + 25% FMCG (MARICO/COLPAL)
        short handling: NONE — underperform leg expressed as honest 0% underweight
        concentrates capital in the two GENUINE -IT-factor sectors (Auto, Defence); spurious names excluded
        timing: scale in T-2..T+1, hold to T+20; rebalance to genuinely-connected names only
        invalidation = Nifty-IT prints a positive abnormal CAAR (thesis broken)
        """,
        bt=bt, bench_eq=bench_eq, per_event=per_ev, n_ev=n_ev,
        caar_pct=caar_pct, caar_t=caar_t,
        cost_survival=cost_survival, payoff_pop=None,
        hit_dir_positive=True,
        practical="""
        All names are liquid large/mid-cap delivery equities — no shortability/margin friction.
        Long-only CNC: capital = basket notional only; cheapest to run, no lot-size constraint.
        Weighted rebalance monthly is trivial; the most retail-friendly, register-not-execute-clean rotation.
        Strictly long-only => no SEBI retail-short issue at all.
        """,
        grade="(set in selection)",
        place_it="On a confirmed weak IT print, rotate into a 40/35/25 Auto/Defence/FMCG long-only CNC basket, scale in T-2..T+1, hold to T+20, rebalance monthly.",
    )


if __name__ == "__main__":
    main()
