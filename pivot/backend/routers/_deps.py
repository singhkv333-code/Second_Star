"""Shared FastAPI dependencies for the Agent System routers.

Single source of truth for the bearer-token auth check. Every router
under the Agent System surface (workflows, runs, approvals, webhooks
[unauth], run_stream) should import `require_user` from here so the
auth path stays consistent and fits the canonical error envelope
(API_CONTRACT.md §2).
"""
from __future__ import annotations

from fastapi import Header

from backend.auth.jwt_handler import get_user_id_from_token
from backend.routers._errors import unauthenticated


def require_user(authorization: str = Header(default=None)) -> int:
    """Resolve the JWT bearer token to a user_id, raising
    `unauthenticated` (401, canonical envelope) on miss/invalid.

    Mirrors the legacy `routers/portfolio.py:get_user_id` pattern so
    Agent System endpoints behave consistently with the rest of the
    API while emitting the canonical error envelope.
    """
    if not authorization:
        raise unauthenticated("missing token")
    token = authorization.replace("Bearer ", "", 1)
    user_id = get_user_id_from_token(token)
    if not user_id:
        raise unauthenticated("invalid token")
    return user_id
