"""Proactive Redis cache warming for the just-logged-in user.

WHY: portfolio / views / markets caches are cache-ASIDE today — the very
first request after a cold cache pays the full compute/network cost, and
only subsequent hits within the TTL are fast. This warmer proactively
populates the same cache keys the route handlers read from, so by the
time the user's dashboard mounts (typically within 1-3 s of the login
POST) every one of those "first" requests already hits a warm entry.

Trigger: called ONCE from POST /auth/login via FastAPI's
:class:`BackgroundTasks` — no periodic re-warm job, no separate scheduler
process. Fire-and-forget: any failure is logged but MUST NOT slow or
fail the login response. Users with no holdings, no Kite session, or any
other edge case still get a normal successful login.

Warm targets (see task doc — this is the exact agreed scope):
    a. Portfolio: summary, holdings, scores, performance(period="1Y").
       Reuses the same cache keys / helper fns already in
       :mod:`services.portfolio_cache`, so warming populates the exact
       entries the endpoints read from.
    b. Views: the GLOBAL /api/views list cache (not user-scoped —
       warming once benefits everyone until the 45 s TTL expires;
       accepted trade-off of "on login only" vs a dedicated scheduler).
    c. Per-holding quote + default-range (5Y) sparkline via
       :mod:`routers.markets` — these are the symbols the just-logged-in
       user is statistically most likely to click into.

The warmer opens its own :class:`SessionLocal` because it runs outside
any request's dependency-injected DB session, and closes it when done.
Kept linear and readable — this is a background job, not a hot path.
"""
from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)


