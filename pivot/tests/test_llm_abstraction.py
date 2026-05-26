"""Unit tests for the LLM abstraction layer.

Covers:
  - LLMMessage / ToolDef / LLMResponse model contracts
  - Factory env-var selection + reset
  - OpenAI message-shape conversion (system/user/assistant/tool/tool_calls)
  - OpenAI tool-defs serialisation
  - OpenAI response parsing (text-only / function_call / mixed)
"""
from __future__ import annotations

import json
import os
from unittest.mock import patch

import pytest

from backend.llm import LLMClient, LLMMessage, LLMResponse, ToolDef, get_llm_client
from backend.llm.factory import (
    reset_llm_client_cache,
    set_llm_client_for_tests,
)
from backend.llm.openai_client import (
    LLMOpenAI,
    _is_reasoning_model,
    _messages_to_input,
    _parse_response,
    _tools_to_responses_format,
)
# backend.llm.sarvam_client was removed when the chat path migrated
# to native function-calling via Azure OpenAI / OpenAI Responses.
# The LLMSarvam class and its emulated-tool-call helpers
# (_build_tool_instruction / _extract_emulated_tool_call) no longer
# exist; tests below that referenced them are removed.


# ── Models ──────────────────────────────────────────────────────────


def test_llm_message_rejects_unknown_role():
    with pytest.raises(Exception):
        LLMMessage(role="banana", content="x")


def test_tool_def_minimal():
    t = ToolDef(name="foo", description="bar")
    assert t.name == "foo"
    assert t.parameters == {}
    assert t.strict is False


def test_llm_response_defaults():
    r = LLMResponse()
    assert r.finish_reason == "stop"
    assert r.tool_calls is None
    assert r.input_tokens == 0


# ── Factory ─────────────────────────────────────────────────────────


def test_factory_returns_openai_by_default(monkeypatch):
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    monkeypatch.delenv("LLM_MODEL", raising=False)
    monkeypatch.setattr("backend.config.settings.llm_provider", "openai")
    monkeypatch.setattr("backend.config.settings.llm_model", "")
    set_llm_client_for_tests(None)
    reset_llm_client_cache()
    client = get_llm_client()
    assert client.provider_name == "openai"
    assert client.model == "gpt-5-mini"


def test_factory_switches_to_azure(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "azure")
    monkeypatch.setenv("LLM_MODEL", "gpt-5.4-mini")
    monkeypatch.setattr(
        "backend.config.settings.azure_openai_endpoint",
        "https://stub.services.ai.azure.com/openai/v1",
    )
    monkeypatch.setattr("backend.config.settings.azure_key", "stub-key")
    set_llm_client_for_tests(None)
    reset_llm_client_cache()
    client = get_llm_client()
    assert client.provider_name == "azure"
    assert client.model == "gpt-5.4-mini"


def test_factory_raises_on_unknown_provider(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "claude-3")
    set_llm_client_for_tests(None)
    reset_llm_client_cache()
    with pytest.raises(ValueError, match="Unknown LLM_PROVIDER"):
        get_llm_client()


def test_set_llm_client_for_tests_overrides_factory():
    class StubClient(LLMClient):
        provider_name = "stub"
        async def complete(self, *a, **kw):
            return LLMResponse(content="stubbed")
    stub = StubClient()
    set_llm_client_for_tests(stub)
    try:
        assert get_llm_client() is stub
    finally:
        set_llm_client_for_tests(None)


# ── OpenAI helpers ──────────────────────────────────────────────────


def test_openai_messages_simple_chat():
    msgs = [
        LLMMessage(role="system", content="you are helpful"),
        LLMMessage(role="user", content="hi"),
    ]
    out = _messages_to_input(msgs)
    assert out == [
        {"role": "system", "content": "you are helpful"},
        {"role": "user", "content": "hi"},
    ]


def test_openai_messages_assistant_with_tool_calls():
    msgs = [
        LLMMessage(
            role="assistant",
            content="",
            tool_calls=[{"id": "c1", "name": "get_x", "arguments": {"y": 1}}],
        ),
        LLMMessage(role="tool", tool_call_id="c1", name="get_x", content="result"),
    ]
    out = _messages_to_input(msgs)
    assert out[0]["type"] == "function_call"
    assert out[0]["call_id"] == "c1"
    assert out[0]["name"] == "get_x"
    assert json.loads(out[0]["arguments"]) == {"y": 1}
    assert out[1]["type"] == "function_call_output"
    assert out[1]["call_id"] == "c1"
    assert out[1]["output"] == "result"


def test_openai_tools_serialisation():
    tools = [ToolDef(name="t", description="d", parameters={"type": "object"}, strict=True)]
    out = _tools_to_responses_format(tools)
    assert out == [{
        "type": "function", "name": "t", "description": "d",
        "parameters": {"type": "object"}, "strict": True,
    }]


def test_openai_parse_text_only_response():
    data = {
        "status": "completed",
        "output": [
            {
                "type": "message",
                "content": [{"type": "output_text", "text": "hello"}],
            }
        ],
    }
    content, tool_calls, finish = _parse_response(data)
    assert content == "hello"
    assert tool_calls == []
    assert finish == "stop"


def test_openai_parse_function_call_response():
    data = {
        "status": "completed",
        "output": [
            {"type": "function_call", "call_id": "abc", "name": "do_x",
             "arguments": '{"foo": "bar"}'},
        ],
    }
    content, tool_calls, finish = _parse_response(data)
    assert content is None
    assert tool_calls == [{"id": "abc", "name": "do_x",
                           "arguments": {"foo": "bar"}}]
    assert finish == "tool_calls"


def test_openai_parse_malformed_function_args_does_not_crash():
    data = {
        "status": "completed",
        "output": [
            {"type": "function_call", "call_id": "x", "name": "y",
             "arguments": "{not-json"},
        ],
    }
    _, tool_calls, _ = _parse_response(data)
    # Surface the parse error in args so the validator can react.
    assert tool_calls[0]["arguments"]["_parse_error"] is True


def test_openai_parse_length_finish():
    data = {
        "status": "incomplete",
        "incomplete_details": {"reason": "max_output_tokens"},
        "output": [{"type": "message", "content": [
            {"type": "output_text", "text": "partial..."},
        ]}],
    }
    _, _, finish = _parse_response(data)
    assert finish == "length"


def test_is_reasoning_model_classification():
    assert _is_reasoning_model("gpt-5-mini") is True
    assert _is_reasoning_model("gpt-5") is True
    assert _is_reasoning_model("o1-mini") is True
    assert _is_reasoning_model("o3") is True
    assert _is_reasoning_model("gpt-4o-mini") is False
    assert _is_reasoning_model("") is False


@pytest.mark.asyncio
async def test_openai_returns_error_on_missing_key():
    client = LLMOpenAI(model="gpt-5-mini", api_key="")
    r = await client.complete(messages=[LLMMessage(role="user", content="hi")])
    assert r.finish_reason == "error"
    assert "OPENAI_API_KEY" in (r.content or "")


# Sarvam-specific tests removed — backend.llm.sarvam_client and
# backend.agents.sarvam_client were deleted when the chat path migrated
# to native function-calling via Azure OpenAI / OpenAI Responses.
