"""Regenerate the /view-pack showcase data with per-view CONDITIONED
strategies, honest evidence, forward scenario models, and real minimum-entry
tickets (``viewpack01.details.json`` + ``.summaries.json``).

What changed vs the first-generation enricher (the doctrine fixes)
------------------------------------------------------------------
1. **The view conditions the strategy.** Every view carries a spec (taxonomy,
   direction, driver, option underlying + structure) — so "gold rises" prices
   a gold-vol call spread labelled MCX Gold, a Middle-East shock gets a
   two-sided NIFTY straddle, "Nifty 30k" gets a far-OTM cheap-ticket spread,
   and strike widths scale with each underlying's REAL σ√T instead of a
   one-size +5%. No more one spread in eight costumes, no more
   gold-view-priced-on-Titan.
2. **Evidence obeys the taxonomy.** Theme/relative/event views keep their
   window backtests but the episodes are LABELLED what they are — rolling
   windows with real end dates — via ``evidence_basis``. Unscheduled-shock
   views (mideast) drop the window pseudo-backtest entirely: no analog set is
   comparable, so hit-rates are forbidden and the trust verdict is
   ``insufficient_data``.
3. **Forward scenario model** (``backend.view_markets.forward_model``): for
   beliefs with no precedent the forward statement is a probability-weighted,
   50%-shrunk, cost-adjusted BAND built from a real driver-beta regression
   (gold book on GC=F, oil book on Brent, IT book on MON100) or an explicitly
   stated direct-book scenario — never a fabricated track record.
4. **Real minimum entries** (``backend.view_markets.affordability``): every
   expression carries an ``entry`` block — a whole-share lite basket around
   ₹1,000, the live-verified catalog ETF route (ITBEES ₹30, GOLDBEES ₹118 …),
   an option's true premium × lot, or an honest "needs margin" boundary.

Editorial fields (thesis, plain_*, stance …) are preserved; the known
"Own own" template artifact is repaired in place.

Run:  python -m scripts.enrich_viewpack        (from the pivot/ dir)
Optional live inputs (Kite session + DB): NFO lot sizes, option-implied
probability for the Nifty-30k view. Both degrade honestly when absent.
"""
from __future__ import annotations

import json
import os
import re
from datetime import date
from typing import Any, Optional

import numpy as np

import backend.core.data.historical  # noqa: F401  (warm circular import)
from backend.market.yfinance_service import canonical_symbol
from backend.services import weighting as _weighting
from backend.services.backtest.validation.monte_carlo import (
    monte_carlo_terminal_distribution,
)
from backend.view_markets import affordability as _afford
from backend.view_markets import etf_catalog as _etfcat
from backend.view_markets import forward_model as _fwd
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
    _TIER_SCHEME,
)
from scripts.strategy_research.v3 import exits as _v3e
from scripts.strategy_research.v3 import universe as _v3u

_PACK_DIR = os.path.join(
    os.path.dirname(__file__), "..", "..", "pivot-next", "components", "views", "pack"
)
_DETAILS = os.path.join(_PACK_DIR, "viewpack01.details.json")
_SUMMARIES = os.path.join(_PACK_DIR, "viewpack01.summaries.json")

_NIFTY_DISPLAY, _NIFTY_SYMBOL = "Nifty 50", "^NSEI"

