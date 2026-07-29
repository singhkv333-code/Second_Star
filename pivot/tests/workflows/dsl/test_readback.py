"""Readback tests — tree → plain English."""
from __future__ import annotations

import pytest
from pydantic import TypeAdapter

from backend.workflows.dsl.readback import tree_to_english
from backend.workflows.dsl.schema import Tree


_TREE = TypeAdapter(Tree)


def test_simple_indicator_lt_constant():
    tree = _TREE.validate_python({
        "type": "comparison", "op": "<",
        "left": {"type": "indicator", "indicator": "rsi", "symbol": "TCS", "period": 14},
        "right": {"type": "constant", "value": 30},
    })
    assert tree_to_english(tree) == "RSI(14) of TCS < 30"


def test_price_gt_threshold():
    tree = _TREE.validate_python({
        "type": "comparison", "op": ">",
        "left": {"type": "price", "symbol": "NIFTY"},
        "right": {"type": "constant", "value": 23000},
    })
    assert tree_to_english(tree) == "price of NIFTY > 23,000"


def test_two_condition_AND():
    tree = _TREE.validate_python({
        "type": "logic", "op": "and",
        "operands": [
            {"type": "comparison", "op": "<",
             "left": {"type": "indicator", "indicator": "rsi", "symbol": "TCS", "period": 14},
             "right": {"type": "constant", "value": 30}},
            {"type": "comparison", "op": ">",
             "left": {"type": "price", "symbol": "NIFTY"},
             "right": {"type": "constant", "value": 23000}},
        ],
    })
    out = tree_to_english(tree)
    assert "RSI(14) of TCS < 30" in out
    assert "AND" in out
    assert "price of NIFTY > 23,000" in out


def test_three_condition_OR():
    tree = _TREE.validate_python({
        "type": "logic", "op": "or",
        "operands": [
            {"type": "comparison", "op": "<",
             "left": {"type": "price", "symbol": "A"},
             "right": {"type": "constant", "value": 1}},
            {"type": "comparison", "op": "<",
             "left": {"type": "price", "symbol": "B"},
             "right": {"type": "constant", "value": 2}},
            {"type": "comparison", "op": "<",
             "left": {"type": "price", "symbol": "C"},
             "right": {"type": "constant", "value": 3}},
        ],
    })
    out = tree_to_english(tree)
    assert " OR " in out
    assert out.count(" OR ") == 2


def test_NOT_wraps_in_parens():
    tree = _TREE.validate_python({
        "type": "logic", "op": "not",
        "operands": [{
            "type": "comparison", "op": ">",
            "left": {"type": "price", "symbol": "X"},
            "right": {"type": "constant", "value": 100},
        }],
    })
    assert tree_to_english(tree) == "NOT (price of X > 100)"


def test_crosses_above_phrasing():
    tree = _TREE.validate_python({
        "type": "comparison", "op": "crosses_above",
        "left": {"type": "indicator", "indicator": "rsi", "symbol": "TCS", "period": 14},
        "right": {"type": "constant", "value": 30},
    })
    assert "crosses above" in tree_to_english(tree)


def test_nested_logic_parenthesizes_subexpressions():
    tree = _TREE.validate_python({
        "type": "logic", "op": "and",
        "operands": [
            {"type": "logic", "op": "or",
             "operands": [
                 {"type": "comparison", "op": "<",
                  "left": {"type": "price", "symbol": "A"},
                  "right": {"type": "constant", "value": 1}},
                 {"type": "comparison", "op": "<",
                  "left": {"type": "price", "symbol": "B"},
                  "right": {"type": "constant", "value": 2}},
             ]},
            {"type": "comparison", "op": ">",
             "left": {"type": "price", "symbol": "C"},
             "right": {"type": "constant", "value": 100}},
        ],
    })
    out = tree_to_english(tree)
    # Inner OR is parenthesised so the precedence is unambiguous.
    assert "(price of A < 1 OR price of B < 2)" in out


def test_integer_constants_format_with_commas():
    tree = _TREE.validate_python({
        "type": "comparison", "op": ">",
        "left": {"type": "price", "symbol": "X"},
        "right": {"type": "constant", "value": 1500000},
    })
    out = tree_to_english(tree)
    assert "1,500,000" in out


def test_float_constants_strip_trailing_zeros():
    tree = _TREE.validate_python({
        "type": "comparison", "op": "<",
        "left": {"type": "indicator", "indicator": "rsi", "symbol": "TCS", "period": 14},
        "right": {"type": "constant", "value": 29.5},
    })
    assert tree_to_english(tree).endswith("< 29.5")


