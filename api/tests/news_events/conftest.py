"""Per-package conftest for news_events tests.

Imports the news_events models at collection time so the session-scoped
``create_test_database`` fixture (top-level conftest.py) sees the new
tables in ``Base.metadata`` before it calls ``create_all``.

Without this import, the news_events tables would only be registered on
the live backend path (which is itself flag-gated), and the test DB
would never get them.
"""
from __future__ import annotations

# noqa: F401 — registration side effect only.
from backend.news_events import models as _models  # noqa: F401
