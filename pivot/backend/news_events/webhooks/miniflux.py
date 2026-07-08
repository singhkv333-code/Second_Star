"""Miniflux outbound-webhook receiver.

Miniflux 2.0.48+ POSTs a JSON payload to the configured URL whenever
new feed entries arrive. The body is signed with HMAC-SHA256 over
the raw bytes, using a shared secret configured on both ends. We
verify the signature, parse the ``entries[]`` array, and feed the
items through the same ingest path the in-process poller uses.

Payload shape (see https://miniflux.app/docs/webhooks.html):

    {
      "event_type": "new_entries",
      "feed": {"id": 17, "title": "...", "site_url": "...", ...},
      "entries": [
        {
          "id": 142,
          "title": "...",
          "url": "https://...",
          "published_at": "2026-05-21T04:00:00Z",
          "content": "...",
          ...
        },
        ...
      ]
    }

Header: ``X-Miniflux-Signature: sha256=<hex>``.

We map each entry's ``feed.id`` / ``feed.title`` → a ``source_id``
under the convention ``miniflux_feed_<feed_id>`` so the
admin/sources surface still reflects per-Miniflux-feed activity.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import re
from datetime import datetime, timezone
from typing import Any, Optional

from backend.news_events.sources.base import FetchedItem

logger = logging.getLogger(__name__)


_HEADER_NAME = "X-Miniflux-Signature"
_SIG_PREFIX = "sha256="


def verify_signature(*, secret: str, raw_body: bytes, signature_header: str) -> bool:
    """Return True iff ``signature_header`` is a valid HMAC-SHA256 of
    ``raw_body`` under ``secret``.

    Format: the header value Miniflux sends is ``sha256=<hex>``; we
    accept the bare hex digest too (some proxies strip the prefix).
    Empty secret or header → False.
    """
    if not secret or not signature_header:
        return False
    sig = signature_header.strip()
    if sig.startswith(_SIG_PREFIX):
        sig = sig[len(_SIG_PREFIX):]
    sig = sig.strip().lower()
    if not re.fullmatch(r"[0-9a-f]{64}", sig):
        return False
    expected = hmac.new(
        secret.encode("utf-8"), raw_body, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(sig, expected)


def _source_id_for(feed: dict[str, Any]) -> str:
    """Construct a stable source_id from a Miniflux ``feed`` payload.

    Prefer the feed id (stable, integer); fall back to a slug of the
    title. We DON'T match against the static SourceDef registry
    here — Miniflux owns its own feed list and may carry sources we
    haven't pre-registered. ``/admin/sources`` will surface these
    dynamically once the first entry lands (via the same
    ``news_source_health`` upsert the poller uses).
    """
    fid = feed.get("id") if isinstance(feed, dict) else None
    if fid is not None:
        return f"miniflux_feed_{fid}"
    title = (feed.get("title") or "unknown") if isinstance(feed, dict) else "unknown"
    slug = re.sub(r"[^a-z0-9]+", "_", title.lower()).strip("_")[:48] or "unknown"
    return f"miniflux_feed_{slug}"


def _parse_published_at(raw: Any) -> Optional[datetime]:
    """Tolerant ISO 8601 parser — Miniflux emits ``"2026-05-21T04:00:00Z"``;
    we accept any string the stdlib understands."""
    if not raw:
        return None
    if isinstance(raw, datetime):
        return raw if raw.tzinfo else raw.replace(tzinfo=timezone.utc)
    if not isinstance(raw, str):
        return None
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _entry_to_item(*, source_id: str, entry: dict[str, Any]) -> Optional[FetchedItem]:
    """Convert one Miniflux entry into a ``FetchedItem``. Returns
    None if mandatory fields are missing."""
    title = (entry.get("title") or "").strip()
    url = (entry.get("url") or "").strip()
    if not title or not url:
        return None
    summary = (entry.get("content") or entry.get("summary") or "").strip() or None
    return FetchedItem(
        source_id=source_id,
        url=url,
        title=title,
        summary=summary,
        published_at=_parse_published_at(entry.get("published_at")),
        raw_metadata={
            "miniflux_entry_id": entry.get("id"),
            "feed_title": entry.get("feed_title"),
        },
    )


def parse_payload(payload: dict[str, Any]) -> tuple[str, list[FetchedItem]]:
    """Coerce a Miniflux webhook body into (source_id, [items, ...]).

    ``event_type`` field on the payload must be ``new_entries`` —
    Miniflux fires other events (e.g. saved_entry) we don't handle.

    Pure function so tests can exercise the mapping without
    invoking the FastAPI router.
    """
    if not isinstance(payload, dict):
        return "miniflux_unknown", []
    if str(payload.get("event_type", "")).lower() != "new_entries":
        return "miniflux_unknown", []
    feed = payload.get("feed")
    if not isinstance(feed, dict):
        return "miniflux_unknown", []
    source_id = _source_id_for(feed)

    entries_raw = payload.get("entries")
    if not isinstance(entries_raw, list):
        return source_id, []

    items: list[FetchedItem] = []
    for e in entries_raw:
        if not isinstance(e, dict):
            continue
        item = _entry_to_item(source_id=source_id, entry=e)
        if item is not None:
            items.append(item)
    return source_id, items
