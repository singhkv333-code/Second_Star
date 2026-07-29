"""Crude / geopolitical-shock view — 3 STRATEGY simulations + the REAL Trust Battery.

Run AFTER crude_geo_event_study.py (uses its cached prices), with the repo venv:
    pivot/.venv/bin/python pivot/scripts/strategy_research/crude_geo_strategies.py

Builds a simulated daily equity curve for each of the 3 grounded strategies, then
runs the repo's actual rigor battery on each curve:
    forward_stats_block · monte_carlo_robustness · sub_period_robustness · trust_verdict

Strategies (grounded in crude_geo_event_study.py — genuine-connectedness winners only):
  A  IMPORTER-BENEFICIARY BASKET (crude-DOWN / de-escalation) — long-only equity,
     conviction-weighted to the names that are BOTH crude-connected AND move on a
     crude crash (paints + OMCs + IndiGo). Staging: CONFIRMATION.
  B  UPSTREAM-vs-OMC PAIR (crude-UP / escalation) — long ONGC (+ve Brent-beta)
     vs short BPCL (-ve Brent-beta), beta-neutral; the cleanest connectedness
     spread. Short via SSF future (honest_short). Staging: PRE-POSITION.
  C  DIRECT MCX CRUDE bull-call-spread (escalation) — defined-risk long-crude
     option, Brent-underlying PROXY for the MCX CRUDEOIL spread. Staging: HYBRID.

Costs: the repo's trading_costs round-trip is charged per episode. All numbers are
printed from a real run; nothing is fabricated. The option leg is a clearly-labelled
Brent proxy (real fills need the live MCX chain).
"""
from __future__ import annotations

import json
import os
import sys
import warnings

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

# Repo battery + costs
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
from backend.services.forward_stats import forward_stats_block
from backend.services.backtest.validation.monte_carlo import monte_carlo_robustness
from backend.services.backtest.validation.sub_periods import sub_period_robustness
from backend.services.backtest.validation.verdict import trust_verdict
from backend.services import trading_costs

OUT_DIR = os.path.join(os.path.dirname(__file__), "_out")
CACHE = os.path.join(OUT_DIR, "crude_prices.pkl")

DRIVER = "BZ=F"
BENCH = "^NSEI"
SIG_WIN = 10           # Brent signal lookback (trading days)
HOLD = 20              # holding horizon (trading days) per episode
UP_THRESH = 0.08       # crude-UP escalation signal
DOWN_THRESH = -0.08    # crude-DOWN de-escalation signal


def load() -> pd.DataFrame:
    px = pd.read_pickle(CACHE)
    rets = px.pct_change().mask(px.pct_change().abs() > 0.5)  # same bad-tick clean
    return px, rets


def episodes(brent: pd.Series, direction: str) -> list[tuple[int, int]]:
    """Non-overlapping (entry_idx, exit_idx) episodes when Brent's SIG_WIN move
    crosses the threshold. direction 'up' or 'down'."""
    b = brent.dropna()
    sig = b.pct_change(SIG_WIN)
    idx = list(range(len(b)))
    thr = UP_THRESH if direction == "up" else DOWN_THRESH
    eps: list[tuple[int, int]] = []
    i = SIG_WIN + 1
    n = len(b)
    while i < n - 1:
        s = sig.iloc[i]
        cross = (s >= thr) if direction == "up" else (s <= thr)
        if cross:
            entry = i
            exit_ = min(i + HOLD, n - 1)
            eps.append((entry, exit_))
            i = exit_ + 1  # no overlap
        else:
            i += 1
    return eps, b.index


def basket_curve(rets: pd.DataFrame, names_w: dict[str, float], eps, bindex) -> tuple[pd.Series, int]:
    """Long-only conviction-weighted basket held only during episodes; cash else.
    Charges an equity round-trip cost per episode."""
    # align strategy days to Brent index
    aligned = rets.reindex(bindex)
    daily = pd.Series(0.0, index=bindex)
    mask = pd.Series(False, index=bindex)
    rt_cost = trading_costs.round_trip_bps() / 1e4  # fractional
    for (e, x) in eps:
        seg = aligned.iloc[e + 1:x + 1]
        w = pd.Series(names_w)
        port = (seg[list(names_w)] * w).sum(axis=1) / w.sum()
        daily.iloc[e + 1:x + 1] = port.values
        mask.iloc[e + 1:x + 1] = True
        # subtract round-trip cost spread over the episode entry day
        if x > e:
            daily.iloc[e + 1] -= rt_cost
    equity = (1.0 + daily.fillna(0.0)).cumprod()
    return equity, len(eps), daily.fillna(0.0), mask


