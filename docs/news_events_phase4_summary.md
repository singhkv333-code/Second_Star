# News & Event Trigger — Phase 4 summary

**Status:** complete. Ready for human review before Phase 5.
**Date:** 2026-05-21.
**Scope:** natural-language → EventSpec parser, Tier-3 disambiguation
flow, spec lifecycle state machine, user-facing CRUD endpoints. The
funnel from Phase 2 already picks up `state='active'` specs, so a
Phase-4 activated spec is immediately visible to Stage 2.

---

## What was built

- **`backend/news_events/parsing/event_spec_parser.py`** — NL →
  `ParsedSpec`. Mirrors `backend/workflows/propose.py`:
  JSON-mode LLM call, `reasoning_effort='minimal'`,
  `prompt_cache_key='news_events.parser.v1'`. Validation against
  the existing `KeywordSet` / `ResolutionCriteria` /
  `RetractionPolicy` Pydantic models. Retry-once-on-validation
  with the concrete error fed back to the model. Tier-3 is
  always forced into the disambiguation branch regardless of what
  the LLM says.

- **`backend/news_events/parsing/disambiguation.py`** — templated
  Tier-3 questions (2 questions, no LLM call). Each option carries
  an `apply` payload that the answer-recorder merges into the
  pending spec. `apply_option` + `apply_answers` are pure
  functions, easy to test, easy to extend with a third question
  later.

- **`backend/news_events/specs.py`** — spec lifecycle helpers.
  `create_spec_from_parsed`, `record_answer`,
  `activate_spec`, `cancel_spec`, plus read helpers. Enforces:
  - tier1/2 → state = `draft` (no disambiguation session created)
  - tier3 → state = `pending_disambiguation` + companion
    `NewsDisambiguationSession` row
  - draft → active (only via `activate_spec`)
  - any non-terminal → cancelled (idempotent on terminal states)
  - cross-user access raises `SpecError(status=404)`
  - expired disambiguation session raises `SpecError(status=410)`

- **`backend/news_events/router.py`** — six new endpoints:
  - `POST /api/news-events/specs` — parse NL → spec OR session
  - `GET  /api/news-events/specs` — list user's specs
  - `GET  /api/news-events/specs/{id}` — one spec
  - `GET  /api/news-events/specs/{id}/disambiguation` — session view
  - `POST /api/news-events/specs/{id}/disambiguate` — record answer
  - `POST /api/news-events/specs/{id}/activate` — draft → active
  - `POST /api/news-events/specs/{id}/cancel` — anything → cancelled
  - All JWT-authed via `routers/_deps.require_user`.
  - All return either `EventSpecResponse` or a `CreateSpecResponse`
    envelope that carries exactly one of `spec` / `disambiguation`.

- **`backend/news_events/schemas.py`** — six new DTOs:
  `DraftSpecRequest`, `EventSpecResponse`, `CreateSpecResponse`,
  `ListSpecsResponse`, `DisambiguationSessionView`,
  `DisambiguationQuestion`, `DisambiguationOption`,
  `DisambiguationAnswer`.

- **Tests** — 26 new (84 total under `tests/news_events/`):
  - `test_event_spec_parser.py` (6) — happy path, Tier-3 force,
    retry-on-validation, fenced JSON, garbage handling,
    short-input rejection. LLM mocked.
  - `test_disambiguation.py` (7) — question shapes, option
    application, deep-merge semantics, unknown-option rejection.
  - `test_specs_state_machine.py` (7) — transitions and guards,
    cross-user 404, idempotent cancel, full disambiguation flow.
  - `test_router_specs.py` (6) — HTTP-surface tests covering both
    tier paths, cross-user isolation, parser-error → 422.
  - **84/84 news_events tests pass.** Existing suite unchanged
    (same 25 pre-existing failures on `prototype`).

## What changed in existing code

**Nothing.** Phase 4 lives entirely under `backend/news_events/`
and `tests/news_events/`. No new migrations (Phase 1's
`news_event_specs` and `news_disambiguation_sessions` tables are
exactly the shape Phase 4 needed). No new dependencies. No touch
on workflows, broker, chat, intent parser, or orders code.

## Verified live (Postgres, prod-like env, real Azure LLM)

Backend restarted with the flag on. Authenticated request flow:

**Tier 1** — `"If the RBI cuts the repo rate, buy a PSU bank ETF"`:

```
POST /api/news-events/specs
  → 200, spec.state=draft, tier=tier1
     description: "RBI announces a cut in the repo rate."
     primary_sources: ["rbi_press_releases", "rbi_notifications"]
     keyword_set.must_have_one:    ["RBI", "repo rate", "rate cut",
                                     "monetary policy"]
     keyword_set.must_have_one_of: [["cuts", "cut", "reduce",
                                     "reduction"]]
     conflict_policy: "fire"
     retraction_policy: {action: "cancel_and_alert", window: 60min}

POST /api/news-events/specs/{id}/activate
  → 200, state=active
```

**Tier 3** — `"If Trump wins the 2028 US election, buy defense
and sell renewables"`:

