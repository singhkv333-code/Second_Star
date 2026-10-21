# Execution layer: autonomy, and conditions with no fixed vocabulary

2026-10-22. This document answers two design questions for the new execution
layer. It builds on the research report from the same day: competitors,
SEBI rules, broker APIs and the reuse map.

1. **Autonomy.** How much does the user want the model to do, and does "rebalance
   it and go aggressive" happen once, keep happening, or something in between?
2. **Open conditions.** How do we stop building a backend for every ratio,
   spread, filter and settlement rule, and let the model define any condition
   itself, safely?

Both answers rest on one principle that runs through the whole design:

> **The model decides what things mean, on a slow clock. Code decides when
> they happen and what is allowed, on a fast clock.**

- The model interprets intent, writes formulas, judges unstructured text and
  re-plans policies. These steps take seconds to days.
- Code evaluates conditions on every bar or event, enforces limits, routes
  actions and keeps state. These steps take milliseconds, and the same inputs
  always give the same result.
- That split keeps the platform flexible (no fixed vocabulary) and honest
  (backtest and live trading run the same compiled thing).

---

## Part 1: Autonomy is three separate dials

"Autonomy" mixes three independent questions. Each Play answers all three
explicitly and shows the answers on its card.

| Dial | Question | Values |
|---|---|---|
| **Lifespan** | How long does this instruction live? | `now` (do it once, immediately) · `once_when` (wait for a condition, act once, done) · `standing` (act every time the condition recurs, until it expires or hits a cap) · `policy` (maintain an objective continuously, e.g. "keep it rebalanced") |
| **Authority** | Who pulls the trigger when it fires? | `notify` (tell me) · `propose` (pre-filled card or ticket, one tap) · `act` (do it within the limits, then tell me) · `manage` (act, and also modify or close what it opened) |
| **Discretion** | How much does the model decide *at fire time*? | `none` (compiled rule; code only) · `judge` (the model only classifies unstructured input such as a post or filing, with a quoted reason) · `plan` (the model chooses the action, for example new weights, within the limits) |

The dials are independent:

| Example | Lifespan | Authority | Discretion |
|---|---|---|---|
| "Alert me if HAL tweets about an order win" | `once_when` | `notify` | `judge` |
| A day trader's trailing stop | `standing` | `manage` | `none` |
| "Keep my defence basket rebalanced, aggressive" | `policy` | `propose` | `plan` |

### How the dials are set from what the user says

The model sets them, not keyword matching. Each dial records the user's words
that set it (`inferred_from`) and a confidence. The defaults live in the
**tool's parameter documentation**, not the system prompt, because a parameter
doc sits closer to the call.

- "buy X if Y", "when X happens" → `once_when`
- "whenever", "every time", "each day" → `standing`
- "keep it", "maintain", "manage", "hedge it", "stay at" → `policy`
- "tell me", "ping me", "alert" → `notify`
- "go ahead", "just do it", "don't ask" → authority one step up, capped by
  limits and by the money rail (below)
- Wording that names nothing → the user's **autonomy profile** (set once at
  onboarding and editable)

**The ambiguity rule is: shortest lifespan, lowest authority, and offer the
upgrade in one tap. Never ask first.**

Worked example. "Rebalance it and go aggressive":
- "Rebalance it" names no schedule, so the lifespan is `now`.
- "Go ahead" raises the authority to `act` on paper, or to `propose` with
  real money.
- "Aggressive" is not an autonomy setting. It is a **parameter of the
  objective**: a risk tilt the planner turns into target weights, such as
  momentum-weighted, a 25% cap per name and no cash buffer.
- The card does the rebalance once and then offers one-tap chips: **[Keep it
  this way · weekly]** and **[only when drift > 5%]**. Choosing one turns the
  same Play into a `policy`.

The upgrade is never applied silently, and the downgrade (pause, or back to
propose) is always one tap.

### What the user sees on the card

