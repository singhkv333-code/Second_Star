"""Tests for the declarative tool-defaults registry.

The registry lets the LLM emit only REQUIRED fields and have optional
fields auto-filled by the executor before dispatch. This file pins:

  * ``get_tool_defaults`` returns the documented dict.
  * The executor merges defaults into the handler args.
  * User-supplied values always win the merge.
"""
from __future__ import annotations

import asyncio

from backend.agents import tool_executor
from backend.agents.tools import get_tool_defaults


def _run(coro):
    # Use a fresh loop — if another test (e.g. pytest-asyncio strict-mode
    # ones) closed the global default loop, get_event_loop raises RuntimeError.
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def test_get_tool_defaults_place_market_order():
    d = get_tool_defaults("place_market_order")
    assert d == {"exchange": "NSE", "product": "CNC", "order_type": "MARKET"}


def test_get_tool_defaults_returns_copy():
    d1 = get_tool_defaults("place_market_order")
    d1["exchange"] = "BSE"
    d2 = get_tool_defaults("place_market_order")
    # Mutating the first dict must not poison the registry.
    assert d2["exchange"] == "NSE"


def test_get_tool_defaults_unknown_tool_returns_empty():
    assert get_tool_defaults("does_not_exist") == {}


def test_execute_tool_injects_defaults(monkeypatch):
    captured: dict = {}

    async def fake_handler(args, kt, db, uid):
        captured.update(args)
        return {"success": True, "data": {}, "logiccard": None}

    monkeypatch.setattr(
        tool_executor, "_place_market_order", fake_handler
    )

    user_args = {"symbol": "INFY", "quantity": 1, "transaction_type": "BUY"}
    result = _run(tool_executor.execute_tool(
        "place_market_order", user_args,
        kite_token="t", db=None, user_id=1,
    ))

    assert result["success"] is True
    # Defaults were merged in.
    assert captured["exchange"] == "NSE"
    assert captured["product"] == "CNC"
    assert captured["order_type"] == "MARKET"
    # Required fields preserved.
    assert captured["symbol"] == "INFY"
    assert captured["quantity"] == 1
    assert captured["transaction_type"] == "BUY"


def test_execute_tool_user_value_wins(monkeypatch):
    captured: dict = {}

    async def fake_handler(args, kt, db, uid):
        captured.update(args)
        return {"success": True, "data": {}, "logiccard": None}

    monkeypatch.setattr(
        tool_executor, "_place_market_order", fake_handler
    )

    user_args = {
        "symbol": "INFY", "quantity": 1, "transaction_type": "BUY",
        "exchange": "BSE",  # user override
        "product": "MIS",   # user override
    }
    _run(tool_executor.execute_tool(
        "place_market_order", user_args,
        kite_token="t", db=None, user_id=1,
    ))

    assert captured["exchange"] == "BSE"
    assert captured["product"] == "MIS"
    # Default still wins for the field the user didn't set.
    assert captured["order_type"] == "MARKET"
