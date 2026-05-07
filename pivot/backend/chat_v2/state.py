"""Conversation state for the v2 chat pipeline.

The v1 chat_service had an implicit state machine encoded in scattered
regex matches and Redis key checks. Each bug we fixed was a missed
phase transition patched with another regex. This module makes the
state machine explicit so transitions are testable, the LLM's policy
per state is auditable, and adding new behavior means writing a new
transition rather than another shortcircuit.

Six conversation states cover everything the v1 implicit machine was
trying to encode:

    IDLE                     fresh — first turn, nothing pending
    EXPLORING                user asking questions / fetching data
    DRAFTING                 macro draft on screen, awaiting amendment / activation
    AWAITING_CLARIFICATION   bot asked, waiting for user's answer
    ACTIVATED                workflow live — conv continues but draft is no longer mutable
    CANCELLED                recently scratched — resurrection guard

The macro_kind in DRAFTING tells the policy layer which tool palette
to expose. We deliberately discriminate at this level rather than
letting the LLM choose among five overlapping macros at runtime —
that overlap was the dominant source of "feels random" routing in v1.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class ConvState(str, Enum):
    """High-level conversation phase. The state at turn start
    determines the policy (tool palette, system prompt block,
    tool_choice). The state at turn end is persisted to Redis."""

    IDLE = "idle"
    EXPLORING = "exploring"
    DRAFTING = "drafting"
    AWAITING_CLARIFICATION = "awaiting_clarification"
    ACTIVATED = "activated"
    CANCELLED = "cancelled"


class MacroKind(str, Enum):
    """The kind of macro draft on screen when state == DRAFTING.
    Used by the policy layer to pin the tool palette to ONE macro
    tool — eliminates the "model picks propose_threshold_order
    instead of propose_workflow" coin flip that plagued v1.

    Single-step orders are collapsed into one ORDER kind because
    the FE renders them as the same logiccard; the macro tool that
    re-emits is the one currently on screen (tracked in macro_tool)."""

    WORKFLOW = "workflow"          # propose_workflow
    THRESHOLD = "threshold"        # propose_threshold_order
    SCHEDULED = "scheduled"        # propose_scheduled_order
    BASKET = "basket"              # propose_basket_allocation
    HOLDING = "holding"            # propose_holding_action
    ORDER = "order"                # place_market / place_limit / create_gtt / create_sl / create_oco / create_sip
    BACKTEST = "backtest"          # run_workflow_backtest


# Canonical macro tool name per kind. The pipeline uses this to pin
# tool_choice when amending a draft of that kind.
MACRO_TOOL: dict[MacroKind, str] = {
    MacroKind.WORKFLOW: "propose_workflow",
    MacroKind.THRESHOLD: "propose_threshold_order",
    MacroKind.SCHEDULED: "propose_scheduled_order",
    MacroKind.BASKET: "propose_basket_allocation",
    MacroKind.HOLDING: "propose_holding_action",
    MacroKind.BACKTEST: "run_workflow_backtest",
    # ORDER kind doesn't have a single canonical tool — the active
    # macro_tool field on the context tracks which order shape is on
    # screen (place_market_order vs place_limit_order vs create_sl_order).
}


# Reverse map for classifying which kind a tool emission produces.
# Built from MACRO_TOOL above plus the order-tool family.
TOOL_TO_KIND: dict[str, MacroKind] = {
    "propose_workflow": MacroKind.WORKFLOW,
    "propose_threshold_order": MacroKind.THRESHOLD,
    "propose_scheduled_order": MacroKind.SCHEDULED,
    "propose_basket_allocation": MacroKind.BASKET,
    "propose_holding_action": MacroKind.HOLDING,
    "run_workflow_backtest": MacroKind.BACKTEST,
    "place_market_order": MacroKind.ORDER,
    "place_limit_order": MacroKind.ORDER,
    "create_gtt_order": MacroKind.ORDER,
    "create_sl_order": MacroKind.ORDER,
    "create_oco_order": MacroKind.ORDER,
    "create_sip": MacroKind.ORDER,
}


@dataclass
class DiscardedDraft:
    """A draft that was evicted by an independent-intent transition.

    Kept for ~3 turns so the bot can honestly answer "show me the
    workflow you were drafting earlier" with "I cleared it when you
    asked about RELIANCE — want me to rebuild it?" rather than
    silently re-creating it (the v1 s_draft T6 bug)."""
    macro_kind: Optional[MacroKind]
    macro_tool: Optional[str]
    summary: str  # short human-readable description
    turns_ago: int = 0


@dataclass
class ConvContext:
    """The single source of truth for a conversation's state.

    Persisted to Redis under key chat:ctx:<conv_id>. Replaces the
    grab-bag of v1 state — active_draft, pending, focus_symbols,
    etc. — with one typed blob that the transition function and
    policy layer both consume.
    """

    conv_id: str
    state: ConvState = ConvState.IDLE

    # ── Draft tracking (when state == DRAFTING) ──────────────────
    macro_kind: Optional[MacroKind] = None
    macro_tool: Optional[str] = None         # exact tool name (e.g. "place_market_order")
    macro_draft: Optional[dict] = None        # last emitted args (for re-emit)
    draft_summary: Optional[str] = None       # human-readable, used in compact prose

    # ── Clarification (when state == AWAITING_CLARIFICATION) ─────
    pending_clarification_text: Optional[str] = None  # what the bot last asked

    # ── Conversation memory ──────────────────────────────────────
    last_tool: Optional[str] = None          # for follow-up tool inheritance
    last_tool_args: dict = field(default_factory=dict)  # e.g. {"indicator": "rsi", "period": 14}
    focus_symbols: list[str] = field(default_factory=list)  # rolling 3, most-recent-first
    discarded_drafts: list[DiscardedDraft] = field(default_factory=list)
    activations: list[str] = field(default_factory=list)  # short summaries of activated agents

    # ── Bookkeeping ──────────────────────────────────────────────
    turn_count: int = 0

    # ── Convenience predicates ───────────────────────────────────

    def is_drafting(self) -> bool:
        return self.state == ConvState.DRAFTING

    def is_clarifying(self) -> bool:
        return self.state == ConvState.AWAITING_CLARIFICATION

    def has_active_draft(self) -> bool:
        return self.state == ConvState.DRAFTING and self.macro_draft is not None

    def push_focus(self, symbol: str, max_keep: int = 3) -> None:
        """Push a symbol to the focus stack (most-recent-first)."""
        sym = symbol.upper().strip()
        if not sym:
            return
        # Dedupe: remove existing then prepend
        self.focus_symbols = [s for s in self.focus_symbols if s != sym]
        self.focus_symbols.insert(0, sym)
        self.focus_symbols = self.focus_symbols[:max_keep]

    def push_discarded(
        self,
        macro_kind: Optional[MacroKind],
        macro_tool: Optional[str],
        summary: str,
        max_keep: int = 3,
    ) -> None:
        """Record a draft that was just evicted."""
        self.discarded_drafts.insert(
            0,
            DiscardedDraft(
                macro_kind=macro_kind,
                macro_tool=macro_tool,
                summary=summary[:200],
                turns_ago=0,
            ),
        )
        self.discarded_drafts = self.discarded_drafts[:max_keep]

    def age_discarded(self) -> None:
        """Increment turns_ago for every discarded draft and drop
        any older than 3 turns (TTL)."""
        for d in self.discarded_drafts:
            d.turns_ago += 1
        self.discarded_drafts = [
            d for d in self.discarded_drafts if d.turns_ago <= 3
        ]
