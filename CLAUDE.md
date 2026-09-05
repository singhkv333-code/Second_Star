# Pivot — project context for Claude

> The auto-loaded context file for this repo: what we are, the architecture as
> it actually runs, the non-negotiables, and where we are going. When facts
> here drift from code, **the code wins** — verify file/flag/tool names before
> relying on them. For anything touching a database, read `docs/DATA_MAP.md`
> first; two of our stores are silently shared and fail without erroring.
> (Last meaningful update: 2026-09-05. Branch `codex/vercel-waitlist-postgres`.)

---

## 1. The one-paragraph essence

**Pivot is a holistic trading platform with AI intelligence** — a hub for
financial analysis that serves traders and investors alike. You bring a
question, a chart, or an idea; Pivot answers it with real market data, draws it
on live candles with evidence you can check, builds the strategy that expresses
it, tests that strategy honestly, and runs it in a book you can watch.

We do not choose between the trader and the investor. The intraday chartist and
the long-horizon researcher share the same data plane, the same chat, the same
strategy builder. They come in through different doors and stay for different
reasons, and that breadth is deliberate.

**Priority order — emphasis, not exclusion:**

1. **Charting is the differentiator.** Evidence per annotation, honest
   confidence, move attribution, chat-native drawing. Auto-detection is a
   commodity in 2026; the judgment layer above it is not.
2. **The strategy and execution system is the other differentiator.** Plain
   English → a typed DSL tree → a backtest against the trust ladder → armed →
   filling in the paper book.
3. **Screening, fundamentals, filings, news, comparison and portfolio are
   complementary breadth.** They are what makes 1 and 2 credible, and they are
   why an investor stays. Necessary. Never the headline.

---

## 2. What kind of company we are

Early-stage, fast-moving, **India-first**. The wedge is a chart that can
explain itself joined to a strategy builder that refuses to flatter you. We
ship quickly and iterate on *real prompts*; correctness and output quality
**are** the product.

The moat is two things a competitor cannot copy by shipping a feature: the
**canvas** — a stateful chart scene the model addresses semantically and a
strategy tree it can edit — and the **verification loop**, the trust ladder
that deflates for multiple testing before we call anything an edge. No Indian
retail platform does the second at all.

---

## 3. The two quality bars we optimise against

Every change should improve at least one of these, without regressing the other:

1. **Execution correctness** — the right *intent*, the right *tool call*, the
   right *card or drawing*, and a faithful *parse* of the user's intent into
   its parameters. (Wrong tool, wrong widget, dropped condition, fabricated
   value, or a buildable strategy that loops/refuses = a correctness failure.)
2. **Output quality** — given a correct widget + text, is the answer actually
   *good*? Convincing, **data-rich**, **structured** (sections, markdown tables
   for comparisons), appropriately long, with a defended view where one is
   warranted. A correct-but-thin answer still fails this bar.

Test before shipping any agent behaviour: *"Did we call the right tool, render
the right thing, parse every parameter, AND say something a sharp retail
investor would find genuinely useful?"*

---

## 4. The non-negotiables

Identity-level. Violating one is never "a small bug."

- **Simulate, don't execute.** Pivot builds, backtests and arms strategies, and
  fills them into a **simulated paper book**. It does not place live broker
  orders. The broker rails exist but are dormant behind `live_orders_enabled`;
  re-arming them is a business decision plus a registered-broker partnership
  under SEBI's April-2026 framework, not a code change.
- **Not a broker. Not a registered advisor.** We give **data and frameworks**,
  never personalised buy/sell/hold advice (stock/portfolio/trade answers end
  "…this is analysis, not financial advice.").
- **Never fabricate.** Quote the card/tool values. No invented prices, dates,
  GMP, levels-by-role (support/resistance/pivot), PE comparators, or
  capabilities. If a tool returns null, **say it's unavailable** — never
  silence, never guess.
