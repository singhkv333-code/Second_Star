# ruff: noqa: E402
# Logging must be configured before the rest of the backend is imported
# so every module-level `logging.getLogger(__name__)` inherits the
# structlog-backed root handler. That forces the call ordering you see
# below — the file-level noqa silences ruff's import-position checker.
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text
import structlog

from backend.config import settings
from backend.observability.logging_setup import configure_logging
from backend.observability.request_context import RequestContextMiddleware

configure_logging()
from backend.observability.sentry_setup import configure_sentry
configure_sentry()
logger = structlog.get_logger(__name__)

from backend.database import SessionLocal
from backend.cache import redis_client
from backend.auth.router import router as auth_router
from backend.routers.orders import router as orders_router
from backend.routers.chat import router as chat_router
from backend.routers.sip import router as sip_router
from backend.routers.strategy import router as strategy_router
from backend.routers.products import router as products_router
from backend.routers.portfolio import router as portfolio_router
from backend.routers.backtest import router as backtest_router
from backend.routers.scheduler import router as scheduler_router
from backend.routers.kite import router as kite_router, callback_alias_router as kite_callback_alias_router
from backend.routers.compare import router as compare_router
from backend.routers.expr_backtest import router as expr_backtest_router
from backend.routers.pairs_backtest import router as pairs_backtest_router
from backend.routers.portfolio_backtest import router as portfolio_backtest_router
from backend.routers.workflows import router as workflows_router
from backend.routers.runs import router as runs_router
from backend.routers.approvals import router as approvals_router
from backend.routers.webhooks import router as webhooks_router
from backend.routers.run_stream import router as run_stream_router
from backend.routers.scheduled import router as scheduled_router
from backend.routers.markets import router as markets_router
from backend.routers.conversations import router as conversations_router
from backend.routers.backtest_alias import router as backtest_alias_router
from backend.routers.financials import router as financials_router
from backend.routers.quotes import router as quotes_router
from backend.routers.portfolio_perf import router as portfolio_perf_router
from backend.routers.paper import router as paper_router
from backend.routers.ipo_applications import router as ipo_applications_router
from backend.routers.events_calendar import router as events_calendar_router
from backend.routers.stock_automations import router as stock_automations_router
from backend.routers.news import router as news_router
from backend.routers.admin import router as admin_router
from backend.routers.quotes_ws import router as quotes_ws_router
from backend.routers.kite_ticker_admin import router as kite_ticker_admin_router
from backend.routers.admin_simulate import router as admin_simulate_router
from backend.routers.backtest_dsl import router as backtest_dsl_router
from backend.routers.options_admin import router as options_admin_router

