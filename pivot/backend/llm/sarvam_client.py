"""Sarvam-m client — fallback / cheap-test backend.

Sarvam-m's chat-completions endpoint REJECTS OpenAI-style `tools` /
`tool_choice` payloads with HTTP 400. We emulate function calling by:
  1. Stuffing tool descriptions + JSON schemas into the system prompt
     as text, with instructions to emit a `<TOOL_CALL>{...}</TOOL_CALL>`
     block when calling.
  2. Parsing that block out of the raw response with a regex.

The 7192-token context window is tight; we trim oldest messages
aggressively when the request payload exceeds 25K chars (~6.2K tokens).

Originally lived at `backend/agents/sarvam_client.py`. That path is
kept as a thin shim re-exporting from here so existing imports don't
break (chat_service used `call_sarvam` directly).
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from typing import Any, Literal, Optional

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


SARVAM_API_URL = "https://api.sarvam.ai/v1/chat/completions"
SARVAM_DEFAULT_MODEL = "sarvam-m"
MAX_RETRIES = 3
TIMEOUT_SECONDS = 30
_CTX_CHAR_BUDGET = 25000


_THINK_BLOCK_RE = re.compile(r"<think>.*?</think>\s*", re.DOTALL | re.IGNORECASE)
_TRUNCATED_THINK_RE = re.compile(r"<think>.*", re.DOTALL | re.IGNORECASE)
_TOOL_CALL_RE = re.compile(
    r"<TOOL_CALL>\s*(\{.*?\})\s*</TOOL_CALL>", re.DOTALL | re.IGNORECASE,
)


def _strip_truncated_think(text: str) -> str:
    """Strip an unterminated <think>...EOF block."""
    if not text or "</think>" in text.lower():
        return text
    return _TRUNCATED_THINK_RE.sub("", text).strip()


def _strip_think_blocks(text: str) -> str:
    if not text:
        return text
    cleaned = _THINK_BLOCK_RE.sub("", text).strip()
    return _strip_truncated_think(cleaned)


def _is_truncated(raw: str, cleaned: str) -> bool:
    if not cleaned:
        return True
    low_clean = cleaned.lower()
    if "<logiccard" in low_clean and "</logiccard>" not in low_clean:
        return True
    low_raw = (raw or "").lower()
    if "<think>" in low_raw and "</think>" not in low_raw:
        return True
    return False


def _build_tool_instruction(tools: list[ToolDef]) -> str:
    """Inject tool defs into the system prompt as text — Sarvam's
    chat-completions endpoint rejects native `tools` payloads.

    Per-property descriptions in the JSON Schema MATTER for Sarvam-m
    tool selection. Stripping them broke 4/5 canonical prompts during
    the token-opt eval (2026-05-03, reverted). Don't try it again.
    """
    lines = [
        "You can call ONE of the tools below if — and only if — the user is asking "
        "for an action that matches a tool. If no tool fits, reply normally.",
        "",
        "Tools (JSON Schema):",
    ]
    for t in tools:
        try:
            schema = json.dumps(t.parameters, separators=(",", ":"))
        except Exception:
            schema = "{}"
        lines.append(f"- {t.name}: {t.description}")
        lines.append(f"  parameters: {schema}")
    lines.extend([
        "",
        "If you decide to call a tool, end your reply with EXACTLY one block:",
        "<TOOL_CALL>{\"name\":\"<tool_name>\",\"arguments\":{...}}</TOOL_CALL>",
        "Use double-quoted JSON. Do not wrap it in markdown fences. Do not invent "
        "tool names. Omit the block entirely when no tool is appropriate.",
    ])
    return "\n".join(lines)


def _extract_emulated_tool_call(raw: str) -> tuple[str, Optional[dict[str, Any]]]:
    """Pull a <TOOL_CALL>{...}</TOOL_CALL> block. Returns (clean_text, parsed|None)."""
    if not raw:
        return raw, None
    match = _TOOL_CALL_RE.search(raw)
    if not match:
        return raw, None
    body = match.group(1)
    try:
        parsed = json.loads(body)
    except json.JSONDecodeError:
        return raw, None
    name = parsed.get("name")
    args = parsed.get("arguments", {})
    if not name or not isinstance(args, dict):
        return raw, None
    cleaned_text = _TOOL_CALL_RE.sub("", raw).strip()
    return cleaned_text, {"name": name, "arguments": args}


def _messages_to_payload(
    messages: list[LLMMessage],
    effective_system: str,
) -> list[dict[str, str]]:
    """Sarvam expects classic chat-completions shape with role+content.
    Tool messages get folded into the user transcript with a marker."""
    out: list[dict[str, str]] = []
    if effective_system:
        out.append({"role": "system", "content": effective_system})
    for m in messages:
        role = m.role
        content = m.content or ""
        if role == "tool":
            # Sarvam doesn't natively understand tool messages; re-frame
            # as a user-role transcript so the assistant has the result.
            tn = m.name or "unknown"
            out.append({
                "role": "user",
                "content": f"[Tool result for `{tn}`] {content}",
            })
        elif role == "assistant" and m.tool_calls:
            # Synthesize an "assistant decided to call X" line so the
            # next turn has continuity.
            tn = m.tool_calls[0].get("name", "unknown")
            out.append({"role": "assistant", "content": f"Calling tool `{tn}`…"})
            if content:
                out.append({"role": "assistant", "content": content})
        else:
            out.append({"role": role, "content": content})

    # Trim oldest user/assistant turns when over budget (keep system).
    while len(json.dumps(out)) > _CTX_CHAR_BUDGET and len(out) > 2:
        start_idx = 1 if effective_system else 0
        if len(out) > start_idx + 2:
            out.pop(start_idx)
            out.pop(start_idx)
        else:
            break
    return out


class LLMSarvam(LLMClient):
    provider_name = "sarvam"

    def __init__(self, model: Optional[str] = None, api_key: Optional[str] = None) -> None:
        self.model = model or settings.llm_model or SARVAM_DEFAULT_MODEL
        self._api_key = api_key or settings.sarvam_api_key
        self._mock_mode = not bool(self._api_key)

    async def complete(
        self,
        messages: list[LLMMessage],
        *,
        tools: Optional[list[ToolDef]] = None,
        tool_choice: Literal["auto", "required", "none"] = "auto",
        max_output_tokens: int = 900,
        reasoning_effort: Optional[ReasoningEffort] = None,
        temperature: float = 0.2,
        response_format: Optional[Literal["json_object"]] = None,
        prompt_cache_key: Optional[str] = None,  # noqa: ARG002 — Sarvam has no prompt cache
    ) -> LLMResponse:
        # CallTrace wraps the whole call: it owns the cost-ledger hand-off
        # in __exit__. We MUST call ``t.set_response(...)`` before each
        # return path so the ledger sees the token counts; the try/finally
        # below is the belt-and-brace for unexpected exception escapes.
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

        final_response: Optional[LLMResponse] = None
        with trace as t:
            try:
                if self._mock_mode:
                    final_response = self._mock_response(messages)
                    t.set_response(final_response)
                    return final_response

                # Find or split the system message; we re-prepend our own
                # tool-instruction text after it.
                system_text = ""
                body_msgs: list[LLMMessage] = []
                for m in messages:
                    if m.role == "system" and not system_text:
                        system_text = m.content
                    else:
                        body_msgs.append(m)
                if tools:
                    instruction = _build_tool_instruction(tools)
                    system_text = f"{system_text}\n\n{instruction}" if system_text else instruction
                    if max_output_tokens < 600:
                        max_output_tokens = 600

                payload_messages = _messages_to_payload(body_msgs, system_text)

                payload: dict[str, Any] = {
                    "model": self.model,
                    "messages": payload_messages,
                    "temperature": temperature,
                    "max_tokens": max_output_tokens,
                }
                if response_format == "json_object":
                    payload["response_format"] = {"type": "json_object"}

                truncation_retried = False
                started = time.monotonic()

                for attempt in range(MAX_RETRIES):
                    try:
                        async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as client:
                            response = await client.post(
                                SARVAM_API_URL,
                                headers={
                                    "Authorization": f"Bearer {self._api_key}",
                                    "Content-Type": "application/json",
                                },
                                json=payload,
                            )
                            response.raise_for_status()
                            data = response.json()
                            choice = data["choices"][0]["message"]
                            raw = choice.get("content") or ""
                            cleaned = _strip_think_blocks(raw)

                            tool_call: Optional[dict[str, Any]] = None
                            if tools:
                                cleaned, tool_call = _extract_emulated_tool_call(cleaned)

                            if (not tool_call and not truncation_retried
                                    and _is_truncated(raw, cleaned)):
                                truncation_retried = True
                                payload["max_tokens"] = min(payload["max_tokens"] * 3, 2048)
                                logger.warning(
                                    "Sarvam truncated; retrying with max_tokens=%d",
                                    payload["max_tokens"],
                                )
                                continue

                            latency_ms = int((time.monotonic() - started) * 1000)
                            usage = data.get("usage") or {}
                            final_response = LLMResponse(
                                content=cleaned or None,
                                tool_calls=(
                                    [{
                                        "id": "sarvam_emulated_0",
                                        "name": tool_call["name"],
                                        "arguments": tool_call["arguments"],
                                    }] if tool_call else None
                                ),
                                finish_reason="tool_calls" if tool_call else "stop",
                                input_tokens=int(usage.get("prompt_tokens", 0) or 0),
                                output_tokens=int(usage.get("completion_tokens", 0) or 0),
                                latency_ms=latency_ms,
                                model=data.get("model", self.model),
                                raw=data,
                            )
                            t.set_response(final_response)
                            return final_response
                    except httpx.HTTPStatusError as e:
                        if e.response.status_code == 429 and attempt < MAX_RETRIES - 1:
                            await asyncio.sleep(2 ** attempt)
                            continue
                        if (
                            e.response.status_code == 422
                            and attempt < MAX_RETRIES - 1
                            and "context window" in e.response.text.lower()
                        ):
                            payload["max_tokens"] = max(300, payload["max_tokens"] // 2)
                            logger.warning(
                                "Sarvam 422 context overflow; retrying with max_tokens=%d",
                                payload["max_tokens"],
                            )
                            continue
                        logger.error("Sarvam %s: %s", e.response.status_code, e.response.text[:300])
                        final_response = LLMResponse(
                            content=f"Sarvam {e.response.status_code}: {e.response.text[:200]}",
                            finish_reason="error",
                            model=self.model,
                            raw={"http_status": e.response.status_code, "body": e.response.text[:500]},
                        )
                        t.set_response(final_response)
                        return final_response
                    except Exception as e:
                        if attempt < MAX_RETRIES - 1:
                            await asyncio.sleep(1)
                            continue
                        logger.error("Sarvam failed after %d attempts: %s", MAX_RETRIES, e)
                        final_response = LLMResponse(
                            content=f"Sarvam transport error: {type(e).__name__}: {e}",
                            finish_reason="error",
                            model=self.model,
                            raw={"transport_error": str(e)},
                        )
                        t.set_response(final_response)
                        return final_response

                final_response = LLMResponse(
                    content="Sarvam exhausted retries",
                    finish_reason="error",
                    model=self.model,
                )
                t.set_response(final_response)
                return final_response
            finally:
                # If an unexpected exception escapes the loop entirely
                # (e.g. an asyncio.CancelledError), make sure the trace
                # still sees whatever partial response we constructed so
                # the cost ledger row reflects what happened.
                if final_response is not None:
                    t.set_response(final_response)

    def _mock_response(self, messages: list[LLMMessage]) -> LLMResponse:
        last = next((m.content for m in reversed(messages) if m.role == "user"), "")
        low = (last or "").lower()
        if "safegrow" in low or "capital guarantee" in low or "protect" in low:
            return LLMResponse(
                content=json.dumps({"strategy_type": "SafeGrow", "explanation": "mock"}),
                model=self.model,
            )
        return LLMResponse(
            content="I understand your query. Based on your portfolio and goals, "
                    "let me help you think through this carefully.",
            model=self.model,
        )
