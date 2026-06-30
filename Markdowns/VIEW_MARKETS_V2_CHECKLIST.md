# View Markets (Pivot V2) — Build Checklist

> The execution plan to ship **View Markets**, the belief-first investing layer
> (**Belief → Expression → Deployment**). Spec: `Markdowns/Version2.md`.
> Philosophy & architecture context: root `/CLAUDE.md`. Strategy/testing/taxonomy
> research: `VIEW_MARKETS_STRATEGY_DESIGN.md`, `VIEW_MARKETS_TESTING_AND_SCORING.md`,
> `VIEW_MARKETS_VIEW_TAXONOMY.md`. Scope contract: `VIEW_MARKETS_PLAN.md`.
>
> **Each item is tagged:** `[REUSE]` (wire up something that already exists),
> `[EXTEND]` (add to an existing module), `[NEW]` (net-new code). Paths are the
> concrete file/module to touch. Latest migration today is **`0022`**, so the
> View Markets migration is **`0023`**.
>
> **Guardrails that must survive V2 (do not regress):** register-not-execute;
> never fabricate; honest boundaries; not an advisor; V1 = **curated views
> only** (no user-authored beliefs, no prediction-exchange/binary contracts);
> calm/visual UX. Ship behind a `view_markets_enabled` flag (default OFF) until
> ready.

---

## STATUS (2026-06-29)

- ✅ **Phase 4 COMPLETE (code)** (ultracode, 2026-06-29) — `backend/view_markets/deployment/`
  (`backtest.py` routes each expression by kind → real engine + Trust Battery, attaches
  verdict/Alignment, honest `insufficient_data` when data missing; `compare.py` ranks the
  3 tiers; `deploy.py` builds an ARMED register-not-execute workflow draft, `requires_approval`
  order steps, links `workflow_id`). e2e test has a trip-wire that fails if any order is placed.
  **401 view_markets tests pass**, ruff clean, broader smoke clean. *(The workflow's final
  verify stage hit a session limit; the validation was completed by hand — all green.)*
- ✅ **Commodity Phase 3 pass COMPLETE** — 6 commodity archetypes (CM1–CM6) in `catalog.py`,
  `commodities.py` (MCX universe + honest backtest-availability gate), commodity instrument
  types in `config_schema.py`, and — the key fix — `honest_short.py` now returns a **tradeable
  `commodity_future`/`commodity_put` short** for MCX (NEVER AVOID, since commodities ARE
  shortable via futures). Commodity legs added to option/pair/basket/multi_asset builders;
  direct-MCX pair/basket **honestly degrades to construct-only** when MCX price history is
  unavailable (ETF-proxy route backtests). Resolves follow-up (b) below.
- 🛒 **COMMODITIES NOW TRADEABLE (2026-06-29):** MCX (crude/gold/silver/metals/natgas)
  moved from research-only → tradeable via register-not-execute. Code guards lifted
  (option_chain/safety/option_strategies/paper-routing/instrument-master/workflow-action),
  6 tests flipped to assert the new behavior, MCX paper-execution verified working.
  **REMAINING FOLLOW-UP:** (a) verify live broker MCX order-routing end-to-end.
- ✅ **Phase 0 COMPLETE** — `VIEW_MARKETS_PLAN.md` (scope contract + curation model
  + "read-not-become a prediction market" principle); `view_markets_enabled`
  flag (default OFF) in `config.py`.
- ✅ **Phase 1 COMPLETE (code)** — 6 tables in `models.py` (`MarketView`,
  `ViewExpression`, `ViewTransmission`, `ViewConfidence`, `ViewExpectation`,
  `ViewFollow`), migration `0023_view_markets.py` (chained off 0022), Pydantic
  schemas, SQLite-parity verified, ruff clean, no new mypy errors, 28 model
  tests green. Adversarially verified (PASS).
- ✅ **Migration 0023 APPLIED to Azure Postgres** (`pivot-db-india`, Central India)
  on 2026-06-29 — `alembic current` = `0023_view_markets (head)`; all 6 tables +
  6 enum types (`view_type`, `view_status`, `expression_tier`, `expression_kind`,
  `confidence_dimension`, `expectation_source`) verified live on the server.
