"""The v2 chat pipeline. One async function (`process_turn`) drives
the entire flow:

    1. Load ConvContext
    2. Apply TurnStart event
    3. Apply ModeOverride event if FE pill is set
    4. Classify the user message → pre-LLM event
    5. Apply that event via transition() → new state
    6. SHORT-CIRCUITS (no LLM):
         - FillerReply           → brief ack
         - AffirmativeAck idle   → "Got it. What would you like next?"
         - AffirmativeAck draft  → "Got it — the draft above…"
         - CancelIntent          → "Cleared."
       (Affirmative in clarify, capability questions, build/order intents,
        and read intents all go to the LLM hop with the right policy.)
    7. LLM HOP:
         - Look up policy_for(ctx)
         - Build messages: base + state_block + history + user
         - Filter tools to policy.tools
         - Call LLM (agentic loop, max 4 hops)
         - Each tool call: execute, apply ToolEmitted, possibly state change
         - Final text response: ensure widget caption, return
    8. Persist ConvContext

The pipeline is a single source of truth — both `/chat/v2` (non-streaming)
and `/chat/stream/v2` (streaming) consume `process_turn`'s return values.
No duplicated `handle()` / `handle_stream()` drift.
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Optional, Tuple

from backend.cache import redis_client
from backend.chat_v2 import classifiers
from backend.chat_v2.events import (
    AffirmativeAck,
    Amendment,
    BuildIntent,
    CancelIntent,
    CapabilityQuestion,
    ClarificationAnswer,
    ClarificationAsked,
    Event,
    FillerReply,
    IndependentIntent,
    ModeOverride,
    ReadIntent,
    TextResponse,
    ToolEmitted,
    TurnEnd,
    TurnStart,
)
from backend.chat_v2.policies import StatePolicy, policy_for
from backend.chat_v2.state import ConvContext, ConvState
from backend.chat_v2.store import load_context, save_context
from backend.chat_v2.transitions import transition

from backend.agents.tool_executor import execute_tool
from backend.llm.base import LLMMessage, ToolDef
from backend.llm.openai_client import LLMOpenAI
from backend.services.chat_service import UserContext
from backend.services.conversation_store import ConversationStore
from backend.services.tool_registry import get_tool_schema

logger = logging.getLogger(__name__)


# ──────────────────────────── Result type ────────────────────────────


@dataclass
class V2TurnResult:
    """What the pipeline returns. Same shape as v1 ChatTurn so the
    router can serialise without a translation layer."""
    response: str
    tools_called: list[str] = field(default_factory=list)
    raw_data: dict = field(default_factory=dict)
    logiccard: Optional[dict] = None
    latency_ms: int = 0
    latency_breakdown: dict = field(default_factory=dict)
    state: str = "idle"           # diagnostic: end-of-turn state label
    final_state: str = "idle"     # alias


# ──────────────────────────── Constants ─────────────────────────────


_MAX_TOOL_HOPS = 4

_FILLER_ACK = "Anytime."
_AFFIRMATIVE_DRAFT_ACK = (
    "Got it — the draft above is what you'll activate. "
    "Click **Save & activate** in the card when you're ready."
)
_AFFIRMATIVE_NO_DRAFT_ACK = "Got it. What would you like next?"
_CANCEL_ACK = "Cleared. What would you like to do next?"


# ──────────────────────────── Public entry ──────────────────────────


async def process_turn(
    *,
    message: str,
    conv_id: str,
    user_ctx: UserContext,
    history_override: Optional[list[dict]] = None,
    mode_override: Optional[str] = None,
    conv_store: Optional[ConversationStore] = None,
    llm_factory: Optional[Any] = None,
) -> V2TurnResult:
    """Run one turn of v2 chat. Returns a V2TurnResult.

    Side effects (intentional):
      - Loads / saves ConvContext to Redis
      - Appends user/assistant messages to ConversationStore history
    """

    t_total_start = time.monotonic()
    breakdown: dict[str, int] = {}

    store = conv_store or ConversationStore()
    history_is_empty = (history_override is not None and len(history_override) == 0)

    # ── 1. Load context ───────────────────────────────────────────
    ctx = load_context(conv_id)

    # ── 2. TurnStart ──────────────────────────────────────────────
    ctx = transition(
        ctx,
        TurnStart(
            user_message=message,
            mode_override=mode_override,  # type: ignore[arg-type]
            history_is_empty=history_is_empty,
        ),
    )

    # ── 3. ModeOverride (pill click) ──────────────────────────────
    if mode_override in ("agent", "automation", "backtest"):
        ctx = transition(ctx, ModeOverride(mode=mode_override))  # type: ignore[arg-type]

    # ── 4-5. Classify + transition ────────────────────────────────
    classified_event = classifiers.classify(message, ctx)
    ctx = transition(ctx, classified_event)

    # ── 6. Short-circuits (no LLM) ────────────────────────────────
    sc = _shortcircuit_response(ctx, classified_event)
    if sc is not None:
        text, raw_data, logiccard = sc
        # Persist state and history.
        store.append(conv_id, message, text)
        save_context(ctx)
        ms = int((time.monotonic() - t_total_start) * 1000)
        breakdown["shortcircuit"] = ms
        breakdown["total"] = ms
        return V2TurnResult(
            response=text,
            tools_called=[],
            raw_data=raw_data,
            logiccard=logiccard,
            latency_ms=ms,
            latency_breakdown=breakdown,
            state=ctx.state.value,
            final_state=ctx.state.value,
        )

    # ── 7. LLM hop ────────────────────────────────────────────────
    policy = policy_for(ctx)

    # Build history snapshot
    if history_override is not None:
        history = history_override[-20:]  # keep last 10 turns
    else:
        history = store.get_history(conv_id, limit=10)

    # Build messages
    messages: list[LLMMessage] = [
        LLMMessage(role="system", content=policy.system_block),
    ]
    # Inject context-specific facts (focus stack, last_tool, discarded drafts)
    facts_block = _facts_block(ctx)
    if facts_block:
        messages.append(LLMMessage(role="system", content=facts_block))
    # History
    for h in history:
        role = h.get("role", "user")
        content = h.get("content") or ""
        if role in ("user", "assistant", "system"):
            messages.append(LLMMessage(role=role, content=content))  # type: ignore[arg-type]
    # Current user message
    messages.append(LLMMessage(role="user", content=message))

    # Filter tools
    tools = _filter_tools(policy.tools)

    # Spin up the LLM client
    llm = (llm_factory() if llm_factory else LLMOpenAI())

    # Agentic loop
    tools_called: list[str] = []
    raw_data: dict = {}
    logiccard: Optional[dict] = None
    response_text: str = ""

    for hop in range(_MAX_TOOL_HOPS):
        # tool_choice: respect policy on first hop only; subsequent
        # hops let the model emit text or chain.
        if hop == 0:
            tc_in = policy.tool_choice
        else:
            tc_in = "auto"
        # Coerce to literal types LLMOpenAI accepts (dict-form
        # tool_choice not currently wired; collapse to 'auto').
        if isinstance(tc_in, dict):
            tc = "auto"
        else:
            tc = tc_in  # type: ignore[assignment]

        t_hop = time.monotonic()
        resp = await llm.complete(
            messages=messages,
            tools=tools,
            tool_choice=tc,
            max_output_tokens=policy.max_output_tokens,
            reasoning_effort=policy.reasoning_effort,
            prompt_cache_key=policy.cache_key,
        )
        breakdown[f"hop_{hop}"] = int((time.monotonic() - t_hop) * 1000)

        if resp.finish_reason == "error":
            response_text = (
                "I'm having trouble reaching the model right now — "
                "please try again in a moment."
            )
            break

        # If the model emitted tool calls, execute them and append
        # results back into the message history for the next hop.
        if resp.tool_calls:
            # Append assistant message bearing the tool_calls
            messages.append(LLMMessage(
                role="assistant",
                content=resp.content or "",
                tool_calls=resp.tool_calls,
            ))
            for tc_obj in resp.tool_calls:
                # Tool calls are dicts: {id, name, arguments(dict)}
                tool_name = tc_obj.get("name", "")
                args = tc_obj.get("arguments") or {}
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except Exception:
                        args = {}
                tc_id = tc_obj.get("id", "")

                tools_called.append(tool_name)

                # Apply ToolEmitted transition BEFORE executing — so the
                # state reflects the LLM's intent regardless of whether
                # the tool execution succeeds.
                ctx = transition(ctx, ToolEmitted(
                    tool_name=tool_name, args=args,
                ))

                # Execute the tool. ASK_USER is synthetic — no
                # executor; the assistant text carries the question.
                if tool_name == "ASK_USER":
                    result_payload = {
                        "success": True,
                        "data": {"question": args.get("question", "")},
                    }
                else:
                    try:
                        exec_result = await execute_tool(
                            tool_name, args,
                            user_ctx.kite_token, user_ctx.db, user_ctx.user_id,
                        )
                        result_payload = exec_result if isinstance(exec_result, dict) else {"data": exec_result}
                    except Exception as e:
                        result_payload = {"error": str(e)[:200], "success": False}

                # Capture raw_data (for FE rendering)
                if isinstance(result_payload, dict):
                    raw_data[tool_name] = result_payload
                # If the tool returned a logiccard, keep it
                if isinstance(result_payload, dict) and "logiccard" in result_payload:
                    logiccard = result_payload["logiccard"]

                # Append tool_result back to the conversation
                messages.append(LLMMessage(
                    role="tool",
                    content=json.dumps(result_payload, default=str)[:4000],
                    tool_call_id=tc_id,
                ))
            # Continue the loop — model gets to chain or finalise.
            continue

        # No tool calls — finalise.
        response_text = resp.content or ""
        break
    else:
        # Hit MAX_TOOL_HOPS. Surface a graceful message.
        response_text = (
            response_text
            or "I needed to look up several things — could you ask "
            "again with more specifics?"
        )

    # Sanitise model text (reuse v1's strip-leakage rules)
    response_text = _sanitise(response_text)
    if not response_text and tools_called:
        response_text = _summarise_tool_call(tools_called[-1], raw_data)

    # ── 8. Persist + return ──────────────────────────────────────
    ctx = transition(ctx, TurnEnd(
        response_text=response_text, tools_called=tools_called,
    ))
    store.append(conv_id, message, response_text)
    save_context(ctx)

    ms = int((time.monotonic() - t_total_start) * 1000)
    breakdown["total"] = ms

    return V2TurnResult(
        response=response_text,
        tools_called=tools_called,
        raw_data=raw_data,
        logiccard=logiccard,
        latency_ms=ms,
        latency_breakdown=breakdown,
        state=ctx.state.value,
        final_state=ctx.state.value,
    )


# ──────────────────────────── Helpers ───────────────────────────────


def _shortcircuit_response(
    ctx: ConvContext, event: Event,
) -> Optional[Tuple[str, dict, Optional[dict]]]:
    """Return (text, raw_data, logiccard) if this turn can be
    answered without an LLM hop, or None if we need to call the model.

    Order of checks mirrors the priority list in the design doc."""

    # Cancel always short-circuits.
    if isinstance(event, CancelIntent):
        return _CANCEL_ACK, {"_render_hint": "cancelled"}, None

    # Filler reply → minimal ack, no draft re-emit.
    if isinstance(event, FillerReply):
        return _FILLER_ACK, {}, None

    # Affirmative depends on state.
    if isinstance(event, AffirmativeAck):
        if ctx.state == ConvState.DRAFTING and ctx.macro_draft is not None:
            return _AFFIRMATIVE_DRAFT_ACK, {}, None
        if ctx.state == ConvState.AWAITING_CLARIFICATION:
            # Do NOT short-circuit — fall through to LLM so the
            # followup hint can merge "yes" with the prior ask.
            return None
        # Other states (IDLE, EXPLORING, ACTIVATED, CANCELLED) with no
        # draft and no clarification → neutral ack.
        return _AFFIRMATIVE_NO_DRAFT_ACK, {}, None

    return None


def _facts_block(ctx: ConvContext) -> str:
    """Per-turn facts injected after the system block (NOT cached —
    this changes per turn). Compact, one section.

    Surfaces:
      - focus_symbols (rolling 3, for pronoun resolution)
      - last_tool + args (for follow-up inheritance)
      - discarded_drafts (for honest recall)
      - active draft summary (so amendments anchor on it)
      - pending clarification (for CLARIFY state context)
    """
    bits: list[str] = []
    if ctx.focus_symbols:
        bits.append("focus_symbols (recent → older): " + ", ".join(ctx.focus_symbols))
    if ctx.last_tool:
        if ctx.last_tool_args:
            args_short = json.dumps(ctx.last_tool_args, default=str)[:200]
            bits.append(f"last_tool: {ctx.last_tool}({args_short})")
        else:
            bits.append(f"last_tool: {ctx.last_tool}")
    if ctx.draft_summary:
        bits.append(f"active_draft: {ctx.draft_summary}")
    if ctx.pending_clarification_text:
        bits.append(
            f"pending_clarification: {ctx.pending_clarification_text}"
        )
    if ctx.discarded_drafts:
        bits.append(
            "discarded_drafts (recent → older): "
            + " | ".join(d.summary for d in ctx.discarded_drafts[:3])
        )
    if ctx.activations:
        bits.append("recently_activated: " + " | ".join(ctx.activations[:3]))
    if not bits:
        return ""
    return "## Conversation context (per-turn)\n" + "\n".join(f"- {b}" for b in bits)


def _filter_tools(names: tuple[str, ...]) -> list[ToolDef]:
    """Return ToolDefs whose function name is in `names`.
    ASK_USER is synthetic — we add it manually.
    Output type matches what LLMOpenAI.complete() expects."""
    all_schemas = get_tool_schema()
    name_set = set(names)
    out: list[ToolDef] = []
    for defn in all_schemas:
        fn = defn.get("function") or {}
        nm = fn.get("name", "")
        if nm in name_set:
            out.append(ToolDef(
                name=nm,
                description=fn.get("description", ""),
                parameters=fn.get("parameters") or {},
            ))
    if "ASK_USER" in name_set:
        out.append(ToolDef(
            name="ASK_USER",
            description="Ask the user a brief clarifying question. Use only when truly ambiguous.",
            parameters={
                "type": "object",
                "properties": {
                    "question": {"type": "string"},
                },
                "required": ["question"],
                "additionalProperties": False,
            },
        ))
    return out


def _sanitise(text: str) -> str:
    """Reuse v1's reasoning-leak stripper. Imported lazily to avoid
    a hard dependency on chat_service at module load."""
    if not text:
        return ""
    try:
        from backend.services.chat_service import _strip_reasoning_leakage
        return _strip_reasoning_leakage(text).strip()
    except Exception:
        return text.strip()


def _summarise_tool_call(tool_name: str, raw_data: dict) -> str:
    """One-line caption for a tool result when the LLM didn't emit
    text (e.g. compact-prose mode). Best-effort."""
    if tool_name in ("propose_workflow", "propose_threshold_order",
                     "propose_scheduled_order", "propose_basket_allocation",
                     "propose_holding_action"):
        return "Drafted. Review the card and click **Save & activate** when ready."
    if tool_name in ("place_market_order", "place_limit_order",
                     "create_gtt_order", "create_sl_order",
                     "create_oco_order", "create_sip"):
        return "Order card created. Review and confirm to execute."
    return f"Done — {tool_name} ran. Result is shown below."
