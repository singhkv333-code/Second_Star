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
    engine = create_engine(
        settings.database_url,
        poolclass=QueuePool,
        pool_size=10,
        max_overflow=20,
        pool_pre_ping=True,
        echo=settings.app_env == "development",
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
        settings.financials_dsn,
        poolclass=QueuePool,
        pool_size=5,
        max_overflow=10,
        pool_pre_ping=True,
        echo=False,
    )

FinancialsSessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=financials_engine,
)


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
