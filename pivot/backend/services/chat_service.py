"""ChatService — the canonical request handler.

Flow:
    1. Load conversation history (Redis, last 20 turns).
    2. Single LLM call with the full tool schema + role-aware system
       prompt (assembler injects the domain primer).
    3. If the model emitted a tool call, run it through
       `execute_tool_with_retry` — args validated against the tool's
       schema, with a one-shot fix-it hop on failure. The synthetic
       ASK_USER tool lets the model escalate ambiguity to the user.
    4. Second LLM call with the tool result so the model writes the
       user-facing reply (the "narrate_tool_result" role prompt).
    5. Post-process to strip placeholders, salvage stripped responses
       when a tool actually ran.
    6. Return a stable response shape including LogicCard / structured
       data so the frontend keeps rendering inline cards.

LLM provider is decided by `LLM_PROVIDER` env (openai by default).
The legacy `call_sarvam` direct path is gone; everything routes
through `get_llm_client()`.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Optional

from backend.llm import LLMClient, LLMMessage, ToolDef, get_llm_client
from backend.prompts import build_system_prompt
from backend.services.conversation_store import ConversationStore, default_store
from backend.services.tool_registry import get_tool_schema
from backend.services.validation_retry import (
    GuardedToolResult,
    ASK_USER_TOOL_NAME,
    ask_user_tool_def,
    execute_tool_with_retry,
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


@dataclass
class ChatTurn:
    response: str
    tools_called: list[str] = field(default_factory=list)
    logiccard: dict | None = None
    latency_ms: int = 0
    sanitised: bool = False
    raw_data: dict = field(default_factory=dict)


@dataclass
class UserContext:
    user_id: int
    kite_token: str
    db: Any
    holdings: list[dict] = field(default_factory=list)


# ── ToolDef adapter ─────────────────────────────────────────────────


def _registry_tools_as_tooldefs() -> list[ToolDef]:
    """Translate the legacy ALL_TOOLS dict shape from agents/tools.py
    into the LLMClient's ToolDef list. The synthetic ASK_USER tool
    is appended so the model has the escape hatch for ambiguity."""
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


# ── Conversion helpers between dict-shaped history and LLMMessage ──


def _history_to_llm_messages(
    history: list[dict[str, str]],
    user_message: str,
) -> list[LLMMessage]:
    msgs: list[LLMMessage] = []
    for h in history or []:
        role = h.get("role")
        content = h.get("content") or ""
        if role in {"user", "assistant"}:
            msgs.append(LLMMessage(role=role, content=content))
    msgs.append(LLMMessage(role="user", content=user_message))
    return msgs


# ── ChatService ─────────────────────────────────────────────────────


class ChatService:
    def __init__(
        self,
        store: ConversationStore | None = None,
        llm_client: Optional[LLMClient] = None,
    ) -> None:
        self.store = store or default_store()
        self._llm = llm_client    # None → resolved per-call via factory

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
        history = (
            history_override
            if history_override is not None
            else self.store.get_history(conv_id, limit=20)
        )

        client = self._client()
        tooldefs = _registry_tools_as_tooldefs()

        # Build the conversation: [system] + history + new user msg.
        system_msg = LLMMessage(
            role="system",
            content=build_system_prompt(role="chat"),
        )
        chat_messages = [system_msg, *_history_to_llm_messages(history, message)]

        try:
            first = await client.complete(
                messages=chat_messages,
                tools=tooldefs,
                tool_choice="auto",
                max_output_tokens=900,
                reasoning_effort="medium",
                temperature=0.2,
            )
        except Exception as e:
            logger.warning(
                "%s call failed (%s); returning graceful fallback",
                client.provider_name, type(e).__name__,
            )
            return ChatTurn(
                response=_LLM_UNAVAILABLE,
                raw_data={"_llm_unavailable": True},
                sanitised=False,
            )

        if first.finish_reason == "error":
            logger.warning("LLM error finish: %s", first.content)
            return ChatTurn(
                response=_LLM_UNAVAILABLE,
                raw_data={"_llm_unavailable": True},
                sanitised=False,
                latency_ms=first.latency_ms,
            )

        tools_called: list[str] = []
        logiccard: Optional[dict] = None
        raw_data: dict = {}
        latency_ms = first.latency_ms

        if first.tool_calls:
            tc = first.tool_calls[0]
            guarded = await execute_tool_with_retry(
                tc["name"],
                tc.get("arguments") or {},
                llm_client=client,
                conversation_messages=chat_messages,
                tools_for_retry=tooldefs,
                kite_token=ctx.kite_token,
                db=ctx.db,
                user_id=ctx.user_id,
            )

            # ASK_USER → surface the model's clarifying question as the
            # assistant reply. No second LLM hop, no tool data.
            if guarded.needs_clarification and guarded.question:
                self.store.append(conv_id, message, guarded.question)
                return ChatTurn(
                    response=guarded.question,
                    tools_called=[ASK_USER_TOOL_NAME],
                    raw_data={"_render_hint": "ask_user"},
                    latency_ms=latency_ms,
                )

            # Validation failed twice — surface the error inline so the
            # user knows the system needed something it didn't get.
            if guarded.error and not guarded.success:
                msg = (
                    f"I couldn't complete that — {guarded.error}. "
                    "Could you rephrase with the specific values you want?"
                )
                self.store.append(conv_id, message, msg)
                return ChatTurn(
                    response=msg,
                    tools_called=[guarded.name],
                    raw_data={"_render_hint": "validation_error"},
                    latency_ms=latency_ms,
                )

            # Successful tool execution.
            tools_called.append(guarded.name)
            logiccard = guarded.logiccard
            raw_data[guarded.name] = guarded.data or {}

            # Second hop — narrate the result.
            tool_result_msg = LLMMessage(
                role="tool",
                tool_call_id=tc.get("id", "fix_attempt"),
                name=guarded.name,
                content=_tool_payload_to_llm_string(guarded),
            )
            second_messages = [
                LLMMessage(
                    role="system",
                    content=build_system_prompt(role="narrate_tool_result"),
                ),
                *_history_to_llm_messages(history, message),
                LLMMessage(
                    role="assistant",
                    tool_calls=[{
                        "id": tc.get("id", "fix_attempt"),
                        "name": guarded.name,
                        "arguments": guarded.args,
                    }],
                ),
                tool_result_msg,
            ]

            try:
                second = await client.complete(
                    messages=second_messages,
                    tools=None,
                    tool_choice="none",
                    max_output_tokens=600,
                    reasoning_effort="low",
                    temperature=0.2,
                )
                text = (second.content or "").strip()
                latency_ms += second.latency_ms
            except Exception as e:
                logger.warning(
                    "%s second-hop failed (%s); using tool result directly",
                    client.provider_name, type(e).__name__,
                )
                text = _tool_payload_to_llm_string(guarded)
        else:
            text = (first.content or "").strip()

        text, sanitised = _post_process(text)

        # Salvage path: if the post-processor stripped to the generic
        # fallback BUT a tool actually ran, replace with a one-liner
        # pointing at the card the FE will render.
        if sanitised and text == _GENERIC_FALLBACK and tools_called:
            text = _tool_summary_line(tools_called[0], logiccard)
            sanitised = False

        self.store.append(conv_id, message, text)

        return ChatTurn(
            response=text,
            tools_called=tools_called,
            logiccard=logiccard,
            latency_ms=latency_ms,
            sanitised=sanitised,
            raw_data=raw_data,
        )


# ---- Helpers ──────────────────────────────────────────────────────


def _tool_payload_to_llm_string(g: GuardedToolResult) -> str:
    """Compact JSON the second-hop LLM consumes as the tool result."""
    if not g.success:
        return json.dumps({"error": g.error or "tool failed"})
    payload: dict[str, Any] = {}
    if g.data:
        payload["data"] = g.data
    if g.logiccard:
        payload["logiccard"] = g.logiccard
    return json.dumps(payload, default=str)[:6000]


def _tool_summary_line(tool_name: str, logiccard: dict | None) -> str:
    """One-line description of what the tool did. Used when the LLM's
    narration was lost to truncation."""
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
    """Defence in depth — strip leaked tool-call blocks / placeholders.
    Returns (cleaned_text, was_sanitised)."""
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
