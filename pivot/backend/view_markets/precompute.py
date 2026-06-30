"""Precompute REAL, cached chart/metric data for the curated Views.

The Views FE needs a real line chart (strategy vs Nifty), a per-holding returns
table/heatmap, a risk/return number, and a gallery mini line — all from REAL
computed prices, never fabricated. Computing curves on every request is slow, so
we precompute ONCE into an on-disk JSON cache (:data:`CACHE_PATH`) and the router
loads it cheaply per request.

EPISODE-GATED methodology (the methodology fix)
-----------------------------------------------
Every headline return on a View card (monsoon +45.5%, IT +48.8%, …) is
**episode-gated**: the strategy is only in the market during the specific
event/season windows the belief is about, not continuously for five years. The
old curve here was a *continuous* 5-year buy-and-hold, so its endpoint did NOT
equal the stored headline. We now build the SAME concatenated, in-position
equity curve the headline came from, by reusing the v3 research engine:

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
    where ``t`` is the SEQUENTIAL in-position trading-day index (calendar time has
    gaps between episodes, so a date axis would lie). Both legs are rebased to a
    ₹1,00,000 base. The strategy leg depends on the expression kind:
      - basket / multi_asset → equal-weight ``members_long`` (the headline basket).
      - pair                 → IT: long basket − IT_f factor; Monsoon: the
                               0.5·long − 0.5·Nifty dollar-neutral spread (the v3
                               leg, faithfully reconstructed).
      - hedge                → long basket − Nifty (market-neutral).
      - option_strategy      → the single underlying's own in-position path
                               (``curve_basis="underlying"`` — honest; no live
                               option chain to reconstruct a payoff from).
  * ``n_episodes`` / ``episode_boundaries`` — the count and the in-position
    indices where each new episode starts (so the FE can mark the stitches).
  * ``curve_basis`` — ``"in_position_episodes"`` (or ``"underlying"``).
  * ``holdings`` — per-holding REAL in-position total return over the episodes.
  * ``risk_return_ratio`` — ``total_return_pct / abs(max_drawdown_pct)`` (1 dp).

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
from backend.services.backtest.validation.monte_carlo import (
    monte_carlo_terminal_distribution,
)
from backend.view_markets import confidence, plain_copy

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

    def _leg(self, view_id: str, kind: str, present: list[str]):
        """Return (src_df, weights_or_leg, curve_basis, underlying_symbol) for the
        in-position curve, reconstructing the EXACT v3 leg for this kind/view.
        ``present`` are member tickers that exist in the matrix."""
        if kind in ("basket", "multi_asset"):
            return self.rets, {m: 1.0 for m in present}, "in_position_episodes", None

        if kind == "hedge":
            src = self.rets.copy()
            src["__LEG__"] = (self._long_ew(present) - self.r_nifty).reindex(src.index)
            return src, "__LEG__", "in_position_episodes", None

        if kind == "pair":
            src = self.rets.copy()
            if view_id == plain_copy.VIEW_IT:
                # IT balanced: long basket / short IT_f (rotation-neutral).
                src["__LEG__"] = (self._long_ew(present) - self.it_f).reindex(src.index)
            else:
                # Monsoon balanced: 0.5·long − 0.5·Nifty dollar-neutral pair
                # (mirror monsoon_v3 exactly, incl. its fillna(0) Nifty handling).
                src["NIFTY"] = self.r_nifty.reindex(self.rets.index)
                long_leg = _v3e._port_daily(src, {m: 1.0 for m in present})
                src["__LEG__"] = 0.5 * long_leg - 0.5 * src["NIFTY"].fillna(0.0)
            return src, "__LEG__", "in_position_episodes", None

        if kind == "option_strategy":
            # No faithful historical option payoff path exists offline → the
            # HONEST fallback is the single underlying's own in-position path.
            return self.rets, present[0], "underlying", present[0]

        # Unknown kind → treat as a long basket (never fabricate).
        return self.rets, {m: 1.0 for m in present}, "in_position_episodes", None

    def _benchmark_equity(self, episodes) -> list[float]:
        """Nifty buy-hold concatenated over the SAME episodes (no cost — the
        do-nothing yardstick), starting at 1.0."""
        paths = _v3e.episode_returns(episodes, self.r_nifty.to_frame("NIFTY"), "NIFTY")
        eq = [1.0]
        for p in paths:
            for r in p.fillna(0.0).values:
                r = float(r) if np.isfinite(r) else 0.0
                eq.append(eq[-1] * (1.0 + r))
        return eq

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
        """Per-member REAL in-position total return (gross, no cost) over the
        concatenated episodes — 'how each holding did on the days in market'."""
        out: list[tuple[str, float]] = []
        for m in present:
            paths = _v3e.episode_returns(episodes, self.rets, m)
            eq = 1.0
            for p in paths:
                for r in p.fillna(0.0).values:
                    eq *= (1.0 + (float(r) if np.isfinite(r) else 0.0))
            out.append((m, (eq - 1.0) * 100.0))
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
    engine: _Engine, view_id: str, kind: str, present: list[str], bench_eq: list[float],
) -> list[dict[str, Any]]:
    """Per-holding rows with position + weight + REAL in-position return.

    basket/multi_asset → each long member equal-weighted (100/n). pair/hedge →
    the long members (weight null) plus ONE 'Nifty 50' row, position short (the
    short leg is the index, never a per-stock short). option_strategy → the
    illustrative legs as long/short holdings (no fabricated per-leg return)."""
    if kind == "option_strategy":
        legs = plain_copy._build_option_legs("bull_call_spread") or []
        leg_sym = canonical_symbol(present[0]) if present else None
        out: list[dict[str, Any]] = []
        for leg in legs:
            action = str(leg.get("action", "")).strip()
            pos = "long" if action.upper() == "BUY" else "short"
            rule = leg.get("strike_rule")
            name = f"{action.title()} {leg.get('option_type', '')}".strip()
            if rule:
                name = f"{name} ({rule})"
            out.append({
                "name": name, "symbol": leg_sym,
                "return_pct": None, "position": pos, "weight_pct": None,
            })
        return out

    n = len(present)
    ew_weight = round(100.0 / n, 1) if (kind in ("basket", "multi_asset") and n) else None
    out = []
    for sym, ret in engine._member_holdings(engine.episodes.get(view_id) or [], present):
        out.append({
            "name": plain_copy.stock_name(sym) or sym,
            "symbol": canonical_symbol(sym),
            "return_pct": round(ret, 1),
            "position": "long",
            "weight_pct": ew_weight,
        })
    if kind in ("pair", "hedge"):
        nifty_total = (
            round((bench_eq[-1] / bench_eq[0] - 1.0) * 100.0, 1)
            if len(bench_eq) >= 2 and bench_eq[0] > 0
            else None
        )
        out.append({
            "name": _NIFTY_DISPLAY, "symbol": _NIFTY_SYMBOL,
            "return_pct": nifty_total, "position": "short", "weight_pct": None,
        })
    return out


def _compute_expression(
    engine: Optional[_Engine], view_id: str, kind: str, cfg: dict[str, Any],
) -> dict[str, Any]:
    """Episode-gated, in-position concatenated curve + detail-page extras for one
    expression. Returns honest empties (with exit_period + historical_alignment
    still populated) on no data / no engine / unsupported view."""
    base = _blank()
    base["exit_period"] = _EXIT_PERIOD.get(view_id)
    base["historical_alignment"] = _historical_alignment(cfg)

    members = _members_long(cfg)
    if not members or engine is None:
        return base
    episodes = engine.episodes.get(view_id) or []
    if not episodes:
        return base

    present = [m for m in members if m in engine.rets.columns]
    if not present:
        return base

    src, weights_or_leg, curve_basis, underlying = engine._leg(view_id, kind, present)

    res = _v3e.backtest_exits(
        episodes, src, weights_or_leg, engine.r_nifty,
        modes=("fixed",), hold_bars=_HOLD_BARS,
    )["fixed"]
    strat_eq = res["equity"]                       # concatenated, starts at 1.0
    n_episodes = int(res["n_episodes"])
    bench_eq = engine._benchmark_equity(episodes)  # same episodes, no cost

    L = min(len(strat_eq), len(bench_eq))
    if L < 2:
        return base

    # Sequential in-position trading-day index ("0","1",…) — calendar time has
    # gaps between episodes so a date axis would misrepresent the path.
    curve = [
        {
            "t": str(i),
            "strategy": round(strat_eq[i] * BASE_VALUE, 2),
            "benchmark": round(bench_eq[i] * BASE_VALUE, 2),
        }
        for i in range(L)
    ]

    # Episode boundaries = the in-position index where each episode's first bar
    # lands (index 0 is the pre-first-episode base point).
    boundaries: list[int] = []
    acc = 1
    for p in _v3e.episode_returns(episodes, src, weights_or_leg):
        if acc < L:
            boundaries.append(acc)
        acc += len(p)

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

    holdings = _episode_holdings(engine, view_id, kind, present, bench_eq)

    # Monte-Carlo terminal-return distribution over the episode-gated daily
    # returns (REUSE the block-bootstrap engine). For an option the daily path
    # is the underlying's, so the distribution is the underlying's — labelled.
    mc = monte_carlo_terminal_distribution(res["daily_rets"])
    if mc is not None and curve_basis == "underlying":
        mc["basis"] = "underlying"

    # Risk/return ratio from the strategy curve itself.
    rr: Optional[float] = None
    strat_vals = [pt["strategy"] for pt in curve]
    if len(strat_vals) >= 2 and strat_vals[0] > 0:
        total = (strat_vals[-1] / strat_vals[0] - 1.0) * 100.0
        dd = _max_drawdown_pct(strat_vals)
        if abs(dd) > 1e-9:
            rr = round(total / abs(dd), 1)

    return {
        **base,
        "equity_curve": curve,
        "holdings": holdings,
        "risk_return_ratio": rr,
        "underlying_symbol": plain_copy.stock_name(underlying) if underlying else None,
        "curve_basis": curve_basis,
        "n_episodes": n_episodes,
        "episode_boundaries": boundaries,
        "episodes": episodes_out,
        "positive_episodes": positive_episodes,
        "monte_carlo": mc,
    }


# ── orchestration ─────────────────────────────────────────────────────────────


def compute_all(db) -> dict[str, Any]:
    """Compute the cache for every expression of the three curated views."""
    from backend.models import MarketView, ViewExpression

    engine = _build_engine()
    out: dict[str, Any] = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "base_value": BASE_VALUE,
        "benchmark": "^NSEI",
        "curve_method": "episode_gated_in_position_concatenated",
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
            payload = _compute_expression(engine, vid, kind, cfg)
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
