"""Consolidated view-enum tools (chat-kernel Phase 1) — dispatch tests.

Each merged tool is a thin adapter over the narrow battle-tested
handlers; these tests pin (1) the view/action → target-handler mapping,
(2) the arg translation (side→transaction_type, id→sip_id/strategy_id),
and (3) the structured one-step-repairable error shape on bad enums.
Narrow handlers are monkeypatched — no broker/db needed.
"""
import asyncio

import pytest

from backend.agents import consolidated_handlers as ch
from backend.agents import tool_executor as tx


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def _recorder(record: dict, name: str):
    async def h(a, kt, db, uid):
        record["target"] = name
        record["args"] = a
        return {"success": True, "data": {"via": name}, "logiccard": None}
    return h


@pytest.mark.parametrize("view,target", [
    ("quote", "_get_live_price"),
    ("ohlc", "_get_ohlc"),
])
def test_market_data_dispatches_legacy_views(monkeypatch, view, target):
    rec: dict = {}
    monkeypatch.setattr(tx, target, _recorder(rec, target))
    out = _run(ch._get_market_data({"symbol": "INFY", "view": view},
                                   "tok", None, 1))
    assert out["success"] and rec["target"] == target
    assert rec["args"]["symbol"] == "INFY"


def test_market_data_bad_view_is_structured():
    out = _run(ch._get_market_data({"symbol": "INFY", "view": "candles"},
                                   "tok", None, 1))
    assert out["success"] is False
    assert out["data"]["field"] == "view"
    assert out["data"]["received_value"] == "candles"
    assert "history" in out["data"]["allowed_values"]
    assert out["data"]["retriable"] is True


def test_portfolio_detail_requires_symbol():
    out = _run(ch._get_portfolio({"view": "detail"}, "tok", None, 1))
    assert out["success"] is False and out["data"]["field"] == "symbol"


def test_portfolio_view_dispatch(monkeypatch):
    rec: dict = {}
    monkeypatch.setattr(tx, "_get_sector_breakdown",
                        _recorder(rec, "_get_sector_breakdown"))
    out = _run(ch._get_portfolio({"view": "sectors"}, "tok", None, 1))
    assert out["success"] and rec["target"] == "_get_sector_breakdown"


def test_manage_automation_translates_id(monkeypatch):
    rec: dict = {}
    monkeypatch.setattr(tx, "_pause_sip", _recorder(rec, "_pause_sip"))
    out = _run(ch._manage_automation(
        {"action": "pause", "kind": "sip", "id": 7}, "tok", None, 1))
    assert out["success"] and rec["args"]["sip_id"] == 7


def test_manage_automation_needs_id_for_mutations():
    out = _run(ch._manage_automation(
        {"action": "delete", "kind": "strategy"}, "tok", None, 1))
    assert out["success"] is False and out["data"]["field"] == "id"


def test_place_order_market_vs_limit(monkeypatch):
    rec: dict = {}
    monkeypatch.setattr(tx, "_place_market_order",
                        _recorder(rec, "_place_market_order"))
    monkeypatch.setattr(tx, "_place_limit_order",
                        _recorder(rec, "_place_limit_order"))
    out = _run(ch._place_order(
        {"symbol": "INFY", "side": "buy", "quantity": 10}, "tok", None, 1))
    assert out["success"] and rec["target"] == "_place_market_order"
    assert rec["args"]["transaction_type"] == "BUY"

    out = _run(ch._place_order(
        {"symbol": "INFY", "side": "sell", "quantity": 5, "price": 1450},
        "tok", None, 1))
    assert out["success"] and rec["target"] == "_place_limit_order"
    assert rec["args"]["transaction_type"] == "SELL"


def test_indicators_tolerates_bare_string(monkeypatch):
    rec: dict = {}
    monkeypatch.setattr(tx, "_get_multiple_indicators",
                        _recorder(rec, "_get_multiple_indicators"))
    out = _run(ch._get_indicators(
        {"symbol": "TCS", "indicators": "rsi"}, "tok", None, 1))
    assert out["success"] and rec["args"]["indicators"] == ["rsi"]


def test_merged_tools_visible_and_narrow_hidden():
    from backend.services.tool_registry import get_tool_schema
    names = {d["function"]["name"] for d in get_tool_schema()}
    assert {"get_market_data", "get_portfolio", "manage_automation",
            "get_indicators", "place_order"} <= names
    assert not ({"get_live_price", "place_market_order", "list_sips",
                 "get_holdings", "get_indicator"} & names)
