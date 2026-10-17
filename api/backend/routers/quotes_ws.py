"""WebSocket: live market ticks (PHASE2_CONTRACT.md §Layer 3).

Endpoint: ``WS /api/ws/quotes?symbols=RELIANCE,TCS,NIFTY_50``

Auth: bearer-in-subprotocol (``Sec-WebSocket-Protocol: bearer.<jwt>``)
or ``?token=<jwt>`` query — same pattern as
``backend/routers/run_stream.py``. We deliberately duplicate the small
helpers rather than refactor that file.

Lifecycle:
    1. Accept the WS upgrade only after JWT auth passes.
    2. Read the initial ``?symbols=`` list (deduped + normalised),
       hot-add any not in the ticker universe.
    3. Send ``hello`` with the accepted subscription set, then replay
       any cached ticks immediately so the UI never sits blank.
    4. Subscribe to the Redis ``ticks`` channel; relay any message whose
       ``symbol`` is in this connection's subscription set.
    5. ``ping`` every 30s on idle. Client may send back ``pong``.
    6. Accept inbound ``subscribe`` / ``unsubscribe`` mutations.
    7. On disconnect, decrement subscriber counts on the manager so
       symbols that were hot-added solely for this connection get
       released.

We use a single Redis pub/sub channel — the contract documents the
choice. Each WS handler runs its own pub/sub subscription via a thread
executor (the redis-py client's `listen()` is blocking).
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, Optional

from fastapi import APIRouter, Query, WebSocket
from starlette.websockets import WebSocketDisconnect, WebSocketState

from backend.auth.jwt_handler import get_user_id_from_token
from backend.cache import get_redis
from backend.kite.ticker import (
    cache_key,
    get_ticker_manager,
    normalize_symbol,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["Markets"])


_WS_CLOSE_UNAUTHENTICATED = 4401
_WS_CLOSE_TOO_MANY = 4413
_PING_INTERVAL_SECONDS = 30.0
_MAX_SYMBOLS_PER_CONN = 100
_PUBSUB_CHANNEL = "ticks"


# ─────────────────────────────────────────────────────────────────────
# Helpers — copied (intentionally) from run_stream.py to keep the auth
# pattern aligned without coupling the two routers.
# ─────────────────────────────────────────────────────────────────────


def _extract_token(websocket: WebSocket, query_token: Optional[str]) -> Optional[str]:
    if query_token:
        return query_token
    proto_header = websocket.headers.get("sec-websocket-protocol", "")
    for raw in proto_header.split(","):
        item = raw.strip()
        if item.startswith("bearer."):
            return item[len("bearer."):]
    return None


def _accept_subprotocol(websocket: WebSocket) -> Optional[str]:
    proto_header = websocket.headers.get("sec-websocket-protocol", "")
    for raw in proto_header.split(","):
        item = raw.strip()
        if item.startswith("bearer."):
            return item
    return None


def _parse_symbols(raw: Optional[str]) -> list[str]:
    if not raw:
        return []
    out: list[str] = []
    seen: set[str] = set()
    for item in raw.split(","):
        norm = normalize_symbol(item)
        if not norm or norm in seen:
            continue
        seen.add(norm)
        out.append(norm)
    return out


def _replay_cached_tick(symbol: str) -> Optional[dict[str, Any]]:
    """Read the latest cached tick for ``symbol`` if present."""
    try:
        rc = get_redis()
        raw = rc.get(cache_key(symbol))
        if raw is None:
            return None
        if isinstance(raw, (bytes, bytearray)):
            raw = raw.decode()
        data = json.loads(raw)
        return _payload_to_tick_frame(data)
    except Exception:
        return None


def _payload_to_tick_frame(payload: dict[str, Any]) -> dict[str, Any]:
    """Translate a cache/pub-sub payload into the WS ``tick`` frame."""
    return {
        "type": "tick",
        "symbol": payload.get("symbol"),
        "ltp": payload.get("ltp"),
        "change_pct": payload.get("change_pct"),
        "ts": payload.get("ts"),
    }


# ─────────────────────────────────────────────────────────────────────
# Connection state
# ─────────────────────────────────────────────────────────────────────


class _ConnState:
    """Per-connection mutable state.

    Holds the symbol subscription set plus a flag for whether we
    successfully hot-added each symbol via the ticker manager. We
    decrement on disconnect so non-seed symbols get freed up.
    """

    __slots__ = ("symbols",)

    def __init__(self) -> None:
        self.symbols: set[str] = set()


# ─────────────────────────────────────────────────────────────────────
# Redis pub/sub listener — wraps blocking listen() on an executor.
# ─────────────────────────────────────────────────────────────────────


async def _redis_listener(
    queue: "asyncio.Queue[dict[str, Any]]",
    stop_event: asyncio.Event,
) -> None:
    """Forward messages from the ``ticks`` channel into ``queue``.

    Runs in the asyncio loop; the blocking redis call is shipped to a
    default executor so we don't stall the loop.
    """
    rc = get_redis()
    if not hasattr(rc, "pubsub"):
        # MockRedis path — no pub/sub. The handler still works via
        # explicit calls to ``synthetic_publish_for_tests`` (test-only)
        # or just relays the cache. We simply wait for stop.
        await stop_event.wait()
        return

    loop = asyncio.get_running_loop()
    pubsub = rc.pubsub(ignore_subscribe_messages=True)
    try:
        await loop.run_in_executor(None, pubsub.subscribe, _PUBSUB_CHANNEL)

        def _next_message() -> Optional[dict[str, Any]]:
            return pubsub.get_message(timeout=1.0)

        while not stop_event.is_set():
            msg = await loop.run_in_executor(None, _next_message)
            if not msg:
                continue
            if msg.get("type") != "message":
                continue
            data = msg.get("data")
            if isinstance(data, (bytes, bytearray)):
                data = data.decode()
            if not isinstance(data, str):
                continue
            try:
                payload = json.loads(data)
            except json.JSONDecodeError:
                continue
            try:
                queue.put_nowait(payload)
            except asyncio.QueueFull:
                # Slow consumer — drop. The cache replay will catch up.
                pass
    except Exception:
        logger.exception("redis listener crashed; closing")
    finally:
        try:
            await loop.run_in_executor(None, pubsub.close)
        except Exception:
            pass


# ─────────────────────────────────────────────────────────────────────
# Inbound message handler
# ─────────────────────────────────────────────────────────────────────


async def _handle_inbound(
    websocket: WebSocket,
    state: _ConnState,
) -> None:
    """Read client → server messages until disconnect."""
    while True:
        try:
            raw = await websocket.receive_text()
        except WebSocketDisconnect:
            return
        except RuntimeError:
            # Receive after close
            return
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if not isinstance(msg, dict):
            continue
        kind = msg.get("type")
        if kind == "subscribe":
            symbols = _coerce_symbols(msg.get("symbols"))
            if not symbols:
                continue
            await _apply_subscribe(websocket, state, symbols)
        elif kind == "unsubscribe":
            symbols = _coerce_symbols(msg.get("symbols"))
            if not symbols:
                continue
            await _apply_unsubscribe(state, symbols)
        elif kind == "pong":
            continue
        else:
            # Unknown message types are silently ignored; loose
            # forward-compat with future client revs.
            continue


def _coerce_symbols(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    out: list[str] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, str):
            continue
        norm = normalize_symbol(item)
        if not norm or norm in seen:
            continue
        seen.add(norm)
        out.append(norm)
    return out


async def _apply_subscribe(
    websocket: WebSocket,
    state: _ConnState,
    symbols: list[str],
) -> None:
    """Honour a subscribe message; enforce the 100-symbol cap."""
    accepted: list[str] = []
    for sym in symbols:
        if sym in state.symbols:
            continue
        if len(state.symbols) >= _MAX_SYMBOLS_PER_CONN:
            await _safe_send(websocket, {"type": "error", "code": "too_many_symbols"})
            break
        state.symbols.add(sym)
        accepted.append(sym)
    if accepted:
        try:
            get_ticker_manager().add_symbols(accepted)
        except Exception:
            logger.exception("ticker.add_symbols raised in WS handler")
        # Replay cached ticks for the newly-added symbols.
        for sym in accepted:
            frame = _replay_cached_tick(sym)
            if frame is not None:
                await _safe_send(websocket, frame)


async def _apply_unsubscribe(
    state: _ConnState,
    symbols: list[str],
) -> None:
    to_drop = [s for s in symbols if s in state.symbols]
    for sym in to_drop:
        state.symbols.discard(sym)
    if to_drop:
        try:
            get_ticker_manager().remove_symbols(to_drop)
        except Exception:
            logger.exception("ticker.remove_symbols raised in WS handler")


async def _safe_send(websocket: WebSocket, payload: dict[str, Any]) -> bool:
    """Send a JSON frame; return False if the socket is dead."""
    if websocket.client_state != WebSocketState.CONNECTED:
        return False
    try:
        await websocket.send_json(payload)
        return True
    except Exception:
        return False


# ─────────────────────────────────────────────────────────────────────
# Endpoint
# ─────────────────────────────────────────────────────────────────────


@router.websocket("/ws/quotes")
async def quotes_ws(
    websocket: WebSocket,
    symbols: Optional[str] = Query(default=None),
    token: Optional[str] = Query(default=None),
) -> None:
    bearer = _extract_token(websocket, token)
    if not bearer:
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

    subprotocol = _accept_subprotocol(websocket)
    if subprotocol is not None:
        await websocket.accept(subprotocol=subprotocol)
    else:
        await websocket.accept()

    state = _ConnState()
    requested = _parse_symbols(symbols)
    over_cap = len(requested) > _MAX_SYMBOLS_PER_CONN
    if over_cap:
        await _safe_send(websocket, {"type": "error", "code": "too_many_symbols"})
        requested = requested[:_MAX_SYMBOLS_PER_CONN]

    if requested:
        state.symbols.update(requested)
        try:
            get_ticker_manager().add_symbols(requested)
        except Exception:
            logger.exception("ticker.add_symbols raised at WS hello")

    await _safe_send(
        websocket,
        {"type": "hello", "subscribed": sorted(state.symbols)},
    )

    # Replay cached ticks immediately.
    for sym in sorted(state.symbols):
        frame = _replay_cached_tick(sym)
        if frame is not None:
            await _safe_send(websocket, frame)

    queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=1000)
    stop_event = asyncio.Event()

    listener_task = asyncio.create_task(_redis_listener(queue, stop_event))
    inbound_task = asyncio.create_task(_handle_inbound(websocket, state))

    try:
        await _relay_loop(websocket, state, queue, inbound_task)
    finally:
        stop_event.set()
        inbound_task.cancel()
        listener_task.cancel()
        # Wait briefly for cleanup; swallow CancelledError.
        for t in (inbound_task, listener_task):
            try:
                await asyncio.wait_for(t, timeout=2.0)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass
            except Exception:
                logger.debug("cleanup task raised", exc_info=True)
        # Release symbol refcounts.
        if state.symbols:
            try:
                get_ticker_manager().remove_symbols(list(state.symbols))
            except Exception:
                logger.exception("ticker.remove_symbols failed on disconnect")
        # Close if still open
        if websocket.client_state == WebSocketState.CONNECTED:
            try:
                await websocket.close()
            except Exception:
                pass


async def _relay_loop(
    websocket: WebSocket,
    state: _ConnState,
    queue: "asyncio.Queue[dict[str, Any]]",
    inbound_task: "asyncio.Task[None]",
) -> None:
    """Forward filtered ticks; emit a 30s ping on idle."""
    while True:
        if inbound_task.done():
            return
        try:
            payload = await asyncio.wait_for(
                queue.get(), timeout=_PING_INTERVAL_SECONDS,
            )
        except asyncio.TimeoutError:
            if not await _safe_send(
                websocket, {"type": "ping", "ts": int(time.time())},
            ):
                return
            continue
        except asyncio.CancelledError:
            return

        sym = payload.get("symbol") if isinstance(payload, dict) else None
        if not isinstance(sym, str):
            continue
        if normalize_symbol(sym) not in state.symbols:
            continue
        if not await _safe_send(websocket, _payload_to_tick_frame(payload)):
            return
