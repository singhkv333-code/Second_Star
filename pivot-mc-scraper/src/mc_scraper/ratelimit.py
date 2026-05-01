"""Token-bucket rate limiters.

Two implementations:
  * `AsyncTokenBucket` — pure-asyncio, in-process. No DB round-trips. The
    typical worker loop should use this and rely on N workers each capping
    themselves to ``total_rate / N``.
  * `PgTokenBucket` — kept for cross-process coordination, but in practice
    the per-row SELECT-FOR-UPDATE serializes when many concurrent fetchers
    contend for it, so prefer AsyncTokenBucket unless you genuinely need
    DB-coordinated throttle.
"""
from __future__ import annotations

import asyncio
import time

import asyncpg


class AsyncTokenBucket:
    """Lock-free in-process token bucket. Refill is computed lazily on each
    acquire, so the bucket consumes ~zero CPU when idle."""

    def __init__(self, refill_per_sec: float = 10.0, capacity: float = 10.0):
        self._refill = refill_per_sec
        self._capacity = capacity
        self._tokens = capacity
        self._last = time.monotonic()
        self._lock = asyncio.Lock()

    async def configure(self) -> None:
        # Kept for API parity with PgTokenBucket.
        return None

    async def acquire(self) -> None:
        while True:
            async with self._lock:
                now = time.monotonic()
                self._tokens = min(
                    self._capacity, self._tokens + (now - self._last) * self._refill
                )
                self._last = now
                if self._tokens >= 1.0:
                    self._tokens -= 1.0
                    return
                deficit = 1.0 - self._tokens
                wait = deficit / max(self._refill, 0.001)
            await asyncio.sleep(min(wait, 1.0))


class PgTokenBucket:
    def __init__(self, pool: asyncpg.Pool, refill_per_sec: float = 10.0, capacity: float = 10.0):
        self._pool = pool
        self._refill = refill_per_sec
        self._capacity = capacity

    async def configure(self) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE mc.rate_bucket
                   SET capacity = $1, refill_per_sec = $2,
                       tokens = LEAST(tokens, $1), updated_at = now()
                 WHERE id = 1
                """,
                self._capacity, self._refill,
            )

    async def acquire(self) -> None:
        """Block until at least one token is available, then consume it."""
        while True:
            wait = await self._try_acquire()
            if wait <= 0:
                return
            await asyncio.sleep(min(wait, 1.0))

    async def _try_acquire(self) -> float:
        """Returns 0 on success, otherwise seconds to wait before retrying."""
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                row = await conn.fetchrow(
                    "SELECT tokens, capacity, refill_per_sec, "
                    "EXTRACT(EPOCH FROM (now() - updated_at))::float8 AS elapsed "
                    "FROM mc.rate_bucket WHERE id = 1 FOR UPDATE"
                )
                tokens = float(row["tokens"])
                cap = float(row["capacity"])
                refill = float(row["refill_per_sec"])
                elapsed = float(row["elapsed"]) or 0.0
                tokens = min(cap, tokens + elapsed * refill)
                if tokens >= 1.0:
                    tokens -= 1.0
                    await conn.execute(
                        "UPDATE mc.rate_bucket SET tokens = $1, updated_at = now() WHERE id = 1",
                        tokens,
                    )
                    return 0.0
                # Persist the refill we just computed so other workers see progress.
                await conn.execute(
                    "UPDATE mc.rate_bucket SET tokens = $1, updated_at = now() WHERE id = 1",
                    tokens,
                )
                deficit = 1.0 - tokens
                return deficit / max(refill, 0.001)
