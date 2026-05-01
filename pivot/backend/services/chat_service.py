"""ChatService — the canonical request handler.

Replaces the ad-hoc routing in ``backend/routers/chat.py`` (intent classifier,
chart short-circuit, backtest interception). The flow is:

    1. Load conversation history from Redis.
    2. Single LLM call with the *full* tool schema and system prompt v2.
    3. If the model emitted a tool call, execute it, then make a second LLM
       call with the tool result as a synthetic user-role message and let the
       model write the final reply.
    4. Post-process to strip any leaked placeholders.
    5. Persist the turn to Redis (plain text only, no tool payloads).
    6. Return a stable response shape including any LogicCard / structured
       data so the frontend keeps rendering inline cards.

No intent classifier. No subset routing. No regex shortcuts for charts or
backtests — those are now tools (``get_price_history`` / ``run_backtest``)
the model selects on the merits.

The two slash-commands ``/screen`` and ``/expr-backtest`` are kept as
*explicit* user shortcuts (they're typed by the user, not inferred by us)
and short-circuit before the LLM is called.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

from backend.agents.sarvam_client import call_sarvam
from backend.prompts import system_prompt
from backend.services.conversation_store import ConversationStore, default_store
from backend.services.tool_registry import ToolResult, execute, get_tool_schema


logger = logging.getLogger(__name__)


_PLACEHOLDER_RE = re.compile(r"<[A-Z][A-Z0-9_]*>")
_TOOL_CALL_BLOCK_RE = re.compile(r"<TOOL_CALL>.*?(?:</TOOL_CALL>|$)", re.DOTALL | re.IGNORECASE)
_GENERIC_FALLBACK = "Sorry, I had trouble with that — could you rephrase?"
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
    sanitised: bool = False                 # True if post-processor stripped anything
    raw_data: dict = field(default_factory=dict)


@dataclass
class UserContext:
    user_id: int
    kite_token: str
    db: Any                                 # SQLAlchemy session
    holdings: list[dict] = field(default_factory=list)


class ChatService:
    def __init__(self, store: ConversationStore | None = None) -> None:
        self.store = store or default_store()

    async def handle(
        self,
        message: str,
        conv_id: str,
        ctx: UserContext,
        *,
        history_override: list[dict] | None = None,
    ) -> ChatTurn:
        """Run a single chat turn end-to-end.

        ``history_override`` lets the legacy router keep passing the client-
        carried message list during the cut-over; once Redis-backed
        conversations are fully wired in, this argument goes away.
        """
        history = history_override if history_override is not None else self.store.get_history(conv_id, limit=10)

        tools = get_tool_schema()
        first = await call_sarvam(
            messages=[*history, {"role": "user", "content": message}],
            system_prompt=system_prompt(),
            temperature=0.2,
            max_tokens=900,
            tools=tools,
            tool_choice="auto",
        )

        tool_call = first.get("tool_call") if isinstance(first, dict) else None
        tools_called: list[str] = []
        logiccard = None
        raw_data: dict = {}
        latency_ms = int(first.get("latency_ms") or 0)

        if tool_call:
            name = tool_call.get("name", "")
            args = tool_call.get("arguments") or {}
            tool_result = await execute(name, args,
                                        kite_token=ctx.kite_token,
                                        db=ctx.db, user_id=ctx.user_id)
            tools_called.append(name)
            logiccard = tool_result.logiccard
            raw_data[name] = tool_result.data

            # Second hop — the model now sees the tool result and writes the reply.
            tool_msg = {
                "role": "user",
                "content": f"[Tool result for `{name}`] {tool_result.to_llm_string()}",
            }
            second = await call_sarvam(
                messages=[*history,
                          {"role": "user", "content": message},
                          {"role": "assistant", "content": "Calling tool…"},
                          tool_msg],
                system_prompt=system_prompt(),
                temperature=0.2,
                max_tokens=600,
                tools=None,                  # we already ran the tool; no second tool call
                tool_choice=None,
            )
            text = (second.get("content") or "").strip()
            latency_ms += int(second.get("latency_ms") or 0)
        else:
            text = (first.get("content") or "").strip()

        text, sanitised = _post_process(text)

        # Persist plain text only — never the tool-call payload.
        self.store.append(conv_id, message, text)

        return ChatTurn(
            response=text,
            tools_called=tools_called,
            logiccard=logiccard,
            latency_ms=latency_ms,
            sanitised=sanitised,
            raw_data=raw_data,
        )


# ---- Post-processing safety net ---------------------------------------


def _post_process(text: str) -> tuple[str, bool]:
    """Defence in depth.

    If the upstream fixes are doing their job, this strips nothing. We track
    the boolean so the caller can log a counter.
    """
    if not text:
        return _GENERIC_FALLBACK, True

    original = text

    # Strip leaked TOOL_CALL blocks (closed or unclosed).
    text = _TOOL_CALL_BLOCK_RE.sub("", text)

    # Strip leaked uppercase placeholders like <LTP>, <STRIKE>.
    text = _PLACEHOLDER_RE.sub("", text)

    # If the entire reply is just the legacy 4-line greeting, we surface a
    # gentler reply rather than that pitch — the system prompt no longer
    # mandates it, but legacy Sarvam fine-tuning may still emit it.
    if _LATENT_GREETING_RE.search(text):
        text = _GENERIC_FALLBACK

    text = text.strip()
    if not text:
        text = _GENERIC_FALLBACK

    return text, text != original
