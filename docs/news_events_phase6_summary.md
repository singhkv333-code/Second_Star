# News & Event Trigger — Phase 6 summary

**Status:** complete. Ready for human review before Phase 7
(optional transport upgrade per Tier-B research) or before
production rollout planning.
**Date:** 2026-05-21.
**Scope:** Tier-3 hardening — Polymarket Gamma cross-check
integrated into the aggregator, plus a retraction-window watcher
that cancels pending workflow approvals when a RETRACTION verdict
lands inside the spec's safety window.

Kalshi deferred (RSA-PSS auth — bigger effort, not on the critical
path; can land as a follow-up adapter behind the same shape as the
Polymarket client).

---

## What was built

- **Migration `0010_news_retraction_tracking`** — additive ALTERs
  on our own ``news_fired_events`` table:
  - ``retraction_detected_at`` `TIMESTAMPTZ NULL`
  - ``retraction_classification_id`` `String(36) NULL` (soft FK to
    the triggering classification)
  - ``retraction_action_taken`` `VARCHAR(48) NULL`
    (one of ``cancel_pending_approvals`` /
    ``cancel_and_alert`` / ``ignore`` /
    ``no_pending_approvals`` / ``workflow_run_missing`` /
    ``window_expired``)
  - Covering index ``ix_news_fired_events_retraction_watch`` on
    ``(retraction_status, retraction_window_ends_at)`` for the
    watcher's hot query.

- **`backend/news_events/sources/polymarket.py`** — Polymarket
  Gamma API client. Free, no auth. Two surfaces:
  ``search_markets(query)`` and ``get_market(id_or_slug)``.
  Tolerant parser handles both the ``outcomes`` / ``outcomePrices``
  parallel-arrays shape and the ``tokens`` array shape Gamma
  returns for some markets. Never raises — returns ``None`` on any
  failure path so the aggregator keeps moving.

- **`backend/news_events/pipeline/prediction_market.py`** —
  ``evaluate_prediction_market_signal(db, spec)``:
  - Returns ``None`` immediately if the spec has no
    ``prediction_market_threshold`` (Tier 1/2 or Tier 3 without the
    market option chosen).
  - First call for a spec: searches Polymarket for the spec
    description, picks the highest-relevance open market, and
    caches its id on
    ``resolution_criteria.polymarket_market_id``. Subsequent calls
    bypass the search.
  - Fetches the current snapshot, returns ``True`` if YES price ≥
    threshold, ``False`` if below, ``None`` if the fetch failed.

- **`backend/news_events/pipeline/aggregate.py`** — extended.
  ``evaluate_firing`` gains an optional
  ``prediction_market_signal`` kwarg. When ``True``, a synthetic
  ``_ClassRow(source_id='prediction_market', verdict='YES')`` is
  spliced into the candidate set and the existing per-tier
  partition logic re-uses it as one secondary YES. When ``False``
  with ``conflict_policy='hold'``, it counts as a conflict and the
  spec holds. The synthetic row's marker id (``__pm__``) is
  filtered out at the boundary so audit rows only carry real
  classification UUIDs.

- **`backend/news_events/pipeline/retraction.py`** — the watcher
  logic:
  - ``scan_for_retractions()`` scans
    ``news_fired_events WHERE retraction_status='none' AND retraction_window_ends_at IS NOT NULL``.
  - Expired windows are flipped to ``handled`` with action
    ``window_expired`` — they exit the scan set on subsequent
    ticks.
  - Open windows are checked for a fresh
    ``classifier_verdict='RETRACTION'`` row created after
    ``fired_at``. When found, ``handle_one_retraction`` runs the
    spec's ``retraction_policy.action`` and records the audit
    columns.
  - Approval cancellation marks every open
    ``WorkflowApproval`` for the linked ``workflow_run_id`` as
    ``rejected`` and appends a structured "why" suffix to
    ``summary`` so the user sees the explanation in the
    existing approval UI.
  - ``cancel_and_alert`` adds a WARNING-level structured log line
    on top of the cancellation.

- **`backend/news_events/workers/retraction_watcher.py`** — one
  more APScheduler job attached to the existing scheduler at 60s
  cadence, registered in main.py alongside the poller and the
  Stage-3-6 funnel.

