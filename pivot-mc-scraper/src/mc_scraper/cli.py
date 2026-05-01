"""Typer CLI entrypoint for mc-scraper."""
from __future__ import annotations

import asyncio
import json

import typer
from rich.console import Console

from .config import get_settings
from .db import init_database, open_pool
from .discover import discover_all
from .http import make_client
from .monitor import status_once, watch
from .sources.appfeeds import probe_all
from .tracker import run as tracker_run
from .worker import run_one_company, run_worker, unstick_stale


app = typer.Typer(no_args_is_help=True, add_completion=False)
console = Console()


@app.command()
def init() -> None:
    """Create the financials database and apply migrations."""
    asyncio.run(init_database())
    console.print(f"[green]ok[/green] database '{get_settings().financials_db_name}' ready")


@app.command()
def discover(concurrency: int = typer.Option(4, "--concurrency", "-c")) -> None:
    """Crawl A–Z listing pages, populate companies and seed scrape jobs."""
    async def _go():
        pool = await open_pool()
        try:
            return await discover_all(pool, concurrency=concurrency)
        finally:
            await pool.close()
    summary = asyncio.run(_go())
    console.print_json(data=summary)


@app.command()
def work(
    concurrency: int = typer.Option(16, "--concurrency"),
    batch_size: int = typer.Option(8, "--batch-size"),
    rate_limit: float = typer.Option(10.0, "--rate-limit"),
    force: bool = typer.Option(False, "--force"),
) -> None:
    """Run a worker. Run this command N times in N terminals for N parallel workers."""
    async def _go():
        pool = await open_pool()
        try:
            await unstick_stale(pool)
            await run_worker(
                pool,
                concurrency=concurrency,
                batch_size=batch_size,
                rate_limit=rate_limit,
                force=force,
            )
        finally:
            await pool.close()
    asyncio.run(_go())


@app.command()
def status(
    watch_mode: bool = typer.Option(False, "--watch", "-w"),
) -> None:
    """Show progress dashboard."""
    if watch_mode:
        try:
            asyncio.run(watch())
        except KeyboardInterrupt:
            pass
    else:
        asyncio.run(status_once())


@app.command()
def unstick() -> None:
    """Reset jobs whose lock is older than 15 minutes."""
    async def _go():
        pool = await open_pool()
        try:
            return await unstick_stale(pool)
        finally:
            await pool.close()
    n = asyncio.run(_go())
    console.print(f"unstuck {n} job(s)")


@app.command(name="retry-failed")
def retry_failed() -> None:
    """Move all failed jobs back to pending and clear attempt counters."""
    async def _go():
        pool = await open_pool()
        try:
            async with pool.acquire() as conn:
                return await conn.fetchval(
                    "WITH u AS (UPDATE mc.scrape_jobs SET status='pending', attempts=0, "
                    "last_error=NULL WHERE status='failed' RETURNING 1) SELECT count(*) FROM u"
                )
        finally:
            await pool.close()
    n = asyncio.run(_go())
    console.print(f"requeued {n} failed job(s)")


@app.command()
def track(refresh: int = typer.Option(15, "--refresh", "-r")) -> None:
    """Live single-line progress with rate, avg-per-company, ETA."""
    try:
        asyncio.run(tracker_run(refresh_seconds=refresh))
    except KeyboardInterrupt:
        pass


@app.command(name="probe-appfeeds")
def probe_appfeeds(sc_id: str = typer.Argument(...)) -> None:
    """Probe appfeeds.moneycontrol.com endpoints; report which return JSON with data."""
    async def _go():
        pool = await open_pool()
        try:
            async with make_client() as client:
                results = await probe_all(client, sc_id, save_pool=pool)
            return results
        finally:
            await pool.close()
    results = asyncio.run(_go())
    summary = [
        {
            "statement": r.statement,
            "basis": r.basis,
            "endpoint": r.endpoint,
            "http_status": r.http_status,
            "is_json": r.is_json,
            "has_data": r.has_data,
            "sample": r.sample[:120],
        }
        for r in results
    ]
    console.print_json(data=summary)


@app.command(name="backfill-availability")
def backfill_availability(
    overwrite: bool = typer.Option(
        False,
        "--overwrite",
        help="Re-compute the heuristic for rows that already have availability_date.",
    ),
) -> None:
    """Populate availability_date with the SEBI-deadline heuristic.

    Annual filings: period_end + 60 days.
    Quarterly filings: period_end + 45 days.

    Marks rows with availability_source='heuristic' so a later exchange-filing
    backfill knows it can replace them.
    """
    async def _go():
        # Bypass the standard pool because its 60s command_timeout is too tight
        # for a full-table backfill UPDATE on a large statement_lines table.
        from .config import get_settings as _gs
        import asyncpg as _ap
        conn = await _ap.connect(dsn=_gs().financials_dsn(), command_timeout=None)
        try:
            await conn.execute("SET statement_timeout = 0")
            where = "" if overwrite else " AND availability_date IS NULL"
            annual = await conn.execute(
                f"""
                UPDATE mc.statement_lines
                   SET availability_date   = period_end + INTERVAL '60 days',
                       availability_source = 'heuristic'
                 WHERE statement IN ('balance_sheet','profit_loss','cash_flow','ratios')
                   AND period_end IS NOT NULL
                   {where}
                """
            )
            quarterly = await conn.execute(
                f"""
                UPDATE mc.statement_lines
                   SET availability_date   = period_end + INTERVAL '45 days',
                       availability_source = 'heuristic'
                 WHERE statement = 'quarterly_results'
                   AND period_end IS NOT NULL
                   {where}
                """
            )
            return {"annual_rows": annual, "quarterly_rows": quarterly}
        finally:
            await conn.close()
    summary = asyncio.run(_go())
    console.print_json(data=summary)


@app.command(name="test-one")
def test_one(sc_id: str = typer.Argument(...)) -> None:
    """Sanity-check a single company end-to-end. Prints per-statement row counts."""
    async def _go():
        pool = await open_pool()
        try:
            return await run_one_company(pool, sc_id)
        finally:
            await pool.close()
    summary = asyncio.run(_go())
    console.print_json(data=summary)


if __name__ == "__main__":
    app()
