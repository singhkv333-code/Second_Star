"""An unreachable financials DB must not turn Indian tickers into US ones, and
holdings carry each position's value and return, ranked by `sort_by`."""
import asyncio

from backend.agents import tool_executor as tx
from backend.market import financials_db, security_meta


def test_a_failed_db_lookup_keeps_indian_tickers_indian(monkeypatch):
    def down(*a, **k):
        raise ConnectionError("financials DB unreachable")
    monkeypatch.setattr(financials_db, "resolve_symbol", down)
    # in the curated universe: no DB call at all
    assert security_meta.classify("TCS")["asset_class"] == "in_equity"
    # outside it, the failed lookup keeps the India default (it used to be us_equity)
    assert security_meta.classify("ZOMATO")["asset_class"] == "in_equity"
    # a genuine miss is still a miss
    monkeypatch.setattr(financials_db, "resolve_symbol", lambda *a, **k: None)
    assert security_meta.classify("QQQQX")["asset_class"] == "us_equity"
    assert security_meta.classify("BTC")["asset_class"] == "crypto"


def test_holdings_carry_value_and_return_and_honour_sort(monkeypatch):
    rows = [
        {"tradingsymbol": "A", "quantity": 10, "average_price": 100.0, "last_price": 90.0, "pnl": -100.0},
        {"tradingsymbol": "B", "quantity": 1, "average_price": 100.0, "last_price": 150.0, "pnl": 50.0},
        {"tradingsymbol": "C", "quantity": 5, "average_price": 0, "last_price": None, "pnl": 0.0},
    ]
    monkeypatch.setattr("backend.services.portfolio_cache.get_holdings_cached", lambda uid, kt: rows)
    out = asyncio.run(tx._get_holdings({"sort_by": "value"}, None, None, 1))["data"]["holdings"]
    assert [r["tradingsymbol"] for r in out] == ["A", "B", "C"]
    assert out[0]["value"] == 900.0 and out[0]["pnl_pct"] == -10.0
    assert out[1]["pnl_pct"] == 50.0 and out[2]["value"] is None and out[2]["pnl_pct"] is None
    by_pnl = asyncio.run(tx._get_holdings({"sort_by": "pnl"}, None, None, 1))["data"]["holdings"]
    assert by_pnl[0]["tradingsymbol"] == "B"
