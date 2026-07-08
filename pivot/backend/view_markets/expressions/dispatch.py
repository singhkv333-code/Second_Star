"""View Markets — Phase 3 dispatch: the ONE public entry point.

``suggest_expressions(db, view, tier=None) -> list[ViewExpression]`` turns a
curated ``MarketView`` into proper Conservative/Balanced/Aggressive expressions
and persists them as ``ViewExpression`` rows. This is the single seam Phases 4–5
(backtest wiring, chat tools, REST) call.

Algorithm (frozen):

  1. **Pick archetypes** — for each tier, an ordered preference list (the §5
     per-tier structure menu) intersected with
     ``catalog.archetypes_for_view_type(view.view_type)`` and the lightweight
     ``_archetype_applies`` gate. Exactly ONE expression is built per requested
     tier (the first preference that constructs honestly), so a view yields a
     clean 3-tier ladder — Conservative / Balanced / Aggressive — not a flood of
     overlapping cards.
  2. **Build each (archetype, tier)** — look up the kind builder in
     ``builders.BUILDERS[archetype.expression_kind]`` and the knobs in
     ``tiers.tier_knobs(archetype.expression_kind, tier)``; resolve the kind's
     context (underlying / pair legs / theme symbols) from the view WITHOUT
     fabricating instruments (``thematic_map`` scenario → transmission edges →
     ``sector_universe``), then call the builder. A builder that *degrades*
     honestly (thin data / un-tradeable short → AVOID) returns an envelope and is
     used as-is; a builder that *cannot* construct (raises
     ``StrategyResolutionError`` / ``ValueError`` — e.g. no live chain, an
     unresolved leg) makes dispatch fall through to the next preference.
  3. **Attach timing** — ``timing.timing_to_trigger(view, mode)`` where ``mode``
     is the tier knob's ``timing_default`` (Cons=Confirmation, Bal=Hybrid,
     Aggr=Pre-position); the tier's ``rebalance`` cadence is stamped on the spec;
     stash on ``config.timing``. Mapping only — no workflow created here.
  4. **ENFORCE disclosures** — populate the five required ``ViewExpression``
     columns (``rationale`` / ``risk_profile`` / ``capital_intensity`` /
     ``historical_strength`` / ``time_horizon``) from the archetype + tier knobs +
     builder output. NEVER blank: re-uses ``config_schema.DISCLOSURE_FIELDS`` and
     the same blank-check as ``curation._missing_disclosures``; raises if any
     remains unset. ``historical_strength`` stays construction-time/qualitative —
     Phase 4 attaches the Trust verdict + ``backtest_run_id``.
  5. **Persist** — write a ``ViewExpression`` row per built tier with ``config`` =
     the envelope dict and the disclosure columns filled. Does NOT commit (caller
     owns the txn), mirroring ``curation.create_view``. Returns the flushed rows.

``tier`` argument: ``None`` (default) builds all three tiers; a single
``ExpressionTier`` value builds just that tier (the chat ``suggest_view_
expression(view, tier?)`` path).

register-not-execute is preserved end-to-end: dispatch only *describes* armed
workflows (``config.timing``); it never creates a workflow or places an order.
"""
from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Optional, cast

from backend.models import ExpressionKind, ExpressionTier, ViewExpression
from backend.services.backtest.pairs.engine import PairsError
from backend.services.option_strategies import StrategyResolutionError
from backend.view_markets.expressions import (
    builders,
    catalog,
    commodities,
    config_schema,
    tiers,
    timing,
)
from backend.view_markets.expressions.config_schema import DISCLOSURE_FIELDS

if TYPE_CHECKING:  # pragma: no cover - typing only
    from sqlalchemy.orm import Session

    from backend.models import MarketView
    from backend.view_markets.expressions.catalog import Archetype
    from backend.view_markets.expressions.tiers import TierKnobs

# The three tiers, in card order (Conservative → Balanced → Aggressive).
ALL_TIERS: tuple[str, ...] = (tiers.CONSERVATIVE, tiers.BALANCED, tiers.AGGRESSIVE)

# Builder errors that mean "this archetype can't construct here" → fall through to
# the next tier preference. A *degrade* (un-tradeable short / thin data) returns an
# envelope and is NOT in this set — honest degrades are preserved, never swallowed.
_BUILD_FALLBACK_ERRORS: tuple[type[Exception], ...] = (
    StrategyResolutionError,
    PairsError,
    ValueError,
)


class ExpressionDispatchError(Exception):
    """Raised when a view can't be expressed honestly (e.g. NO applicable
    archetype for a tier, or a built expression is missing a required
    disclosure)."""