def pair_curve(rets: pd.DataFrame, long_s: str, short_s: str, beta: float, eps, bindex):
    """Beta-neutral long/short spread held during episodes. Long single-stock-future
    proxy returns ~ the cash stock return; SSF financing/roll ~ folded into cost.
    Charges two round-trips (both legs) per episode."""
    aligned = rets.reindex(bindex)
    daily = pd.Series(0.0, index=bindex)
    mask = pd.Series(False, index=bindex)
    rt_cost = 2 * trading_costs.round_trip_bps() / 1e4
    for (e, x) in eps:
        lo = aligned[long_s].iloc[e + 1:x + 1].values
        sh = aligned[short_s].iloc[e + 1:x + 1].values
        spread = lo - beta * sh                     # beta-neutral: long 1, short beta
        d = daily.iloc[e + 1:x + 1].values.copy()
        d[:] = spread
        daily.iloc[e + 1:x + 1] = d
        mask.iloc[e + 1:x + 1] = True
        if x > e:
            daily.iloc[e + 1] -= rt_cost
    equity = (1.0 + daily.fillna(0.0)).cumprod()
    return equity, len(eps), daily.fillna(0.0), mask


def option_curve(brent: pd.Series, eps, bindex, *, delta: float = 0.5,
                 prem_at_risk: float = 1.0, max_payoff: float = 1.5):
    """DEFINED-RISK MCX crude bull-call-spread PROXY on Brent (BZ=F).
    During each escalation episode the sleeve takes a capped/floored long-delta
    exposure: cumulative episode P&L floored at -prem_at_risk (lose the debit) and
    capped at +max_payoff (spread max). Daily marks scale Brent daily return by
    `delta` while the running episode P&L is inside the [floor, cap] band.
    Option round-trip cost (MCX premium) charged on the debit at entry."""
    b = brent.reindex(bindex)
    bret = b.pct_change()
    daily = pd.Series(0.0, index=bindex)
    mask = pd.Series(False, index=bindex)
    # option round-trip on the premium notional (approx, MCX segment)
    opt_rt = (trading_costs.option_leg_bps("buy", segment="MCX-OPT")
              + trading_costs.option_leg_bps("sell", segment="MCX-OPT")) / 1e4
    for (e, x) in eps:
        cum = 0.0
        for j in range(e + 1, x + 1):
            r = bret.iloc[j]
            if not np.isfinite(r):
                r = 0.0
            step = delta * r
            new = cum + step
            new = max(-prem_at_risk, min(max_payoff, new))
            daily.iloc[j] = new - cum
            cum = new
            mask.iloc[j] = True
        daily.iloc[e + 1] -= opt_rt * prem_at_risk
    equity = (1.0 + daily.fillna(0.0)).cumprod()
    return equity, len(eps), daily.fillna(0.0), mask


def _battery_on(eq: list[float], daily: list[float], n_trades: int, num_trials: int) -> dict:
    fs = forward_stats_block(eq, num_trials=num_trials)
    mc = monte_carlo_robustness(daily)
    sp = sub_period_robustness(eq, n_periods=4)
    total_ret = (eq[-1] / eq[0] - 1.0) * 100 if eq and eq[0] else None
    verdict = trust_verdict(forward_stats=fs, monte_carlo=mc, sub_periods=sp,
                            total_return_pct=total_ret, n_trades=n_trades)
    return {"total_return_pct": round(total_ret, 2) if total_ret else None,
            "forward_stats": fs, "monte_carlo": mc, "sub_periods": sp, "verdict": verdict}


