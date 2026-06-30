"""v3 episode backtester — three exit variants, each with a NIFTY-comparison
block (§4, §5). Generalizes _crude_bt_common.basket_curve / _it_bt_common.
build_event_curve: concatenate per-episode daily strategy returns into ONE equity
curve (cash between episodes), real Indian round-trip cost charged on each
episode's entry bar, next-bar fill baked into the episode (entry, exit) positions.

The three modes share entry/cost/fill; they differ ONLY in when the episode path
is truncated:
  fixed  — hold to window end (v2 baseline / control).
  target — exit first bar cumulative return ≥ TARGET, else window end.
  manual — workflow-armed; here simulated == fixed (user closes by hand live).

Episode convention: ``(entry_pos, exit_pos)`` are integer positions into the
``daily_rets`` index; the strategy is IN POSITION on bars entry_pos..exit_pos
INCLUSIVE (the runner already applied the one-bar lag when building them).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from backend.services import trading_costs

DEFAULT_RT = trading_costs.round_trip_bps() / 1e4


def _port_daily(seg: pd.DataFrame, weights_or_leg) -> pd.Series:
    """Daily portfolio return over a window segment. ``weights_or_leg`` is either
    a single ticker (str) or a {ticker: weight} dict (basket, weight-normalised)."""
    if isinstance(weights_or_leg, str):
        return seg[weights_or_leg].fillna(0.0)
    w = pd.Series(weights_or_leg, dtype=float)
    cols = [c for c in w.index if c in seg.columns]
    w = w[cols]
    if w.sum() == 0:
        return pd.Series(0.0, index=seg.index)
    return (seg[cols].fillna(0.0) * w).sum(axis=1) / w.sum()


def episode_returns(episodes, daily_rets, weights_or_leg) -> list[pd.Series]:
    """Per-episode daily portfolio return Series (bars entry..exit inclusive)."""
    paths: list[pd.Series] = []
    for (e, x) in episodes:
        if x < e or x >= len(daily_rets):
            continue
        seg = daily_rets.iloc[e:x + 1]
        paths.append(_port_daily(seg, weights_or_leg).reset_index(drop=True))
    return paths


def mfe_analysis(episode_paths: list[pd.Series]) -> dict:
    """Max-Favourable-Excursion across episodes (§4.2). For each episode,
    MFE = max cumulative return over the fixed window. Returns the distribution +
    a PRE-DECLARED target = round number AT-OR-BELOW the median MFE, and a
    sensitivity target = 0.75·median_MFE. Never grid-searched."""
    mfes = []
    for p in episode_paths:
        cum = (1.0 + p.fillna(0.0)).cumprod() - 1.0
        if len(cum):
            mfes.append(float(cum.max()))
    if not mfes:
        return {"median_pct": None, "p25": None, "p75": None,
                "target_pct_declared": None, "sensitivity_pct": None,
                "rounding": "down_to_round", "n": 0}
    arr = np.array(mfes) * 100.0
    med = float(np.median(arr))
    # round DOWN to the nearest round % anchor (…, 3, 5, 8, 10, 12, 15, 20)
    anchors = [3, 5, 8, 10, 12, 15, 20, 25, 30]
    below = [a for a in anchors if a <= med]
    target = float(below[-1]) if below else max(1.0, float(np.floor(med)))
    return {
        "median_pct": round(med, 2),
        "p25": round(float(np.percentile(arr, 25)), 2),
        "p75": round(float(np.percentile(arr, 75)), 2),
        "per_episode_mfe_pct": [round(m, 2) for m in arr.tolist()],
        "target_pct_declared": target,
        "sensitivity_pct": round(0.75 * med, 2),
        "rounding": "down_to_round_anchor",
        "n": len(mfes),
    }


def _apply_mode(path: pd.Series, mode: str, target_frac: float | None) -> pd.Series:
    """Truncate an episode daily-return path per exit mode. Returns the (possibly
    shortened) daily-return path actually realised."""
    if mode in ("fixed", "manual") or target_frac is None:
        return path
    # target: exit the first bar cumulative return >= target
    cum = (1.0 + path.fillna(0.0)).cumprod() - 1.0
    hit = np.where(cum.values >= target_frac)[0]
    if len(hit):
        return path.iloc[: hit[0] + 1]
    return path


def _concat_curve(episodes, daily_rets, weights_or_leg, mode, target_frac,
                  cost_rt) -> dict:
    """Build the concatenated equity curve for one exit mode."""
    paths = episode_returns(episodes, daily_rets, weights_or_leg)
    equity = [1.0]
    daily: list[float] = []
    per_ep: list[float] = []
    hit_target: list[bool] = []
    bars_to_target: list[int | None] = []
    n_ep = 0
    for p in paths:
        realised = _apply_mode(p, mode, target_frac)
        n_ep += 1
        start = equity[-1]
        first = True
        for r in realised.values:
            r = float(r) if np.isfinite(r) else 0.0
            r_net = r - (cost_rt if first else 0.0)
            equity.append(equity[-1] * (1.0 + r_net))
            daily.append(r_net)
            first = False
        per_ep.append(equity[-1] / start - 1.0)
        if mode == "target" and target_frac is not None:
            cum = (1.0 + p.fillna(0.0)).cumprod() - 1.0
            h = np.where(cum.values >= target_frac)[0]
            hit_target.append(bool(len(h)))
            bars_to_target.append(int(h[0]) + 1 if len(h) else None)
    return {"equity": equity, "daily": daily, "per_episode": per_ep,
            "n_episodes": n_ep, "hit_target": hit_target,
            "bars_to_target": bars_to_target}


def nifty_comparison(strat_daily, strat_per_ep, nifty_episode_paths) -> dict:
    """§5 block: strategy vs NIFTY buy-hold over the SAME concatenated episode
    windows (no costs on the benchmark — the do-nothing yardstick). Includes the
    HAC NIFTY-beta of the strategy's daily returns on NIFTY's."""
    from .factors import ols_hac
    # NIFTY concatenated daily over the same episode windows
    nifty_daily: list[float] = []
    nifty_per_ep: list[float] = []
    for p in nifty_episode_paths:
        vals = p.fillna(0.0).values
        nifty_daily.extend(float(v) for v in vals)
        nifty_per_ep.append(float((1.0 + p.fillna(0.0)).prod() - 1.0))
    strat_total = float(np.prod([1.0 + r for r in strat_daily]) - 1.0) * 100.0
    nifty_total = float(np.prod([1.0 + r for r in nifty_daily]) - 1.0) * 100.0
    # align lengths for beta (strategy daily can be shorter under target mode)
    m = min(len(strat_daily), len(nifty_daily))
    beta = beta_t = alpha_ann = None
    if m > 30:
        y = np.array(strat_daily[:m])
        x = np.array(nifty_daily[:m])
        X = np.column_stack([np.ones(m), x])
        b, se, t, _r2, _n = ols_hac(y, X)
        beta = round(float(b[1]), 3)
        beta_t = round(float(t[1]), 2)
        alpha_ann = round(float(b[0]) * 252 * 100.0, 2)
    n_beat = sum(1 for a, b in zip(strat_per_ep, nifty_per_ep) if a > b)
    n_ep = len(strat_per_ep)
    return {
        "strategy_total_pct": round(strat_total, 2),
        "nifty_total_pct": round(nifty_total, 2),
        "excess_pct": round(strat_total - nifty_total, 2),
        "nifty_beta": beta,
        "nifty_beta_t": beta_t,
        "alpha_ann_pct": alpha_ann,
        "pct_episodes_beat": round(100.0 * n_beat / n_ep, 1) if n_ep else None,
        "n_episodes": n_ep,
        "window_basis": "concatenated_episode_windows",
    }


def backtest_exits(episodes, daily_rets, weights_or_leg, nifty_rets, *,
                   modes=("fixed", "target", "manual"), target_pct=None,
                   hold_bars=20, cost_rt=DEFAULT_RT) -> dict:
    """Run the three exit variants on one (episodes, weights_or_leg). Returns
    {mode: EpisodeBTResult}. ``nifty_rets`` is the NIFTY daily-return Series whose
    index aligns with ``daily_rets``. ``target_pct`` is the PRE-DECLARED % target
    (from mfe_analysis); when None the target mode falls back to hold-to-window."""
    target_frac = (target_pct / 100.0) if target_pct is not None else None
    # NIFTY buy-hold over identical episode windows
    nifty_df = nifty_rets.to_frame("NIFTY")
    nifty_paths = episode_returns(episodes, nifty_df, "NIFTY")
    results: dict[str, dict] = {}
    for mode in modes:
        c = _concat_curve(episodes, daily_rets, weights_or_leg, mode,
                          target_frac, cost_rt)
        nc = nifty_comparison(c["daily"], c["per_episode"], nifty_paths)
        res = {
            "mode": mode,
            "params": {"hold_bars": hold_bars,
                       "target_pct": target_pct if mode == "target" else None},
            "equity": c["equity"],
            "daily_rets": c["daily"],
            "per_episode_rets": c["per_episode"],
            "per_episode_pct": [round(r * 100, 2) for r in c["per_episode"]],
            "n_episodes": c["n_episodes"],
            "nifty_comparison": nc,
        }
        if mode == "target":
            res["hit_target"] = c["hit_target"]
            res["bars_to_target"] = c["bars_to_target"]
        results[mode] = res
    return results
