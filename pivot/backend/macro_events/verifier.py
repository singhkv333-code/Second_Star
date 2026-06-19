"""Layered macro-outcome verifier.

``verify_macro_outcome`` answers one question: *did the macro event with
this ``kind`` produce the ``expected_outcome`` the user is waiting for?*
It is the safety-critical gate between "the calendar says a decision is
due" and "fire a real order".

Three short-circuit tiers (user-confirmed "both, layered"):

  1. **Official-endpoint parse** — fetch the canonical RSS source for
     this kind (RBI / Fed / a Google-News CPI query) and isolate the
     release headline by keyword.
  2. **LLM confirm** — an LLM reads ONLY the fetched headline/summary
     and extracts the decision (cut/hold/hike) or the numeric figure
     (CPI). Anti-hallucination gate: the model's ``evidence`` quote must
     be a verbatim substring of the fetched text, else the answer is
     discarded as ``unknown``.
  3. **Prediction-market resolution fallback** — only when Tier 1/2 is
     inconclusive (no source text / low confidence / guard tripped).
     Conservative: confirms ``expected_outcome`` only off a clearly
     resolved matching market; otherwise ``unknown``.

Fail-safe everywhere: any uncertainty returns ``OutcomeResult.unknown``,
which the scheduler treats as "do not fire". A wrong calendar date or a
down feed therefore causes a missed/late fire, never a false one.

Every dependency (RSS fetch, LLM call, PM search) is injectable so the
unit tests run with zero network and a deterministic LLM.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Awaitable, Callable, Optional

from backend.macro_events.outcomes import OutcomeResult
from backend.macro_events.source_of_truth import (
    SourceOfTruth,
    get_source_of_truth,
)

logger = logging.getLogger(__name__)


# Injection seam types.
RssFetcher = Callable[[str, str], Awaitable[list]]
LlmComplete = Callable[[str, str], Awaitable[str]]
PmSearch = Callable[[str], Awaitable[list]]


_RATE_SYSTEM = (
    "You verify a central-bank interest-rate decision from an official "
    "news headline. You are given the verbatim headline + summary of a "
    "press release. Decide what the bank did to its policy rate.\n\n"
    "Return ONLY JSON: {\"decision\": \"cut\" | \"hold\" | \"hike\" | "
    "\"unknown\", \"confidence\": 0..1, \"evidence\": \"<a short verbatim "
    "quote copied EXACTLY from the provided text that justifies the "
    "decision>\"}.\n"
    "Rules: 'cut'/'reduce'/'lower'/'slash' → cut. 'hike'/'raise'/'increase' "
    "→ hike. 'unchanged'/'kept'/'held'/'status quo'/'maintains' → hold. "
    "If the text does not clearly state the rate action, decision is "
    "'unknown' with confidence 0. The evidence MUST be copied verbatim "
    "from the text — never paraphrase or invent."
)

_PRINT_SYSTEM = (
    "You extract a headline inflation (CPI) figure from an official news "
    "headline. You are given the verbatim headline + summary. Extract the "
    "single most relevant year-on-year CPI / retail-inflation percentage "
    "the release reports.\n\n"
    "Return ONLY JSON: {\"value\": <number or null>, \"confidence\": 0..1, "
    "\"evidence\": \"<a short verbatim quote copied EXACTLY from the text "
    "containing that figure>\"}.\n"
    "Rules: value is the percentage as a number (e.g. 4.8 for '4.8%'). If "
    "the text does not clearly state a CPI figure, value is null with "
    "confidence 0. The evidence MUST be copied verbatim from the text."
)


def _norm(text: str) -> str:
    """Lower-case + collapse whitespace for the evidence-substring guard."""
    return re.sub(r"\s+", " ", (text or "").lower()).strip()


def _evidence_supported(evidence: str, source_text: str) -> bool:
    """The model's evidence quote must appear verbatim (whitespace- and
    case-insensitive) in the fetched source text. Empty evidence fails.
    This is the primary anti-hallucination defence."""
    ev = _norm(evidence)
    if len(ev) < 4:  # too short to be meaningful evidence
        return False
    return ev in _norm(source_text)


async def _default_rss_fetch(source_id: str, feed_url: str) -> list:
    from backend.news_events.sources.rss import RSSAdapter

    return await RSSAdapter(source_id=source_id, feed_url=feed_url).fetch()


async def _default_llm_complete(system: str, user: str) -> str:
    from backend.llm import LLMMessage, get_llm_client

    client = get_llm_client()
    resp = await client.complete(
        messages=[
            LLMMessage(role="system", content=system),
            LLMMessage(role="user", content=user),
        ],
        response_format="json_object",
        reasoning_effort="minimal",
        max_output_tokens=300,
        temperature=0.0,
        prompt_cache_key="macro_verifier_v1",
    )
    if resp.finish_reason == "error":
        return ""
    return resp.content or ""


async def _default_pm_search(query: str) -> list:
    from backend.news_events.sources.polymarket import search_via_public_search

    return await search_via_public_search(query, limit=5)


def _parse_json(raw: str) -> Optional[dict[str, Any]]:
    raw = (raw or "").strip()
    if not raw:
        return None
    start = raw.find("{")
    end = raw.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        obj = json.loads(raw[start:end + 1])
    except json.JSONDecodeError:
        return None
    return obj if isinstance(obj, dict) else None


def _matched_items(items: list, keywords: tuple[str, ...]) -> list:
    """Filter RSS items to those whose title/summary contain a keyword,
    newest-first. Items are ``FetchedItem`` (title/summary/url)."""
    out = []
    for it in items:
        hay = ((getattr(it, "title", "") or "") + " "
               + (getattr(it, "summary", "") or "")).lower()
        if any(k in hay for k in keywords):
            out.append(it)
    return out


def _item_text(it: Any) -> str:
    title = getattr(it, "title", "") or ""
    summary = getattr(it, "summary", "") or ""
    return f"{title}\n{summary}".strip()


def _compare(value: float, comparison: str, threshold: float) -> bool:
    if comparison == ">":
        return value > threshold
    if comparison == ">=":
        return value >= threshold
    if comparison == "<":
        return value < threshold
    if comparison == "<=":
        return value <= threshold
    if comparison in ("==", "="):
        return value == threshold
    return False


async def _verify_official(
    sot: SourceOfTruth,
    expected_outcome: str,
    *,
    min_confidence: float,
    comparison: Optional[str],
    threshold: Optional[float],
    rss_fetch: RssFetcher,
    llm_complete: LlmComplete,
) -> OutcomeResult:
    """Tiers 1+2. Returns a confident verdict (matched True/False with a
    real decision), or ``unknown`` to signal the caller to try Tier 3."""
    from backend.news_events.config import get_source

    src = get_source(sot.primary_source_id)
    if src is None:
        return OutcomeResult.unknown(
            reason=f"source {sot.primary_source_id} not registered",
            tier="official",
        )
    try:
        items = await rss_fetch(src.source_id, src.feed_url)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[macro_verifier] rss fetch failed kind=%s err=%s",
                       sot.kind, exc)
        return OutcomeResult.unknown(reason=f"rss fetch failed: {exc}",
                                     tier="official")

    matched = _matched_items(items or [], sot.match_keywords)[:3]
    if not matched:
        return OutcomeResult.unknown(reason="no matching release headline",
                                     tier="official")

    source_text = "\n\n".join(_item_text(it) for it in matched)
    system = _RATE_SYSTEM if sot.decision_kind == "rate" else _PRINT_SYSTEM
    user = (
        f"Macro event: {sot.label}.\n\n"
        f"Press headlines/summaries:\n\"\"\"\n{source_text}\n\"\"\""
    )
    raw = await llm_complete(system, user)
    parsed = _parse_json(raw)
    if not parsed:
        return OutcomeResult.unknown(reason="llm returned no parseable JSON",
                                     tier="llm")

    try:
        confidence = max(0.0, min(1.0, float(parsed.get("confidence", 0.0))))
    except (TypeError, ValueError):
        confidence = 0.0
    evidence = str(parsed.get("evidence", "") or "")

    # Anti-hallucination gate: evidence must be verbatim in the source.
    if not _evidence_supported(evidence, source_text):
        return OutcomeResult.unknown(
            reason="evidence quote not found in source text (guard tripped)",
            tier="llm",
        )

    top = matched[0]
    audit = {
        "tier": "official",
        "source_id": sot.primary_source_id,
        "headline": getattr(top, "title", None),
        "url": getattr(top, "url", None),
        "llm_confidence": confidence,
        "evidence": evidence,
    }

    if sot.decision_kind == "rate":
        decision = str(parsed.get("decision", "unknown")).strip().lower()
        if decision not in {"cut", "hold", "hike"}:
            return OutcomeResult.unknown(reason="llm decision not recognised",
                                         tier="llm")
        if confidence < min_confidence:
            return OutcomeResult.unknown(
                reason=f"low confidence {confidence:.2f}", tier="llm")
        return OutcomeResult(
            matched=(decision == expected_outcome),
            decision=decision,  # type: ignore[arg-type]
            confidence=confidence,
            tier="official",
            evidence=evidence,
            audit={**audit, "decision": decision},
        )

    # print kinds (CPI) — extract numeric figure and compare to threshold.
    if comparison is None or threshold is None:
        return OutcomeResult.unknown(
            reason="print kind requires comparison + threshold to judge",
            tier="llm",
        )
    raw_val = parsed.get("value")
    if raw_val is None:
        return OutcomeResult.unknown(reason="no CPI figure extracted",
                                     tier="llm")
    try:
        value = float(raw_val)
    except (TypeError, ValueError):
        return OutcomeResult.unknown(reason="CPI figure not numeric",
                                     tier="llm")
    if confidence < min_confidence:
        return OutcomeResult.unknown(reason=f"low confidence {confidence:.2f}",
                                     tier="llm")
    is_met = _compare(value, comparison, threshold)
    decision = "met" if is_met else "not_met"
    return OutcomeResult(
        matched=(decision == expected_outcome),
        decision=decision,  # type: ignore[arg-type]
        confidence=confidence,
        tier="official",
        evidence=evidence,
        audit={**audit, "value": value, "comparison": comparison,
               "threshold": threshold, "decision": decision},
    )


async def _verify_prediction_market(
    sot: SourceOfTruth,
    expected_outcome: str,
    *,
    pm_search: PmSearch,
) -> OutcomeResult:
    """Tier 3 — conservative resolution fallback. Confirms the expected
    outcome ONLY off a clearly resolved matching market (closed, YES
    price ≥ 0.95). Otherwise ``unknown``. Never contradicts a confident
    official answer — this only runs when Tier 1/2 was inconclusive."""
    if not sot.pm_fallback_query:
        return OutcomeResult.unknown(reason="no pm fallback configured",
                                     tier="prediction_market")
    try:
        snaps = await pm_search(sot.pm_fallback_query)
    except Exception as exc:  # noqa: BLE001
        return OutcomeResult.unknown(reason=f"pm search failed: {exc}",
                                     tier="prediction_market")
    for snap in snaps or []:
        closed = bool(getattr(snap, "closed", False))
        yes = float(getattr(snap, "yes_price", 0.0) or 0.0)
        if closed and yes >= 0.95:
            # A resolved-YES market matching the fallback query confirms
            # the expected outcome (the query is phrased for it).
            return OutcomeResult(
                matched=True,
                decision=expected_outcome,  # type: ignore[arg-type]
                confidence=yes,
                tier="prediction_market",
                evidence=getattr(snap, "question", None),
                audit={
                    "tier": "prediction_market",
                    "market_id": getattr(snap, "market_id", None),
                    "question": getattr(snap, "question", None),
                    "yes_price": yes,
                },
            )
    return OutcomeResult.unknown(reason="no resolved market confirmed outcome",
                                 tier="prediction_market")


async def verify_macro_outcome(
    kind: str,
    expected_outcome: str,
    *,
    min_confidence: float = 0.85,
    comparison: Optional[str] = None,
    threshold: Optional[float] = None,
    allow_prediction_market_fallback: bool = True,
    rss_fetch: RssFetcher | None = None,
    llm_complete: LlmComplete | None = None,
    pm_search: PmSearch | None = None,
) -> OutcomeResult:
    """Layered verification entry point. See module docstring.

    Returns an :class:`OutcomeResult`; the caller fires only when
    ``.matched`` is True.
    """
    sot = get_source_of_truth(kind)
    if sot is None:
        return OutcomeResult.unknown(reason=f"unknown macro kind {kind!r}")

    rss_fetch = rss_fetch or _default_rss_fetch
    llm_complete = llm_complete or _default_llm_complete
    pm_search = pm_search or _default_pm_search

    official = await _verify_official(
        sot, expected_outcome,
        min_confidence=min_confidence,
        comparison=comparison,
        threshold=threshold,
        rss_fetch=rss_fetch,
        llm_complete=llm_complete,
    )
    # A confident official verdict (matched, OR a recognised decision that
    # simply isn't the user's target) is authoritative — return it.
    if official.decision != "unknown":
        return official

    # Tier 1/2 inconclusive → conservative prediction-market fallback.
    if allow_prediction_market_fallback:
        pm = await _verify_prediction_market(
            sot, expected_outcome, pm_search=pm_search,
        )
        if pm.matched:
            return pm

    return official  # the unknown verdict (carries the tier/reason audit)
