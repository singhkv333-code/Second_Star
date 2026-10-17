"""Equity basket square-off / delete / holdings-name enrichment.

Conftest pins PAPER_TRADING_ENABLED=false and mocks nothing about marks by
default; these tests flip paper trading on and pin get_mark_price offline,
mirroring tests/test_paper_router.py's fixture.
"""
from __future__ import annotations

import pytest

from backend.models import PaperPosition, Strategy
from backend.paper.broker import PaperBroker
from backend.paper.money import to_money


@pytest.fixture(autouse=True)
def _paper_on_and_offline_marks(monkeypatch):
    monkeypatch.setattr("backend.config.settings.paper_trading_enabled", True)
    monkeypatch.setattr(
        "backend.paper.broker.get_mark_price",
        lambda symbol, token="mock_token": to_money(100.0),
    )


def _uid(headers) -> int:
    from backend.auth.jwt_handler import get_user_id_from_token
    return get_user_id_from_token(headers["Authorization"].replace("Bearer ", ""))


def _create_basket(client, auth_headers, members):
    r = client.post(
        "/strategies/baskets",
        json={"name": "Test Basket", "members": members, "weighting": "equal"},
        headers=auth_headers,
    )
    assert r.status_code == 201, r.text
    return r.json()


def test_basket_holdings_carry_names(client, auth_headers):
    basket = _create_basket(
        client, auth_headers,
        [{"symbol": "INFY", "weight": 50}, {"symbol": "TCS", "weight": 50}],
    )
    assert all(m.get("name") for m in basket["members"])

    r = client.get("/strategies/baskets", headers=auth_headers)
    assert r.status_code == 200, r.text
    listed = next(b for b in r.json()["baskets"] if b["id"] == basket["id"])
    assert all(m.get("name") for m in listed["members"])


def test_close_basket_sells_held_positions(client, auth_headers, db):
    user_id = _uid(auth_headers)
    broker = PaperBroker(db, user_id, price_fn=lambda s: to_money(100.0))
    broker.place_order(
        tradingsymbol="INFY", transaction_type="BUY", quantity=10, order_type="MARKET",
    )
    db.commit()

    basket = _create_basket(client, auth_headers, [{"symbol": "INFY", "weight": 100}])

    r = client.post(f"/strategies/baskets/{basket['id']}/close", headers=auth_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["count"] == 1
    assert body["registered"][0]["symbol"] == "INFY"
    assert body["registered"][0]["transaction_type"] == "SELL"

    pos = (
        db.query(PaperPosition)
        .filter(PaperPosition.user_id == user_id, PaperPosition.symbol == "INFY")
        .first()
    )
    assert pos is not None and int(pos.quantity) == 0

    # Closing doesn't delete — the basket is still listed, tradeable again.
    r2 = client.get("/strategies/baskets", headers=auth_headers)
    assert basket["id"] in [b["id"] for b in r2.json()["baskets"]]


def test_close_basket_with_no_position_is_a_noop(client, auth_headers):
    basket = _create_basket(client, auth_headers, [{"symbol": "WIPRO", "weight": 100}])
    r = client.post(f"/strategies/baskets/{basket['id']}/close", headers=auth_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["count"] == 0
    assert body["skipped"][0]["symbol"] == "WIPRO"


def test_delete_basket_squares_off_and_hard_deletes(client, auth_headers, db):
    user_id = _uid(auth_headers)
    broker = PaperBroker(db, user_id, price_fn=lambda s: to_money(100.0))
    broker.place_order(
        tradingsymbol="TCS", transaction_type="BUY", quantity=5, order_type="MARKET",
    )
    db.commit()

    basket = _create_basket(client, auth_headers, [{"symbol": "TCS", "weight": 100}])

    r = client.delete(f"/strategies/baskets/{basket['id']}", headers=auth_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "deleted"
    assert body["squareoff"]["count"] == 1
    assert body["squareoff"]["registered"][0]["symbol"] == "TCS"

    pos = (
        db.query(PaperPosition)
        .filter(PaperPosition.user_id == user_id, PaperPosition.symbol == "TCS")
        .first()
    )
    assert pos is not None and int(pos.quantity) == 0

    # Hard-deleted: gone from the strategies table, not just hidden.
    row = db.query(Strategy).filter(Strategy.id == basket["id"]).first()
    assert row is None

    r2 = client.get("/strategies/baskets", headers=auth_headers)
    assert basket["id"] not in [b["id"] for b in r2.json()["baskets"]]


def test_delete_basket_with_no_position_still_deletes(client, auth_headers):
    basket = _create_basket(client, auth_headers, [{"symbol": "HDFCBANK", "weight": 100}])
    r = client.delete(f"/strategies/baskets/{basket['id']}", headers=auth_headers)
    assert r.status_code == 200, r.text
    assert r.json()["squareoff"]["count"] == 0

    r2 = client.get("/strategies/baskets", headers=auth_headers)
    assert basket["id"] not in [b["id"] for b in r2.json()["baskets"]]
