# Paywall build — message board

Two workers: **lead** (main session, builds) and **verifier** (the agent, researches + verifies).
Append-only. Newest entry at the BOTTOM. Each entry: `## [who] [time] subject`, then short bullets.
Status tags: `TODO` · `DOING` · `DONE` · `BLOCKED` · `QUESTION`.

## File ownership (never edit the other worker's files; never git stash/reset/checkout)
- lead: `charto/data/entitlements.py`, `charto/data/billing.py`, `charto/data/plans_catalog.json`, edits to `charto/data/dataserver.py`, `charto/data/alerts.py`, `charto/preview/**`, `pivot/**`
- verifier: `docs/paywall/RESEARCH.md`, `docs/paywall/ENDPOINTS.md`, `charto/data/test_entitlements*.py`
- shared: this file (append only)

## The spec (from the user's hand-written sheet, 2026-09-28)
| Feature | Free | Pro ₹499 (annual ₹449/mo) | Pro+ ₹999 (annual ₹899/mo) |
|---|---|---|---|
| AI credits (row placement ambiguous daily vs monthly) | 15 | 200 | 500 |
| Alerts F (unspecified) | ? | ? | ? |
| Alerts T (technical) | 20 | 100 | 1000 |
| Alerts P (price) | 20 | 400 | 1000 |
| Watchlist alerts | no | yes | yes |
| Indicators per chart | 5 | 10 | all |
| Charts per tab (layout) | 4 | 8 | 8 |
| Parallel charts | 10 | 20 | 50 |
| Ad-free | no | yes | yes |
| Custom timeframes | no | yes | yes |
| AI summaries | 10 | unlimited | unlimited |
| Multi-condition alerts | yes | yes | yes |
| Alert expiry | 2 months | 6 months | never |
| Watchlists | unlimited | unlimited | unlimited |
| Screen (screener) alerts | 3 | 50 | 75 |
| Historical bars | 10K | ? | ? |
| Saved screens | 5 | 50 | 50 |

---

## [lead] 2026-09-28 PLAN — architecture (DOING)
- **One source of truth**: plans live in charto's account DB (`charto_users.db`), because that is where the users are. pivot API never stores a plan; it asks charto (`GET /billing/me`, `POST /billing/consume` with the user's bearer or the internal key).
- **Catalog as data**: `charto/data/plans_catalog.json` — plans × feature keys. Code checks FEATURE KEYS (`alerts.price`, `ai.credits`), never `plan == "pro"`.
- **Feature kinds**: `flag` (bool), `limit` (cap on LIVE objects, counted at write time: alerts, layout charts, saved screens), `quota` (metered usage over a window, in a ledger: ai.credits, ai.summaries), `value` (non-count params: alerts.expiry_days, bars.history).
- **Tables** (new, in charto_users.db): `subscriptions` (one row per user: plan, cycle, status, period_start/end, cancel_at_period_end, provider ids), `usage_ledger` (subject, feature, amount, window, idem_key — UNIQUE(subject, feature, idem_key) so a retry never double-counts), `billing_events` (provider event id UNIQUE — webhook idempotency), `entitlement_grants` (admin comps/overrides with expiry and reason).
- **Effective plan**: status active / trialing → paid plan; past_due → paid plan until `grace_until` (3 days); cancelled → paid until period end; else free. Computed on read, never by a cron that can fail.
- **Error contract** for every refusal: HTTP **402** `{error:"<one plain sentence>", code:"plan_limit"|"feature_locked"|"quota_exhausted", feature, limit, used, plan, upgrade_to, resets_at?}`. Client must never be trusted; client mirrors `/billing/me` for UX only.
- **Downgrade**: never delete. Over-limit ARMED alerts beyond the new cap are paused (newest first) with note `[paused: over plan limit]`; layouts/screens stay readable, only new writes are blocked.
- **Payments**: Razorpay Subscriptions adapter (`charto/data/billing.py`): checkout creates a subscription, webhook verified by HMAC-SHA256 over the RAW body (`X-Razorpay-Signature`), idempotent on `x-razorpay-event-id`. No keys in env → endpoints answer 503 "billing is not configured" (honest boundary). Admin grant route for comps behind `CHARTO_ADMIN_EMAILS`.
- **Routes (new)**: `GET /billing/plans` (public), `GET /billing/me`, `POST /billing/checkout`, `POST /billing/verify`, `POST /billing/cancel`, `POST /billing/webhook`, `POST /billing/consume` (internal for pivot), `POST /admin/billing/grant`, `POST /charts/lease` (parallel charts heartbeat).
- verifier: when ENDPOINTS.md is done, flag anything that CONSUMES AI and that I haven't listed here.

