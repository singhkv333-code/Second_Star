# News & Event Trigger — Phase 3 summary

**Status:** complete. Ready for human review before Phase 4.
**Date:** 2026-05-21.
**Scope:** Funnel Stages 3-6 (full-article fetch + embedding
similarity + LLM excerpt extraction + LLM classifier with retraction
detection) plus a separate APScheduler worker that drains the
pending queue.

---

## What was built

- **Migration `0009_news_body_and_embeddings`** — additive ALTERs
  on our own Phase-1 tables:
  - `news_articles.body_fetched_at` `TIMESTAMPTZ NULL`
  - `news_articles.body_fetch_status` `VARCHAR(32) NULL`
  - `news_articles.text_embedding` `JSONB / JSON NULL`
  - `news_event_specs.description_embedding` `JSONB / JSON NULL`
  - covering index `ix_news_classifications_pending` for the
    funnel's hot query.

- **`backend/news_events/pipeline/fetch_body.py`** — Stage 3.
  Robots.txt cache (per host, process-lifetime), identifying
  User-Agent on every request, exponential backoff retry on 5xx
  (3 attempts), 4xx treated as fatal (no retry-storm against a
  blocking publisher), trafilatura extraction with conservative
  settings, output capped at 50 KB to bound downstream LLM cost.

- **`backend/news_events/pipeline/embed.py`** — Stage 4. OpenAI
  `text-embedding-3-small` via direct httpx (no SDK dep). Cached
  spec / article embeddings on the new JSON columns —
  re-embedding the same spec or the same article body is a no-op.
  `cosine_similarity` helper. `SIM_THRESHOLD = 0.20` gates Stage 5.

- **`backend/news_events/pipeline/excerpt.py`** — Stage 5. Uses the
  existing `get_llm_client()` factory with
  `response_format="json_object"`, `reasoning_effort="minimal"`,
  `temperature=0.0`, `max_output_tokens=400`, and
  `prompt_cache_key="news_events.excerpt.v1"`. Returns 2-3
  verbatim sentences from the body or the empty string on parse
  failure / empty body (Stage 6 then falls back to title-only).

- **`backend/news_events/pipeline/classify.py`** — Stage 6. Extends
  the trusted prompt shape from `backend/triggers/classifier.py`
  with a 5-state verdict (YES / NO / AMBIGUOUS / UNRELATED /
  RETRACTION), a `confidence` float, an `is_retraction` flag,
  and a one-sentence reason. `prompt_cache_key="news_events.classify.v1"`.
  Never raises — any failure path returns
  `UNRELATED, confidence=0` so the funnel keeps moving.

- **`backend/news_events/pipeline/funnel.py`** —
  `process_pending(batch_size=5)` orchestrates Stages 3-6 over the
  pending classifications. Joins
  `news_article_classifications` → `news_articles` → `news_event_specs`
  with the same `near_dup_of IS NULL` + `state='active'` guards the
  earlier phases established. Per-row exception handling: a single
  failure marks the row UNRELATED with the error in `excerpt`, then
  the batch continues. Returns a `FunnelTickResult` summary.

- **`backend/news_events/workers/funnel.py`** —
  `register_funnel_worker(scheduler)` attaches one APScheduler job
  ticking every 60s. `max_instances=1, coalesce=True` keeps a slow
  tick from stacking.

- **`backend/main.py`** — flag-gated registration now wires both
  `register_poller` and `register_funnel_worker`. With the flag
  off, neither line executes and neither module is imported.

- **`backend/news_events/router.py`** — `/admin/metrics` gets a
  fourth real counter: `articles_sent_to_llm` = COUNT
  classifications with `classifier_verdict IS NOT NULL` for
  articles in the window.

- **`requirements.txt`** — one new dependency: `trafilatura==1.12.2`.
  Pure Python, MIT, no compiled extensions.

- **Tests** — 28 new (58 total under `tests/news_events/`):
  - `test_fetch_body.py` (6) — robots.txt cache reuse, success
    path, robots-disallow, 4xx-fatal, 5xx-retry-then-succeed,
    extract-failed.
  - `test_embed.py` (9) — cosine on orthogonal / identical /
    opposite / zero / mismatched / empty vectors, spec embedding
    caching, article embedding persistence, swallowed client error.
  - `test_excerpt_and_classify.py` (9) — clean JSON, fenced JSON,
    garbage handling, empty-body short-circuit, YES verdict,
    RETRACTION forces `is_retraction=True`, unknown-verdict
    fallback, confidence clamping, exception swallowed.
  - `test_funnel.py` (4) — end-to-end with all externals stubbed:
    YES verdict reaches the row, below-threshold gate skips
    Stage 5+6, inactive specs ignored, already-classified rows
    excluded from the batch.
  - **58/58 pass.** Existing suite still at the same 25
    pre-existing failures (verified independently).

## What changed in existing code

Two flag-gated additions to `pivot/backend/main.py` (now includes
the funnel worker registration alongside the poller). One new
requirement line in `requirements.txt`. **No other existing file
was modified.** No touch on workflows, broker, chat, intent parser,
or orders code.

## Verified live (Postgres, prod-like env)

1. Migration `0008 → 0009` applied cleanly. Backend restarted with
   the flag on; structured log confirms:
   ```
   [news_events.poller] registered 5 source jobs
   [news_events.funnel] registered drain job (60s)
   ```
2. **Stage 3 against a real RBI press release URL** — fetched
   HTTP 200, trafilatura extracted 5415 chars of text. (See
   "Surprises" below — the extraction grabs page chrome on
   `rbi.org.in/Scripts/BS_PressReleaseDisplay.aspx?...`; site-specific
   tuning needed before Phase 5.)
