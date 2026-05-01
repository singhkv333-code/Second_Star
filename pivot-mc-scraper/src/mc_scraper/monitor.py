"""Live progress dashboard."""
from __future__ import annotations

import asyncio

import asyncpg
from rich.console import Console
from rich.live import Live
from rich.table import Table

from .db import open_pool


async def _snapshot(pool: asyncpg.Pool) -> dict:
    async with pool.acquire() as conn:
        status_rows = await conn.fetch(
            "SELECT status::text AS status, COUNT(*)::int AS n "
            "FROM mc.scrape_jobs GROUP BY status ORDER BY status"
        )
        recent = await conn.fetch(
            """
            SELECT j.sc_id, j.statement::text, j.basis::text, j.locked_by,
                   EXTRACT(EPOCH FROM (now() - j.locked_at))::int AS held_s
              FROM mc.scrape_jobs j
             WHERE j.status = 'in_progress'
             ORDER BY j.locked_at DESC
             LIMIT 8
            """
        )
        rows = await conn.fetchval("SELECT count(*) FROM mc.statement_lines")
        companies = await conn.fetchval("SELECT count(*) FROM mc.companies")
    return {
        "status": dict((r["status"], r["n"]) for r in status_rows),
        "recent": [dict(r) for r in recent],
        "rows": int(rows or 0),
        "companies": int(companies or 0),
    }


def _render(snap: dict) -> Table:
    t = Table(title="mc-scraper progress", expand=True)
    t.add_column("section")
    t.add_column("value")
    t.add_row("companies", str(snap["companies"]))
    t.add_row("statement_lines", f"{snap['rows']:,}")
    t.add_row("", "")
    for k in ("pending", "in_progress", "done", "failed", "no_data"):
        t.add_row(f"jobs.{k}", str(snap["status"].get(k, 0)))
    if snap["recent"]:
        t.add_row("", "")
        t.add_row("in-flight", "(sc_id / stmt / basis / worker / held)")
        for r in snap["recent"]:
            t.add_row(
                "",
                f"{r['sc_id']} / {r['statement']} / {r['basis']} / {r['locked_by']} / {r['held_s']}s",
            )
    return t


async def watch(refresh_seconds: float = 2.0) -> None:
    pool = await open_pool()
    try:
        with Live(refresh_per_second=2) as live:
            while True:
                snap = await _snapshot(pool)
                live.update(_render(snap))
                await asyncio.sleep(refresh_seconds)
    finally:
        await pool.close()


async def status_once() -> None:
    pool = await open_pool()
    try:
        snap = await _snapshot(pool)
        Console().print(_render(snap))
    finally:
        await pool.close()
