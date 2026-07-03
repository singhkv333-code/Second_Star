"""Strategy A backtest — INFY bear put spread (Pre-position, defined-risk option).

HONEST OPTION LIMITATION: yfinance has no historical NFO option premium / IV
history, so we CANNOT reconstruct a true bear-put-spread premium curve. We
backtest the DEFINED-RISK DELTA PROXY: a net short-delta (~-0.40) position on
the INFY underlying (a debit put spread is outright-bearish on INFY's PRICE, not
its abnormal return), with a per-event loss FLOOR equal to the net debit
(~1.8% of underlying notional) — exactly the defined-risk shape. The proxy
IGNORES theta decay, vega, and STT-on-intrinsic, all of which would REDUCE the
real spread's return — so the proxy is, if anything, optimistic. Treat it as the
directional skeleton of the trade, not a premium-accurate option backtest.

pivot/.venv/bin/python pivot/scripts/strategy_research/it_trouble_backtest_a.py
"""
from __future__ import annotations

import numpy as np

from _it_bt_common import (
    BENCH, RT_COST, fetch, build_event_curve, benchmark_curve,
    run_battery, print_report,
)

DELTA = -0.40            # net short delta of an ATM / ~6% OTM debit put spread
NET_DEBIT_FLOOR = -0.018  # defined-risk max loss as fraction of underlying notional
WIN_LO, WIN_HI = -3, 5   # pre-position: arm T-3, exit into the +5 reaction


def main():
    print("STRATEGY A — INFY bear put spread (Pre-position, defined-risk)\n")
    close = fetch(["INFY.NS", BENCH])
    ret = close.pct_change()
    idx = ret.index
    if "INFY.NS" not in ret.columns:
        print("  [degraded] INFY.NS unavailable — cannot backtest A."); return
    infy = ret["INFY.NS"]
    bench = ret[BENCH]

    def leg(t0_pos, hi, _idx):
        seg = infy.iloc[t0_pos + WIN_LO: hi + 1].fillna(0.0).values
        out, cum = [], 0.0
        for r in seg:
            day = DELTA * float(r)            # short-delta on INFY price move
            if cum + day < NET_DEBIT_FLOOR:   # defined-risk floor binds
                day = NET_DEBIT_FLOOR - cum
            cum += day
            out.append(day)
        return out

    eq, n_ev, per_ev = build_event_curve(leg, idx, WIN_LO, WIN_HI, cost_per_event=RT_COST)
    bench_eq = benchmark_curve(bench, idx, WIN_LO, WIN_HI)

    # cost-survival = net / gross total return.
    gross_eq, *_ = build_event_curve(leg, idx, WIN_LO, WIN_HI, cost_per_event=0.0)
    gross = gross_eq[-1] / gross_eq[0] - 1.0
    net = eq[-1] / eq[0] - 1.0
    cost_survival = max(0.0, min(1.0, net / gross)) if gross > 0 else 0.0

    bt = run_battery(eq, n_trades=n_ev, num_trials=3)
    print_report(
        "STRATEGY A — INFY bear put spread (Pre-position, defined-risk option)",
        struct="""
        expression_kind = option_strategy / bear_put_spread (INFY monthly NFO)
        legs: BUY ~ATM put, SELL ~6% OTM put, same monthly expiry; debit = max loss
        short handling: defined-risk (sold put covered) — no naked short, no SSF
        timing: PRE-POSITION (arm T-3 before INFY result; one-time run_at)
        entry T-3 / exit into +1..+2 reaction / invalidation = guidance RAISED or gap-up >3%
        """,
        bt=bt, bench_eq=bench_eq, per_event=per_ev, n_ev=n_ev,
        caar_pct=-0.82, caar_t=-0.46,          # INFY react CAAR (event study)
        cost_survival=cost_survival, payoff_pop=0.45,  # debit spread POP ~ 45%
        hit_dir_positive=True,
        option_proxy_note=("delta-proxy on the INFY underlying with a -1.8% "
                           "defined-risk floor; NO historical option premium/IV — "
                           "theta/vega/STT-on-intrinsic NOT modelled (proxy is optimistic)."),
        practical="""
        INFY monthly options are among the most liquid single-stock chains (tight spreads).
        Defined-risk debit spread: capital = net debit only; NO margin, NO short ban issue.
        Lot size respected (1 lot INFY); register-not-execute, user places & squares pre-expiry.
        Physical settlement + STT-on-intrinsic => MUST square off before expiry (flagged).
        Capital-light and genuinely placeable by retail.
        """,
        grade="C  (placeable, defined-risk, but the INFY-specific edge is the weakest of the three: react CAAR only -0.82%, t=-0.46)",
        place_it="Arm a 1-lot INFY monthly bear put spread T-3 before the print; cap risk at the net debit; skip if guidance is pre-flagged up.",
    )


if __name__ == "__main__":
    main()
