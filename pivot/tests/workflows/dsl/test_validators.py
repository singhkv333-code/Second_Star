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


# ── component field ──────────────────────────────────────────────────


def test_component_valid_for_bollinger_accepted():
    """Bollinger has named bands — ``component='lower'`` is allowed."""
    tree = _TREE.validate_python({
        "type": "comparison", "op": "<",
        "left": {"type": "price", "symbol": "NIFTYBEES"},
        "right": {
            "type": "indicator", "indicator": "bb",
            "symbol": "NIFTYBEES", "period": 20, "component": "lower",
        },
    })
    semantic_validate(tree)


def test_component_on_single_output_indicator_rejected():
    """RSI is single-output — supplying ``component`` is a planner bug
    we want surfaced loudly, not silently dropped."""
    tree = _TREE.validate_python({
        "type": "comparison", "op": "<",
        "left": {
            "type": "indicator", "indicator": "rsi",
            "symbol": "TCS", "period": 14, "component": "upper",
        },
        "right": _leaf_constant(30),
    })
    with pytest.raises(DSLValidationError, match="single-output"):
        semantic_validate(tree)


def test_component_unknown_value_rejected():
    """``component='middel'`` (typo) on Bollinger should fail with a
    helpful error listing valid components."""
    tree = _TREE.validate_python({
        "type": "comparison", "op": "<",
        "left": {"type": "price", "symbol": "TCS"},
        "right": {
            "type": "indicator", "indicator": "bb",
            "symbol": "TCS", "period": 20, "component": "middel",
        },
    })
    with pytest.raises(DSLValidationError, match="Unknown component"):
        semantic_validate(tree)


def test_component_none_is_default_path():
    """A tree with no ``component`` field validates exactly as before
    — guards backwards-compat with already-persisted trees."""
    tree = _TREE.validate_python({
        "type": "comparison", "op": "<",
        "left": {"type": "indicator", "indicator": "bb",
                 "symbol": "TCS", "period": 20},
        "right": _leaf_constant(0.2),
    })
    semantic_validate(tree)


# ── position leaf placement ─────────────────────────────────────────


def test_position_leaf_rejected_in_entry_tree():
    """Default ``semantic_validate`` (entry-tree context) rejects any
    position leaf — there is no position at entry-evaluation time."""
    tree = _TREE.validate_python({
        "type": "comparison", "op": ">",
        "left": {"type": "position", "field": "unrealised_pct"},
        "right": _leaf_constant(0.1),
    })
    with pytest.raises(DSLValidationError, match="position"):
        semantic_validate(tree)


def test_position_leaf_accepted_in_exit_tree_context():
    """allow_position=True is the exit-tree path. Same tree must
    validate cleanly."""
    tree = _TREE.validate_python({
        "type": "comparison", "op": ">",
        "left": {"type": "position", "field": "unrealised_pct"},
        "right": _leaf_constant(0.1),
    })
    semantic_validate(tree, allow_position=True)


def test_position_basis_rejected_on_scalar_field():
    """basis is only meaningful for unrealised_pct / unrealised_abs."""
    tree = _TREE.validate_python({
        "type": "comparison", "op": ">",
        "left": {"type": "position", "field": "entry_price", "basis": "low"},
        "right": _leaf_constant(0),
    })
    with pytest.raises(DSLValidationError, match="basis"):
        semantic_validate(tree, allow_position=True)


def test_position_basis_low_accepted_on_unrealised_pct():
    tree = _TREE.validate_python({
        "type": "comparison", "op": "<=",
        "left": {"type": "position", "field": "unrealised_pct",
                 "basis": "low"},
        "right": _leaf_constant(-0.05),
    })
    semantic_validate(tree, allow_position=True)


# ── exit policy lowering ────────────────────────────────────────────


def test_lower_stop_loss_pct_produces_bar_low_tree():
    from backend.workflows.dsl.backtest.schema import (
        ExitPolicyDeclarative, ExitPolicyTree, lower_exit_policy,
    )
    lowered = lower_exit_policy(
        ExitPolicyDeclarative(kind="stop_loss_pct", value=0.03)
    )
    assert isinstance(lowered, ExitPolicyTree)
    assert lowered.exit_at == "stop_price"
    assert lowered.stop_price_pct == 0.03
    t = lowered.tree
    assert t["op"] == "<="
    assert t["left"]["type"] == "position"
    assert t["left"]["field"] == "unrealised_pct"
    assert t["left"]["basis"] == "low"
    assert t["right"]["value"] == -0.03


def test_lower_n_day_hold_produces_bars_held_tree():
    from backend.workflows.dsl.backtest.schema import (
        ExitPolicyDeclarative, ExitPolicyTree, lower_exit_policy,
    )
    lowered = lower_exit_policy(
        ExitPolicyDeclarative(kind="n_day_hold", bars=7)
    )
    assert isinstance(lowered, ExitPolicyTree)
    assert lowered.exit_at == "next_open"
    assert lowered.stop_price_pct is None
    t = lowered.tree
    assert t["op"] == ">="
    assert t["left"]["field"] == "bars_held"
    assert t["right"]["value"] == 7.0


def test_lower_passes_through_already_lowered_tree():
    """Calling lower_exit_policy on a tree-shaped policy is a no-op."""
    from backend.workflows.dsl.backtest.schema import (
        ExitPolicyTree, lower_exit_policy,
    )
    src = ExitPolicyTree(
        kind="tree",
        tree={"type": "comparison", "op": ">=",
              "left": {"type": "position", "field": "bars_held"},
              "right": {"type": "constant", "value": 30}},
    )
    assert lower_exit_policy(src) is src
