"""Regression: an explicit "equity + gold" clarify answer must actually
drive the builder to include a gold sleeve, not just tweak an allow-list
that's already permissive by default. Reported 2026-07-14 — user picked
"a roughly balanced mix of equity and gold" and got a pure-equity
"Diversified Equity Basket" with zero gold exposure.
"""
from __future__ import annotations

from backend.services.clarify_engine import (
    fold_free_text_into_slots,
    normalize_answer_into_slots,
)
from backend.services.strategy_contracts import ClarifyQuestion, SlotState


def _asset_prefs_question() -> ClarifyQuestion:
    return ClarifyQuestion(
        id="q1", slot="asset_prefs", prompt="Which basket structure?", voi=1.0,
    )


def test_explicit_gold_mix_answer_sets_gold_requested():
    slots = normalize_answer_into_slots(
        _asset_prefs_question(),
        "A roughly balanced mix of equity and gold",
        SlotState(),
    )
    assert slots.asset_prefs.gold_requested is True
    assert "gold" in slots.asset_prefs.allow


def test_pure_equity_answer_does_not_set_gold_requested():
    slots = normalize_answer_into_slots(
        _asset_prefs_question(), "Pure equity, no gold", SlotState(),
    )
    assert slots.asset_prefs.gold_requested is False
    assert "gold" in slots.asset_prefs.deny


def test_free_text_fold_also_sets_gold_requested():
    slots = fold_free_text_into_slots(
        "conservative, medium term, with some gold", SlotState(),
    )
    assert slots.asset_prefs.gold_requested is True