# ════════════════════════════════════════════════════════════════════════════
# §5 per-tier structure menu — the ordered archetype preference per (view_type,
# tier). Each tier picks the FIRST entry that constructs; the list is the §5
# fallback ladder (defined-risk option → pair → screened basket), so a tier always
# resolves to a real, honest structure even when the richest one needs live data
# Pivot doesn't have offline. This is the "spanning the tier ladder" map.
# ════════════════════════════════════════════════════════════════════════════

_TIER_PLAN: dict[str, dict[str, tuple[str, ...]]] = {
    catalog.EVENT: {
        # Defined-risk debit/credit spread → risk-weighted basket fallback.
        tiers.CONSERVATIVE: (
            "E1_rate_debit_spread",
            "E9_budget_election_rotation",
            "E10_shock_hedged_basket",
        ),
        # The professional rates expression: NBFC-vs-bank pair (a NON-basket),
        # then the defined-risk vol-sell, then the rotation basket.
        tiers.BALANCED: (
            "E2_nbfc_bank_pair",
            "E4_iv_crush_harvest",
            "E9_budget_election_rotation",
        ),
        # Outright ATM debit spread / straddle, then the directional spread / pair.
        tiers.AGGRESSIVE: (
            "E3_event_straddle",
            "E1_rate_debit_spread",
            "E2_nbfc_bank_pair",
        ),
    },
    catalog.RELATIVE: {
        # Smart-beta ETF vs index future, or sector-vs-index (index-future short),
        # or the relative-options pair — every short here is an index future/put.
        tiers.CONSERVATIVE: (
            "R3_factor_etf_vs_index",
            "R2_sector_vs_index",
            "R5_relative_options",
            "R1_cointegrated_pair",
        ),
        # Cointegrated SSF pair / sector-vs-index, degrading to ratio-RS.
        tiers.BALANCED: (
            "R1_cointegrated_pair",
            "R2_sector_vs_index",
            "R4_ratio_rs",
        ),
        tiers.AGGRESSIVE: (
            "R1_cointegrated_pair",
            "R2_sector_vs_index",
            "R4_ratio_rs",
        ),
    },
    catalog.THEME: {
        # Purity/conviction basket → multi-asset → hedged overlay.
        tiers.CONSERVATIVE: (
            "T1_purity_conviction_basket",
            "T4_multi_asset",
            "T3_optionized_hedged",
        ),
        tiers.BALANCED: (
            "T1_purity_conviction_basket",
            "T2_factor_tilt",
            "T4_multi_asset",
        ),
        tiers.AGGRESSIVE: (
            "T2_factor_tilt",
            "T4_multi_asset",
            "T1_purity_conviction_basket",
        ),
    },
}

# ════════════════════════════════════════════════════════════════════════════
# COMMODITY (MCX) per-tier menu — used INSTEAD of ``_TIER_PLAN`` when the view
# names a tradeable MCX commodity (``_resolve_view_symbols`` sets ``syms.commodity``).
# Commodities became tradeable via register-not-execute on 2026-06-29; these plans
# surface the CM1–CM6 archetypes (defined-risk MCX option → producer-vs-importer /
# gold-vs-silver pair → direct-MCX multi-asset), each ordered "richest structure
# that constructs honestly first, equity fallback last". Every CM archetype carries
# ``params["commodity"]`` so dispatch folds the leverage note into the disclosures.
# The CM keys are in the view-type pool already (the archetypes declare their
# ``view_types``); offline (no live MCX chain) a CM option degrades to the pair.
# ════════════════════════════════════════════════════════════════════════════

