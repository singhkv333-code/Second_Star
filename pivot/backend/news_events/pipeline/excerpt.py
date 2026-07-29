"""Stage 5 — LLM excerpt extraction.

Given an article body + a spec description, pull the 2-3 sentences
that are most likely to confirm or deny that the event happened.
Stage 6 then classifies on those sentences alone — context
distillation per the Phase-0 design.

Why a separate stage instead of feeding the full body to the
classifier:
  1. Cost. Classifier prompts are small; full bodies are not.
  2. Cache hit-rate. A stable system prompt + small body → high
     prompt-cache reuse on the OpenAI backend.
  3. Defense against off-topic body sections (e.g. a list of related
     headlines at the bottom of an article).

Single LLM call; ``reasoning_effort='minimal'`` and a bounded output
budget (~300 tokens) keep the call fast and cheap.
"""
from __future__ import annotations

import json
import logging
from typing import Optional

from backend.llm.base import LLMMessage
from backend.llm.factory import get_llm_client

logger = logging.getLogger(__name__)


# The system prompt is constant — the OpenAI client passes
# ``prompt_cache_key="news_events.excerpt.v1"`` so warm-cache
# requests hit the cached prefix.
_PROMPT_CACHE_KEY = "news_events.excerpt.v1"

_SYSTEM_PROMPT = (
    "You are Pivot's news excerpt extractor. Given a news article and "
    "a target event, return ONLY a JSON object with one key "
    "\"excerpt\" whose value is at most 3 sentences from the article "
    "verbatim that most directly bear on whether the event "
    "happened. If no sentence in the article addresses the event "
    "either way, return an empty string for \"excerpt\". Never "
    "summarise or paraphrase; copy sentences verbatim. Never add "
    "commentary."
)


def _parse_json(text: str) -> Optional[dict]:
    if not text:
        return None
    s = text.strip()
    if s.startswith("```"):
        nl = s.find("\n")
        if nl != -1:
            s = s[nl + 1:]
        if s.rstrip().endswith("```"):
            s = s.rstrip()[:-3].rstrip()
    try:
        parsed = json.loads(s)
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        return None


async def extract_excerpt(
    *,
    event_description: str,
    article_title: str,
    article_body: str,
    max_body_chars: int = 12_000,
) -> str:
    """Return the article excerpt most relevant to the event.

    Never raises. On any LLM or parse failure returns the empty
    string — Stage 6 will then classify on title+summary, the same
    pre-body fallback Phase 2 already supports.
    """
    body = (article_body or "").strip()[:max_body_chars]
    if not body:
        return ""

    user_message = (
        f"Target event: {event_description}\n\n"
        f"Article title: {article_title}\n\n"
        f"Article body:\n{body}\n\n"
        "Return the JSON now."
    )

    client = get_llm_client()
    try:
        response = await client.complete(
            messages=[
                LLMMessage(role="system", content=_SYSTEM_PROMPT),
                LLMMessage(role="user", content=user_message),
            ],
            response_format="json_object",
            reasoning_effort="minimal",
            max_output_tokens=400,
            temperature=0.0,
            prompt_cache_key=_PROMPT_CACHE_KEY,
        )
    except Exception as exc:  # noqa: BLE001 — Phase-3 must never crash the funnel
        logger.warning("[news_events.excerpt] llm_failed err=%s", exc)
        return ""

    parsed = _parse_json(response.content or "")
    if not parsed:
        logger.warning(
            "[news_events.excerpt] parse_failed content=%r",
            (response.content or "")[:200],
        )
        return ""
    excerpt = parsed.get("excerpt", "")
    if not isinstance(excerpt, str):
        return ""
    return excerpt.strip()
