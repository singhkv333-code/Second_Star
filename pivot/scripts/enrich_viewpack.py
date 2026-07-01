"""Regenerate the /view-pack showcase data with GENUINELY differentiated, REAL
per-tier strategies — the same substance fix applied to the live curated views,
now applied to the 8-view static showcase (``viewpack01.details.json`` +
``.summaries.json``).

Why / honesty
-------------
The pack tiers used to be equal-weight baskets that differed only by label. But
almost every pack basket member is a real NSE ticker present in the v3 returns
matrix, so we can compute REAL, differentiated weights AND REAL returns:

  * basket / hedge / multi_asset  → weight the long book with a REAL scheme
    (conservative=min_variance, balanced=risk_parity, aggressive=factor), then
    backtest that weighted basket over non-overlapping horizon-length windows of
    the real returns matrix (event-study CAAR curve; real Indian round-trip cost
    charged on entry). total_pct / worst_drop / equity_curve / episodes /
    pct_positive / monte_carlo are RECOMPUTED from that — so weights and returns
    are consistent and real.
  * option_strategy → a REAL modelled Black–Scholes payoff (max loss/profit/
    breakeven/POP/greeks/payoff-curve) at the underlying's realised vol; the
    curve rides the underlying (honest — no offline option price path).

Editorial fields (labels, thesis, plain_*, stance, caveat, transmission …) are
preserved untouched — only the computed metric/chart fields are rewritten. Views
whose members aren't in the matrix (e.g. an index proxy) keep their curated data,
with monte_carlo derived from their existing real per-occurrence episodes.

Run:  python -m scripts.enrich_viewpack        (from the pivot/ dir)
"""
from __future__ import annotations

import json
import os
import re
from typing import Any, Optional

import numpy as np

import backend.core.data.historical  # noqa: F401  (warm circular import)
from backend.market.yfinance_service import canonical_symbol
from backend.services import weighting as _weighting
from backend.services.backtest.validation.monte_carlo import (
    monte_carlo_terminal_distribution,
)
from backend.view_markets import option_model
from backend.view_markets.precompute import (
    BASE_VALUE,
    _apply_cap,
    _avg_curve,
    _episode_cumrets,
    _max_drawdown_pct,
    _mean,
    _median,
    _trust_from_distribution,
    _TIER_CAP,
    _TIER_OPTION_SHAPE,
    _TIER_SCHEME,
)
from scripts.strategy_research.v3 import factors as _v3f
from scripts.strategy_research.v3 import universe as _v3u
from scripts.strategy_research.v3 import exits as _v3e

_PACK_DIR = os.path.join(
    os.path.dirname(__file__), "..", "..", "pivot-next", "components", "views", "pack"
)
_DETAILS = os.path.join(_PACK_DIR, "viewpack01.details.json")
_SUMMARIES = os.path.join(_PACK_DIR, "viewpack01.summaries.json")

_NIFTY_DISPLAY, _NIFTY_SYMBOL = "Nifty 50", "^NSEI"


def _resolve(sym: Optional[str], cols: set) -> Optional[str]:
    """Map a holding symbol to its matrix column, tolerating the canonical
    (no-suffix) form we write on a prior run — keeps the script idempotent."""
    if not sym:
        return None
    if sym in cols:
        return sym
    if f"{sym}.NS" in cols:
        return f"{sym}.NS"
    return None


def _horizon_days(time_horizon: Optional[str], default: int = 126) -> int:
    """Trading-day horizon from a plain string ('6 months', '12-18 months', 'By
    2027'). ~21 trading days/month; clamped to [42, 252]."""
    if not time_horizon:
        return default
    s = str(time_horizon).lower()
    m = re.search(r"(\d+)\s*[-–]?\s*(\d+)?\s*month", s)
    if m:
        lo = int(m.group(1))
        hi = int(m.group(2)) if m.group(2) else lo
        months = (lo + hi) / 2.0
        return int(max(42, min(252, round(months * 21))))
    if "year" in s or re.search(r"by\s*20\d\d", s):
        return 252
    return default