_COMMODITY_TIER_PLAN: dict[str, dict[str, tuple[str, ...]]] = {
    catalog.EVENT: {
        # Defined-risk commodity option (the headline NON-basket) → producer/importer
        # pair → crude-shock multi-asset / equity rotation fallback.
        tiers.CONSERVATIVE: (
            "CM1_commodity_directional_option",
            "CM3_commodity_producer_vs_importer_pair",
            "CM6_crude_shock_hedged_basket",
            "E9_budget_election_rotation",
        ),
        tiers.BALANCED: (
            "CM3_commodity_producer_vs_importer_pair",
            "CM2_commodity_event_straddle",
            "CM1_commodity_directional_option",
            "E9_budget_election_rotation",
        ),
        # Outright MCX straddle / directional option, then the leveraged sleeves.
        tiers.AGGRESSIVE: (
            "CM2_commodity_event_straddle",
            "CM1_commodity_directional_option",
            "CM6_crude_shock_hedged_basket",
            "CM3_commodity_producer_vs_importer_pair",
        ),
    },
    catalog.RELATIVE: {
        # Gold-vs-silver bullion ratio (direct MCX, construct-only) → producer/importer
        # equity pair (DOES backtest) → ratio/RS equity fallback.
        tiers.CONSERVATIVE: (
            "CM4_gold_silver_ratio_pair",
            "CM3_commodity_producer_vs_importer_pair",
            "R4_ratio_rs",
        ),
        tiers.BALANCED: (
            "CM4_gold_silver_ratio_pair",
            "CM3_commodity_producer_vs_importer_pair",
            "R4_ratio_rs",
        ),
        tiers.AGGRESSIVE: (
            "CM3_commodity_producer_vs_importer_pair",
            "CM4_gold_silver_ratio_pair",
            "R4_ratio_rs",
        ),
    },
    catalog.THEME: {
        # Direct-MCX multi-asset sleeve → defined-risk commodity option → equity basket.
        tiers.CONSERVATIVE: (
            "CM5_commodity_multi_asset",
            "CM1_commodity_directional_option",
            "T1_purity_conviction_basket",
        ),
        tiers.BALANCED: (
            "CM5_commodity_multi_asset",
            "CM1_commodity_directional_option",
            "T1_purity_conviction_basket",
        ),
        tiers.AGGRESSIVE: (
            "CM6_crude_shock_hedged_basket",
            "CM1_commodity_directional_option",
            "T4_multi_asset",
        ),
    },
}

# Direction tokens (commodity views are directional). Bearish wins a tie so a
# "crude spikes then fades" reads short only on an explicit fade. Default → long
# (the CM defined-risk templates default bullish: bull_call_spread / long sleeve).
_BEARISH_TOKENS: frozenset[str] = frozenset({
    "fall", "falls", "falling", "fell", "drop", "drops", "decline", "declines",
    "down", "downside", "slump", "weaken", "weak", "bearish", "bear", "crash",
    "plunge", "sink", "slide", "lower", "selloff", "correction", "downturn",
})
_BULLISH_TOKENS: frozenset[str] = frozenset({
    "rise", "rises", "rising", "rose", "spike", "spikes", "rally", "rallies",
    "surge", "surges", "jump", "jumps", "up", "upside", "strong", "bullish",
    "bull", "soar", "climb", "gain", "gains", "higher", "boom", "shock",
    "squeeze", "spiking",
})


def _detect_direction(text: str) -> str:
    """Read a directional lean ("long"/"short") off the view text (default long).

    A bearish keyword routes a commodity directional option to its defined-risk
    BEARISH template (``bear_put_spread``) and a multi-asset commodity sleeve to a
    TRADEABLE MCX short — never a fabricated short."""
    tokens = {t for t in re.split(r"[^a-z0-9]+", text.lower()) if t}
    if tokens & _BEARISH_TOKENS:
        return "short"
    if tokens & _BULLISH_TOKENS:
        return "long"
    return "long"


# Representative liquid names for an archetype's leg *tags* (E2's nbfc/bank). These
# are canonical India defaults sourced from the thematic seed / sector_universe —
# the curator or chat overrides them with concrete tickers. Never a fabricated
# price, only a representative symbol so the pair builder has a leg to resolve.
_LEG_TAG_SYMBOLS: dict[str, str] = {
    "nbfc": "BAJFINANCE",
    "private_bank": "HDFCBANK",
    "psu_bank": "SBIN",
    "bank": "HDFCBANK",
    "life_insurance": "HDFCLIFE",
    "insurance": "HDFCLIFE",
}

# Factor keywords → the cross_sectional factor name (R3 smart-beta tilt).
_FACTOR_KEYWORDS: tuple[tuple[str, str], ...] = (
    ("momentum", "momentum"),
    ("value", "value"),
    ("quality", "quality"),
    ("low vol", "low_vol"),
    ("low-vol", "low_vol"),
    ("minimum vol", "low_vol"),
    ("multi-factor", "multi"),
    ("multi factor", "multi"),
)

# Default index for option/hedge legs. NIFTY carries weeklies (clean for straddles);
# archetypes that need BANKNIFTY pin it via ``params["default_underlying"]``.
_DEFAULT_INDEX: str = "NIFTY"


# ════════════════════════════════════════════════════════════════════════════
# View → instrument resolution (honest; never fabricates a name)
# ════════════════════════════════════════════════════════════════════════════


