"""Tests for POST /orders/register — the chat LogicCard confirm path.

The register endpoint routes the order by the account's paper-vs-live mode:
  - PAPER mode           -> fills the simulated paper book (no broker, ever)
  - LIVE mode (paper off) -> places via the user's active broker connector

In the test env paper trading is pinned OFF (conftest), so by default the
register path takes the LIVE route. A freshly-registered user has no broker
session, so it falls back to the Kite mock helper -> a MOCK order id + a
broker status (not a bare 'registered' intent). Paper-mode tests opt back in
by flipping the flag.
"""
from unittest.mock import patch

from backend.paper.money import to_money


# ── paper OFF (default in tests): orders wire to the broker ────────────────

def test_register_single_market_order_routes_to_broker(client, auth_headers):
    payload = {
        "symbol": "RELIANCE",
        "exchange": "NSE",
        "transaction_type": "BUY",
        "order_type": "MARKET",
        "quantity": 10,
        "price": 2500.0,
        "product": "CNC",
    }
    r = client.post("/orders/register", json=payload, headers=auth_headers)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["symbol"] == "RELIANCE"
    assert body["transaction_type"] == "BUY"
    assert body["order_type"] == "MARKET"
    assert body["quantity"] == 10
    assert body["id"] > 0
    # Broker route: status reflects the (mock) broker outcome, NOT a bare
    # 'registered' intent — the order actually went to the broker seam.
    assert body["status"] != "registered"
    assert body["status"] == "COMPLETE"


def test_register_invokes_broker_routing_when_paper_off(client, auth_headers):
    """The register path now goes THROUGH the broker seam when paper is off
    (previously it short-circuited to a bare TradeLog and never wired up)."""
    with patch("backend.routers.orders.submit_order_for_user") as m:
        m.return_value = {"order_id": "BRK-1", "status": "PENDING"}
        r = client.post(
            "/orders/register",
            json={"symbol": "INFY", "transaction_type": "BUY",
                  "order_type": "MARKET", "quantity": 3},
            headers=auth_headers,
        )
    assert r.status_code == 201, r.text
    m.assert_called_once()
    # The broker's status flows into the persisted row.
    assert r.json()["status"] == "PENDING"


def test_register_basket_routes_each_leg(client, auth_headers):
    payload = {
        "basket": True,
        "legs": [
            {
                "symbol": "INFY", "exchange": "NSE",
                "transaction_type": "BUY", "order_type": "MARKET",
                "quantity": 5, "product": "CNC",
            },
            {
                "symbol": "TCS", "exchange": "NSE",
                "transaction_type": "BUY", "order_type": "LIMIT",
                "quantity": 3, "price": 4100.0, "product": "CNC",
            },
        ],
    }
    r = client.post("/orders/register", json=payload, headers=auth_headers)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["count"] == 2
    assert {row["symbol"] for row in body["registered"]} == {"INFY", "TCS"}
    # paper OFF -> each leg routed to the (mock) broker, not just registered.
    assert all(row["status"] != "registered" for row in body["registered"])


def test_register_gtt_routes_to_broker_gtt_api(client, auth_headers):
    """A GTT order_type must go through the GTT submission path (place_gtt),
    NOT the regular place_order path — Kite rejects order_type='GTT' on a
    plain order, so it would never create the trigger."""
    payload = {
        "symbol": "HDFCBANK", "exchange": "NSE",
        "transaction_type": "BUY", "order_type": "GTT",
        "quantity": 5, "price": 789.0, "trigger_price": 788.0,
        "product": "CNC",
    }
    with patch("backend.routers.orders.get_mark_price", return_value=None), \
         patch("backend.routers.orders.submit_gtt_for_user") as gtt, \
         patch("backend.routers.orders.submit_order_for_user") as place:
        gtt.return_value = {"trigger_id": 9001, "status": "active"}
        r = client.post("/orders/register", json=payload, headers=auth_headers)
    assert r.status_code == 201, r.text
    # routed to the GTT API, not the regular order API
    gtt.assert_called_once()
    place.assert_not_called()
    kwargs = gtt.call_args.kwargs
    assert kwargs["trigger_price"] == 788.0
    assert kwargs["limit_price"] == 789.0
    body = r.json()
    assert body["trigger_price"] == 788.0
    assert body["price"] == 789.0
    assert body["order_type"] == "GTT"
    assert body["status"] == "active"


def test_register_live_broker_rejection_surfaces_502(client, auth_headers):
    """When a LIVE order is rejected by the broker (e.g. Kite IP allow-list),
    the real reason must surface — NOT a silent 'registered' the UI shows as
    'Placed'. Paper is OFF in tests, so this is the live path."""
    with patch(
        "backend.routers.orders.submit_order_for_user",
        side_effect=RuntimeError("IP (1.2.3.4) is not allowed to place orders"),
    ):
        r = client.post(
            "/orders/register",
            json={"symbol": "INFY", "transaction_type": "BUY",
                  "order_type": "MARKET", "quantity": 1},
            headers=auth_headers,
        )
    assert r.status_code == 502, r.text
    detail = r.json()["detail"]
    assert "Broker rejected" in detail
    assert "not allowed to place orders" in detail  # the broker's own message


