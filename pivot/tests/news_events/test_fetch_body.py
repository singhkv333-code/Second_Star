"""Tests for backend.news_events.pipeline.fetch_body.

httpx is mocked via a transport so we never touch the network.
Covers the four ``BodyFetchStatus`` paths plus robots.txt cache.
"""
from __future__ import annotations

import asyncio

import httpx
import pytest

from backend.news_events.pipeline import fetch_body as fb


_ARTICLE_HTML = (
    "<html><head><title>Test</title></head><body>"
    "<article><h1>RBI cuts repo rate by 25 bps</h1>"
    "<p>The Monetary Policy Committee unanimously voted to reduce the "
    "policy repo rate to 5.75% with immediate effect. The decision is "
    "expected to support credit growth across the economy.</p>"
    "<p>Inflation projections were revised downward to 4.1% for FY26.</p>"
    "</article></body></html>"
)


def _make_transport(handler):
    return httpx.MockTransport(handler)


def _patched_async_client(monkeypatch, handler):
    """Force every httpx.AsyncClient(...) inside fetch_body to use a
    MockTransport. Honours timeout/headers kwargs by ignoring them."""
    real_init = httpx.AsyncClient.__init__

    def _patched_init(self, *args, **kwargs):
        kwargs["transport"] = _make_transport(handler)
        real_init(self, *args, **kwargs)

    monkeypatch.setattr(httpx.AsyncClient, "__init__", _patched_init)


@pytest.fixture(autouse=True)
def _clear_robots_cache():
    fb._robots_cache.clear()
    yield
    fb._robots_cache.clear()


def test_fetch_ok_extracts_body(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nAllow: /\n")
        return httpx.Response(200, text=_ARTICLE_HTML, headers={"content-type": "text/html"})

    _patched_async_client(monkeypatch, handler)

    res = asyncio.run(fb.fetch_article_body("https://example.test/articles/1"))
    assert res.status == "ok"
    assert res.body_text is not None
    assert "Monetary Policy Committee" in res.body_text
    assert "5.75%" in res.body_text


def test_fetch_robots_disallowed(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(
                200, text="User-agent: *\nDisallow: /private/\n"
            )
        # Should not be hit
        return httpx.Response(200, text=_ARTICLE_HTML)

    _patched_async_client(monkeypatch, handler)
    res = asyncio.run(
        fb.fetch_article_body("https://example.test/private/article")
    )
    assert res.status == "robots_disallowed"
    assert res.body_text is None


def test_fetch_4xx_is_fatal(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(403, text="forbidden")

    _patched_async_client(monkeypatch, handler)
    res = asyncio.run(fb.fetch_article_body("https://example.test/x"))
    assert res.status == "http_error"
    assert res.http_status == 403


def test_fetch_5xx_retries_then_succeeds(monkeypatch):
    state = {"calls": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        state["calls"] += 1
        if state["calls"] < 3:
            return httpx.Response(502, text="bad gateway")
        return httpx.Response(200, text=_ARTICLE_HTML)

    _patched_async_client(monkeypatch, handler)
    # Speed up the retry backoff.
    monkeypatch.setattr(fb, "_RETRY_BASE_DELAY", 0.0)
    res = asyncio.run(fb.fetch_article_body("https://example.test/flaky"))
    assert res.status == "ok"
    assert state["calls"] == 3


def test_extract_failed_returns_status(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        # No <article> / paragraphs — trafilatura returns None.
        return httpx.Response(200, text="<html><body></body></html>")

    _patched_async_client(monkeypatch, handler)
    res = asyncio.run(fb.fetch_article_body("https://example.test/empty"))
    assert res.status == "extract_failed"
    assert res.body_text is None


def test_robots_cache_is_reused(monkeypatch):
    fetches = {"robots": 0, "article": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            fetches["robots"] += 1
            return httpx.Response(200, text="User-agent: *\nAllow: /\n")
        fetches["article"] += 1
        return httpx.Response(200, text=_ARTICLE_HTML)

    _patched_async_client(monkeypatch, handler)

    async def run_twice():
        await fb.fetch_article_body("https://example.test/a/1")
        await fb.fetch_article_body("https://example.test/a/2")

    asyncio.run(run_twice())
    assert fetches["robots"] == 1
    assert fetches["article"] == 2