- **The model writes addresses, not coordinates.** A tool never accepts a
  price, a pixel or a timestamp the model made up. It accepts an *address* —
  `"09:15 @ high"`, `repeat:"session"` — and a deterministic resolver lands it
  on real bars. This is `mark.py`'s contract, and it is a platform rule.
- **Model reads, code computes.** The model chooses and explains; arithmetic is
  Python. Proven the hard way: `pivotted/filing_llm.py`'s grounding gates
  caught a 10,000× unit error in the *deterministic* resolver.
- **A rate without a control is decoration.** Every claimed edge ships with its
  base rate and its sample size.
- **Honest boundaries over fake success.** Never narrate "done/running" on a
  failure path. State the boundary in one line and **name the nearest real
  thing with a concrete number** (US tech → `MON100`; flexi-cap MF →
  `NIFTYBEES`).
- **India scope.** NSE & BSE equities, indices (NIFTY/BANKNIFTY/SENSEX), NSE
  options (NFO), **MCX commodities**, and crypto on the chart. US/foreign
  equities and off-exchange mutual funds are out of scope → offer the listed
  ETF proxy.
- **No opinion markets. Ever.** Pivot is not a prediction exchange, a
  binary-contract venue, a community voting market, or a curated-opinion
  publisher. The "View Markets" / belief-OS direction was retired on
  2026-09-05 and has no successor. Do not reintroduce it under a new name.
- **Calm, professional voice.** No slang, no emoji. Match the user's *brevity*,
  not their register. Decline off-domain asks in one line.

---

## 5. Architecture as it actually runs

Four services behind one nginx on
`pivot-india.centralindia.cloudapp.azure.com` (VM `Claudecodeforpivot`;
**ssh is closed — use `az vm run-command`**).

| Port | Service | Owns |
|---|---|---|
| `:5174` | **charto dataserver** (`charto/data/dataserver.py`) | Bars, indicators, patterns, the chart scene, drawings, alerts, live ticks, the paper book, armed strategies, auth |
| `:5175` | **charto/web** (Next.js) | The company page `/stock/[symbol]`, `/paper`, `/strategies` |
| `:5176` | **pivotted** (`pivotted/server.py`) | `/research/` — the filings + fundamentals research chat behind the company page's ask bar |
| `:8000` | **pivot API** (FastAPI) | *Not deployed yet.* Workflows DSL, backtesters, the tool registry, options, screener |
| `:3000` | **pivot-next** (Next.js) | *Not deployed yet.* The intended one app |

`charto/preview` (vanilla JS + a vendored Lightweight Charts v5) is served
straight off disk by nginx at `/`, so preview edits are live immediately — but
**every `<script>` carries a `?v=N` stamp that must be bumped on every JS edit.**

**pivot is a library in production, not a service.** `charto/data/execution_bridge.py`
imports `backend.agents.tools`, `backend.prompts.assembler`,
`backend.services.tool_registry` and `backend.workflows.*` to borrow the
strategy engine; `pivotted/fundamentals.py` imports `backend.market.financials_db`
rather than re-deriving the numbers. Both are deliberate — one derivation, one
set of numbers.

### Data — read `docs/DATA_MAP.md` before touching any store

One Postgres server (`pivot-db-india`, Central India, PG 18) with three
databases — `pivot_db`, `financials`, `pivot_enrich` — plus two SQLite files
on the VM. Two traps that have already cost us:

- **`charto_users.db` is not an auth database.** It is the whole live
  user-state plane: users (the **11 real accounts**), sessions, workspace,
  layouts, conversations, alerts, the paper book, armed strategies and the
  journal — all through one shared connection and lock (`ds._users`). You
  cannot migrate "just auth" out of it.
- **`pivot_db` is upstream master and fails silently.** Quarterly data,
  instrument master and company identity live there and reach the user through
  `sync_*.py` into `charto_bars.db`. Charto reads the *cache*, so a broken
  upstream shows up as data that stops advancing, never as an error.

**Market data:** Zerodha Kite is primary (live quotes, historical OHLCV, F&O);
yfinance is the automatic fallback. When `source != "kite"`, **tag the relay**.
Fundamentals come from Moneycontrol via the `financials` DB. The daily Kite
token expires ~6 AM IST.

