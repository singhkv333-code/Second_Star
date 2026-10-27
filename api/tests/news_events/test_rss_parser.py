"""Unit tests for backend.news_events.sources.rss.parse_feed.

We feed the parser hand-written XML strings (one RSS 2.0, one Atom 1.0,
one malformed) and assert on what it returns. No network — these tests
run on every CI invocation regardless of egress.
"""
from __future__ import annotations

import pytest

from backend.news_events.sources.base import SourceFetchError
from backend.news_events.sources.rss import parse_feed


_RSS_2_0_BODY = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Test Feed</title>
    <item>
      <title>RBI cuts repo rate by 25 bps</title>
      <link>https://example.test/articles/rbi-repo-cut-25bps</link>
      <description>Monetary policy committee announced...</description>
      <pubDate>Wed, 21 May 2026 04:00:00 +0000</pubDate>
      <guid>rbi-2026-05-21-001</guid>
      <category>Monetary Policy</category>
      <category>Banks</category>
    </item>
    <item>
      <title>RBI raises SLR ceiling</title>
      <link>https://example.test/articles/rbi-slr-ceiling</link>
      <description>Effective 1 June 2026...</description>
      <pubDate>Tue, 20 May 2026 09:30:00 +0000</pubDate>
    </item>
    <item>
      <title>An item with no link gets skipped</title>
    </item>
  </channel>
</rss>
"""


_ATOM_1_0_BODY = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>BBC World</title>
  <entry>
    <title>Major election result announced</title>
    <link href="https://example.test/articles/election-result"/>
    <summary>Country X has declared a winner...</summary>
    <published>2026-05-21T03:00:00Z</published>
    <id>tag:example.test,2026-05-21:election-result</id>
  </entry>
  <entry>
    <title>An entry without a link</title>
    <published>2026-05-21T02:00:00Z</published>
  </entry>
</feed>
"""


def test_parse_rss_2_extracts_well_formed_items():
    items = parse_feed("rss_test", _RSS_2_0_BODY)
    # The third item has no link and is skipped.
    assert len(items) == 2

    first = items[0]
    assert first.source_id == "rss_test"
    assert first.title == "RBI cuts repo rate by 25 bps"
    assert first.url == "https://example.test/articles/rbi-repo-cut-25bps"
    assert first.summary and first.summary.startswith("Monetary policy")
    assert first.published_at is not None
    assert first.published_at.year == 2026 and first.published_at.month == 5
    assert first.raw_metadata.get("guid") == "rbi-2026-05-21-001"
    assert first.raw_metadata.get("categories") == ["Monetary Policy", "Banks"]


def test_parse_atom_extracts_well_formed_entries():
    items = parse_feed("atom_test", _ATOM_1_0_BODY)
    assert len(items) == 1  # The link-less entry is skipped.
    only = items[0]
    assert only.title == "Major election result announced"
    assert only.url == "https://example.test/articles/election-result"
    assert only.summary == "Country X has declared a winner..."
    assert only.published_at is not None
    assert only.raw_metadata.get("id", "").startswith("tag:example.test")


def test_parse_malformed_raises_source_fetch_error():
    with pytest.raises(SourceFetchError):
        parse_feed("rss_test", "<not-xml>")


def test_parse_unknown_root_raises_source_fetch_error():
    body = "<?xml version='1.0'?><something-else/>"
    with pytest.raises(SourceFetchError):
        parse_feed("rss_test", body)


def test_parse_handles_empty_channel():
    body = (
        "<?xml version='1.0' encoding='UTF-8'?>"
        "<rss version='2.0'><channel><title>Empty</title></channel></rss>"
    )
    items = parse_feed("rss_test", body)
    assert items == []
