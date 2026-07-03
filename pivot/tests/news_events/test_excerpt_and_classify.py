"""Tests for Stages 5 and 6 with a stubbed LLM client.

We monkey-patch ``backend.news_events.pipeline.excerpt.get_llm_client``
(and the same in ``classify``) to return a fake client whose
``.complete(...)`` yields a fixed JSON response. That lets us assert
the parsing layer without touching OpenAI.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

import pytest

from backend.news_events.pipeline import classify as classify_mod
from backend.news_events.pipeline import excerpt as excerpt_mod


@dataclass
class _FakeResponse:
    content: str


class _FakeClient:
    """Minimal stub with the same surface ``LLMClient`` exposes for
    our two callers (``.complete(...)`` + ``.model``)."""

    def __init__(self, content: str, model: str = "fake-model"):
        self._content = content
        self.model = model
        self.captured_kwargs: dict[str, Any] | None = None

    async def complete(self, **kwargs):  # noqa: D401
        self.captured_kwargs = kwargs
        return _FakeResponse(self._content)


# ── Stage 5 ──────────────────────────────────────────────────────────


def test_extract_excerpt_returns_clean_string(monkeypatch):
    fake = _FakeClient(
        '{"excerpt": "The MPC cut the repo rate to 5.75% with immediate effect."}'
    )
    monkeypatch.setattr(excerpt_mod, "get_llm_client", lambda: fake)
    out = asyncio.run(
        excerpt_mod.extract_excerpt(
            event_description="RBI cuts repo rate",
            article_title="RBI cuts repo rate",
            article_body="Body...",
        )
    )
    assert "5.75%" in out
    assert fake.captured_kwargs is not None
    assert fake.captured_kwargs.get("prompt_cache_key") == "news_events.excerpt.v1"


def test_extract_excerpt_handles_fenced_json(monkeypatch):
    fenced = "```json\n{\"excerpt\": \"Verbatim sentence.\"}\n```"
    monkeypatch.setattr(
        excerpt_mod, "get_llm_client", lambda: _FakeClient(fenced)
    )
    out = asyncio.run(
        excerpt_mod.extract_excerpt(
            event_description="x", article_title="y", article_body="z"
        )
    )
    assert out == "Verbatim sentence."


def test_extract_excerpt_returns_empty_on_garbage(monkeypatch):
    monkeypatch.setattr(
        excerpt_mod, "get_llm_client", lambda: _FakeClient("not-json")
    )
    out = asyncio.run(
        excerpt_mod.extract_excerpt(
            event_description="x", article_title="y", article_body="z"
        )
    )
    assert out == ""


def test_extract_excerpt_skips_with_empty_body(monkeypatch):
    called = {"n": 0}

    class _C:
        async def complete(self, **kwargs):
            called["n"] += 1
            return _FakeResponse('{"excerpt":""}')

    monkeypatch.setattr(excerpt_mod, "get_llm_client", lambda: _C())
    out = asyncio.run(
        excerpt_mod.extract_excerpt(
            event_description="x", article_title="y", article_body=""
        )
    )
    assert out == ""
    # No LLM call when the body is empty — saves one round trip per row.
    assert called["n"] == 0


# ── Stage 6 ──────────────────────────────────────────────────────────


def test_classify_yes_high_confidence(monkeypatch):
    body = (
        '{"verdict": "YES", "confidence": 0.92, "is_retraction": false,'
        ' "reason": "Article confirms the repo rate was cut."}'
    )
    monkeypatch.setattr(
        classify_mod, "get_llm_client", lambda: _FakeClient(body)
    )
    res = asyncio.run(
        classify_mod.classify_excerpt(
            event_description="RBI cuts repo rate",
            excerpt="The MPC cut the repo rate to 5.75%.",
            article_title="RBI cuts repo rate",
        )
    )
    assert res.verdict == "YES"
    assert res.confidence == 0.92
    assert res.is_retraction is False


def test_classify_retraction_forces_flag_true(monkeypatch):
    # is_retraction omitted from response — code must still set the flag.
    body = (
        '{"verdict": "RETRACTION", "confidence": 0.91,'
        ' "reason": "Earlier confirmation was withdrawn."}'
    )
    monkeypatch.setattr(
        classify_mod, "get_llm_client", lambda: _FakeClient(body)
    )
    res = asyncio.run(
        classify_mod.classify_excerpt(
            event_description="X", excerpt="e", article_title="t"
        )
    )
    assert res.verdict == "RETRACTION"
    assert res.is_retraction is True


def test_classify_unknown_verdict_falls_back(monkeypatch):
    body = '{"verdict": "PROBABLY", "confidence": 0.99}'
    monkeypatch.setattr(
        classify_mod, "get_llm_client", lambda: _FakeClient(body)
    )
    res = asyncio.run(
        classify_mod.classify_excerpt(
            event_description="X", excerpt="e", article_title="t"
        )
    )
    assert res.verdict == "UNRELATED"
    assert res.confidence == 0.0
    assert res.is_retraction is False


def test_classify_clamps_confidence(monkeypatch):
    body = '{"verdict": "YES", "confidence": 99}'
    monkeypatch.setattr(
        classify_mod, "get_llm_client", lambda: _FakeClient(body)
    )
    res = asyncio.run(
        classify_mod.classify_excerpt(
            event_description="X", excerpt="e", article_title="t"
        )
    )
    assert res.confidence == 1.0


def test_classify_swallows_exception(monkeypatch):
    class _Broken:
        async def complete(self, **kwargs):
            raise RuntimeError("offline")

    monkeypatch.setattr(classify_mod, "get_llm_client", lambda: _Broken())
    res = asyncio.run(
        classify_mod.classify_excerpt(
            event_description="X", excerpt="e", article_title="t"
        )
    )
    assert res.verdict == "UNRELATED"
    assert res.reason.startswith("llm_failed")
