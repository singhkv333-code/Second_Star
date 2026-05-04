"""Run a compiled universe query at a specific point in time.

This is the layer the engine calls every rebalance. It returns a snapshot
that includes both the qualifying companies and the per-leaf values used —
the universe view doubles as the audit trail required by the spec.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import asyncpg

from ..expr import compile_to_sql, parse_expression, validate
from ..fields import Registry, load_default_registry


@dataclass
class UniverseSnapshot:
    as_of: date
    expression: str
    rows: list[dict]                  # each row has sc_id, company_name, *_val
    leaf_fields: list[str]            # the leaf identifiers (e.g. price, eps_basic_ttm)
    referenced_fields: list[str]      # the user-facing identifiers
    sql: str
    params: list


async def universe_at(
    conn: asyncpg.Connection,
    expression: str,
    as_of: date,
    *,
    registry: Registry | None = None,
    basis: str = "consolidated",
) -> UniverseSnapshot:
    registry = registry or load_default_registry()

    ast = parse_expression(expression)
    validate(ast, registry)
    compiled = compile_to_sql(ast, registry, basis=basis)

    sql = compiled.sql
    params = [as_of, *compiled.params]
    records = await conn.fetch(sql, *params)

    rows = [dict(r) for r in records]
    return UniverseSnapshot(
        as_of=as_of,
        expression=expression,
        rows=rows,
        leaf_fields=[s.name for s in compiled.leaf_fields],
        referenced_fields=compiled.referenced_fields,
        sql=sql,
        params=params,
    )
