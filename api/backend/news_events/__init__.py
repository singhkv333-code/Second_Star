"""News & Event Trigger subsystem (Phase 1 — ingestion skeleton).

Isolated module. Behaviour is gated by ``settings.news_events_enabled``.
With the flag off the entire subsystem is dormant — the router is not
included, no APScheduler jobs are registered, and the integration seam
is a no-op.

Roadmap (see docs/news_events_phase0_plan.md §4):

  Phase 1 — ingestion skeleton, RSS adapter, raw articles persisted.
  Phase 2 — dedup + keyword filter funnel stages.
  Phase 3 — full-article fetch, embedding similarity, LLM classify.
  Phase 4 — event specs + NL parser + Tier-3 disambiguation.
  Phase 5 — confidence aggregator, firing, approvals.
  Phase 6 — Tier-3 hardening (Polymarket cross-check, retractions).
  Phase 7 — optional transport upgrade (WebSub / n8n).
"""
from __future__ import annotations

# Importing models at package import time registers them with
# backend.database.Base.metadata so alembic --autogenerate sees them
# even though the new tables are created by the explicit 0007 migration.
from backend.news_events import models  # noqa: F401

__all__ = ["models"]
