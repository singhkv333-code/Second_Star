"""Focused unit tests for the Phase-3 tier-knob table (``expressions.tiers``).

``tiers.py`` is the single place Conservative / Balanced / Aggressive semantics
live: pure DATA (``TIER_KNOBS[(expression_kind, tier)]``) plus one getter
(``tier_knobs``). These tests pin the load-bearing §5 contract that the builders
and ``dispatch`` depend on:

* every catalog ``expression_kind × tier`` cell exists (no silent default),
* the getter resolves and raises ``KeyError`` on an undefined cell,
* the numeric knobs (pair z-bands, single-name caps, gold sleeves) equal the
  §5 ``VIEW_MARKETS_STRATEGY_DESIGN.md`` tables exactly,
* tier → timing-mode and the keys equal the ``backend.models`` enum *values*
  (so dispatch can map without importing the ORM into ``tiers``).

Pure-data assertions — no db, no external engines — so the file is fully
self-contained (the shared ``conftest`` is still auto-discovered for the suite).
"""
from __future__ import annotations

import dataclasses

import pytest

from backend.models import ExpressionKind, ExpressionTier
from backend.view_markets.expressions import catalog
from backend.view_markets.expressions.tiers import (
    AGGRESSIVE,
    BALANCED,
    CONSERVATIVE,
    TIER_KNOBS,
    TierKnobs,
    tier_knobs,
)

ALL_KINDS = (
    catalog.KIND_OPTION,
    catalog.KIND_PAIR,
    catalog.KIND_BASKET,
    catalog.KIND_MULTI_ASSET,
    catalog.KIND_HEDGE,
)
ALL_TIERS = (CONSERVATIVE, BALANCED, AGGRESSIVE)


# ── enum-value alignment ────────────────────────────────────────────────────


def test_tier_constants_equal_model_enum_values() -> None:
    """The tier string constants must equal ``ExpressionTier`` values verbatim."""
    assert {CONSERVATIVE, BALANCED, AGGRESSIVE} == {
        t.value for t in ExpressionTier
    }


def test_kind_constants_equal_model_enum_values() -> None:
    """The catalog kind constants must equal ``ExpressionKind`` values verbatim."""
    assert set(ALL_KINDS) == {k.value for k in ExpressionKind}


# ── coverage: every kind × tier resolves ────────────────────────────────────


def test_every_kind_x_tier_has_a_cell() -> None:
    """No (kind, tier) the catalog can emit may be missing from TIER_KNOBS."""
    expected = {(k, t) for k in ALL_KINDS for t in ALL_TIERS}
    assert set(TIER_KNOBS) == expected
    assert len(TIER_KNOBS) == 15


def test_every_catalog_archetype_kind_is_covered() -> None:
    """Every expression_kind any catalog archetype dispatches on has all 3 tiers."""
    catalog_kinds = {a.expression_kind for a in catalog.ARCHETYPE_CATALOG.values()}
    for kind in catalog_kinds:
        for tier in ALL_TIERS:
            assert tier_knobs(kind, tier) is not None


@pytest.mark.parametrize("kind", ALL_KINDS)
@pytest.mark.parametrize("tier", ALL_TIERS)
def test_getter_returns_tierknobs(kind: str, tier: str) -> None:
    knobs = tier_knobs(kind, tier)
    assert isinstance(knobs, TierKnobs)
    # The four always-present descriptive dimensions are non-blank strings.
    assert knobs.capital_intensity.strip()
    assert knobs.leverage.strip()
    assert knobs.hedge_ratio.strip()
    assert knobs.n_legs.strip()


def test_getter_raises_keyerror_on_unknown_cell() -> None:
    with pytest.raises(KeyError):
        tier_knobs("not_a_kind", CONSERVATIVE)
    with pytest.raises(KeyError):
        tier_knobs(catalog.KIND_OPTION, "not_a_tier")


def test_tierknobs_is_frozen() -> None:
    knobs = tier_knobs(catalog.KIND_PAIR, BALANCED)
    with pytest.raises(dataclasses.FrozenInstanceError):
        knobs.single_name_cap = 0.99  # type: ignore[misc]


# ── §5 numeric contract: pair z-bands ───────────────────────────────────────


