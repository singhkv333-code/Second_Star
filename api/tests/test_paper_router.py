"""HTTP tests for the /paper REST router (P4).

Offline by construction: positions/orders are opened through PaperBroker with
an injected/monkeypatched offline mark price, against the SAME ``db`` session
the TestClient is bound to (conftest overrides get_db -> the db fixture). We
then GET each /paper/* endpoint with a real Bearer token and assert 200 plus
the expected shape.

The router is a READ-ONLY view layer, so these tests assert response shape and
status, not book mutation.
"""
from __future__ import annotations

import datetime as dt

import pytest

from backend.main import app
from backend.models import PaperAccount
from backend.paper.broker import PaperBroker
from backend.paper.money import to_money
from backend.paper.snapshots import snapshot_account_nav
from backend.paper.valuation import mark_positions
from backend.routers import paper as paper_router


@pytest.fixture(autouse=True)
def _wire_router_and_paper_on(monkeypatch):
    """Register the /paper router on the app (the lead wires it in main.py;
    in this isolated test we mount it ourselves, idempotently) and flip the
    paper-trading flag ON (conftest pins it OFF)."""
    if not any(
        getattr(r, "path", "").startswith("/paper") for r in app.routes
    ):
        app.include_router(paper_router.router)
    monkeypatch.setattr(
        "backend.config.settings.paper_trading_enabled", True
    )
    # Broker resolves a live mark via get_mark_price; pin it offline.
    monkeypatch.setattr(
        "backend.paper.broker.get_mark_price",
        lambda symbol, token="mock_token": to_money(100.0),
    )


def _uid(db, headers) -> int:
    """Resolve the user id behind an auth header by its registered email."""
    # The token subject is the user id; just pull the only/most-recent user
    # matching is brittle — instead decode via the same helper the router uses.
    from backend.auth.jwt_handler import get_user_id_from_token
    return get_user_id_from_token(headers["Authorization"].replace("Bearer ", ""))


def _open_positions(db, user_id):
    """Open two market positions + one resting LIMIT order via the broker on
    the request-bound session, then apply marks and a NAV snapshot."""
    broker = PaperBroker(db, user_id, price_fn=lambda s: to_money(100.0))
    broker.place_order(
        tradingsymbol="INFY", transaction_type="BUY",
        quantity=10, order_type="MARKET",
    )
    broker.place_order(
        tradingsymbol="TCS", transaction_type="BUY",
        quantity=5, order_type="MARKET",
    )
    # A resting LIMIT BUY reserves cash and shows up in the order blotter.
    broker.place_order(
        tradingsymbol="RELIANCE", transaction_type="BUY",
        quantity=2, order_type="LIMIT", price=90.0,
    )
    db.flush()

    acct = (
        db.query(PaperAccount)
        .filter(PaperAccount.user_id == user_id)
        .first()
    )
    # Per-symbol marks. The NAV snapshot ALSO re-marks (it takes a price_fn),
    # so reuse the same map there or it would clobber the marks we assert on.
    marks = {"INFY": to_money(110), "TCS": to_money(190)}

    def _mark(symbol):
        return marks.get(symbol, to_money(100))

    mark_positions(db, acct.id, price_fn=_mark)
    snapshot_account_nav(db, acct, dt.date(2026, 5, 28), price_fn=_mark)
    db.flush()
    return acct


# ── summary ──────────────────────────────────────────────────────────────


