# View strategy rebuild — beta fixes (2026-07-03)

Three complaints fixed: (1) ETF-preference collapsing small-ticket baskets
into mostly/only ETFs; (2) backtest methodology not stated and not applied
uniformly; (3) weights that ignored share-price lumpiness. Engine changes in
`backend/view_markets/{candidate_bench.py (new), affordability.py,
precompute.py}`; regenerated `precomputed_views.json`; crude view members
populated from its own thesis (was undeployable). **461/461 view tests pass.**

## 1. What the engine does now

**Selection over a bigger universe (new `candidate_bench.py`).** Per view, a
thesis-aligned bench is built from the NIFTY-500 industry taxonomy plus the
view's scenario winners/beneficiary resolvers (86 candidates for monsoon vs
the 6 held names before). Every candidate is scored by an
**event-conditioned backtest**: its return measured inside each of the
view's own historical occurrence windows — the same windows the headline
uses. Ranking = mean episode return × positive rate × n/(n+4) shrinkage
(small samples pulled toward zero). Candidates with <4 occurrences are
labelled `insufficient_history` and never substitute.

**The weight→shares formulation (`fit_allocation`).**
1. *Substitute*: a name whose share price busts the ticket ceiling is
   replaced by the best affordable event-tested bench candidate; the
   substitute inherits the dropped name's weight slot (stated per swap).
2. *Return-tilt*: `w'ᵢ = wᵢ × clip(1 + 0.5·(rᵢ−r̄)/max|r−r̄|, 0.3, 1.5)`,
   renormalised — event returns tilt the weights, bounded so they never
   dominate.
3. *Integer fit*: largest-remainder share seeding + greedy top-up of the
   most-underweight affordable name.
4. *Budget escalation*: if no faithful fit exists at ₹2,000 the budget
   steps +₹500 up to ₹5,000; `min_entry_inr` is the real spend of the first
   faithful fit — computed, not hard-coded.

**ETF preference can no longer produce all-ETF baskets.** The ETF-core route
is now a *last resort* (only when substitution still can't build a faithful
stock basket), hard-caps the ETF at ≤50% of the ticket, and sizes satellites
weight-proportionally. A pure-ETF ticket only appears when *no* affordable
thesis stock exists, with an explicit note.

**Method is stated.** Every basket entry carries `selection_method`, e.g.
*"Event-conditioned backtest: per-name return measured inside each of the
view's 8 historical occurrence windows, over a thesis-aligned universe of
59 names."* Each leg carries its own `event_mean_pct`. Option tiers remain
modelled (Black–Scholes, stated as "priced at deploy"); pair tiers remain
`margin_required` (honest boundary).

## 2. Backtest method per view

| view | occurrences | method |
|---|---|---|
| IT giants in trouble | 8 weak-guidance prints (2022–2026) | event-conditioned: enter T+1 after print, hold 20 bars |
| Monsoon Kharif | 4 IMD-normal seasons (2010/11/16/21) | event-conditioned: sowing window Jun–Aug |
| Crude de-escalation | **64 Brent −8%/10d triggers (2010–2026)** — new; view previously had NO computed episodes | event-conditioned: enter next bar after trigger, 20-bar hold, non-overlapping |

Crude note: the DB-stored research rationale quotes +24.5% over a filtered
12-episode subset; the live engine now computes **+1.71% average per
occurrence over all 64 unfiltered triggers** (net of entry cost) — the
broader, more conservative number now drives the curve and entry stats. The
thesis's "positive, cost-surviving" claim holds; the honest headline is the
unfiltered one. No view needed the forward expected-return model this round
(all three have real occurrence histories); the model path
(`forward_model.scenario_forward`) remains the stated fallback for
unprecedented events.

## 3. Old vs new — what a small ticket actually buys

| view / tier | before | after |
|---|---|---|
| Monsoon conservative | `etf_core_plus_names` ₹1,883 — **55% ETF** (8× CONSUMBEES + 1 MARICO); BRITANNIA/MRF/HUL dropped | `lite_basket` ₹3,473 — **0% ETF**: 34× JISLJALEQS *(substituted for BRITANNIA, +24.9%/season over 4 seasons)*, MARICO, GODREJCP, APOLLOTYRE |
| IT conservative | ₹1,914, 5 names, worst drift 0.09 | ₹1,822, 5 names, return-tilted, worst drift 0.06 |
| IT aggressive | ₹1,984, 4 names | ₹1,862, 4 names, tighter fit |
| Crude conservative | **null — undeployable** (empty members) | `lite_basket` ₹1,886: PATANJALI *(sub for INDIGO)*, BERGEPAINT, 4× IOC |
| Crude aggressive | **null** | `lite_basket` ₹1,911: PATANJALI, BERGEPAINT, BPCL, 2× IOC |
| balanced tiers (pairs) | `margin_required` | unchanged (short legs need margin — honest) |
| Monsoon aggressive (options) | premium × lot | unchanged (modelled, priced at deploy) |

The monsoon minimum honestly ROSE (₹1,883 → ₹3,473): that is the real cost
of holding a faithful 4-stock basket instead of ETF units — exactly the
trade-off requested ("higher minimum budget to accommodate securities", but
buying stocks, not more NIFTYBEES-class units).

## 4. Verification

- Regenerated via `python -m backend.view_markets.precompute`: 9/9
  expressions computed real episode-gated curves (was 6/9 — crude was empty).
- Live API inspected for all three views: every basket entry is
  `lite_basket` with real stocks; zero ETF-only entries; substitutions and
  drops stated; `selection_method` present on every basket entry.
- 461/461 backend view tests pass (one test updated in spirit:
  `core_satellite` keeps accepting a single satellite — one real share still
  beats a pure-ETF ticket under the never-all-ETF rule).
- Crude members persisted to DB (was `members_long: []` on all three tiers;
  now the thesis's own importer basket, with a dated `members_note`).

## 5. Known follow-ups

- A substitute taken at partition time can still be priced out at the final
  (smaller) escalated budget (SCHAEFFLER for MRF at ₹3,500) — a second
  substitution pass would recover one more name; the basket remains faithful
  without it.
- FE does not yet render `substitutions` / `selection_method` /
  `event_mean_pct` as structured UI (the entry `note` text covers it);
  candidates for the Views detail page.
- The crude research rationale text (24.5%/12 episodes) vs the live
  64-trigger headline should be reconciled in the DB copy at the next
  curation pass.

## 6. Card-copy reconciliation (round 2, same day)

The computed stats changed but persisted card PROSE still quoted old
numbers — worse, the /view-pack `historical_strength` was a template
artifact: every one of the 8 views shipped the IDENTICAL monsoon-derived
"4 episodes. Strategy 45.55% vs NIFTY … beats NIFTY in 100.0%" line
(fabrication by copy-paste, plus re-introduced benchmark-beating framing).

Fixed both surfaces:
- **DB views**: all 9 expressions' `historical_strength` regenerated from
  their live computed per-occurrence stats ("64 past occurrences. Average
  +1.71% per occurrence … positive in 41 of 64"), with the deploy-time
  research battery (PSR/DSR/MinTRL) kept as dated provenance; crude's
  `risk_profile` now marks its beta/maxDD as "the filtered research subset
  (12 episodes; card stats use all 64 unfiltered triggers)".
- **/view-pack**: `_strength_text()` in the enricher derives the line per
  expression — rolling-window views state average per WINDOW + positive
  count ("windows are calendar slices, not distinct events"); the shock
  view claims NO track record (forward model only); option tiers state the
  modelled-payoff basis. No benchmark-beating framing anywhere.