def test_pair_z_bands_match_spec() -> None:
    """§5 RELATIVE z-bands: Cons 2.5/0.5/4.0, Bal 2.0/0.5/4.0, Aggr 1.75/0.4/4.5."""
    cons = tier_knobs(catalog.KIND_PAIR, CONSERVATIVE)
    bal = tier_knobs(catalog.KIND_PAIR, BALANCED)
    aggr = tier_knobs(catalog.KIND_PAIR, AGGRESSIVE)

    assert (cons.pair_z_entry, cons.pair_z_exit, cons.pair_z_stop) == (2.5, 0.5, 4.0)
    assert (bal.pair_z_entry, bal.pair_z_exit, bal.pair_z_stop) == (2.0, 0.5, 4.0)
    assert (aggr.pair_z_entry, aggr.pair_z_exit, aggr.pair_z_stop) == (1.75, 0.4, 4.5)

    # Entry tightens (fewer, cleaner) Conservative → Aggressive; engine default sits
    # at Balanced (2.0/0.5/4.0).
    assert cons.pair_z_entry > bal.pair_z_entry > aggr.pair_z_entry


def test_only_pair_kind_carries_z_bands() -> None:
    """z-bands are a pair-only knob; other kinds leave them None."""
    for kind in ALL_KINDS:
        for tier in ALL_TIERS:
            knobs = tier_knobs(kind, tier)
            if kind == catalog.KIND_PAIR:
                assert knobs.pair_z_entry is not None
            else:
                assert knobs.pair_z_entry is None
                assert knobs.pair_z_exit is None
                assert knobs.pair_z_stop is None


# ── §5 numeric contract: single-name caps ───────────────────────────────────


def test_single_name_caps_match_spec() -> None:
    """§5 THEME caps: 0.10 / 0.15 / 0.20 for basket & multi-asset kinds."""
    expected = {CONSERVATIVE: 0.10, BALANCED: 0.15, AGGRESSIVE: 0.20}
    for kind in (catalog.KIND_BASKET, catalog.KIND_MULTI_ASSET):
        for tier, cap in expected.items():
            assert tier_knobs(kind, tier).single_name_cap == cap


# ── §4.6 gold sleeves (multi-asset only) ────────────────────────────────────


def test_gold_sleeve_pct_match_spec() -> None:
    """§4.6 gold sleeve shrinks Cons→Aggr: 0.09 / 0.05 / 0.025 (multi-asset)."""
    expected = {CONSERVATIVE: 0.09, BALANCED: 0.05, AGGRESSIVE: 0.025}
    for tier, pct in expected.items():
        assert tier_knobs(catalog.KIND_MULTI_ASSET, tier).gold_sleeve_pct == pct

    # gold sleeve is a multi-asset-only knob.
    for kind in ALL_KINDS:
        if kind == catalog.KIND_MULTI_ASSET:
            continue
        for tier in ALL_TIERS:
            assert tier_knobs(kind, tier).gold_sleeve_pct is None


# ── timing default per tier (§5 EVENT/THEME rows) ───────────────────────────


def test_timing_default_per_tier_matches_spec() -> None:
    """Conservative=confirmation, Balanced=hybrid, Aggressive=pre_position — uniform
    across every kind."""
    expected = {
        CONSERVATIVE: "confirmation",
        BALANCED: "hybrid",
        AGGRESSIVE: "pre_position",
    }
    for kind in ALL_KINDS:
        for tier, mode in expected.items():
            assert tier_knobs(kind, tier).timing_default == mode


def test_aggressive_options_carry_event_size_cut() -> None:
    """Pre-position on high-uncertainty events cuts size 30–50% (§2): only the
    Aggressive option cell sets size_cut; non-pre-position cells leave it None."""
    aggr_opt = tier_knobs(catalog.KIND_OPTION, AGGRESSIVE)
    assert aggr_opt.size_cut == 0.4

    assert tier_knobs(catalog.KIND_OPTION, CONSERVATIVE).size_cut is None
    assert tier_knobs(catalog.KIND_OPTION, BALANCED).size_cut is None


# ── option / hedge kinds expose a strike-posture knob ───────────────────────


def test_option_and_hedge_kinds_carry_moneyness() -> None:
    """Option & hedge builders read option_moneyness; it must be populated for
    those kinds (non-blank)."""
    for kind in (catalog.KIND_OPTION, catalog.KIND_HEDGE):
        for tier in ALL_TIERS:
            assert tier_knobs(kind, tier).option_moneyness


def test_basket_kinds_carry_concentration_and_rebalance() -> None:
    """Basket & multi-asset builders read basket_concentration + rebalance."""
    for kind in (catalog.KIND_BASKET, catalog.KIND_MULTI_ASSET):
        for tier in ALL_TIERS:
            knobs = tier_knobs(kind, tier)
            assert knobs.basket_concentration
            assert knobs.rebalance
