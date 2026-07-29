"""Tests for /api/backtest/{fields,validate,run} top-level aliases (#51).

The aliases delegate to the same handlers used by the canonical
/api/backtest/expr/* routes. We don't re-test the handler logic here
(that lives in the expression backtester package's own suite); we only
prove the alias paths exist, are auth-gated, and dispatch the same way.
"""
from __future__ import annotations

from unittest.mock import patch

from fastapi.testclient import TestClient


def test_alias_fields_unauth(client: TestClient) -> None:
    r = client.get("/api/backtest/fields")
    assert r.status_code == 401


def test_alias_validate_unauth(client: TestClient) -> None:
    r = client.post(
        "/api/backtest/validate", json={"expression": "pe_ratio < 15"},
    )
    assert r.status_code == 401


def test_alias_fields_authed_dispatches_to_handler(
    client: TestClient, auth_headers: dict[str, str],
) -> None:
    """Auth passes — handler is invoked. We mock the registry loader so
    the test doesn't depend on the financials DB."""
    fake_payload = {
        "base_fields": [],
        "computed_fields": [],
        "specials": ["price"],
        "ttm_suffix_note": "...",
    }
    with patch(
        "backend.routers.backtest_alias._list_fields",
        return_value=fake_payload,
    ):
        r = client.get("/api/backtest/fields", headers=auth_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert "base_fields" in body
    assert "computed_fields" in body
    assert "specials" in body


def test_alias_validate_authed_dispatches_to_handler(
    client: TestClient, auth_headers: dict[str, str],
) -> None:
    fake_payload = {"ok": True, "referenced_fields": ["pe_ratio"], "warnings": []}
    with patch(
        "backend.routers.backtest_alias._validate_expr",
        return_value=fake_payload,
    ):
        r = client.post(
            "/api/backtest/validate",
            headers=auth_headers,
            json={"expression": "pe_ratio < 15"},
        )
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is True
