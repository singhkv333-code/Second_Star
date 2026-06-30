"""Shared backtest engine for the Crude / geopolitical-shock view (strategies A/B/C).

Honest, reproducible single-event-study-grounded backtester used by:
    crude_geo_backtest_a.py   importer-beneficiary BASKET   (crude-DOWN)
    crude_geo_backtest_b.py   upstream-vs-OMC PAIR          (crude-UP)
    crude_geo_backtest_c.py   direct MCX crude option PROXY (crude-UP)

Design rules (NON-negotiable, see CLAUDE.md):
  * DATA = yfinance only (Kite not connected). Longest sensible window
    (2010-01-01 -> today, max available). NSE tickers use `.NS`.
  * NO look-ahead: the Brent 10d-move SIGNAL is read at the CLOSE of day i;
    the position is effective from day i+1 (one-bar lag, next-bar fill).
  * REAL Indian costs from backend.services.trading_costs charged per episode
    round-trip (equity for A, 2 legs for B, MCX-OPT premium for C).
  * Pairs are beta-neutral (spread = long - beta*short), beta = market-betas.
  * Option legs: no historical option chain -> backtest the DEFINED-RISK
    UNDERLYING PROXY and LABEL the limitation; never fabricate an option curve.
  * Full real Trust Battery on the equity curve; bad-tick mask |daily r|>0.5.

Everything printed is from a real run. Reproducible "like a pro in VS Code".
"""
from __future__ import annotations

import math
import os
import sys
import warnings

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

# Repo battery + costs + two-dial confidence
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
from backend.services.forward_stats import forward_stats_block, max_drawdown_pct  # noqa: E402
from backend.services.backtest.validation.monte_carlo import monte_carlo_robustness  # noqa: E402
from backend.services.backtest.validation.sub_periods import sub_period_robustness  # noqa: E402
from backend.services.backtest.validation.verdict import trust_verdict  # noqa: E402
from backend.services import trading_costs  # noqa: E402
from backend.view_markets import confidence as conf  # noqa: E402

OUT_DIR = os.path.join(os.path.dirname(__file__), "_out")
os.makedirs(OUT_DIR, exist_ok=True)
BT_CACHE = os.path.join(OUT_DIR, "crude_bt_prices.pkl")

# ── View parameters (measurable statement) ───────────────────────────────────
DRIVER = "BZ=F"        # Brent front-month = the driver
BENCH = "^NSEI"        # NIFTY 50 = benchmark / buy-hold
SIG_WIN = 10           # Brent signal lookback (trading days)
HOLD = 20             # holding horizon (trading days) per episode
UP_THRESH = 0.08       # crude-UP escalation signal (10d Brent >= +8%)
DOWN_THRESH = -0.08    # crude-DOWN de-escalation signal (10d Brent <= -8%)
TRADING_DAYS = 252

# Full universe needed across A/B/C
UNIVERSE = [
    "ASIANPAINT.NS", "BERGEPAINT.NS", "HINDPETRO.NS", "BPCL.NS", "IOC.NS",
    "INDIGO.NS", "ONGC.NS", "OIL.NS", "GOLDBEES.NS", "RELIANCE.NS",
    BENCH, DRIVER,
]


# ── Data ──────────────────────────────────────────────────────────────────────
def fetch(start: str = "2010-01-01", *, force: bool = False) -> pd.DataFrame:
    """Longest-sensible-window daily closes for the universe (yfinance, live).
    Cached to crude_bt_prices.pkl for reproducibility; pass force=True to refresh."""
    if (not force) and os.path.exists(BT_CACHE):
        px = pd.read_pickle(BT_CACHE)
        if px.index.max() >= pd.Timestamp.today().normalize() - pd.Timedelta(days=5):
            return px
    import yfinance as yf
    raw = yf.download(UNIVERSE, start=start, auto_adjust=True, progress=False)
    px = raw["Close"].copy()
    px = px.dropna(how="all")
    px.to_pickle(BT_CACHE)
    return px


def clean_returns(px: pd.DataFrame) -> pd.DataFrame:
    """Daily simple returns with a |r|>0.5 bad-tick mask (yfinance split glitches)."""
    r = px.pct_change()
    return r.mask(r.abs() > 0.5)


