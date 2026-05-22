"""APScheduler poller — one job per enabled source.

Plugs into the existing ``AsyncIOScheduler`` exactly like
``backend/workflows/scheduler.py`` does. ``register_poller(scheduler)``
is called from main.py's startup hook when ``news_events_enabled`` is
true; with the flag off, this module is never imported and nothing is
registered.

Each source gets its own interval job at its configured cadence. We
prefer per-source jobs over a single big-tick job because:

  1. Cadences differ widely (180s for Google News, 900s for RBI
     speeches) — running them all on the tightest cadence wastes
     budget.
  2. ``max_instances=1`` per job stops a slow source from blocking
     others.
  3. APScheduler's SQLAlchemy job store survives restarts; the jobs
     re-arm at app boot.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from backend.news_events.config import SourceDef, enabled_sources
from backend.news_events.pipeline.ingest import build_adapter, ingest_one_source

if TYPE_CHECKING:  # pragma: no cover
    from apscheduler.schedulers.asyncio import AsyncIOScheduler  # type: ignore[import-untyped]

logger = logging.getLogger(__name__)


def _job_id(source_id: str) -> str:
    """Stable job ID so re-registrations replace the existing job."""
    return f"news_events.poll.{source_id}"


async def _poll_source(source_id: str) -> None:
    """Single source tick. Pulled out as a top-level coroutine so
    APScheduler can pickle it for the SQLAlchemy job store."""
    from backend.news_events.config import get_source  # local — avoid cycle

    source = get_source(source_id)
    if source is None or not source.enabled:
        return
    adapter = build_adapter(source)
    try:
        await ingest_one_source(adapter)
    except Exception:  # noqa: BLE001 — never let a poll job kill the loop
        # `ingest_one_source` already handles SourceFetchError. Anything
        # that escapes is a bug; we log and swallow so the next tick
        # gets a chance.
        logger.exception(
            "[news_events.poller] unexpected error polling source=%s",
            source_id,
        )


def register_poller(scheduler: "AsyncIOScheduler") -> None:
    """Attach one interval job per enabled RSS source to the shared
    scheduler. Idempotent — re-registering replaces existing jobs by
    ID (mirrors ``backend.workflows.scheduler.register_workflow_scheduler``).

    Sources with ``kind != 'rss'`` are skipped — Telegram is push-only
    via ``workers/telegram_worker.py``, Miniflux is push-only via the
    webhook receiver in ``router.py``. Each transport owns its own
    worker.
    """
    registered: list[str] = []
    for source in enabled_sources():
        if source.kind != "rss":
            continue
        scheduler.add_job(
            _poll_source,
            trigger="interval",
            seconds=source.poll_interval_seconds,
            id=_job_id(source.source_id),
            name=f"Pivot news_events — poll {source.source_id}",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
            args=[source.source_id],
            # First tick after 10s — gives the app time to finish
            # startup before any source fetch kicks off.
            next_run_time=None,
            misfire_grace_time=30,
        )
        registered.append(source.source_id)

    logger.info(
        "[news_events.poller] registered %d source jobs: %s",
        len(registered),
        ", ".join(registered) if registered else "(none)",
    )


def list_registered_jobs(scheduler: "AsyncIOScheduler") -> list[SourceDef]:
    """Helper for the admin endpoint — returns the SourceDef rows
    backed by an active APScheduler job. With the feature flag off
    this returns ``[]``."""
    out: list[SourceDef] = []
    for source in enabled_sources():
        if scheduler.get_job(_job_id(source.source_id)) is not None:
            out.append(source)
    return out
