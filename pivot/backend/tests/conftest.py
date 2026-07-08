"""Re-use the project-level conftest from `pivot/tests/conftest.py`.

We keep tests for the Kite ticker / WS modules in this directory so
they live next to the code they test, but they need the same env-var
setup (mock mode + sqlite) the top-level conftest performs at import.
"""
from __future__ import annotations

# Importing the project conftest as a side-effect: it patches os.environ
# and exposes fixtures (`client`, `db`, `auth_headers`).
from tests.conftest import (  # noqa: F401
    client,
    db,
    auth_headers,
    create_test_database,
)
