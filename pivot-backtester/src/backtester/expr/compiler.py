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
    - TTM leaf -> sum of the last 4 *quarterly* rows (``period_kind='quarterly'``
      on the field's own statement) where ``availability_date <= T``, falling
      back to the latest *annual* value (which already spans twelve months) for
      companies without 4 full quarters. The live mc data is annual-only, so the
      annual fallback is what actually fires today.
    - Annual leaf -> latest fundamental value where ``availability_date <= T``,
      excluding quarterly rows if any are ever scraped.
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
from .ast import BinOp, BoolOp, Compare, Expr, Func, Ident, Neg, Not, Number
from .grammar import parse_expression

# Cross-sectional transforms (window functions over the universe at date T).
# Rankings order/score the cross-section (emitted in the `ranked` CTE).
# Transforms produce a per-row value (emitted in the inner `ranked_t` CTE so a
# ranking can be computed over them — window funcs can't nest in one SELECT).
_XS_RANKINGS = frozenset({"rank", "decile", "quantile", "zscore", "percentrank"})
_XS_TRANSFORMS = frozenset({"winsorize", "neutralize"})
_XS_FUNCS = _XS_RANKINGS | _XS_TRANSFORMS

# The expression language uses ==/!= (Python-ish); SQL needs =/<>.
_SQL_COMPARE_OP = {"==": "=", "!=": "<>"}


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
    # `basis` is interpolated raw into the CTE SQL string literals below, so it
    # must be a known-safe value — reject anything else to prevent SQL injection
    # (defense-in-depth; callers should also validate at their API boundary).
    if basis not in ("consolidated", "standalone"):
        raise ValueError(f"invalid basis {basis!r}; expected consolidated|standalone")
    expanded = _expand(ast, registry)
    leaves = _collect_leaves(expanded, registry)

    # Number → param-index assignment in source order.
    numbers: list[float] = []
    def _next_param(value: float) -> int:
        numbers.append(value)
        # $1 reserved for backtest_date.
        return len(numbers) + 1

    # Cross-sectional pre-pass: transform columns (inner ranked_t CTE) + ranking
    # columns (ranked CTE). Their number params are bound before the predicate's.
    transform_cols, ranking_cols, func_alias = _emit_xs_columns(expanded, _next_param)
    has_xs = bool(transform_cols or ranking_cols)
    leaf_ref = "ranked" if has_xs else "cte"
    predicate_sql = _emit(expanded, _next_param, func_alias, leaf_ref=leaf_ref)

    cte_sql = _emit_ctes(leaves, basis=basis)
    universe_sql = _emit_universe(leaves, predicate_sql, transform_cols, ranking_cols)

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
    if isinstance(node, Func):
        return Func(node.name, tuple(_expand(a, registry) for a in node.args))
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
        elif isinstance(n, Func):
            for a in n.args:
                walk(a)

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
        elif isinstance(n, Func):
            for a in n.args:
                walk(a)

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
    line_item_pref = _line_item_pref_sql(spec.line_items)

    if spec.ttm:
        # Trailing-twelve-months. Two sources, in preference order:
        #   q — sum of the last 4 *quarterly* rows (the textbook TTM), kept only
        #       for companies that have all 4 (HAVING COUNT(*) = 4).
        #   a — the latest *annual* value, which already spans twelve months and
        #       is the correct TTM proxy when quarterly data is absent.
        # We COALESCE q over a, so quarterly wins when present and annual is the
        # fallback. The live mc schema is annual-only (no `quarterly_results`
        # statement; period_kind = 'annual'), so today every company resolves via
        # `a`; the quarterly branch lights up automatically if quarters are ever
        # scraped. Quarterly/annual are distinguished by period_kind on the field's
        # own statement — NOT by a separate 'quarterly_results' statement name.
        return f"""{alias} AS (
  WITH q AS (
    WITH per_period AS (
      SELECT DISTINCT ON (sc_id, period_end)
             sc_id, period_end, value_numeric
      FROM mc.statement_lines
      WHERE statement = '{spec.statement}'
        AND period_kind = 'quarterly'
        AND basis = '{basis}'
        AND line_item IN ({line_items_sql})
        AND availability_date IS NOT NULL
        AND availability_date <= $1
        AND value_numeric IS NOT NULL
      ORDER BY sc_id, period_end, availability_date DESC, {line_item_pref}, line_order
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
  ),
  a AS (
    SELECT DISTINCT ON (sc_id) sc_id, value_numeric::numeric AS val
    FROM mc.statement_lines
    WHERE statement = '{spec.statement}'
      AND period_kind IS DISTINCT FROM 'quarterly'
      AND basis = '{basis}'
      AND line_item IN ({line_items_sql})
      AND availability_date IS NOT NULL
      AND availability_date <= $1
      AND value_numeric IS NOT NULL
    ORDER BY sc_id, period_end DESC, availability_date DESC, {line_item_pref}, line_order
  )
  SELECT sc_id, COALESCE(q.val, a.val) AS val
  FROM a FULL OUTER JOIN q USING (sc_id)
)"""

    # Annual point-in-time fundamental. `period_kind IS DISTINCT FROM 'quarterly'`
    # keeps annual (and any unlabelled) rows while excluding quarterly rows should
    # they ever be scraped — a no-op against today's annual-only data.
    return f"""{alias} AS (
  SELECT DISTINCT ON (sc_id) sc_id, value_numeric::numeric AS val
  FROM mc.statement_lines
  WHERE statement = '{spec.statement}'
    AND period_kind IS DISTINCT FROM 'quarterly'
    AND basis = '{basis}'
    AND line_item IN ({line_items_sql})
    AND availability_date IS NOT NULL
    AND availability_date <= $1
    AND value_numeric IS NOT NULL
  ORDER BY sc_id, period_end DESC, availability_date DESC, {line_item_pref}, line_order
)"""


def _sql_string_array(items: Iterable[str]) -> str:
    """Render a Python list of strings as a SQL string list literal.

    Safe because the strings come from our YAML, not user input — but we still
    escape single quotes defensively.
    """
    escaped = [s.replace("'", "''") for s in items]
    return ", ".join(f"'{s}'" for s in escaped)


def _line_item_pref_sql(items: list[str]) -> str:
    """A CASE expression that turns the YAML ``line_items`` list into an
    authoritative *preference order*.

    When a company reports several synonyms for the same field in one period
    (e.g. both "Revenue From Operations [Net]" and "Total Operating Revenues"),
    the ``DISTINCT ON`` tiebreak would otherwise fall to ``line_order`` — i.e.
    whichever happens to print first in the statement. By ranking on the YAML
    position first, the *first listed* synonym wins, so the lists in
    ``base_fields.yaml`` mean what they say. Strings come from our YAML, not
    user input; we still escape quotes defensively.
    """
    whens = " ".join(
        f"WHEN '{s.replace(chr(39), chr(39) * 2)}' THEN {i}" for i, s in enumerate(items)
    )
    return f"CASE line_item {whens} ELSE {len(items)} END"


# ---- Universe SELECT ----------------------------------------------------


def _emit_universe(
    leaves: list[BaseFieldSpec | PriceFieldSpec],
    predicate_sql: str,
    transform_columns: list[tuple[str, str]] | None = None,
    ranking_columns: list[tuple[str, str]] | None = None,
) -> str:
    transform_columns = transform_columns or []
    ranking_columns = ranking_columns or []
    if transform_columns or ranking_columns:
        return _emit_universe_ranked(
            leaves, predicate_sql, transform_columns, ranking_columns
        )
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


def _emit_universe_ranked(
    leaves: list[BaseFieldSpec | PriceFieldSpec],
    predicate_sql: str,
    transform_columns: list[tuple[str, str]],
    ranking_columns: list[tuple[str, str]],
) -> str:
    """Cross-sectional path. Window functions can't sit in a WHERE (and can't
    nest), so they live in CTE layers and the predicate filters the result:

      * Rankings only (rank/decile/zscore/…) — one ``ranked`` CTE computes the
        leaf vals + the ranking window columns over the survivorship-filtered
        universe at date T.
      * Transforms present (winsorize/neutralize) — an inner ``ranked_t`` CTE
        computes leaf vals + ``industry_slug`` + the transform columns (``_xt_N``);
        the outer ``ranked`` CTE then computes the rankings (``_xs_N``) over those
        transform columns / leaf vals. This is what lets ``decile(neutralize(roe))``
        compose without nesting window functions.

    ``universe`` filters the predicate, which references ``_xs_N`` / ``_xt_N`` cols
    and leaf vals (all surfaced on ``ranked``)."""
    joins = "\n".join(
        f"  JOIN {_cte_alias(s)} ON {_cte_alias(s)}.sc_id = c.sc_id"
        for s in leaves
    )
    leaf_cols = ",\n".join(f"    {_cte_alias(s)}.val AS {s.name}_val" for s in leaves)
    out_cols = "".join(f",\n    ranked.{s.name}_val" for s in leaves)

    if not transform_columns:
        # One level: rankings computed directly over the JOINed leaf CTEs.
        xs_cols = ",\n".join(f"    {sql} AS {alias}" for alias, sql in ranking_columns)
        inner_cols = ",\n".join(c for c in (leaf_cols, xs_cols) if c)
        return f"""ranked AS (
  SELECT
    c.sc_id,
    c.company_name,
{inner_cols}
  FROM mc.companies c
{joins}
  WHERE
    (c.delisted_on IS NULL OR c.delisted_on > $1)
    AND (c.listed_on IS NULL OR c.listed_on <= $1)
),
universe AS (
  SELECT
    ranked.sc_id,
    ranked.company_name{out_cols}
  FROM ranked
  WHERE ({predicate_sql})
)
SELECT * FROM universe"""

    # Two levels: ranked_t (transforms) -> ranked (rankings over them).
    t_cols = ",\n".join(f"    {sql} AS {alias}" for alias, sql in transform_columns)
    t_inner = ",\n".join(c for c in (leaf_cols, t_cols) if c)
    leaf_pass = ",\n".join(f"    ranked_t.{s.name}_val AS {s.name}_val" for s in leaves)
    t_pass = ",\n".join(f"    ranked_t.{alias} AS {alias}" for alias, _ in transform_columns)
    r_cols = ",\n".join(f"    {sql} AS {alias}" for alias, sql in ranking_columns)
    ranked_inner = ",\n".join(c for c in (leaf_pass, t_pass, r_cols) if c)
    return f"""ranked_t AS (
  SELECT
    c.sc_id,
    c.company_name,
    c.industry_slug,
{t_inner}
  FROM mc.companies c
{joins}
  WHERE
    (c.delisted_on IS NULL OR c.delisted_on > $1)
    AND (c.listed_on IS NULL OR c.listed_on <= $1)
),
ranked AS (
  SELECT
    ranked_t.sc_id,
    ranked_t.company_name,
{ranked_inner}
  FROM ranked_t
),
universe AS (
  SELECT
    ranked.sc_id,
    ranked.company_name{out_cols}
  FROM ranked
  WHERE ({predicate_sql})
)
SELECT * FROM universe"""


# ---- Predicate emission --------------------------------------------------


def _emit(
    node: Expr,
    next_param,
    func_alias: dict | None = None,
    *,
    leaf_ref: str = "cte",
) -> str:
    """Walk the expanded AST emitting SQL.

    ``next_param`` binds numeric literals to positional params. ``func_alias``
    maps id(Func) → its ``_xs_N`` / ``_xt_N`` column alias. ``leaf_ref`` selects how
    a leaf Ident renders: ``"cte"`` → ``f_x.val`` (inside a CTE that JOINs the leaf
    CTEs); any other value is treated as a CTE alias holding ``<name>_val`` columns
    (e.g. ``"ranked"`` → ``ranked.x_val``, ``"ranked_t"`` → ``ranked_t.x_val``)."""
    func_alias = func_alias or {}
    if isinstance(node, BoolOp):
        op = " AND " if node.op == "AND" else " OR "
        return "(" + op.join(
            _emit(o, next_param, func_alias, leaf_ref=leaf_ref) for o in node.operands
        ) + ")"
    if isinstance(node, Not):
        return f"(NOT {_emit(node.operand, next_param, func_alias, leaf_ref=leaf_ref)})"
    if isinstance(node, Compare):
        sql_op = _SQL_COMPARE_OP.get(node.op, node.op)
        return (
            f"({_emit(node.left, next_param, func_alias, leaf_ref=leaf_ref)} "
            f"{sql_op} {_emit(node.right, next_param, func_alias, leaf_ref=leaf_ref)})"
        )
    if isinstance(node, BinOp):
        left = _emit(node.left, next_param, func_alias, leaf_ref=leaf_ref)
        right = _emit(node.right, next_param, func_alias, leaf_ref=leaf_ref)
        if node.op == "/":
            return f"({left} / NULLIF({right}, 0))"
        return f"({left} {node.op} {right})"
    if isinstance(node, Neg):
        return f"(-{_emit(node.operand, next_param, func_alias, leaf_ref=leaf_ref)})"
    if isinstance(node, Number):
        idx = next_param(node.value)
        return f"${idx}"
    if isinstance(node, Func):
        alias = func_alias.get(id(node))
        assert alias is not None, "Func node was not pre-collected for the ranked CTE"
        return f"ranked.{alias}"
    if isinstance(node, Ident):
        # After expansion, every Ident is a leaf.
        if leaf_ref == "cte":
            return f"{_cte_alias(_dummy_spec_for_emit(node.name))}.val"
        return f"{leaf_ref}.{node.name}_val"
    raise AssertionError(f"unhandled node {type(node)}")


def _emit_func_sql(func: Func, next_param, leaf_emit) -> str:
    """SQL for one cross-sectional window function. ``leaf_emit`` emits its value
    arg over the JOINed leaf CTEs (``f_x.val``), binding numbers via next_param."""
    name = func.name
    if name == "quantile":
        arg_sql = leaf_emit(func.args[0])
        n = int(func.args[1].value)  # validator guarantees an int literal 2..100
        return f"NTILE({n}) OVER (ORDER BY {arg_sql})"
    arg_sql = leaf_emit(func.args[0])
    if name == "rank":
        return f"RANK() OVER (ORDER BY {arg_sql})"
    if name == "decile":
        return f"NTILE(10) OVER (ORDER BY {arg_sql})"
    if name == "percentrank":
        return f"PERCENT_RANK() OVER (ORDER BY {arg_sql})"
    if name == "zscore":
        return (
            f"(({arg_sql}) - AVG({arg_sql}) OVER ()) "
            f"/ NULLIF(STDDEV_SAMP({arg_sql}) OVER (), 0)"
        )
    if name == "winsorize":
        # Sigma-clip: clamp each value to mean ± k·stdev across the cross-section.
        # (Percentile winsorization can't be a Postgres window function; this is
        # the windowable, zscore-compatible robustness primitive — tame outliers
        # before ranking/zscoring.) k is a validated positive number literal.
        k = float(func.args[1].value)
        mean = f"AVG({arg_sql}) OVER ()"
        sd = f"STDDEV_SAMP({arg_sql}) OVER ()"
        return (
            f"GREATEST(LEAST({arg_sql}, {mean} + {k} * {sd}), {mean} - {k} * {sd})"
        )
    if name == "neutralize":
        # Industry-neutral factor: subtract the industry mean (demean within
        # mc.companies.industry_slug, which is fully populated; `sector` is not).
        # c.industry_slug is in scope inside the `ranked` CTE's SELECT.
        return f"(({arg_sql}) - AVG({arg_sql}) OVER (PARTITION BY c.industry_slug))"
    raise AssertionError(f"unknown cross-sectional func {name!r}")


def _emit_xs_columns(node: Expr, next_param):
    """Pre-pass over the expanded AST. Returns ``(transform_columns,
    ranking_columns, alias_map)``:

      * ``transform_columns`` = ``[(_xt_N, sql), ...]`` for the inner ``ranked_t``
        CTE — winsorize/neutralize, computed over the JOINed leaf CTEs.
      * ``ranking_columns`` = ``[(_xs_N, sql), ...]`` for the ``ranked`` CTE —
        rank/decile/…; their value arg is either a leaf (→ ``ranked_t.x_val`` when
        transforms exist, else ``f_x.val``) or a transform (→ ``ranked_t._xt_M``).
      * ``alias_map`` = id(Func) → its column alias (both _xt and _xs).

    Func-arg numbers are bound here (before the predicate's), consistent with the
    CTE-before-universe textual order."""
    transforms: list[tuple[str, Func]] = []   # (alias, node) in source order
    rankings: list[tuple[str, Func]] = []
    alias_map: dict[int, str] = {}
    t_count = [0]
    r_count = [0]

    def collect(n: Expr) -> None:
        if isinstance(n, BoolOp):
            for o in n.operands:
                collect(o)
        elif isinstance(n, Not):
            collect(n.operand)
        elif isinstance(n, (Compare, BinOp)):
            collect(n.left); collect(n.right)
        elif isinstance(n, Neg):
            collect(n.operand)
        elif isinstance(n, Func):
            if n.name in _XS_TRANSFORMS:
                alias = f"_xt_{t_count[0]}"; t_count[0] += 1
                alias_map[id(n)] = alias
                transforms.append((alias, n))
            else:  # ranking
                alias = f"_xs_{r_count[0]}"; r_count[0] += 1
                alias_map[id(n)] = alias
                rankings.append((alias, n))
                collect(n.args[0])  # collect a wrapped transform, if any

    collect(node)
    has_transforms = bool(transforms)

    # Transforms read the JOINed leaf CTEs (f_x.val); industry_slug is in scope.
    def t_leaf_emit(x: Expr) -> str:
        return _emit(x, next_param, alias_map, leaf_ref="cte")

    transform_columns = [
        (alias, _emit_func_sql(f, next_param, t_leaf_emit)) for alias, f in transforms
    ]

    # Rankings read ranked_t when transforms exist (leaf -> ranked_t.x_val,
    # wrapped transform -> ranked_t._xt_M); otherwise the leaf CTEs directly.
    ranking_leaf_ref = "ranked_t" if has_transforms else "cte"

    def r_leaf_emit(x: Expr) -> str:
        if isinstance(x, Func):  # a wrapped transform — its column in ranked_t
            return f"ranked_t.{alias_map[id(x)]}"
        return _emit(x, next_param, alias_map, leaf_ref=ranking_leaf_ref)

    ranking_columns = [
        (alias, _emit_func_sql(f, next_param, r_leaf_emit)) for alias, f in rankings
    ]

    return transform_columns, ranking_columns, alias_map


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
    if isinstance(node, Func):
        return f"{node.name}(" + ", ".join(_pretty(a) for a in node.args) + ")"
    if isinstance(node, Number):
        return repr(node.value)
    if isinstance(node, Ident):
        return node.name
    raise AssertionError
