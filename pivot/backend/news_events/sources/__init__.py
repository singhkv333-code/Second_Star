"""Source adapters for the news_events ingestion stage.

Each adapter implements ``SourceAdapter`` (defined in ``base.py``) and
yields a stream of ``FetchedItem`` records. Concrete adapters today:

  - ``rss.RSSAdapter`` — generic RSS 2.0 / Atom 1.0 polled feed.

Push-based adapters (WebSub, n8n bridge) live behind the same interface
and are Phase 7 work.
"""
from __future__ import annotations

from backend.news_events.sources.base import FetchedItem, SourceAdapter
from backend.news_events.sources.rss import RSSAdapter

__all__ = ["FetchedItem", "SourceAdapter", "RSSAdapter"]

# Phase 7 — Telegram + Miniflux transports live in their own modules:
#   sources/telegram_source.py  (translator + channel registry)
#   workers/telegram_worker.py  (long-lived asyncio client)
#   routers' Miniflux webhook endpoint lives in router.py
# Imported lazily by their respective callers so the optional
# telethon dependency stays optional.
