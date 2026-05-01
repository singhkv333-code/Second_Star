"""Worker: claim → fetch → parse → persist loop."""
from __future__ import annotations

import asyncio
import os
import socket
from dataclasses import dataclass
from datetime import datetime, time, timezone, timedelta
from typing import List, Optional

import asyncpg
import structlog

from .config import get_settings
from .fetch import FetchedPage, build_url, fetch_all_pages, gzip_html
from .http import make_client
from .parse.statement import iter_cells
from .ratelimit import AsyncTokenBucket
from .sources.appfeeds import fetch_one as fetch_appfeeds


log = structlog.get_logger()


@dataclass
class JobRow:
    id: int
    sc_id: str
    company_slug: str
    statement: str
    basis: str


async def claim_jobs(conn: asyncpg.Connection, worker_id: str, batch: int) -> List[JobRow]:
    rows = await conn.fetch(
        """
        WITH picked AS (
            SELECT j.id
            FROM mc.scrape_jobs j
            WHERE j.status = 'pending'
            ORDER BY j.id
            LIMIT $1
            FOR UPDATE SKIP LOCKED
        )
        UPDATE mc.scrape_jobs j
           SET status = 'in_progress',
               locked_by = $2,
               locked_at = now(),
               attempts = j.attempts + 1,
               started_at = COALESCE(j.started_at, now())
          FROM picked p, mc.companies c
         WHERE j.id = p.id AND c.sc_id = j.sc_id
         RETURNING j.id, j.sc_id, c.company_slug, j.statement::text, j.basis::text
        """,
        batch, worker_id,
    )
    return [JobRow(**dict(r)) for r in rows]


async def mark_done(
    conn: asyncpg.Connection,
    job: JobRow,
    *,
    pages: int,
    rows_inserted: int,
) -> None:
    await conn.execute(
        """
        UPDATE mc.scrape_jobs
           SET status = 'done',
               pages_fetched = $2,
               rows_inserted = $3,
               last_error = NULL,
               finished_at = now(),
               locked_by = NULL,
               locked_at = NULL
         WHERE id = $1
        """,
        job.id, pages, rows_inserted,
    )


async def mark_no_data(conn: asyncpg.Connection, job: JobRow, note: str = "") -> None:
    await conn.execute(
        """
        UPDATE mc.scrape_jobs
           SET status = 'no_data',
               last_error = $2,
               finished_at = now(),
               locked_by = NULL,
               locked_at = NULL
         WHERE id = $1
        """,
        job.id, note or None,
    )


async def mark_failed_or_retry(
    conn: asyncpg.Connection, job: JobRow, error: str, max_attempts: int = 5
) -> None:
    row = await conn.fetchrow(
        "SELECT attempts FROM mc.scrape_jobs WHERE id = $1", job.id
    )
    attempts = int(row["attempts"]) if row else max_attempts
    if attempts >= max_attempts:
        await conn.execute(
            """
            UPDATE mc.scrape_jobs
               SET status = 'failed',
                   last_error = $2,
                   finished_at = now(),
                   locked_by = NULL,
                   locked_at = NULL
             WHERE id = $1
            """,
            job.id, error[:1000],
        )
    else:
        await conn.execute(
            """
            UPDATE mc.scrape_jobs
               SET status = 'pending',
                   last_error = $2,
                   locked_by = NULL,
                   locked_at = NULL
             WHERE id = $1
            """,
            job.id, error[:1000],
        )


_RAW_PAGE_SQL = """
INSERT INTO mc.raw_pages
    (sc_id, statement, basis, page_no, url, http_status, html_gz)
VALUES ($1, $2::mc.statement_type, $3::mc.basis, $4, $5, $6, $7)
ON CONFLICT (sc_id, statement, basis, page_no) DO UPDATE
   SET url = EXCLUDED.url,
       fetched_at = now(),
       http_status = EXCLUDED.http_status,
       html_gz = EXCLUDED.html_gz
"""

