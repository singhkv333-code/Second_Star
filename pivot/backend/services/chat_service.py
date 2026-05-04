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
from typing import Any, AsyncIterator, Optional

from backend.llm import LLMClient, LLMMessage, ToolDef, get_llm_client
from backend.llm.base import ReasoningEffort
from backend.prompts import build_system_prompt
from backend.prompts.assembler import UserContext as PromptUserContext
from backend.services.chat_trace import TurnTrace, start_turn
from backend.services.conversation_store import ConversationStore, default_store
from backend.services.fast_path import try_fast_path
from backend.services.tool_registry import get_tool_schema
from backend.services.tool_router import (
    cache_key_for,
    filter_registry_tools,
    select_tool_names,
)
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


def _registry_tools_as_tooldefs(
    selected_names: Optional[set[str]] = None,
) -> list[ToolDef]:
    """Translate `agents/tools.py` ALL_TOOLS dicts → LLMClient ToolDefs.

    When `selected_names` is provided (from `tool_router.select_tool_names`),
    only matching tools are returned. The synthetic ASK_USER tool is always
    appended so the model has the clarification escape hatch regardless
    of routing.
    """
    raw = filter_registry_tools(get_tool_schema(), selected_names)
    out: list[ToolDef] = []
    for defn in raw:
        fn = defn.get("function") or {}
        out.append(ToolDef(
            name=fn.get("name", ""),
            description=fn.get("description", ""),
            parameters=fn.get("parameters") or {},
        ))
    out.append(ask_user_tool_def())
    return out


