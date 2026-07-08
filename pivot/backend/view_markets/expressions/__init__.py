"""View Markets — Phase 3 EXPRESSION ENGINE (view → tiered, deployable strategies).

Turns a curated ``MarketView`` into proper Conservative/Balanced/Aggressive
strategies — EXPLICITLY NOT "always a simple basket" (spec
``Markdowns/VIEW_MARKETS_STRATEGY_DESIGN.md``). Mirrors the repo's own pattern
(``option_strategies.TEMPLATES`` dict, ``weighting`` scheme dispatch): a
strategy's difference lives in a declarative CATALOG entry + per-``expression_kind``
BUILDER, never one ``.py`` per strategy.

Module map::

    config_schema  — the pinned ViewExpression.config JSON envelope (one place)
    catalog        — ARCHETYPE_CATALOG: frozen archetype registry (DATA)
    tiers          — TIER_KNOBS per (expression_kind, tier) (DATA) + tier_knobs()
    honest_short   — no-retail-delivery-short rule + AVOID type + commodity short
    commodities    — MCX commodity universe + is_fno/lot-size + leverage-note convention
    screens        — theme purity + liquidity + single_name_cap + min-names floor
    cross_sectional— decile/rank + FACTOR_ETF_MAP (factor → smart-beta ETF)
    merger_arb     — open-offer/buyback spread + break-prob + annualized + proration
    timing         — Pre-position/Confirmation/Hybrid → workflow trigger SPEC (map only)
    builders/      — option / pair / basket / multi_asset / hedge (delegate to engines)
    dispatch       — suggest_expressions(db, view, tier?) → list[ViewExpression]
                     (the ONE public entry point; enforces disclosures, persists)

Invariants (every builder): register-not-execute (expressions deploy as ARMED
workflows the user confirms); never fabricate (degrade to AVOID/honest when an
instrument isn't tradeable); India microstructure hard-coded (weeklies =
NIFTY/SENSEX only, BANKNIFTY monthly; single-stock options monthly + physical +
STT-on-intrinsic; foreign → listed ETF proxy); MCX commodities are TRADEABLE via
register-not-execute (since 2026-06-29) and SHORTABLE via MCX futures — so they
are LEVERAGED: every commodity expression carries a leverage note
(``commodities.LEVERAGE_NOTE``) and is never auto-sized, and a direct-MCX
pair/basket degrades to construct-only when the data layer has no commodity OHLCV;
defined-risk first (stated max loss).

Importing this package is side-effect-free (pure data + lazy heavy imports inside
builder functions); no DB connect, no scheduler, no network.
"""
from __future__ import annotations

from backend.view_markets.expressions import (
    builders,
    catalog,
    commodities,
    config_schema,
    cross_sectional,
    dispatch,
    honest_short,
    merger_arb,
    screens,
    tiers,
    timing,
)
from backend.view_markets.expressions.catalog import (
    ARCHETYPE_CATALOG,
    Archetype,
)
from backend.view_markets.expressions.dispatch import suggest_expressions
from backend.view_markets.expressions.tiers import TIER_KNOBS, tier_knobs

__all__ = [
    # modules
    "config_schema",
    "catalog",
    "tiers",
    "honest_short",
    "commodities",
    "screens",
    "cross_sectional",
    "merger_arb",
    "timing",
    "builders",
    "dispatch",
    # public surface
    "ARCHETYPE_CATALOG",
    "Archetype",
    "TIER_KNOBS",
    "tier_knobs",
    "suggest_expressions",
]
