# Execution layer: final plan (v2, stress-tested)

2026-10-22. This replaces v1 (d458f831) of this file. v1 answered two
questions: how much the model does on its own, and how conditions work
without a fixed vocabulary. v2 tests that answer against three things: what
broke last time, what the 2025-26 research says breaks agent harnesses, and
the actual code. It then fixes what failed and sets out the harness.

**Verdict: the architecture holds, with eleven revisions (§3).** Seven of them
come from weak points in v1 itself. The old system failed because of
**structure**:
- two engines;
- a borrowed 36k-token adapter;
- a second translator model;
- three fill rules for one sentence;
- no validation at arm time;
- one shared position per symbol.

This design removes each of those by construction rather than by prompt
rules. It would fail the same way only if we break the hard rules in §6:
borrowing the old tools, adding a second model in the loop, or letting a card
render something that isn't in the store.

The one idea everything rests on:

> **The model decides what things mean, on a slow clock. Code decides when
> they happen and what is allowed, on a fast clock.**

---

## 1. What broke last time, and what prevents it now

| Old failure (source) | Root cause | What prevents it in this design |
|---|---|---|
| ~36k-token borrowed adapter, 190 s turns, 15% failed calls (`EXECUTION_MODE_GRADE_2026_09_21`) | Pivot's tools lent into another runtime, carrying rules for tools that weren't there | Our own tools, written for this runtime. Nothing is imported from `execution_bridge` or `pivot/backend/workflows`. |
| "how many shares?" with **0 strategies saved** | A parameter doc said ASK_USER, and ASK_USER wasn't on the wire | Defaults live in our own parameter docs ("size defaults to ₹10k; say so"). Never ask first. The wire is checked for names of tools that aren't on it. |
| A 20-day EMA silently became a 50-day one | A second LLM re-translated the rule on every edit | One model writes the Play JSON directly. The edit result carries a structural diff (`draft_delta`). If the structure changed, the play drops to `propose`. |
| Backtest, alerts and live strategies used **three different fill rules** | `strategies.on_bar` ignored `closed`; the backtest was a separate engine | **One runtime.** The backtest *is* that runtime replayed over history, and every rule names its bar. |
| 47% of armed rules sat on symbols with no bars; `RSI>70 AND RSI<30` armed fine | No validation when a rule was armed | Every input must resolve to fresh data, and a **historical fire count** is computed at compile time. "Would have fired 0 times in 2 years" or "fires every day" is shown before anything is armed. |
| An exit sold shares that belonged to another rule; a duplicate arm doubled the size | `paper_positions UNIQUE(account_id, symbol)`; no idempotency | Each play has its own sub-ledger and can exit only its own quantity. Idempotency key `(play, bar_ts, leg)`. Identical plays are de-duplicated by hash. |
| A single global bar hook silently replaced the alerts hook | Hook stored in one variable | Already fixed (`register_bar_hook` is a list). Plays add one more subscriber. |
| "Saved" shown with no store behind it; journal writes it couldn't make | Card text came from the model | **A card renders only from the stored row.** The tool returns an id, and the UI reads the row. |
| Research chat had no path to an order | Separate Research and Execution modes | One chat. Execution is two tools in the same harness. |
| Three data planes (backtest 21 Sep, chart 2 Sep, screener 22 Jul) | Different stores per feature | Measures, backtest and live all read `ds.get_bars`, and every output carries `as_of`. |

## 2. What the research says breaks agent harnesses, and our answer

