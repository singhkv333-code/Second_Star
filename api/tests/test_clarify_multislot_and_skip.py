"""Fix-pass 2026-07-05 — clarify residual gaps F5 + F6.

F5  A stated factor / macro-scenario / event-positioning view FILLS the view
    slot → the skip-entirely gate must build directly (no clarify card).
F6  A free-text clarify answer that pins several slots at once ("Around 3
    lakh, 5 plus years, equities only.") must fold EVERY slot it mentions,
    so the resume path never re-asks an already-answered slot.
"""
from __future__ import annotations

from backend.services.clarify_engine import (
    fold_free_text_into_slots,
    should_ask,
)
from backend.services.strategy_contracts import SlotState


# ── F5: stated view → skip-entirely (build directly) ────────────────

def test_f5_stated_view_skips_clarify():
    """A stated factor / theme / event view is sufficiently specified — the
    skip gate must return False (build directly), NOT open a clarify card."""
    for req in [
        "Build me a strategy that benefits from momentum",
        "Create a strategy around the upcoming RBI rate decision",
        "design a portfolio for the EV supply chain story",
        "a low-vol quality basket",
        "profit from a good monsoon",
    ]:
        assert should_ask(req, SlotState(), ctx=None) is False, req


def test_f5_no_view_still_asks():
    """A genuinely view-less, unspecified build still surfaces the card."""
    for req in ["build me a strategy", "design a portfolio",
                "make me a basket"]:
        assert should_ask(req, SlotState(), ctx=None) is True, req


# ── F6: multi-slot free-text folding ────────────────────────────────

def test_f6_folds_capital_horizon_and_assets_in_one_line():
    """The exact transcript answer that broke the resume path."""
    slots = SlotState()
    slots = fold_free_text_into_slots("Around 3 lakh, 5 plus years, equities only.", slots)
    assert slots.capital_inr == 300000.0
    assert not slots.assumed.capital_inr
    assert slots.horizon == "long"          # "5 plus years" → long
    assert not slots.assumed.horizon
    assert not slots.assumed.asset_prefs    # "equities only" pinned assets


def test_f6_folds_view_and_risk():
    slots = SlotState()
    slots = fold_free_text_into_slots("Bullish on India long term, aggressive risk is fine.", slots)
    assert slots.view.direction == "bull"
    assert not slots.assumed.view
    assert slots.risk == "aggressive"
    assert not slots.assumed.risk
    assert slots.horizon == "long"          # "long term"


def test_f6_horizon_bucketing():
    assert fold_free_text_into_slots("hold for a few months", SlotState()).horizon == "tactical"
    assert fold_free_text_into_slots("about 3 years", SlotState()).horizon == "medium"
    assert fold_free_text_into_slots("7 years plus", SlotState()).horizon == "long"


def test_f6_does_not_clobber_a_real_prior_answer():
    """Only fills slots still flagged assumed — never overrides a real value."""
    slots = SlotState()
    slots.risk = "conservative"
    slots.mark_assumed("risk", value=False)   # a real prior answer
    slots = fold_free_text_into_slots("aggressive please", slots)
    assert slots.risk == "conservative"       # untouched


def test_f6_transcript_resume_no_reask_capital():
    """The F6 session end-to-end at the slot level: after the two free-text
    answers, capital + horizon + risk + view + assets are all filled, so the
    resume cursor has nothing left to re-ask."""
    slots = SlotState()
    slots = fold_free_text_into_slots("Bullish on India long term, aggressive risk is fine.", slots)
    slots = fold_free_text_into_slots("Around 3 lakh, 5 plus years, equities only.", slots)
    assert slots.capital_inr == 300000.0 and not slots.assumed.capital_inr
    assert not slots.assumed.horizon
    assert not slots.assumed.risk
    assert not slots.assumed.view
    assert not slots.assumed.asset_prefs
