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
from typing import Any, AsyncIterator, Literal, Optional

import httpx

from backend.config import settings
from backend.llm._trace import CallTrace
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
_API_URL = "https://api.openai.com/v1/responses"  # legacy module-level constant; LLMOpenAI.API_URL is the live source of truth.


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
    """OpenAI Responses API client. Default model: gpt-5-mini.

    URL + auth-header strategy are class attributes so Azure-on-Foundry
    can subclass and override (Azure uses `api-key:` header instead of
    `Authorization: Bearer` and a tenant-specific base URL).
    """

    provider_name = "openai"
    API_URL: str = "https://api.openai.com/v1/responses"

    def __init__(self, model: Optional[str] = None, api_key: Optional[str] = None) -> None:
        self.model = model or settings.llm_model or "gpt-5-mini"
        self._api_key = api_key or settings.openai_api_key

    def _auth_headers(self) -> dict[str, str]:
        """Headers sent with every Responses-API call. Override in
        subclasses with a different auth shape."""
        return {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

    def _translate_reasoning_effort(self, effort: Optional[str]) -> Optional[str]:
        """Map our ReasoningEffort literal onto whatever the provider
        accepts on the wire. OpenAI accepts 'minimal' natively; Azure
        rejects it on gpt-5.4 deployments and wants 'none' instead.
        Subclasses override to do the mapping."""
        return effort

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
        prompt_cache_key: Optional[str] = None,
    ) -> LLMResponse:
        trace = CallTrace(
            kind="complete",
            messages=messages,
            tools=tools,
            model=self.model,
            reasoning_effort=reasoning_effort,
            prompt_cache_key=prompt_cache_key,
            max_output_tokens=max_output_tokens,
            provider=self.provider_name,
        )

        if not self._api_key:
            with trace as t:
                resp = LLMResponse(
                    content="OPENAI_API_KEY is not set",
                    finish_reason="error",
                    model=self.model,
                    raw={"error": "missing_api_key"},
                )
                t.set_response(resp)
                return resp

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
            effort_on_wire = self._translate_reasoning_effort(reasoning_effort)
            if effort_on_wire:
                payload["reasoning"] = {"effort": effort_on_wire}
        if tools:
            payload["tools"] = _tools_to_responses_format(tools)
            if tool_choice in {"required", "none"}:
                payload["tool_choice"] = tool_choice
            else:
                payload["tool_choice"] = "auto"
        if response_format == "json_object":
            # Responses API moved this from `response_format` (Chat
            # Completions location) to `text.format`. Sending the old
            # key 400s with "Unsupported parameter: 'response_format'".
            payload["text"] = {"format": {"type": "json_object"}}
        # Prompt caching: a stable per-role key hints the API to keep
        # the system + tools prefix in its cache. Cache hits drop input
        # token cost ~75% and trim ~150-300ms off the first-token
        # latency. The cache TTL is on the order of minutes; the key
        # is a hint, not a binding identifier — same prefix bytes still
        # cache without the key, but routing is much more reliable
        # with it.
        if prompt_cache_key:
            payload["prompt_cache_key"] = prompt_cache_key

        with trace as t:
            started = time.monotonic()
            try:
                async with httpx.AsyncClient(timeout=_DEFAULT_TIMEOUT) as client:
                    resp = await client.post(
                        self.API_URL,
                        headers=self._auth_headers(),
                        json=payload,
                    )
            except httpx.HTTPError as e:
                err_resp = LLMResponse(
                    content=f"transport error: {type(e).__name__}: {e}",
                    finish_reason="error",
                    model=self.model,
                    raw={"transport_error": str(e)},
                    latency_ms=int((time.monotonic() - started) * 1000),
                )
                t.set_response(err_resp)
                return err_resp

            latency_ms = int((time.monotonic() - started) * 1000)

            if resp.status_code != 200:
                body_preview = resp.text[:500]
                logger.warning(
                    "OpenAI Responses API %s: %s", resp.status_code, body_preview
                )
                err_resp = LLMResponse(
                    content=f"OpenAI {resp.status_code}: {body_preview}",
                    finish_reason="error",
                    model=self.model,
                    raw={"http_status": resp.status_code, "body": body_preview},
                    latency_ms=latency_ms,
                )
                t.set_response(err_resp)
                return err_resp

            data = resp.json()
            content, tool_calls, finish = _parse_response(data)

            usage = data.get("usage") or {}
            cached_tokens = int(
                (usage.get("input_tokens_details") or {}).get("cached_tokens", 0) or 0
            )
            # Explicit hand-off to the trace so the cost ledger sees the
            # cached-input subtotal even if the LLMResponse shape ever
            # drops cached_tokens. Belt-and-brace with set_response below.
            t.set_cached_tokens(cached_tokens)
            final = LLMResponse(
                content=content,
                tool_calls=tool_calls or None,
                finish_reason=finish,
                input_tokens=int(usage.get("input_tokens", 0) or 0),
                output_tokens=int(usage.get("output_tokens", 0) or 0),
                reasoning_tokens=int(
                    (usage.get("output_tokens_details") or {}).get("reasoning_tokens", 0) or 0
                ),
                cached_tokens=cached_tokens,
                latency_ms=latency_ms,
                model=data.get("model", self.model),
                raw=data,
            )
            t.set_response(final)
            return final


