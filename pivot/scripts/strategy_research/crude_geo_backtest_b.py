"""STRATEGY B backtest — Upstream-vs-OMC RELATIVE PAIR (crude-UP / escalation).

PRE-POSITION-staged, beta-neutral market-neutral pair:
    LONG ONGC (Brent-beta +0.074, t=4.64)  vs  SHORT BPCL (-0.069, t=-4.43)
the widest GENUINE crude-beta spread (market-beta stripped). Short leg via SSF
single-stock future (honest_short: BPCL is F&O-eligible -> clean defined short,
NOT a fabricated delivery short). Hedge ratio beta = ONGC/BPCL market-betas
(0.915/1.072 = 0.854) to strip the index.

Honest mechanics: signal = Brent 10d >= +8% read at close of day i; pair held
day i+1..i+20 (one-bar lag); spread = ONGC - beta*BPCL; TWO round-trips charged
(both legs). Full real Trust Battery + NIFTY buy-hold + two-dial alignment.
    pivot/.venv/bin/python pivot/scripts/strategy_research/crude_geo_backtest_b.py
"""
from __future__ import annotations

import numpy as np

import _crude_bt_common as C

LONG, SHORT = "ONGC.NS", "BPCL.NS"
BETA = 0.915 / 1.072                 # market-beta neutral hedge ratio
ANALOG_N = 10                        # SPIKE analogs (event-study n_events)
# Genuine connectedness |t| of the two legs (Brent-beta t).
LEG_T = {"ONGC.NS": 4.64, "BPCL.NS": 4.43}
# Spike-sample CAAR% (thesis = ONGC UP, BPCL DOWN on crude-up -> spread +ve).
SPIKE_CAAR = {"ONGC.NS": 2.95, "BPCL.NS": -2.11}


def main() -> None:
    px = C.fetch()
    rets = C.clean_returns(px)
    brent = px[C.DRIVER]
    eps, bidx = C.episodes(brent, "up")
    print(f"{'='*82}\nSTRATEGY B · Upstream-vs-OMC PAIR (crude-UP, PRE-POSITION)")
    print(f"window {px.index.min().date()}..{px.index.max().date()} ({len(px)} rows)   "
          f"crude-UP episodes={len(eps)}  beta={BETA:.3f}  (sig {C.SIG_WIN}d / hold {C.HOLD}d)")

    eq, daily, mask, ep_rets = C.pair_curve(rets, LONG, SHORT, BETA, eps, bidx)
    eq_g, _, _, _ = C.pair_curve(rets, LONG, SHORT, BETA, eps, bidx, charge_costs=False)

    hit_rate = (sum(1 for r in ep_rets if r > 0) / len(ep_rets)) if ep_rets else None
    desc = C.descriptive(eq, daily, mask, ep_rets, len(bidx))
    bench = C.benchmark(px, bidx)

    td = daily[mask].values
    tt = C._battery_on(np.cumprod(1 + td).tolist(), td.tolist(), len(eps), 3)
    full = C._battery_on(eq.dropna().values.tolist(),
                         eq.pct_change().dropna().values.tolist(), len(eps), 3)

    print("\n-- DESCRIPTIVE (full calendar NAV, market-neutral spread) --")
    print(f"  total={desc['total_return_pct']}%  CAGR={desc['cagr_pct']}%  trades={desc['n_trades']}  "
          f"win_rate={desc['win_rate']}  avg/med trade={desc['avg_trade_pct']}%/{desc['med_trade_pct']}%")
    print(f"  best/worst trade={desc['best_trade_pct']}%/{desc['worst_trade_pct']}%  "
          f"in-position days={desc['in_position_days']}/{desc['calendar_days']}")
    print(f"  NIFTY buy-hold (context only; pair is market-neutral): total={bench['total_return_pct']}%  "
          f"CAGR={bench['cagr_pct']}%  maxDD={bench['max_drawdown_pct']}%")

    print("\n-- TRUST BATTERY --")
    C.print_block("trade-time", tt)
    C.print_block("full-NAV  ", full)

    fs = tt["forward_stats"]
    rel_strength = min(1.0, float(np.mean(list(LEG_T.values()))) / 6.0)
    # Alignment: long CAAR up + short CAAR down -> favourable spread, /5% sat.
    spread_caar = SPIKE_CAAR["ONGC.NS"] - SPIKE_CAAR["BPCL.NS"]   # +5.06%
    align = float(np.clip(spread_caar / 5.0, 0, 1))
    sig_p = C.two_sided_p(0.82)          # ONGC spike CAAR t (weak in spike sample)
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
    print(f"\n[note] SHORT BPCL is via SSF future (honest_short: F&O-eligible -> clean defined "
          f"short, NOT delivery). SPIKE-sample CAAR t is WEAK (0.82) -> escalation edge is "
          f"regime-clustered (2022 Ukraine). Verdict on trade-time curve.")


if __name__ == "__main__":
    main()
