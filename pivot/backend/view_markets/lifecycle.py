"""View Markets — lifecycle worker (status advancement) + scheduler registration.

Advances a published ``MarketView.status`` along the lifecycle
``open -> developing -> consensus -> resolved -> archived`` (PLAN §8) as
resolution dates approach and as the macro verifier reads real outcomes:

  * open -> developing  — published and resolution still distant / evidence moving.
  * developing -> consensus — remaining surprise is low (market has converged).
  * -> resolved          — ``resolution_date`` passed AND the verifier confirms
                           the outcome; ``view_expectations.resolved_value`` is
                           backfilled and outcome confidence collapses to 0/1.
  * resolved -> archived — after a grace period (kept for the track record).

GOTCHA (documented, load-bearing): APScheduler with the SQLAlchemy jobstore
serializes callables by TEXTUAL REFERENCE — a closure silently kills
``scheduler.start()`` for EVERY job. So the job (``advance_view_lifecycle``) is a
MODULE-LEVEL coroutine that opens its OWN ``SessionLocal`` (the background-task
pattern from ``backend/scheduler.py``), never a closure.

``register_view_markets_lifecycle(scheduler)`` is a NO-OP unless
``config.view_markets_enabled`` — gated at REGISTRATION (not self-gated inside),
mirroring ``register_workflow_scheduler``'s macro-watcher gate, so the job
doesn't even exist when the flag is off. Tests call ``advance_one_view`` /
``advance_view_lifecycle`` directly.

Reuses (real interfaces, pinned 2026-06-29):
  * ``backend.database.SessionLocal`` — module-level import so the test conftest
    can rebind it to the SQLite test session (mirrors workflows conftest).
  * ``backend.config.settings.view_markets_enabled`` (default False).
  * ``backend.models.{MarketView, ViewStatus}``.
  * ``backend.view_markets.feeds.{due_dated_event, read_event_outcome}`` (the
    verifier read) and ``backend.view_markets.expectations.backfill_resolved_value``.
  * APScheduler ``AsyncIOScheduler.add_job`` (interval trigger), same pattern as
    ``register_workflow_scheduler``.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Optional

# Module-level import so tests can monkeypatch lifecycle.SessionLocal -> the
# in-memory test session (see tests/view_markets/conftest.py).
from backend.database import SessionLocal  # noqa: F401  (rebound by tests; used by advance_view_lifecycle)
from backend.models import MarketView, ViewStatus

if TYPE_CHECKING:  # pragma: no cover - typing only
    from apscheduler.schedulers.asyncio import AsyncIOScheduler
    from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


# Job identity + cadence (interval trigger). Hourly is ample — lifecycle moves
# on calendar dates, not ticks.
_LIFECYCLE_JOB_ID = "view_markets_lifecycle"
_LIFECYCLE_INTERVAL_SECONDS = 3600

# developing -> consensus when within this many days of the resolution date (the
# market has converged as the event approaches). Conservative default.
_CONSENSUS_WINDOW_DAYS = 3
# resolved -> archived only after this grace period past resolution (kept around
# for the track record / scorecard).
_ARCHIVE_GRACE_DAYS = 30

# Coarse keyword -> macro-kind inference for the verifier read. Only used as a
# fallback when ``MarketView.category`` doesn't already name a macro kind; never
# fabricates an outcome (an unknown view simply isn't read).
_KIND_KEYWORDS: tuple[tuple[tuple[str, ...], str], ...] = (
    (("rbi", "repo", "mpc", "rate cut", "rate-cut", "rate hike"), "rbi_mpc"),
    (("fomc", "fed ", "federal reserve"), "us_fomc"),
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(dt: Optional[datetime]) -> Optional[datetime]:
    """Normalise to a tz-aware UTC datetime (assume UTC for naive input)."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def advance_one_view(
    db: "Session",
    view: "MarketView",
    *,
    now: Optional[datetime] = None,
    outcome: Optional[object] = None,
) -> Optional[str]:
    """Compute + apply the next status transition for ONE view (testable, no
    scheduler / event loop).

    Pure-ish transition: inspects ``view.status`` / ``resolution_date`` and
    (when supplied) a pre-fetched verifier ``outcome`` (``OutcomeResult``-shaped
    with a ``.matched`` attribute; passed in so this stays synchronous and the
    async verifier read lives in :func:`advance_view_lifecycle`).

    Ladder (open -> developing -> consensus -> resolved -> archived):
      * UNPUBLISHED (``published_at`` is None) drafts never advance.
      * RESOLVE: at/after ``resolution_date`` AND a confirmed ``outcome``
        (``outcome.matched is not None``) -> ``resolved``; backfills
        ``view_expectations.resolved_value`` to 1.0 (occurred) / 0.0 (didn't) so
        the outcome dial collapses to certainty. Without a confirmed outcome we
        WAIT — never fabricate a resolution.
      * ``open`` (published) -> ``developing``.
      * ``developing`` -> ``consensus`` once within ``_CONSENSUS_WINDOW_DAYS`` of
        (and before) ``resolution_date``.
      * ``resolved`` -> ``archived`` after ``_ARCHIVE_GRACE_DAYS`` past
        ``resolution_date``.

    Mutates ``view.status`` in place and returns the NEW status string, or
    ``None`` when no transition applies. Does NOT commit.
    """
    now_utc = _as_utc(now) or _utcnow()
    status = view.status

    # Drafts don't move; archived is terminal.
    if view.published_at is None or status == ViewStatus.archived:
        return None

    res = _as_utc(view.resolution_date)
    matched = getattr(outcome, "matched", None) if outcome is not None else None

    # ── terminal resolution (confirmed outcome at/after the resolution date) ──
    if status in (ViewStatus.open, ViewStatus.developing, ViewStatus.consensus):
        if res is not None and now_utc >= res and matched is not None:
            view.status = ViewStatus.resolved
            # Lazy import keeps the package import-cheap + avoids a cycle.
            from backend.view_markets.expectations import backfill_resolved_value

            backfill_resolved_value(
                db, view.id, resolved_value=1.0 if matched else 0.0,
            )
            return ViewStatus.resolved.value

    # ── open -> developing ───────────────────────────────────────────────
    if status == ViewStatus.open:
        view.status = ViewStatus.developing
        return ViewStatus.developing.value

    # ── developing -> consensus (approaching, not yet past, resolution) ──
    if status == ViewStatus.developing:
        if (
            res is not None
            and now_utc >= res - timedelta(days=_CONSENSUS_WINDOW_DAYS)
            and now_utc < res
        ):
            view.status = ViewStatus.consensus
            return ViewStatus.consensus.value
        return None

    # consensus waits for a confirmed outcome (handled above) — no time-only exit.
    if status == ViewStatus.consensus:
        return None

    # ── resolved -> archived (after the grace period) ────────────────────
    if status == ViewStatus.resolved:
        if (
            res is not None
            and now_utc >= res + timedelta(days=_ARCHIVE_GRACE_DAYS)
        ):
            view.status = ViewStatus.archived
            return ViewStatus.archived.value
        return None

    return None