_STATEMENT_LINE_SQL = """
INSERT INTO mc.statement_lines
    (sc_id, statement, basis, period_label, period_end, period_kind,
     section, line_item, line_order, value_text, value_numeric,
     unit, page_no, source_url, source)
VALUES ($1, $2::mc.statement_type, $3::mc.basis,
        $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15)
ON CONFLICT (sc_id, statement, basis, period_label, line_item, line_order)
DO UPDATE SET
    value_text = EXCLUDED.value_text,
    value_numeric = EXCLUDED.value_numeric,
    period_end = EXCLUDED.period_end,
    period_kind = EXCLUDED.period_kind,
    section = EXCLUDED.section,
    unit = EXCLUDED.unit,
    page_no = EXCLUDED.page_no,
    source_url = EXCLUDED.source_url,
    source = EXCLUDED.source,
    scraped_at = now()
"""


async def persist_pages(
    conn: asyncpg.Connection,
    job: JobRow,
    pages: List[FetchedPage],
    *,
    source: str = "mc_html",
) -> int:
    """Insert raw HTML + cells in a single transaction with executemany.

    Builds the full row-batch in memory, then issues one prepared-statement
    bind per table — usually 1 round-trip for raw_pages and 1 for statement_lines
    regardless of how many cells. ~5-10x faster than per-cell inserts.
    """
    if not pages:
        return 0
    raw_rows = []
    line_rows = []
    for page in pages:
        raw_rows.append((
            job.sc_id, job.statement, job.basis, page.page_no,
            page.url, page.http_status, gzip_html(page.html),
        ))
        stmt = page.parsed
        if stmt is None:
            continue
        for idx, period, line, raw, num in iter_cells(stmt):
            duration = stmt.durations[idx] if idx < len(stmt.durations) else None
            kind = period.period_kind or duration
            line_rows.append((
                job.sc_id, job.statement, job.basis,
                period.label, period.period_end, kind,
                line.section, line.line_item, line.line_order,
                raw, num,
                stmt.unit, page.page_no, page.url, source,
            ))
    async with conn.transaction():
        if raw_rows:
            await conn.executemany(_RAW_PAGE_SQL, raw_rows)
        if line_rows:
            await conn.executemany(_STATEMENT_LINE_SQL, line_rows)
    return len(line_rows)


async def try_appfeeds(
    client, job: JobRow
) -> Optional[List[FetchedPage]]:
    """Attempt the JSON appfeeds path. Returns synthetic FetchedPage list on
    success or None if the endpoint had no usable data."""
    probe = await fetch_appfeeds(client, job.sc_id, job.statement, job.basis)
    if not probe.has_data or probe.parsed is None:
        return None
    page = FetchedPage(
        page_no=1,
        url=probe.url,
        http_status=probe.http_status,
        html=f"appfeeds:{probe.endpoint}",
        parsed=probe.parsed,
    )
    return [page]


def _within_market_hours(now: Optional[datetime] = None) -> bool:
    """09:15–15:30 IST = 03:45–10:00 UTC, Mon–Fri."""
    now = now or datetime.now(tz=timezone.utc)
    if now.weekday() >= 5:
        return False
    ist_open = time(3, 45)
    ist_close = time(10, 0)
    return ist_open <= now.time() <= ist_close


def make_worker_id() -> str:
    return f"{socket.gethostname()}/{os.getpid()}"


async def unstick_stale(pool: asyncpg.Pool, *, older_than_minutes: int = 15) -> int:
    async with pool.acquire() as conn:
        n = await conn.fetchval(
            """
            UPDATE mc.scrape_jobs
               SET status = 'pending', locked_by = NULL, locked_at = NULL
             WHERE status = 'in_progress'
               AND locked_at < now() - ($1 * interval '1 minute')
            RETURNING 1
            """,
            older_than_minutes,
        )
        return int(n or 0)