def test_volume_node_phrasing():
    tree = _TREE.validate_python({
        "type": "comparison", "op": ">",
        "left": {"type": "volume", "symbol": "TCS", "bars": 5},
        "right": {"type": "constant", "value": 100000},
    })
    out = tree_to_english(tree)
    assert "5-bar volume of TCS" in out


def test_indicator_component_prepends_phrase():
    tree = _TREE.validate_python({
        "type": "comparison", "op": "<",
        "left": {"type": "price", "symbol": "NIFTYBEES"},
        "right": {
            "type": "indicator", "indicator": "bb",
            "symbol": "NIFTYBEES", "period": 20, "component": "lower",
        },
    })
    assert tree_to_english(tree) == "price of NIFTYBEES < lower BB(20) of NIFTYBEES"


def test_indicator_without_component_unchanged():
    tree = _TREE.validate_python({
        "type": "comparison", "op": "<",
        "left": {"type": "indicator", "indicator": "rsi",
                 "symbol": "TCS", "period": 14},
        "right": {"type": "constant", "value": 30},
    })
    assert tree_to_english(tree) == "RSI(14) of TCS < 30"


# ── position leaf readback ──────────────────────────────────────────


def test_position_unrealised_pct_renders_as_pnl():
    tree = _TREE.validate_python({
        "type": "comparison", "op": ">=",
        "left": {"type": "position", "field": "unrealised_pct"},
        "right": {"type": "constant", "value": 0.10},
    })
    assert tree_to_english(tree) == "unrealised P&L ≥ 0.1"


def test_position_basis_low_adds_phrase():
    tree = _TREE.validate_python({
        "type": "comparison", "op": "<=",
        "left": {"type": "position", "field": "unrealised_pct",
                 "basis": "low"},
        "right": {"type": "constant", "value": -0.05},
    })
    assert tree_to_english(tree) == "unrealised P&L at bar low ≤ -0.05"


def test_position_bars_held_renders_short():
    tree = _TREE.validate_python({
        "type": "comparison", "op": ">=",
        "left": {"type": "position", "field": "bars_held"},
        "right": {"type": "constant", "value": 30},
    })
    assert tree_to_english(tree) == "bars held ≥ 30"


def test_position_drawdown_from_peak_renders_friendly():
    tree = _TREE.validate_python({
        "type": "comparison", "op": ">=",
        "left": {"type": "position", "field": "drawdown_from_peak_pct"},
        "right": {"type": "constant", "value": 0.08},
    })
    assert tree_to_english(tree) == "drawdown from peak ≥ 0.08"


# ── C.4 + C.5 readbacks ────────────────────────────────────────────


def test_gap_leaf_reads_naturally():
    tree = _TREE.validate_python({
        "type": "comparison", "op": "<",
        "left": {"type": "gap", "symbol": "NIFTY"},
        "right": {"type": "constant", "value": -0.01},
    })
    assert tree_to_english(tree) == "gap of NIFTY < -0.01"


def test_pct_change_renders_with_bars():
    tree = _TREE.validate_python({
        "type": "comparison", "op": ">",
        "left": {"type": "pct_change", "symbol": "TCS", "bars": 5},
        "right": {"type": "constant", "value": 0.03},
    })
    assert tree_to_english(tree) == "5-bar % change of TCS > 0.03"


def test_spread_renders_as_ratio():
    tree = _TREE.validate_python({
        "type": "comparison", "op": "<",
        "left": {"type": "spread", "a": "TCS", "b": "INFY"},
        "right": {"type": "constant", "value": 1.5},
    })
    assert tree_to_english(tree) == "TCS / INFY < 1.5"


def test_math_binary_renders_infix():
    tree = _TREE.validate_python({
        "type": "comparison", "op": ">",
        "left": {"type": "math", "op": "-", "operands": [
            {"type": "price", "symbol": "TCS"},
            {"type": "price", "symbol": "TCS", "offset": 1},
        ]},
        "right": {"type": "constant", "value": 0},
    })
    # Math is at depth 1 → parens; price-offset suffix preserved.
    assert tree_to_english(tree) == \
        "(price of TCS - price of TCS (1 bar ago)) > 0"


def test_math_min_renders_function_call():
    tree = _TREE.validate_python({
        "type": "comparison", "op": "<",
        "left": {"type": "math", "op": "min", "operands": [
            {"type": "price", "symbol": "TCS"},
            {"type": "price", "symbol": "INFY"},
        ]},
        "right": {"type": "constant", "value": 4000},
    })
    assert tree_to_english(tree) == "min(price of TCS, price of INFY) < 4,000"
