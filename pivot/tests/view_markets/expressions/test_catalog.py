"""Unit tests for the Phase-3 archetype catalog (``expressions.catalog``).

The catalog is **pure data + accessors** (no I/O), so these tests assert the
registry's *integrity* against the real Pivot primitives it dispatches on:

  * 25 archetypes seeded (E1–E10 + R1–R5 + T1–T4 + CM1–CM6), keyed without
    collision.
  * The view-type lookups return the contracted counts (event=14, relative=8,
    theme=9) and round-trip through the real ``backend.models`` enums + the
    curated-view fixtures from the shared conftest.
  * ``expression_kind`` / ``view_types`` / ``tier``-adjacent strings equal the
    ORM enum *values* (so dispatch can map without importing the ORM).
  * Every ``template_or_scheme`` an EXISTS option/basket archetype names is a
    REAL ``option_strategies.TEMPLATES`` key / ``weighting`` scheme — i.e. the
    catalog never points a built builder at a fabricated primitive.
  * The two known GAPs (E6 broken-wing, R5 relative-options two-underlying) are
    flagged ``status="GAP"`` and do NOT silently claim a real template.
  * ``Archetype`` is genuinely frozen (immutable DATA) and every cell has a
    matching ``tiers.TIER_KNOBS`` row for all three tiers.
"""
from __future__ import annotations

import dataclasses

import pytest

from backend.models import ExpressionKind, ViewType
from backend.services.option_strategies import TEMPLATES
from backend.view_markets.expressions import catalog, tiers
from backend.view_markets.expressions.catalog import (
    EVENT,
    KIND_BASKET,
    KIND_HEDGE,
    KIND_MULTI_ASSET,
    KIND_OPTION,
    KIND_PAIR,
    RELATIVE,
    THEME,
    Archetype,
)

# Weighting schemes that actually dispatch in ``services.weighting`` (§3a).
_WEIGHTING_SCHEMES = {
    "equal",
    "mcap",
    "factor",
    "risk_parity",
    "min_variance",
    "black_litterman",
}
# Pair primitives that are real ``services.backtest.pairs`` entry points.
_PAIR_PRIMITIVES = {"engle_granger", "rolling_zscore"}

_EXPECTED_KEYS = {
    # EVENT E1–E10
    "E1_rate_debit_spread",
    "E2_nbfc_bank_pair",
    "E3_event_straddle",
    "E4_iv_crush_harvest",
    "E5_pead_drift",
    "E6_broken_wing",
    "E7_merger_arb",
    "E8_index_inclusion",
    "E9_budget_election_rotation",
    "E10_shock_hedged_basket",
    # RELATIVE R1–R5
    "R1_cointegrated_pair",
    "R2_sector_vs_index",
    "R3_factor_etf_vs_index",
    "R4_ratio_rs",
    "R5_relative_options",
    # THEME T1–T4
    "T1_purity_conviction_basket",
    "T2_factor_tilt",
    "T3_optionized_hedged",
    "T4_multi_asset",
    # COMMODITY CM1–CM6 (MCX — tradeable via register-not-execute)
    "CM1_commodity_directional_option",
    "CM2_commodity_event_straddle",
    "CM3_commodity_producer_vs_importer_pair",
    "CM4_gold_silver_ratio_pair",
    "CM5_commodity_multi_asset",
    "CM6_crude_shock_hedged_basket",
}


# ── shape / completeness ────────────────────────────────────────────────────


def test_catalog_has_25_archetypes_keyed_without_collision() -> None:
    assert len(catalog.ARCHETYPE_CATALOG) == 25
    assert set(catalog.ARCHETYPE_CATALOG) == _EXPECTED_KEYS
    # key field matches its registry key (no copy/paste drift).
    for key, arch in catalog.ARCHETYPE_CATALOG.items():
        assert arch.key == key


def test_view_type_constants_equal_orm_enum_values() -> None:
    assert {EVENT, RELATIVE, THEME} == {e.value for e in ViewType}
    assert {
        KIND_OPTION,
        KIND_PAIR,
        KIND_BASKET,
        KIND_MULTI_ASSET,
        KIND_HEDGE,
    } == {e.value for e in ExpressionKind}


def test_every_archetype_field_uses_real_enum_values() -> None:
    valid_view_types = {e.value for e in ViewType}
    valid_kinds = {e.value for e in ExpressionKind}
    for arch in catalog.ARCHETYPE_CATALOG.values():
        assert arch.view_types, f"{arch.key} has no view_types"
        assert set(arch.view_types) <= valid_view_types, arch.key
        assert arch.expression_kind in valid_kinds, arch.key
        assert arch.status in {"EXISTS", "GAP"}, arch.key
        assert arch.timing_default in {"pre_position", "confirmation", "hybrid"}


# ── lookups: by view_type / kind / key ──────────────────────────────────────


@pytest.mark.parametrize(
    ("view_type", "expected"),
    [(EVENT, 14), (RELATIVE, 8), (THEME, 9)],
)
def test_archetypes_for_view_type_counts(view_type: str, expected: int) -> None:
    found = catalog.archetypes_for_view_type(view_type)
    assert len(found) == expected
    # every returned archetype genuinely declares the requested view type, and
    # catalog (priority) order is preserved.
    assert all(view_type in a.view_types for a in found)
    ordered = [a for a in catalog.ARCHETYPE_CATALOG.values() if view_type in a.view_types]
    assert [a.key for a in found] == [a.key for a in ordered]


