"""Lightweight progress tracker. Prints a single-line snapshot to stdout every
N seconds with: companies fetched / remaining, jobs done / total, rate, avg
time per company, and ETA. Designed to be tail-followable from a log file.
"""
from __future__ import annotations

import asyncio
import time
from collections import deque
from datetime import datetime, timezone

import asyncpg

from .db import open_pool


async def _snap(conn: asyncpg.Connection) -> dict:
    row = await conn.fetchrow(
        """
        WITH j AS (
            SELECT
                count(*) FILTER (WHERE status='pending')      AS pending,
                count(*) FILTER (WHERE status='in_progress')  AS in_progress,
                count(*) FILTER (WHERE status='done')         AS done,
                count(*) FILTER (WHERE status='no_data')      AS no_data,
                count(*) FILTER (WHERE status='failed')       AS failed,
                count(*)                                       AS total,
                count(DISTINCT sc_id) FILTER (WHERE status NOT IN ('pending','in_progress')) AS companies_settled,
                count(DISTINCT sc_id)                          AS companies_total
            FROM mc.scrape_jobs
        )
        SELECT * FROM j
        """
    )
    rows = await conn.fetchval("SELECT count(*) FROM mc.statement_lines")
    return {
        **{k: int(v) for k, v in row.items() if v is not None},
        "rows": int(rows or 0),
    }


def _fmt_eta(seconds: float) -> str:
    if seconds <= 0 or seconds != seconds:  # NaN
        return "—"
    h, rem = divmod(int(seconds), 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}h{m:02d}m"
    if m:
        return f"{m}m{s:02d}s"
    return f"{s}s"


async def run(refresh_seconds: int = 15, history: int = 8) -> None:
    """Sleep-loop tracker. Exits when no jobs remain pending+in_progress."""
    pool = await open_pool()
    history_q: deque[tuple[float, int]] = deque(maxlen=history)
    started = time.time()
    try:
        async with pool.acquire() as conn:
            initial = await _snap(conn)
        history_q.append((time.time(), initial["done"] + initial["no_data"] + initial["failed"]))
        print(
            f"[tracker] start utc={datetime.now(timezone.utc).isoformat(timespec='seconds')} "
            f"jobs_total={initial['total']} companies={initial['companies_total']}",
            flush=True,
        )
        while True:
            await asyncio.sleep(refresh_seconds)
            async with pool.acquire() as conn:
                s = await _snap(conn)
            now = time.time()
            settled = s["done"] + s["no_data"] + s["failed"]
            history_q.append((now, settled))

            # Rate from a sliding window.
            if len(history_q) >= 2:
                t0, n0 = history_q[0]
                dt = max(now - t0, 0.001)
                rate_per_sec = max((settled - n0) / dt, 0.0)
            else:
                rate_per_sec = 0.0

            remaining_jobs = max(s["pending"] + s["in_progress"], 0)
            eta_s = remaining_jobs / rate_per_sec if rate_per_sec > 0 else float("inf")

            companies_done = s["companies_settled"]
            companies_total = s["companies_total"]
            companies_remaining = max(companies_total - companies_done, 0)

            # Avg seconds per company across the whole run.
            elapsed = now - started
            companies_per_sec = (companies_done - 0) / max(elapsed, 0.001)
            avg_per_company = (1.0 / companies_per_sec) if companies_per_sec > 0 else float("inf")

            line = (
                f"[{datetime.now().strftime('%H:%M:%S')}] "
                f"companies {companies_done}/{companies_total} "
                f"(remaining {companies_remaining}) | "
                f"jobs done={s['done']} no_data={s['no_data']} failed={s['failed']} "
                f"in_progress={s['in_progress']} pending={s['pending']} | "
                f"rate={rate_per_sec*60:.1f} jobs/min | "
                f"avg/company={_fmt_eta(avg_per_company)} | "
                f"eta={_fmt_eta(eta_s)} | "
                f"rows={s['rows']:,}"
            )
            print(line, flush=True)

            if remaining_jobs == 0 and s["total"] > 0:
                print(
                    f"[tracker] DONE total_elapsed={_fmt_eta(elapsed)} "
                    f"avg/company={_fmt_eta(avg_per_company)} "
                    f"final rows={s['rows']:,} "
                    f"done={s['done']} no_data={s['no_data']} failed={s['failed']}",
                    flush=True,
                )
                return
    finally:
        await pool.close()
