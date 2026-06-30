"""Focused unit tests for the Phase-3 honest-short decision rule.

``honest_short`` is the anti-fabrication guard: Indian retail cannot short
single stocks or ETFs in delivery (spec §1.6), so the expression engine routes
every desired short leg through this one rule — SSF future / index future / put
proxy / AVOID — and NEVER invents a fake delivery short or price. These tests
pin the decision rule + the India-microstructure constants the builders depend
on.

Pure functions (no db / no external engine), so the file is self-contained; the
shared ``tests/view_markets/conftest.py`` is still auto-discovered for the suite.
"""
from __future__ import annotations

import dataclasses

import pytest

from backend.view_markets.expressions import honest_short as hs
from backend.view_markets.expressions.config_schema import base_envelope

# The frozen ShortMode vocabulary (mirrors honest_short.ShortMode Literal).
_MODES = {
    "ssf_future", "put", "put_spread", "index_future", "index_put",
    "commodity_future", "commodity_put", "avoid",
}


# ── invariants every ShortLeg must satisfy ──────────────────────────────────
def _assert_invariants(leg: hs.ShortLeg) -> None:
    assert leg.mode in _MODES
    # tradeable is False *iff* the leg is an AVOID (dataclass contract).
    assert leg.tradeable == (leg.mode != "avoid")
    # an AVOID and any non-clean vehicle must be flagged degraded.
    if leg.mode in {"avoid", "put", "put_spread"}:
        assert leg.degraded is True
    assert isinstance(leg.warnings, list)
    assert leg.note  # never blank


# ── index shorts ────────────────────────────────────────────────────────────
@pytest.mark.parametrize("sym", ["NIFTY", "NIFTY 50", "nifty50", "SENSEX", "BANKNIFTY"])
def test_index_short_routes_to_index_future(sym: str) -> None:
    leg = hs.short_leg_for(sym, is_index=True)
    _assert_invariants(leg)
    assert leg.mode == "index_future"
    assert leg.tradeable is True
    assert leg.degraded is False
    assert "FUT" in leg.instrument


def test_banknifty_short_flags_monthly_only() -> None:
    leg = hs.short_leg_for("BANKNIFTY", is_index=True)
    assert leg.mode == "index_future"
    assert any("monthly" in w.lower() for w in leg.warnings)


def test_nifty_short_flags_weekly_availability() -> None:
    leg = hs.short_leg_for("NIFTY", is_index=True)
    assert any("weekly" in w.lower() for w in leg.warnings)


def test_short_etf_as_index_is_avoid_not_a_delivery_short() -> None:
    """The load-bearing guard: "short NIFTYBEES" is NOT a real expression."""
    leg = hs.short_leg_for("NIFTYBEES", is_index=True)
    _assert_invariants(leg)
    assert leg.mode == "avoid"
    assert leg.tradeable is False
    assert leg.note == hs.UNSHORTABLE_ETF_NOTE
    assert "SLB" in leg.note


# ── single-stock shorts ─────────────────────────────────────────────────────
def test_ssf_eligible_stock_uses_single_stock_future() -> None:
    leg = hs.short_leg_for("INFY", ssf_eligible=True)
    _assert_invariants(leg)
    assert leg.mode == "ssf_future"
    assert leg.tradeable is True
    assert leg.degraded is False
    assert leg.instrument == "INFY FUT"


def test_unknown_eligibility_degrades_to_defined_risk_put() -> None:
    leg = hs.short_leg_for("INFY", ssf_eligible=None)
    _assert_invariants(leg)
    assert leg.mode == "put"
    assert leg.tradeable is True
    assert leg.degraded is True
    # carries the single-stock-option microstructure warning + an "unconfirmed" flag.
    assert hs.SINGLE_STOCK_OPTION_WARNING in leg.warnings
    assert any("unconfirmed" in w.lower() for w in leg.warnings)


def test_not_fno_eligible_stock_is_avoid_never_a_fabricated_put() -> None:
    leg = hs.short_leg_for("SMALLCAPX", ssf_eligible=False)
    _assert_invariants(leg)
    assert leg.mode == "avoid"
    assert leg.tradeable is False
    assert leg.degraded is True
    assert "F&O" in leg.note


def test_allow_intraday_only_annotates_does_not_make_avoid_tradeable() -> None:
    leg = hs.short_leg_for("SMALLCAPX", ssf_eligible=False, allow_intraday=True)
    assert leg.mode == "avoid"
    assert leg.tradeable is False  # still not a position short
    assert any("intraday" in w.lower() for w in leg.warnings)


