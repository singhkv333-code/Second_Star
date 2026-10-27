"""Helpers for raising canonical-envelope errors per
docs/API_CONTRACT.md §2.

The global exception handlers in `backend/main.py` rewrap every
HTTPException raised under `/api/*` into:

    { "error": { "code": "<stable-code>", "message": "...", "details": {...} } }

The handlers know how to map status codes to default codes; routers
that need a specific error code (e.g. `state_conflict` on a 409, or
the `expired` reason on a 409 approval) can raise with a
`detail = {"code": "...", "message": "...", "details": {...}}` dict
to override.

Helpers below reduce boilerplate so every workflow router stays
consistent.
"""
from __future__ import annotations

from typing import Any, Optional

from fastapi import HTTPException


def http_error(
    status_code: int,
    code: str,
    message: str,
    *,
    details: Optional[dict[str, Any]] = None,
) -> HTTPException:
    """Build an HTTPException whose `detail` is the canonical
    {code, message, details} dict the global handler trusts as-is."""
    payload: dict[str, Any] = {"code": code, "message": message}
    if details is not None:
        payload["details"] = details
    return HTTPException(status_code=status_code, detail=payload)


def not_found(message: str = "not found") -> HTTPException:
    return http_error(404, "not_found", message)


def state_conflict(
    message: str, *, details: Optional[dict[str, Any]] = None,
) -> HTTPException:
    return http_error(409, "state_conflict", message, details=details)


def validation_error(
    message: str, *, details: Optional[dict[str, Any]] = None,
) -> HTTPException:
    return http_error(422, "validation_error", message, details=details)


def unauthenticated(message: str = "missing or invalid token") -> HTTPException:
    return http_error(401, "unauthenticated", message)


def rate_limited(message: str = "rate limit exceeded") -> HTTPException:
    return http_error(429, "rate_limited", message)


def not_yet_available(message: str) -> HTTPException:
    return http_error(503, "not_yet_available", message)