def test_archetypes_for_view_type_round_trips_through_curated_fixtures(
    event_view, relative_view, theme_view
) -> None:
    """The dispatch input is a real ``MarketView``; its ``view_type`` resolves."""
    assert len(catalog.archetypes_for_view_type(event_view.view_type)) == 14
    assert len(catalog.archetypes_for_view_type(relative_view.view_type)) == 8
    assert len(catalog.archetypes_for_view_type(theme_view.view_type)) == 9


def test_archetypes_for_kind_partitions_the_catalog() -> None:
    by_kind = {
        k: catalog.archetypes_for_kind(k)
        for k in (KIND_OPTION, KIND_PAIR, KIND_BASKET, KIND_MULTI_ASSET, KIND_HEDGE)
    }
    # union over kinds == the whole catalog, with no archetype double-counted.
    total = sum(len(v) for v in by_kind.values())
    assert total == 25
    seen = {a.key for group in by_kind.values() for a in group}
    assert seen == _EXPECTED_KEYS
    for kind, group in by_kind.items():
        assert all(a.expression_kind == kind for a in group)


def test_get_archetype_known_and_unknown() -> None:
    arch = catalog.get_archetype("E1_rate_debit_spread")
    assert arch is not None and arch.expression_kind == KIND_OPTION
    assert catalog.get_archetype("does_not_exist") is None


def test_existing_archetypes_are_exactly_the_non_gaps() -> None:
    existing = catalog.existing_archetypes()
    assert all(a.status == "EXISTS" for a in existing)
    gaps = {a.key for a in catalog.ARCHETYPE_CATALOG.values() if a.status == "GAP"}
    # the §6 gaps the contract pins are flagged, not silently EXISTS.
    assert {"E6_broken_wing", "R5_relative_options"} <= gaps
    assert {a.key for a in existing}.isdisjoint(gaps)


# ── no fabricated primitives: template_or_scheme points at real engines ──────


def test_exists_option_archetypes_name_real_templates() -> None:
    for arch in catalog.archetypes_for_kind(KIND_OPTION):
        if arch.status != "EXISTS":
            continue
        assert arch.template_or_scheme in TEMPLATES, (
            f"{arch.key} EXISTS but template {arch.template_or_scheme!r} "
            "is not a real option_strategies.TEMPLATES key"
        )


def test_broken_wing_is_a_gap_not_a_fake_template() -> None:
    e6 = catalog.get_archetype("E6_broken_wing")
    assert e6 is not None
    assert e6.status == "GAP"
    # broken_wing_butterfly is deliberately NOT in TEMPLATES (composed via legs).
    assert e6.template_or_scheme not in TEMPLATES


def test_relative_options_card_is_a_gap() -> None:
    r5 = catalog.get_archetype("R5_relative_options")
    assert r5 is not None
    # two-underlying relative-options card is the gap even though the per-leg
    # template name happens to be a real one.
    assert r5.status == "GAP"
    assert r5.params.get("two_underlying") is True


def test_exists_basket_archetypes_name_real_weighting_schemes() -> None:
    for arch in catalog.archetypes_for_kind(KIND_BASKET):
        if arch.status != "EXISTS":
            continue
        assert arch.template_or_scheme in _WEIGHTING_SCHEMES, arch.key


def test_pair_archetypes_name_real_pair_primitives_or_compose() -> None:
    for arch in catalog.archetypes_for_kind(KIND_PAIR):
        # pairs either dispatch on a real cointegration/zscore primitive or
        # compose (None, e.g. R3 factor-ETF-vs-index future).
        assert (
            arch.template_or_scheme in _PAIR_PRIMITIVES
            or arch.template_or_scheme is None
        ), arch.key


# ── frozen DATA + tier coverage ─────────────────────────────────────────────


def test_archetype_is_frozen() -> None:
    arch = catalog.get_archetype("E1_rate_debit_spread")
    assert arch is not None
    with pytest.raises(dataclasses.FrozenInstanceError):
        arch.status = "GAP"  # type: ignore[misc]


def test_every_kind_has_tier_knobs_for_all_three_tiers() -> None:
    """Every (expression_kind, tier) the catalog can emit has knobs — no
    builder ever runs on silent defaults (mirrors the §5 coverage check)."""
    kinds_in_use = {a.expression_kind for a in catalog.ARCHETYPE_CATALOG.values()}
    for kind in kinds_in_use:
        for tier in (tiers.CONSERVATIVE, tiers.BALANCED, tiers.AGGRESSIVE):
            knobs = tiers.tier_knobs(kind, tier)  # raises KeyError if missing
            assert isinstance(knobs, tiers.TierKnobs)


def test_dataclass_type_is_exported() -> None:
    # the registry stores Archetype instances (the frozen DATA unit).
    assert all(
        isinstance(a, Archetype) for a in catalog.ARCHETYPE_CATALOG.values()
    )
