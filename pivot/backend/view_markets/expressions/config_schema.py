"""View Markets — Phase 3 ViewExpression ``config`` JSON schema (pinned once).

Every expression the engine emits is persisted as a ``ViewExpression`` ORM row
(Phase-1 table) whose ``config`` JSONB column carries a **single, versioned
envelope** with the kind-specific structure inside ``structure``. Pinning the
envelope here (rather than letting each builder invent its own shape) is what
lets dispatch, the REST layer, the FE cards, and Phase-4 backtest wiring all
read one stable contract.

The five *disclosure* fields (``rationale`` / ``risk_profile`` /
``capital_intensity`` / ``historical_strength`` / ``time_horizon``) are NOT in
``config`` — they are first-class ``ViewExpression`` columns (see
``backend.models.ViewExpression``) and are enforced non-blank by
``curation._missing_disclosures`` and again by ``dispatch.suggest_expressions``.

Envelope (``config``)::

    {
      "schema_version": 1,
      "archetype": "E1_rate_debit_spread",      # catalog.Archetype.key
      "expression_kind": "option_strategy",      # mirrors the column
      "tier": "balanced",                        # mirrors the column
      "label": "Bank Nifty bull-call debit spread",
      "instruments": [                            # every leg/holding, India-typed
        {
          "symbol": "BANKNIFTY",
          "exchange": "NFO",                     # NSE | BSE | NFO | MCX
          "segment": "NFO-OPT",                  # NFO-OPT | EQ | NFO-FUT | ETF | INDEX | MCX-OPT | MCX-FUT
          "instrument_type": "index_option",     # see INSTRUMENT_TYPES
          "role": "long",                        # long | short | hedge | underlying
          "tradeable": true                      # false => honest-short degrade
        }
      ],
      "structure": { ... },                      # KIND-SPECIFIC (see STRUCTURE_KEYS)
      "timing": {                                # from timing.timing_to_trigger
        "mode": "pre_position",                  # pre_position | confirmation | hybrid
        "trigger_spec": { ... }                  # workflow trigger SPEC, NOT a created wf
      },
      "expressability": {                        # from honest_short / screens
        "symmetric": true,
        "degraded": false,
        "short_mode": null,                      # ssf_future|put|put_spread|index_future|index_put|AVOID
        "notes": []
      },
      "scores": {                                # CONSTRUCTION-TIME only (Phase 4 adds Trust)
        "construction_alignment": 72.0,          # 0..100, ceiling-tiered by construct rigor
        "basket_purity": null,                   # themes only
        "alignment_kind": "event_study"          # event_study | relative_value | basket_purity
      },
      "costs": { "round_trip_bps": 12.0, "note": "..." },
      "warnings": [ "BANKNIFTY is monthly-only (no weeklies as of 2026)" ],
      "disclaimer": "This is analysis, not financial advice. ..."
    }

``structure`` payloads by ``expression_kind`` (the builder owns the inner shape;
keys below are the agreed contract the cards read):

  * ``option_strategy`` — the resolved ``option_strategies.resolve_strategy``
    payload subset: ``{template, legs[], net_premium, max_loss, max_profit,
    pop, breakevens[], net_greeks{}, capital_required, payoff[], critique{}}``.
  * ``pair`` — ``{a, b, alpha, beta, half_life_days, adf_tstat, cointegrated_at,
    lookback, z_entry, z_exit, z_stop, leg_a{lots|shares,notional},
    leg_b{...}, residual_beta, short_leg{...honest_short...}, rigor_tier}``.
  * ``basket`` — ``{scheme, weights{sym:w}, purity{sym:score}, basket_purity,
    single_name_cap, min_names, n_names, liquidity{sym:{...}}, factor_tilt?,
    fallback_reason?, etf_proxy?}``.
  * ``multi_asset`` — ``{asset_class_scheme, sleeves:[{kind, weight, detail}]}``
    where ``kind`` ∈ {equity_basket, gold_etf, hedge}.
  * ``hedge`` — ``{underlying_index, hedge_template, legs[], max_loss, net_cost,
    floor_level?}``.

No fabricated numbers ever reach this envelope: when an instrument isn't
tradeable the builder sets ``instruments[*].tradeable = false`` and routes the
short leg through ``honest_short`` (``expressability.degraded = true``) rather
than inventing a price/short.
"""
from __future__ import annotations

from typing import Any, Literal, TypedDict

# Bump when the envelope shape changes incompatibly. Cards/backtest read this.
CONFIG_SCHEMA_VERSION: int = 1

# The five required disclosures live as ViewExpression COLUMNS, not in config.
# Mirrors ``curation._DISCLOSURE_FIELDS`` — kept in lock-step so dispatch can
# enforce the same set before persisting.
DISCLOSURE_FIELDS: tuple[str, ...] = (
    "rationale",
    "risk_profile",
    "capital_intensity",
    "historical_strength",
    "time_horizon",
)

# India-typed instrument vocabulary used on ``instruments[*].instrument_type``.
INSTRUMENT_TYPES: tuple[str, ...] = (
    "equity",            # cash/delivery single stock (NSE/BSE EQ)
    "etf",               # listed ETF (incl. smart-beta, foreign->ETF proxy)
    "gold_etf",          # gold / silver ETF (GOLDBEES / SILVERBEES)
    "index",             # an index level (NIFTY/BANKNIFTY/SENSEX) — reference only
    "index_future",      # NFO index future (the legal index short)
    "index_option",      # NFO index option (weeklies: NIFTY/SENSEX; BANKNIFTY monthly)
    "stock_future",      # single-stock future (SSF) — ~208 eligible names
    "stock_option",      # single-stock option — monthly + physical + STT-on-intrinsic
    "commodity_future",  # MCX commodity future (CRUDEOIL/GOLD/SILVER…) — LEVERAGED;
    #                      the symmetric long/short leg (commodities ARE shortable)
    "commodity_option",  # MCX commodity option — defined-risk directional / straddle
)