@dataclass
class _ViewSymbols:
    """Resolved instrument candidates for a view (no fabricated names)."""

    longs: list[str] = field(default_factory=list)
    shorts: list[str] = field(default_factory=list)
    theme: Optional[str] = None
    factor: Optional[str] = None
    scenario_key: Optional[str] = None
    underlying: str = _DEFAULT_INDEX
    # Commodity (MCX): the DIRECT MCX symbol the view names (GOLD/CRUDEOIL…) or
    # ``None``; ``direction`` is the long/short lean for the directional vehicles.
    commodity: Optional[str] = None
    commodity_group: Optional[str] = None
    direction: Optional[str] = None


def _view_text(view: "MarketView") -> str:
    """Lower-cased title + thesis + category — the matcher input."""
    return " ".join(
        str(p) for p in (view.title, view.thesis, view.category) if p
    ).lower()


def _looks_like_ticker(token: str) -> bool:
    """Heuristic: an UPPERCASE alphanumeric node from a transmission edge."""
    t = token.strip()
    return bool(t) and t.upper() == t and t.replace("&", "").replace("-", "").isalnum()


def _resolve_view_symbols(db: "Session", view: "MarketView") -> _ViewSymbols:
    """Resolve long/short instrument candidates + theme/factor for a view.

    Layered + honest — each layer only adds REAL names, never an invented ticker:

      1. ``thematic_map`` scenario (winners → longs, losers → shorts);
      2. the view's transmission edges (``to_node`` tickers as longs);
      3. ``sector_universe`` by a sector keyword detected in the title/thesis.

    Returns whatever resolved; an empty ``longs`` is left empty (a pair/basket
    builder then refuses honestly rather than trading a fabricated leg).
    """
    from backend.services import sector_universe, thematic_map

    syms = _ViewSymbols()
    text = _view_text(view)

    # 1) Thematic scenario (rate_cut / crude_spike / slowdown / …). The detector
    # needs a positioning verb (it deliberately ignores bare quotes), so wrap the
    # view text in a build phrase — the same workaround basket_builder uses.
    probe = " ".join(str(p) for p in (view.title, view.thesis) if p)
    scenario = thematic_map.detect_thematic_scenario(
        f"build me a strategy to position for {probe}"
    )
    if scenario is not None:
        syms.scenario_key = scenario.key
        syms.theme = scenario.key
        syms.longs = [t for t, _ in scenario.winners]
        syms.shorts = [t for t, _ in scenario.losers]

    # 2) Transmission edges (curated cause→effect tickers).
    if not syms.longs:
        try:
            from backend.models import ViewTransmission

            edges = (
                db.query(ViewTransmission)
                .filter(ViewTransmission.view_id == view.id)
                .order_by(ViewTransmission.seq)
                .all()
            )
            syms.longs = [
                str(e.to_node) for e in edges if _looks_like_ticker(str(e.to_node))
            ]
        except Exception:  # pragma: no cover - DB-shape defensive
            pass

    # 3) Sector keyword → universe (e.g. "IT outperforms Nifty" → it → TCS/INFY).
    if not syms.longs:
        sector = None
        for token in sorted(set(text.replace("/", " ").split()), key=len, reverse=True):
            sector = sector_universe.normalize_sector(token)
            if sector is not None:
                break
        if sector is not None:
            rows = sector_universe.query_screener(sector=sector, limit=10)
            syms.longs = [str(r["symbol"]) for r in rows]
            if syms.theme is None:
                syms.theme = str(sector)

    # Factor tilt (R3) keyword scan.
    for kw, factor in _FACTOR_KEYWORDS:
        if kw in text:
            syms.factor = factor
            break

    # 4) Commodity (MCX) detection — a tradeable MCX commodity NAMED in the view
    # makes this a COMMODITY view, so dispatch prefers the CM archetypes and passes
    # the leverage-noted commodity ctx. Resolves only a symbol the commodity
    # universe recognises (``normalize_commodity``) — never a fabricated instrument.
    syms.commodity = commodities.normalize_commodity(text)
    if syms.commodity is not None:
        syms.commodity_group = commodities.commodity_group(syms.commodity)
        syms.direction = _detect_direction(text)

    if syms.theme is None:
        syms.theme = str(view.category) if view.category else None
    return syms