```
POST /api/news-events/specs
  → 200, spec=null,
     disambiguation.questions = [exact_event, retraction_policy]

POST .../specs/{id}/disambiguate {q=exact_event,
                                  opt=multi_source_consensus}
  → 200, still in pending_disambiguation, 2 questions visible

POST .../specs/{id}/disambiguate {q=retraction_policy,
                                  opt=cancel_and_alert}
  → 200, spec.state=draft
     description: "Donald Trump wins the 2028 United States
                   presidential election — fires only on multi-source
                   consensus."
     min_secondary_confirmations: 1
     retraction_policy.action: "cancel_and_alert"

POST .../specs/{id}/activate
  → 200, state=active

GET .../specs
  → [{state="active", tier="tier3", description="Donald Trump
       wins the 2028 ... — fires only on multi-source consensus."}]
```

The activated spec is immediately picked up by the Stage-2
keyword evaluator (already wired in Phase 2) and the Stage-3-6
funnel worker (already wired in Phase 3) — no additional plumbing
needed for them to start operating on the new spec.

## Isolation audit

- [x] All new code under `backend/news_events/` and
      `tests/news_events/`.
- [x] No migration in Phase 4 — the Phase-1 tables already had
      the right shape.
- [x] No new FK constraints into existing tables.
- [x] No new third-party dependency.
- [x] LLM client reused via `get_llm_client()` factory — the
      parser routes through whatever provider is configured
      (Azure for this account, OpenAI for others).
- [x] Disambiguation generation is templated (zero LLM cost) —
      the LLM is only called for the initial parsing pass.
- [x] Cross-user access: every endpoint checks
      `spec.user_id == request_user_id` and returns 404 (not 403)
      on miss, mirroring the existing workflows-router behaviour.
- [x] Disambiguation sessions auto-expire after 30 min (TTL
      enforced on read; expired session → 410).
- [x] All state transitions are idempotent on the destination
      state (re-activate of active is a no-op; re-cancel of
      cancelled is a no-op).
- [x] Feature flag still gates the whole subsystem at the main.py
      level.

## Surprises and decisions worth flagging

1. **Templated questions instead of LLM-generated.** The Phase-0
   plan was open on this. Templated wins: zero LLM cost on
   disambiguation, deterministic phrasing, easy to test, easy to
   extend. The trade-off is a fixed question set per tier — fine
   for v1 because the Tier-3 dimensions (exact-event, retraction)
   are well-known.
2. **Azure parser output is excellent.** The live Tier-1 call
   returned an unusually rich keyword set (4 `must_have_one` terms
   plus a 4-term `must_have_one_of` group). That richness costs
   nothing at Stage 2 (still pure CPU) and helps recall on the
   RBI feed. We may want to bound the keyword count in the prompt
   if specs start ballooning.
3. **Soft FK navigation between spec and session.** I store the
   spec's id inside the session's `pending_event_spec._spec_id`
   payload (a JSON key prefixed with `_`) rather than adding a
   schema column. Keeps the migration story untouched and works
   on both Postgres and SQLite. The state machine reads
   `_spec_id` to find the matching session.
4. **`/specs/{id}/disambiguation` is a GET, not POST.** Lets the FE
   re-fetch the current state of an in-progress session (e.g. on
   page reload) without retrying a POST. The POST variant is
   `/disambiguate` (singular) which writes an answer.
5. **No PATCH endpoint for editing a draft.** Phase 4 ships
   create / activate / cancel only. If a user wants to refine the
   keyword_set or change the workflow_id, they cancel and
   re-create. Editing is a Phase-5+ nicety if the friction
   becomes real.

## Open questions still outstanding

- (Q1) Touch 1 wrapper — still deferred to Phase 5 (when firing
  actually runs a workflow).
- (Q4) Embedding provider — resolved in Phase 3 (OpenAI
  `text-embedding-3-small`).
- (Q5) n8n proxy — Phase 7.
- (Q6) NSE / BSE filings — deferred.
- (Q7) Kalshi — Phase 6.
- (Q8) Approvals UI — Phase 5.
- (Q9) Prod-egress verification of BS / ET / Mint / SEBI —
  outstanding.
- (Q10) Retention policy — outstanding.

## Recommended next step

Move to **Phase 5 — aggregation + firing + confirmation**:

- Confidence aggregator (`pipeline/aggregate.py`) implementing the
  per-tier rules from the spec.
- Stage 8 order construction (`pipeline/propose.py`).
- **Touch 1**: add the public `fire_external_event(workflow_id,
  triggered_step_index, fired_at, audit_context)` wrapper next to
  `_fire_watch_run` in `backend/workflows/scheduler.py` (~15 LOC,
  approved in the Phase-0 plan).
- Audit log writes to `news_fired_events`.
- The existing `workflow_approvals` machinery covers the mandatory
  user-confirmation step — no new endpoint needed.

Phase 6 then layers on Tier-3 hardening (Polymarket cross-check,
retraction detection, the safety-window watcher).
