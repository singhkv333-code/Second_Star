"""Schema-layer tests for the DSL.

Pydantic-level validation only — depth limits and indicator-registry
lookups live in test_validators.py.
"""
from __future__ import annotations

import math

import pytest
from pydantic import TypeAdapter, ValidationError

from backend.workflows.dsl.schema import (
    ComparisonNode,
    ConstantNode,
    IndicatorNode,
    LogicNode,
    PriceNode,
    Tree,
    VolumeNode,
)


_TREE = TypeAdapter(Tree)


# ── Leaf nodes ───────────────────────────────────────────────────────


def test_indicator_round_trip():
    payload = {"type": "indicator", "indicator": "rsi", "symbol": "TCS", "period": 14}
    node = _TREE.validate_python(payload)
    assert isinstance(node, IndicatorNode)
    assert node.indicator == "rsi"
    assert node.symbol == "TCS"
    assert node.period == 14
    assert node.exchange == "NSE"


def test_indicator_strips_whitespace():
    node = _TREE.validate_python(
        {"type": "indicator", "indicator": "  rsi ", "symbol": " TCS ", "period": 14}
    )
    assert node.indicator == "rsi"
    assert node.symbol == "TCS"


def test_indicator_period_must_be_positive():
    with pytest.raises(ValidationError):
        _TREE.validate_python(
            {"type": "indicator", "indicator": "rsi", "symbol": "TCS", "period": 0}
        )


def test_indicator_period_upper_bound():
    with pytest.raises(ValidationError):
        _TREE.validate_python(
            {"type": "indicator", "indicator": "rsi", "symbol": "TCS", "period": 5001}
        )


def test_price_default_exchange():
    node = _TREE.validate_python({"type": "price", "symbol": "NIFTY"})
    assert isinstance(node, PriceNode)
    assert node.exchange == "NSE"


def test_volume_default_bars():
    node = _TREE.validate_python({"type": "volume", "symbol": "TCS"})
    assert isinstance(node, VolumeNode)
    assert node.bars == 1


def test_constant_rejects_nan():
    with pytest.raises(ValidationError):
        _TREE.validate_python({"type": "constant", "value": math.nan})


def test_constant_rejects_infinity():
    with pytest.raises(ValidationError):
        _TREE.validate_python({"type": "constant", "value": math.inf})


def test_constant_accepts_integers_and_floats():
    assert _TREE.validate_python({"type": "constant", "value": 30}).value == 30.0
    assert _TREE.validate_python({"type": "constant", "value": 30.5}).value == 30.5


# ── Comparison ──────────────────────────────────────────────────────


def test_comparison_basic_shape():
    node = _TREE.validate_python({
        "type": "comparison", "op": "<",
        "left": {"type": "indicator", "indicator": "rsi", "symbol": "TCS", "period": 14},
        "right": {"type": "constant", "value": 30},
    })
    assert isinstance(node, ComparisonNode)
    assert node.op == "<"
    assert isinstance(node.left, IndicatorNode)
    assert isinstance(node.right, ConstantNode)


def test_comparison_rejects_unknown_op():
    with pytest.raises(ValidationError):
        _TREE.validate_python({
            "type": "comparison", "op": "approximately_equals",
            "left": {"type": "constant", "value": 1},
            "right": {"type": "constant", "value": 1},
        })


@pytest.mark.parametrize("op", [
    ">", "<", ">=", "<=", "==",
    "crosses_above", "crosses_below",
])
def test_comparison_accepts_all_supported_ops(op):
    node = _TREE.validate_python({
        "type": "comparison", "op": op,
        "left": {"type": "price", "symbol": "X"},
        "right": {"type": "constant", "value": 0},
    })
    assert node.op == op


# ── Logic ───────────────────────────────────────────────────────────


def test_logic_and_with_two_operands():
    node = _TREE.validate_python({
        "type": "logic", "op": "and",
        "operands": [
            {"type": "comparison", "op": "<",
             "left": {"type": "price", "symbol": "X"},
             "right": {"type": "constant", "value": 30}},
            {"type": "comparison", "op": ">",
             "left": {"type": "price", "symbol": "Y"},
             "right": {"type": "constant", "value": 100}},
        ],
    })
    assert isinstance(node, LogicNode)
    assert node.op == "and"
    assert len(node.operands) == 2


def test_logic_and_rejects_single_operand():
    with pytest.raises(ValidationError):
        _TREE.validate_python({
            "type": "logic", "op": "and",
            "operands": [
                {"type": "comparison", "op": "<",
                 "left": {"type": "price", "symbol": "X"},
                 "right": {"type": "constant", "value": 30}},
            ],
        })


def test_logic_not_requires_exactly_one_operand():
    with pytest.raises(ValidationError):
        _TREE.validate_python({
            "type": "logic", "op": "not",
            "operands": [
                {"type": "comparison", "op": "<",
                 "left": {"type": "price", "symbol": "X"},
                 "right": {"type": "constant", "value": 1}},
                {"type": "comparison", "op": "<",
                 "left": {"type": "price", "symbol": "Y"},
                 "right": {"type": "constant", "value": 2}},
            ],
        })


def test_logic_not_accepts_one_operand():
    node = _TREE.validate_python({
        "type": "logic", "op": "not",
        "operands": [
            {"type": "comparison", "op": "<",
             "left": {"type": "price", "symbol": "X"},
             "right": {"type": "constant", "value": 1}},
        ],
    })
    assert node.op == "not"
    assert len(node.operands) == 1


def test_logic_caps_operand_count():
    with pytest.raises(ValidationError):
        _TREE.validate_python({
            "type": "logic", "op": "or",
            "operands": [
                {"type": "comparison", "op": "<",
                 "left": {"type": "price", "symbol": f"S{i}"},
                 "right": {"type": "constant", "value": 1}}
                for i in range(9)
            ],
        })


# ── Discriminated union ────────────────────────────────────────────


def test_unknown_type_is_rejected():
    with pytest.raises(ValidationError):
        _TREE.validate_python({"type": "spreadsheet_formula", "value": 1})


def test_recursive_nesting_works():
    """Tree-of-trees should parse cleanly without forward-ref errors."""
    payload = {
        "type": "logic", "op": "and",
        "operands": [
            {
                "type": "logic", "op": "or",
                "operands": [
                    {"type": "comparison", "op": ">",
                     "left": {"type": "indicator", "indicator": "rsi", "symbol": "TCS", "period": 14},
                     "right": {"type": "constant", "value": 70}},
                    {"type": "comparison", "op": "<",
                     "left": {"type": "indicator", "indicator": "rsi", "symbol": "TCS", "period": 14},
                     "right": {"type": "constant", "value": 30}},
                ],
            },
            {"type": "comparison", "op": ">",
             "left": {"type": "price", "symbol": "NIFTY"},
             "right": {"type": "constant", "value": 23000}},
        ],
    }
    node = _TREE.validate_python(payload)
    assert isinstance(node, LogicNode)
    assert node.op == "and"
    # Two top-level operands; first is itself a LogicNode (OR).
    assert isinstance(node.operands[0], LogicNode)
    assert node.operands[0].op == "or"
