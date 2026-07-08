"""NL → ParsedSpec. Mirrors backend/workflows/propose.py in shape:

  1. Build a focused system prompt that enumerates tiers, the
     keyword-set shape, the resolution-criteria shape, and the
     retraction-policy shape.
  2. Call ``get_llm_client().complete(...)`` with
     ``response_format='json_object'`` + ``reasoning_effort='minimal'``.
  3. Parse the JSON, validate it against the same Pydantic models
     the Phase-1 ``schemas.py`` ships (``KeywordSet``,
     ``ResolutionCriteria``, ``RetractionPolicy``).
  4. On validation failure, retry ONCE with the concrete error
     embedded in the prompt.

``ParsedSpec`` is the parser's output dataclass — the spec lifecycle
machinery in ``backend/news_events/specs.py`` turns it into a row.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Literal, Optional

from pydantic import ValidationError

from backend.llm.base import LLMMessage
from backend.llm.factory import get_llm_client
from backend.news_events.schemas import (
    KeywordSet,
    ResolutionCriteria,
    RetractionPolicy,
)

logger = logging.getLogger(__name__)


Tier = Literal["tier1", "tier2", "tier3"]


@dataclass
class ParsedSpec:
    """Strongly-typed output of the parser. Always carries fully
    validated child objects; the dict-shape conversions to the DB
    JSON columns happen in ``specs.py``.

    ``needs_disambiguation`` is True iff the parser concluded the
    event is Tier 3. The spec state machine routes those into a
    ``DisambiguationSession`` before activation.
    """

    description: str
    tier: Tier
    keyword_set: KeywordSet
    resolution_criteria: ResolutionCriteria
    retraction_policy: RetractionPolicy
    needs_disambiguation: bool = False
    warnings: list[str] = field(default_factory=list)
    raw: dict | None = None


class ParserError(Exception):
    """Raised when the parser cannot produce a valid ParsedSpec after
    one retry. The router maps this to a 422 with the message."""


_PROMPT_CACHE_KEY = "news_events.parser.v1"

_SYSTEM_PROMPT = """You are Pivot's news-event automation parser.

