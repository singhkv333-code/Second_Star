"""Regression coverage for the 2026-07-14 eval #14 bug.

Prompt: "Create an automation to book profits on my HDFCBANK position
once it's up 8%, and cut losses if it drops 5%." The model built the
automation by hand-authoring a `trigger.exit_compound` step (via
`propose_workflow`) rather than going through the dedicated DSL NL
translator (`propose_dsl_workflow`), and got the tree node shapes wrong
in a way `TriggerExitCompoundConfig._validate_tree` genuinely rejected
— TWICE, with two different unrecognised shapes:

  1. `{"type": "position_unrealised_pct", "symbol": "HDFCBANK"}` — the
     position FIELD spelled directly as the node's "type" tag, instead
     of `{"type": "position", "field": "unrealised_pct"}`.
  2. (after a self-correction retry that fixed #1) a root node shaped
     `{"type": "or", "conditions": [...]}` instead of the correct
     `{"type": "logic", "op": "or", "operands": [...]}`.

Both failures then fell through chat_service's single-shot tool-error
path, which produced a fabricated "clarifying question" about the
position/entry-price reference — masking the real internal shape bug
as if it were an ambiguity in the user's request.

The fix lives in `backend.workflows.dsl.schema.normalize_tree_aliases`
(extending its existing LLM-node-shape-alias table) — applied at both
`TriggerCompoundConfig._validate_tree` (entry) and
`TriggerExitCompoundConfig._validate_tree` (exit) before Pydantic's
tagged-union parse. These tests exercise the step-config layer
directly (not the chat loop) so they don't need a live LLM.
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from backend.workflows.schemas import (
    TriggerCompoundConfig,
    TriggerExitCompoundConfig,
)


def _hdfcbank_profit_loss_tree(position_leaf_shape: str) -> dict:
    """Build the exit tree in one of the two shapes the model actually
    emitted in the live eval trace.

    ``position_leaf_shape``:
      "type_as_field" — attempt 1: `{"type": "position_unrealised_pct"}`
      "correct_leaf"  — attempt 2: `{"type": "position", "field": ...}`
    Both attempts kept the same bad `{"type": "or", "conditions": [...]}`
    root shape — that's the SECOND independent bug the retry didn't fix.
    """
    if position_leaf_shape == "type_as_field":
        def leaf():
            return {"type": "position_unrealised_pct", "symbol": "HDFCBANK"}
    else:
        def leaf():
            return {"type": "position", "symbol": "HDFCBANK", "field": "unrealised_pct"}

    return {
        "type": "or",
        "conditions": [
            {
                "type": "comparison", "op": ">=",
                "left": leaf(),
                "right": {"type": "constant", "value": 0.08},
            },
            {
                "type": "comparison", "op": "<=",
                "left": leaf(),
                "right": {"type": "constant", "value": -0.05},
            },
        ],
    }


def test_exit_compound_accepts_eval14_first_attempt_shape():
    """Attempt #1 from the live eval trace: position field spelled as
    the node's "type" tag, PLUS the bare-"or" root. Both must now
    normalize into a valid exit tree."""
    cfg = TriggerExitCompoundConfig(
        entry=_hdfcbank_profit_loss_tree("type_as_field"),
        target_symbol="HDFCBANK",
    )
    assert cfg.entry["type"] == "or"  # raw config is untouched; only
    # validation-time parsing normalizes — confirms the fix doesn't
    # mutate what gets persisted, only what gets validated.


def test_exit_compound_accepts_eval14_second_attempt_shape():
    """Attempt #2: the position leaf was fixed by the model's own
    self-correction retry, but the bare-"or" root survived and failed
    validation a SECOND time in the live eval. Must now validate."""
    cfg = TriggerExitCompoundConfig(
        entry=_hdfcbank_profit_loss_tree("correct_leaf"),
        target_symbol="HDFCBANK",
    )
    assert cfg.entry["conditions"][0]["left"]["field"] == "unrealised_pct"


def test_entry_compound_still_rejects_position_leaf():
    """The fix must stay scoped to shape-aliasing — it must NOT loosen
    the genuine entry-vs-exit semantic rule that a position leaf can
    never appear in an ENTRY tree (trigger.compound, not
    trigger.exit_compound)."""
    with pytest.raises(ValidationError, match="only valid in an EXIT tree"):
        TriggerCompoundConfig(
            entry={
                "type": "or",
                "conditions": [
                    {
                        "type": "comparison", "op": ">=",
                        "left": {"type": "position_unrealised_pct"},
                        "right": {"type": "constant", "value": 0.08},
                    },
                ],
            },
        )


def test_entry_compound_accepts_bare_and_shape_for_ordinary_conditions():
    """The bare-op-as-type alias is a general node-shape fix, not
    position-specific — an ordinary price/indicator AND should also
    normalize on the entry side."""
    cfg = TriggerCompoundConfig(
        entry={
            "type": "and",
            "conditions": [
                {"type": "comparison", "op": "<",
                 "left": {"type": "price", "symbol": "HDFCBANK"},
                 "right": {"type": "constant", "value": 1700}},
                {"type": "comparison", "op": ">",
                 "left": {"type": "price", "symbol": "HDFCBANK"},
                 "right": {"type": "constant", "value": 1600}},
            ],
        },
    )
    assert cfg.entry["type"] == "and"
