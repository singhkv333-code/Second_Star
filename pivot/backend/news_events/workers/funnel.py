"""APScheduler job that drains the Stage 3-6 funnel.

Same shape as ``workers/poller.py`` — one job, attached to the
existing ``AsyncIOScheduler``. Cadence is 60s by default, but the
batch size (``DEFAULT_BATCH_SIZE``) is the real lever: each tick
processes at most ``DEFAULT_BATCH_SIZE`` (article, spec) pairs. If
the backlog grows, raise the batch size before raising the tick
frequency — the LLM-bound cost is per-pair, not per-tick.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from backend.news_events.pipeline.funnel import process_pending

if TYPE_CHECKING:  # pragma: no cover
    from apscheduler.schedulers.asyncio import AsyncIOScheduler  # type: ignore[import-untyped]

logger = logging.getLogger(__name__)


_FUNNEL_JOB_ID = "news_events.funnel.drain"
_FUNNEL_INTERVAL_SECONDS = 60


async def _drain_tick() -> None:
    """One pass over pending classifications. Wrapped here so the
    APScheduler job can pickle a top-level coroutine."""
    try:
        await process_pending()
    except Exception:  # noqa: BLE001
        logger.exception("[news_events.funnel] tick failed; will retry next interval")


def register_funnel_worker(scheduler: "AsyncIOScheduler") -> None:
    """Attach the funnel-drain job. Idempotent — re-registration
    replaces the existing job by ID."""
    scheduler.add_job(
        _drain_tick,
        trigger="interval",
        seconds=_FUNNEL_INTERVAL_SECONDS,
        id=_FUNNEL_JOB_ID,
        name="Pivot news_events — funnel drain (Stages 3-6)",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
        misfire_grace_time=30,
    )
    logger.info(
        "[news_events.funnel] registered drain job (%ss)",
        _FUNNEL_INTERVAL_SECONDS,
    )
