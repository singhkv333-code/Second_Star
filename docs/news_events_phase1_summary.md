# News & Event Trigger — Phase 1 summary

**Status:** complete. Ready for human review before Phase 2.
**Date:** 2026-05-21.
**Scope:** ingestion skeleton — Stage 0 of the funnel only. Feature
flag in place; with the flag off the subsystem is dormant.

---

## What was built

- **Migration `0007_news_events`** (`pivot/migrations/versions/0007_news_events.py`)
  — six additive tables, no `ALTER` on any existing table. Soft FKs to
  `workflows` / `workflow_runs` are plain `String(36)` columns without
  DB-level constraints, keeping the migration 100 % additive.
- **Isolated module** `pivot/backend/news_events/` containing:
  - `feature_flag.py` — single read of `settings.news_events_enabled`.
  - `models.py` — ORM mirror of the six new tables, importing
    `backend.database.Base`.
  - `schemas.py` — Pydantic models (`EventSpec`, `KeywordSet`,
    `ResolutionCriteria`, `RetractionPolicy`, plus the admin DTOs the
    Phase-1 router uses). Inherits a `_Strict` base mirroring
    `backend/workflows/schemas.py`.
  - `config.py` — Phase-1 source registry: RBI press releases /
    notifications / speeches, BBC World, Google News India-markets
    keyword. Each entry carries a per-source poll cadence.
  - `sources/base.py` — `SourceAdapter` ABC + `FetchedItem` dataclass
    + `SourceFetchError`. Push and pull adapters share this shape so
    Phase 7's WebSub / n8n drop-in doesn't touch the funnel.
  - `sources/rss.py` — RSS 2.0 + Atom 1.0 parser using stdlib
    `xml.etree`. No new dependency. Sets the identifying
    User-Agent on every request.
  - `pipeline/ingest.py` — Stage 0: fetch → dedup on `url_hash` →
    insert `news_articles` rows → upsert `news_source_health`.
  - `workers/poller.py` — registers one APScheduler job per enabled
    source on the existing `AsyncIOScheduler`, each at the source's
    configured cadence.
  - `router.py` — FastAPI router under `/api/news-events` exposing
    `/admin/sources`, `/admin/metrics`, and
    `/admin/sources/{id}/poll`. JWT-auth via the existing
    `routers/_deps.require_user`.
- **Tests** under `pivot/tests/news_events/`:
  - `test_rss_parser.py` — five tests against recorded XML.
  - `test_ingest.py` — four tests covering persist, dedup, failure
    bookkeeping, and reset-on-recovery.
  - `test_router.py` — four tests covering auth, registry shape,
    zero-state metrics, and 404 on unknown source.
  - All 13 pass; the existing test suite has 25 pre-existing failures
    on `prototype` (verified independently with a stash sweep — none
    introduced by Phase 1).

## What changed in existing code

Exactly two files, both flag-gated:

- `pivot/backend/config.py`
  — added `news_events_enabled: bool = False` and
  `news_events_user_agent` settings.

- `pivot/backend/main.py`
  — three additions, each behind `if settings.news_events_enabled:`:
  (a) router include, (b) poller registration inside the existing
  startup hook, (c) one info log line. With the flag off, every
  added line is short-circuited and the news_events package is never
  imported.

No existing migration, ORM model, router, scheduler, or chat surface
was modified. Touch 1 (the public `fire_external_event` wrapper next
to `_fire_watch_run`) is **not yet applied** — it's not needed until
Phase 5.

## Verified live (Postgres, prod-like env)

`alembic upgrade head` advanced revision `0006 → 0007`. Backend
restarted with `NEWS_EVENTS_ENABLED=true` and authenticated requests
exercised the live surface:

```
POST /api/news-events/admin/sources/bbc_world/poll
  → 30 seen / 30 new

POST /api/news-events/admin/sources/google_news_search_india_markets/poll
  → 100 seen / 100 new

POST /api/news-events/admin/sources/rbi_press_releases/poll
  → 10 seen / 10 new

POST /api/news-events/admin/sources/bbc_world/poll   (second call)
  → 30 seen / 0 new          ← dedup works on the live DB

GET  /api/news-events/admin/metrics?window_hours=1
  → 140 articles_ingested, 3 sources_active
```

`news_source_health` rows are populated per source after each poll;
`last_successful_fetch_at` is set; `consecutive_failures` reset to 0.

## Isolation audit

- [x] All new code under `pivot/backend/news_events/`.
- [x] No `ALTER` on any existing table; six new tables, additive.
- [x] No new FK constraints into existing tables (soft FKs only).
- [x] Reused existing infra: structlog, `Base`, `SessionLocal`,
      `get_db`, `routers/_deps.require_user`, APScheduler instance,
      `settings`. No parallel scheduler, no parallel DB engine.
- [x] Feature flag default OFF. Existing tests behave identically
      with the flag on or off (the news_events router is mounted
      via `if settings.news_events_enabled` so test isolation is
      preserved).
- [x] Workers are background APScheduler jobs; no synchronous
      callout from the request path.
- [x] Polite scraping: identifying User-Agent on every fetch;
      `SourceFetchError` raised on 4xx/5xx so the bookkeeping
      escalates rather than retry-storming.
- [x] Idempotency: `news_articles.url_hash` UNIQUE; race-loser falls
      into the `IntegrityError` branch and is counted as "seen but
      not new".
- [x] No changes to broker, order, or Kite code.
- [x] No changes to chat surface, intent parser, or workflow drafter.

## Surprises and decisions worth flagging

1. **`feedparser` not added.** Stdlib `xml.etree` handled all five
   Phase-1 feeds cleanly. If a future source has weird namespaces or
   non-conforming dates we'll revisit, but Phase 1 ships with zero
   new third-party deps.
2. **Google News RSS yielded 100 items immediately** — more density
   than the per-source poll cadence (300s) assumes. Phase 2's dedup
   will keep it under control, but if survivor counts spike at
   Stage 2 we may want a per-source ingest budget.
3. **RBI feeds publish on business-hours cadence**; off-hours polls
   return the same items repeatedly. The dedup-on-`url_hash` design
   handles this for free.
4. **The plan listed Touch 1 as a Phase-5 prerequisite.** Phase 1
   needed neither Touch 1 nor Touch 3 — the workflows engine is
   untouched.

## Open questions still outstanding (deferred to later phases)

The Phase-0 plan's §6 open questions remain unanswered, except where
Phase 1 implicitly defaulted them:

- (Q1) Touch 1 wrapper — still deferred to Phase 5.
- (Q4) Embedding provider — Phase 3.
- (Q5) n8n proxy — Phase 7.
- (Q6) NSE/BSE filings — deferred, Phase 1 ships with the 5 verified
  sources only.
- (Q8) Approvals UI — Phase 5.
- (Q9) Prod-egress verification of BS/ET/Mint/SEBI — outstanding;
  needed before any of those four sources can be activated in
  production. Phase 1 stays healthy without them.
- (Q10) Retention policy — outstanding; Phase 1 stores everything
  forever. A pruner job lands in Phase 2 or later.

## Recommended next step

Move to **Phase 2 — funnel stages 1-2** (cross-source dedup via
title simhash + per-event keyword/regex filter + admin metrics for
in/after-dedup/after-keyword counters). No additional existing-code
touches; same isolation rules.
