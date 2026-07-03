"""Redis-backed JWT revocation list for the beta auth surface.

We keep JWTs stateless (no DB round-trip on every request) and gain
explicit logout by storing only the ``jti`` of revoked tokens, with a
TTL equal to the token's own remaining lifetime so the revocation entry
expires together with the token. Worst case (Redis flushed) is that an
unexpired token starts validating again — bounded by the access-token
expiry, never longer.

Helpers are intentionally tiny: ``revoke(jti, ttl)`` writes the key;
``is_revoked(jti)`` reads it. The ``_deps.require_user`` dependency wires
the read into every authenticated request.
"""
from __future__ import annotations

import logging

from backend.cache import redis_client

logger = logging.getLogger(__name__)


def _key(jti: str) -> str:
    return f"auth:revoked:{jti}"


def revoke(jti: str, ttl_seconds: int) -> bool:
    """Mark a token's jti as revoked for ``ttl_seconds``. Returns True if
    the write succeeded. ``ttl_seconds<=0`` is a no-op (token would be
    expired anyway). Best-effort: a Redis error logs + returns False so
    logout still appears to succeed from the user's perspective."""
    if not jti or ttl_seconds <= 0:
        return False
    try:
        redis_client.set(_key(jti), "1", ex=ttl_seconds)
        return True
    except Exception as e:  # noqa: BLE001
        logger.warning("revocation.revoke redis error: %s", e)
        return False


def is_revoked(jti: str | None) -> bool:
    """Returns True iff this jti is on the revocation list. None / empty
    jti is treated as "not revocable" (legacy tokens without jti) and
    must NOT be rejected here — that decision belongs to the caller."""
    if not jti:
        return False
    try:
        return bool(redis_client.exists(_key(jti)))
    except Exception as e:  # noqa: BLE001
        logger.warning("revocation.is_revoked redis error: %s", e)
        return False
