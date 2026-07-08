"""View Markets — Phase 3 archetype catalog (declarative registry, DATA).

Mirrors the repo's own ``option_strategies.TEMPLATES`` / ``weighting`` scheme
dispatch pattern: a strategy's *difference* lives in a **frozen catalog entry**
(kind + template/scheme + params + tier knobs + India guards), NEVER a bespoke
``.py`` per strategy. ~21 archetypes × 3 tiers collapse to this one table plus
five per-``expression_kind`` builders.

Source of truth: ``Markdowns/VIEW_MARKETS_STRATEGY_DESIGN.md`` — the EVENT menu
E1–E10 (§2), the RELATIVE family (§3: cointegrated pair / sector-vs-index /
factor-ETF-vs-index / ratio-RS / relative-options), the THEME family (§4:
purity-conviction basket / factor-tilt / optionized-hedged overlay /
multi-asset), and the §6 archetype→primitive EXISTS|GAP map. The COMMODITY family
(CM1–CM6) was added in the MCX pass (2026-06-29) once commodities became
tradeable via register-not-execute: directional/straddle MCX options,
producer-vs-importer + gold-vs-silver pairs, and direct-MCX-sleeve multi-asset.
Commodities are LEVERAGED — every commodity expression carries a leverage note
(``commodities.LEVERAGE_NOTE``) and is never auto-sized.

This module is pure data + lookup helpers — no I/O, no heavy imports, importable
in microseconds. ``view_types`` / ``expression_kind`` / ``tier`` are stored as
plain strings that equal the ``backend.models`` enum *values*
(``ViewType``/``ExpressionKind``/``ExpressionTier``) so dispatch can map them
without importing the ORM here.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

# View-type string constants (== backend.models.ViewType values).
EVENT = "event"
RELATIVE = "relative"
THEME = "theme"

# Expression-kind string constants (== backend.models.ExpressionKind values).
KIND_OPTION = "option_strategy"
KIND_PAIR = "pair"
KIND_BASKET = "basket"
KIND_MULTI_ASSET = "multi_asset"
KIND_HEDGE = "hedge"


@dataclass(frozen=True)
class Archetype:
    """One strategy archetype — the declarative unit the engine dispatches on.

    Fields
    ------
    key
        Stable identifier (used in ``config.archetype``, traces, tests). The
        EVENT archetypes keep their spec id prefix (``E1_…``).
    label
        Human card title.
    view_types
        Which ``ViewType`` value(s) this archetype can express.
    expression_kind
        Which ``ExpressionKind`` builder handles it (the dispatch key).
    template_or_scheme
        The concrete primitive selector handed to the builder: an
        ``option_strategies.TEMPLATES`` key (option kinds), a ``weighting``
        scheme (basket kinds), ``"engle_granger"`` (pairs), or ``None`` when the
        builder composes its own (multi-asset / merger).
    params
        Static knobs the builder reads (e.g. ``{"timing": "pre_position",
        "size_cut": 0.4}``); tier-varying knobs come from ``tiers.tier_knobs``.
    applies_when
        Human-readable applicability gate (category/keyword hints the dispatch
        matcher uses — a real predicate is layered in INTEGRATE, kept as a
        documented tag in the skeleton so the catalog stays pure-data).
    required_primitive
        The real Pivot module the builder delegates to (for the EXISTS|GAP map).
    status
        ``"EXISTS"`` (primitive is built) or ``"GAP"`` (glue/new-calc needed,
        per §6) — surfaced so the build order is honest.
    timing_default
        Default ``timing.TimingMode`` for this archetype's spec.
    """

    key: str
    label: str
    view_types: tuple[str, ...]
    expression_kind: str
    template_or_scheme: Optional[str]
    params: dict[str, object] = field(default_factory=dict)
    applies_when: str = ""
    required_primitive: str = ""
    status: str = "EXISTS"
    timing_default: str = "confirmation"


# ════════════════════════════════════════════════════════════════════════════
# The registry — one entry per archetype (DATA).
# ════════════════════════════════════════════════════════════════════════════

_ARCHETYPES: tuple[Archetype, ...] = (
    # ── EVENT (E1–E10) ──────────────────────────────────────────────────────
    Archetype(
        key="E1_rate_debit_spread",
        label="Rate-event defined-risk directional (bull-call debit spread)",
        view_types=(EVENT,),
        expression_kind=KIND_OPTION,
        template_or_scheme="bull_call_spread",
        params={"size_cut": 0.4, "default_underlying": "BANKNIFTY"},
        applies_when="rate event (RBI MPC / budget) with directional outcome+expression conf",
        required_primitive="services.option_strategies (TEMPLATES + greeks/POP/margin)",
        status="EXISTS",
        timing_default="pre_position",
    ),
    Archetype(
        key="E2_nbfc_bank_pair",
        label="NBFC-vs-bank rate pair (transmission asymmetry, beta-stripped)",
        view_types=(EVENT, RELATIVE),
        expression_kind=KIND_PAIR,
        template_or_scheme="engle_granger",
        params={"leg_a_tag": "nbfc", "leg_b_tag": "private_bank"},
        applies_when="rate view, HIGH outcome / LOW expression conf (the rate trap)",
        required_primitive="services.backtest.pairs (EG/OU) + sector_universe",
        status="EXISTS",
        timing_default="confirmation",
    ),
    Archetype(
        key="E3_event_straddle",
        label="Event straddle / strangle (realized > priced)",
        view_types=(EVENT,),
        expression_kind=KIND_OPTION,
        template_or_scheme="long_straddle",
        params={"alt_template": "long_strangle", "close_into_crush": True},
        applies_when="LOW outcome conf, expect a big move either way",
        required_primitive="services.option_strategies (straddle/strangle)",
        status="EXISTS",
        timing_default="pre_position",
    ),
    Archetype(
        key="E4_iv_crush_harvest",
        label="IV-crush harvest (iron condor / fly, defined-risk; or calendar)",
        view_types=(EVENT,),
        expression_kind=KIND_OPTION,
        template_or_scheme="iron_condor",
        params={"alt_template": "iron_butterfly", "defined_risk_only": True},
        applies_when="believe realized < priced; term-structure rich pre-print",
        required_primitive="services.option_strategies (iron_condor/iron_butterfly)",
        status="EXISTS",
        timing_default="pre_position",
    ),
    Archetype(
        key="E5_pead_drift",
        label="PEAD drift (SUE/EAR rank, enter day +2, long-tilted) / post-crush vertical",
        view_types=(EVENT,),
        expression_kind=KIND_BASKET,
        template_or_scheme="factor",
        params={"entry_day": 2, "hold_days": 60, "long_only": True,
                "option_alt": "bull_call_spread"},
        applies_when="direction known AFTER the print (earnings)",
        required_primitive="propose_basket_allocation (long) + earnings event trigger",
        status="GAP",  # surprise/consensus feed is the dominant gap (§6)
        timing_default="confirmation",
    ),
    Archetype(
        key="E6_broken_wing",
        label="Broken-wing butterfly (directional lean at near-zero debit)",
        view_types=(EVENT,),
        expression_kind=KIND_OPTION,
        template_or_scheme="broken_wing_butterfly",
        params={"capped_premium": True},
        applies_when="strong fundamental view, capped premium",
        required_primitive="services.option_strategies (broken-wing) — GAP template",
        status="GAP",  # broken_wing not yet a TEMPLATES key; builder composes legs
        timing_default="pre_position",
    ),
    Archetype(
        key="E7_merger_arb",
        label="Merger / open-offer arb (long-only, tender at offer)",
        view_types=(EVENT,),
        expression_kind=KIND_BASKET,
        template_or_scheme="equal",
        params={"long_only": True, "tender": True},
        applies_when="deal-completion view; open offer / buyback retail quota",
        required_primitive="merger_arb (spread/break-prob calc) + basket engine",
        status="GAP",
        timing_default="pre_position",
    ),
    Archetype(
        key="E8_index_inclusion",
        label="Index-inclusion front-run (long the add into effective date)",
        view_types=(EVENT,),
        expression_kind=KIND_BASKET,
        template_or_scheme="mcap",
        params={"long_add": True, "short_delete_if_fno": True},
        applies_when="announced index add/delete; predictable passive flow",
        required_primitive="basket engine + event trigger",
        status="GAP",  # corporate-actions feed missing
        timing_default="pre_position",
    ),
    Archetype(
        key="E9_budget_election_rotation",
        label="Budget / election thematic rotation (risk-weighted sector basket + vol hedge)",
        view_types=(EVENT, THEME),
        expression_kind=KIND_BASKET,
        template_or_scheme="risk_parity",
        params={"scenario_seed": "thematic_map", "vol_hedge": "nifty_put"},
        applies_when="mandate/policy surprise with big sector dispersion",
        required_primitive="thematic_map + propose_basket_allocation + index options",
        status="EXISTS",
        timing_default="confirmation",
    ),
    Archetype(
        key="E10_shock_hedged_basket",
        label="Commodity / geopolitical-shock hedged basket (gold + defensives + collar)",
        view_types=(EVENT, THEME),
        expression_kind=KIND_MULTI_ASSET,
        template_or_scheme=None,
        params={"gold_sleeve": True, "energy_importer_pair": True,
                "portfolio_hedge": "collar"},
        applies_when="unscheduled supply shock; India is a net energy importer",
        required_primitive="weighting + pairs + option_strategies + pre-armed trigger",
        status="EXISTS",
        timing_default="confirmation",
    ),
    # ── RELATIVE ────────────────────────────────────────────────────────────
    Archetype(
        key="R1_cointegrated_pair",
        label="Cointegrated pair (the flagship non-basket expression)",
        view_types=(RELATIVE,),
        expression_kind=KIND_PAIR,
        template_or_scheme="engle_granger",
        params={"rigor_tier": 1},  # highest score ceiling
        applies_when="A-beats-B with a stable hedge ratio + stationary spread",
        required_primitive="services.backtest.pairs (EG/Johansen/OU/z-bands)",
        status="EXISTS",
        timing_default="confirmation",
    ),
    Archetype(
        key="R2_sector_vs_index",
        label="Sector-vs-index (sector basket leg A vs index leg B, beta-adjusted)",
        view_types=(RELATIVE,),
        expression_kind=KIND_PAIR,
        template_or_scheme="engle_granger",
        params={"rigor_tier": 2, "leg_b": "NIFTY"},
        applies_when="sector rotation / RS view vs the index",
        required_primitive="sector_universe + propose_basket_allocation + pairs",
        status="EXISTS",
        timing_default="confirmation",
    ),
    Archetype(
        key="R3_factor_etf_vs_index",
        label="Factor smart-beta ETF vs index future (the realistic retail factor tilt)",
        view_types=(RELATIVE,),
        expression_kind=KIND_PAIR,
        template_or_scheme=None,
        params={"rigor_tier": 3, "short_leg": "index_future"},
        applies_when="momentum/value/quality/low-vol/multi-factor tilt vs NIFTY",
        required_primitive="cross_sectional.FACTOR_ETF_MAP + honest_short (index future)",
        status="GAP",
        timing_default="confirmation",
    ),
    Archetype(
        key="R4_ratio_rs",
        label="Ratio / relative-strength (graceful degrade when ADF fails)",
        view_types=(RELATIVE,),
        expression_kind=KIND_PAIR,
        template_or_scheme="rolling_zscore",
        params={"rigor_tier": 4, "lower_alignment_ceiling": True},
        applies_when="relative view but cointegration/stationarity NOT proven",
        required_primitive="services.backtest.pairs.rolling_zscore (on the ratio)",
        status="EXISTS",
        timing_default="confirmation",
    ),
    Archetype(
        key="R5_relative_options",
        label="Relative via options (two-sided vertical pair — most India-legal)",
        view_types=(RELATIVE,),
        expression_kind=KIND_OPTION,
        template_or_scheme="bull_call_spread",
        params={"leg_b_template": "bear_put_spread", "two_underlying": True,
                "rigor_tier": 3},
        applies_when="A-beats-B expressible with defined-risk option spreads, no short stock",
        required_primitive="services.option_strategies (combined two-underlying) — GAP card",
        status="GAP",
        timing_default="confirmation",
    ),
    # ── THEME ───────────────────────────────────────────────────────────────
    Archetype(
        key="T1_purity_conviction_basket",
        label="Purity / conviction-weighted basket (the replacement for flat)",
        view_types=(THEME,),
        expression_kind=KIND_BASKET,
        template_or_scheme="risk_parity",
        params={"purity_weighted": True, "conviction_tiers": True},
        applies_when="long structural theme expressible as a screened equity basket",
        required_primitive="screens (purity/liquidity/cap) + weighting + sector_universe",
        status="GAP",  # purity + liquidity + single_name_cap are new layers
        timing_default="confirmation",
    ),
    Archetype(
        key="T2_factor_tilt",
        label="Factor tilt within the theme (multi-factor picks names in the universe)",
        view_types=(THEME,),
        expression_kind=KIND_BASKET,
        template_or_scheme="factor",
        params={"composite": ["value", "momentum", "quality"]},
        applies_when="theme defines the universe, factor selects/over-weights names",
        required_primitive="weighting.factor (composite) + cross_sectional",
        status="EXISTS",
        timing_default="confirmation",
    ),
    Archetype(
        key="T3_optionized_hedged",
        label="Optionized / hedged overlay (index-level Nifty hedge on the theme)",
        view_types=(THEME,),
        expression_kind=KIND_HEDGE,
        template_or_scheme="collar",
        params={"alt_templates": ["protective_put", "covered_call"],
                "hedge_index": "NIFTY"},
        applies_when="shape the directional theme payoff with a defended index hedge",
        required_primitive="services.option_strategies (collar/put/covered call) at index",
        status="EXISTS",
        timing_default="confirmation",
    ),
    Archetype(
        key="T4_multi_asset",
        label="Multi-asset theme (equity + gold ETF + hedge, risk-parity sleeves)",
        view_types=(THEME,),
        expression_kind=KIND_MULTI_ASSET,
        template_or_scheme=None,
        params={"gold_sleeve_pct": {"conservative": 0.09, "balanced": 0.05,
                                    "aggressive": 0.025}},
        applies_when="cross-asset theme; gold is the canonical India diversifier",
        required_primitive="weighting (asset-class risk_parity) + gold ETF + hedge",
        status="EXISTS",
        timing_default="confirmation",
    ),
    # ── COMMODITY (MCX — tradeable via register-not-execute as of 2026-06-29) ──
    # Commodities are LEVERAGED: every commodity expression carries the leverage
    # note (``commodities.LEVERAGE_NOTE``) and is NEVER auto-sized. The option /
    # chain / paper layers already handle MCX underlyings (research_only lifted);
    # the pairs/basket DATA layer does NOT carry direct-MCX OHLCV, so direct
    # commodity pairs/baskets degrade to construct-only (the ETF-proxy route
    # backtests) — see ``commodities.price_history_available``.
    Archetype(
        key="CM1_commodity_directional_option",
        label="Commodity directional (defined-risk MCX option — crude/gold/metals)",
        view_types=(EVENT, THEME),
        expression_kind=KIND_OPTION,
        template_or_scheme="bull_call_spread",
        params={"commodity": True, "default_underlying": "CRUDEOIL",
                "leverage_note": True, "alt_bearish_template": "bear_put_spread"},
        applies_when="directional commodity view (crude/gold/silver/metals) with a defined-risk option",
        required_primitive="services.option_strategies (MCX underlyings) + implied_move",
        status="EXISTS",
        timing_default="confirmation",
    ),
    Archetype(
        key="CM2_commodity_event_straddle",
        label="Commodity event straddle/strangle (inventory / OPEC / FOMC vol)",
        view_types=(EVENT,),
        expression_kind=KIND_OPTION,
        template_or_scheme="long_straddle",
        params={"commodity": True, "default_underlying": "CRUDEOIL",
                "alt_template": "long_strangle", "leverage_note": True},
        applies_when="scheduled commodity vol event (EIA/OPEC/Fed) — realized > priced either way",
        required_primitive="services.option_strategies (MCX straddle/strangle) + implied_move",
        status="EXISTS",
        timing_default="pre_position",
    ),
    Archetype(
        key="CM3_commodity_producer_vs_importer_pair",
        label="Commodity producer-vs-importer pair (upstream vs OMC, crude-driven)",
        view_types=(EVENT, RELATIVE),
        expression_kind=KIND_PAIR,
        template_or_scheme="engle_granger",
        params={"commodity": True, "leg_a_role": "producer", "leg_b_role": "refiner",
                "crude_intent": "crude_up", "leverage_note": True,
                "direct_future_variant": "CRUDEOIL"},
        applies_when="crude-direction view: long upstream producers vs short OMC importers (margin asymmetry)",
        required_primitive="sector_universe (crude_up/down_beneficiaries, oil_role) + pairs (EG/OU)",
        status="EXISTS",  # equity producer-vs-importer legs backtest; the direct-crude-future variant degrades
        timing_default="confirmation",
    ),
    Archetype(
        key="CM4_gold_silver_ratio_pair",
        label="Gold-vs-silver ratio pair (direct MCX bullion, or GOLDBEES/SILVERBEES proxy)",
        view_types=(RELATIVE,),
        expression_kind=KIND_PAIR,
        template_or_scheme="rolling_zscore",
        params={"commodity": True, "leg_a": "GOLD", "leg_b": "SILVER",
                "etf_proxy_legs": ["GOLDBEES", "SILVERBEES"],
                "lower_alignment_ceiling": True, "leverage_note": True},
        applies_when="gold/silver ratio (mean-reverting bullion spread); direct MCX legs are construct-only",
        required_primitive="commodities (GOLD_SILVER legs) + pairs.rolling_zscore (ETF-proxy route backtests)",
        status="EXISTS",
        timing_default="confirmation",
    ),
    Archetype(
        key="CM5_commodity_multi_asset",
        label="Commodity multi-asset sleeve (equity + DIRECT MCX gold/silver + hedge)",
        view_types=(THEME,),
        expression_kind=KIND_MULTI_ASSET,
        template_or_scheme=None,
        params={"commodity": True, "direct_mcx_sleeve": "GOLD",
                "etf_proxy_sleeve": "GOLDBEES", "leverage_note": True},
        applies_when="cross-asset theme adding a DIRECT MCX bullion sleeve alongside the gold-ETF route",
        required_primitive="multi_asset builder + commodities (direct MCX leg) + gold ETF + hedge",
        status="EXISTS",
        timing_default="confirmation",
    ),
    Archetype(
        key="CM6_crude_shock_hedged_basket",
        label="Crude-shock hedged basket (defensives + DIRECT MCX crude leg + collar)",
        view_types=(EVENT, THEME),
        expression_kind=KIND_MULTI_ASSET,
        template_or_scheme=None,
        params={"commodity": True, "direct_crude_leg": "CRUDEOIL",
                "energy_importer_pair": True, "portfolio_hedge": "collar",
                "leverage_note": True},
        applies_when="energy supply shock; hedge a net-importer book with a DIRECT MCX crude long + defensives",
        required_primitive="multi_asset builder + commodities (crude leg) + pairs + option_strategies",
        status="EXISTS",
        timing_default="confirmation",
    ),
)

# Public registry keyed by archetype key (frozen-by-convention; do not mutate).
ARCHETYPE_CATALOG: dict[str, Archetype] = {a.key: a for a in _ARCHETYPES}


# ════════════════════════════════════════════════════════════════════════════
# Lookups (by view_type / kind / key)
# ════════════════════════════════════════════════════════════════════════════


def get_archetype(key: str) -> Optional[Archetype]:
    """Return the archetype for ``key`` (or ``None`` if unknown)."""
    return ARCHETYPE_CATALOG.get(key)


def archetypes_for_view_type(view_type: str) -> list[Archetype]:
    """All archetypes that can express the given ``ViewType`` value, in catalog
    (priority) order. ``view_type`` is the enum *value* (``"event"`` etc.)."""
    return [a for a in _ARCHETYPES if view_type in a.view_types]


def archetypes_for_kind(expression_kind: str) -> list[Archetype]:
    """All archetypes handled by one ``ExpressionKind`` builder."""
    return [a for a in _ARCHETYPES if a.expression_kind == expression_kind]


def existing_archetypes() -> list[Archetype]:
    """Archetypes whose required primitive is already built (status EXISTS)."""
    return [a for a in _ARCHETYPES if a.status == "EXISTS"]


__all__ = [
    "EVENT",
    "RELATIVE",
    "THEME",
    "KIND_OPTION",
    "KIND_PAIR",
    "KIND_BASKET",
    "KIND_MULTI_ASSET",
    "KIND_HEDGE",
    "Archetype",
    "ARCHETYPE_CATALOG",
    "get_archetype",
    "archetypes_for_view_type",
    "archetypes_for_kind",
    "existing_archetypes",
]
