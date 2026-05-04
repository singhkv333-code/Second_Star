"""Tests for markets endpoints (#44) — indices / quote / sparkline.

We mock `yfinance.Ticker` so tests are deterministic and don't hit
the network. The endpoints themselves don't have user-scoped state;
testing concentrates on the request/response shape contract + the
yfinance-failure path that surfaces as `not_yet_available`.
"""
from __future__ import annotations

from unittest.mock import patch

import pandas as pd
from fastapi.testclient import TestClient


# ── /auth/me ─────────────────────────────────────────────────────────


def test_auth_me_unauth(client: TestClient) -> None:
    resp = client.get("/auth/me")
    assert resp.status_code == 401


def test_auth_me_returns_user(
    client: TestClient, auth_headers: dict[str, str],
) -> None:
    resp = client.get("/auth/me", headers=auth_headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "email" in body
    assert "id" in body
    assert body["is_active"] is True


# ── Helpers for mocking yfinance ─────────────────────────────────────


class _FakeTicker:
    def __init__(self, info: dict, hist: pd.DataFrame) -> None:
        self.info = info
        self._hist = hist

    def history(self, period: str = "1y", interval: str = "1d") -> pd.DataFrame:
        return self._hist


def _two_day_hist(close_today: float, close_yesterday: float) -> pd.DataFrame:
    return pd.DataFrame({
        "Open": [close_yesterday * 0.99, close_today * 0.99],
        "High": [close_yesterday * 1.01, close_today * 1.01],
        "Low":  [close_yesterday * 0.98, close_today * 0.98],
        "Close": [close_yesterday, close_today],
        "Volume": [1_000_000, 1_500_000],
    }, index=pd.to_datetime(["2026-05-01", "2026-05-02"]))


# ── /api/markets/indices ─────────────────────────────────────────────


def test_indices_unauth(client: TestClient) -> None:
    resp = client.get("/api/markets/indices")
    assert resp.status_code == 401


def test_indices_happy_path(
    client: TestClient, auth_headers: dict[str, str],
) -> None:
    """All four indices return — yfinance is mocked to succeed."""
    mock = lambda sym: _FakeTicker(  # noqa: E731
        info={}, hist=_two_day_hist(24142.10, 24180.50),
    )
    with patch("backend.routers.markets.yf.Ticker", side_effect=mock):
        resp = client.get("/api/markets/indices", headers=auth_headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "items" in body
    assert len(body["items"]) == 4
    for item in body["items"]:
        assert "name" in item
        assert "symbol" in item
        assert "value" in item
        assert "change" in item
        assert "change_pct" in item
    names = [it["name"] for it in body["items"]]
    assert "NIFTY 50" in names
    assert "SENSEX" in names


def test_indices_partial_failure_returns_only_successful(
    client: TestClient, auth_headers: dict[str, str],
) -> None:
    """If yfinance fails for some indices but not all, return what we got."""
    counter = {"n": 0}
    def maybe_fail(sym: str) -> _FakeTicker:
        counter["n"] += 1
        if counter["n"] in (2, 3):  # 2nd + 3rd ticker fail
            raise RuntimeError("yfinance: rate limited")
        return _FakeTicker(info={}, hist=_two_day_hist(100, 99))
    with patch("backend.routers.markets.yf.Ticker", side_effect=maybe_fail):
        resp = client.get("/api/markets/indices", headers=auth_headers)
    assert resp.status_code == 200
    assert len(resp.json()["items"]) == 2  # 2 succeeded


def test_indices_all_fail_returns_503(
    client: TestClient, auth_headers: dict[str, str],
) -> None:
    def always_fail(sym: str) -> _FakeTicker:
        raise RuntimeError("network down")
    with patch("backend.routers.markets.yf.Ticker", side_effect=always_fail):
        resp = client.get("/api/markets/indices", headers=auth_headers)
    assert resp.status_code == 503
    body = resp.json()
    assert body["error"]["code"] == "not_yet_available"


# ── /api/markets/quote/{symbol} ──────────────────────────────────────


def test_quote_unauth(client: TestClient) -> None:
    resp = client.get("/api/markets/quote/RELIANCE")
    assert resp.status_code == 401


def test_quote_happy_path(
    client: TestClient, auth_headers: dict[str, str],
) -> None:
    info = {
        "longName": "Reliance Industries Limited",
        "sector": "Energy",
        "industry": "Oil & Gas Refining & Marketing",
        "fiftyTwoWeekHigh": 3024.9,
        "fiftyTwoWeekLow": 2220.3,
        "marketCap": 1_980_000_000_000,
        "trailingPE": 28.4,
    }
    hist = _two_day_hist(2934.55, 2894.05)
    with patch(
        "backend.routers.markets.yf.Ticker",
        return_value=_FakeTicker(info, hist),
    ):
        resp = client.get(
            "/api/markets/quote/RELIANCE", headers=auth_headers,
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["symbol"] == "RELIANCE"
    assert body["name"] == "Reliance Industries Limited"
    assert body["sector"] == "Energy"
    assert body["ltp"] == 2934.55
    assert body["change"] == round(2934.55 - 2894.05, 2)
    assert body["w52_high"] == 3024.9
    assert body["pe_ratio"] == 28.4


def test_quote_unknown_symbol_returns_404(
    client: TestClient, auth_headers: dict[str, str],
) -> None:
    """yfinance returning an empty history → 404."""
    empty_hist = pd.DataFrame(columns=["Open", "High", "Low", "Close", "Volume"])
    with patch(
        "backend.routers.markets.yf.Ticker",
        return_value=_FakeTicker(info={}, hist=empty_hist),
    ):
        resp = client.get(
            "/api/markets/quote/GHOST", headers=auth_headers,
        )
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "not_found"


# ── /api/markets/sparkline/{symbol} ──────────────────────────────────


def test_sparkline_unauth(client: TestClient) -> None:
    resp = client.get("/api/markets/sparkline/RELIANCE")
    assert resp.status_code == 401


def test_sparkline_happy_path(
    client: TestClient, auth_headers: dict[str, str],
) -> None:
    series = pd.DataFrame(
        {"Close": [100.0, 102.5, 101.0, 103.5, 105.0]},
        index=pd.to_datetime([
            "2025-05-01", "2025-08-01", "2025-11-01",
            "2026-02-01", "2026-05-01",
        ]),
    )
    with patch(
        "backend.routers.markets.yf.Ticker",
        return_value=_FakeTicker(info={}, hist=series),
    ):
        resp = client.get(
            "/api/markets/sparkline/RELIANCE?range=1Y",
            headers=auth_headers,
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["symbol"] == "RELIANCE"
    assert body["range"] == "1Y"
    assert len(body["points"]) == 5
    # Each point has {t, v}.
    assert body["points"][-1]["v"] == 105.0


def test_sparkline_default_range_is_1y(
    client: TestClient, auth_headers: dict[str, str],
) -> None:
    series = pd.DataFrame(
        {"Close": [100.0, 110.0]},
        index=pd.to_datetime(["2025-05-01", "2026-05-01"]),
    )
    with patch(
        "backend.routers.markets.yf.Ticker",
        return_value=_FakeTicker(info={}, hist=series),
    ):
        resp = client.get(
            "/api/markets/sparkline/INFY", headers=auth_headers,
        )
    assert resp.status_code == 200
    assert resp.json()["range"] == "1Y"


def test_sparkline_invalid_range_returns_422(
    client: TestClient, auth_headers: dict[str, str],
) -> None:
    resp = client.get(
        "/api/markets/sparkline/INFY?range=10Y",
        headers=auth_headers,
    )
    assert resp.status_code == 422


def test_sparkline_empty_history_returns_404(
    client: TestClient, auth_headers: dict[str, str],
) -> None:
    empty = pd.DataFrame(columns=["Close"])
    with patch(
        "backend.routers.markets.yf.Ticker",
        return_value=_FakeTicker(info={}, hist=empty),
    ):
        resp = client.get(
            "/api/markets/sparkline/GHOST", headers=auth_headers,
        )
    assert resp.status_code == 404
