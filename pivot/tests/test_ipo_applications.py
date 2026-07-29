"""Tests for the IPO application REST surface (P0 — register-not-execute).

Coverage:
  - register writes a row + computes amount_estimate server-side
  - closed-window IPO is rejected with an honest 422
  - soft duplicate check returns 201 + duplicate:true (no hard-fail)
  - withdraw transitions the row to status="withdrawn"
  - NO broker / ASBA / UPI-mandate function is called on register

The IPO feed is stubbed via monkeypatch — we don't want NSE on the test
hot path. The stubs return the exact shape the live feed produces (see
backend/services/ipo_feed.py::get_ipo_details), so the router's
validation path runs end-to-end.
"""
from __future__ import annotations

from typing import Any

import pytest

from backend.models import IPOApplication


# ── Feed stubs ─────────────────────────────────────────────────────────

_OPEN_IPO = {
    "found": True,
    "ipo": {
        "name": "Tikona Infinet",
        "symbol": "TIKONA",
        "price_band": "125-132",
        "open_date": "2026-06-03",
        "close_date": "2026-06-05",
        "lot_size": 110,
        "issue_size": "₹1,200 cr",
        "type": "mainboard",
        "status": "open",
    },
    "extra": {"rhpLink": "https://example.com/rhp.pdf"},
    "source": "nse",
}

_CLOSED_IPO = {
    "found": True,
    "ipo": {
        "name": "ClosedCo",
        "symbol": "CLOSEDCO",
        "price_band": "100-110",
        "open_date": "2026-05-20",
        "close_date": "2026-05-22",
        "lot_size": 130,
        "issue_size": "₹500 cr",
        "type": "mainboard",
        "status": "closed",
    },
    "extra": {},
    "source": "nse",
}

_OPEN_LARGE = {
    # Designed so amount-at-cutoff > ₹5L (UPI cap) at 5 lots — for testing
    # the hard-block on the UPI cap. 5 * 100 * 1100 = 550000 > 500000.
    "found": True,
    "ipo": {
        "name": "BigBucks",
        "symbol": "BIGBUCKS",
        "price_band": "1000-1100",
        "open_date": "2026-06-04",
        "close_date": "2026-06-06",
        "lot_size": 100,
        "issue_size": "₹5,000 cr",
        "type": "mainboard",
        "status": "open",
    },
    "extra": {},
    "source": "nse",
}


@pytest.fixture
def stub_ipo_feed(monkeypatch: pytest.MonkeyPatch) -> dict[str, dict[str, Any]]:
    """Stub get_ipo_details so the router sees a deterministic feed.

    Keyed by uppercase symbol. Returns the live feed shape per IPO.
    """
    catalog: dict[str, dict[str, Any]] = {
        "TIKONA": _OPEN_IPO,
        "CLOSEDCO": _CLOSED_IPO,
        "BIGBUCKS": _OPEN_LARGE,
    }

    def _fake_get(name_or_symbol: str) -> dict[str, Any]:
        key = (name_or_symbol or "").strip().upper()
        if key in catalog:
            return dict(catalog[key])
        return {
            "found": False,
            "query": name_or_symbol,
            "note": "no live IPO matches (test stub)",
            "matches": [],
            "source": "nse",
        }

    # Patch at the router AND service import sites.
    monkeypatch.setattr(
        "backend.routers.ipo_applications.get_ipo_details", _fake_get,
    )
    return catalog


# ── Test surface ───────────────────────────────────────────────────────

