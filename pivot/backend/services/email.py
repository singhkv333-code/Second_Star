"""Deferred-send email service.

Beta launches with ``settings.email_enabled = False``: we have no
provider wired yet but the auth flows (email-verify, password-reset)
need an outbound call so the end-to-end shape is exercised every day.
``send_email`` therefore LOGS the subject + link (and the body when no
link is set) at INFO level, always returns True, and never raises.

When a provider is configured we flip the flag and add the real client
underneath this same signature — callers don't change.
"""
from __future__ import annotations

import logging
from typing import Optional

from backend.config import settings

logger = logging.getLogger(__name__)


def send_email(
    to: str,
    subject: str,
    body: str,
    *,
    link: Optional[str] = None,
) -> bool:
    """Send (or log) an email. Returns True on success-or-deferred.

    Never raises. Auth flows MUST be able to call this with confidence
    that a transport failure won't break signup / forgot-password.
    """
    if not settings.email_enabled:
        if link:
            logger.info(
                "EMAIL[deferred] to=%s subject=%r link=%s",
                to, subject, link,
            )
        else:
            # Inline body (truncated) so a dev can copy the verification
            # code out of logs without a link to click.
            preview = body if len(body) <= 500 else body[:500] + "..."
            logger.info(
                "EMAIL[deferred] to=%s subject=%r body=%s",
                to, subject, preview,
            )
        return True

    # Real-send path: not wired in beta. Once an SMTP / SES / SendGrid
    # client is configured here, callers won't change.
    try:
        logger.info(
            "EMAIL[send] to=%s subject=%r (provider not yet wired; logging only)",
            to, subject,
        )
        return True
    except Exception as e:  # noqa: BLE001 — never break the auth flow
        logger.warning("send_email failed for %s: %s", to, e)
        return False
