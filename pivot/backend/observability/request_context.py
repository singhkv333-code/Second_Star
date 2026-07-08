"""Per-request context: request_id, user_id, and the ASGI middleware
that binds them.

The contextvars exported here are read by structlog's
`merge_contextvars` processor (configured in
`backend.observability.logging_setup`) — every log line emitted
during the request will automatically carry `request_id` and, when
known, `user_id`.

We deliberately do NOT log request or response bodies (PII risk).
Method, path, status, latency_ms, client_ip — that's it.
"""

from __future__ import annotations

import time
import uuid
from contextvars import ContextVar
from typing import Awaitable, Callable

import structlog
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp

logger = structlog.get_logger("backend.request")

# Public contextvars. `None` defaults are intentional — read sites
# should treat absence as "not in a request" rather than crash.
request_id_var: ContextVar[str | None] = ContextVar(
    "request_id", default=None,
)
user_id_var: ContextVar[int | None] = ContextVar(
    "user_id", default=None,
)


def get_request_id() -> str | None:
    """Read the current request id, if any."""
    return request_id_var.get()


def _client_ip(request: Request) -> str | None:
    """Best-effort client IP. X-Forwarded-For first hop wins; else
    falls back to the ASGI scope's client tuple.
    """
    xff = request.headers.get("x-forwarded-for")
    if xff:
        first = xff.split(",")[0].strip()
        if first:
            return first
    if request.client is not None:
        return request.client.host
    return None


def _maybe_user_id(request: Request) -> int | None:
    """Try to extract a user id from a Bearer token. Never raises —
    invalid/missing/expired tokens are the route handler's problem,
    not the logger's.
    """
    auth = request.headers.get("authorization")
    if not auth:
        return None
    parts = auth.split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None
    token = parts[1].strip()
    if not token:
        return None
    try:
        # Import inside the function so an import-time circular
        # dependency between auth and observability is impossible.
        from backend.auth.jwt_handler import get_user_id_from_token
        return get_user_id_from_token(token)
    except Exception:
        return None


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Bind request_id (and user_id when known) into structlog's
    contextvars for the lifetime of one request, then clear on the
    way out so reused asyncio tasks don't leak state.

    Also emits exactly one `request.start` and one `request.end`
    log line per request, with `X-Request-ID` echoed on the response.
    """

    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        incoming_rid = request.headers.get("x-request-id")
        request_id = (
            incoming_rid.strip()
            if incoming_rid and incoming_rid.strip()
            else uuid.uuid4().hex[:16]
        )

        rid_token = request_id_var.set(request_id)
        uid_token = None

        route = request.url.path
        method = request.method
        client_ip = _client_ip(request)

        # Bind for every log line emitted in this request's task.
        structlog.contextvars.bind_contextvars(
            request_id=request_id,
            route=route,
            method=method,
        )

        user_id = _maybe_user_id(request)
        if user_id is not None:
            uid_token = user_id_var.set(user_id)
            structlog.contextvars.bind_contextvars(user_id=user_id)

        start_ns = time.perf_counter_ns()
        logger.info(
            "request.start",
            method=method,
            path=route,
            client_ip=client_ip,
        )

        status_code: int = 500
        response: Response | None = None
        try:
            response = await call_next(request)
            status_code = response.status_code
            return response
        except Exception:
            latency_ms = (time.perf_counter_ns() - start_ns) / 1_000_000.0
            logger.error(
                "request.error",
                method=method,
                path=route,
                latency_ms=round(latency_ms, 3),
                exc_info=True,
            )
            raise
        finally:
            if response is not None:
                response.headers["X-Request-ID"] = request_id
                latency_ms = (
                    time.perf_counter_ns() - start_ns
                ) / 1_000_000.0
                if status_code >= 500:
                    log_fn = logger.error
                elif status_code >= 400:
                    log_fn = logger.warning
                else:
                    log_fn = logger.info
                log_fn(
                    "request.end",
                    method=method,
                    path=route,
                    status_code=status_code,
                    latency_ms=round(latency_ms, 3),
                )
            # Always reset contextvars + structlog bindings so a
            # subsequent task on the same event loop never sees stale
            # request_id / user_id.
            structlog.contextvars.clear_contextvars()
            request_id_var.reset(rid_token)
            if uid_token is not None:
                user_id_var.reset(uid_token)
