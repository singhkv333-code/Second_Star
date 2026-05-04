"""Postgres pool, schema bootstrap, advisory-lock helpers."""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Optional

import asyncpg

from .config import PROJECT_ROOT, Settings, get_settings

SQL_DIR = PROJECT_ROOT / "sql"


async def ensure_database_exists(settings: Settings) -> None:
    """Connect to the maintenance DSN and CREATE DATABASE financials if absent."""
    conn = await asyncpg.connect(dsn=settings.pivot_pg_dsn)
    try:
        exists = await conn.fetchval(
            "SELECT 1 FROM pg_database WHERE datname = $1",
            settings.financials_db_name,
        )
        if not exists:
            # CREATE DATABASE cannot run inside a transaction block.
            await conn.execute(
                f'CREATE DATABASE "{settings.financials_db_name}"'
            )
    finally:
        await conn.close()


async def run_migrations(settings: Settings) -> None:
    """Apply every sql/*.sql file in lexicographic order against the financials DB."""
    conn = await asyncpg.connect(dsn=settings.financials_dsn())
    try:
        for path in sorted(SQL_DIR.glob("*.sql")):
            sql = path.read_text(encoding="utf-8")
            await conn.execute(sql)
    finally:
        await conn.close()


async def open_pool(settings: Optional[Settings] = None, **kwargs) -> asyncpg.Pool:
    settings = settings or get_settings()
    return await asyncpg.create_pool(
        dsn=settings.financials_dsn(),
        min_size=1,
        max_size=kwargs.pop("max_size", 8),
        command_timeout=60,
        **kwargs,
    )


async def init_database() -> None:
    """One-shot init used by `mc-scraper init`."""
    settings = get_settings()
    await ensure_database_exists(settings)
    # Race-safe migrations: tiny retry loop in case two CLIs init at once.
    for attempt in range(3):
        try:
            await run_migrations(settings)
            return
        except asyncpg.PostgresError:
            if attempt == 2:
                raise
            await asyncio.sleep(0.5)
