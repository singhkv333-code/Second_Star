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
    search_via_public_search,
)

logger = logging.getLogger(__name__)


Side = Literal["YES", "NO"]


_PROMPT_CACHE_KEY = "news_events.polymarket_match.v1"
_DEFAULT_TOP_K = 8
_MIN_AUTO_PICK_CONFIDENCE = 0.70


# Imperative prefixes the chat user typically wraps around their actual
# event description. Polymarket's /public-search is keyword-based and
# returns 0 hits when the query starts with these. Stripped before the
# search hop; the LLM still sees the raw description so it can
# disambiguate intent (e.g. negation).
_PREFIX_PATTERNS: tuple[str, ...] = (
    "alert me if ", "alert me when ", "tell me if ", "tell me when ",
    "let me know if ", "let me know when ", "ping me if ", "ping me when ",
    "notify me if ", "notify me when ", "watch for ", "wake me up if ",
    "wake me up when ", "send me a ping if ", "send me a ping when ",
)
# Threshold-clause suffixes that aren't part of the event itself.
# We drop everything from the first occurrence onward.
_THRESHOLD_SUFFIX_MARKERS: tuple[str, ...] = (
    " probability above ", " probability below ", " probability >= ",
    " probability <= ", " goes above ", " goes below ",
    " crosses above ", " crosses below ", " above ", " below ",
)


def _search_query_from_description(desc: str) -> str:
    """Strip chat-imperative prefixes + trailing threshold clauses so
    Polymarket's keyword search has a clean event phrase to match.

    Conservative — we only strip when the prefix is a clear chat
    convenience phrase. The LLM still sees the raw description for
    side disambiguation, so over-stripping costs less than over-keeping.
    """
    s = (desc or "").strip()
    if not s:
        return s
    lower = s.lower()
    for prefix in _PREFIX_PATTERNS:
        if lower.startswith(prefix):
            s = s[len(prefix):].lstrip()
            lower = s.lower()
            break
    # Drop the threshold clause if any marker is present beyond
    # position 8 (so we don't truncate a 2-word query like "above zero").
    for marker in _THRESHOLD_SUFFIX_MARKERS:
        idx = lower.find(marker)
        if idx > 8:
            s = s[:idx].rstrip()
            lower = s.lower()
    # Trim trailing question marks / quotes that chat users tack on.
    return s.strip("?.,!\"' ").strip()


# English stop-words we drop in the keyword fallback. Polymarket's
# /public-search appears to AND tokens, so long natural-language
# queries over-restrict. The fallback keeps only content words.
_STOP_WORDS: frozenset[str] = frozenset({
    "a", "an", "the", "and", "or", "but", "of", "for", "in", "at", "on",
    "to", "by", "with", "from", "as", "is", "be", "was", "were", "are",
    "am", "been", "being", "do", "does", "did", "have", "has", "had",
    "will", "would", "shall", "should", "may", "might", "can", "could",
    "if", "when", "while", "until", "during", "after", "before", "next",
    "this", "that", "these", "those", "it", "its", "i", "me", "my",
    "we", "us", "our", "you", "your", "he", "she", "they", "them",
    "their", "his", "her", "any", "some", "all", "no", "not", "very",
    "really", "soon", "ever", "now", "today",
})


def _keyword_fallback_query(desc: str) -> str:
    """Keep only non-stopword tokens, preserving order. Used as a
    second-chance query when the cleaned query returns zero markets.

    Example: 'Will India win the next T20 World Cup'
        → 'India win T20 World Cup'
    """
    s = _search_query_from_description(desc)
    tokens = [
        t for t in s.split()
        if t.lower().strip(",.?!:;\"'") not in _STOP_WORDS
    ]
    return " ".join(tokens).strip()


# All-caps / capitalized tokens that LOOK like entities but are
# actually shouting modals / negations / answer literals. They match
# tangentially-related markets ("not meet", "Will X") and pollute the
# ranking. Filtered out by the entity-fallback extractor.
_ENTITY_DROP_TOKENS: frozenset[str] = frozenset({
    "not", "no", "yes", "will", "won't", "wont",
    "should", "shall", "would", "could", "may", "might",
    "going", "happen", "happens", "true", "false",
})


def _entity_fallback_query(desc: str) -> str:
    """Aggressive fallback: keep only proper nouns + numeric tokens.

    Polymarket's /public-search appears to rank by total token overlap,
    so a long natural-language query can score a tangentially related
    market higher than the actually-correct one. This fallback yanks
    out the entities — capitalized words, numbers, dollar amounts —
    which tend to be the discriminating tokens.

    Tokens whose lowercased form is in ``_ENTITY_DROP_TOKENS`` or the
    general stop-word set are dropped even when capitalized — "NOT"
    and "Will" otherwise pollute the search.

    Example: 'Trump wins the 2028 US presidential election'
        → 'Trump 2028 US'
    'Trump does NOT win the 2028 US election'
        → 'Trump 2028 US' (NOT dropped)
    """
    s = _search_query_from_description(desc)
    out: list[str] = []
    for raw in s.split():
        tok = raw.strip(",.?!:;\"'")
        if not tok:
            continue
        # Drop only modal/negation/answer-literal tokens. Don't apply
        # the general stop-word set here — it contains pronouns like
        # 'us' that overlap with legitimate acronyms ("US"). The
        # uppercase/digit/$ gate below already filters lowercase
        # function words.
        if tok.lower() in _ENTITY_DROP_TOKENS:
            continue
        if (any(ch.isdigit() for ch in tok)
                or tok[0].isupper()
                or tok[0] == "$"):
            out.append(tok)
    return " ".join(out).strip()


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

    # Four-tier search chain. Polymarket's /public-search ranks by
    # total token overlap, so a long natural-language query can
    # actually score the WRONG (older / tangentially related) market
    # highest. We fan out: cleaned phrase → keyword (stop-words off)
    # → entity-only (proper nouns + numbers) → raw description.
    # Stop on the first chain that returns ≥ 1 open active market;
    # if all four return empty, the user genuinely has no match.
    primary_q = _search_query_from_description(desc) or desc
    keyword_q = _keyword_fallback_query(desc)
    entity_q = _entity_fallback_query(desc)

    tried: list[str] = []
    snapshots: list[PolymarketSnapshot] = []

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
            reason=f"no open markets matched any of {tried!r}",
        )

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
