"""Pure transition function for the v2 chat state machine.

`transition(ctx, event) -> ctx'` is a pure function: same inputs,
same outputs, no side effects, no LLM calls, no Redis access.

The pipeline orchestrates by:
    1. classify(message, ctx)   -> Event (pre-LLM)
    2. transition(ctx, event)   -> new ctx (pre-LLM state)
    3. run LLM with policy[ctx.state]
    4. for each ToolEmitted / ClarificationAsked from LLM:
           transition(ctx, event) -> ctx
    5. persist ctx

This file is the spec. Every conversation behavior MUST be a
transition rule named here. New phenomena get a new event type and
a new rule, never an inline regex inside chat_service.

Run unit tests:  pytest tests/chat_v2/test_transitions.py
"""
from __future__ import annotations

from dataclasses import replace
from typing import Optional

from backend.chat_v2.events import (
    ActivationConfirmed,
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
from backend.chat_v2.state import (
    MACRO_TOOL,
    TOOL_TO_KIND,
    ConvContext,
    ConvState,
    MacroKind,
)


# Tools that produce a draft when emitted (i.e. their emission
# transitions us into DRAFTING with the corresponding macro_kind).
_DRAFT_PRODUCING_TOOLS = frozenset(TOOL_TO_KIND.keys())


# Read-only tools — emission stays in EXPLORING and updates last_tool.
_READ_TOOLS = frozenset({
    "get_live_price", "get_stock_quote", "get_stock_history", "get_ohlc",
    "get_index_level", "get_top_movers", "get_market_status",
    "get_portfolio_summary", "get_holdings", "get_holding_detail",
    "get_sector_breakdown", "get_tax_summary", "get_active_products",
    "get_indicator", "get_multiple_indicators", "get_returns",
    "get_performance_metrics", "compare_performance",
    "get_correlation_matrix",
    "list_pending_orders", "list_gtt_orders", "list_strategies",
    "list_workflows", "list_sips", "list_baskets",
})


# Mode override → macro kind mapping.
_MODE_TO_KIND: dict[str, MacroKind] = {
    "agent": MacroKind.WORKFLOW,
    "automation": MacroKind.SCHEDULED,
    "backtest": MacroKind.BACKTEST,
}


def transition(ctx: ConvContext, event: Event) -> ConvContext:
    """Apply one event to the context and return the new context.

    Pure function — never mutates `ctx` in place; always returns a
    `replace`-ed copy. Make sure the dataclass fields you care about
    are listed in the replace() call.
    """

    # ─── Turn boundaries ─────────────────────────────────────────

    if isinstance(event, TurnStart):
        # Fresh-session eviction: empty history from FE means a new
        # chat under the same conv_id; wipe everything.
        if event.history_is_empty:
            return replace(
                ctx,
                state=ConvState.IDLE,
                macro_kind=None,
                macro_tool=None,
                macro_draft=None,
                draft_summary=None,
                pending_clarification_text=None,
                last_tool=None,
                last_tool_args={},
                discarded_drafts=[],
                turn_count=0,
            )
        # Bump turn count and age the discarded-drafts TTL.
        new_ctx = replace(ctx, turn_count=ctx.turn_count + 1)
        new_ctx.age_discarded()
        return new_ctx

    if isinstance(event, TurnEnd):
        # Hook for pipeline-level cleanup — currently a no-op,
        # but we route through here so future changes have a place.
        return ctx

    # ─── Pre-LLM mode override ───────────────────────────────────

    if isinstance(event, ModeOverride):
        # Pill click — force into DRAFTING with the chosen macro_kind.
        # Drops any in-flight draft of a different shape into discarded.
        kind = _MODE_TO_KIND.get(event.mode)
        if kind is None:
            return ctx
        if ctx.state == ConvState.DRAFTING and ctx.macro_kind != kind:
            new_ctx = replace(ctx)
            new_ctx.push_discarded(
                ctx.macro_kind,
                ctx.macro_tool,
                ctx.draft_summary or f"prior {ctx.macro_kind.value if ctx.macro_kind else ''} draft",
            )
            return replace(
                new_ctx,
                state=ConvState.DRAFTING,
                macro_kind=kind,
                macro_tool=MACRO_TOOL.get(kind),
                macro_draft=None,
                draft_summary=None,
                pending_clarification_text=None,
            )
        return replace(
            ctx,
            state=ConvState.DRAFTING,
            macro_kind=kind,
            macro_tool=MACRO_TOOL.get(kind),
            pending_clarification_text=None,
        )

    # ─── Pre-LLM classification events ───────────────────────────

    if isinstance(event, CancelIntent):
        # Cancel from any state → CANCELLED, drop the draft.
        return replace(
            ctx,
            state=ConvState.CANCELLED,
            macro_kind=None,
            macro_tool=None,
            macro_draft=None,
            draft_summary=None,
            pending_clarification_text=None,
        )

    if isinstance(event, AffirmativeAck):
        # Stays in current state. The pipeline decides whether to
        # short-circuit (DRAFTING → fast ack) or let the LLM resolve
        # (AWAITING_CLARIFICATION → merge with prior ask).
        return ctx

    if isinstance(event, FillerReply):
        # Filler never amends; stays in current state.
        return ctx

    if isinstance(event, CapabilityQuestion):
        # Capability questions stay in EXPLORING and must NEVER push
        # us into DRAFTING — even if there's a build-shaped phrase
        # in the message ("can I build an agent?").
        if ctx.state == ConvState.IDLE:
            return replace(ctx, state=ConvState.EXPLORING)
        # If currently DRAFTING, capability questions don't evict;
        # the user is asking about the system, not changing topic.
        return ctx

    if isinstance(event, IndependentIntent):
        # Mid-draft topic shift → push draft to discarded, return
        # to EXPLORING. The LLM hop will then run with EXPLORING's
        # tool palette (read-only) and answer the new question.
        if ctx.state == ConvState.DRAFTING:
            new_ctx = replace(ctx)
            new_ctx.push_discarded(
                ctx.macro_kind,
                ctx.macro_tool,
                ctx.draft_summary or "prior draft",
            )
            return replace(
                new_ctx,
                state=ConvState.EXPLORING,
                macro_kind=None,
                macro_tool=None,
                macro_draft=None,
                draft_summary=None,
            )
        # Mid-clarification topic shift also evicts.
        if ctx.state == ConvState.AWAITING_CLARIFICATION:
            return replace(
                ctx,
                state=ConvState.EXPLORING,
                pending_clarification_text=None,
            )
        return replace(ctx, state=ConvState.EXPLORING)

    if isinstance(event, Amendment):
        # User edited the draft; stay in DRAFTING. The policy layer
        # will pin tool_choice to the macro_tool currently on screen.
        return ctx

    if isinstance(event, ClarificationAnswer):
        # User answered the bot's question. Stay in
        # AWAITING_CLARIFICATION — the LLM hop with the
        # AWAITING_CLARIFICATION policy will resolve and likely
        # transition us into DRAFTING via a ToolEmitted event.
        return ctx

    if isinstance(event, BuildIntent):
        # Push into DRAFTING with the LIKELY macro kind. The LLM
        # may emit a different macro tool (causing a ToolEmitted
        # transition that updates macro_kind precisely), but pinning
        # the right palette upfront prevents the v1 "model picks
        # propose_threshold_order when we wanted propose_workflow"
        # coin flip.
        likely_kind = (
            MacroKind.WORKFLOW if event.likely_macro == "workflow"
            else MacroKind.ORDER if event.likely_macro == "order"
            else None
        )
        return replace(
            ctx,
            state=ConvState.DRAFTING,
            macro_kind=likely_kind,
            macro_tool=MACRO_TOOL.get(likely_kind) if likely_kind else None,
            pending_clarification_text=None,
        )

    if isinstance(event, ReadIntent):
        # Read intents stay in EXPLORING (default). If we were
        # IDLE, advance into EXPLORING.
        if ctx.state == ConvState.IDLE:
            return replace(ctx, state=ConvState.EXPLORING)
        # If we were ACTIVATED / CANCELLED, transition to EXPLORING.
        if ctx.state in (ConvState.ACTIVATED, ConvState.CANCELLED):
            return replace(ctx, state=ConvState.EXPLORING)
        return ctx

    # ─── LLM-driven events ───────────────────────────────────────

    if isinstance(event, ToolEmitted):
        tool = event.tool_name
        # Draft-producing tool → DRAFTING with that macro kind.
        if tool in _DRAFT_PRODUCING_TOOLS:
            kind = TOOL_TO_KIND[tool]
            new_ctx = replace(
                ctx,
                state=ConvState.DRAFTING,
                macro_kind=kind,
                macro_tool=tool,
                macro_draft=event.args,
                # Compose a one-line summary for compact-prose / discarded.
                draft_summary=_summarize_draft(tool, event.args),
                pending_clarification_text=None,
            )
            # Capture any symbol mentioned in the draft for focus_stack.
            sym = _extract_symbol(event.args)
            if sym:
                new_ctx.push_focus(sym)
            return new_ctx
        # ASK_USER — clarification request from the LLM.
        if tool == "ASK_USER":
            return replace(
                ctx,
                state=ConvState.AWAITING_CLARIFICATION,
                pending_clarification_text=str(event.args.get("question", ""))[:300],
            )
        # Read-only tool — record last_tool / last_tool_args for
        # follow-up inheritance ("what about TCS?" should reuse RSI).
        if tool in _READ_TOOLS:
            new_ctx = replace(
                ctx,
                last_tool=tool,
                last_tool_args=dict(event.args),
            )
            # If we were IDLE, move to EXPLORING.
            if ctx.state == ConvState.IDLE:
                new_ctx = replace(new_ctx, state=ConvState.EXPLORING)
            sym = _extract_symbol(event.args)
            if sym:
                new_ctx.push_focus(sym)
            return new_ctx
        # Unknown tool — leave state unchanged but update last_tool
        # so the policy layer doesn't infer a stale tool palette.
        return replace(ctx, last_tool=tool)

    if isinstance(event, ClarificationAsked):
        # LLM emitted a free-form question without using ASK_USER.
        # Same effect as an ASK_USER tool call.
        return replace(
            ctx,
            state=ConvState.AWAITING_CLARIFICATION,
            pending_clarification_text=event.question_text[:300],
        )

    if isinstance(event, TextResponse):
        # Plain text response — does not change state by itself.
        return ctx

    # ─── Out-of-turn events ──────────────────────────────────────

    if isinstance(event, ActivationConfirmed):
        # FE clicked Save & Activate.
        new_ctx = replace(ctx)
        new_ctx.activations.insert(0, event.summary[:200])
        new_ctx.activations = new_ctx.activations[:10]  # cap
        return replace(
            new_ctx,
            state=ConvState.ACTIVATED,
            macro_kind=None,
            macro_tool=None,
            macro_draft=None,
            draft_summary=None,
        )

    # Unknown event — fail loud in dev, no-op in prod.
    return ctx


# ─────────────────────── Helpers ──────────────────────────────────


def _extract_symbol(args: dict) -> Optional[str]:
    """Pull a single ticker symbol out of a tool's args, if any.
    Used to update the focus stack on read tools / draft tools."""
    if not isinstance(args, dict):
        return None
    sym = args.get("symbol") or args.get("ticker")
    if isinstance(sym, str) and 1 < len(sym) <= 12 and sym.upper() == sym.replace(" ", ""):
        return sym
    return None


def _summarize_draft(tool: str, args: dict) -> str:
    """One-line description of a draft for discarded-history /
    compact-prose use. Best-effort; falls back to the tool name."""
    if not isinstance(args, dict):
        return tool
    sym = args.get("symbol") or ""
    qty = args.get("quantity") or args.get("qty") or ""
    side = args.get("side") or ""
    if tool == "propose_workflow":
        name = args.get("name") or ""
        steps_count = len(args.get("steps") or [])
        return f"workflow '{name}' ({steps_count} steps)" if name else f"workflow ({steps_count} steps)"
    if tool == "propose_basket_allocation":
        symbols = args.get("symbols") or []
        n = len(symbols) if isinstance(symbols, list) else "?"
        return f"basket of {n} symbols"
    if sym:
        bits = [str(sym)]
        if side:
            bits.append(str(side))
        if qty:
            bits.append(f"qty={qty}")
        return f"{tool}({', '.join(bits)})"
    return tool
