"""Shared FastAPI dependencies for the Agent System routers.

Single source of truth for the bearer-token auth check. Every router
under the Agent System surface (workflows, runs, approvals, webhooks
[unauth], run_stream) should import `require_user` from here so the
auth path stays consistent and fits the canonical error envelope
(API_CONTRACT.md §2).
"""
from __future__ import annotations

from fastapi import Depends, Header

from backend.auth.jwt_handler import get_jti_from_token, verify_token
from backend.auth.revocation import is_revoked
from backend.routers._errors import http_error, unauthenticated


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


def require_admin(user_id: int = Depends(require_user)) -> int:
    """Admin-only gate for /admin* surfaces (ticker control, chat-trace
    inspection, event simulation, F&O refresh).

    Membership comes from ``settings.admin_user_ids`` (comma-separated pivot
    user ids, set via ADMIN_USER_IDS in .env). FAIL-CLOSED: an empty/unset
    list means nobody is admin — every admin endpoint 403s. Added 2026-07-04
    (beta-prep): these surfaces were previously reachable by ANY
    authenticated user, verified live with a throwaway account.
    """
    from backend.config import settings

    # Test env: any authenticated user is admin (the suite registers ad-hoc
    # users and can't know a configured admin id). Production/dev enforce the
    # fail-closed ADMIN_USER_IDS allow-list.
    if getattr(settings, "app_env", "development") == "test":
        return user_id

    raw = (getattr(settings, "admin_user_ids", "") or "").strip()
    admin_ids = {
        int(part) for part in raw.split(",") if part.strip().isdigit()
    }
    if user_id not in admin_ids:
        raise http_error(403, "forbidden", "admin access required")
    return user_id
