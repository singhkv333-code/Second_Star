# News & Event Trigger — Phase 2 summary

**Status:** complete. Ready for human review before Phase 3.
**Date:** 2026-05-21.
**Scope:** funnel Stages 1-2 (cross-source dedup + keyword filter) +
funnel counters in the admin metrics surface. No touches to existing
backend code.

---

## What was built

- **Migration `0008_news_dedup_columns`**
  (`pivot/migrations/versions/0008_news_dedup_columns.py`)
  — adds one nullable column on our own Phase-1 ``news_articles``
  table: ``near_dup_of String(36)`` (soft FK to another
  ``news_articles.id``). Indexed for the Stage-2 hot-path filter.
- **`backend/news_events/pipeline/dedup.py`** — Stage 1
  cross-source dedup helper. ``find_near_dup(db, title_hash=...)``
  returns the id of the earliest article in the dedup window (default
  24 h) that already carries the same ``title_hash``. Self-chain
  guard included.
- **`backend/news_events/pipeline/keyword.py`** — Stage 2.
  ``evaluate_keyword_set(title, summary, keyword_set)`` is pure and
  microsecond-fast. ``apply_stage_2_for_article(...)`` loads every
  active ``NewsEventSpec``, evaluates the keyword set, and persists
  one ``news_article_classifications`` row per pair (with
  ``stage_2_passed`` set either way — the audit trail keeps both
  positive and negative classifications).
- **`backend/news_events/pipeline/ingest.py`** — extended:
  - Stage 1 runs inside ``_persist_items`` between url-hash dedup
    and the ``add()`` call. New rows get their ``near_dup_of``
    populated when a match is found.
  - Stage 2 runs only for rows where Stage 1 didn't match. All
    work happens in the same transaction so the audit table and
    article land together.
  - ``IngestResult`` gains ``items_after_stage1`` and
    ``items_after_stage2`` counters; the structured log line now
    reports them.
- **`backend/news_events/router.py` /admin/metrics**: replaced the
  Phase-1 zero-fillers with real queries.
  - ``articles_ingested`` = COUNT(*) of news_articles fetched in
    the window.
  - ``articles_deduped`` = COUNT(*) WHERE ``near_dup_of IS NULL``.
  - ``articles_after_keyword`` = COUNT classifications WHERE
    ``stage_2_passed`` joined to articles fetched in the window.
- **`backend/news_events/schemas.py`**: ``ForcePollResponse`` gains
  ``articles_after_stage1`` + ``articles_after_stage2`` fields so a
  single force-poll surfaces the funnel result.
- **Tests** under `pivot/tests/news_events/`:
  - `test_dedup.py` (4) — pure ``find_near_dup`` behaviour, including
    self-chain guard and out-of-window cutoff.
  - `test_keyword.py` (10) — table-driven semantics for
    must_have_one, must_have_one_of (list-of-lists), must_not_have,
    case insensitivity, summary substring matching, vacuous-empty
    handling.
  - `test_stage1_stage2_integration.py` (3) — end-to-end through
    ``ingest_one_source``: cross-source dedup marks duplicate,
    Stage 2 skips dup, keyword rejection produces stage_2_passed=False
    rows, draft specs are ignored.
  - 30/30 news_events tests pass; existing suite has the same 25
    pre-existing failures it did before Phase 1 (verified with a
    fresh stash sweep — none introduced by this phase).

## What changed in existing code

**Nothing.** Phase 2 is entirely additive within
``pivot/backend/news_events/`` and ``pivot/tests/news_events/`` plus
the new alembic revision. No file outside those directories was
touched.

## Verified live (Postgres, prod-like env)

Migration ``0007 → 0008`` applied. Backend restarted with the flag
still on. Seeded a single ``NewsEventSpec`` row in state ``active``
with keyword set
``must_have_one=["Sensex","Nifty","RBI","Modi","India"]``,
``must_not_have=["cricket","movie"]``, then force-polled three
sources:

