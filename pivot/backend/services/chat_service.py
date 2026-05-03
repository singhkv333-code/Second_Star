"""ChatService — agentic-loop request handler.

The architecture (Prompt 2):

    1. **Fast path.** Greetings / thanks / help asks return canned text
       in <1 ms. No LLM. Caught by `services.fast_path.try_fast_path`.
    2. **Agentic loop.** A single message list grows turn by turn:
       [system, ...history, user] → LLM call (low reasoning) → if it
       emitted tool_calls, append assistant + run each tool, append
       results, loop. If finish_reason='stop', we have the final
       reply. Circuit-breaker at MAX_TOOL_CALLS=8.
    3. **Schema-driven completeness.** Before any tool execution,
       `services.completeness.check_completeness` walks the tool's
       JSON Schema and lists missing required fields. If anything is
       missing we generate a single clarification question via a
       minimal-reasoning LLM call and return — no executor invocation
       with garbage args.
    4. **Validation retry.** After completeness passes, the JSON-Schema
       validator catches type/enum/length violations. Failures feed
       back into the loop as a tool_result error so the model can
       self-correct on its next iteration.

Reasoning effort assignments (from Prompt 2's table):

    - chat hop (tool selection / synthesis): low
    - clarification question generation: minimal
    - narrate-tool-result on a non-loop final hop: minimal

The loop pattern subsumes the soft-failure-retry hop from Prompt 1 —
errors are now fed back as tool results and the model has the next
iteration to call ASK_USER, retry, or finish.
"""
from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any, Optional

from backend.llm import LLMClient, LLMMessage, ToolDef, get_llm_client
from backend.prompts import build_system_prompt
from backend.services.chat_trace import TurnTrace, start_turn
from backend.services.conversation_store import ConversationStore, default_store
from backend.services.fast_path import try_fast_path
from backend.services.tool_registry import get_tool_schema
from backend.services.validation_retry import (
    ASK_USER_TOOL_NAME,
    GuardedToolResult,
    ask_user_tool_def,
    execute_with_completeness,
)


logger = logging.getLogger(__name__)


_PLACEHOLDER_RE = re.compile(r"<[A-Z][A-Z0-9_]*>")
_TOOL_CALL_BLOCK_RE = re.compile(r"<TOOL_CALL>.*?(?:</TOOL_CALL>|$)", re.DOTALL | re.IGNORECASE)
_GENERIC_FALLBACK = "Sorry, I had trouble with that — could you rephrase?"
_LLM_UNAVAILABLE = (
    "The AI backend is temporarily unavailable. You can still:\n"
    "• Run a backtest directly: `backtest pe_ratio < 15 from 2020-01-01 to 2024-12-31`\n"
    "• Screen the universe: `/screen roe > 18`\n"
    "• Type a stock ticker (e.g. `RELIANCE`) for a snapshot."
)
_LATENT_GREETING_RE = re.compile(
    r"execute\s+orders\s+on\s+zerodha\.\s+build\s+capital\s+protection",
    re.IGNORECASE,
)

# Circuit breaker — caps how many tool round-trips one user turn can
# trigger. The agentic loop is allowed to call several tools in a
# row but not run away.
_MAX_TOOL_CALLS = 8


@dataclass
class ChatTurn:
    response: str
    tools_called: list[str] = field(default_factory=list)
    logiccard: dict | None = None
    latency_ms: int = 0
    sanitised: bool = False
    raw_data: dict = field(default_factory=dict)
    latency_breakdown: dict[str, int] = field(default_factory=dict)


@dataclass
class UserContext:
    user_id: int
    kite_token: str
    db: Any
    holdings: list[dict] = field(default_factory=list)


# ── ToolDef adapter ─────────────────────────────────────────────────


def _registry_tools_as_tooldefs() -> list[ToolDef]:
    """Translate `agents/tools.py` ALL_TOOLS dicts → LLMClient ToolDefs.
    Synthetic ASK_USER appended so the model has the escape hatch."""
    out: list[ToolDef] = []
    for defn in get_tool_schema():
        fn = defn.get("function") or {}
        out.append(ToolDef(
            name=fn.get("name", ""),
            description=fn.get("description", ""),
            parameters=fn.get("parameters") or {},
        ))
    out.append(ask_user_tool_def())
    return out


def _history_to_llm_messages(history: list[dict[str, str]]) -> list[LLMMessage]:
    msgs: list[LLMMessage] = []
    for h in history or []:
        role = h.get("role")
        content = h.get("content") or ""
        if role in {"user", "assistant"}:
            msgs.append(LLMMessage(role=role, content=content))
    return msgs


def _summarise_tool_result(g: GuardedToolResult) -> str:
    """Compact JSON the loop's next iteration consumes as the tool
    result. Errors get a structured prefix so the model treats them
    as a recovery hint rather than data."""
    if not g.success:
        return json.dumps({
            "error": g.error or "tool failed",
            "hint": "Decide whether to call a different tool, "
                    "call ASK_USER for clarification, or finish "
                    "with a brief explanation. Do not retry the "
                    "same call with the same arguments.",
        })
    payload: dict[str, Any] = {}
    if g.data:
        payload["data"] = g.data
    if g.logiccard:
        payload["logiccard"] = g.logiccard
    return json.dumps(payload, default=str)[:6000]


