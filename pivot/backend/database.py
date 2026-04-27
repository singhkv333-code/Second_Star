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


class Base(DeclarativeBase):
    pass


def get_db():
    """FastAPI dependency: yields a database session, closes it after request."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
