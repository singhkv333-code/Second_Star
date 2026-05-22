# News & Event Trigger — Phase 0 Plan

**Status:** Awaiting human approval. No feature code has been written.
**Date:** 2026-05-21.
**Scope:** Architecture summary + isolated module design + phased build order + open questions. Feature code begins only after approval.

---

## 1. Existing architecture — what's actually there

> Verified by reading the repo, not assumed.

### 1.1 Backend skeleton

- **FastAPI** app at `pivot/backend/main.py`. 23 routers registered (auth / orders / chat / workflows / runs / approvals / webhooks / scheduled / markets / quotes / kite / etc.).
- **Config:** `pivot/backend/config.py` — pydantic `BaseSettings` with `.env`. Includes `llm_provider` (`openai|sarvam|azure`), `llm_model`, `newsapi_key`, `kite_*`, `kite_token_enc_key` (Fernet).
- **Logging:** structlog JSON renderer with `request_id` / `user_id` context, sqlalchemy/uvicorn clamped to WARNING. Sentry optional. Per-LLM-call cost ledger.
- **Secrets at rest:** Fernet via `backend/security/encryption.py` (commit 5440f18). Helper `read_kite_access_token()` already in use at 10 call sites.
- **DB:** SQLAlchemy 2.0 + psycopg2 sync. Single `models.py` (528 lines). Two engines: operational Postgres + read-only Moneycontrol financials.
- **Tests:** pytest with in-memory SQLite, shared session fixture, TestClient with `get_db` override.

### 1.2 Workflows engine (the consumer of "event happened")

- **Models** (in `pivot/backend/models.py`): `workflows`, `workflow_steps`, `workflow_runs`, `workflow_run_steps`, `workflow_approvals`, `workflow_webhook_tokens`.
- **Trigger types defined in `backend/workflows/schemas.py`:** `schedule`, `manual`, `webhook`, `price`, `indicator`, `market_relative_time`, **`event`** — `TriggerEventConfig` already at line 204 with `keywords`, `event_description`, `min_confidence`, `sources`, `hours_back`, `poll_seconds`, `max_runtime_minutes`.
- **`triggered_by='event_alert'` is already in the `workflow_runs` CHECK constraint** at `models.py:329`. No migration needed to fire as `event_alert`.
- **Scheduler:** APScheduler `AsyncIOScheduler`. Workflow-side jobs registered in `backend/workflows/scheduler.py`:
  - `_poll_due_workflows` — 30s, fires `trigger.schedule` workflows.
  - `_poll_watch_triggers` — 60s, runs `price` / `indicator` watchers.
- **The firing seam** is `_fire_watch_run(workflow_id, triggered_step_index, triggered_by, fired_at)` at `backend/workflows/scheduler.py:668`. It inserts a `WorkflowRun` row with `context={}` and `asyncio.create_task(engine.execute_run(run_id))`. **This function is our integration point — it accepts any `triggered_by` value the constraint allows, including `event_alert`.**
- **Approval / confirmation surface** already exists: `WorkflowApproval` rows + `action.place_order` step with `requires_approval=true` pauses the run until the user approves. The Tier-spec requirement of "explicit user confirmation before execution" is **a configuration of the existing engine**, not new code.
- **Broker gating:** `KITE_MOCK_MODE` flag in `backend/kite/auth.py` gates real Kite calls. `/orders/register` still writes a `TradeLog` row without calling the broker. The "register-not-execute" rule still holds for the user-facing order path; only workflow-internal `action.place_order` calls Kite, and only when the user has approved (if approval is configured).

### 1.3 Existing news / classification machinery

- `backend/triggers/news_client.py` — **NewsAPI.org** polling client (httpx async, 100 req/day free tier, keyword OR-join, source allowlist).
- `backend/triggers/credibility.py` — per-source credibility scoring.
- `backend/triggers/classifier.py` — `classify_article(article, event_description, client) -> (matched, confidence, reason)` using `client.complete()` with `response_format="json_object"` and `reasoning_effort="minimal"`. **Directly reusable as Stage 6.**
- `fetch.news` step executor calls these. **No shared firehose, no funnel, no dedup, no RSS — single-source per-workflow polling.**
- No RSS parsing, no trafilatura, no WebSub anywhere in the tree.