# ── the per-view strategy spec: the view CONDITIONS the strategy ──────────────
# taxonomy ∈ {theme, relative, event, shock}; driver = (series_key, source)
# where source ∈ {matrix, driver, book}; option.structure ∈ {vertical, straddle};
# p_yes/p_source per doctrine A1 (market-implied when readable, stated when not).
_VIEW_SPECS: dict[str, dict[str, Any]] = {
    "ai_jobs": {
        "taxonomy": "theme", "bullish": True,
        "driver": ("MON100.NS", "matrix"), "driver_label": "Nasdaq 100 (MON100, INR)",
        "move_yes": 18.0, "move_no": -8.0,
        "p_yes": 0.5, "p_source": "assumed even odds — no liquid market for this outcome (stated)",
        "option": {"underlying": "INFY.NS", "label": "Infosys",
                   "structure": "vertical", "offset": 0.0, "width_mult": 0.5,
                   "lot_key": "INFY"},
        "etf_category": "it",
    },
    "ai_search": {
        "taxonomy": "relative", "bullish": True,
        "driver": ("MON100.NS", "matrix"), "driver_label": "Nasdaq 100 (MON100, INR)",
        "move_yes": 18.0, "move_no": -8.0,
        "p_yes": 0.5, "p_source": "assumed even odds — no liquid market for this outcome (stated)",
        "option": {"underlying": "PERSISTENT.NS", "label": "Persistent Systems",
                   "structure": "vertical", "offset": 0.0, "width_mult": 0.6,
                   "lot_key": "PERSISTENT"},
        "etf_category": "it",
    },
    "nuclear": {
        "taxonomy": "theme", "bullish": True,
        "driver": ("book", "book"), "driver_label": "the power & capex basket itself",
        "move_yes": 15.0, "move_no": -8.0,
        "p_yes": 0.5, "p_source": "assumed even odds — no liquid market for this outcome (stated)",
        "option": {"underlying": "NTPC.NS", "label": "NTPC",
                   "structure": "vertical", "offset": 0.0, "width_mult": 0.8,
                   "lot_key": "NTPC"},
        "etf_category": "power",
    },
    "ev": {
        "taxonomy": "theme", "bullish": True,
        "driver": ("book", "book"), "driver_label": "the EV-supplier basket itself",
        "move_yes": 15.0, "move_no": -8.0,
        "p_yes": 0.5, "p_source": "assumed even odds — no liquid market for this outcome (stated)",
        "option": {"underlying": "M&M.NS", "label": "Mahindra & Mahindra",
                   "structure": "vertical", "offset": 2.0, "width_mult": 0.7,
                   "lot_key": "M&M"},
        "etf_category": "ev",
    },
    "mideast": {
        "taxonomy": "shock", "bullish": True,
        "driver": ("BRENT", "driver"), "driver_label": "Brent crude (BZ=F)",
        "move_yes": 25.0, "move_no": -5.0,
        "p_yes": 0.5, "p_source": "assumed even odds — geopolitics has no honest base rate (stated)",
        "option": {"underlying": "NIFTY", "label": _NIFTY_DISPLAY,
                   "structure": "straddle", "lot_key": "NIFTY",
                   "rename": "Boldly Both-Ways bet",
                   "plain_label": "Buy both directions on the index",
                   "plain_one_liner": (
                       "Buy a call and a put on the Nifty at the same strike — "
                       "profits if the shock moves markets hard in EITHER "
                       "direction, loses the premium if nothing much happens."
                   )},
        "etf_category": None,
    },
    "nifty30k": {
        "taxonomy": "event", "bullish": True,
        "driver": ("NIFTY", "driver"), "driver_label": "Nifty 50",
        "move_yes": 19.0, "move_no": 0.0,
        "p_yes": 0.2, "p_source": "editorial assumption (stated) — replaced by the option-implied read when a live chain is available",
        "p_implied_target": 30000.0,
        "option": {"underlying": "NIFTY", "label": _NIFTY_DISPLAY,
                   "structure": "vertical", "offset": 8.0, "width_fixed": 6.0,
                   "lot_key": "NIFTY"},
        "etf_category": "nifty50",
    },
    "gold": {
        "taxonomy": "event", "bullish": True,
        "driver": ("GOLD", "driver"), "driver_label": "Gold (COMEX, USD)",
        "move_yes": 10.0, "move_no": -4.0,
        "p_yes": 0.5, "p_source": "assumed even odds — no liquid market read at generation (stated)",
        "option": {"underlying": "GOLD", "label": "MCX Gold (GOLDM)",
                   "structure": "vertical", "offset": 0.0, "width_mult": 0.8,
                   "lot_key": None},
        "etf_category": "gold",
    },
    "fintech": {
        "taxonomy": "theme", "bullish": True,
        "driver": ("book", "book"), "driver_label": "the fintech & market-infra basket itself",
        "move_yes": 20.0, "move_no": -10.0,
        "p_yes": 0.5, "p_source": "assumed even odds — no liquid market for this outcome (stated)",
        "option": {"underlying": "BSE.NS", "label": "BSE",
                   "structure": "vertical", "offset": 5.0, "width_mult": 0.7,
                   "lot_key": "BSE"},
        "etf_category": "capital_markets",
    },
}