def _pair_legs(
    view: "MarketView", archetype: "Archetype", syms: _ViewSymbols
) -> tuple[Optional[str], str]:
    """Resolve (long leg A, short leg B) for a pair / relative-options archetype.

    Direction is "A beats B" → long A / short B. Resolution order:

      * explicit leg *tags* (E2 nbfc/bank) → :data:`_LEG_TAG_SYMBOLS`;
      * an explicit ``params['leg_b']`` (R2/R3 index leg) with leg A from longs;
      * otherwise leg A = first long candidate, leg B = first short candidate or
        the index (``NIFTY``) when no concrete short name exists.

    Leg A may be ``None`` when nothing resolves — the caller treats that as "this
    archetype can't express this view" and falls through (never fabricates).
    """
    params = archetype.params
    tag_a = params.get("leg_a_tag")
    tag_b = params.get("leg_b_tag")
    if tag_a or tag_b:
        a = _LEG_TAG_SYMBOLS.get(str(tag_a)) if tag_a else None
        b = _LEG_TAG_SYMBOLS.get(str(tag_b)) if tag_b else None
        # Prefer the scenario's own NBFC/bank winners when present, else defaults.
        a = a or (syms.longs[0] if syms.longs else None)
        b = b or (syms.shorts[0] if syms.shorts else _DEFAULT_INDEX)
        return a, str(b)

    leg_b_param = params.get("leg_b")
    long_a = syms.longs[0] if syms.longs else None
    if leg_b_param:
        return long_a, str(leg_b_param)

    short_b = syms.shorts[0] if syms.shorts else _DEFAULT_INDEX
    return long_a, str(short_b)


def _build_ctx(
    view: "MarketView",
    archetype: "Archetype",
    tier: str,
    knobs: "TierKnobs",
    syms: _ViewSymbols,
) -> dict[str, Any]:
    """Assemble the kind-specific ``**ctx`` for a builder (the seam each builder
    reads). Extra keys a builder doesn't name are absorbed by its ``**ctx`` and
    ignored, so a comprehensive context is safe."""
    kind = archetype.expression_kind
    ctx: dict[str, Any] = {}

    if kind == catalog.KIND_OPTION:
        if archetype.params.get("two_underlying"):  # R5 relative-options pair
            a, b = _pair_legs(view, archetype, syms)
            if a is None:
                raise ValueError(
                    f"{archetype.key}: relative-options long leg unresolved"
                )
            ctx["symbol_a"] = a
            ctx["symbol_b"] = b
            ctx["underlying"] = a
        elif archetype.params.get("commodity"):
            # CM1/CM2: defined-risk MCX option on the named commodity; ``direction``
            # flips CM1 to its bearish defined-risk template. Falls back to the
            # archetype default commodity when the view named none (never invented).
            ctx["underlying"] = (
                syms.commodity
                or archetype.params.get("default_underlying")
                or syms.underlying
            )
            ctx["direction"] = syms.direction
        else:
            ctx["underlying"] = (
                archetype.params.get("default_underlying") or syms.underlying
            )
    elif kind == catalog.KIND_PAIR:
        if archetype.params.get("commodity"):
            _fill_commodity_pair_ctx(ctx, archetype, syms)
        else:
            a, b = _pair_legs(view, archetype, syms)
            if a is None:
                raise ValueError(f"{archetype.key}: pair long leg A unresolved")
            factor = syms.factor or archetype.params.get("factor")
            # R3 (factor smart-beta ETF vs index) — ``template_or_scheme`` is None,
            # the long leg IS a factor ETF. Without a detected factor we'd mislabel a
            # stock as an ETF, so refuse and fall through to the sector pair (R2).
            if archetype.template_or_scheme is None and not factor:
                raise ValueError(
                    f"{archetype.key}: factor-ETF tilt needs a detected factor "
                    "(none in the view) — falling back to a sector/cointegrated pair."
                )
            ctx["symbol_a"] = a
            ctx["symbol_b"] = b
            if factor:
                ctx["factor"] = factor
    elif kind in (catalog.KIND_BASKET, catalog.KIND_MULTI_ASSET):
        if syms.longs:
            ctx["symbols"] = list(syms.longs)
        ctx["theme"] = syms.theme or view.category
        if archetype.params.get("commodity"):
            # CM5/CM6: the direct-MCX sleeve. Pass the named commodity (else the
            # builder uses the archetype's ``direct_mcx_sleeve``/``direct_crude_leg``
            # default) + the long/short lean. A bearish lean routes the sleeve's
            # short through honest_short → a TRADEABLE MCX future/put, never AVOID.
            if syms.commodity:
                ctx["commodity_symbol"] = syms.commodity
            ctx["commodity_direction"] = syms.direction or "long"
    elif kind == catalog.KIND_HEDGE:
        ctx["underlying"] = archetype.params.get("hedge_index") or syms.underlying

    return ctx


