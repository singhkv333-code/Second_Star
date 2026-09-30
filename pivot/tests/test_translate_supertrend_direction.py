"""Supertrend is a DIRECTION (+1/-1) in the DSL, so a translated comparison
of the close against it is rewritten into a test of the direction's sign."""
from backend.workflows.dsl.llm_translate import _direction_not_level

P = {"type": "price", "symbol": "RELIANCE", "basis": "close"}
ST = {"type": "indicator", "indicator": "supertrend", "period": 10,
      "settings": {"multiplier": 3}}
ZERO = {"type": "constant", "value": 0}


def cmp(op, left, right):
    return {"type": "comparison", "op": op, "left": left, "right": right}


def test_price_below_supertrend_is_a_downtrend():
    assert _direction_not_level(cmp("<", P, ST)) == cmp("<", ST, ZERO)


def test_price_above_supertrend_is_an_uptrend():
    assert _direction_not_level(cmp(">=", P, ST)) == cmp(">", ST, ZERO)


def test_supertrend_on_the_left_is_flipped():
    # "Supertrend above the close" means the line is overhead: a downtrend.
    assert _direction_not_level(cmp(">", ST, P)) == cmp("<", ST, ZERO)


def test_crosses_keep_their_edge():
    assert _direction_not_level(cmp("crosses_below", P, ST)) == cmp("crosses_below", ST, ZERO)
    assert _direction_not_level(cmp("crosses_above", ST, P)) == cmp("crosses_below", ST, ZERO)


def test_equals_one_is_a_sign_test():
    assert _direction_not_level(cmp("==", ST, {"type": "constant", "value": 1})) == cmp(">", ST, ZERO)
    assert _direction_not_level(cmp("==", ST, {"type": "constant", "value": -1})) == cmp("<", ST, ZERO)


def test_nested_and_untouched_nodes():
    sma = {"type": "indicator", "indicator": "sma", "period": 200}
    tree = {"type": "logic", "op": "and",
            "operands": [cmp("<", P, ST), cmp(">", P, sma)]}
    out = _direction_not_level(tree)
    assert out["operands"][0] == cmp("<", ST, ZERO)
    assert out["operands"][1] == cmp(">", P, sma)