_EVIDENCE_BASIS = {
    "theme": "rolling_windows",
    "relative": "rolling_windows",
    "event": "rolling_windows",
    "shock": "shock_no_analogs",
}


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
    """Non-overlapping [entry, exit] windows of ``horizon`` bars over the
    history (most-recent-aligned). These are ROLLING WINDOWS, not events —
    and are always labelled as such downstream (doctrine A2)."""
    eps: list[tuple[int, int]] = []
    hi = idx_len - 1
    while hi - horizon >= 0:
        eps.append((hi - horizon + 1, hi))
        hi -= horizon
    return list(reversed(eps))


def _book_series(rets, legs: list[tuple[str, float, int]]):
    """Daily return series of a signed, weighted book.
    ``legs`` = [(symbol, weight, +1 long | -1 short)] — weights normalised
    within each side."""
    import pandas as pd
    longs = [(s, w) for s, w, sgn in legs if sgn > 0]
    shorts = [(s, w) for s, w, sgn in legs if sgn < 0]
    out = pd.Series(0.0, index=rets.index)
    for side, sign in ((longs, 1.0), (shorts, -1.0)):
        if not side:
            continue
        tot = sum(w for _, w in side) or 1.0
        for s, w in side:
            out = out + sign * (w / tot) * rets[s].fillna(0.0)
    return out


def _basket_metrics(rets, nifty, present, tier, kind, horizon):
    """Windowed metrics for a weighted basket/hedge tier, with REAL window
    end dates on every episode (no more dateless 'Window N')."""
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
    legs = [(m, weights.get(m, 0.0), 1) for m in present]
    leg = _book_series(common, legs)
    if hedge:
        leg = leg - nifty.reindex(common.index).fillna(0.0)
    bench = nifty.reindex(common.index).fillna(0.0)

    eps = _windows(len(common), horizon)
    if len(eps) < 3:
        return None

    strat_paths = [leg.iloc[e:x + 1].reset_index(drop=True) for e, x in eps]
    bench_paths = [bench.iloc[e:x + 1].reset_index(drop=True) for e, x in eps]
    end_dates = [common.index[x] for _, x in eps]
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
    # actually sat through, NOT the drawdown of the smoothed average curve.
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

    daily = np.concatenate([p.fillna(0.0).values for p in strat_paths]) if strat_paths else np.array([])
    mc = monte_carlo_terminal_distribution(daily, horizon=horizon) if daily.size else None

    months = max(1, round(horizon / 21))
    episodes = [
        {"label": f"{months}-mo window ended {end_dates[i].strftime('%b %Y')}",
         "date": str(end_dates[i].date()),
         "return_pct": round(per_ep[i], 1),
         "benchmark_pct": round(bench_ep[i], 1),
         "positive": per_ep[i] > 0}
        for i in range(n)
    ]

    return {
        "weight_scheme": scheme_used, "weight_fallback": fb,
        "weights": weights,
        "book_daily": leg,
        "equity_curve": curve,
        "holdings": holdings,
        "strategy_total_pct": round(avg_ret, 2) if avg_ret is not None else None,
        "worst_drop_pct": round(worst, 1),
        "risk_return_ratio": rr, "n_episodes": n,
        "episodes": episodes, "positive_episodes": n_pos,
        "pct_positive": pct_pos, "n_positive": n_pos,
        "trust_verdict": verdict, "historical_alignment": align,
        "monte_carlo": mc, "curve_basis": "in_position_episodes",
    }