app = FastAPI(
    title="Pivot API",
    description="AI-powered investing platform for Indian retail investors",
    version=settings.app_version,
    docs_url="/docs",
    redoc_url="/redoc",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Per-request context middleware. Must be registered AFTER CORS so
# preflight (OPTIONS) responses also carry an X-Request-ID header.
app.add_middleware(RequestContextMiddleware)

app.include_router(auth_router)
app.include_router(orders_router)
app.include_router(chat_router)
app.include_router(sip_router)
app.include_router(strategy_router)
app.include_router(products_router)
app.include_router(portfolio_router)
app.include_router(backtest_router)
app.include_router(scheduler_router)
app.include_router(kite_router)
app.include_router(kite_callback_alias_router)
app.include_router(compare_router)
app.include_router(expr_backtest_router)
app.include_router(pairs_backtest_router)
app.include_router(portfolio_backtest_router)
app.include_router(backtest_alias_router)
# scheduled_router MUST mount before workflows_router — otherwise the
# more-specific path /api/workflows/scheduled-runs gets caught by the
# /api/workflows/{id} route in workflows_router.
app.include_router(scheduled_router)
app.include_router(markets_router)
app.include_router(quotes_router)
app.include_router(portfolio_perf_router)
app.include_router(paper_router)
app.include_router(ipo_applications_router)
app.include_router(events_calendar_router)
app.include_router(stock_automations_router)
app.include_router(news_router)
app.include_router(conversations_router)
app.include_router(workflows_router)
app.include_router(financials_router)
app.include_router(runs_router)
app.include_router(approvals_router)
app.include_router(webhooks_router)
app.include_router(run_stream_router)
app.include_router(admin_router)
app.include_router(quotes_ws_router)
app.include_router(kite_ticker_admin_router)
# admin simulate-trigger endpoints — self-guarded against production
# (every endpoint 404s when settings.app_env == "production").
app.include_router(admin_simulate_router)
# DSL-tree backtester (Phase B). Sits alongside the legacy
# /backtest/* paths under a separate /api/backtest/dsl/* namespace.
app.include_router(backtest_dsl_router)
# F&O P0 admin surface — chain/universe inspection + manual refresh.
app.include_router(options_admin_router)

# ── News & Event Trigger subsystem (flag-gated) ──────────────────────
# Entire subsystem is opt-in via `settings.news_events_enabled`. With
# the flag off, nothing below this comment imports, registers, or runs.
# See docs/news_events_phase0_plan.md and backend/news_events/.
if settings.news_events_enabled:
    from backend.news_events.router import router as news_events_router

    app.include_router(news_events_router)
    logger.info("[news_events] router mounted under /api/news-events")


# ─── Canonical error envelope (docs/API_CONTRACT.md §2) ───────────────
#
# Every non-2xx response from the Agent System surface MUST use:
#   { "error": { "code": "...", "message": "...", "details": {...} } }
#
# FastAPI's default HTTPException serialisation produces `{"detail": ...}`
# which the frontend's `isError(result)` check (`"error" in result`)
# silently misses. We install handlers below to wrap every raised
# HTTPException + every Pydantic body-validation error.
#
# Status codes outside the locked set fall back to "internal_error".
_ERROR_CODE_BY_STATUS = {
    400: "validation_error",
    401: "unauthenticated",
    403: "unauthenticated",
    404: "not_found",
    409: "state_conflict",
    422: "validation_error",
    429: "rate_limited",
    500: "internal_error",
    503: "not_yet_available",
}


# Scope discipline: the canonical envelope is contractual ONLY for the
# Agent System surface (`/api/*` and the WebSocket). Legacy routes
# (`/auth/*`, `/portfolio/*`, etc.) keep FastAPI's default `{"detail": ...}`
# shape so we don't regress their existing test suites. The handlers
# below sniff request.url.path and only rewrap when the path matches.
def _is_api_v1(request: Request) -> bool:
    return request.url.path.startswith("/api/")


@app.exception_handler(HTTPException)
async def _http_exception_handler(
    request: Request, exc: HTTPException,
) -> JSONResponse:
    """Wrap any raised HTTPException into the §2 canonical envelope —
    but only for /api/* routes. Legacy routes get the FastAPI default.

    Routers under /api/* may raise:
      - HTTPException(status_code, detail="message string")
        → message=string, details=None
      - HTTPException(status_code, detail={"code": "...", "message": "...",
        "details": {...}})  ← preferred for endpoint-specific codes
        → use the embedded shape verbatim
      - HTTPException(status_code, detail={"any": "dict"})  (legacy)
        → message="request failed", details=detail
    """
    if not _is_api_v1(request):
        return JSONResponse(
            status_code=exc.status_code,
            content={"detail": exc.detail},
            headers=getattr(exc, "headers", None),
        )

    code = _ERROR_CODE_BY_STATUS.get(exc.status_code, "internal_error")
    detail = exc.detail
    if isinstance(detail, dict) and "code" in detail and "message" in detail:
        # Router opted into a specific error code — trust it.
        body: dict[str, object] = {
            "error": {
                "code": str(detail["code"]),
                "message": str(detail["message"]),
                "details": detail.get("details"),
            }
        }
    elif isinstance(detail, str):
        body = {
            "error": {
                "code": code,
                "message": detail,
                "details": None,
            }
        }
    else:
        body = {
            "error": {
                "code": code,
                "message": "request failed",
                "details": detail if isinstance(detail, dict) else None,
            }
        }
    return JSONResponse(status_code=exc.status_code, content=body)


@app.exception_handler(RequestValidationError)
async def _validation_exception_handler(
    request: Request, exc: RequestValidationError,
) -> JSONResponse:
    """Pydantic body validation → 422.

    Under /api/*: emits the canonical envelope with `validation_error`
    code and per-field details. Legacy routes keep FastAPI's default
    422 shape.
    """
    errors = exc.errors()
    if not _is_api_v1(request):
        return JSONResponse(
            status_code=422, content={"detail": errors},
        )

    details: dict[str, object] = {"errors": errors}
    if errors:
        first = errors[0]
        loc = list(first.get("loc", []))
        details["reason"] = str(first.get("type", ""))
        # Try to extract a step_index when the loc looks like
        # ("body", "steps", <int>, ...). The frontend uses this to
        # highlight the offending step in the editor.
        try:
            i = loc.index("steps")
            if i + 1 < len(loc) and isinstance(loc[i + 1], int):
                details["step_index"] = loc[i + 1]
            if i + 2 < len(loc):
                details["field"] = ".".join(str(p) for p in loc[i + 2:])
        except ValueError:
            # `steps` not in loc — top-level field validation
            if loc and loc[0] == "body" and len(loc) > 1:
                details["field"] = ".".join(str(p) for p in loc[1:])
    body = {
        "error": {
            "code": "validation_error",
            "message": (
                errors[0]["msg"]
                if errors and errors[0].get("msg") else
                "request body invalid"
            ),
            "details": details,
        }
    }
    return JSONResponse(status_code=422, content=body)


@app.on_event("startup")
async def startup():
    """Start the SIP/strategy scheduler. All times in IST."""
    from backend.scheduler import init_scheduler
    from backend.utils.time_utils import format_ist, now_ist

    try:
        init_scheduler(database_url=settings.database_url)
        # Plug the workflows poll job into the same AsyncIOScheduler.
        # It scans `workflows` every 30s for active+due trigger.schedule
        # rows and fires them via the engine.
        from backend import scheduler as scheduler_module
        from backend.workflows.scheduler import register_workflow_scheduler
        if scheduler_module.scheduler is not None:
            register_workflow_scheduler(scheduler_module.scheduler)
            # News & Event Trigger pollers — additive, flag-gated.
            # With the flag off, this branch is a no-op and the
            # subsystem's modules are never imported.
            if settings.news_events_enabled:
                from backend.news_events.workers.poller import register_poller
                from backend.news_events.workers.funnel import register_funnel_worker
                from backend.news_events.workers.retraction_watcher import (
                    register_retraction_watcher,
                )

                register_poller(scheduler_module.scheduler)
                register_funnel_worker(scheduler_module.scheduler)
                register_retraction_watcher(scheduler_module.scheduler)

                # Phase 7 Tier-A: Telegram MTProto channel reader.
                # Long-lived asyncio task (not an APScheduler job)
                # because Telethon's run_until_disconnected is its
                # own event loop. start_telegram_worker is
                # idempotent + gracefully no-ops when creds /
                # session aren't configured.
                if settings.telegram_enabled:
                    from backend.news_events.workers.telegram_worker import (
                        start_telegram_worker,
                    )

                    start_telegram_worker()

                # Polymarket CLOB WS prediction-market trigger.
                # Long-lived asyncio task that owns a persistent
                # WS connection. start_polymarket_ws_worker is
                # idempotent + gracefully no-ops when no active
                # WS-mode specs exist (it never opens the socket
                # until set_subscriptions lands a non-empty set).
                if settings.polymarket_ws_enabled:
                    from backend.news_events.workers.polymarket_ws_worker import (
                        start_polymarket_ws_worker,
                    )

                    start_polymarket_ws_worker()
        logger.info(
            f"[{format_ist(now_ist())}] "
            f"Pivot backend started. Scheduler running on IST."
        )
    except Exception as e:
        logger.warning(f"Scheduler failed to start: {e}")

    # WHY this is fire-and-forget: warmup is a token cost we'd rather
    # spend in the background after the server is serving traffic
    # than block startup on. The function logs its own outcomes; if
    # it fails the app still works (just no p99 cache benefit).
    try:
        from backend.services.cache_warmup import schedule_warmup_after_startup
        schedule_warmup_after_startup()
    except Exception as e:
        logger.info(f"Cache warmup scheduling skipped: {e}")

    # Phase 2: auto-start the Kite ticker if a real access token exists
    # in DB. Wrapped — startup must never fail because the ticker
    # can't reach upstream Kite WS.
    try:
        _maybe_autostart_kite_ticker()
    except Exception as e:
        logger.info(f"Kite ticker autostart skipped: {e}")


def _maybe_autostart_kite_ticker() -> None:
    """Best-effort: find the most recent active KiteSession and boot
    the ticker under it. Silent when no real session exists or when
    we're in mock mode."""
    from backend.kite.auth import KITE_MOCK_MODE, read_kite_access_token
    from backend.kite.portfolio import get_holdings
    from backend.kite.ticker import get_ticker_manager
    from backend.models import KiteSession

    if KITE_MOCK_MODE:
        logger.info("Kite ticker autostart: mock mode, skipping")
        return
    db = SessionLocal()
    try:
        session = (
            db.query(KiteSession)
            .filter(KiteSession.is_active.is_(True))
            .order_by(KiteSession.updated_at.desc().nullslast(), KiteSession.id.desc())
            .first()
        )
        if session is None:
            logger.info("Kite ticker autostart: no active KiteSession")
            return
        token = read_kite_access_token(session)
        if not token or token.startswith("mock_"):
            logger.info("Kite ticker autostart: token unavailable / mocked")
            return
        seeds: list[str] = []
        token_invalid = False
        try:
            holdings = get_holdings(token) or []
            for h in holdings:
                ts = h.get("tradingsymbol") if isinstance(h, dict) else None
                if ts:
                    seeds.append(str(ts))
        except Exception as e:
            msg = str(e).lower()
            if "incorrect" in msg or "tokenexception" in msg or "access_token" in msg:
                token_invalid = True
            logger.info(f"Kite ticker autostart: holdings seed failed: {e}")

        if token_invalid:
            # Stale token (typically expired at 7:30 IST or rotated
            # api_key). Mark the session inactive so we don't thrash
            # the WS reconnect loop. User must re-do Kite OAuth.
            try:
                session.is_active = False
                db.add(session)
                db.commit()
                logger.info(
                    "Kite ticker autostart: stale token; KiteSession id=%s marked inactive — please re-auth.",
                    session.id,
                )
            except Exception as commit_err:
                logger.info(
                    f"Kite ticker autostart: could not invalidate session: {commit_err}"
                )
            return

        get_ticker_manager().start(
            access_token=token,
            user_id=int(session.user_id) if session.user_id else None,
            seed_symbols=seeds,
        )
    finally:
        db.close()


@app.on_event("shutdown")
async def shutdown():
    try:
        from backend import scheduler as scheduler_module
        if scheduler_module.scheduler:
            scheduler_module.scheduler.shutdown()
    except Exception:
        pass
    try:
        from backend.kite.ticker import get_ticker_manager
        get_ticker_manager().stop()
    except Exception:
        pass
    # Phase 7 — graceful shutdown of the Telegram worker so the
    # MTProto disconnect lands cleanly. No-op when the worker
    # never started.
    if settings.news_events_enabled and settings.telegram_enabled:
        try:
            from backend.news_events.workers.telegram_worker import (
                stop_telegram_worker,
            )
            await stop_telegram_worker()
        except Exception:
            pass


@app.get("/health")
def health_check():
    """Health check — verifies app, database, Redis, and reports AI/broker mock mode."""
    db_status = "ok"
    try:
        db = SessionLocal()
        db.execute(text("SELECT 1"))
        db.close()
    except Exception as e:
        db_status = f"error: {str(e)}"

    redis_status = "ok"
    try:
        redis_client.ping()
    except Exception as e:
        redis_status = f"error: {str(e)}"

    return {
        "status": "ok" if db_status == "ok" else "degraded",
        "version": settings.app_version,
        "environment": settings.app_env,
        "database": db_status,
        "redis": redis_status,
        "mock_mode": {
            "kite": not bool(settings.kite_api_key),
            "openai": not bool(settings.openai_api_key),
            "azure": not bool(settings.azure_key),
        },
    }


@app.get("/")
def root():
    return {"message": "Pivot API is running", "docs": "/docs", "health": "/health"}