def battery(name: str, equity: pd.Series, n_trades: int, daily: pd.Series,
            mask: pd.Series, num_trials: int = 3) -> dict:
    # Full calendar NAV (idle cash between signals = flat).
    full = _battery_on(equity.dropna().values.tolist(),
                       equity.pct_change().dropna().values.tolist(), n_trades, num_trials)
    # Trade-time curve: only in-position days (the fair read of the signal's edge).
    td = daily[mask].values
    tt_eq = np.cumprod(1.0 + td).tolist()
    tt = _battery_on(tt_eq, td.tolist(), n_trades, num_trials)
    return {"name": name, "n_trades": n_trades, "full": full, "trade_time": tt}


def _show_block(tag: str, b: dict) -> None:
    fs = b["forward_stats"]
    print(f"  [{tag}] total={b['total_return_pct']}%  obs={fs['n_obs']} obsSharpe={fs['observed_sharpe']} "
          f"PSR={fs['psr']} DSR={fs['deflated_sharpe']} MinTRL={fs['min_trl']}")
    mc = b["monte_carlo"]
    if mc:
        print(f"        MC: dd_p95={mc['dd_p95_severity_pct']}% prob_loss={mc['prob_loss']} "
              f"term_med={mc['terminal_median_pct']}% term_p05={mc['terminal_p05_pct']}%")
    sp = b["sub_periods"]
    if sp:
        print(f"        sub_periods={sp['period_returns_pct']} pos_frac={sp['positive_period_frac']} "
              f"conc={sp['concentration']}")
    v = b["verdict"]
    print(f"        VERDICT: {v['verdict'].upper()} ({v['label']}) conf={v['confidence']} flags={v['flags']}")


def show(res: dict) -> None:
    print(f"\n{'='*78}\n  {res['name']}   episodes(trades)={res['n_trades']}")
    _show_block("full-NAV   ", res["full"])
    _show_block("trade-time ", res["trade_time"])


def main() -> None:
    px, rets = load()
    brent = px[DRIVER]
    down_eps, bidx = episodes(brent, "down")
    up_eps, _ = episodes(brent, "up")
    print(f"crude-DOWN episodes={len(down_eps)}  crude-UP episodes={len(up_eps)}  (signal {SIG_WIN}d, hold {HOLD}d)")

    # --- Strategy A: importer-beneficiary basket (crude-DOWN), conviction weights ---
    # Weights = genuine-connectedness conviction (paints highest |t| + significant CAAR;
    # OMCs connected daily; IndiGo connected). Tyres EXCLUDED from core (loose daily beta).
    a_w = {"ASIANPAINT.NS": 0.22, "BERGEPAINT.NS": 0.18, "HINDPETRO.NS": 0.16,
           "BPCL.NS": 0.14, "IOC.NS": 0.12, "INDIGO.NS": 0.18}
    a_eq, a_n, a_d, a_m = basket_curve(rets, a_w, down_eps, bidx)
    show(battery("A · Importer-beneficiary BASKET (crude-DOWN, CONFIRMATION)", a_eq, a_n, a_d, a_m))

    # --- Strategy B: upstream-vs-OMC pair (crude-UP), beta-neutral ---
    # Long ONGC (Brent-beta +0.074, t=4.64) vs short BPCL (-0.069, t=-4.43). Hedge
    # ratio = ONGC market-beta / BPCL market-beta to strip index (0.92/1.07).
    beta = 0.92 / 1.07
    b_eq, b_n, b_d, b_m = pair_curve(rets, "ONGC.NS", "BPCL.NS", beta, up_eps, bidx)
    show(battery("B · Upstream-vs-OMC PAIR (crude-UP, PRE-POSITION)", b_eq, b_n, b_d, b_m))

    # --- Strategy C: direct MCX crude bull-call-spread proxy (crude-UP) ---
    c_eq, c_n, c_d, c_m = option_curve(brent, up_eps, bidx)
    show(battery("C · DIRECT MCX crude bull-call-spread PROXY (crude-UP, HYBRID)", c_eq, c_n, c_d, c_m))

    print("\n[note] Trust Battery run on the FULL calendar NAV (cash between signals = "
          "flat). Few analog events => short effective track record; verdicts are the "
          "HONEST statistical read, not a target.")


if __name__ == "__main__":
    main()
