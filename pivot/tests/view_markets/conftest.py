"""View Markets test scaffold.

Mirrors ``tests/workflows/conftest.py``. The repo's top-level
``tests/conftest.py`` wires ``APP_ENV=test``, an in-memory SQLite engine via
``StaticPool``, and the ``db`` / ``client`` / ``auth_headers`` fixtures, and its
session-scoped ``create_test_database`` runs ``Base.metadata.create_all`` AFTER
importing ``backend.models`` — so the six View-Markets tables (``market_views``,
``view_expressions``, ``view_transmission``, ``view_confidence``,
``view_expectations``, ``view_follows``) are already created in the test DB. We
re-use those wholesale (pytest discovers parent conftests automatically) and only
add View-Markets-scoped seams here.

Sync sessions only (psycopg2 in prod; SQLite in tests). Async DB access is
forbidden per docs/ARCHITECTURE.md §3 — the lifecycle worker is async but its
DB work is sync inside its own ``SessionLocal``.
"""
from __future__ import annotations

from typing import Iterator

import pytest
from sqlalchemy.orm import Session  # noqa: F401  (used in fixture annotations)

# The lifecycle worker holds its own module-level SessionLocal binding (it opens
# a background session like the APScheduler jobs do). Rebind it to the in-memory
# test session so any direct call to advance_view_lifecycle() in a test sees the
# same rows the request-overridden session inserted.
from backend.view_markets import lifecycle as lifecycle_mod
from tests.conftest import TestSessionLocal


@pytest.fixture(autouse=True)
def _bind_view_markets_engine_to_test_db(
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[None]:
    """Repoint ``lifecycle.SessionLocal`` at the in-memory test DB."""
    monkeypatch.setattr(lifecycle_mod, "SessionLocal", TestSessionLocal)
    yield


@pytest.fixture
def view_db(db: Session) -> Iterator[Session]:
    """Alias for the parent ``db`` fixture, named for self-documenting
    View-Markets tests and as a seam for later per-suite seeding (e.g. a seed
    curated MarketView)."""
    yield db
