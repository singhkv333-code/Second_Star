"""Entity matching and source-merging tests for company news."""
from __future__ import annotations

from datetime import datetime, timezone

from backend.services.company_news import (
    NewsCandidate,
    _clean_feed_description,
    _deduplicate,
    _feed_image,
    _is_relevant,
    _publisher_logo,
    _strip_publisher_suffix,
)


def _item(title: str, provider: str = "gdelt") -> NewsCandidate:
    return NewsCandidate(
        title=title,
        publisher="Reuters",
        url=f"https://example.com/{len(title)}",
        published_at=datetime(2026, 9, 6, tzinfo=timezone.utc),
        thumbnail=None,
        thumbnail_kind=None,
        summary=None,
        provider=provider,
    )


def test_entity_gate_rejects_generic_market_story() -> None:
    generic = _item("Stocks fall as oil prices surge across Asian markets")
    specific = _item("HDFC Bank shares rise after CEO succession update")
    assert not _is_relevant(generic, "HDFCBANK", "HDFC Bank Limited")
    assert _is_relevant(specific, "HDFCBANK", "HDFC Bank Limited")


def test_short_symbol_requires_market_context() -> None:
    assert not _is_relevant(
        _item("TCS wins local school quiz"), "TCS",
        "Tata Consultancy Services Limited",
    )
    assert _is_relevant(
        _item("TCS shares rise after major deal"), "TCS",
        "Tata Consultancy Services Limited",
    )


def test_cross_source_duplicate_headlines_collapse() -> None:
    google = _item("HDFC Bank shares rise after CEO exit - Reuters", "gdelt")
    yahoo = _item("HDFC Bank shares rise after CEO exit", "yahoo_finance")
    assert len(_deduplicate([google, yahoo])) == 1


def test_cross_source_duplicate_keeps_available_image() -> None:
    google = _item("HDFC Bank shares rise after CEO exit - Reuters", "gdelt")
    yahoo = NewsCandidate(
        **{
            **_item("HDFC Bank shares rise after CEO exit", "yahoo_finance").__dict__,
            "thumbnail": "https://images.example.com/hdfc.jpg",
            "thumbnail_kind": "article",
        },
    )
    merged = _deduplicate([google, yahoo])
    assert merged[0].thumbnail == "https://images.example.com/hdfc.jpg"
    assert merged[0].thumbnail_kind == "article"


def test_google_publisher_suffix_is_removed() -> None:
    assert _strip_publisher_suffix(
        "HDFC Bank shares rise after CEO exit - Reuters", "Reuters",
    ) == "HDFC Bank shares rise after CEO exit"


def test_publisher_logo_uses_valid_source_domain() -> None:
    logo = _publisher_logo("https://www.reuters.com")
    assert logo and "domain_url=https%3A%2F%2Fwww.reuters.com" in logo
    assert _publisher_logo("") is None


def test_official_feed_extracts_real_media_and_clean_summary() -> None:
    from xml.etree import ElementTree as ET

    node = ET.fromstring("""
      <item xmlns:media="http://search.yahoo.com/mrss/">
        <media:content medium="image" url="https://images.example.com/story.jpg" />
      </item>
    """)
    assert _feed_image(node, "", "https://publisher.example/story") == (
        "https://images.example.com/story.jpg"
    )
    assert _clean_feed_description("<img src='x'> Company <b>results</b> rise") == (
        "Company results rise"
    )
