"""Compile an AST + registry into a parameterised SQL universe query.

The output query takes:
    $1 : backtest_date (DATE)
    $2..N : numeric literals from the expression, in source order

and returns rows ``(sc_id, company_name, <leaf>_val ...)`` — one row per
company that satisfies the predicate at the given date.

Design notes
------------
* The compiler first **expands** computed fields into base/price leaves by
  recursively substituting their YAML expressions. The expansion happens at
  AST level, not text level, so injection is impossible.
* Each leaf becomes one CTE producing ``(sc_id, val)``:
    - ``price`` -> latest close <= T from ``mc.daily_prices``.
    - TTM leaf -> sum of last 4 quarterly_results values where
      ``availability_date <= T``. Companies without 4 full quarters are excluded
      via ``HAVING COUNT(*) = 4``.
    - Annual leaf -> latest fundamental value where
      ``availability_date <= T``.
* Division gets ``NULLIF`` on the denominator. This is the difference between
  a clean filter and a backtest crash — please never remove it.
* The survivorship guard
  ``(delisted_on IS NULL OR delisted_on > $1) AND (listed_on IS NULL OR listed_on <= $1)``
  is added unconditionally. A test (``test_survivorship.py``) verifies it.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

from ..fields import (
    BaseFieldSpec,
    ComputedFieldSpec,
    PriceFieldSpec,
    Registry,
)
from .ast import BinOp, BoolOp, Compare, Expr, Ident, Neg, Not, Number
from .grammar import parse_expression


# ---- Public API ---------------------------------------------------------


@dataclass
class CompiledQuery:
    sql: str
    params: list[float]                 # numeric literals, in $2..$N order
    leaf_fields: list[BaseFieldSpec | PriceFieldSpec]
    referenced_fields: list[str]        # the original user-facing names
    expansion_text: str                 # debug: fully-expanded predicate

    def with_date(self, date_param) -> tuple[str, list]:
        """Helper: prepend backtest_date as $1 and return ``(sql, params)``."""
        return self.sql, [date_param, *self.params]


def compile_to_sql(
    ast: Expr,
    registry: Registry,
    *,
    basis: str = "consolidated",
) -> CompiledQuery:
    expanded = _expand(ast, registry)
    leaves = _collect_leaves(expanded, registry)

    # Number → param-index assignment in source order.
    numbers: list[float] = []
    def _next_param(value: float) -> int:
        numbers.append(value)
        # $1 reserved for backtest_date.
        return len(numbers) + 1

    predicate_sql = _emit(expanded, _next_param)

    cte_sql = _emit_ctes(leaves, basis=basis)
    universe_sql = _emit_universe(leaves, predicate_sql)

    sql = cte_sql + ",\n" + universe_sql

    referenced = _user_facing_idents(ast)
    return CompiledQuery(
        sql=sql,
        params=numbers,
        leaf_fields=leaves,
        referenced_fields=referenced,
        expansion_text=_pretty(expanded),
    )


# ---- Expansion ----------------------------------------------------------


def _expand(node: Expr, registry: Registry) -> Expr:
    """Substitute every computed identifier with its parsed expression."""
    if isinstance(node, Ident):
        spec = registry.lookup(node.name)
        if isinstance(spec, ComputedFieldSpec):
            inner = parse_expression(spec.expr_text)
            return _expand(inner, registry)
        return node
    if isinstance(node, BoolOp):
        return BoolOp(node.op, tuple(_expand(o, registry) for o in node.operands))
    if isinstance(node, Not):
        return Not(_expand(node.operand, registry))
    if isinstance(node, Compare):
        return Compare(node.op, _expand(node.left, registry), _expand(node.right, registry))
    if isinstance(node, BinOp):
        return BinOp(node.op, _expand(node.left, registry), _expand(node.right, registry))
    if isinstance(node, Neg):
        return Neg(_expand(node.operand, registry))
    if isinstance(node, Number):
        return node
    raise AssertionError(f"unhandled node {type(node)}")


def _collect_leaves(
    expanded: Expr,
    registry: Registry,
) -> list[BaseFieldSpec | PriceFieldSpec]:
    """Return every distinct leaf field in source order (idents only)."""
    seen: dict[str, BaseFieldSpec | PriceFieldSpec] = {}

    def walk(n: Expr) -> None:
        if isinstance(n, Ident):
            spec = registry.lookup(n.name)
            assert isinstance(spec, (BaseFieldSpec, PriceFieldSpec)), (
                "computed field leaked past expansion"
            )
            if n.name not in seen:
                seen[n.name] = spec
        elif isinstance(n, BoolOp):
            for o in n.operands:
                walk(o)
        elif isinstance(n, Not):
            walk(n.operand)
        elif isinstance(n, Compare) or isinstance(n, BinOp):
            walk(n.left); walk(n.right)
        elif isinstance(n, Neg):
            walk(n.operand)

    walk(expanded)
    return list(seen.values())


def _user_facing_idents(node: Expr) -> list[str]:
    seen: dict[str, None] = {}

    def walk(n: Expr) -> None:
        if isinstance(n, Ident):
            seen.setdefault(n.name, None)
        elif isinstance(n, BoolOp):
            for o in n.operands: walk(o)
        elif isinstance(n, Not):
            walk(n.operand)
        elif isinstance(n, Compare) or isinstance(n, BinOp):
            walk(n.left); walk(n.right)
        elif isinstance(n, Neg):
            walk(n.operand)

    walk(node)
    return list(seen.keys())


# ---- CTE emission --------------------------------------------------------


def _cte_alias(spec: BaseFieldSpec | PriceFieldSpec) -> str:
    return f"f_{spec.name}"


def _emit_ctes(
    leaves: list[BaseFieldSpec | PriceFieldSpec],
    *,
    basis: str,
) -> str:
    parts: list[str] = []
    for spec in leaves:
        parts.append(_emit_one_cte(spec, basis=basis))
    return "WITH " + ",\n".join(parts)


def _emit_one_cte(spec: BaseFieldSpec | PriceFieldSpec, *, basis: str) -> str:
    alias = _cte_alias(spec)

    if isinstance(spec, PriceFieldSpec):
        return f"""{alias} AS (
  SELECT DISTINCT ON (sc_id) sc_id, close::numeric AS val
  FROM mc.daily_prices
  WHERE trade_date <= $1
  ORDER BY sc_id, trade_date DESC
)"""

    # BaseFieldSpec
    line_items_sql = _sql_string_array(spec.line_items)

    if spec.ttm:
        return f"""{alias} AS (
  WITH per_period AS (
    SELECT DISTINCT ON (sc_id, period_end)
           sc_id, period_end, value_numeric
    FROM mc.statement_lines
    WHERE statement = 'quarterly_results'
      AND basis = '{basis}'
      AND line_item IN ({line_items_sql})
      AND availability_date IS NOT NULL
      AND availability_date <= $1
      AND value_numeric IS NOT NULL
    ORDER BY sc_id, period_end, availability_date DESC, line_order
  ),
  ranked AS (
    SELECT sc_id, value_numeric,
           ROW_NUMBER() OVER (
             PARTITION BY sc_id ORDER BY period_end DESC
           ) AS rn
    FROM per_period
  )
  SELECT sc_id, SUM(value_numeric)::numeric AS val
  FROM ranked
  WHERE rn <= 4
  GROUP BY sc_id
  HAVING COUNT(*) = 4
)"""

    # Annual point-in-time fundamental.
    return f"""{alias} AS (
  SELECT DISTINCT ON (sc_id) sc_id, value_numeric::numeric AS val
  FROM mc.statement_lines
  WHERE statement = '{spec.statement}'
    AND basis = '{basis}'
    AND line_item IN ({line_items_sql})
    AND availability_date IS NOT NULL
    AND availability_date <= $1
    AND value_numeric IS NOT NULL
  ORDER BY sc_id, period_end DESC, availability_date DESC, line_order
)"""


def _sql_string_array(items: Iterable[str]) -> str:
    """Render a Python list of strings as a SQL string list literal.

    Safe because the strings come from our YAML, not user input — but we still
    escape single quotes defensively.
    """
    escaped = [s.replace("'", "''") for s in items]
    return ", ".join(f"'{s}'" for s in escaped)


# ---- Universe SELECT ----------------------------------------------------


def _emit_universe(leaves: list[BaseFieldSpec | PriceFieldSpec], predicate_sql: str) -> str:
    joins = "\n".join(
        f"  JOIN {_cte_alias(s)} ON {_cte_alias(s)}.sc_id = c.sc_id"
        for s in leaves
    )
    select_cols = ",\n".join(
        f"  {_cte_alias(s)}.val AS {s.name}_val"
        for s in leaves
    ) or "  c.sc_id"

    if not leaves:
        # Degenerate case — predicate has no field references, e.g. ``1 < 2``.
        # We still want to honour the survivorship guard.
        return f"""universe AS (
  SELECT c.sc_id, c.company_name
  FROM mc.companies c
  WHERE
    (c.delisted_on IS NULL OR c.delisted_on > $1)
    AND (c.listed_on IS NULL OR c.listed_on <= $1)
    AND ({predicate_sql})
)
SELECT * FROM universe"""

    return f"""universe AS (
  SELECT
    c.sc_id,
    c.company_name,
{select_cols}
  FROM mc.companies c
{joins}
  WHERE
    (c.delisted_on IS NULL OR c.delisted_on > $1)
    AND (c.listed_on IS NULL OR c.listed_on <= $1)
    AND ({predicate_sql})
)
SELECT * FROM universe"""


# ---- Predicate emission --------------------------------------------------


def _emit(node: Expr, next_param) -> str:
    """Walk the expanded AST emitting SQL.

    ``next_param`` is a closure that consumes a Python float and returns the
    integer parameter index to substitute (used to bind numeric literals).
    """
    if isinstance(node, BoolOp):
        op = " AND " if node.op == "AND" else " OR "
        return "(" + op.join(_emit(o, next_param) for o in node.operands) + ")"
    if isinstance(node, Not):
        return f"(NOT {_emit(node.operand, next_param)})"
    if isinstance(node, Compare):
        return f"({_emit(node.left, next_param)} {node.op} {_emit(node.right, next_param)})"
    if isinstance(node, BinOp):
        left = _emit(node.left, next_param)
        right = _emit(node.right, next_param)
        if node.op == "/":
            return f"({left} / NULLIF({right}, 0))"
        return f"({left} {node.op} {right})"
    if isinstance(node, Neg):
        return f"(-{_emit(node.operand, next_param)})"
    if isinstance(node, Number):
        idx = next_param(node.value)
        return f"${idx}"
    if isinstance(node, Ident):
        # After expansion, every Ident is a leaf.
        return f"{_cte_alias(_dummy_spec_for_emit(node.name))}.val"
    raise AssertionError(f"unhandled node {type(node)}")


def _dummy_spec_for_emit(name: str):
    """Return a minimal object with a `.name` attribute, for alias rendering.

    The compiler has already collected real specs in ``leaves``; here we only
    need the name to derive the alias, so a trivial object suffices.
    """
    class _N:
        pass
    n = _N()
    n.name = name
    return n


# ---- Debug / pretty-print -----------------------------------------------


def _pretty(node: Expr) -> str:
    if isinstance(node, BoolOp):
        op = " AND " if node.op == "AND" else " OR "
        return "(" + op.join(_pretty(o) for o in node.operands) + ")"
    if isinstance(node, Not):
        return f"NOT {_pretty(node.operand)}"
    if isinstance(node, Compare):
        return f"({_pretty(node.left)} {node.op} {_pretty(node.right)})"
    if isinstance(node, BinOp):
        return f"({_pretty(node.left)} {node.op} {_pretty(node.right)})"
    if isinstance(node, Neg):
        return f"(-{_pretty(node.operand)})"
    if isinstance(node, Number):
        return repr(node.value)
    if isinstance(node, Ident):
        return node.name
    raise AssertionError
