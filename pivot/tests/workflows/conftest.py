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


@pytest.fixture
def workflow_db(db: Session) -> Iterator[Session]:
    """Alias for the parent `db` fixture. Exists so workflow tests read
    self-documenting (`workflow_db` reads better than `db` when the test
    is exercising workflow tables specifically) and so we have a clear
    seam to layer per-suite seeding later (e.g. seed a baseline user)."""
    yield db
