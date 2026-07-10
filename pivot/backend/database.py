from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, DeclarativeBase
from sqlalchemy.pool import QueuePool
from backend.config import settings


if settings.app_env == "test":
    TEST_DATABASE_URL = "sqlite:///./pivot_test.db"
    engine = create_engine(
        TEST_DATABASE_URL,
        connect_args={"check_same_thread": False},
    )
else:
    # Pool caps are sized against the Azure tier's max_connections=50 (~45
    # usable after the SUPERUSER reserve). Worst case per process across the
    # three engines is 10+7+5 = 22, so one server + one script/migration can
    # coexist. The old caps (30+15+15 = 60) let a single dev server exhaust
    # the whole instance — "remaining connection slots are reserved" took the
    # portfolio page down twice on 2026-07-10. pool_use_lifo lets idle
    # overflow connections age out via pool_recycle instead of being kept
    # warm forever.
    engine = create_engine(
        settings.database_url,
        poolclass=QueuePool,
        pool_size=5,
        max_overflow=5,
        pool_timeout=10,
        pool_use_lifo=True,
        pool_pre_ping=True,   # cloud DB: reconnect if a pooled conn was reaped/dropped
        pool_recycle=900,
        echo=False,
    )

SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine,
)


# Separate read-only engine for the Moneycontrol-derived `financials` DB.
# Lives in the same Postgres instance but is logically distinct: schema `mc.*`,
# written only by pivot-mc-scraper. Kept on its own engine so a slow
# fundamentals query never starves the operational pool.
if settings.app_env == "test":
    financials_engine = create_engine(
        "sqlite:///./pivot_test.db",
        connect_args={"check_same_thread": False},
    )
else:
    financials_engine = create_engine(
        # Prefer the read replica when configured (FINANCIALS_READ_DSN) —
        # the app only ever READS mc.*, so the whole engine can point at a
        # replica while the primary keeps serving scraper/dev writes.
        settings.financials_read_dsn or settings.financials_dsn,
        poolclass=QueuePool,
        pool_size=3,
        max_overflow=4,
        pool_timeout=10,
        pool_use_lifo=True,
        pool_pre_ping=True,   # cloud DB: reconnect if a pooled conn was reaped/dropped
        pool_recycle=900,
        echo=False,
    )

FinancialsSessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=financials_engine,
)


# Separate read-only engine for the yfinance-enriched `pivot_enrich` DB
# (enrich.company_profile / enrich.v_company_enriched). Logically distinct from
# both pivot_db and financials; built by scripts/enrich_company_profiles.py.
# Disabled (None) when ENRICH_DSN is unset so the app runs without it.
if settings.app_env == "test" or not settings.enrich_dsn:
    enrich_engine = None
    EnrichSessionLocal = None
else:
    enrich_engine = create_engine(
        settings.enrich_dsn,
        poolclass=QueuePool,
        pool_size=2,
        max_overflow=3,
        pool_timeout=10,
        pool_use_lifo=True,
        pool_pre_ping=True,   # cloud DB: reconnect if a pooled conn was reaped/dropped
        pool_recycle=900,
        echo=False,
    )
    EnrichSessionLocal = sessionmaker(
        autocommit=False,
        autoflush=False,
        bind=enrich_engine,
    )


# Flag-gated latency tracing (PIVOT_PERF_TRACE) — no-ops when disabled.
from backend.services.perf_trace import install_sqlalchemy  # noqa: E402

install_sqlalchemy(engine, db="pivot")
install_sqlalchemy(financials_engine, db="financials")
install_sqlalchemy(enrich_engine, db="enrich")


class Base(DeclarativeBase):
    pass


def get_db():
    """FastAPI dependency: yields a database session, closes it after request."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_financials_db():
    """FastAPI dependency: yields a read-only session against the financials DB."""
    db = FinancialsSessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_enrich_db():
    """FastAPI dependency: yields a read-only session against the pivot_enrich DB.

    Yields None when ENRICH_DSN is unset (feature disabled) so callers can
    degrade gracefully rather than crash.
    """
    if EnrichSessionLocal is None:
        yield None
        return
    db = EnrichSessionLocal()
    try:
        yield db
    finally:
        db.close()