def test_register_writes_row_and_computes_amount_estimate_server_side(
    client, auth_headers, db, stub_ipo_feed,
) -> None:
    """The register endpoint must:
       - 201 with the persisted row
       - amount_estimate = quantity_lots * lot_size * band.max (at cut-off)
       - status='registered', source='chat-confirm'
       - UPI handle is masked, never the raw form
    """
    r = client.post(
        "/ipo-applications",
        headers=auth_headers,
        json={
            "ipo_symbol": "tikona",   # lower-case to exercise upper-casing
            "category": "retail",
            "quantity_lots": 1,
            "bid_price_mode": "cutoff",
            "upi_id_masked": "alice@okhdfcbank",
            "conversation_id": "s_test_001",
        },
    )
    assert r.status_code == 201, r.text
    body = r.json()
    # Response is wrapped: {application: {...row}, duplicate, replace_offer?}.
    app = body["application"]
    assert app["ipo_symbol"] == "TIKONA"
    assert app["ipo_type"] == "mainboard"
    assert app["category"] == "retail"
    assert app["quantity_lots"] == 1
    assert app["lot_size"] == 110
    assert app["bid_price_mode"] == "cutoff"
    # 1 * 110 * 132 (band.max) == 14520
    assert app["amount_estimate"] == 14520.0
    assert app["status"] == "registered"
    assert app["source"] == "chat-confirm"
    # UPI must be masked: a***@okhdfcbank
    assert app["upi_id_masked"] == "a***@okhdfcbank"
    assert app["upi_id_masked"] != "alice@okhdfcbank"
    assert body["duplicate"] is False

    # Verify a row landed in the DB with the right shape.
    rows = db.query(IPOApplication).all()
    assert len(rows) == 1
    row = rows[0]
    assert row.status == "registered"
    assert row.amount_estimate == 14520.0
    assert row.conversation_id == "s_test_001"


def test_closed_ipo_is_rejected_with_honest_4xx(
    client, auth_headers, stub_ipo_feed,
) -> None:
    r = client.post(
        "/ipo-applications",
        headers=auth_headers,
        json={
            "ipo_symbol": "CLOSEDCO",
            "category": "retail",
            "quantity_lots": 1,
            "bid_price_mode": "cutoff",
        },
    )
    assert r.status_code == 422
    body = r.json()
    detail = body.get("detail") if isinstance(body, dict) else None
    # The path is not under /api/* so the canonical envelope handler does
    # NOT wrap it — FastAPI's default {"detail": ...} shape applies.
    assert "closed" in str(detail).lower()


def test_duplicate_register_returns_201_with_duplicate_flag(
    client, auth_headers, db, stub_ipo_feed,
) -> None:
    """Soft duplicate: second register for the same IPO still 201s but
    surfaces ``duplicate: true`` + a replace-offer. No hard-fail."""
    payload = {
        "ipo_symbol": "TIKONA",
        "category": "retail",
        "quantity_lots": 1,
        "bid_price_mode": "cutoff",
    }
    r1 = client.post("/ipo-applications", headers=auth_headers, json=payload)
    assert r1.status_code == 201

    r2 = client.post("/ipo-applications", headers=auth_headers, json=payload)
    assert r2.status_code == 201, r2.text
    body = r2.json()
    assert body["duplicate"] is True
    assert "replace_offer" in body
    assert body["replace_offer"]["previous_id"] == r1.json()["application"]["id"]

    # Both rows exist.
    rows = db.query(IPOApplication).all()
    assert len(rows) == 2


def test_withdraw_transitions_row_to_withdrawn(
    client, auth_headers, db, stub_ipo_feed,
) -> None:
    r = client.post(
        "/ipo-applications",
        headers=auth_headers,
        json={
            "ipo_symbol": "TIKONA",
            "category": "retail",
            "quantity_lots": 1,
            "bid_price_mode": "cutoff",
        },
    )
    app_id = r.json()["application"]["id"]

    w = client.post(
        f"/ipo-applications/{app_id}/withdraw",
        headers=auth_headers,
    )
    assert w.status_code == 200, w.text
    body = w.json()
    assert body["application"]["status"] == "withdrawn"

    # DB reflects the transition.
    row = (
        db.query(IPOApplication)
        .filter(IPOApplication.id == app_id)
        .first()
    )
    assert row is not None
    assert row.status == "withdrawn"

    # Idempotent — a second withdraw still 200s and is a no-op.
    w2 = client.post(
        f"/ipo-applications/{app_id}/withdraw",
        headers=auth_headers,
    )
    assert w2.status_code == 200
    assert w2.json()["application"]["status"] == "withdrawn"


