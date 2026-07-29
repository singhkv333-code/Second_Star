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
import threading
import time
from typing import Any, Callable, Optional

from backend.cache import redis_client

logger = logging.getLogger(__name__)


_TTL_S = 12  # 10-15s per-user live financial data (see module docstring)

# ── Stale-while-revalidate (2026-07-03 perf pass) ─────────────────────────
# The FE polls /portfolio/summary every 30s and re-reads on tab switches —
# with a hard 12s TTL every one of those reads was a cache MISS that paid the
# full broker/compute path (~600-1100ms measured). SWR splits freshness from
# availability: entries live for _HARD_TTL_S, but once older than _TTL_S the
# read RETURNS THE STALE VALUE IMMEDIATELY and kicks a background refresh, so
# the caller never waits on compute while data stays ≤ ~12s + compute stale.
_HARD_TTL_S = 120
# Per-key in-flight guard so a burst of stale reads spawns ONE refresh.
_refresh_inflight: set[str] = set()
_refresh_lock = threading.Lock()


def _swr_read(key: str) -> tuple[Optional[Any], bool]:
    """(payload, is_stale). Unwraps the SWR envelope; legacy raw entries are
    treated as fresh (they expire within the old 12s TTL anyway)."""
    raw = _read(key)
    if raw is None:
        return None, False
    if isinstance(raw, dict) and "_swr_v" in raw:
        age = time.time() - float(raw.get("_swr_ts") or 0)
        return raw["_swr_v"], age > _TTL_S
    return raw, False


def _swr_write(key: str, value: Any) -> None:
    _write(key, {"_swr_v": value, "_swr_ts": time.time()}, ttl_s=_HARD_TTL_S)


def _kick_refresh(key: str, compute: Callable[[], Any]) -> None:
    """Run compute() on a daemon thread and rewrite the envelope. One in-flight
    refresh per key; failures leave the stale entry serving until hard expiry."""
    with _refresh_lock:
        if key in _refresh_inflight:
            return
        _refresh_inflight.add(key)

    def _run() -> None:
        try:
            _swr_write(key, compute())
        except Exception as e:  # noqa: BLE001 — stale keeps serving
            logger.debug("portfolio SWR refresh failed for %s: %s", key, e)
        finally:
            with _refresh_lock:
                _refresh_inflight.discard(key)

    threading.Thread(target=_run, name=f"swr:{key[:40]}", daemon=True).start()
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


def _paper_summary_or_none(user_id: int) -> Optional[dict]:
    """Roll the SIMULATED paper book into the Kite `/summary` shape when the
    user is in paper mode, else None. Read FRESH (not cached): a paper book is
    a cheap local query and a just-placed paper fill must show immediately.
    This is why chat portfolio reads previously disagreed with the Portfolio
    page/header — the cache read Kite (empty) while the account was in paper
    mode with a real book."""
    try:
        from backend.database import SessionLocal
        from backend.paper.routing import should_use_paper
        from backend.paper.portfolio import account_summary
        db = SessionLocal()
        try:
            if not should_use_paper(db, int(user_id)):
                return None
            s = account_summary(db, int(user_id))
            if not s.get("exists"):
                return None
            return {
                "total_value": s["nav"],
                "invested_value": s["invested"],
                "total_pnl": s["total_pnl"],
                "total_pnl_pct": s["total_pnl_pct"],
                "day_pnl": s["day_pnl"],
                "num_holdings": s["num_positions"],
            }
        finally:
            db.close()
    except Exception:  # noqa: BLE001 — never let paper break a portfolio read
        logger.debug("paper summary resolve failed for user %s", user_id, exc_info=True)
        return None


def _paper_holdings_or_none(user_id: int, kite_token: str) -> Optional[list[dict]]:
    """The paper book's holdings in Kite shape when in paper mode, else None.
    Fresh read (see _paper_summary_or_none)."""
    try:
        from backend.database import SessionLocal
        from backend.paper.routing import should_use_paper
        from backend.services.portfolio_source import resolve_holdings
        db = SessionLocal()
        try:
            if not should_use_paper(db, int(user_id)):
                return None
            return [dict(h) for h in resolve_holdings(db, int(user_id), kite_token)]
        finally:
            db.close()
    except Exception:  # noqa: BLE001
        logger.debug("paper holdings resolve failed for user %s", user_id, exc_info=True)
        return None


def get_summary_cached(user_id: int, kite_token: str) -> dict:
    """Return portfolio summary. PAPER mode → the simulated book, read fresh
    (agrees with the Portfolio page/header). LIVE mode → SWR-cached Kite.

    Falls back to a live fetch on cache miss; populates the cache for
    the next caller. Errors in the cache layer are non-fatal — we
    always fall through to the broker.
    """
    paper = _paper_summary_or_none(user_id)
    if paper is not None:
        return paper
    from backend.kite.portfolio import get_portfolio_summary

    key = f"{_SUMMARY_PREFIX}{user_id}"
    cached, stale = _swr_read(key)
    if cached is not None:
        if stale:
            _kick_refresh(key, lambda: get_portfolio_summary(kite_token))
        return cached
    fresh = get_portfolio_summary(kite_token)
    _swr_write(key, fresh)
    return fresh


def get_holdings_cached(user_id: int, kite_token: str) -> list[dict]:
    """Return holdings list. PAPER mode → the simulated book, read fresh; LIVE
    mode → SWR-cached Kite (see get_summary_cached)."""
    paper = _paper_holdings_or_none(user_id, kite_token)
    if paper is not None:
        return paper
    from backend.kite.portfolio import get_holdings

    key = f"{_HOLDINGS_PREFIX}{user_id}"
    cached, stale = _swr_read(key)
    if cached is not None:
        if stale:
            _kick_refresh(key, lambda: get_holdings(kite_token))
        return cached
    fresh = get_holdings(kite_token)
    _swr_write(key, fresh)
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

    SWR note: unlike get_summary_cached/get_holdings_cached, this CANNOT
    refresh in a background thread — `compute` closes over request-scoped
    state (the FastAPI DB session), which is torn down when the request
    returns and is not thread-safe. Instead the entry serves (possibly
    stale) for the full hard TTL and the first post-expiry request pays
    the recompute — one payer per ~2 min instead of one per 12s.
    """
    cached, _stale = _swr_read(key)
    if cached is not None:
        return cached
    fresh = compute()
    _swr_write(key, fresh)
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
