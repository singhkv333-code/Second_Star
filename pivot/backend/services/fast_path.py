"""Pre-LLM fast-path classifier.

Catches conversational starters (greetings, thanks, help asks) that
don't need an LLM at all. Each match returns a canned response in
under a millisecond — that's a ~5000× speedup over the agentic loop
and avoids burning tokens on "hi".

Conservative on purpose: strict equality after normalization, plus
prefix-with-trailing-content so "hello, what's RELIANCE's price"
does NOT match (the model needs to handle it). Better to miss a
fast-path opportunity than mis-route a real query.
"""
from __future__ import annotations

import re
from typing import Optional


# Order matters slightly: longer phrases come before single words so
# "good morning" matches before any subset would.
_GREETINGS = (
    "good morning", "good afternoon", "good evening", "good night",
    "namaste", "namaskar",
    "hello", "hey", "hi", "yo", "sup",
)

_THANKS = (
    "thank you very much", "thanks a lot", "thank you",
    "thanks", "thx", "ty", "appreciate it", "cheers",
)

_HELP_QUERIES = (
    "what can you do",
    "what can you help me with",
    "how do you work",
    "what is pivot",
    "what features do you have",
    "show me what you can do",
    "/help",
    "help",
    "capabilities",
)


_GREETING_REPLY = (
    "Hi! Tell me what you'd like to do — check a price, build an agent, "
    "look at your portfolio, or run a backtest."
)

_THANKS_REPLY = "Anytime."

_HELP_REPLY = (
    "I can help you with:\n"
    "• Live prices, fundamentals, and screening of Indian stocks\n"
    "• Building automated trading agents in plain English\n"
    "• Backtesting strategies on historical data\n"
    "• Tracking your portfolio and active agents\n\n"
    "Just describe what you want — for example, *\"buy 10 RELIANCE every "
    "weekday at 3:55 PM\"* or *\"show stocks where PE < 15 and ROE > 18\"*."
)


# Strip trailing punctuation that doesn't change meaning ("hi!", "hello?", "thx.")
_TRAILING_PUNCT_RE = re.compile(r"[?!.,;:]+$")


def _normalize(message: str) -> str:
    """Lowercase, strip whitespace + trailing punctuation, collapse
    multiple spaces. Returns "" for empty/whitespace input.
    """
    s = (message or "").strip().lower()
    # Repeatedly strip trailing punctuation + whitespace so "hi ,"
    # → "hi" not "hi " (regex stops at the space the first time).
    prev = None
    while prev != s:
        prev = s
        s = _TRAILING_PUNCT_RE.sub("", s).rstrip()
    s = re.sub(r"\s+", " ", s)
    return s


def _matches_phrase(normalized: str, phrases: tuple[str, ...]) -> bool:
    """Match either equality or prefix-followed-by-end. Crucially does
    NOT match "hello, what's RELIANCE's price" — that has more content
    after the greeting, so it goes to the LLM.

    Match shapes:
      "hello"                           → match
      "hello!"   (after _normalize)     → match
      "hello there"                     → no match (extra content)
      "is hello supported"              → no match (greeting not at start)
    """
    if not normalized:
        return False
    for p in phrases:
        if normalized == p:
            return True
    return False


def try_fast_path(message: str) -> Optional[str]:
    """Return a canned response if the message is purely conversational.

    None means "send to the LLM". Latency is microseconds; the function
    is safe to call on every chat turn.
    """
    n = _normalize(message)
    if not n:
        return None

    if _matches_phrase(n, _GREETINGS):
        return _GREETING_REPLY
    if _matches_phrase(n, _THANKS):
        return _THANKS_REPLY
    if _matches_phrase(n, _HELP_QUERIES):
        return _HELP_REPLY

    return None
