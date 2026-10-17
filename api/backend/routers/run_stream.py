"""WebSocket: live run stream (API_CONTRACT.md §10).

Endpoint: ``WS /api/runs/{run_id}/stream``

On connect:
  - Authenticate via ``Sec-WebSocket-Protocol: bearer.<jwt>`` (browser
    upgrade-friendly) or ``?token=<jwt>`` query param. Bad/missing token
    closes with WS code 4401 (custom — outside the IANA range,
    per-app convention from API_CONTRACT.md §10).
  - Confirm the run exists and belongs to the authenticated user; else
    close 4404.
  - Send a ``snapshot`` frame containing the full Run shape (§4).
  - Subscribe to the per-run pub/sub bus (``backend.workflows.events``);
    fan-out queue is created there.

While connected:
  - Forward every frame the engine publishes (``step_update``,
    ``run_update``, ``approval_requested``) verbatim as JSON.
  - Send ``{"type": "ping"}`` every 30s; client SHOULD respond
    ``{"type": "pong"}`` (we don't enforce — the ping itself is the
    liveness probe; if the socket is dead, the send raises).
  - On a ``run_update`` whose status is terminal (``succeeded`` |
    ``failed`` | ``cancelled``), close cleanly with WS code 1000.

Design notes:
  - The WS endpoint never writes to the DB. It only reads to build
    the initial snapshot, then relays events from the bus.
  - Subscribers pull from an ``asyncio.Queue`` provided by the bus;
    fan-out is non-blocking (slow consumer drops events). The DB row
    remains source of truth — the WS is decorative.
  - We use ``asyncio.wait`` over the queue.get + a 30s timeout so
    the ping fires even on idle runs.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Optional

from fastapi import APIRouter, Query, WebSocket
from sqlalchemy.orm import Session

from backend.auth.jwt_handler import get_user_id_from_token
from backend.database import SessionLocal
from backend.models import Workflow, WorkflowRun
from backend.routers.runs import _to_run_out
from backend.workflows.events import RUN_BUS

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["Agents"])


# Custom WS close codes (outside the IANA reserved range; we own 4xxx).
_WS_CLOSE_UNAUTHENTICATED = 4401
_WS_CLOSE_NOT_FOUND = 4404

# Idle ping cadence per API_CONTRACT.md §10.
_PING_INTERVAL_SECONDS = 30.0

# Terminal run statuses that trigger a clean 1000 close.
_TERMINAL_STATUSES = {"succeeded", "failed", "cancelled"}


def _extract_token(websocket: WebSocket, query_token: Optional[str]) -> Optional[str]:
    """Pull the bearer token from either the ``Sec-WebSocket-Protocol``
    upgrade header (form: ``bearer.<jwt>``) or the ``?token=`` query.

    Browsers can't set arbitrary headers on WS upgrades, so the
    subprotocol channel is the standard escape hatch (matches
    Kubernetes/JupyterHub conventions). We accept either; query is
    handy for curl + tests.
    """
    if query_token:
        return query_token
    proto_header = websocket.headers.get("sec-websocket-protocol", "")
    for raw in proto_header.split(","):
        item = raw.strip()
        if item.startswith("bearer."):
            return item[len("bearer."):]
    return None


def _accept_subprotocol(websocket: WebSocket) -> Optional[str]:
    """If the client offered ``bearer.<jwt>``, echo it back as the
    accepted subprotocol so the upgrade succeeds. Otherwise return
    None and accept without one."""
    proto_header = websocket.headers.get("sec-websocket-protocol", "")
    for raw in proto_header.split(","):
        item = raw.strip()
        if item.startswith("bearer."):
            return item
    return None


def _load_run(db: Session, run_id: str, user_id: int) -> Optional[WorkflowRun]:
    """Fetch a run row and assert ownership via its parent workflow.
    Returns None on miss-or-not-yours (caller closes 4404)."""
    return (
        db.query(WorkflowRun)
        .join(Workflow, Workflow.id == WorkflowRun.workflow_id)
        .filter(WorkflowRun.id == run_id, Workflow.user_id == user_id)
        .first()
    )


def _run_status_value(run: WorkflowRun) -> str:
    raw = run.status
    return raw.value if hasattr(raw, "value") else str(raw)


def _is_terminal_status(status: str) -> bool:
    return status in _TERMINAL_STATUSES


@router.websocket("/runs/{run_id}/stream")
async def run_stream(
    websocket: WebSocket,
    run_id: str,
    token: Optional[str] = Query(default=None),
) -> None:
    """WS endpoint per API_CONTRACT.md §10. See module docstring."""
    bearer = _extract_token(websocket, token)
    if not bearer:
        # Reject the upgrade BEFORE accept(): clients see this as a
        # failed handshake, not a connection close. WS spec says a
        # 4xxx close after accept is the right channel for app-level
        # auth failures, so we accept-then-close to surface the code.
        await websocket.accept()
        await websocket.close(
            code=_WS_CLOSE_UNAUTHENTICATED, reason="missing token",
        )
        return

    user_id = get_user_id_from_token(bearer)
    if not user_id:
        await websocket.accept()
        await websocket.close(
            code=_WS_CLOSE_UNAUTHENTICATED, reason="invalid token",
        )
        return

    # Build snapshot. Open a sync session for the read; close before
    # we go into the long-lived send loop (no need to hold a DB
    # connection idle for the WS lifetime).
    db = SessionLocal()
    try:
        run = _load_run(db, run_id, user_id)
        if run is None:
            await websocket.accept()
            await websocket.close(
                code=_WS_CLOSE_NOT_FOUND, reason="run not found",
            )
            return
        snapshot_run = _to_run_out(db, run).model_dump(mode="json")
        initial_status = _run_status_value(run)
    finally:
        db.close()

    subprotocol = _accept_subprotocol(websocket)
    if subprotocol is not None:
        await websocket.accept(subprotocol=subprotocol)
    else:
        await websocket.accept()

    # Subscribe BEFORE sending the snapshot so we don't miss any
    # frames the engine publishes between the snapshot read and the
    # subscription registration.
    queue = await RUN_BUS.subscribe(run_id)
    try:
        await websocket.send_json({"type": "snapshot", "run": snapshot_run})

        # If the run already finished before we connected, close
        # cleanly. The frontend will read the snapshot's terminal
        # status and stop reconnecting.
        if _is_terminal_status(initial_status):
            await websocket.close(code=1000)
            return

        await _relay_loop(websocket, run_id, queue)
    finally:
        await RUN_BUS.unsubscribe(run_id, queue)


async def _relay_loop(
    websocket: WebSocket,
    run_id: str,
    queue: "asyncio.Queue[dict[str, Any]]",
) -> None:
    """Forward bus events to the socket; emit a 30s ping on idle.

    Exits cleanly on:
      - terminal ``run_update`` (sends frame, then closes 1000)
      - client disconnect (any send/recv raises)
    """
    while True:
        try:
            event = await asyncio.wait_for(
                queue.get(), timeout=_PING_INTERVAL_SECONDS,
            )
        except asyncio.TimeoutError:
            try:
                await websocket.send_json({"type": "ping"})
            except Exception:
                # Socket dead — bail out; finally-block unsubscribes.
                return
            continue
        except asyncio.CancelledError:
            return

        try:
            await websocket.send_json(event)
        except Exception:
            # Socket closed mid-send. Caller's finally will unsubscribe.
            return

        # Terminal run_update → server closes 1000 (API_CONTRACT.md §10).
        if (
            isinstance(event, dict)
            and event.get("type") == "run_update"
            and isinstance(event.get("status"), str)
            and _is_terminal_status(event["status"])
        ):
            try:
                await websocket.close(code=1000)
            except Exception:
                pass
            logger.debug("run_stream closed 1000 for run %s", run_id)
            return
