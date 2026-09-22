"""Shared HTTP + mock helpers for broker connectors.

``kite.py``/``dhan.py``/``fyers.py`` each grew their own private ``_http``
before there were six brokers. Rather than copy that block a fourth, fifth and
sixth time, the connectors added later import from here. The existing three are
deliberately left alone — they work, and rewriting them to prove a point is how
a working order path acquires a regression.
"""
from __future__ import annotations

from datetime import datetime, time, timedelta, timezone
from typing import Any, Optional

try:  # requests is present in prod; guard so imports never hard-fail in tests
    import requests
except Exception:  # pragma: no cover
    requests = None  # type: ignore[assignment]

IST = timezone(timedelta(hours=5, minutes=30))

# A session with no real token runs against deterministic mock data so the
# onboarding UI is demoable without a live broker account.
MOCK_SENTINEL = "mock"


def use_mock(token: str) -> bool:
    """True when this session has no real credential behind it."""
    return not token or token.startswith(MOCK_SENTINEL)


def http(
    method: str,
    url: str,
    *,
    headers: Optional[dict] = None,
    json: Any = None,
    params: Optional[dict] = None,
    timeout: int = 12,
) -> dict:
    """Thin JSON request. Raises for non-2xx so callers translate the failure
    into ``NeedsManualLogin`` or an honest error dict — never a silent {}."""
    if requests is None:  # pragma: no cover
        raise RuntimeError("requests not installed")
    resp = requests.request(
        method, url, headers=headers or {}, json=json, params=params, timeout=timeout
    )
    resp.raise_for_status()
    try:
        return resp.json()
    except ValueError:
        return {}


def next_expiry_at(hour: int, minute: int = 0) -> datetime:
    """The next occurrence of ``hour:minute`` IST, as an aware UTC datetime.

    Every major Indian broker kills the access token at a fixed wall-clock time
    rather than after a duration — Kite and Groww at 06:00 IST, Upstox at
    03:30. A token minted at 20:00 today and one minted at 02:00 tomorrow both
    die at the same instant, so expiry is a *clock time*, not `now + 24h`.
    Getting this wrong makes the UI promise hours of life a token does not have.
    """
    now = datetime.now(IST)
    target = datetime.combine(now.date(), time(hour, minute), tzinfo=IST)
    if target <= now:
        target += timedelta(days=1)
    return target.astimezone(timezone.utc)
