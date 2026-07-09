"""Redis-backed fixed-window rate limiter for expensive / abusable endpoints.

Complements ``auth/rate_limit.py`` (which is login-specific). This is a general
per-principal throttle used as a FastAPI dependency:

    @router.post("/chat", dependencies=[Depends(rate_limit("chat", 30, 60))])

The principal is the authenticated user id when a valid bearer token is present,
else the client IP — so authed users get a per-user budget and anonymous
callers (e.g. self-registration) are throttled per IP. Fixed-window counter via
Redis INCR + EXPIRE; on any Redis error it FAILS OPEN (allows the request) so a
cache outage never takes the API down — the same posture as the login limiter.
"""
from __future__ import annotations

import logging
import os
from typing import Callable, Optional

from fastapi import Depends, Header, Request

from backend.cache import redis_client
from backend.routers._errors import rate_limited

logger = logging.getLogger(__name__)


def _client_ip(request: Request) -> str:
    """Best-effort client IP. Honour the first hop of X-Forwarded-For when
    present (the app runs behind a proxy in prod), else the socket peer."""
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _principal(request: Request, authorization: Optional[str]) -> str:
    """`u:<id>` for an authenticated caller, else `ip:<addr>`."""
    if authorization and authorization.startswith("Bearer "):
        # Import locally to avoid a heavy import at module load.
        from backend.auth.jwt_handler import get_user_id_from_token

        uid = get_user_id_from_token(authorization.replace("Bearer ", "", 1))
        if uid:
            return f"u:{uid}"
    return f"ip:{_client_ip(request)}"


def _hit(key: str, window_s: int) -> int:
    """Increment the window counter, stamping a TTL on first hit. Returns the
    new count, or 0 on any Redis error (fail-open)."""
    # Test-harness escape (2026-07-10): the suite registers a fresh user
    # per test, which trips the 5/hour register throttle and cascades 429s
    # through every fixture-dependent test. Set ONLY by tests/conftest.py;
    # same fail-open return value as a Redis outage, prod posture unchanged.
    if os.getenv("PIVOT_DISABLE_THROTTLE") == "1":
        return 0
    try:
        incr = getattr(redis_client, "incr", None)
        if incr is None:  # MockRedis path
            cur = redis_client.get(key)
            n = (int(cur) if cur is not None else 0) + 1
            redis_client.set(key, str(n), ex=window_s)
            return n
        n = int(incr(key))
        if n == 1:
            expire = getattr(redis_client, "expire", None)
            if expire is not None:
                expire(key, window_s)
            else:
                redis_client.set(key, str(n), ex=window_s)
        return n
    except Exception as e:  # noqa: BLE001 — fail open on Redis errors
        logger.warning("throttle redis error on %s: %s", key, e)
        return 0


def rate_limit(bucket: str, limit: int, window_s: int) -> Callable:
    """FastAPI dependency: allow at most ``limit`` requests per ``window_s``
    seconds per principal for this ``bucket``. 429 with Retry-After on excess."""

    def dependency(
        request: Request,
        authorization: Optional[str] = Header(default=None),
    ) -> None:
        principal = _principal(request, authorization)
        key = f"rl:{bucket}:{principal}"
        count = _hit(key, window_s)
        if count > limit:
            raise rate_limited(
                f"Too many requests — try again in about {window_s} seconds."
            )

    return dependency