Every Play card shows a one-line contract with editable chips:

```
Runs: every time it triggers · Acts: proposes, you confirm · Limit ₹50,000 · Ends 30 Nov
```

### Mandate: hard limits enforced by code

Authority of `act` or above needs a **mandate**. The model cannot widen it.
It changes only through UI controls, or a confirm card that the user taps.

- Per-play mandate: capital, maximum per order, maximum fires, expiry,
  allowed instruments and segments, allowed hours.
- Account-wide mandate: maximum daily loss, maximum open plays, maximum gross
  exposure, F&O selling allowed yes/no, and a kill switch that pauses
  everything.

Every action passes a pre-trade check in code and leaves a receipt: inputs,
measure values, judge evidence, the mandate check, and what was done.

This is the gap in Robinhood Agents, where the account ceiling is the only real
gate once approvals are switched off. Our limits apply per play and
account-wide, every action is checked first, and every action leaves a receipt.

### Escalation: a play acting on its own still stops to ask

This follows the "Approver" level (L4) from Feng, McDonald and Zhang, *Levels of
Autonomy for AI Agents* (2025). Even with authority set to `act`, a play drops
to `propose` for that one fire when:

1. it is the play's first fire;
2. judge confidence is below the play's threshold;
3. the action would breach any limit;
4. price has moved beyond the slippage band since the trigger;
5. data is stale (`as_of` more than 2 bars old), or the source is not Kite;
6. the model's plan changed the structure, not just the parameters. This is
   detected by a structural diff (`draft_delta`), never by a regex.

### Risk-reducing actions get more autonomy than risk-increasing ones

- Exits, stop-losses, square-offs and hedges may run at `manage` even when
  entries are `propose`. A stop-loss that waits for a tap is not a stop-loss.
- With real money this is handled **without unattended algo execution**. When
  the user taps to confirm an entry, the exit goes to the broker as a native
  order in the same action: a Dhan Super Order with a trailing jump, or a Kite
  GTT OCO. The user placed both, and the broker holds the exit.

### Autonomy is earned

- A new Play starts at `propose`.
- After N proposals accepted unedited, or a paper run of M fires, the card
  offers to step up: "let it act within ₹Y".
- Edits and rejections reset the counter.

### The money rail caps authority (decided by regulation, not by product)

| Book | Maximum authority | Why |
|---|---|---|
| Paper | `manage` | It's a simulation |
| Real money, v1 | `propose`, plus broker-native exits | The user places every order; no unattended execution |
| Real money, after empanelment | `act` / `manage` | Exchange algo ID, broker partner, white-box strategies |

The dials and the product are built in full now; only the cap moves later.

### Policy plays (continuous objectives)

- A policy has a cadence or a drift trigger, for example weekly, or when any
  weight drifts more than 5%.
- On each fire the **planner** runs the model on the slow clock. The model
  reads measures, may define new ones, and returns target weights or orders
  with a reason.
- Code turns targets into orders: lot sizes, costs and the mandate check. The
  result goes through the normal router at the play's authority.
- The planner never runs on a tick.

---

## Part 2: Measures, conditions with no fixed vocabulary

Any fixed condition vocabulary will be missing something:
- the alerts address grammar;
- the DSL node types;
- a ratio, a spread, P/B relative to the sector median, PCR, a three-day FII
  selling streak, a custom security's NAV, a settlement payoff.

So the platform has **one primitive the model authors itself**.

### Measure

A **Measure** is a named, versioned function over declared inputs. The model
writes it, a validator checks it, and the runtime evaluates it in the sandbox.

