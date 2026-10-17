"""Evaluator tests — the heart of the DSL.

We use a hand-rolled stub accessor so the tests run offline and stay
deterministic. The same shape any production accessor exposes.
"""
from __future__ import annotations

from typing import Optional

import pytest
from pydantic import TypeAdapter

from backend.workflows.dsl.evaluator import Ternary, evaluate
from backend.workflows.dsl.schema import Tree


_TREE = TypeAdapter(Tree)


class _StubAccessor:
    """Trivial in-memory accessor. Constructors take dicts that
    represent the desired return for each leaf type."""

    def __init__(
        self,
        *,
        prices: Optional[dict[str, float]] = None,
        indicators: Optional[dict[tuple[str, str, int], float]] = None,
        volumes: Optional[dict[tuple[str, int], float]] = None,
    ):
        self._prices = prices or {}
        self._indicators = indicators or {}
        self._volumes = volumes or {}
        self.calls: list[tuple] = []

    def get_price(
        self, *, symbol, exchange="NSE", basis="close", offset=0,
    ):
        self.calls.append(("price", symbol, exchange, basis, offset))
        # Tests may register either (symbol,) or (symbol, basis, offset).
        return self._prices.get(
            (symbol, basis, offset),
            self._prices.get(symbol),
        )

    def get_indicator(
        self, *, symbol, indicator, period, exchange="NSE",
        component=None, offset=0,
    ):
        self.calls.append(
            ("indicator", symbol, indicator, period, exchange,
             component, offset),
        )
        # Try the most specific key, fall back through generality
        # so existing tests with (symbol, indicator, period) tuples
        # keep working.
        return self._indicators.get(
            (symbol, indicator, period, component, offset),
            self._indicators.get(
                (symbol, indicator, period, component),
                self._indicators.get((symbol, indicator, period)),
            ),
        )

    def get_volume(self, *, symbol, bars=1, exchange="NSE", offset=0):
        self.calls.append(("volume", symbol, bars, exchange, offset))
        return self._volumes.get(
            (symbol, bars, offset),
            self._volumes.get((symbol, bars)),
        )


# ── Basic comparisons ───────────────────────────────────────────────


@pytest.mark.parametrize("op,left,right,expected", [
    (">", 100, 50, Ternary.TRUE),
    (">", 50, 100, Ternary.FALSE),
    ("<", 50, 100, Ternary.TRUE),
    (">=", 100, 100, Ternary.TRUE),
    ("<=", 100, 100, Ternary.TRUE),
    ("==", 100, 100, Ternary.TRUE),
    ("==", 100, 101, Ternary.FALSE),
])
def test_basic_comparison_ops(op, left, right, expected):
    tree = _TREE.validate_python({
        "type": "comparison", "op": op,
        "left": {"type": "price", "symbol": "X"},
        "right": {"type": "constant", "value": right},
    })
    accessor = _StubAccessor(prices={"X": left})
    out = evaluate(tree, accessor=accessor)
    assert out.value is expected


def test_indicator_below_threshold_fires():
    tree = _TREE.validate_python({
        "type": "comparison", "op": "<",
        "left": {"type": "indicator", "indicator": "rsi", "symbol": "TCS", "period": 14},
        "right": {"type": "constant", "value": 30},
    })
    accessor = _StubAccessor(indicators={("TCS", "rsi", 14): 27.3})
    assert evaluate(tree, accessor=accessor).value is Ternary.TRUE


# ── Kleene three-valued logic ───────────────────────────────────────


def test_missing_data_propagates_as_unknown():
    """If an indicator value is missing, the whole comparison is UNKNOWN
    — not False, not True. This prevents spurious fires."""
    tree = _TREE.validate_python({
        "type": "comparison", "op": "<",
        "left": {"type": "indicator", "indicator": "rsi", "symbol": "TCS", "period": 14},
        "right": {"type": "constant", "value": 30},
    })
    accessor = _StubAccessor()  # nothing registered → returns None
    assert evaluate(tree, accessor=accessor).value is Ternary.UNKNOWN


def test_and_with_one_false_short_circuits_to_false():
    """Even when other branches are UNKNOWN, a single False makes the
    AND False — there's no ambiguity."""
    tree = _TREE.validate_python({
        "type": "logic", "op": "and",
        "operands": [
            {"type": "comparison", "op": "<",
             "left": {"type": "price", "symbol": "X"},
             "right": {"type": "constant", "value": 100}},   # 200 < 100 → False
            {"type": "comparison", "op": ">",
             "left": {"type": "indicator", "indicator": "rsi", "symbol": "Y", "period": 14},
             "right": {"type": "constant", "value": 50}},   # missing → UNKNOWN
        ],
    })
    accessor = _StubAccessor(prices={"X": 200})
    assert evaluate(tree, accessor=accessor).value is Ternary.FALSE


def test_and_unknown_plus_true_is_unknown():
    """All knowns are True; some operands are UNKNOWN. AND can't decide."""
    tree = _TREE.validate_python({
        "type": "logic", "op": "and",
        "operands": [
            {"type": "comparison", "op": ">",
             "left": {"type": "price", "symbol": "X"},
             "right": {"type": "constant", "value": 100}},   # 200 > 100 → True
            {"type": "comparison", "op": ">",
             "left": {"type": "indicator", "indicator": "rsi", "symbol": "Y", "period": 14},
             "right": {"type": "constant", "value": 50}},   # missing → UNKNOWN
        ],
    })
    accessor = _StubAccessor(prices={"X": 200})
    assert evaluate(tree, accessor=accessor).value is Ternary.UNKNOWN


