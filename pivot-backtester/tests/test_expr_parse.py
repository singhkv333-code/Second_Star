"""Parser unit tests — no DB.

These cover: precedence, suggestions, injection rejection, case insensitivity.
"""
from __future__ import annotations

import pytest

from backtester.expr import parse_expression
from backtester.expr.ast import BinOp, BoolOp, Compare, Ident, Number
from backtester.expr.validator import ValidationError, validate
from backtester.fields import load_default_registry


def test_simple_comparison_parses():
    ast = parse_expression("pe_ratio < 10")
    assert isinstance(ast, Compare)
    assert ast.op == "<"
    assert isinstance(ast.left, Ident) and ast.left.name == "pe_ratio"
    assert isinstance(ast.right, Number) and ast.right.value == 10


def test_and_groups_below_or():
    """`a OR b AND c` ≡ `a OR (b AND c)`."""
    ast = parse_expression("pe_ratio < 10 OR roe > 15 AND debt_to_equity < 1")
    assert isinstance(ast, BoolOp) and ast.op == "OR"
    # Right-hand operand should be the AND group.
    assert any(isinstance(op, BoolOp) and op.op == "AND" for op in ast.operands)


def test_arithmetic_precedence():
    """`a + b * c` ≡ `a + (b * c)`."""
    ast = parse_expression("revenue + net_profit * 2 > 0")
    assert isinstance(ast, Compare)
    add = ast.left
    assert isinstance(add, BinOp) and add.op == "+"
    mul = add.right
    assert isinstance(mul, BinOp) and mul.op == "*"


def test_parens_override_precedence():
    ast = parse_expression("(revenue + net_profit) * 2 > 0")
    assert isinstance(ast, Compare)
    outer = ast.left
    assert isinstance(outer, BinOp) and outer.op == "*"
    inner = outer.left
    assert isinstance(inner, BinOp) and inner.op == "+"


def test_case_insensitive_keywords():
    a = parse_expression("pe_ratio < 10 and roe > 15")
    b = parse_expression("pe_ratio < 10 AND roe > 15")
    assert a == b


def test_unary_minus():
    ast = parse_expression("-net_profit > -100")
    assert isinstance(ast, Compare)


def test_validator_unknown_field_suggests():
    reg = load_default_registry()
    ast = parse_expression("pe_rato < 10")     # typo
    with pytest.raises(ValidationError) as exc:
        validate(ast, reg)
    msg = str(exc.value)
    assert "pe_ratio" in msg, f"expected suggestion in: {msg}"


def test_validator_rejects_top_level_arithmetic():
    """`pe_ratio` alone is a numeric expression, not a predicate."""
    reg = load_default_registry()
    ast = parse_expression("pe_ratio")
    with pytest.raises(ValidationError):
        validate(ast, reg)


def test_sql_injection_attempt_fails_to_parse():
    """Apostrophes / semicolons / -- aren't in the grammar."""
    import lark
    with pytest.raises(lark.exceptions.LarkError):
        parse_expression("pe_ratio < 10; DROP TABLE companies;--")


def test_ttm_suffix_resolves():
    reg = load_default_registry()
    ast = parse_expression("net_profit_ttm > 0")
    result = validate(ast, reg)
    assert "net_profit_ttm" in result.referenced_fields


def test_ttm_on_non_eligible_field_rejected():
    reg = load_default_registry()
    ast = parse_expression("equity_share_capital_ttm > 0")
    with pytest.raises(ValidationError) as exc:
        validate(ast, reg)
    assert "equity_share_capital" in str(exc.value)