def _is_reasoning_model(name: str) -> bool:
    """True for GPT-5 family + o-series. Used to decide whether to
    send reasoning.effort and whether to skip temperature."""
    n = (name or "").lower()
    return n.startswith(("gpt-5", "o1", "o3", "o4"))


# ── Streaming ──────────────────────────────────────────────────────


async def _stream_responses_api(
    url: str,
    headers: dict[str, str],
    payload: dict[str, Any],
    timeout: httpx.Timeout,
) -> AsyncIterator[dict[str, Any]]:
    """Open a streamed POST to /v1/responses (or Azure equivalent) and
    yield each parsed SSE event as a dict.

    The Responses API SSE protocol uses `event: <name>` followed by
    `data: <json>`. Each `data:` line is a complete event payload that
    already includes a `type` field, so we don't strictly need the
    `event:` line — but we ignore both blanks and `event:` lines.

    Yields the JSON-decoded dict for each `data:` chunk. Skips the
    terminal `data: [DONE]` if present (Responses API sends a typed
    `response.completed` event instead, but Chat Completions style
    [DONE] sometimes leaks through).
    """
    streamed_payload = dict(payload)
    streamed_payload["stream"] = True
    sse_headers = dict(headers)
    sse_headers["Accept"] = "text/event-stream"
    async with httpx.AsyncClient(timeout=timeout) as client:
        async with client.stream(
            "POST",
            url,
            headers=sse_headers,
            json=streamed_payload,
        ) as resp:
            if resp.status_code != 200:
                # Read the error body before yielding so callers see it
                # as an error event rather than a silent empty stream.
                body = await resp.aread()
                yield {
                    "type": "error",
                    "status": resp.status_code,
                    "message": body.decode("utf-8", errors="replace")[:500],
                }
                return
            async for line in resp.aiter_lines():
                if not line or not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if not data or data == "[DONE]":
                    continue
                try:
                    yield json.loads(data)
                except json.JSONDecodeError:
                    logger.warning("OpenAI SSE: malformed data line: %r", data[:200])
                    continue


class StreamingClient:
    """Optional streaming surface on LLMOpenAI.

    Kept as a separate small helper so the abstract LLMClient contract
    doesn't grow a `stream()` method that Sarvam can't honour. Callers
    feature-test via `isinstance(client, LLMOpenAI)` or the helper
    function below.
    """

    pass


