"""Test fixtures.

Unit tests (parser, compiler) need nothing here.

Integration tests (PIT, survivorship) need Postgres. We use the maintenance
DSN at ``PIVOT_PG_DSN`` to create a scratch DB, apply the pivot-mc-scraper
migrations + the local seed, hand a connection to the test, then drop the DB.

If Postgres isn't reachable, ``pg_dsn`` skips the test with a useful message.
"""
from __future__ import annotations

import os
import secrets
from pathlib import Path
from urllib.parse import urlparse, urlunparse

import asyncpg
import pytest
import pytest_asyncio


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PIVOT_PG_DSN = "postgresql://pivot_user:pivot_password@localhost:5432/postgres"


def _scraper_sql_dir() -> Path:
    candidates = [
        Path(os.environ.get("MC_SCRAPER_PATH", "")) / "sql",
        PROJECT_ROOT.parent / "pivot-mc-scraper" / "sql",
    ]
    for c in candidates:
        if c and c.is_dir() and any(c.glob("*.sql")):
            return c
    raise RuntimeError(
        "Could not find pivot-mc-scraper/sql. "
        "Set MC_SCRAPER_PATH or place the repo as a sibling."
    )


def _maint_dsn() -> str:
    return os.environ.get("PIVOT_PG_DSN", DEFAULT_PIVOT_PG_DSN)


def _swap_db(dsn: str, db_name: str) -> str:
    p = urlparse(dsn)
    return urlunparse(p._replace(path=f"/{db_name}"))


async def _maint_reachable() -> bool:
    try:
        conn = await asyncpg.connect(dsn=_maint_dsn(), timeout=2)
        await conn.close()
        return True
    except Exception:
        return False


@pytest_asyncio.fixture
async def pg_dsn():
    """Yields a DSN to a freshly-seeded scratch DB. Drops it on teardown."""
    if not await _maint_reachable():
        pytest.skip(
            f"PIVOT_PG_DSN ({_maint_dsn()}) not reachable; "
            "skipping integration tests. Run `cd pivot && docker compose up -d postgres` first."
        )

    db_name = f"backtester_test_{secrets.token_hex(4)}"
    maint_dsn = _maint_dsn()
    test_dsn = _swap_db(maint_dsn, db_name)

    # Create
    conn = await asyncpg.connect(dsn=maint_dsn)
    try:
        await conn.execute(f'CREATE DATABASE "{db_name}"')
    finally:
        await conn.close()

    # Apply scraper migrations + seed
    sql_dir = _scraper_sql_dir()
    seed_path = Path(__file__).parent / "fixtures" / "seed_test_db.sql"

    conn = await asyncpg.connect(dsn=test_dsn)
    try:
        for path in sorted(sql_dir.glob("*.sql")):
            await conn.execute(path.read_text(encoding="utf-8"))
        await conn.execute(seed_path.read_text(encoding="utf-8"))
    finally:
        await conn.close()

    try:
        yield test_dsn
    finally:
        # Drop. Disconnect any open conns first.
        conn = await asyncpg.connect(dsn=maint_dsn)
        try:
            await conn.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = $1 AND pid <> pg_backend_pid()",
                db_name,
            )
            await conn.execute(f'DROP DATABASE "{db_name}"')
        finally:
            await conn.close()


@pytest_asyncio.fixture
async def pg_conn(pg_dsn):
    conn = await asyncpg.connect(dsn=pg_dsn)
    try:
        yield conn
    finally:
        await conn.close()