async def run_worker(
    pool: asyncpg.Pool,
    *,
    concurrency: int = 16,
    batch_size: int = 8,
    rate_limit: float = 10.0,
    force: bool = False,
) -> None:
    settings = get_settings()
    if settings.respect_market_hours and _within_market_hours() and not force:
        log.warning("market_hours.refusing_start", message="Pass --force to override.")
        return

    worker_id = make_worker_id()
    bucket = AsyncTokenBucket(refill_per_sec=rate_limit, capacity=rate_limit)
    await bucket.configure()

    sem = asyncio.Semaphore(concurrency)

    async with make_client(settings) as client:
        async def _process_one(job: JobRow):
            async with sem:
                try:
                    af_pages = await try_appfeeds(client, job)
                    if af_pages is not None:
                        async with pool.acquire() as conn:
                            inserted = await persist_pages(
                                conn, job, af_pages, source="mc_appfeeds"
                            )
                            await mark_done(conn, job, pages=1, rows_inserted=inserted)
                            log.info(
                                "job.done", id=job.id, sc=job.sc_id,
                                stmt=job.statement, basis=job.basis,
                                pages=1, rows=inserted, source="mc_appfeeds",
                            )
                            return
                    pages, status = await fetch_all_pages(
                        client,
                        company_slug=job.company_slug,
                        sc_id=job.sc_id,
                        statement=job.statement,
                        basis=job.basis,
                        rate_limiter=bucket,
                    )
                    async with pool.acquire() as conn:
                        if status == "ok" and pages:
                            inserted = await persist_pages(conn, job, pages, source="mc_html")
                            await mark_done(conn, job, pages=len(pages), rows_inserted=inserted)
                            log.info(
                                "job.done", id=job.id, sc=job.sc_id,
                                stmt=job.statement, basis=job.basis,
                                pages=len(pages), rows=inserted, source="mc_html",
                            )
                        elif status == "js_only":
                            await mark_no_data(conn, job, note="quarterly is JS-rendered (appfeeds empty)")
                        else:
                            await mark_no_data(conn, job, note="empty / no statement table")
                except Exception as exc:  # noqa: BLE001
                    log.warning("job.error", id=job.id, error=str(exc))
                    async with pool.acquire() as conn:
                        await mark_failed_or_retry(conn, job, str(exc))

        # Continuous producer/consumer: keep `concurrency` jobs in flight at all
        # times. Whenever a slot frees, claim more — never wait for a slow batch.
        in_flight: set[asyncio.Task] = set()
        idle_loops = 0
        while True:
            # Top up to keep concurrency saturated.
            slots = concurrency - len(in_flight)
            if slots > 0:
                want = min(batch_size, slots)
                async with pool.acquire() as conn:
                    jobs = await claim_jobs(conn, worker_id, want)
                if not jobs:
                    if not in_flight:
                        idle_loops += 1
                        if idle_loops >= 3:
                            log.info("worker.idle_exit", worker=worker_id)
                            return
                        await asyncio.sleep(2.0)
                        continue
                else:
                    idle_loops = 0
                    for j in jobs:
                        in_flight.add(asyncio.create_task(_process_one(j)))
            # Wait for at least one task to complete before topping up.
            if in_flight:
                done, in_flight = await asyncio.wait(
                    in_flight, return_when=asyncio.FIRST_COMPLETED
                )
                # Re-raise any unexpected exceptions.
                for t in done:
                    exc = t.exception()
                    if exc is not None:
                        log.warning("task.crashed", error=str(exc))


