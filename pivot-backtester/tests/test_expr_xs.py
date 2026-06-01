"""Cross-sectional transforms (Phase 2.1) — parse / validate / compile.

rank / decile / quantile / zscore / percentrank let a screen RANK across the
universe at date T (window functions), not just threshold a raw factor — which
unlocks long-only factor selection ("the top-decile-ROE names"). DB-free
structural tests, mirroring test_expr_compile.py.
"""
from __future__ import annotations

import pytest

from backtester.expr import compile_to_sql, parse_expression
from backtester.expr.ast import Compare, Func, Number
from backtester.expr.validator import ValidationError, validate
from backtester.fields import load_default_registry


@pytest.fixture(scope="module")
def reg():
    return load_default_registry()


def _sql(expr, reg):
    return compile_to_sql(parse_expression(expr), reg).sql


# ── parse ───────────────────────────────────────────────────────────

def test_func_parses_as_node():
    ast = parse_expression("decile(roe) == 10")
    assert isinstance(ast, Compare)
    assert isinstance(ast.left, Func) and ast.left.name == "decile"
    assert len(ast.left.args) == 1


def test_quantile_parses_two_args():
    ast = parse_expression("quantile(roe, 5) >= 4")
    f = ast.left
    assert isinstance(f, Func) and f.name == "quantile"
    assert len(f.args) == 2 and isinstance(f.args[1], Number) and f.args[1].value == 5


# ── validate ────────────────────────────────────────────────────────

def test_valid_xs_predicates_accepted(reg):
    for expr in (
        "decile(roe) == 10",
        "zscore(pe_ratio) > 1.5",
        "quantile(roe, 5) >= 4",
        "rank(roe) <= 100 AND pe_ratio < 30",
        "percentrank(debt_to_equity) < 0.2",
    ):
        validate(parse_expression(expr), reg)  # must not raise


@pytest.mark.parametrize("expr,frag", [
    ("foobar(roe) > 1", "Unknown function"),
    ("decile(roe, 2) == 1", "argument"),
    ("quantile(roe, 2.5) > 1", "integer literal"),
    ("zscore(rank(roe)) > 1", "nested"),
    ("decile(roe)", "boolean predicate"),  # bare func is not a predicate
])
def test_bad_xs_rejected(reg, expr, frag):
    with pytest.raises(ValidationError) as e:
        validate(parse_expression(expr), reg)
    assert frag.lower() in str(e.value).lower()


# ── compile ─────────────────────────────────────────────────────────

def test_decile_emits_ntile_and_ranked_cte(reg):
    sql = _sql("decile(roe) == 10", reg)
    assert "NTILE(10) OVER (ORDER BY" in sql
    assert "ranked AS (" in sql          # the intermediate window layer
    assert "ranked._xs_0" in sql         # predicate references the window column


def test_quantile_emits_ntile_n(reg):
    assert "NTILE(5) OVER (ORDER BY" in _sql("quantile(roe, 5) >= 4", reg)


def test_zscore_emits_avg_and_stddev(reg):
    sql = _sql("zscore(pe_ratio) > 1.5", reg)
    assert "AVG(" in sql and "STDDEV_SAMP(" in sql and "OVER ()" in sql


def test_rank_and_percentrank(reg):
    assert "RANK() OVER (ORDER BY" in _sql("rank(roe) <= 100", reg)
    assert "PERCENT_RANK() OVER (ORDER BY" in _sql("percentrank(roe) > 0.9", reg)


def test_equality_operator_maps_to_sql(reg):
    # The expression language uses ==/!=; SQL needs =/<>.
    sql = _sql("decile(roe) == 10", reg)
    assert "==" not in sql and " = $" in sql
    sql2 = _sql("decile(roe) != 1", reg)
    assert "!=" not in sql2 and " <> $" in sql2


def test_no_func_keeps_original_structure(reg):
    # Backward-compat: a plain threshold screen grows NO cross-sectional layer.
    # (Note: TTM leaf CTEs have their own internal `ranked` ROW_NUMBER subquery,
    # so we assert on the unambiguous XS markers instead of the bare "ranked".)
    sql = _sql("pe_ratio < 15 AND roe > 18", reg)
    assert "_xs_" not in sql
    assert "NTILE(10) OVER" not in sql and "PERCENT_RANK" not in sql


def test_xs_and_base_filter_combine(reg):
    # Top-decile ROE among cheap stocks — both the window col and a leaf appear.
    sql = _sql("decile(roe) == 10 AND pe_ratio < 30", reg)
    assert "ranked._xs_0" in sql
    assert "ranked." in sql  # leaf vals referenced through the ranked CTE too