def _pair_metrics(rets, nifty, longs, shorts, horizon):
    """Windowed metrics for a long/short pair book (equal weight within each
    side) — the pair tier previously kept stale curated numbers."""
    present = longs + shorts
    common = rets[present].dropna()
    if len(common) < horizon + 5:
        return None
    legs = ([(s, 1.0, 1) for s in longs] + [(s, 1.0, -1) for s in shorts])
    book = _book_series(common, legs)
    eps = _windows(len(common), horizon)
    if len(eps) < 3:
        return None
    paths = [book.iloc[e:x + 1].reset_index(drop=True) for e, x in eps]
    end_dates = [common.index[x] for _, x in eps]
    per_ep = [float((1.0 + p.fillna(0.0)).prod() - 1.0) * 100.0 for p in paths]
    cum = _episode_cumrets(paths, _v3e.DEFAULT_RT)
    avg_s, _ = _avg_curve(cum, cum)
    curve = [{"t": str(i), "strategy": round((1.0 + avg_s[i]) * BASE_VALUE, 2),
              "benchmark": round(BASE_VALUE, 2)} for i in range(len(avg_s))]
    n = len(per_ep)
    n_pos = sum(1 for r in per_ep if r > 0)
    med = _median(per_ep)
    pct_pos = round(n_pos / n * 100.0, 1) if n else None
    verdict, align = _trust_from_distribution(n, pct_pos, med)
    ep_dds = []
    for p in paths:
        eq = list((1.0 + p.fillna(0.0)).cumprod().values)
        if eq:
            ep_dds.append(_max_drawdown_pct([1.0] + eq))
    worst = min(ep_dds) if ep_dds else None
    months = max(1, round(horizon / 21))
    daily = np.concatenate([p.fillna(0.0).values for p in paths]) if paths else np.array([])
    return {
        "book_daily": book,
        "equity_curve": curve,
        "strategy_total_pct": round(_mean(per_ep), 2),
        "worst_drop_pct": round(worst, 1) if worst is not None else None,
        "n_episodes": n, "n_positive": n_pos, "positive_episodes": n_pos,
        "pct_positive": pct_pos,
        "trust_verdict": verdict, "historical_alignment": align,
        "monte_carlo": monte_carlo_terminal_distribution(daily, horizon=horizon) if daily.size else None,
        "episodes": [
            {"label": f"{months}-mo window ended {end_dates[i].strftime('%b %Y')}",
             "date": str(end_dates[i].date()),
             "return_pct": round(per_ep[i], 1), "benchmark_pct": 0.0,
             "positive": per_ep[i] > 0}
            for i in range(n)
        ],
        "curve_basis": "in_position_episodes",
    }


# ── driver / vol / live-context helpers ───────────────────────────────────────
def _driver_returns(rets, key: str, source: str, book_daily=None):
    if source == "book":
        return book_daily
    if source == "driver":
        return _v3u.series(key)
    if key in rets.columns:
        return rets[key].dropna()
    return None


# Vol estimates use the trailing ~3 years — representative of today's regime,
# not diluted (or inflated) by 2010-era or Covid-crash history.
_VOL_WINDOW_BARS = 756


def _sigma_for(rets, underlying: str) -> Optional[float]:
    if underlying in rets.columns:
        return option_model.realized_vol_annual(
            rets[underlying].dropna().tail(_VOL_WINDOW_BARS).values)
    try:
        return option_model.realized_vol_annual(
            _v3u.series(underlying).tail(_VOL_WINDOW_BARS).values)
    except Exception:
        return None


def _book_sigma(book_daily) -> Optional[float]:
    return option_model.realized_vol_annual(
        book_daily.dropna().tail(_VOL_WINDOW_BARS).values)


def _spot_for(px, underlying: str) -> Optional[float]:
    if underlying in px.columns:
        s = px[underlying].dropna()
        return float(s.iloc[-1]) if len(s) else None
    try:
        s = _v3u.driver_close(underlying)
        return float(s.iloc[-1]) if len(s) else None
    except Exception:
        return None


def _live_context() -> dict[str, Any]:
    """Best-effort live inputs: NFO lot sizes + option-implied probability for
    the Nifty-30k view. Everything degrades to honest None when the session
    or DB is unavailable."""
    ctx: dict[str, Any] = {"lots": {}, "implied_p": {}}
    try:
        from backend.database import SessionLocal
        from backend.market.instrument_master import get_lot_size
        db = SessionLocal()
        try:
            keys = {s["option"].get("lot_key") for s in _VIEW_SPECS.values()}
            for k in keys:
                if k:
                    ctx["lots"][k] = get_lot_size(db, k)
            try:
                from backend.view_markets.implied_move import implied_probability
                spec = _VIEW_SPECS["nifty30k"]
                p = implied_probability(
                    db, "NIFTY", target_level=spec["p_implied_target"],
                    direction="above", horizon_days=126,
                )
                if p is not None:
                    ctx["implied_p"]["nifty30k"] = float(p)
            except Exception as exc:  # noqa: BLE001
                print(f"  [live] implied probability unavailable: {exc}")
        finally:
            db.close()
    except Exception as exc:  # noqa: BLE001
        print(f"  [live] DB context unavailable: {exc}")
    return ctx


