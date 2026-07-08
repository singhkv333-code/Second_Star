"""REFINED R1 — Long-Auto vs Short-Nifty-IT market-neutral pair.

Top-gainer grounding (it_trouble_topgainers_scan): the Auto complex
(TVSMOTOR/EICHERMOT/M&M) is the single most GENUINELY-connected IT-trouble
beneficiary in the universe — TVSMOTOR b_it=-0.22 (t -5.7), EICHERMOT b_it=-0.22
(t -6.3), Nifty Auto b_it=-0.13 (t -6.9) — they structurally rise when the
IT-specific factor falls, with positive abnormal event CAARs. This REPLACES the
prior B long leg (FMCG, b_it~-0.05, weak).

Structure: LONG equal-weight TVSMOTOR+EICHERMOT+M&M  vs  SHORT Nifty-IT.
The short leg is expressed HONESTLY/defined-risk: a Nifty-IT FUTURES short (index
future tracks ^CNXIT ~1:1 ex-carry) or, per India microstructure, an IT bear put
spread — NO retail single-stock delivery short. We backtest the index-return
short as the futures proxy (ignores small basis/roll, flagged). Both legs are
BETA-HEDGED to NIFTY on the strictly-OOS pre-event window [t0-130,t0-11] => the
spread is ~market-neutral. Window: arm T-2, hold to T+20 (PEAD drift).

pivot/.venv/bin/python pivot/scripts/strategy_research/it_trouble_refined_bt_r1.py
"""
from __future__ import annotations

import numpy as np

from _it_bt_common import (
    BENCH, RT_COST, fetch, estimation_beta,
    build_event_curve, benchmark_curve, run_battery, print_report,
)

LONG = ["TVSMOTOR.NS", "EICHERMOT.NS", "M&M.NS"]
SHORT = "^CNXIT"               # Nifty-IT (futures-short proxy / bear-put-spread underlying)
WIN_LO, WIN_HI = -2, 20        # arm T-2, hold to +20 (drift window)
COST = 2.0 * RT_COST           # long basket RT + short future RT


def _caar(per_ev):
    a = np.array(per_ev) * 100.0
    t = a.mean() / (a.std(ddof=1) / np.sqrt(len(a))) if len(a) > 1 and a.std(ddof=1) > 0 else 0.0
    return float(a.mean()), float(t)


def main():
    print("REFINED R1 — Long-Auto vs Short-Nifty-IT market-neutral pair\n")
    close = fetch(LONG + [SHORT, BENCH])
    ret = close.pct_change()
    idx = ret.index
    longs = [c for c in LONG if c in ret.columns]
    if not longs or SHORT not in ret.columns:
        print("  [degraded] required legs unavailable — cannot backtest R1."); return
    bench = ret[BENCH]
    long_basket = ret[longs].mean(axis=1)
    short_leg = ret[SHORT]
    print(f"  long leg = {[c.replace('.NS','') for c in longs]} ; short leg = Nifty-IT ({SHORT})")

    def leg(t0_pos, hi, _idx):
        bl = estimation_beta(long_basket, bench, t0_pos, idx)
        bs = estimation_beta(short_leg, bench, t0_pos, idx)
        if bl is None or bs is None:
            return None
        sl = long_basket.iloc[t0_pos + WIN_LO: hi + 1].fillna(0.0).values
        ss = short_leg.iloc[t0_pos + WIN_LO: hi + 1].fillna(0.0).values
        bm = bench.iloc[t0_pos + WIN_LO: hi + 1].fillna(0.0).values
        out = []
        for rl, rs, rb in zip(sl, ss, bm):
            long_excess = float(rl) - bl * float(rb)        # beta-stripped long Auto
            short_excess = -(float(rs) - bs * float(rb))    # beta-stripped short IT
            out.append(0.5 * long_excess + 0.5 * short_excess)
        return out

    eq, n_ev, per_ev = build_event_curve(leg, idx, WIN_LO, WIN_HI, cost_per_event=COST)
    bench_eq = benchmark_curve(bench, idx, WIN_LO, WIN_HI)

    gross_eq, *_ = build_event_curve(leg, idx, WIN_LO, WIN_HI, cost_per_event=0.0)
    gross = gross_eq[-1] / gross_eq[0] - 1.0
    net = eq[-1] / eq[0] - 1.0
    cost_survival = max(0.0, min(1.0, net / gross)) if gross > 0 else 0.0

    caar_pct, caar_t = _caar(per_ev)
    bt = run_battery(eq, n_trades=n_ev, num_trials=3)
    print_report(
        "REFINED R1 — Long-Auto vs Short-Nifty-IT pair (market-neutral)",
        struct="""
        expression_kind = pair / market_neutral ; short = Nifty-IT future (honest_short) or IT bear put spread
        legs: LONG TVSMOTOR+EICHERMOT+M&M (genuine -IT-factor loaders) vs SHORT Nifty-IT
        both legs beta-hedged to NIFTY on the pre-event window => ~market-neutral
        short handling: index future (SPAN margin, monthly roll) OR defined-risk IT bear put spread; NO delivery short
        timing: arm T-2, enter T-1 open, hold to T+20 (PEAD drift)
        exit T+20 or +6% pair profit; invalidation = IT guidance RAISED or IT gaps up >3%
        """,
        bt=bt, bench_eq=bench_eq, per_event=per_ev, n_ev=n_ev,
        caar_pct=caar_pct, caar_t=caar_t,
        cost_survival=cost_survival, payoff_pop=None,
        hit_dir_positive=True,
        option_proxy_note=("short leg backtested as the Nifty-IT INDEX return "
                           "(futures-short proxy, ~1:1 ex-carry); small basis/roll "
                           "and SPAN-margin carry NOT modelled. If expressed as an IT "
                           "bear put spread instead, theta/vega/STT-on-intrinsic apply."),
        practical="""
        Auto names (TVSMOTOR/EICHERMOT/M&M) are F&O-liquid large caps; Nifty-IT future is deep/liquid.
        Short via Nifty-IT future = honest short (no delivery-short ban); ~12-15% SPAN margin + monthly roll.
        Beta-hedged 2-leg => operationally heavier; capital = long notional + short margin.
        Register-not-execute; defined-risk variant (IT bear put spread) keeps it fully SEBI-clean.
        Placeable by an F&O-enabled retail account.
        """,
        grade="(set in selection)",
        place_it="Beta-hedged LONG TVSMOTOR/EICHERMOT/M&M vs SHORT Nifty-IT (future or IT bear put spread), armed T-2, held to T+20; exit on +6% pair or guidance-up invalidation.",
    )


if __name__ == "__main__":
    main()
