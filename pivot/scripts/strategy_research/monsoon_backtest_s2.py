"""S2 — Forecast Run-up: Tractor/2W vs NIFTY · PAIR (beta-neutral) · PRE-POSITION.

Long EW [M&M, TVSMOTOR] financed by a SHORT NIFTY index FUTURE, sized to the
long basket's market beta (estimated on an EXPANDING window of returns strictly
BEFORE each season's entry -> no look-ahead, clamped [0.4,1.6]). In-market the
FORECAST window [Apr15..Jun15] only (armed pre-position in early April), flat
otherwise. Index-future short is the clean legal short (honest_short: no SLB/ETF
problem). Deployed every year (it is a calendar/positioning edge, only weakly
LPA-conditional) -> 16 seasonal trades.
"""
from monsoon_backtest_common import (
    fetch, in_window, simulate, run_battery, print_battery, two_dials, save,
    nifty_buyhold, NIFTY, YEARS, WIN, LEG_RT,
)
import numpy as np
import pandas as pd

LONG = ["M&M.NS", "TVSMOTOR.NS"]


def beta_before(long_ret: pd.Series, nifty_ret: pd.Series, entry: pd.Timestamp) -> float:
    """Market beta of the long basket estimated on all returns STRICTLY before the
    season entry date (expanding, no look-ahead). Clamp to [0.4, 1.6]."""
    mask = long_ret.index < entry
    x = nifty_ret[mask].dropna()
    y = long_ret[mask].reindex(x.index).dropna()
    x = x.reindex(y.index)
    if len(y) < 60 or x.var() == 0:
        return 0.85  # sensible prior before enough history
    beta = float(np.cov(y, x)[0, 1] / np.var(x))
    return float(np.clip(beta, 0.4, 1.6))


def main():
    print(f"per-leg round-trip cost = {LEG_RT*10000:.1f} bps  (2 long + 1 index short = 3 legs)")
    nifty = fetch(NIFTY)
    rn = nifty.pct_change()
    cols = {t: fetch(t).pct_change() for t in LONG}
    r = pd.DataFrame(cols)
    idx = r.index.intersection(rn.index)
    r = r.loc[idx]
    rn = rn.loc[idx]
    long_ret = r.mean(axis=1)

    # Build beta-neutral excess return season-by-season with pre-entry beta.
    port = pd.Series(0.0, index=long_ret.index)
    im = pd.Series(False, index=long_ret.index)
    (sm, sd), (em, ed) = WIN["forecast"]
    betas = {}
    for y in YEARS:
        entry = pd.Timestamp(f"{y}-{sm}-{sd}")
        b = beta_before(long_ret, rn, entry)
        betas[y] = round(b, 3)
        w = in_window(long_ret.index, y, "forecast")
        im |= w
        port[w] = long_ret[w] - b * rn[w]   # long stocks minus beta*index
    print(f"  pre-entry market betas per season: {betas}")

    eq, held, trades = simulate(port, im, n_legs=3)
    bench = nifty_buyhold(nifty, long_ret.index)
    out = run_battery("S2 Forecast Run-up Tractor/2W vs NIFTY (PAIR/PRE-POSITION)",
                      eq, held, trades, n_trades=len(YEARS), num_trials=3,
                      bench=bench, dd_tol=-20.0)
    print_battery(out)
    # Forecast-window tractor/2W CAAR +7.46% t=2.83 (strongest in the study).
    hit = out["wins"] / max(out["n_trades"], 1)
    dials = two_dials(out, hit_rate=hit, sample_n=len(YEARS),
                      relationship_strength=0.55, caar_pct=7.46, sig_t=2.83)
    save(out, dials, "monsoon_bt_s2.json")


if __name__ == "__main__":
    main()
