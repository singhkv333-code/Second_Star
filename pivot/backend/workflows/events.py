"""In-process pub/sub for live run events.

The engine writes status to the DB FIRST and then publishes to any
subscribers (WebSocket clients) via this module. Persistence-before-
emit per ARCHITECTURE.md §7 invariant 2.

Design:
  - One asyncio.Queue per (run_id, subscriber). Subscribers register on
    WS connect, drain on disconnect.
  - Fan-out is non-blocking: if a subscriber's queue is full (slow
    consumer), the event is dropped for that subscriber. The DB remains
    the source of truth — the WS is decorative.
  - All subscribers see all event types for their run_id; filtering is
    a frontend concern.

Schema of published events mirrors API_CONTRACT.md §10 frames. The
producer side here just forwards opaque dicts; the WS endpoint
serialises them to JSON.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, AsyncIterator, Dict, Set


# WS frame is a JSON object. Concrete frame shapes are defined in
# API_CONTRACT.md §10; we don't enforce them here at the type level
# because the engine emits each shape from a different code site —
# enforcing would require Union types that buy little. The WS endpoint
# performs the json.dumps() at the boundary.
Frame = Dict[str, Any]


logger = logging.getLogger(__name__)


class _RunBus:
    """Per-process bus mapping run_id -> set of subscriber queues."""

    def __init__(self) -> None:
        # key: run_id; value: subscriber queues
        self._subs: Dict[str, Set[asyncio.Queue[Frame]]] = {}
        self._lock: asyncio.Lock = asyncio.Lock()

    async def subscribe(self, run_id: str) -> asyncio.Queue[Frame]:
        """Register a new subscriber queue for `run_id`. Caller must
        invoke `unsubscribe(run_id, queue)` on disconnect to avoid a
        memory leak when long-running runs accumulate dead subscribers."""
        q: asyncio.Queue[Frame] = asyncio.Queue(maxsize=128)
        async with self._lock:
            self._subs.setdefault(run_id, set()).add(q)
        return q

    async def unsubscribe(
        self, run_id: str, q: asyncio.Queue[Frame],
    ) -> None:
        async with self._lock:
            subs = self._subs.get(run_id)
            if subs is None:
                return
            subs.discard(q)
            if not subs:
                self._subs.pop(run_id, None)

    async def publish(self, run_id: str, event: Frame) -> None:
        """Best-effort fan-out. Does NOT raise on slow consumers — the
        DB row already has the new state so a missed frame is a UI
        annoyance, not a correctness bug."""
        async with self._lock:
            subs = list(self._subs.get(run_id, ()))
        for q in subs:
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                logger.warning(
                    "run_stream subscriber queue full for run %s; dropping",
                    run_id,
                )

    def publish_threadsafe(self, run_id: str, event: Frame) -> None:
        """Sync publish helper for callers running outside the loop
        (e.g. the engine's threadpool wrappers around sync DB code).
        Schedules the publish on the running loop without awaiting."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # No running loop — caller is in a pure-sync context and
            # there are no live subscribers it could reach anyway.
            return
        loop.create_task(self.publish(run_id, event))


# Module-level singleton. There is one bus per Python process. Multi-
# process deployments would need Redis pub/sub here in v2.
RUN_BUS = _RunBus()


async def stream(run_id: str) -> AsyncIterator[Frame]:
    """Async iterator over events for a single run. Caller must wrap in
    try/finally to guarantee unsubscribe — see run_stream.py."""
    q = await RUN_BUS.subscribe(run_id)
    try:
        while True:
            yield await q.get()
    finally:
        await RUN_BUS.unsubscribe(run_id, q)
