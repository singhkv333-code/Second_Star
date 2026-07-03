"""Periodic global-cache warmer — no user ever pays a cold fill.

WHY: every global data cache (screener fundamentals, screener market
metrics, views list, per-symbol financials) was cache-ASIDE: the first
request after each TTL expiry paid the full Azure/yfinance cost (measured
0.8-2.5s each). Login-time warming (services/cache_warm.py) only helps the
user who logs in *after* the cache went cold — an active user browsing when
a TTL lapses still eats the refill.

This module re-warms those caches on a fixed cadence SHORTER than their
TTLs, so entries never expire in practice — every request, from every
user, at any time, is a warm read. Runs as an APScheduler interval job
(registered in workflows/scheduler.py; the function is MODULE-LEVEL —
closures kill APScheduler's serialization, see the F&O gotcha).

Cadence: every 8 minutes.
  - screener market metrics (TTL 10 min)  → kicked every run
  - screener fundamentals  (TTL 30 min)   → recomputed every run (~1-2s)
  - views list             (TTL ~45s)     → recomputed every run
  - financials for the top-mcap universe  → every ~4h (soft-TTL 30 min +
    6h hard TTL means visited symbols self-sustain via SWR; this seeds
    the most-likely first visits)

Everything is best-effort: any failure is logged and skipped — a warmer
must never take down the scheduler or leave a cache worse than it found it.
"""
from __future__ import annotations

import json
import logging
import time

from backend.cache import redis_client

logger = logging.getLogger(__name__)

_FINANCIALS_SEED_MARKER = "warm:financials:last_ts"
_FINANCIALS_SEED_EVERY_S = 4 * 3600
_FINANCIALS_SEED_COUNT = 20  # top-mcap universe names


def warm_global_data_caches() -> None:
    """One warm pass over every global cache. Module-level for APScheduler."""
    t0 = time.time()

    # ── 1. Screener market metrics (price / day change / 1Y) ──────────────
    try:
        from backend.routers.screener import _kick_metrics_refresh

        _kick_metrics_refresh()  # guarded: no-ops if a refresh is in flight
    except Exception as e:  # noqa: BLE001
        logger.debug("data_warmer: metrics kick failed: %s", e)

    # ── 2. Screener fundamentals (PE/ROE map) — recompute + rewrite ───────
    try:
        from backend.routers.screener import (
            _FUND_CACHE_KEY,
            _FUND_CACHE_TTL_SECONDS,
            _fetch_fundamentals_map,
        )
        from backend.services.sector_universe import _UNIVERSE

        fmap = _fetch_fundamentals_map([r.symbol for r in _UNIVERSE])
        if fmap:
            redis_client.set(
                _FUND_CACHE_KEY, json.dumps(fmap), ex=_FUND_CACHE_TTL_SECONDS
            )
    except Exception as e:  # noqa: BLE001
        logger.debug("data_warmer: fundamentals warm failed: %s", e)

    # ── 3. Views list (global) ─────────────────────────────────────────────
    try:
        from backend.database import SessionLocal
        from backend.routers.views import list_views

        db = SessionLocal()
        try:
            list_views(status=None, view_type=None, category=None,
                       db=db, user_id=None)
        finally:
            db.close()
    except Exception as e:  # noqa: BLE001
        logger.debug("data_warmer: views warm skipped: %s", e)

    # ── 4. Financials seed for the top-mcap universe (every ~4h) ──────────
    try:
        last = redis_client.get(_FINANCIALS_SEED_MARKER)
        last_ts = float(last) if last else 0.0
        if time.time() - last_ts > _FINANCIALS_SEED_EVERY_S:
            redis_client.set(_FINANCIALS_SEED_MARKER, str(time.time()),
                             ex=24 * 3600)
            from backend.routers.financials import (
                _build_financials_payload,
                _write_financials_cache,
            )
            from backend.services.sector_universe import _UNIVERSE

            top = sorted(_UNIVERSE, key=lambda r: -r.mcap_cr)[:_FINANCIALS_SEED_COUNT]
            for r in top:
                try:
                    _write_financials_cache(
                        r.symbol.upper(),
                        _build_financials_payload(r.symbol.upper()),
                    )
                except Exception:  # noqa: BLE001 — per-symbol, keep going
                    continue
            logger.info("data_warmer: seeded financials for %d symbols", len(top))
    except Exception as e:  # noqa: BLE001
        logger.debug("data_warmer: financials seed skipped: %s", e)

    logger.info("data_warmer: pass done in %.1fs", time.time() - t0)


__all__ = ["warm_global_data_caches"]
