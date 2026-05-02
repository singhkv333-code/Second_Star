from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text
import logging

from backend.config import settings
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
from backend.routers.kite import router as kite_router
from backend.routers.compare import router as compare_router
from backend.routers.expr_backtest import router as expr_backtest_router
from backend.routers.workflows import router as workflows_router
from backend.routers.runs import router as runs_router
from backend.routers.approvals import router as approvals_router
from backend.routers.webhooks import router as webhooks_router
from backend.routers.run_stream import router as run_stream_router
from backend.routers.scheduled import router as scheduled_router
from backend.routers.markets import router as markets_router
from backend.routers.conversations import router as conversations_router

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

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
app.include_router(compare_router)
app.include_router(expr_backtest_router)
# scheduled_router MUST mount before workflows_router — otherwise the
# more-specific path /api/workflows/scheduled-runs gets caught by the
# /api/workflows/{id} route in workflows_router.
app.include_router(scheduled_router)
app.include_router(markets_router)
app.include_router(conversations_router)
app.include_router(workflows_router)
app.include_router(runs_router)
app.include_router(approvals_router)
app.include_router(webhooks_router)
app.include_router(run_stream_router)


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
        logger.info(
            f"[{format_ist(now_ist())}] "
            f"Pivot backend started. Scheduler running on IST."
        )
    except Exception as e:
        logger.warning(f"Scheduler failed to start: {e}")


@app.on_event("shutdown")
async def shutdown():
    try:
        from backend import scheduler as scheduler_module
        if scheduler_module.scheduler:
            scheduler_module.scheduler.shutdown()
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
            "sarvam": not bool(settings.sarvam_api_key),
            "openai": not bool(settings.openai_api_key),
        },
    }


@app.get("/")
def root():
    return {"message": "Pivot API is running", "docs": "/docs", "health": "/health"}