def _fill_commodity_pair_ctx(
    ctx: dict[str, Any], archetype: "Archetype", syms: _ViewSymbols
) -> None:
    """Resolve the legs for a commodity pair (CM3 producer-vs-importer / CM4
    gold-vs-silver) WITHOUT fabricating a leg.

    * **CM4** pins its bullion legs on the archetype (direct MCX ``GOLD``/``SILVER``)
      and stays construct-only (no ``use_etf_proxy``) so a missing-OHLCV spread
      degrades honestly rather than fabricating a cointegration; the SHORT leg
      (``SILVER``) resolves to a TRADEABLE MCX future via ``honest_short``.
    * **CM3** resolves real producer-vs-OMC/refiner EQUITY legs from
      ``sector_universe`` (which DO carry aligned OHLCV → they backtest), flipping
      long/short on a crude-DOWN view. Raises ``ValueError`` (→ dispatch falls
      through) when no real producer leg resolves — never an invented ticker.
    """
    from backend.services import sector_universe

    params = archetype.params
    if params.get("leg_a"):  # CM4 direct MCX bullion ratio
        ctx["symbol_a"] = str(params["leg_a"])
        ctx["symbol_b"] = str(params.get("leg_b") or "SILVER")
        return

    producers = sector_universe.crude_up_beneficiaries() or []
    refiners = sector_universe.crude_down_beneficiaries() or []
    if syms.direction == "short":  # crude DOWN → refiner margins expand vs producers
        long_leg = refiners[0] if refiners else (syms.longs[0] if syms.longs else None)
        short_leg = (
            producers[0] if producers else (syms.shorts[0] if syms.shorts else _DEFAULT_INDEX)
        )
    else:  # crude UP → upstream producers gain vs OMC/refiner importers
        long_leg = producers[0] if producers else (syms.longs[0] if syms.longs else None)
        short_leg = (
            refiners[0] if refiners else (syms.shorts[0] if syms.shorts else _DEFAULT_INDEX)
        )
    if long_leg is None:
        raise ValueError(
            f"{archetype.key}: commodity producer/importer long leg unresolved "
            "(no crude beneficiary universe) — falling through, no leg fabricated."
        )
    ctx["symbol_a"] = str(long_leg)
    ctx["symbol_b"] = str(short_leg)


# ════════════════════════════════════════════════════════════════════════════
# Disclosure synthesis (the five ViewExpression columns — never blank)
# ════════════════════════════════════════════════════════════════════════════


def _phrase(value: Optional[str]) -> str:
    """Humanise a snake_case knob value into a readable fragment."""
    return str(value or "").replace("_", " ").strip()


_KIND_HUMAN: dict[str, str] = {
    catalog.KIND_OPTION: "defined-risk option structure",
    catalog.KIND_PAIR: "market-neutral pair",
    catalog.KIND_BASKET: "screened, conviction-weighted basket",
    catalog.KIND_MULTI_ASSET: "multi-asset sleeve portfolio",
    catalog.KIND_HEDGE: "index-level hedge overlay",
}

_DEFAULT_HORIZON: dict[str, str] = {
    catalog.KIND_OPTION: "the event / single-expiry window",
    catalog.KIND_PAIR: "the spread's mean-reversion window (half-life < horizon)",
    catalog.KIND_BASKET: "a multi-quarter structural hold",
    catalog.KIND_MULTI_ASSET: "a multi-quarter structural hold",
    catalog.KIND_HEDGE: "the hedge's option expiry",
}


def _historical_strength(envelope: dict[str, Any]) -> str:
    """Construction-time, qualitative relationship strength (NOT a backtest).

    Phase 4 replaces/augments this with the Trust verdict + ``backtest_run_id``."""
    kind = str(envelope.get("expression_kind"))
    struct = envelope.get("structure", {}) or {}
    if kind == catalog.KIND_PAIR:
        ca = struct.get("cointegrated_at")
        hl = struct.get("half_life_days")
        if ca:
            base = (
                f"Residual stationary at the {ca} ADF level"
                + (f"; OU half-life ≈ {hl} days." if hl else ".")
            )
        elif hl:
            base = (
                f"Spread half-life ≈ {hl} days but stationarity is not confirmed "
                "(ratio/RS rigor, lower ceiling)."
            )
        else:
            base = (
                "Spread statistics pending a live aligned series (degraded "
                "offline — no fabricated cointegration)."
            )
    elif kind in (catalog.KIND_BASKET, catalog.KIND_MULTI_ASSET):
        bp = (envelope.get("scores", {}) or {}).get("basket_purity")
        base = (
            f"Construction-time Basket Purity {bp}/100."
            if bp is not None
            else "Construction-time purity gradient (curated/segment-estimated)."
        )
    elif kind in (catalog.KIND_OPTION, catalog.KIND_HEDGE):
        pop = struct.get("pop")
        if isinstance(pop, (int, float)):
            base = f"Defined-risk structure; model probability-of-profit ≈ {round(pop * 100)}%."
        else:
            base = "Defined-risk structure convex to the surprise, bounded loss."
    else:  # pragma: no cover - all kinds covered above
        base = "Construction-time qualitative relationship strength."
    return base + " Trust verdict + event-study/backtest attached in Phase 4."