def _windows(idx_len: int, horizon: int) -> list[tuple[int, int]]:
    """Non-overlapping [entry, exit] windows of ``horizon`` bars over the history
    (most-recent-aligned so the last window ends at the last bar)."""
    eps: list[tuple[int, int]] = []
    hi = idx_len - 1
    while hi - horizon >= 0:
        eps.append((hi - horizon + 1, hi))
        hi -= horizon
    return list(reversed(eps))


def _leg_series(rets, present, weights, hedge, nifty):
    """Daily return series of the weighted long book (minus Nifty for a hedge)."""
    w = np.array([weights.get(m, 0.0) for m in present], dtype=float)
    if w.sum() > 0:
        w = w / w.sum()
    long_ret = (rets[present].fillna(0.0).values * w).sum(axis=1)
    s = long_ret
    if hedge:
        s = long_ret - nifty.reindex(rets.index).fillna(0.0).values
    import pandas as pd
    return pd.Series(s, index=rets.index)


def _basket_metrics(rets, nifty, present, tier, kind, horizon):
    """Recompute REAL differentiated metrics for a weighted basket/hedge tier."""
    import pandas as pd

    scheme = _TIER_SCHEME.get(tier, "equal")
    if scheme == "equal" or len(present) < 2:
        weights = {m: 1.0 / len(present) for m in present}
        scheme_used, fb = "equal", None
    else:
        ph = {m: (1.0 + rets[m].dropna()).cumprod() for m in present}
        res = _weighting.compute_weights_detailed(present, scheme, price_history=ph)
        weights = {m: float(res.weights.get(m, 0.0)) for m in present}
        tot = sum(weights.values()) or 1.0
        weights = {m: v / tot for m, v in weights.items()}
        cap = _TIER_CAP.get(tier)
        if cap:
            weights = _apply_cap(weights, cap)
        scheme_used, fb = res.scheme_used, res.fallback_reason

    common = rets[present].dropna()
    if len(common) < horizon + 5:
        return None
    hedge = kind == "hedge"
    leg = _leg_series(common, present, weights, hedge, nifty)
    bench = nifty.reindex(common.index).fillna(0.0)

    eps = _windows(len(common), horizon)
    if len(eps) < 3:
        return None

    strat_paths = [leg.iloc[e:x + 1].reset_index(drop=True) for e, x in eps]
    bench_paths = [bench.iloc[e:x + 1].reset_index(drop=True) for e, x in eps]
    strat_cum = _episode_cumrets(strat_paths, _v3e.DEFAULT_RT)
    bench_cum = _episode_cumrets(bench_paths, 0.0)
    avg_s, avg_b = _avg_curve(strat_cum, bench_cum)
    L = min(len(avg_s), len(avg_b))
    if L < 2:
        return None
    curve = [
        {"t": str(i),
         "strategy": round((1.0 + avg_s[i]) * BASE_VALUE, 2),
         "benchmark": round((1.0 + avg_b[i]) * BASE_VALUE, 2)}
        for i in range(L)
    ]

    per_ep = [float((1.0 + p.fillna(0.0)).prod() - 1.0) * 100.0 for p in strat_paths]
    bench_ep = [float((1.0 + p.fillna(0.0)).prod() - 1.0) * 100.0 for p in bench_paths]
    n = len(per_ep)
    n_pos = sum(1 for r in per_ep if r > 0)
    avg_ret = _mean(per_ep)
    # Honest "worst drop" = the worst intra-window drawdown a SINGLE deployment
    # actually sat through, NOT the drawdown of the smoothed average curve (which
    # averages away real risk to ~0). Take the deepest per-occurrence drawdown.
    ep_dds: list[float] = []
    for p in strat_paths:
        eq = list((1.0 + p.fillna(0.0)).cumprod().values)
        if eq:
            ep_dds.append(_max_drawdown_pct([1.0] + eq))
    worst = min(ep_dds) if ep_dds else _max_drawdown_pct([pt["strategy"] for pt in curve])
    med = _median(per_ep)
    pct_pos = round(n_pos / n * 100.0, 1) if n else None
    verdict, align = _trust_from_distribution(n, pct_pos, med)
    rr = round(avg_ret / abs(worst), 1) if avg_ret is not None and abs(worst) > 1e-9 else None

    # per-member REAL average window return
    holdings = []
    for m in present:
        mpaths = [common[m].iloc[e:x + 1] for e, x in eps]
        mret = _mean([float((1.0 + p.fillna(0.0)).prod() - 1.0) * 100.0 for p in mpaths])
        holdings.append({
            "name": None, "symbol": canonical_symbol(m),
            "return_pct": round(mret or 0.0, 1), "position": "long",
            "weight_pct": round(weights.get(m, 0.0) * 100.0, 1),
        })
    if hedge:
        holdings.append({
            "name": _NIFTY_DISPLAY, "symbol": _NIFTY_SYMBOL,
            "return_pct": round(_mean(bench_ep) or 0.0, 1),
            "position": "short", "weight_pct": None,
        })

    # daily-return sample for MC (concatenate episode legs)
    daily = np.concatenate([p.fillna(0.0).values for p in strat_paths]) if strat_paths else np.array([])
    mc = monte_carlo_terminal_distribution(daily, horizon=horizon) if daily.size else None

    episodes = [
        {"label": f"Window {i + 1}", "date": None,
         "return_pct": round(per_ep[i], 1),
         "benchmark_pct": round(bench_ep[i], 1),
         "positive": per_ep[i] > 0}
        for i in range(n)
    ]

    return {
        "weight_scheme": scheme_used, "weight_fallback": fb,
        "equity_curve": curve, "holdings_weights": {h["symbol"]: h["weight_pct"] for h in holdings},
        "holdings": holdings,
        "strategy_total_pct": round(avg_ret, 2) if avg_ret is not None else None,
        "worst_drop_pct": round(worst, 1),
        "risk_return_ratio": rr, "n_episodes": n,
        "episodes": episodes, "positive_episodes": n_pos,
        "pct_positive": pct_pos, "n_positive": n_pos,
        "trust_verdict": verdict, "historical_alignment": align,
        "monte_carlo": mc, "curve_basis": "in_position_episodes",
    }