def _option_block(rets, spec: dict[str, Any], horizon: int) -> Optional[dict[str, Any]]:
    """The spec-conditioned option payoff model for the aggressive tier."""
    o = spec["option"]
    sigma = _sigma_for(rets, o["underlying"])
    if not sigma or sigma > 0.9:
        sigma = 0.28                       # stated equity-vol fallback
    if o["structure"] == "straddle":
        return option_model.model_long_straddle(
            sigma_annual=sigma, horizon_days=horizon,
            underlying_label=o["label"],
        )
    width = o.get("width_fixed") or option_model.width_for_vol(
        sigma, horizon, mult=o.get("width_mult", 0.8))
    return option_model.model_vertical_spread(
        bullish=spec["bullish"], sigma_annual=sigma, horizon_days=horizon,
        width_pct=float(width), atm_offset_pct=float(o.get("offset", 0.0)),
        underlying_label=o["label"],
    )


def _forward_block(rets, spec: dict[str, Any], book_daily, sigma_annual,
                   horizon: int, live_ctx: dict[str, Any], vid: str,
                   ) -> Optional[dict[str, Any]]:
    """The scenario forward model for a basket/hedge/pair book."""
    if book_daily is None or sigma_annual is None:
        return None
    key, source = spec["driver"]
    p_yes, p_source = spec["p_yes"], spec["p_source"]
    if vid in live_ctx.get("implied_p", {}):
        p_yes = live_ctx["implied_p"][vid]
        p_source = "option-implied from the live NIFTY chain (risk-neutral)"
    if source == "book":
        beta_block = _fwd.stated_direct_beta()
    else:
        drv = _driver_returns(rets, key, source)
        beta_block = _fwd.driver_beta(book_daily, drv) if drv is not None else None
        if beta_block is None:
            return None
    return _fwd.scenario_forward(
        p_yes=p_yes, p_source=p_source, driver=spec["driver_label"],
        driver_move_yes_pct=spec["move_yes"], driver_move_no_pct=spec["move_no"],
        beta_block=beta_block, sigma_annual=sigma_annual, horizon_days=horizon,
    )


_WEAK_TRACK_PREFIX = "Heads-up: this bundle has historically tracked"


def _weak_tracking_warning(e: dict, fw: Optional[dict], driver_label: str) -> None:
    """When the REAL regression says the book barely follows the view's driver,
    say so on the expression — expression-fit honesty (doctrine A5/G5)."""
    if not fw:
        return
    beta = (fw.get("beta") or {})
    if beta.get("basis") != "regression":
        return
    if abs(beta.get("beta") or 0.0) < 0.25 and (beta.get("r2") or 0.0) < 0.05:
        warnings = e.get("warnings") or []
        if not any(str(w).startswith(_WEAK_TRACK_PREFIX) for w in warnings):
            warnings.append(
                f"{_WEAK_TRACK_PREFIX} {driver_label} only weakly "
                f"(beta {beta.get('beta')}, R² {beta.get('r2')}) — it may not "
                "move much even if the view resolves your way."
            )
        e["warnings"] = warnings


def _entry_for_basket(px, weights: dict[str, float], etf_category: Optional[str],
                      kind: str) -> dict[str, Any]:
    prices = {}
    for s in weights:
        ser = px[s].dropna() if s in px.columns else None
        if ser is not None and len(ser):
            prices[s] = float(ser.iloc[-1])
    as_of = str(px.index[-1].date())
    etf = _etfcat.entry(etf_category) if etf_category else None
    block = _afford.entry_block(kind=kind, weights=weights, prices=prices,
                                etf=etf, as_of=as_of)
    # entry legs carry display symbols without the .NS suffix
    for leg in block.get("legs", []) or []:
        leg["symbol"] = canonical_symbol(leg["symbol"])
    for d in block.get("dropped", []) or []:
        d["symbol"] = canonical_symbol(d["symbol"])
    return block


