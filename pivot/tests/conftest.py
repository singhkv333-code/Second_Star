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