async def stream_openai(
    client: "LLMOpenAI",
    messages: list[LLMMessage],
    *,
    tools: Optional[list[ToolDef]] = None,
    tool_choice: Literal["auto", "required", "none"] = "auto",
    max_output_tokens: int = 1500,
    reasoning_effort: Optional[ReasoningEffort] = None,
    temperature: float = 0.2,
    response_format: Optional[Literal["json_object"]] = None,
    prompt_cache_key: Optional[str] = None,
) -> AsyncIterator[dict[str, Any]]:
    """Stream a Responses API call.

    Yields raw SSE event dicts as they arrive. The chat service
    interprets these into user-facing deltas. Token deltas appear as
    `response.output_text.delta`; tool-call argument deltas as
    `response.function_call_arguments.delta`; the run finishes with
    `response.completed` carrying usage and final state.

    On a transport error or non-200, yields a single
    `{"type": "error", ...}` event then stops — matching the contract
    of `complete()` which never raises.
    """
    trace = CallTrace(
        kind="stream",
        messages=messages,
        tools=tools,
        model=client.model,
        reasoning_effort=reasoning_effort,
        prompt_cache_key=prompt_cache_key,
        max_output_tokens=max_output_tokens,
        provider=client.provider_name,
    )

    if not client._api_key:
        with trace as t:
            t.set_stream_result(
                text="", tool_calls=[],
                usage={"finish_reason": "error"},
            )
            yield {"type": "error", "message": "OPENAI_API_KEY is not set"}
            return

    payload: dict[str, Any] = {
        "model": client.model,
        "input": _messages_to_input(messages),
        "max_output_tokens": max_output_tokens,
    }
    if temperature is not None and not _is_reasoning_model(client.model):
        payload["temperature"] = temperature
    if reasoning_effort and _is_reasoning_model(client.model):
        effort_on_wire = client._translate_reasoning_effort(reasoning_effort)
        if effort_on_wire:
            payload["reasoning"] = {"effort": effort_on_wire}
    if tools:
        payload["tools"] = _tools_to_responses_format(tools)
        payload["tool_choice"] = tool_choice if tool_choice in {"required", "none"} else "auto"
    if response_format == "json_object":
        payload["text"] = {"format": {"type": "json_object"}}
    if prompt_cache_key:
        payload["prompt_cache_key"] = prompt_cache_key

    # Buffers used to reconstruct the full response for the trace.
    text_acc: list[str] = []
    tc_acc: dict[str, dict[str, Any]] = {}  # by item_id
    final_usage: dict[str, Any] = {}

    with trace as t:
        try:
            async for ev in _stream_responses_api(
                client.API_URL, client._auth_headers(), payload, _DEFAULT_TIMEOUT,
            ):
                etype = ev.get("type", "")
                if etype == "response.output_text.delta":
                    delta = ev.get("delta") or ""
                    if delta:
                        if not text_acc:
                            t.mark_first_delta()
                        text_acc.append(delta)
                elif etype == "response.output_item.added":
                    item = ev.get("item") or {}
                    if item.get("type") == "function_call":
                        item_id = item.get("id") or ""
                        if item_id:
                            tc_acc.setdefault(item_id, {
                                "id": item.get("call_id") or item_id,
                                "name": item.get("name") or "",
                                "args_str": item.get("arguments") or "",
                            })
                elif etype == "response.function_call_arguments.delta":
                    item_id = ev.get("item_id") or ""
                    if item_id:
                        slot = tc_acc.setdefault(item_id, {"id": item_id, "name": "", "args_str": ""})
                        slot["args_str"] += ev.get("delta", "") or ""
                elif etype == "response.output_item.done":
                    item = ev.get("item") or {}
                    if item.get("type") == "function_call":
                        item_id = item.get("id") or ""
                        if item_id:
                            slot = tc_acc.setdefault(item_id, {"id": item_id, "name": "", "args_str": ""})
                            if item.get("call_id"):
                                slot["id"] = item["call_id"]
                            if item.get("name"):
                                slot["name"] = item["name"]
                            if item.get("arguments"):
                                slot["args_str"] = item["arguments"]
                elif etype == "response.completed":
                    resp_obj = ev.get("response") or {}
                    usage = resp_obj.get("usage") or {}
                    cached_tokens = int(
                        (usage.get("input_tokens_details") or {}).get(
                            "cached_tokens", 0
                        ) or 0
                    )
                    # Explicit hand-off mirrors the non-streaming path —
                    # set_stream_result also propagates cached_tokens via
                    # the usage dict, but having the setter called at
                    # extraction keeps the wiring symmetric.
                    t.set_cached_tokens(cached_tokens)
                    final_usage = {
                        "input_tokens": int(usage.get("input_tokens", 0) or 0),
                        "output_tokens": int(usage.get("output_tokens", 0) or 0),
                        "reasoning_tokens": int(
                            (usage.get("output_tokens_details") or {}).get(
                                "reasoning_tokens", 0
                            ) or 0
                        ),
                        "cached_tokens": cached_tokens,
                        "finish_reason": resp_obj.get("status"),
                    }
                yield ev
        except httpx.HTTPError as e:
            yield {
                "type": "error",
                "message": f"transport error: {type(e).__name__}: {e}",
            }
        finally:
            # Build the parsed tool_calls list for the trace.
            parsed_tc: list[dict[str, Any]] = []
            for slot in tc_acc.values():
                args_str = slot.get("args_str") or "{}"
                try:
                    args = json.loads(args_str) if args_str else {}
                except json.JSONDecodeError:
                    args = {"_raw_arguments": args_str, "_parse_error": True}
                parsed_tc.append({
                    "id": slot.get("id", ""),
                    "name": slot.get("name", ""),
                    "arguments": args,
                })
            t.set_stream_result(
                text="".join(text_acc),
                tool_calls=parsed_tc,
                usage=final_usage,
            )
