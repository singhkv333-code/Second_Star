"""Unit tests for validate-and-retry tool execution.

Covers:
  - Pydantic-style error formatter (terse, structured, one line per error)
  - JSON-Schema arg validator (required / type / enum)
  - The retry loop: success on first try, fix-it on second, ASK_USER bubble,
    LLM error during retry, max-retries exhausted
"""
from __future__ import annotations

import pytest
from pydantic import BaseModel, Field, ValidationError

from backend.llm.base import LLMClient, LLMMessage, LLMResponse, ToolDef
from backend.services import validation_retry as vr


# ── Error formatter ─────────────────────────────────────────────────


class _DummyOrder(BaseModel):
    symbol: str = Field(..., min_length=1)
    quantity: int = Field(..., gt=0)


def test_format_validation_errors_includes_loc_and_msg():
    try:
        _DummyOrder.model_validate({"quantity": -1})
    except ValidationError as e:
        out = vr.format_validation_errors_terse(e)
    assert "symbol" in out
    assert "quantity" in out
    assert "Field required" in out
    assert "-1" in out


def test_format_validation_errors_handles_root_loc():
    class Strict(BaseModel):
        x: int
    try:
        Strict.model_validate("not a dict")
    except ValidationError as e:
        out = vr.format_validation_errors_terse(e)
    assert out  # produces SOMETHING, not empty


# ── Arg validator ───────────────────────────────────────────────────


def test_validate_args_passes_when_valid():
    schema = {
        "type": "object",
        "properties": {"x": {"type": "string"}, "n": {"type": "integer"}},
        "required": ["x"],
    }
    assert vr._validate_args_against_schema({"x": "hi", "n": 1}, schema) is None


def test_validate_args_flags_missing_required():
    schema = {"type": "object", "properties": {"x": {"type": "string"}}, "required": ["x"]}
    err = vr._validate_args_against_schema({}, schema)
    assert err and "x" in err and "required" in err.lower()


def test_validate_args_flags_type_mismatch():
    schema = {"type": "object", "properties": {"n": {"type": "integer"}}, "required": []}
    err = vr._validate_args_against_schema({"n": "not-an-int"}, schema)
    assert err and "integer" in err and "str" in err


def test_validate_args_flags_enum_membership():
    schema = {"type": "object", "properties": {
        "side": {"type": "string", "enum": ["BUY", "SELL"]},
    }, "required": []}
    err = vr._validate_args_against_schema({"side": "HODL"}, schema)
    assert err and "BUY" in err and "SELL" in err


def test_validate_args_returns_none_for_unknown_schema():
    assert vr._validate_args_against_schema({"x": 1}, None) is None  # type: ignore


# ── ASK_USER tool def + intercept ───────────────────────────────────


def test_ask_user_tool_def_shape():
    t = vr.ask_user_tool_def()
    assert t.name == vr.ASK_USER_TOOL_NAME
    assert "question" in t.parameters["properties"]
    assert "question" in t.parameters["required"]


@pytest.mark.asyncio
async def test_ask_user_intercepted_returns_clarification():
    """ASK_USER must NOT hit the executor — surfaces the question
    directly so the chat service can render it as an assistant reply."""
    result = await vr.execute_tool_with_retry(
        vr.ASK_USER_TOOL_NAME,
        {"question": "What dip threshold should trigger the buy?"},
        llm_client=_StubLLMClient(),
        conversation_messages=[],
        tools_for_retry=[],
        kite_token="x", db=None, user_id=1,
    )
    assert result.needs_clarification is True
    assert result.question == "What dip threshold should trigger the buy?"
    assert result.success is False


@pytest.mark.asyncio
async def test_ask_user_with_empty_question_triggers_retry():
    """Empty question now goes through the validation-retry hop so the
    LLM can fix its own malformed call. Previously it dumped straight
    to an error, which surfaced as a confusing "validation_error"
    message in chat for perfectly reasonable user prompts.

    With max_retries=0 we skip the LLM hop and surface the validation
    error directly — that's what we assert here."""
    result = await vr.execute_tool_with_retry(
        vr.ASK_USER_TOOL_NAME,
        {"question": "  "},
        llm_client=_StubLLMClient(),
        conversation_messages=[],
        tools_for_retry=[],
        kite_token="x", db=None, user_id=1,
        max_retries=0,
    )
    assert result.needs_clarification is False
    assert "minimum length" in (result.error or "").lower()


# ── Retry loop ──────────────────────────────────────────────────────


class _StubLLMClient(LLMClient):
    """Programmable stub: each call returns the next queued response."""
    provider_name = "stub"

    def __init__(self, responses: list[LLMResponse] | None = None) -> None:
        self._responses = list(responses or [])
        self.calls: list[dict] = []

    async def complete(self, messages, **kwargs):
        self.calls.append({
            "messages": messages, "tools": kwargs.get("tools"),
            "tool_choice": kwargs.get("tool_choice"),
        })
        if not self._responses:
            return LLMResponse(content="(no stubbed response)", finish_reason="stop")
        return self._responses.pop(0)


