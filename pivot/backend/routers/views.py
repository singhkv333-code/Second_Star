"""View Markets — /api/views REST router (V2 belief OS).

Read-mostly surface for the "Views" FE tab: global, curated content (no
per-user filtering on reads), with per-user "follow" + per-user
"deploy" (calls register-not-execute via :mod:`view_markets.deployment`).

Every endpoint is flag-gated on ``settings.view_markets_enabled`` — when the
flag is OFF the router answers 404 with the canonical error envelope.

Shape contract: see the task spec / Markdowns/Version2.md. The FE mirrors
the Pydantic models here verbatim. Tolerant of missing keys in
``ViewExpression.config`` (no fabrication: missing scores -> ``scores:None``).
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Optional

from fastapi import APIRouter, Depends, Header
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import func
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from backend.auth.jwt_handler import get_user_id_from_token
from backend.cache import redis_client
from backend.config import settings
from backend.database import get_db
from backend.models import (
    ConfidenceDimension,
    MarketView,
    ViewConfidence,
    ViewExpectation,
    ViewExpression,
    ViewFollow,
    ViewPosition,
    ViewPositionStatus,
    ViewStatus,
    ViewTransmission,
    ViewType,
    Workflow,
)
from backend.routers._errors import http_error, not_found, state_conflict, validation_error
from backend.posthog_client import get_posthog
from backend.routers.orders import _persist_leg
from backend.brokers.sessions import get_active_broker_session
from backend.paper.routing import should_use_paper
from backend.utils.time_utils import format_ist
from backend.view_markets import plain_copy, positions as positions_svc, precompute
from backend.view_markets.deployment.compare import compare_tiers
from backend.view_markets.deployment.deploy import deploy_expression
from backend.view_markets.deployment.backtest import backtest_expression


logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["Views"])

# ── response cache (curated content — global, not per-user) ─────────────────
#
# _build_summary() does ~6 sequential DB round-trips PER view (confidence
# blocks, 3-4 summary counts, a separate ViewExpression query) with no
# batching, so a cold GET /api/views walking N curated views is N*6 round
# trips. Views change on a curation cadence (minutes/hours), not per-request,
# so a short TTL response cache removes essentially all of that cost for the
# overwhelming majority of requests without ever serving meaningfully stale
# content. Deliberately keyed WITHOUT user_id: reads are documented as global
# curated content, and `is_following`/`follower_count` staleness is bounded
# by the TTL (a follow toggling mid-window is an acceptable, self-healing
# trade-off for a 30-45s window — see CLAUDE.md's cache conventions).
_LIST_CACHE_TTL_S = 45
_LIST_CACHE_PREFIX = "views:list:v1:"
_DETAIL_CACHE_TTL_S = 45
_DETAIL_CACHE_PREFIX = "views:detail:v1:"


def _cache_get_model(cache_key: str, model: type[BaseModel]) -> Optional[BaseModel]:
    """Best-effort cache read; any failure (unreachable Redis, stale/bad
    shape from a prior deploy) is treated as a miss — caching must never be
    able to break or corrupt a response."""
    try:
        raw = redis_client.get(cache_key)
    except Exception:  # noqa: BLE001
        logger.debug("[views] cache read failed key=%s", cache_key, exc_info=True)
        return None
    if not raw:
        return None
    try:
        return model.model_validate_json(raw)
    except (ValidationError, ValueError):
        logger.debug("[views] cache hit with stale/invalid shape key=%s", cache_key)
        return None


def _cache_set_model(cache_key: str, value: BaseModel, ttl_s: int) -> None:
    try:
        redis_client.set(cache_key, value.model_dump_json(), ex=ttl_s)
    except Exception:  # noqa: BLE001
        logger.debug("[views] cache write failed key=%s", cache_key, exc_info=True)


# ── flag gate ───────────────────────────────────────────────────────────────


def _require_flag() -> None:
    """Raise canonical 404 when the View Markets layer is disabled."""
    if not getattr(settings, "view_markets_enabled", False):
        raise not_found("view markets not enabled")


# ── optional auth (reads ungated; follow/deploy best-effort) ────────────────


def _optional_user_id(authorization: Optional[str] = Header(default=None)) -> Optional[int]:
    """Resolve the bearer token to a user_id when present, else ``None``.

    Reads are GLOBAL (curated content). Follow + deploy use the user id when
    available; absent in dev/tests we mirror paper.py's user=1 fallback so the
    FE works without a login flow."""
    if not authorization:
        if getattr(settings, "app_env", "development") == "development":
            return 1
        return None
    uid = get_user_id_from_token(authorization.replace("Bearer ", "", 1))
    if uid:
        return int(uid)
    if getattr(settings, "app_env", "development") == "development":
        return 1
    return None


# ── letter band (V2 spec bands; intentionally distinct from confidence.letter_band) ──


def _letter_band(score: Optional[int]) -> Optional[str]:
    """API-contract letter bands (A>=85, B>=70, C>=55, D>=40, else F).

    Note: distinct from ``view_markets.confidence.letter_band`` which uses
    the internal Trust-ladder bands (A>=80…E); the API contract uses a
    coarser five-band scheme for the FE dial."""
    if score is None:
        return None
    if score >= 85:
        return "A"
    if score >= 70:
        return "B"
    if score >= 55:
        return "C"
    if score >= 40:
        return "D"
    return "F"


# ── Pydantic v2 response models ─────────────────────────────────────────────


class CurvePoint(BaseModel):
    t: str
    strategy: float
    benchmark: float


class Holding(BaseModel):
    name: str
    symbol: str
    # Real in-position return for basket members / the Nifty short leg; ``None``
    # for option legs (no faithful historical per-leg payoff).
    return_pct: Optional[float] = None
    # "long" for basket members; "short" for the pair's Nifty leg; long/short
    # per option leg.
    position: Optional[str] = None
    # Equal-weight 100/n for a basket; ``None`` when not applicable (pair legs,
    # the Nifty short, option legs).
    weight_pct: Optional[float] = None
    # Display metadata (multi-asset): real company/coin name is in `name`;
    # these add the logo + asset class + pricing currency so the card can
    # render a logo and an INR/USD-aware badge. Null logo → FE monogram.
    logo_url: Optional[str] = None
    asset_class: Optional[str] = None   # in_equity|in_etf|us_equity|us_etf|crypto
    currency: Optional[str] = None      # INR | USD


class Episode(BaseModel):
    """One past occurrence of the event/season the expression is about."""

    label: str
    date: str
    return_pct: Optional[float] = None
    benchmark_pct: Optional[float] = None
    positive: bool = False


class MonteCarlo(BaseModel):
    """Block-bootstrap distribution of simulated TERMINAL return %."""

    n_sims: int
    terminal_pct: list[float] = Field(default_factory=list)
    p05: Optional[float] = None
    p25: Optional[float] = None
    median: Optional[float] = None
    p75: Optional[float] = None
    p95: Optional[float] = None
    prob_loss: Optional[float] = None
    # "underlying" when the curve (and thus the MC) is on the option underlying.
    basis: Optional[str] = None


class OptionLeg(BaseModel):
    action: str
    option_type: str
    strike_rule: Optional[str] = None
    delta: Optional[float] = None
    strike_offset: Optional[int] = None


class SimilarView(BaseModel):
    id: str
    short_title: Optional[str] = None


class FundamentalSide(BaseModel):
    pe: Optional[float] = None
    roe: Optional[float] = None


class FundamentalComparison(BaseModel):
    basket: FundamentalSide = Field(default_factory=FundamentalSide)
    nifty: FundamentalSide = Field(default_factory=FundamentalSide)


class ConfidenceBlock(BaseModel):
    score: Optional[int] = None
    letter: Optional[str] = None


class ConfidenceBlockWithEvidence(ConfidenceBlock):
    evidence: Optional[str] = None


class BestExpression(BaseModel):
    id: str
    tier: str
    expression_kind: str
    grade: Optional[str] = None
    trust_verdict: Optional[str] = None
    total_return_pct: Optional[float] = None
    excess_return_pct: Optional[float] = None
    # ── layman layer ──
    plain_label: Optional[str] = None
    nifty_total_pct: Optional[float] = None
    n_episodes: Optional[int] = None
    pct_episodes_beat: Optional[float] = None
    worst_drop_pct: Optional[float] = None
    # Positive-outcome frequency over the past occurrences — the ONLY trust
    # basis shown on screen (never benchmark-beating). Real-or-null.
    pct_positive: Optional[float] = None
    n_positive: Optional[int] = None
    # ── real computed chart (for the gallery mini line) ──
    equity_curve: list[CurvePoint] = Field(default_factory=list)


class ViewSummary(BaseModel):
    id: str
    view_type: str
    title: str
    thesis: Optional[str] = None
    category: Optional[str] = None
    time_horizon: Optional[str] = None
    status: str
    resolution_date: Optional[datetime] = None
    created_at: datetime
    published_at: Optional[datetime] = None
    outcome_confidence: ConfidenceBlock = Field(default_factory=ConfidenceBlock)
    expression_confidence: ConfidenceBlock = Field(default_factory=ConfidenceBlock)
    best_expression: Optional[BestExpression] = None
    expression_count: int = 0
    transmission_count: int = 0
    follower_count: int = 0
    is_following: bool = False
    # True when there is NO finished/headline basket yet (a "developing" idea).
    # Single source of truth shared by the gallery card and the detail page so
    # the two surfaces never contradict each other (e.g. card says "no finished
    # basket" while the detail shows full numbers as if it were live).
    is_developing: bool = False
    # ── layman layer ──
    plain_one_liner: Optional[str] = None
    plain_summary: Optional[str] = None
    # 7-8-word Polymarket-style headline (curated, else the raw title).
    short_title: Optional[str] = None
    # Calm YES/NO presentation-only reading, hoisted onto the SUMMARY so the
    # gallery card can render its two-button (Yes/No) affordance without a
    # detail fetch. ``None`` today on the live path (no curated stance source
    # wired yet — never fabricate); the /view-pack demo JSON carries the real
    # copy. Forward reference: the Stance model is defined further down, so
    # ViewSummary is model_rebuild()'d after it. Same field/type as ViewDetail —
    # the two surfaces stay in lock-step once a live source is wired.
    stance: Optional["Stance"] = None
    # The single BEST past occurrence of the headline strategy (return % + label)
    # — the most striking-yet-honest number for the card, always shown alongside
    # the typical return. None when there's no per-occurrence sample.
    best_episode_pct: Optional[float] = None
    best_episode_label: Optional[str] = None


class TransmissionEdge(BaseModel):
    seq: int
    from_node: str
    to_node: str
    edge_label: Optional[str] = None
    strength: Optional[float] = None
    evidence: Optional[str] = None
    # ── layman layer ──
    from_label: Optional[str] = None
    to_label: Optional[str] = None
    strength_label: Optional[str] = None
    plain_evidence: Optional[str] = None


class ExpectationRow(BaseModel):
    source: str
    market_id: Optional[str] = None
    expected_value: Optional[float] = None
    user_view_value: Optional[float] = None
    surprise_sign: Optional[str] = None
    as_of: Optional[datetime] = None
    resolved_value: Optional[float] = None
    # ── layman layer ──
    source_label: Optional[str] = None


class DetailConfidence(BaseModel):
    outcome: ConfidenceBlockWithEvidence = Field(
        default_factory=ConfidenceBlockWithEvidence
    )
    expression: ConfidenceBlockWithEvidence = Field(
        default_factory=ConfidenceBlockWithEvidence
    )


class ExpressionDetail(BaseModel):
    id: str
    tier: str
    expression_kind: str
    label: Optional[str] = None
    rationale: Optional[str] = None
    risk_profile: Optional[str] = None
    capital_intensity: Optional[str] = None
    historical_strength: Optional[str] = None
    time_horizon: Optional[str] = None
    workflow_id: Optional[str] = None
    backtest_run_id: Optional[str] = None
    instruments: list[Any] = Field(default_factory=list)
    warnings: list[Any] = Field(default_factory=list)
    disclaimer: Optional[str] = None
    structure: dict[str, Any] = Field(default_factory=dict)
    scores: Optional[dict[str, Any]] = None
    is_deployable: bool = False
    # ── layman layer ──
    plain_label: Optional[str] = None
    plain_one_liner: Optional[str] = None
    plain_why: Optional[str] = None
    plain_risk: Optional[str] = None
    capital_label: Optional[str] = None
    trust_badge: Optional[str] = None
    members: list[str] = Field(default_factory=list)
    n_names: Optional[int] = None
    strategy_total_pct: Optional[float] = None
    nifty_total_pct: Optional[float] = None
    excess_return_pct: Optional[float] = None
    n_episodes: Optional[int] = None
    pct_episodes_beat: Optional[float] = None
    worst_drop_pct: Optional[float] = None
    # Positive-outcome frequency over the past occurrences (contract rule #3 —
    # the ONLY basis for trust, never benchmark-beating). ``None`` for
    # option/derivative tiers (rule #4 — no real historical option payoff
    # exists to compute a positive-outcome frequency from).
    pct_positive: Optional[float] = None
    n_positive: Optional[int] = None
    # ── honest strategy identity ──
    strategy_name: Optional[str] = None
    strategy_type: Optional[str] = None
    option_legs: Optional[list[OptionLeg]] = None
    option_legs_note: Optional[str] = None
    # ── real computed chart + per-holding returns ──
    equity_curve: list[CurvePoint] = Field(default_factory=list)
    holdings: list[Holding] = Field(default_factory=list)
    underlying_symbol: Optional[str] = None
    curve_basis: Optional[str] = None
    risk_return_ratio: Optional[float] = None
    # Episode-gated curve metadata: the x-axis is a sequential in-position
    # trading-day index, and these are the indices where each new episode starts.
    curve_n_episodes: Optional[int] = None
    episode_boundaries: list[int] = Field(default_factory=list)
    # ── round-3 detail-page data (real-or-empty / null) ──
    # Past occurrences of the event/season + how the strategy did each time.
    episodes: list[Episode] = Field(default_factory=list)
    positive_episodes: Optional[int] = None
    # Plain hold-rule string ("Held through the Jun–Aug monsoon window …").
    exit_period: Optional[str] = None
    # Per-EXPRESSION alignment dial (NOT the view-level one); null = suppressed.
    historical_alignment: Optional[ConfidenceBlock] = None
    # "Thousands of simulations" terminal-return distribution; null when N/A.
    monte_carlo: Optional[MonteCarlo] = None
    # REAL per-tier weighting scheme actually used (min_variance / risk_parity /
    # factor / equal) — the substance behind genuinely different tiers.
    weight_scheme: Optional[str] = None
    # REAL modelled Black–Scholes payoff for the option tier (max loss/profit/
    # breakeven/POP/greeks/payoff-curve). Passthrough of the computed dict; null
    # for non-option kinds.
    option_model: Optional[dict[str, Any]] = None
    # Real minimum-entry ticket (lite whole-share basket / catalog ETF route /
    # option premium × lot / honest boundary). Passthrough; null when unbuilt.
    entry: Optional[dict[str, Any]] = None
    # The four comparable metrics — avg gain / avg loss / max gain / max loss —
    # from the same per-occurrence distribution (modelled analogue for option
    # tiers, basis-labelled). Passthrough; null when there is no history.
    gain_loss: Optional[dict[str, Any]] = None


class StanceSide(BaseModel):
    """One side of the calm, presentation-only YES/NO stance reading.

    A READING device only — never a wager, odds, or a clickable contract."""

    verdict: str
    summary: str


class StanceNoSide(StanceSide):
    # False -> the honest "no clean trade" treatment (asymmetric views), never
    # rendered as a failure.
    has_trade: bool = True


class Stance(BaseModel):
    yes: StanceSide
    no: StanceNoSide


# ViewSummary references Stance via a forward reference (Stance is defined
# after it, since ViewDetail extends ViewSummary). Resolve it now that Stance
# exists so the `stance` field validates.
ViewSummary.model_rebuild()


class ViewDetail(ViewSummary):
    transmission: list[TransmissionEdge] = Field(default_factory=list)
    confidence: DetailConfidence = Field(default_factory=DetailConfidence)
    expectations: list[ExpectationRow] = Field(default_factory=list)
    expressions: list[ExpressionDetail] = Field(default_factory=list)
    # ── layman layer ──
    plain_thesis: Optional[str] = None
    benchmark_label: Optional[str] = None
    description: Optional[str] = None
    bullets: list[str] = Field(default_factory=list)
    similar_views: list[SimilarView] = Field(default_factory=list)
    fundamental_comparison: Optional[FundamentalComparison] = None
    # Calm YES/NO presentation-only reading (contract §B). ``None`` on today's
    # live path — no curated stance data source is wired yet for the curated
    # views (never fabricate); the static /view-pack demo JSON carries the
    # real curated stance copy. The FIELD exists so the FE type is satisfied.
    stance: Optional[Stance] = None


class ListResponse(BaseModel):
    items: list[ViewSummary]


class DeployResponse(BaseModel):
    workflow_id: str
    status: str
    steps_count: int
    activated: bool


class FollowResponse(BaseModel):
    is_following: bool
    follower_count: int


# ── projection helpers ─────────────────────────────────────────────────────


_TIER_ORDER = {"conservative": 0, "balanced": 1, "aggressive": 2}

_DEPLOYABLE_KINDS = {"basket", "multi_asset", "pair", "option_strategy", "hedge"}


def _str_enum(val: Any) -> str:
    return str(getattr(val, "value", val)) if val is not None else ""


def _expression_score(cfg: dict[str, Any]) -> Optional[float]:
    """Pull a numeric expression_score from config.scores.backtest."""
    scores = (cfg or {}).get("scores") or {}
    bt = scores.get("backtest") or {}
    val = bt.get("expression_score")
    if isinstance(val, (int, float)):
        return float(val)
    return None


def _verdict(cfg: dict[str, Any]) -> Optional[str]:
    scores = (cfg or {}).get("scores") or {}
    bt = scores.get("backtest") or {}
    val = bt.get("trust_verdict")
    return str(val) if val else None


def _as_float(v: Any) -> Optional[float]:
    if isinstance(v, (int, float)):
        return float(v)
    return None


def _as_int(v: Any) -> Optional[int]:
    if isinstance(v, bool):
        return None
    if isinstance(v, int):
        return v
    if isinstance(v, float):
        return int(round(v))
    return None


def _clean_numbers(cfg: dict[str, Any]) -> dict[str, Any]:
    """Whitelisted, layman-safe numbers from scores.backtest.

    Prefers ``backtest.nifty_comparison`` when present (the dedicated
    benchmark-over-matched-windows block); falls back to the flat backtest
    fields. ``worst_drop_pct`` is max drawdown with its negative sign kept.
    Missing -> ``None`` (never invented).
    """
    bt = (cfg or {}).get("scores") or {}
    bt = (bt.get("backtest") or {}) if isinstance(bt, dict) else {}
    nc = bt.get("nifty_comparison") if isinstance(bt.get("nifty_comparison"), dict) else {}

    def pick(nc_key: str, bt_key: str) -> Any:
        if nc and nc.get(nc_key) is not None:
            return nc.get(nc_key)
        return bt.get(bt_key)

    return {
        "strategy_total_pct": _as_float(pick("strategy_total_pct", "total_return_pct")),
        "nifty_total_pct": _as_float(pick("nifty_total_pct", "nifty_total_pct")),
        "excess_return_pct": _as_float(pick("excess_pct", "excess_return_pct")),
        "n_episodes": _as_int(pick("n_episodes", "n_episodes")),
        "pct_episodes_beat": _as_float(pick("pct_episodes_beat", "pct_episodes_beat")),
        "worst_drop_pct": _as_float(bt.get("max_dd_pct")),
    }


def _headline_numbers(pre: dict[str, Any], cfg: dict[str, Any]) -> dict[str, Any]:
    """Layman-safe headline numbers, preferring the AVERAGE-per-occurrence figures
    from the precompute cache.

    An expression is deployed ONCE PER OCCURRENCE of its event, so the honest
    headline is the average return over the event's past occurrences — NOT the
    return compounded across all of them (which overstates a single deployment,
    e.g. four ~20% monsoon seasons stacking to a misleading +109%). Falls back to
    the stored backtest fields when no precompute is available (uncached / dev)."""
    nums = _clean_numbers(cfg)
    avg_s = _as_float((pre or {}).get("avg_episode_return_pct"))
    if avg_s is not None:
        nums["strategy_total_pct"] = avg_s
        avg_b = _as_float(pre.get("avg_episode_benchmark_pct"))
        if avg_b is not None:
            nums["nifty_total_pct"] = avg_b
        avg_x = _as_float(pre.get("avg_episode_excess_pct"))
        if avg_x is not None:
            nums["excess_return_pct"] = avg_x
        # The occurrence count drives the "Avg over N occurrences" / win-rate copy
        # — take it from the same place the average came from so they never drift.
        n_ep = _as_int(pre.get("n_episodes"))
        if n_ep:
            nums["n_episodes"] = n_ep
    return nums


_PRICED_AT_DEPLOY_BADGE = "Priced at deploy"


def _not_backtested(
    kind: str, pre: dict[str, Any], option_legs: Optional[Any] = None,
) -> bool:
    """Rule #4 — an expression has NO faithful historical backtest when it is
    an option/derivative structure: there is no offline option chain, so any
    curve rides the underlying's own price path
    (``curve_basis == "underlying"``), never a real option payoff. Trust /
    alignment / max-drop must never be shown as a real backtested figure for
    these — "Priced at deploy", not a fabricated number sitting in the same
    column as a genuinely backtested one."""
    curve_basis = (pre or {}).get("curve_basis")
    return kind == "option_strategy" or curve_basis == "underlying" or bool(option_legs)


def _effective_trust_verdict(
    pre: dict[str, Any], cfg_verdict: Optional[str],
) -> Optional[str]:
    """Prefer the precompute's OWN-return-distribution verdict (contract rule
    #3 — positive-outcome frequency + N + median) over the stored/offline
    verdict, whenever a real per-occurrence sample was available to derive
    it. Falls back to the stored cfg verdict when precompute hasn't run
    (uncached / dev) so nothing regresses."""
    own = (pre or {}).get("trust_verdict")
    return str(own) if own else cfg_verdict


def _stance_for_view(view: MarketView) -> Optional[Stance]:
    """Calm YES/NO presentation-only reading (contract §B). ``None`` today on
    the live path — no curated stance data source is wired yet for the
    curated views (never fabricate a stance); the static /view-pack demo JSON
    carries the real curated stance copy. Wiring a live source here (e.g. a
    ``market_views.stance`` column) is a follow-up, not a live-path
    regression — the schema FIELD already exists so the FE type is
    satisfied."""
    return None


def _curve_points(pre: dict[str, Any]) -> list[CurvePoint]:
    """Coerce a precomputed equity_curve into CurvePoint models (real-or-empty)."""
    raw = pre.get("equity_curve") if isinstance(pre, dict) else None
    if not isinstance(raw, list):
        return []
    out: list[CurvePoint] = []
    for p in raw:
        if not isinstance(p, dict):
            continue
        t = p.get("t")
        s = _as_float(p.get("strategy"))
        b = _as_float(p.get("benchmark"))
        if t and s is not None and b is not None:
            out.append(CurvePoint(t=str(t), strategy=s, benchmark=b))
    return out


def _holdings(pre: dict[str, Any]) -> list[Holding]:
    """Coerce precomputed holdings into Holding models (real-or-empty).

    ``return_pct`` may be ``None`` (option legs); position/weight pass through.
    """
    raw = pre.get("holdings") if isinstance(pre, dict) else None
    if not isinstance(raw, list):
        return []
    out: list[Holding] = []
    for h in raw:
        if not isinstance(h, dict):
            continue
        name, sym = h.get("name"), h.get("symbol")
        if not (name and sym):
            continue
        pos = h.get("position")
        out.append(Holding(
            name=str(name),
            symbol=str(sym),
            return_pct=_as_float(h.get("return_pct")),
            position=str(pos) if pos else None,
            weight_pct=_as_float(h.get("weight_pct")),
            logo_url=(str(h["logo_url"]) if h.get("logo_url") else None),
            asset_class=(str(h["asset_class"]) if h.get("asset_class") else None),
            currency=(str(h["currency"]) if h.get("currency") else None),
        ))
    return out


def _episodes(pre: dict[str, Any]) -> list[Episode]:
    """Coerce precomputed per-episode segments into Episode models."""
    raw = pre.get("episodes") if isinstance(pre, dict) else None
    if not isinstance(raw, list):
        return []
    out: list[Episode] = []
    for ep in raw:
        if not isinstance(ep, dict):
            continue
        label, date = ep.get("label"), ep.get("date")
        if not (label and date):
            continue
        out.append(Episode(
            label=str(label),
            date=str(date),
            return_pct=_as_float(ep.get("return_pct")),
            benchmark_pct=_as_float(ep.get("benchmark_pct")),
            positive=bool(ep.get("positive")),
        ))
    return out


def _monte_carlo(pre: dict[str, Any]) -> Optional[MonteCarlo]:
    """Coerce a precomputed Monte-Carlo block into a MonteCarlo model (or None)."""
    raw = pre.get("monte_carlo") if isinstance(pre, dict) else None
    if not isinstance(raw, dict):
        return None
    n_sims = _as_int(raw.get("n_sims"))
    if n_sims is None:
        return None
    terminal = [
        v for v in (_as_float(x) for x in (raw.get("terminal_pct") or []))
        if v is not None
    ]
    basis = raw.get("basis")
    return MonteCarlo(
        n_sims=n_sims,
        terminal_pct=terminal,
        p05=_as_float(raw.get("p05")),
        p25=_as_float(raw.get("p25")),
        median=_as_float(raw.get("median")),
        p75=_as_float(raw.get("p75")),
        p95=_as_float(raw.get("p95")),
        prob_loss=_as_float(raw.get("prob_loss")),
        basis=str(basis) if basis else None,
    )


def _historical_alignment(pre: dict[str, Any]) -> Optional[ConfidenceBlock]:
    """Per-expression alignment dial {score, letter} or None (suppressed)."""
    raw = pre.get("historical_alignment") if isinstance(pre, dict) else None
    if not isinstance(raw, dict):
        return None
    score = _as_int(raw.get("score"))
    letter = raw.get("letter")
    if score is None or not letter:
        return None
    return ConfidenceBlock(score=score, letter=str(letter))


def _best_from_expression(view: MarketView, best: ViewExpression) -> BestExpression:
    cfg = best.config if isinstance(best.config, dict) else {}
    bt = (cfg.get("scores") or {}).get("backtest") or {}
    plain = plain_copy.plain_for_expression(view, best)
    pre = precompute.expression_precompute(str(best.id))
    nums = _headline_numbers(pre, cfg)
    kind = _str_enum(best.expression_kind)
    not_bt = _not_backtested(kind, pre)
    return BestExpression(
        id=str(best.id),
        tier=_str_enum(best.tier),
        expression_kind=kind,
        grade=bt.get("grade"),
        trust_verdict=(
            None if not_bt else _effective_trust_verdict(pre, bt.get("trust_verdict"))
        ),
        # AVERAGE return per occurrence (not compounded across occurrences).
        total_return_pct=nums["strategy_total_pct"],
        excess_return_pct=nums["excess_return_pct"],
        plain_label=plain.get("plain_label"),
        nifty_total_pct=nums["nifty_total_pct"],
        n_episodes=nums["n_episodes"],
        pct_episodes_beat=nums["pct_episodes_beat"],
        # Rule #4: no fabricated backtested drawdown for option/derivative
        # tiers — "Priced at deploy", never a number in the drawdown column.
        worst_drop_pct=None if not_bt else nums["worst_drop_pct"],
        pct_positive=_as_float(pre.get("pct_positive")),
        n_positive=_as_int(pre.get("n_positive")),
        equity_curve=_curve_points(pre),
    )


def _best_expression(
    view: MarketView, exprs: list[ViewExpression],
) -> Optional[BestExpression]:
    """The HEADLINE expression whose numbers the card + detail lead with.

    Product decision: lead with the HIGHEST-RETURNING expression (by total
    return) so the card mini-line and detail chart show the best result. A
    developing curated view (``headline_tier`` None, e.g. an empty/unscreened
    basket) has no finished hero -> None. Never invents a number.
    """
    is_curated, tier = plain_copy.headline_tier(view)
    if is_curated and tier is None:
        return None  # developing: no finished hero

    # Highest AVERAGE-per-occurrence return among expressions with a real number,
    # but REAL-backtested tiers rank ahead of option/underlying-basis ones. An
    # option tier's episode returns are the UNDERLYING's move (curve rides the
    # underlying; its own return is "priced at deploy"), so leading the card with
    # it would misattribute the underlying's return — and its best_episode — to
    # the option. Prefer a backtested basket/pair/hedge; fall back to an option
    # only when nothing else has a number.
    scored: list[tuple[int, float, ViewExpression]] = []
    for e in exprs:
        cfg = e.config if isinstance(e.config, dict) else {}
        pre = precompute.expression_precompute(str(e.id))
        tot = _headline_numbers(pre, cfg)["strategy_total_pct"]
        if tot is None:
            continue
        backtested = 0 if _not_backtested(_str_enum(e.expression_kind), pre) else 1
        scored.append((backtested, tot, e))
    if not scored:
        return None
    scored.sort(key=lambda t: (t[0], t[1]), reverse=True)
    return _best_from_expression(view, scored[0][2])


def _confidence_score_letter(score_frac: Optional[float]) -> tuple[Optional[int], Optional[str]]:
    """DB stores 0..1; the dial is 0..100. Letter via _letter_band."""
    if score_frac is None:
        return None, None
    score = int(round(score_frac * 100))
    return score, _letter_band(score)


def _confidence_blocks(
    db: Session, view_id: str,
) -> tuple[ConfidenceBlock, ConfidenceBlock, dict[str, ViewConfidence]]:
    """Return (outcome, expression) summary blocks + the raw rows by dim."""
    rows = (
        db.query(ViewConfidence)
        .filter(ViewConfidence.view_id == view_id)
        .all()
    )
    by_dim: dict[str, ViewConfidence] = {}
    for r in rows:
        by_dim[_str_enum(r.dimension)] = r
    outcome_row = by_dim.get(ConfidenceDimension.outcome.value)
    expr_row = by_dim.get(ConfidenceDimension.expression.value)
    o_score, o_letter = _confidence_score_letter(
        outcome_row.score if outcome_row else None
    )
    e_score, e_letter = _confidence_score_letter(
        expr_row.score if expr_row else None
    )
    return (
        ConfidenceBlock(score=o_score, letter=o_letter),
        ConfidenceBlock(score=e_score, letter=e_letter),
        by_dim,
    )


def _summary_counts(
    db: Session, view_id: str, user_id: Optional[int],
) -> tuple[int, int, int, bool]:
    expr_count = (
        db.query(func.count(ViewExpression.id))
        .filter(ViewExpression.view_id == view_id)
        .scalar()
        or 0
    )
    trans_count = (
        db.query(func.count(ViewTransmission.id))
        .filter(ViewTransmission.view_id == view_id)
        .scalar()
        or 0
    )
    follower_count = (
        db.query(func.count(ViewFollow.id))
        .filter(ViewFollow.view_id == view_id)
        .scalar()
        or 0
    )
    is_following = False
    if user_id is not None:
        is_following = (
            db.query(ViewFollow.id)
            .filter(
                ViewFollow.view_id == view_id,
                ViewFollow.user_id == user_id,
            )
            .first()
            is not None
        )
    return int(expr_count), int(trans_count), int(follower_count), is_following


def _build_summary(
    db: Session, view: MarketView, user_id: Optional[int],
) -> ViewSummary:
    outcome, expression, _ = _confidence_blocks(db, str(view.id))
    expr_count, trans_count, follower_count, is_following = _summary_counts(
        db, str(view.id), user_id,
    )
    # Load minimal expressions for best_expression projection.
    exprs = (
        db.query(ViewExpression)
        .filter(ViewExpression.view_id == str(view.id))
        .all()
    )
    plain = plain_copy.plain_for_view(view)
    best = _best_expression(view, exprs)
    # The single best past occurrence of the headline strategy (real, from the
    # precompute episode list) — the most striking honest number for the card.
    best_ep_pct: Optional[float] = None
    best_ep_label: Optional[str] = None
    if best is not None:
        pre = precompute.expression_precompute(str(best.id))
        eps = pre.get("episodes") if isinstance(pre, dict) else None
        if isinstance(eps, list) and eps:
            top = max(
                eps,
                key=lambda ep: (
                    ep.get("return_pct") if isinstance(ep.get("return_pct"), (int, float))
                    else -1e9
                ),
            )
            best_ep_pct = _as_float(top.get("return_pct"))
            best_ep_label = top.get("label")
    return ViewSummary(
        id=str(view.id),
        view_type=_str_enum(view.view_type),
        title=view.title,
        thesis=view.thesis,
        category=view.category,
        time_horizon=view.time_horizon,
        status=_str_enum(view.status),
        resolution_date=view.resolution_date,
        created_at=view.created_at,
        published_at=view.published_at,
        outcome_confidence=outcome,
        expression_confidence=expression,
        best_expression=best,
        # No finished headline basket -> the idea is still developing. Both the
        # gallery card and the detail page key their "developing" framing off
        # this so the two surfaces stay consistent.
        is_developing=best is None,
        expression_count=expr_count,
        transmission_count=trans_count,
        follower_count=follower_count,
        is_following=is_following,
        plain_one_liner=plain.get("plain_one_liner"),
        plain_summary=plain.get("plain_summary"),
        short_title=plain_copy.short_title(str(view.id)) or view.title,
        # None today (no live stance source) — the FE degrades to a plain "View
        # details" card when absent. Wired here so the summary lights up the
        # same day a live stance source lands in _stance_for_view.
        stance=_stance_for_view(view),
        best_episode_pct=best_ep_pct,
        best_episode_label=best_ep_label,
    )


def _expression_detail(view: MarketView, e: ViewExpression) -> ExpressionDetail:
    cfg = e.config if isinstance(e.config, dict) else {}
    structure = cfg.get("structure") if isinstance(cfg.get("structure"), dict) else {}
    scores = cfg.get("scores") if isinstance(cfg.get("scores"), dict) else None
    instruments = cfg.get("instruments") if isinstance(cfg.get("instruments"), list) else []
    warnings = cfg.get("warnings") if isinstance(cfg.get("warnings"), list) else []
    kind = _str_enum(e.expression_kind)
    is_deployable = bool(e.workflow_id) or (kind in _DEPLOYABLE_KINDS)

    # ── layman layer ──
    plain = plain_copy.plain_for_expression(view, e)
    members = plain_copy.basket_members(cfg)
    n_names = _as_int((structure or {}).get("n_names")) or (len(members) or None)
    bt = (cfg.get("scores") or {}).get("backtest") or {}

    # Honest strategy identity + REAL precomputed chart/holdings.
    ident = plain_copy.strategy_identity(view, e)
    pre = precompute.expression_precompute(str(e.id))
    # AVERAGE return per occurrence (precompute), falling back to stored fields.
    nums = _headline_numbers(pre, cfg)
    option_legs = ident.get("option_legs")
    legs_models = (
        [OptionLeg(**leg) for leg in option_legs] if option_legs else None
    )
    # Rule #4: option/derivative tiers have no faithful historical backtest —
    # never a fabricated drawdown/trust; the badge reads "Priced at deploy".
    not_bt = _not_backtested(kind, pre, option_legs)
    effective_verdict = (
        None if not_bt else _effective_trust_verdict(pre, bt.get("trust_verdict"))
    )
    trust_badge_str = (
        _PRICED_AT_DEPLOY_BADGE if not_bt else plain_copy.trust_badge(effective_verdict)
    )

    return ExpressionDetail(
        id=str(e.id),
        tier=_str_enum(e.tier),
        expression_kind=kind,
        label=cfg.get("label"),
        rationale=e.rationale,
        risk_profile=e.risk_profile,
        capital_intensity=e.capital_intensity,
        historical_strength=e.historical_strength,
        time_horizon=e.time_horizon,
        workflow_id=str(e.workflow_id) if e.workflow_id else None,
        backtest_run_id=str(e.backtest_run_id) if e.backtest_run_id else None,
        instruments=list(instruments),
        warnings=plain_copy.plain_warnings(warnings),
        disclaimer=cfg.get("disclaimer"),
        structure=dict(structure or {}),
        scores=dict(scores) if scores is not None else None,
        is_deployable=is_deployable,
        plain_label=plain.get("plain_label"),
        plain_one_liner=plain.get("plain_one_liner"),
        plain_why=plain.get("plain_why"),
        plain_risk=plain.get("plain_risk"),
        capital_label=plain_copy.capital_label(e.capital_intensity),
        trust_badge=trust_badge_str,
        members=members,
        n_names=n_names,
        strategy_total_pct=nums["strategy_total_pct"],
        nifty_total_pct=nums["nifty_total_pct"],
        excess_return_pct=nums["excess_return_pct"],
        n_episodes=nums["n_episodes"],
        pct_episodes_beat=nums["pct_episodes_beat"],
        # Rule #4: no fabricated backtested drawdown for option/derivative
        # tiers — "Priced at deploy", never a number in the drawdown column.
        worst_drop_pct=None if not_bt else nums["worst_drop_pct"],
        pct_positive=_as_float(pre.get("pct_positive")),
        n_positive=_as_int(pre.get("n_positive")),
        strategy_name=ident.get("strategy_name"),
        strategy_type=ident.get("strategy_type"),
        option_legs=legs_models,
        option_legs_note=ident.get("option_legs_note"),
        equity_curve=_curve_points(pre),
        holdings=_holdings(pre),
        underlying_symbol=pre.get("underlying_symbol"),
        curve_basis=pre.get("curve_basis"),
        risk_return_ratio=_as_float(pre.get("risk_return_ratio")),
        curve_n_episodes=_as_int(pre.get("n_episodes")),
        episode_boundaries=[
            b for b in (pre.get("episode_boundaries") or []) if isinstance(b, int)
        ],
        episodes=_episodes(pre),
        positive_episodes=_as_int(pre.get("positive_episodes")),
        exit_period=pre.get("exit_period"),
        historical_alignment=_historical_alignment(pre),
        monte_carlo=_monte_carlo(pre),
        weight_scheme=pre.get("weight_scheme"),
        option_model=(
            pre.get("option_model")
            if isinstance(pre.get("option_model"), dict)
            else None
        ),
        entry=(
            pre.get("entry")
            if isinstance(pre.get("entry"), dict)
            else None
        ),
        gain_loss=(
            pre.get("gain_loss")
            if isinstance(pre.get("gain_loss"), dict)
            else None
        ),
    )


def _order_expressions(exprs: list[ViewExpression]) -> list[ViewExpression]:
    """conservative -> balanced -> aggressive; top expression_score first within tier."""
    def key(e: ViewExpression) -> tuple[int, float]:
        tier = _str_enum(e.tier)
        tier_idx = _TIER_ORDER.get(tier, 99)
        sc = _expression_score(e.config if isinstance(e.config, dict) else {})
        # Higher score first within a tier; missing-score sorts after scored.
        sc_key = -(sc if sc is not None else -1e18)
        return (tier_idx, sc_key)
    return sorted(exprs, key=key)


# ── endpoints ──────────────────────────────────────────────────────────────


def _list_cache_key(
    status: Optional[str], view_type: Optional[str], category: Optional[str],
) -> str:
    """Deterministic cache key covering every filter `list_views` accepts.

    Missing filters are represented by an explicit empty segment so
    ``status=None`` and ``status=""`` (never sent) can't collide with a
    real value that happens to be empty."""
    return (
        f"{_LIST_CACHE_PREFIX}"
        f"status={status or ''}:type={view_type or ''}:category={category or ''}"
    )


class SecurityMetaRequest(BaseModel):
    symbols: list[str]


class SecurityMeta(BaseModel):
    symbol: str
    name: str
    logo_url: Optional[str] = None
    asset_class: Optional[str] = None   # in_equity|in_etf|us_equity|us_etf|crypto
    currency: Optional[str] = None      # INR | USD


@router.post("/views/security-meta", response_model=list[SecurityMeta])
def views_security_meta(request: SecurityMetaRequest):
    """Resolve display metadata (name / logo / asset class / currency) for a
    batch of security symbols — Indian, US, ETF, or crypto. The Views/strategy
    cards call this to render a real company name + logo + asset badge for
    each constituent instead of a bare ticker. Read-only, no auth needed
    (metadata only). Capped at 200 symbols per call."""
    syms = [str(s) for s in (request.symbols or []) if s][:200]
    if not syms:
        return []
    from backend.view_markets.security_meta import resolve_many
    meta = resolve_many(syms)
    out: list[SecurityMeta] = []
    for s in syms:
        m = meta.get(s)
        if not m:
            continue
        out.append(SecurityMeta(
            symbol=str(s).upper(),
            name=str(m.get("name") or str(s).upper()),
            logo_url=m.get("logo_url"),
            asset_class=m.get("asset_class"),
            currency=m.get("currency"),
        ))
    return out


@router.get("/views", response_model=ListResponse)
def list_views(
    status: Optional[str] = None,
    view_type: Optional[str] = None,
    category: Optional[str] = None,
    db: Session = Depends(get_db),
    user_id: Optional[int] = Depends(_optional_user_id),
) -> ListResponse:
    _require_flag()

    # Validate filters up front (unchanged behaviour: an unknown enum value
    # is a 400 regardless of cache state) before touching Redis or Postgres.
    status_enum: Optional[ViewStatus] = None
    if status:
        try:
            status_enum = ViewStatus(status)
        except ValueError as exc:
            raise validation_error(f"unknown status {status!r}") from exc
    view_type_enum: Optional[ViewType] = None
    if view_type:
        try:
            view_type_enum = ViewType(view_type)
        except ValueError as exc:
            raise validation_error(f"unknown view_type {view_type!r}") from exc

    cache_key = _list_cache_key(status, view_type, category)
    cached = _cache_get_model(cache_key, ListResponse)
    if cached is not None:
        assert isinstance(cached, ListResponse)  # narrows for mypy
        return cached

    q = db.query(MarketView)
    if status_enum is not None:
        q = q.filter(MarketView.status == status_enum)
    else:
        # Default: hide archived.
        q = q.filter(MarketView.status != ViewStatus.archived)
    if view_type_enum is not None:
        q = q.filter(MarketView.view_type == view_type_enum)
    if category:
        q = q.filter(MarketView.category == category)
    q = q.order_by(MarketView.created_at.desc())
    items = [_build_summary(db, v, user_id) for v in q.all()]
    resp = ListResponse(items=items)
    _cache_set_model(cache_key, resp, _LIST_CACHE_TTL_S)
    return resp


# ── My Views — the per-user position ledger ─────────────────────────────────
#
# Registered BEFORE GET /views/{view_id} so the literal "positions" segment is
# never captured as a view id. Everything here is register-not-execute ledger
# arithmetic (see backend.view_markets.positions): Pivot records what the user
# armed and how it is doing — it never places, resizes, or exits an order.


class PositionLegOut(BaseModel):
    symbol: Optional[str] = None
    side: str = "long"
    weight: Optional[float] = None
    # entry/current level in INR (return math) + the security's native currency
    # (the $ level for US/crypto). asset_class/currency say what it is.
    asset_class: Optional[str] = None    # in_equity|in_etf|us_equity|us_etf|crypto
    currency: Optional[str] = None       # INR | USD
    entry_price: Optional[float] = None
    entry_price_native: Optional[float] = None
    last_price: Optional[float] = None
    last_price_native: Optional[float] = None
    return_pct: Optional[float] = None


class ViewPositionOut(BaseModel):
    id: str
    view_id: str
    expression_id: str
    workflow_id: Optional[str] = None
    # The view, at a glance (dateless question title + resolution state).
    view_title: Optional[str] = None
    view_status: Optional[str] = None
    view_resolved: bool = False
    resolution_date: Optional[datetime] = None
    # The strategy identity (fun name + tier), same source the detail page uses.
    tier: Optional[str] = None
    expression_kind: Optional[str] = None
    strategy_name: Optional[str] = None
    # The ledger.
    status: str
    entry_at: Optional[datetime] = None
    exited_at: Optional[datetime] = None
    capital_inr: Optional[float] = None
    open_fraction: float = 1.0
    take_profit_pct: Optional[float] = None
    stop_loss_pct: Optional[float] = None
    take_profit_hit: bool = False
    stop_loss_hit: bool = False
    # Live, up-to-date performance (None = honestly unpriceable, never 0).
    return_pct: Optional[float] = None
    unrealized_pnl_inr: Optional[float] = None
    open_value_inr: Optional[float] = None
    realized_pnl_inr: Optional[float] = None
    legs: list[PositionLegOut] = Field(default_factory=list)
    exits: list[dict[str, Any]] = Field(default_factory=list)
    note: Optional[str] = None


class PositionsResponse(BaseModel):
    items: list[ViewPositionOut]


class PositionUpdateRequest(BaseModel):
    """Partial update — only the fields the user actually sent are applied
    (an explicit null CLEARS that level)."""
    take_profit_pct: Optional[float] = None
    stop_loss_pct: Optional[float] = None
    capital_inr: Optional[float] = None


class PositionExitRequest(BaseModel):
    pct: float = Field(gt=0, le=100)


class PositionExitResponse(BaseModel):
    position: ViewPositionOut
    exited_pct: float
    note: str


def _require_user(user_id: Optional[int]) -> int:
    if user_id is None:
        raise http_error(401, "unauthorized", "sign in to see your views")
    return user_id


def _position_out(db: Session, pos: ViewPosition) -> ViewPositionOut:
    """One ledger row + its live snapshot, joined to the view/expression copy."""
    view = pos.view or db.get(MarketView, pos.view_id)
    expr = pos.expression or db.get(ViewExpression, pos.expression_id)

    view_title = None
    view_status = None
    resolved = False
    resolution_date = None
    if view is not None:
        extras = plain_copy.view_extras(view)
        view_title = extras.get("short_title") or view.title
        view_status = _str_enum(view.status)
        resolved = view_status in ("resolved", "archived")
        resolution_date = view.resolution_date

    tier = _str_enum(expr.tier) if expr is not None else None
    kind = _str_enum(expr.expression_kind) if expr is not None else None
    strategy_name = None
    if view is not None and expr is not None:
        strategy_name = plain_copy.strategy_identity(view, expr).get(
            "strategy_name"
        )

    snap = positions_svc.position_snapshot(pos)
    return ViewPositionOut(
        id=str(pos.id),
        view_id=str(pos.view_id),
        expression_id=str(pos.expression_id),
        workflow_id=str(pos.workflow_id) if pos.workflow_id else None,
        view_title=view_title,
        view_status=view_status,
        view_resolved=resolved,
        resolution_date=resolution_date,
        tier=tier,
        expression_kind=kind,
        strategy_name=strategy_name,
        status=_str_enum(pos.status),
        entry_at=pos.entry_at,
        exited_at=pos.exited_at,
        capital_inr=pos.capital_inr,
        open_fraction=float(pos.open_fraction or 0.0),
        take_profit_pct=pos.take_profit_pct,
        stop_loss_pct=pos.stop_loss_pct,
        take_profit_hit=snap["take_profit_hit"],
        stop_loss_hit=snap["stop_loss_hit"],
        return_pct=snap["return_pct"],
        unrealized_pnl_inr=snap["unrealized_pnl_inr"],
        open_value_inr=snap["open_value_inr"],
        realized_pnl_inr=snap["realized_pnl_inr"],
        legs=[PositionLegOut(**leg) for leg in snap["legs"]],
        exits=list(pos.exits or []),
        note=pos.note,
    )


def _load_position_or_404(
    db: Session, position_id: str, user_id: int
) -> ViewPosition:
    pos = (
        db.query(ViewPosition)
        .filter(
            ViewPosition.id == position_id,
            ViewPosition.user_id == user_id,
        )
        .one_or_none()
    )
    if pos is None:
        raise not_found(f"position {position_id} not found")
    return pos


@router.get("/views/positions", response_model=PositionsResponse)
def list_positions(
    db: Session = Depends(get_db),
    user_id: Optional[int] = Depends(_optional_user_id),
) -> PositionsResponse:
    """Every view the user has put a position behind — open first, newest
    first — each with its live return since entry."""
    _require_flag()
    uid = _require_user(user_id)
    rows = (
        db.query(ViewPosition)
        .filter(ViewPosition.user_id == uid)
        .order_by(ViewPosition.entry_at.desc())
        .all()
    )
    rows.sort(key=lambda p: _str_enum(p.status) != "open")  # open first, stable
    return PositionsResponse(items=[_position_out(db, p) for p in rows])


@router.patch("/views/positions/{position_id}", response_model=ViewPositionOut)
def update_position(
    position_id: str,
    body: PositionUpdateRequest,
    db: Session = Depends(get_db),
    user_id: Optional[int] = Depends(_optional_user_id),
) -> ViewPositionOut:
    """Edit the user's exit plan (take-profit / stop-loss levels) or declared
    position size. Only the fields present in the request are touched; an
    explicit null clears that field. Levels are LEDGER levels — nothing is
    auto-executed."""
    _require_flag()
    uid = _require_user(user_id)
    pos = _load_position_or_404(db, position_id, uid)

    sent = body.model_fields_set
    if "take_profit_pct" in sent:
        tp = body.take_profit_pct
        if tp is not None and tp <= 0:
            raise validation_error("take_profit_pct must be positive")
        pos.take_profit_pct = tp
    if "stop_loss_pct" in sent:
        sl = body.stop_loss_pct
        if sl is not None and sl <= 0:
            raise validation_error(
                "stop_loss_pct must be positive (the loss magnitude, e.g. 8)"
            )
        pos.stop_loss_pct = sl
    if "capital_inr" in sent:
        cap = body.capital_inr
        if cap is not None and cap <= 0:
            raise validation_error("capital_inr must be positive")
        pos.capital_inr = cap

    db.commit()
    return _position_out(db, pos)


@router.post(
    "/views/positions/{position_id}/exit", response_model=PositionExitResponse
)
def exit_position(
    position_id: str,
    body: PositionExitRequest,
    db: Session = Depends(get_db),
    user_id: Optional[int] = Depends(_optional_user_id),
) -> PositionExitResponse:
    """Record a partial (pct < 100) or full exit of the OPEN fraction at
    current marks. Register-not-execute: this updates the ledger and reminds
    the user to place the actual orders in their own broker app."""
    _require_flag()
    uid = _require_user(user_id)
    pos = _load_position_or_404(db, position_id, uid)
    try:
        result = positions_svc.apply_exit(db, pos, pct_of_open=body.pct)
    except ValueError as exc:
        raise validation_error(str(exc)) from exc
    db.commit()
    return PositionExitResponse(
        position=_position_out(db, pos),
        exited_pct=float(result["exited_pct"]),
        note=str(result["note"]),
    )


def _load_view_or_404(db: Session, view_id: str) -> MarketView:
    view = db.query(MarketView).filter(MarketView.id == view_id).one_or_none()
    if view is None:
        raise not_found(f"view {view_id} not found")
    return view


@router.get("/views/{view_id}", response_model=ViewDetail)
def get_view(
    view_id: str,
    db: Session = Depends(get_db),
    user_id: Optional[int] = Depends(_optional_user_id),
) -> ViewDetail:
    _require_flag()

    cache_key = f"{_DETAIL_CACHE_PREFIX}{view_id}"
    cached = _cache_get_model(cache_key, ViewDetail)
    if cached is not None:
        assert isinstance(cached, ViewDetail)  # narrows for mypy
        return cached

    view = _load_view_or_404(db, view_id)

    summary = _build_summary(db, view, user_id)

    # Confidence detail (with evidence).
    outcome_block, expression_block, by_dim = _confidence_blocks(db, str(view.id))
    o_row = by_dim.get(ConfidenceDimension.outcome.value)
    e_row = by_dim.get(ConfidenceDimension.expression.value)
    detail_conf = DetailConfidence(
        outcome=ConfidenceBlockWithEvidence(
            score=outcome_block.score,
            letter=outcome_block.letter,
            evidence=o_row.evidence if o_row else None,
        ),
        expression=ConfidenceBlockWithEvidence(
            score=expression_block.score,
            letter=expression_block.letter,
            evidence=e_row.evidence if e_row else None,
        ),
    )

    # Transmission edges (ordered by seq).
    tx_rows = (
        db.query(ViewTransmission)
        .filter(ViewTransmission.view_id == str(view.id))
        .order_by(ViewTransmission.seq.asc())
        .all()
    )
    transmission = [
        TransmissionEdge(
            seq=int(r.seq or 0),
            from_node=r.from_node,
            to_node=r.to_node,
            edge_label=r.edge_label,
            strength=_as_float(r.strength),
            evidence=r.evidence,
            from_label=plain_copy.node_label(r.from_node),
            to_label=plain_copy.node_label(r.to_node),
            strength_label=plain_copy.strength_label(_as_float(r.strength)),
            plain_evidence=plain_copy.plain_evidence(r.edge_label),
        )
        for r in tx_rows
    ]

    # Expectations.
    exp_rows = (
        db.query(ViewExpectation)
        .filter(ViewExpectation.view_id == str(view.id))
        .order_by(ViewExpectation.as_of.desc())
        .all()
    )
    expectations = [
        ExpectationRow(
            source=_str_enum(r.source),
            market_id=r.market_id,
            expected_value=_as_float(r.expected_value),
            user_view_value=_as_float(r.user_view_value),
            surprise_sign=r.surprise_sign,
            as_of=r.as_of,
            resolved_value=_as_float(r.resolved_value),
            source_label=plain_copy.source_label(_str_enum(r.source)),
        )
        for r in exp_rows
    ]

    # Expressions ordered Cons -> Bal -> Aggr, top-scored first within tier.
    expr_rows = (
        db.query(ViewExpression)
        .filter(ViewExpression.view_id == str(view.id))
        .all()
    )
    expressions = [
        _expression_detail(view, e) for e in _order_expressions(expr_rows)
    ]

    plain = plain_copy.plain_for_view(view)
    extras = plain_copy.view_extras(view)
    similar = [
        SimilarView(id=s["id"], short_title=s.get("short_title"))
        for s in plain_copy.similar_views(str(view.id))
    ]
    fc_raw = precompute.fundamental_comparison(str(view.id))
    fundamental = (
        FundamentalComparison(
            basket=FundamentalSide(**(fc_raw.get("basket") or {})),
            nifty=FundamentalSide(**(fc_raw.get("nifty") or {})),
        )
        if isinstance(fc_raw, dict)
        else None
    )
    detail = ViewDetail(
        **summary.model_dump(),
        transmission=transmission,
        confidence=detail_conf,
        expectations=expectations,
        expressions=expressions,
        plain_thesis=plain.get("plain_thesis"),
        benchmark_label=plain.get("benchmark_label"),
        description=extras.get("description"),
        bullets=list(extras.get("bullets") or []),
        similar_views=similar,
        fundamental_comparison=fundamental,
        # stance already arrives via **summary.model_dump() — the summary now
        # carries it (same _stance_for_view source), so passing it again here
        # would be a duplicate keyword. Detail and summary stay in lock-step.
    )
    _cache_set_model(cache_key, detail, _DETAIL_CACHE_TTL_S)
    return detail


def _load_expression_or_404(db: Session, expression_id: str) -> ViewExpression:
    try:
        expr = (
            db.query(ViewExpression)
            .filter(ViewExpression.id == expression_id)
            .one_or_none()
        )
    except DBAPIError:
        # A malformed id (e.g. not a UUID) makes Postgres raise on the cast,
        # which also poisons the session — roll back and treat it as a miss so
        # the caller gets a clean 404 instead of a 500.
        db.rollback()
        expr = None
    if expr is None:
        raise not_found(f"expression {expression_id} not found")
    return expr


class DeployRequest(BaseModel):
    activate: bool = False
    timing_mode: Optional[str] = None
    # The user-declared position size for the My Views ledger (never invented;
    # a missing capital just means the ledger shows % returns without rupees).
    capital_inr: Optional[float] = Field(default=None, gt=0)


def _ensure_position(
    db: Session,
    expression: ViewExpression,
    user_id: Optional[int],
    *,
    capital_inr: Optional[float],
    workflow_id: Optional[str],
) -> None:
    """Create the My Views ledger row for this (user, expression) if the user
    doesn't already hold an OPEN one — deploys are idempotent on the ledger.
    Best-effort: a ledger hiccup must never fail the deploy itself."""
    if user_id is None:
        return
    try:
        existing = (
            db.query(ViewPosition)
            .filter(
                ViewPosition.user_id == user_id,
                ViewPosition.expression_id == str(expression.id),
                ViewPosition.status == ViewPositionStatus.open,
            )
            .first()
        )
        if existing is not None:
            return
        view = _load_view(db, expression)
        if view is None:
            return
        positions_svc.create_position(
            db,
            view,
            expression,
            user_id=user_id,
            capital_inr=capital_inr,
            workflow_id=workflow_id,
        )
    except Exception:  # noqa: BLE001
        logger.warning("view position ledger create failed", exc_info=True)


def _load_view(db: Session, expression: ViewExpression) -> Optional[MarketView]:
    view = getattr(expression, "view", None)
    if view is not None:
        return view
    if expression.view_id:
        return db.get(MarketView, str(expression.view_id))
    return None


@router.post("/views/expressions/{expression_id}/deploy", response_model=DeployResponse)
def deploy(
    expression_id: str,
    body: Optional[DeployRequest] = None,
    db: Session = Depends(get_db),
    user_id: Optional[int] = Depends(_optional_user_id),
) -> DeployResponse:
    _require_flag()
    expression = _load_expression_or_404(db, expression_id)
    req = body or DeployRequest()

    # Re-use the existing draft when one is linked and the caller isn't asking
    # to (re-)arm it. NEVER places an order; deploy_expression itself is
    # register-not-execute.
    if expression.workflow_id and not req.activate:
        wf = (
            db.query(Workflow)
            .filter(Workflow.id == str(expression.workflow_id))
            .one_or_none()
        )
        if wf is not None:
            steps_count = len(wf.steps) if wf.steps is not None else 0
            # Re-deploy of an existing draft still lands on the ledger —
            # the user pressed Deploy, so My Views must show it.
            _ensure_position(
                db,
                expression,
                user_id,
                capital_inr=req.capital_inr,
                workflow_id=str(wf.id),
            )
            db.commit()
            return DeployResponse(
                workflow_id=str(wf.id),
                status=_str_enum(wf.status),
                steps_count=int(steps_count),
                activated=False,
            )

    try:
        result = deploy_expression(
            db,
            expression,
            timing_mode=req.timing_mode,  # type: ignore[arg-type]
            activate=bool(req.activate),
            user_id=user_id,
        )
    except ValueError as exc:
        raise validation_error(str(exc)) from exc

    # The fresh deploy lands on the My Views ledger (best-effort, same txn).
    _ensure_position(
        db,
        expression,
        user_id,
        capital_inr=req.capital_inr,
        workflow_id=str(result.get("workflow_id") or "") or None,
    )

    db.commit()

    _ph = get_posthog()
    if _ph:
        _ph.capture(
            "opinion_expression_deployed",
            distinct_id=str(user_id) if user_id else "anonymous",
            properties={
                "expression_id": expression_id,
                "expression_kind": str(getattr(expression, "expression_kind", "")),
                "tier": str(getattr(expression, "tier", "")),
                "activated": bool(result.get("activated")),
            },
        )

    return DeployResponse(
        workflow_id=str(result.get("workflow_id") or ""),
        status=str(result.get("status") or ""),
        steps_count=int(len(result.get("steps") or [])),
        activated=bool(result.get("activated")),
    )


# ── place-through-broker (register-not-execute, user-initiated) ─────────────
#
# Deploy places the strategy's concrete, affordable, whole-share basket through
# the user's *connected broker* (or the paper book, honouring the account's
# paper-vs-live mode) — the SAME routing seam the chat LogicCard uses
# (``_persist_leg`` → ``submit_order_for_user``). It is user-initiated (the
# user pressed Deploy), never an auto-firing automation, so it stays inside the
# register-not-execute boundary: no live order without an explicit human action,
# and a live account with no broker session gets the honest 409 to connect one.
#
# Only equity/ETF baskets with a real priced entry ticket are placeable this
# way; option structures / unaffordable baskets fall back to the automation
# deploy (the FE routes those to /deploy instead).


def _placeable_legs_from_entry(entry: Optional[dict[str, Any]]) -> list[dict[str, Any]]:
    """Build BUY-MARKET order legs from an expression's affordable entry ticket.

    Handles the whole-share / ETF-unit entry bases (``lite_basket``,
    ``etf_core_plus_names``, ``etf_substitute``). Returns ``[]`` for option /
    unaffordable / priced-at-deploy bases — nothing concrete to place."""
    if not isinstance(entry, dict):
        return []
    basis = entry.get("basis")
    raw: list[dict[str, Any]] = []
    if basis in ("lite_basket", "etf_core_plus_names"):
        raw = [leg for leg in (entry.get("legs") or []) if isinstance(leg, dict)]
    elif basis == "etf_substitute" and isinstance(entry.get("etf"), dict):
        raw = [entry["etf"]]
    legs: list[dict[str, Any]] = []
    for leg in raw:
        # NSE tradingsymbols carry no ".NS" suffix; ETFs (NIFTYBEES, …) trade on
        # NSE too. Quantity is whole shares (equity) or units (ETF).
        symbol = str(leg.get("symbol") or "").replace(".NS", "").strip().upper()
        qty = int(leg.get("shares") or leg.get("units") or 0)
        if not symbol or qty <= 0:
            continue
        legs.append({
            "symbol": symbol,
            "exchange": "NSE",
            "transaction_type": "BUY",
            "order_type": "MARKET",
            "quantity": qty,
            "price": None,
            "trigger_price": None,
            "product": "CNC",
        })
    return legs


class PlaceLegIn(BaseModel):
    """A user-adjusted leg from the deploy confirmation modal — the symbol must
    be one of the strategy's own affordable-entry names (validated server-side;
    a mismatch is dropped, so the modal can never place an off-strategy order)."""
    symbol: str
    quantity: int = Field(..., ge=0)


class PlaceRequest(BaseModel):
    conversation_id: Optional[str] = None
    # User-declared position size for the My Views ledger (never invented).
    capital_inr: Optional[float] = Field(default=None, gt=0)
    # Per-company share counts the user set in the deploy confirmation modal.
    # When present they OVERRIDE the computed basket (each symbol validated
    # against the strategy's own entry names; qty 0 drops that leg).
    legs: Optional[list[PlaceLegIn]] = None


class PlacedLegOut(BaseModel):
    id: int
    symbol: str
    exchange: str
    transaction_type: str
    order_type: str
    quantity: int
    price: Optional[float] = None
    status: str
    placed_at: str


class PlaceResponse(BaseModel):
    registered: list[PlacedLegOut]
    count: int
    # "broker" (live account, order placed through the connected broker) or
    # "paper" (simulated book) — so the FE can label the confirmation honestly.
    routed_to: str


@router.post(
    "/views/expressions/{expression_id}/place",
    response_model=PlaceResponse,
    status_code=201,
)
def place(
    expression_id: str,
    body: Optional[PlaceRequest] = None,
    db: Session = Depends(get_db),
    user_id: Optional[int] = Depends(_optional_user_id),
) -> PlaceResponse:
    _require_flag()
    if user_id is None:
        raise http_error(401, "auth_required", "Log in to place orders.")

    expression = _load_expression_or_404(db, expression_id)
    req = body or PlaceRequest()

    pre = precompute.expression_precompute(str(expression.id))
    entry = pre.get("entry") if isinstance(pre, dict) else None
    legs = _placeable_legs_from_entry(entry)
    if not legs:
        raise validation_error(
            "This strategy can't be placed as a simple share basket — deploy it "
            "as an automation instead."
        )

    # Apply the user's per-company adjustments from the confirmation modal.
    # Only the strategy's own entry names are honoured (validated against the
    # computed set) — an unknown symbol or qty 0 is dropped, never placed.
    if req.legs is not None:
        wanted = {leg.symbol.replace(".NS", "").strip().upper(): leg.quantity for leg in req.legs}
        adjusted: list[dict[str, Any]] = []
        for leg in legs:
            qty = wanted.get(leg["symbol"])
            if qty is None:
                adjusted.append(leg)  # untouched leg keeps its computed qty
            elif qty > 0:
                adjusted.append({**leg, "quantity": int(qty)})
            # qty == 0 → user removed this company from the basket
        legs = adjusted
        if not legs:
            raise validation_error("Add at least one share to place this basket.")

    # Route every leg through the shared order seam (broker or paper book,
    # honouring the account mode). A PAPER account fills the simulated book
    # with no broker; only a LIVE deploy needs a connected broker (honest
    # boundary; register-not-execute). Connecting any broker (incl. the dev
    # mock connector) creates a session and unblocks the live path.
    paper = should_use_paper(db, user_id)
    if not paper and get_active_broker_session(db, user_id) is None:
        raise state_conflict(
            "No broker connected — connect your broker (e.g. Zerodha Kite) in "
            "Brokers settings to place this basket."
        )
    rows = [
        _persist_leg(db, user_id, leg, conversation_id=req.conversation_id)
        for leg in legs
    ]

    # The placed strategy lands on the My Views ledger (best-effort, same txn).
    _ensure_position(
        db, expression, user_id, capital_inr=req.capital_inr, workflow_id=None,
    )

    db.commit()
    for r in rows:
        db.refresh(r)

    return PlaceResponse(
        registered=[
            PlacedLegOut(
                id=r.id,
                symbol=r.symbol,
                exchange=r.exchange or "NSE",
                transaction_type=r.transaction_type,
                order_type=r.order_type,
                quantity=r.quantity,
                price=r.price,
                status=r.status,
                placed_at=format_ist(r.placed_at),
            )
            for r in rows
        ],
        count=len(rows),
        routed_to="paper" if paper else "broker",
    )


# ── immediate multi-asset paper placement (the "Deploy = execute now" path) ──
# The curated basket / multi_asset expressions mix Indian equities with US
# equities/ETFs and crypto. The NSE-only /place ticket above can't size those
# (it forces exchange='NSE' off a precomputed entry ticket). This path instead
# reuses the allocate_basket LEG BUILDER (the same one deploy_expression arms)
# and the multi-asset mark → quantize → fill seam (submit_order_for_user →
# PaperBroker, which fractional-quantizes US/crypto) to place the WHOLE basket
# into the paper book at a user-declared amount — synchronously, with NO
# workflow/agent created. The user pressed Deploy, so it stays inside
# register-not-execute: a paper account fills the simulated book; a live account
# with no broker session gets the honest 409. Preview computes the exact
# per-leg breakdown (no writes) so the FE can confirm before placing.

# Default basket notional when the caller doesn't declare one (mirrors the
# Strategy-calculator's ₹1,00,000 default).
_DEFAULT_PLACE_INR: float = 100_000.0


def _inr_txt(v: float) -> str:
    """Indian-grouped rupee string for user-facing reasons (₹1,00,000)."""
    n = int(round(v))
    s = str(abs(n))
    if len(s) > 3:
        head, tail = s[:-3], s[-3:]
        parts = []
        while len(head) > 2:
            parts.insert(0, head[-2:])
            head = head[:-2]
        parts.insert(0, head)
        s = ",".join(parts) + "," + tail
    return f"₹{'-' if n < 0 else ''}{s}"


class BasketFillLeg(BaseModel):
    """One computed leg of an immediate basket placement (preview + skipped)."""
    symbol: str
    # Display name + logo for the deploy card (best-effort; logo may be null →
    # the FE renders a monogram).
    name: Optional[str] = None
    logo_url: Optional[str] = None
    asset_class: str
    exchange: str
    weight: float
    slice_inr: float
    # INR mark used to size the leg; None when no live price is available.
    mark_inr: Optional[float] = None
    # Whole shares (Indian) or fractional units (US/crypto); 0 when skipped.
    quantity: float
    # "ok" | "no_price" | "slice_too_small" | "short_unsupported"
    #        | "market_closed" | "insufficient_buying_power" | "rejected"
    status: str


class BasketPreviewResponse(BaseModel):
    placeable: bool
    routed_to: str  # "paper" | "broker"
    total_inr: float
    legs: list[BasketFillLeg]        # legs that WILL be placed
    skipped: list[BasketFillLeg]     # legs dropped, with an honest reason
    # Present (and the FE shows it as the pop-up reason) only when NOT placeable.
    reason: Optional[str] = None


class BasketPlacedLeg(BaseModel):
    symbol: str
    exchange: str
    quantity: float
    fill_price: Optional[float] = None
    status: str
    order_id: Optional[str] = None


class BasketPlaceResponse(BaseModel):
    placed: list[BasketPlacedLeg]
    count: int
    routed_to: str
    total_inr: float
    skipped: list[BasketFillLeg] = Field(default_factory=list)


class BasketPlaceRequest(BaseModel):
    capital_inr: Optional[float] = Field(default=None, gt=0)
    conversation_id: Optional[str] = None


_NO_LIVE_PRICE_REASON = (
    "No live prices are available for this basket right now — the market for "
    "these instruments may be closed. Try again during their market hours."
)


def _market_label(asset_class: str) -> str:
    if asset_class in ("us_equity", "us_etf"):
        return "US market"
    if asset_class in ("in_equity", "in_etf"):
        return "Indian market (NSE)"
    return "market"


def _not_placeable_reason(
    skipped: list[BasketFillLeg], total_inr: float, cash: Optional[float],
) -> str:
    """The single most relevant reason NOTHING could be placed — market-closed
    outranks buying-power outranks no-price, so the pop-up names the real
    blocker (e.g. 'US market is closed on weekends')."""
    closed = [s for s in skipped if s.status == "market_closed"]
    if closed:
        # Only reached for a LIVE/broker account now — paper deploys skip the
        # hours gate (see _compute_basket_fills). Keep it to one tight line and
        # name only the market(s) actually closed, no unrelated-venue boilerplate.
        markets = sorted({_market_label(s.asset_class) for s in closed})
        verb = "is" if len(markets) == 1 else "are"
        return (
            f"The {' and '.join(markets)} {verb} closed right now, so these "
            "orders can't reach your broker yet. Try again during market hours."
        )
    if cash is not None and any(
        s.status == "insufficient_buying_power" for s in skipped
    ):
        return (
            f"This basket needs about {_inr_txt(total_inr)} but your paper "
            f"account has {_inr_txt(cash)} free. Lower the amount or add paper "
            "capital, then try again."
        )
    return _NO_LIVE_PRICE_REASON


def _basket_place_legs(expression: ViewExpression) -> list[dict[str, Any]]:
    """The allocate_basket legs for a placeable basket/multi_asset expression.

    Reuses ``deploy._action_steps`` so the placed basket is IDENTICAL to what
    the automation path would arm. Raises ``validation_error`` (422) with a
    plain reason for option/hedge/pair kinds or a basket with no tradeable
    legs — the FE surfaces that reason in the deploy pop-up."""
    from backend.view_markets.deployment.deploy import _action_steps, _kind

    kind = _kind(expression)
    if kind not in ("basket", "multi_asset"):
        label = {
            "option_strategy": "This options strategy",
            "hedge": "This hedge",
            "pair": "This pair trade",
        }.get(kind, "This strategy")
        raise validation_error(
            f"{label} can't be placed as a simple share/units basket — Deploy "
            "here handles equity, ETF and crypto baskets only."
        )
    config = dict(expression.config or {})
    structure = config.get("structure") or {}
    if not isinstance(structure, dict):
        structure = {}
    try:
        actions, _ = _action_steps(
            kind, config, structure, label=config.get("label") or "basket",
        )
    except ValueError as exc:
        raise validation_error(str(exc)) from exc
    for step in actions:
        if step.get("step_type") == "action.allocate_basket":
            legs = list((step.get("config") or {}).get("legs") or [])
            if legs:
                return legs
    raise validation_error("This strategy has no placeable basket legs.")


def _compute_basket_fills(
    legs_cfg: list[dict[str, Any]],
    total_inr: float,
    available_inr: Optional[float] = None,
    paper: bool = False,
) -> tuple[list[BasketFillLeg], list[BasketFillLeg]]:
    """Size each leg at ``total_inr * normalised_weight`` using the multi-asset
    INR mark (NSE / US via Alpaca-yf×fx / crypto via Kraken-CoinGecko×fx) and
    the asset-class quantizer (whole shares for Indian, fractional for US/
    crypto). Returns (placeable_legs, skipped_legs) — mirrors the
    ``action.allocate_basket`` executor so preview == what actually fills.

    When ``available_inr`` is given (a paper account's free cash), legs are
    walked in order and any leg whose cost exceeds the running remaining cash is
    skipped as ``insufficient_buying_power`` — so the preview matches the book's
    per-order buying-power gate instead of promising a leg that then rejects."""
    from backend.market.market_hours import (
        asset_class_for_symbol,
        is_market_open_for_symbol,
    )
    from backend.paper.marks import get_mark_price
    from backend.paper.quantity import quantize_qty
    from backend.view_markets.security_meta import resolve_security_meta

    weights_sum = sum(float(leg.get("weight") or 0.0) for leg in legs_cfg) or 1.0
    remaining = available_inr  # None = no cash gate (broker account)
    ok: list[BasketFillLeg] = []
    skipped: list[BasketFillLeg] = []
    for leg in legs_cfg:
        sym = str(leg.get("symbol") or "").upper()
        if not sym:
            continue
        side = str(leg.get("side", "long"))
        weight = float(leg.get("weight") or 0.0) / weights_sum
        slice_inr = round(total_inr * weight, 2)
        ac = str(leg.get("asset_class") or asset_class_for_symbol(sym) or "in_equity")
        exch = str(leg.get("exchange") or "NSE")
        try:
            meta = resolve_security_meta(sym)
        except Exception:  # noqa: BLE001 — display only, never fail sizing
            meta = {}
        name = meta.get("name") or sym
        logo = meta.get("logo_url")

        def _mk(status: str, qty: float, mark: Optional[float]) -> BasketFillLeg:
            return BasketFillLeg(
                symbol=sym, name=name, logo_url=logo, asset_class=ac,
                exchange=exch, weight=round(weight, 6), slice_inr=slice_inr,
                mark_inr=mark, quantity=qty, status=status,
            )

        if side == "short":
            # Live shorts aren't brokered in v1 (the executor rejects them too).
            skipped.append(_mk("short_unsupported", 0.0, None))
            continue
        # Market-hours gate — LIVE/BROKER accounts only. A paper deploy fills the
        # SIMULATED book at the live mark (register-not-execute); it is never
        # routed to a real venue, so gating it on NSE/US hours is wrong — the
        # chat "Deploy basket" path (strategy.trade_basket) never gates paper,
        # and this path must match it. For a live/broker account the gate stays:
        # a real MARKET order to a closed venue only rests until the open.
        if not paper and not is_market_open_for_symbol(sym):
            skipped.append(_mk("market_closed", 0.0, None))
            continue
        mark = get_mark_price(sym, None)
        mark_f = float(mark) if mark and float(mark) > 0 else None
        if mark_f is None:
            skipped.append(_mk("no_price", 0.0, None))
            continue
        qty = quantize_qty(slice_inr / mark_f, symbol=sym, asset_class=ac)
        qty_f = float(qty) if qty is not None else 0.0
        if qty_f <= 0:
            skipped.append(_mk("slice_too_small", 0.0, mark_f))
            continue
        cost = qty_f * mark_f
        if remaining is not None and cost > remaining + 1e-6:
            skipped.append(_mk("insufficient_buying_power", 0.0, mark_f))
            continue
        if remaining is not None:
            remaining -= cost
        ok.append(_mk("ok", qty_f, mark_f))
    return ok, skipped


def _paper_cash_available(db: Session, user_id: int) -> Optional[float]:
    """A paper account's free cash (the buy-side buying-power gate), or None for
    a live/broker account (buying power lives at the broker, checked at place)."""
    if not should_use_paper(db, user_id):
        return None
    from backend.models import PaperAccount

    acct = (
        db.query(PaperAccount)
        .filter(PaperAccount.user_id == user_id, PaperAccount.is_active.is_(True))
        .one_or_none()
    )
    if acct is None:
        acct = (
            db.query(PaperAccount)
            .filter(PaperAccount.user_id == user_id)
            .one_or_none()
        )
    return float(acct.cash_available) if acct is not None else None


@router.post(
    "/views/expressions/{expression_id}/place-basket/preview",
    response_model=BasketPreviewResponse,
)
def place_basket_preview(
    expression_id: str,
    body: Optional[BasketPlaceRequest] = None,
    db: Session = Depends(get_db),
    user_id: Optional[int] = Depends(_optional_user_id),
) -> BasketPreviewResponse:
    _require_flag()
    if user_id is None:
        raise http_error(401, "auth_required", "Log in to place orders.")
    expression = _load_expression_or_404(db, expression_id)
    total = float((body and body.capital_inr) or _DEFAULT_PLACE_INR)
    legs_cfg = _basket_place_legs(expression)  # 422 with a plain reason if not
    cash = _paper_cash_available(db, user_id)
    paper = should_use_paper(db, user_id)
    ok, skipped = _compute_basket_fills(legs_cfg, total, cash, paper=paper)
    reason = None if ok else _not_placeable_reason(skipped, total, cash)
    return BasketPreviewResponse(
        placeable=bool(ok),
        routed_to="paper" if paper else "broker",
        total_inr=total,
        legs=ok,
        skipped=skipped,
        reason=reason,
    )


@router.post(
    "/views/expressions/{expression_id}/place-basket",
    response_model=BasketPlaceResponse,
    status_code=201,
)
def place_basket(
    expression_id: str,
    body: Optional[BasketPlaceRequest] = None,
    db: Session = Depends(get_db),
    user_id: Optional[int] = Depends(_optional_user_id),
) -> BasketPlaceResponse:
    _require_flag()
    if user_id is None:
        raise http_error(401, "auth_required", "Log in to place orders.")
    expression = _load_expression_or_404(db, expression_id)
    req = body or BasketPlaceRequest()
    total = float(req.capital_inr or _DEFAULT_PLACE_INR)

    legs_cfg = _basket_place_legs(expression)  # 422 with a plain reason if not
    cash = _paper_cash_available(db, user_id)
    paper = should_use_paper(db, user_id)
    ok, skipped = _compute_basket_fills(legs_cfg, total, cash, paper=paper)
    if not ok:
        raise validation_error(_not_placeable_reason(skipped, total, cash))

    if not paper and get_active_broker_session(db, user_id) is None:
        raise state_conflict(
            "No broker connected — connect your broker (e.g. Zerodha Kite) in "
            "Brokers settings to place this basket."
        )

    from backend.paper.routing import submit_order_for_user

    # Statuses that mean the book did NOT take the leg — a rejected buy deploys
    # no capital, so it's reported as skipped, never counted as placed (honest
    # count; mirrors the allocate_basket executor's fill test).
    _NOT_FILLED = {"REJECTED", "REJECT", "CANCELLED", "FAILED", "ERROR"}
    # A MARKET order placed while the venue is CLOSED doesn't fill — it RESTS
    # ("queued for open"). The market-hours gate above should have skipped it,
    # but if a leg rests anyway (race at the open/close boundary) never count it
    # as placed — report it as market_closed.
    _RESTING = {"RESTING", "QUEUED", "PENDING", "OPEN"}

    placed: list[BasketPlacedLeg] = []
    rejected: list[BasketFillLeg] = []
    for i, leg in enumerate(ok):
        r = submit_order_for_user(
            db, user_id,
            tradingsymbol=leg.symbol,
            exchange=leg.exchange,
            transaction_type="BUY",
            quantity=leg.quantity,  # raw; PaperBroker quantizes by asset class
            order_type="MARKET",
            product="CNC",
            tag="view_place",
            source="view",
            conversation_id=req.conversation_id,
            client_request_id=f"viewplace:{expression.id}:{i}:{leg.symbol}",
            label=leg.symbol,
        )
        status = str(r.get("status", ""))
        su = status.upper()
        if su in _NOT_FILLED or su in _RESTING:
            # Carry the book's real reason (e.g. insufficient_buying_power) so
            # the FE explains WHY a leg didn't fill, never a vague "rejected".
            reason = (
                "market_closed" if su in _RESTING
                else (str(r.get("reject_reason") or "").strip() or "rejected")
            )
            rejected.append(BasketFillLeg(
                symbol=leg.symbol, name=leg.name, logo_url=leg.logo_url,
                asset_class=leg.asset_class, exchange=leg.exchange,
                weight=leg.weight, slice_inr=leg.slice_inr,
                mark_inr=leg.mark_inr, quantity=leg.quantity, status=reason,
            ))
            continue
        placed.append(BasketPlacedLeg(
            symbol=leg.symbol,
            exchange=leg.exchange,
            quantity=leg.quantity,
            fill_price=(
                float(r["average_price"])
                if r.get("average_price") is not None else leg.mark_inr
            ),
            status=status,
            order_id=str(r["order_id"]) if r.get("order_id") is not None else None,
        ))

    # Nothing filled → don't leave rejected order rows or a phantom ledger row.
    if not placed:
        db.rollback()
        raise validation_error(
            "The book rejected every leg of this basket — nothing was placed. "
            "This usually means no live price was available; try again during "
            "market hours."
        )

    # Record the placed strategy on the My Views ledger (best-effort, same txn).
    _ensure_position(db, expression, user_id, capital_inr=total, workflow_id=None)
    db.commit()

    return BasketPlaceResponse(
        placed=placed,
        count=len(placed),
        routed_to="paper" if paper else "broker",
        total_inr=total,
        skipped=skipped + rejected,
    )


@router.post("/views/{view_id}/compare")
def compare(
    view_id: str,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    _require_flag()
    view = _load_view_or_404(db, view_id)
    try:
        result = compare_tiers(db, view)
    except ValueError as exc:
        raise validation_error(str(exc)) from exc
    db.commit()
    return result


@router.post("/views/expressions/{expression_id}/backtest")
def backtest(
    expression_id: str,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    _require_flag()
    expression = _load_expression_or_404(db, expression_id)
    try:
        result = backtest_expression(db, expression, persist=True)
    except ValueError as exc:
        raise validation_error(str(exc)) from exc
    db.commit()
    return result


def _follower_count(db: Session, view_id: str) -> int:
    return int(
        db.query(func.count(ViewFollow.id))
        .filter(ViewFollow.view_id == view_id)
        .scalar()
        or 0
    )


@router.post("/views/{view_id}/follow", response_model=FollowResponse)
def follow(
    view_id: str,
    db: Session = Depends(get_db),
    user_id: Optional[int] = Depends(_optional_user_id),
) -> FollowResponse:
    _require_flag()
    view = _load_view_or_404(db, view_id)
    if user_id is None:
        raise http_error(401, "unauthenticated", "login required to follow a view")
    existing = (
        db.query(ViewFollow)
        .filter(
            ViewFollow.view_id == str(view.id),
            ViewFollow.user_id == user_id,
        )
        .one_or_none()
    )
    if existing is None:
        db.add(ViewFollow(view_id=str(view.id), user_id=user_id))
        db.commit()

    _ph = get_posthog()
    if _ph:
        _ph.capture("opinion_followed", distinct_id=str(user_id), properties={
            "view_id": str(view.id),
            "view_type": str(view.view_type.value) if hasattr(view.view_type, "value") else str(view.view_type),
        })

    return FollowResponse(
        is_following=True, follower_count=_follower_count(db, str(view.id)),
    )


@router.delete("/views/{view_id}/follow", response_model=FollowResponse)
def unfollow(
    view_id: str,
    db: Session = Depends(get_db),
    user_id: Optional[int] = Depends(_optional_user_id),
) -> FollowResponse:
    _require_flag()
    view = _load_view_or_404(db, view_id)
    if user_id is None:
        raise http_error(401, "unauthenticated", "login required to unfollow a view")
    existing = (
        db.query(ViewFollow)
        .filter(
            ViewFollow.view_id == str(view.id),
            ViewFollow.user_id == user_id,
        )
        .one_or_none()
    )
    if existing is not None:
        db.delete(existing)
        db.commit()
    return FollowResponse(
        is_following=False, follower_count=_follower_count(db, str(view.id)),
    )


__all__ = ["router"]
