"""Long-running supervisor for the Polymarket WS prediction-market trigger.

Mirrors the Telegram worker pattern (``telegram_worker.py``): one
asyncio task started from main.py's startup hook, gracefully no-ops
when the feature flag is off. Owns the WS client + evaluator pair
and keeps the in-memory registration set aligned with the DB.

Two sources of registrations are scanned every
``polymarket_ws_reconcile_interval_s`` seconds:

  1. NewsEventSpec rows whose ``resolution_criteria`` carry a
     ``polymarket_token_id`` (slice 1/2 standalone-trigger path).
     Registered under key ``spec:<uuid>``; firing calls ``fire_spec``.
  2. WorkflowStep rows where ``step_type == 'trigger.polymarket'``
     under an active Workflow (slice 4 compositional-DSL path).
     Registered under key ``wf:<workflow_id>:<step_index>``; firing
     calls ``fire_external_event(workflow_id, triggered_step_index)``.

Both kinds share the same WS connection (CLOB market WS accepts
many ``assets_ids`` in one subscribe). The evaluator routes each
fire to the right pipeline via its fire_handler closure.

Each registration carries a ``mode`` (threshold vs resolution) +
``resolve_on`` (YES / NO / ANY for resolution mode), so the same
two scan paths produce both event-completion and price-cross
triggers without forking the supervisor.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Optional

from sqlalchemy.orm import Session

from backend.config import settings
from backend.database import SessionLocal
from backend.models import Workflow, WorkflowStatus, WorkflowStep
from backend.news_events.models import NewsEventSpec
from backend.news_events.pipeline.prediction_market_ws import (
    PolymarketWSEvaluator,
    ResolveOn,
    ThresholdDirection,
    TriggerMode,
)
from backend.news_events.sources.polymarket_ws import PolymarketWSClient

logger = logging.getLogger(__name__)


_running_task: Optional[asyncio.Task] = None
_client: Optional[PolymarketWSClient] = None
_evaluator: Optional[PolymarketWSEvaluator] = None


@dataclass(frozen=True)
class WsRegSpec:
    """Per-registration config the supervisor passes to the evaluator.

    Carries both threshold-mode fields (threshold/direction) and
    resolution-mode fields (resolve_on). The evaluator picks the
    relevant ones based on ``mode``.
    """

    asset_id: str
    mode: TriggerMode = "threshold"
    threshold: Optional[float] = None
    direction: ThresholdDirection = "above"
    resolve_on: ResolveOn = "YES"


# ── source 1: NewsEventSpec scan ─────────────────────────────────────


def _extract_ws_config(spec: NewsEventSpec) -> Optional[WsRegSpec]:
    """Pull the WS-mode config out of a spec's resolution_criteria.

    A spec opts into WS mode by setting ``polymarket_token_id``.
    Threshold mode (default) ALSO requires ``prediction_market_threshold``.
    Resolution mode only requires the token_id; ``polymarket_resolve_on``
    is read if set, otherwise defaults to YES.
    """
    rc = dict(spec.resolution_criteria or {})
    token = rc.get("polymarket_token_id")
    if not token:
        return None

    mode_raw = str(rc.get("polymarket_trigger_mode", "threshold")).lower()
    mode: TriggerMode = "resolution" if mode_raw == "resolution" else "threshold"

    threshold: Optional[float] = None
    direction: ThresholdDirection = "above"
    if mode == "threshold":
        raw_threshold = rc.get("prediction_market_threshold")
        if raw_threshold is None:
            return None
        try:
            threshold = float(raw_threshold)
        except (TypeError, ValueError):
            return None
        if not (0.0 <= threshold <= 1.0):
            return None
        dir_raw = str(rc.get("polymarket_threshold_direction", "above")).lower()
        direction = "below" if dir_raw == "below" else "above"

    resolve_on_raw = str(rc.get("polymarket_resolve_on", "YES")).upper()
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


def _scan_active_news_specs() -> dict[str, WsRegSpec]:
    """Sync DB scan: spec_id → WsRegSpec for every active WS-mode
    NewsEventSpec. Called inside a worker thread."""
    out: dict[str, WsRegSpec] = {}
    db: Session = SessionLocal()
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


# ── source 2: WorkflowStep scan ──────────────────────────────────────


def _extract_step_ws_config(step_config: dict) -> Optional[WsRegSpec]:
    """Pull a WsRegSpec out of a trigger.polymarket step's config.

    Returns None when the step is missing token_id or fails validation
    (mode='threshold' with no threshold, threshold out of range, etc.) —
    the supervisor skips it; the engine never fires that branch.
    """
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


def _scan_active_workflow_steps() -> dict[tuple[str, int], WsRegSpec]:
    """Sync DB scan: (workflow_id, step_index) → WsRegSpec for every
    trigger.polymarket step under an active Workflow."""
    out: dict[tuple[str, int], WsRegSpec] = {}
    db: Session = SessionLocal()
    try:
        rows = (
            db.query(Workflow.id, WorkflowStep.step_index, WorkflowStep.config)
            .join(WorkflowStep, WorkflowStep.workflow_id == Workflow.id)
            .filter(
                Workflow.status == WorkflowStatus.active,
                WorkflowStep.step_type == "trigger.polymarket",
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


# ── reconcile pass ───────────────────────────────────────────────────


def _build_workflow_fire_handler(workflow_id: str, step_index: int):
    """Closure that calls fire_external_event on a trigger.polymarket
    cross / resolution. Bound to (workflow_id, step_index) at scan
    time; safe to keep alive across reconciles because both ids are
    stable for the lifetime of the workflow row."""
    from datetime import datetime, timezone

    async def _handler(payload: dict) -> None:
        from backend.workflows.scheduler import fire_external_event
        try:
            run_id = await fire_external_event(
                workflow_id=workflow_id,
                triggered_step_index=step_index,
                fired_at=datetime.now(timezone.utc),
                audit_context={
                    "source": "polymarket_ws",
                    **payload,
                },
            )
            logger.info(
                "[polymarket_ws.supervisor] workflow fire workflow_id=%s "
                "step_index=%d run_id=%s mode=%s",
                workflow_id, step_index, run_id, payload.get("mode"),
            )
        except Exception:  # noqa: BLE001
            logger.exception(
                "[polymarket_ws.supervisor] fire_external_event failed "
                "workflow_id=%s step_index=%d",
                workflow_id, step_index,
            )

    return _handler


async def _reconcile_once(
    evaluator: PolymarketWSEvaluator,
    client: PolymarketWSClient,
) -> None:
    """One pass: read both DB sources, diff against the evaluator's
    current keys, push registrations + subscriptions. All DB I/O in
    a worker thread; the evaluator's API is sync."""
    news = await asyncio.to_thread(_scan_active_news_specs)
    wf_steps = await asyncio.to_thread(_scan_active_workflow_steps)

    # Build the desired key set.
    desired_news_keys = {f"spec:{sid}" for sid in news.keys()}
    desired_wf_keys = {f"wf:{wid}:{idx}" for (wid, idx) in wf_steps.keys()}
    desired_all = desired_news_keys | desired_wf_keys

    # Drop registrations that vanished.
    current = evaluator.registered_keys()
    for stale_key in current - desired_all:
        evaluator.unregister(stale_key)

    # (Re-)register news specs.
    for spec_id, reg in news.items():
        evaluator.register_spec(
            spec_id=spec_id,
            asset_id=reg.asset_id,
            threshold=reg.threshold,
            direction=reg.direction,
            mode=reg.mode,
            resolve_on=reg.resolve_on,
        )

    # (Re-)register workflow steps with a closure that calls
    # fire_external_event.
    for (wf_id, step_idx), reg in wf_steps.items():
        evaluator.register(
            key=f"wf:{wf_id}:{step_idx}",
            asset_id=reg.asset_id,
            fire_handler=_build_workflow_fire_handler(wf_id, step_idx),
            mode=reg.mode,
            threshold=reg.threshold,
            direction=reg.direction,
            resolve_on=reg.resolve_on,
        )

    # Push the union to the WS client.
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
    Idempotent; gracefully no-ops when no active registrations exist
    (the WS client never connects until set_subscriptions lands a
    non-empty set).
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
    """Graceful shutdown hook."""
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
    """Public hook for the chat tool / API: when a user just created
    or activated a new WS-mode spec OR an active workflow with a
    trigger.polymarket step, skip the interval wait so the
    subscription opens within an event-loop tick.

    No-op when the worker isn't running.
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