```jsonc
{
  "id": "M:pb_rel_hdfc_icici@3",          // name@version; the version is the hash of inputs + code
  "title": "HDFC Bank P/B ÷ ICICI Bank P/B",
  "formula": "(close_H / bvps_H) / (close_I / bvps_I)",   // plain English/maths, always shown
  "inputs": {                                // DATA ADDRESSES the runtime can fetch live and historically
    "close_H": {"bars": {"symbol": "HDFCBANK", "interval": "1d", "field": "close", "lookback": 300}},
    "close_I": {"bars": {"symbol": "ICICIBANK", "interval": "1d", "field": "close", "lookback": 300}},
    "bvps_H":  {"sql": "SELECT ... FROM quarters WHERE symbol='HDFCBANK' ...", "refresh": "1d"},
    "bvps_I":  {"sql": "SELECT ... FROM quarters WHERE symbol='ICICIBANK' ...", "refresh": "1d"}
  },
  "code": "def compute(data, params):\n    ...",   // sandboxed; same contract as the query tool
  "output": {"kind": "series", "unit": "ratio"},  // scalar | series | bool ; unit feeds the magnitude guard
  "clock": "bar:1d",                         // bar:<iv> | event:<stream> | schedule:<cron> | on_demand
  "check": {"at": "2026-10-20", "expect": 1.3, "tol": 0.1, "source": "company_profile.pb"},
  "provenance": {"author": "model", "conversation": "...", "validated_at": "...", "sources": ["url"]}
}
```

**Input kinds.** These are the only things the runtime must know how to
fetch. Everything else is the model's arithmetic.

| Kind | Fetches | Live path |
|---|---|---|
| `bars` | OHLCV for any symbol or interval. Several symbols are aligned causally (`custom_indicators.align_basket`) | `ds.get_bars` (memory, forming bar dropped for closed-bar clocks) |
| `sql` | One read-only SELECT on the market DB (fundamentals, quarters, deals, results, pattern stats) | Cached per `refresh`. **Not allowed on clocks faster than 1 minute** |
| `chain` | Option chain field by underlying, expiry and strike selector (ATM±k, delta) | Kite overlay plus bhavcopy |
| `events` | Counts, recency and fields from a normalised event stream (filings, posts, news, macro), with a filter | Event store |
| `judge` | The output series of a Judge (below) | Judge cache |
| `measure` | Another measure (composition) | Measure engine |
| `holding` | A paper position or custom security's quantity, cost or P&L | Paper book |

**How a Measure is born.** This makes "the model makes any ratio itself" real:
1. **Explore** with `query` (SQL plus Python, already live in chat v2).
2. If the formula isn't known, **search** for it (web search is on every turn),
   e.g. the definition of a Piotroski F-score.
3. **Define** it: the model calls `measure.define`. The validator then:
   - checks the code against the sandbox's AST and policy rules;
   - runs it over the full lookback and backfills history;
   - checks it is deterministic (two runs give identical output);
   - counts NaN, None and inf values;
   - checks the unit's scale (the magnitude guard from `alerts`);
   - checks the latency budget;
   - checks it against a **known value** where one exists (`check`), for example
     a P/B we already store.
4. The validator returns the current value, a backfilled series (so the measure
   is **chartable and backtestable at once**), and any problems found, which the
   model fixes and resubmits.
5. Saved measures are reused: library measures are shared, user measures are
   private by default, and duplicates are found by `(inputs, code)` hash.

**White box by construction.** Every Play is formulas the user can read and
code they can open. That matches SEBI's white-box category and the
"evidence you can check" rule.

**Built-ins are just measures.** The 48 indicators, patterns and volume-profile
levels become pre-written library measures. One evaluation path, not two.

### Judge: the measure's sibling, for things code cannot read

A Judge is a model-evaluated predicate over unstructured input, for example
"is this post bullish for HAL?" or "does this filing announce an order above
₹500 cr?"

```jsonc
{
  "id": "J:hal_order_win@1",
  "question": "Does this announce a new order or contract for HAL? If so, what is its value in ₹ crore?",
  "over": {"events": {"streams": ["nse_filings", "x:@HALHQBLR", "rss:et_markets"], "match": ["HAL", "Hindustan Aeronautics"]}},
  "returns": {"verdict": "bool", "value_cr": "number|null", "confidence": "0-1", "quote": "verbatim"},
  "funnel": ["prefilter", "small", "strong_if_positive"]
}
```