def _is_commodity_envelope(
    archetype: "Archetype", envelope: dict[str, Any]
) -> bool:
    """True when the built expression is an MCX commodity one (carry the leverage
    note in ``risk_profile``). Detected off the archetype's ``commodity`` flag OR
    any India-typed MCX leg in the envelope (covers a commodity sleeve added to an
    otherwise-equity multi-asset / basket)."""
    if archetype.params.get("commodity"):
        return True
    for inst in envelope.get("instruments", []) or []:
        if config_schema.is_commodity_segment(inst.get("segment")):
            return True
    return False


def _disclosures(
    view: "MarketView",
    archetype: "Archetype",
    tier: str,
    knobs: "TierKnobs",
    envelope: dict[str, Any],
) -> dict[str, str]:
    """Build the five required disclosure strings — every one non-blank."""
    kind = archetype.expression_kind
    kind_human = _KIND_HUMAN.get(kind, "strategy")
    thesis = (view.thesis or view.title or "the curated view").strip()

    rationale = (
        f"{archetype.label}. As the {tier} tier this maps the view "
        f"— {thesis} — onto a {kind_human}."
    )

    expressability = envelope.get("expressability", {}) or {}
    degraded = bool(expressability.get("degraded"))
    risk_bits = [
        f"Hedge: {_phrase(knobs.hedge_ratio)}",
        f"leverage: {_phrase(knobs.leverage) or 'none'}",
    ]
    risk_profile = (
        "; ".join(risk_bits)
        + ". Defined-risk first (stated max loss where the structure caps it); "
        "register-not-execute (armed, you place every order)."
    )
    if degraded:
        risk_profile += (
            " Short/leg degraded to an honest proxy (see warnings) — never a "
            "fabricated delivery short."
        )
    # Commodity (MCX) expressions are LEVERAGED — fold the leverage note into the
    # ``risk_profile`` disclosure COLUMN (the builders already carry it in
    # ``config.warnings``); commodity legs are NEVER auto-sized (register-not-execute).
    if _is_commodity_envelope(archetype, envelope):
        risk_profile += " " + commodities.LEVERAGE_NOTE

    capital_intensity = (
        _phrase(knobs.capital_intensity) or f"{tier} tier capital intensity"
    )

    historical_strength = _historical_strength(envelope)

    time_horizon = (
        (view.time_horizon or "").strip()
        or _DEFAULT_HORIZON.get(kind, "the view's stated horizon")
    )

    return {
        "rationale": rationale,
        "risk_profile": risk_profile,
        "capital_intensity": capital_intensity,
        "historical_strength": historical_strength,
        "time_horizon": time_horizon,
    }


# ════════════════════════════════════════════════════════════════════════════
# Public entry
# ════════════════════════════════════════════════════════════════════════════


def suggest_expressions(
    db: "Session",
    view: "MarketView",
    tier: Optional[str] = None,
) -> list["ViewExpression"]:
    """Build + persist tiered expressions for ``view`` (the ONE public entry).

    See the module docstring for the full algorithm. ``tier=None`` builds all
    three tiers; a single ``ExpressionTier`` value builds just that one. Enforces
    the five required disclosures on every row (never blank) and persists each as
    a ``ViewExpression`` (config JSON envelope + disclosure columns). Does NOT
    commit. Raises :class:`ExpressionDispatchError` when no archetype applies to a
    requested tier or a disclosure is missing.
    """
    view_type = str(getattr(view.view_type, "value", view.view_type))
    if view_type not in _TIER_PLAN:
        raise ExpressionDispatchError(
            f"no tier plan for view_type {view_type!r}; expected one of "
            f"{tuple(_TIER_PLAN)}."
        )

    pool = {a.key for a in catalog.archetypes_for_view_type(view_type)}
    syms = _resolve_view_symbols(db, view)

    # A view that NAMES a tradeable MCX commodity uses the COMMODITY menu (CM1–CM6
    # surfaced first); everything else keeps the equity/index plan unchanged.
    if syms.commodity is not None and view_type in _COMMODITY_TIER_PLAN:
        plan = _COMMODITY_TIER_PLAN[view_type]
    else:
        plan = _TIER_PLAN[view_type]

    rows: list[ViewExpression] = []
    for t in _tiers_to_build(tier):
        rows.append(_build_tier(db, view, view_type, t, plan, pool, syms))

    db.flush()
    return rows