# ── ChatService ─────────────────────────────────────────────────────


class ChatService:
    def __init__(
        self,
        store: ConversationStore | None = None,
        llm_client: Optional[LLMClient] = None,
    ) -> None:
        self.store = store or default_store()
        self._llm = llm_client

    def _client(self) -> LLMClient:
        return self._llm if self._llm is not None else get_llm_client()

    async def handle(
        self,
        message: str,
        conv_id: str,
        ctx: UserContext,
        *,
        history_override: list[dict] | None = None,
    ) -> ChatTurn:
        turn_started = time.monotonic()
        breakdown: dict[str, int] = {}
        trace = start_turn(conv_id, message)
        trace.event("turn.start", message_preview=message[:120])

        # ── Fast path ──────────────────────────────────────────────
        fast_response = try_fast_path(message)
        if fast_response is not None:
            trace.event("fast_path.matched")
            self.store.append(conv_id, message, fast_response)
            total = int((time.monotonic() - turn_started) * 1000)
            breakdown["fast_path"] = total
            breakdown["total"] = total
            _log_timing("fast_path", message, total, breakdown, tools=[])
            trace.event("turn.end", total_ms=total, tools_called=[])
            trace.end()
            return ChatTurn(
                response=fast_response,
                latency_ms=total,
                latency_breakdown=breakdown,
            )

        # ── Agentic loop setup ─────────────────────────────────────
        history = (
            history_override
            if history_override is not None
            else self.store.get_history(conv_id, limit=20)
        )

        client = self._client()
        tooldefs = _registry_tools_as_tooldefs()

        messages: list[LLMMessage] = [
            LLMMessage(role="system", content=build_system_prompt(role="chat")),
            *_history_to_llm_messages(history),
            LLMMessage(role="user", content=message),
        ]

        tools_called: list[str] = []
        logiccard: Optional[dict] = None
        raw_data: dict = {}
        hop_index = 0

        # ── Loop ───────────────────────────────────────────────────
        while hop_index < _MAX_TOOL_CALLS:
            hop_index += 1
            trace.event("llm.call", hop=hop_index, reasoning_effort="low",
                        tools_offered=len(tooldefs))
            try:
                response = await client.complete(
                    messages=messages,
                    tools=tooldefs,
                    tool_choice="auto",
                    max_output_tokens=1500,
                    reasoning_effort="low",
                    temperature=0.2,
                )
            except Exception as e:
                logger.warning(
                    "%s call failed at hop %d (%s); falling back",
                    client.provider_name, hop_index, type(e).__name__,
                )
                trace.event("llm.exception", hop=hop_index,
                            type=type(e).__name__)
                break
            breakdown[f"llm_hop_{hop_index}"] = response.latency_ms
            trace.event("llm.response", hop=hop_index,
                        finish_reason=response.finish_reason,
                        latency_ms=response.latency_ms,
                        input_tokens=response.input_tokens,
                        output_tokens=response.output_tokens,
                        reasoning_tokens=response.reasoning_tokens)

            if response.finish_reason == "error":
                logger.warning("LLM error finish at hop %d: %s",
                               hop_index, response.content)
                trace.event("turn.end", reason="llm_error")
                trace.end()
                return self._unavailable(turn_started, breakdown)

            if response.finish_reason != "tool_calls":
                # Final text — return it.
                text, sanitised = _post_process(response.content or "")
                if sanitised and text == _GENERIC_FALLBACK and tools_called:
                    text = _tool_summary_line(tools_called[-1], logiccard)
                    sanitised = False
                self.store.append(conv_id, message, text)
                total = int((time.monotonic() - turn_started) * 1000)
                breakdown["total"] = total
                _log_timing(client.provider_name, message, total, breakdown,
                            tools=tools_called)
                trace.event("turn.end", total_ms=total,
                            tools_called=tools_called, reason="stop")
                trace.end()
                return ChatTurn(
                    response=text,
                    tools_called=tools_called,
                    logiccard=logiccard,
                    latency_ms=total,
                    sanitised=sanitised,
                    raw_data=raw_data,
                    latency_breakdown=breakdown,
                )

            # finish_reason == "tool_calls" — append assistant message
            # carrying the tool_calls, then run each.
            messages.append(LLMMessage(
                role="assistant",
                content=response.content or "",
                tool_calls=response.tool_calls,
            ))

            for tc in response.tool_calls or []:
                trace.event("tool.invoke", tool=tc.get("name"),
                            args=tc.get("arguments"))
                guarded = await execute_with_completeness(
                    tc["name"],
                    tc.get("arguments") or {},
                    llm_client=client,
                    user_message=message,
                    kite_token=ctx.kite_token,
                    db=ctx.db,
                    user_id=ctx.user_id,
                )
                breakdown[f"tool_{guarded.name}"] = (
                    breakdown.get(f"tool_{guarded.name}", 0) + guarded.latency_ms
                )
                trace.event("tool.result", tool=guarded.name,
                            success=guarded.success,
                            needs_clarification=guarded.needs_clarification,
                            error=guarded.error,
                            latency_ms=guarded.latency_ms)

                # Completeness or ASK_USER → surface immediately.
                if guarded.needs_clarification and guarded.question:
                    self.store.append(conv_id, message, guarded.question)
                    total = int((time.monotonic() - turn_started) * 1000)
                    breakdown["total"] = total
                    _log_timing(client.provider_name, message, total, breakdown,
                                tools=[guarded.name])
                    trace.event("turn.end", total_ms=total,
                                tools_called=[guarded.name],
                                reason="needs_clarification")
                    trace.end()
                    return ChatTurn(
                        response=guarded.question,
                        tools_called=[guarded.name],
                        raw_data={"_render_hint": "ask_user"},
                        latency_ms=total,
                        latency_breakdown=breakdown,
                    )

                # Append the tool's result to the conversation. Even
                # errors go through this path — the model sees the
                # error in the next iteration and decides what to do.
                tool_msg_content = _summarise_tool_result(guarded)
                messages.append(LLMMessage(
                    role="tool",
                    tool_call_id=tc.get("id", f"call_{hop_index}"),
                    name=guarded.name,
                    content=tool_msg_content,
                ))

                if guarded.success:
                    if guarded.name not in tools_called:
                        tools_called.append(guarded.name)
                    if guarded.logiccard:
                        logiccard = guarded.logiccard
                    if guarded.data:
                        raw_data[guarded.name] = guarded.data

            # back to top of loop — model now sees tool results

        # Circuit-breaker hit.
        logger.warning("agentic loop hit MAX_TOOL_CALLS=%d", _MAX_TOOL_CALLS)
        total = int((time.monotonic() - turn_started) * 1000)
        breakdown["total"] = total
        msg = (
            "I needed to look up several things and got a bit lost. "
            "Could you ask again with more specifics?"
        )
        self.store.append(conv_id, message, msg)
        _log_timing(client.provider_name, message, total, breakdown,
                    tools=tools_called, note="hit_max_tool_calls")
        trace.event("turn.end", total_ms=total, tools_called=tools_called,
                    reason="circuit_breaker")
        trace.end()
        return ChatTurn(
            response=msg,
            tools_called=tools_called,
            raw_data={"_render_hint": "circuit_breaker"},
            latency_ms=total,
            latency_breakdown=breakdown,
        )

    def _unavailable(
        self, turn_started: float, breakdown: dict[str, int],
    ) -> ChatTurn:
        total = int((time.monotonic() - turn_started) * 1000)
        breakdown["total"] = total
        return ChatTurn(
            response=_LLM_UNAVAILABLE,
            raw_data={"_llm_unavailable": True},
            sanitised=False,
            latency_ms=total,
            latency_breakdown=breakdown,
        )