- 🔬 **Research complete** — strategy-design, testing-&-scoring, and view-taxonomy
  docs written (see header). **⚠️ ACTION-NEEDED finding:** the taxonomy research
  reports (needs independent verification) that India (MeitY/PROGA 2025) has moved
  to **block Polymarket and restrict Kalshi** as of mid-2026 — which would force
  demoting the existing `trigger.polymarket`/`trigger.kalshi` + PM odds to a
  **hidden internal prior** and surfacing Pivot's own **option-implied**
  probabilities instead. This affects the already-shipped event-triggers beta,
  not just V2. **Verify before acting.**
- ✅ **Phase 2 COMPLETE (code)** (ultracode, 2026-06-29) — `backend/view_markets/`
  package, 8 modules (transmission, implied_move, feeds, event_study, expectations,
  confidence, curation, lifecycle) + seed example + flag-gated lifecycle worker
  wired in `main.py` startup. **114 tests pass**, ruff clean, imports clean, no new
  migration (reuses Phase-1 tables). Generators NOT built (manual curation in beta).
  Not committed/pushed/applied to Azure.
- 🧭 **BETA DECISION:** **views are MANUALLY CURATED by the user** for now — the
  automatic event/relative/theme **view-generators are DEFERRED** (not built in
  Phase 2). Phase 2 ships the enrichment/scoring/lifecycle that operates on
  human-authored views.
- Nothing committed or pushed.

---

## Phase 0 — Foundations & decisions (do first)

- [ ] **Lock the V1 scope contract.** Curated views only; the three view types
      (Event / Relative / Theme); Conservative/Balanced/Aggressive expression
      tiers; Pre-position/Confirmation/Hybrid timing; two confidence dimensions
      (outcome vs expression); lifecycle Open→Developing→Consensus→Resolved→
      Archived. Write it as `docs/VIEW_MARKETS_PLAN.md`. `[NEW]`
- [ ] **Decide curation model.** V1 views are *backend-generated + human-
      curated*, not user-typed. Decide: who authors the seed set, how a view is
      validated before publish, and the review gate. `[NEW]`
- [ ] **Add the feature flag** `view_markets_enabled` (default OFF) +
      per-surface sub-flags. `pivot/backend/config.py`. `[EXTEND]`
- [ ] **Confirm "read prediction markets, don't become one."** Polymarket/
      Kalshi may *inform* "what's priced in"; View Markets renders consensus,
      never an outcome-trading surface. Encode this in the plan doc. `[REUSE]`

---

## Phase 1 — Data model (migration 0023)

> No view/belief/theme/transmission tables exist today. `models.py` has
> `Workflow`, `Strategy`, `ForwardIdea`, `OptionStrategy`, paper tables, and the
> news-events tables — none model a "view."

- [ ] **`market_views`** — `id, user_id?(null=curated), view_type
      (event|relative|theme), title, thesis, category, time_horizon, status
      (open|developing|consensus|resolved|archived), resolution_date?,
      created_at, updated_at, published_at`. `pivot/backend/models.py`. `[NEW]`
- [ ] **`view_expressions`** — `id, view_id, tier (conservative|balanced|
      aggressive), expression_kind (basket|option_strategy|pair|multi_asset|
      hedge), config(JSONB), rationale, risk_profile, capital_intensity,
      historical_strength, time_horizon, backtest_run_id?, workflow_id?
      (when deployed)`. `[NEW]`
- [ ] **`view_transmission`** — the cause→effect DAG: `id, view_id, from_node,
      to_node, edge_label, strength, evidence`. (Renders the transmission map;
      see Phase 2.) `[NEW]`
- [ ] **`view_confidence`** — `id, view_id, dimension (outcome|expression),
      score, evidence, updated_at`. `[NEW]`
- [ ] **`view_expectations`** — surprise framing: `id, view_id, source
      (polymarket|kalshi|consensus|model), market_id?, expected_value,
      user_view_value, surprise_sign, as_of, resolved_value?`. `[NEW]`
- [ ] **`view_follows`** — user follows a view for lifecycle updates: `id,
      user_id, view_id, created_at`. `[NEW]`
- [ ] **Write `0023_view_markets.py`**, apply to Azure Postgres, add SQLite
      `create_all` parity for tests (watch the uuid-FK / soft-ref gotchas noted
      in the paper-trading work). `pivot/migrations/versions/`. `[NEW]`
- [ ] **Pydantic schemas** for every table (request/response). Mirror enums as
      `Literal[...]`. `pivot/backend/schemas.py` (or a `view_markets/schemas.py`).
      `[NEW]`

---

## Phase 2 — View generation & evidence pipeline (backend)