---

## 6. The chat brain (how a turn flows)

```
user message
  → deterministic pre-LLM layer (intent classify, reply-class, special-case
     detectors)                                    services/chat_service.py
  → the FULL tool set + per-intent prompt packs    services/tool_router.py
  → LLM tool-calling loop with system_core.md as contract + a per-turn
     REPLY-CLASS directive pinning length/structure
  → tool_executor dispatches                       agents/tool_executor.py
  → tool returns data + a `_render_hint`           services/tool_registry.py
  → reply text (markdown) + an inline card or a drawing on the chart
```

- **`prompts/system_core.md` (994 lines) is the contract**, plus 19 per-intent
  packs in `prompts/modules/*.md` assembled by `prompts/assembler.py`. The old
  monolithic `system.md` was retired 2026-07-03. Changing agent behaviour is
  almost always editing one of these.
- **The model sees every tool, every turn.** `tool_router.py` does *not* narrow
  the tool set — the ~40-regex keyword router was deleted 2026-07-16 because
  misroutes (the right tool not being offered) were the second-largest source
  of failures, and a byte-stable toolset prefix-caches so its marginal cost is
  near zero. `tool_router`'s remaining job is **prompt-module selection**.
- **A tool + its prompt module + its evals is one unit.** Change any leg and
  re-run the others' evals before merging.
- **Single-shot tool calls.** The pipeline does not retry the LLM's call on
  validation failure — a wrong guess shows the wrong card. Hence the heavy
  pre-LLM determinism and ASK_USER discipline.
- **Cards and drawings are the commit surface.** For any order verb, *call the
  tool* — prose "Confirm: Buy 10 …" is uncommittable.
- **REPLY-CLASS** (injected per turn): `ANALYSIS` (250-450w, sectioned),
  `EXPLAINER` (250-500w), `SHORT-ANALYTICAL`/`CAPABILITY` (≤120w),
  `SMALL-TALK` (1-2 sentences), plus card-driven `DRAFT`/`AUTOMATION`/`BACKTEST`.

---

## 7. Subsystem map

- **Chart engine** (`charto/preview/`, `charto/data/`) — 26k lines of vanilla
  JS over a vendored Lightweight Charts v5 (see `preview/VENDOR_PATCHES.md`),
  against a 497M-row minute store. 26 indicators computed **server-side** (the
  FE fetches, never computes), 34 candle + 22 chart patterns with base-rate
  controls, a 15-tool fib/Gann ratio rail, volume profile, drawings addressable
  by never-recycled D-refs, and the universal `mark` tool. **Every fraction of
  time is measured in BARS, not seconds.**
- **Workflows / strategies** (`pivot/backend/workflows/`) — a linear, ordered
  list of typed steps (trigger → fetch → condition → action → notify; no
  branching in v1). Idempotent actions, persist-before-external-call, per-step
  retries, approval gating, advisory lock, schema validation at every boundary.
  The DSL evaluator is driven through a **five-method `DataAccessor` Protocol**,
  which is why charto needs one accessor (`strategies.py:299`), not a translator.
- **Backtester** (`backtester/`, `services/backtest/`) — the differentiator is
  the **trust ladder**: Deflated Sharpe, Minimum Track Record Length, block-
  bootstrap Monte Carlo, walk-forward, a no-skill permutation test, a trial
  counter that deflates for multiple testing, and a plain-English verdict
  (`insufficient_data → no_edge → unproven → promising`). Signals fill next-bar
  open.
- **Alerts** (`charto/data/alerts.py`) — composed-expression rules with **no
  `kind` column**; crossing side is persisted per condition; boot catch-up
  fires `late=1`; the tick hook swallows every exception, because an exception
  in `_live_on_tick` loses the minute.
- **Paper trading** (`charto/data/paper.py`, `pivot/backend/paper/`) — the
  simulated book that armed strategies fill into. Charto's tables in
  `charto_users.db` hold the live positions.