### 1.4 LLM abstraction

- `backend/llm/base.py` `LLMClient.complete(messages, *, tools, max_output_tokens, reasoning_effort, response_format, prompt_cache_key, ...)` — async, returns `LLMResponse` with usage tokens including `cached_tokens` for the cost ledger.
- Providers: OpenAI / Sarvam / Azure (Azure inherits OpenAI). Selected via env. **Cached only on OpenAI** (cache warmup explicitly skips Azure and Sarvam — confirmed in startup log).
- **No embedding API exists in the codebase.** Tool routing is regex/keyword based.

### 1.5 Chat & disambiguation

- Chat state in **Redis** (`chat:conv:{conv_id}`, 24h TTL, 20-turn cap, plain-text only). Per-session isolation per memory.
- **Multi-turn clarification implemented:** `PendingToolCall` in Redis (10min TTL); chat splices the user's next reply into the missing field deterministically with zero LLM calls when the type matches. We will reuse this surface for Tier-3 disambiguation.
- Workflow drafting via `backend/workflows/propose.py` — JSON-mode + retry-on-validation against the step registry. We'll mirror this pattern for event-spec parsing.

### 1.6 Background-job substrate

- **APScheduler `AsyncIOScheduler` with `SQLAlchemyJobStore`** — survives restarts.
- Jobs already running: SIP execution (09:15 IST), strategy trigger check (60s), Kite token refresh (07:30 IST), daily summary (15:45 IST), workflow schedule polling (30s), workflow watch triggers (60s).
- Pattern is well-trodden: register a job in main.py lifespan, run it on the existing event loop. We will follow it.

---

## 2. Source verification (May 2026)

| Source | Status |
|---|---|
| RBI press releases / notifications / speeches RSS | **Live**, parseable, fresh items. |
| BBC World RSS | **Live**, ~30 items, hourly. |
| Google News RSS search | **Live** for keyword queries. |
| SEBI `sebirss.xml` | Edge-blocked from research fetcher. Re-verify from prod egress with a browser UA before relying on it. |
| Business Standard category RSS (×6) | All returned **403** from research fetcher — almost certainly UA-blocking, not deprecated. Re-verify from prod with a browser-like UA. |
| Economic Times markets RSS | Edge-blocked. Re-verify from prod. |
| LiveMint /rss/markets | Edge-blocked. Re-verify from prod. |
| **WebSub** | **None of the verified Indian financial / regulator feeds advertise a hub.** Plan polling-only. WebSub is not viable as a primary transport for Pivot's source list. |
| Polymarket Gamma API | **Free, no auth.** Generous limits. |
| Kalshi REST API | **Free + RSA-PSS** auth, sessions 30 min. Private key shown once. |
| GDELT v2 events API | **Free, no key.** Distinct from new paid `gdeltcloud.com`. |
| n8n self-host | **Community edition free** under Sustainable Use License — fine as an internal RSS-to-webhook proxy, do not redistribute as part of the product. |

**Implication:** Phase 1 ships with the 5 verified-live feeds (RBI ×3, BBC, Google News). BS / ET / Mint / SEBI get a one-line "verify from prod egress" task before they are activated in production. Plan polling-only for all sources; WebSub is deferred to Phase 7 if and only if a future source advertises one.

---

## 3. Proposed isolated module

> All new code lives under `pivot/backend/news_events/`. Existing code is touched in exactly **two** places — both flagged below and gated on approval.

### 3.1 Module layout