def test_default_call_is_unknown_eligibility() -> None:
    # ssf_eligible defaults to None → put proxy (not a silent clean short).
    leg = hs.short_leg_for("RELIANCE")
    assert leg.mode == "put"
    assert leg.degraded is True


# ── commodity shorts (MCX — the symmetric short equities can't do) ───────────
@pytest.mark.parametrize("sym", ["CRUDEOIL", "GOLD", "SILVER", "COPPER", "NATURALGAS"])
def test_commodity_short_is_a_tradeable_future_never_avoid(sym: str) -> None:
    """The whole point of the MCX pass: a commodity is SYMMETRICALLY shortable."""
    leg = hs.short_leg_for(sym, is_commodity=True)
    _assert_invariants(leg)
    assert leg.mode == "commodity_future"
    assert leg.tradeable is True
    assert leg.degraded is False  # a clean symmetric short, not a degraded proxy
    assert "FUT" in leg.instrument
    # carries the leverage / register-not-execute note (never auto-sized).
    assert any("leverag" in w.lower() for w in leg.warnings)


def test_commodity_defined_risk_short_is_a_put() -> None:
    leg = hs.short_leg_for("GOLD", is_commodity=True, prefer_defined_risk=True)
    _assert_invariants(leg)
    assert leg.mode == "commodity_put"
    assert leg.tradeable is True
    assert leg.degraded is False
    assert leg.instrument == "GOLD PE"


def test_commodity_not_on_master_is_avoid_not_a_fabricated_contract() -> None:
    leg = hs.short_leg_for("UNOBTANIUM", is_commodity=True, fno_eligible=False)
    _assert_invariants(leg)
    assert leg.mode == "avoid"
    assert leg.tradeable is False
    assert leg.degraded is True


def test_index_flag_wins_over_commodity_flag() -> None:
    # An index symbol flagged both ways still routes to the index future (index
    # branch is evaluated first), never a commodity future.
    leg = hs.short_leg_for("NIFTY", is_index=True, is_commodity=True)
    assert leg.mode == "index_future"


# ── avoid_annotation (the AVOID/underweight first-class expression) ──────────
def test_avoid_annotation_is_non_tradeable_degraded() -> None:
    leg = hs.avoid_annotation("XYZ", reason="Bottom-decile factor name")
    _assert_invariants(leg)
    assert leg.mode == "avoid"
    assert leg.tradeable is False
    assert leg.degraded is True
    assert "Bottom-decile" in leg.note


def test_avoid_annotation_surfaces_underweight() -> None:
    leg = hs.avoid_annotation("XYZ", reason="Weak name", suggested_underweight=0.05)
    assert "5%" in leg.note


def test_avoid_annotation_blank_reason_has_fallback_note() -> None:
    leg = hs.avoid_annotation("XYZ", reason="   ")
    assert leg.note  # never blank


# ── microstructure helpers ──────────────────────────────────────────────────
@pytest.mark.parametrize(
    "sym,expected",
    [
        ("NIFTY", True),
        ("NIFTY 50", True),
        ("SENSEX", True),
        ("BANKNIFTY", False),
        ("NIFTYBANK", False),
        ("INFY", False),
    ],
)
def test_is_weekly_eligible(sym: str, expected: bool) -> None:
    assert hs.is_weekly_eligible(sym) is expected


@pytest.mark.parametrize(
    "sym,expected",
    [
        ("NASDAQ100", "MON100"),
        ("US_TECH", "MON100"),
        ("MON100", "MON100"),  # already a proxy → pass-through
        ("INFY", None),
        ("", None),
    ],
)
def test_foreign_proxy(sym: str, expected) -> None:
    assert hs.foreign_proxy(sym) == expected


# ── ShortLeg is a frozen dataclass + slots into the config envelope ──────────
def test_short_leg_is_frozen() -> None:
    leg = hs.short_leg_for("INFY", ssf_eligible=True)
    assert dataclasses.is_dataclass(leg)
    with pytest.raises(dataclasses.FrozenInstanceError):
        leg.mode = "avoid"  # type: ignore[misc]


def test_short_leg_feeds_expressability_envelope() -> None:
    """A degraded short leg flips ``expressability.degraded`` in the config envelope."""
    leg = hs.short_leg_for("INFY", ssf_eligible=None)
    env = base_envelope(
        archetype="R1_cointegrated_pair",
        expression_kind="pair",
        tier="balanced",
        label="IT pair",
    )
    env["expressability"]["degraded"] = leg.degraded
    env["expressability"]["short_mode"] = leg.mode
    assert env["expressability"]["degraded"] is True
    assert env["expressability"]["short_mode"] == "put"
