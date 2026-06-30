"""STRATEGY C backtest — DIRECT MCX crude defined-risk BULL-CALL-SPREAD (escalation).

HYBRID-staged, defined-risk DIRECT crude leg: long ATM CRUDEOIL call + short
higher-strike call on MCX CRUDEOIL (research-only lifted 2026-06-29). Defensive
complement: small GOLDBEES sleeve (supply-shock macro hedge, marginal +Brent-beta).

BACKTEST LIMITATION (stated, not fabricated):
  * There is NO aligned historical MCX CRUDEOIL daily OHLCV and NO historical
    option chain. So the REAL MCX option curve is BACKTEST-UNAVAILABLE.
  * What is simulated here is a clearly-labelled DEFINED-RISK UNDERLYING PROXY on
    Brent (BZ=F): per escalation episode a capped/floored long-delta (0.5) sleeve,
    episode P&L floored at -1.0 debit and capped at +1.5 (the spread geometry).
    This approximates the PAYOFF SHAPE, not real option prices/theta/IV.
  * The honest read leans on the event study + payoff math, not this proxy curve.

Signal = Brent 10d >= +8% read at close of day i; sleeve active i+1..i+20
(one-bar lag); MCX-OPT premium round-trip charged on the debit at entry.
    pivot/.venv/bin/python pivot/scripts/strategy_research/crude_geo_backtest_c.py
"""
from __future__ import annotations

import numpy as np

import _crude_bt_common as C

ANALOG_N = 10            # SPIKE analogs (event-study n_events)
# Bull-call-spread payoff geometry (defined risk).
DELTA, DEBIT, MAXPAY = 0.5, 1.0, 1.5
# Brent IS the driver by construction -> alignment is structural, not estimated.
# Defined-risk POP for a ~1:1.5 debit spread entered on a confirmed up-break ~0.45.
POP = 0.45


def main() -> None:
    px = C.fetch()
    rets = C.clean_returns(px)
    brent = px[C.DRIVER]
    eps, bidx = C.episodes(brent, "up")
    print(f"{'='*82}\nSTRATEGY C · DIRECT MCX crude bull-call-spread (crude-UP, HYBRID)")
    print(f"window {px.index.min().date()}..{px.index.max().date()} ({len(px)} rows)   "
          f"crude-UP episodes={len(eps)}  (sig {C.SIG_WIN}d / hold {C.HOLD}d)")
    print("** REAL MCX option curve BACKTEST-UNAVAILABLE (no aligned MCX OHLCV / chain). **")
    print("** Below is a clearly-labelled Brent-underlying DEFINED-RISK PAYOFF PROXY. **")

    eq, daily, mask, ep_rets = C.option_proxy_curve(
        brent, eps, bidx, delta=DELTA, prem_at_risk=DEBIT, max_payoff=MAXPAY)
    eq_g, _, _, _ = C.option_proxy_curve(
        brent, eps, bidx, delta=DELTA, prem_at_risk=DEBIT, max_payoff=MAXPAY,
        charge_costs=False)

    hit_rate = (sum(1 for r in ep_rets if r > 0) / len(ep_rets)) if ep_rets else None
    desc = C.descriptive(eq, daily, mask, ep_rets, len(bidx))
    bench = C.benchmark(px, bidx)

    td = daily[mask].values
    tt = C._battery_on(np.cumprod(1 + td).tolist(), td.tolist(), len(eps), 3)
    full = C._battery_on(eq.dropna().values.tolist(),
                         eq.pct_change().dropna().values.tolist(), len(eps), 3)

    print("\n-- DESCRIPTIVE (PROXY full NAV) --")
    print(f"  total={desc['total_return_pct']}%  CAGR={desc['cagr_pct']}%  trades={desc['n_trades']}  "
          f"win_rate={desc['win_rate']}  avg/med trade={desc['avg_trade_pct']}%/{desc['med_trade_pct']}%")
    print(f"  best/worst trade={desc['best_trade_pct']}%/{desc['worst_trade_pct']}%  "
          f"in-position days={desc['in_position_days']}/{desc['calendar_days']}")
    print(f"  NIFTY buy-hold (context): total={bench['total_return_pct']}%  CAGR={bench['cagr_pct']}%")

    print("\n-- TRUST BATTERY (on the PROXY curve) --")
    C.print_block("trade-time", tt)
    C.print_block("full-NAV  ", full)

    fs = tt["forward_stats"]
    rel_strength = 1.0       # Brent IS the driver by construction (structural)
    align = 1.0              # direct crude leg = perfect structural alignment
    sig_p = 0.001            # driver alignment is definitional, not estimated
    cs = C.cost_drag(eq_g.iloc[-1] / eq_g.iloc[0] * 100 - 100,
                     eq.iloc[-1] / eq.iloc[0] * 100 - 100)
    verdict = tt["verdict"]["verdict"]

    out, expr = C.two_dial(
        hit_rate=hit_rate, relationship_strength=rel_strength,
        sample_n=ANALOG_N, min_trl=fs["min_trl"], verdict=verdict,
        caar_alignment=align, significance_p=sig_p, cost_survival=cs,
        deflated_sharpe=fs["deflated_sharpe"], n_obs=fs["n_obs"], payoff_pop=POP)
    ind_o = C.indicative_dial("outcome", hit_rate=hit_rate,
                              relationship_strength=rel_strength, sample_n=ANALOG_N)
    ind_e = C.indicative_dial("expression", caar_alignment=align, significance_p=sig_p,
                              cost_survival=cs, deflated_sharpe=fs["deflated_sharpe"],
                              n_obs=fs["n_obs"], payoff_pop=POP)
    print("\n-- TWO-DIAL ALIGNMENT SCORE (official; suppressed below MinTRL) --")
    C.print_dial(out)
    C.print_dial(expr)
    print(f"    [indicative pre-suppression soft-blend] outcome={ind_o.letter} {ind_o.score} | "
          f"expression={ind_e.letter} {ind_e.score}  "
          f"(inputs: hit={hit_rate:.0%} rel={rel_strength:.2f} align={align:.2f} "
          f"sig_p={sig_p:.3f} cost_survival={cs:.2f} POP={POP})")
    print(f"\n[note] PROXY only — real fills need the live MCX CRUDEOIL chain (Kite not "
          f"connected). Defined-risk long-crude = many small premium decays + rare big "
          f"spikes (negative skew); convex TAIL insurance, small-sized. Verdict on proxy curve.")


if __name__ == "__main__":
    main()