```
pivot/backend/news_events/                    NEW package
├── __init__.py
├── feature_flag.py                           one flag: NEWS_EVENTS_ENABLED
├── config.py                                 source list, per-source poll intervals, adaptive windows
├── models.py                                 NEW ORM tables only (see §3.3)
├── schemas.py                                EventSpec / ResolutionCriteria / RetractionPolicy / KeywordSet
├── sources/
│   ├── base.py                               SourceAdapter interface (pull or push behind same shape)
│   ├── rss.py                                feedparser-based poller
│   ├── gdelt.py                              optional cross-check
│   ├── polymarket.py                         Tier-3 cross-check (Phase 6)
│   ├── kalshi.py                             Tier-3 cross-check (Phase 6)
│   └── websub.py                             push receiver stub (Phase 7)
├── pipeline/
│   ├── ingest.py                             Stage 0 — call source adapters
│   ├── dedup.py                              Stage 1 — title-hash / simhash
│   ├── keyword.py                            Stage 2 — must_have / must_not regex
│   ├── fetch_body.py                         Stage 3 — polite scraper + trafilatura + robots.txt
│   ├── embed.py                              Stage 4 — embedding similarity
│   ├── excerpt.py                            Stage 5 — LLM excerpt extraction
│   ├── classify.py                           Stage 6 — wraps existing classifier.py
│   ├── aggregate.py                          Stage 7 — per-tier firing rules
│   └── propose.py                            Stage 8 — build proposed orders, call firing seam
├── parsing/
│   ├── event_spec_parser.py                  NL → EventSpec (mirrors workflows/propose.py)
│   └── disambiguation.py                     Tier-3 2–3 question flow (reuses chat PendingToolCall)
├── workers/
│   ├── poller.py                             APScheduler job: per-source polling
│   ├── funnel.py                             worker that drains a per-event queue through the funnel
│   └── adaptive.py                           MPC days, earnings season, election windows
├── integration.py                            the seam: fire_event_alert(...) wrapper
├── audit.py                                  writes news_fired_events rows
├── metrics.py                                per-stage counters + per-source health
└── router.py                                 FastAPI router mounted under /api/news-events
```

**Tests:** mirror the package under `pivot/tests/news_events/` with one test file per stage plus integration tests that replay historical articles (Phase 3).

### 3.2 Feature flag

A single setting in `backend/config.py`: `news_events_enabled: bool = False`. With it off:

- The router is not included in main.py.
- No APScheduler jobs are registered.
- `integration.py` is a no-op.
- Migration 0007 still runs (tables exist), but they stay empty.

This satisfies the "with the flag off, Pivot behaves exactly as today" requirement.

### 3.3 New database tables (additive only, migration `0007_news_events.py`)

| Table | Purpose | Key columns |
|---|---|---|
| `news_event_specs` | User-defined event automations (one row per active rule). Wraps Tier metadata, resolution criteria, retraction policy, deadline, watch-window start, full keyword set, link to a workflow. | `id` PK, `user_id`, `workflow_id` (UUID, **no FK** to avoid touching `workflows.id`; flagged below), `tier`, `description`, `resolution_criteria` JSONB, `retraction_policy` JSONB, `deadline_at`, `watch_window_start_at`, `keyword_set` JSONB, `state` ('draft' / 'pending_disambiguation' / 'active' / 'fired' / 'expired' / 'cancelled'), `created_at`, `updated_at` |
| `news_articles` | Raw articles ingested from sources (deduped). | `id` PK, `source_id`, `url`, `url_hash` UNIQUE, `title`, `title_hash`, `summary`, `published_at`, `fetched_at`, `body_text` (nullable, populated only for survivors of Stage 2), `raw_metadata` JSONB |
| `news_article_classifications` | Per (article, event_spec) classifier verdict. Audit trail for why we did or didn't fire. | `id` PK, `article_id` FK, `event_spec_id` FK, `stage_2_passed` bool, `embedding_similarity` float nullable, `classifier_verdict` ('YES' / 'NO' / 'AMBIGUOUS' / 'UNRELATED' / 'RETRACTION'), `confidence` float, `excerpt` text, `model` text, `created_at` |
| `news_source_health` | Per-source poll health. | `source_id` PK, `last_successful_fetch_at`, `last_error_at`, `last_error_message`, `consecutive_failures`, `articles_seen_24h`, `articles_passed_24h` |
| `news_fired_events` | Audit + idempotency. One row per fired event. Holds the "why we fired" payload. | `id` PK, `event_spec_id` FK, `workflow_run_id` (UUID, no FK), `fired_at`, `tier`, `aggregated_confidence`, `supporting_classification_ids` JSONB array, `prediction_market_snapshot` JSONB nullable, `retraction_window_ends_at`, `retraction_status` ('none' / 'detected' / 'handled') |
| `news_disambiguation_sessions` | Tier-3 multi-question state during spec creation. | `id` PK, `user_id`, `conversation_id`, `pending_event_spec` JSONB, `questions` JSONB, `answers` JSONB, `state`, `created_at`, `expires_at` |

