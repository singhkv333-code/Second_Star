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

import httpx

from backend.agents.sarvam_client import call_sarvam
from backend.prompts import system_prompt
from backend.services.conversation_store import ConversationStore, default_store
from backend.services.tool_registry import ToolResult, execute, get_tool_schema


logger = logging.getLogger(__name__)


_PLACEHOLDER_RE = re.compile(r"<[A-Z][A-Z0-9_]*>")
_TOOL_CALL_BLOCK_RE = re.compile(r"<TOOL_CALL>.*?(?:</TOOL_CALL>|$)", re.DOTALL | re.IGNORECASE)
_GENERIC_FALLBACK = "Sorry, I had trouble with that — could you rephrase?"
# Friendlier message when the LLM provider is down/422'ing.
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
        # 20 turns matches CONV_MAX_TURNS in conversation_store; the older
        # 10-turn limit was the proximate cause of "the bot forgot what I
        # said earlier" — Redis kept the data, we just weren't fetching it.
        history = history_override if history_override is not None else self.store.get_history(conv_id, limit=20)

        tools = get_tool_schema()
        try:
            first = await call_sarvam(
                messages=[*history, {"role": "user", "content": message}],
                system_prompt=system_prompt(),
                temperature=0.2,
                # Sarvam-m's total context is 7192 tokens. System prompt
                # + 39 tool schemas already eats ~5200, leaving ~2000.
                # Setting max_tokens above ~900 makes `prompt_tokens +
                # max_tokens` overflow and Sarvam returns 422. (Earlier
                # bumping to 2000 broke chat outright — confirmed via
                # error: "5211 + 2000 = 7211 exceeds 7192".)
                max_tokens=900,
                tools=tools,
                tool_choice="auto",
            )
        except (httpx.HTTPStatusError, httpx.RequestError, Exception) as e:
            # The LLM provider is down or rejecting requests (most commonly a
            # 422 from Sarvam on a malformed payload, or a 401 with a bad key).
            # Returning 500 here would render in the FE as a confusing
            # "Failed to fetch" — instead, surface the pre-canned shortcut
            # menu so the user can still run a backtest / screen / quote.
            logger.warning(
                "Sarvam call failed (%s); returning graceful fallback",
                type(e).__name__,
            )
            return ChatTurn(
                response=_LLM_UNAVAILABLE,
                tools_called=[],
                logiccard=None,
                latency_ms=0,
                sanitised=False,
                raw_data={"_llm_unavailable": True},
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
            try:
                second = await call_sarvam(
                    messages=[*history,
                              {"role": "user", "content": message},
                              {"role": "assistant", "content": "Calling tool…"},
                              tool_msg],
                    system_prompt=system_prompt(),
                    temperature=0.2,
                    # Second hop has no tool schema in the system prompt
                    # so the prompt budget is much smaller; we can give
                    # the reply a bit more headroom than the first hop.
                    max_tokens=600,
                    tools=None,                  # we already ran the tool; no second tool call
                    tool_choice=None,
                )
                text = (second.get("content") or "").strip()
                latency_ms += int(second.get("latency_ms") or 0)
            except Exception as e:
                # Tool already executed; just summarise the tool result
                # without an LLM-narrated reply.
                logger.warning(
                    "Sarvam second-hop failed (%s); using tool result directly",
                    type(e).__name__,
                )
                text = tool_result.to_llm_string()
        else:
            text = (first.get("content") or "").strip()

        text, sanitised = _post_process(text)

        # If post-processing left us with the generic fallback BUT a tool
        # actually executed (and therefore produced a card the FE will
        # render), replace the text with a brief one-liner that points
        # at the card. The fallback "Sorry, I had trouble — could you
        # rephrase?" is misleading when, e.g., propose_workflow
        # successfully built a draft and we just lost the LLM's
        # narration to truncation.
        if sanitised and text == _GENERIC_FALLBACK and tools_called:
            text = _tool_summary_line(tools_called[0], logiccard)
            sanitised = False

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


# ---- Salvage helpers ---------------------------------------------------


def _tool_summary_line(tool_name: str, logiccard: dict | None) -> str:
    """One-line description of what the tool did, for use when the LLM's
    narration was lost to truncation. Keeps the user oriented when the
    card below is real but the bubble above would otherwise be the
    generic "Sorry, I had trouble" fallback.
    """
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