| Failure class | Evidence | Our answer |
|---|---|---|
| **Compounding steps.** 10 steps at 95% each succeed only ~60% of the time | [Openlayer](https://www.openlayer.com/blog/ai-agent-failure-modes-tool-calling-loops-propagation), [Atlan](https://atlan.com/know/agent-harness-failures-anti-patterns/) | A typical build is **1 tool round plus the answer**: `play` takes measures inline. Code validates between steps. |
| **Silent tool failures** (HTTP 200 with an empty body) and the agent carrying on | [Arize](https://arize.com/blog/common-ai-agent-failures/) | Missing data makes the value UNKNOWN, so the play holds and the card names what is missing. Every error says what to fix, with an example ([Anthropic, tools](https://www.anthropic.com/engineering/writing-tools-for-agents)). |
| **Too many tools.** Our own bench: the tool surface, not the prompt, costs ~2.9 s TTFT per round; 10 strict tools save ~2.1 s per round | `project_chat_tool_latency_bench_2026_10_18` | The tool count **stays at 10**: `strategy` and `alerts` are replaced by `play`, and `measure` is added. |
| **Context rot.** Long, stale tool results; "lost in the middle" | [Anthropic, context](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents) | Tool results are shaped (an id, the current value, ≤5 points, problems). Series go to the chart, not the model. Stale tool results are cleared first. The judge and the planner run in **fresh, tiny contexts**, never in chat history. |
| **Information injection beats prompt injection.** Fabricated news worked 62% of the time vs 14% for prompt injection; credibility cues ("BREAKING", a named source) drove it; only hierarchical aggregation helped | [Fake News, Real Crashes, arXiv 2610.26438](https://arxiv.org/abs/2610.26438) | A judge has **no tools and returns a typed fact**, never an action. Code binds the source by account id, not by display name. Cue words are stripped before judging. A judge alone can never trigger `act`: it needs a primary-source match (an NSE/BSE filing) or price confirmation. This is the hierarchical-aggregation pattern. |
| **Lookahead in model-written code.** A leaky strategy with a Sharpe ratio of 35 passed Deflated Sharpe; "prompting to avoid lookahead is weak, make the environment causal" | [arXiv 2608.27734](https://arxiv.org/pdf/2608.27734) | Inputs are **point-in-time by construction**: bars are cut at t, and SQL inputs must name a `known_at` column (no `known_at` means live-only, never backtested). The validator runs the existing **truncation (repaint) test** from `custom_indicators`. |
| **Search inflates false edges** | same | Every measure or play variant that gets backtested increments the trial counter, so the trust ladder deflates for it (and `num_trials:1` is no longer stuck). |
| **Loops and runaway cost** | [Openlayer](https://www.openlayer.com/blog/ai-agent-failure-modes-tool-calling-loops-propagation) | Round cap (existing). Daily judge budget per user. Code pre-filters before any model is called. Judgments are cached per `(judge, event)`. |
| **Guardrails that live only in prompts** | All of the above | Mandate, escalation, idempotency and causality are all code. The prompt only describes intent. |

## 3. Revisions to v1 (weak points found while stress-testing it)

1. **The paper book can't serve the day-trader half.** `paper.py` is
   *long-only delivery* (`_NO_FUTURE` refuses NFO, MCX and futures), with one
   position per symbol. Straddles, intraday shorts, MIS square-off and futures
   need **a new intraday/derivatives paper book**:
   - play-scoped lots;
   - short positions;
   - option and futures legs with lot sizes;
   - margin from Kite's basket-margin API instead of our own SPAN model;
   - auto square-off at 15:20.

   This is the largest single build for the day-trader half. `paper.py` stays
   as the delivery book.
2. **Too many rounds.** v1 needed `measure.define` → `play.compile` → answer,
   about 3 rounds × ~8 s. Now `play` accepts **inline measures** (promoted to
   the library when the play is saved), so a typical turn is 1 tool round plus
   the answer.
3. **Too many tools.** v1 added 4; now it's 2 inside chat v2 (§4).
   - A **judge** becomes `measure` with `kind:"judge"`.
   - A **custom security** becomes `measure` with `as_symbol:"@DEFENCE"`: a
     NAV measure published as a chartable, alertable symbol.
4. **Lookahead.** Point-in-time inputs, `known_at` on SQL, and the
   truncation test (§2).
5. **Information injection.** The judge's isolation and corroboration rules
   (§2).
6. **Late fires after a restart.** `alerts.catch_up` fires with `late=1`. A
   late fire **never trades**: it drops to notify, with "missed while
   offline".
7. **The historical fire count** becomes the universal compile-time check. It
   catches impossible rules, too-loose rules, wrong units and dead symbols
   with one mechanism.
8. **The policy planner** runs as a fresh, small context: objective, mandate,
   holdings, the referenced measures and the last 3 receipts. It returns plan
   JSON. If the structure changes, it drops to `propose`.
9. **Sandbox throughput.** Measured at 33 ms per process, and 500 jobs in one
   process also take about 33 ms. Use **persistent workers partitioned per
   user**, evaluate by clock batch, and recycle after N runs.
10. **Fills for a rule that names a closed bar** happen at the next bar's
    open, in replay and live alike. This closes the one-bar gap the old
    system left between backtest and live.
11. **Kite token expiry (~6 AM).** Live-data plays go UNKNOWN and the account
    shows "data paused, re-login". They never fire on stale or yfinance data
    without escalating.

---

## 4. The harness: the same recipe as chat v2

Chat v2 (2026-10-18) is the proven recipe:
- 10 consolidated tools that fan out to engine functions;
- a ~1.8k-token prompt first, live context after;
- flag-gated (`harness:"v2"`);
- a live blind A/B against v1 that v2 won 21-7: p90 25.7 s vs 57.6 s, input
  tokens 30k vs 79k per turn, cost down 32%.

The execution layer is **built into that harness**, not alongside it.

### 4.1 The chat (one model, 10 tools)

`read_chart · draw · workspace · market_data · search · query · screen · journal · measure · play`

(`alerts` and `strategy` are retired. An alert is a Play with authority
`notify`.)

**`measure`** has actions define, test, list and get.
- `define` takes `{title, formula, inputs, code, output, clock, check?, kind?: "value"|"judge", as_symbol?}`.
- Input kinds: `bars · sql(known_at) · chain · events · judge · measure · holding`.
- The description is about 900 tokens. It holds the compute contract, the
  input kinds, one example, and "if you don't know the formula, search first".
- The result is `{id, now, as_of, last_5, unit, problems[], chart_card}`. The
  backfilled series goes to the card, never into the context.

**`play`** has actions compile, edit, pause, resume, list, explain and kill_all.
- `compile` takes `{title, contract, mandate, when, do, manage, settle?, measures?: [inline]}`.
- The parameter docs hold the dial defaults ("no schedule named → `now` /
  `once_when`; 'go ahead' → one step up; ambiguous → shortest lifespan and
  lowest authority, and offer the upgrade"). They do not live in the prompt.
- The result is `{id, readback, contract_line, fires_hist:{n, per_month, last}, base_rate?, diff?, problems[]}`.
  The card renders from the stored row.
- Large free-form objects (`when`, `manage`) are sent as JSON strings and
  validated on the server with actionable errors, because strict mode rejects
  free-form objects. `strict:false` is used, per the deferred-function trap.

The **prompt** gets about 6 lines on execution: the slow/fast-clock rule,
"build, don't ask", "quote the card", and "real money = propose".

**Live context** goes after the prompt, so the cache holds: the focused
symbol, the user's autonomy profile, and the active play count. Never the full
play list; that comes from `play list` when needed.

### 4.2 Two background model callers, never in chat history

| Caller | Context | Tools | Output |
|---|---|---|---|
| **Judge** | ~300-token brief + one event, source identity added by code, cue words stripped | **None** | `{verdict, value?, confidence, quote}`; no quote means unknown |
| **Planner** (policy plays, weekly or on drift) | Objective + mandate + holdings + the referenced measures + 3 receipts (~2k tokens) | `query`, `measure test` (read-only) | Plan JSON. Code sizes the orders; the mandate is checked; structural change drops to `propose` |

"Explain this fire" runs lazily when the user opens a receipt, not on every
fire.

### 4.3 Measurement (the same discipline as v2)

- **One instrumented, multi-turn live run** per step, with the triad
  (tokens, latency, quality). Fix the problems, retest once.
- Labelled sets:
  - 30 autonomy-contract prompts across trader types;
  - 15 measure asks;
  - 10 fabricated-news injections (BREAKING / named source / blended);
  - a replay-determinism check (replay twice → identical receipts).
- A/B against the current `strategy`/`alerts` tools behind `harness:"v2x"`,
  blind-judged, before anything ships.

## 5. The runtime (code only)

```
Sources ─► Event store ─────────────┐
Kite/crypto ticks ─► bars (exists) ─┼─► Measure engine (persistent sandbox workers, clock batches, as_of)
                                    │        │
Judge worker (no tools) ◄── prefilter ◄──────┘
                                    ▼
Play runtime (alerts.py lineage: crossing state, closed-bar ctx, catch-up)
  watching → fired → proposed | acting → in position → managing → settled
                                    │  escalation: first fire · low confidence · limit · slippage
                                    │              · stale/non-Kite · structural plan change · late
                                    ▼
Mandate check (code) ─► Router: notify │ ticket │ paper (delivery │ intraday/derivatives) │ broker-native exit
                                    ▼
Receipts (inputs, measure values, judge quote, mandate check, action) ─► journal, card, explain
```

Backtest is this same runtime replayed over history, with the trial counter
and trust-ladder verdict reused.

## 6. Hard rules (break one and we are back to the old system)

1. Never import `execution_bridge`, `strategies.py` runtime, or Pivot workflow
   tools and schemas.
2. Never put a second model in the compile path. The only model calls outside
   chat are the judge and the planner, both tool-less or read-only.
3. A card renders only from a stored row. A tool never returns "saved"
   without an id.
4. The model never writes a price, a size in shares, or a timestamp. It
   writes addresses, measures and ₹ budgets; code converts them.
5. Every input is point-in-time. No `known_at` means no backtest.
6. A judge never causes `act` alone.
7. Late, stale or non-Kite data never trades. It escalates.
8. Real money stops at `propose` (plus broker-native exits) until we are
   empanelled with NSE as an algo provider.
9. Behaviour fixes go into **tool and parameter docs**, not the prompt.
   Every fix should make the wire smaller.

## 7. Build order (each step ships behind the flag, with its eval)

| # | Step | Lines (est.) | Done when |
|---|---|---|---|
| 1 | **Measure engine**: `measure` tool, validator (policy, determinism, NaN, units, truncation, known value), backfill + chart card, persistent workers, indicators as library measures | ~1,200 | 15 varied asks ("HDFC/ICICI P/B", "NIFTY PCR", "3-day FII streak", "basket NAV") correct with **zero backend edits**; eval ≤ 1 tool round on 80% |
| 2 | **Plays**: `play` tool, the three dials, mandate, escalation, receipts, historical fire count; runtime on the `alerts` lineage; notify + delivery-paper routes; play-scoped lots | ~1,500 | ≥ 90% contract inference on 30 labelled prompts, 0 silent upgrades, replay determinism |
| 3 | **Custom securities v1** (`as_symbol`): baskets, ratios and pairs as symbols, with NAV, alerts and share links | ~500 | "Make me a defence basket and alert me when it breaks its 50-DMA" works end to end |
| 4 | **Event store + judges**: NSE/BSE filings, SEBI/RBI/PIB, RSS, X watchlist; funnel; cue stripping; corroboration | ~1,200 | A real filing fires with a quote and logged latency; injection set reaches `act` 0/10 |
| 5 | **Intraday/derivatives paper book + day-trader management**: short positions, option/futures legs, Kite basket margin, square-off, bracket, trailing, OCO, time exit, opening range, straddle, re-entry, P&L exit | ~2,000 | A replayed day's 09:20-straddle fills match the backtest bar for bar |
| 6 | **Policy plays + planner** | ~600 | "Rebalance it, go aggressive" does it once, offers the chip, and the chip turns it into a weekly policy |
| 7 | **Real-money proposals**: pre-filled tickets; Dhan Super Order / Kite GTT OCO exits when the user confirms | ~600 | A confirmed entry places its exit at the broker in the same action |

About 7.6k new lines. Retiring the old strategy runtime, bridge, `triggers/`
and the v1 `alerts`/`strategy` tools removes more than that. When we're done
the codebase is smaller.

## 8. Risks that remain (stated, not hidden)

- **The model can write a wrong formula** that passes validation, such as
  book value per share in the wrong unit. The known-value check and the unit
  guard reduce this but don't remove it, so the formula is always shown on
  the card.
- **Interpreting the autonomy contract will be wrong sometimes.** We default
  to the safe side and offer one-tap upgrades, so a wrong guess costs a tap,
  not money.
- **Event latency.** We will be seconds to minutes behind HFT on posts. The
  product's value is interpretation, base rates and confirmation, not speed.
- **One VM core** limits measure throughput. Persistent workers and clock
  batching should hold to hundreds of live plays. Step 2's eval measures it.
- **Regulation.** Paper trading on live prices needs our data licence
  confirmed. Unattended real-money trading needs empanelment.

## 9. Open decisions (none blocks steps 1-3)

1. Which app is "Listen" (send the X handle)?
2. The custom-securities description, which didn't arrive in the message.
3. US stocks and perps in scope? (This changes CLAUDE.md's India-only rule.)
4. Start NSE empanelment and get a legal opinion now?
5. The X API watchlist budget (~$300 a month) — needed by step 4.