**No `ALTER` on any existing table.** The two "soft FKs" (`workflow_id`, `workflow_run_id`) are stored as plain UUIDs without database-level FK constraints to keep the migration 100 % additive. We enforce referential integrity in code.

### 3.4 New API namespace — `/api/news-events/`

| Method + path | Purpose |
|---|---|
| `POST /api/news-events/specs` | Submit NL text → returns either a draft `EventSpec` for confirmation or a Tier-3 disambiguation session. |
| `POST /api/news-events/specs/{id}/disambiguate` | Answer a disambiguation question; returns next question or final `EventSpec`. |
| `POST /api/news-events/specs/{id}/activate` | User confirms spec → state becomes `active`, watchers attach. |
| `GET /api/news-events/specs/{id}` | Fetch spec + current state + fired-events summary. |
| `POST /api/news-events/specs/{id}/cancel` | Cancel an active spec (sets `state='cancelled'`, detaches watchers). |
| `GET /api/news-events/fired/{id}` | Fetch the audit trail for a fired event. |
| `GET /api/news-events/admin/metrics` | Per-stage counts, per-source health, p95 funnel latency. (Internal / admin auth.) |
| `GET /api/news-events/admin/sources` | List sources, last fetch, error rate; manual re-poll endpoint. |

User confirmation **before any order execution** is handled by the existing `workflow_approvals` machinery — when the spec fires, the engine pauses on the `action.place_order` step until the user approves. No new "confirm" endpoint is needed; the existing approvals UI / `/api/workflow-approvals/{id}/decide` path is reused.

### 3.5 Integration points with existing code

> Every touch on existing code is listed here. Each carries an explicit minimal-change justification and is **gated on approval**.

**Touch 1 — `backend/workflows/scheduler.py`: expose `_fire_watch_run` as a public seam.**
- Current state: function is private (`_fire_watch_run`) and accepts `(workflow_id, triggered_step_index, triggered_by, fired_at)`. It inserts `context={}` (empty dict).
- Proposed change: add **one** public wrapper next to it — `async def fire_external_event(workflow_id, triggered_step_index, fired_at, audit_context: dict)` — that delegates to the existing private function but seeds `WorkflowRun.context` with an `audit_context` field rather than `{}`. The internal `_fire_watch_run` signature is unchanged.
- Why minimal: 1 new function, ~15 LOC, no signature change to existing callers, the `event_alert` value is already permitted by the model CHECK constraint.
- Alternative (zero touch): we instead `INSERT` into `workflow_runs` directly from `news_events.integration`. This works but duplicates ~20 lines of engine-coupled logic. **Recommend Touch 1.**

**Touch 2 — `backend/main.py`: conditional router registration.**
- Add three lines guarded by the feature flag: register the `news_events` router, register the `news_events` APScheduler jobs in the lifespan, log a startup line.
- Why minimal: the existing pattern for every router/job. Zero behavioural change when the flag is off.

**Optional touch — `backend/workflows/registry.py`: add a `trigger.news_event` step type.**
- Lets a workflow declare `{step_type: "trigger.news_event", config: {event_spec_id: "..."}}` so the existing engine "sees" the news-event trigger as a first-class step. This is the most idiomatic way to integrate, BUT it can be deferred: in Phase 5 we can fire the workflow run via Touch 1 without a new step type, and let the workflow start at step 0 (some existing step like `trigger.manual` or a no-op shim).
- Recommendation: **defer to Phase 5** and only add if the engine refuses to start a run whose first step isn't a registered trigger. We will measure this in Phase 1 and revisit.

**No other existing files are modified.**

### 3.6 Reuse of existing infra

