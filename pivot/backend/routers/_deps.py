"""Shared FastAPI dependencies for the Agent System routers.

Single source of truth for the bearer-token auth check. Every router
under the Agent System surface (workflows, runs, approvals, webhooks
[unauth], run_stream) should import `require_user` from here so the
auth path stays consistent and fits the canonical error envelope
(API_CONTRACT.md §2).
"""
from __future__ import annotations

from fastapi import Header

from backend.auth.jwt_handler import get_jti_from_token, verify_token
from backend.auth.revocation import is_revoked
from backend.routers._errors import unauthenticated


def require_user(authorization: str = Header(default=None)) -> int:
    """Resolve the JWT bearer token to a user_id, raising
    `unauthenticated` (401, canonical envelope) on miss/invalid.

    Mirrors the legacy `routers/portfolio.py:get_user_id` pattern so
    Agent System endpoints behave consistently with the rest of the
    API while emitting the canonical error envelope.

    Revocation: if the token carries a ``jti`` claim and that jti is on
    the Redis revocation list (set by POST /auth/logout), reject as
    invalid. Legacy tokens minted before the jti claim shipped are still
    accepted — they simply can't be individually revoked.
    """
    if not authorization:
        raise unauthenticated("missing token")
    token = authorization.replace("Bearer ", "", 1)
    payload = verify_token(token, "access")
    if not payload:
        raise unauthenticated("invalid token")
    jti = get_jti_from_token(token)
    if jti and is_revoked(jti):
        raise unauthenticated("invalid token")
    sub = payload.get("sub")
    if sub is None:
        raise unauthenticated("invalid token")
    try:
        return int(sub)
    except (TypeError, ValueError) as exc:
        raise unauthenticated("invalid token") from exc
