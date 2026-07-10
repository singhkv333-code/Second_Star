# View Markets (Pivot V2) — V1 Scope Contract & Decisions

> The decisions doc for **View Markets**, Pivot's belief-first investing layer
> (**Belief → Expression → Deployment**). This is the binding V1 scope contract:
> what we build, what we deliberately do **not**, and the data/curation/safety
> decisions every later phase must respect.
>
> **Source spec:** [`Markdowns/Version2.md`](./Version2.md)
> **Build checklist:** [`Markdowns/VIEW_MARKETS_V2_CHECKLIST.md`](./VIEW_MARKETS_V2_CHECKLIST.md)
> **Companion docs (authored in parallel — read alongside this one):**
> - `Markdowns/VIEW_MARKETS_STRATEGY_DESIGN.md` — how each expression is
>   *constructed* (proper, effective strategies, not always a flat basket).
> - `Markdowns/VIEW_MARKETS_TESTING_AND_SCORING.md` — the testing standards +
>   the user-facing "historical alignment / score" reference.
> - `Markdowns/VIEW_MARKETS_VIEW_TAXONOMY.md` — the curated view taxonomy /
>   seed set.
>
> **Status:** Phase 0 + Phase 1 (this doc, the `view_markets_enabled` flag, the
> data model, migration `0023_view_markets`, Pydantic schemas) are **built and
> committed locally; the migration is NOT applied to Azure** — left for the
> human to apply. Everything ships behind `view_markets_enabled` (default OFF).

---

## 1. What View Markets is (one paragraph)

