"""In-memory ring-buffer tracing for chat turns.

When chats fail, the only useful artefact is the full message chain
plus what the loop did at each step. This module records both, keyed
by conversation id, capped per-conv so memory stays bounded.

API:
  - `start_turn(conv_id, message)` → returns a TurnTrace handle
  - `trace.event(name, **fields)` → append an event
  - `trace.end()` → close the turn

Reads:
  - `get_recent_turns(conv_id, limit)` → most recent N turns
  - `get_turn(conv_id, turn_idx)` → one specific turn

Surfaced via `routers/admin.py::GET /admin/conv/{id}/trace`.

Not persisted (in-memory only). When the process restarts the trace
buffer is empty; that's the right tradeoff for v1 — production
observability lives in structured logs which CAN be tailed.
"""
from __future__ import annotations

import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Optional


_MAX_TURNS_PER_CONV = 25
_MAX_EVENTS_PER_TURN = 100


@dataclass
class TraceEvent:
    name: str
    timestamp_ms: int                      # epoch ms
    elapsed_ms: int                        # since turn start
    fields: dict[str, Any] = field(default_factory=dict)


@dataclass
class TurnTrace:
    turn_id: str
    conv_id: str
    user_message: str
    started_at_ms: int
    ended_at_ms: Optional[int] = None
    events: list[TraceEvent] = field(default_factory=list)

    def event(self, name: str, **fields: Any) -> None:
        if len(self.events) >= _MAX_EVENTS_PER_TURN:
            return
        now = int(time.time() * 1000)
        self.events.append(TraceEvent(
            name=name,
            timestamp_ms=now,
            elapsed_ms=now - self.started_at_ms,
            fields=fields,
        ))

    def end(self) -> None:
        if self.ended_at_ms is None:
            self.ended_at_ms = int(time.time() * 1000)

    def to_dict(self) -> dict[str, Any]:
        return {
            "turn_id": self.turn_id,
            "conv_id": self.conv_id,
            "user_message": self.user_message,
            "started_at_ms": self.started_at_ms,
            "ended_at_ms": self.ended_at_ms,
            "duration_ms": (
                (self.ended_at_ms - self.started_at_ms)
                if self.ended_at_ms is not None else None
            ),
            "events": [
                {
                    "name": e.name,
                    "elapsed_ms": e.elapsed_ms,
                    "fields": e.fields,
                }
                for e in self.events
            ],
        }


# Per-conv ring buffer of recent turns. Lock guards mutations only;
# reads under the GIL are atomic for our access patterns.
_TURNS: dict[str, deque[TurnTrace]] = {}
_LOCK = threading.Lock()


def start_turn(conv_id: str, user_message: str) -> TurnTrace:
    """Begin a new trace for this turn. Returns the trace handle the
    caller writes events into."""
    trace = TurnTrace(
        turn_id=str(uuid.uuid4()),
        conv_id=conv_id,
        user_message=user_message[:500],
        started_at_ms=int(time.time() * 1000),
    )
    with _LOCK:
        bucket = _TURNS.setdefault(conv_id, deque(maxlen=_MAX_TURNS_PER_CONV))
        bucket.append(trace)
    return trace


def get_recent_turns(conv_id: str, limit: int = 10) -> list[TurnTrace]:
    bucket = _TURNS.get(conv_id) or deque()
    return list(bucket)[-limit:]


def get_turn(conv_id: str, turn_id: str) -> Optional[TurnTrace]:
    for t in _TURNS.get(conv_id) or ():
        if t.turn_id == turn_id:
            return t
    return None


def reset() -> None:
    """Test helper. Wipes the buffer."""
    with _LOCK:
        _TURNS.clear()