- `backend/llm/factory.py` `get_llm_client()` for every LLM call (Stages 5, 6, parsing, disambiguation).
- `backend/triggers/classifier.py` `classify_article(...)` prompt + JSON contract reused as Stage 6's classifier (extended to return `is_retraction`).
- `backend/triggers/credibility.py` per-source credibility score used by Stage 7's aggregator.
- `backend/observability/logging_setup.py` structlog for all log lines.
- `backend/security/encryption.py` Fernet for the Kalshi RSA private key.
- `backend/services/llm_cost.py` cost ledger — every LLM call already auto-records into `llm_usage`.
- `WorkflowApproval` table + `action.place_order(requires_approval=true)` for the mandatory user confirmation step.
- `SessionLocal()` + the existing alembic chain (revision 0006 → 0007).

---

## 4. Phased build order

Each phase ends with a stop, a written summary, and a checklist of what changed.

| Phase | Deliverable | Touches existing code? |
|---|---|---|
| **1 — Ingestion skeleton** | Module skeleton, feature flag, migration 0007 (tables empty), `SourceAdapter` interface, RSS adapter, 5 watchers (RBI ×3, BBC, Google News), polling job, raw `news_articles` rows persisted, smoke `/admin/sources` endpoint. | Touch 2 (main.py registration, behind flag). |
| **2 — Funnel stages 1–2** | Dedup (title-hash, optional simhash), per-event keyword/regex filter, `/admin/metrics` showing `in / after_dedup / after_keyword` counts. | None. |
| **3 — Funnel stages 3–6** | Polite scraper + trafilatura + robots.txt cache (Stage 3), embedding similarity using OpenAI `text-embedding-3-small` (Stage 4), LLM excerpt extraction (Stage 5), classifier extended to return `is_retraction` (Stage 6). Historical replay test: feed the last RBI repo decision through the pipeline end-to-end and assert it classifies YES. | None. |
| **4 — Event specs + parsing + disambiguation** | `news_event_specs` row + state machine, NL parser (mirrors `workflows/propose.py`), Tier-3 disambiguation reusing `PendingToolCall`, `/api/news-events/specs` + `disambiguate` + `activate` endpoints. | None. |
| **5 — Aggregation + firing + confirmation** | Stage 7 per-tier aggregator, Stage 8 order construction, **Touch 1** — call `fire_external_event(...)` → existing engine runs the workflow → existing `workflow_approvals` table holds the user-confirmation step. Audit trail in `news_fired_events`. | Touch 1. Optional Touch 3 if engine requires a registered first step. |
| **6 — Tier 3 hardening** | Polymarket Gamma cross-check, retraction detection (Stage 6 retraction flag + safety-window watcher), retraction policy execution, full disambiguation-to-fire flow for political/geopolitical events. Kalshi optional (depends on RSA-PSS effort budget). | None. |
| **7 — Transport upgrade (optional)** | WebSub receiver (if any source ever adopts one) or n8n-as-RSS-to-webhook bridge. SourceAdapter contract stays the same. | None. |

**Cost budget check:** with the funnel surviving ~10–30 articles/day to the LLM classifier and ~$0.0001 per excerpt + classification call, the worst-case daily cost is well under ₹100. Embeddings on `text-embedding-3-small` at $0.02 per 1M tokens are ~₹1.5/day for the full survivor set.

---

## 5. Risks and how we prevent them

| Risk | Mitigation |
|---|---|
| Pipeline backlog or crash degrades order-execution latency | News-events worker is a separate APScheduler job; no synchronous call from the request path; failure is logged + surfaced in `/admin/metrics`, never raised into the engine. |
| Spec parser produces an `EventSpec` that fires too easily | Tier-3 requires disambiguation **before** the spec becomes `active`. Tier-1/2 specs still surface a draft + activate step. State machine prevents silent activation. |
| Duplicate firing of the same event | Idempotency at two levels: (a) `news_articles.url_hash` UNIQUE prevents reprocessing; (b) `news_fired_events.event_spec_id` + a once-fired guard prevents re-firing. The `client_request_id` in `action.place_order` covers broker-side dedup. |
| Indian publisher feed throws 403 on a non-browser UA | All HTTP fetches set a recognisable browser User-Agent identifying Pivot, honour `robots.txt`, and back off on 429/403. Source-health table flags persistent failures. |
| BS / ET / Mint / SEBI URLs turn out to be edge-blocked from prod too | Phase 1 ships with the 5 verified-live feeds and explicit "verify from prod egress" tickets for the others. Funnel works fine with a smaller source set. |
| LLM classifier hallucinates a YES on irrelevant content | Stage 7 requires the tier's confidence rule (Tier 2: 1 primary OR 2 secondary; Tier 3: primary + 1 secondary, conflicts → hold). Audit log records every classification including the excerpt. |
| Retraction within the safety window after firing | Stage 6 returns `is_retraction`; the safety-window watcher cancels pending approvals via the existing `workflow_approvals` API and surfaces an alert. |
| Hidden coupling to existing tests | Existing tests must still pass unchanged; CI gate. We do not modify shared fixtures or models. |
| Hidden coupling to existing chat / intent parser | News-event spec parsing lives in `news_events.parsing.event_spec_parser`, not in `agents/parser.py`. Reuses the LLM client only. |

