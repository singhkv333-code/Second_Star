# Pivot — Platform Audit & Phased Fix Plan (2026-07-03)

> Judgment of the whole product against its own philosophy ("correctness and
> output quality ARE the product; never fabricate; honest boundaries") and the
> eight standards the founder set. Evidence: five parallel code audits
> (chat brain, DB access, data inventory, strategy soundness, FE perf/visuals)
> + a hands-on visual pass of every surface (fresh QA account) + live
> screenshots of Public.com and ElevenLabs. Polymarket/Kalshi were
> **unreachable from this network (geo-block)** — comparisons to them are from
> established product knowledge, stated as such.
> Every issue below carries its evidence anchor. Phases are ordered by
> trust-impact, not by effort.

---

## 0. Report card

| # | Dimension | Grade | One-line verdict |
|---|-----------|-------|------------------|
| 1 | Strategy context in chat | **C+** | Draft/order machinery is genuinely sophisticated; View Markets — the V2 flagship — is *completely unaddressable from chat*, and context falls off a 6-turn cliff. |
| 2 | Tool calling in chat | **B-** | Heavy deterministic guarding + route-stable caching, but a regex-only router with silent fallback, single-shot calls, and *zero committed eval numbers*. |
| 3 | DB query batching | **C** | Screener/greeks/scheduler are model citizens; `/api/views` costs ~120 queries (~7s cold), conversations 51, paper marks is a network N+1. Nothing measures query counts. |
| 4 | Data quality | **C+** | Point-in-time fundamentals and honest degradation are real strengths — but the same stock shows **three different prices on three surfaces**, and the highest-ROI datasets (IV history, futures OI, index weights, corp-actions) are absent. |
| 5 | View-market strategy sense | **C-** | Where the doctrine runs (mideast, ev, option math) it beats any Indian retail product; but all 8 views ship an identical fabricated `rationale`, nifty30k's strikes can't profit from its own event, and the ₹2k "entries" are 3–8%-POP lottery tickets. |
| 6 | Site performance | **C** | 925KB of JSON in a client bundle, zero code-splitting, `no-store` on every GET, one 65-second chat turn observed live. |
| 7 | Visual/graph quality | **B-** | Broad, honest chart inventory; but the payoff diagram omits max-P/L/breakevens, the transmission "map" is a list not a DAG, and the portfolio chart contradicts its own headline. |
| 8 | UI/UX vs inspo set | **B-** | The foundation is *not* AI slop — white chrome, serif accents, border-only cards are already Public.com-adjacent. What separates us from Polymarket/Kalshi is density, empty-state craft, and data consistency, not taste. |

**Overall: C+.** The philosophy (honesty scaffolding, register-not-execute,
never-fabricate) is implemented deeper than most funded fintechs. The gap is
*trust erosion at the seams*: fabricated boilerplate rationale, three prices
for one stock, lottery tickets framed as affordability, dev artifacts visible
in prod surfaces. All fixable; none requires new architecture.

---

## 1. What the evidence showed (by standard)

### 1.1 Strategy context in chat — C+
Strong and real: `PendingToolCall` fast-resume with zero LLM hop, a per-symbol
addressable draft map with honest LRU eviction, route-stable prompt-cache
keying (`tool_router.py:972-1002`), hard per-user isolation.

The breaks:
- **View Markets has no chat surface at all.** No view tools registered
  (`tool_registry.py:527-622` registers only price/backtest tools), zero
  router rules, no `active_view` Redis slot. "Backtest that view", "deploy
  the second one", "make it cheaper" → fallback read-tools → prose or decline.
  The flagship V2 surface is invisible to the flagship V1 surface.
- **6-turn context cliff** (`conversation_store.py:49`): anything older is
  gone. The durable `ChatSummary` that could bridge it is **write-only** —
  generated, stored, never injected into a turn.
- **Cards survive only as prose captions** (Redis stores text only; card JSON
  lives in one 10-min-TTL draft slot + a 4-entry map). "Change the strike to
  24000" four turns later forces reconstruction from prose — a fabrication
  vector on exactly the never-fabricate non-negotiable.
- **`handle()` / `handle_stream()` have drifted**: the M1
  prose-clarification→ASK_USER retry exists only in the non-streaming path
  (`chat_service.py:6035-6192`), and production uses `/stream`.

### 1.2 Tool calling — B-
- Router is ~30 regex rules; a miss silently falls to 7 read tools
  (`tool_router.py:906-943`). No semantic backstop.
- Single-shot is real for everything except the two workflow tools
  (`chat_service.py:6467-6485` is the only bounded retry). A mechanical arg
  error on an order/option tool dead-ends into a clarification.
- Tool results hard-truncated at 6,000 chars **with no truncation marker**
  (`chat_service.py:3659`) — the model reasons on silently partial chains.
- The 27-predicate pre-LLM guard cascade (~450 lines, order-dependent,
  duplicated across two code paths) has no guard-interaction tests.
- `tests/eval_results/` is empty: current tool accuracy is **unmeasured**.
- Live observation: "Analyse TCS" took **65s** and rendered no card/chart —
  correct-and-honest text, but the technicals table read "unavailable" on
  every window (stale data session narrated around).

### 1.3 DB batching — C
Well-batched exemplars exist and should be the house pattern
(`services/portfolio_greeks.py:74-88`, screener's single multi-CTE + one
Kite overlay call). Against that:
- `GET /api/views`: ~6 queries × ~20 views ≈ **120 round-trips ≈ 7s cold**
  at 62ms RTT (`routers/views.py:1094`, `:818-846`).
- `GET /api/conversations`: 1+50 lazy loads, full message bodies fetched to
  compute a count + preview (`routers/conversations.py:161-163`).
- Paper `mark_positions`: one Kite HTTP call **per position** (the API takes
  a list), plus a fresh `SessionLocal()` per option symbol
  (`paper/marks.py:38,75-113`) — multiplied per account by the EOD snapshotter.
- View detail re-queries what `_build_summary` already loaded
  (`routers/views.py:1358-1422`).
- **No query-count telemetry** — none of this is visible in logs today.

### 1.4 Data quality — C+
Genuinely good: Moneycontrol statements are **point-in-time correct**
(backtest-safe, `financials_db.py:44-206`); live chain IV is status-flagged
and never fabricated; the research cache is 2,209 symbols × 16.5 years.

The trust-killers seen live:
- **Three prices for HDFCBANK on three surfaces at once** — portfolio LTP
  ₹1,643, watchlist chip ₹1,718, screener grid ₹796 (chips vs grid vs
  portfolio use different, unreconciled sources). TCS: chip ₹3,895 vs grid
  ₹1,982. This is the single most credibility-destroying thing on the site.
- Chat quoted a **6.25% dividend yield for TCS** (real: ~1.5–3%) and had to
  hedge around its own bad number.
- A fresh account shows **₹77,945 portfolio value + P&L in the top bar** with
  no "Paper" label — fabricated-looking numbers on first login.
- Structural gaps (full ranked list in the appendix): **no option IV history**
  (the code explicitly disables IV-rank features, `chat_service.py:2528-2536`),
  **no futures OI/basis** (`historical.py:244` sets `oi=False`), **no index
  constituents/weights** (Relative views lack a rigorous benchmark), **no
  corporate-action adjustment table** (unadjusted price jumps threaten every
  backtest), promoter% is an insider proxy, FII/DII is a calendar marker with
  no flow numbers, ~1,852 companies still unmapped.

### 1.5 View-market strategies — C- (auditor's grade; scaffolding B, content D+)
- **P0 integrity bug: all 8 views carry an identical fabricated `rationale`**
  — leftover rural-basket boilerplate citing Britannia and HAC t-stats on
  AI/nuclear/gold views. `enrich_viewpack.py` never overwrites it. This is
  pseudo-quant garble and the single biggest regulatory/credibility exposure.
- **nifty30k is internally inconsistent three ways**: forward model says
  p≈0 (option-implied), thesis prose says ~6%, spec implies 19% move — and
  the "30K bet" call spread tops out at +14%, structurally unable to profit
  from the event it is named after.
- **The ₹2k small tickets are 3.1–7.9%-POP far-OTM longs, mostly rolled to
  21–42 days on 6–12-month views** — re-buying a ~5%-POP ticket ~6× is a
  near-certain cumulative loss, framed as accessibility.
- Tier-label failures: a "Conservative" two-stock momentum duo with -43.6%
  drawdown; a no_edge/46.9%-positive tier shipped as "Calm & Cushioned".
- Cosmetic construction: entries cluster ₹1,844–1,999 (tuned to the budget),
  "core-satellite" is ~90% ETF + one token share, gold's equity basket tracks
  gold at β=0.19/R²=0.02 while the entry quietly fixes it with GOLDBEES.
- Forward "expected positive" rides entirely on hand-typed asymmetric moves
  at p=0.5 — honestly shrunk and banded, but the *sign* is an assumption.

### 1.6 Site performance — C
- `app/view-pack/page.tsx:21-22` statically imports **925KB of JSON into the
  client bundle** of a `"use client"` page.
- **Zero `next/dynamic` anywhere**; recharts loads eagerly in 20+ components,
  even behind "Advanced" disclosures. 158 files are client components; 2 are
  server components.
- `cache:"no-store"` on every GET (`lib/api.ts:230`); no SWR/react-query.
- App-wide 30s portfolio poll regardless of tab/visibility
  (`AppShell.tsx:124,392`); screener refetches the full universe up to 6×
  while warming (`ScreenerPage.tsx:607-628`) — observed ~6s+ to fill.
- Live quotes fetch **one request per holding** and currently all fail on a
  `127.0.0.1` vs `localhost` CORS mismatch → the red "10 errors" toast on
  core pages.
- Already good: in-flight GET dedup, batched logos, WS quote singleton.

### 1.7 Visuals — B-
Inventory is broad (18+ components) and honest (no fabricated lines, MC uses
API percentiles). The gaps that matter:
- **PayoffDiagram omits max profit, max loss, net debit, POP, and a spot
  marker** — the numbers every option trader reads first (Sensibull table
  stakes).
- **Transmission map is a vertical list** — the spec's differentiator
  ("oil↑ → inflation↑ → rates↑ → energy wins / airlines lose") requires a
  branching DAG with winner/loser leaves; the current component cannot
  express one branch.
- **No probability-over-time chart** for Event views — Polymarket's signature
  visualization, and we already read Polymarket/Kalshi.
- Portfolio chart: axis-less thin line that *slopes down* under a green
  "+4.50% total return" headline — a self-contradiction on the money page.
- Strategy line chart floats context-free (no 0% baseline, no drawdown
  shading).
- Empty states render as fake data: "+0.0%" green dashed sparklines,
  "BACKTESTED · 1Y" chip beside "0 simulated trades", an all-grey P&L heatmap.

### 1.8 UI/UX vs Polymarket · Kalshi · Public · ElevenLabs — B-
Honest comparison: the foundation is **already in the right family**. White
chrome, black pill CTAs, serif display accents (the "Good Morning" and
Portfolio headers rhyme with Public.com's serif identity), border-only cards,
color reserved for meaning (profit/loss green/red) — that is the ElevenLabs
discipline (monochrome chrome, color as content). This is not AI slop.

Where we visibly lag the inspo set:
1. **Information density.** The Views gallery holds 3 sparse cards and dead
   whitespace below the fold; Polymarket/Kalshi lead with a dense,
   scannable grid where every card carries a probability, a sparkline, and
   volume. Our card carries two green numbers whose relationship is unclear
   ("+15.4% best past run" vs "+11.5%" — two returns, no label hierarchy).
2. **Empty-state craft.** Kalshi/Public never show a dashed +0.0% chart or a
   contradictory chip; they design the zero state. Our Agents tab reads
   unfinished for exactly this reason.
3. **Data consistency as design.** Three prices for one stock is a UX bug
   before it is a data bug — the user experiences it as "this site is making
   numbers up."
4. **Small chrome noise**: a repeated "Agent" pill on every card, a "10
   errors" dev toast in a demo path, filter-chip rows duplicating the
   sidebar filters, tab-content that isn't URL-addressable (`/views` 404s —
   nothing on the site is shareable/deep-linkable).
5. **Naming**: fun tier names ("Slow & Steady") are a genuine differentiator
   and match the calm voice — but cutesy names on -100%-max-loss/5%-POP
   products ("Tiny but Mighty 30K bet") undercut the honesty contract the
   rest of the product works hard for.

---

## 2. The phased plan

> Rule for every phase: each item names its evidence and its done-check.
> No item is speculative work; everything traces to a finding above.
> S/M/L ≈ days / 1–2 weeks / weeks.

### Phase 0 — Trust hotfixes (S; do before anything else)
The four things a sharp visitor would screenshot and tweet.

| # | Fix | Evidence | Done-check |
|---|-----|----------|-----------|
| 0.1 | Delete or regenerate the fabricated `rationale` on every pack view/tier (never ship copy-pasted HAC t-stats citing Britannia on an AI view) | strategy audit #1 | no two views share rationale text; no instrument named that isn't in the view |
| 0.2 | One price source of truth: watchlist chips, screener grid, portfolio LTP all read the same quote service, tagged with source+as-of; fix the `127.0.0.1`→`localhost` CORS mismatch; add a **batch** quotes endpoint (one call for N symbols) | HDFCBANK ₹1,643/₹1,718/₹796; console CORS errors | same symbol never shows two prices on one screen; zero CORS errors |
| 0.3 | Label all simulated money "Paper" in the top bar + portfolio; suppress the dev error toast outside dev | fresh-account ₹77,945 unlabeled | new account sees "Paper ₹—" not a fake net worth |
| 0.4 | nifty30k coherence: strikes must bracket 30,000 (+19–25%); one probability number across thesis/spec/forward (option-implied wins when readable) | strategy audit #2 | the aggressive tier profits if Nifty touches 30k; one p everywhere |
| 0.5 | Small-ticket honesty: floor POP (≥15%) or relabel "speculative lottery — not the view's entry"; never a rolled ticket shorter than the view horizon; exclude longshots from the view's min-entry headline | strategy audit #3/#4 | no sub-15%-POP ticket presented as an "entry"; no 42d ticket on a 126d view |
| 0.6 | Fix the dividend-yield unit bug surfaced in chat (TCS 6.25%) | chat screenshot | spot-check 10 names against NSE |

### Phase 1 — Chat: context + tool reliability (M)
The product IS the chat; these are the highest-leverage brain fixes.

1. **Wire View Markets into chat**: register view tools (list/get/express/
   deploy-view), router rules seeded from `detect_thematic_scenario` +
   `_POSITIONING_RE`, an `active_view` Redis slot, render-hints for View
   cards. Done-check: "show me views on rates" → view card; "deploy the
   conservative one" → position.
2. **Bridge the 6-turn cliff**: inject the (already-computed) `ChatSummary`
   as a context block on turns whose referent falls outside the window.
3. **Persist compact structured card snapshots** (per rendered card, longer
   TTL, addressable by index/symbol) so "change the strike" never
   reconstructs from prose.
4. **Port the M1 ask-user retry to the streaming path**, then extract the
   shared turn-loop so `handle`/`handle_stream` cannot drift again.
5. **Bounded self-correct retry** for mechanical validation failures
   (bad enum/type) on order + option tools — same pattern the workflow tools
   already have.
6. **Truncation honesty**: summarize oversize tool payloads with an explicit
   "partial data" marker instead of a silent 6,000-char cut.
7. **Router backstop**: when only the fallback floor matches, one cheap LLM
   classification pass before giving up on build-shaped verbs.
8. **One instrumented multi-turn eval run, committed** (tokens + latency +
   quality per item) so tool accuracy finally has a number. One run, fix,
   retest once — per house rules.

### Phase 2 — Round-trips & load time (S–M)
1. `/api/views`: collapse ~120 queries → ~5 (GROUP-BY counts, IN-batched
   confidence/expressions, one follow-set query). Also stop the detail
   route double-loading what `_build_summary` fetched.
2. `conversations`: `selectinload` or count+lateral-preview → 2 queries.
3. Paper marks: one `get_live_quote` for all equity symbols, one
   `IN (...)` instrument resolve, thread the session (no per-symbol
   `SessionLocal()`). Fixes the EOD snapshotter multiplicatively.
4. **Query-count middleware** (`before/after_cursor_execute` → per-request
   query count + DB ms in the existing request log) — the reason none of
   this was visible.
5. FE: move the 925KB pack JSON server-side (RSC or fetch), `next/dynamic`
   the chart components + `optimizePackageImports`, SWR
   (stale-while-revalidate) for GETs, visibility-gate the 30s poll.

### Phase 3 — Data foundation (M; parallelizable, mostly free sources)
Ranked by impact-per-effort from the data audit:
1. **Option IV history** — snapshot our own live ATM IV daily (we already
   compute it); unlocks IV-rank ("is protection cheap?") which the code
   currently hard-disables. (S)
2. **Futures OI + basis** — flip `oi=True` for FUT tokens + daily snapshot;
   NSE F&O bhavcopy backfill. (S–M)
3. **Index constituents + weights** (niftyindices CSVs) — honest benchmarks
   and betas for every Relative view. (S–M)
4. **Corporate-actions adjustment table** — protects every backtest from
   unadjusted split jumps (this is a *correctness* item, not enrichment). (M)
5. **Sectoral indices + India VIX history** — vol-regime conditioning for
   event studies and option widths. (S)
6. **MCX spot history** — completes the gold/crude views end-to-end. (S–M)
7. Then: delivery % (conviction filter), FII/DII actual flows, bulk/block
   deals. Defer: shareholding time series, earnings estimates (L, revisit
   after the above land).

### Phase 4 — Strategy content quality (M)
1. **Tier-label gates**: "Conservative" requires breadth (≥5 names) + a
   drawdown meaningfully better than the aggressive tier; no_edge tiers get
   demoted or relabeled — never "Calm & Cushioned".
2. **Meaningful satellites**: ≥20% of the ticket in the basket's actual
   top-weight names, else drop the core-satellite framing and say "ETF proxy
   + optional tilt".
3. **Gold view honesty**: GOLDBEES/MCX *is* the conservative expression;
   the jeweller basket is relabeled "gold-adjacent equities (weak gold
   beta)".