def test_or_with_one_true_short_circuits_to_true():
    tree = _TREE.validate_python({
        "type": "logic", "op": "or",
        "operands": [
            {"type": "comparison", "op": ">",
             "left": {"type": "price", "symbol": "X"},
             "right": {"type": "constant", "value": 100}},   # True
            {"type": "comparison", "op": ">",
             "left": {"type": "indicator", "indicator": "rsi", "symbol": "Y", "period": 14},
             "right": {"type": "constant", "value": 50}},   # missing → UNKNOWN
        ],
    })
    accessor = _StubAccessor(prices={"X": 200})
    assert evaluate(tree, accessor=accessor).value is Ternary.TRUE


def test_not_inverts_true_and_false():
    base = {"type": "comparison", "op": ">",
            "left": {"type": "price", "symbol": "X"},
            "right": {"type": "constant", "value": 100}}
    tree = _TREE.validate_python({"type": "logic", "op": "not", "operands": [base]})

    assert evaluate(tree, accessor=_StubAccessor(prices={"X": 200})).value is Ternary.FALSE
    assert evaluate(tree, accessor=_StubAccessor(prices={"X": 50})).value is Ternary.TRUE


def test_not_propagates_unknown():
    base = {"type": "comparison", "op": ">",
            "left": {"type": "price", "symbol": "X"},
            "right": {"type": "constant", "value": 100}}
    tree = _TREE.validate_python({"type": "logic", "op": "not", "operands": [base]})
    assert evaluate(tree, accessor=_StubAccessor()).value is Ternary.UNKNOWN


# ── Crossings — the stateful path ───────────────────────────────────


def test_crosses_above_returns_false_on_first_tick():
    """No previous-tick state → can't observe a transition yet."""
    tree = _TREE.validate_python({
        "type": "comparison", "op": "crosses_above",
        "left": {"type": "indicator", "indicator": "rsi", "symbol": "TCS", "period": 14},
        "right": {"type": "constant", "value": 30},
    })
    accessor = _StubAccessor(indicators={("TCS", "rsi", 14): 35.0})
    out = evaluate(tree, accessor=accessor)
    assert out.value is Ternary.FALSE
    # State was populated for the next tick.
    assert any("left:" in k for k in out.new_state)
    assert any("right:" in k for k in out.new_state)


def test_crosses_above_fires_on_transition():
    tree = _TREE.validate_python({
        "type": "comparison", "op": "crosses_above",
        "left": {"type": "indicator", "indicator": "rsi", "symbol": "TCS", "period": 14},
        "right": {"type": "constant", "value": 30},
    })
    accessor = _StubAccessor(indicators={("TCS", "rsi", 14): 35.0})

    # First tick: rsi=25, below threshold.
    accessor._indicators[("TCS", "rsi", 14)] = 25.0
    first = evaluate(tree, accessor=accessor)
    assert first.value is Ternary.FALSE

    # Second tick: rsi=35, ABOVE threshold. With prev state from
    # first tick, this is a crosses_above.
    accessor._indicators[("TCS", "rsi", 14)] = 35.0
    second = evaluate(tree, accessor=accessor, prev_state=first.new_state)
    assert second.value is Ternary.TRUE


def test_crosses_above_does_not_fire_if_still_above():
    tree = _TREE.validate_python({
        "type": "comparison", "op": "crosses_above",
        "left": {"type": "indicator", "indicator": "rsi", "symbol": "TCS", "period": 14},
        "right": {"type": "constant", "value": 30},
    })
    accessor = _StubAccessor(indicators={("TCS", "rsi", 14): 35.0})
    # Tick 1: 35 (already above)
    s1 = evaluate(tree, accessor=accessor)
    # Tick 2: 40 (still above — no transition)
    accessor._indicators[("TCS", "rsi", 14)] = 40.0
    s2 = evaluate(tree, accessor=accessor, prev_state=s1.new_state)
    assert s2.value is Ternary.FALSE


def test_crosses_below_fires_on_transition():
    tree = _TREE.validate_python({
        "type": "comparison", "op": "crosses_below",
        "left": {"type": "indicator", "indicator": "rsi", "symbol": "TCS", "period": 14},
        "right": {"type": "constant", "value": 70},
    })
    accessor = _StubAccessor(indicators={("TCS", "rsi", 14): 80.0})
    s1 = evaluate(tree, accessor=accessor)
    accessor._indicators[("TCS", "rsi", 14)] = 65.0
    s2 = evaluate(tree, accessor=accessor, prev_state=s1.new_state)
    assert s2.value is Ternary.TRUE


# ── The motivating real-world example ──────────────────────────────


def test_rsi_tcs_below_30_and_nifty_above_23k():
    """The user's canonical 'why DSL exists' example."""
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

    # Both conditions met → fires.
    accessor = _StubAccessor(
        prices={"NIFTY": 23250},
        indicators={("TCS", "rsi", 14): 26.5},
    )
    assert evaluate(tree, accessor=accessor).value is Ternary.TRUE

    # Only RSI condition met → False.
    accessor2 = _StubAccessor(
        prices={"NIFTY": 22500},
        indicators={("TCS", "rsi", 14): 26.5},
    )
    assert evaluate(tree, accessor=accessor2).value is Ternary.FALSE

    # NIFTY data missing → UNKNOWN (we won't fire on incomplete data).
    accessor3 = _StubAccessor(
        indicators={("TCS", "rsi", 14): 26.5},
    )
    assert evaluate(tree, accessor=accessor3).value is Ternary.UNKNOWN


# ── Ternary __bool__ contract ──────────────────────────────────────


def test_ternary_truthy_only_for_TRUE():
    assert bool(Ternary.TRUE) is True
    assert bool(Ternary.FALSE) is False
    assert bool(Ternary.UNKNOWN) is False  # critical: UNKNOWN is NOT truthy
