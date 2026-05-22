"""Funnel pipeline stages for news_events.

Phase 1 ships Stage 0 only — source ingestion into ``news_articles``.
Future phases drop in alongside without restructuring:

  Stage 0 — ``ingest`` (Phase 1)
  Stage 1 — ``dedup``   (Phase 2)
  Stage 2 — ``keyword`` (Phase 2)
  Stage 3 — ``fetch_body``
  Stage 4 — ``embed``
  Stage 5 — ``excerpt``
  Stage 6 — ``classify``
  Stage 7 — ``aggregate``
  Stage 8 — ``propose``
"""