# ── Signal / episodes (NO look-ahead) ─────────────────────────────────────────
def episodes(brent: pd.Series, direction: str) -> tuple[list[tuple[int, int]], pd.Index]:
    """Non-overlapping (entry_i, exit_i) episodes. Signal = Brent SIG_WIN move read
    at CLOSE of day i; position effective day i+1 .. i+HOLD (set by callers)."""
    b = brent.dropna()
    sig = b.pct_change(SIG_WIN)
    thr = UP_THRESH if direction == "up" else DOWN_THRESH
    eps: list[tuple[int, int]] = []
    i, n = SIG_WIN + 1, len(b)
    while i < n - 1:
        s = sig.iloc[i]
        cross = (s >= thr) if direction == "up" else (s <= thr)
        if pd.notna(s) and cross:
            entry = i
            exit_ = min(i + HOLD, n - 1)
            eps.append((entry, exit_))
            i = exit_ + 1            # no overlap
        else:
            i += 1
    return eps, b.index


# ── Strategy simulators (next-bar fills, real costs) ──────────────────────────
def basket_curve(rets, names_w, eps, bindex, *, charge_costs=True):
    """Long-only conviction-weighted delivery basket held day i+1..exit during each
    episode (cash otherwise). One equity round-trip charged on the entry bar."""
    aligned = rets.reindex(bindex)
    daily = pd.Series(0.0, index=bindex)
    mask = pd.Series(False, index=bindex)
    rt = trading_costs.round_trip_bps() / 1e4 if charge_costs else 0.0
    w = pd.Series(names_w)
    ep_rets: list[float] = []
    for (e, x) in eps:
        seg = aligned.iloc[e + 1:x + 1]
        port = (seg[list(names_w)] * w).sum(axis=1) / w.sum()
        daily.iloc[e + 1:x + 1] = port.values
        mask.iloc[e + 1:x + 1] = True
        if x > e:
            daily.iloc[e + 1] -= rt
        ep_rets.append(float((1.0 + port.fillna(0.0)).prod() - 1.0 - (rt if x > e else 0.0)))
    equity = (1.0 + daily.fillna(0.0)).cumprod()
    return equity, daily.fillna(0.0), mask, ep_rets


def pair_curve(rets, long_s, short_s, beta, eps, bindex, *, charge_costs=True):
    """Beta-neutral long/short spread (long 1, short beta) held i+1..exit per episode.
    Long & short legs each charged a round-trip (2x). SSF financing folded into cost."""
    aligned = rets.reindex(bindex)
    daily = pd.Series(0.0, index=bindex)
    mask = pd.Series(False, index=bindex)
    rt = 2 * trading_costs.round_trip_bps() / 1e4 if charge_costs else 0.0
    ep_rets: list[float] = []
    for (e, x) in eps:
        lo = aligned[long_s].iloc[e + 1:x + 1].fillna(0.0).values
        sh = aligned[short_s].iloc[e + 1:x + 1].fillna(0.0).values
        spread = lo - beta * sh
        daily.iloc[e + 1:x + 1] = spread
        mask.iloc[e + 1:x + 1] = True
        if x > e:
            daily.iloc[e + 1] -= rt
        ep_rets.append(float((1.0 + pd.Series(spread)).prod() - 1.0 - (rt if x > e else 0.0)))
    equity = (1.0 + daily.fillna(0.0)).cumprod()
    return equity, daily.fillna(0.0), mask, ep_rets