- **Options / F&O** (`services/option_strategies.py`, `strategy_builder.py`) —
  15+ templates with live greeks/payoff/margin/POP + rule-based critique.
  **APScheduler jobs must be module-level** (closures kill the scheduler).
- **Research** (`pivotted/`) — filings (577,952 grounded facts over 3,892
  companies), Moneycontrol quarterly, shareholding. Model READS, code COMPUTES,
  three grounding gates.
- **Events** (`macro_events/`, `triggers/`) — a hardcoded 2026 macro calendar
  (RBI MPC, CPI, FOMC) plus a verifier that reads the real outcome before
  firing. Fail-safe: never false-fires.
- **Screener, themes, baskets** (`services/thematic_map.py`,
  `sector_universe.py`, `weighting.py`) — ~19 sectors, ~200 tickers, weighting
  schemes equal/mcap/risk-parity/min-variance/black-litterman/factor.

A new chat-rendered capability = a new `_render_hint` + a new card + (usually)
a deploy path. Every new surface follows that template.

---

## 8. Working conventions

- **Commit freely; ask before pushing** — any branch, any remote.
- Kite is primary for market data; tag the relay when `source != "kite"`.
- Ports: `3000` pivot-next · `8000` pivot API · `5174` charto · `5175`
  charto/web · `5176` pivotted.
- **The VM's ssh is closed** — use `az vm run-command`, which runs as **root**,
  so `git` needs `sudo -u azureuser`. Deploy polls the `charto-deploy` branch
  every 30s. `deploy.sh` does `git reset --hard` (wipes unpushed VM edits).
- **nginx has a route allowlist.** Unlisted routes silently return 200 with
  `index.html` — they never 404. Apply with `deploy/apply_nginx.sh` (self-
  applies with rollback) and gate with `deploy/check_routes.sh`.
- Bump `?v=N` on every edited `charto/preview` JS file.
- Evals: **one instrumented multi-turn live run**, fix, retest at most once —
  no restart-and-rerun loops. Every eval/quality report carries the **triad**:
  tokens + latency + quality verdict per item.
- Plain `grep` is broken in this zsh — use `/usr/bin/grep`.

---

## 9. Where we're going

One platform, reached by small reversible infrastructure moves rather than a
rewrite. Full plan and per-deletion evidence in the current plan file; the
sequence is:

0. **Truth** — this file, `docs/DATA_MAP.md`, and the removal of retired files.
1. **Excise opinion markets** — ~43k lines across backend, frontend, schema and
   prose. Two generic utilities (`security_meta`, `commodities`) are rescued to
   `backend/market/` first, because the portfolio and paper book import them.
2. **Deploy the pivot API beside charto** on `:8000`, additive, nothing moves.
   **Share the session, not the store** — pivot reads charto's session; the 11
   real accounts never migrate.
3. **One shell** — `pivot-next` gains `/chart`, mounting `charto/preview`;
   `NAV_ITEMS` becomes Chart · Chat · Screener · Strategies · Portfolio · Paper.
4. **One tool surface by deletion** — charto's 47 tools and pivot's 99 overlap;
   dedupe rather than wrap. Charto owns anything chart- or bar-shaped; pivot
   owns anything workflow-, backtest- or fundamentals-shaped.
5. **Close the verification loop** — the agent runs its own backtest, reads the
   trust verdict, and revises or reports honestly. A `no_edge` strategy cannot
   be armed without a recorded override.
6. **Execution means the paper book** — order verbs route to the simulated
   book; broker rails stay dormant.

The codebase should be **smaller** when this is done, not larger.

---

## 10. North star

Pivot wins when someone — trader or investor — can bring a question to a chart,
see it answered with evidence rather than assertion, turn it into a strategy
without knowing what a DSL is, find out honestly whether that strategy has an
edge, and watch it run — all inside one calm platform. **Correctness and output
quality are the product.** Build toward that; keep the boundaries honest; never
fabricate.
