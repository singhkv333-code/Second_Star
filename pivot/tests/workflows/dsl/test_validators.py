"""Semantic-validator tests."""
from __future__ import annotations

import pytest
from pydantic import TypeAdapter

from backend.workflows.dsl.schema import Tree
from backend.workflows.dsl.validators import (
    DSLValidationError,
    MAX_DEPTH,
    semantic_validate,
)


_TREE = TypeAdapter(Tree)


def _leaf_indicator():
    return {"type": "indicator", "indicator": "rsi", "symbol": "TCS", "period": 14}


def _leaf_constant(v=30):
    return {"type": "constant", "value": v}


def _basic_comparison():
    return {"type": "comparison", "op": "<",
            "left": _leaf_indicator(), "right": _leaf_constant(30)}


# ── Root shape ──────────────────────────────────────────────────────


def test_root_must_be_comparison_or_logic():
    """A leaf at the root can't fire."""
    bare_indicator = _TREE.validate_python(_leaf_indicator())
    with pytest.raises(DSLValidationError, match="root must be"):
        semantic_validate(bare_indicator)


def test_comparison_at_root_is_valid():
    semantic_validate(_TREE.validate_python(_basic_comparison()))


def test_logic_at_root_is_valid():
    tree = _TREE.validate_python({
        "type": "logic", "op": "and",
        "operands": [_basic_comparison(), _basic_comparison()],
    })
    semantic_validate(tree)


# ── Depth ──────────────────────────────────────────────────────────


def test_depth_within_limit_is_accepted():
    # depth = 4 (logic → logic → comparison → leaf)
    tree = _TREE.validate_python({
        "type": "logic", "op": "and",
        "operands": [
            {"type": "logic", "op": "or",
             "operands": [_basic_comparison(), _basic_comparison()]},
            _basic_comparison(),
        ],
    })
    semantic_validate(tree)


def test_depth_above_limit_rejected():
    # build a deeply nested AND chain so depth exceeds MAX_DEPTH
    inner = _basic_comparison()
    deep = inner
    for _ in range(MAX_DEPTH + 1):
        deep = {"type": "logic", "op": "and",
                "operands": [deep, _basic_comparison()]}
    tree = _TREE.validate_python(deep)
    with pytest.raises(DSLValidationError, match="depth"):
        semantic_validate(tree)


# ── Indicator-registry lookup ──────────────────────────────────────


def test_unknown_indicator_rejected():
    tree = _TREE.validate_python({
        "type": "comparison", "op": "<",
        "left": {"type": "indicator", "indicator": "totally_made_up_xyz",
                 "symbol": "TCS", "period": 14},
        "right": _leaf_constant(30),
    })
    with pytest.raises(DSLValidationError, match="Unknown indicator"):
        semantic_validate(tree)


def test_known_indicators_pass():
    for ind in ("rsi", "sma", "ema", "macd", "atr"):
        tree = _TREE.validate_python({
            "type": "comparison", "op": "<",
            "left": {"type": "indicator", "indicator": ind,
                     "symbol": "TCS", "period": 14},
            "right": _leaf_constant(30),
        })
        semantic_validate(tree)


def test_unknown_indicator_in_deep_subtree_still_caught():
    tree = _TREE.validate_python({
        "type": "logic", "op": "and",
        "operands": [
            _basic_comparison(),
            {"type": "comparison", "op": ">",
             "left": {"type": "indicator", "indicator": "imaginary_zzz",
                      "symbol": "X", "period": 1},
             "right": _leaf_constant(0)},
        ],
    })
    with pytest.raises(DSLValidationError, match="imaginary_zzz"):
        semantic_validate(tree)


# ── Vacuous comparisons ────────────────────────────────────────────


def test_constant_on_both_sides_rejected():
    tree = _TREE.validate_python({
        "type": "comparison", "op": "<",
        "left": _leaf_constant(30),
        "right": _leaf_constant(50),
    })
    with pytest.raises(DSLValidationError, match="Vacuous"):
        semantic_validate(tree)


def test_one_constant_one_market_value_is_fine():
    semantic_validate(_TREE.validate_python(_basic_comparison()))


def test_market_value_on_both_sides_is_fine():
    """Comparing one indicator to another should pass."""
    tree = _TREE.validate_python({
        "type": "comparison", "op": ">",
        "left": {"type": "indicator", "indicator": "rsi", "symbol": "TCS", "period": 14},
        "right": {"type": "indicator", "indicator": "rsi", "symbol": "INFY", "period": 14},
    })
    semantic_validate(tree)