- **`backend/news_events/pipeline/funnel.py`** — wired
  prediction-market consultation BEFORE the aggregator call. The
  snapshot dict is passed through to ``fire_spec`` so it lands on
  ``news_fired_events.prediction_market_snapshot`` for the audit
  pane.

- **`backend/news_events/router.py`** — the ``/fired/{id}`` audit
  endpoint now surfaces the four new Phase-6 fields
  (``retraction_detected_at``, ``retraction_classification_id``,
  ``retraction_action_taken``, ``prediction_market_snapshot``).

- **Tests** — 22 new (130 total):
  - ``test_polymarket.py`` (8) — mocked httpx: search happy path,
    5xx → empty, blank query short-circuit, wrapped-data payload,
    get_market 404, tokens-array fallback shape,
    unparseable JSON, price clamping into [0, 1].
  - ``test_aggregate_pm.py`` (5) — PM=YES counts as secondary YES,
    PM=NO becomes a conflict, PM=None mirrors Phase-5 behaviour,
    PM=YES pushes a 2-real-source spec over a 3-required threshold,
    synthetic marker ``__pm__`` never leaks into supporting ids.
  - ``test_retraction.py`` (8) — handler: cancel_pending_approvals,
    cancel_and_alert log emission, ignore is a no-op,
    no_pending_approvals branch, workflow_run_missing branch;
    scan: open-window-with-retraction handles, expired-window
    flips to ``handled``, no-retraction-yet leaves the row alone.
  - 130/130 news_events tests pass; existing suite at the same 25
    pre-existing failures, no regressions.

## What changed in existing code

**Nothing** outside the two flag-gated edits already in place:
``main.py`` gains one more import + one more registration call,
all inside the existing ``if settings.news_events_enabled:``
block.

The Touch-1 seam from Phase 5 in ``backend/workflows/scheduler.py``
remains unchanged. No other pre-news_events file is modified by
Phase 6.

## Verified live (Postgres, prod-like env)

1. **Migration `0009 → 0010`** applied cleanly. Backend restarted
   with the flag on; structured log confirms three workers
   registered:
   ```
   [news_events.poller] registered 5 source jobs
   [news_events.funnel] registered drain job (60s)
   [news_events.retraction] registered watcher job (60s)
   ```

2. **Polymarket Gamma API** — live ``search_markets("Trump 2028
   election")`` returned three open markets with parsed snapshots
   (yes_price 0.485-0.545). Client serialises the response
   correctly; relevance ranking is a Polymarket-side quirk we'll
   tune with better search params or LLM-based matching in a
   follow-up.

3. **Retraction watcher end-to-end** — seeded a workflow + run +
   open approval, then a fired event with ``fired_at`` 60s in the
   past + a fresh ``RETRACTION`` classification:
   ```
   scan_for_retractions:
     candidates_seen      = 2
     retractions_detected = 1
     approvals_cancelled  = 1
     alerts_emitted       = 1
     actions              = {'cancel_and_alert': 1}

   fired.retraction_status            = 'handled'
   fired.retraction_action_taken      = 'cancel_and_alert'
   fired.retraction_classification_id = 755a35da-...
   approval.decision                  = 'rejected'
   approval.summary suffix:           '[news_events retraction] Event retraction
                                       detected at 2026-05-21T04:24:55Z
                                       (classification 755a35da-...).'
   ```
   The second candidate was the Phase-5 demo's stale fired event
   (window 60min, long expired) which was correctly flipped to
   ``handled``/``window_expired`` and exited the scan set.

4. **Audit endpoint** — ``GET /api/news-events/fired/{id}``
   returns the full Phase-6 shape including
   ``retraction_status='handled'``,
   ``retraction_action_taken='cancel_and_alert'``,
   ``retraction_classification_id``, and (when applicable)
   ``prediction_market_snapshot``.

## Isolation audit

- [x] All Phase-6 code under ``backend/news_events/`` and
      ``tests/news_events/``.
- [x] Migration `0010` ALTERs only ``news_fired_events``, a
      news_events-owned Phase-1 table.
- [x] No new FK constraint into any pre-news_events table.
- [x] No new third-party dependency. Polymarket client uses the
      existing ``httpx``.
- [x] Polymarket API calls are anonymous, identified via Pivot's
      news_events User-Agent, and never raise — graceful
      degradation throughout.
- [x] Retraction watcher and prediction-market evaluator both
      operate on their own session scope; neither crashes the
      funnel if anything fails.
- [x] Approval cancellation reuses the existing
      ``WorkflowApproval.decision='rejected'`` path the engine
      already handles. No new approval-decision surface.
- [x] Synthetic PM marker (``__pm__``) is filtered out of audit
      ids so audit joins never lookup a fake classification UUID.
- [x] Feature flag still gates the whole subsystem.

## Surprises and decisions worth flagging

1. **Polymarket's `search` parameter has weak relevance.** A
   "Trump 2028 election" search returned non-political markets
   (Rihanna album, Playboi Carti, etc.) — Polymarket appears to
   fall back to popular open markets when no strong match exists.
   For v1 we accept whatever the API returns; the cached
   ``polymarket_market_id`` can be hand-overridden via a future
   spec PATCH endpoint, or the disambiguation flow could prompt
   the user to confirm a market URL when they choose
   ``prediction_market_resolution``.

2. **Postgres `now()` is transaction-start, not statement-time.**
   The live retraction demo initially failed because a single
   transaction creating the fired event then the retraction
   classification gave both rows the same ``now()`` — making
   ``classification.created_at > fired_at`` false when fired_at
   was captured by Python's wall clock AFTER the transaction had
   begun. Production doesn't hit this (the classification commits
   in a Stage-6 tick well before the next funnel tick fires).
   Test fix: backdate ``fired_at`` by a clear margin or commit
   the fired-event row before opening the retraction-create
   transaction. Documented in the live-demo script so future
   reproductions don't trip.