4. **Forward-model asymmetry**: justify move_yes/move_no asymmetry per view
   or default symmetric so p=0.5 → ~0 expectation and the band carries the
   message.
5. **Copy pass**: conditional framing ("a call spread would capture the move
   if…") replaces imperative stance lines; keep fun names off sub-10%-POP /
   -100%-max-loss structures.

### Phase 5 — Visual & UX finish (M)
1. **PayoffDiagram**: annotate max P/L, both breakevens, POP, net debit, and
   a current-spot line (+ compact payoff table).
2. **Transmission DAG**: SVG node-link with branching and colored
   winner/loser leaves — this is a spec differentiator, currently
   unexpressible.
3. **Priced-in probability-over-time** line for Event views (we already
   ingest Polymarket/Kalshi).
4. **Portfolio chart**: axes, benchmark ghost line, and reconcile the curve
   with the headline return (the down-sloping "+4.5%" is a bug either way).
5. **Strategy charts**: 0% baseline + max-drawdown shading.
6. **Designed empty states**: kill +0.0% dashed sparklines, the
   "Backtested·1Y / 0 trades" contradiction, and the all-grey heatmap;
   replace with explicit zero states.
7. **Density pass on Views gallery** toward Polymarket-grade scannability:
   one labeled headline stat per card, tighter grid, no dead columns.
8. **Routable tabs** (`/views`, `/portfolio`, …) so every surface is
   deep-linkable/shareable; de-dup redundant chips ("Agent" on every card).

### Explicitly NOT in this plan (avoiding unnecessary work)
- No design-system rewrite — the visual language is right; it needs
  consistency and density, not replacement.
- No move to async SQLAlchemy or a different ORM — batching + telemetry
  solves the observed problem at ~62ms RTT.
- No react-query migration of the whole app — SWR on GETs only.
- No new chart library — recharts + lightweight-charts cover the inventory;
  the gap is annotations, not tooling.
- No shareholding/estimates ingestion yet (L-effort; sequenced after the
  S/M data wins prove out the pipeline pattern).

---

## Appendix A — Ranked data-gap table (from the data audit)

| Rank | Dataset | Unlocks | Source | Effort |
|------|---------|---------|--------|--------|
| 1 | Option IV history + IV rank | honest "IV cheap/rich", removes a shipped boundary | snapshot own chain | S |
| 2 | Futures OI + basis | trend confirmation, carry costs | Kite `oi=True` + NSE bhavcopy | S–M |
| 3 | Index constituents/weights | honest benchmark/beta for Relative views | niftyindices.com | S–M |
| 4 | Delivery % | conviction filter (screener + evidence) | NSE delivery bhavcopy | M |
| 5 | FII/DII + MF flows (numbers) | flow-driven macro views | NSE/CDSL + AMFI | M |
| 6 | Corporate actions table | backtest price integrity | NSE bhavcopy / yfinance actions | M |
| 7 | Sectoral indices + VIX history | rotation views, vol-regime conditioning | niftyindices / yfinance | S |
| 8 | Results history + estimates | earnings-surprise views | yfinance + MC scraper (estimates hard) | M–L |
| 9 | Shareholding pattern TS | pledge risk, promoter buying | NSE/BSE filings | L |
| 10 | Yield curve / OIS / INR forwards | rigorous rate-cut + INR views | RBI/FBIL/CCIL | M |
| 11 | MCX spot history | crude/gold views end-to-end | MCX bhavcopy / yfinance | S–M |
| 12 | Bulk/block + insider deals | unusual-activity evidence | NSE/SEBI daily | M |

## Appendix B — Where the audits sit
Full agent reports (chat brain, DB, data, strategies, FE) are in the session
transcript of 2026-07-03; every claim in §1 carries its file:line there.
