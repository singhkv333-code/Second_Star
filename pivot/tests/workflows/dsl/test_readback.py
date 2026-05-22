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
