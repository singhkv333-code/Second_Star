"""Structured clarifying card for UNDER-SPECIFIED automation / agent builds.

The companion to ``clarify_engine`` (which clarifies *portfolio / basket*
builds). This module clarifies *agent / workflow* builds — "make me an agent
that buys options in RELIANCE", "build an agent for TCS" — where the user named
an instrument but left the **kind of automation** (schedule vs condition vs
alert) and the **size** unstated.

Before this, that case produced a free-text ``ASK_USER`` question
(``system.md`` §"Build an agent for X with no other context"). Now it emits the
SAME ``clarify_card`` widget the portfolio builder uses — paginated one-click
chips — so the user taps an answer instead of typing prose.

Design (deliberately deterministic — NO LLM call):
  * The decision-relevant unknowns for an agent build are a SMALL, closed set
    (action-kind, size), unlike the portfolio builder's open VOI space — so the
    questions are generated from a fixed grounded template, not an LLM. That
    keeps the clarify turn at ~0 LLM hops.
  * Questions reuse the ``ClarifyQuestion`` / ``ClarifyOption`` wire shape
    verbatim (``strategy_contracts``) so the existing FE ``ClarifyCard`` and the
    in-band resume machinery render / round-trip them unchanged.
  * Answers fold into a plain-dict agent slot-state and, when the flow stops,
    are assembled into an enriched natural-language intent that
    ``propose_workflow`` builds the draft from (``register-not-execute`` and the
    not-advice disclaimer are enforced downstream by the builder exactly as for
    any other workflow draft).

The chat layer (``chat_service._try_resume_clarify``) branches on the
``ClarifyState.kind == "agent"`` discriminator: it uses
:func:`normalize_agent_answer_into_slots` to fold answers and
:func:`build_agent_intent` + ``propose_workflow`` to build, instead of the
portfolio ``normalize_answer_into_slots`` + ``build_strategy``.
"""
from __future__ import annotations

import re
from typing import Any, Optional

# The agent-build slot vocabulary. Closed set — these are the ONLY things a
# generated question may target (mirrors clarify_engine._SLOT_FIELDS discipline).
AGENT_SLOT_ACTION = "action"
AGENT_SLOT_SIZE = "size"

# ── Symbol / asset detection (light, deterministic) ─────────────────────────

_TICKER_RE = re.compile(r"\b[A-Z][A-Z&]{1,11}\b")
_TICKER_STOP = {
    "AND", "OR", "THE", "FOR", "WITH", "BUY", "SELL", "SIP", "ETF", "MF",
    "PE", "ROE", "PB", "RSI", "SMA", "EMA", "IV", "OI", "PCR", "NSE", "BSE",
    "INR", "RS", "I", "A", "TO", "IN", "ON", "AT", "IF", "F", "O", "ME", "AN",
    "AGENT", "BOT", "ALERT", "WHEN", "EVERY", "MAKE", "BUILD", "CREATE",
}
_OPTIONS_CUES = (
    "option", "call", "put", "spread", "straddle", "strangle",
    "iron condor", "f&o", "fno", "strike", "expiry",
)


_LOWER_FILLER = {
    "it", "that", "this", "them", "the", "stock", "stocks", "share", "shares",
    "market", "my", "me", "something", "anything", "everything", "options",
    "option", "call", "put", "schedule", "condition", "alert", "sip", "agent",
}


def extract_symbol(request: str) -> Optional[str]:
    """Best-effort single NSE ticker the request names (e.g. RELIANCE, TCS).

    Two passes: (1) the first uppercase ticker-shaped token, (2) a fallback for
    lowercase prompts — a word after in/for/of/on/into, upper-cased as a
    candidate. The model usually passes ``symbol`` explicitly to the tool, so
    this is a backstop; a miss just frames the chips generically ("the stock"),
    never wrong.
    """
    if not request:
        return None
    for tok in _TICKER_RE.findall(request):
        if tok in _TICKER_STOP or len(tok) < 2:
            continue
        return tok
    m = re.search(
        r"\b(?:in|for|of|on|into)\s+([A-Za-z][A-Za-z&]{2,15})\b",
        request, re.IGNORECASE,
    )
    if m:
        cand = m.group(1).upper()
        if cand not in _TICKER_STOP and cand.lower() not in _LOWER_FILLER:
            return cand
    return None


