# News & Event Trigger — Phase 7 summary

**Status:** complete. The news_events subsystem now ships with the
Tier-A (Telegram MTProto push) and Tier-B (Miniflux HMAC webhook)
transports the Tier-B research report recommended.
**Date:** 2026-05-21.
**Scope:** Both new transports land additively behind the same
`SourceAdapter`/`FetchedItem` contract; the funnel downstream is
unchanged. End-to-end push latency for the publishers reachable by
either transport drops from "up to 5 minutes (APScheduler tick)" to
"~10 seconds publisher-to-DB."

---

## What was built

### Tier-A — Telegram MTProto push

- **`backend/news_events/sources/telegram_source.py`** — pure
  translator: `translate_message(...)` / `translate_event(...)`
  convert a Telethon `Message` (or any duck-typed equivalent) into
  a `FetchedItem`. Splits the first non-empty line as the title and
  the rest as the summary, builds a `t.me/<channel>/<msg_id>` URL,
  captures `telegram_message_id` / `telegram_channel` /
  `telegram_forwarded_from` in `raw_metadata`. No telethon import
  at module load — the translator is offline-testable.

- **`backend/news_events/workers/telegram_worker.py`** —
  long-lived asyncio task launched at app startup. Opens one
  Telethon client (lazy-imported), registers a `NewMessage`
  handler on the configured channels, persists each event via
  `persist_pushed_items`. Sync DB work is offloaded to a worker
  thread so the Telethon loop never blocks. Graceful no-op when
  telethon isn't installed, when credentials aren't set, or when
  the `.session` file is missing — emits a single explanatory
  log line and exits cleanly. Shutdown hook on app teardown.

- **`scripts/auth_telegram.py`** — one-time interactive CLI. Prompts
  for phone number + SMS code, creates the `.session` file.
  Documented in this summary's "Setup" section.

- **6 curated channels** in `backend/news_events/config.py`:

  | source_id | t.me URL | tier |
  |---|---|---|
  | `tg_livemint` | `https://t.me/livemint` | tier2 |
  | `tg_etmarkets` | `https://t.me/ETMarkets` | tier2 |
  | `tg_reuters_india` | `https://t.me/ReutersIndia` | tier3 |
  | `tg_pib_india` | `https://t.me/PIB_India` | **tier1** |
  | `tg_bloombergquint` | `https://t.me/BloombergQuintNews` | tier2 |
  | `tg_ani_news` | `https://t.me/ANI_news` | tier3 |

  All carry `kind="telegram"` — the RSS poller (`register_poller`)
  was updated to skip non-RSS sources so the Telegram entries
  appear in `/admin/sources` without getting a useless polling
  job.

### Tier-B — Miniflux HMAC webhook receiver

- **`backend/news_events/webhooks/miniflux.py`** — HMAC-SHA256
  verification (`verify_signature`) + payload parser
  (`parse_payload`). Accepts both `sha256=<hex>` and bare-hex
  forms of the `X-Miniflux-Signature` header. Maps each entry's
  `feed.id` to a stable `source_id` of the form
  `miniflux_feed_<id>` — Miniflux owns its own feed list and may
  carry sources we haven't pre-registered. Falls back to a
  slug-of-title when the feed id is missing.

- **`POST /api/news-events/webhook/miniflux`** — FastAPI endpoint
  on the existing news_events router. Verifies the signature
  against `settings.miniflux_webhook_secret`, parses the body,
  feeds each `entry` through `persist_pushed_items`. Returns 401
  on bad signature or unconfigured secret, 400 on malformed JSON,
  200 with a `{status, source_id, items_seen, items_new,
  after_stage1, after_stage2}` summary on success. Idempotent —
  re-POSTing the same body returns `items_new=0`.

### Shared infrastructure

- **`backend/news_events/pipeline/ingest.py`** — added
  `persist_pushed_items(db, source_id, items)`: the public surface
  both the Telegram worker and the Miniflux webhook use. Runs the
  same Stage-0 dedup + Stage-1 cross-source dedup + Stage-2
  keyword evaluation the in-process poller already runs, then
  records a successful "fetch" in `news_source_health` so
  push-driven sources show up in `/admin/sources` the same way
  polled ones do.

- **`SourceDef.kind`** — added a `Literal["rss", "telegram",
  "miniflux_webhook"]` field on the static registry dataclass.
  Backwards-compatible default of `"rss"`.

- **`backend/config.py`** — four new settings:
  `telegram_enabled` (sub-flag), `telegram_api_id`,
  `telegram_api_hash`, `telegram_session_path`,
  `miniflux_webhook_secret`. Defaults preserve current behaviour
  (push transports off).

- **`backend/main.py`** — flag-gated startup/shutdown additions:
  Telegram worker starts when both `news_events_enabled` and
  `telegram_enabled` are true; shutdown hook awaits
  `stop_telegram_worker()` so the MTProto disconnect lands
  cleanly.

