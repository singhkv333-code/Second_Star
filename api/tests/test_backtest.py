"""Backtest router tests — skip if yfinance has no network access."""
import pytest
import httpx


def test_backtest_monthly_calendar(client, auth_headers):
    """Run a monthly-SIP-equivalent strategy via the new wide-primitive shape."""
    strategy_definition = {
        "symbol": "INFY",
        "entry": {
            "operator": "single",
            "conditions": [
                {"signal": "first_day_of_month", "params": {}}
            ],
        },
        "exit": {
            "operator": "first_of",
            "conditions": [
                {"exit_type": "end_of_period", "params": {}}
            ],
        },
        "position_size_inr": 10000.0,
        "starting_capital": 100000.0,
        "period": "1y",
    }
    try:
        r = client.post(
            "/backtest/run",
            json={"strategy_definition": strategy_definition},
            headers=auth_headers,
        )
    except (httpx.HTTPError, ConnectionError) as e:
        pytest.skip(f"network unavailable: {e}")

    if r.status_code == 400:
        detail = r.json().get("detail", "")
        if "Insufficient historical data" in str(detail) or "yfinance" in str(detail).lower():
            pytest.skip("yfinance unavailable offline")

    assert r.status_code == 200, r.text
    body = r.json()
    assert "metrics" in body
    assert "disclaimer" in body
    assert "total_return_pct" in body["metrics"]
