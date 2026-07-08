"""Tests for the canonical {error: {code, message, details}} envelope.

Mounted as global FastAPI exception handlers in backend/main.py for
every /api/* route per docs/API_CONTRACT.md §2. Legacy non-/api/
routes keep FastAPI's default {"detail": ...} shape — verified
implicitly by the fact that the existing auth/portfolio/etc test
suites still pass.
"""
from __future__ import annotations

from fastapi.testclient import TestClient


def test_missing_token_returns_canonical_unauthenticated(
    client: TestClient,
) -> None:
    """No bearer token → the §2 canonical shape with code
    `unauthenticated`. The frontend's `isError(result)` checks
    `"error" in result`."""
    resp = client.get("/api/step-types")
    assert resp.status_code == 401
    body = resp.json()
    assert "error" in body
    assert body["error"]["code"] == "unauthenticated"
    assert isinstance(body["error"]["message"], str)
    assert body["error"]["message"]
    # `detail` (FastAPI's default) MUST NOT leak through.
    assert "detail" not in body


def test_invalid_token_returns_canonical_unauthenticated(
    client: TestClient,
) -> None:
    resp = client.get(
        "/api/step-types",
        headers={"Authorization": "Bearer not-a-real-token"},
    )
    assert resp.status_code == 401
    body = resp.json()
    assert body["error"]["code"] == "unauthenticated"


def test_legacy_routes_keep_default_detail_shape(
    client: TestClient,
) -> None:
    """Sanity: a non-/api route (e.g. /portfolio/summary) still uses
    the legacy `{"detail": ...}` shape. Otherwise we'd regress every
    existing route's tests in this repo."""
    resp = client.get("/portfolio/summary")
    # auth-required: the legacy router raises 401 with detail string
    assert resp.status_code == 401
    assert "detail" in resp.json()
    assert "error" not in resp.json()
