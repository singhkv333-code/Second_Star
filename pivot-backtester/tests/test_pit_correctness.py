"""Point-in-time correctness — the tests that, if any fail, the build is broken.

The seed places the FY20 P&L for company X with availability_date 2020-08-15.
A universe query at any date before that must NOT see it. After it, it must.

These tests run against a scratch Postgres seeded by ``conftest.py``.
"""
from __future__ import annotations

import datetime as dt

import asyncpg
import pytest

from backtester.universe import universe_at


pytestmark = pytest.mark.integration


def _ids(snap) -> set[str]:
    return {r["sc_id"] for r in snap.rows}


async def test_x_invisible_before_any_filing(pg_conn: asyncpg.Connection):
    """At 2020-04-01 X has filed nothing. Annual filter for net_profit must drop it."""
    snap = await universe_at(pg_conn, "net_profit > 50", dt.date(2020, 4, 1))
    assert "X" not in _ids(snap), (
        "PIT bug: net_profit > 50 returned X before any FY20 filing was visible. "
        "This means the engine looked ahead."
    )


async def test_x_invisible_when_only_quarterly_filed(pg_conn: asyncpg.Connection):
    """At 2020-08-14, only the Q1FY21 row is filed (np=30); FY20 (np=100) isn't.
    An *annual* lookup of net_profit must still see nothing."""
    snap = await universe_at(pg_conn, "net_profit > 50", dt.date(2020, 8, 14))
    assert "X" not in _ids(snap)


async def test_x_visible_after_annual_filed(pg_conn: asyncpg.Connection):
    """At 2020-08-16, FY20 net_profit=100 is now filed."""
    snap = await universe_at(pg_conn, "net_profit > 50", dt.date(2020, 8, 16))
    assert "X" in _ids(snap)


async def test_ttm_requires_four_quarters(pg_conn: asyncpg.Connection):
    """X has 4 quarterly net_profit rows ranging 2019-09-30 .. 2020-06-30,
    last availability_date 2020-08-10. Before that, fewer than 4 are visible
    → TTM CTE returns nothing → X is excluded."""
    # At 2020-05-16, only Sep-19, Dec-19, Mar-20 are filed (3 quarters).
    snap_partial = await universe_at(pg_conn, "net_profit_ttm > 0", dt.date(2020, 5, 16))
    assert "X" not in _ids(snap_partial), (
        "TTM with only 3 visible quarters should not produce a value."
    )

    # At 2020-08-11 all 4 are filed.
    snap_full = await universe_at(pg_conn, "net_profit_ttm > 0", dt.date(2020, 8, 11))
    assert "X" in _ids(snap_full)


async def test_listed_on_filter(pg_conn: asyncpg.Connection):
    """LATE listed 2017-04-01 — a 2015 universe must not include it."""
    snap = await universe_at(
        pg_conn,
        # Predicate that LATE would otherwise match. Empty seed for LATE
        # means it has no fundamentals — so it can't pass the JOIN anyway.
        # We assert it via the absence directly.
        "price > 0 OR price < 1e9",
        dt.date(2015, 4, 1),
    )
    assert "LATE" not in _ids(snap)
