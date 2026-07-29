"""LLM call 2 — news-article match classifier.

Given a single article + the user's trigger event, decide whether the
article *confirms* the event happened. Returns
``(matched: bool, confidence: float, reason: str)``.

The prompt is verbatim from the implementation guideline. The
classifier never raises — on any error it returns
``(False, 0.0, "<reason>")`` so the monitor task keeps moving.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from backend.llm.base import LLMMessage
from backend.llm.factory import get_llm_client
from backend.triggers.models import NewsArticle


logger = logging.getLogger(__name__)


_PROMPT_TEMPLATE = """You are Pivot's event verification engine. Determine if this news article
confirms the user's trigger event has occurred.
Trigger event: "{trigger_event}"
Article:
- Title: {title}
- Description: {description}
- Source: {source}
- Published: {published_at}
Return ONLY this JSON:
{{
  "match": true | false,
  "confidence": 0.0 to 1.0,
  "reason": "<one sentence>"
}}
Critical rules:
- match=true ONLY if the article CONFIRMS the event happened
- Speculation, prediction, or analyst commentary is NOT a match
- confidence must reach 0.85 to fire a trigger
- For ambiguous cases, lean toward match=false"""


def _extract_json(text: str) -> dict[str, Any] | None:
    if not text:
        return None
    s = text.strip()
    if s.startswith("```"):
        first_nl = s.find("\n")
        if first_nl != -1:
            s = s[first_nl + 1:]
        if s.rstrip().endswith("```"):
            s = s.rstrip()[:-3].rstrip()
    try:
        parsed = json.loads(s)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass
    start = s.find("{")
    end = s.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    try:
        parsed = json.loads(s[start:end + 1])
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        return None
    return None


def _coerce(payload: dict[str, Any]) -> tuple[bool, float, str]:
    matched_raw = payload.get("match")
    if isinstance(matched_raw, bool):
        matched = matched_raw
    elif isinstance(matched_raw, str):
        matched = matched_raw.strip().lower() in ("true", "yes", "1")
    else:
        matched = False
    conf_raw = payload.get("confidence", 0.0)
    try:
        conf = float(conf_raw)
    except (TypeError, ValueError):
        conf = 0.0
    if conf < 0.0:
        conf = 0.0
    elif conf > 1.0:
        conf = 1.0
    reason_raw = payload.get("reason", "")
    reason = str(reason_raw) if reason_raw is not None else ""
    if not matched and conf >= 0.85:
        # Defensive: if the model says match=false with sky-high
        # confidence, that's a contradiction. Keep match=false and
        # ignore — fire_rules won't trip.
        pass
    return matched, conf, reason


async def classify_article(
    article: NewsArticle,
    trigger_event: str,
) -> tuple[bool, float, str]:
    """Classify one article. Never raises."""
    prompt = _PROMPT_TEMPLATE.format(
        trigger_event=trigger_event.replace('"', '\\"'),
        title=article.title.replace('"', '\\"'),
        description=(article.description or "").replace('"', '\\"'),
        source=article.source or article.source_id or "unknown",
        published_at=article.published_at.isoformat(),
    )
    client = get_llm_client()
    try:
        # See parser.py for the rationale on the larger budget — same
        # reasoning-model headroom story applies to the classifier.
        response = await client.complete(
            messages=[LLMMessage(role="user", content=prompt)],
            max_output_tokens=1200,
            temperature=0.1,
            response_format="json_object",
            reasoning_effort="minimal",
        )
    except Exception as e:
        logger.warning("classify_article: LLM call raised: %s", e)
        return False, 0.0, "classifier error"

    if response.finish_reason == "error":
        return False, 0.0, "classifier returned error"

    data = _extract_json(response.content or "")
    if data is None:
        logger.info(
            "classify_article: malformed LLM response: %r", response.content,
        )
        return False, 0.0, "malformed classifier response"
    return _coerce(data)