def _build_user_context(ctx: "UserContext") -> Optional[PromptUserContext]:
    """Assemble a compact prompt-ready context block from the chat
    UserContext. Pulls portfolio totals (from already-loaded holdings)
    and the user's active-workflows count (one DB query).

    Returns None when the context contains nothing useful — that lets
    the prompt assembler skip rendering an empty block.
    """
    portfolio_total: Optional[float] = None
    holdings_count: Optional[int] = None
    if ctx.holdings:
        try:
            portfolio_total = sum(
                float(h.get("last_price", 0) or 0) * float(h.get("quantity", 0) or 0)
                for h in ctx.holdings
            ) or None
        except (TypeError, ValueError):
            portfolio_total = None
        holdings_count = len(ctx.holdings) or None

    active_workflows: Optional[int] = None
    try:
        # Lazy import — avoids a circular at module load.
        from backend.models import Workflow, WorkflowStatus
        active_workflows = (
            ctx.db.query(Workflow)
            .filter(
                Workflow.user_id == ctx.user_id,
                Workflow.status == WorkflowStatus.active,
            )
            .count()
        )
    except Exception:
        # If the workflows table or model is unavailable for any
        # reason, the chat shouldn't 500. Quiet degrade.
        active_workflows = None

    if (
        portfolio_total is None
        and holdings_count is None
        and not active_workflows
    ):
        return None
    return PromptUserContext(
        user_id=ctx.user_id,
        portfolio_total_inr=portfolio_total,
        holdings_count=holdings_count,
        active_workflows_count=active_workflows,
    )


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
    as a recovery hint rather than data.

    Tool-specific hints: ``propose_workflow`` validation errors are
    almost always recoverable (an unknown step_type, a numeric field
    given as a ref-string, a missing config key). Telling the model
    "or finish with text" on those errors gave it permission to bail
    out with a chatty apology rather than retry — observed in the
    agent-bucket trace test on 2026-05-04 where the model received
    an unknown-step-type error and then wrote "Sorry — I hit a
    validation error" instead of fixing the step. So the hint for
    propose_workflow says: just emit the corrected draft, no asking,
    no apology.
    """
    if not g.success:
        if g.name == "propose_workflow":
            hint = (
                "RE-EMIT propose_workflow with the SAME draft but "
                "with the specific issue above fixed. Do NOT call "
                "ASK_USER. Do NOT write a 'Sorry, validation error' "
                "message — the user only sees that as a failure. "
                "The fix is usually mechanical: pick a real step_type "
                "from the listed allowed set, fill the named missing "
                "config key, or change a string field to the right "
                "type. Most drafts succeed within 1-2 retries."
            )
        else:
            hint = (
                "Decide whether to call a different tool, call "
                "ASK_USER for clarification, or finish with a brief "
                "explanation. Do not retry the same call with the "
                "same arguments."
            )
        return json.dumps({
            "error": g.error or "tool failed",
            "hint": hint,
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
        # Per-hop tool router — narrows the visible tool surface from
        # ~48 down to ~8-12 based on keyword matches in the user's
        # current message. Halves input tokens on most turns. The
        # router is tolerant: if no rules match it returns the
        # always-include floor + fallback read tools, so we never
        # ship a turn with zero tools.
        selected_names = select_tool_names(message)
        tooldefs = _registry_tools_as_tooldefs(selected_names)
        # Route-stable cache key — a fresh hash of the routed toolset
        # so each unique route caches its own system + tools prefix.
        # Without this, every route shift used to miss the cache for
        # one turn before warming.
        cache_key = cache_key_for(selected_names)
        # Reasoning-effort: "low" universally. We tried bumping to
        # "medium" for propose_workflow turns; quality went up modestly
        # but latency on multi-trigger drafts blew past the client
        # timeout (>2 minutes per turn at gpt-5-mini medium). The
        # speed/quality tradeoff favors low — the remaining quality
        # gap is closed in the system prompt + tool description, not
        # by burning reasoning tokens.
        effort: ReasoningEffort = "low"
        max_output: int = 1500
        trace.event(
            "tool_router.select",
            n_selected=len(tooldefs),
            names=sorted([t.name for t in tooldefs])[:12],
            cache_key=cache_key,
            reasoning_effort=effort,
            max_output_tokens=max_output,
        )

        prompt_ctx = _build_user_context(ctx)
        messages: list[LLMMessage] = [
            LLMMessage(
                role="system",
                content=build_system_prompt(role="chat", user_context=prompt_ctx),
            ),
            *_history_to_llm_messages(history),
            LLMMessage(role="user", content=message),
        ]

        tools_called: list[str] = []
        logiccard: Optional[dict] = None
        raw_data: dict = {}
        hop_index = 0
        # Track the most recent tool error so the circuit-breaker
        # fallback can surface a specific reason instead of a generic
        # "I had trouble". The user's "internal step-format issue"
        # message was caused by the breaker swallowing this.
        last_tool_error: Optional[str] = None

        # ── Loop ───────────────────────────────────────────────────
        while hop_index < _MAX_TOOL_CALLS:
            hop_index += 1
            trace.event("llm.call", hop=hop_index, reasoning_effort=effort,
                        tools_offered=len(tooldefs))
            try:
                response = await client.complete(
                    messages=messages,
                    tools=tooldefs,
                    tool_choice="auto",
                    max_output_tokens=max_output,
                    reasoning_effort=effort,
                    temperature=0.2,
                    prompt_cache_key=cache_key,
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
            # Stash cache-hit token count alongside the hop latency so
            # _log_timing surfaces it without changing the log shape.
            if response.cached_tokens:
                breakdown[f"llm_hop_{hop_index}_cached"] = response.cached_tokens
            trace.event("llm.response", hop=hop_index,
                        finish_reason=response.finish_reason,
                        latency_ms=response.latency_ms,
                        input_tokens=response.input_tokens,
                        output_tokens=response.output_tokens,
                        reasoning_tokens=response.reasoning_tokens,
                        cached_tokens=response.cached_tokens)

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
                elif guarded.error:
                    last_tool_error = (
                        f"{guarded.name}: {guarded.error}"
                    )

            # back to top of loop — model now sees tool results

        # Circuit-breaker hit.
        logger.warning("agentic loop hit MAX_TOOL_CALLS=%d (last_err=%s)",
                       _MAX_TOOL_CALLS, last_tool_error)
        total = int((time.monotonic() - turn_started) * 1000)
        breakdown["total"] = total
        if last_tool_error:
            msg = (
                "I couldn't finish that build — the workflow draft kept "
                f"failing validation. Last error: {last_tool_error[:240]}. "
                "Try rephrasing with the specific values you want."
            )
        else:
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

    # ── Streaming surface ─────────────────────────────────────────────
    #
    # `handle_stream` is the SSE-fronted twin of `handle`. It runs the
    # same agentic loop, but yields events as work progresses:
    #
    #   {"type": "start"}
    #   {"type": "tool_start", "name": "..."}              (per tool invoke)
    #   {"type": "tool_done",  "name": "...", "ok": bool}
    #   {"type": "delta",      "text": "..."}              (final-hop tokens)
    #   {"type": "done",       ...full ChatTurn payload}
    #   {"type": "error",      "message": "..."}
    #
    # Why `handle_stream` only streams the FINAL hop's text:
    #   - Intermediate hops carry tool_calls; their text content is
    #     usually empty. There's nothing for the user to read.
    #   - Tool execution is serial and 0–2s typically; the FE can show
    #     a "Running tool…" pill from `tool_start` until `tool_done`.
    #   - Streaming the final hop is where the perceived-latency win
    #     lives — first token within ~1s, full reply ~3-5s later.

    async def handle_stream(
        self,
        message: str,
        conv_id: str,
        ctx: UserContext,
        *,
        history_override: list[dict] | None = None,
    ) -> AsyncIterator[dict]:
        from backend.llm.openai_client import LLMOpenAI, stream_openai

        turn_started = time.monotonic()
        breakdown: dict[str, int] = {}
        trace = start_turn(conv_id, message)
        trace.event("turn.start.stream", message_preview=message[:120])

        yield {"type": "start"}

        # ── Fast path ──────────────────────────────────────────────
        fast_response = try_fast_path(message)
        if fast_response is not None:
            trace.event("fast_path.matched")
            self.store.append(conv_id, message, fast_response)
            total = int((time.monotonic() - turn_started) * 1000)
            breakdown["fast_path"] = total
            breakdown["total"] = total
            yield {"type": "delta", "text": fast_response}
            yield {
                "type": "done",
                "response": fast_response,
                "tools_called": [],
                "logiccard": None,
                "raw_data": None,
                "latency_ms": total,
                "latency_breakdown": breakdown,
            }
            trace.end()
            return

        # ── Agentic loop setup ─────────────────────────────────────
        history = (
            history_override
            if history_override is not None
            else self.store.get_history(conv_id, limit=20)
        )

        client = self._client()
        # Streaming is currently OpenAI-only (Sarvam doesn't true-stream
        # tool calls). Detect at runtime; on Sarvam we degrade to the
        # non-streaming `handle()` and emit the result as one delta.
        can_stream = isinstance(client, LLMOpenAI)

        if not can_stream:
            turn = await self.handle(
                message, conv_id, ctx, history_override=history_override,
            )
            yield {"type": "delta", "text": turn.response}
            yield {
                "type": "done",
                "response": turn.response,
                "tools_called": turn.tools_called,
                "logiccard": turn.logiccard,
                "raw_data": turn.raw_data or None,
                "latency_ms": turn.latency_ms,
                "latency_breakdown": turn.latency_breakdown,
            }
            return

        selected_names = select_tool_names(message)
        tooldefs = _registry_tools_as_tooldefs(selected_names)
        cache_key = cache_key_for(selected_names)
        # Same effort + budget as the non-streaming path. Low everywhere
        # for speed; quality is closed in the system prompt instead.
        effort: ReasoningEffort = "low"
        max_output: int = 1500
        trace.event(
            "tool_router.select",
            n_selected=len(tooldefs),
            names=sorted([t.name for t in tooldefs])[:12],
            cache_key=cache_key,
            reasoning_effort=effort,
        )

        prompt_ctx = _build_user_context(ctx)
        messages: list[LLMMessage] = [
            LLMMessage(
                role="system",
                content=build_system_prompt(role="chat", user_context=prompt_ctx),
            ),
            *_history_to_llm_messages(history),
            LLMMessage(role="user", content=message),
        ]

        tools_called: list[str] = []
        logiccard: Optional[dict] = None
        raw_data: dict = {}
        hop_index = 0
        accumulated_text = ""
        # Track the most recent tool error so the streaming
        # circuit-breaker can surface it to the user.
        last_tool_error: Optional[str] = None

        while hop_index < _MAX_TOOL_CALLS:
            hop_index += 1
            hop_started = time.monotonic()
            trace.event(
                "llm.stream", hop=hop_index,
                reasoning_effort=effort, tools_offered=len(tooldefs),
            )

            text_parts: list[str] = []
            # Function-call accumulator keyed by **item_id** (the
            # `fc_...` value Responses API uses on every delta event).
            # The downstream `call_id` (`call_...`) lives inside the
            # slot — that's what we send back as the tool_call_id when
            # we feed the result to the next hop.
            tc_acc: dict[str, dict[str, Any]] = {}
            cached_tokens = 0
            stream_error: Optional[str] = None

            async for ev in stream_openai(
                client,
                messages=messages,
                tools=tooldefs,
                tool_choice="auto",
                max_output_tokens=max_output,
                reasoning_effort=effort,
                temperature=0.2,
                prompt_cache_key=cache_key,
            ):
                etype = ev.get("type")
                # Verbose stream-debug: emit every event type the first time
                # we see it on a hop to catch missed event paths.
                logger.debug("stream ev hop=%d type=%s keys=%s",
                             hop_index, etype, list(ev.keys()))
                if etype == "error":
                    stream_error = ev.get("message") or "stream error"
                    break

                if etype == "response.output_text.delta":
                    delta = ev.get("delta") or ""
                    if delta:
                        text_parts.append(delta)
                        # Stream user-visible text live.
                        yield {"type": "delta", "text": delta}
                    continue

                if etype == "response.output_item.added":
                    item = ev.get("item") or {}
                    if item.get("type") == "function_call":
                        # Key the accumulator by item_id (the value
                        # delta events reference). Stash call_id
                        # separately — it's what the next-hop
                        # function_call_output must echo back.
                        item_id = item.get("id") or ""
                        if item_id:
                            tc_acc.setdefault(item_id, {
                                "item_id": item_id,
                                "call_id": item.get("call_id") or "",
                                "name": item.get("name", "") or "",
                                "args_str": item.get("arguments", "") or "",
                            })
                    continue

                if etype == "response.function_call_arguments.delta":
                    item_id = ev.get("item_id") or ""
                    if item_id:
                        slot = tc_acc.setdefault(item_id, {
                            "item_id": item_id,
                            "call_id": "",
                            "name": "",
                            "args_str": "",
                        })
                        slot["args_str"] += ev.get("delta", "") or ""
                    continue

                if etype == "response.output_item.done":
                    item = ev.get("item") or {}
                    if item.get("type") == "function_call":
                        item_id = item.get("id") or ""
                        if item_id:
                            slot = tc_acc.setdefault(item_id, {
                                "item_id": item_id,
                                "call_id": "",
                                "name": "",
                                "args_str": "",
                            })
                            if item.get("call_id"):
                                slot["call_id"] = item["call_id"]
                            if item.get("name"):
                                slot["name"] = item["name"]
                            if item.get("arguments"):
                                slot["args_str"] = item["arguments"]
                    continue

                if etype == "response.completed":
                    resp_obj = ev.get("response") or {}
                    usage = resp_obj.get("usage") or {}
                    cached_tokens = int(
                        (usage.get("input_tokens_details") or {}).get(
                            "cached_tokens", 0
                        ) or 0
                    )
                    continue

                # Other events (response.created, response.in_progress,
                # reasoning deltas) are ignored.

            hop_ms = int((time.monotonic() - hop_started) * 1000)
            breakdown[f"llm_hop_{hop_index}"] = hop_ms
            if cached_tokens:
                breakdown[f"llm_hop_{hop_index}_cached"] = cached_tokens

            if stream_error:
                logger.warning("stream error at hop %d: %s", hop_index, stream_error)
                trace.event("turn.end", reason="llm_error")
                trace.end()
                yield {"type": "error", "message": _LLM_UNAVAILABLE}
                yield {
                    "type": "done",
                    "response": _LLM_UNAVAILABLE,
                    "tools_called": tools_called,
                    "logiccard": logiccard,
                    "raw_data": {"_llm_unavailable": True},
                    "latency_ms": int((time.monotonic() - turn_started) * 1000),
                    "latency_breakdown": breakdown,
                }
                return

            hop_text = "".join(text_parts)
            accumulated_text = hop_text  # final hop's text wins

            # No tool calls → final hop. Wrap up.
            if not tc_acc:
                text, sanitised = _post_process(hop_text)
                if sanitised and text == _GENERIC_FALLBACK and tools_called:
                    text = _tool_summary_line(tools_called[-1], logiccard)
                    sanitised = False
                # If the post-processor rewrote the text, the user has
                # already seen the raw stream — emit a correction by
                # sending the cleaned text as a single replacement.
                if sanitised:
                    yield {"type": "replace", "text": text}
                self.store.append(conv_id, message, text)
                total = int((time.monotonic() - turn_started) * 1000)
                breakdown["total"] = total
                _log_timing(client.provider_name, message, total, breakdown,
                            tools=tools_called, note="stream")
                trace.event("turn.end", total_ms=total,
                            tools_called=tools_called, reason="stop")
                trace.end()
                yield {
                    "type": "done",
                    "response": text,
                    "tools_called": tools_called,
                    "logiccard": logiccard,
                    "raw_data": raw_data or None,
                    "latency_ms": total,
                    "latency_breakdown": breakdown,
                }
                return

            # Build tool_calls list with parsed args for the assistant
            # message + executor. The downstream `id` here is the
            # `call_id` — that's what function_call_output must echo;
            # `item_id` is internal to the streaming protocol and not
            # used past this point.
            tool_calls: list[dict[str, Any]] = []
            for slot in tc_acc.values():
                args_str = slot.get("args_str") or "{}"
                try:
                    args = json.loads(args_str) if args_str else {}
                except json.JSONDecodeError:
                    args = {"_raw_arguments": args_str, "_parse_error": True}
                tool_calls.append({
                    "id": slot.get("call_id") or slot.get("item_id", ""),
                    "name": slot.get("name", ""),
                    "arguments": args,
                })

            messages.append(LLMMessage(
                role="assistant",
                content=hop_text,
                tool_calls=tool_calls,
            ))

            for tc in tool_calls:
                yield {"type": "tool_start", "name": tc.get("name", "")}
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
                yield {
                    "type": "tool_done",
                    "name": guarded.name,
                    "ok": guarded.success,
                    "error": guarded.error,
                }

                if guarded.needs_clarification and guarded.question:
                    self.store.append(conv_id, message, guarded.question)
                    total = int((time.monotonic() - turn_started) * 1000)
                    breakdown["total"] = total
                    yield {"type": "delta", "text": guarded.question}
                    yield {
                        "type": "done",
                        "response": guarded.question,
                        "tools_called": [guarded.name],
                        "logiccard": None,
                        "raw_data": {"_render_hint": "ask_user"},
                        "latency_ms": total,
                        "latency_breakdown": breakdown,
                    }
                    _log_timing(client.provider_name, message, total, breakdown,
                                tools=[guarded.name], note="stream-ask")
                    trace.event("turn.end", total_ms=total,
                                tools_called=[guarded.name],
                                reason="needs_clarification")
                    trace.end()
                    return

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
                elif guarded.error:
                    last_tool_error = f"{guarded.name}: {guarded.error}"

            # next iteration of the loop will stream the next hop

        # Circuit-breaker hit during streaming.
        logger.warning("stream loop hit MAX_TOOL_CALLS=%d (last_err=%s)",
                       _MAX_TOOL_CALLS, last_tool_error)
        total = int((time.monotonic() - turn_started) * 1000)
        breakdown["total"] = total
        if last_tool_error:
            msg = (
                "I couldn't finish that build — the workflow draft kept "
                f"failing validation. Last error: {last_tool_error[:240]}. "
                "Try rephrasing with the specific values you want."
            )
        else:
            msg = (
                "I needed to look up several things and got a bit lost. "
                "Could you ask again with more specifics?"
            )
        self.store.append(conv_id, message, msg)
        yield {"type": "delta", "text": msg}
        yield {
            "type": "done",
            "response": msg,
            "tools_called": tools_called,
            "logiccard": logiccard,
            "raw_data": {"_render_hint": "circuit_breaker"},
            "latency_ms": total,
            "latency_breakdown": breakdown,
        }

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
