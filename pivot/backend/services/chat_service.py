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
from typing import Any, AsyncIterator, Literal, Optional

from backend.llm import LLMClient, LLMMessage, ToolDef, get_llm_client
from backend.llm.base import ReasoningEffort
from backend.prompts import build_system_prompt
from backend.prompts.assembler import UserContext as PromptUserContext
from backend.services.chat_trace import TurnTrace, start_turn
from backend.services.conversation_store import ConversationStore, default_store
from backend.services.fast_path import try_fast_path
from backend.services.tool_registry import get_tool_schema
from backend.services.workflow_skeleton import try_workflow_skeleton
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


# Phrases that signal "user wants to BUILD an agent / workflow".
# Used to flip tool_choice to required and reasoning_effort to minimal
# (A1 + B4 from the latency analysis). Conservative — we'd rather miss
# a tightening opportunity than apply it to a query like "what's
# RELIANCE's price". When this hits, we still let the LLM choose which
# tool (propose_workflow, create_strategy, …) but force it to commit.
_AGENT_INTENT_RE = re.compile(
    # "build|create|set up an agent / strategy / workflow"
    r"\b(?:build|create|set\s*up|setup|make|generate|design)\s+"
    r"(?:me\s+)?an?\s+(?:agent|strategy|workflow|automation|rule|bot|sip)\b"
    # Time-anchored buy/sell: "every Monday at 9:15 buy 5 NIFTYBEES"
    r"|\bevery\s+(?:weekday|monday|tuesday|wednesday|thursday|friday|"
    r"day|week|hour|morning|evening)\b[^\.]{0,80}"
    r"\b(?:buy|sell|order|trade|invest|put|allocate)\b"
    # "buy ... every Monday"
    r"|\b(?:buy|sell)\b[^\.]{0,40}\bevery\b"
    # Indicator-anchored conditional: "when RSI < 30"
    r"|\bwhen(?:ever)?\b[^\.]{0,80}\b(?:rsi|sma|ema|macd)\b"
    # Watch / monitor + and/then
    r"|\b(?:watch|monitor|track|alert\s+me|notify\s+me)\b[^\.]{0,80}"
    r"\b(?:and|then)\b"
    # Explicit phrases
    r"|\bautomatic(?:ally)?\s+execut"
    # Conditional rule shapes: "if SYM dips/drops/rises X% then Y"
    # Without this, "if reliance dips 5% on monday set a stop loss"
    # falls into the slow auto-tool path instead of A1+B4.
    r"|\bif\b[^\.]{0,120}\b(?:dips?|drops?|falls?|rises?|crosses?|hits?|reaches?)\b"
    r"|\bwhen(?:ever)?\b[^\.]{0,80}\b(?:dips?|drops?|falls?|rises?|crosses?)\b\s*\d+\s*%"
    # Stop-loss / take-profit / target language. Setting an SL is
    # almost always a workflow build (the user wants something automated
    # tied to a trigger or holding). The middle slop allows "set a 2%
    # stop loss" / "place a trailing stop" / "add an SL at ₹1400".
    r"|\b(?:set|place|put|create|add)\s+(?:a\s+|an\s+)?"
    r"(?:[\d.]+\s*%\s+)?"
    r"(?:stop[- ]?loss|sl|stoploss|trailing\s+stop|take[- ]?profit|tp|target)\b"
    # "place a buy/sell ... when X" — conditional order
    r"|\b(?:place|put)\s+a?\s*(?:buy|sell|stop|limit|gtt|sl|stoploss)\b[^\.]{0,80}\bwhen\b"
    # "X% dip" / "5% drop" + ANY action verb in same sentence
    r"|\b\d+\s*%\s*(?:dip|drop|fall|rise|crash|gain)\b",
    re.IGNORECASE,
)


