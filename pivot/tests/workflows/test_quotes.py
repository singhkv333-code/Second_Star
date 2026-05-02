"""Tests for /api/quotes/index/{symbol}/history (#50).

Thin alias over markets/sparkline that resolves friendly index names
(NIFTY50, SENSEX, BANKNIFTY, NIFTYMIDCAP100) to yfinance ^-symbols.
"""
from __future__ import annotations

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
        index=pd.to_datetime([f"2026-{i+1:02d}-01" for i in range(len(values))]),
    )


def test_index_history_unauth(client: TestClient) -> None:
    r = client.get("/api/quotes/index/NIFTY50/history")
    assert r.status_code == 401


def test_index_history_resolves_nifty50(
    client: TestClient, auth_headers: dict[str, str],
) -> None:
    """NIFTY50 must resolve to ^NSEI when calling yfinance underneath."""
    captured: dict[str, str] = {}

    def capturing_ticker(sym: str) -> _FakeTicker:
        captured["sym"] = sym
        return _FakeTicker(_series([100.0, 110.0, 120.0]))

    with patch(
        "backend.routers.markets.yf.Ticker",
        side_effect=capturing_ticker,
    ):
        r = client.get(
            "/api/quotes/index/NIFTY50/history?period=1Y",
            headers=auth_headers,
        )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["symbol"] == "^NSEI"
    assert body["range"] == "1Y"
    assert len(body["points"]) == 3
    assert captured["sym"] == "^NSEI"


def test_index_history_resolves_sensex(
    client: TestClient, auth_headers: dict[str, str],
) -> None:
    captured: dict[str, str] = {}

    def capturing(sym: str) -> _FakeTicker:
        captured["sym"] = sym
        return _FakeTicker(_series([1.0, 2.0]))

    with patch("backend.routers.markets.yf.Ticker", side_effect=capturing):
        r = client.get(
            "/api/quotes/index/SENSEX/history",
            headers=auth_headers,
        )
    assert r.status_code == 200
    assert captured["sym"] == "^BSESN"
    assert r.json()["range"] == "1Y"  # default period


def test_index_history_unknown_symbol_returns_404(
    client: TestClient, auth_headers: dict[str, str],
) -> None:
    r = client.get(
        "/api/quotes/index/MADE_UP_INDEX/history",
        headers=auth_headers,
    )
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "not_found"


def test_index_history_invalid_period_returns_422(
    client: TestClient, auth_headers: dict[str, str],
) -> None:
    r = client.get(
        "/api/quotes/index/NIFTY50/history?period=10Y",
        headers=auth_headers,
    )
    assert r.status_code == 422


def test_index_history_passthrough_for_caret_symbols(
    client: TestClient, auth_headers: dict[str, str],
) -> None:
    """Advanced users can pass ^NSEBANK directly; should not 404."""
    captured: dict[str, str] = {}

    def capturing(sym: str) -> _FakeTicker:
        captured["sym"] = sym
        return _FakeTicker(_series([1.0]))

    with patch("backend.routers.markets.yf.Ticker", side_effect=capturing):
        r = client.get(
            "/api/quotes/index/^NSEBANK/history",
            headers=auth_headers,
        )
    assert r.status_code == 200
    assert captured["sym"] == "^NSEBANK"