def _build_tier(
    db: "Session",
    view: "MarketView",
    view_type: str,
    tier: str,
    plan: dict[str, tuple[str, ...]],
    pool: set[str],
    syms: _ViewSymbols,
) -> "ViewExpression":
    """Build + persist the single best expression for one tier (first preference
    that constructs honestly). Raises :class:`ExpressionDispatchError` when none
    of the tier's preferences can be expressed."""
    attempts: list[str] = []
    for key in plan.get(tier, ()):
        archetype = catalog.get_archetype(key)
        if archetype is None or key not in pool:
            continue
        if not _archetype_applies(view, archetype):
            continue

        knobs = tiers.tier_knobs(archetype.expression_kind, tier)
        builder: Callable[..., dict[str, Any]] = builders.BUILDERS[
            archetype.expression_kind
        ]
        try:
            ctx = _build_ctx(view, archetype, tier, knobs, syms)
            envelope = builder(db, view, archetype, tier, **ctx)
        except _BUILD_FALLBACK_ERRORS as exc:
            attempts.append(f"{key}: {type(exc).__name__}: {exc}")
            continue

        # 3) Timing SPEC (mapping only — no workflow created).
        spec = timing.timing_to_trigger(
            view, cast("timing.TimingMode", knobs.timing_default)
        )
        spec["rebalance"] = knobs.rebalance
        envelope["timing"] = spec

        # 4) Disclosures (enforced non-blank, same gate as curation).
        disclosures = _disclosures(view, archetype, tier, knobs, envelope)
        _enforce_disclosures(
            disclosures, where=f"{view_type}/{tier}/{key}"
        )

        # 5) Persist (no commit).
        row = ViewExpression(
            view_id=view.id,
            tier=ExpressionTier(tier),
            expression_kind=ExpressionKind(archetype.expression_kind),
            config=envelope,
            rationale=disclosures["rationale"],
            risk_profile=disclosures["risk_profile"],
            capital_intensity=disclosures["capital_intensity"],
            historical_strength=disclosures["historical_strength"],
            time_horizon=disclosures["time_horizon"],
        )
        db.add(row)
        db.flush()
        return row

    raise ExpressionDispatchError(
        f"no archetype could express a {view_type} view at the {tier} tier "
        f"(attempted: {attempts or 'none in plan/pool'})."
    )


def _archetype_applies(view: "MarketView", archetype: "Archetype") -> bool:
    """Applicability gate (spec step 1). The §5 tier plan already encodes which
    archetype fits which tier, so this stays a permissive seam: it returns ``True``
    unless an archetype declares a hard requirement the view can't meet. Kept as a
    real predicate hook so future category/keyword gating layers in here without
    touching the (pure-data) catalog."""
    return True


# ════════════════════════════════════════════════════════════════════════════
# Frozen helpers (disclosure-enforcement + tier resolution)
# ════════════════════════════════════════════════════════════════════════════


def _tiers_to_build(tier: Optional[str]) -> tuple[str, ...]:
    """Resolve the requested tier(s): all three when ``None``, else the one."""
    if tier is None:
        return ALL_TIERS
    if tier not in ALL_TIERS:
        raise ExpressionDispatchError(
            f"unknown tier {tier!r}; expected one of {ALL_TIERS}"
        )
    return (tier,)


def _enforce_disclosures(disclosures: Mapping[str, object], *, where: str) -> None:
    """Raise :class:`ExpressionDispatchError` if any required disclosure is blank.

    Mirrors ``curation._missing_disclosures`` (whitespace-only counts as blank)
    so dispatch-built expressions pass the same publish gate as hand-curated ones.
    """
    missing = [
        f for f in DISCLOSURE_FIELDS
        if disclosures.get(f) is None
        or (isinstance(disclosures.get(f), str) and not str(disclosures[f]).strip())
    ]
    if missing:
        raise ExpressionDispatchError(
            f"{where}: built expression is missing required disclosures: "
            f"{', '.join(missing)} (a buildable expression must never ship blank)."
        )


def _timing_mode_for_tier(tier: str) -> str:
    """The default timing mode for a tier (Cons=Confirmation, Bal=Hybrid,
    Aggr=Pre-position) — read from a representative tier-knob row."""
    return tiers.tier_knobs(catalog.KIND_BASKET, tier).timing_default


__all__ = [
    "ALL_TIERS",
    "ExpressionDispatchError",
    "suggest_expressions",
    "timing",
]
