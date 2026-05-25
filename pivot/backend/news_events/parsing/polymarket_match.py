"""LLM-driven Polymarket contract matcher.

Given a free-text event description from chat ("alert me if BJP wins
the 2029 general election"), this module:

  1. Hits Polymarket's Gamma /markets search with the description.
  2. Pulls the top-K open binary markets as candidates, extracting
     each one's YES / NO token ids from ``raw["clobTokenIds"]``.
  3. Hands the (description, candidates) pair to the LLM with a
     strict JSON contract so the model picks ONE candidate + which
     side (YES / NO) the user is asking about.
  4. Returns a ``MatchResult`` carrying the chosen market_id +
     token_id + confidence, plus the full candidate list so a
     low-confidence result can be surfaced as a chat picker.

Why pick YES vs NO at match time:
  The threshold trigger needs a token_id, and YES vs NO matters: a
  user who says "alert me if Trump becomes president > 80%" wants
  the YES token's price >= 0.80. A user who says "alert me if Modi
  WON'T be PM by 2029 > 60%" wants the NO token's price >= 0.60.
  The LLM hop is the only thing that can tell those apart from
  natural language.

Never raises. Failures (network down, LLM down, malformed JSON)
collapse to ``MatchResult(matched=False, candidates=[], reason=...)``
so the caller can prompt the user to retry or pick manually.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Literal, Optional

from backend.llm.base import LLMMessage
from backend.llm.factory import get_llm_client
from backend.news_events.sources.polymarket import (
    PolymarketSnapshot,
    search_markets,
)

logger = logging.getLogger(__name__)


Side = Literal["YES", "NO"]


_PROMPT_CACHE_KEY = "news_events.polymarket_match.v1"
_DEFAULT_TOP_K = 8
_MIN_AUTO_PICK_CONFIDENCE = 0.70


_SYSTEM_PROMPT = """You are Pivot's prediction-market contract matcher.

You receive:
  - a USER EVENT DESCRIPTION (free text, sometimes vague)
  - a list of CANDIDATE Polymarket binary markets (each with an
    index, a question, and the current YES price)

Your job: pick the single best matching candidate AND say which
side (YES or NO) the user is asking about.

Return ONLY this JSON:

{
  "match_index": <integer index into candidates, or null if no good match>,
  "side": "YES" | "NO",
  "confidence": <float in [0, 1]>,
  "reason": "<one short sentence>"
}

Rules:
- ``match_index`` is null if none of the candidates is a clearly
  better match than chance. Don't force a match.
- ``side`` is YES iff the user's event is asking about the AFFIRMATIVE
  resolution of the candidate's question (the question and the user
  event line up directly). It is NO when the user's event is the
  NEGATION of the candidate's question (e.g. user wants "Modi WON'T
  be PM" and the candidate is "Will Modi be PM by 2029?", side = NO).
- ``confidence`` reflects your confidence in BOTH the candidate
  choice and the side choice. Use < 0.7 when there are multiple
  similarly plausible candidates or the user description is too
  vague to disambiguate.
- ``reason`` is one short sentence the user will see.