def option_proxy_curve(brent, eps, bindex, *, delta=0.5, prem_at_risk=1.0,
                       max_payoff=1.5, charge_costs=True):
    """DEFINED-RISK bull-call-spread PROXY on the Brent underlying (BZ=F).
    LIMITATION: no historical MCX/Brent option chain -> this is an underlying
    defined-risk proxy, NOT a real option curve. Per episode the sleeve marks a
    long-delta exposure (delta * Brent daily return) with cumulative episode P&L
    floored at -prem_at_risk (lose the debit) and capped at +max_payoff (spread
    max). MCX-OPT premium round-trip charged on the debit at entry."""
    b = brent.reindex(bindex)
    bret = b.pct_change()
    daily = pd.Series(0.0, index=bindex)
    mask = pd.Series(False, index=bindex)
    opt_rt = (trading_costs.option_leg_bps("buy", segment="MCX-OPT")
              + trading_costs.option_leg_bps("sell", segment="MCX-OPT")) / 1e4
    if not charge_costs:
        opt_rt = 0.0
    ep_rets: list[float] = []
    for (e, x) in eps:
        cum = 0.0
        seg0 = cum
        for j in range(e + 1, x + 1):
            r = bret.iloc[j]
            r = float(r) if np.isfinite(r) else 0.0
            new = max(-prem_at_risk, min(max_payoff, cum + delta * r))
            daily.iloc[j] = new - cum
            cum = new
            mask.iloc[j] = True
        daily.iloc[e + 1] -= opt_rt * prem_at_risk
        ep_rets.append(float(cum - seg0 - opt_rt * prem_at_risk))
    equity = (1.0 + daily.fillna(0.0)).cumprod()
    return equity, daily.fillna(0.0), mask, ep_rets


# ── Battery + descriptive metrics ─────────────────────────────────────────────
def _battery_on(eq, daily, n_trades, num_trials):
    fs = forward_stats_block(eq, num_trials=num_trials)
    mc = monte_carlo_robustness(daily)
    sp = sub_period_robustness(eq, n_periods=4)
    total = (eq[-1] / eq[0] - 1.0) * 100 if eq and eq[0] else None
    mdd = max_drawdown_pct(eq)
    verdict = trust_verdict(forward_stats=fs, monte_carlo=mc, sub_periods=sp,
                            total_return_pct=total, n_trades=n_trades)
    return {"total_return_pct": round(total, 2) if total is not None else None,
            "max_drawdown_pct": round(mdd, 2) if mdd is not None else None,
            "forward_stats": fs, "monte_carlo": mc, "sub_periods": sp, "verdict": verdict}


def descriptive(equity_full, daily_full, mask, ep_rets, n_days_total):
    """CAGR, total return, max DD, win rate, in-position days from the FULL NAV."""
    eq = equity_full.dropna()
    total = float(eq.iloc[-1] / eq.iloc[0] - 1.0)
    years = max(1e-9, len(eq) / TRADING_DAYS)
    cagr = (1.0 + total) ** (1.0 / years) - 1.0
    in_pos = int(mask.sum())
    wins = sum(1 for r in ep_rets if r > 0)
    win_rate = wins / len(ep_rets) if ep_rets else None
    return {
        "total_return_pct": round(total * 100, 2),
        "cagr_pct": round(cagr * 100, 2),
        "n_trades": len(ep_rets),
        "win_rate": round(win_rate, 3) if win_rate is not None else None,
        "avg_trade_pct": round(float(np.mean(ep_rets)) * 100, 2) if ep_rets else None,
        "med_trade_pct": round(float(np.median(ep_rets)) * 100, 2) if ep_rets else None,
        "best_trade_pct": round(max(ep_rets) * 100, 2) if ep_rets else None,
        "worst_trade_pct": round(min(ep_rets) * 100, 2) if ep_rets else None,
        "in_position_days": in_pos,
        "calendar_days": int(n_days_total),
    }


def benchmark(px, bindex):
    """NIFTY buy-and-hold over the full window, same start/end as the strategy."""
    nifty = px[BENCH].reindex(bindex).dropna()
    eq = nifty / nifty.iloc[0]
    total = float(eq.iloc[-1] / eq.iloc[0] - 1.0)
    years = max(1e-9, len(eq) / TRADING_DAYS)
    cagr = (1.0 + total) ** (1.0 / years) - 1.0
    mdd = max_drawdown_pct(eq.values.tolist())
    daily = nifty.pct_change().dropna().values.tolist()
    fs = forward_stats_block(eq.values.tolist(), num_trials=1)
    return {"total_return_pct": round(total * 100, 2), "cagr_pct": round(cagr * 100, 2),
            "max_drawdown_pct": round(mdd, 2) if mdd is not None else None,
            "obs_sharpe": fs["observed_sharpe"]}


