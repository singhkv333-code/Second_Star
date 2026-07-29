"""SourceAdapter interface — polled and pushed feeds share this shape.

The adapter knows how to turn one source (an RSS URL, a webhook
payload, an API page) into a stream of ``FetchedItem`` records. The
pipeline downstream is identical regardless of transport — that's the
whole point. Phase 7 swaps a polled RSS adapter for a WebSub receiver
behind the same shape and the funnel doesn't notice.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional


@dataclass(frozen=True)
class FetchedItem:
    """One article candidate yielded by a source adapter.

    Intentionally a small record — the ingest stage decides which
    fields to persist on ``news_articles``. ``raw_metadata`` is the
    catch-all for source-specific extras (RSS guid, Atom categories,
    GDELT mention counts, etc.) so the funnel can mine them later
    without a schema change.
    """

    source_id: str
    url: str
    title: str
    summary: Optional[str]
    published_at: Optional[datetime]
    raw_metadata: dict[str, Any]


class SourceAdapter(ABC):
    """Pull one source's current items.

    Implementations MUST be safe to call concurrently across event-loop
    ticks (the APScheduler poller may invoke fetch() multiple times if
    a previous call hung past its interval, although ``coalesce=True``
    on the job avoids most of that). Implementations MUST raise
    ``SourceFetchError`` on any non-recoverable failure so the poller's
    health bookkeeping can record it; transient HTTP errors should
    propagate the same way and the poller will increment the
    consecutive-failures counter.
    """

    @property
    @abstractmethod
    def source_id(self) -> str:  # pragma: no cover - trivial
        """Stable identifier — must match the key in news_events/config.py."""
        ...

    @abstractmethod
    async def fetch(self) -> list[FetchedItem]:
        """Fetch the current set of items.

        Returns an ordered list — the ingest stage relies only on the
        de-dup key (url_hash) and doesn't care about order, but a
        deterministic order makes debugging easier.
        """
        ...


class SourceFetchError(Exception):
    """Raised by SourceAdapter.fetch() when the source could not be
    read. The poller catches this, writes the message to
    ``news_source_health.last_error_message``, and increments
    ``consecutive_failures``."""
