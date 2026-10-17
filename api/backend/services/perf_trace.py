"""Flag-gated per-call tracing of DB queries and Redis commands.

Set ``PIVOT_PERF_TRACE=/path/perf.jsonl`` before boot to enable. Each SQL
statement and each Redis command then emits one JSONL record:

    {ts_ms, kind: "sql"|"redis"|"sql_connect", db, op, detail, dur_ms,
     caller, conv_id}

- ``caller``  — closest application stack frame (module:line function)
  above sqlalchemy/redis/this module, so every round-trip is attributable
  to the service that issued it.
- ``conv_id`` — the ambient chat conversation id (turn_context contextvar)
  when the call happens inside a chat turn; null otherwise.

Purpose: the latency dissection of chat turns. Postgres AND Redis both
live on Azure Central India (~62 ms RTT), so *call count × RTT* — not
query execution — is the expected cost driver; this file measures it
instead of guessing. Disabled (the default) it costs one env check at
import. Like llm/_trace.py, records carry statement/key previews —
dev-only, don't enable in prod.

Leaf module: imports stdlib + turn_context only. database.py and cache.py
call ``install_sqlalchemy(engine, db=...)`` / ``wrap_redis(client)`` at
construction time; both are no-ops when the flag is off.
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
from typing import Any, Optional

_PATH: Optional[str] = os.environ.get("PIVOT_PERF_TRACE", "").strip() or None
_LOCK = threading.Lock()


def is_enabled() -> bool:
    return _PATH is not None


_SKIP_PREFIXES = (
    "backend.services.perf_trace",
    "backend.database",
    "backend.cache",
    "sqlalchemy",
    "redis",
    "contextlib",
)


def _caller() -> str:
    """First application frame above the driver layers. sys._getframe walk —
    cheaper than inspect.stack() since this runs on EVERY query."""
    try:
        f = sys._getframe(2)
        depth = 0
        while f is not None and depth < 25:
            mod = f.f_globals.get("__name__") or ""
            if mod.startswith("backend.") and not mod.startswith(_SKIP_PREFIXES):
                return f"{mod}:{f.f_lineno} {f.f_code.co_name}"
            f = f.f_back
            depth += 1
    except Exception:
        pass
    return "<non-backend>"


def _conv_id() -> Optional[str]:
    try:
        from backend.services.turn_context import get_conversation_id
        return get_conversation_id()
    except Exception:
        return None


def emit(kind: str, op: str, detail: str, dur_ms: float, *, db: str = "") -> None:
    if _PATH is None:
        return
    rec: dict[str, Any] = {
        "ts_ms": int(time.time() * 1000),
        "kind": kind,
        "db": db,
        "op": op,
        "detail": detail[:220],
        "dur_ms": round(dur_ms, 2),
        "caller": _caller(),
        "conv_id": _conv_id(),
    }
    try:
        line = json.dumps(rec, ensure_ascii=False)
        with _LOCK:
            with open(_PATH, "a", encoding="utf-8") as fh:
                fh.write(line + "\n")
    except Exception:
        pass  # tracing must never break the request path


# ── SQLAlchemy ─────────────────────────────────────────────────────────


def install_sqlalchemy(engine: Any, *, db: str) -> None:
    """Attach per-statement timing to an engine. No-op when disabled."""
    if not is_enabled() or engine is None:
        return
    from sqlalchemy import event

    @event.listens_for(engine, "before_cursor_execute")
    def _before(conn, cursor, statement, parameters, context, executemany):
        context._perf_t0 = time.monotonic()

    @event.listens_for(engine, "after_cursor_execute")
    def _after(conn, cursor, statement, parameters, context, executemany):
        t0 = getattr(context, "_perf_t0", None)
        dur = (time.monotonic() - t0) * 1000 if t0 is not None else -1.0
        stmt = " ".join(statement.split())
        emit("sql", stmt.split(" ", 1)[0].upper(), stmt, dur, db=db)

    @event.listens_for(engine, "connect")
    def _connect(dbapi_conn, conn_record):
        # A fresh DBAPI connection mid-turn means a full TCP+TLS+auth
        # handshake to Azure (hundreds of ms) that no statement timing
        # shows. Duration isn't observable here; the event itself is
        # the signal.
        emit("sql_connect", "CONNECT", "new DBAPI connection", 0.0, db=db)


# ── Redis ──────────────────────────────────────────────────────────────


def wrap_redis(client: Any) -> Any:
    """Wrap a redis-py client's execute_command with timing. Returns the
    same client. No-op when disabled or client is None/Mock."""
    if not is_enabled() or client is None:
        return client
    if getattr(client, "_perf_wrapped", False):
        return client
    orig = client.execute_command

    def timed_execute(*args: Any, **kwargs: Any) -> Any:
        t0 = time.monotonic()
        try:
            return orig(*args, **kwargs)
        finally:
            op = str(args[0]) if args else "?"
            key = str(args[1])[:80] if len(args) > 1 else ""
            emit("redis", op, key, (time.monotonic() - t0) * 1000)

    try:
        client.execute_command = timed_execute
        client._perf_wrapped = True
    except Exception:
        pass
    return client