- **Prefilter (code):** entity and keyword match, so most events never reach a
  model.
- **Small model:** classifies what's left. TypeSafe-style typed judgments are
  a candidate here.
- **Strong model:** confirms positives and **must return a verbatim quote**.
  If it gives no quote, the judge returns unknown, which never fires. This is
  the `macro_events/verifier.py` rule.
- Results are cached by event ID, so a backtest replays the same judgments it
  made live. Judge outputs are series, so measures can use them, e.g. "bullish
  posts in the last hour".

### Settlement is a measure too

- A custom security's NAV, a payoff at expiry, a play's P&L attribution and a
  maturity value are all measures.
- Settling is a scheduled trigger that evaluates the settlement measure and
  books the result in the paper book.
- No settlement code per product.

### Conditions shrink to a tiny grammar

`Play.when` is a boolean tree over measures, judges and time. It uses a few
operators that the runtime owns because they need state:
- `above` / `below` / `between`;
- `cross_up` / `cross_down` (side persisted per condition, as in `alerts.cstate`);
- `enters` / `exits`;
- `for N bars`;
- `within T of` (sequence and confirmation, e.g. "judge true, then price holds
  above VWAP for 2 bars within 10 minutes");
- `at` (a time of day or session event).

Anything else is a measure that returns a bool.

### Performance: compile once, evaluate fast

- The sandbox starts a process per run: measured at about 33 ms, and 500 jobs
  in one process also take about 33 ms. That is fine for define, backfill and
  slow clocks. On the 1-minute clock, with hundreds of plays on a one-core
  machine, it is not.
- Fix: **long-lived sandbox workers.**
  - Each worker holds compiled measures, partitioned per user for user code.
  - Bindings are fetched once per clock tick and shared.
  - Measures on the same clock are evaluated in one batch.
  - Workers are recycled after N evaluations or on a memory high-water mark.
  - The isolation layers stay the same: AST allowlist, audit hook, rlimits,
    no environment, uid 65534.
- Only measures that a live Play references are evaluated. Charts evaluate on
  demand and cache, as custom indicators already do.

### Missing and stale data

- A binding that returns None makes the measure None, and the condition
  UNKNOWN, so the play **holds** (the three-valued evaluator rule).
- Each output carries `as_of` = the oldest of its inputs. Acting on stale data
  escalates to `propose`, with the reason shown.

---

## Part 3: The architecture

```
 CHAT (one model, a few tools, slow clock)        RUNTIME (code, fast clock, no model on the hot path)
 ────────────────────────────────────────         ─────────────────────────────────────────────────────
 query            explore (exists)                 Sources ─► Event store (filings, posts, news, macro)
 measure.define   write a formula ───────► Measures ─► Measure engine (sandbox workers, clocks, cache)
 judge.define     write a question ──────► Judges   ─► Judge funnel (prefilter → small → strong+quote)
 play.compile     intent → Play ─────────► Plays    ─► Play runtime (alerts.py lineage):
 play.edit / pause / explain                            watching → fired → (proposed | acting)
                                                         → in position → managing → settled
                                                           │ escalation rules
                                                           ▼
                                                        Mandate check (code) ──► Router:
                                                           notify · ticket · paper fill · broker-native exit
                                                           │
 planner          policy re-plan ◄── schedule/drift ───────┤  (slow clock only)
 explain          why it fired ◄──── receipt ──────────────┘  receipts / journal
```

**The tool surface is four tools.** The model already has `query` and web
search, and the old strategy tool is retired.
- `measure`: define, test, list
- `judge`: define, test
- `play`: compile, edit, pause, resume, list, explain
- `custom_security`: create a basket, ratio or payoff template. This is sugar
  for "a measure that is a NAV, plus a symbol".

### Play object (abridged)

```jsonc
{
  "id": "P:...", "title": "...", "readback": "plain-English, generated from the tree",
  "contract": {"lifespan": "once_when", "authority": "propose", "discretion": "judge",
               "inferred_from": "buy HAL if they announce a big order", "confidence": 0.8,
               "expires": "2026-11-30"},
  "mandate": {"capital_inr": 50000, "max_order_inr": 25000, "max_fires": 1, "hours": "09:15-15:20"},
  "when": {"all": [{"j": "J:hal_order_win", "is": true, "where": "value_cr >= 500"},
                   {"m": "M:close_vs_vwap", "op": "above", "rhs": 0, "for": "2 bars", "within": "10m"}]},
  "do": {"enter": {"symbol": "HAL", "side": "buy", "size": {"inr": 25000}}},
  "manage": {"stop": {"m": "M:atr_stop_2x"}, "target": {"pct": 4}, "trail": null,
             "time_exit": "15:15", "reentry": {"max": 0}},
  "settle": null,
  "base_rate": {"event": "NSE order win > 5% mcap", "n": 31, "median_1d": "…", "control": "…"}
}
```

Day-trader plays are mostly `manage`. Event plays are mostly `when` with a
judge. A custom security is a measure plus a symbol. **One compiler, one
runtime, one router.**

### What is reused

| Piece | Becomes |
|---|---|
| `alerts.py` worker, crossing state, closed-bar context, catch-up | Play runtime core |
| `custom_indicators` + `indicator_sandbox` (`free` mode, `align_basket`) | Measure engine, generalised beyond one symbol's OHLCV |
| `chat_v2.query` | Unchanged; becomes the exploration step before `measure.define` |
| `indicators.py`, `patterns.py` | Library measures |
| `macro_events/verifier.py` | The judge funnel's rule that no quote means no fire |
| `news_events` Telegram and RSS, `webfeeds.py`, `company_news` NSE fetch | Event-store sources |
| `paper.py` (OCO, crypto in INR) | Router sink for paper fills; holding inputs |
| DSL `evaluator.py` three-valued logic, `readback.py`, `strategies.draft_delta` | `when` evaluation, readback, structural diff for escalation |
| `notify.py` outbox | Router sink for notify |
| `weighting.py` | Planner helpers for policy plays and custom securities |

---

## Part 4: Build order

Each step ends with a live, multi-turn eval with the triad (tokens, latency,
quality). Bump `?v=N` on every edit to a front-end file.

1. **Measures.** `measure.define`, the validator, backfill and chart overlay;
   persistent sandbox workers; indicators exposed as library measures. Done
   when 15 varied asks ("HDFC/ICICI P/B", "NIFTY PCR", "3-day FII streak", "my
   basket NAV") define a correct measure with zero backend edits.
2. **Plays: runtime and contract.** `play.compile` with the three dials and
   `inferred_from`, mandate, escalation and receipts, on the `alerts` lineage;
   paper and notify routes. Done when 30 autonomy-labelled prompts infer
   lifespan and authority correctly in ≥ 90% of cases and never upgrade
   silently.
3. **Judges and the event store.** NSE filings, SEBI, RBI and PIB releases, RSS
   and an X watchlist; the funnel; cached replays. Done when a real filing
   fires with its quote, and the latency is logged.
4. **Management and day-trader policies.** Bracket, trailing, OCO, time exit,
   opening range, straddle legs, re-entry, P&L exit; replay backtest on the
   same runtime. Done when a replayed day's fills match the backtest bar for
   bar.
5. **Policy plays, planner, custom securities.** Done when "rebalance it, go
   aggressive" does it once, offers the chip, and the chip turns it into a
   weekly policy.
6. **Real-money proposals.** Pre-filled tickets, with broker-native exits on
   confirm (Dhan Super Order, Kite GTT OCO).

Out of scope until empanelment and a legal opinion: unattended real-money
`act` and `manage`.
