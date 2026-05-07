"""Portfolio summary / holdings Redis cache.

WHY: chat sessions burst-query the portfolio. A typical "show my
portfolio → which sector am I most exposed to → what's the tax hit"
sequence calls `get_holdings()` 3-5 times in 60s; today each call
hits the broker (or mock store + DB walk). Caching at the user_id
boundary collapses the burst.

TTL is 30 seconds. That's:
  - long enough to cover a typical chat-burst (a user clicking
    through portfolio Q&A within a single thought)
  - short enough that staleness is below broker reporting lag
    (Zerodha's holdings API itself has ~30s lag from order fill)
  - simple — no invalidation hooks needed for v1; the TTL itself
    is the invalidation. When real Kite is wired up, add
    `invalidate(user_id)` calls at order-success points.

Cardinality: O(active users). One key per user, two payload fields
(summary + holdings). Bounded — every key has a TTL set, so Redis
won't accumulate.

Keys:
  portfolio:summary:{user_id}   → JSON  (TTL 30s)
  portfolio:holdings:{user_id}  → JSON  (TTL 30s)
"""
from __future__ import annotations

import json
import logging
from typing import Any, Optional

from backend.cache import redis_client

logger = logging.getLogger(__name__)


_TTL_S = 30
_SUMMARY_PREFIX = "portfolio:summary:"
_HOLDINGS_PREFIX = "portfolio:holdings:"


def _read(key: str) -> Optional[Any]:
    try:
        raw = redis_client.get(key)
        if raw is None:
            return None
        if isinstance(raw, (bytes, bytearray)):
            raw = raw.decode()
        return json.loads(raw)
    except Exception as e:
        logger.debug("portfolio cache read failed for %s: %s", key, e)
        return None


def _write(key: str, value: Any) -> None:
    try:
        redis_client.set(key, json.dumps(value, default=str), ex=_TTL_S)
    except Exception as e:
        logger.debug("portfolio cache write failed for %s: %s", key, e)


def get_summary_cached(user_id: int, kite_token: str) -> dict:
    """Return portfolio summary, served from cache when fresh.

    Falls back to a live fetch on cache miss; populates the cache for
    the next caller. Errors in the cache layer are non-fatal — we
    always fall through to the broker.
    """
    from backend.kite.portfolio import get_portfolio_summary

    key = f"{_SUMMARY_PREFIX}{user_id}"
    cached = _read(key)
    if cached is not None:
        return cached
    fresh = get_portfolio_summary(kite_token)
    _write(key, fresh)
    return fresh


def get_holdings_cached(user_id: int, kite_token: str) -> list[dict]:
    """Return holdings list, served from cache when fresh."""
    from backend.kite.portfolio import get_holdings

    key = f"{_HOLDINGS_PREFIX}{user_id}"
    cached = _read(key)
    if cached is not None:
        return cached
    fresh = get_holdings(kite_token)
    _write(key, fresh)
    return fresh


def invalidate(user_id: int) -> None:
    """Clear cached portfolio for a user. Call after a successful order
    placement so the user sees the new state on the next read.

    Not wired into the order-placement code paths today — the 30s TTL
    is the practical invalidation. Hook this in once real Kite is on
    and we want sub-30s freshness post-order.
    """
    try:
        redis_client.delete(f"{_SUMMARY_PREFIX}{user_id}")
        redis_client.delete(f"{_HOLDINGS_PREFIX}{user_id}")
    except Exception as e:
        logger.debug("portfolio cache invalidate failed for %s: %s", user_id, e)
