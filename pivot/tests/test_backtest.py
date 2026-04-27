"""Backtest tests — skip if yfinance has no network access."""
import pytest
import httpx


def test_backtest_sip(client, auth_headers):
    try:
        r = client.post(
            "/backtest/run",
            json={
                "symbol": "INFY",
                "strategy_type": "sip",
                "trigger_condition": {"interval_days": 30},
                "period": "1y",
                "starting_capital": 100000,
            },
            headers=auth_headers,
        )
    except (httpx.HTTPError, ConnectionError) as e:
        pytest.skip(f"network unavailable: {e}")

    if r.status_code == 400:
        detail = r.json().get("detail", "")
        if "Insufficient historical data" in str(detail):
            pytest.skip("yfinance unavailable offline")

    assert r.status_code == 200, r.text
    body = r.json()
    assert "total_return_pct" in body
    assert "disclaimer" in body