Keep the JSON tight — no markdown fence, no extra prose, no
trailing comma."""


@dataclass(frozen=True)
class Candidate:
    """One candidate market with both token ids unpacked."""

    market_id: str
    slug: Optional[str]
    question: Optional[str]
    yes_price: float
    yes_token_id: Optional[str]
    no_token_id: Optional[str]
    closed: bool


@dataclass
class MatchResult:
    """Output of ``match_event_to_polymarket_contract``.

    On a strong match (``matched=True``):
        market_id, token_id, side, question, confidence — all set.

    On a weak match (``matched=False``):
        candidates — surface to the user so they can pick manually
                     in the chat UI.
        reason — short string explaining why we didn't auto-pick
                 (e.g. "no candidates found", "low confidence",
                 "llm parse failed").
    """

    matched: bool
    market_id: Optional[str] = None
    token_id: Optional[str] = None
    side: Optional[Side] = None
    question: Optional[str] = None
    confidence: float = 0.0
    reason: str = ""
    candidates: list[Candidate] = field(default_factory=list)


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


def _extract_token_ids(snapshot: PolymarketSnapshot) -> tuple[Optional[str], Optional[str]]:
    """Pull (yes_token_id, no_token_id) out of the raw Gamma payload.

    Gamma returns ``clobTokenIds`` as a parallel array to ``outcomes``.
    Both can be JSON-encoded strings or actual lists depending on the
    endpoint — defensive parse for both. Returns (None, None) on any
    shape mismatch.
    """
    raw = snapshot.raw or {}
    outcomes = raw.get("outcomes")
    tokens = raw.get("clobTokenIds")
    if isinstance(outcomes, str):
        try:
            outcomes = json.loads(outcomes)
        except json.JSONDecodeError:
            outcomes = None
    if isinstance(tokens, str):
        try:
            tokens = json.loads(tokens)
        except json.JSONDecodeError:
            tokens = None
    if not (isinstance(outcomes, list) and isinstance(tokens, list)
            and len(outcomes) == len(tokens)):
        return None, None
    yes_tok: Optional[str] = None
    no_tok: Optional[str] = None
    for label, tok in zip(outcomes, tokens):
        norm = str(label).strip().lower()
        if norm in {"yes", "true"} and yes_tok is None:
            yes_tok = str(tok)
        elif norm in {"no", "false"} and no_tok is None:
            no_tok = str(tok)
    return yes_tok, no_tok


def _to_candidate(snapshot: PolymarketSnapshot) -> Optional[Candidate]:
    yes_tok, no_tok = _extract_token_ids(snapshot)
    if not yes_tok and not no_tok:
        return None
    return Candidate(
        market_id=snapshot.market_id,
        slug=snapshot.slug,
        question=snapshot.question,
        yes_price=float(snapshot.yes_price),
        yes_token_id=yes_tok,
        no_token_id=no_tok,
        closed=bool(snapshot.closed),
    )


def _build_user_message(event_description: str, candidates: list[Candidate]) -> str:
    lines = [f"USER EVENT DESCRIPTION:\n{event_description.strip()}\n",
             "CANDIDATES:"]
    for i, c in enumerate(candidates):
        lines.append(
            f"[{i}] {c.question or '(no question text)'} "
            f"— YES price: {c.yes_price:.3f}"
        )
    lines.append("\nReturn the JSON now.")
    return "\n".join(lines)


async def match_event_to_polymarket_contract(
    event_description: str,
    *,
    top_k: int = _DEFAULT_TOP_K,
    min_auto_pick_confidence: float = _MIN_AUTO_PICK_CONFIDENCE,
) -> MatchResult:
    """Top-level entry. See module docstring for behaviour."""
    desc = (event_description or "").strip()
    if not desc:
        return MatchResult(matched=False, reason="empty event description")

    snapshots = await search_markets(desc, limit=top_k)
    if not snapshots:
        return MatchResult(matched=False, reason="no candidates returned by Polymarket")

    candidates = [c for c in (_to_candidate(s) for s in snapshots) if c is not None]
    if not candidates:
        return MatchResult(
            matched=False,
            reason="candidates returned but none had token ids",
            candidates=[],
        )

    client = get_llm_client()
    user_msg = _build_user_message(desc, candidates)
    try:
        response = await client.complete(
            messages=[
                LLMMessage(role="system", content=_SYSTEM_PROMPT),
                LLMMessage(role="user", content=user_msg),
            ],
            response_format="json_object",
            reasoning_effort="minimal",
            max_output_tokens=300,
            temperature=0.0,
            prompt_cache_key=_PROMPT_CACHE_KEY,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("[polymarket_match] llm_failed err=%s", exc)
        return MatchResult(
            matched=False,
            reason=f"llm_failed: {exc}",
            candidates=candidates,
        )

    parsed = _parse_json(response.content or "")
    if not parsed:
        logger.warning(
            "[polymarket_match] parse_failed content=%r",
            (response.content or "")[:200],
        )
        return MatchResult(
            matched=False,
            reason="llm response was not valid JSON",
            candidates=candidates,
        )

    match_index = parsed.get("match_index")
    if match_index is None:
        return MatchResult(
            matched=False,
            reason=str(parsed.get("reason") or "llm declined to pick"),
            candidates=candidates,
        )
    try:
        idx = int(match_index)
    except (TypeError, ValueError):
        return MatchResult(
            matched=False,
            reason=f"llm returned non-int match_index={match_index!r}",
            candidates=candidates,
        )
    if not (0 <= idx < len(candidates)):
        return MatchResult(
            matched=False,
            reason=f"llm returned out-of-range match_index={idx}",
            candidates=candidates,
        )

    side_raw = str(parsed.get("side", "YES")).strip().upper()
    side: Side = "YES" if side_raw not in {"NO"} else "NO"

    try:
        confidence = float(parsed.get("confidence", 0.0))
    except (TypeError, ValueError):
        confidence = 0.0
    confidence = max(0.0, min(1.0, confidence))
    reason = str(parsed.get("reason") or "").strip()[:500]

    chosen = candidates[idx]
    token_id = chosen.yes_token_id if side == "YES" else chosen.no_token_id
    if not token_id:
        return MatchResult(
            matched=False,
            reason=f"chosen candidate has no {side} token id",
            candidates=candidates,
        )

    if confidence < min_auto_pick_confidence:
        return MatchResult(
            matched=False,
            reason=(
                f"low confidence ({confidence:.2f} < "
                f"{min_auto_pick_confidence:.2f}): {reason or '(no reason)'}"
            ),
            candidates=candidates,
            # Surface the chosen index too so the picker can pre-highlight
            # the LLM's best guess.
            market_id=chosen.market_id,
            token_id=token_id,
            side=side,
            question=chosen.question,
            confidence=confidence,
        )

    return MatchResult(
        matched=True,
        market_id=chosen.market_id,
        token_id=token_id,
        side=side,
        question=chosen.question,
        confidence=confidence,
        reason=reason,
        candidates=candidates,
    )
