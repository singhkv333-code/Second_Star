"""Deterministic message classifiers — pure regex, no LLM.

Reads the user message + prior context and emits one of the
pre-LLM events from events.py. The pipeline applies the resulting
event through transition() to decide the state for the LLM hop.

Single source of truth. Where v1 had _PURE_AFFIRMATIVE_RE,
_DEPENDENT_INTENT_RE, _INDEPENDENT_INTENT_RE, _FILLER_REPLY_RE, etc.
sprinkled across chat_service.py, here they live in one file with
explicit return types and documented overlap rules.

Classification priority (first match wins):

    1. CancelIntent          highest — destructive
    2. AffirmativeAck        bare 'ok' / 'yes' — special-cased
    3. FillerReply           bare 'thanks' / 'cool' — never amend
    4. CapabilityQuestion    'can I X', 'do you support Y' — never auto-build
    5. IndependentIntent     'what's RSI of <other ticker>' while drafting
    6. Amendment             'make it 5 shares' while drafting
    7. ClarificationAnswer   any reply while AWAITING_CLARIFICATION
    8. BuildIntent           imperative build phrase
    9. ReadIntent            default — fetch / quote / question
"""
from __future__ import annotations

import re
from typing import Optional

from backend.chat_v2.events import (
    AffirmativeAck,
    Amendment,
    BuildIntent,
    CancelIntent,
    CapabilityQuestion,
    ClarificationAnswer,
    Event,
    FillerReply,
    IndependentIntent,
    ReadIntent,
)
from backend.chat_v2.state import ConvContext, ConvState


# ──────────────────────────── Regexes ────────────────────────────────

# Pure affirmative — the WHOLE message must be a known yes-token
# (with optional trailing punctuation).
_PURE_AFFIRMATIVE_RE = re.compile(
    r"^\s*(?:"
    r"ok(?:ay)?|yes|yep|yeah|yup|sure|sounds\s+good|good|perfect|great|"
    r"go\s+ahead|do\s+it|proceed|right|correct|confirmed?|aye|"
    r"ok\s+activate(?:\s+it)?|yes\s+activate(?:\s+it)?"
    r")\s*[\.\!]?\s*$",
    re.IGNORECASE,
)

# Filler reply — short ack-like message that's not affirmative.
# Bare 'thanks' / 'cool' / 'got it' / 'nice' / 'awesome'.
_FILLER_REPLY_RE = re.compile(
    r"^\s*(?:"
    r"thanks?|thank\s+you|ty|tysm|ta|cheers|"
    r"cool|nice|awesome|wow|"
    r"got\s+it|gotcha|noted|understood|"
    r"k|kk|hmm|hmmm|ah|oh|huh"
    r")\s*[\.\!]?\s*$",
    re.IGNORECASE,
)

# Cancel intent — explicit, never ambiguous.
_CANCEL_RE = re.compile(
    r"\b(?:"
    r"cancel(?:\s+(?:that|it|this|the\s+\w+))?|"
    r"scrap(?:\s+(?:it|that|this))?|"
    r"scratch(?:\s+(?:it|that|this))?|"
    r"never(?:\s*mind)?|nvm|"
    r"forget(?:\s+(?:it|that|this))|"
    r"abort|undo|reset|"
    r"don'?t\s+(?:do\s+(?:it|that)|bother|proceed)|"
    r"actually\s+nevermind"
    r")\b",
    re.IGNORECASE,
)

# Capability questions — "can I X?", "do you support Y?", "is X possible?"
# Must NOT trigger a draft. Surfaced by v1 s_newuser bug.
_CAPABILITY_Q_RE = re.compile(
    r"^\s*(?:"
    # "can I / can you / could I / could you" + verb
    r"(?:can|could|may|will|would|do|does)\s+(?:i|you|we|pivot)\s+\w+"
    # "is X possible / supported / available"
    r"|is\s+(?:it|this|that)\s+(?:possible|supported|available|wired|wirable|allowed)"
    # "what does Pivot / what can Pivot"
    r"|what\s+(?:does|can|will)\s+(?:pivot|you|the\s+app)"
    # "how do I / how does Pivot / how does this"
    r"|how\s+(?:do|does|can|will|would)\s+(?:i|you|pivot|the\s+app|this|that|it)"
    # "is there a way to"
    r"|is\s+there\s+(?:a\s+)?way\s+to"
    # "are there"
    r"|are\s+there\s+"
    # "tell me about / explain"
    r"|tell\s+me\s+(?:about|how)|explain"
    r")\b",
    re.IGNORECASE,
)


# Build intent — imperative "build me X", "set up Y", "make X".
# Strongly indicates the user wants a draft created.
_BUILD_INTENT_RE = re.compile(
    r"\b(?:"
    r"build\s+(?:me\s+)?an?\s+(?:agent|strategy|automation|workflow|rule)"
    r"|(?:create|make|set\s*up|setup)\s+(?:me\s+)?an?\s+(?:agent|strategy|automation|workflow|rule|sip)"
    r"|automate\s+"
    r"|schedule\s+(?:a\s+)?(?:buy|sell|order|trade)"
    r"|set\s+me\s+up\s+to\s+"
    r")\b",
    re.IGNORECASE,
)