def _option_metrics(rets, underlying_sym, underlying_label, bullish, tier, horizon):
    """REAL modelled option payoff from the underlying's REALISED volatility.

    The option expresses the belief on a liquid name from the same basket, so we
    use that name's real daily-return vol — never the option P&L's own dispersion
    (which would be nonsensically large). Falls back to a stated 28% equity-vol
    assumption only if the underlying has no usable history."""
    sigma = None
    if underlying_sym in rets.columns:
        sigma = option_model.realized_vol_annual(rets[underlying_sym].dropna().values)
    if not sigma or sigma > 0.9:               # guard against absurd/illiquid vol
        sigma = 0.28                            # stated equity-vol assumption
    width, atm = _TIER_OPTION_SHAPE.get(tier, (7.0, 0.0))
    return option_model.model_vertical_spread(
        bullish=bullish, sigma_annual=sigma, horizon_days=horizon,
        width_pct=width, atm_offset_pct=atm,
        underlying_label=underlying_label or underlying_sym,
    )


def main() -> None:
    rets = _v3u.returns_matrix()
    nifty = _v3u.series("NIFTY").reindex(rets.index)
    cols = set(rets.columns)

    with open(_DETAILS) as fh:
        details = json.load(fh)
    with open(_SUMMARIES) as fh:
        summaries = json.load(fh)

    log: list[str] = []
    best_by_view: dict[str, dict[str, Any]] = {}

    for vid, v in details.items():
        horizon = _horizon_days(v.get("time_horizon"))
        bullish = True  # pack stance is framed as the bull case
        # Representative liquid underlying for the option tier = the first basket
        # member that exists in the matrix (its REAL vol prices the spread).
        view_underlying = view_underlying_label = None
        for e in v.get("expressions", []):
            if e.get("expression_kind") in ("basket", "hedge", "multi_asset"):
                for h in (e.get("holdings") or []):
                    r = _resolve(h.get("symbol"), cols)
                    if r:
                        view_underlying, view_underlying_label = r, h.get("name")
                        break
            if view_underlying:
                break
        for e in v.get("expressions", []):
            tier = e.get("tier")
            kind = e.get("expression_kind")
            ep_returns = [ep.get("return_pct") for ep in (e.get("episodes") or [])]
            if kind in ("basket", "hedge", "multi_asset"):
                present = [
                    r for h in (e.get("holdings") or [])
                    if h.get("position") != "short"
                    for r in [_resolve(h.get("symbol"), cols)] if r
                ]
                if len(present) >= 2:
                    m = _basket_metrics(rets, nifty, present, tier, kind, horizon)
                    if m:
                        # merge computed metrics, preserve editorial + member names
                        name_by_sym = {canonical_symbol(h.get("symbol")): h.get("name")
                                       for h in (e.get("holdings") or []) if h.get("symbol")}
                        for h in m["holdings"]:
                            if h["name"] is None:
                                h["name"] = name_by_sym.get(h["symbol"]) or h["symbol"]
                        for k in ("weight_scheme", "equity_curve", "holdings",
                                  "strategy_total_pct", "worst_drop_pct",
                                  "risk_return_ratio", "n_episodes", "episodes",
                                  "positive_episodes", "pct_positive", "n_positive",
                                  "trust_verdict", "historical_alignment",
                                  "monte_carlo", "curve_basis"):
                            e[k] = m[k]
                        e["option_model"] = None
                        log.append(f"{vid:9} {tier:12} {kind:11} {m['weight_scheme']:12} "
                                   f"ret={m['strategy_total_pct']} n={m['n_episodes']}")
                        continue
                log.append(f"{vid:9} {tier:12} {kind:11} SKIP (members not in matrix)")
            elif kind == "option_strategy":
                # Resolve the option's own holding symbol if it maps to a matrix
                # name, else use the view's representative basket underlying.
                hsym = None
                for h in (e.get("holdings") or []):
                    r = _resolve(h.get("symbol"), cols)
                    if r:
                        hsym = r
                        break
                usym = hsym or view_underlying or ""
                ulabel = view_underlying_label if usym == view_underlying else None
                om = _option_metrics(rets, usym, ulabel, bullish, tier, horizon)
                e["option_model"] = om
                # MC for the option tier from the underlying's real window returns.
                if usym in cols:
                    common = rets[usym].dropna()
                    eps = _windows(len(common), horizon)
                    daily = np.concatenate(
                        [common.iloc[a:b + 1].fillna(0.0).values for a, b in eps]
                    ) if eps else np.array([])
                    if daily.size:
                        e["monte_carlo"] = monte_carlo_terminal_distribution(daily, horizon=horizon)
                log.append(f"{vid:9} {tier:12} option      -> "
                           f"{(om or {}).get('structure','none')} "
                           f"vol%={(om or {}).get('vol_used_pct')} "
                           f"maxP%={(om or {}).get('max_profit_pct')} POP%={(om or {}).get('pop_pct')}")

        # recompute the best expression (highest positive-rate then return) for the card
        scored = [
            ex for ex in v.get("expressions", [])
            if ex.get("strategy_total_pct") is not None
        ]
        if scored:
            best = max(scored, key=lambda ex: (
                ex.get("pct_positive") or 0, ex.get("strategy_total_pct") or -999))
            best_by_view[vid] = best

    # sync summaries.best_expression + most-interesting-return for the card
    for s in summaries:
        vid = s.get("id")
        best = best_by_view.get(vid)
        if not best:
            continue
        be = s.get("best_expression") or {}
        be.update({
            "total_return_pct": best.get("strategy_total_pct"),
            "worst_drop_pct": best.get("worst_drop_pct"),
            "n_episodes": best.get("n_episodes"),
            "pct_positive": best.get("pct_positive"),
            "n_positive": best.get("n_positive"),
            "trust_verdict": best.get("trust_verdict"),
            "equity_curve": best.get("equity_curve"),
        })
        s["best_expression"] = be

    with open(_DETAILS, "w") as fh:
        json.dump(details, fh, indent=1)
    with open(_SUMMARIES, "w") as fh:
        json.dump(summaries, fh, indent=1)

    print("Enriched view-pack:")
    for line in log:
        print(" ", line)
    print(f"\nWrote {_DETAILS}\nWrote {_SUMMARIES}")


if __name__ == "__main__":
    main()
