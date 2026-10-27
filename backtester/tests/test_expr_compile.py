"""Compiler unit tests — no DB.

We assert *structural* properties of the generated SQL rather than its exact
text, so cosmetic changes to whitespace / aliases don't break tests.
"""
from __future__ import annotations

import pytest

from backtester.expr import compile_to_sql, parse_expression
from backtester.fields import load_default_registry


def _compile(expr: str):
    reg = load_default_registry()
    return compile_to_sql(parse_expression(expr), reg)


def test_simple_pe_compiles_to_two_ctes():
    """pe_ratio expands to price / eps_basic_ttm — exactly two leaves."""
    c = _compile("pe_ratio < 10")
    leaf_names = {s.name for s in c.leaf_fields}
    assert leaf_names == {"price", "eps_basic_ttm"}


def test_division_emits_nullif():
    c = _compile("pe_ratio < 10")
    assert "NULLIF(" in c.sql


def test_survivorship_guard_present():
    c = _compile("pe_ratio < 10")
    assert "delisted_on IS NULL OR" in c.sql
    assert "listed_on IS NULL OR" in c.sql


def test_compound_predicate_collects_all_leaves():
    c = _compile("pe_ratio < 10 AND roe > 15 AND debt_to_equity < 1")
    leaves = {s.name for s in c.leaf_fields}
    # roe expands to net_profit_ttm + (equity_share_capital + reserves)
    # debt_to_equity expands to total_debt + (equity_share_capital + reserves)
    assert "price" in leaves
    assert "eps_basic_ttm" in leaves
    assert "net_profit_ttm" in leaves
    assert "equity_share_capital" in leaves
    assert "reserves" in leaves
    assert "total_debt" in leaves


def test_numeric_literals_become_parameters():
    c = _compile("pe_ratio < 10 AND roe > 15")
    # roe expands to (net_profit_ttm * 100) / (...) — so 100 enters the
    # param list as a literal between the user's 10 and 15.
    assert c.params == [10.0, 100.0, 15.0]
    # The SQL must reference each param exactly once.
    for i in (2, 3, 4):
        assert f"${i}" in c.sql


def test_referenced_fields_are_user_facing_names():
    """The user wrote pe_ratio; the audit trail should say pe_ratio, not its expansion."""
    c = _compile("pe_ratio < 10 AND roe > 15")
    assert c.referenced_fields == ["pe_ratio", "roe"]


def test_quarterly_cte_demands_4_quarters():
    """The quarterly leg still demands 4 full quarters (partial-TTM is a footgun)."""
    c = _compile("net_profit_ttm > 0")
    assert "HAVING COUNT(*) = 4" in c.sql
    assert "rn <= 4" in c.sql


def test_ttm_targets_real_statement_not_quarterly_results():
    """TTM must query the field's own statement + period_kind, not a phantom
    'quarterly_results' statement (which the live mc schema does not have)."""
    c = _compile("net_profit_ttm > 0")
    assert "quarterly_results" not in c.sql
    assert "period_kind = 'quarterly'" in c.sql
    # net_profit lives on profit_loss.
    assert "statement = 'profit_loss'" in c.sql


def test_ttm_falls_back_to_annual():
    """When 4 quarters aren't present, TTM resolves to the latest annual value
    (which already spans 12 months) — so annual-only data still yields a value."""
    c = _compile("net_profit_ttm > 0")
    assert "COALESCE(q.val, a.val)" in c.sql
    assert "FULL OUTER JOIN q USING (sc_id)" in c.sql
    # The fallback path excludes quarterly rows so it can't double-count.
    assert "period_kind IS DISTINCT FROM 'quarterly'" in c.sql


def test_annual_field_uses_distinct_on_period_end_desc():
    c = _compile("equity_share_capital > 0")
    assert "DISTINCT ON (sc_id)" in c.sql
    assert "ORDER BY sc_id, period_end DESC" in c.sql


def test_availability_date_is_the_filter_not_period_end():
    """The non-negotiable PIT filter."""
    c = _compile("net_profit > 0")
    assert "availability_date <= $1" in c.sql
    # period_end alone must NOT be the visibility filter.
    assert "period_end <= $1" not in c.sql


def test_price_cte_uses_trade_date():
    c = _compile("price > 100")
    assert "FROM mc.daily_prices" in c.sql
    assert "trade_date <= $1" in c.sql


def test_unknown_field_raises():
    from backtester.expr.validator import ValidationError, validate
    reg = load_default_registry()
    ast = parse_expression("zorglub > 0")
    with pytest.raises(ValidationError):
        validate(ast, reg)
