"""Related next questions, offered under a finished answer.

The Perplexity affordance: once an answer lands, show three or four things
the user would plausibly ask next, each one a complete question they can send
as-is.

Why a separate call rather than asking the answering model for them inline:

  - The answer turn is the expensive one (a ~50k-token cached prefix, the full
    tool set, medium reasoning). Follow-ups need none of that — they need the
    question and the gist of the answer, and nothing else. Folding them in
    would spend the big model's output budget on them and risk them bleeding
    into the visible reply.
  - It runs AFTER the answer is on screen, from the frontend, so it costs the
    user no perceived latency. If it fails, is slow, or returns nothing, the
    turn is already complete and the block simply does not appear.

Non-deterministic by construction: there is no template and no keyword table.
The model reads the exchange and writes the questions. The only rules are
shape rules (how many, how long, self-contained) plus the scope boundary,
because a suggestion the product cannot answer is worse than no suggestion.
"""
from __future__ import annotations

import json
import logging
import re

from backend.llm.base import LLMMessage
from backend.llm.factory import get_llm_client

logger = logging.getLogger(__name__)

# Enough of the answer to know what was actually said; the tail matters as
# much as the head, since that is where the caveats and open threads sit.
_HEAD = 1400
_TAIL = 700

_SYSTEM = (
    "You write the follow-up questions a user would naturally ask next, "
    "having just read this answer in an Indian markets research product.\n\n"
    "Return ONLY a JSON array of 3 or 4 strings. No prose, no keys, no "
    "markdown.\n\n"
    "Each string is one question, written as the USER would type it, "
    "under 12 words, and self-contained — it names its own subject, so it "
    "still makes sense read on its own with no memory of this answer.\n\n"
    "Take them somewhere new. Each should open a different direction: a "
    "thread the answer raised but did not follow, the next decision the "
    "reader now faces, a comparison, a risk, a horizon. Never re-ask what "
    "was just answered, and never ask two questions that would be answered "
    "by the same data.\n\n"
    "Stay inside what this product does: Indian equities and indices (NSE "
    "and BSE), NSE options, MCX commodities, fundamentals, filings, news, "
    "screening, charting, portfolio, backtests and strategies. No foreign "
    "stocks, no off-exchange mutual funds, and never a question that asks "
    "for personalised buy/sell advice."
)


def _coerce(raw: str) -> list[str]:
    """Pull the array out of whatever the model returned.

    Kept forgiving on shape and strict on content: a fenced block or a
    stray sentence around the JSON is recoverable and common, so recover it
    rather than dropping a good set of suggestions over punctuation.
    """
    text = (raw or "").strip()
    if not text:
        return []
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text).strip()
    if not text.startswith("["):
        m = re.search(r"\[.*\]", text, re.DOTALL)
        if not m:
            return []
        text = m.group(0)
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, list):
        return []

    out: list[str] = []
    seen: set[str] = set()
    for item in parsed:
        if not isinstance(item, str):
            continue
        q = " ".join(item.split()).strip().strip('"')
        # A suggestion is a thing to click, not a paragraph. The cap is
        # generous enough to keep a good long question and tight enough to
        # drop a model that started explaining itself.
        if not (4 <= len(q) <= 110):
            continue
        key = q.lower().rstrip("?")
        if key in seen:
            continue
        seen.add(key)
        out.append(q)
    return out[:4]


def _excerpt(answer: str) -> str:
    a = (answer or "").strip()
    if len(a) <= _HEAD + _TAIL:
        return a
    return f"{a[:_HEAD]}\n...\n{a[-_TAIL:]}"


async def generate_followups(question: str, answer: str) -> list[str]:
    """Three or four next questions. Returns [] on any failure — never raises.

    The caller renders nothing when this is empty, so every failure mode
    (provider down, malformed JSON, a model that wrote prose) degrades to the
    answer simply not having a suggestion block.
    """
    q = (question or "").strip()
    a = (answer or "").strip()
    # Nothing to build on: a one-line answer to "hi" has no next question that
    # isn't a product tour, and that is exactly the thing we don't want here.
    if not q or len(a) < 200:
        return []

    user = f"The user asked:\n{q}\n\nThe answer they read:\n{_excerpt(a)}"
    try:
        resp = await get_llm_client().complete(
            messages=[
                LLMMessage(role="system", content=_SYSTEM),
                LLMMessage(role="user", content=user),
            ],
            tools=None,
            tool_choice="none",
            max_output_tokens=700,
            reasoning_effort="minimal",
            temperature=0.7,
        )
    except Exception as e:  # noqa: BLE001 — best-effort, never break the turn
        logger.warning("followup generation failed: %s: %s", type(e).__name__, e)
        return []

    out = _coerce(getattr(resp, "content", None) or "")
    if not out:
        logger.info("followup generation returned nothing usable")
    return out