def _looks_like_agent_intent(message: str) -> bool:
    """True for messages that clearly want a multi-step automation.

    Drives the A1 (tool_choice=required) and B4 (reasoning=minimal)
    optimisations. False on simple data queries / orders so they keep
    the broader 'auto' tool surface.
    """
    if not message:
        return False
    return bool(_AGENT_INTENT_RE.search(message))


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

        # ── Workflow skeleton fast-path ────────────────────────────
        # Canonical agent shapes (scheduled SIP, RSI threshold, price
        # threshold) skip the LLM hop entirely. Validates the draft
        # against the step registry before returning so a structurally
        # broken skeleton falls through to the LLM rather than going
        # out wrong.
        skeleton = try_workflow_skeleton(message)
        if skeleton is not None:
            try:
                from backend.workflows.propose import (
                    ProposalValidationError, validate_draft_against_registry,
                )
                validate_draft_against_registry(skeleton)
            except ProposalValidationError as e:
                trace.event("workflow_skeleton.invalid", error=str(e)[:120])
                skeleton = None
        if skeleton is not None:
            trace.event(
                "workflow_skeleton.matched",
                workflow_name=skeleton.get("name"),
                step_types=[s["step_type"] for s in skeleton.get("steps") or []],
            )
            response_text = _workflow_skeleton_caption(skeleton)
            self.store.append(conv_id, message, response_text)
            total = int((time.monotonic() - turn_started) * 1000)
            breakdown["workflow_skeleton"] = total
            breakdown["total"] = total
            _log_timing("workflow_skeleton", message, total, breakdown,
                        tools=["propose_workflow"])
            trace.event("turn.end", total_ms=total,
                        tools_called=["propose_workflow"], reason="skeleton")
            trace.end()
            # Stash the draft under the tool name (matches the agentic
            # loop's convention for raw_data) AND let the router's
            # hoisting lift name/steps/rationale to top-level so the
            # FE's WorkflowDraftCard can read them directly. We
            # deliberately do NOT set top-level _render_hint here —
            # the hoister only fires when it's absent, and we want it
            # to fire so the draft fields get hoisted alongside the
            # render hint.
            return ChatTurn(
                response=response_text,
                tools_called=["propose_workflow"],
                latency_ms=total,
                raw_data={"propose_workflow": skeleton},
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
        is_agent_intent = _looks_like_agent_intent(message)
        # When agent intent is clear, strip the immediate-order tools
        # from the surface — keeping them around made the model pick
        # `place_limit_order` / `create_sl_order` for prompts that
        # were really workflow asks (e.g. "buy 10 INFY if it drops
        # 3% from yesterday close"). Forcing the choice to
        # propose_workflow is the better outcome; if the user really
        # wanted an immediate order they'd phrase it without the
        # conditional ("buy 10 INFY at market").
        _IMMEDIATE_ORDER_TOOLS = frozenset({
            "place_market_order", "place_limit_order",
            "create_gtt_order", "create_sl_order", "create_oco_order",
            "create_dip_buy", "place_basket_order",
        })
        if is_agent_intent and selected_names is not None:
            selected_names = (selected_names - _IMMEDIATE_ORDER_TOOLS) | {
                "propose_workflow",
            }
        tooldefs = _registry_tools_as_tooldefs(selected_names)
        # Route-stable cache key — a fresh hash of the routed toolset
        # so each unique route caches its own system + tools prefix.
        # Without this, every route shift used to miss the cache for
        # one turn before warming.
        cache_key = cache_key_for(selected_names)

        # ── Agent-intent fast path tuning (A1 + B4) ────────────────
        # When the message signals "build me an agent" we know the
        # intended tool is propose_workflow. We then:
        #   A1. Set tool_choice="required" so the model MUST emit a
        #       tool call instead of think-aloud text. Removes 1–3
        #       wasted hops per turn.
        #   B4. Drop reasoning_effort to "minimal". Trace data showed
        #       gpt-5-mini hops with 800 reasoning tokens added ~10s
        #       of latency without changing the JSON output, since
        #       few-shot examples in the tool description already
        #       guide structure.
        # The skeleton fast-path (above) intercepts the easiest agent
        # shapes pre-LLM; this branch covers everything that fell
        # through to the model.
        agent_tool_choice: Literal["auto", "required"] = (
            "required" if is_agent_intent else "auto"
        )
        # Reasoning-effort: "low" universally except for agent turns
        # which run "minimal". We tried bumping to "medium" earlier
        # for propose_workflow turns; quality went up modestly but
        # latency on multi-trigger drafts blew past the client
        # timeout. Going the other way (low → minimal) cut p50
        # dramatically without measurable quality loss.
        effort: ReasoningEffort = "minimal" if is_agent_intent else "low"
        max_output: int = 1500
        # A2: per-tool retry budget for propose_workflow. The
        # validation-retry loop is great at fixing typos but it can't
        # fix structurally unsatisfiable requests. After 3 attempts
        # we escalate to ASK_USER. Other tools keep the broader cap.
        propose_workflow_attempts = 0
        _PROPOSE_WORKFLOW_MAX_ATTEMPTS = 3
        trace.event(
            "tool_router.select",
            n_selected=len(tooldefs),
            names=sorted([t.name for t in tooldefs])[:12],
            cache_key=cache_key,
            reasoning_effort=effort,
            max_output_tokens=max_output,
            tool_choice=agent_tool_choice,
            agent_intent=is_agent_intent,
        )

        prompt_ctx = _build_user_context(ctx)
        # Follow-up nudge: when the last assistant turn was a
        # clarification (ends with `?`), the user's current message is
        # answering it. Without this hint the model often re-plans the
        # whole turn from scratch — observed: a "2" reply taking 25s
        # because the model walks the full reasoning loop again. With
        # the hint, it merges the answer into the prior intent and
        # emits the tool directly. Cheap nudge, big latency win.
        #
        # Stronger v2: include the original user request inline (not
        # just "earlier intent") so the model can't lose the load-
        # bearing context. After the clarifying answer, the model has
        # everything it needs — failing to emit a tool here is the
        # single most-reported failure shape ("user already told me X
        # but now I'm asking again").
        followup_hint: Optional[LLMMessage] = None
        original_intent: Optional[str] = None
        if history and len(message) <= 50:
            last_assistant = next(
                (h for h in reversed(history)
                 if isinstance(h, dict) and h.get("role") == "assistant"),
                None,
            )
            last_text = (last_assistant or {}).get("content") or ""
            if last_text.rstrip().endswith("?"):
                # First user message in history = the original ask.
                first_user = next(
                    (h for h in history
                     if isinstance(h, dict) and h.get("role") == "user"),
                    None,
                )
                original_intent = (first_user or {}).get("content") or ""
                followup_hint = LLMMessage(
                    role="system",
                    content=(
                        "FOLLOW-UP TURN. The user is answering your "
                        f"clarifying question. Their ORIGINAL request was: "
                        f'"{original_intent[:280]}". Their LAST clarification '
                        f'asked: "{last_text[-200:]}". Their CURRENT reply '
                        f'is: "{message}". '
                        "Merge the reply into the original request and call "
                        "the matching tool (propose_workflow / "
                        "place_market_order / etc.) IMMEDIATELY with the "
                        "complete arguments. Do NOT ask another question. "
                        "Do NOT paraphrase back as 'Confirm: …'. Do NOT "
                        "ignore the original request. If the merged request "
                        "still has missing required fields, fill them with "
                        "sensible defaults (qty=1, exchange=NSE, "
                        "order_type=market) rather than asking a second "
                        "round."
                    ),
                )

        base_messages: list[LLMMessage] = [
            LLMMessage(
                role="system",
                content=build_system_prompt(role="chat", user_context=prompt_ctx),
            ),
        ]
        if followup_hint is not None:
            base_messages.append(followup_hint)

        messages: list[LLMMessage] = [
            *base_messages,
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
        # Per (tool, error-signature) counter. Caps how many times the
        # model can fail with the SAME class of error before we give
        # up the retry loop and ask the user. Without this, gpt-5-mini
        # would loop 8× on a structurally unsatisfiable propose_workflow
        # field (e.g. trigger_price required but user gave a percentage),
        # blowing through the circuit-breaker at 45–55s with a generic
        # "couldn't finish" message.
        error_counts: dict[tuple[str, str], int] = {}
        _SAME_ERROR_LIMIT = 2

        # ── Loop ───────────────────────────────────────────────────
        while hop_index < _MAX_TOOL_CALLS:
            hop_index += 1
            # A1: only force tool_choice on the FIRST hop. Subsequent
            # hops carry tool results and must allow the model to emit
            # a final text response (otherwise the loop never exits).
            hop_tool_choice: Literal["auto", "required"] = (
                agent_tool_choice if hop_index == 1 else "auto"
            )
            trace.event("llm.call", hop=hop_index, reasoning_effort=effort,
                        tools_offered=len(tooldefs),
                        tool_choice=hop_tool_choice)
            try:
                response = await client.complete(
                    messages=messages,
                    tools=tooldefs,
                    tool_choice=hop_tool_choice,
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
                # Always ensure the assistant text describes any widget
                # that's about to render. Prevents a card-with-no-text
                # bubble that reads as a glitch in the chat.
                text = _ensure_widget_caption(
                    text,
                    tool_name=(tools_called[-1] if tools_called else ""),
                    logiccard=logiccard,
                    raw_data=raw_data,
                )
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
                    # A2: per-tool attempt cap for propose_workflow.
                    # The model sometimes fails for DIFFERENT reasons
                    # on consecutive attempts (e.g. wrong cron then
                    # wrong step_type) — the same-error escalation
                    # below misses that case because the signature
                    # changes. A flat 3-attempt cap on propose_workflow
                    # specifically catches it: by attempt 4 we've seen
                    # enough that the model isn't going to nail it.
                    if guarded.name == "propose_workflow":
                        propose_workflow_attempts += 1
                        if propose_workflow_attempts >= _PROPOSE_WORKFLOW_MAX_ATTEMPTS:
                            # Last-ditch macro fallback. If the user's
                            # phrasing matches a known shape (SL on
                            # holding, etc.), hydrate that as a draft
                            # rather than emitting the generic "I
                            # couldn't" message — the user already
                            # waited 20+ seconds, give them a workable
                            # starting point.
                            fb_draft = _try_macro_fallback(message)
                            if fb_draft is not None:
                                fb_text = (
                                    "I couldn't fit your full request "
                                    "into a single workflow shape, so "
                                    "I've drafted a simplified version "
                                    "you can edit. The trigger has been "
                                    "set to manual — review the steps "
                                    "and adjust the trigger before "
                                    "activating."
                                )
                                self.store.append(conv_id, message, fb_text)
                                total = int((time.monotonic() - turn_started) * 1000)
                                breakdown["total"] = total
                                _log_timing(
                                    client.provider_name, message, total,
                                    breakdown, tools=tools_called,
                                    note="propose_workflow_macro_fallback",
                                )
                                trace.event(
                                    "turn.end", total_ms=total,
                                    tools_called=tools_called + ["propose_holding_action"],
                                    reason="propose_workflow_macro_fallback",
                                    attempts=propose_workflow_attempts,
                                )
                                trace.end()
                                return ChatTurn(
                                    response=fb_text,
                                    tools_called=tools_called + ["propose_holding_action"],
                                    raw_data={
                                        **fb_draft,
                                        "propose_workflow": fb_draft,
                                    },
                                    latency_ms=total,
                                    latency_breakdown=breakdown,
                                )
                            question = _format_recoverable_failure_question(
                                tool_name=guarded.name,
                                error=guarded.error or "",
                            )
                            self.store.append(conv_id, message, question)
                            total = int((time.monotonic() - turn_started) * 1000)
                            breakdown["total"] = total
                            _log_timing(
                                client.provider_name, message, total, breakdown,
                                tools=tools_called,
                                note=(
                                    f"propose_workflow_attempts="
                                    f"{propose_workflow_attempts}"
                                ),
                            )
                            trace.event(
                                "turn.end", total_ms=total,
                                tools_called=tools_called,
                                reason="propose_workflow_attempt_cap",
                                attempts=propose_workflow_attempts,
                                last_error=(guarded.error or "")[:120],
                            )
                            trace.end()
                            return ChatTurn(
                                response=question,
                                tools_called=tools_called + [guarded.name],
                                raw_data={"_render_hint": "ask_user"},
                                latency_ms=total,
                                latency_breakdown=breakdown,
                            )
                    # Same-error escalation. Two consecutive failures of
                    # the same (tool, error-class) → ASK_USER instead of
                    # hammering the loop. The signature is the first
                    # ~80 chars of the error message — captures the
                    # "field required" / "must be one of" / etc. shape
                    # without being so broad it conflates unrelated
                    # failures.
                    sig = (guarded.error or "")[:80]
                    key = (guarded.name, sig)
                    error_counts[key] = error_counts.get(key, 0) + 1
                    if error_counts[key] >= _SAME_ERROR_LIMIT:
                        question = _format_recoverable_failure_question(
                            tool_name=guarded.name, error=guarded.error or "",
                        )
                        self.store.append(conv_id, message, question)
                        total = int((time.monotonic() - turn_started) * 1000)
                        breakdown["total"] = total
                        _log_timing(
                            client.provider_name, message, total, breakdown,
                            tools=tools_called,
                            note=f"same_error_escalation:{guarded.name}",
                        )
                        trace.event(
                            "turn.end", total_ms=total,
                            tools_called=tools_called,
                            reason="same_error_escalation",
                            tool=guarded.name, sig=sig,
                        )
                        trace.end()
                        return ChatTurn(
                            response=question,
                            tools_called=tools_called + [guarded.name],
                            raw_data={"_render_hint": "ask_user"},
                            latency_ms=total,
                            latency_breakdown=breakdown,
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

        # ── Workflow skeleton fast-path ────────────────────────────
        skeleton = try_workflow_skeleton(message)
        if skeleton is not None:
            try:
                from backend.workflows.propose import (
                    ProposalValidationError, validate_draft_against_registry,
                )
                validate_draft_against_registry(skeleton)
            except ProposalValidationError:
                skeleton = None
        if skeleton is not None:
            trace.event(
                "workflow_skeleton.matched",
                workflow_name=skeleton.get("name"),
                step_types=[s["step_type"] for s in skeleton.get("steps") or []],
            )
            response_text = _workflow_skeleton_caption(skeleton)
            self.store.append(conv_id, message, response_text)
            total = int((time.monotonic() - turn_started) * 1000)
            breakdown["workflow_skeleton"] = total
            breakdown["total"] = total
            yield {"type": "tool_start", "name": "propose_workflow"}
            yield {"type": "tool_done", "name": "propose_workflow", "ok": True}
            yield {"type": "delta", "text": response_text}
            # Streaming path: the chat router's `done` payload doesn't
            # go through the same hoist logic as the non-streaming
            # `/chat` POST — it ships raw_data verbatim. So here we
            # DO need to hoist the draft fields ourselves so the FE's
            # WorkflowDraftCard finds name/steps at the top level.
            stream_raw_data = {
                **skeleton,
                "propose_workflow": skeleton,
                "_render_hint": "workflow_draft_card",
            }
            yield {
                "type": "done",
                "response": response_text,
                "tools_called": ["propose_workflow"],
                "logiccard": None,
                "raw_data": stream_raw_data,
                "latency_ms": total,
                "latency_breakdown": breakdown,
            }
            _log_timing("workflow_skeleton", message, total, breakdown,
                        tools=["propose_workflow"], note="stream-skeleton")
            trace.event("turn.end", total_ms=total,
                        tools_called=["propose_workflow"], reason="skeleton")
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
        is_agent_intent = _looks_like_agent_intent(message)
        # Streaming mirror of the non-streaming narrowing: drop
        # immediate-order tools when agent intent is clear so the
        # model commits to propose_workflow.
        _IMMEDIATE_ORDER_TOOLS = frozenset({
            "place_market_order", "place_limit_order",
            "create_gtt_order", "create_sl_order", "create_oco_order",
            "create_dip_buy", "place_basket_order",
        })
        if is_agent_intent and selected_names is not None:
            selected_names = (selected_names - _IMMEDIATE_ORDER_TOOLS) | {
                "propose_workflow",
            }
        tooldefs = _registry_tools_as_tooldefs(selected_names)
        cache_key = cache_key_for(selected_names)
        # A1 + B4 (mirror of non-streaming path): when the message
        # signals "build me an agent", lock tool_choice to required
        # and drop reasoning_effort to minimal. See _looks_like_agent_intent.
        agent_tool_choice: Literal["auto", "required"] = (
            "required" if is_agent_intent else "auto"
        )
        effort: ReasoningEffort = "minimal" if is_agent_intent else "low"
        max_output: int = 1500
        # A2 — mirrored: 3-attempt cap on propose_workflow specifically.
        propose_workflow_attempts = 0
        _PROPOSE_WORKFLOW_MAX_ATTEMPTS = 3
        trace.event(
            "tool_router.select",
            n_selected=len(tooldefs),
            names=sorted([t.name for t in tooldefs])[:12],
            cache_key=cache_key,
            reasoning_effort=effort,
            tool_choice=agent_tool_choice,
            agent_intent=is_agent_intent,
        )

        prompt_ctx = _build_user_context(ctx)
        # Stronger follow-up nudge — mirror of the non-streaming path.
        # Carries the original user request inline so the model can't
        # treat the answer as a fresh prompt.
        followup_hint_msg: Optional[LLMMessage] = None
        if history and len(message) <= 50:
            last_assistant = next(
                (h for h in reversed(history)
                 if isinstance(h, dict) and h.get("role") == "assistant"),
                None,
            )
            last_text = (last_assistant or {}).get("content") or ""
            if last_text.rstrip().endswith("?"):
                first_user = next(
                    (h for h in history
                     if isinstance(h, dict) and h.get("role") == "user"),
                    None,
                )
                original_intent = (first_user or {}).get("content") or ""
                followup_hint_msg = LLMMessage(
                    role="system",
                    content=(
                        "FOLLOW-UP TURN. The user is answering your "
                        "clarifying question. Their ORIGINAL request was: "
                        f'"{original_intent[:280]}". Their LAST clarification '
                        f'asked: "{last_text[-200:]}". Their CURRENT reply '
                        f'is: "{message}". '
                        "Merge the reply into the original request and call "
                        "the matching tool (propose_workflow / "
                        "place_market_order / etc.) IMMEDIATELY with the "
                        "complete arguments. Do NOT ask another question. "
                        "If the merged request still has missing required "
                        "fields, fill them with sensible defaults (qty=1, "
                        "exchange=NSE, order_type=market) rather than "
                        "asking a second round."
                    ),
                )

        base_msgs: list[LLMMessage] = [
            LLMMessage(
                role="system",
                content=build_system_prompt(role="chat", user_context=prompt_ctx),
            ),
        ]
        if followup_hint_msg is not None:
            base_msgs.append(followup_hint_msg)
        messages: list[LLMMessage] = [
            *base_msgs,
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
        error_counts: dict[tuple[str, str], int] = {}
        _SAME_ERROR_LIMIT = 2

        while hop_index < _MAX_TOOL_CALLS:
            hop_index += 1
            hop_started = time.monotonic()
            # A1: only force tool_choice on hop 1; later hops MUST be
            # allowed to emit final text (otherwise the loop never ends).
            hop_tool_choice: Literal["auto", "required"] = (
                agent_tool_choice if hop_index == 1 else "auto"
            )
            trace.event(
                "llm.stream", hop=hop_index,
                reasoning_effort=effort, tools_offered=len(tooldefs),
                tool_choice=hop_tool_choice,
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
                tool_choice=hop_tool_choice,
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
                # Caption-augment for widgets — ensures a workflow_draft_card
                # / logic_card / backtest_chart never renders without a
                # short conversational lead-in.
                augmented = _ensure_widget_caption(
                    text,
                    tool_name=(tools_called[-1] if tools_called else ""),
                    logiccard=logiccard,
                    raw_data=raw_data,
                )
                if augmented != text:
                    sanitised = True
                    text = augmented
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
                    # A2 (streaming): 3-attempt cap on propose_workflow.
                    if guarded.name == "propose_workflow":
                        propose_workflow_attempts += 1
                        if propose_workflow_attempts >= _PROPOSE_WORKFLOW_MAX_ATTEMPTS:
                            # Streaming mirror of the non-streaming
                            # macro fallback. Same logic — surface a
                            # simplified draft instead of "couldn't
                            # do it".
                            fb_draft = _try_macro_fallback(message)
                            if fb_draft is not None:
                                fb_text = (
                                    "I couldn't fit your full request "
                                    "into a single workflow shape, so "
                                    "I've drafted a simplified version "
                                    "you can edit. The trigger has been "
                                    "set to manual — review the steps "
                                    "and adjust the trigger before "
                                    "activating."
                                )
                                self.store.append(conv_id, message, fb_text)
                                total = int((time.monotonic() - turn_started) * 1000)
                                breakdown["total"] = total
                                stream_raw_data = {
                                    **fb_draft,
                                    "propose_workflow": fb_draft,
                                    "_render_hint": "workflow_draft_card",
                                }
                                yield {"type": "tool_start", "name": "propose_holding_action"}
                                yield {"type": "tool_done", "name": "propose_holding_action", "ok": True}
                                yield {"type": "delta", "text": fb_text}
                                yield {
                                    "type": "done",
                                    "response": fb_text,
                                    "tools_called": tools_called + ["propose_holding_action"],
                                    "logiccard": None,
                                    "raw_data": stream_raw_data,
                                    "latency_ms": total,
                                    "latency_breakdown": breakdown,
                                }
                                _log_timing(
                                    client.provider_name, message, total,
                                    breakdown, tools=tools_called,
                                    note="stream-propose_workflow_macro_fallback",
                                )
                                trace.event(
                                    "turn.end", total_ms=total,
                                    tools_called=tools_called + ["propose_holding_action"],
                                    reason="propose_workflow_macro_fallback",
                                    attempts=propose_workflow_attempts,
                                )
                                trace.end()
                                return
                            question = _format_recoverable_failure_question(
                                tool_name=guarded.name,
                                error=guarded.error or "",
                            )
                            self.store.append(conv_id, message, question)
                            total = int((time.monotonic() - turn_started) * 1000)
                            breakdown["total"] = total
                            yield {"type": "delta", "text": question}
                            yield {
                                "type": "done",
                                "response": question,
                                "tools_called": tools_called + [guarded.name],
                                "logiccard": None,
                                "raw_data": {"_render_hint": "ask_user"},
                                "latency_ms": total,
                                "latency_breakdown": breakdown,
                            }
                            _log_timing(
                                client.provider_name, message, total, breakdown,
                                tools=tools_called,
                                note=(
                                    f"stream-propose_workflow_attempts="
                                    f"{propose_workflow_attempts}"
                                ),
                            )
                            trace.event(
                                "turn.end", total_ms=total,
                                tools_called=tools_called,
                                reason="propose_workflow_attempt_cap",
                                attempts=propose_workflow_attempts,
                            )
                            trace.end()
                            return
                    sig = (guarded.error or "")[:80]
                    key = (guarded.name, sig)
                    error_counts[key] = error_counts.get(key, 0) + 1
                    if error_counts[key] >= _SAME_ERROR_LIMIT:
                        question = _format_recoverable_failure_question(
                            tool_name=guarded.name, error=guarded.error or "",
                        )
                        self.store.append(conv_id, message, question)
                        total = int((time.monotonic() - turn_started) * 1000)
                        breakdown["total"] = total
                        yield {"type": "delta", "text": question}
                        yield {
                            "type": "done",
                            "response": question,
                            "tools_called": tools_called + [guarded.name],
                            "logiccard": None,
                            "raw_data": {"_render_hint": "ask_user"},
                            "latency_ms": total,
                            "latency_breakdown": breakdown,
                        }
                        _log_timing(
                            client.provider_name, message, total, breakdown,
                            tools=tools_called,
                            note=f"stream-same-error:{guarded.name}",
                        )
                        trace.event(
                            "turn.end", total_ms=total,
                            tools_called=tools_called,
                            reason="same_error_escalation",
                            tool=guarded.name, sig=sig,
                        )
                        trace.end()
                        return

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


def _workflow_skeleton_caption(skeleton: dict) -> str:
    """Conversational message that accompanies the workflow_draft_card.

    The widget alone is silent — without a few words of human prose
    above it, the chat feels jumpy: a card appears with no acknowledgment.
    Here we describe the proposed agent in one short paragraph: trigger,
    action, and what to do next. Always under 240 chars so it doesn't
    crowd the card.
    """
    steps = skeleton.get("steps") or []
    name = (skeleton.get("name") or "Agent draft").rstrip(".")
    trigger_step = next((s for s in steps if s.get("step_type", "").startswith("trigger.")), None)
    action_step = next((s for s in steps if s.get("step_type", "").startswith("action.")), None)

    when_phrase = "on its trigger"
    if trigger_step:
        cfg = trigger_step.get("config") or {}
        if trigger_step["step_type"] == "trigger.schedule":
            cron = (cfg.get("cron") or "").strip()
            # Render a friendly time from "MM HH * * DOW"
            parts = cron.split()
            if len(parts) == 5:
                mm, hh, _, _, dow = parts
                dow_label = {
                    "1-5": "every weekday",
                    "*": "every day",
                    "1": "every Monday", "2": "every Tuesday",
                    "3": "every Wednesday", "4": "every Thursday",
                    "5": "every Friday",
                }.get(dow, f"on cron `{cron}`")
                try:
                    when_phrase = f"{dow_label} at {int(hh):02d}:{int(mm):02d} IST"
                except ValueError:
                    when_phrase = f"on `{cron}`"
        elif trigger_step["step_type"] == "trigger.indicator":
            ind = (cfg.get("indicator") or "").upper()
            period = cfg.get("period")
            op = cfg.get("operator", "")
            val = cfg.get("value")
            op_word = {
                "<": "drops below", ">": "rises above",
                "crosses_above": "crosses above",
                "crosses_below": "crosses below",
            }.get(op, op)
            when_phrase = f"when {ind}({period}) {op_word} {val}"
        elif trigger_step["step_type"] == "trigger.price":
            sym = cfg.get("symbol", "")
            op = cfg.get("operator", "")
            val = cfg.get("value")
            op_word = {
                "<": "drops below ₹", ">": "rises above ₹",
                "crosses_above": "crosses above ₹",
                "crosses_below": "crosses below ₹",
            }.get(op, f"{op} ₹")
            when_phrase = f"when {sym} {op_word}{val:g}".rstrip()

    do_phrase = "places the configured order"
    if action_step and action_step["step_type"] == "action.place_order":
        cfg = action_step.get("config") or {}
        do_phrase = (
            f"{cfg.get('side', 'buy')}s {cfg.get('quantity', '')} "
            f"{cfg.get('symbol', '')} at {cfg.get('order_type', 'market')}"
        ).strip()

    return (
        f"Here's a draft for **{name}** — it {do_phrase} {when_phrase}. "
        "Review the steps below and click Activate when you're happy "
        "with it."
    )


def _try_macro_fallback(message: str) -> Optional[dict]:
    """Last-ditch hydration when propose_workflow has hit its 3-attempt
    cap. Pattern-matches the user's message against the four macros
    and returns a draft dict on hit, or None to fall through to the
    normal escalation message.

    The match logic is intentionally generous — we'd rather emit a
    *partial* draft the user can edit than show "I couldn't do it".
    The user has already seen 30+ seconds of the model trying; giving
    them a workable starting point beats restating the request.

    Strategy:
      - SL phrasing → propose_holding_action with
        action_kind=set_stoploss, trigger_kind=manual (the user runs
        the workflow when the conditions are met). Drops the trigger
        condition since the model couldn't fit it; the user can edit
        the trigger in the editor before activating.
      - Indicator threshold + qty → propose_threshold_order
      - Schedule + qty → propose_scheduled_order
    """
    import re
    from backend.services.workflow_macros import hydrate_and_validate

    msg = message.strip()
    if not msg:
        return None

    # Pattern A: stop-loss phrasing → holding_action with manual trigger.
    sl_match = re.search(
        r"(?P<pct>\d+(?:\.\d+)?)\s*%\s*(?:stop[- ]?loss|stop|sl|loss)\b",
        msg, re.IGNORECASE,
    )

    # Tokens we never want as a symbol — days of week, common verbs,
    # indicators, etc. The case-insensitive symbol regex would happily
    # grab "MONDAY" otherwise.
    _SYMBOL_BLOCKLIST = {
        "MONDAY", "TUESDAY", "WEDNESDAY", "THURSDAY", "FRIDAY",
        "SATURDAY", "SUNDAY",
        "MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN",
        "TODAY", "YESTERDAY", "TOMORROW",
        "RSI", "SMA", "EMA", "MACD", "SL", "TP", "MP",
        "NSE", "BSE", "AT", "OF", "ON", "IF", "TO", "FROM",
        "IT", "OR", "AND", "ELSE", "WHEN", "THEN", "WHILE",
        "BUY", "SELL", "PLACE", "SET", "ADD", "STOP", "LOSS",
        "AGENT", "STRATEGY", "WORKFLOW", "AUTOMATION",
        "MARKET", "LIMIT", "OPEN", "CLOSE", "HIGH", "LOW",
        "PRICE", "QUANTITY",
    }

    def _pick_symbol(text: str) -> Optional[str]:
        # Try anchored extraction first: "on my SYM" / "stop loss on SYM" /
        # "SL on SYM" / "set ... on SYM". The user often spells out an
        # explicit anchor when describing an SL on an existing holding.
        for pat in (
            r"\b(?:on\s+my\s+|stop\s*-?\s*loss\s+on\s+my\s+|sl\s+on\s+my\s+|"
            r"trailing\s+stop\s+on\s+my\s+)([A-Za-z][A-Za-z0-9\-_]{2,15})\b",
            r"\b(?:on|for)\s+([A-Za-z][A-Za-z0-9\-_]{2,15})\b",
        ):
            m = re.search(pat, text, re.IGNORECASE)
            if m:
                cand = m.group(1).upper()
                if cand not in _SYMBOL_BLOCKLIST:
                    return cand
        # Fallback: first ALL-CAPS token (real tickers are typed
        # uppercase by convention).
        for m in re.finditer(r"\b([A-Z][A-Z0-9\-_]{2,15})\b", text):
            cand = m.group(1)
            if cand not in _SYMBOL_BLOCKLIST:
                return cand
        # Last resort: any case-insensitive 3+ letter token.
        for m in re.finditer(r"\b([A-Za-z][A-Za-z0-9\-_]{2,15})\b", text):
            cand = m.group(1).upper()
            if cand not in _SYMBOL_BLOCKLIST:
                return cand
        return None

    if sl_match:
        symbol = _pick_symbol(msg)
        if symbol is None:
            return None
        try:
            return hydrate_and_validate("holding_action", {
                "symbol": symbol,
                "action_kind": "set_stoploss",
                "trigger_kind": "manual",
                "sl_offset_pct": float(sl_match.group("pct")),
            })
        except (ValueError, TypeError):
            return None

    # Other fallbacks could go here — for now SL is the most-asked.
    return None


def _format_recoverable_failure_question(*, tool_name: str, error: str) -> str:
    """User-facing question after a same-error retry escalation.

    Maps the most common structural failures into a focused ask rather
    than dumping the raw schema error. The previous behaviour ("Try
    rephrasing with the specific values you want") is too generic — the
    user has no idea which value to give. We can do better because we
    know which tool failed and roughly which field tripped the schema.
    """
    err_lc = (error or "").lower()
    if tool_name == "propose_workflow":
        if "trigger_price" in err_lc and "required" in err_lc:
            return (
                "I tried to draft that agent but the stop-loss step needs "
                "either an absolute trigger price (e.g. ₹1,420) or a "
                "percentage below entry (e.g. 2%). Which would you like "
                "to use?"
            )
        if "quantity" in err_lc:
            return (
                "I started drafting that agent but I need a quantity for "
                "the order step — how many shares per fire?"
            )
        if "cron" in err_lc or "schedule" in err_lc:
            return (
                "I drafted the agent but the schedule didn't parse — "
                "could you tell me the day(s) and time? e.g. 'every "
                "weekday at 09:15 IST'."
            )
        # Runtime-relative threshold ("5% below Monday's open" / "below
        # previous close" / "X% drop from yesterday"). Workflows v1
        # triggers all need static price levels — these references
        # require fetching Monday's open at fire time which the trigger
        # types don't support today. Rather than say "structural issue",
        # name the gap and offer the two viable alternatives.
        if any(
            tok in err_lc
            for tok in ("operator", "value", "trigger.price",
                        "trigger.indicator", "trigger.event")
        ) or any(
            tok in err_lc for tok in ("input should be", "extra inputs", "literal_error")
        ):
            return (
                "I tried that draft a few times but couldn't fit it into "
                "Pivot's trigger types — those need a fixed price level "
                "or a fixed indicator threshold (RSI < 30, EMA cross, "
                "etc.). Two ways to express what you want:\n"
                "  • Pick an absolute price (e.g. *trigger when RELIANCE "
                "drops below ₹2,800*), or\n"
                "  • Use a daily-checkpoint shape — *every weekday at "
                "09:30, if price is more than 5% below the day's open, "
                "set a 2% stop loss* — and I'll wire that up.\n"
                "Which would you like?"
            )
        return (
            "I started drafting that agent but the trigger or action "
            "didn't fit Pivot's step catalog. Could you restate it as a "
            "single sentence — *when X, do Y* — using a concrete price "
            "level, time, or indicator threshold?"
        )
    if tool_name in {"place_market_order", "place_limit_order"}:
        return (
            "I couldn't place that order from what was given — could you "
            "confirm the symbol, quantity, and (for limit orders) the "
            "limit price?"
        )
    return (
        f"I tried `{tool_name}` twice and the same input issue came back. "
        "Could you restate that with specific values?"
    )


def _tool_summary_line(tool_name: str, logiccard: dict | None) -> str:
    """One-liner used when the post-processor stripped the LLM's
    narration but a tool actually produced a card."""
    if tool_name == "propose_workflow":
        return (
            "Here's a draft of that agent — the trigger, action(s), and "
            "any conditions are laid out below. Review and click Activate "
            "when you're happy."
        )
    if logiccard:
        action = logiccard.get("action") or ""
        symbol = logiccard.get("symbol") or ""
        qty = logiccard.get("quantity") or logiccard.get("qty") or ""
        if action and symbol:
            qty_part = f" {qty}" if qty else ""
            return (
                f"Here's a {action}{qty_part} {symbol} order ready to go — "
                "the card below shows the full details. Click Confirm when "
                "ready."
            )
        return (
            "I've prepared the action for you — review the card below and "
            "click Confirm to send it through."
        )
    if tool_name.startswith("get_") or tool_name.startswith("list_"):
        return "Here's what I found — the details are in the card below."
    return f"Done — `{tool_name}` ran. The result is shown below."


# Render hints whose widgets need an accompanying conversational caption
# in the assistant text. If the LLM produced no text (or only a placeholder
# we sanitised), we synthesise one rather than leaving the widget mute.
_WIDGET_RENDER_HINTS = frozenset({
    "workflow_draft_card",
    "logic_card",
    "indicator_backtest_chart",
    "financial_backtest_chart",
})


def _ensure_widget_caption(
    text: str,
    *,
    tool_name: str,
    logiccard: dict | None,
    raw_data: dict,
) -> str:
    """Make sure assistant text accompanies any widget render.

    The chat pattern is `text + widget`, never `widget alone`. When the
    LLM:
      - emitted no text → synthesise one matching the widget kind.
      - emitted a single-word affirmation ("done", "okay") → upgrade
        to a descriptive line.
      - emitted a full sentence → leave it; the model already nailed it.

    Returns the (possibly-upgraded) text. Never empty.
    """
    # Inside chat_service, raw_data is keyed by tool_name and the
    # _render_hint lives nested. The router hoists it later. Look both
    # places so this helper is correct regardless of caller.
    rd = raw_data or {}
    render_hint = rd.get("_render_hint")
    if not render_hint:
        for v in rd.values():
            if isinstance(v, dict) and v.get("_render_hint"):
                render_hint = v["_render_hint"]
                break
    if render_hint not in _WIDGET_RENDER_HINTS and not logiccard:
        return text

    cleaned = (text or "").strip()
    too_terse = (
        not cleaned
        or len(cleaned) < 12
        or cleaned.lower() in {
            "done", "ok", "okay", "sure", "got it", "yes", "no",
            _GENERIC_FALLBACK.lower().rstrip("?."),
        }
    )
    if not too_terse:
        return cleaned

    # Synthesise per-widget caption.
    if render_hint == "workflow_draft_card":
        skeleton = rd.get("propose_workflow") or rd
        if isinstance(skeleton, dict) and skeleton.get("steps"):
            return _workflow_skeleton_caption(skeleton)
        return _tool_summary_line("propose_workflow", None)
    if render_hint == "indicator_backtest_chart":
        return (
            "Here's the backtest — equity curve, signals, and headline "
            "metrics are in the chart below."
        )
    if render_hint == "financial_backtest_chart":
        return (
            "Here's the fundamentals backtest — performance vs. NIFTY and "
            "the rebalance trades are below."
        )
    if logiccard or render_hint == "logic_card":
        return _tool_summary_line(tool_name or "", logiccard)
    return _tool_summary_line(tool_name or "", logiccard)


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
