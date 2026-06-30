"""Unit tests for the Phase-3 merger / open-offer arb calculator (E7).

Pure math + honest caveats — no DB, no external engines, fully self-contained.
Asserts: spread / annualized / implied-break-prob / proration math, the
long-only honest caveat (acquirer-short out of scope), missing-input fields stay
``None`` (never fabricated), and input validation.
"""
from __future__ import annotations

import math

import pytest

from backend.view_markets.expressions.merger_arb import (
    MergerArbMetrics,
    merger_arb_metrics,
)


def test_spread_and_gross_return() -> None:
    m = merger_arb_metrics(target_price=98.0, offer_price=100.0, days_to_close=100)
    assert isinstance(m, MergerArbMetrics)
    assert m.spread_abs == pytest.approx(2.0)
    assert m.spread_pct == pytest.approx(2.0 / 98.0 * 100.0)
    # gross == spread_pct (held to close, fully accepted)
    assert m.gross_return_pct == pytest.approx(m.spread_pct)


def test_annualized_simple_scaling() -> None:
    m = merger_arb_metrics(target_price=98.0, offer_price=100.0, days_to_close=100)
    assert m.annualized_return_pct is not None
    assert m.annualized_return_pct == pytest.approx(m.gross_return_pct * 365.0 / 100.0)
    # ~3-4 month process annualizes UP relative to the gross spread.
    assert m.annualized_return_pct > m.gross_return_pct


def test_implied_break_prob_risk_neutral() -> None:
    # spread 2, downside span (offer - broken) = 100 - 90 = 10 => p = 0.2
    m = merger_arb_metrics(
        target_price=98.0, offer_price=100.0, days_to_close=120, broken_price=90.0
    )
    assert m.implied_break_prob == pytest.approx(0.2)
    assert m.broken_price == 90.0


def test_implied_break_prob_clamped_to_unit_interval() -> None:
    # broken_price just under the offer => tiny downside span => p > 1 raw,
    # must clamp to 1.0 rather than report fake odds.
    m = merger_arb_metrics(
        target_price=98.0, offer_price=100.0, days_to_close=90, broken_price=99.5
    )
    assert m.implied_break_prob == 1.0


def test_implied_break_prob_none_without_broken_price() -> None:
    m = merger_arb_metrics(target_price=98.0, offer_price=100.0, days_to_close=90)
    # Missing input => None, never fabricated.
    assert m.implied_break_prob is None
    assert m.broken_price is None
    assert m.prorated_return_pct is None
    assert m.acceptance_ratio is None


def test_implied_break_prob_none_when_broken_above_offer() -> None:
    # Non-positive downside span => ill-defined => None (with a note).
    m = merger_arb_metrics(
        target_price=98.0, offer_price=100.0, days_to_close=90, broken_price=105.0
    )
    assert m.implied_break_prob is None
    assert "not computed" in m.note


def test_proration_blends_accepted_and_stub() -> None:
    # 70% accepted at offer 100, 30% stub valued at broken 90, bought at 98.
    m = merger_arb_metrics(
        target_price=98.0,
        offer_price=100.0,
        days_to_close=120,
        broken_price=90.0,
        acceptance_ratio=0.7,
    )
    accepted = 0.7 * (100.0 - 98.0)
    stub = 0.3 * (90.0 - 98.0)
    expected = (accepted + stub) / 98.0 * 100.0
    assert m.prorated_return_pct == pytest.approx(expected)
    # Proration drags the blended return BELOW the clean gross return.
    assert m.prorated_return_pct < m.gross_return_pct
    assert m.acceptance_ratio == 0.7


def test_proration_stub_flat_at_cost_without_broken_price() -> None:
    # No broken estimate => stub held flat at cost => only the accepted leg earns.
    m = merger_arb_metrics(
        target_price=98.0,
        offer_price=100.0,
        days_to_close=120,
        acceptance_ratio=0.5,
    )
    expected = 0.5 * (100.0 - 98.0) / 98.0 * 100.0
    assert m.prorated_return_pct == pytest.approx(expected)


def test_negative_spread_flagged() -> None:
    m = merger_arb_metrics(target_price=102.0, offer_price=100.0, days_to_close=90)
    assert m.spread_abs == pytest.approx(-2.0)
    assert "ABOVE the offer" in m.note


def test_honest_long_only_caveat_always_present() -> None:
    m = merger_arb_metrics(target_price=98.0, offer_price=100.0, days_to_close=90)
    # The acquirer-short out-of-scope honesty rule is always stated.
    assert "acquirer-short" in m.note
    assert "out of scope" in m.note


@pytest.mark.parametrize(
    "kwargs",
    [
        {"target_price": 0.0, "offer_price": 100.0, "days_to_close": 90},
        {"target_price": 98.0, "offer_price": -1.0, "days_to_close": 90},
        {"target_price": 98.0, "offer_price": 100.0, "days_to_close": 0},
        {
            "target_price": 98.0,
            "offer_price": 100.0,
            "days_to_close": 90,
            "broken_price": 0.0,
        },
        {
            "target_price": 98.0,
            "offer_price": 100.0,
            "days_to_close": 90,
            "acceptance_ratio": 0.0,
        },
        {
            "target_price": 98.0,
            "offer_price": 100.0,
            "days_to_close": 90,
            "acceptance_ratio": 1.5,
        },
    ],
)
def test_validation_rejects_bad_inputs(kwargs: dict) -> None:
    with pytest.raises(ValueError):
        merger_arb_metrics(**kwargs)


def test_full_acceptance_matches_gross() -> None:
    # acceptance_ratio == 1.0 (full quota) => prorated == gross return.
    m = merger_arb_metrics(
        target_price=98.0,
        offer_price=100.0,
        days_to_close=120,
        broken_price=90.0,
        acceptance_ratio=1.0,
    )
    assert m.prorated_return_pct == pytest.approx(m.gross_return_pct)
    assert not math.isnan(m.prorated_return_pct)
