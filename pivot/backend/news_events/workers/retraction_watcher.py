"""APScheduler job for the Phase-6 retraction-window watcher.

Same shape as ``workers/poller.py`` and ``workers/funnel.py``:
one job attached to the existing ``AsyncIOScheduler``. Cadence is
60s — fast enough that a typical retraction is acted on inside a
single safety window minute, slow enough that the per-tick scan
stays cheap.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from backend.news_events.pipeline.retraction import scan_for_retractions

if TYPE_CHECKING:  # pragma: no cover
    from apscheduler.schedulers.asyncio import AsyncIOScheduler  # type: ignore[import-untyped]

logger = logging.getLogger(__name__)


_RETRACTION_JOB_ID = "news_events.retraction.scan"
_RETRACTION_INTERVAL_SECONDS = 60


def _scan_tick() -> None:
    """One pass over fired events for retraction signals. Sync — the
    ``scan_for_retractions`` helper uses its own DB session and is
    safe to call from APScheduler's worker thread without an event
    loop.
    """
    try:
        scan_for_retractions()
    except Exception:  # noqa: BLE001
        logger.exception(
            "[news_events.retraction] scan failed; will retry next interval"
        )


def register_retraction_watcher(scheduler: "AsyncIOScheduler") -> None:
    """Attach the retraction-scan job. Idempotent — re-registration
    replaces the existing job by ID (same pattern as the poller and
    funnel workers)."""
    scheduler.add_job(
        _scan_tick,
        trigger="interval",
        seconds=_RETRACTION_INTERVAL_SECONDS,
        id=_RETRACTION_JOB_ID,
        name="Pivot news_events — retraction-window watcher",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
        misfire_grace_time=30,
    )
    logger.info(
        "[news_events.retraction] registered watcher job (%ss)",
        _RETRACTION_INTERVAL_SECONDS,
    )
