"""Redis-backed login rate limiter / account-lockout for beta auth.

Per (normalized-email, client-ip) we track failed-login attempts. After
``MAX_FAILURES`` failures inside ``WINDOW_SECONDS`` the pair is locked
for ``LOCK_SECONDS`` and ``is_locked()`` returns the remaining seconds
the caller hands back as ``Retry-After``.

Why both email AND ip? Email-only buckets let one attacker lock a victim
out of their own account by spraying bad passwords; IP-only buckets let
a botnet bypass the lock. Pairing them keeps the noise localised: a
single bad actor at one IP can't deny service to the real user, who
will login from a different IP and bypass the lock.

Redis-only by design — counters expire on their own, no sweeper job
needed. When Redis is unavailable the cache layer falls back to
MockRedis (in-memory), which still works for a single-process dev box;
production deploys a real Redis so the counters survive worker restarts.
"""
from __future__ import annotations

import hashlib
import logging
from typing import Tuple

from backend.cache import redis_client

logger = logging.getLogger(__name__)

# Tunables. Picked to be strict enough that a 5-bad-password spray is the
# attacker's last chance for ~15 min, lax enough that a typo-prone real
# user has headroom.
MAX_FAILURES = 5
WINDOW_SECONDS = 15 * 60  # rolling window the counter is valid for
LOCK_SECONDS = 15 * 60    # how long the (email, ip) pair stays locked


def _bucket_key(email: str, ip: str) -> str:
    """Stable, length-bounded Redis key for the (email, ip) bucket.

    Hashing the (lowercased) email + ip keeps the key short and avoids
    leaking the raw email into Redis keyspace (which often shows up in
    monitoring / SCAN output)."""
    norm = f"{(email or '').strip().lower()}|{(ip or '').strip()}"
    digest = hashlib.sha1(norm.encode("utf-8")).hexdigest()
    return f"auth:login_fail:{digest}"


def _lock_key(email: str, ip: str) -> str:
    """Companion lock key — set only when the failure counter trips the
    threshold. Reading TTL on this key gives the Retry-After seconds."""
    norm = f"{(email or '').strip().lower()}|{(ip or '').strip()}"
    digest = hashlib.sha1(norm.encode("utf-8")).hexdigest()
    return f"auth:login_lock:{digest}"


def _to_int(value: object) -> int:
    """Redis returns bytes (real client) or str/int (MockRedis). Normalise
    to int — anything we can't parse counts as 0 (fresh bucket)."""
    if value is None:
        return 0
    if isinstance(value, int):
        return value
    if isinstance(value, bytes):
        try:
            return int(value.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            return 0
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return 0
    return 0


def is_locked(email: str, ip: str) -> Tuple[bool, int]:
    """Returns (locked, retry_after_seconds). ``retry_after_seconds`` is 0
    when not locked. Best-effort: if Redis is down we fail OPEN (return
    not-locked) so a Redis outage doesn't take auth down with it."""
    try:
        key = _lock_key(email, ip)
        if not redis_client.exists(key):
            return False, 0
        # ttl() returns seconds-remaining for a key with TTL; -1 = no TTL,
        # -2 = key missing. Both MockRedis and real redis expose ttl().
        ttl_fn = getattr(redis_client, "ttl", None)
        if ttl_fn is None:
            # Fallback: assume the configured lock window.
            return True, LOCK_SECONDS
        ttl = ttl_fn(key)
        try:
            ttl_int = int(ttl)
        except (TypeError, ValueError):
            ttl_int = LOCK_SECONDS
        if ttl_int <= 0:
            # Key without TTL or already expiring — treat as a short residual.
            return True, max(1, LOCK_SECONDS)
        return True, ttl_int
    except Exception as e:  # noqa: BLE001 — fail open on Redis errors
        logger.warning("rate_limit.is_locked redis error: %s", e)
        return False, 0


def record_failure(email: str, ip: str) -> int:
    """Increment the failure counter for this (email, ip) pair. Returns
    the new counter value. When the counter crosses ``MAX_FAILURES`` we
    also set the lock key with TTL = ``LOCK_SECONDS``.

    Best-effort: a Redis hiccup returns 0 (caller continues, no lockout)."""
    try:
        bucket = _bucket_key(email, ip)
        incr_fn = getattr(redis_client, "incr", None)
        if incr_fn is None:
            # MockRedis doesn't define incr; emulate with get/set.
            current = _to_int(redis_client.get(bucket))
            new_val = current + 1
            redis_client.set(bucket, str(new_val), ex=WINDOW_SECONDS)
        else:
            raw = incr_fn(bucket)
            new_val = _to_int(raw)
            # First failure: stamp the window TTL so the counter decays.
            if new_val == 1:
                expire_fn = getattr(redis_client, "expire", None)
                if expire_fn is not None:
                    expire_fn(bucket, WINDOW_SECONDS)
                else:
                    redis_client.set(bucket, str(new_val), ex=WINDOW_SECONDS)
        if new_val >= MAX_FAILURES:
            redis_client.set(_lock_key(email, ip), "1", ex=LOCK_SECONDS)
        return new_val
    except Exception as e:  # noqa: BLE001
        logger.warning("rate_limit.record_failure redis error: %s", e)
        return 0


def clear_failures(email: str, ip: str) -> None:
    """Reset the (email, ip) counter + lock — call on a successful login
    so a typo-prone real user doesn't accumulate failures across days."""
    try:
        redis_client.delete(_bucket_key(email, ip))
        redis_client.delete(_lock_key(email, ip))
    except Exception as e:  # noqa: BLE001
        logger.warning("rate_limit.clear_failures redis error: %s", e)
