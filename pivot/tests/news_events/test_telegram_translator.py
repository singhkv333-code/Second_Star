"""Pure tests for backend.news_events.sources.telegram_source.

The translator is duck-typed against Telethon's Message/Chat shape,
so we feed in lightweight stub objects without importing telethon.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from backend.news_events.sources.telegram_source import (
    channel_username_from_feed_url,
    translate_event,
    translate_message,
)


@dataclass
class _StubMessage:
    id: int
    message: str
    date: Optional[datetime] = None
    fwd_from: object = None


@dataclass
class _StubChat:
    username: Optional[str]


@dataclass
class _StubEvent:
    message: object
    chat: object


# ── channel_username_from_feed_url ───────────────────────────────────


def test_channel_username_parses_basic_link():
    assert channel_username_from_feed_url("https://t.me/livemint") == "livemint"
    assert channel_username_from_feed_url("https://t.me/s/ETMarkets") == "ETMarkets"
    assert channel_username_from_feed_url("http://t.me/PIB_India/") == "PIB_India"


def test_channel_username_rejects_garbage():
    assert channel_username_from_feed_url("") is None
    assert channel_username_from_feed_url("https://example.com/x") is None
    assert channel_username_from_feed_url(None) is None  # type: ignore[arg-type]


# ── translate_message ────────────────────────────────────────────────


def test_translate_message_title_summary_split():
    msg = _StubMessage(
        id=42,
        message="RBI cuts repo rate by 25 bps\n\nMPC reduces repo rate to 5.75%",
        date=datetime(2026, 5, 21, 4, 0, tzinfo=timezone.utc),
    )
    item = translate_message(
        source_id="tg_livemint", channel_username="livemint", message=msg
    )
    assert item is not None
    assert item.title == "RBI cuts repo rate by 25 bps"
    assert item.summary is not None
    assert "5.75%" in item.summary
    assert item.url == "https://t.me/livemint/42"
    assert item.source_id == "tg_livemint"
    assert item.published_at is not None
    assert item.raw_metadata["telegram_message_id"] == 42
    assert item.raw_metadata["telegram_channel"] == "livemint"


def test_translate_message_single_line_no_summary():
    msg = _StubMessage(id=10, message="Sensex closes at all-time high")
    item = translate_message(
        source_id="tg_etmarkets", channel_username="ETMarkets", message=msg
    )
    assert item is not None
    assert item.title == "Sensex closes at all-time high"
    assert item.summary is None


def test_translate_message_strips_decorative_whitespace():
    msg = _StubMessage(id=11, message="\n\n   \n   RBI cut rate by 25 bps   \n\n")
    item = translate_message(
        source_id="tg_livemint", channel_username="livemint", message=msg
    )
    assert item is not None
    assert item.title == "RBI cut rate by 25 bps"


def test_translate_message_empty_returns_none():
    msg = _StubMessage(id=12, message="")
    item = translate_message(
        source_id="tg_x", channel_username="x", message=msg
    )
    assert item is None


def test_translate_message_no_id_returns_none():
    msg = _StubMessage(id=None, message="Some text")  # type: ignore[arg-type]
    item = translate_message(
        source_id="tg_x", channel_username="x", message=msg
    )
    assert item is None


def test_translate_message_caps_long_summary():
    long = "title\n" + ("body " * 1000)
    msg = _StubMessage(id=20, message=long)
    item = translate_message(
        source_id="tg_x", channel_username="x", message=msg
    )
    assert item is not None
    assert item.summary is not None
    assert len(item.summary) <= 2000


def test_translate_message_records_forwarded_from():
    @dataclass
    class _Fwd:
        from_name: str

    msg = _StubMessage(
        id=30,
        message="Wire copy: RBI press release",
        fwd_from=_Fwd(from_name="Reuters India"),
    )
    item = translate_message(
        source_id="tg_etmarkets", channel_username="ETMarkets", message=msg
    )
    assert item is not None
    assert item.raw_metadata.get("telegram_forwarded_from") == "Reuters India"


# ── translate_event (full pipeline including chat lookup) ────────────


def test_translate_event_resolves_via_registry():
    """The translate_event helper finds the matching SourceDef by
    chat.username. Uses the live registry, which includes tg_livemint."""
    event = _StubEvent(
        message=_StubMessage(id=99, message="Phase 7 demo headline"),
        chat=_StubChat(username="livemint"),
    )
    item = translate_event(event)
    assert item is not None
    assert item.source_id == "tg_livemint"
    assert item.title == "Phase 7 demo headline"


def test_translate_event_unknown_chat_returns_none():
    event = _StubEvent(
        message=_StubMessage(id=99, message="Some text"),
        chat=_StubChat(username="some_random_channel_not_in_registry"),
    )
    assert translate_event(event) is None


def test_translate_event_missing_message_returns_none():
    event = _StubEvent(message=None, chat=_StubChat(username="livemint"))
    assert translate_event(event) is None