3. **Stage 3 against Google News URLs** — robots.txt at
   `news.google.com/robots.txt` has `Disallow: /rss/articles`, so
   the fetcher correctly returned `body_fetch_status='robots_disallowed'`
   without touching the body URL. Polite-citizen rules verified.
4. **Stage 4 / 5 / 6** — live verification was blocked by an OpenAI
   429 (this account's embeddings quota is currently exhausted).
   The funnel handled it gracefully: rows remained pending with
   `embedding_similarity=NULL`, no verdict written, no row was
   poisoned. Next tick re-tries once the quota refreshes. Tests
   exercise the success and failure paths end-to-end with stubbed
   externals; the wiring is correct.

`/api/news-events/admin/metrics?window_hours=2` now returns the
Phase-3 counter:

```json
{
  "window_hours": 2,
  "sources_active": 3,
  "articles_ingested": 162,
  "articles_deduped": 162,
  "articles_after_keyword": 19,
  "articles_sent_to_llm": 0,
  "events_fired": 0
}
```

## Isolation audit

- [x] All new code under `backend/news_events/` and
      `tests/news_events/`.
- [x] Migration `0009` ALTERs only our own news_events tables.
- [x] No new FK constraints into pre-existing tables.
- [x] One new third-party dependency: `trafilatura==1.12.2`,
      pure Python, MIT, no compiled extensions, no version conflict
      with the existing tree.
- [x] LLM client reused via `get_llm_client()` — provider routing
      stays in the existing factory.
- [x] OpenAI embeddings called directly via httpx (no SDK dep)
      because no embedding interface exists on the LLM client
      abstraction yet. The call uses `settings.openai_api_key`
      (already in `backend/config.py`).
- [x] Funnel runs on the existing `AsyncIOScheduler` — no new
      scheduler / process / thread pool.
- [x] Failure modes never crash the loop: every external call
      (HTTP fetch, embedding API, both LLM calls) is wrapped in
      `try/except` and persists a status so the next tick can
      decide what to do.
- [x] Stage-3 errors are not retried within the same process —
      the row's `body_fetch_status` records the outcome and the
      funnel skips it next tick. Avoids retry-storming a
      publisher that's blocking us.
- [x] Idempotency: re-running the funnel against the same
      classification is a no-op once `classifier_verdict` is set
      (the SELECT filter excludes already-verdicted rows).

## Surprises and decisions worth flagging

1. **Google News `news.google.com/rss/articles/...` URLs are
   robots-disallowed.** The Phase-1 source registry treats Google
   News as a Tier-2 keyword feed, but its RSS items wrap Google's
   own redirect URLs rather than the publisher's. We honour the
   robots rule. To get bodies for Google News-discovered stories
   we'd need a one-hop redirect-resolver (Phase 4+ if needed)
   that follows the Google News URL to the publisher article.
   For Tier-2 keyword discovery the title + summary is usually
   enough — Phase 5 will revisit if false-negatives spike.
2. **Trafilatura on RBI's ASP.NET site grabs the side-nav.**
   Their press release pages have heavy chrome and a tiny content
   region; trafilatura's heuristics pick the wrong block. Not a
   correctness bug in our code but a content-quality issue worth
   per-site tuning (a custom Stage-3 hook for RBI URLs in Phase 5).
   Stage 5 (excerpt extraction) will at least filter the nav out
   when the LLM hunts for relevant sentences — the chrome contains
   no event-bearing text.
3. **No embedding interface on the LLM abstraction.** I called
   OpenAI's embeddings endpoint directly via httpx rather than
   extending `LLMClient`. Rationale: the existing abstraction is
   chat-completion-shaped, and adding an `embed()` method only for
   this subsystem would change the LLM client surface for a single
   caller. If a second caller emerges, we extend the abstraction
   then.
4. **OpenAI 429 was instructive.** The funnel handled the quota
   error exactly as designed — log + None + skip, no churn, no
   data corruption. Same behaviour expected for any future
   transient outage.
5. **Negative classifications still persisted with
   `classifier_verdict` filled in.** Even an UNRELATED verdict
   removes the row from the pending queue, so the audit trail is
   complete and the funnel doesn't re-classify the same article
   against the same spec on every tick.

## Open questions still outstanding

The Phase-0 plan's §6 open questions, status as of Phase 3:

- (Q1) Touch 1 wrapper — still deferred to Phase 5.
- (Q4) Embedding provider — **resolved**: OpenAI
  `text-embedding-3-small`. Self-host migration path is a single
  function swap inside `embed.py::embed_text`.
- (Q5) n8n proxy — Phase 7.
- (Q6) NSE / BSE filings — deferred, Phase 1 sources only.
- (Q7) Kalshi — Phase 6.
- (Q8) Approvals UI — Phase 5.
- (Q9) Prod-egress verification of BS / ET / Mint / SEBI —
  outstanding.
- (Q10) Retention policy — outstanding; deferred to a retention
  job (probably Phase 5 or 6).

## Recommended next step

Move to **Phase 4 — Event specs + NL parsing + Tier-3
disambiguation**:

- NL → `EventSpec` parser (mirrors `backend/workflows/propose.py`)
- Disambiguation session machinery (Tier-3, reuses chat
  `PendingToolCall` pattern conceptually but lives in the
  `news_disambiguation_sessions` table)
- User-facing endpoints: `POST /api/news-events/specs`,
  `POST .../disambiguate`, `POST .../activate`,
  `GET .../specs/{id}`, `POST .../specs/{id}/cancel`.
- Spec state machine: draft → pending_disambiguation → active
  → fired / expired / cancelled.

Phase 4 has no Stage-7+ work — firing waits for Phase 5.
