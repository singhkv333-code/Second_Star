"""Sentry error reporting wiring for the Pivot backend.

If `settings.sentry_dsn` is blank (the default), this module is a
no-op — the app continues to work for dev environments without a DSN.
When set, unhandled exceptions are captured, the per-request
`request_id` from our structlog contextvars is attached as a tag, and
a configurable traces sample rate keeps ingestion costs bounded.

`configure_sentry()` is idempotent and never raises into its caller —
Sentry failure must not take down the app.
"""

from __future__ import annotations

import logging
from typing import Any

import sentry_sdk
import structlog
from sentry_sdk.integrations.asyncio import AsyncioIntegration
from sentry_sdk.integrations.fastapi import FastApiIntegration
from sentry_sdk.integrations.logging import LoggingIntegration
from sentry_sdk.integrations.starlette import StarletteIntegration

from backend.config import settings
from backend.observability.request_context import request_id_var, user_id_var

logger = structlog.get_logger(__name__)

_CONFIGURED: bool = False


def _attach_request_context(
    event: dict[str, Any], hint: dict[str, Any],
) -> dict[str, Any]:
    """Tag the Sentry event with the current request_id / user_id.

    Pulled from structlog contextvars set by RequestContextMiddleware.
    Wrapped in try/except so a bug in enrichment cannot crash the
    error-reporting path itself — on failure we drop enrichment and
    return the event unmodified.
    """
    try:
        request_id = request_id_var.get()
        user_id = user_id_var.get()
        if request_id is not None or user_id is not None:
            tags = event.setdefault("tags", {})
            if request_id is not None:
                tags["request_id"] = request_id
            if user_id is not None:
                tags["user_id"] = user_id
    except Exception:
        # Never let enrichment fail the error path.
        pass
    return event


def configure_sentry() -> bool:
    """Initialise Sentry. Idempotent.

    Returns True when Sentry is active, False when disabled (no DSN)
    or when init itself failed. Never raises into the caller.
    """
    global _CONFIGURED
    if _CONFIGURED:
        return True

    dsn = settings.sentry_dsn
    if not dsn:
        logger.info("Sentry disabled (no DSN)")
        return False

    try:
        sentry_sdk.init(
            dsn=dsn,
            environment=settings.app_env,
            release=settings.app_version,
            traces_sample_rate=settings.sentry_traces_sample_rate,
            integrations=[
                FastApiIntegration(transaction_style="endpoint"),
                StarletteIntegration(transaction_style="endpoint"),
                AsyncioIntegration(),
                LoggingIntegration(
                    level=logging.INFO,
                    event_level=logging.ERROR,
                ),
            ],
            before_send=_attach_request_context,
            before_send_transaction=_attach_request_context,
            # No client IPs in events.
            send_default_pii=False,
        )
    except Exception as exc:
        # A bad DSN format or transport failure should never take the
        # app down — log a warning and continue without Sentry.
        logger.warning(
            "Sentry init failed; continuing without error reporting",
            error=str(exc),
        )
        return False

    _CONFIGURED = True
    logger.info(
        "Sentry enabled",
        environment=settings.app_env,
        release=settings.app_version,
        traces_sample_rate=settings.sentry_traces_sample_rate,
    )
    return True