> Turn the prose `thematic_map.py` scenarios + macro/event/prediction-market
> infra into structured, evidence-backed, machine-readable views.

- [ ] **Transmission DAG from `thematic_map`.** Promote the six scenarios'
      `thesis` (prose) → structured `from_node → edge → to_node` chains with
      `winners/losers` as leaf beneficiaries. Keep `confirm`/`invalidate` as
      evidence. `services/thematic_map.py` → new
      `view_markets/transmission.py`. `[EXTEND]`
- [⏸] **DEFERRED (beta decision 2026-06-29) — automatic Event / Relative / Theme
      view-generators are NOT built.** Views are **manually curated** by the user
      via `curation.py` for now. The generators (auto-creating views from
      macro_events / correlation+pairs / thematic_map) are a post-beta upgrade; the
      reuse seams (`macro_events` calendar+verifier, `compare_performance`, pairs,
      `thematic_map`+`sector_universe`) remain ready for them. Tracked, not built.
- [ ] **Market-expectations / surprise aggregator** `[NEW]` — the missing piece:
      for a view, fetch "what's priced in" from Polymarket/Kalshi
      (`news_events/sources/polymarket.py`, `kalshi.py`,
      `pipeline/prediction_market.py`) and/or consensus, store the
      Expected/User-View/Difference into `view_expectations`. `view_markets/
      expectations.py`. `[REUSE]` sources, `[NEW]` aggregator.
- [ ] **Confidence scorer** `[NEW]` — compute the two dimensions: *outcome
      confidence* (event likelihood from prediction-market odds / calendar
      certainty) and *expression confidence* (historical relationship strength
      from correlation/backtest). Write to `view_confidence`. `view_markets/
      confidence.py`.
- [ ] **Evidence attachment** — each view stores the data it was generated from
      (historical returns, correlations, event-study refs) so "every view has
      evidence." `[NEW]`
- [ ] **Lifecycle worker** — advance view `status` as resolution dates approach
      / consensus shifts / the event resolves (hook into the verifier). Reuse
      the APScheduler pattern (module-level jobs — closure gotcha!).
      `view_markets/lifecycle.py` + register in scheduler. `[NEW]` + `[REUSE]`.

---

## Phase 3 — Expression engine (view → tiered, deployable strategies) — **LOCKED**

> Turn a curated view into **proper, effective** Conservative/Balanced/Aggressive
> strategies — explicitly **NOT "always a simple basket"** (see
> `VIEW_MARKETS_STRATEGY_DESIGN.md`). Phase 2 already built the *enrichment/scoring*
> side (implied-move, event-study CAR, two-dial confidence + Alignment Score,
> expectations, transmission, curation, lifecycle) — Phase 3 produces the
> **expressions** those score.
>
> **ARCHITECTURE (locked — matches the repo's own pattern: `option_strategies.py`
> `TEMPLATES` dict, `weighting.py` scheme dispatch, the category-grouped step
> catalog). NOT one `.py` per strategy.** A strategy's "difference" lives in a
> **declarative catalog entry** (kind + template/scheme/params + tier knobs) and
> its **per-`expression_kind` builder**, never a bespoke file. ~21 archetypes × 3
> tiers ≈ 60 variants collapse to ~13 meaningful files instead of 60.

**Package layout — `backend/view_markets/expressions/`:**

- [ ] **`catalog.py`** `[NEW]` — declarative **archetype registry** (DATA, frozen
      dataclasses, like `TEMPLATES`). One entry per archetype from the strategy
      doc: events E1–E10 (rate debit-spread, NBFC-vs-bank pair, event straddle,
      IV-crush iron-fly/calendar, PEAD, broken-wing, merger/open-offer arb, index
      inclusion, budget/election rotation, shock hedged-basket); relative
      (cointegrated pair, sector-vs-index, factor-ETF-vs-index, ratio/RS,
      relative-options); theme (purity/conviction basket, factor-tilt, optionized/
      hedged overlay, multi-asset). Each entry: `key, label, view_types,
      expression_kind, template/scheme/params, applies_when, required_primitive,
      status(EXISTS|GAP)`.
- [ ] **`tiers.py`** `[NEW]` — Conservative/Balanced/Aggressive as **knob settings
      in ONE place** (capital intensity, leverage, hedge ratio, # legs, option
      moneyness, pair z-thresholds, basket concentration/caps). Per the §5 tier
      tables in the strategy doc.