def test_summary_traded_user_200_keys(client, auth_headers, db):
    uid = _uid(db, auth_headers)
    _open_positions(db, uid)

    r = client.get("/paper/summary", headers=auth_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["exists"] is True
    assert body["mode"] == "paper"
    assert body["num_positions"] == 2
    # One resting LIMIT order.
    assert body["num_open_orders"] == 1
    for k in (
        "starting_capital", "cash_available", "cash_reserved", "buying_power",
        "positions_mv", "invested", "nav", "unrealized_pnl",
        "realized_pnl_cum", "day_pnl", "total_pnl", "total_pnl_pct",
        "unrealized_pct", "is_stale",
    ):
        assert k in body, f"missing key {k}"
    # Money fields cast to float, not Decimal/str.
    for k in ("nav", "positions_mv", "unrealized_pnl", "cash_available"):
        assert isinstance(body[k], float)
    # Marked INFY 110 + TCS 190 -> 10*110 + 5*190 = 2050.
    assert body["positions_mv"] == 2050.0


def test_summary_new_user_200_exists_false(client, auth_headers, db):
    # auth_headers user never traded -> empty book.
    r = client.get("/paper/summary", headers=auth_headers)
    assert r.status_code == 200, r.text
    assert r.json() == {"exists": False}


def test_summary_unauthenticated_401(client):
    # In the test env (app_env != development) the dev fallback is OFF, so a
    # missing token is rejected.
    r = client.get("/paper/summary")
    assert r.status_code == 401


# ── holdings ─────────────────────────────────────────────────────────────


def test_holdings_200_shape(client, auth_headers, db):
    uid = _uid(db, auth_headers)
    _open_positions(db, uid)

    r = client.get("/paper/holdings", headers=auth_headers)
    assert r.status_code == 200, r.text
    rows = r.json()
    assert isinstance(rows, list)
    assert [row["symbol"] for row in rows] == ["INFY", "TCS"]
    # Sorted by market value desc: INFY 1100 >= TCS 950.
    assert rows[0]["market_value"] >= rows[1]["market_value"]
    for row in rows:
        for k in (
            "symbol", "quantity", "avg_cost", "last_price", "market_value",
            "unrealized_pnl", "unrealized_pct", "day_pnl", "invested",
            "realized_pnl", "sector", "stale", "last_mark_at",
        ):
            assert k in row, f"missing holdings key {k}"
        assert isinstance(row["market_value"], float)
    assert rows[0]["sector"] == "IT"
    assert rows[0]["last_price"] == 110.0


def test_holdings_new_user_empty_list(client, auth_headers):
    r = client.get("/paper/holdings", headers=auth_headers)
    assert r.status_code == 200, r.text
    assert r.json() == []


# ── orders (resting blotter) ─────────────────────────────────────────────


def test_orders_200_resting_blotter(client, auth_headers, db):
    uid = _uid(db, auth_headers)
    _open_positions(db, uid)

    r = client.get("/paper/orders", headers=auth_headers)
    assert r.status_code == 200, r.text
    orders = r.json()
    assert isinstance(orders, list)
    # Only the resting LIMIT survives (the two MARKET orders filled).
    assert len(orders) == 1
    o = orders[0]
    assert o["symbol"] == "RELIANCE"
    assert o["side"] == "BUY"
    assert o["order_type"] == "LIMIT"
    assert o["status"] == "resting"
    assert o["limit_price"] == 90.0
    assert isinstance(o["reserved_cash"], float)
    assert o["reserved_cash"] > 0
    assert o["created_at"] is not None


def test_orders_new_user_empty_list(client, auth_headers):
    r = client.get("/paper/orders", headers=auth_headers)
    assert r.status_code == 200, r.text
    assert r.json() == []


# ── fills journal ────────────────────────────────────────────────────────


def test_fills_200_journal(client, auth_headers, db):
    uid = _uid(db, auth_headers)
    _open_positions(db, uid)

    r = client.get("/paper/fills", headers=auth_headers)
    assert r.status_code == 200, r.text
    fills = r.json()
    assert isinstance(fills, list)
    # Two MARKET buys filled (the LIMIT is still resting -> no fill).
    assert len(fills) == 2
    syms = {f["symbol"] for f in fills}
    assert syms == {"INFY", "TCS"}
    for f in fills:
        assert f["side"] == "BUY"
        for k in (
            "id", "symbol", "side", "quantity", "fill_price", "gross_value",
            "charges", "net_cashflow", "realized_pnl", "filled_at", "order_id",
        ):
            assert k in f
        assert isinstance(f["fill_price"], float)
        assert isinstance(f["gross_value"], float)
        # BUY fills carry null realized P&L.
        assert f["realized_pnl"] is None


def test_fills_limit_query_param_caps(client, auth_headers, db):
    uid = _uid(db, auth_headers)
    _open_positions(db, uid)  # produces 2 fills

    r = client.get("/paper/fills?limit=1", headers=auth_headers)
    assert r.status_code == 200, r.text
    assert len(r.json()) == 1


def test_fills_new_user_empty_list(client, auth_headers):
    r = client.get("/paper/fills", headers=auth_headers)
    assert r.status_code == 200, r.text
    assert r.json() == []


# ── nav curve ────────────────────────────────────────────────────────────


def test_nav_200_curve(client, auth_headers, db):
    uid = _uid(db, auth_headers)
    _open_positions(db, uid)

    r = client.get("/paper/nav", headers=auth_headers)
    assert r.status_code == 200, r.text
    curve = r.json()
    assert isinstance(curve, list)
    assert len(curve) == 1
    pt = curve[0]
    assert pt["as_of_date"] == dt.date(2026, 5, 28).isoformat()
    for k in (
        "as_of_date", "nav", "cash_available", "positions_mv",
        "realized_pnl_cum", "unrealized_pnl", "nifty_close",
    ):
        assert k in pt
    assert isinstance(pt["nav"], float)
    assert pt["nifty_close"] is None


def test_nav_date_query_params_parsed(client, auth_headers, db):
    uid = _uid(db, auth_headers)
    _open_positions(db, uid)  # snapshot on 2026-05-28

    # Window excluding the snapshot date -> empty.
    r = client.get(
        "/paper/nav?start=2026-06-01&end=2026-06-30", headers=auth_headers
    )
    assert r.status_code == 200, r.text
    assert r.json() == []

    # Window including it -> one point.
    r2 = client.get(
        "/paper/nav?start=2026-05-01&end=2026-05-31", headers=auth_headers
    )
    assert r2.status_code == 200, r2.text
    assert len(r2.json()) == 1


def test_nav_bad_date_400(client, auth_headers):
    r = client.get("/paper/nav?start=not-a-date", headers=auth_headers)
    assert r.status_code == 400


def test_nav_new_user_empty_list(client, auth_headers):
    r = client.get("/paper/nav", headers=auth_headers)
    assert r.status_code == 200, r.text
    assert r.json() == []
