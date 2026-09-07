"""The paper book's arithmetic, and what the strategy runtime refuses.

These test the two things that would be worst to get wrong silently: money
that does not reconcile, and a draft that is stored but can never fire. Both
run against a temporary users database so nothing here touches a real book.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from decimal import Decimal

import pytest

import dataserver as server
import paper
import strategies
import trading_costs


@pytest.fixture()
def book(tmp_path, monkeypatch):
    """A fresh account database, wired in where the modules look for it."""
    db = sqlite3.connect(tmp_path / "users.db", check_same_thread=False)
    db.execute("PRAGMA foreign_keys=ON")
    db.executescript(
        "CREATE TABLE users (id INTEGER PRIMARY KEY, email TEXT);"
        "INSERT INTO users (id, email) VALUES (7, 'a@b.c');")
    monkeypatch.setattr(server, "_users", db)
    monkeypatch.setattr(server, "_users_lock", threading.Lock())
    monkeypatch.setattr(paper, "_ready", False)
    monkeypatch.setattr(strategies, "_ready", False)
    paper.init_db()
    strategies.init_db()
    # One price, so a test never depends on which bars happen to be on disk.
    monkeypatch.setattr(paper, "mark_price", lambda s: Decimal("100.0000"))
    monkeypatch.setattr(paper, "prev_close", lambda s: Decimal("100.0000"))
    monkeypatch.setattr(paper, "_sector", lambda s: "Test")
    yield db
    db.close()


def test_a_buy_costs_what_the_cost_table_says(book) -> None:
    out = paper.place_order(7, "ACME", "BUY", 10)
    net, charges = trading_costs.buy_cost(100.0, 10)
    assert out["status"] == "filled"
    # 4 dp, because that is the ledger's precision — the cost table returns
    # full float precision and the book quantizes it once, on the way in, so
    # every later replay of the same chain reproduces the same paise.
    assert out["charges"] == pytest.approx(charges, abs=1e-4)
    assert out["net_cashflow"] == pytest.approx(-net, abs=1e-4)
    _c, summary = paper.api_summary(7)
    # Cash out equals the net debit to the paise — no residue anywhere.
    assert summary["cash_available"] == pytest.approx(
        float(paper.SEED_CAPITAL) - net, abs=1e-4)


def test_avg_cost_carries_the_buy_charges(book) -> None:
    """The basis has to be cost-inclusive, or the realised P&L on the sell is
    a gross number the user then has to discount in their head."""
    paper.place_order(7, "ACME", "BUY", 10)
    _c, holdings = paper.api_holdings(7)
    net, _charges = trading_costs.buy_cost(100.0, 10)
    assert holdings[0]["avg_cost"] == pytest.approx(net / 10, abs=1e-4)
    assert holdings[0]["avg_cost"] > 100.0


def test_a_round_trip_at_one_price_loses_exactly_the_friction(book) -> None:
    paper.place_order(7, "ACME", "BUY", 10)
    out = paper.place_order(7, "ACME", "SELL", 10)
    buy_net, buy_ch = trading_costs.buy_cost(100.0, 10)
    sell_net, sell_ch = trading_costs.sell_cost(100.0, 10)
    assert out["realized_pnl"] == pytest.approx(sell_net - buy_net, abs=1e-3)
    _c, summary = paper.api_summary(7)
    assert summary["cash_available"] == pytest.approx(
        float(paper.SEED_CAPITAL) - buy_ch - sell_ch, abs=1e-3)
    assert summary["num_positions"] == 0
    # The closed lot keeps its realised total; the roll-up still counts it.
    assert summary["realized_pnl_cum"] == pytest.approx(
        sell_net - buy_net, abs=1e-3)


def test_averaging_up_compounds_the_basis(book, monkeypatch) -> None:
    paper.place_order(7, "ACME", "BUY", 10)
    monkeypatch.setattr(paper, "mark_price", lambda s: Decimal("200.0000"))
    paper.place_order(7, "ACME", "BUY", 10)
    _c, holdings = paper.api_holdings(7)
    n1, _ = trading_costs.buy_cost(100.0, 10)
    n2, _ = trading_costs.buy_cost(200.0, 10)
    assert holdings[0]["quantity"] == 20
    assert holdings[0]["avg_cost"] == pytest.approx((n1 + n2) / 20, abs=1e-3)


def test_the_book_is_long_only(book) -> None:
    with pytest.raises(paper.Reject) as exc:
        paper.place_order(7, "ACME", "SELL", 5)
    assert exc.value.reason == "insufficient_position"
    paper.place_order(7, "ACME", "BUY", 5)
    with pytest.raises(paper.Reject):
        paper.place_order(7, "ACME", "SELL", 6)


def test_a_buy_beyond_the_cash_is_refused_and_leaves_nothing(book) -> None:
    with pytest.raises(paper.Reject) as exc:
        paper.place_order(7, "ACME", "BUY", 100_000)
    assert exc.value.reason == "insufficient_buying_power"
    _c, summary = paper.api_summary(7)
    assert summary["cash_available"] == pytest.approx(float(paper.SEED_CAPITAL))
    assert summary["num_positions"] == 0


def test_a_resting_buy_reserves_its_cash_and_gives_it_back(book) -> None:
    out = paper.place_order(7, "ACME", "BUY", 10, order_type="LIMIT",
                            limit_price=90)
    assert out["status"] == "resting"
    _c, summary = paper.api_summary(7)
    assert summary["cash_reserved"] > 0
    # NAV must NOT dip while an order rests — the money is still owned.
    assert summary["nav"] == pytest.approx(float(paper.SEED_CAPITAL))
    assert summary["num_open_orders"] == 1
    paper.cancel_order(7, out["order_id"])
    _c, summary = paper.api_summary(7)
    assert summary["cash_reserved"] == 0
    assert summary["cash_available"] == pytest.approx(float(paper.SEED_CAPITAL))


def test_a_resting_buy_fills_when_its_price_arrives(book) -> None:
    out = paper.place_order(7, "ACME", "BUY", 10, order_type="LIMIT",
                            limit_price=90)
    paper.load_index()
    paper.on_price("ACME", 95.0)          # above the limit — must not fill
    _c, summary = paper.api_summary(7)
    assert summary["num_open_orders"] == 1
    paper.on_price("ACME", 88.0)          # through it — fills at the limit
    _c, fills = paper.api_fills(7)
    assert len(fills) == 1
    assert fills[0]["fill_price"] == pytest.approx(90.0)
    _c, summary = paper.api_summary(7)
    assert summary["cash_reserved"] == 0


def test_nav_starts_at_the_seed_so_a_day_one_curve_exists(book) -> None:
    paper.place_order(7, "ACME", "BUY", 10)
    _c, curve = paper.api_nav(7)
    assert len(curve) >= 2, "one point draws no curve at all"
    assert curve[0]["nav"] == pytest.approx(float(paper.SEED_CAPITAL))
    assert curve[-1]["positions_mv"] > 0


# ── what the runtime refuses to arm ──────────────────────────────────

def _draft(**over):
    entry = {"type": "comparison", "op": "<",
             "left": {"type": "indicator", "indicator": "rsi",
                      "symbol": "ACME", "period": 14, "exchange": "NSE",
                      "offset": 0},
             "right": {"type": "constant", "value": 30}}
    order = {"symbol": "ACME", "side": "buy", "quantity": 5,
             "order_type": "market", "product": "CNC"}
    order.update(over.pop("order", {}))
    return {"name": "t", "timeframe_assumed": over.pop("tf", "daily"),
            "steps": [
                {"step_type": "trigger.compound", "config": {"entry": entry}},
                {"step_type": "action.place_order", "config": order},
            ] + over.pop("steps", [])}


def test_a_schedule_is_refused_by_name(book) -> None:
    d = {"name": "sip", "steps": [
        {"step_type": "trigger.schedule", "config": {"cron": "0 10 * * FRI"}},
        {"step_type": "action.place_order",
         "config": {"symbol": "ACME", "side": "buy", "quantity": 5}}]}
    with pytest.raises(strategies.Unbuildable) as exc:
        strategies.parse_draft(d)
    assert "clock" in str(exc.value)


def test_a_short_entry_is_refused(book) -> None:
    with pytest.raises(strategies.Unbuildable) as exc:
        strategies.parse_draft(_draft(order={"side": "sell"}))
    assert "long-only" in str(exc.value)


def test_a_draft_without_a_size_is_refused(book) -> None:
    with pytest.raises(strategies.Unbuildable):
        strategies.parse_draft(_draft(order={"quantity": 0}))


def test_an_interval_charto_cannot_fold_is_refused(book) -> None:
    with pytest.raises(strategies.Unbuildable) as exc:
        strategies.parse_draft(_draft(tf="3m"))
    assert "3m" in str(exc.value)


def test_a_draft_that_places_nothing_is_refused(book) -> None:
    d = _draft()
    d["steps"] = d["steps"][:1]
    with pytest.raises(strategies.Unbuildable):
        strategies.parse_draft(d)


def test_a_readable_draft_parses_to_what_the_card_showed(book) -> None:
    got = strategies.parse_draft(_draft())
    assert got["symbol"] == "ACME"
    assert got["interval"] == "1d"
    assert got["side"] == "BUY"
    assert got["quantity"] == 5
    assert got["entry"]["op"] == "<"


# ── the accessor ─────────────────────────────────────────────────────

def _rows(n=120, price=100.0):
    return [(i * 86400, price, price + 1, price - 1, price, 1000 + i)
            for i in range(n)]


def test_the_accessor_reads_price_and_indicator_from_charto_bars(monkeypatch):
    rows = _rows()
    monkeypatch.setattr(server, "get_bars", lambda s, iv, to, lim: {
        "bars": [{"t": r[0], "o": r[1], "h": r[2], "l": r[3], "c": r[4],
                  "v": r[5]} for r in rows]})
    acc = strategies.ChartoDataAccessor(default_tf="1d")
    assert acc.get_price(symbol="ACME", basis="close") == pytest.approx(100.0)
    assert acc.get_price(symbol="ACME", basis="high") == pytest.approx(101.0)
    assert acc.get_indicator(symbol="ACME", indicator="sma",
                             period=10) == pytest.approx(100.0)
    # An indicator Charto has no mapping for must be UNKNOWN, never a guess.
    assert acc.get_indicator(symbol="ACME", indicator="not_a_thing",
                             period=10) is None
    # No bars is UNKNOWN too — a rule on a symbol with no data holds.
    monkeypatch.setattr(server, "get_bars", lambda *a, **k: {"bars": []})
    empty = strategies.ChartoDataAccessor(default_tf="1d")
    assert empty.get_price(symbol="NOPE") is None


def test_position_fields_answer_from_the_open_lot(monkeypatch):
    rows = _rows()
    monkeypatch.setattr(server, "get_bars", lambda s, iv, to, lim: {
        "bars": [{"t": r[0], "o": r[1], "h": r[2], "l": r[3], "c": r[4],
                  "v": r[5]} for r in rows]})
    acc = strategies.ChartoDataAccessor(
        default_tf="1d",
        position={"symbol": "ACME", "entry_price": 50.0, "bars_held": 4,
                  "peak_pct": 1.2})
    assert acc.get_position_field(field="entry_price") == pytest.approx(50.0)
    assert acc.get_position_field(field="bars_held") == pytest.approx(4)
    # close is 100 against a 50 entry — +100%.
    assert acc.get_position_field(field="unrealised_pct") == pytest.approx(1.0)
    # A stop reads the bar's LOW, which is what makes an intrabar stop honest.
    assert acc.get_position_field(
        field="unrealised_pct", basis="low") == pytest.approx(0.98)
    assert acc.get_position_field(
        field="drawdown_from_peak_pct") == pytest.approx(0.2)
    # No position at all is UNKNOWN, not zero.
    bare = strategies.ChartoDataAccessor(default_tf="1d")
    assert bare.get_position_field(field="unrealised_pct") is None