# Order intent — direct buy/sell ("buy 10 RELIANCE", "place order").
_ORDER_INTENT_RE = re.compile(
    r"\b(?:"
    r"buy|sell|short|exit|squareoff|square\s+off|"
    r"place\s+(?:an?\s+)?order|put\s+(?:in\s+)?(?:an?\s+)?order|"
    r"set\s+(?:a\s+)?stop\s*-?\s*loss|gtt|sip"
    r")\s+\d?\s*",
    re.IGNORECASE,
)


# "Independent intent" while a draft is on screen — phrases that
# mean "stop the current thing, do this other thing instead". The v1
# canonical form: 'what's the RSI of RELIANCE' while a NIFTYBEES
# workflow is being drafted.
_INDEPENDENT_INTENT_RE = re.compile(
    r"\b(?:"
    # Direct queries about a different subject
    r"what(?:'s|\s+is)\s+(?:the\s+)?(?:rsi|macd|sharpe|price|level|p/?e|p&l|pl)"
    # Different ticker quote
    r"|(?:show|tell)\s+me\s+(?:about\s+)?[A-Z]{2,12}"
    # 'instead', 'switch to', 'change topic'
    r"|switch\s+to|change\s+topic|on\s+(?:second|other)\s+thought"
    # 'actually wait' commonly precedes an independent question
    r"|actually\s+wait"
    r")\b",
    re.IGNORECASE,
)


# Amendment phrasings — 'make it 5 shares', 'change to ₹1,500',
# 'add a stop loss', 'use limit instead'.
_AMENDMENT_RE = re.compile(
    r"\b(?:"
    r"make\s+it\s+\d+|change\s+(?:it\s+)?to\s+\d+|actually\s+\d+|"
    r"actually\s+(?:make\s+it|change|use)|instead|"
    r"add\s+(?:a|the|an)\s+|remove\s+(?:the|that)|"
    r"increase|decrease|raise|lower|adjust|tweak"
    r")\b",
    re.IGNORECASE,
)


def _is_pure_affirmative(msg: str) -> bool:
    return bool(_PURE_AFFIRMATIVE_RE.match(msg or ""))


def _is_filler(msg: str) -> bool:
    return bool(_FILLER_REPLY_RE.match(msg or ""))


def _is_cancel(msg: str) -> bool:
    return bool(_CANCEL_RE.search(msg or ""))


def _is_capability_question(msg: str) -> bool:
    if not msg:
        return False
    # Capability questions almost always end with "?"; if there's no
    # question mark and no canonical opener, it's probably an
    # instruction, not a question.
    has_q = "?" in msg
    has_opener = bool(_CAPABILITY_Q_RE.search(msg))
    return has_opener and (has_q or len(msg) < 100)


def _is_build_intent(msg: str) -> bool:
    return bool(_BUILD_INTENT_RE.search(msg or ""))


def _is_order_intent(msg: str) -> bool:
    return bool(_ORDER_INTENT_RE.search(msg or ""))


def _is_independent_intent(msg: str) -> bool:
    return bool(_INDEPENDENT_INTENT_RE.search(msg or ""))


def _is_amendment_phrasing(msg: str) -> bool:
    return bool(_AMENDMENT_RE.search(msg or ""))


# ──────────────────────────── Classifier ──────────────────────────────


def classify(message: str, ctx: ConvContext) -> Event:
    """Map a user message + current context to one pre-LLM event.

    The order of checks reflects the priority documented at the top
    of this file. The first matching classifier wins; subsequent
    classifiers are not consulted.

    The returned event does NOT mutate ctx — the transition function
    in transitions.py does that. This separation keeps classification
    pure and unit-testable.
    """
    msg = (message or "").strip()

    # 1. Cancel — highest priority, even mid-clarification.
    if _is_cancel(msg):
        return CancelIntent(user_message=message)

    # 2. Pure affirmative — meaning depends on state, but the
    # classification is decoupled from interpretation.
    if _is_pure_affirmative(msg):
        return AffirmativeAck(user_message=message)

    # 3. Filler reply — short, ack-like, but never an instruction.
    if _is_filler(msg):
        return FillerReply(user_message=message)

    # 4. Capability question — must be answered, never auto-built.
    if _is_capability_question(msg):
        return CapabilityQuestion(user_message=message)

    # 5. While in clarification, anything not above is treated as
    # answering the bot's question. Routes back to DRAFTING via the
    # LLM hop.
    if ctx.state == ConvState.AWAITING_CLARIFICATION:
        return ClarificationAnswer(user_message=message)

    # 6. While DRAFTING, decide independent vs amendment.
    if ctx.state == ConvState.DRAFTING:
        if _is_independent_intent(msg):
            return IndependentIntent(user_message=message)
        # Default mid-draft: assume amendment. The LLM will treat
        # ambiguous text as an edit to the existing draft.
        return Amendment(user_message=message)

    # 7. Build / order imperatives push us into DRAFTING.
    if _is_build_intent(msg):
        return BuildIntent(user_message=message, likely_macro="workflow")
    if _is_order_intent(msg):
        return BuildIntent(user_message=message, likely_macro="order")

    # 8. Default — read-only intent, stays in EXPLORING.
    return ReadIntent(user_message=message)
