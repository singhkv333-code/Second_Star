"""OpenAI client — primary chat backend.

Uses the Responses API (`/v1/responses`) rather than chat completions.
Reasons:
  - Native reasoning support: `reasoning.effort` parameter for GPT-5
    family models without the o-series-only restrictions.
  - Cleaner tool-call shape: function_call items appear in `output[]`
    with explicit `call_id`, `name`, `arguments` rather than nested
    inside `choices[0].message.tool_calls[i]`.
  - Forward-compatible with custom tools, file inputs, image inputs.

We hit the API with raw httpx instead of pulling in the openai SDK
because the rest of the codebase already uses httpx and the SDK adds
~50 MB of transitive deps for what amounts to two endpoints.
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any, Literal, Optional

import httpx

from backend.config import settings
from backend.llm.base import (
    FinishReason,
    LLMClient,
    LLMMessage,
    LLMResponse,
    ReasoningEffort,
    ToolDef,
)


logger = logging.getLogger(__name__)


_DEFAULT_TIMEOUT = httpx.Timeout(connect=5.0, read=120.0, write=10.0, pool=5.0)
_API_URL = "https://api.openai.com/v1/responses"


def _messages_to_input(messages: list[LLMMessage]) -> list[dict[str, Any]]:
    """Convert LLMMessage list → Responses API `input` shape.

    The Responses API accepts a typed list:
      - {"role": "system"|"user"|"assistant", "content": str}
      - {"type": "function_call", "call_id", "name", "arguments"}
      - {"type": "function_call_output", "call_id", "output"}
    Tool result messages (role=='tool') get translated into a
    function_call_output item; assistant messages with tool_calls get
    translated into one or more function_call items.
    """
    out: list[dict[str, Any]] = []
    for m in messages:
        if m.role == "tool":
            if not m.tool_call_id:
                # Without an id we have no way to anchor the output —
                # drop with a warning rather than crash.
                logger.warning("LLMMessage role=tool missing tool_call_id; dropping")
                continue
            out.append({
                "type": "function_call_output",
                "call_id": m.tool_call_id,
                "output": m.content or "",
            })
            continue

        if m.role == "assistant" and m.tool_calls:
            # Each tool call becomes its own function_call item.
            for tc in m.tool_calls:
                args = tc.get("arguments", {})
                if not isinstance(args, str):
                    args = json.dumps(args)
                out.append({
                    "type": "function_call",
                    "call_id": tc.get("id", ""),
                    "name": tc.get("name", ""),
                    "arguments": args,
                })
            if m.content:
                out.append({"role": "assistant", "content": m.content})
            continue

        out.append({"role": m.role, "content": m.content})
    return out


def _tools_to_responses_format(tools: list[ToolDef]) -> list[dict[str, Any]]:
    """Responses API accepts tools as flat dicts with type=function."""
    return [
        {
            "type": "function",
            "name": t.name,
            "description": t.description,
            "parameters": t.parameters,
            "strict": t.strict,
        }
        for t in tools
    ]


def _parse_response(data: dict[str, Any]) -> tuple[Optional[str], list[dict[str, Any]], FinishReason]:
    """Pull `content`, `tool_calls`, and `finish_reason` out of the
    Responses-API payload."""
    output_items = data.get("output") or []
    text_parts: list[str] = []
    tool_calls: list[dict[str, Any]] = []

    for item in output_items:
        itype = item.get("type")
        if itype == "function_call":
            args_str = item.get("arguments", "") or "{}"
            try:
                args = json.loads(args_str) if args_str else {}
            except json.JSONDecodeError:
                # Malformed args — surface as raw string so the validator
                # can produce a useful retry hop.
                args = {"_raw_arguments": args_str, "_parse_error": True}
            tool_calls.append({
                "id": item.get("call_id") or item.get("id") or "",
                "name": item.get("name", ""),
                "arguments": args,
            })
        elif itype == "message":
            for c in item.get("content") or []:
                if isinstance(c, dict) and c.get("type") == "output_text":
                    text_parts.append(c.get("text", ""))
        # `reasoning` items are present on GPT-5 outputs; we don't
        # surface their content (it's the <think> trace) but the
        # token counts are accounted for via `usage.reasoning_tokens`.

    content = "\n".join(p for p in text_parts if p).strip() or None

    # Responses API uses status + incomplete_details rather than a
    # finish_reason field. Map them onto our enum.
    status = data.get("status", "")
    incomplete = (data.get("incomplete_details") or {}).get("reason", "")
    if tool_calls:
        finish: FinishReason = "tool_calls"
    elif incomplete == "max_output_tokens":
        finish = "length"
    elif status in {"completed", ""}:
        finish = "stop"
    else:
        finish = "error"

    return content, tool_calls, finish


class LLMOpenAI(LLMClient):
    """OpenAI Responses API client. Default model: gpt-5-mini."""

    provider_name = "openai"

    def __init__(self, model: Optional[str] = None, api_key: Optional[str] = None) -> None:
        self.model = model or settings.llm_model or "gpt-5-mini"
        self._api_key = api_key or settings.openai_api_key

    async def complete(
        self,
        messages: list[LLMMessage],
        *,
        tools: Optional[list[ToolDef]] = None,
        tool_choice: Literal["auto", "required", "none"] = "auto",
        max_output_tokens: int = 1500,
        reasoning_effort: Optional[ReasoningEffort] = None,
        temperature: float = 0.2,
        response_format: Optional[Literal["json_object"]] = None,
    ) -> LLMResponse:
        if not self._api_key:
            return LLMResponse(
                content="OPENAI_API_KEY is not set",
                finish_reason="error",
                model=self.model,
                raw={"error": "missing_api_key"},
            )

        payload: dict[str, Any] = {
            "model": self.model,
            "input": _messages_to_input(messages),
            "max_output_tokens": max_output_tokens,
        }
        # GPT-5 family ignores temperature for reasoning runs; setting
        # it on a non-reasoning model still works. We send it
        # universally since the provider accepts/ignores per-model.
        if temperature is not None and not _is_reasoning_model(self.model):
            payload["temperature"] = temperature
        if reasoning_effort and _is_reasoning_model(self.model):
            payload["reasoning"] = {"effort": reasoning_effort}
        if tools:
            payload["tools"] = _tools_to_responses_format(tools)
            if tool_choice in {"required", "none"}:
                payload["tool_choice"] = tool_choice
            else:
                payload["tool_choice"] = "auto"
        if response_format == "json_object":
            payload["response_format"] = {"type": "json_object"}

        started = time.monotonic()
        try:
            async with httpx.AsyncClient(timeout=_DEFAULT_TIMEOUT) as client:
                resp = await client.post(
                    _API_URL,
                    headers={
                        "Authorization": f"Bearer {self._api_key}",
                        "Content-Type": "application/json",
                    },
                    json=payload,
                )
        except httpx.HTTPError as e:
            return LLMResponse(
                content=f"transport error: {type(e).__name__}: {e}",
                finish_reason="error",
                model=self.model,
                raw={"transport_error": str(e)},
                latency_ms=int((time.monotonic() - started) * 1000),
            )

        latency_ms = int((time.monotonic() - started) * 1000)

        if resp.status_code != 200:
            # Don't raise — return as a structured error so the caller
            # can decide how to react (retry, fall back, surface).
            body_preview = resp.text[:500]
            logger.warning(
                "OpenAI Responses API %s: %s", resp.status_code, body_preview
            )
            return LLMResponse(
                content=f"OpenAI {resp.status_code}: {body_preview}",
                finish_reason="error",
                model=self.model,
                raw={"http_status": resp.status_code, "body": body_preview},
                latency_ms=latency_ms,
            )

        data = resp.json()
        content, tool_calls, finish = _parse_response(data)

        usage = data.get("usage") or {}
        return LLMResponse(
            content=content,
            tool_calls=tool_calls or None,
            finish_reason=finish,
            input_tokens=int(usage.get("input_tokens", 0) or 0),
            output_tokens=int(usage.get("output_tokens", 0) or 0),
            reasoning_tokens=int(
                (usage.get("output_tokens_details") or {}).get("reasoning_tokens", 0) or 0
            ),
            latency_ms=latency_ms,
            model=data.get("model", self.model),
            raw=data,
        )


def _is_reasoning_model(name: str) -> bool:
    """True for GPT-5 family + o-series. Used to decide whether to
    send reasoning.effort and whether to skip temperature."""
    n = (name or "").lower()
    return n.startswith(("gpt-5", "o1", "o3", "o4"))