def warm_user_cache(user_id: int) -> None:
    """Populate the portfolio/views/markets caches for ``user_id``.

    Fire-and-forget from a FastAPI BackgroundTask. Never raises — a broad
    try/except at the outer boundary swallows every error path, since a
    failed warm is strictly less bad than a failed login. Individual step
    failures are logged at WARNING (portfolio/views — user-visible if they
    happen) or DEBUG (per-symbol quote/sparkline — expected for stale/dead
    symbols, don't spam WARN).

    No-op under pytest: `PYTEST_CURRENT_TEST` is set by pytest for the
    duration of every test. A user with no real broker session still gets
    a non-empty MOCK holdings list (`get_kite_token`'s "mock_token"
    fallback), and warming those symbols' quotes/sparklines calls the real
    `get_quote`/`get_sparkline` — which are NOT mock-mode-gated and make
    genuine unmocked yfinance/Kite network calls regardless of the
    portfolio being mock. Since `BackgroundTasks` runs synchronously
    in-process under Starlette's TestClient, that would fire on every
    login test: real network calls the test never asked for, writing into
    the same real-Redis quote/sparkline cache other tests read from. This
    optimization has zero value in a test process that tears down after
    each test anyway, so skip it entirely rather than trying to mock
    every login test against it.
    """
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return

    # Import lazily so that a repo-wide import failure in one of the
    # target modules can't take down the login endpoint at import time.
    try:
        from backend.database import SessionLocal
    except Exception as e:  # noqa: BLE001
        logger.warning("cache_warm: SessionLocal import failed: %s", e)
        return

    db = None
    try:
        db = SessionLocal()

        # ── (a) Portfolio ─────────────────────────────────────────────
        # Reuse the exact cache-aside helpers + keys the route handlers
        # use — no duplicate implementation of "compute + write cache".
        holdings: list[dict[str, Any]] = []
        try:
            from backend.routers.portfolio import (
                compute_portfolio_scores, get_kite_token,
            )
            from backend.services.portfolio_cache import (
                cache_aside, get_holdings_cached, get_summary_cached,
                performance_cache_key, scores_cache_key,
            )

            token = get_kite_token(user_id, db)

            # summary — populates portfolio:summary:{user_id}
            try:
                get_summary_cached(user_id, token)
            except Exception as e:  # noqa: BLE001
                logger.warning(
                    "cache_warm: portfolio summary failed for user %s: %s",
                    user_id, e,
                )

            # holdings — populates portfolio:holdings:{user_id}
            # We KEEP the list around for the per-symbol quote/sparkline
            # warm below (avoids a second broker call).
            try:
                holdings = [dict(h) for h in get_holdings_cached(user_id, token)]
            except Exception as e:  # noqa: BLE001
                logger.warning(
                    "cache_warm: portfolio holdings failed for user %s: %s",
                    user_id, e,
                )
                holdings = []

            # scores — populates portfolio:scores:{user_id}
            try:
                cache_aside(
                    scores_cache_key(user_id),
                    lambda: compute_portfolio_scores(db, user_id),
                )
            except Exception as e:  # noqa: BLE001
                logger.warning(
                    "cache_warm: portfolio scores failed for user %s: %s",
                    user_id, e,
                )

            # performance (period="1Y" — the FE default)
            # populates portfolio:performance:{user_id}:1Y
            try:
                from backend.routers.portfolio_perf import _compute_performance

                cache_aside(
                    performance_cache_key(user_id, "1Y"),
                    lambda: _compute_performance(user_id, token, "1Y"),
                )
            except Exception as e:  # noqa: BLE001
                # No holdings raises http_error(404) here — that's expected
                # for brand-new / mock users; log at DEBUG.
                logger.debug(
                    "cache_warm: portfolio performance skipped for user %s: %s",
                    user_id, e,
                )
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "cache_warm: portfolio block failed for user %s: %s",
                user_id, e,
            )

        # ── (b) Views (GLOBAL list) ───────────────────────────────────
        # Call the route function directly with default filters — this
        # is the same code path the FE hits on the Views tab mount, and
        # it writes the same views:list:v1:… key. Silently 404s when
        # the view_markets_enabled flag is off; broad except catches it.
        try:
            from backend.routers.views import list_views

            list_views(
                status=None, view_type=None, category=None,
                db=db, user_id=None,
            )
        except Exception as e:  # noqa: BLE001
            # 404 when the flag is disabled is EXPECTED, not an error.
            logger.debug("cache_warm: views list skipped: %s", e)

        # ── (c) Per-holding quote + sparkline ─────────────────────────
        # Populates quote:yf:v1:{exchange}:{sym} and
        # sparkline:yf:v1:{exchange}:{sym}:5Y:{interval} via the exact
        # same route handlers the FE stock-page mount will hit.
        # Iterated serially so a background task never fans out into a
        # burst of parallel yfinance calls (which have their own
        # rate-limit surface); the user only has a handful of holdings
        # in practice.
        if holdings:
            try:
                from backend.routers.markets import get_quote, get_sparkline
            except Exception as e:  # noqa: BLE001
                logger.warning("cache_warm: markets import failed: %s", e)
                get_quote = None  # type: ignore[assignment]
                get_sparkline = None  # type: ignore[assignment]
            for h in holdings:
                sym = str(h.get("tradingsymbol", "")).strip().upper()
                if not sym:
                    continue
                exchange = str(h.get("exchange") or "NSE").upper()
                if exchange not in ("NSE", "BSE"):
                    exchange = "NSE"
                if get_quote is not None:
                    try:
                        # `_user_id` is only used by the Depends() auth gate
                        # at the FastAPI layer; the function body never
                        # reads it, so calling directly with the warm user
                        # id is equivalent.
                        get_quote(symbol=sym, exchange=exchange, _user_id=user_id)
                    except Exception as e:  # noqa: BLE001
                        logger.debug(
                            "cache_warm: quote skipped for %s: %s", sym, e,
                        )
                if get_sparkline is not None:
                    try:
                        # range="5Y" mirrors the FE default on the stock
                        # page; that's the entry a warmed user is most
                        # likely to click into first.
                        get_sparkline(
                            symbol=sym, range="5Y",
                            exchange=exchange, _user_id=user_id,
                        )
                    except Exception as e:  # noqa: BLE001
                        logger.debug(
                            "cache_warm: sparkline skipped for %s: %s", sym, e,
                        )
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "cache_warm: unexpected outer error for user %s: %s", user_id, e,
        )
    finally:
        if db is not None:
            try:
                db.close()
            except Exception:  # noqa: BLE001
                pass


__all__ = ["warm_user_cache"]