def detect_asset(request: str) -> str:
    """``"options"`` when the request mentions an option/F&O instrument, else
    ``"equity"``. Drives whether the size chips read in lots or shares."""
    text = (request or "").lower()
    return "options" if any(cue in text for cue in _OPTIONS_CUES) else "equity"


# ── Under-spec gate ─────────────────────────────────────────────────────────

# An action VERB is present (buy/sell/sip/alert) but ...
_ACTION_VERB_RE = re.compile(
    r"\b(buy|buys|buying|sell|sells|selling|sip|invest|alert|notify|short)\b",
    re.IGNORECASE,
)
# ... a TRIGGER (when/every/if/cross/below/above/schedule/RSI<n) would make it
# buildable, as would a concrete SIZE (n shares/lots/₹n). When NEITHER is
# present the build kind is genuinely open → ask.
_TRIGGER_RE = re.compile(
    r"\b(when|every|if|cross(?:es)?|below|above|drops?|rises?|"
    r"daily|weekly|monthly|schedule|at\s+open|at\s+close|"
    r"rsi|sma|ema|macd|breaks?|hits?|reaches?)\b",
    re.IGNORECASE,
)
_SIZE_RE = re.compile(
    r"\b\d+\s*(?:share|shares|lot|lots|qty|quantity|units?)\b"
    r"|\b(?:buy|buys|sell|sells|short)\s+\d+\b"  # "buy 50 TCS" — bare qty
    r"|₹\s*[\d,]+|\b(?:rs\.?|inr)\s*[\d,]+\b|\bworth\b",
    re.IGNORECASE,
)
# "build an agent for X" / "make me a bot" with no action verb at all — still
# an agent build that must be clarified rather than fabricated.
_AGENT_BUILD_RE = re.compile(
    r"\b(build|make|create|set\s*up|setup|design|want|need)\b"
    r"[^.]{0,30}\b(agent|automation|bot|rule|workflow)\b",
    re.IGNORECASE,
)


def should_ask_agent(request: str) -> bool:
    """True when an agent ask leaves BOTH the trigger and the size unstated —
    the build kind is open and a default would be a guess.

    Fires when the request either names an action verb (buy/sell/SIP/alert) OR
    is an explicit "build an agent" phrasing, AND carries neither a trigger nor
    a size. Returns False (let the model build / ask directly) the moment a
    trigger or size anchors the draft. Deterministic and cheap."""
    if not request:
        return False
    if not (_ACTION_VERB_RE.search(request) or _AGENT_BUILD_RE.search(request)):
        return False
    if _TRIGGER_RE.search(request) or _SIZE_RE.search(request):
        return False
    return True


# ── Card generation (deterministic, grounded) ───────────────────────────────


def _action_question(symbol: Optional[str], asset: str) -> dict[str, Any]:
    who = symbol or "the stock"
    instr = f"{who} options" if asset == "options" else who
    opts = [
        {"id": "schedule",
         "label": f"Buy {instr} on a schedule (e.g. every Friday)"},
        {"id": "condition",
         "label": f"Buy {instr} when a price / indicator condition hits"},
        {"id": "sip",
         "label": f"Run a recurring SIP into {instr}"},
        {"id": "alert",
         "label": f"Just alert me about {who} — place no order"},
    ]
    return {
        "id": "q_action",
        "slot": AGENT_SLOT_ACTION,
        "prompt": f"What should the {instr} agent do?",
        "voi": 1.0,
        "options": opts,
        "free_text": True,
        "skippable": False,
    }


def _size_question(symbol: Optional[str], asset: str) -> dict[str, Any]:
    who = symbol or "it"
    if asset == "options":
        opts = [
            {"id": "lot_1", "label": "1 lot"},
            {"id": "lot_2", "label": "2 lots"},
            {"id": "lot_5", "label": "5 lots"},
        ]
        prompt = f"How many lots of {who} per trade?"
    else:
        opts = [
            {"id": "qty_10", "label": "10 shares"},
            {"id": "qty_25", "label": "25 shares"},
            {"id": "inr_10000", "label": "₹10,000 worth"},
        ]
        prompt = f"How much {who} per trade?"
    return {
        "id": "q_size",
        "slot": AGENT_SLOT_SIZE,
        "prompt": prompt,
        "voi": 0.7,
        "options": opts,
        "free_text": True,
        # Skippable: an alert-only agent or an undecided user can move on; the
        # draft then carries a sensible default size the user edits on the card.
        "skippable": True,
    }