3. **The watcher's expired-window cleanup is automatic.** Older
   ``news_fired_events`` rows with ``retraction_status='none'``
   whose window has passed get flipped to ``handled`` /
   ``window_expired`` on the next scan tick — they drop out of
   the scan set without needing a separate retention job.

4. **PM signal contribution to the aggregator is intentionally
   conservative.** A ``True`` PM signal counts as exactly ONE
   secondary YES — never as a primary, never as more than one.
   This means a Tier-3 spec that requires
   ``min_secondary_confirmations=2`` and has no real news YES
   still won't fire just because the prediction market is bullish.
   The market is corroborative evidence, not the lone signal.

5. **Kalshi deferred.** RSA-PSS auth is a meaningful integration
   effort and the Phase-0 plan flagged it as optional. We'll
   ship a Kalshi adapter behind the same shape as
   ``polymarket.py`` (search + get_market + snapshot dataclass)
   if/when the product calls for a second cross-check source.

## Open questions still outstanding

- (Q1) Touch 1 — DONE (Phase 5).
- (Q4) Embedding provider — DONE (Phase 3).
- (Q5) n8n proxy / Tier-B push transport — Phase 7 (research
  complete, documented in
  ``docs/news_events_tier_b_explainer.html``).
- (Q6) NSE / BSE filings — deferred; the Tier-B research surfaced
  the changedetection.io+Playwright path as the implementation
  route.
- (Q7) Kalshi — explicitly deferred from Phase 6.
- (Q8) Approvals UI — handled implicitly: the existing
  ``WorkflowApproval`` rejection path the engine already honours
  means the retraction simply marks an approval rejected, with
  an explanatory suffix on the summary. The FE displays whatever
  the existing approval-inbox component does.
- (Q9) Prod-egress verification of BS / ET / Mint / SEBI —
  outstanding (Phase 7's changedetection.io path makes this less
  critical because it tolerates failed direct fetches).
- (Q10) Retention policy — outstanding.

## Recommended next step

Two viable paths:

- **Phase 7 (transport upgrade)** — implement the Tier-B stack
  documented in
  ``docs/news_events_tier_b_explainer.html``:
  Miniflux + RSSHub + changedetection.io. Drops end-to-end
  publisher-to-funnel latency from "up to 5 min" to "~10 s" and
  finally closes the SEBI / NSE-filings gap.

- **Production rollout planning** — the core subsystem is now
  functionally complete. Phases 1-6 give us:
  source ingestion → dedup → keyword filter → body fetch →
  embedding similarity → LLM excerpt → LLM classification →
  per-tier aggregation → workflow handoff with approval gating →
  retraction handling → audit trail. Phase 7 improves latency
  and source coverage; it's not a correctness blocker for
  shipping the first internal alpha.

Pick whichever fits the product timeline.
