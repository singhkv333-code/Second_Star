"""Stage 4 — embedding similarity.

Phase 3 uses OpenAI's ``text-embedding-3-small`` (1536 dimensions,
~$0.02 per 1M tokens). We call the API directly via httpx so this
module doesn't depend on the OpenAI Python SDK (the existing backend
doesn't either).

The flow per (article, spec):

  1. ``ensure_spec_embedding(spec)`` — compute and cache the spec's
     description embedding the first time we see it. Persisted on
     ``news_event_specs.description_embedding``.
  2. ``ensure_article_embedding(article)`` — compute the article's
     embedding over ``(title + summary + body_text)``. Persisted on
     ``news_articles.text_embedding``.
  3. ``cosine_similarity(a, b)`` — standard cosine on the two
     vectors. The funnel rejects pairs below ``SIM_THRESHOLD`` so the
     LLM-bound Stages 5+6 only see plausibly-on-topic articles.

The threshold is intentionally low (0.20). Per Anthropic guidance,
``text-embedding-3-small`` produces vectors where unrelated pairs sit
around 0.05–0.15 and tangentially-related pairs around 0.20–0.35;
0.20 keeps the LLM cost down while accepting the messy middle. Tune
upward if Phase 6 metrics show too many AMBIGUOUS verdicts.
"""
from __future__ import annotations

import logging
import math
from typing import Optional, Sequence

import httpx

from backend.config import settings

logger = logging.getLogger(__name__)


EMBEDDING_MODEL: str = "text-embedding-3-small"
EMBEDDING_DIM: int = 1536
# Stage 4 gate. Pairs below this cosine never reach Stage 5.
SIM_THRESHOLD: float = 0.20
# Cap the input length so a runaway body doesn't blow up the API call.
# 8191 tokens is the model's limit; we cap on chars (~4 chars/token).
_MAX_INPUT_CHARS: int = 30_000


class EmbeddingClientError(Exception):
    """Raised when the embeddings API returns an unrecoverable error.
    Callers persist a None embedding and try again on the next tick.
    """


async def embed_text(text: str) -> list[float]:
    """Call OpenAI's /v1/embeddings and return the float vector.

    Empty / whitespace input returns a zero vector — vector arithmetic
    against a zero vector yields cosine = 0, which falls below
    ``SIM_THRESHOLD`` and short-circuits the funnel naturally.
    """
    text = (text or "").strip()
    if not text:
        return [0.0] * EMBEDDING_DIM
    text = text[:_MAX_INPUT_CHARS]

    if not settings.openai_api_key:
        raise EmbeddingClientError(
            "OPENAI_API_KEY is empty — embedding stage cannot run"
        )

    headers = {
        "Authorization": f"Bearer {settings.openai_api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": EMBEDDING_MODEL,
        "input": text,
        "encoding_format": "float",
    }
    try:
        async with httpx.AsyncClient(timeout=20.0, headers=headers) as client:
            resp = await client.post(
                "https://api.openai.com/v1/embeddings",
                json=payload,
            )
    except httpx.HTTPError as exc:
        raise EmbeddingClientError(f"network error: {exc}") from exc

    if resp.status_code != 200:
        raise EmbeddingClientError(
            f"upstream {resp.status_code}: {resp.text[:200]}"
        )

    try:
        data = resp.json()
        return list(data["data"][0]["embedding"])
    except (KeyError, IndexError, ValueError) as exc:
        raise EmbeddingClientError(f"malformed response: {exc}") from exc


def cosine_similarity(a: Sequence[float], b: Sequence[float]) -> float:
    """Cosine similarity in [-1, 1]. Returns 0.0 on dimension mismatch
    or zero-vector input — same end effect as 'not related'."""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = 0.0
    na = 0.0
    nb = 0.0
    for x, y in zip(a, b):
        dot += x * y
        na += x * x
        nb += y * y
    if na == 0 or nb == 0:
        return 0.0
    return dot / math.sqrt(na * nb)


# ── DB-bound helpers (kept thin so tests can mock embed_text directly) ──


async def ensure_spec_embedding(
    *,
    db,
    spec,
) -> Optional[list[float]]:
    """Compute the spec's description embedding once and persist it.

    Returns the cached or freshly-computed vector. None on embedding
    failure — caller will treat similarity as 0 and skip the LLM
    stages for this tick.
    """
    cached = spec.description_embedding
    if cached:
        return list(cached)
    try:
        vec = await embed_text(spec.description)
    except EmbeddingClientError as exc:
        logger.warning(
            "[news_events.embed] spec_embedding_failed spec_id=%s err=%s",
            spec.id,
            exc,
        )
        return None
    spec.description_embedding = vec
    db.flush()
    return vec


async def ensure_article_embedding(
    *,
    db,
    article,
) -> Optional[list[float]]:
    """Compute the article embedding once (title + summary + body)."""
    cached = article.text_embedding
    if cached:
        return list(cached)
    parts = [article.title or ""]
    if article.summary:
        parts.append(article.summary)
    if article.body_text:
        parts.append(article.body_text)
    text = "\n\n".join(p for p in parts if p)
    try:
        vec = await embed_text(text)
    except EmbeddingClientError as exc:
        logger.warning(
            "[news_events.embed] article_embedding_failed article_id=%s err=%s",
            article.id,
            exc,
        )
        return None
    article.text_embedding = vec
    db.flush()
    return vec
