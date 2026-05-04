"""Tests for /portfolio endpoints."""
import pytest
import httpx


def test_summary(client, auth_headers):
    r = client.get("/portfolio/summary", headers=auth_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert "total_value" in body


def test_holdings_has_sector(client, auth_headers):
    r = client.get("/portfolio/holdings", headers=auth_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert isinstance(body, list)
    for item in body:
        assert "sector" in item


def test_sector_breakdown(client, auth_headers):
    r = client.get("/portfolio/sector", headers=auth_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert "sectors" in body
    assert isinstance(body["sectors"], list)
    assert "total_value" in body


def test_yields_sorted_and_best_flagged(client, auth_headers):
    try:
        r = client.get("/portfolio/yields", headers=auth_headers)
    except (httpx.HTTPError, ConnectionError) as e:
        pytest.skip(f"network unavailable: {e}")

    if r.status_code >= 500:
        pytest.skip(f"upstream (mfapi.in) unavailable: {r.status_code}")

    assert r.status_code == 200, r.text
    body = r.json()
    assert isinstance(body, list)
    if not body:
        pytest.skip("empty yields list - nothing to verify")

    yields = [item["after_tax_yield_pct"] for item in body]
    assert yields == sorted(yields, reverse=True), "yields not sorted desc"
    assert body[0].get("is_best") is True
