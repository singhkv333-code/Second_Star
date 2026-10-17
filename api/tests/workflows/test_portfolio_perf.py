"""Tests for /api/portfolio/performance (#49).

Holdings are mocked via Kite mock-mode (the env in conftest.py forces
KITE_MOCK_MODE=True since KITE_API_KEY=""). yfinance is mocked here so
tests don't hit the network.
"""
from __future__ import annotations

import threading
from unittest.mock import patch

import pandas as pd
from fastapi.testclient import TestClient


class _FakeTicker:
    def __init__(self, hist: pd.DataFrame) -> None:
        self.info: dict = {}
        self._hist = hist

    def history(self, period: str = "1y", interval: str = "1d") -> pd.DataFrame:
        return self._hist


def _series(values: list[float]) -> pd.DataFrame:
    return pd.DataFrame(
        {"Close": values},
        index=pd.to_datetime([f"2025-{(i % 12) + 1:02d}-01" for i in range(len(values))]),
    )


def test_performance_unauth(client: TestClient) -> None:
    r = client.get("/api/portfolio/performance")
    assert r.status_code == 401


def test_performance_happy_path(
    client: TestClient, auth_headers: dict[str, str],
) -> None:
    """All five mock holdings get a series — portfolio value is the
    weighted sum, and starting/ending values match the math."""
    # Each ticker returns a 3-point rising close series, scaled so the
    # final-day total has a known value.
    def per_symbol(sym: str) -> _FakeTicker:
        return _FakeTicker(_series([100.0, 110.0, 120.0]))

    with patch(
        "backend.routers.portfolio_perf.yf.Ticker", side_effect=per_symbol,
    ):
        r = client.get(
            "/api/portfolio/performance?period=1Y", headers=auth_headers,
        )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["period"] == "1Y"
    assert len(body["points"]) == 3
    # Mock holdings: 10+5+20+50+30 = 115 shares × 100 = 11500 starting,
    # × 120 = 13800 ending. Total return = 2300 = +20%.
    assert body["starting_value"] == 11500.0
    assert body["ending_value"] == 13800.0
    assert body["total_return"] == 2300.0
    assert body["total_return_pct"] == 20.0


def test_performance_partial_failure_skips_bad_symbol(
    client: TestClient, auth_headers: dict[str, str],
) -> None:
    """One symbol fetch fails — others still produce a valid series.

    The endpoint fetches holdings concurrently (a thread pool, not a
    serial loop — see `routers/portfolio_perf.py`), so the counter below
    is lock-protected: which *particular* holding lands on count==2 is no
    longer deterministic across threads, but the test only asserts on
    "exactly one failed, the rest produced a valid series" — order-
    independent by design.
    """
    counter = {"n": 0}
    lock = threading.Lock()

    def maybe_fail(sym: str) -> _FakeTicker:
        with lock:
            counter["n"] += 1
            n = counter["n"]
        if n == 2:  # exactly one of the five fetches fails
            raise RuntimeError("yfinance: rate limited")
        return _FakeTicker(_series([100.0, 105.0]))

    with patch(
        "backend.routers.portfolio_perf.yf.Ticker", side_effect=maybe_fail,
    ):
        r = client.get(
            "/api/portfolio/performance", headers=auth_headers,
        )
    assert r.status_code == 200
    # The series is non-empty (4/5 holdings contributed).
    assert len(r.json()["points"]) >= 1
    assert r.json()["ending_value"] > 0


def test_performance_all_yfinance_fail_returns_503(
    client: TestClient, auth_headers: dict[str, str],
) -> None:
    def always_fail(sym: str) -> _FakeTicker:
        raise RuntimeError("network down")

    with patch(
        "backend.routers.portfolio_perf.yf.Ticker", side_effect=always_fail,
    ):
        r = client.get(
            "/api/portfolio/performance", headers=auth_headers,
        )
    assert r.status_code == 503
    assert r.json()["error"]["code"] == "not_yet_available"


def test_performance_invalid_period_returns_422(
    client: TestClient, auth_headers: dict[str, str],
) -> None:
    r = client.get(
        "/api/portfolio/performance?period=10Y",
        headers=auth_headers,
    )
    assert r.status_code == 422


def test_performance_no_holdings_returns_404(
    client: TestClient, auth_headers: dict[str, str],
) -> None:
    """Empty holdings → 404 not_found (chart needs at least one position)."""
    with patch(
        "backend.routers.portfolio_perf.get_holdings_cached", return_value=[],
    ):
        r = client.get(
            "/api/portfolio/performance", headers=auth_headers,
        )
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "not_found"
