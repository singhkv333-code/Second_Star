"""PostHog analytics client — singleton initialized at app startup.

Usage in route handlers:
    from backend.posthog_client import get_posthog
    ph = get_posthog()
    if ph:
        ph.capture(str(user_id), "event_name", {"key": "value"})

Rules:
- distinct_id is always str(user_id) — never email or PII.
- PII (email, name) belongs only in ph.identify() person properties.
- Safe event properties: metadata, counts, flags, enum strings.
"""
import atexit
import logging
from typing import Optional

logger = logging.getLogger(__name__)

try:
    from posthog import Posthog as _Posthog
    _POSTHOG_AVAILABLE = True
except ImportError:
    _Posthog = None  # type: ignore[assignment,misc]
    _POSTHOG_AVAILABLE = False

_client: Optional[object] = None


def init_posthog(api_key: str, host: str) -> None:
    """Initialize the PostHog client. No-op when api_key is empty or the
    package is not installed."""
    global _client
    if not api_key or not _POSTHOG_AVAILABLE:
        if not _POSTHOG_AVAILABLE:
            logger.warning("posthog package not installed — analytics disabled")
        return
    _client = _Posthog(
        project_api_key=api_key,
        host=host,
        enable_exception_autocapture=True,
    )
    atexit.register(_client.shutdown)  # type: ignore[union-attr]
    logger.info("PostHog analytics initialized (host=%s)", host)


def get_posthog():
    """Return the initialized Posthog client, or None when analytics is disabled."""
    return _client


def shutdown_posthog() -> None:
    """Flush and shut down the PostHog client on app shutdown."""
    if _client is not None:
        _client.shutdown()  # type: ignore[union-attr]
