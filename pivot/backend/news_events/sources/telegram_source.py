"""Telegram MTProto channel source (Phase 7, Tier-A push).

This module owns the pure translation work (``Telegram message →
FetchedItem``) plus the small wrapper that walks a Telethon
``NewMessage`` event. The long-running asyncio loop lives in
``backend/news_events/workers/telegram_worker.py`` — keeping it
separate so the translator can be unit-tested without Telethon
installed.

We do NOT import ``telethon`` at module load. The two ``Any`` type
hints in this file are placeholders for the runtime types; tests pass
in lightweight stubs (any object exposing ``message``, ``chat`` etc.).
"""
from __future__ import annotations

import logging
import re
from typing import Any, Optional

from backend.news_events.sources.base import FetchedItem

logger = logging.getLogger(__name__)


# Telegram channel usernames map 1:1 to our ``source_id`` registry
# entries — the worker uses this to ask: "is this channel one we
# care about?" The mapping lives in the source registry, not here,
# so adding a new channel is a registry edit.

_T_ME_RE = re.compile(r"^https?://t\.me/(?:s/)?([A-Za-z0-9_]+)/?$")


def channel_username_from_feed_url(feed_url: str) -> Optional[str]:
    """Extract the channel username from a Telegram source's
    ``feed_url``. Returns None if the URL doesn't match the t.me
    shape (catches typos in the registry early)."""
    if not feed_url:
        return None
    m = _T_ME_RE.match(feed_url.strip())
    return m.group(1) if m else None


def configured_telegram_channels() -> list[tuple[str, str]]:
    """Return ``[(source_id, channel_username), ...]`` for every
    enabled Telegram source in the registry. Used by the worker
    to build the Telethon ``chats=[...]`` filter."""
    from backend.news_events.config import enabled_sources

    out: list[tuple[str, str]] = []
    for src in enabled_sources():
        if src.kind != "telegram":
            continue
        username = channel_username_from_feed_url(src.feed_url)
        if username:
            out.append((src.source_id, username))
        else:
            logger.warning(
                "[news_events.telegram] source %s has malformed feed_url=%r",
                src.source_id, src.feed_url,
            )
    return out


# ── Pure translator ─────────────────────────────────────────────────


def _split_title_and_summary(text: str) -> tuple[str, Optional[str]]:
    """A Telegram message has no separate title field. Split the
    first non-empty line as the title and the rest as the summary.
    Trim aggressively — channel posts often start with emoji /
    decorative whitespace that we don't want in the title-hash.
    """
    if not text:
        return "", None
    lines = [ln.strip() for ln in text.strip().splitlines() if ln.strip()]
    if not lines:
        return "", None
    title = lines[0][:300]
    summary = "\n".join(lines[1:])[:2000] if len(lines) > 1 else None
    return title, summary


def _post_url(channel_username: str, message_id: int) -> str:
    """Public web URL for a channel post. ``t.me/<channel>/<msg_id>``."""
    return f"https://t.me/{channel_username}/{int(message_id)}"


def translate_message(
    *,
    source_id: str,
    channel_username: str,
    message: Any,
) -> Optional[FetchedItem]:
    """Convert a Telethon ``Message`` into a ``FetchedItem``.

    Returns None when the message has no usable text (sticker-only,
    poll, service message). Defensive: every attribute lookup is
    duck-typed so a stub object with only ``id``, ``message``, and
    ``date`` works in tests.
    """
    raw_text = getattr(message, "message", None) or getattr(message, "text", None) or ""
    raw_text = (raw_text or "").strip()
    if not raw_text:
        return None

    msg_id = getattr(message, "id", None)
    if msg_id is None:
        return None
    msg_id = int(msg_id)

    title, summary = _split_title_and_summary(raw_text)
    if not title:
        return None

    published_at = getattr(message, "date", None)

    metadata: dict[str, Any] = {
        "telegram_message_id": msg_id,
        "telegram_channel": channel_username,
    }
    fwd = getattr(message, "fwd_from", None)
    if fwd is not None:
        fwd_chan = getattr(fwd, "from_name", None) or getattr(fwd, "from_id", None)
        if fwd_chan:
            metadata["telegram_forwarded_from"] = str(fwd_chan)

    return FetchedItem(
        source_id=source_id,
        url=_post_url(channel_username, msg_id),
        title=title,
        summary=summary,
        published_at=published_at,
        raw_metadata=metadata,
    )


def translate_event(event: Any) -> Optional[FetchedItem]:
    """Convenience wrapper for the Telethon ``events.NewMessage``
    handler — pulls the message and resolves the channel username
    via the chat object on the event.

    Returns None if the message is from a chat we don't have a
    registry entry for, or if translation fails.
    """
    message = getattr(event, "message", None)
    chat = getattr(event, "chat", None)
    if message is None or chat is None:
        return None
    channel_username = getattr(chat, "username", None)
    if not channel_username:
        return None
    # Reverse-lookup source_id by channel username.
    src_id = None
    for sid, uname in configured_telegram_channels():
        if uname.lower() == channel_username.lower():
            src_id = sid
            break
    if src_id is None:
        # Not in our registry — silently ignore.
        return None
    return translate_message(
        source_id=src_id,
        channel_username=channel_username,
        message=message,
    )


__all__ = [
    "configured_telegram_channels",
    "channel_username_from_feed_url",
    "translate_event",
    "translate_message",
]
