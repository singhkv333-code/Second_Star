"""S1 — Kharif Sowing Agri-Input Basket · BASKET · CONFIRMATION.

Long-only EW basket [COROMANDEL, CHAMBLFERT, RALLIS, UPL, PIIND], deployed ONLY
in the sowing window [Jun01..Aug31] and ONLY in years where IMD confirms a NORMAL
monsoon (final LPA >= 96). Flat in deficient years (the confirmation gate) and
flat outside the sowing window (hard exit by Aug31, never into the Oct-Dec drift).

Honesty notes:
  * The backtest gate uses the FINAL season LPA as the resolver. The LIVE trigger
    fires on IMD's June seasonal-update forecast, which carries forecast error vs
    the final number (corr ~0.6-0.7). So the live edge is SOFTER than this gated
    backtest — flagged explicitly.
  * Long delivery only; no short leg -> no honest_short issue. RALLIS/CHAMBLFERT
    are cash-only mid-caps (wider slippage); modelled with the standard cost layer
    (real slippage on mid-caps is worse -> treat metrics as optimistic).
"""
from monsoon_backtest_common import (
    fetch, in_window, simulate, run_battery, print_battery, two_dials, save,
    nifty_buyhold, NIFTY, YEARS, NORMAL_YEARS, DEFICIENT_YEARS, LEG_RT,
)
import pandas as pd

BASKET = ["COROMANDEL.NS", "CHAMBLFERT.NS", "RALLIS.NS", "UPL.NS", "PIIND.NS"]


def main():
    print(f"NORMAL years (deployed): {NORMAL_YEARS}")
    print(f"DEFICIENT years (FLAT, confirmation gate): {DEFICIENT_YEARS}")
    print(f"per-leg round-trip cost = {LEG_RT*10000:.1f} bps  (5-leg basket)")

    nifty = fetch(NIFTY)
    cols = {}
    for t in BASKET:
        try:
            cols[t] = fetch(t).pct_change()
        except Exception as e:
            print(f"  DEGRADE: {t} -> {e}")
    r = pd.DataFrame(cols)
    idx = r.index.intersection(nifty.index)
    r = r.loc[idx]
    # equal-weight basket of the names that actually have data on each day
    port = r.mean(axis=1, skipna=True)

    im = pd.Series(False, index=port.index)
    for y in NORMAL_YEARS:                       # CONFIRMATION gate: normal years only
        im |= in_window(port.index, y, "sowing")

    eq, held, trades = simulate(port, im, n_legs=len(cols))
    # benchmark span = in-market index range
    span = port.index[im.reindex(port.index).fillna(False)]
    bench = nifty_buyhold(nifty, port.index)
    out = run_battery("S1 Kharif Sowing Agri-Input Basket (BASKET/CONFIRMATION)",
                      eq, held, trades, n_trades=len(NORMAL_YEARS), num_trials=3,
                      bench=bench, dd_tol=-20.0)
    print_battery(out)
    # CAAR for the agri-input cluster sowing window ~ +4.73 normal-minus-def; use
    # the genuine sowing divergence as the alignment magnitude; t from COROMANDEL
    # sowing beta (most representative, t=1.38). hit_rate = winning normal years.
    hit = out["wins"] / max(out["n_trades"], 1)
    dials = two_dials(out, hit_rate=hit, sample_n=len(NORMAL_YEARS),
                      relationship_strength=0.45, caar_pct=4.73, sig_t=1.38)
    save(out, dials, "monsoon_bt_s1.json")
    print("\n  CONFIRMATION-GATE NOTE: live IMD June-forecast error makes the real "
          "edge softer than this final-LPA-gated backtest.")


if __name__ == "__main__":
    main()
