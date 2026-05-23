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
    PositionNode,
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


# ── Position leaf ───────────────────────────────────────────────────


def test_position_leaf_each_field_round_trips():
    """Every documented position field must Pydantic-parse cleanly
    when wrapped in a comparison (the realistic shape)."""
    for field in (
        "entry_price", "unrealised_pct", "unrealised_abs",
        "bars_held", "peak_unrealised_pct", "drawdown_from_peak_pct",
    ):
        tree = _TREE.validate_python({
            "type": "comparison", "op": ">",
            "left": {"type": "position", "field": field},
            "right": {"type": "constant", "value": 0},
        })
        leaf = tree.left
        assert isinstance(leaf, PositionNode)
        assert leaf.field == field


def test_position_basis_optional_and_lowercase():
    leaf = _TREE.validate_python({
        "type": "comparison", "op": ">",
        "left": {"type": "position", "field": "unrealised_pct",
                 "basis": "low"},
        "right": {"type": "constant", "value": 0},
    }).left
    assert leaf.basis == "low"


def test_position_unknown_field_rejected():
    with pytest.raises(ValidationError):
        _TREE.validate_python({
            "type": "comparison", "op": ">",
            "left": {"type": "position", "field": "made_up_field"},
            "right": {"type": "constant", "value": 0},
        })


def test_position_unknown_basis_rejected_by_pydantic():
    with pytest.raises(ValidationError):
        _TREE.validate_python({
            "type": "comparison", "op": ">",
            "left": {"type": "position", "field": "unrealised_pct",
                     "basis": "midpoint"},
            "right": {"type": "constant", "value": 0},
        })


# ── Time-shift / basis on leaves ────────────────────────────────────


def test_indicator_offset_field_round_trips():
    leaf = _TREE.validate_python({
        "type": "comparison", "op": ">",
        "left": {"type": "indicator", "indicator": "rsi",
                 "symbol": "TCS", "period": 14, "offset": 5},
        "right": {"type": "constant", "value": 30},
    }).left
    assert leaf.offset == 5


def test_price_basis_and_offset():
    leaf = _TREE.validate_python({
        "type": "comparison", "op": ">",
        "left": {"type": "price", "symbol": "NIFTY",
                 "basis": "open", "offset": 1},
        "right": {"type": "constant", "value": 22000},
    }).left
    assert leaf.basis == "open"
    assert leaf.offset == 1


def test_offset_upper_bound_rejected():
    with pytest.raises(ValidationError):
        _TREE.validate_python({
            "type": "comparison", "op": ">",
            "left": {"type": "indicator", "indicator": "rsi",
                     "symbol": "TCS", "period": 14, "offset": 501},
            "right": {"type": "constant", "value": 30},
        })


def test_price_invalid_basis_rejected():
    with pytest.raises(ValidationError):
        _TREE.validate_python({
            "type": "comparison", "op": ">",
            "left": {"type": "price", "symbol": "TCS", "basis": "midpoint"},
            "right": {"type": "constant", "value": 100},
        })


# ── Conditional / aggregate node Pydantic-level ─────────────────────


def test_conditional_node_round_trip():
    tree = _TREE.validate_python({
        "type": "comparison", "op": "<=",
        "left": {"type": "position", "field": "unrealised_pct",
                 "basis": "low"},
        "right": {
            "type": "conditional",
            "if": {"type": "comparison", "op": ">",
                   "left": {"type": "indicator", "indicator": "atr",
                            "symbol": "TCS", "period": 14},
                   "right": {"type": "constant", "value": 50}},
            "then": {"type": "constant", "value": -0.08},
            "else": {"type": "constant", "value": -0.05},
        },
    })
    assert tree.right.type == "conditional"


def test_aggregate_highest_round_trip():
    leaf = _TREE.validate_python({
        "type": "comparison", "op": ">=",
        "left": {"type": "price", "symbol": "TCS"},
        "right": {
            "type": "aggregate", "op": "highest",
            "source": {"type": "price", "symbol": "TCS", "offset": 1},
            "bars": 20,
        },
    }).right
    assert leaf.op == "highest"
    assert leaf.bars == 20


def test_aggregate_bars_upper_bound_rejected():
    with pytest.raises(ValidationError):
        _TREE.validate_python({
            "type": "aggregate", "op": "highest",
            "source": {"type": "price", "symbol": "TCS"},
            "bars": 2001,
        })


def test_aggregate_unknown_op_rejected():
    with pytest.raises(ValidationError):
        _TREE.validate_python({
            "type": "aggregate", "op": "median_of",
            "source": {"type": "price", "symbol": "TCS"},
            "bars": 20,
        })


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
