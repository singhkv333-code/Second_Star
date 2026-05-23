"""HTTP-surface tests for /api/backtest/dsl/*.

Uses the small-app pattern from elsewhere in the repo. The engine
is monkey-patched so the tests don't hit yfinance.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.database import get_db
from backend.routers.backtest_dsl import router as bt_router
from backend.workflows.dsl.backtest.schema import (
    BacktestDiagnostics,
    BacktestMetrics,
    BacktestResult,
)


# Bring the ORM model into Base.metadata so the session-scoped
# fixture in the top-level conftest creates the table for tests.
# (Mirrors the news_events tests/conftest.py pattern.)
from backend.workflows.dsl.backtest import models as _models  # noqa: F401


def _build_client(db):
    app = FastAPI()
    app.include_router(bt_router)

    def _override():
        yield db
    app.dependency_overrides[get_db] = _override
    return TestClient(app)


def _stub_result(*, request_id: str = "stub-id") -> BacktestResult:
    """Build a BacktestResult shaped object the patched engine can
    return without any pandas work."""
    return BacktestResult(
        request_id=request_id,
        user_id=1,
        requested_at=datetime.now(timezone.utc),
        completed_at=datetime.now(timezone.utc),
        tree_summary="RSI(14) of TCS < 30",
        request=_basic_request(),
        trades=[],
        equity_curve=[],
        metrics=BacktestMetrics(
            total_return_pct=0.0, cagr_pct=0.0,
            max_drawdown_pct=0.0, max_drawdown_duration_days=0,
            win_rate_pct=0.0, total_trades=0,
            winning_trades=0, losing_trades=0,
            ending_value=100_000.0,
        ),
        diagnostics=BacktestDiagnostics(
            bars_evaluated=0, warmup_bars_skipped=0,
            unknown_value_bars=0, fire_bars=0,
            symbols_loaded=["TCS:NSE"], indicator_cache_keys=[],
        ),
    )


def _basic_request() -> "Any":
    from backend.workflows.dsl.backtest.schema import BacktestRequest
    return BacktestRequest(
        tree={
            "type": "comparison", "op": "<",
            "left": {"type": "indicator", "indicator": "rsi",
                     "symbol": "TCS", "period": 14},
            "right": {"type": "constant", "value": 30},
        },
        primary_symbol="TCS",
        start_date=date(2024, 1, 1),
        end_date=date(2024, 12, 31),
        starting_capital=100_000.0,
        quantity=10,
        save=False,
    )


def _body(**override) -> dict:
    body = _basic_request().model_dump(mode="json")
    body.update(override)
    return body


# ── Auth ────────────────────────────────────────────────────────────


def test_run_requires_auth(db):
    client = _build_client(db)
    r = client.post("/api/backtest/dsl/run", json=_body())
    assert r.status_code == 401


def test_list_requires_auth(db):
    client = _build_client(db)
    r = client.get("/api/backtest/dsl/runs")
    assert r.status_code == 401


# ── Validation ─────────────────────────────────────────────────────


def test_run_422_on_bad_tree(db, auth_headers):
    client = _build_client(db)
    bad = _body(tree={"type": "comparison", "op": "approximately"})
    r = client.post(
        "/api/backtest/dsl/run", json=bad, headers=auth_headers,
    )
    assert r.status_code == 422


def test_run_422_on_vacuous_comparison(db, auth_headers):
    """Both sides constant — semantic_validate rejects with 422."""
    client = _build_client(db)
    body = _body(tree={
        "type": "comparison", "op": "<",
        "left": {"type": "constant", "value": 1},
        "right": {"type": "constant", "value": 2},
    })
    r = client.post(
        "/api/backtest/dsl/run", json=body, headers=auth_headers,
    )
    assert r.status_code == 422
    assert "Vacuous" in r.text or "vacuous" in r.text


def test_run_422_on_inverted_date_range(db, auth_headers):
    client = _build_client(db)
    body = _body(start_date="2024-12-01", end_date="2024-01-01")
    r = client.post(
        "/api/backtest/dsl/run", json=body, headers=auth_headers,
    )
    assert r.status_code == 422


# ── Happy path ─────────────────────────────────────────────────────


def test_run_returns_result_with_persisted_id(db, auth_headers, monkeypatch):
    """Patched engine returns a stub result; row persisted."""
    client = _build_client(db)

    def _fake_run(*, request, user_id, fetcher=None):
        return _stub_result()

    monkeypatch.setattr(
        "backend.routers.backtest_dsl.run_backtest", _fake_run
    )

    body = _body(save=True)
    r = client.post(
        "/api/backtest/dsl/run", json=body, headers=auth_headers,
    )
    assert r.status_code == 200, r.text
    result = r.json()
    # Persisted runs return the row UUID as the result's request_id.
    assert result["request_id"]
    assert result["tree_summary"] == "RSI(14) of TCS < 30"
    assert result["metrics"]["total_trades"] == 0

    # List endpoint includes the persisted row.
    list_resp = client.get(
        "/api/backtest/dsl/runs", headers=auth_headers,
    )
    assert list_resp.status_code == 200
    runs = list_resp.json()["runs"]
    assert any(run["id"] == result["request_id"] for run in runs)


def test_run_save_false_does_not_persist(db, auth_headers, monkeypatch):
    client = _build_client(db)

    def _fake_run(*, request, user_id, fetcher=None):
        return _stub_result()

    monkeypatch.setattr(
        "backend.routers.backtest_dsl.run_backtest", _fake_run
    )

    body = _body(save=False)
    r = client.post(
        "/api/backtest/dsl/run", json=body, headers=auth_headers,
    )
    assert r.status_code == 200
    list_resp = client.get(
        "/api/backtest/dsl/runs", headers=auth_headers,
    )
    # Nothing persisted.
    assert list_resp.json()["runs"] == []


# ── Engine-failure path ────────────────────────────────────────────


def test_engine_value_error_returns_422(db, auth_headers, monkeypatch):
    client = _build_client(db)

    def _fake_run(*, request, user_id, fetcher=None):
        raise ValueError("no bars returned for TCS")

    monkeypatch.setattr(
        "backend.routers.backtest_dsl.run_backtest", _fake_run
    )
    r = client.post(
        "/api/backtest/dsl/run", json=_body(save=True), headers=auth_headers,
    )
    assert r.status_code == 422

    # The persisted row should be in status='failed' with the error.
    list_resp = client.get(
        "/api/backtest/dsl/runs", headers=auth_headers,
    )
    runs = list_resp.json()["runs"]
    assert len(runs) == 1
    assert runs[0]["status"] == "failed"


# ── Cross-user 404 ─────────────────────────────────────────────────


def test_cross_user_get_returns_404(db, auth_headers, monkeypatch):
    """User A creates a run; user B fetches it → 404."""
    client = _build_client(db)

    def _fake_run(*, request, user_id, fetcher=None):
        return _stub_result()
    monkeypatch.setattr(
        "backend.routers.backtest_dsl.run_backtest", _fake_run
    )

    r = client.post(
        "/api/backtest/dsl/run", json=_body(save=True),
        headers=auth_headers,
    )
    run_id = r.json()["request_id"]

    # Mint a different user's token.
    from backend.auth.jwt_handler import create_access_token
    other = {"Authorization": f"Bearer {create_access_token(user_id=9999, email='o@p.com')}"}
    r2 = client.get(f"/api/backtest/dsl/runs/{run_id}", headers=other)
    assert r2.status_code == 404


# ── Cancel ─────────────────────────────────────────────────────────


def test_cancel_succeeded_run_is_idempotent_noop(db, auth_headers, monkeypatch):
    """Cancel on a terminal-state row returns the row unchanged
    (status stays 'succeeded')."""
    client = _build_client(db)

    def _fake_run(*, request, user_id, fetcher=None):
        return _stub_result()
    monkeypatch.setattr(
        "backend.routers.backtest_dsl.run_backtest", _fake_run
    )

    r = client.post(
        "/api/backtest/dsl/run", json=_body(save=True),
        headers=auth_headers,
    )
    run_id = r.json()["request_id"]
    r2 = client.post(
        f"/api/backtest/dsl/runs/{run_id}/cancel", headers=auth_headers,
    )
    assert r2.status_code == 200
    assert r2.json()["status"] == "succeeded"   # unchanged


def test_get_run_returns_full_result(db, auth_headers, monkeypatch):
    client = _build_client(db)

    def _fake_run(*, request, user_id, fetcher=None):
        return _stub_result()
    monkeypatch.setattr(
        "backend.routers.backtest_dsl.run_backtest", _fake_run
    )

    r = client.post(
        "/api/backtest/dsl/run", json=_body(save=True),
        headers=auth_headers,
    )
    run_id = r.json()["request_id"]
    r2 = client.get(
        f"/api/backtest/dsl/runs/{run_id}", headers=auth_headers,
    )
    assert r2.status_code == 200
    body = r2.json()
    assert body["id"] == run_id
    assert body["status"] == "succeeded"
    assert body["result"]["tree_summary"] == "RSI(14) of TCS < 30"
