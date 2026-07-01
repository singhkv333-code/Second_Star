"""Portfolio summary / holdings / scores / performance Redis cache.

WHY: chat sessions and the FE dashboard burst-query the portfolio. A
typical "show my portfolio → which sector am I most exposed to → what's
the tax hit" sequence — or a dashboard mount that fires summary,
holdings, scores, and the performance chart together — calls the
underlying broker/compute path several times within a couple of
seconds. Caching at the `user_id` (+ endpoint, + query params) boundary
collapses the burst.

TTL is short: 10-15s (see `_TTL_S`). This is **live, per-user financial
data** — deliberately much shorter than the 30-60s convention used
elsewhere in this codebase for public/market-wide views (e.g.
`services/top_movers.py`'s 60s). That's:
  - long enough to cover a typical page-mount/chat-burst (several reads
    within one thought/one dashboard load)
  - short enough that staleness stays well below broker reporting lag
    (Zerodha's holdings API itself has ~30s lag from order fill) and
    doesn't make a user distrust a number that just changed
  - simple — no invalidation hooks needed for v1; the TTL itself is the
    invalidation. When real Kite is wired up, add `invalidate(user_id)`
    calls at order-success points.

Cardinality: O(active users) × O(distinct query-param combos, e.g.
performance periods). Bounded — every key has a TTL set, so Redis won't
accumulate.

Keys:
  portfolio:summary:{user_id}              → JSON  (TTL _TTL_S)
  portfolio:holdings:{user_id}             → JSON  (TTL _TTL_S)
  portfolio:scores:{user_id}               → JSON  (TTL _TTL_S)
  portfolio:performance:{user_id}:{period} → JSON  (TTL _TTL_S)
"""
from __future__ import annotations

import json
import logging
from typing import Any, Callable, Optional

from backend.cache import redis_client

logger = logging.getLogger(__name__)


_TTL_S = 12  # 10-15s per-user live financial data (see module docstring)
_SUMMARY_PREFIX = "portfolio:summary:"
_HOLDINGS_PREFIX = "portfolio:holdings:"
_SCORES_PREFIX = "portfolio:scores:"
_PERFORMANCE_PREFIX = "portfolio:performance:"

# All known /api/portfolio/performance periods — used only so `invalidate()`
# can clear every period-specific key for a user. Kept here (rather than
# imported from `routers/portfolio_perf.py`) to avoid a router→service
# import cycle; if a period is ever added there without updating this list,
# the worst case is a stale performance cache surviving `_TTL_S` longer,
# which is harmless (TTL still expires it).
_PERFORMANCE_PERIODS = ("1M", "3M", "6M", "1Y", "5Y")


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


def _write(key: str, value: Any, ttl_s: int = _TTL_S) -> None:
    try:
        redis_client.set(key, json.dumps(value, default=str), ex=ttl_s)
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


def cache_aside(key: str, compute: Callable[[], Any], ttl_s: int = _TTL_S) -> Any:
    """Generic cache-aside wrapper for endpoints whose compute step needs
    request-scoped context (a DB session, other closures) that this module
    can't reconstruct on its own — unlike `get_summary_cached`/
    `get_holdings_cached`, which own their whole fetch.

    Used by `/portfolio/scores` and `/api/portfolio/performance`: the
    caller builds a zero-arg closure over its request-scoped state and
    passes it in; this function only owns the read-through-cache/write
    mechanics. Errors in the cache layer are non-fatal — `compute()` is
    always the fallback of record.
    """
    cached = _read(key)
    if cached is not None:
        return cached
    fresh = compute()
    _write(key, fresh, ttl_s=ttl_s)
    return fresh


def scores_cache_key(user_id: int) -> str:
    return f"{_SCORES_PREFIX}{user_id}"


def performance_cache_key(user_id: int, period: str) -> str:
    return f"{_PERFORMANCE_PREFIX}{user_id}:{period}"


def invalidate(user_id: int) -> None:
    """Clear cached portfolio for a user. Call after a successful order
    placement so the user sees the new state on the next read.

    Not wired into the order-placement code paths today — the short TTL
    is the practical invalidation. Hook this in once real Kite is on and
    we want sub-TTL freshness post-order.
    """
    try:
        redis_client.delete(f"{_SUMMARY_PREFIX}{user_id}")
        redis_client.delete(f"{_HOLDINGS_PREFIX}{user_id}")
        redis_client.delete(f"{_SCORES_PREFIX}{user_id}")
        for period in _PERFORMANCE_PERIODS:
            redis_client.delete(performance_cache_key(user_id, period))
    except Exception as e:
        logger.debug("portfolio cache invalidate failed for %s: %s", user_id, e)
