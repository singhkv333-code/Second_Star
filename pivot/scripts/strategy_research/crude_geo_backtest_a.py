"""STRATEGY A backtest — Importer-beneficiary BASKET (crude-DOWN / de-escalation).

CONFIRMATION-staged, long-only NSE delivery basket of the names that are BOTH
crude-connected (significant -ve Brent-beta) AND move on a crude crash:
    ASIANPAINT 22 · BERGEPAINT 18 · INDIGO 18 · HINDPETRO 16 · BPCL 14 · IOC 12
Tyres are EXCLUDED (t~0 daily Brent-beta — headline CAAR is not crude-driven).

Honest mechanics: signal = Brent 10d <= -8% read at close of day i; basket held
day i+1..i+20 (one-bar lag, next-bar fill); real equity round-trip cost charged
per episode; cash between signals. Full real Trust Battery + NIFTY buy-hold + the
two-dial alignment score. Run with the repo venv:
    pivot/.venv/bin/python pivot/scripts/strategy_research/crude_geo_backtest_a.py
"""
from __future__ import annotations

import json
import numpy as np

import _crude_bt_common as C

WEIGHTS = {"ASIANPAINT.NS": 0.22, "BERGEPAINT.NS": 0.18, "INDIGO.NS": 0.18,
           "HINDPETRO.NS": 0.16, "BPCL.NS": 0.14, "IOC.NS": 0.12}
# Genuine-connectedness |t| of the constituents (from crude_event_study.json).
BRENT_T = {"ASIANPAINT.NS": 5.34, "BERGEPAINT.NS": 5.15, "INDIGO.NS": 4.97,
           "HINDPETRO.NS": 5.72, "BPCL.NS": 4.43, "IOC.NS": 2.60}
# Crash-sample CAAR% (thesis = importers OUTPERFORM on crude-down).
CRASH_CAAR = {"ASIANPAINT.NS": 4.85, "BERGEPAINT.NS": 5.17, "INDIGO.NS": -1.13,
              "HINDPETRO.NS": 2.22, "BPCL.NS": 0.27, "IOC.NS": -1.61}
# INDEPENDENT analog count for OUTCOME sample-sufficiency = de-duplicated crash
# shocks from the event study (NOT the 67 overlapping +-8% trigger episodes,
# which cluster in 2014-16/2020/2022 and are not independent draws).
ANALOG_N = 7  # crash analogs (crude_event_study.json CAAR n_events)


def main() -> None:
    px = C.fetch()
    rets = C.clean_returns(px)
    brent = px[C.DRIVER]
    eps, bidx = C.episodes(brent, "down")
    print(f"{'='*82}\nSTRATEGY A · Importer-beneficiary BASKET (crude-DOWN, CONFIRMATION)")
    print(f"window {px.index.min().date()}..{px.index.max().date()} "
          f"({len(px)} rows)   crude-DOWN episodes={len(eps)}  (sig {C.SIG_WIN}d / hold {C.HOLD}d)")

    # Net (with costs) and gross (no costs) curves.
    eq, daily, mask, ep_rets = C.basket_curve(rets, WEIGHTS, eps, bidx)
    eq_g, _, _, ep_g = C.basket_curve(rets, WEIGHTS, eps, bidx, charge_costs=False)

    # Per-episode hit-rate vs NIFTY (thesis = basket BEATS market on crude-down).
    nifty = rets[C.BENCH].reindex(bidx)
    hits = 0
    for (e, x) in eps:
        bret = float((1 + nifty.iloc[e + 1:x + 1].fillna(0)).prod() - 1)
        sret = ep_rets[eps.index((e, x))]
        if sret > bret:
            hits += 1
    hit_rate = hits / len(eps) if eps else None

    desc = C.descriptive(eq, daily, mask, ep_rets, len(bidx))
    bench = C.benchmark(px, bidx)

    # Battery — trade-time (in-position days, the fair read) + full-NAV.
    td = daily[mask].values
    tt = C._battery_on(np.cumprod(1 + td).tolist(), td.tolist(), len(eps), 3)
    full = C._battery_on(eq.dropna().values.tolist(),
                         eq.pct_change().dropna().values.tolist(), len(eps), 3)

    print("\n-- DESCRIPTIVE (full calendar NAV) --")
    print(f"  total={desc['total_return_pct']}%  CAGR={desc['cagr_pct']}%  "
          f"trades={desc['n_trades']}  win_rate={desc['win_rate']}  "
          f"avg/med trade={desc['avg_trade_pct']}%/{desc['med_trade_pct']}%")
    print(f"  best/worst trade={desc['best_trade_pct']}%/{desc['worst_trade_pct']}%  "
          f"in-position days={desc['in_position_days']}/{desc['calendar_days']}  "
          f"hit-rate vs NIFTY={hit_rate:.0%}")
    print(f"  NIFTY buy-hold (same window): total={bench['total_return_pct']}%  "
          f"CAGR={bench['cagr_pct']}%  maxDD={bench['max_drawdown_pct']}%")

    print("\n-- TRUST BATTERY --")
    C.print_block("trade-time", tt)
    C.print_block("full-NAV  ", full)

    # ── Two-dial alignment (REAL confidence module) ──
    fs = tt["forward_stats"]
    mean_t = float(np.mean(list(BRENT_T.values())))
    rel_strength = min(1.0, mean_t / 6.0)             # |t| ~6 saturates
    # Alignment: weight-avg favourable CAAR (importer outperformance), /2.5% sat.
    align = float(np.clip(sum(WEIGHTS[s] * CRASH_CAAR[s] for s in WEIGHTS)
                          / sum(WEIGHTS.values()) / 2.5, 0, 1))
    # Significance from the most significant connected leg's crash CAAR t.
    sig_p = C.two_sided_p(2.51)                        # ASIANPAINT crash t
    cs = C.cost_drag(eq_g.iloc[-1] / eq_g.iloc[0] * 100 - 100,
                     eq.iloc[-1] / eq.iloc[0] * 100 - 100)
    verdict = tt["verdict"]["verdict"]

    out, expr = C.two_dial(
        hit_rate=hit_rate, relationship_strength=rel_strength,
        sample_n=ANALOG_N, min_trl=fs["min_trl"], verdict=verdict,
        caar_alignment=align, significance_p=sig_p, cost_survival=cs,
        deflated_sharpe=fs["deflated_sharpe"], n_obs=fs["n_obs"])
    ind_o = C.indicative_dial("outcome", hit_rate=hit_rate,
                              relationship_strength=rel_strength, sample_n=ANALOG_N)
    ind_e = C.indicative_dial("expression", caar_alignment=align, significance_p=sig_p,
                              cost_survival=cs, deflated_sharpe=fs["deflated_sharpe"],
                              n_obs=fs["n_obs"])
    print("\n-- TWO-DIAL ALIGNMENT SCORE (official; suppressed below MinTRL) --")
    C.print_dial(out)
    C.print_dial(expr)
    print(f"    [indicative pre-suppression soft-blend] outcome={ind_o.letter} {ind_o.score} | "
          f"expression={ind_e.letter} {ind_e.score}  "
          f"(inputs: hit={hit_rate:.0%} rel={rel_strength:.2f} align={align:.2f} "
          f"sig_p={sig_p:.3f} cost_survival={cs:.2f})")

    print(f"\n[note] cost-survival uses gross vs net total return; option/MCX N/A here. "
          f"Verdict on trade-time curve is the signal's edge read.")


if __name__ == "__main__":
    main()