async def run_one_company(pool: asyncpg.Pool, sc_id: str) -> dict:
    """End-to-end run for a single company. Used by `mc-scraper test-one`."""
    settings = get_settings()

    async with pool.acquire() as conn:
        company = await conn.fetchrow(
            "SELECT sc_id, company_slug FROM mc.companies WHERE sc_id = $1", sc_id
        )
        if company is None:
            # Bootstrap a single-company entry for ad-hoc testing (Reliance default).
            home = f"https://www.moneycontrol.com/india/stockpricequote/refineries/relianceindustries/{sc_id}"
            await conn.execute(
                """
                INSERT INTO mc.companies (sc_id, company_name, company_slug, industry_slug, home_url)
                VALUES ($1, $1, lower(replace($1, ' ', '')), 'refineries', $2)
                ON CONFLICT (sc_id) DO NOTHING
                """,
                sc_id, home,
            )
            company = await conn.fetchrow(
                "SELECT sc_id, company_slug FROM mc.companies WHERE sc_id = $1", sc_id
            )
        # Special-case Reliance to use the verified slug.
        if sc_id == "RI":
            await conn.execute(
                "UPDATE mc.companies SET company_slug = 'relianceindustries' WHERE sc_id = $1",
                sc_id,
            )
            company = await conn.fetchrow(
                "SELECT sc_id, company_slug FROM mc.companies WHERE sc_id = $1", sc_id
            )

        # Ensure all 10 jobs exist, mark them pending.
        await conn.execute(
            """
            INSERT INTO mc.scrape_jobs (sc_id, statement, basis)
            SELECT $1, s::mc.statement_type, b::mc.basis
              FROM unnest(
                ARRAY['balance_sheet','profit_loss','cash_flow','ratios','quarterly_results']
              ) AS s(s)
              CROSS JOIN unnest(ARRAY['standalone','consolidated']) AS b(b)
            ON CONFLICT (sc_id, statement, basis) DO UPDATE
               SET status='pending', locked_by=NULL, locked_at=NULL,
                   last_error=NULL, attempts=0
            """,
            sc_id,
        )

    bucket = AsyncTokenBucket(refill_per_sec=settings.rate_limit, capacity=settings.rate_limit)
    await bucket.configure()

    summary: dict[str, dict] = {}
    async with make_client(settings) as client:
        async with pool.acquire() as conn:
            jobs = await conn.fetch(
                """
                SELECT j.id, j.sc_id, c.company_slug,
                       j.statement::text AS statement, j.basis::text AS basis
                  FROM mc.scrape_jobs j
                  JOIN mc.companies c ON c.sc_id = j.sc_id
                 WHERE j.sc_id = $1
                 ORDER BY j.statement, j.basis
                """,
                sc_id,
            )

        for r in jobs:
            job = JobRow(**dict(r))
            key = f"{job.statement}/{job.basis}"
            try:
                af_pages = await try_appfeeds(client, job)
                if af_pages is not None:
                    async with pool.acquire() as conn:
                        inserted = await persist_pages(
                            conn, job, af_pages, source="mc_appfeeds"
                        )
                        await mark_done(conn, job, pages=1, rows_inserted=inserted)
                    summary[key] = {"pages": 1, "rows": inserted, "source": "mc_appfeeds"}
                    continue
                pages, status = await fetch_all_pages(
                    client,
                    company_slug=job.company_slug,
                    sc_id=job.sc_id,
                    statement=job.statement,
                    basis=job.basis,
                    rate_limiter=bucket,
                )
                async with pool.acquire() as conn:
                    if status == "ok" and pages:
                        inserted = await persist_pages(conn, job, pages, source="mc_html")
                        await mark_done(conn, job, pages=len(pages), rows_inserted=inserted)
                        summary[key] = {"pages": len(pages), "rows": inserted, "source": "mc_html"}
                    elif status == "js_only":
                        await mark_no_data(conn, job, note="quarterly is JS-rendered (appfeeds empty)")
                        summary[key] = {"pages": 0, "rows": 0, "note": "js_only_appfeeds_empty"}
                    else:
                        await mark_no_data(conn, job, note="empty")
                        summary[key] = {"pages": 0, "rows": 0, "note": "no_data"}
            except Exception as exc:  # noqa: BLE001
                summary[key] = {"error": str(exc)}
                async with pool.acquire() as conn:
                    await mark_failed_or_retry(conn, job, str(exc))
    return summary