def test_register_insufficient_funds_returns_402(client, auth_headers):
    """The pre-trade funds guard must block an unaffordable live BUY with a
    clear 402, not place it."""
    from backend.paper.routing import InsufficientFundsError

    with patch(
        "backend.routers.orders.submit_order_for_user",
        side_effect=InsufficientFundsError(required=6548, available=100),
    ):
        r = client.post(
            "/orders/register",
            json={"symbol": "RELIANCE", "transaction_type": "BUY",
                  "order_type": "MARKET", "quantity": 5},
            headers=auth_headers,
        )
    assert r.status_code == 402, r.text
    assert "Insufficient funds" in r.json()["detail"]


def test_register_paper_routing_error_still_registers(
    client, auth_headers, monkeypatch,
):
    """In PAPER mode a transient routing failure must NOT be lost — it falls
    back to a registered intent (the live-surface rule applies only to live)."""
    monkeypatch.setattr("backend.config.settings.paper_trading_enabled", True)
    with patch(
        "backend.routers.orders.submit_order_for_user",
        side_effect=RuntimeError("paper book hiccup"),
    ):
        r = client.post(
            "/orders/register",
            json={"symbol": "INFY", "transaction_type": "BUY",
                  "order_type": "MARKET", "quantity": 1},
            headers=auth_headers,
        )
    assert r.status_code == 201, r.text
    assert r.json()["status"] == "registered"


# ── validation / auth ──────────────────────────────────────────────────────

def test_register_missing_required_fields_returns_422(client, auth_headers):
    # Missing transaction_type and order_type
    payload = {"symbol": "INFY", "quantity": 1}
    r = client.post("/orders/register", json=payload, headers=auth_headers)
    assert r.status_code == 422


def test_register_live_mode_without_broker_session_errors(
    client, auth_headers, monkeypatch,
):
    """Paper OFF (conftest) + real key (non-mock) + no broker connected: the
    endpoint must NOT silently mock-place and report a phantom 'Placed'. It
    must fail honestly so the UI prompts the user to connect their broker."""
    monkeypatch.setattr("backend.kite.auth.KITE_MOCK_MODE", False)
    r = client.post(
        "/orders/register",
        json={"symbol": "INFY", "transaction_type": "BUY",
              "order_type": "MARKET", "quantity": 1},
        headers=auth_headers,
    )
    assert r.status_code == 409, r.text
    assert "broker" in r.json()["detail"].lower()


def test_register_requires_auth(client):
    r = client.post("/orders/register", json={"symbol": "INFY", "quantity": 1,
                                              "transaction_type": "BUY",
                                              "order_type": "MARKET"})
    assert r.status_code == 401


def test_registered_order_appears_in_history(client, auth_headers):
    """End-to-end check: register an order, then GET /orders/history sees it."""
    r = client.post(
        "/orders/register",
        json={"symbol": "TCS", "transaction_type": "SELL", "order_type": "LIMIT",
              "quantity": 2, "price": 4000.0},
        headers=auth_headers,
    )
    assert r.status_code == 201

    h = client.get("/orders/history", headers=auth_headers)
    assert h.status_code == 200
    history = h.json()
    assert any(row["symbol"] == "TCS" and row["action"] == "SELL"
               for row in history)


# ── paper ON: strictly simulated, never a broker ───────────────────────────

def test_register_paper_on_fills_paper_book_never_broker(
    client, auth_headers, db, monkeypatch,
):
    """With paper trading ON (account.mode defaults to 'paper'), the register
    path must fill the SIMULATED paper book and must NOT contact any broker.

    Guards the user's "when paper mode is on, strictly do not wire to broker"
    requirement: the Kite mock helper and the live connector resolver both
    raise if called, so any broker contact fails the test loudly."""
    from backend.models import PaperOrder

    monkeypatch.setattr(
        "backend.config.settings.paper_trading_enabled", True,
    )
    monkeypatch.setattr(
        "backend.paper.broker.get_mark_price",
        lambda symbol, token="mock_token": to_money(100.0),
    )
    no_broker = patch(
        "backend.paper.routing._kite.place_order",
        side_effect=AssertionError("broker contacted in paper mode!"),
    )
    no_connector = patch(
        "backend.paper.routing.get_connector",
        side_effect=AssertionError("broker connector resolved in paper mode!"),
    )
    with no_broker, no_connector:
        r = client.post(
            "/orders/register",
            json={"symbol": "RELIANCE", "transaction_type": "BUY",
                  "order_type": "MARKET", "quantity": 4},
            headers=auth_headers,
        )
    assert r.status_code == 201, r.text
    body = r.json()
    # Simulated outcome (filled), and a paper order actually landed in the book.
    assert body["status"] == "filled"
    assert db.query(PaperOrder).filter_by(symbol="RELIANCE").count() == 1