---

## 6. Open questions for the human

Numbered for easy reply.

1. **Confirm Touch 1** — add a public `fire_external_event(...)` wrapper next to `_fire_watch_run` in `backend/workflows/scheduler.py`, ~15 LOC, no signature change to existing callers. Approve, prefer the zero-touch alternative (duplicate-insert from the news_events module), or propose another shape?

2. **`trigger.news_event` step type (Touch 3)** — should the engine see news-event triggers as a first-class step type (cleaner, but touches the registry), or do we drive everything through Touch 1 without a new step type? Recommendation: defer the decision to Phase 5 after measuring whether the engine demands a registered first step.

3. **EventSpec relationship to TriggerEventConfig** — the existing `TriggerEventConfig` is per-workflow-step and NewsAPI-tied. The new `EventSpec` is richer (tier, resolution criteria, retraction policy, deadline, keyword sets). Plan: keep both. `TriggerEventConfig` continues to drive the old per-workflow NewsAPI path; the new system is opted into via `news_events_enabled=true` and the new endpoints. Confirm or override.

4. **Embedding provider for Stage 4** — recommend OpenAI `text-embedding-3-small` (cheap, ~₹1.5/day for the full survivor set, no new infra). Alternative: self-hosted sentence-transformers `all-MiniLM-L6-v2` (free but adds a container dependency). Confirm OpenAI, or prefer self-hosted from day one?

5. **n8n proxy vs in-process polling** — recommend in-process polling (feedparser + httpx) for Phase 1, identical to the existing watchers. n8n proxy stays a Phase 7 option if push latency becomes a constraint. Confirm or push for n8n earlier?

6. **NSE / BSE corporate filings** — direct scraping is fragile (cookie + browser UA). Options: (a) skip in Phase 1 and design a swap-in source later; (b) use a wrapped paid third-party (e.g., a corporate-actions API) from Phase 6; (c) attempt a session-cookie scraper. Recommendation: **(a)** for the MVP — none of the verified live feeds today.

7. **Kalshi** — RSA-PSS auth, sessions expire 30 min, private key shown once. Worth Phase 6 effort, or Tier-3 cross-check is Polymarket-only for v1?

8. **User confirmation surface** — the plan reuses the existing `workflow_approvals` table + endpoints. Is the existing approval UI already wired in `pivot-next/` such that a news-event-fired workflow run will land in the same approval inbox, or does the FE need a new view? (If the FE work is non-trivial, I'd flag it as a separate phase.)

9. **Production egress verification of BS / ET / Mint / SEBI** — when can I run a one-off fetch from prod (or a VM with an Indian IP and a browser UA) to confirm these are reachable? This is a 10-minute task but blocks promoting them past Phase 1 staging.

10. **Audit log retention** — `news_articles` and `news_article_classifications` will accumulate. Default retention proposal: articles 90 days, classifications kept indefinitely (compact). Confirm or specify.

---

## 7. What I will NOT do until approval

- Write any feature code under `news_events/`.
- Run the migration.
- Modify any existing file except after the touches in §3.5 are explicitly approved.
- Change the existing NewsAPI / `triggers/` path.
- Change broker, order, or Kite code.
- Change the chat surface, intent parser, or workflow drafter.
- Push, commit, or open a PR.

Phase 1 starts only when you reply with approval (and answers to the questions in §6 you want to redirect).