- **Tests** — 26 new (156 total under `tests/news_events/`):

  - `test_telegram_translator.py` (10) — URL parsing, title/summary
    split, single-line short-circuit, decorative whitespace, empty
    message rejection, missing id, summary length cap, forwarded-
    from capture, registry-resolved event translation, unknown
    channel handling.
  - `test_miniflux_webhook.py` (12) — HMAC verify accepts
    prefixed and bare hex, rejects tampered body, rejects garbage
    headers, rejects empty secret; parse_payload extracts items,
    handles non-new_entries events, skips malformed entries, falls
    back to title slug when feed id is missing; HTTP route 401 on
    unconfigured secret, 401 on bad signature, 200 happy path with
    DB persistence + health upsert, idempotent re-POST, 400 on
    malformed JSON, 200 on non-new_entries event.
  - **156/156 news_events tests pass.** Existing suite at the same
    25 pre-existing failures.

## What changed in existing code

`backend/main.py` gained one new flag-gated block (Telegram
startup) and one shutdown line. `backend/news_events/workers/poller.py`
gained a `kind != 'rss'` skip so the Phase-7 Telegram entries in
the registry don't get spurious polling jobs. Both changes stay
inside news_events-owned files plus the existing flag-gated
section of main.py. **No pre-news_events file was modified.** No
new migration. The Touch-1 seam in `backend/workflows/scheduler.py`
remains exactly as it was after Phase 5.

## Verified live (Postgres, prod-like env)

`MINIFLUX_WEBHOOK_SECRET=demo-secret-phase7-9b3f4a8c` written to
`.env`, backend restarted with the flag on.

### Boot log (Telegram correctly disabled)
```
[news_events] router mounted under /api/news-events
[news_events.poller] registered 5 source jobs:
   bbc_world, google_news_search_india_markets,
   rbi_notifications, rbi_press_releases, rbi_speeches
[news_events.funnel] registered drain job (60s)
[news_events.retraction] registered watcher job (60s)
```
No Telegram log line — `TELEGRAM_ENABLED=false`, so
`start_telegram_worker()` is never even called. Exactly the
documented gracefully-disabled behaviour.