def generate_agent_clarify_card(request: str) -> Optional[dict[str, Any]]:
    """Build the ``clarify_card`` payload dict for an under-specified agent ask,
    or ``None`` to let the model build directly.

    Returns the SAME shape the FE ClarifyCard renders:
    ``{session_slot_state, total, index, questions:[...]}`` where each question
    matches :class:`ClarifyQuestion`. ``session_slot_state`` carries the agent
    slot-state (symbol + asset + empty answers) which the resume path round-trips
    via Redis ``ClarifyState`` — the FE ignores it."""
    if not should_ask_agent(request):
        return None
    symbol = extract_symbol(request)
    asset = detect_asset(request)
    questions = [
        _action_question(symbol, asset),
        _size_question(symbol, asset),
    ]
    slots = {"symbol": symbol, "asset": asset, "answers": {}}
    return {
        "session_slot_state": slots,
        "total": len(questions),
        "index": 0,
        "questions": questions,
    }


# ── Answer ingestion ────────────────────────────────────────────────────────


def normalize_agent_answer_into_slots(
    question: dict[str, Any], answer: str, slots: dict[str, Any],
) -> dict[str, Any]:
    """Fold ONE agent-clarify answer into the agent slot-state dict.

    ``answer`` is a chip ``id``, a chip ``label``, or free text. We resolve the
    chip id where possible (prefer it) and store BOTH the canonical value and
    the human label under ``slots["answers"][slot]`` so
    :func:`build_agent_intent` can phrase the enriched build intent. Mutates and
    returns ``slots``."""
    slot = str(question.get("slot") or "").strip()
    raw = (answer or "").strip()
    if not slot or not raw:
        return slots
    label = raw
    value = raw
    for opt in question.get("options") or []:
        oid = str(opt.get("id") or "")
        olabel = str(opt.get("label") or "")
        if oid and oid.lower() == raw.lower():
            value = oid
            label = olabel or oid
            break
        if olabel and olabel.lower() == raw.lower():
            value = oid or olabel
            label = olabel
            break
    answers = slots.setdefault("answers", {})
    answers[slot] = {"value": value, "label": label}
    return slots


# Map an action chip id → a CONCRETE, buildable enrichment phrase. These pin a
# sensible default trigger so the workflow planner always produces a valid draft
# (a vague "when a condition is met" fails validation — there's no level to
# build). The draft is register-not-execute and fully editable, so the user
# tunes the exact level/day on the card — exactly the system's silent-default
# pattern. We are NOT fabricating market data; we're proposing an editable
# starting point.
_ACTION_PHRASE = {
    "schedule": "Buy {sym} on a recurring schedule — every Friday at 9:30 IST.",
    "condition": "Buy {sym} when its 14-day RSI drops below 30 (oversold).",
    "sip": "Run a weekly SIP buying {sym} every Monday at 9:30 IST.",
    "alert": "Alert me (place no order) when {sym} moves more than 3% in a day.",
}


def build_agent_intent(request: str, slots: dict[str, Any]) -> str:
    """Assemble an enriched natural-language intent for ``propose_workflow`` from
    the original request + the answered agent slots.

    The builder (``propose_workflow_async``) plans + drafts from this string, so
    we phrase the answers as plain English the planner already understands —
    never fabricating a value the user didn't choose (a skipped slot just isn't
    mentioned, and the builder picks a sensible default the user edits)."""
    symbol = (slots or {}).get("symbol")
    asset = (slots or {}).get("asset") or "equity"
    answers = (slots or {}).get("answers") or {}
    sym = symbol or "the stock"
    if asset == "options":
        sym = f"{symbol} options" if symbol else "the options"

    parts = [(request or "").strip()]

    action = (answers.get(AGENT_SLOT_ACTION) or {}).get("value")
    if action in _ACTION_PHRASE:
        parts.append(_ACTION_PHRASE[action].format(sym=sym))

    size = answers.get(AGENT_SLOT_SIZE) or {}
    size_label = (size.get("label") or "").strip()
    if size_label and size_label.lower() not in {"skip", "something else"}:
        parts.append(f"Size each trade as {size_label}.")

    return " ".join(p for p in parts if p)


__all__ = [
    "AGENT_SLOT_ACTION",
    "AGENT_SLOT_SIZE",
    "extract_symbol",
    "detect_asset",
    "should_ask_agent",
    "generate_agent_clarify_card",
    "normalize_agent_answer_into_slots",
    "build_agent_intent",
]
