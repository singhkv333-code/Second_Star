"""End-to-end ChatService tests using a stub LLM client.

Covers the full flow without hitting any real provider:
  - Plain text reply (no tool call)
  - Tool call → execute → narrate (two-hop)
  - ASK_USER bubble surfaces as the assistant message
  - Validation failure surfaces as a structured error

The stub implements LLMClient.complete and returns a programmable
response queue. ChatService picks it up via set_llm_client_for_tests.
"""
from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest

from backend.llm import LLMClient, LLMMessage, LLMResponse, ToolDef
from backend.llm.factory import set_llm_client_for_tests
from backend.services.chat_service import (
    ChatService,
    UserContext,
)
from backend.services import validation_retry as vr
from backend.services.tool_registry import ToolResult


class _StubClient(LLMClient):
    provider_name = "stub"
    model = "stub-model"

    def __init__(self, queue: list[LLMResponse]) -> None:
        self.queue = list(queue)
        self.calls: list[dict[str, Any]] = []

    async def complete(self, messages, **kwargs):
        self.calls.append({"messages": messages, "kwargs": kwargs})
        if not self.queue:
            return LLMResponse(content="(empty queue)", finish_reason="stop")
        return self.queue.pop(0)


class _StubStore:
    """In-memory replacement for ConversationStore so we don't need
    a Redis connection during tests."""
    def __init__(self) -> None:
        self.appended: list[tuple[str, str, str]] = []

    def get_history(self, conv_id: str, limit: int = 20):
        return []

    def append(self, conv_id: str, user: str, assistant: str) -> None:
        self.appended.append((conv_id, user, assistant))


@pytest.fixture
def stub_ctx():
    return UserContext(user_id=1, kite_token="x", db=None, holdings=[])


@pytest.fixture(autouse=True)
def _clear_stub():
    set_llm_client_for_tests(None)
    yield
    set_llm_client_for_tests(None)


@pytest.mark.asyncio
async def test_plain_text_reply_passes_through(stub_ctx):
    stub = _StubClient(queue=[
        LLMResponse(content="Pivot is an automation platform.", finish_reason="stop"),
    ])
    set_llm_client_for_tests(stub)
    svc = ChatService(store=_StubStore())
    turn = await svc.handle("what is pivot?", "u1", stub_ctx, history_override=[])
    assert turn.response == "Pivot is an automation platform."
    assert turn.tools_called == []


@pytest.mark.asyncio
async def test_tool_call_two_hop_narration(stub_ctx, monkeypatch):
    """Model picks a tool → wrapper executes → second hop narrates."""
    async def fake_execute(name, args, **kw):
        return ToolResult(
            name=name, args=args, success=True,
            data={"price": 2487.50}, logiccard=None, error=None,
        )
    monkeypatch.setattr(vr, "execute", fake_execute)
    monkeypatch.setattr(vr, "_schema_for_tool", lambda n: {
        "type": "object",
        "properties": {"symbol": {"type": "string"}},
        "required": ["symbol"],
    })

    stub = _StubClient(queue=[
        LLMResponse(
            content=None,
            tool_calls=[{
                "id": "call_1", "name": "get_live_price",
                "arguments": {"symbol": "RELIANCE"},
            }],
            finish_reason="tool_calls",
        ),
        LLMResponse(content="RELIANCE is trading at ₹2,487.50.", finish_reason="stop"),
    ])
    set_llm_client_for_tests(stub)
    svc = ChatService(store=_StubStore())
    turn = await svc.handle("price of reliance", "u1", stub_ctx, history_override=[])

    assert turn.tools_called == ["get_live_price"]
    assert "2,487" in turn.response or "2487" in turn.response
    # Second hop got the tool result message
    second_call = stub.calls[1]
    msgs = second_call["messages"]
    tool_msg = next(m for m in msgs if m.role == "tool")
    assert "2487.5" in tool_msg.content


@pytest.mark.asyncio
async def test_ask_user_surfaces_as_assistant_question(stub_ctx, monkeypatch):
    """When the model picks ASK_USER, the wrapper short-circuits and
    surfaces the question. No second LLM hop, no card."""
    monkeypatch.setattr(vr, "_schema_for_tool", lambda n: None)

    stub = _StubClient(queue=[
        LLMResponse(
            content=None,
            tool_calls=[{
                "id": "askcall", "name": vr.ASK_USER_TOOL_NAME,
                "arguments": {"question": "What dip threshold should trigger the buy?"},
            }],
            finish_reason="tool_calls",
        ),
    ])
    set_llm_client_for_tests(stub)
    svc = ChatService(store=_StubStore())
    turn = await svc.handle("buy on a dip", "u1", stub_ctx, history_override=[])

    assert turn.response == "What dip threshold should trigger the buy?"
    assert turn.tools_called == [vr.ASK_USER_TOOL_NAME]
    assert turn.raw_data == {"_render_hint": "ask_user"}
    # Only one LLM call (no second hop after ASK_USER)
    assert len(stub.calls) == 1


@pytest.mark.asyncio
async def test_validation_failure_after_retry_surfaces_error(stub_ctx, monkeypatch):
    """First call: bad args. Retry: still bad args. Surface error."""
    monkeypatch.setattr(vr, "_schema_for_tool", lambda n: {
        "type": "object",
        "properties": {"symbol": {"type": "string"},
                       "quantity": {"type": "integer"}},
        "required": ["symbol", "quantity"],
    })

    stub = _StubClient(queue=[
        # Initial: model emits a buy with NO quantity
        LLMResponse(
            content=None,
            tool_calls=[{
                "id": "c1", "name": "place_market_order",
                "arguments": {"symbol": "RELIANCE"},
            }],
            finish_reason="tool_calls",
        ),
        # Retry hop: model gives up, returns plain text
        LLMResponse(content="not sure how to proceed", finish_reason="stop"),
    ])
    set_llm_client_for_tests(stub)
    svc = ChatService(store=_StubStore())
    turn = await svc.handle("buy reliance", "u1", stub_ctx, history_override=[])

    assert turn.tools_called == ["place_market_order"]
    assert "couldn't complete" in turn.response.lower()
    assert turn.raw_data.get("_render_hint") == "validation_error"


@pytest.mark.asyncio
async def test_llm_error_returns_unavailable_fallback(stub_ctx):
    stub = _StubClient(queue=[
        LLMResponse(content="HTTP 502 bad gateway", finish_reason="error"),
    ])
    set_llm_client_for_tests(stub)
    svc = ChatService(store=_StubStore())
    turn = await svc.handle("hi", "u1", stub_ctx, history_override=[])
    assert "temporarily unavailable" in turn.response.lower()
    assert turn.raw_data == {"_llm_unavailable": True}
