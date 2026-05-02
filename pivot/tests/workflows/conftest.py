"""Workflows test scaffold.

The repo's top-level tests/conftest.py wires APP_ENV=test, an in-memory
SQLite engine via StaticPool, and the `db` + `client` + `auth_headers`
fixtures. We re-use those wholesale — pytest discovers parent conftests
automatically — and only add a workflow-scoped helper here.

Sync sessions only (psycopg2 in prod; SQLite in tests). Async access to
the DB is forbidden in this codebase per docs/ARCHITECTURE.md §3.
"""
from __future__ import annotations

from typing import Iterator

import pytest
from sqlalchemy.orm import Session

# Engine modules that hold their own SessionLocal binding need to be
# rebound to the in-memory test session when tests fire async runs in
# the background (the FastAPI request DI is overridden but background
# tasks open their own session via `SessionLocal()`).
from backend.workflows import engine as engine_mod
from tests.conftest import TestSessionLocal


@pytest.fixture(autouse=True)
def _bind_engine_to_test_db(
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[None]:
    """Repoint the engine's module-level SessionLocal at the in-memory
    test DB so background `engine.execute_run` tasks see the same
    rows the API tests just inserted via the dependency-overridden
    request session.

    Also replace the inter-retry sleep with a no-op so flaky-fail tests
    don't burn 16-second backoffs."""
    monkeypatch.setattr(engine_mod, "SessionLocal", TestSessionLocal)
    monkeypatch.setattr(engine_mod, "_engine_sleep", lambda s: None)
    yield


@pytest.fixture
def workflow_db(db: Session) -> Iterator[Session]:
    """Alias for the parent `db` fixture. Exists so workflow tests read
    self-documenting (`workflow_db` reads better than `db` when the test
    is exercising workflow tables specifically) and so we have a clear
    seam to layer per-suite seeding later (e.g. seed a baseline user)."""
    yield db
