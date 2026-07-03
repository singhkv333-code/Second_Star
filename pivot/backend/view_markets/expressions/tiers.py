"""View Markets — Phase 3 tier knobs (Conservative / Balanced / Aggressive).

Tiering is ONE pipeline with different knobs (spec §1.4, §5): Conservative /
Balanced / Aggressive are not three engines — they are knob settings (capital
intensity, leverage, hedge ratio, # legs, option moneyness, pair z-thresholds,
basket concentration / single-name cap, rebalance cadence, gold sleeve) on the
same object, keyed by ``(expression_kind, tier)``.

This is the single place those knobs live (DATA), transcribed from the §5
per-kind tier tables in ``Markdowns/VIEW_MARKETS_STRATEGY_DESIGN.md``. Builders
call ``tier_knobs(kind, tier)`` and read only the fields relevant to their kind
(option builders read ``option_moneyness`` / ``n_legs``; pair builders read the
``pair_z_*`` bands; basket builders read ``single_name_cap`` /
``basket_concentration`` / ``rebalance``; multi-asset reads ``gold_sleeve_pct``).

Pure data + one getter — no I/O. ``tier`` / ``expression_kind`` strings equal
the ``backend.models`` enum values.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from backend.view_markets.expressions.catalog import (
    KIND_BASKET,
    KIND_HEDGE,
    KIND_MULTI_ASSET,
    KIND_OPTION,
    KIND_PAIR,
)

CONSERVATIVE = "conservative"
BALANCED = "balanced"
AGGRESSIVE = "aggressive"


@dataclass(frozen=True)
class TierKnobs:
    """The knob settings for one ``(expression_kind, tier)`` cell.

    Not every field applies to every kind; unused fields stay ``None``. The
    spec §5 dimensions map as:

      * ``capital_intensity`` — "Premium-capped (small)" / "~2× gross" / "larger".
      * ``leverage`` — "none" / "defined (spread width)" / "multi-lot futures".
      * ``hedge_ratio`` — "full (defined-risk)" / "beta-neutral" / "partial".
      * ``n_legs`` — structural leg count guidance ("2", "2-4", "many").
      * ``option_moneyness`` — strike posture ("otm_wing" / "atm" / "atm_debit").
      * ``pair_z_entry`` / ``pair_z_exit`` / ``pair_z_stop`` — the z-bands
        (engine defaults 2.0 / 0.5 / 4.0; widened/tightened per tier).
      * ``basket_concentration`` — weighting scheme bias name.
      * ``single_name_cap`` — hard cap fraction (0.10 / 0.15 / 0.20 per §5/§4.3).
      * ``rebalance`` — cadence ("semi_annual" / "quarterly_drift" / "drift_wide").
      * ``gold_sleeve_pct`` — multi-asset gold weight (0.09 / 0.05 / 0.025).
      * ``timing_default`` — Conservative=Confirmation, Balanced=Hybrid,
        Aggressive=Pre-position (spec §5 EVENT/THEME rows).
      * ``size_cut`` — Pre-position size reduction on high-uncertainty events
        (30–50%); only meaningful when timing is pre-position.
    """

    capital_intensity: str
    leverage: str
    hedge_ratio: str
    n_legs: str
    timing_default: str
    option_moneyness: Optional[str] = None
    pair_z_entry: Optional[float] = None
    pair_z_exit: Optional[float] = None
    pair_z_stop: Optional[float] = None
    basket_concentration: Optional[str] = None
    single_name_cap: Optional[float] = None
    rebalance: Optional[str] = None
    gold_sleeve_pct: Optional[float] = None
    size_cut: Optional[float] = None


# ════════════════════════════════════════════════════════════════════════════
# The §5 tables, keyed by (expression_kind, tier).
# ════════════════════════════════════════════════════════════════════════════

TIER_KNOBS: dict[tuple[str, str], TierKnobs] = {
    # ── OPTION strategies (EVENT §5 rows + relative-options) ────────────────
    (KIND_OPTION, CONSERVATIVE): TierKnobs(
        capital_intensity="premium_capped_small",
        leverage="none",
        hedge_ratio="full_defined_risk",
        n_legs="2",
        timing_default="confirmation",
        option_moneyness="otm_credit_wing",   # bull-put / bear-call credit spread
    ),
    (KIND_OPTION, BALANCED): TierKnobs(
        capital_intensity="defined_spread_width",
        leverage="defined",
        hedge_ratio="defined_risk",
        n_legs="2-4",
        timing_default="hybrid",
        option_moneyness="atm_spread",         # iron fly/condor or ATM vertical
    ),
    (KIND_OPTION, AGGRESSIVE): TierKnobs(
        capital_intensity="larger_first_tranche",
        leverage="defined_atm_larger",
        hedge_ratio="partial_event_tail",
        n_legs="1-4",
        timing_default="pre_position",
        option_moneyness="atm_debit",          # ATM debit spread / straddle / ratio
        size_cut=0.4,                          # cut 30-50% on high-uncertainty events
    ),
    # ── PAIR (RELATIVE §5 rows; z-bands off engine defaults 2.0/0.5/4.0) ────
    (KIND_PAIR, CONSERVATIVE): TierKnobs(
        capital_intensity="etf_long_plus_one_index_future",
        leverage="premium_or_single_future",
        hedge_ratio="beta_adjusted_index",
        n_legs="2",
        timing_default="confirmation",
        pair_z_entry=2.5, pair_z_exit=0.5, pair_z_stop=4.0,  # wider entry = fewer, cleaner
    ),
    (KIND_PAIR, BALANCED): TierKnobs(
        capital_intensity="two_leg_2x_gross",
        leverage="beta_hedged_1to1",
        hedge_ratio="beta_neutral_residual_zero",
        n_legs="2",
        timing_default="hybrid",
        pair_z_entry=2.0, pair_z_exit=0.5, pair_z_stop=4.0,  # engine defaults
    ),
    (KIND_PAIR, AGGRESSIVE): TierKnobs(
        capital_intensity="heavy_many_ssf_lots",
        leverage="multi_lot_futures",
        hedge_ratio="beta_plus_sector_neutral",
        n_legs="many",
        timing_default="pre_position",
        pair_z_entry=1.75, pair_z_exit=0.4, pair_z_stop=4.5,  # earlier entry, wider stop
    ),
    # ── BASKET (THEME §5 rows: universe/weighting/cap/rebalance) ────────────
    (KIND_BASKET, CONSERVATIVE): TierKnobs(
        capital_intensity="cash_long_etf_proxy_or_pure_play",
        leverage="none",
        hedge_ratio="full_cash",
        n_legs="basket",
        timing_default="confirmation",
        basket_concentration="risk_parity",    # equal risk, not equal capital
        single_name_cap=0.10,
        rebalance="semi_annual",
    ),
    (KIND_BASKET, BALANCED): TierKnobs(
        capital_intensity="cash_long_basket",
        leverage="none",
        hedge_ratio="full_cash",
        n_legs="basket",
        timing_default="hybrid",
        basket_concentration="purity_scaled_mcap",  # free-float mcap × conviction
        single_name_cap=0.15,
        rebalance="quarterly_drift",
    ),
    (KIND_BASKET, AGGRESSIVE): TierKnobs(
        capital_intensity="cash_long_basket_concentrated",
        leverage="none",
        hedge_ratio="full_cash",
        n_legs="basket",
        timing_default="pre_position",
        basket_concentration="factor",          # momentum+quality / black_litterman
        single_name_cap=0.20,
        rebalance="drift_wide_momentum_refresh",
    ),
    # ── MULTI_ASSET (THEME §4.6 gold sleeves) ───────────────────────────────
    (KIND_MULTI_ASSET, CONSERVATIVE): TierKnobs(
        capital_intensity="cash_multi_sleeve",
        leverage="none",
        hedge_ratio="collar_or_protective_put",
        n_legs="2-3_sleeves",
        timing_default="confirmation",
        basket_concentration="risk_parity",
        single_name_cap=0.10,
        rebalance="semi_annual",
        gold_sleeve_pct=0.09,                   # 8-10% gold
    ),
    (KIND_MULTI_ASSET, BALANCED): TierKnobs(
        capital_intensity="cash_multi_sleeve",
        leverage="none",
        hedge_ratio="covered_call_financed_put",
        n_legs="2-3_sleeves",
        timing_default="hybrid",
        basket_concentration="purity_scaled_mcap",
        single_name_cap=0.15,
        rebalance="quarterly_drift",
        gold_sleeve_pct=0.05,                   # 5% gold
    ),
    (KIND_MULTI_ASSET, AGGRESSIVE): TierKnobs(
        capital_intensity="cash_multi_sleeve",
        leverage="defined_long_call_spread",
        hedge_ratio="long_call_spread_convexity",
        n_legs="2-3_sleeves",
        timing_default="pre_position",
        basket_concentration="factor",
        single_name_cap=0.20,
        rebalance="drift_wide_momentum_refresh",
        gold_sleeve_pct=0.025,                  # 2-3% gold tail hedge only
    ),
    # ── HEDGE (THEME §4.5 index-level overlay) ──────────────────────────────
    (KIND_HEDGE, CONSERVATIVE): TierKnobs(
        capital_intensity="premium_or_zero_cost",
        leverage="none",
        hedge_ratio="full_floor",
        n_legs="1-2",
        timing_default="confirmation",
        option_moneyness="zero_cost_collar",    # floor + finance (Nifty)
    ),
    (KIND_HEDGE, BALANCED): TierKnobs(
        capital_intensity="covered_call_financed",
        leverage="defined",
        hedge_ratio="partial_financed",
        n_legs="2",
        timing_default="hybrid",
        option_moneyness="covered_call_financed_put",
    ),
    (KIND_HEDGE, AGGRESSIVE): TierKnobs(
        capital_intensity="premium_only",
        leverage="defined_premium",
        hedge_ratio="convex_upside",
        n_legs="1-2",
        timing_default="pre_position",
        option_moneyness="long_call_spread",    # convexity, capital-light
    ),
}


def tier_knobs(expression_kind: str, tier: str) -> TierKnobs:
    """Return the :class:`TierKnobs` for one ``(expression_kind, tier)``.

    Raises ``KeyError`` for an unknown combination so a builder never silently
    runs on default knobs (every kind×tier the catalog can produce MUST have a
    row above). ``expression_kind`` / ``tier`` are the enum *values*.
    """
    try:
        return TIER_KNOBS[(expression_kind, tier)]
    except KeyError as exc:
        raise KeyError(
            f"no tier knobs for (kind={expression_kind!r}, tier={tier!r}); "
            "every expression_kind×tier the catalog emits must be defined in "
            "TIER_KNOBS."
        ) from exc


__all__ = [
    "CONSERVATIVE",
    "BALANCED",
    "AGGRESSIVE",
    "TierKnobs",
    "TIER_KNOBS",
    "tier_knobs",
]
