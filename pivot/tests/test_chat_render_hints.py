"""Regression tests for chat → frontend render-hint surfacing.

The frontend reads `_render_hint` at the top level of `raw_data`. Some
tools (notably propose_workflow) put their render hint inside
`raw_data[tool_name]` because chat_service stores tool result data
keyed by tool name. The chat router lifts the nested payload up so
the FE can dispatch on it.

This test specifically guards against the bug observed in
screenshot/2026-05-03 where `propose_workflow` returned a draft but
the FE only saw the disclaimer text, never the workflow_draft_card.
"""
from unittest.mock import patch, AsyncMock

from backend.services.chat_service import (
    ChatService, ChatTurn, UserContext, _tool_summary_line, _GENERIC_FALLBACK,
)
from backend.services.tool_registry import ToolResult


def test_tool_summary_line_for_propose_workflow():
    line = _tool_summary_line("propose_workflow", None)
    assert "draft" in line.lower()


def test_tool_summary_line_for_logiccard_with_action_and_symbol():
    lc = {"action": "BUY", "symbol": "RELIANCE", "type": "market_order"}
    line = _tool_summary_line("place_market_order", lc)
    assert "BUY" in line and "RELIANCE" in line


def test_tool_summary_line_for_get_tool():
    assert _tool_summary_line("get_holdings", None) == "Here's what I found."


def test_render_hint_lifted_from_nested_tool_payload(client, auth_headers):
    """End-to-end: when a tool returns a payload with `_render_hint`,
    the chat router must lift it so the FE sees raw_data._render_hint
    at the top level. We patch chat_service.handle to simulate a
    propose_workflow tool result."""
    fake_turn = ChatTurn(
        response="Here's a draft of that workflow.",
        tools_called=["propose_workflow"],
        logiccard=None,
        latency_ms=0,
        sanitised=False,
        raw_data={
            "propose_workflow": {
                "_render_hint": "workflow_draft_card",
                "name": "RELIANCE 3:55 PM buy",
                "description": "Buy 10 RELIANCE every weekday",
                "steps": [
                    {"step_type": "trigger.schedule",
                     "label": "Every weekday at 3:55 PM IST", "config": {}},
                    {"step_type": "action.place_order",
                     "label": "Buy 10 RELIANCE", "config": {}},
                ],
                "rationale": "Test", "warnings": [],
            },
        },
    )

    async def fake_handle(self, message, conv_id, ctx, **kwargs):
        return fake_turn

    with patch.object(ChatService, "handle", new=fake_handle):
        r = client.post(
            "/chat",
            json={"messages": [{"role": "user", "content": "build me an agent"}],
                  "include_portfolio_context": False},
            headers=auth_headers,
        )

    assert r.status_code == 200, r.text
    body = r.json()
    rd = body.get("raw_data") or {}
    # The fix: _render_hint must be at the TOP level so the FE can
    # dispatch without descending into raw_data[tool_name].
    assert rd.get("_render_hint") == "workflow_draft_card", rd
    assert rd.get("name") == "RELIANCE 3:55 PM buy", rd
    assert rd.get("steps") and len(rd["steps"]) == 2, rd


def test_render_hint_does_not_overwrite_an_existing_top_level_hint(client, auth_headers):
    """If the response already has _render_hint at the top level (e.g. set
    by _run_indicator_backtest), we must NOT overwrite it from a nested
    tool dict."""
    fake_turn = ChatTurn(
        response="ok",
        tools_called=[],
        logiccard=None,
        latency_ms=0,
        sanitised=False,
        raw_data={
            "_render_hint": "indicator_backtest_chart",
            "symbol": "RELIANCE",
            # A spurious nested dict with a different hint should not win.
            "some_tool": {"_render_hint": "logic_card", "type": "market_order"},
        },
    )

    async def fake_handle(self, message, conv_id, ctx, **kwargs):
        return fake_turn

    with patch.object(ChatService, "handle", new=fake_handle):
        r = client.post(
            "/chat",
            json={"messages": [{"role": "user", "content": "backtest reliance rsi"}],
                  "include_portfolio_context": False},
            headers=auth_headers,
        )

    assert r.json()["raw_data"]["_render_hint"] == "indicator_backtest_chart"


def test_logiccard_response_falls_through_to_logic_card_hint(client, auth_headers):
    """When logiccard is set and no other hint exists, we tag logic_card.
    Existing behaviour — guarded so the lift change doesn't regress it."""
    fake_turn = ChatTurn(
        response="Buy 10 RELIANCE at market.",
        tools_called=["place_market_order"],
        logiccard={"type": "market_order", "action": "BUY", "symbol": "RELIANCE",
                   "details": [], "explanation": "x", "disclaimer": "y",
                   "requires_confirmation": True},
        latency_ms=0,
        sanitised=False,
        raw_data={"place_market_order": {}},   # no _render_hint inside
    )

    async def fake_handle(self, message, conv_id, ctx, **kwargs):
        return fake_turn

    with patch.object(ChatService, "handle", new=fake_handle):
        r = client.post(
            "/chat",
            json={"messages": [{"role": "user", "content": "buy 10 RELIANCE"}],
                  "include_portfolio_context": False},
            headers=auth_headers,
        )

    assert r.json()["raw_data"]["_render_hint"] == "logic_card"