@pytest.mark.asyncio
async def test_retry_loop_executes_when_args_already_valid(monkeypatch):
    """Happy path: args validate → execute returns ToolResult → done."""
    from backend.services.tool_registry import ToolResult

    async def fake_execute(name, args, **kw):
        return ToolResult(name=name, args=args, success=True,
                          data={"ran": True}, logiccard=None, error=None)

    monkeypatch.setattr(vr, "execute", fake_execute)
    monkeypatch.setattr(vr, "_schema_for_tool", lambda n: {
        "type": "object", "properties": {"x": {"type": "string"}}, "required": ["x"],
    })

    out = await vr.execute_tool_with_retry(
        "valid_tool",
        {"x": "ok"},
        llm_client=_StubLLMClient(),
        conversation_messages=[],
        tools_for_retry=[],
        kite_token="x", db=None, user_id=1,
    )
    assert out.success is True
    assert out.data == {"ran": True}


@pytest.mark.asyncio
async def test_retry_loop_fixes_args_via_llm(monkeypatch):
    """Bad args → LLM gets the validation error, returns new tool_call
    with corrected args → execute succeeds."""
    from backend.services.tool_registry import ToolResult

    async def fake_execute(name, args, **kw):
        return ToolResult(name=name, args=args, success=True,
                          data={"args_seen": args}, logiccard=None, error=None)

    monkeypatch.setattr(vr, "execute", fake_execute)
    monkeypatch.setattr(vr, "_schema_for_tool", lambda n: {
        "type": "object", "properties": {"qty": {"type": "integer"}},
        "required": ["qty"],
    })

    fix_resp = LLMResponse(
        content=None,
        tool_calls=[{
            "id": "fix1", "name": "buy", "arguments": {"qty": 5},
        }],
        finish_reason="tool_calls",
    )
    stub = _StubLLMClient(responses=[fix_resp])

    out = await vr.execute_tool_with_retry(
        "buy",
        {"qty": "five"},  # wrong type
        llm_client=stub,
        conversation_messages=[LLMMessage(role="user", content="buy 5")],
        tools_for_retry=[ToolDef(name="buy", description="d", parameters={})],
        kite_token="x", db=None, user_id=1,
    )

    assert out.success is True
    assert out.data == {"args_seen": {"qty": 5}}
    assert len(stub.calls) == 1
    # Validation error gets sent in the tool-result message
    last_call = stub.calls[0]
    msgs = last_call["messages"]
    tool_msg = next(m for m in msgs if m.role == "tool")
    assert "VALIDATION_FAILED" in tool_msg.content


@pytest.mark.asyncio
async def test_retry_loop_escalates_to_ask_user(monkeypatch):
    """Bad args → LLM picks ASK_USER on retry → needs_clarification."""
    monkeypatch.setattr(vr, "_schema_for_tool", lambda n: {
        "type": "object", "properties": {"threshold": {"type": "number"}},
        "required": ["threshold"],
    })

    fix_resp = LLMResponse(
        content=None,
        tool_calls=[{
            "id": "ask", "name": vr.ASK_USER_TOOL_NAME,
            "arguments": {"question": "What dip % should trigger the buy?"},
        }],
        finish_reason="tool_calls",
    )
    stub = _StubLLMClient(responses=[fix_resp])

    out = await vr.execute_tool_with_retry(
        "buy_on_dip",
        {},  # missing threshold
        llm_client=stub,
        conversation_messages=[LLMMessage(role="user", content="buy on dip")],
        tools_for_retry=[
            ToolDef(name="buy_on_dip", description="d", parameters={}),
            vr.ask_user_tool_def(),
        ],
        kite_token="x", db=None, user_id=1,
    )
    assert out.needs_clarification is True
    assert "dip" in (out.question or "").lower()


@pytest.mark.asyncio
async def test_retry_loop_surfaces_error_when_max_retries_exhausted(monkeypatch):
    """LLM returns no tool call on retry → we stop and return error."""
    monkeypatch.setattr(vr, "_schema_for_tool", lambda n: {
        "type": "object", "properties": {"x": {"type": "string"}}, "required": ["x"],
    })

    no_tool_resp = LLMResponse(content="hmm I'm not sure", finish_reason="stop")
    stub = _StubLLMClient(responses=[no_tool_resp])

    out = await vr.execute_tool_with_retry(
        "thing",
        {},  # missing x
        llm_client=stub,
        conversation_messages=[],
        tools_for_retry=[ToolDef(name="thing", description="d", parameters={})],
        kite_token="x", db=None, user_id=1,
    )
    assert out.success is False
    assert "did not produce a tool call" in (out.error or "")


@pytest.mark.asyncio
async def test_retry_loop_surfaces_llm_error_during_retry(monkeypatch):
    monkeypatch.setattr(vr, "_schema_for_tool", lambda n: {
        "type": "object", "properties": {"x": {"type": "string"}}, "required": ["x"],
    })
    err_resp = LLMResponse(content="HTTP 500", finish_reason="error")
    stub = _StubLLMClient(responses=[err_resp])

    out = await vr.execute_tool_with_retry(
        "thing",
        {},
        llm_client=stub,
        conversation_messages=[],
        tools_for_retry=[ToolDef(name="thing", description="d", parameters={})],
        kite_token="x", db=None, user_id=1,
    )
    assert out.success is False
    assert "LLM error during retry" in (out.error or "")