def main() -> None:
    rets = _v3u.returns_matrix()
    px = _v3u.close_matrix()
    nifty = _v3u.series("NIFTY").reindex(rets.index)
    cols = set(rets.columns)
    live_ctx = _live_context()

    with open(_DETAILS) as fh:
        details = json.load(fh)
    with open(_SUMMARIES) as fh:
        summaries = json.load(fh)

    log: list[str] = []
    best_by_view: dict[str, dict[str, Any]] = {}
    fwd_by_view: dict[str, dict[str, Any]] = {}
    min_entry_by_view: dict[str, float] = {}

    for vid, v in details.items():
        spec = _VIEW_SPECS.get(vid)
        if not spec:
            log.append(f"{vid:9} NO SPEC — left untouched")
            continue
        horizon = _horizon_days(v.get("time_horizon"))
        basis = _EVIDENCE_BASIS[spec["taxonomy"]]
        v["evidence_basis"] = basis
        v["generated"] = {
            "on": str(date.today()), "engine": "enrich_viewpack v2 (spec-conditioned)",
            "taxonomy": spec["taxonomy"], "universe_cols": int(rets.shape[1]),
        }

        def _note_entry(block: Optional[dict[str, Any]]) -> None:
            m = (block or {}).get("min_entry_inr")
            if m:
                min_entry_by_view[vid] = min(min_entry_by_view.get(vid, 1e12), float(m))

        for e in v.get("expressions", []):
            tier = e.get("tier")
            kind = e.get("expression_kind")
            # repair the "Own own" template artifact wherever it survived
            if isinstance(e.get("plain_one_liner"), str):
                e["plain_one_liner"] = e["plain_one_liner"].replace("Own own ", "Own ")

            if kind in ("basket", "hedge", "multi_asset"):
                present = [
                    r for h in (e.get("holdings") or [])
                    if h.get("position") != "short"
                    for r in [_resolve(h.get("symbol"), cols)] if r
                ]
                if len(present) < 2 and vid == "nifty30k":
                    present = []           # index-proxy basket handled below
                if spec["taxonomy"] == "shock":
                    # Doctrine: no analog set is comparable — the window
                    # pseudo-backtest is forbidden for unscheduled shocks.
                    m = _basket_metrics(rets, nifty, present, tier, kind, horizon) \
                        if len(present) >= 2 else None
                    e["episodes"] = []
                    e["equity_curve"] = None
                    e["strategy_total_pct"] = None
                    e["worst_drop_pct"] = None
                    e["n_episodes"] = 0
                    e["n_positive"] = None
                    e["positive_episodes"] = None
                    e["pct_positive"] = None
                    e["curve_n_episodes"] = None
                    e["trust_verdict"] = "insufficient_data"
                    e["historical_alignment"] = None
                    e["curve_basis"] = None
                    e["evidence_basis"] = basis
                    if m:
                        e["holdings"] = _named(m["holdings"], e)
                        e["weight_scheme"] = m["weight_scheme"]
                        e["monte_carlo"] = m["monte_carlo"]
                        fw = _forward_block(
                            rets, spec, m["book_daily"],
                            _book_sigma(m["book_daily"]),
                            horizon, live_ctx, vid)
                        e["forward_model"] = fw
                        _weak_tracking_warning(e, fw, spec["driver_label"])
                        if tier == "conservative" and fw:
                            fwd_by_view[vid] = fw
                        entry = _entry_for_basket(px, m["weights"], spec["etf_category"], kind)
                        e["entry"] = entry
                        _note_entry(entry)
                    log.append(f"{vid:9} {tier:12} {kind:11} SHOCK — windows dropped, forward model on")
                    continue
                if len(present) >= 2:
                    m = _basket_metrics(rets, nifty, present, tier, kind, horizon)
                    if m:
                        e["holdings"] = _named(m["holdings"], e)
                        for k in ("weight_scheme", "equity_curve",
                                  "strategy_total_pct", "worst_drop_pct",
                                  "risk_return_ratio", "n_episodes", "episodes",
                                  "positive_episodes", "pct_positive", "n_positive",
                                  "trust_verdict", "historical_alignment",
                                  "monte_carlo", "curve_basis"):
                            e[k] = m[k]
                        e["curve_n_episodes"] = m["n_episodes"]
                        e["option_model"] = None
                        e["evidence_basis"] = basis
                        sigma_book = _book_sigma(m["book_daily"])
                        fw = _forward_block(rets, spec, m["book_daily"], sigma_book,
                                            horizon, live_ctx, vid)
                        e["forward_model"] = fw
                        _weak_tracking_warning(e, fw, spec["driver_label"])
                        if tier == "conservative" and fw:
                            fwd_by_view[vid] = fw
                        entry = _entry_for_basket(px, m["weights"], spec["etf_category"], kind)
                        e["entry"] = entry
                        _note_entry(entry)
                        log.append(f"{vid:9} {tier:12} {kind:11} {m['weight_scheme']:12} "
                                   f"ret={m['strategy_total_pct']} n={m['n_episodes']} "
                                   f"fwd={fw['expected_net_pct'] if fw else '-'} "
                                   f"entry={entry.get('min_entry_inr')}")
                        continue
                # index-proxy basket (nifty30k): ride the driver honestly
                if vid == "nifty30k":
                    drv = _v3u.series("NIFTY")
                    sigma_book = _book_sigma(drv)
                    beta = _fwd.driver_beta(drv, drv)
                    fw = None
                    if beta:
                        p_yes = live_ctx.get("implied_p", {}).get(vid, spec["p_yes"])
                        p_src = ("option-implied from the live NIFTY chain (risk-neutral)"
                                 if vid in live_ctx.get("implied_p", {}) else spec["p_source"])
                        fw = _fwd.scenario_forward(
                            p_yes=p_yes, p_source=p_src, driver=spec["driver_label"],
                            driver_move_yes_pct=spec["move_yes"],
                            driver_move_no_pct=spec["move_no"],
                            beta_block=beta, sigma_annual=sigma_book or 0.14,
                            horizon_days=horizon)
                    e["forward_model"] = fw
                    if fw:
                        fwd_by_view[vid] = fw
                    etf = _etfcat.entry(spec["etf_category"]) if spec["etf_category"] else None
                    if etf:
                        leg = _afford.etf_route(etf)
                        if leg:
                            entry = {"kind": kind, "basis": "etf_substitute",
                                     "min_entry_inr": leg["cost"], "etf": leg,
                                     "as_of": etf.get("as_of"),
                                     "note": (f"{leg['units']} unit(s) of {leg['symbol']} "
                                              f"({leg['tracks']}) — the index itself, one instrument.")}
                            e["entry"] = entry
                            _note_entry(entry)
                    e["evidence_basis"] = basis
                    log.append(f"{vid:9} {tier:12} {kind:11} index-proxy fwd={fw['expected_net_pct'] if fw else '-'}")
                    continue
                log.append(f"{vid:9} {tier:12} {kind:11} SKIP (members not in matrix)")

            elif kind == "pair":
                longs = [r for h in (e.get("holdings") or [])
                         if h.get("position") == "long"
                         for r in [_resolve(h.get("symbol"), cols)] if r]
                shorts = [r for h in (e.get("holdings") or [])
                          if h.get("position") == "short"
                          for r in [_resolve(h.get("symbol"), cols)] if r]
                m = _pair_metrics(rets, nifty, longs, shorts, horizon) \
                    if longs and shorts else None
                if m:
                    for k in ("equity_curve", "strategy_total_pct", "worst_drop_pct",
                              "n_episodes", "episodes", "positive_episodes",
                              "pct_positive", "n_positive", "trust_verdict",
                              "historical_alignment", "monte_carlo", "curve_basis"):
                        e[k] = m[k]
                    e["curve_n_episodes"] = m["n_episodes"]
                    e["evidence_basis"] = basis
                    sigma_book = _book_sigma(m["book_daily"])
                    fw = _forward_block(rets, spec, m["book_daily"], sigma_book,
                                        horizon, live_ctx, vid)
                    e["forward_model"] = fw
                    entry = _afford.entry_block(kind="pair")
                    e["entry"] = entry
                    log.append(f"{vid:9} {tier:12} pair        ret={m['strategy_total_pct']} "
                               f"n={m['n_episodes']} fwd={fw['expected_net_pct'] if fw else '-'}")
                else:
                    e["entry"] = _afford.entry_block(kind="pair")
                    log.append(f"{vid:9} {tier:12} pair        SKIP (legs not in matrix)")

            elif kind == "option_strategy":
                om = _option_block(rets, spec, horizon)
                e["option_model"] = om
                o = spec["option"]
                # spec-driven renames (structure changed → the fun name must too)
                if o.get("rename"):
                    e["strategy_name"] = o["rename"]
                if o.get("plain_label"):
                    e["plain_label"] = o["plain_label"]
                if o.get("plain_one_liner"):
                    e["plain_one_liner"] = o["plain_one_liner"]
                e["strategy_total_pct"] = None
                e["worst_drop_pct"] = None
                e["curve_n_episodes"] = None
                e["evidence_basis"] = basis
                # true rupee minimum: net premium × contract lot (when known)
                lot = live_ctx["lots"].get(o.get("lot_key")) if o.get("lot_key") else None
                spot = _spot_for(px, o["underlying"])
                opt_entry = None
                if om and lot and spot and o["underlying"] != "GOLD":
                    opt_entry = _afford.option_entry(
                        spot=spot, premium_pct_of_spot=om["net_premium_pct"],
                        lot_size=lot)
                entry = _afford.entry_block(kind="option_strategy", option=opt_entry,
                                            as_of=str(px.index[-1].date()))
                e["entry"] = entry
                _note_entry(entry)
                # MC for the option tier from the underlying's window returns
                usym = o["underlying"]
                if usym in cols:
                    common = rets[usym].dropna()
                    eps = _windows(len(common), horizon)
                    daily = np.concatenate(
                        [common.iloc[a:b + 1].fillna(0.0).values for a, b in eps]
                    ) if eps else np.array([])
                    if daily.size:
                        e["monte_carlo"] = monte_carlo_terminal_distribution(daily, horizon=horizon)
                log.append(f"{vid:9} {tier:12} option      {om.get('structure') if om else 'none':18} "
                           f"on {o['label']:22} width={om.get('width_pct') if om else '-'} "
                           f"POP%={om.get('pop_pct') if om else '-'} "
                           f"entry={entry.get('min_entry_inr')}")

        # best expression (highest positive-rate then return) for the card
        scored = [
            ex for ex in v.get("expressions", [])
            if ex.get("strategy_total_pct") is not None
        ]
        if scored:
            best = max(scored, key=lambda ex: (
                ex.get("pct_positive") or 0, ex.get("strategy_total_pct") or -999))
            best_by_view[vid] = best

    # sync summaries: best_expression + forward + min-entry + evidence basis
    for s in summaries:
        vid = s.get("id")
        spec = _VIEW_SPECS.get(vid)
        if not spec:
            continue
        s["evidence_basis"] = _EVIDENCE_BASIS[spec["taxonomy"]]
        if vid in min_entry_by_view:
            s["min_entry_inr"] = round(min_entry_by_view[vid])
        fw = fwd_by_view.get(vid)
        if fw:
            s["forward_expected_net_pct"] = fw["expected_net_pct"]
            s["forward_band_pct"] = fw["band_pct"]
            s["forward_p_source"] = fw["p_source"]
        best = best_by_view.get(vid)
        if not best:
            if spec["taxonomy"] == "shock":
                # the stale windowed numbers must NOT survive on the card
                be = s.get("best_expression") or {}
                for k in ("total_return_pct", "worst_drop_pct", "n_episodes",
                          "pct_positive", "n_positive", "equity_curve"):
                    be[k] = None
                be["trust_verdict"] = "insufficient_data"
                s["best_expression"] = be
                s["best_episode_pct"] = None
                s["best_episode_label"] = None
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
        eps = best.get("episodes") or []
        if eps:
            top = max(eps, key=lambda ep: ep.get("return_pct") or -1e9)
            s["best_episode_pct"] = top.get("return_pct")
            s["best_episode_label"] = top.get("label")

    with open(_DETAILS, "w") as fh:
        json.dump(details, fh, indent=1)
    with open(_SUMMARIES, "w") as fh:
        json.dump(summaries, fh, indent=1)

    print("Enriched view-pack (spec-conditioned):")
    for line in log:
        print(" ", line)
    print(f"\nWrote {_DETAILS}\nWrote {_SUMMARIES}")


def _named(holdings: list[dict], e: dict) -> list[dict]:
    """Merge computed holdings with the editorial member names."""
    name_by_sym = {canonical_symbol(h.get("symbol")): h.get("name")
                   for h in (e.get("holdings") or []) if h.get("symbol")}
    for h in holdings:
        if h.get("name") is None:
            h["name"] = name_by_sym.get(h["symbol"]) or h["symbol"]
    return holdings


if __name__ == "__main__":
    main()