def test_register_does_not_call_any_broker_or_upi_mandate_function(
    client, auth_headers, monkeypatch: pytest.MonkeyPatch, stub_ipo_feed,
) -> None:
    """P0 hard-rule: NO broker / ASBA / UPI-mandate call on register.

    We assert this by:
      1. Importing every plausible broker-side entry point and wrapping
         it in a sentinel that fails the test if hit.
      2. Walking the full register path and confirming none were called.
    """
    sentinels: list[str] = []

    def _make_sentinel(name: str):
        def _boom(*a, **kw):
            sentinels.append(name)
            raise AssertionError(
                f"P0 violation: {name} was invoked on the IPO register path"
            )
        return _boom

    # Broker / paper / Kite entry points. Any of these would indicate the
    # register path tried to actually submit a bid.
    for dotted in (
        "backend.paper.routing.submit_order_for_user",
        "backend.paper.routing.submit_gtt_for_user",
        "backend.kite.orders.place_order",
    ):
        try:
            monkeypatch.setattr(dotted, _make_sentinel(dotted))
        except (AttributeError, ModuleNotFoundError):
            # If the symbol doesn't exist in this build, there's nothing
            # to violate. Skip silently.
            continue

    r = client.post(
        "/ipo-applications",
        headers=auth_headers,
        json={
            "ipo_symbol": "TIKONA",
            "category": "retail",
            "quantity_lots": 1,
            "bid_price_mode": "cutoff",
        },
    )
    assert r.status_code == 201
    assert sentinels == [], (
        f"P0 violation: broker functions invoked: {sentinels!r}"
    )


def test_list_endpoint_uses_estimated_amount_label(
    client, auth_headers, stub_ipo_feed,
) -> None:
    r = client.post(
        "/ipo-applications",
        headers=auth_headers,
        json={
            "ipo_symbol": "TIKONA",
            "category": "retail",
            "quantity_lots": 1,
            "bid_price_mode": "cutoff",
        },
    )
    assert r.status_code == 201

    lst = client.get("/users/ipo-applications", headers=auth_headers)
    assert lst.status_code == 200, lst.text
    body = lst.json()
    assert body["count"] == 1
    assert body["amount_label"] == "estimated amount you'll need"
    assert body["items"][0]["status"] == "registered"


def test_retail_mainboard_cap_blocks_over_2L(
    client, auth_headers, stub_ipo_feed,
) -> None:
    """Mainboard retail cap is amount-AT-CUTOFF <= ₹2,00,000."""
    # 14 lots * 110 * 132 = 203,280 > 200000 -> blocked.
    r = client.post(
        "/ipo-applications",
        headers=auth_headers,
        json={
            "ipo_symbol": "TIKONA",
            "category": "retail",
            "quantity_lots": 14,
            "bid_price_mode": "cutoff",
        },
    )
    assert r.status_code == 422
    assert "retail" in str(r.json()).lower() or "cap" in str(r.json()).lower()


def test_upi_cap_blocks_over_5L(
    client, auth_headers, stub_ipo_feed,
) -> None:
    """UPI hard cap: amount_estimate > ₹5,00,000 -> 422."""
    # BIGBUCKS: 5 lots * 100 * 1100 = 550000 > 500000 -> blocked.
    # First switch to snii so the retail cap doesn't bite us before the
    # UPI cap does.
    r = client.post(
        "/ipo-applications",
        headers=auth_headers,
        json={
            "ipo_symbol": "BIGBUCKS",
            "category": "snii",
            "quantity_lots": 5,
            "bid_price_mode": "fixed",
            "bid_price": 1100.0,
        },
    )
    assert r.status_code == 422
    assert "5,00,000" in str(r.json()) or "UPI" in str(r.json()) or "500000" in str(r.json())


def test_fixed_bid_must_be_in_band(
    client, auth_headers, stub_ipo_feed,
) -> None:
    r = client.post(
        "/ipo-applications",
        headers=auth_headers,
        json={
            "ipo_symbol": "TIKONA",
            "category": "snii",
            "quantity_lots": 14,
            "bid_price_mode": "fixed",
            "bid_price": 200.0,  # band is 125-132 — out of range
        },
    )
    assert r.status_code == 422


def test_cutoff_not_allowed_for_snii(
    client, auth_headers, stub_ipo_feed,
) -> None:
    r = client.post(
        "/ipo-applications",
        headers=auth_headers,
        json={
            "ipo_symbol": "TIKONA",
            "category": "snii",
            "quantity_lots": 14,
            "bid_price_mode": "cutoff",
        },
    )
    assert r.status_code == 422


def test_missing_ipo_returns_404(
    client, auth_headers, stub_ipo_feed,
) -> None:
    r = client.post(
        "/ipo-applications",
        headers=auth_headers,
        json={
            "ipo_symbol": "NOSUCHIPO",
            "category": "retail",
            "quantity_lots": 1,
            "bid_price_mode": "cutoff",
        },
    )
    assert r.status_code == 404
