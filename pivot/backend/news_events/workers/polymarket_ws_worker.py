"""Long-running supervisor for the Polymarket WS prediction-market trigger.

Mirrors the Telegram worker pattern (``telegram_worker.py``): one
asyncio task started from main.py's startup hook, gracefully no-ops
when the feature flag is off. Owns the WS client + evaluator pair
and keeps the in-memory registration set aligned with the DB.

What it does on each tick (every
``polymarket_ws_reconcile_interval_s`` seconds):

  1. Query active NewsEventSpec rows whose ``resolution_criteria``
     carry a ``polymarket_token_id`` + ``prediction_market_threshold``.
  2. Diff against the evaluator's current registrations:
     - new spec_ids → register
     - spec_ids gone or no longer active → unregister
     - existing spec_ids with changed threshold/direction → re-register
  3. Push the new subscribed-asset set into the WS client. The client
     reconnects with the new ``assets_ids`` list.

Failure handling: the entire worker is wrapped in a top-level
exception barrier so a single tick failure can't crash the app.
If the WS client itself dies (its own run loop), it auto-restarts
with backoff — that's owned inside the client, not here.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Optional

from backend.config import settings
from backend.database import SessionLocal
from backend.news_events.models import NewsEventSpec
from backend.news_events.pipeline.prediction_market_ws import (
    PolymarketWSEvaluator,
    ThresholdDirection,
)
from backend.news_events.sources.polymarket_ws import PolymarketWSClient

logger = logging.getLogger(__name__)


_running_task: Optional[asyncio.Task] = None
_client: Optional[PolymarketWSClient] = None
_evaluator: Optional[PolymarketWSEvaluator] = None


def _extract_ws_config(spec: NewsEventSpec) -> Optional[tuple[str, float, ThresholdDirection]]:
    """Pull (asset_id, threshold, direction) out of a spec's
    resolution_criteria, or None if the spec isn't WS-mode-eligible.

    A spec opts into WS mode by setting BOTH:
      - resolution_criteria.polymarket_token_id
      - resolution_criteria.prediction_market_threshold (0..1)

    Direction defaults to "above" (the common case — alert when
    YES probability rises above threshold). "below" is supported for
    "alert when this becomes unlikely" triggers.
    """
    rc = dict(spec.resolution_criteria or {})
    token = rc.get("polymarket_token_id")
    if not token:
        return None
    raw_threshold = rc.get("prediction_market_threshold")
    if raw_threshold is None:
        return None
    try:
        threshold = float(raw_threshold)
    except (TypeError, ValueError):
        return None
    if not (0.0 <= threshold <= 1.0):
        return None
    direction = str(rc.get("polymarket_threshold_direction", "above")).lower()
    if direction not in {"above", "below"}:
        direction = "above"
    return str(token), threshold, direction  # type: ignore[return-value]


def _scan_active_specs() -> dict[str, tuple[str, float, ThresholdDirection]]:
    """Sync DB scan. Returns spec_id → (asset_id, threshold, direction)
    for every active WS-mode spec. Called in a worker thread."""
    out: dict[str, tuple[str, float, ThresholdDirection]] = {}
    db = SessionLocal()
    try:
        rows = (
            db.query(NewsEventSpec)
            .filter(NewsEventSpec.state == "active")
            .all()
        )
        for spec in rows:
            cfg = _extract_ws_config(spec)
            if cfg is None:
                continue
            out[spec.id] = cfg
    finally:
        db.close()
    return out


async def _reconcile_once(
    evaluator: PolymarketWSEvaluator,
    client: PolymarketWSClient,
) -> None:
    """One pass: read DB, diff, push registrations to evaluator,
    push subscriptions to client. All DB I/O happens in a worker
    thread so the event loop stays responsive."""
    desired = await asyncio.to_thread(_scan_active_specs)
    current_specs = evaluator.registered_spec_ids()

    # Registrations to drop.
    for spec_id in current_specs - desired.keys():
        evaluator.unregister(spec_id)

    # Registrations to add or refresh.
    for spec_id, (asset_id, threshold, direction) in desired.items():
        evaluator.register(
            spec_id=spec_id,
            asset_id=asset_id,
            threshold=threshold,
            direction=direction,
        )

    # Push subscription set to the WS client. If the set is identical
    # the client no-ops; if it changed the client reconnects with the
    # new asset list.
    await client.set_subscriptions(evaluator.subscribed_asset_ids())


async def _run_supervisor() -> None:
    """Outer loop: periodically reconcile, sleep, repeat.

    On any per-tick exception we log and continue — losing one tick
    doesn't matter (the next one re-syncs), but losing the worker
    means the WS connection eventually leaks or stales.
    """
    global _client, _evaluator
    _evaluator = PolymarketWSEvaluator(session_factory=SessionLocal)
    _client = PolymarketWSClient(
        on_tick=_evaluator.on_tick,
        on_resolved=_evaluator.on_resolved,
    )
    # First reconcile BEFORE starting the client. If no specs are
    # active, the client never connects (its run loop sleeps until
    # set_subscriptions() lands a non-empty set).
    try:
        await _reconcile_once(_evaluator, _client)
    except Exception:  # noqa: BLE001
        logger.exception(
            "[polymarket_ws.supervisor] initial reconcile failed"
        )
    await _client.start()

    interval = max(5, int(settings.polymarket_ws_reconcile_interval_s or 30))
    logger.info(
        "[polymarket_ws.supervisor] started, reconcile every %ds, "
        "initial_subs=%d",
        interval, len(_evaluator.subscribed_asset_ids()),
    )

    try:
        while True:
            try:
                await asyncio.sleep(interval)
                await _reconcile_once(_evaluator, _client)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                logger.exception(
                    "[polymarket_ws.supervisor] reconcile tick failed; "
                    "continuing"
                )
    except asyncio.CancelledError:
        logger.info("[polymarket_ws.supervisor] cancelled, shutting down")
    finally:
        if _client is not None:
            try:
                await _client.stop()
            except Exception:  # noqa: BLE001
                pass


def start_polymarket_ws_worker(
    loop: asyncio.AbstractEventLoop | None = None,
) -> None:
    """Boot the supervisor as a fire-and-forget asyncio task.

    Idempotent — calling while a worker is already running is a no-op.
    Called from main.py's startup hook behind the
    ``polymarket_ws_enabled`` flag.
    """
    global _running_task
    if _running_task is not None and not _running_task.done():
        logger.info(
            "[polymarket_ws.supervisor] already running, skipping"
        )
        return
    target_loop = loop or asyncio.get_event_loop()
    _running_task = target_loop.create_task(
        _run_supervisor(),
        name="news_events.polymarket_ws_worker",
    )
    logger.info("[polymarket_ws.supervisor] worker task scheduled")


async def stop_polymarket_ws_worker() -> None:
    """Graceful shutdown hook. Cancels the supervisor and waits."""
    global _running_task
    if _running_task is None or _running_task.done():
        return
    _running_task.cancel()
    try:
        await _running_task
    except (asyncio.CancelledError, Exception):  # noqa: BLE001
        pass
    _running_task = None


def request_immediate_reconcile() -> None:
    """Public hook for the chat tool / API: when a user just created a
    new WS-mode spec, skip the interval wait so the spec goes live
    immediately. Schedules a single reconcile pass without disturbing
    the outer cadence.

    No-op when the worker isn't running. Implemented as a fire-and-
    forget task; failures are logged inside _reconcile_once.
    """
    if _client is None or _evaluator is None or _running_task is None:
        return
    if _running_task.done():
        return
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        return
    loop.create_task(
        _reconcile_once(_evaluator, _client),
        name="news_events.polymarket_ws_immediate_reconcile",
    )