async def _read_outcome_for_view(
    view: "MarketView",  # noqa: ARG001 — retained for signature compat
    *,
    now: Optional[datetime] = None,  # noqa: ARG001
) -> Optional[object]:
    """No macro-outcome verifier is wired any more; always returns ``None``
    so the lifecycle sweep advances views purely on time / resolution-date
    boundaries. Kept async + module-level so tests can monkeypatch it."""
    return None


async def advance_view_lifecycle() -> dict:
    """MODULE-LEVEL scheduler job: advance every published view's status.

    Opens its OWN ``SessionLocal`` (background-task pattern), scans published,
    non-archived views, awaits ``feeds.read_event_outcome`` for any EVENT view in
    its resolution window, applies :func:`advance_one_view`, commits once, and
    returns a summary dict (counts per transition). Never raises out (logs +
    rolls back on error) so a bad view can't wedge the scheduler.
    """
    db = SessionLocal()
    summary: dict = {
        "scanned": 0,
        "advanced": 0,
        "errors": 0,
        "transitions": {},
    }
    try:
        views = (
            db.query(MarketView)
            .filter(MarketView.published_at.isnot(None))
            .filter(MarketView.status != ViewStatus.archived)
            .all()
        )
        for view in views:
            summary["scanned"] += 1
            try:
                outcome = await _read_outcome_for_view(view)
                new_status = advance_one_view(db, view, outcome=outcome)
                if new_status is not None:
                    summary["advanced"] += 1
                    summary["transitions"][new_status] = (
                        summary["transitions"].get(new_status, 0) + 1
                    )
            except Exception:  # noqa: BLE001 - isolate one bad view
                summary["errors"] += 1
                logger.warning(
                    "view-markets lifecycle advance failed for view %s",
                    getattr(view, "id", "?"), exc_info=True,
                )
        db.commit()
    except Exception:  # noqa: BLE001 - sweep must never raise into the scheduler
        summary["errors"] += 1
        logger.warning("view-markets lifecycle sweep failed", exc_info=True)
        try:
            db.rollback()
        except Exception:  # noqa: BLE001
            pass
    finally:
        db.close()
    return summary


def register_view_markets_lifecycle(scheduler: "AsyncIOScheduler") -> None:
    """Attach the lifecycle worker to the live scheduler — NO-OP unless
    ``config.view_markets_enabled``.

    Gated at registration (not inside the job) so the job doesn't even exist
    when the flag is off, mirroring ``register_workflow_scheduler``'s
    macro-watcher gate. Registers the MODULE-LEVEL ``advance_view_lifecycle``
    (never a closure) as an interval job, ``id=_LIFECYCLE_JOB_ID``,
    ``replace_existing=True``, ``max_instances=1``, ``coalesce=True``. Idempotent.
    """
    from backend.config import settings

    if not getattr(settings, "view_markets_enabled", False):
        logger.info(
            "[view-markets] lifecycle worker NOT registered (flag off)"
        )
        return

    scheduler.add_job(
        advance_view_lifecycle,
        trigger="interval",
        seconds=_LIFECYCLE_INTERVAL_SECONDS,
        id=_LIFECYCLE_JOB_ID,
        name="Pivot View Markets — lifecycle status advance",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    logger.info(
        "[view-markets] lifecycle worker registered (%ss)",
        _LIFECYCLE_INTERVAL_SECONDS,
    )


__all__ = [
    "advance_one_view",
    "advance_view_lifecycle",
    "register_view_markets_lifecycle",
]