Most retail investors think in **beliefs** ("RBI cuts rates", "IT beats Nifty
over six months", "India enters a manufacturing upcycle"), not in instruments
("buy the 23000 call", "build a 1×2 put spread", "go long the INFY/TCS pair").
View Markets is the layer that turns a belief into an evidence-backed **view**,
shows *what's already priced in* and where the user's view **surprises** the
market, draws the **transmission map** (cause → effect → beneficiaries), and
offers **Conservative / Balanced / Aggressive** deployable expressions the user
can backtest and deploy as an **armed** workflow. It sits on top of Pivot's
existing strategy/workflow/backtest/paper engines; it is a discovery and
expression surface, **not** a new exchange.

---

## 2. The two product bars this must clear

Mirrors the repo's quality bars (`CLAUDE.md`):

1. **Execution correctness** — the right view *type*, a faithful belief→view
   parse, real numbers from real tools, the right expression *kind* per tier,
   and a deployable artifact that arms (never auto-executes).
2. **Output quality** — a view is *convincing, data-rich, structured, and
   honest*: it states the thesis, the consensus, the surprise, the causal
   chain, two separate confidences, and per-tier disclosures. A correct-but-
   thin view (one flat basket, no evidence, no surprise framing) **fails**.

---

## 3. The three view types (locked for V1)

Every view is exactly one of these. (Full taxonomy + seed set in
`VIEW_MARKETS_VIEW_TAXONOMY.md`.)

| Type | Definition | Resolves? | `resolution_date` | Default expression bias |
|------|------------|-----------|-------------------|-------------------------|
| **Event** | A specific, objective outcome by a date (RBI cut, OPEC decision, earnings beat, policy announcement). | Yes — objectively | **Required** | Directional / option structures + timing modes |
| **Relative** | Asset/sector/theme A outperforms benchmark B over horizon T (IT vs Nifty 6m, Gold vs equities, PSU vs Private banks). | Yes — by a measured spread | Optional (end of horizon) | Pair / relative + tilt baskets |
| **Theme** | A long structural narrative (India manufacturing, AI adoption, defence supercycle). | Often not objectively | NULL / open-ended | Beneficiary baskets + multi-asset |

**Actionability gate (spec §"Important Principle"):** a belief without a
*measurable outcome*, a *defined benchmark*, and a *time horizon* is not a view.
The curation gate (§7) rejects vague beliefs ("India will grow") in favour of
specific ones ("India GDP growth exceeds consensus over the next 12 months").

---

## 4. Expression tiers — Conservative / Balanced / Aggressive

Every published view carries **up to three** expressions, one per tier. A tier
is a *risk posture*, realised differently per `expression_kind`
(`basket | option_strategy | pair | multi_asset | hedge`). Concrete
construction rules live in `VIEW_MARKETS_STRATEGY_DESIGN.md`; the **contract**
here is:

- **Conservative** — capital-preserving. Cash/ETF-heavy, defined-risk options
  (debit spreads, not naked), small position sizing, often hedged. Lower upside,
  lower drawdown.
- **Balanced** — the default. A focused beneficiary basket / a defined-risk
  directional structure / a long-leg relative tilt. Moderate conviction sizing.
- **Aggressive** — higher conviction/leverage *within Pivot's limits*: tighter
  option structures, concentrated baskets, larger sizing. **Still register-not-
  execute, still defined-risk where options are involved** — "aggressive" never
  means naked unlimited-loss legs or auto-execution.

Each expression **must** carry the spec §Expressions disclosures, persisted as
first-class columns on `view_expressions`: **why it may work** (`rationale`),
**risk profile**, **capital intensity**, **historical relationship strength**,
**time horizon**. The "historical strength" is the bridge to the testing/scoring
doc — it is computed, never asserted.

**India-first realism.** Universes are NSE/BSE equities, indices, NSE options
(NFO), and listed ETFs (GOLDBEES, NIFTYBEES, sector ETFs, MON100 as the US-tech
proxy). MCX commodities (crude, gold, silver, metals, natgas) are tradeable via
register-not-execute (leveraged — keep the risk caveat). Foreign equities are out
of scope — offer the listed Indian ETF proxy instead. **Shorting equity is not wired** → relative
views use a long-leg + "AVOID" framing (the `thematic_map` convention), or an
options structure, never an unhedged short.

---

## 5. Timing modes (Event views especially)

Being right about an event does not guarantee profit — unrelated market moves
can dominate before resolution. So every event expression supports an entry
mode, encoded as the **trigger shape** of the deployed workflow:

| Mode | Meaning | Workflow trigger shape |
|------|---------|------------------------|
| **Pre-position** | Enter now; capture anticipation. Higher idiosyncratic risk; event may already be priced. | Immediate (`trigger.manual` / `trigger.schedule` now) |
| **Confirmation** | Enter only after the event resolves. Lower uncertainty, misses the initial move. | Event-gated (`trigger.event` / `trigger.scheduled_macro` / `trigger.polymarket` / `trigger.kalshi`) |
| **Hybrid** | Partial now, add on confirmation. Balances risk/opportunity — the **expected default** for many event views. | Split: immediate leg + event-gated leg |

Confirmation/Hybrid reuse the existing event-trigger infra
(`macro_events/`, `news_events/`, the Polymarket/Kalshi adapters).

---

## 6. The two confidence dimensions (kept separate)

A single confidence number is misleading. View Markets stores **two**, in
`view_confidence` (unique per `(view_id, dimension)`):

- **Outcome confidence** — how likely is the event/belief *itself*? (e.g. P(OPEC
  cuts).) Sourced from prediction-market odds / calendar certainty / model.
- **Expression confidence** — *given* the outcome occurs, how likely does the
  proposed expression benefit? (e.g. P(energy stocks outperform | OPEC cuts).)
  Sourced from historical relationship strength (correlation / event study /
  backtest).

These are different concepts and must never be collapsed into one dial. The FE
renders two separate gauges.

---

## 7. Curation model (the key V1 decision)

> **BETA DECISION (2026-06-29):** Views are **MANUALLY CURATED / authored by the
> user** (the founding team acting as the "view curator"). The automatic
> **EVENT / RELATIVE / THEME view-generators are DEFERRED** — they are *not*
> built in Phase 2. Phase 2 ships the **enrichment + scoring + lifecycle
> pipeline** that operates on human-authored views:
> `backend/view_markets/{transmission, implied_move, feeds, event_study,
> expectations, confidence, curation, lifecycle}.py` (transmission DAG,
> option-implied expected move + implied probability, surprise / expectations
> aggregation, event-study CAR/CAAR/BHAR with the Trust Battery verdict, the
> two-dial confidence / Alignment Score, the manual curation/authoring service,
> the data-feed shims, and the lifecycle worker). The generators in §7.1 below
> remain the documented *future* path; in beta the **curation service replaces
> them** and a human supplies the seed views. Everything stays gated behind
> `config.view_markets_enabled` (default `False`).

**V1 views are backend-generated + human-curated. Views are NOT user-typed.**
(Spec §"Initial Scope" + §"Explicitly Out of Scope".) This is a hard product
decision for V1.

**Authoring pipeline (who/what creates the seed set):**

1. **Generators (machine first draft).** Backend generators draft views from
   real data, *not* hand-typed opinions:
   - *Event* ← `macro_events/calendar.py` (RBI/CPI/FOMC 2026 dates) + the
     verifier for real-outcome reads.
   - *Relative* ← `get_correlation_matrix` + `compare_performance` +
     `services/backtest/pairs/cointegration.py` for the relationship score.
   - *Theme* ← the six `services/thematic_map.py` scenarios (winners/losers/
     confirm/invalidate) + `sector_universe.py` beneficiary baskets.
   Every generated view must attach the **evidence** it was built from
   (historical returns, correlations, event-study refs, calendar entry).
2. **Validation (machine gate).** A view cannot enter review unless it passes
   the **actionability gate** (§3: measurable outcome + benchmark + horizon),
   has ≥1 transmission edge, ≥1 expression per intended tier with all five
   disclosures populated, both confidence dimensions scored, and (for event/
   relative) an expectations row. No fabricated numbers — values must trace to
   a tool/source or the view is rejected.
3. **Human review gate (publish).** A reviewer (the founding team in V1; a
   designated "view curator" role thereafter) reads the drafted view and either
   **publishes** it (sets `published_at`, moves `status` past `open`) or sends
   it back. **Only `published_at IS NOT NULL` views are surfaced** to users when
   `view_markets_enabled` is on. The DB is permissive (a draft is just an
   unpublished row); the *gate* is enforced in the service/router layer + this
   review step, mirroring how IPO/option intents are register-only.
4. **`user_id` semantics.** `market_views.user_id` is **NULL for every curated
   V1 view**. The nullable column is forward-compatible with the future
   user-authored path (§10) but no V1 code writes a non-NULL `user_id` to a
   view, and no "create your own belief" surface ships in V1.

---

## 8. Lifecycle

`view_status`: **Open → Developing → Consensus → Resolved → Archived.**

- **Open** — published, accepting follows; resolution still distant.
- **Developing** — evidence/expectations are moving; consensus forming.
- **Consensus** — the market has largely converged toward the view (low
  remaining surprise).
- **Resolved** — the event occurred / the horizon ended; `resolved_value` is
  backfilled on the expectations row and outcome confidence collapses to 0/1.
- **Archived** — closed out, kept for the historical/track record.

A later **lifecycle worker** (Phase 2) advances `status` as resolution dates
approach and as the verifier reads real outcomes. It will reuse the APScheduler
pattern — **module-level jobs only** (the documented closure-kills-the-scheduler
gotcha). Users **follow** a view (`view_follows`) to get lifecycle updates.

---

## 9. "We READ prediction markets — we never BECOME one" (hard principle)

View Markets consumes Polymarket + Kalshi (and consensus/model) odds **only** as
the "what's priced in" input to the **surprise** framing, stored in
`view_expectations` (`source ∈ {polymarket, kalshi, consensus, model}`,
`expected_value` vs `user_view_value` → `surprise_sign`). This reuses the
existing `news_events/sources/{polymarket,kalshi}.py` adapters and the shared
prediction-market evaluator.

View Markets **is NOT**, and V1 must not drift into:

- a prediction exchange or order book for outcomes,
- binary YES/NO contracts or trading on outcome contracts,
- a betting/community-voting market,
- a financial-advisory or certainty-claiming recommendation engine.

Expressions deploy only as **armed Pivot workflows** the user confirms in their
own broker app. **Register-not-execute stays absolute** (SEBI retail-algo
posture). Paper trading remains fully simulated. We give **data + frameworks**,
never personalised buy/sell advice; every view ends with the standing analysis-
not-advice disclaimer; **no fabricated numbers, ever** — every figure traces to
a card/tool/source.

---

## 10. Explicitly OUT of scope for V1 (do not build)

- User-created beliefs / custom belief builders / custom benchmarks / personal
  theses. (Future direction only — §"Future Direction" in `Version2.md`. The
  nullable `user_id` reserves the seam; no V1 surface uses it.)
- A prediction exchange, binary YES/NO contracts, community voting, trading
  outcome contracts.
- Auto-execution of any kind (register-not-execute is non-negotiable).
- Scenario-conditional backtesting ("if monsoon −20%…") — a **stretch**, not a
  V1 gate.
- Equity shorting as an expression leg (not wired) — use long-leg/AVOID or an
  options structure.

---

## 11. Data model (Phase 1 — shipped in `0023_view_markets`)

Six tables, all UUID-PK (`gen_random_uuid()` in Postgres; `String(36)` +
`_uuid_str` in the model for SQLite test parity), mirroring the `Workflow`
table conventions. Enum columns are real Postgres ENUM types in the migration
and `SQLEnum(..., native_enum=False)` (→ CHECK on SQLite) in the model. Within
the domain every FK to `market_views(id)` is a **hard** uuid↔uuid FK with
`ON DELETE CASCADE`; `backtest_run_id` / `workflow_id` are **soft** refs
(cross-domain, no FK), exactly like `paper_orders`.

| Table | Purpose | Key columns / enums |
|-------|---------|---------------------|
| `market_views` | The belief | `user_id?`, `view_type{event,relative,theme}`, `title`, `thesis`, `category`, `time_horizon`, `status{open,developing,consensus,resolved,archived}`, `resolution_date?`, `published_at?` |
| `view_expressions` | Tiered deployable strategies | `view_id`, `tier{conservative,balanced,aggressive}`, `expression_kind{basket,option_strategy,pair,multi_asset,hedge}`, `config(JSONB)`, disclosures (`rationale`,`risk_profile`,`capital_intensity`,`historical_strength`,`time_horizon`), `backtest_run_id?`(soft), `workflow_id?`(soft) |
| `view_transmission` | Cause→effect DAG | `view_id`, `seq`, `from_node`, `to_node`, `edge_label`, `strength`, `evidence` |
| `view_confidence` | Two dimensions | `view_id`, `dimension{outcome,expression}`, `score`, `evidence`; **UNIQUE(view_id,dimension)** |
| `view_expectations` | Surprise framing | `view_id`, `source{polymarket,kalshi,consensus,model}`, `market_id?`, `expected_value`, `user_view_value`, `surprise_sign`, `as_of`, `resolved_value?` |
| `view_follows` | Lifecycle subscription | `user_id`, `view_id`, **UNIQUE(user_id,view_id)** |

Indexes: `market_views(status)`, `market_views(view_type)`,
`market_views(user_id)`, and `view_id` on each child (+ `view_follows(user_id)`).
Pydantic request/response schemas mirror every enum as `Literal[...]`
(`backend/schemas.py`, "View Markets" section).

**Migration safety:** `0023` chains `down_revision = "0022_user_auth_beta"`. It
is additive-only (no ALTER on existing tables) and has **not** been applied to
Azure/production Postgres — the human applies it.

---

## 12. Phase pointers (where the rest lives)

The full build sequence is in `VIEW_MARKETS_V2_CHECKLIST.md`. After this Phase 1
foundation: Phase 2 = view generation & evidence pipeline; Phase 3 = the
expression engine (the "proper, effective strategies" the user asked for — see
`VIEW_MARKETS_STRATEGY_DESIGN.md`); Phase 4 = backtest through the existing
**trust battery** + the user-facing alignment score (see
`VIEW_MARKETS_TESTING_AND_SCORING.md`); Phase 5 = chat tools/routing/cards;
Phase 6 = the Views FE surface; Phase 7 = REST API, tests, eval, seed set,
flag flip.
