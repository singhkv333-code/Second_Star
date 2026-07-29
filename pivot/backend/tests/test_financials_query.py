"""Integration tests for :mod:`backend.services.financials_query`.

These probe the *real* financials DB (Azure PG, `mc.*`) via a rebound
:class:`FinancialsSessionLocal`. The project conftest pins ``APP_ENV=test``
which routes the app's `FinancialsSessionLocal` at the sqlite test DB — no
real data lives there. So a module-scoped fixture creates a fresh SQLAlchemy
engine from the ``FINANCIALS_READ_DSN``/``FINANCIALS_DSN`` in ``.env`` and
rebinds every module attribute that reads it: `backend.database`,
`backend.market.financials_db`, and `backend.services.financials_query`.

The whole suite skips when no Postgres DSN is resolvable, so CI (which
doesn't ship Azure creds) passes on skip rather than failing to import.
"""
from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any

import pytest


# ── DSN discovery (independent of the app config) ────────────────────────


def _dsn_from_env_or_file() -> str | None:
    """Resolve a live Postgres DSN for the financials DB.

    Order:
      1. ``FINANCIALS_READ_DSN`` env override (matches the app's preference).
      2. ``FINANCIALS_DSN`` env override.
      3. The same-named lines in the repo ``.env``.
    Returns ``None`` if nothing usable is found.
    """
    for var in ("FINANCIALS_READ_DSN", "FINANCIALS_DSN"):
        v = os.environ.get(var)
        if v and v.startswith(("postgresql://", "postgres://", "postgresql+")):
            return v

    # Walk up from this file: backend/tests/ → backend/ → pivot/
    env_path = Path(__file__).resolve().parents[2] / ".env"
    if not env_path.is_file():
        return None
    read_dsn: str | None = None
    write_dsn: str | None = None
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith("FINANCIALS_READ_DSN="):
            _, _, val = line.partition("=")
            val = val.strip().strip('"').strip("'")
            if val:
                read_dsn = val
        elif line.startswith("FINANCIALS_DSN="):
            _, _, val = line.partition("=")
            val = val.strip().strip('"').strip("'")
            if val:
                write_dsn = val
    return read_dsn or write_dsn


_DSN = _dsn_from_env_or_file()

pytestmark = pytest.mark.skipif(
    _DSN is None,
    reason=(
        "No FINANCIALS_DSN / FINANCIALS_READ_DSN configured — skipping live "
        "financials integration tests"
    ),
)


# ── Rebind FinancialsSessionLocal to the real DSN for this module ────────


@pytest.fixture(scope="module", autouse=True)
def _rebind_financials_session():
    """Point the app's FinancialsSessionLocal at the real Azure DSN for the
    lifetime of this module. Restores the original bindings after teardown so
    other integration tests in the same suite aren't affected."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    assert _DSN is not None  # skipif above guarantees this

    engine = create_engine(_DSN, pool_pre_ping=True, pool_recycle=300)
    real_session = sessionmaker(
        bind=engine, autocommit=False, autoflush=False,
    )

    # Every module that captured `FinancialsSessionLocal` at import time
    # needs to be rebound — module attributes are already-resolved names.
    from backend import database as db_mod
    from backend.market import financials_db as fdb_mod
    from backend.services import financials_query as fq_mod

    originals: list[tuple[Any, str, Any]] = [
        (db_mod, "FinancialsSessionLocal", db_mod.FinancialsSessionLocal),
        (fdb_mod, "FinancialsSessionLocal", fdb_mod.FinancialsSessionLocal),
        (fq_mod, "FinancialsSessionLocal", fq_mod.FinancialsSessionLocal),
    ]
    for mod, name, _orig in originals:
        setattr(mod, name, real_session)

    try:
        yield
    finally:
        for mod, name, orig in originals:
            setattr(mod, name, orig)
        engine.dispose()


def _run(coro):
    return asyncio.run(coro)


# ── Tests ────────────────────────────────────────────────────────────────


def test_reliance_net_profit_max_returns_year_and_value():
    """agg=max on net_profit for RELIANCE must return a real year + value."""
    from backend.services.financials_query import query_financials

    result = _run(query_financials({
        "symbols": ["RELIANCE"],
        "fields": ["net_profit"],
        "agg": "max",
        "years": 10,
    }))

    assert "symbols" in result
    reliance = result["symbols"].get("RELIANCE")
    assert reliance is not None, result
    assert "error" not in reliance, reliance
    field = reliance["fields"]["net_profit"]
    assert field.get("value") is not None, field
    assert field.get("period_end"), field
    assert float(field["value"]) > 0
    # period_end is an ISO date string.
    assert isinstance(field["period_end"], str) and "-" in field["period_end"]


def test_unknown_field_raises_value_error_naming_valid_fields():
    """Unknown field → ValueError whose message includes the canonical vocab,
    so the LLM can self-repair on the next turn without a second exchange."""
    from backend.services.financials_query import query_financials

    with pytest.raises(ValueError) as ei:
        _run(query_financials({
            "symbols": ["RELIANCE"],
            "fields": ["totally_bogus_field"],
        }))
    msg = str(ei.value)
    assert "totally_bogus_field" in msg
    # At least one canonical field name has to show up in the error.
    assert any(f in msg for f in ("revenue", "net_profit", "roe", "pe"))


def test_unknown_symbol_returns_per_symbol_error_not_global_failure():
    """A bad symbol becomes a per-symbol error entry — the good symbol still
    returns data. One bad input must never fail the whole call."""
    from backend.services.financials_query import query_financials

    result = _run(query_financials({
        "symbols": ["RELIANCE", "ZZZNOTASTOCK"],
        "fields": ["revenue"],
        "agg": "latest",
    }))

    good = result["symbols"].get("RELIANCE")
    bad = result["symbols"].get("ZZZNOTASTOCK")
    assert good is not None and bad is not None, result

    assert bad.get("error"), "expected per-symbol error entry on unknown symbol"

    assert "error" not in good, good
    assert good["fields"]["revenue"].get("value") is not None


def test_tcs_revenue_cagr_returns_number():
    """CAGR over TCS revenue for the last 5 years should compute cleanly."""
    from backend.services.financials_query import query_financials

    result = _run(query_financials({
        "symbols": ["TCS"],
        "fields": ["revenue"],
        "agg": "cagr",
        "years": 5,
    }))
    tcs = result["symbols"].get("TCS")
    assert tcs is not None, result
    assert "error" not in tcs, tcs
    field = tcs["fields"]["revenue"]
    if field.get("value") is None:
        # Legitimate null path — must carry a note explaining why.
        assert field.get("note"), field
    else:
        assert isinstance(field["value"], (int, float))
        assert field.get("unit") == "%"
        # Sanity: revenues have gone up; a real number, not fabricated,
        # sits within [-100, 200]% CAGR — well outside a broken arithmetic.
        assert -100.0 < float(field["value"]) < 200.0
        # Endpoints must be surfaced so the LLM can narrate accurately.
        assert field.get("start_period_end") and field.get("end_period_end")
