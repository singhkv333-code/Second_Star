"""REFINED R3 — Nifty-IT bear put spread + GoldBeES risk-off hedge.

Top-gainer grounding: Nifty-IT / ITBeES sit at the BOTTOM of every event window
(mean window ret -1.13% / -0.93%), and GoldBeES is the top non-stock gainer
(top-6 hit-frequency 0.62, mean window +2.99%) with ZERO IT-factor and FX
loading (b_it=+0.03 t=0.08, b_fx=+0.06 t=0.06) — i.e. its gain is a broad
RISK-OFF hedge, genuinely connected to the macro that *causes* the IT weakness,
independent of the IT bet itself. Pairing the (weak, single-name) prior INFY
bear put spread with this empirically-validated hedge converts a thin directional
bet into a defined-risk bearish-plus-hedge structure.

Legs: (a) Nifty-IT bear put spread — delta proxy (net short-delta -0.40 on the
^CNXIT underlying with a -1.8% net-debit defined-risk floor; same honest option
caveat as prior A: NO historical option premium/IV, theta/vega/STT NOT modelled,
so the proxy is optimistic). (b) GoldBeES long sized 30% of the option notional.
Window: arm T-3, exit into the T+1..+5 reaction.

pivot/.venv/bin/python pivot/scripts/strategy_research/it_trouble_refined_bt_r3.py
"""
from __future__ import annotations

import numpy as np

from _it_bt_common import (
    BENCH, RT_COST, fetch, build_event_curve, benchmark_curve,
    run_battery, print_report,
)

IT_IDX = "^CNXIT"
GOLD = "GOLDBEES.NS"
DELTA = -0.40
NET_DEBIT_FLOOR = -0.018          # defined-risk max loss (fraction of underlying notional)
GOLD_W = 0.30                     # gold sized 30% of option notional
WIN_LO, WIN_HI = -3, 5


def _caar(per_ev):
    a = np.array(per_ev) * 100.0
    t = a.mean() / (a.std(ddof=1) / np.sqrt(len(a))) if len(a) > 1 and a.std(ddof=1) > 0 else 0.0
    return float(a.mean()), float(t)


def main():
    print("REFINED R3 — Nifty-IT bear put spread + GoldBeES hedge\n")
    close = fetch([IT_IDX, GOLD, BENCH])
    ret = close.pct_change()
    idx = ret.index
    if IT_IDX not in ret.columns:
        print("  [degraded] Nifty-IT unavailable — cannot backtest R3."); return
    have_gold = GOLD in ret.columns
    if not have_gold:
        print("  [degraded] GoldBeES unavailable — running spread leg ONLY (no hedge)")
    it = ret[IT_IDX]
    gold = ret[GOLD] if have_gold else None
    bench = ret[BENCH]

    def leg(t0_pos, hi, _idx):
        its = it.iloc[t0_pos + WIN_LO: hi + 1].fillna(0.0).values
        gs = (gold.iloc[t0_pos + WIN_LO: hi + 1].fillna(0.0).values
              if have_gold else np.zeros(len(its)))
        out, cum = [], 0.0
        for r_it, r_g in zip(its, gs):
            spread_day = DELTA * float(r_it)             # short-delta on Nifty-IT price
            if cum + spread_day < NET_DEBIT_FLOOR:       # defined-risk floor binds
                spread_day = NET_DEBIT_FLOOR - cum
            cum += spread_day
            out.append(spread_day + GOLD_W * float(r_g))  # + gold hedge carry
        return out

    eq, n_ev, per_ev = build_event_curve(leg, idx, WIN_LO, WIN_HI, cost_per_event=RT_COST)
    bench_eq = benchmark_curve(bench, idx, WIN_LO, WIN_HI)

    gross_eq, *_ = build_event_curve(leg, idx, WIN_LO, WIN_HI, cost_per_event=0.0)
    gross = gross_eq[-1] / gross_eq[0] - 1.0
    net = eq[-1] / eq[0] - 1.0
    cost_survival = max(0.0, min(1.0, net / gross)) if gross > 0 else 0.0

    caar_pct, caar_t = _caar(per_ev)
    bt = run_battery(eq, n_trades=n_ev, num_trials=3)
    print_report(
        "REFINED R3 — Nifty-IT bear put spread + GoldBeES risk-off hedge (defined-risk)",
        struct="""
        expression_kind = option_strategy + etf_hedge (defined-risk, pre-position)
        leg a: Nifty-IT bear put spread — BUY ~ATM put, SELL ~6% OTM put, monthly NFO; debit = max loss
        leg b: GoldBeES long sized 30% of option notional (independent risk-off carry, zero IT/FX loading)
        short handling: defined-risk (sold put covered) — no naked short, no SSF
        timing: arm T-3, exit spread into T+1..+5 reaction or at max-profit; hold Gold as standalone trail
        invalidation = guidance pre-flagged up or IT gaps up >3%
        """,
        bt=bt, bench_eq=bench_eq, per_event=per_ev, n_ev=n_ev,
        caar_pct=caar_pct, caar_t=caar_t,
        cost_survival=cost_survival, payoff_pop=0.45,
        hit_dir_positive=True,
        option_proxy_note=("spread = delta-proxy on the Nifty-IT underlying with a "
                           "-1.8% defined-risk floor; NO historical option premium/IV "
                           "— theta/vega/STT-on-intrinsic NOT modelled (proxy optimistic). "
                           "Gold leg is a real GoldBeES ETF return."),
        practical="""
        Nifty-IT options are deep/liquid; defined-risk debit spread => capital = net debit only, no margin/short ban.
        GoldBeES is a liquid NSE ETF (plain CNC delivery) — total capital = net debit + ETF notional.
        Lot size respected; physical settlement + STT-on-intrinsic => square spread before expiry (flagged).
        Register-not-execute; the hedge diversifies the single bearish bet without doubling its beta.
        """,
        grade="(set in selection)",
        place_it="Arm a Nifty-IT monthly bear put spread T-3 before the print + a GoldBeES leg ~30% of the debit; cap spread risk at the net debit, hold Gold as a risk-off trail.",
    )


if __name__ == "__main__":
    main()