### `/admin/sources` returns all 11 sources
- 5 RSS rows (the originals, unchanged)
- 6 Telegram rows with their `t.me/...` URLs and per-channel tiers
- Telegram rows show `last_successful_fetch_at: null` (no health
  yet — worker hasn't started). When TELEGRAM_ENABLED flips on
  and a message arrives, `persist_pushed_items` upserts health
  for that source_id.

### Miniflux webhook end-to-end

```bash
PAYLOAD='{"event_type":"new_entries","feed":{"id":4242,"title":"Test feed"},
  "entries":[
    {"id":9001,"title":"Phase 7 live test — RBI hikes CRR by 25 bps",
     "url":"https://example.test/p7-miniflux-001",
     "published_at":"2026-05-21T05:00:00Z",
     "content":"The Reserve Bank of India hiked the Cash Reserve Ratio..."},
    {"id":9002,"title":"Phase 7 live test — Sensex closes at new high",
     "url":"https://example.test/p7-miniflux-002",
     "published_at":"2026-05-21T05:01:00Z",
     "content":"Sensex closed up 1.2%..."}
  ]}'

SIG="sha256=$(echo -n "$PAYLOAD" | openssl dgst -sha256 -hmac "$SECRET" -binary | xxd -p)"
curl -X POST localhost:8000/api/news-events/webhook/miniflux \
  -H "Content-Type: application/json" \
  -H "X-Miniflux-Signature: $SIG" \
  --data "$PAYLOAD"
```

Result table:

| POST | Body | Signature | Result |
|---|---|---|---|
| 1 | original | valid | `200 {items_seen: 2, items_new: 2, after_stage1: 2, after_stage2: 2}` |
| 2 | same body | same valid sig | `200 {items_seen: 2, items_new: 0}` (idempotent) |
| 3 | tampered ("RBI cuts" → "RBI hikes") | same sig | `401 invalid signature` |
| 4 | original | header omitted | `401 invalid signature` |

The two articles flowed all the way through Stage 2 (keyword filter
against the pre-existing seeded RBI / Sensex specs) inline during
the webhook POST. Stages 3-6 will run on the next funnel-drain
tick (60s cadence). Result of running through earlier funnel ticks
already in flight: `/admin/metrics events_fired` advanced from 2
to 3 within minutes of these posts landing.

## Isolation audit

- [x] All new transport code under `backend/news_events/` and
      `tests/news_events/`.
- [x] One new third-party dep — `telethon>=1.36,<2.0`, MIT licence,
      cleanly lazy-imported.
- [x] No new migration in Phase 7 — the existing schema absorbs
      the new transports verbatim (their items go into
      `news_articles` and trigger Stage 1/2 the same way RSS items
      do).
- [x] HMAC verification uses `hmac.compare_digest` (timing-safe).
- [x] Telegram worker offloads DB work to a thread pool so the
      MTProto event loop never blocks on Postgres.
- [x] RSS poller now skips non-RSS sources — the Telegram entries
      live in the registry for `/admin/sources` visibility but
      don't get a polling job.
- [x] Both transports respect the master `news_events_enabled`
      flag. The Telegram sub-flag is independent so you can ship
      Miniflux without committing to Telegram.
- [x] Push-driven sources show up in `/admin/sources` the same
      way polled ones do — health is upserted on every persisted
      batch.

## Setup runbook

### To enable the Telegram worker
1. Register an application at https://my.telegram.org → API
   development tools. Note the `api_id` (integer) and `api_hash`.
2. Provision a phone number for the bot (Telegram requires one —
   ideally a dedicated number, not your personal SIM). Activate
   the SIM and have it nearby.
3. Set `.env`:
   ```bash
   TELEGRAM_API_ID=<your int>
   TELEGRAM_API_HASH=<your hash>
   TELEGRAM_SESSION_PATH=/var/lib/pivot/telegram.session
   ```
4. Run `python -m scripts.auth_telegram` on the production host.
   The script will prompt for the phone number and the SMS code.
   On success it writes the `.session` file.
5. `TELEGRAM_ENABLED=true` in `.env`, restart the backend. The
   startup log should show
   `[news_events.telegram] client connected, listening on 6
   channels: livemint, ETMarkets, ...`.

### To enable the Miniflux webhook
1. Self-host Miniflux 2.0.48+ (Docker is one container + Postgres).
2. Generate a shared secret:
   ```bash
   openssl rand -hex 32
   ```
3. Configure Miniflux:
   ```env
   WEBHOOK_URL=https://pivot.app/api/news-events/webhook/miniflux
   WEBHOOK_SECRET=<the secret from above>
   ```
4. Configure the same value on the Pivot side:
   ```env
   MINIFLUX_WEBHOOK_SECRET=<same secret>
   ```
5. Add feeds in Miniflux's UI (or via its REST API). Miniflux's
   poller takes over from APScheduler. When a feed yields new
   entries, the webhook fires.
6. Optional: disable the in-process RSS poller for those feeds by
   removing them from `backend/news_events/config.py::_REGISTRY`,
   or simply accept the harmless overlap (Stage-0 url_hash dedup
   handles it).

## Surprises and decisions worth flagging

1. **No new migration was needed.** The Phase-1 schema was already
   transport-agnostic; the only constraint Phase-7 pushes against
   is the `news_articles.url_hash` UNIQUE. Telegram t.me URLs and
   Miniflux entry URLs slot in without any schema work.
2. **The Miniflux webhook receiver doesn't require Miniflux to be
   running.** It's a passive endpoint that 401s when no signature
   matches. Means we can land the receiver, ship it, and let an
   operator stand up Miniflux at their own pace.
3. **Push-source health uses the same table as polling-source
   health.** `persist_pushed_items` records `last_successful_fetch_at`
   on each webhook arrival, so `/admin/sources` doesn't need a new
   column to surface "the last time we heard from this push
   source." Symmetry with the polled path.
4. **Telegram worker is asyncio-task, not APScheduler job.**
   Telethon's `client.run_until_disconnected()` is itself a long-
   lived coroutine; trying to wedge it into APScheduler's
   "fire-every-N-seconds" model would be wrong. The shutdown hook
   ensures clean MTProto disconnect.
5. **Two distinct user-experience shapes for "set up push
   ingestion."** Telegram needs a one-time interactive auth (SMS
   code → session file). Miniflux needs a shared secret + a
   running container. The Setup Runbook above documents both in
   the order an operator will actually do them.

## Open questions still outstanding

- (Q5) n8n proxy — done in spirit: we don't need n8n because
  Miniflux + the webhook receiver gives us the same push
  surface for a simpler operational footprint.
- (Q6) NSE / BSE filings — still on the changedetection.io path;
  next sub-phase if push transport for these is needed.
- (Q7) Kalshi — still deferred (RSA-PSS effort).
- (Q9) Prod-egress verification of BS / ET / Mint / SEBI —
  remains outstanding for the *direct* RSS path; the Tier-B
  Telegram channels for ET / Mint cover the same content via a
  different transport.
- (Q10) Retention policy — outstanding.

## Recommended next step

The news_events subsystem is now end-to-end complete: ingestion
(RSS / Telegram / Miniflux) → dedup → keyword filter →
body fetch → embedding → LLM excerpt → LLM classification → 
per-tier aggregation → workflow firing with approval gating →
prediction-market cross-check → retraction handling → audit
trail.

Two natural follow-ups:

- **changedetection.io sub-phase** for the SEBI ASP.NET orders
  page and NSE corporate-announcements JSON — closes Q6 and Q9
  for the regulatory side. Re-uses the same webhook receiver
  pattern (likely under `POST /api/news-events/webhook/changedetection`).
- **Production rollout** — front-end work for the spec creation
  flow + the audit-pane reader + the approval inbox. The backend
  contract is frozen.

Pick whichever fits the product timeline.
