"""Tests for /api/news (#53).

yfinance is mocked. We test both the old flat news shape and the new
nested-under-`content` shape since both have shipped from upstream
within the last 12 months.
"""
from __future__ import annotations

from unittest.mock import patch

from fastapi.testclient import TestClient


class _FakeTicker:
    def __init__(self, news: list) -> None:
        self.info: dict = {}
        self.news = news

    def history(self, *args, **kwargs):  # noqa: D401 - signature for compatibility
        import pandas as pd
        return pd.DataFrame()


def test_news_unauth(client: TestClient) -> None:
    r = client.get("/api/news?symbol=RELIANCE")
    assert r.status_code == 401


def test_news_missing_symbol_returns_422(
    client: TestClient, auth_headers: dict[str, str],
) -> None:
    r = client.get("/api/news", headers=auth_headers)
    assert r.status_code == 422


def test_news_old_flat_shape(
    client: TestClient, auth_headers: dict[str, str],
) -> None:
    payload = [
        {
            "title": "Reliance posts record Q4 results",
            "publisher": "Bloomberg",
            "link": "https://example.com/news/1",
            "providerPublishTime": 1714600000,
            "thumbnail": {
                "resolutions": [
                    {"url": "https://img.example.com/1.jpg"},
                ],
            },
        },
    ]
    with patch(
        "backend.routers.news.yf.Ticker",
        return_value=_FakeTicker(payload),
    ):
        r = client.get(
            "/api/news?symbol=RELIANCE", headers=auth_headers,
        )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["symbol"] == "RELIANCE"
    assert len(body["items"]) == 1
    item = body["items"][0]
    assert item["title"] == "Reliance posts record Q4 results"
    assert item["publisher"] == "Bloomberg"
    assert item["url"] == "https://example.com/news/1"
    assert item["thumbnail"] == "https://img.example.com/1.jpg"
    assert item["published_at"] is not None


def test_news_new_nested_shape(
    client: TestClient, auth_headers: dict[str, str],
) -> None:
    payload = [
        {
            "id": "abc",
            "content": {
                "title": "TCS lands £1bn HSBC deal",
                "summary": "Multi-year contract...",
                "pubDate": "2026-04-30T09:00:00Z",
                "provider": {"displayName": "Reuters"},
                "clickThroughUrl": {"url": "https://reuters.example.com/x"},
                "thumbnail": {"originalUrl": "https://img.example.com/tcs.jpg"},
            },
        },
    ]
    with patch(
        "backend.routers.news.yf.Ticker",
        return_value=_FakeTicker(payload),
    ):
        r = client.get(
            "/api/news?symbol=TCS&limit=5", headers=auth_headers,
        )
    assert r.status_code == 200
    body = r.json()
    assert len(body["items"]) == 1
    it = body["items"][0]
    assert it["title"] == "TCS lands £1bn HSBC deal"
    assert it["publisher"] == "Reuters"
    assert it["url"] == "https://reuters.example.com/x"
    assert it["summary"].startswith("Multi-year")
    assert it["thumbnail"] == "https://img.example.com/tcs.jpg"
    # Pydantic serializes UTC dt as "Z" rather than "+00:00".
    assert it["published_at"] in (
        "2026-04-30T09:00:00Z", "2026-04-30T09:00:00+00:00",
    )


def test_news_limit_caps_results(
    client: TestClient, auth_headers: dict[str, str],
) -> None:
    payload = [
        {"title": f"Story {i}", "publisher": "X", "link": f"https://e/{i}"}
        for i in range(20)
    ]
    with patch(
        "backend.routers.news.yf.Ticker",
        return_value=_FakeTicker(payload),
    ):
        r = client.get(
            "/api/news?symbol=INFY&limit=3", headers=auth_headers,
        )
    assert r.status_code == 200
    assert len(r.json()["items"]) == 3


def test_news_yfinance_failure_returns_503(
    client: TestClient, auth_headers: dict[str, str],
) -> None:
    def boom(_sym: str) -> None:
        raise RuntimeError("network down")

    with patch("backend.routers.news.yf.Ticker", side_effect=boom):
        r = client.get(
            "/api/news?symbol=INFY", headers=auth_headers,
        )
    assert r.status_code == 503
    assert r.json()["error"]["code"] == "not_yet_available"


def test_news_skips_titleless_items(
    client: TestClient, auth_headers: dict[str, str],
) -> None:
    """Items without a title or content.title should be filtered."""
    payload = [
        {"title": ""},
        {"content": {"title": ""}},
        {"title": "Real story", "publisher": "P", "link": "https://e/1"},
    ]
    with patch(
        "backend.routers.news.yf.Ticker",
        return_value=_FakeTicker(payload),
    ):
        r = client.get(
            "/api/news?symbol=RELIANCE", headers=auth_headers,
        )
    assert r.status_code == 200
    items = r.json()["items"]
    assert len(items) == 1
    assert items[0]["title"] == "Real story"