## [verifier] 2026-09-28 DONE — Phase 1 A (RESEARCH.md, 201 lines) + B (ENDPOINTS.md)
**A. RESEARCH.md**
- TV actual table (fetched today): charts/tab 1/2/4/8/16 · indicators 2/5/10/25/50 · hist bars 5K/10K/10K/20K/40K (INTRADAY only; daily+ = all history, every plan) · price alerts 3/20/100/400/1000 · technical 20/20/100/400/1000 · watchlist alerts —/—/—/2/15 · parallel conns 2/10/20/50/200. Open-ended alerts = Premium+ only.
- TV definitions: technical = "set for an indicator, drawing, or strategy"; channel + move-% ops ALWAYS technical; **drawing alerts are technical**. Parallel connection = one browser/app TAB (a multi-chart layout is ONE); at the limit TV **evicts the oldest**, it does not refuse.
- Razorpay: HMAC-SHA256 over the raw body with the WEBHOOK secret, at-least-once delivery, dedupe on x-razorpay-event-id, webhook disabled after 24h of failures, pending = T+3 daily retries → halted. RBI 2026 e-mandate: ≤₹15,000 without AFA (every price here qualifies), 24h pre-debit notice. GST SAC 9983 @18%; B2C convention is GST-inclusive display. Stripe India: invite-only since May 2024.
- Metering: ledger + UNIQUE idem key (matches the lead's plan); daily window at 00:00 **IST**; debit before the LLM call, refund when no output; never debit suggestions/titles.
- 15 recommendations at the end. Main one: close the open AI routes before gating (below).

**B. ENDPOINTS.md**
- Auth hook: `_auth_user(headers)` L14235, inline per branch, no middleware. **`/chat` accepts anonymous turns** (`_req.user` may be None, L16854).
- GATE/METER: `POST /alerts` + `POST /alerts/<id>` (patch can null `expires` and change class) · `POST /layouts` save/copy (`len(spec.charts)`, up to 8 panes) · `GET /bars` + `/indicator` (limit ≤20000 + unlimited `to` paging, **no auth**) · `GET /stream` (EventSource, cannot carry a Bearer) · `POST /chat` (METER).
- Alerts have **no kind column**. The price/technical classifier is spec-derived (§4, TV rule). chat `set_alert`→`api_create` and `update_alert`→`api_patch`, so gating inside alerts.py covers both paths.
- **Nothing to gate yet**: watchlist alerts, screen alerts, saved screens (charto has no store; watchlists are localStorage, WL_MAX=12) and custom timeframes (fixed whitelist). "AI summaries" maps to no route; the lead must define it.
- **AI consumers NOT in lead's plan**: `POST /suggest` (anonymous legacy LLM), `POST /audio/transcribe` (paid STT), pivotted `POST /research/chat/stream` (**no auth at all**), pivot `/chat`, `/chat/stream`, `/chat/followups`, `/audio/transcribe`. Also open but not AI: `GET /live` (**unauthenticated admin**: starts/stops venue drivers), `POST /execution/backtest` (anonymous CPU).
- Waiting for "READY FOR VERIFY".

## [lead] 2026-09-28 READY FOR VERIFY — charto side (pivot + pivotted still DOING)
Thanks — RESEARCH/ENDPOINTS changed four decisions (TV alert classes incl. drawings/channel/%-move = technical; intraday-only depth; evict-oldest leases; count→insert lock).
Built (all uncommitted, lead's files):
- `charto/data/plans_catalog.json` · `entitlements.py` (bind/plan_of/value/require_flag/check_limit/check_count/consume/refund/usage/summary/public_catalog/set_subscription/grant/record_event) · `billing.py` (Razorpay: checkout/verify/cancel/webhook, `billing.http` is the injectable wire for tests, `billing.on_downgrade` hook).
- `alerts.py`: `alert_class`, `armed_counts`, `_plan_gate` (multi-condition flag, class cap on ARMED, expiry CLAMP incl. `expires:null`), `enforce_caps(uid)` (pause newest over cap, clamp expiries, never delete), `_PLAN_LOCK` around count→insert and patch. MAX_PER_USER → 2500 box ceiling (plan decides).
- `dataserver.py` routes: `GET /billing/plans` (public) · `GET /billing/me` (anon ok) · `POST /billing/checkout|verify|cancel` (auth) · `POST /billing/webhook` (raw body, HMAC) · `POST /billing/consume` (bearer, or `X-Internal-Key`=$CHARTO_INTERNAL_KEY + email; quota features only; `refund:true`) · `POST /admin/billing/grant` ($CHARTO_ADMIN_EMAILS) · `POST /charts/lease {tab_id, release?, reclaim?}` (evicts oldest → evicted tab gets 402 code `evicted`).
- `/chat`: debit 1 `ai.credits` before the model (idem = body.turn_id or sha(chat_id|len|last msg)), refund on done.error / exception (not on client disconnect); `done.credits = {used,limit,left,resets_at}`. Anonymous = 3/day per hashed IP.
- `/bars`: intraday depth cap via `_bars_for_plan` (reads Bearer if present; `history_limit` in body, `has_more:false`).
- `/layouts` save: `chart.panes` = len(spec.charts), `chart.indicators` = len(spec.workspace.indicators). `open_chart` tool (not replace): `chart.panes` on len(_req.chart_pairs)+1.
- Refusal = HTTP 402 `{error, code: plan_limit|feature_locked|quota_exhausted|evicted, feature, limit, used, plan, upgrade_to, resets_at?}`.
Smoke-tested through the real Handler with a temp CHARTO_USERS_DB (set it! importing dataserver opens charto_users.db otherwise). verifier: Phase 2 is yours — also please cover the webhook (fake RAZORPAY_* env + monkeypatched `billing.http`), signature rejection, event redelivery, pending→grace, cancelled-through-period-end, and downgrade→`enforce_caps`.

## [verifier] 2026-09-28 DONE — Phase 2: `charto/data/test_entitlements_api.py` — 28 pass / 4 FAIL (all 4 are real bugs)
Run: `cd charto/data && ../../pivot/.venv/bin/python -m pytest -q -p no:cacheprovider test_entitlements_api.py` (~18s). The tests drive the real Handler over HTTP against a temp DB per test, with a fake model, fake bars and a fake Razorpay wire. charto_users.db was not touched.
**PASS (28)**:
- `/billing/plans` and `/billing/me` mirror the server's plan.
- TV alert classifier.
- Free price and technical caps give a 402 with the full contract; Pro passes; the two pools are separate.
- A client claiming a plan (body or header) is still refused.
- Pause frees a slot, and re-arming is gated.
- A class-changing edit is gated; an edit within the class at the cap is allowed.
- **12 racing creates for the last slot: exactly 1 wins.**
- Expiry is clamped on create and on patch `expires:null`/+400d; Pro+ stays open-ended.
- The chat `set_alert` path shares the gate.
- Webhook: bad signature → 400, no change; activation → pro; redelivery → `duplicate`, applied once.
- pending → pro through grace → free after it.
- Cancel → pro until period_end → free.
- **halted → 55 alerts kept (0 deleted), 15 newest paused, oldest 20+20 armed, expiries clamped, new create 402, delete works.**
- Free 15 credits: the 16th turn is 402 `quota_exhausted` with `resets_at`, and **the model is not called**.
- Pro has 200 credits.
- The same turn retried 3× is charged once.
- A failed turn is refunded.
- Anonymous users get 3 turns, then 402 with `upgrade_to: free`.
- `/billing/consume` needs identity and refuses non-quota features; replay charges 0.
- `/bars`: Free 1m capped at 10,000 with `has_more:false` and a `history_limit` note; paging past the cut returns []; a bogus bearer gets Free depth; Pro is unlimited; daily is uncapped for everyone.
- `/layouts`: Free 4 panes/5 indicators; Pro 8/10 with `upgrade_to:null` above 8.
- A layout saved over the limit still opens and deletes after a downgrade; a re-save over the cap is 402 and the row stays intact.
- The `open_chart` tool is gated on panes; `replace` is not.
- Leases: 11th tab evicts the oldest; the evicted tab gets 402 `evicted`; reclaim works; release frees a slot; Pro holds 20.
- The admin grant is 403 for non-admins and works for admins.

**FAIL (4) — fix these (lead's files):**
1. **`/chat` turn_id replay = free AI.** `consume()` returns `replay` (charged 0) and the handler then runs the model anyway. A fixed `turn_id` with different questions gave **20 model calls for 1 credit**.
   - Fix: on a replay, don't run the model (return 409 or the cached `done`), OR derive the key server-side from the content hash (e.g. key = turn_id + sha(last message)), so a new question is a new charge.
2. **A user can refund their own credits.** `POST /billing/consume {refund:true}` with the user's own bearer refunded all 15 (ledger 15 → 0).
   - Fix: allow refunds only with `X-Internal-Key`, never with an end-user bearer.
   - Also note: after a refund, a consume with the same key is a replay → free.
3. **Stale webhook downgrades a payer.** A `subscription.pending` (previous cycle, `current_end=now`) arriving after `subscription.charged` (new cycle) overwrote active → past_due. After grace the user is Free despite having paid.
   - Fix: in `_apply`, ignore an entity whose `current_end` is older than the stored `period_end` (or compare the event `created_at`).
4. **`/indicator` bypasses history depth.** It reads `_rows` → `get_bars` with limit ≤20000 and never checks the plan, so `sma(1)` on 1m served **20,000 points to a Free user** (cap 10K).
   - Fix: clamp `limit` to `_ent.value(uid, "chart.history_bars")` for intraday there (and in `_rows` callers such as `read_indicators`/`get_indicator`, if depth matters on the chat path).

**Still not gated / not metered (by design or pending):**
- `POST /suggest` (anonymous legacy LLM, unmetered).
- `POST /audio/transcribe` (paid STT, unmetered).
- pivotted `/research/chat/stream` (no auth; pending per the lead).
- `GET /stream` (parallel-chart leases are client-cooperative: a client that never leases streams freely).
- `GET /live` (unauthenticated admin).
- `POST /execution/backtest` (anonymous).
- `ai.summaries` has no consumer.
- Watchlist and screen alerts, saved screens and custom timeframes are unbuilt, so nothing is enforced.

**Lower-risk notes:**
- `enforce_caps` runs only from the webhook and the grant route. A comp grant that simply EXPIRES, or a past_due grace that lapses with no `halted`, leaves over-limit alerts armed. Create stays gated, so this is only a leak of running alerts.

## [lead] 2026-09-28 READY FOR VERIFY — pivot + pivotted, and the 4 fixes
- Fixes: (1) chat key = sha(turn_id|chat_id|len|last message), computed server-side; (2) `refund:true` without a valid `X-Internal-Key` → 403; (3) `billing._apply` ignores an entity whose `current_end` < stored `period_end` for the same sub id (returns `ignored: stale`); (4) `/indicator` clamps `limit` to intraday depth. Plus an hourly `_plan_sweep_loop` → `enforce_caps` for lapses that arrive as no event.
- **How callers authenticate to /billing/consume:**
  - pivot `backend/billing/meter.py`: a charto session token → forwarded `Authorization: Bearer` **plus** `X-Internal-Key`; a pivot JWT → `X-Internal-Key` + `email` in the body (the pivot User row's email). Refund = same headers + `refund:true`. Charto unreachable / non-200-non-402 → the turn is ALLOWED and logged (`PIVOT_METER_FAIL_CLOSED=1` refuses with 503).
  - pivotted `server.py _meter`: always `X-Internal-Key`; plus the visitor's Bearer when present, otherwise `client` = X-Real-IP → charto meters at the anonymous allowance. Same fail-open policy (`PIVOTTED_METER_FAIL_CLOSED`).
  - pivot routes: `/chat` debits after slash shortcuts (never on a slash), refunds on exception; `/chat/stream` debits only when no slash result, refunds when an `{"type":"error"}` event is emitted; `done` carries `credits`.

## [verifier] 2026-09-28 FINAL — suite 34 pass / 1 FAIL; the 4 charto fixes verified; 2 caller bugs in pivot + pivotted
Run: `cd charto/data && ../../pivot/.venv/bin/python -m pytest -q -p no:cacheprovider test_entitlements_api.py` → **34 passed, 1 failed** (~20s, temp DB per test).

**The 4 fixes all pass**:
- The turn-id replay probe now runs ≤15 model calls.
- The self-refund probe keeps 15 credits.
- A stale pending event no longer downgrades a payer.
- `/indicator` stays ≤10K on 1m.

**New tests, all passing**:
- `test_refund_needs_the_service_key`: a bearer refund → 403; a wrong key → 403; the right key → refunded once only.
- `test_service_debit_by_email_meters_that_account`: pivot's JWT path meters the right account (15, then 402), and an email without the key → 401.

**FAIL / still wrong** (read-only review of the pivot and pivotted diffs):
1. **The replay exploit is back on pivot and pivotted.**
   - Both take the key from the client's `Idempotency-Key` header as is: `meter.turn_key` returns `header_key` verbatim, and pivotted server.py uses `self.headers.get("Idempotency-Key")`.
   - charto answers a replayed key with **200 `{charged:0, replay:true}`**. `meter.debit` and pivotted `_meter` both treat 200 as "allowed" and run the model.
   - So a fixed Idempotency-Key header buys unlimited pivot `/chat` and `/chat/stream` turns and unlimited research turns. It is the same bug charto `/chat` had.
   - Fix: build the key server-side as sha(header_key | conversation | len | last message), as charto `/chat` now does. Or do not run the model when `replay` is true and the content differs.
2. **Unknown identities share ONE anonymous bucket** (test `test_unknown_identities_do_not_share_one_anonymous_bucket` FAILS).
   - The triggers:
     - pivot's JWT path sends the service key plus an email. pivot and charto users are disjoint (DATA_MAP), so for any pivot-only account the email is not found and uid is None.
     - pivotted sends a bearer with NO `client`, so an expired charto token takes the same path.
   - Either way, charto meters `a:hash("")`, so 3 turns a day are shared by all of those users. The test showed a 4th unrelated user refused for other people's turns.
   - Fix (charto `_billing_consume`): if the service names an email that does not exist, answer 404/400 rather than falling through to anonymous. Callers should always send `client` (the visitor IP) so an anonymous fallback is per visitor.
3. **Fail-open on misconfiguration** (a design risk, not a crash). pivot `debit` and pivotted `_meter` allow the turn on any status other than 200 or 402, including 401.
   - If `CHARTO_INTERNAL_KEY` is unset on the box, every pivot-JWT user and every anonymous research visitor is **silently unmetered**. The only trace is a log warning.
   - Recommend a boot-time check (refuse to start, or log at ERROR once) when the key is missing, or treat 401 as fail-closed.
4. Minor issues:
   - pivot non-stream `/chat` refunds only on an exception, not when `_chat_service.handle` returns an error turn.
   - plan.js keeps the tab id in sessionStorage, which the browser's "Duplicate tab" copies, so two tabs share one lease. The lease is client-cooperative anyway, since `/stream` still carries no identity.
   - The plan sweep looks right: hourly, idempotent, pause-not-delete.

**Still ungated** (unchanged from my earlier list):
- `POST /suggest` and `POST /audio/transcribe` are unmetered.
- `GET /live` is unauthenticated admin.
- `POST /execution/backtest` is anonymous.
- `ai.summaries` has no consumer.
- Watchlist and screener alerts, saved screens and custom timeframes are not built.

Verifier stopping here. No servers were touched and nothing was committed.
