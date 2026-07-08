"""
Tests for the Compare feature: data layer, chart_parser, and /compare endpoint.

Network-dependent tests (yfinance) skip automatically when yfinance returns
empty (rate-limit or no internet). Pure-logic and HTTP tests always run.
"""
import asyncio

import pytest

from backend.agents.chart_parser import parse_chart_request
from backend.market.yfinance_service import (
    calculate_returns,
    fetch_multi_symbol,
    fetch_price_history,
    normalise_to_base100,
)


def _skip_if_no_data(records, symbol):
    if not records:
        pytest.skip(f"yfinance returned no data for {symbol} (network or rate limit)")


def test_fetch_infy_1y():
    records = fetch_price_history("INFY", "1y", "1wk")
    _skip_if_no_data(records, "INFY")
    assert len(records) >= 40
    keys = {"date", "open", "high", "low", "close", "volume"}
    for r in records:
        assert keys.issubset(r.keys())
    assert all(r["close"] > 0 for r in records)


def test_fetch_nifty_index():
    records = fetch_price_history("NIFTY50", "3m", "1d")
    _skip_if_no_data(records, "NIFTY50")
    assert len(records) > 0
    closes = [r["close"] for r in records]
    assert all(15000 <= c <= 30000 for c in closes), f"Nifty out of expected range: {min(closes)}..{max(closes)}"


def test_normalise_to_base100():
    series = [
        {"date": "2024-01-01", "close": 1500.0},
        {"date": "2024-02-01", "close": 1650.0},
        {"date": "2024-03-01", "close": 1425.0},
    ]
    result = normalise_to_base100(series)
    assert len(result) == 3
    assert result[0]["value"] == 100.0
    assert result[1]["value"] == pytest.approx(110.0, rel=1e-4)
    assert result[2]["value"] == pytest.approx(95.0, rel=1e-4)


def test_calculate_returns():
    series = [
        {"date": "2024-01-01", "close": 100.0},
        {"date": "2024-01-02", "close": 105.0},
        {"date": "2024-01-03", "close": 90.0},
        {"date": "2024-01-04", "close": 95.0},
        {"date": "2024-01-05", "close": 120.0},
    ]
    stats = calculate_returns(series)
    assert stats["total_return_pct"] == pytest.approx(20.0, rel=1e-3)
    assert stats["max_drawdown_pct"] < 0
    assert stats["max_drawdown_pct"] == pytest.approx(((90 - 105) / 105) * 100, rel=1e-3)
    assert stats["best_day_pct"] > 0
    assert stats["worst_day_pct"] < 0
    assert stats["volatility_annualised"] > 0
    assert stats["cagr_pct"] is None  # span < 1 year


def test_calculate_returns_empty():
    assert calculate_returns([])["total_return_pct"] == 0.0
    assert calculate_returns([{"date": "2024-01-01", "close": 100.0}])["total_return_pct"] == 0.0


def test_multi_symbol_alignment():
    aligned = fetch_multi_symbol(["INFY", "TCS"], "3m", "1d")
    assert set(aligned.keys()) == {"INFY", "TCS"}
    if not aligned["INFY"] or not aligned["TCS"]:
        pytest.skip("yfinance returned no data for INFY/TCS (network or rate limit)")
    assert len(aligned["INFY"]) == len(aligned["TCS"])
    infy_dates = {p["date"] for p in aligned["INFY"]}
    tcs_dates = {p["date"] for p in aligned["TCS"]}
    assert infy_dates == tcs_dates


# --- chart_parser ---

def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def test_chart_parser_comparison():
    parsed = _run(parse_chart_request("compare INFY and TCS over 6 months"))
    assert parsed is not None
    assert "INFY" in parsed["symbols"] and "TCS" in parsed["symbols"]
    assert parsed["period"] == "6m"
    assert parsed["chart_type"] == "comparison"
    assert parsed["normalise"] is True


def test_chart_parser_not_chart_request():
    assert _run(parse_chart_request("buy 10 INFY at market")) is None
    assert _run(parse_chart_request("how am I doing")) is None
    assert _run(parse_chart_request("")) is None


def test_chart_parser_index_ytd():
    parsed = _run(parse_chart_request("show me Nifty this year"))
    assert parsed is not None
    assert "NIFTY50" in parsed["symbols"]
    assert parsed["period"] == "ytd"
    assert parsed["chart_type"] == "single"


def test_chart_parser_since_year():
    parsed = _run(parse_chart_request("how has Reliance done since 2022"))
    assert parsed is not None
    assert "RELIANCE" in parsed["symbols"]
    assert parsed["period"] == "max"
    assert parsed["start_date"] == "2022-01-01"


def test_chart_parser_backtest_sip():
    parsed = _run(parse_chart_request(
        "backtest buying NIFTYBEES 5000 every month for 2 years"
    ))
    assert parsed is not None
    assert "NIFTYBEES" in parsed["symbols"]
    assert parsed["period"] == "2y"
    assert parsed["chart_type"] == "backtest"
    assert parsed["sip_amount"] == 5000.0


# --- /compare endpoint ---

def test_compare_endpoint_valid(client, auth_headers):
    r = client.post(
        "/compare",
        headers=auth_headers,
        json={
            "symbols": ["INFY", "TCS"],
            "period": "3m",
            "chart_type": "comparison",
            "normalise": True,
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["chart_type"] == "comparison"
    assert len(body["series"]) == 2
    for s in body["series"]:
        assert {"symbol", "data", "stats"}.issubset(s.keys())
    if not body["series"][0]["data"]:
        pytest.skip("yfinance returned empty data")
    assert body["series"][0]["data"][0]["value"] == 100.0


def test_compare_endpoint_invalid_symbol(client, auth_headers):
    r = client.post(
        "/compare",
        headers=auth_headers,
        json={
            "symbols": ["INVALIDXYZ123"],
            "period": "1m",
            "chart_type": "single",
            "normalise": False,
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["series"][0]["data"] == []
    assert body["series"][0]["note"] and "INVALIDXYZ123" in body["series"][0]["note"]


def test_compare_endpoint_too_many_symbols(client, auth_headers):
    r = client.post(
        "/compare",
        headers=auth_headers,
        json={
            "symbols": ["INFY", "TCS", "RELIANCE", "HDFCBANK", "ICICIBANK", "AXISBANK"],
            "period": "3m",
            "chart_type": "comparison",
            "normalise": True,
        },
    )
    assert r.status_code == 422


def test_compare_endpoint_empty_symbols(client, auth_headers):
    r = client.post(
        "/compare",
        headers=auth_headers,
        json={
            "symbols": [],
            "period": "3m",
            "chart_type": "comparison",
            "normalise": True,
        },
    )
    assert r.status_code == 422


def test_compare_endpoint_unauthenticated(client):
    r = client.post(
        "/compare",
        json={"symbols": ["INFY"], "period": "1m", "chart_type": "single", "normalise": False},
    )
    assert r.status_code == 401
