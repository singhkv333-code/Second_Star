"""Stage 6 — LLM classification with retraction detection.

Reuses the prompt shape from ``backend/triggers/classifier.py`` (the
trusted, in-production NewsAPI classifier) and extends the JSON
contract with:

  - a 5-state verdict instead of a boolean: YES / NO / AMBIGUOUS /
    UNRELATED / RETRACTION
  - an ``is_retraction`` flag that the Phase-6 safety-window watcher
    consumes
  - ``confidence`` in [0, 1] (unchanged from the original prompt)
  - a one-sentence ``reason`` for the audit trail

The classifier is a single LLM call with ``response_format='json_object'``
+ ``reasoning_effort='minimal'``. Never raises — on any failure
returns ``UNRELATED`` with confidence 0 so the funnel keeps moving.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Literal, Optional

from backend.llm.base import LLMMessage
from backend.llm.factory import get_llm_client

logger = logging.getLogger(__name__)


Verdict = Literal["YES", "NO", "AMBIGUOUS", "UNRELATED", "RETRACTION"]
_VALID_VERDICTS: frozenset[str] = frozenset(
    {"YES", "NO", "AMBIGUOUS", "UNRELATED", "RETRACTION"}
)

_PROMPT_CACHE_KEY = "news_events.classify.v1"

_SYSTEM_PROMPT = """You are Pivot's event verification engine.

Given a target event and a SHORT, RELEVANT excerpt from a news
article, decide whether the article confirms, denies, retracts, or
fails to address the event. Return ONLY this JSON:

{
  "verdict": "YES" | "NO" | "AMBIGUOUS" | "UNRELATED" | "RETRACTION",
  "confidence": 0.0 to 1.0,
  "is_retraction": true | false,
  "reason": "<one sentence>"
}

Rules:
- YES iff the article CONFIRMS the event already happened. Speculation,
  prediction, analyst commentary, or "expected to" language is NOT
  YES — those are AMBIGUOUS at best.
- NO iff the article actively denies the event (e.g. "RBI held rates",
  "the bill failed").
- RETRACTION iff the article reports that an earlier confirmation of
  the event has been retracted, overturned, paused, or proved false.
  Also set ``is_retraction`` to true on this branch.
- AMBIGUOUS iff the article addresses the event but the verdict isn't
  clear from the excerpt alone (mixed signals, leaked rumours,
  conditional language).
- UNRELATED iff the excerpt does not address the event.
- ``confidence`` is your confidence in the verdict you chose.
  ``confidence < 0.85`` should generally land you in AMBIGUOUS or
  UNRELATED rather than YES/NO/RETRACTION.
- ``is_retraction`` mirrors the RETRACTION branch — true for
  RETRACTION, false otherwise.
"""


@dataclass(frozen=True)
class ClassificationResult:
    """Stage-6 output. Persisted as columns on
    ``news_article_classifications``."""

    verdict: Verdict
    confidence: float
    is_retraction: bool
    reason: str
    model: Optional[str] = None


def _safe_unrelated(reason: str, model: Optional[str] = None) -> ClassificationResult:
    return ClassificationResult(
        verdict="UNRELATED",
        confidence=0.0,
        is_retraction=False,
        reason=reason,
        model=model,
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


async def classify_excerpt(
    *,
    event_description: str,
    excerpt: str,
    article_title: str,
) -> ClassificationResult:
    """Run the LLM classifier on (event, excerpt, title) → verdict.

    ``excerpt`` is the Stage-5 output. When it's empty, we fall back
    to the title alone — Stage 6 still gets a verdict, just lower
    confidence than the excerpt path.
    """
    excerpt = (excerpt or "").strip()
    user_message = (
        f"Target event: {event_description}\n\n"
        f"Article title: {article_title}\n\n"
        f"Article excerpt:\n{excerpt or '(no excerpt extracted — judge on title alone)'}\n\n"
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
            max_output_tokens=300,
            temperature=0.0,
            prompt_cache_key=_PROMPT_CACHE_KEY,
        )
    except Exception as exc:  # noqa: BLE001 — funnel must continue
        logger.warning("[news_events.classify] llm_failed err=%s", exc)
        return _safe_unrelated(f"llm_failed: {exc}")

    parsed = _parse_json(response.content or "")
    if not parsed:
        logger.warning(
            "[news_events.classify] parse_failed content=%r",
            (response.content or "")[:200],
        )
        return _safe_unrelated("parse_failed")

    verdict = str(parsed.get("verdict", "")).strip().upper()
    if verdict not in _VALID_VERDICTS:
        return _safe_unrelated(f"unknown verdict {verdict!r}")

    try:
        confidence = float(parsed.get("confidence", 0.0))
    except (TypeError, ValueError):
        confidence = 0.0
    confidence = max(0.0, min(1.0, confidence))

    is_retraction = bool(parsed.get("is_retraction", verdict == "RETRACTION"))
    # Belt-and-suspenders: RETRACTION verdict forces the flag.
    if verdict == "RETRACTION":
        is_retraction = True

    reason = str(parsed.get("reason", "")).strip()[:500] or "(no reason given)"
    model_used: Optional[str] = getattr(client, "model", None)

    return ClassificationResult(
        verdict=verdict,  # type: ignore[arg-type]
        confidence=confidence,
        is_retraction=is_retraction,
        reason=reason,
        model=model_used,
    )
