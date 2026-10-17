"""LLM-driven Kalshi contract matcher.

Near-verbatim sibling of ``polymarket_match`` — it reuses that module's
four-tier search-query chain and the ``Candidate`` / ``MatchResult`` /
``Side`` dataclasses unchanged (venue-agnostic), and differs only in:

  - searching Kalshi (``sources.kalshi.search_via_public_search``), and
  - token-id extraction: Kalshi has ONE ticker per binary market, so we
    synthesize per-side asset ids ``{ticker}:YES`` / ``{ticker}:NO``
    (via ``kalshi_asset_id``) instead of reading a clobTokenIds array.

Never raises — failures collapse to ``MatchResult(matched=False, ...)``.
"""
from __future__ import annotations

import logging
from typing import Optional

from backend.llm.base import LLMMessage
from backend.llm.factory import get_llm_client
from backend.news_events.parsing.polymarket_match import (
    Candidate,
    MatchResult,
    Side,
    _build_user_message,
    _entity_fallback_query,
    _keyword_fallback_query,
    _parse_json,
    _search_query_from_description,
)
from backend.news_events.sources.kalshi import (
    KalshiSnapshot,
    kalshi_asset_id,
    search_via_public_search,
)

logger = logging.getLogger(__name__)


_PROMPT_CACHE_KEY = "news_events.kalshi_match.v1"
_DEFAULT_TOP_K = 8
_MIN_AUTO_PICK_CONFIDENCE = 0.70


_SYSTEM_PROMPT = """You are Pivot's prediction-market contract matcher.

You receive:
  - a USER EVENT DESCRIPTION (free text, sometimes vague)
  - a list of CANDIDATE Kalshi binary markets (each with an index, a
    question, and the current YES price)

Your job: pick the single best matching candidate AND say which side
(YES or NO) the user is asking about.

Return ONLY this JSON:

{
  "match_index": <integer index into candidates, or null if no good match>,
  "side": "YES" | "NO",
  "confidence": <float in [0, 1]>,
  "reason": "<one short sentence>"
}

Rules:
- ``match_index`` is null if none of the candidates is a clearly better
  match than chance. Don't force a match.
- ``side`` is YES iff the user's event asks about the AFFIRMATIVE
  resolution of the candidate's question; NO when the user's event is
  the NEGATION of the candidate's question.
- ``confidence`` reflects confidence in BOTH the candidate choice and
  the side choice. Use < 0.7 when multiple candidates are similarly
  plausible or the description is too vague.
- ``reason`` is one short sentence the user will see.

Keep the JSON tight — no markdown fence, no extra prose."""


def _extract_token_ids(
    snapshot: KalshiSnapshot,
) -> tuple[Optional[str], Optional[str]]:
    """Kalshi has one ticker per binary market; synthesize per-side
    asset ids so the candidate carries both 'token' slots."""
    tk = snapshot.market_id
    if not tk:
        return None, None
    return kalshi_asset_id(tk, "YES"), kalshi_asset_id(tk, "NO")


def _to_candidate(snapshot: KalshiSnapshot) -> Optional[Candidate]:
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


async def match_event_to_kalshi_contract(
    event_description: str,
    *,
    top_k: int = _DEFAULT_TOP_K,
    min_auto_pick_confidence: float = _MIN_AUTO_PICK_CONFIDENCE,
) -> MatchResult:
    """Top-level entry — mirrors match_event_to_polymarket_contract."""
    desc = (event_description or "").strip()
    if not desc:
        return MatchResult(matched=False, reason="empty event description")

    primary_q = _search_query_from_description(desc) or desc
    keyword_q = _keyword_fallback_query(desc)
    entity_q = _entity_fallback_query(desc)

    tried: list[str] = []
    snapshots: list[KalshiSnapshot] = []
    for q in (entity_q, keyword_q, primary_q, desc):
        if not q or q in tried:
            continue
        tried.append(q)
        snapshots = await search_via_public_search(q, limit=top_k)
        if snapshots:
            break

    if not snapshots:
        return MatchResult(
            matched=False,
            reason=f"no open kalshi markets matched any of {tried!r}",
        )

    candidates = [c for c in (_to_candidate(s) for s in snapshots) if c is not None]
    if not candidates:
        return MatchResult(
            matched=False,
            reason="candidates returned but none had a ticker",
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
        logger.warning("[kalshi_match] llm_failed err=%s", exc)
        return MatchResult(matched=False, reason=f"llm_failed: {exc}",
                           candidates=candidates)

    parsed = _parse_json(response.content or "")
    if not parsed:
        return MatchResult(matched=False, reason="llm response was not valid JSON",
                           candidates=candidates)

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
        return MatchResult(matched=False,
                           reason=f"llm non-int match_index={match_index!r}",
                           candidates=candidates)
    if not (0 <= idx < len(candidates)):
        return MatchResult(matched=False,
                           reason=f"llm out-of-range match_index={idx}",
                           candidates=candidates)

    side_raw = str(parsed.get("side", "YES")).strip().upper()
    side: Side = "NO" if side_raw == "NO" else "YES"
    try:
        confidence = max(0.0, min(1.0, float(parsed.get("confidence", 0.0))))
    except (TypeError, ValueError):
        confidence = 0.0
    reason = str(parsed.get("reason") or "").strip()[:500]

    chosen = candidates[idx]
    token_id = chosen.yes_token_id if side == "YES" else chosen.no_token_id
    if not token_id:
        return MatchResult(matched=False,
                           reason=f"chosen candidate has no {side} asset id",
                           candidates=candidates)

    if confidence < min_auto_pick_confidence:
        return MatchResult(
            matched=False,
            reason=(f"low confidence ({confidence:.2f} < "
                    f"{min_auto_pick_confidence:.2f}): {reason or '(no reason)'}"),
            candidates=candidates,
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
