"""Unit tests for the ``fetch.news`` workflow step executor.

Replaces the deleted ``test_triggers.py`` — the dedicated triggers API
stack was retired in favour of step-types inside the workflow engine
(``backend/triggers/__init__.py``). The step executor reuses
``news_client.fetch_news`` and ``classifier.classify_article``; both
are stubbed here so the test stays hermetic.
"""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest

from backend.triggers.models import NewsArticle
from backend.workflows.steps import fetches as fetches_mod


def _make_article(
    *, art_id: str, title: str, source: str = "reuters",
) -> NewsArticle:
    return NewsArticle(
        id=art_id,
        title=title,
        description=f"{title} description",
        source=source,
        source_id=source,
        url=f"https://{source}.example/{art_id}",
        published_at=datetime(2026, 5, 13, 9, 30, tzinfo=timezone.utc),
        credibility_score=0.9,
    )


def _ctx(config: dict[str, Any]) -> SimpleNamespace:
    """Minimal stand-in for ``_ExecutorContext`` — the news executor
    only touches ``ctx.config``."""
    return SimpleNamespace(config=config)


@pytest.mark.asyncio
async def test_fetch_news_aggregates_match_from_classifier() -> None:
    """3 articles in; classifier marks one as a high-confidence match.

    The executor must surface ``matched=True``, the correct
    ``max_confidence``, and ``top_article.id`` pointing at the matched
    article.
    """
    articles = [
        _make_article(art_id="a1", title="RBI cuts repo rate by 25bps"),
        _make_article(art_id="a2", title="Analysts predict RBI may cut rates"),
        _make_article(art_id="a3", title="Unrelated banking headline"),
    ]

    async def fake_fetch_news(
        keywords: list[str], *, hours_back: int | None = None,
    ) -> list[NewsArticle]:
        assert keywords  # the executor passed the keywords through
        return list(articles)

    async def fake_classify_article(
        article: NewsArticle, event_description: str,
    ) -> tuple[bool, float, str]:
        if article.id == "a1":
            return True, 0.92, "yes — confirms the cut"
        return False, 0.4, "no — speculation"

    with patch.object(
        fetches_mod, "execute_fetch_news",
        wraps=fetches_mod.execute_fetch_news,
    ):
        # The executor imports its helpers lazily inside the function
        # body, so we patch at the source modules.
        with patch(
            "backend.triggers.news_client.fetch_news",
            side_effect=fake_fetch_news,
        ), patch(
            "backend.triggers.classifier.classify_article",
            side_effect=fake_classify_article,
        ):
            out = await fetches_mod.execute_fetch_news(_ctx({
                "keywords": ["RBI", "repo rate"],
                "event_description": "RBI cuts the repo rate",
                "min_confidence": 0.85,
                "hours_back": 24,
            }))

    assert out is not None
    assert out["matched"] is True
    assert out["matched_count"] == 1
    # max_confidence is across all classified articles, not just matches.
    assert out["max_confidence"] == pytest.approx(0.92)
    assert out["top_article"] is not None
    assert out["top_article"]["id"] == "a1"
    assert out["event_description"] == "RBI cuts the repo rate"
    assert len(out["articles"]) == 3


@pytest.mark.asyncio
async def test_fetch_news_empty_keywords_raises() -> None:
    """``keywords=[]`` is a config error — schema accepts a list, but the
    executor refuses to call NewsAPI with an empty query and raises so
    the engine surfaces a clean step failure (max_retries=3 still wraps
    transient HTTP errors only)."""
    with pytest.raises(ValueError, match="keywords"):
        await fetches_mod.execute_fetch_news(_ctx({
            "keywords": [],
            "event_description": "anything",
        }))


@pytest.mark.asyncio
async def test_fetch_news_empty_api_key_returns_empty_no_raise() -> None:
    """Mock-mode tolerance: when ``NEWSAPI_KEY`` is empty, the underlying
    client returns ``[]`` and the executor must surface the empty
    aggregate (``matched=False``, ``articles=[]``) without raising — a
    downstream ``condition.boolean`` then takes the false branch.
    """
    async def empty_fetch(
        keywords: list[str], *, hours_back: int | None = None,
    ) -> list[NewsArticle]:
        return []

    # Even though the classifier shouldn't be called (no articles), patch
    # it to a sentinel so the test fails loudly if the executor ever
    # invokes it on an empty list.
    async def boom_classify(
        article: NewsArticle, event_description: str,
    ) -> tuple[bool, float, str]:
        raise AssertionError(
            "classifier must not be called with zero articles"
        )

    with patch(
        "backend.triggers.news_client.fetch_news",
        side_effect=empty_fetch,
    ), patch(
        "backend.triggers.classifier.classify_article",
        side_effect=boom_classify,
    ):
        out = await fetches_mod.execute_fetch_news(_ctx({
            "keywords": ["RBI", "repo rate"],
            "event_description": "RBI cuts the repo rate",
        }))

    assert out == {
        "articles": [],
        "matched": False,
        "max_confidence": 0.0,
        "matched_count": 0,
        "top_article": None,
        "event_description": "RBI cuts the repo rate",
    }


@pytest.mark.asyncio
async def test_fetch_news_source_allowlist_filters_articles() -> None:
    """``sources=['reuters']`` drops everything not from Reuters before
    classification. Subtle: the dropped article must NOT be classified."""
    articles = [
        _make_article(art_id="r1", title="Reuters: RBI cuts repo", source="reuters"),
        _make_article(art_id="x1", title="Random blog post", source="yahoo-news"),
    ]
    classified: list[str] = []

    async def fake_fetch_news(
        keywords: list[str], *, hours_back: int | None = None,
    ) -> list[NewsArticle]:
        return list(articles)

    async def fake_classify(
        article: NewsArticle, event_description: str,
    ) -> tuple[bool, float, str]:
        classified.append(article.id)
        return True, 0.9, "ok"

    with patch(
        "backend.triggers.news_client.fetch_news",
        side_effect=fake_fetch_news,
    ), patch(
        "backend.triggers.classifier.classify_article",
        side_effect=fake_classify,
    ):
        out = await fetches_mod.execute_fetch_news(_ctx({
            "keywords": ["RBI"],
            "event_description": "RBI cuts the repo rate",
            "sources": ["reuters"],
        }))

    assert out is not None
    assert classified == ["r1"]
    assert len(out["articles"]) == 1
    assert out["matched"] is True
    assert out["top_article"]["id"] == "r1"


@pytest.mark.asyncio
async def test_fetch_news_min_confidence_floor() -> None:
    """An article classified as matched=True but with confidence below
    ``min_confidence`` must NOT count toward the aggregate ``matched``."""
    articles = [
        _make_article(art_id="m1", title="Maybe an RBI cut"),
    ]

    async def fake_fetch_news(
        keywords: list[str], *, hours_back: int | None = None,
    ) -> list[NewsArticle]:
        return list(articles)

    async def fake_classify(
        article: NewsArticle, event_description: str,
    ) -> tuple[bool, float, str]:
        return True, 0.7, "borderline"

    with patch(
        "backend.triggers.news_client.fetch_news",
        side_effect=fake_fetch_news,
    ), patch(
        "backend.triggers.classifier.classify_article",
        side_effect=fake_classify,
    ):
        out = await fetches_mod.execute_fetch_news(_ctx({
            "keywords": ["RBI"],
            "event_description": "RBI cuts the repo rate",
            "min_confidence": 0.85,
        }))

    assert out is not None
    assert out["matched"] is False
    assert out["matched_count"] == 0
    assert out["max_confidence"] == pytest.approx(0.7)
    assert out["top_article"] is None
