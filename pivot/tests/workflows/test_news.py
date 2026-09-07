"""Tests for the multi-source /api/news endpoint."""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import patch

from fastapi.testclient import TestClient

from backend.services.company_news import (
    NewsCandidate,
    NewsSourcesUnavailable,
    _normalize_yahoo,
)


def _candidate(title: str = "Reliance posts record Q4 results") -> NewsCandidate:
    return NewsCandidate(
        title=title,
        publisher="Reuters",
        url="https://example.com/news/1",
        published_at=datetime(2026, 9, 6, tzinfo=timezone.utc),
        thumbnail="https://img.example.com/1.jpg",
        summary="Company-specific reporting.",
        provider="gdelt",
    )


def test_news_unauth(client: TestClient) -> None:
    response = client.get("/api/news?symbol=RELIANCE")
    assert response.status_code == 401


def test_news_missing_symbol_returns_422(
    client: TestClient, auth_headers: dict[str, str],
) -> None:
    response = client.get("/api/news", headers=auth_headers)
    assert response.status_code == 422


def test_news_returns_aggregated_contract(
    client: TestClient, auth_headers: dict[str, str],
) -> None:
    with patch(
        "backend.routers.news.aggregate_company_news",
        return_value=[_candidate()],
    ) as aggregate:
        response = client.get(
            "/api/news?symbol=RELIANCE&exchange=NSE&limit=5"
            "&company_name=Reliance%20Industries%20Limited",
            headers=auth_headers,
        )
    assert response.status_code == 200, response.text
    item = response.json()["items"][0]
    assert item["publisher"] == "Reuters"
    assert item["provider"] == "gdelt"
    assert item["kind"] == "article"
    aggregate.assert_called_once_with(
        symbol="RELIANCE",
        exchange="NSE",
        company_name="Reliance Industries Limited",
        limit=5,
    )


def test_yahoo_old_flat_shape_is_normalized() -> None:
    item = _normalize_yahoo({
        "title": "Reliance posts record Q4 results",
        "publisher": "Bloomberg",
        "link": "https://example.com/news/1",
        "providerPublishTime": 1714600000,
        "thumbnail": {"resolutions": [{"url": "https://img.example.com/1.jpg"}]},
    })
    assert item is not None
    assert item.publisher == "Bloomberg"
    assert item.provider == "yahoo_finance"
    assert item.thumbnail == "https://img.example.com/1.jpg"


def test_yahoo_new_nested_shape_is_normalized() -> None:
    item = _normalize_yahoo({
        "content": {
            "title": "TCS lands £1bn HSBC deal",
            "summary": "Multi-year contract...",
            "pubDate": "2026-04-30T09:00:00Z",
            "provider": {"displayName": "Reuters"},
            "clickThroughUrl": {"url": "https://reuters.example.com/x"},
            "thumbnail": {"originalUrl": "https://img.example.com/tcs.jpg"},
        },
    })
    assert item is not None
    assert item.title == "TCS lands £1bn HSBC deal"
    assert item.summary == "Multi-year contract..."
    assert item.published_at == datetime(2026, 4, 30, 9, tzinfo=timezone.utc)


def test_news_all_sources_unavailable_returns_503(
    client: TestClient, auth_headers: dict[str, str],
) -> None:
    with patch(
        "backend.routers.news.aggregate_company_news",
        side_effect=NewsSourcesUnavailable("down"),
    ):
        response = client.get("/api/news?symbol=INFY", headers=auth_headers)
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "not_yet_available"


def test_yahoo_skips_titleless_items() -> None:
    assert _normalize_yahoo({"title": ""}) is None
    assert _normalize_yahoo({"content": {"title": ""}}) is None