def cost_drag(gross_total_pct, net_total_pct):
    """Cost-survival in 0..1: how much of the GROSS edge survives Indian costs."""
    if gross_total_pct is None or gross_total_pct == 0:
        return None
    if gross_total_pct <= 0:
        return 0.0
    return max(0.0, min(1.0, net_total_pct / gross_total_pct))


# ── Two-dial alignment score (REAL backend.view_markets.confidence) ───────────
def two_dial(*, hit_rate, relationship_strength, sample_n, min_trl, verdict,
             caar_alignment, significance_p, cost_survival, deflated_sharpe,
             n_obs, payoff_pop=None):
    """Score both dials with the REAL confidence module. SUPPRESSES below MinTRL /
    insufficient_data (the honest gate). Returns (TwoDial-ish dict)."""
    outcome = conf.score_outcome_dial(
        hit_rate=hit_rate, relationship_strength=relationship_strength,
        sample_n=sample_n, min_trl=min_trl, verdict=verdict)
    expr = conf.score_expression_dial(
        caar_bhar_alignment=caar_alignment, significance_p=significance_p,
        cost_survival=cost_survival, payoff_pop=payoff_pop, verdict=verdict,
        deflated_sharpe=deflated_sharpe, n_obs=n_obs, min_trl=min_trl)
    return outcome, expr


def indicative_dial(dimension, **kw):
    """Soft-blend WITHOUT the MinTRL suppression gate (pass min_trl=None), purely
    for colour next to the official (suppressed) number. Clearly labelled."""
    if dimension == "outcome":
        return conf.score_outcome_dial(
            hit_rate=kw.get("hit_rate"),
            relationship_strength=kw.get("relationship_strength"),
            sample_n=kw.get("sample_n"), min_trl=None, verdict="unproven")
    return conf.score_expression_dial(
        caar_bhar_alignment=kw.get("caar_alignment"),
        significance_p=kw.get("significance_p"),
        cost_survival=kw.get("cost_survival"),
        payoff_pop=kw.get("payoff_pop"),
        verdict="unproven", deflated_sharpe=kw.get("deflated_sharpe"),
        n_obs=kw.get("n_obs"), min_trl=None)


def two_sided_p(t):
    """Two-sided p-value from a t/z stat via the normal approx (math only)."""
    if t is None:
        return None
    return 2.0 * (1.0 - 0.5 * (1.0 + math.erf(abs(t) / math.sqrt(2.0))))


# ── Pretty print ──────────────────────────────────────────────────────────────
def print_block(tag, b):
    fs = b["forward_stats"]
    print(f"  [{tag}] total={b['total_return_pct']}%  maxDD={b['max_drawdown_pct']}%  "
          f"obs={fs['n_obs']} obsSharpe={fs['observed_sharpe']} skew={fs['skew']} "
          f"kurt={fs['kurtosis']}")
    print(f"        PSR={fs['psr']} DSR={fs['deflated_sharpe']} MinTRL={fs['min_trl']} "
          f"(num_trials={fs['num_trials']})")
    mc = b["monte_carlo"]
    if mc:
        print(f"        MC: dd_p95={mc['dd_p95_severity_pct']}% dd_med={mc['dd_median_pct']}% "
              f"prob_loss={mc['prob_loss']} term_med={mc['terminal_median_pct']}% "
              f"term_p05={mc['terminal_p05_pct']}%")
    sp = b["sub_periods"]
    if sp:
        print(f"        sub_periods={sp['period_returns_pct']} pos_frac={sp['positive_period_frac']} "
              f"conc={sp['concentration']}")
    v = b["verdict"]
    print(f"        VERDICT: {v['verdict'].upper()} ({v['label']}) conf={v['confidence']} "
          f"flags={v['flags']}")


def print_dial(d):
    if d.suppressed:
        print(f"    {d.dimension.upper():10s}: SUPPRESSED (—)  [{d.rationale}]")
    else:
        print(f"    {d.dimension.upper():10s}: {d.letter} {d.score}/100  [{d.rationale}]")