# ── Helpers ────────────────────────────────────────────────────────


def _log_timing(
    provider: str,
    message: str,
    total_ms: int,
    breakdown: dict[str, int],
    *,
    tools: list[str] | None = None,
    note: str | None = None,
) -> None:
    """Emit a structured per-turn latency log line."""
    parts = [f"{k}={v}" for k, v in sorted(breakdown.items()) if k != "total"]
    tool_str = f"tools={tools}" if tools else "tools=[]"
    note_str = f" note={note!r}" if note else ""
    msg_preview = message.strip().replace("\n", " ")[:80]
    logger.info(
        "chat turn %dms [%s] %s %s (msg=%r)%s",
        total_ms, provider, tool_str, " ".join(parts), msg_preview, note_str,
    )


def _tool_summary_line(tool_name: str, logiccard: dict | None) -> str:
    """One-liner used when the post-processor stripped the LLM's
    narration but a tool actually produced a card."""
    if tool_name == "propose_workflow":
        return "Here's a draft of that workflow — review the steps below."
    if logiccard:
        action = logiccard.get("action") or ""
        symbol = logiccard.get("symbol") or ""
        if action and symbol:
            return f"Proposed: {action} {symbol}. Review the card below to confirm."
        return "Here's the action I prepared — review and confirm below."
    if tool_name.startswith("get_") or tool_name.startswith("list_"):
        return "Here's what I found."
    return f"Done — `{tool_name}` ran. See result below."


def _post_process(text: str) -> tuple[str, bool]:
    """Defence-in-depth: strip leaked tool-call blocks / placeholders.
    Returns (cleaned, was_sanitised)."""
    if not text:
        return _GENERIC_FALLBACK, True
    original = text
    text = _TOOL_CALL_BLOCK_RE.sub("", text)
    text = _PLACEHOLDER_RE.sub("", text)
    if _LATENT_GREETING_RE.search(text):
        text = _GENERIC_FALLBACK
    text = text.strip()
    if not text:
        text = _GENERIC_FALLBACK
    return text, text != original