- [ ] **`builders/` — one builder per `expression_kind` (delegate to existing
      engines, never reinvent):**
  - [ ] `option_builder.py` → `services/option_strategies.py` `TEMPLATES`
        (bull_call_spread, iron_condor, straddle, calendar, broken_wing, collar,
        covered_call, ratio…). `[REUSE]`
  - [ ] `pair_builder.py` → `services/backtest/pairs/` (EG/Johansen/OU, z-bands,
        beta-hedge, per-leg lot sizing, residual-beta≈0). `[REUSE]`
  - [ ] `basket_builder.py` → `propose_basket_allocation` + `weighting.py` +
        `sector_universe.py`, gated by `screens.py`. `[REUSE]`
  - [ ] `multi_asset_builder.py` → equity + gold ETF + hedge sleeves, risk-parity
        at the **asset-class** level. `[REUSE]` + `[EXTEND]`
  - [ ] `hedge_builder.py` → protective put / zero-cost collar / covered call —
        **index-level (NIFTY) hedges**, not thin single-stock options. `[REUSE]`
- [ ] **`honest_short.py`** `[NEW]` — the **no-retail-delivery-short decision
      rule** (single-stock → SSF-eligible? futures/puts : AVOID-annotate; index →
      NIFTY/BANKNIFTY future or put, never ETF delivery short) + the **AVOID /
      underweight expression type** as a first-class output. Critical so we never
      fabricate a fake short. `[NEW]`
- [ ] **`screens.py`** `[NEW]` — theme **Basket Purity Score** (curated tag →
      fundamentals-segment → LLM-relevance, clamped/flagged) + **liquidity screen**
      (ADV floor, free-float mcap, impact cost, options-availability flag) +
      **`single_name_cap`** (iterative redistribution) + **min-names floor** (refuse
      a 3-stock "theme" → offer the ETF proxy). The "good basket, not flat basket"
      engine. `[NEW]`
- [ ] **`cross_sectional.py`** `[NEW]` — decile/rank engine + **factor → smart-beta
      ETF catalog** (NIFTY200 Momentum 30 / NIFTY100 Quality 30 / NIFTY50 Value 20 /
      Alpha Low-Vol 30 / Multi-Factor) for the factor-ETF-vs-index relative
      expression. `[NEW]`
- [ ] **`merger_arb.py`** `[NEW]` — open-offer/buyback **spread + implied-break-
      probability + annualized return + proration** calc (the retail-friendly
      Indian event arb). `[NEW]`
- [ ] **`timing.py`** `[NEW]` + `[REUSE]` — map **Pre-position / Confirmation /
      Hybrid** to the deployed workflow's trigger shape (arm-now vs event-gated vs
      split tranche ladder), reusing `trigger.schedule/event/scheduled_macro`.
      Prediction-market triggers stay **flag-gated + PROGA-hidden** (see Phase 5).
- [ ] **`dispatch.py`** `[NEW]` — `suggest_expressions(db, view, tier?) ->
      list[ViewExpression]`: pick applicable catalog archetypes for the view, build
      each tier via its kind-builder + tier knobs, run the **required disclosures**
      (why / risk_profile / capital_intensity / historical_strength / time_horizon —
      enforced, never blank), and persist to `view_expressions`. This is the one
      public entry point Phases 4–5 call.