# MCX commodity segments. Commodities became tradeable via register-not-execute
# on 2026-06-29 (the "MCX research-only" block was lifted across the chain /
# safety / paper / instrument-master layers). A commodity instrument is
# LEVERAGED: every commodity expression MUST carry a leverage note and MUST NEVER
# auto-size — see ``expressions.commodities.LEVERAGE_NOTE``.
COMMODITY_SEGMENTS: tuple[str, ...] = ("MCX-OPT", "MCX-FUT", "MCX")

# The instrument-type values that mark a leg as an MCX commodity.
COMMODITY_INSTRUMENT_TYPES: tuple[str, ...] = ("commodity_future", "commodity_option")


def is_commodity_segment(segment: str | None) -> bool:
    """True when ``segment`` is an MCX commodity segment (MCX / MCX-OPT / MCX-FUT).

    Builders use this to decide whether the leverage-risk note convention applies
    (commodities are leveraged) and to route transaction costs through the MCX
    exchange rate (``trading_costs.OPT_EXCHANGE_PCT_MCX``).
    """
    return bool(segment) and str(segment).upper().startswith("MCX")

InstrumentRole = Literal["long", "short", "hedge", "underlying"]
TimingMode = Literal["pre_position", "confirmation", "hybrid"]
AlignmentKind = Literal["event_study", "relative_value", "basket_purity"]

# Structure keys are documented above; STRUCTURE_KEYS is the canonical set the
# tests assert a built expression carries per kind (the BUILD agents implement).
STRUCTURE_KEYS: dict[str, tuple[str, ...]] = {
    "option_strategy": (
        "template", "legs", "net_premium", "max_loss", "max_profit", "pop",
        "breakevens", "net_greeks", "capital_required",
    ),
    "pair": (
        "a", "b", "beta", "half_life_days", "z_entry", "z_exit", "z_stop",
        "leg_a", "leg_b", "residual_beta", "short_leg",
    ),
    "basket": (
        "scheme", "weights", "basket_purity", "single_name_cap", "n_names",
    ),
    "multi_asset": ("asset_class_scheme", "sleeves"),
    "hedge": ("underlying_index", "hedge_template", "legs", "max_loss"),
}


class InstrumentSpec(TypedDict, total=False):
    """One leg/holding in ``config.instruments`` — India-typed + tradeability."""

    symbol: str
    exchange: str          # NSE | BSE | NFO | MCX
    segment: str           # NFO-OPT | EQ | NFO-FUT | ETF | INDEX | MCX-OPT | MCX-FUT
    instrument_type: str   # one of INSTRUMENT_TYPES
    role: InstrumentRole
    tradeable: bool        # False => honest-short degraded this leg
    note: str


class ExpressionConfig(TypedDict, total=False):
    """The pinned ``ViewExpression.config`` envelope (documentation contract)."""

    schema_version: int
    archetype: str
    expression_kind: str
    tier: str
    label: str
    instruments: list[InstrumentSpec]
    structure: dict[str, Any]
    timing: dict[str, Any]
    expressability: dict[str, Any]
    scores: dict[str, Any]
    costs: dict[str, Any]
    warnings: list[str]
    disclaimer: str


# Single source for the closing disclaimer line every expression carries (spec
# §1: "every analysis ends 'this is analysis, not financial advice'").
DISCLAIMER: str = (
    "This is analysis, not financial advice. Pivot registers/arms intents only "
    "— you confirm and place every order in your own broker app. No live "
    "broker auto-execution; paper trading is simulated."
)


def base_envelope(
    *,
    archetype: str,
    expression_kind: str,
    tier: str,
    label: str,
) -> ExpressionConfig:
    """Return a fresh, minimally-populated config envelope for a builder to fill.

    Stamps ``schema_version`` / identity fields / the standard disclaimer and
    initialises the optional collections to empty so a builder only fills what
    it produces. The BUILD agents extend ``structure`` / ``instruments`` /
    ``timing`` / ``expressability`` / ``scores`` per kind.
    """
    return ExpressionConfig(
        schema_version=CONFIG_SCHEMA_VERSION,
        archetype=archetype,
        expression_kind=expression_kind,
        tier=tier,
        label=label,
        instruments=[],
        structure={},
        timing={},
        expressability={"symmetric": True, "degraded": False, "notes": []},
        scores={},
        costs={},
        warnings=[],
        disclaimer=DISCLAIMER,
    )


__all__ = [
    "CONFIG_SCHEMA_VERSION",
    "DISCLOSURE_FIELDS",
    "INSTRUMENT_TYPES",
    "COMMODITY_SEGMENTS",
    "COMMODITY_INSTRUMENT_TYPES",
    "is_commodity_segment",
    "STRUCTURE_KEYS",
    "DISCLAIMER",
    "InstrumentRole",
    "TimingMode",
    "AlignmentKind",
    "InstrumentSpec",
    "ExpressionConfig",
    "base_envelope",
]
