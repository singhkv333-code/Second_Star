"""Precompute REAL, cached chart/metric data for the curated Views.

The Views FE needs a real line chart (strategy vs Nifty), a per-holding returns
table/heatmap, a risk/return number, and a gallery mini line — all from REAL
computed prices, never fabricated. Computing curves on every request is slow, so
we precompute ONCE into an on-disk JSON cache (:data:`CACHE_PATH`) and the router
loads it cheaply per request.

AVERAGE-OCCURRENCE methodology (the methodology fix)
----------------------------------------------------
Every headline return on a View card is **per occurrence**: a view's expression is
deployed ONCE PER OCCURRENCE of its event (each monsoon season, each weak-IT print),
so the honest headline is the AVERAGE return over the event's past occurrences —
NOT the return compounded across all of them. The earlier curve here concatenated
and compounded every occurrence into one line (four ~20% monsoon seasons stacking
to a misleading +109% ramp, implying a single deployment earns >100%). We now build
the **average single occurrence**: each past episode's in-position cumret path,
time-normalised across occurrences of differing length and averaged (event-study
CAAR style). Its endpoint == the average per-occurrence return. Built by reusing the
v3 research engine:

  * ``v3/exits.py``    — ``backtest_exits`` / ``episode_returns`` build the
    per-episode daily strategy returns and concatenate them into ONE equity
    curve (cash between episodes, real Indian round-trip cost charged on each
    episode's entry bar), starting at 1.0. The endpoint of the default (``fixed``)
    variant IS the stored ``total_return_pct``.
  * ``v3/universe.py`` — the SAME ``returns_matrix()`` (auto_adjust=True parquet)
    the stored headline was computed from. NOTE: the production ``fetch_multi_symbol``
    layer uses ``auto_adjust=False`` (no dividend adjustment) and diverges ~1.8pp
    on dividend-paying names — it cannot reproduce the headline within tolerance,
    so we deliberately use the engine's own data here (verified: every endpoint
    matches the stored headline to the rounding).
  * ``v3/factors.py``  — reconstructs the IT_f factor for the IT pair leg.

Per ViewExpression we emit, all over the SAME concatenated episodes:

  * ``equity_curve`` — ``[{"t": "0", "strategy": float, "benchmark": float}, …]``
    the AVERAGE single occurrence, where ``t`` is the in-occurrence trading-day
    index (occurrences differ in length, so they are aligned on normalised progress
    and averaged; a calendar/date axis would lie). Both legs are rebased to a
    ₹1,00,000 base and start there; the strategy endpoint == the average
    per-occurrence return. The strategy leg depends on the expression kind:
      - basket / multi_asset → equal-weight ``members_long`` (the headline basket).
      - pair                 → IT: long basket − IT_f factor; Monsoon: the
                               0.5·long − 0.5·Nifty dollar-neutral spread (the v3
                               leg, faithfully reconstructed).
      - hedge                → long basket − Nifty (market-neutral).
      - option_strategy      → the single underlying's own in-position path
                               (``curve_basis="underlying"`` — honest; no live
                               option chain to reconstruct a payoff from).
  * ``avg_episode_return_pct`` / ``avg_episode_benchmark_pct`` /
    ``avg_episode_excess_pct`` — the AVERAGE return over the past occurrences (the
    headline a single deployment can expect); ``n_episodes`` is the occurrence count.
    ``episode_boundaries`` is now always ``[]`` (one averaged occurrence, no stitches).
  * ``curve_basis`` — ``"in_position_episodes"`` (or ``"underlying"``).
  * ``holdings`` — per-holding REAL AVERAGE in-position return per occurrence.
  * ``risk_return_ratio`` — ``avg_episode_return_pct / abs(max_drawdown_pct)`` of the
    average-occurrence curve (1 dp).

HONESTY: if the engine is unavailable, the basket is empty (a developing view
like Crude), or a leg can't be faithfully reconstructed, we serve an empty curve
/ empty holdings / ``None`` ratio — never a fabricated or rescaled line.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any, Optional

import numpy as np

# Warm the circular import path (core.data.historical ↔ yfinance_service) before
# importing the yfinance layer directly, mirroring the app's import order.
import backend.core.data.historical  # noqa: F401
from backend.market.yfinance_service import canonical_symbol
from backend.services import weighting as _weighting
from backend.services.backtest.validation.monte_carlo import (
    monte_carlo_terminal_distribution,
)
from backend.view_markets import confidence, option_model, plain_copy
from backend.view_markets import episode_stats as _estats
from backend.view_markets import option_universe as _ouniv

logger = logging.getLogger(__name__)

# ── config ──────────────────────────────────────────────────────────────────

BASE_VALUE = 100_000.0                  # ₹1,00,000 rebased base

CACHE_PATH = os.path.join(os.path.dirname(__file__), "precomputed_views.json")

VIEW_IDS = [plain_copy.VIEW_IT, plain_copy.VIEW_MONSOON, plain_copy.VIEW_CRUDE]

# ── v3 episode engine (reuse — do NOT reinvent) ──────────────────────────────
# Imported lazily-guarded so a deploy without the research scripts degrades to
# honest empty curves rather than crashing the precompute.
try:
    from scripts.strategy_research.v3 import universe as _v3u
    from scripts.strategy_research.v3 import factors as _v3f
    from scripts.strategy_research.v3 import exits as _v3e
    from scripts.strategy_research._it_bt_common import WEAK_ANALOGS as _IT_EVENTS

    _V3_OK = True
except Exception as exc:  # noqa: BLE001
    logger.warning("precompute: v3 engine unavailable (%s); episode curves disabled", exc)
    _V3_OK = False

# IT: enter T+1 after a weak-IT print, hold to T+20 (fixed). EST_GUARD mirrors
# it_v3._episodes (t0-130 of history must exist before the entry bar).
_IT_WIN_LO, _IT_WIN_HI, _IT_EST_GUARD = 1, 20, 131
# Monsoon: the v2 SOWING window, restricted to IMD-normal years (96-104% LPA).
_MON_SOWING = (("06", "01"), ("08", "31"))
_MON_NORMAL_YEARS = [2010, 2011, 2016, 2021]
_HOLD_BARS = 20

_NIFTY_DISPLAY = "Nifty 50"
_NIFTY_SYMBOL = "^NSEI"

# Curated short context label per weak-IT-guidance print date (from it_v3 _out
# events) — plain, no jargon, real event each maps to.
_IT_EVENT_LABELS: dict[str, str] = {
    "2022-04-11": "TCS Q4FY22 print",
    "2022-07-08": "Q1FY23 margins",
    "2023-01-09": "Q3FY23 slowdown",
    "2023-04-12": "Q4FY23 weak FY24 guide",
    "2023-07-12": "Q1FY24 furloughs",
    "2023-10-11": "Q2FY24 soft growth",
    "2024-04-12": "Q4FY24 muted FY25",
    "2025-01-09": "Q3FY25 guidance cut",
}

_MONTHS = ["", "Jan", "Feb", "Mar", "Apr", "May", "Jun",
           "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

# Plain-English hold rule per view (the "exit_period" string the detail page
# shows). Curated from the real event windows / hold rule, never invented.
_EXIT_PERIOD: dict[str, str] = {
    plain_copy.VIEW_MONSOON: (
        "Held through the Jun–Aug monsoon window (~3 months) each year"
    ),
    plain_copy.VIEW_IT: (
        "Held ~20 trading days (about a month) after each weak-guidance print"
    ),
    plain_copy.VIEW_CRUDE: (
        "Exits at a profit target, else ~20 trading days"
    ),
}

# ── tier differentiation (the substance fix) ──────────────────────────────────
# The tiers used to share ONE equal-weight long book (``{m: 1.0}``), so
# Conservative/Balanced/Aggressive differed only by the hedge overlay. We now
# weight each tier's long book with a REAL scheme computed from the returns
# matrix (verified non-fallback on the curated members), so the tiers hold the
# same names in genuinely different proportions AND recompute genuinely different
# returns/drawdowns. The ladder mirrors the spec tier knobs (defensive → balanced
# → momentum) using only schemes computable offline from returns (``mcap`` needs
# market caps we don't have here and would honestly fall back to equal, so it is
# intentionally not used):
#   conservative → min_variance  (capital-preserving, lowest-vol tilt)
#   balanced     → risk_parity   (equal risk contribution across names)
#   aggressive   → factor         (momentum/quality tilt; concentrates on strength)
_TIER_SCHEME: dict[str, str] = {
    "conservative": "min_variance",
    "balanced": "risk_parity",
    "aggressive": "factor",
}
# Hard single-name cap per tier (spec §5/§4.3). Only applied when feasible
# (cap × n_names ≥ 1); on the small curated baskets a 0.10/0.15 cap is infeasible
# and is skipped rather than forced into an invalid distribution.
_TIER_CAP: dict[str, float] = {
    "conservative": 0.10, "balanced": 0.15, "aggressive": 0.20,
}
# Thesis direction of each view's OPTION expression (weak IT guidance → bearish;
# a normal monsoon → bullish). Fixes the old hardcoded bull-call-spread.
_VIEW_BULLISH: dict[str, bool] = {
    plain_copy.VIEW_IT: False,        # weak-guidance print → bear put spread
    plain_copy.VIEW_MONSOON: True,    # normal sowing season → bull call spread
    plain_copy.VIEW_CRUDE: True,
}
# Per-tier width multiplier on the underlying's REAL expected move (σ√T) —
# replaces the old fixed 10/7/5% widths so a calm index and a wild single
# stock stop getting the same spread. Conservative wider & cheaper (higher
# POP, lower payoff ratio); aggressive tight ATM.
_TIER_WIDTH_MULT: dict[str, float] = {
    "conservative": 1.2,
    "balanced": 0.9,
    "aggressive": 0.7,
}
# Vol estimates use the trailing ~3 years — today's regime, not 2010-era or
# Covid-crash history.
_VOL_WINDOW_BARS = 756
# ETF substitution category per view (only where the exposure genuinely
# matches the catalog entry — never a stretch).
_VIEW_ETF_CATEGORY: dict[str, Optional[str]] = {
    plain_copy.VIEW_MONSOON: "consumption",
}


def _apply_cap(weights: dict[str, float], cap: float) -> dict[str, float]:
    """Water-fill a single-name cap onto ``weights`` (renormalised to 1). No-op if
    the cap is infeasible (cap × n < 1)."""
    w = {k: float(v) for k, v in weights.items()}
    syms = list(w)
    # Skip unless the cap leaves genuine headroom above equal-weight (cap > 1/n).
    # When cap == 1/n the only capped distribution summing to 1 is equal-weight,
    # so capping there would silently FLATTEN a real scheme back to equal.
    if not syms or cap * len(syms) < 1.0 + 1e-6:
        return w
    for _ in range(len(syms)):
        over = [s for s in syms if w[s] > cap + 1e-9]
        if not over:
            break
        excess = sum(w[s] - cap for s in over)
        for s in over:
            w[s] = cap
        under = [s for s in syms if w[s] < cap - 1e-9]
        pool = sum(w[s] for s in under)
        if pool <= 0:
            break
        for s in under:
            w[s] += excess * (w[s] / pool)
    tot = sum(w.values()) or 1.0
    return {s: v / tot for s, v in w.items()}


def _blank() -> dict[str, Any]:
    return {
        "equity_curve": [],
        "holdings": [],
        "risk_return_ratio": None,
        "underlying_symbol": None,
        "curve_basis": None,
        "n_episodes": 0,
        "episode_boundaries": [],
        # round-3 detail-page fields (real-or-empty / null)
        "episodes": [],
        "positive_episodes": 0,
        "exit_period": None,
        "historical_alignment": None,
        "monte_carlo": None,
        # per-tier weighting (the substance fix): the REAL scheme used + any
        # honest fallback reason. None on empty/developing expressions.
        "weight_scheme": None,
        "weight_fallback": None,
        # REAL modelled option payoff (Black–Scholes) for the option tier — max
        # loss/profit/breakeven/POP/greeks/payoff-curve. None for non-option kinds.
        "option_model": None,
        # Real minimum-entry ticket (lite whole-share basket / catalog ETF /
        # option premium × lot / honest boundary). None when unbuilt.
        "entry": None,
        # The four comparable metrics (avg/max gain and loss) from the same
        # per-occurrence distribution; modelled analogue for option tiers.
        "gain_loss": None,
        # AVERAGE return over the event's past occurrences (NOT compounded across
        # them) — the honest headline a single deployment can actually earn.
        "avg_episode_return_pct": None,
        "avg_episode_benchmark_pct": None,
        "avg_episode_excess_pct": None,
        # Positive-outcome frequency over the SAME past occurrences — the ONLY
        # basis for trust (never benchmark-beating; see
        # ``_trust_from_distribution``).
        "pct_positive": None,
        "n_positive": None,
        # Own-return-distribution trust verdict (contract rule #3). None for
        # option/derivative expressions (rule #4 — no real historical option
        # payoff exists) or when there isn't yet a real occurrence sample.
        "trust_verdict": None,
    }


# ── tiny helpers ──────────────────────────────────────────────────────────────


def _str_enum(val: Any) -> str:
    return str(getattr(val, "value", val)) if val is not None else ""


def _members_long(cfg: dict[str, Any]) -> list[str]:
    structure = cfg.get("structure") if isinstance(cfg.get("structure"), dict) else {}
    ml = structure.get("members_long")
    if isinstance(ml, list):
        return [str(s) for s in ml if s]
    return []


def _max_drawdown_pct(values: list[float]) -> float:
    peak = values[0] if values else 0.0
    max_dd = 0.0
    for v in values:
        if v > peak:
            peak = v
        if peak > 0:
            dd = (v - peak) / peak * 100.0
            if dd < max_dd:
                max_dd = dd
    return max_dd


def _mean(xs: list[float]) -> Optional[float]:
    vals = [float(x) for x in xs if x is not None and np.isfinite(float(x))]
    return (sum(vals) / len(vals)) if vals else None


def _median(xs: list[float]) -> Optional[float]:
    vals = [float(x) for x in xs if x is not None and np.isfinite(float(x))]
    return float(np.median(vals)) if vals else None


def _letter_for_score(score: int) -> str:
    """API-contract letter band for the historical-alignment dial:
    A>=85, B>=70, C>=55, D>=40, else F."""
    if score >= 85:
        return "A"
    if score >= 70:
        return "B"
    if score >= 55:
        return "C"
    if score >= 40:
        return "D"
    return "F"


def _trust_from_distribution(
    n: int, pct_positive: Optional[float], median_pct: Optional[float],
) -> tuple[Optional[str], Optional[dict[str, Any]]]:
    """Contract rule #3 — the trust verdict + historical-alignment dial,
    derived ONLY from the expression's OWN past-occurrence return distribution
    (positive-outcome frequency + N + median) — NEVER from beating a
    benchmark.

      N < 8                           -> "insufficient_data", alignment None
      p >= 0.70 AND median > 0        -> "promising",          alignment 78
      0.58 <= p < 0.70                -> "unproven",            alignment 60
      otherwise (p < 0.58 or med<=0)  -> "no_edge",              alignment 42

    Deterministic and real: every input is a real per-occurrence number
    already computed by the episode engine — nothing is fabricated.
    """
    if n < 8 or pct_positive is None:
        return "insufficient_data", None
    p = pct_positive / 100.0
    med = median_pct if median_pct is not None else 0.0
    if p >= 0.70 and med > 0:
        score, verdict = 78, "promising"
    elif 0.58 <= p < 0.70:
        score, verdict = 60, "unproven"
    else:
        score, verdict = 42, "no_edge"
    return verdict, {"score": score, "letter": _letter_for_score(score)}


# ── average-occurrence curve (the methodology fix) ────────────────────────────
# The old curve CONCATENATED + COMPOUNDED every past occurrence into one line, so
# four ~20% monsoon seasons stacked to a misleading +109% ramp — implying a single
# deployment earns >100%. A view's expression is deployed ONCE PER OCCURRENCE, so
# the honest path is the AVERAGE single occurrence: each past episode's in-position
# cumulative-return path, time-normalised across occurrences of differing length
# and averaged (event-study CAAR style). Its endpoint == the average per-occurrence
# return — the number a single deployment can actually expect.


def _episode_cumrets(paths: list, cost_rt: float) -> list[Any]:
    """For each per-episode daily-return Series, the cumulative-return path WITH a
    leading 0.0 (index 0 = entry/base, then cumret after each in-position bar). The
    real Indian round-trip cost is charged on the entry bar only (0.0 for the
    benchmark, the do-nothing yardstick)."""
    out: list[Any] = []
    for p in paths:
        vals = [float(r) if np.isfinite(r) else 0.0 for r in p.fillna(0.0).values]
        cum = [0.0]
        eq = 1.0
        for i, r in enumerate(vals):
            r_net = r - (cost_rt if i == 0 else 0.0)
            eq *= (1.0 + r_net)
            cum.append(eq - 1.0)
        out.append(np.asarray(cum, dtype=float))
    return out


def _avg_curve(
    strat_cumrets: list, bench_cumrets: list,
) -> tuple[list[float], list[float]]:
    """Average the per-episode cumret paths onto a common grid (= the longest
    occurrence's bar count) by linear interpolation over normalised progress, then
    mean across occurrences. Both series start at 0.0; the endpoint == the mean of
    each occurrence's final cumret (the average per-occurrence return). No
    fabrication: every input is a real episode path."""
    usable = [c for c in strat_cumrets if len(c) >= 2]
    if not usable:
        return [], []
    grid_n = max(len(c) - 1 for c in usable)          # bars in the longest episode
    if grid_n < 1:
        return [], []
    grid = np.linspace(0.0, 1.0, grid_n + 1)

    def resample(cumrets: list) -> list[float]:
        stacked = []
        for c in cumrets:
            bars = len(c) - 1
            if bars < 1:
                continue
            xp = np.linspace(0.0, 1.0, bars + 1)
            stacked.append(np.interp(grid, xp, c))
        if not stacked:
            return []
        return [float(v) for v in np.mean(np.vstack(stacked), axis=0)]

    return resample(strat_cumrets), resample(bench_cumrets)


# ── episode-window construction (real event dates / seasons) ──────────────────


def _it_episodes(idx) -> tuple[list[tuple[int, int]], list[dict[str, str]]]:
    """(entry_pos, exit_pos) inclusive for each weak-IT print: enter T+1, hold to
    T+20 (mirrors it_v3._episodes, with the estimation-window guard). Returns the
    episode positions AND a parallel list of ``{label, date}`` metadata (same
    order, same filter) so the detail page can name each episode."""
    import pandas as pd

    eps: list[tuple[int, int]] = []
    meta: list[dict[str, str]] = []
    for a in _IT_EVENTS:
        pos = idx.searchsorted(pd.Timestamp(a))
        lo, hi = pos + _IT_WIN_LO, pos + _IT_WIN_HI
        if lo < _IT_EST_GUARD or hi >= len(idx):
            continue
        eps.append((int(lo), int(hi)))
        ts = pd.Timestamp(a)
        meta.append(
            {
                "label": _IT_EVENT_LABELS.get(a, "Weak IT print"),
                "date": f"{_MONTHS[ts.month]} {ts.year}",
            }
        )
    return eps, meta


def _monsoon_episodes(idx) -> tuple[list[tuple[int, int]], list[dict[str, str]]]:
    """One (entry_pos, exit_pos) per IMD-normal year over the SOWING window;
    enter the first bar after window start (one-bar lag), exit the last bar on/
    before window end (mirrors monsoon_v3._window_episodes). Returns the episode
    positions AND a parallel ``{label, date}`` metadata list."""
    import pandas as pd

    (sm, sd), (em, ed) = _MON_SOWING
    eps: list[tuple[int, int]] = []
    meta: list[dict[str, str]] = []
    for y in _MON_NORMAL_YEARS:
        ws = pd.Timestamp(f"{y}-{sm}-{sd}")
        we = pd.Timestamp(f"{y}-{em}-{ed}")
        lo = int(idx.searchsorted(ws, side="left"))
        hi = int(idx.searchsorted(we, side="right")) - 1
        entry = lo + 1
        if entry < hi and hi < len(idx):
            eps.append((entry, hi))
            meta.append({"label": f"{y} monsoon", "date": f"Jun–Aug {y}"})
    return eps, meta


# ── the engine context (built ONCE per process) ───────────────────────────────


class _Engine:
    """Holds the v3 returns matrix + reusable factors so we load the (large)
    parquet matrix and Nifty series exactly once for all expressions."""

    def __init__(self) -> None:
        self.rets = _v3u.returns_matrix()
        self.px = _v3u.close_matrix()
        self.idx = self.rets.index
        self.r_nifty = _v3u.series("NIFTY").reindex(self.idx)
        it_syms = [s for s in _v3f.it_symbols(_v3u.industry_map())
                   if s in self.rets.columns]
        self.it_f = _v3f.it_factor(self.rets, it_syms)
        it_eps, it_meta = _it_episodes(self.idx)
        mon_eps, mon_meta = _monsoon_episodes(self.idx)
        self.episodes: dict[str, list[tuple[int, int]]] = {
            plain_copy.VIEW_IT: it_eps,
            plain_copy.VIEW_MONSOON: mon_eps,
            # Crude is a developing view with an empty basket → no episodes here.
        }
        # Parallel per-episode {label, date} metadata (same order as episodes).
        self.episode_meta: dict[str, list[dict[str, str]]] = {
            plain_copy.VIEW_IT: it_meta,
            plain_copy.VIEW_MONSOON: mon_meta,
        }

    # — the long basket as a daily EW return series —
    def _long_ew(self, present: list[str]):
        return _v3f.ew_factor(self.rets, present)

    def _long_weighted(self, present: list[str], weights: dict[str, float]):
        """Weighted daily return series of the long book (falls back to equal
        weight if the weights are empty/degenerate)."""
        import pandas as pd

        w = pd.Series({m: float(weights.get(m, 0.0)) for m in present}, dtype=float)
        if w.sum() <= 0:
            return self._long_ew(present)
        w = w / w.sum()
        return self.rets[present].fillna(0.0).mul(w, axis=1).sum(axis=1)

    def tier_weights(
        self, present: list[str], tier: str,
    ) -> tuple[dict[str, float], str, Optional[str]]:
        """REAL per-tier target weights over ``present`` (summing to 1) using a
        scheme computed from the returns matrix, plus the scheme actually used and
        any honest fallback reason. Equal-weight for <2 names or an unknown tier."""
        scheme = _TIER_SCHEME.get(tier)
        if scheme is None or len(present) < 2:
            n = len(present) or 1
            return {m: 1.0 / n for m in present}, "equal", None
        price_history = {
            m: (1.0 + self.rets[m].dropna()).cumprod() for m in present
        }
        res = _weighting.compute_weights_detailed(
            present, scheme, price_history=price_history,
        )
        weights = {m: float(res.weights.get(m, 0.0)) for m in present}
        tot = sum(weights.values()) or 1.0
        weights = {m: v / tot for m, v in weights.items()}
        cap = _TIER_CAP.get(tier)
        if cap:
            weights = _apply_cap(weights, cap)
        return weights, res.scheme_used, res.fallback_reason

    def _leg(
        self, view_id: str, kind: str, present: list[str],
        weights: dict[str, float],
    ):
        """Return (src_df, weights_or_leg, curve_basis, underlying_symbol) for the
        in-position curve, reconstructing the EXACT v3 leg for this kind/view.
        ``present`` are member tickers that exist in the matrix; ``weights`` is the
        tier's REAL long-book weighting, applied to the basket and to the long leg
        of a pair/hedge (so tiers hold the same names in different proportions)."""
        if kind in ("basket", "multi_asset"):
            return self.rets, dict(weights), "in_position_episodes", None

        if kind == "hedge":
            src = self.rets.copy()
            src["__LEG__"] = (
                self._long_weighted(present, weights) - self.r_nifty
            ).reindex(src.index)
            return src, "__LEG__", "in_position_episodes", None

        if kind == "pair":
            src = self.rets.copy()
            if view_id == plain_copy.VIEW_IT:
                # IT balanced: weighted long basket / short IT_f (rotation-neutral).
                src["__LEG__"] = (
                    self._long_weighted(present, weights) - self.it_f
                ).reindex(src.index)
            else:
                # Monsoon balanced: 0.5·long − 0.5·Nifty dollar-neutral pair
                # (mirror monsoon_v3 exactly, incl. its fillna(0) Nifty handling).
                src["NIFTY"] = self.r_nifty.reindex(self.rets.index)
                long_leg = _v3e._port_daily(src, dict(weights))
                src["__LEG__"] = 0.5 * long_leg - 0.5 * src["NIFTY"].fillna(0.0)
            return src, "__LEG__", "in_position_episodes", None

        if kind == "option_strategy":
            # No faithful historical option payoff path exists offline → the
            # HONEST fallback is the single underlying's own in-position path
            # (the modelled Black–Scholes payoff is attached separately).
            return self.rets, present[0], "underlying", present[0]

        # Unknown kind → treat as a long basket (never fabricate).
        return self.rets, dict(weights), "in_position_episodes", None

    def _benchmark_per_episode_pct(self, episodes) -> list[float]:
        """Nifty buy-hold total return % within each episode window (same order
        as ``episodes``) — the per-episode benchmark the strategy is judged
        against."""
        out: list[float] = []
        for p in _v3e.episode_returns(episodes, self.r_nifty.to_frame("NIFTY"), "NIFTY"):
            tot = float((1.0 + p.fillna(0.0)).prod() - 1.0) * 100.0
            out.append(round(tot, 2))
        return out

    def _member_holdings(self, episodes, present: list[str]) -> list[tuple[str, float]]:
        """Per-member AVERAGE in-position return (gross, no cost) across the past
        occurrences — 'how each holding did, on average, each time' — NOT compounded
        across occurrences (which would overstate it the same way the old headline
        did)."""
        out: list[tuple[str, float]] = []
        for m in present:
            paths = _v3e.episode_returns(episodes, self.rets, m)
            per_ep = [
                float((1.0 + p.fillna(0.0)).prod() - 1.0) * 100.0 for p in paths
            ]
            out.append((m, _mean(per_ep) or 0.0))
        return out


def _build_engine() -> Optional[_Engine]:
    if not _V3_OK:
        return None
    try:
        return _Engine()
    except Exception as exc:  # noqa: BLE001
        logger.warning("precompute: failed to build v3 engine (%s)", exc)
        return None


# ── core curve maths (episode-gated, REAL series only) ────────────────────────


def _historical_alignment(cfg: dict[str, Any]) -> Optional[dict[str, Any]]:
    """Per-EXPRESSION historical-alignment dial → ``{score, letter}`` or ``None``.

    The seeded ``expression_score`` was set equal to the view-level
    ``construction_alignment`` — so every expression of a view carried the SAME
    number (e.g. both the monsoon basket AND its option spread showed 79). That
    is the bug the detail page exposed: the dial must vary by strategy and tier.

    We recompute the dial here from this expression's OWN realised backtest
    evidence (episode-beat consistency, deflated-Sharpe edge, realised
    reward:risk) blended with the belief-design fit, capped at the Trust-verdict
    ceiling. Every input is a real per-expression number already on the config —
    nothing is fabricated. Returns ``None`` (FE shows 'not enough track record')
    when the seeded dial was suppressed / N is below MinTRL / no design score."""
    scores = (cfg or {}).get("scores") or {}
    if not isinstance(scores, dict):
        return None
    bt = scores.get("backtest") or {}
    bt = bt if isinstance(bt, dict) else {}
    return confidence.score_historical_alignment(
        construction_alignment=scores.get("construction_alignment"),
        pct_episodes_beat=bt.get("pct_episodes_beat"),
        deflated_sharpe=bt.get("deflated_sharpe"),
        total_return_pct=bt.get("total_return_pct"),
        max_dd_pct=bt.get("max_dd_pct"),
        verdict=bt.get("trust_verdict"),
        n_obs=bt.get("n_obs"),
        min_trl=bt.get("min_trl"),
        expression_dial=bt.get("expression_dial"),
    )


def _episode_holdings(
    engine: _Engine, view_id: str, kind: str, present: list[str],
    avg_bench_pct: Optional[float], weights: dict[str, float],
    option_payload: Optional[dict[str, Any]] = None,
) -> list[dict[str, Any]]:
    """Per-holding rows with position + REAL weight + REAL in-position return.

    basket/multi_asset → each long member at its REAL per-tier weight. pair/hedge
    → the weighted long members plus ONE short-leg row (the IT sector factor for
    the IT pair, else the Nifty index — never a per-stock short). option_strategy
    → the modelled, direction-correct legs (no fabricated per-leg return)."""
    if kind == "option_strategy":
        legs = (option_payload or {}).get("legs") or []
        leg_sym = canonical_symbol(present[0]) if present else None
        out: list[dict[str, Any]] = []
        for leg in legs:
            action = str(leg.get("action", "")).strip()
            pos = "long" if action.upper() == "BUY" else "short"
            label = leg.get("strike_label")
            name = f"{action.title()} {leg.get('option_type', '')}".strip()
            if label:
                name = f"{name} ({label})"
            out.append({
                "name": name, "symbol": leg_sym,
                "return_pct": None, "position": pos, "weight_pct": None,
            })
        return out

    out: list[dict[str, Any]] = []
    for sym, ret in engine._member_holdings(engine.episodes.get(view_id) or [], present):
        w = weights.get(sym)
        out.append({
            "name": plain_copy.stock_name(sym) or sym,
            "symbol": canonical_symbol(sym),
            "return_pct": round(ret, 1),
            "position": "long",
            "weight_pct": round(w * 100.0, 1) if w is not None else None,
        })
    if kind in ("pair", "hedge"):
        # The short leg is the IT sector factor for the IT pair, else the Nifty
        # index. For the IT pair we do NOT surface the Nifty number on an
        # "IT sector" row (it would mislabel the short) — its own return is null.
        it_pair = kind == "pair" and view_id == plain_copy.VIEW_IT
        out.append({
            "name": "IT sector (short)" if it_pair else _NIFTY_DISPLAY,
            "symbol": None if it_pair else _NIFTY_SYMBOL,
            "return_pct": (
                None if it_pair
                else (round(avg_bench_pct, 1) if avg_bench_pct is not None else None)
            ),
            "position": "short", "weight_pct": None,
        })
    return out


def _compute_expression(
    engine: Optional[_Engine], view_id: str, kind: str, tier: str,
    cfg: dict[str, Any],
) -> dict[str, Any]:
    """Episode-gated, in-position concatenated curve + detail-page extras for one
    expression. Returns honest empties (with exit_period populated; trust /
    historical_alignment left ``None``) on no data / no engine / unsupported
    view — trust is ONLY ever derived from a real per-occurrence return sample
    (``_trust_from_distribution`` below), never from the cfg-stored
    benchmark-beat blend (``_historical_alignment``, kept for callers that
    still want the belief-design-fit dial from stored backtest evidence)."""
    base = _blank()
    base["exit_period"] = _EXIT_PERIOD.get(view_id)

    members = _members_long(cfg)
    if not members or engine is None:
        return base
    episodes = engine.episodes.get(view_id) or []
    if not episodes:
        return base

    present = [m for m in members if m in engine.rets.columns]
    if not present:
        return base

    # REAL per-tier weights (the substance fix) — genuinely different proportions
    # per tier, computed from the returns matrix, replacing the old {m: 1.0}.
    weights, scheme_used, weight_fallback = engine.tier_weights(present, tier)
    src, weights_or_leg, curve_basis, underlying = engine._leg(
        view_id, kind, present, weights,
    )

    # REAL modelled Black–Scholes payoff for the option tier (max loss/profit/
    # breakeven/POP/greeks/payoff-curve). The historical return stays honestly
    # "priced at deploy" (no offline option price path). None if vol is unestimable.
    option_payload: Optional[dict[str, Any]] = None
    if kind == "option_strategy" and present:
        sigma = option_model.realized_vol_annual(
            engine.rets[present[0]].dropna().tail(_VOL_WINDOW_BARS).values
        )
        if sigma and sigma > 0.9:
            sigma = 0.28  # guard an illiquid/absurd realised vol → stated default
        if sigma:
            # Strike width from the underlying's REAL expected move over the
            # hold, scaled by the tier's risk posture (no fixed 10/7/5%).
            width = option_model.width_for_vol(
                sigma, _HOLD_BARS, mult=_TIER_WIDTH_MULT.get(tier, 0.9),
            )
            option_payload = option_model.model_vertical_spread(
                bullish=_VIEW_BULLISH.get(view_id, True),
                sigma_annual=sigma,
                horizon_days=_HOLD_BARS,
                width_pct=width,
                atm_offset_pct=0.0,
                underlying_label=plain_copy.stock_name(present[0]) or present[0],
            )

    entry_block = _entry_for(engine, view_id, kind, present, weights, option_payload)

    res = _v3e.backtest_exits(
        episodes, src, weights_or_leg, engine.r_nifty,
        modes=("fixed",), hold_bars=_HOLD_BARS,
    )["fixed"]
    n_episodes = int(res["n_episodes"])

    # AVERAGE-OCCURRENCE curve: each past occurrence's in-position cumret path
    # (strategy net of entry cost; Nifty no cost), time-normalised and averaged —
    # NOT compounded across occurrences. Endpoint == the average per-occurrence
    # return (what one deployment can expect), never a stacked >100% ramp.
    strat_paths = _v3e.episode_returns(episodes, src, weights_or_leg)
    bench_paths = _v3e.episode_returns(
        episodes, engine.r_nifty.to_frame("NIFTY"), "NIFTY"
    )
    strat_cum = _episode_cumrets(strat_paths, _v3e.DEFAULT_RT)
    bench_cum = _episode_cumrets(bench_paths, 0.0)
    avg_s, avg_b = _avg_curve(strat_cum, bench_cum)

    L = min(len(avg_s), len(avg_b))
    if L < 2:
        return base

    # x is the in-occurrence trading-day index ("0","1",…) — the path of a single
    # TYPICAL occurrence (occurrences differ in length; they are aligned + averaged).
    curve = [
        {
            "t": str(i),
            "strategy": round((1.0 + avg_s[i]) * BASE_VALUE, 2),
            "benchmark": round((1.0 + avg_b[i]) * BASE_VALUE, 2),
        }
        for i in range(L)
    ]

    # Per-episode segments: strategy (net, episode-gated) vs Nifty over the SAME
    # window, named from the real event dates/seasons.
    meta = engine.episode_meta.get(view_id) or []
    strat_pe = res["per_episode_pct"]
    bench_pe = engine._benchmark_per_episode_pct(episodes)
    episodes_out: list[dict[str, Any]] = []
    positive_episodes = 0
    for i, strat_ret in enumerate(strat_pe):
        ret = round(float(strat_ret), 1)
        bench = round(float(bench_pe[i]), 1) if i < len(bench_pe) else None
        is_pos = ret > 0
        if is_pos:
            positive_episodes += 1
        episodes_out.append({
            "label": meta[i]["label"] if i < len(meta) else f"Episode {i + 1}",
            "date": meta[i]["date"] if i < len(meta) else None,
            "return_pct": ret,
            "benchmark_pct": bench,
            "positive": is_pos,
        })

    # AVERAGE return over the occurrences — the honest headline (mean per-episode,
    # not compounded). Sourced from the engine's per-episode numbers so it stays
    # consistent with the dated per-occurrence list above.
    avg_ret = _mean(strat_pe)
    avg_bench = _mean(bench_pe)
    avg_excess = (
        round(avg_ret - avg_bench, 2)
        if avg_ret is not None and avg_bench is not None
        else None
    )

    holdings = _episode_holdings(
        engine, view_id, kind, present, avg_bench, weights, option_payload,
    )

    # Monte-Carlo terminal-return distribution for a SINGLE occurrence: bootstrap
    # from the full episode-gated daily-return sample, but simulate a path of one
    # average-occurrence length (not the whole concatenated history) so the spread
    # centres on a single deployment, matching the headline.
    horizon = (
        max(1, int(round(len(res["daily_rets"]) / n_episodes)))
        if n_episodes
        else None
    )
    mc = monte_carlo_terminal_distribution(res["daily_rets"], horizon=horizon)
    if mc is not None and curve_basis == "underlying":
        mc["basis"] = "underlying"

    # Risk/return ratio from the average-occurrence curve (reward of a typical
    # occurrence per unit of its drawdown).
    rr: Optional[float] = None
    strat_vals = [pt["strategy"] for pt in curve]
    if avg_ret is not None and len(strat_vals) >= 2:
        dd = _max_drawdown_pct(strat_vals)
        if abs(dd) > 1e-9:
            rr = round(avg_ret / abs(dd), 1)

    # ── trust (contract rule #3) — the expression's OWN positive-outcome
    # frequency + N + median, NEVER beating a benchmark. Option/derivative
    # tiers (rule #4) have no faithful historical option payoff — the curve
    # rides the underlying's own path (``curve_basis == "underlying"``) — so
    # we suppress trust/alignment entirely rather than presenting the
    # underlying stock's own win-rate as if it were the option's.
    is_option_expression = kind == "option_strategy" or curve_basis == "underlying"
    if is_option_expression:
        pct_positive: Optional[float] = None
        n_positive: Optional[int] = None
        trust_verdict: Optional[str] = None
        own_alignment: Optional[dict[str, Any]] = None
        gain_loss = _estats.modelled_option_stats(option_payload)
    else:
        pct_positive = (
            round(positive_episodes / n_episodes * 100.0, 1) if n_episodes else None
        )
        n_positive = positive_episodes if n_episodes else None
        median_ret = _median(strat_pe)
        trust_verdict, own_alignment = _trust_from_distribution(
            n_episodes, pct_positive, median_ret,
        )
        gain_loss = _estats.gain_loss_stats([float(r) for r in strat_pe])

    return {
        **base,
        "equity_curve": curve,
        "holdings": holdings,
        "risk_return_ratio": rr,
        "underlying_symbol": plain_copy.stock_name(underlying) if underlying else None,
        "curve_basis": curve_basis,
        "n_episodes": n_episodes,
        # Single averaged occurrence → no inter-episode stitches to mark.
        "episode_boundaries": [],
        "episodes": episodes_out,
        "positive_episodes": positive_episodes,
        "monte_carlo": mc,
        "avg_episode_return_pct": round(avg_ret, 2) if avg_ret is not None else None,
        "avg_episode_benchmark_pct": (
            round(avg_bench, 2) if avg_bench is not None else None
        ),
        "avg_episode_excess_pct": avg_excess,
        "pct_positive": pct_positive,
        "n_positive": n_positive,
        "trust_verdict": trust_verdict,
        "historical_alignment": own_alignment,
        "weight_scheme": scheme_used,
        "weight_fallback": weight_fallback,
        "option_model": option_payload,
        "entry": entry_block,
        "gain_loss": gain_loss,
    }


def _nfo_lot_size(underlying: str) -> Optional[int]:
    """Best-effort contract lot: the live instrument master first, then the
    dated exchange snapshot (option_universe.json). Honest None when both
    are unavailable."""
    try:
        from backend.database import SessionLocal
        from backend.market.instrument_master import get_lot_size
        db = SessionLocal()
        try:
            lot = get_lot_size(db, underlying.replace(".NS", ""))
            if lot:
                return lot
        finally:
            db.close()
    except Exception:  # noqa: BLE001
        pass
    return _ouniv.lot_for(underlying)


def _entry_for(
    engine: "_Engine", view_id: str, kind: str, present: list[str],
    weights: dict[str, float], option_payload: Optional[dict[str, Any]],
) -> Optional[dict[str, Any]]:
    """The real minimum-entry ticket for a live-view expression (same engine
    as the pack: lite whole-share basket → catalog ETF → honest boundary)."""
    try:
        from backend.view_markets import affordability as _afford
        from backend.view_markets import etf_catalog as _etfcat

        as_of = str(engine.px.index[-1].date()) if len(engine.px.index) else None
        if kind == "option_strategy":
            opt = None
            small = None
            if option_payload and present:
                sym = present[0]
                ser = engine.px[sym].dropna() if sym in engine.px.columns else None
                spot = float(ser.iloc[-1]) if ser is not None and len(ser) else None
                lot = _nfo_lot_size(sym)
                if spot and lot:
                    opt = _afford.option_entry(
                        spot=spot,
                        premium_pct_of_spot=option_payload["net_premium_pct"],
                        lot_size=lot,
                    )
                    # A budget-sized far-OTM long single as the small-ticket
                    # alternative; shrink time when the view-length ticket
                    # can't fit (premium ~ sqrt(T)) and say so.
                    sigma = option_model.realized_vol_annual(
                        engine.rets[sym].dropna().tail(_VOL_WINDOW_BARS)
                    ) if sym in engine.rets.columns else None
                    bullish = (option_payload.get("direction") != "bearish")
                    horizon = int(option_payload.get("horizon_days") or 126)
                    for days in (horizon, 42, 21):
                        small = option_model.affordable_ticket(
                            bullish=bullish, sigma_annual=sigma or 0.28,
                            horizon_days=days, spot=spot, lot=lot,
                            budget_inr=_afford.ENTRY_BUDGET_INR,
                            underlying=plain_copy.stock_name(sym))
                        if small:
                            if days < horizon:
                                small["rolled"] = True
                                small["note"] = (
                                    f"A ~{max(1, round(days / 21))}-month option "
                                    "on a longer view — you would re-buy it each "
                                    "expiry and premiums compound. " + small["note"]
                                )
                            break
            return _afford.entry_block(kind=kind, option=opt,
                                       small_ticket=small, as_of=as_of)
        if kind == "pair":
            return _afford.entry_block(kind="pair")
        prices = {}
        for s in present:
            ser = engine.px[s].dropna() if s in engine.px.columns else None
            if ser is not None and len(ser):
                prices[s] = float(ser.iloc[-1])
        etf_cat = _VIEW_ETF_CATEGORY.get(view_id)
        etf = _etfcat.entry(etf_cat) if etf_cat else None
        block = _afford.entry_block(kind=kind, weights=dict(weights),
                                    prices=prices, etf=etf, as_of=as_of)
        for leg in block.get("legs", []) or []:
            leg["symbol"] = str(leg["symbol"]).replace(".NS", "")
        for d in block.get("dropped", []) or []:
            d["symbol"] = str(d["symbol"]).replace(".NS", "")
        return block
    except Exception:  # noqa: BLE001
        return None


# ── orchestration ─────────────────────────────────────────────────────────────


def compute_all(db) -> dict[str, Any]:
    """Compute the cache for every expression of the three curated views."""
    from backend.models import MarketView, ViewExpression

    engine = _build_engine()
    out: dict[str, Any] = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "base_value": BASE_VALUE,
        "benchmark": "^NSEI",
        "curve_method": "average_occurrence_normalised",
        "engine_available": engine is not None,
        "expressions": {},
        "fundamental_comparison": {},
    }
    summary: list[str] = []
    for vid in VIEW_IDS:
        view = db.query(MarketView).filter(MarketView.id == vid).one_or_none()
        if view is None:
            summary.append(f"view {vid[:8]} MISSING")
            continue
        out["fundamental_comparison"][vid] = _fundamental_comparison(view)
        exprs = (
            db.query(ViewExpression)
            .filter(ViewExpression.view_id == vid)
            .all()
        )
        for e in exprs:
            cfg = e.config if isinstance(e.config, dict) else {}
            kind = _str_enum(e.expression_kind)
            tier = _str_enum(e.tier)
            payload = _compute_expression(engine, vid, kind, tier, cfg)
            out["expressions"][str(e.id)] = payload
            n_pts = len(payload["equity_curve"])
            status = (
                f"{n_pts}pts/{payload['n_episodes']}ep/{len(payload['holdings'])}hold"
                if n_pts
                else "EMPTY (no real data / developing)"
            )
            summary.append(
                f"  {view.title[:18]:18} {_str_enum(e.tier):12} {kind:15} -> {status}"
            )
    out["_summary"] = summary
    return out


def _fundamental_comparison(view) -> Optional[dict[str, Any]]:
    """Basket-avg PE/ROE vs Nifty from the Moneycontrol DB, else ``None``.

    Honest by construction: a basket average is only half the comparison — the
    Nifty benchmark PE/ROE is NOT a single ticker in the Moneycontrol schema, so
    without a real benchmark figure we cannot serve an honest side-by-side.
    We therefore return ``None`` (the FE omits the block) rather than fabricate
    a benchmark. Wire a real index-fundamentals source here to enable it.
    """
    return None


def main() -> None:
    import backend.models  # noqa: F401  (ensure mappers configured)
    from backend.database import SessionLocal

    db = SessionLocal()
    try:
        data = compute_all(db)
    finally:
        db.close()
    summary = data.pop("_summary", [])
    with open(CACHE_PATH, "w") as fh:
        json.dump(data, fh, indent=2)
    print(f"Wrote {CACHE_PATH}")
    print(f"generated_at={data['generated_at']} method={data['curve_method']} "
          f"engine={'OK' if data['engine_available'] else 'UNAVAILABLE'}")
    print("Per-expression:")
    for line in summary:
        print(line)
    n_curves = sum(
        1 for p in data["expressions"].values() if p["equity_curve"]
    )
    print(
        f"\n{n_curves}/{len(data['expressions'])} expressions computed a REAL "
        f"episode-gated curve; the rest are empty honestly (developing / no data)."
    )


# ── request-time loader (cheap) ────────────────────────────────────────────────

_CACHE: dict[str, Any] = {}
_CACHE_MTIME: float = 0.0


def load_precomputed() -> dict[str, Any]:
    """Load (and memoize on mtime) the on-disk cache. Returns {} if absent."""
    global _CACHE, _CACHE_MTIME
    try:
        mtime = os.path.getmtime(CACHE_PATH)
    except OSError:
        return {}
    if not _CACHE or mtime != _CACHE_MTIME:
        try:
            with open(CACHE_PATH) as fh:
                _CACHE = json.load(fh)
            _CACHE_MTIME = mtime
        except (OSError, ValueError):
            return {}
    return _CACHE


def expression_precompute(expression_id: str) -> dict[str, Any]:
    """Per-expression cached payload, or honest empties when absent."""
    cache = load_precomputed()
    exprs = cache.get("expressions") if isinstance(cache, dict) else None
    if isinstance(exprs, dict):
        hit = exprs.get(str(expression_id))
        if isinstance(hit, dict):
            return hit
    return _blank()


def fundamental_comparison(view_id: str) -> Optional[dict[str, Any]]:
    cache = load_precomputed()
    fc = cache.get("fundamental_comparison") if isinstance(cache, dict) else None
    if isinstance(fc, dict):
        return fc.get(str(view_id))
    return None


if __name__ == "__main__":
    main()
