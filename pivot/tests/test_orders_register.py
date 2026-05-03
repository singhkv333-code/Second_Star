"""Tests for POST /orders/register — the chat LogicCard confirm path.

This is the v1 "register but don't execute" endpoint. The chat builds a
LogicCard with a `register_payload`; when the user clicks Confirm in the
UI, that payload is POSTed here and a TradeLog row is written with
`source="chat-confirm"` and `kite_order_id=None`. No broker call.
"""


def test_register_single_market_order(client, auth_headers, db):
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
    assert body["status"] == "registered"
    assert body["transaction_type"] == "BUY"
    assert body["order_type"] == "MARKET"
    assert body["quantity"] == 10
    assert body["id"] > 0


def test_register_basket_order_writes_one_row_per_leg(client, auth_headers):
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
    assert all(row["status"] == "registered" for row in body["registered"])


def test_register_gtt_payload_persists_trigger_price(client, auth_headers):
    payload = {
        "symbol": "HDFCBANK", "exchange": "NSE",
        "transaction_type": "BUY", "order_type": "GTT",
        "quantity": 1, "price": 1500.0, "trigger_price": 1480.0,
        "product": "CNC",
    }
    r = client.post("/orders/register", json=payload, headers=auth_headers)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["trigger_price"] == 1480.0
    assert body["price"] == 1500.0
    assert body["order_type"] == "GTT"


def test_register_missing_required_fields_returns_422(client, auth_headers):
    # Missing transaction_type and order_type
    payload = {"symbol": "INFY", "quantity": 1}
    r = client.post("/orders/register", json=payload, headers=auth_headers)
    assert r.status_code == 422


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