**Invariants (enforced in every builder):** register-not-execute (expressions
deploy as *armed* workflows the user confirms); never fabricate (degrade to
AVOID/honest when an instrument isn't tradeable); India microstructure hard-coded
(weeklies = NIFTY/SENSEX only, BANKNIFTY monthly; single-stock options monthly +
physical + STT-on-intrinsic; MCX commodities tradeable via register-not-execute; foreign → listed ETF proxy);
defined-risk first (stated max loss).

**Tests** — `tests/view_markets/expressions/`: catalog integrity, each builder,
tier knobs, the honest-short rule (asserts NO fabricated delivery short), screens
(purity/cap/min-names refusal), `dispatch` end-to-end (view → 3 tiers persisted
with full disclosures), merger-arb/cross-sectional math.

**Already DONE in Phase 2 — do NOT rebuild:** `implied_move`, `event_study` (CAR),
`confidence` (two-dial + Alignment Score), `expectations`, `transmission`,
`curation`, `lifecycle`, `feeds`. Phase 4 backtests each built expression and
attaches its Trust verdict / Alignment Score.

---

## Phase 4 — Backtest, deploy & automate wiring

- [ ] **Backtest each expression** through the existing engines + **trust
      battery** (PSR/DSR/MinTRL/MC/walk-forward/verdict). Baskets →
      `backtest_portfolio`; pairs → `backtest_pairs`; option/directional →
      `backtest_dsl_tree`/`backtest_workflow`. Store `backtest_run_id` on the
      expression. `[REUSE]`
- [ ] **Compare-variants** — run Conservative/Balanced/Aggressive in parallel
      and rank by the Trust verdict; surface the comparison on the card.
      `[REUSE]` engines, `[NEW]` orchestration.
- [ ] **(Optional, later) Scenario-conditional backtest** — "if monsoon −20% vs
      baseline, what's the return?" The current backtester is pure price replay;
      this is a genuine extension, not V1-critical. Mark as **stretch**.
      `backtester/engine.py`. `[EXTEND]`.
- [ ] **Deploy path** — "Deploy" on an expression → `createWorkflow` →
      `activate` (→ optional `run`), exactly the workflow-draft-card lifecycle.
      Link `view_expressions.workflow_id`. `[REUSE]`
- [ ] **Approval gating preserved** for any order-placing step. `[REUSE]`

---

## Phase 5 — Chat integration (tools, routing, cards)

> Make View Markets reachable from the chat surface — the front door.

- [ ] **New chat tools** `[NEW]` in `agents/tools.py` (+ handlers in
      `tool_executor.py`, + `_REAL_TOOLS` in `tool_registry.py`). **Beta surface =
      EXPLORE curated views + EXPRESS + DEPLOY** (chat does NOT auto-create views —
      `propose_*_view` tools are DEFERRED with the generators):
  - [ ] `list_views(filter?)` / `get_view(id)` — browse/open the curated set
  - [ ] `suggest_view_expression(view, tier?)` → calls `expressions/dispatch.py`
  - [ ] `compare_view_expressions(view)` — the 3-tier comparison
  - [ ] `deploy_view_expression(expression_id, timing_mode)` — arm the workflow
  - [ ] `follow_view(id)` — lifecycle updates
  - [ ] *(deferred: `propose_event_view`/`propose_relative_view`/`propose_theme_view`
        until the generators ship)*
- [ ] **Tool subsets** — add `VIEW_MARKETS_CREATE` / `_EXPRESS` / `_DEPLOY` to
      `TOOL_SUBSETS`. `agents/tools.py`. `[EXTEND]`
- [ ] **Routing rules** — `services/tool_router.py`: route belief phrasings to
      the View Markets subset ("I think …", "RBI cuts rates", "IT beats Nifty",
      "position me for a manufacturing upcycle"). **Reuse `thematic_map.
      detect_thematic_scenario` + `_POSITIONING_RE`** as the seed matcher.
      `[REUSE]` + `[EXTEND]`.
- [ ] **system.md routing section** — when to surface a View vs a plain
      workflow/automation; never fabricate confidence/odds; surprise framing
      rules; the two-confidence distinction; disclaimer discipline.
      `prompts/system.md`. `[EXTEND]`
- [ ] **⚠️ PROGA caveat (per `VIEW_MARKETS_VIEW_TAXONOMY.md`, VERIFY first).**
      Never surface a clickable Polymarket/Kalshi odds/bet. PM odds are a **hidden
      internal prior** only; surface Pivot's **own option-implied probability**
      (`implied_move.py`) to the user. Applies to the existing
      `trigger.polymarket`/`trigger.kalshi` too — not just V2. `[EXTEND]`
- [ ] **REPLY-CLASS** — add/confirm a `VIEW` reply-class (sectioned, visual,
      calm; no dense tables). `services/chat_service.py`. `[EXTEND]`
- [ ] **Render hints** `[NEW]` — `view_card`, `view_expression_card`,
      `transmission_card` on the tool results.

---

## Phase 6 — Frontend (the Views surface + cards)

> Today's nav: Chat · Portfolio · Agents · Calendar · Screener. View Markets is
> heavily UX-driven (cards/timelines/diagrams/confidence dials).

- [ ] **New "Views" top-level tab** — add a `TabKey` + `NAV_ITEMS` entry +
      route. `pivot-next/components/AppShell.tsx` + a new
      `components/views/ViewsTab.tsx`. `[NEW]` + `[EXTEND]`.
- [ ] **Views gallery** — browse curated views by category/type/horizon with
      confidence indicators and lifecycle status. `[NEW]`
- [ ] **View detail layout** (per spec §Suggested View Layout):
      Header (title/category/horizon) → Thesis → Market Expectations
      (consensus + surprise) → **Transmission Map** (cause/effect diagram) →
      Expressions (Conservative/Balanced/Aggressive) → Deployment
      (Backtest/Deploy/Automate) → Related Views. `[NEW]`
- [ ] **Transmission diagram component** — visual DAG (nodes/edges), progressive
      disclosure, no data overload. `[NEW]`
- [ ] **Confidence dials** — two separate gauges (outcome vs expression) +
      letter-band Alignment Score. **Already computed by Phase 2 `confidence.py`
      (gated by Trust verdict, suppressed below MinTRL)** — the FE only renders the
      stored values; never re-derive or imply certainty. `[NEW]` (render only)
- [ ] **Expression cards** — tier toggle; each shows why/risk/capital/strength/
      horizon + a Trust-verdict pill from the backtest; **Deploy** reuses the
      workflow create/activate flow. Mirror `WorkflowDraftCard.tsx` /
      `OptionStrategyCard.tsx` patterns. `[REUSE]` + `[NEW]`.
- [ ] **Inline chat View card** — when chat proposes a view, render a compact
      card with "Open in Views →" (mirrors `workflow_draft_card`). `pivot-next/
      components/chat/`. `[NEW]`
- [ ] **Follow / lifecycle UI** — follow a view, see Open→…→Resolved progress.
      `[NEW]`
- [ ] **lib/api.ts + lib/types.ts** — typed clients + types for all View
      Markets endpoints/cards. `[EXTEND]`
- [ ] **Visual QA** light + dark; desktop-first; calm/guided aesthetic per spec
      design principles. `[NEW]`

---

## Phase 7 — REST API, testing, eval & rollout

- [ ] **REST router** — `/api/views` CRUD + `/expressions` + `/follow` +
      `/{id}/backtest` + `/{id}/deploy`. `pivot/backend/routers/views.py`,
      registered in `main.py`. `[NEW]`
- [ ] **Backend tests** — curation/publish gate, the **expression engine**
      (catalog/builders/tiers/dispatch/honest-short/screens), expression→workflow
      deploy, no-fabrication guard. *(Phase 2 already covers expectations/surprise,
      two-dial confidence, event-study, lifecycle — 114 tests green.)*
      `pivot/tests/view_markets/`. `[NEW]`
- [ ] **Frontend tests** — gallery, view-detail, transmission diagram, expression
      tier toggle, deploy flow (vitest + RTL). `[NEW]`
- [ ] **Live multi-turn chat eval** — belief prompts ("I think RBI cuts", "IT
      beats Nifty 6m", "position me for a manufacturing upcycle") → correct view
      type, correct expressions, honest confidence, no fabricated odds. Report
      the **triad** (tokens + latency + quality) per item. One instrumented run,
      fix, retest once. `[NEW]`
- [ ] **Seed curated view set** — an initial professionally-built library (start
      from the six `thematic_map` scenarios + the 2026 macro calendar events).
      `[REUSE]` + `[NEW]`.
- [ ] **Docs** — update `docs/API_CONTRACT.md` (new endpoints/cards **before**
      implementation lands, per the contract rule), `docs/ARCHITECTURE.md`, and
      this checklist's status. `[EXTEND]`
- [ ] **Flag flip & rollout** — enable `view_markets_enabled` for internal →
      beta → GA. `[EXTEND]`

---

## Out of scope for V1 (explicitly do NOT build)

- User-created beliefs / custom belief builders / custom benchmarks.
- A prediction exchange, binary YES/NO contracts, community voting, or trading
  outcome contracts.
- Auto-execution of any kind (register-not-execute stays absolute).
- Scenario-conditional backtesting is a **stretch**, not a V1 gate.

---

## Fast-start sequence (smallest path to a demo)

1. Phase 1 data model + `0023` migration.
2. One **event view** end-to-end (RBI rate cut): generator (calendar+verifier) →
   transmission DAG from `thematic_map` rate-cut scenario → expectations from a
   Polymarket/Kalshi odds read → two confidence dials → three expressions
   (banks basket / rate-sensitive calls / NBFC pair) → backtest each → deploy
   the chosen one as an armed workflow.
3. Minimal Views tab + view-detail + expression cards (reuse workflow deploy).
4. Chat tool `propose_event_view` + routing reuse of
   `detect_thematic_scenario`.
5. Eval the single vertical, then fan out to relative & theme views.