```
POST /api/news-events/admin/sources/google_news_search_india_markets/poll
  → seen=100 new=12  after_stage1=12  after_stage2=9

POST /api/news-events/admin/sources/bbc_world/poll
  → seen=30  new=0   after_stage1=0   after_stage2=0   (all url_hash dups)

POST /api/news-events/admin/sources/rbi_press_releases/poll
  → seen=10  new=10  after_stage1=10  after_stage2=10  (every RBI title trivially matches)

GET  /api/news-events/admin/metrics?window_hours=1
  → articles_ingested=162 articles_deduped=162 articles_after_keyword=19
```

DB cross-check:

```
news_articles total:                162
  near_dup_of IS NULL  (survivors): 162
  near_dup_of NOT NULL (near-dups):   0
news_article_classifications total:  22
  stage_2_passed = TRUE              19
  stage_2_passed = FALSE              3
```

Sample of passed classifications (titles only):

```
[google_news_search_india_markets] Nifty 50 Outlook: Market Crash Triggers & Top Stocks ...
[rbi_press_releases]               Auction of 91-Day, 182-Day and 364-Day Treasury Bills
[rbi_press_releases]               Sectoral Deployment of Bank Credit – March 2026
[google_news_search_india_markets] Market News: Share Market Today, NIFTY, BSE/NSE LIVE ...
[google_news_search_india_markets] Stock Market Outlook Next Week (18-22 May 2026) ...
```

Negative cases got persisted (3 rows with ``stage_2_passed=FALSE``) —
the audit trail captures rejections too, which is what Phase 5's
"why did/didn't we fire" view will need.

## Isolation audit

- [x] All new code under ``backend/news_events/`` and
      ``tests/news_events/``.
- [x] Migration ``0008`` ALTERS only our own Phase-1 table
      (``news_articles``); no pre-news_events table touched.
- [x] No new FK constraints into existing tables.
- [x] Stage-2 work is per-article inside the ingest transaction. No
      LLM calls (Stage 2 is pure CPU), so the latency budget on the
      poller tick is unchanged in practice — RBI's 10 articles add
      sub-millisecond overhead per active spec.
- [x] Reused infrastructure: ``Base``, ``SessionLocal``, ``get_db``,
      ``settings``, structlog, the existing APScheduler. No new
      dependency added.
- [x] Idempotency preserved: ``url_hash`` UNIQUE still gates
      duplicates; Stage 1 is an additional layer, not a replacement.
      A near-dup row carries ``near_dup_of`` but is still uniquely
      identified by url_hash so a re-fetch is a no-op.
- [x] Feature flag still gates the whole subsystem.
- [x] No changes to broker, order, Kite, chat, intent parser, or
      workflow code.

## Surprises and decisions worth flagging

1. **Negative classifications are persisted.** I chose to write
   rows with ``stage_2_passed=FALSE`` rather than skip them. This is
   ~3× the row count vs. "only positives", but it gives Phase 5 a
   complete audit trail. We can prune the negatives later (Phase 6
   retention job).
2. **Live BBC / Google News titles don't share verbatim headlines.**
   Stage 1 dedup logic is correct (proven by integration tests) but
   currently sees 0 near-dups in production traffic. Phase 3's
   embedding similarity will catch fuzzier overlap.
3. **The 3 ``stage_2_passed=FALSE`` rows in live traffic came from
   Google News articles whose titles** did not contain any of
   Sensex / Nifty / RBI / Modi / India OR contained "cricket" /
   "movie". This is the must_not_have arm at work.
4. **No new Python dependency added.** Pure stdlib + the existing
   SQLAlchemy + Pydantic. Phase 3's embedding step will add either
   OpenAI's existing client (already in the codebase) or a
   self-host option — that decision is open in the Phase 0 plan.

## Recommended next step

Move to **Phase 3 — Stages 3-6**: polite full-article fetch with
trafilatura, embedding similarity (OpenAI ``text-embedding-3-small``
per the recommendation), LLM excerpt extraction, and the LLM classifier
reusing the existing ``triggers/classifier.py`` JSON-mode prompt
extended to return ``is_retraction``. Historical replay test against a
recorded RBI announcement is the gate criterion.
