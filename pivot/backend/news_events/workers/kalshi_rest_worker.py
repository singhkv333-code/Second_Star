"""REST poll supervisor for the Kalshi prediction-market trigger.

Mirrors ``polymarket_ws_worker`` (a long-lived ``asyncio.Task`` started
from main.py's startup hook, gracefully no-ops when the flag is off), but
the transport is a polite REST poll loop instead of a WS push — Kalshi's
market-data WebSocket needs RSA-signed auth, while the REST market-data
endpoints are keyless, so REST is the correct beta path.

It REUSES the venue-agnostic ``PolymarketWSEvaluator`` (no clone): each
active ``trigger.kalshi`` workflow step is registered under key
``kalshi:wf:<workflow_id>:<step_index>`` with a ``fire_handler`` closure
that calls ``fire_external_event``. Each reconcile+poll tick:

  1. reconcile the evaluator's registrations against the DB,
  2. batch-fetch the watched tickers in ONE request,
  3. for each registered ``asset_id`` (``"<ticker>:<side>"``), feed the
     SIDE-CORRECT probability into ``evaluator.on_tick`` (NO side uses
     ``1 - yes_price`` since Kalshi only exposes the YES price), and
  4. for settled markets, derive a resolution payload and feed
     ``evaluator.on_resolved``.

Only the workflow-step path is wired (standalone NewsEventSpec alerts are
a fast-follow); that path never touches the evaluator's polymarket-labelled
spec handler, so the shared evaluator needs no change.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Optional

from sqlalchemy.orm import Session

from backend.config import settings
from backend.database import SessionLocal
from backend.models import Workflow, WorkflowStatus, WorkflowStep
from backend.news_events.pipeline.prediction_market_ws import (
    PolymarketWSEvaluator,
    ResolveOn,
    ThresholdDirection,
    TriggerMode,
)
from backend.news_events.sources import kalshi as kalshi_src
from backend.news_events.workers.polymarket_ws_worker import WsRegSpec

logger = logging.getLogger(__name__)


_running_task: Optional[asyncio.Task] = None
_evaluator: Optional[PolymarketWSEvaluator] = None


def _key(workflow_id: str, step_index: int) -> str:
    return f"kalshi:wf:{workflow_id}:{step_index}"


def _extract_step_ws_config(step_config: dict) -> Optional[WsRegSpec]:
    """Pull a WsRegSpec out of a trigger.kalshi step config. Returns None
    when the step is missing token_id or fails validation (threshold mode
    with no/out-of-range threshold)."""
    cfg = step_config or {}
    token = cfg.get("token_id")
    if not token:
        return None
    mode_raw = str(cfg.get("mode", "threshold")).lower()
    mode: TriggerMode = "resolution" if mode_raw == "resolution" else "threshold"

    threshold: Optional[float] = None
    direction: ThresholdDirection = "above"
    if mode == "threshold":
        raw_threshold = cfg.get("threshold")
        if raw_threshold is None:
            return None
        try:
            threshold = float(raw_threshold)
        except (TypeError, ValueError):
            return None
        if not (0.0 <= threshold <= 1.0):
            return None
        dir_raw = str(cfg.get("direction", "above")).lower()
        direction = "below" if dir_raw == "below" else "above"

    resolve_on_raw = str(cfg.get("resolve_on", "YES")).upper()
    resolve_on: ResolveOn = (
        "NO" if resolve_on_raw == "NO"
        else "ANY" if resolve_on_raw == "ANY"
        else "YES"
    )
    return WsRegSpec(
        asset_id=str(token),
        mode=mode,
        threshold=threshold,
        direction=direction,
        resolve_on=resolve_on,
    )


def _scan_active_kalshi_steps() -> dict[tuple[str, int], WsRegSpec]:
    """Sync DB scan: (workflow_id, step_index) → WsRegSpec for every
    trigger.kalshi step under an active workflow."""
    out: dict[tuple[str, int], WsRegSpec] = {}
    db: Session = SessionLocal()
    try:
        rows = (
            db.query(Workflow.id, WorkflowStep.step_index, WorkflowStep.config)
            .join(WorkflowStep, WorkflowStep.workflow_id == Workflow.id)
            .filter(
                Workflow.status == WorkflowStatus.active,
                WorkflowStep.step_type == "trigger.kalshi",
            )
            .all()
        )
        for wf_id, step_index, cfg in rows:
            reg = _extract_step_ws_config(cfg or {})
            if reg is None:
                continue
            out[(str(wf_id), int(step_index))] = reg
    finally:
        db.close()
    return out


def _build_workflow_fire_handler(workflow_id: str, step_index: int):
    """Closure that calls fire_external_event on a kalshi cross/settle.
    Bound to (workflow_id, step_index); in-memory only (never serialized,
    so the APScheduler closure constraint does not apply)."""
    from datetime import datetime, timezone

    async def _handler(payload: dict) -> None:
        from backend.workflows.scheduler import fire_external_event
        try:
            run_id = await fire_external_event(
                workflow_id=workflow_id,
                triggered_step_index=step_index,
                fired_at=datetime.now(timezone.utc),
                audit_context={"source": "kalshi_rest", **payload},
            )
            logger.info(
                "[kalshi_rest.supervisor] workflow fire wf=%s step=%d "
                "run=%s mode=%s",
                workflow_id, step_index, run_id, payload.get("mode"),
            )
        except Exception:  # noqa: BLE001
            logger.exception(
                "[kalshi_rest.supervisor] fire_external_event failed "
                "wf=%s step=%d", workflow_id, step_index,
            )

    return _handler


async def _reconcile(evaluator: PolymarketWSEvaluator) -> None:
    """Diff DB → evaluator registrations (register new, drop vanished)."""
    wf_steps = await asyncio.to_thread(_scan_active_kalshi_steps)
    desired = {_key(wid, idx) for (wid, idx) in wf_steps.keys()}

    for stale in evaluator.registered_keys() - desired:
        if stale.startswith("kalshi:"):
            evaluator.unregister(stale)

    for (wf_id, step_idx), reg in wf_steps.items():
        evaluator.register(
            key=_key(wf_id, step_idx),
            asset_id=reg.asset_id,
            fire_handler=_build_workflow_fire_handler(wf_id, step_idx),
            mode=reg.mode,
            threshold=reg.threshold,
            direction=reg.direction,
            resolve_on=reg.resolve_on,
        )


async def _poll_prices(evaluator: PolymarketWSEvaluator) -> None:
    """One price poll: batch-fetch the watched tickers, then drive the
    evaluator with the side-correct probability (and resolution)."""
    asset_ids = list(evaluator.subscribed_asset_ids())
    if not asset_ids:
        return
    tickers = sorted({kalshi_src.split_kalshi_asset_id(a)[0] for a in asset_ids})
    snaps = await kalshi_src.get_markets(tickers)
    if not snaps:
        return
    ts = time.time()
    for asset_id in asset_ids:
        ticker, side = kalshi_src.split_kalshi_asset_id(asset_id)
        snap = snaps.get(ticker)
        if snap is None:
            continue
        if snap.settled:
            payload = kalshi_src.resolution_payload(snap)
            if payload is not None:
                await evaluator.on_resolved(asset_id, payload)
            continue
        # NO side watches the NO probability = 1 - yes_price (Kalshi only
        # exposes the YES price). YES side watches yes_price directly.
        mid = snap.yes_price if side == "YES" else (1.0 - snap.yes_price)
        await evaluator.on_tick(asset_id, mid, ts)


async def _run_supervisor() -> None:
    global _evaluator
    _evaluator = PolymarketWSEvaluator(session_factory=SessionLocal)
    try:
        await _reconcile(_evaluator)
        await _poll_prices(_evaluator)
    except Exception:  # noqa: BLE001
        logger.exception("[kalshi_rest.supervisor] initial tick failed")

    interval = max(10, int(settings.kalshi_rest_reconcile_interval_s or 30))
    logger.info(
        "[kalshi_rest.supervisor] started, poll every %ds, initial_subs=%d",
        interval, len(_evaluator.subscribed_asset_ids()),
    )
    try:
        while True:
            try:
                await asyncio.sleep(interval)
                await _reconcile(_evaluator)
                await _poll_prices(_evaluator)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                logger.exception(
                    "[kalshi_rest.supervisor] tick failed; continuing")
    except asyncio.CancelledError:
        logger.info("[kalshi_rest.supervisor] cancelled, shutting down")


def start_kalshi_rest_worker(
    loop: asyncio.AbstractEventLoop | None = None,
) -> None:
    """Boot the supervisor as a fire-and-forget asyncio task. Idempotent;
    no-ops when no active trigger.kalshi steps exist (the poll loop just
    finds nothing to subscribe)."""
    global _running_task
    if _running_task is not None and not _running_task.done():
        logger.info("[kalshi_rest.supervisor] already running, skipping")
        return
    target_loop = loop or asyncio.get_event_loop()
    _running_task = target_loop.create_task(
        _run_supervisor(), name="news_events.kalshi_rest_worker",
    )
    logger.info("[kalshi_rest.supervisor] worker task scheduled")


async def stop_kalshi_rest_worker() -> None:
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
    """Public hook for the chat tool / API: skip the interval wait when a
    new active trigger.kalshi step lands. No-op when the worker isn't
    running."""
    if _evaluator is None or _running_task is None or _running_task.done():
        return
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        return
    loop.create_task(
        _reconcile(_evaluator), name="news_events.kalshi_rest_immediate_reconcile",
    )
