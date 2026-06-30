"""S3 — M&M Defined-Risk Monsoon Call Spread · OPTION_STRATEGY · HYBRID.

M&M monthly bull call SPREAD (long ATM + short higher-strike call = defined risk)
laddered 50/30/20 across [Apr15..Sep30], hard exit by Sep30.

LIMITATION (stated, not fabricated): yfinance has NO historical option IV, so a
true premium/theta P&L cannot be backtested here. This script backtests the
DEFINED-RISK UNDERLYING PROXY: M&M underlying return scaled by a ~0.5 net spread
delta and CAPPED per-window to mimic the bounded payoff of a debit call spread
(the spread cannot make more than (width - debit) however far M&M runs, and
cannot lose more than the net debit). That captures the directional edge and the
defined-risk shape; it does NOT model theta decay or IV — the true option curve
must be recomputed with the repo option_strategies engine + an IV source. The
proxy is OPTIMISTIC on theta (a held call spread bleeds time value) and the
directional edge is the honest part.

Option-leg costs use the option cost layer indirectly via the per-leg fraction;
single-stock options are physically settled + STT-on-intrinsic -> square off
before monthly expiry (modelled as the hard Sep30 exit).
"""
from monsoon_backtest_common import (
    fetch, in_window, simulate, run_battery, print_battery, two_dials, save,
    nifty_buyhold, NIFTY, YEARS, WIN, LEG_RT,
)
import numpy as np
import pandas as pd
from backend.services.trading_costs import option_leg_bps

NAME = "M&M.NS"
SPREAD_DELTA = 0.5      # net delta of an ATM/OTM debit call spread (~0.5 at entry)
# Defined-risk cap: a debit spread's max gain ~ (width-debit) ~ a few x the debit.
# Cap the cumulative in-window proxy return at +60% / floor at -100% (debit lost).
WIN_CAP, WIN_FLOOR = 0.60, -1.00


def main():
    opt_rt = option_leg_bps("buy") + option_leg_bps("sell")
    print(f"option per-leg round-trip cost = {opt_rt*10000:.1f} bps  (2 option legs)")
    print(f"PROXY: underlying delta={SPREAD_DELTA}, defined-risk cap +{WIN_CAP*100:.0f}%/"
          f"{WIN_FLOOR*100:.0f}%; NO IV/theta modelled (stated limitation).")

    nifty = fetch(NIFTY)
    s = fetch(NAME)
    raw = s.pct_change() * SPREAD_DELTA
    idx = raw.index.intersection(nifty.index)
    raw = raw.loc[idx]

    im = pd.Series(False, index=raw.index)
    for y in YEARS:
        im |= in_window(raw.index, y, "s3")

    # Apply the defined-risk cap per season on the cumulative in-window path.
    port = raw.copy()
    for y in YEARS:
        w = in_window(raw.index, y, "s3")
        if not w.any():
            continue
        seg = raw[w].fillna(0.0)
        cum = (1.0 + seg).cumprod() - 1.0
        capped = cum.clip(lower=WIN_FLOOR, upper=WIN_CAP)
        # convert capped cumulative back to per-day increments
        prev = capped.shift(1).fillna(0.0)
        port.loc[w] = (1.0 + capped) / (1.0 + prev) - 1.0

    eq, held, trades = simulate(port, im, n_legs=2, leg_rt=opt_rt)
    bench = nifty_buyhold(nifty, raw.index)
    out = run_battery("S3 M&M Defined-Risk Monsoon Call Spread PROXY (OPTION/HYBRID)",
                      eq, held, trades, n_trades=len(YEARS), num_trials=3,
                      bench=bench, dd_tol=-20.0)
    print_battery(out)
    # M&M season CAAR proxy; sig from M&M monsoon-beta t in the full study (~1.1).
    hit = out["wins"] / max(out["n_trades"], 1)
    dials = two_dials(out, hit_rate=hit, sample_n=len(YEARS),
                      relationship_strength=0.45, caar_pct=4.0, sig_t=1.1)
    save(out, dials, "monsoon_bt_s3.json")
    print("\n  LIMITATION REMINDER: directional defined-risk PROXY only. True option "
          "premium/theta P&L needs the option_strategies engine + historical IV.")


if __name__ == "__main__":
    main()