Translate the user's free-form text into a structured event spec.
The user is describing a real-world event they want to watch for,
typically as part of a trading rule (e.g. "If the RBI cuts the repo
rate, buy a PSU bank ETF" — the parser handles only the event half).

Return ONLY this JSON shape, with NO commentary:

{
  "description": "<one-sentence canonical description of the event>",
  "tier": "tier1" | "tier2" | "tier3",
  "keyword_set": {
    "must_have_one":        [<string>, ...],
    "must_have_one_of":     [[<string>, ...], ...],
    "must_not_have":        [<string>, ...]
  },
  "resolution_criteria": {
    "primary_sources":              [<source_id>, ...],
    "min_secondary_confirmations":  <integer 0-10>,
    "min_confidence":               <0.0-1.0>,
    "prediction_market_threshold":  <0.0-1.0> or null,
    "conflict_policy":              "hold" | "fire" | "alert"
  },
  "retraction_policy": {
    "safety_window_minutes": <integer 0-1440>,
    "action": "cancel_pending_approvals" | "cancel_and_alert" | "ignore"
  },
  "needs_disambiguation": true | false
}

TIER RULES:
- tier1 = official / scheduled events with a single authoritative
  source. RBI policy decisions, SEBI rulings, scheduled earnings
  releases, F&O expiry. Set ``min_secondary_confirmations: 0`` and
  ``conflict_policy: "fire"``.
- tier2 = corporate news / filings / market events with multiple
  publishers but low ambiguity. Earnings beats, rating upgrades,
  large M&A announcements. ``min_secondary_confirmations: 1`` is
  typical, ``conflict_policy: "hold"``.
- tier3 = political / geopolitical / prediction-market events.
  Elections, geopolitical conflict, sovereign ratings actions.
  HIGH ambiguity. Set ``needs_disambiguation: true``, leave
  ``primary_sources`` empty and ``min_secondary_confirmations: 1``,
  and pick ``conflict_policy: "hold"``.

KEYWORD-SET RULES:
- ``must_have_one``: 2-6 distinctive terms; the article passes if at
  least one is present in title+summary. CASE-INSENSITIVE substring.
- ``must_have_one_of``: list of lists. Use ONLY when the event
  has TWO orthogonal axes both of which must be present
  (e.g. "RBI" AND ("rate" OR "policy")).
- ``must_not_have``: 0-4 terms that, if present, rule the article out
  (e.g. "speculate", "analyst predicts", "rumour").

RETRACTION-POLICY DEFAULTS by tier:
- tier1: safety_window_minutes=60, action="cancel_and_alert"
- tier2: safety_window_minutes=120, action="cancel_and_alert"
- tier3: safety_window_minutes=240, action="cancel_pending_approvals"

VALID SOURCE IDS for primary_sources (Tier 1):
  rbi_press_releases, rbi_notifications, rbi_speeches

If the user mentions a regulator or scheduled event covered by one
of those IDs, list it. Otherwise leave ``primary_sources`` empty —
the funnel will still operate on the Tier-2/3 secondary feeds."""


def _extract_json(text: str) -> Optional[dict]:
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


class _ParseValidationError(ValueError):
    """Internal validation failure with a (loc, msg) breakdown for
    the retry prompt. Kept private; surface error is ``ParserError``.
    """

    def __init__(self, *, loc: str, msg: str):
        super().__init__(f"{loc}: {msg}")
        self.loc = loc
        self.msg = msg


def _validate_or_raise(raw: dict) -> ParsedSpec:
    """Coerce the raw JSON dict into a ParsedSpec; raise on any
    structural problem so the caller can format a retry prompt."""
    description = str(raw.get("description", "")).strip()
    if not description or len(description) < 4:
        raise _ParseValidationError(loc="description", msg="description too short")

    tier = str(raw.get("tier", "")).strip().lower()
    if tier not in {"tier1", "tier2", "tier3"}:
        raise _ParseValidationError(loc="tier", msg=f"unknown tier {tier!r}")

    try:
        keyword_set = KeywordSet.model_validate(raw.get("keyword_set", {}))
        resolution_criteria = ResolutionCriteria.model_validate(
            raw.get("resolution_criteria", {})
        )
        retraction_policy = RetractionPolicy.model_validate(
            raw.get("retraction_policy", {})
        )
    except ValidationError as ve:
        first = ve.errors()[0] if ve.errors() else {"msg": str(ve), "loc": ()}
        raise _ParseValidationError(
            loc="/".join(str(p) for p in first.get("loc", [])),
            msg=str(first.get("msg", ve)),
        ) from ve

    needs_disambiguation = bool(raw.get("needs_disambiguation", tier == "tier3"))
    # Tier 3 ALWAYS needs disambiguation — belt and suspenders.
    if tier == "tier3":
        needs_disambiguation = True

    return ParsedSpec(
        description=description,
        tier=tier,  # type: ignore[arg-type]
        keyword_set=keyword_set,
        resolution_criteria=resolution_criteria,
        retraction_policy=retraction_policy,
        needs_disambiguation=needs_disambiguation,
        raw=raw,
    )


async def parse_event_spec(text: str) -> ParsedSpec:
    """Top-level parser entry. Calls the LLM, validates, retries
    once with the validation error fed back into the prompt.
    Raises ``ParserError`` on second failure."""
    text = (text or "").strip()
    if len(text) < 4:
        raise ParserError("input text is too short to parse")

    client = get_llm_client()
    base_messages = [
        LLMMessage(role="system", content=_SYSTEM_PROMPT),
        LLMMessage(role="user", content=text),
    ]

    async def _call(messages: list[LLMMessage]) -> tuple[Optional[dict], Optional[str]]:
        try:
            response = await client.complete(
                messages=messages,
                response_format="json_object",
                reasoning_effort="minimal",
                temperature=0.0,
                max_output_tokens=900,
                prompt_cache_key=_PROMPT_CACHE_KEY,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("[news_events.parser] llm_failed err=%s", exc)
            return None, f"llm_failed: {exc}"
        raw = _extract_json(response.content or "")
        if raw is None:
            return None, f"parse_failed: not JSON ({(response.content or '')[:100]!r})"
        return raw, None

    raw, err = await _call(base_messages)
    if raw is None:
        raise ParserError(err or "parse_failed")

    try:
        return _validate_or_raise(raw)
    except _ParseValidationError as ve:
        feedback = (
            f"Your previous response failed validation: "
            f"{ve.msg!r} at {ve.loc}. Return ONLY the corrected JSON now."
        )
        logger.info("[news_events.parser] retry_with_feedback %s", feedback)
        retry_messages = base_messages + [
            LLMMessage(role="assistant", content=json.dumps(raw)),
            LLMMessage(role="user", content=feedback),
        ]
        raw2, err2 = await _call(retry_messages)
        if raw2 is None:
            raise ParserError(err2 or "retry_failed") from ve
        try:
            return _validate_or_raise(raw2)
        except _ParseValidationError as ve2:
            raise ParserError(f"validation_failed_after_retry: {ve2}") from ve2
