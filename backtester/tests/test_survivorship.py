"""Survivorship: a delisted company must appear in past universes, not future ones."""
from __future__ import annotations

import datetime as dt

import asyncpg
import pytest

from backtester.universe import universe_at


pytestmark = pytest.mark.integration


def _ids(snap) -> set[str]:
    return {r["sc_id"] for r in snap.rows}


async def test_delisted_company_present_in_pre_delisting_universe(pg_conn):
    """Y had pe < 10 in 2015 (price=100, eps_ttm=20). Even though Y is delisted today,
    it must show up in a 2015-06-01 universe."""
    snap = await universe_at(pg_conn, "pe_ratio < 10", dt.date(2015, 6, 1))
    ids = _ids(snap)
    assert "Y" in ids, (
        "Survivorship bug: a then-listed company was excluded from a past universe."
    )


async def test_delisted_company_absent_after_delisting(pg_conn):
    """Y delisted 2018-06-30. A 2019 universe must not include Y."""
    snap = await universe_at(pg_conn, "pe_ratio < 10", dt.date(2019, 4, 1))
    assert "Y" not in _ids(snap), (
        "Survivorship guard missing: a delisted company appeared in a "
        "post-delisting universe."
    )


async def test_active_control_still_visible(pg_conn):
    """Z is active. At 2019-04-01 its pe = 50/8 = 6.25 < 10."""
    snap = await universe_at(pg_conn, "pe_ratio < 10", dt.date(2019, 4, 1))
    assert "Z" in _ids(snap)


async def test_survivorship_guard_present_in_compiled_sql():
    """Belt-and-braces: even without running the query, the guard must compile in.

    A future refactor that drops the guard would make the integration tests
    skip-pass (e.g. on a CI box without Docker), which is the worst kind of
    silent regression."""
    from backtester.expr import compile_to_sql, parse_expression
    from backtester.fields import load_default_registry
    reg = load_default_registry()
    sql = compile_to_sql(parse_expression("pe_ratio < 10"), reg).sql
    assert "delisted_on IS NULL OR" in sql
    assert "listed_on IS NULL OR" in sql
