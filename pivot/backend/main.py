from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
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
app.include_router(workflows_router)


@app.on_event("startup")
async def startup():
    """Start the SIP/strategy scheduler. All times in IST."""
    from backend.scheduler import init_scheduler
    from backend.utils.time_utils import format_ist, now_ist

    try:
        init_scheduler(database_url=settings.database_url)
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
