"""Typed events that drive state transitions in the v2 chat pipeline.

The transition function consumes events one at a time. A turn produces
a sequence: TurnStart at the top, then any number of LLM-driven events
(ToolEmitted, ClarificationAsked, TextResponse), then TurnEnd.

Distinguishing events at the type level — rather than overloading a
single dict — means the transition function can `match` on type and
the policy layer can ask "what events did this state see?" without
parsing strings.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Optional


# ─────────────────────────── Turn boundaries ─────────────────────────


@dataclass
class TurnStart:
    """Emitted once at the top of every turn before any classification."""
    user_message: str
    mode_override: Optional[Literal["agent", "automation", "backtest"]] = None
    history_is_empty: bool = False  # FE hands us an empty history (fresh chat)


@dataclass
class TurnEnd:
    """Emitted once at the bottom of every turn after the LLM hop
    completes. The state at this point is what gets persisted."""
    response_text: str
    tools_called: list[str] = field(default_factory=list)


# ─────────────────────── Pre-LLM classification ──────────────────────
# Deterministic events derived from the user message + prior context,
# computed before the LLM hop. The transition function applies these
# to determine which state's policy to use for the LLM call.


@dataclass
class IndependentIntent:
    """User shifted to a wholly new topic mid-draft (e.g. asked
    'what's RSI of RELIANCE' while a NIFTYBEES workflow draft is on
    screen). Causes DRAFTING -> EXPLORING with the draft pushed to
    discarded_drafts."""
    user_message: str


@dataclass
class Amendment:
    """User edited the active draft ('make it 5 shares', 'add a
    stop loss', 'use limit instead'). Stays in DRAFTING; the macro
    tool is re-emitted with edits."""
    user_message: str


@dataclass
class AffirmativeAck:
    """Pure affirmative ('ok', 'yes', 'sure', 'sounds good'). Meaning
    depends on state — see transition rules:
        DRAFTING                 -> short ack (FE shows the card)
        AWAITING_CLARIFICATION   -> fall through (LLM merges with prior ask)
        IDLE / EXPLORING / etc.  -> short ack, no LLM call
    """
    user_message: str


@dataclass
class FillerReply:
    """Conversational filler ('thanks', 'cool', 'nice'). Always a
    short ack, never re-emit the draft (the v1 s_filler bug)."""
    user_message: str


@dataclass
class CancelIntent:
    """Explicit cancel ('cancel that', 'scratch it', 'never mind')."""
    user_message: str


@dataclass
class BuildIntent:
    """Imperative build / order request ('build me an agent', 'buy
    10 X', 'set up Y'). Triggers state transition into DRAFTING for
    the LLM hop to fill in details."""
    user_message: str
    likely_macro: Optional[str] = None  # router hint, e.g. "workflow" / "order"


@dataclass
class ReadIntent:
    """Read-only query ('what's the price of X', 'show portfolio',
    'top gainers'). Stays in EXPLORING."""
    user_message: str


@dataclass
class CapabilityQuestion:
    """User is asking what Pivot can do, not asking it to do
    something. Should NOT trigger a draft. v1 s_newuser bug —
    'Can I try without real money?' was being routed to a backtest
    draft instead of getting an answer."""
    user_message: str


@dataclass
class ClarificationAnswer:
    """User is answering a clarification the bot just asked. The
    transition routes back to DRAFTING (likely) or EXPLORING."""
    user_message: str


@dataclass
class ModeOverride:
    """FE pill click — user explicitly chose Agent / Automation /
    Backtest mode. Forces state into DRAFTING with a known macro_kind
    regardless of message text."""
    mode: Literal["agent", "automation", "backtest"]


# ─────────────────────────── LLM-driven events ────────────────────────


@dataclass
class ToolEmitted:
    """LLM called a tool. The transition function inspects the tool
    name to decide whether this moves us into DRAFTING (a propose_*
    macro), AWAITING_CLARIFICATION (ASK_USER), or stays in EXPLORING
    (a read tool like get_live_price)."""
    tool_name: str
    args: dict
    raw_result: Optional[dict] = None


@dataclass
class ClarificationAsked:
    """LLM emitted a free-form prose question (no ASK_USER tool call,
    just a question in the response text). Detected by the prose-cue
    regex on the LLM's output. Moves us to AWAITING_CLARIFICATION."""
    question_text: str


@dataclass
class TextResponse:
    """LLM finished with a plain prose response, no tool calls left."""
    text: str


# ──────────────────────── User-driven post-turn ───────────────────────


@dataclass
class ActivationConfirmed:
    """FE Save & Activate was clicked — the draft is now live.
    Out-of-turn event (FE -> backend), but flowing through the same
    transition function keeps state consistent."""
    workflow_id: str
    summary: str


# Union for type hints
Event = (
    TurnStart | TurnEnd
    | IndependentIntent | Amendment | AffirmativeAck | FillerReply
    | CancelIntent | BuildIntent | ReadIntent | CapabilityQuestion
    | ClarificationAnswer | ModeOverride
    | ToolEmitted | ClarificationAsked | TextResponse
    | ActivationConfirmed
)
