import os

# Must be set BEFORE importing anything that loads backend.config
os.environ["APP_ENV"] = "test"
os.environ["DATABASE_URL"] = "sqlite:///./pivot_test.db"
os.environ["JWT_SECRET_KEY"] = "test-secret-key-minimum-32-characters-long"
os.environ["REDIS_URL"] = "redis://localhost:6379/0"
# Force mock mode for all external APIs during tests — empty string is falsy
os.environ["KITE_API_KEY"] = ""
os.environ["KITE_API_SECRET"] = ""
os.environ["OPENAI_API_KEY"] = ""
os.environ["AZURE_KEY"] = ""
# Demo seeder runs on /auth/register in dev; disable for tests so a
# freshly-registered user starts truly empty. Tests that exercise the
# seeder explicitly opt-in by un-setting this in their own setup.
os.environ["DEMO_SEED_ON_REGISTER"] = "0"
# Paper trading is ON by default in dev/prod, but pinned OFF in tests so the
# existing engine/action tests exercise the stable Kite-mock execution path
# deterministically. Paper-specific tests opt in (monkeypatch the flag True);
# see tests/test_paper_routing.py.
os.environ["PAPER_TRADING_ENABLED"] = "false"
# Market-hours-aware paper fills are ON in dev/prod, but pinned OFF in tests so
# the deterministic immediate-fill assertions hold regardless of wall-clock.
# The market-hours behaviour is covered by its own opt-in tests.
os.environ["PAPER_RESPECT_MARKET_HOURS"] = "false"
# The deployment .env raises the paper seed to 5L for the beta test; pin the
# test seed to 150000 (os.environ wins over .env in pydantic) so the paper
# broker/account balance assertions stay deterministic.
os.environ["PAPER_SEED_CAPITAL"] = "150000"

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from fastapi.testclient import TestClient
from backend.database import Base, get_db
from backend.main import app

# Shared in-memory SQLite: all sessions see the same data
TEST_DATABASE_URL = "sqlite:///:memory:"
test_engine = create_engine(
    TEST_DATABASE_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)


@pytest.fixture(scope="session", autouse=True)
def create_test_database():
    """Create all tables in the test database before tests, drop after."""
    # Import models so Base.metadata knows about every table
    from backend import models  # noqa: F401
    Base.metadata.create_all(bind=test_engine)
    yield
    Base.metadata.drop_all(bind=test_engine)


@pytest.fixture
def db():
    """Fresh database session for each test, rolled back after."""
    connection = test_engine.connect()
    transaction = connection.begin()
    session = TestSessionLocal(bind=connection)
    yield session
    session.close()
    transaction.rollback()
    connection.close()


@pytest.fixture
def client(db):
    """Test client with database dependency overridden."""
    def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture
def auth_headers(client):
    """Register a fresh user and return Authorization headers with a valid token."""
    import uuid
    email = f"u_{uuid.uuid4().hex[:8]}@pivot.com"
    r = client.post(
        "/auth/register",
        json={"email": email, "password": "password123", "full_name": "Test"},
    )
    assert r.status_code == 201, r.text
    token = r.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture(autouse=True)
def _clear_portfolio_cache():
    """Redis is a real, un-isolated external process (see REDIS_URL above),
    but the `db` fixture rolls back every test's transaction — so a
    numeric user id (e.g. 1, the first autoincrement value) gets reused
    across many tests. Without this, a portfolio-cache entry (short TTL,
    keyed only by user_id/period) written by one test can leak into a
    later test that reuses the same id and expects different mocked
    upstream data within the TTL window. Scoped to the `portfolio:*`
    namespace so it can't touch any other subsystem's cache.
    """
    from backend.cache import redis_client
    try:
        for key in redis_client.keys("portfolio:*"):
            redis_client.delete(key)
    except Exception:
        pass
    yield


@pytest.fixture(autouse=True)
def _clear_markets_yf_cache():
    """Same real-Redis cross-test leak as `_clear_portfolio_cache`, for the
    markets-router yfinance caches (quote/sparkline/ohlc). A symbol like
    RELIANCE is reused across many tests with different mocked `yf.Ticker`
    payloads (e.g. a bare-bones mock without `longName` vs. a full one) — the
    fixed-TTL cache entry from whichever test ran first would otherwise leak
    into every later test hitting the same symbol within the TTL window.
    Scoped to the three yfinance-fallback prefixes so it can't touch any
    other subsystem's cache.
    """
    from backend.cache import redis_client
    try:
        for prefix in (
            "quote:yf:v1:", "sparkline:yf:v1:", "ohlc:yf:v1:",
            # Kite tick cache (backend/kite/ticker.py's cache_key prefix,
            # read by markets.py's _read_cached_kite_tick). Seeded directly
            # by backend/tests/test_quotes_ws.py with a 90s Redis TTL, but
            # only a 5s freshness window (_KITE_TICK_FRESH_SECONDS) — narrow
            # enough that it's usually harmless, but a fast-running test
            # combo can land inside that 5s window and get served a stale
            # tick (no company name) instead of exercising its own mock.
            "price:",
        ):
            for key in redis_client.keys(f"{prefix}*"):
                redis_client.delete(key)
    except Exception:
        pass
    yield
