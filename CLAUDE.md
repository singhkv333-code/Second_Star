# Pivot — project context for Claude

> The auto-loaded context file for this repo: what we are, the architecture as
> it actually runs, the non-negotiables, and where we are going. When facts
> here drift from code, **the code wins** — verify file/flag/tool names before
> relying on them. For anything touching a database, read `docs/DATA_MAP.md`
> first; two of our stores are silently shared and fail without erroring.
> (Last meaningful update: 2026-10-17, the cleanup and folder layout. `README.md`
> is the folder map; `deploy/README.md` is how the VM runs it.)

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

- **Confirm, then execute.** Plays fill a **simulated paper book** by default.
  A play switched to live (`PIVOT_LIVE_PLAYS=1`, decided 2026-10-28) sends
  one-stock orders to the trader's own connected broker, and only when the
  trader confirms each one (the app's prompt, the Inbox or the emailed link):
  nothing is ever placed unattended, in line with SEBI's retail algo rules.
  Options, futures, crypto and baskets stay on paper. The older strategy
  rails stay dormant behind `live_orders_enabled`.
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
  Python. Proven the hard way: `research/filing_llm.py`'s grounding gates
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

Behind nginx on `app.pivotnow.in` and `pivot-india.centralindia.cloudapp.azure.com`
(VM `pivot-clean-stage`; **no ssh — use `az vm run-command`**, which runs as root).
The release is a trimmed copy of this repo; `deploy/README.md` has the details.

| Port | Unit | Source | Owns |
|---|---|---|---|
| `:3000` | `pivot-app` | `app/` (Next.js) | The product: workspace shell, chat, stock pages, screener, strategies, paper, sign-in, public SEO pages |
| `:5174` | `pivot-dataserver` | `dataserver/` | Bars, indicators, patterns, the chart's chat and scene, drawings, alerts, live ticks, accounts and sessions, billing, the paper book, armed strategies |
| `:8000` | `pivot-api` | `api/` (FastAPI, package `backend`) | The full chat, tool registry, workflows DSL, backtesters, fundamentals, screener, options, `/api/public/*` |
| `:5175` | `pivot-site` | `site/` (Next.js) | `/paper`, `/strategies`, `/brokers` on the VM; the same app is www.pivotnow.in on Vercel |

`chart/` (vanilla JS over a patched Lightweight Charts v5) is served straight
from the release by nginx at `/` and `/chart-app/`, and mounted in the shell —
**every `<script>` carries a `?v=N` stamp that must be bumped on every edit.**

**The API is also a library.** `dataserver/execution_bridge.py` imports
`backend.agents.tools`, `backend.prompts.assembler`,
`backend.services.tool_registry` and `backend.workflows.*` to borrow the
strategy engine, and `research/` imports `backend.market.financials_db` rather
than re-deriving the numbers. Both are deliberate — one derivation, one set of
numbers.

### Data — read `docs/DATA_MAP.md` before touching any store

One Postgres server (`pivot-db-india`, Central India, PG 18) with three
databases — `pivot_db`, `financials`, `pivot_enrich` — plus two SQLite files
on the VM. Two traps that have already cost us:

- **The users database is not an auth database.** (`/srv/pivot-data/charto_users.db`
  on the VM, `dataserver/users.db` locally, `PIVOT_USERS_DB` either way.) It is the whole live
  user-state plane: users (the **11 real accounts**), sessions, workspace,
  layouts, conversations, alerts, the paper book, armed strategies and the
  journal — all through one shared connection and lock (`ds._users`). You
  cannot migrate "just auth" out of it.
- **`pivot_db` is upstream master and fails silently.** Quarterly data,
  instrument master and company identity live there and reach the user through
  `dataserver/jobs/sync_*.py` into the bars store. The dataserver reads the *cache*, so a broken
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
- **Every tool is reachable every turn, and code never narrows the set.** The
  ~40-regex keyword router was deleted 2026-07-16 because misroutes (the right
  tool not being offered) were the second-largest source of failures. Since
  2026-09-24 the flex engine keeps 24 core tools loaded and puts specialised
  groups (orders, options, automations, IPOs, quant research, fixed income)
  behind the provider's hosted `tool_search` (`flex_chat.NAMESPACES`): the
  model sees each group's description and opens it itself, in the same
  response. The hosted `web_search` (India-located) is on every turn.
  `tool_router`'s remaining job is **prompt-module selection**.
- **A tool + its prompt module + its evals is one unit.** Change any leg and
  re-run the others' evals before merging.
- **Single-shot tool calls.** The pipeline does not retry the LLM's call on
  validation failure — a wrong guess shows the wrong card. Hence the heavy
  pre-LLM determinism and ASK_USER discipline.
- **Cards and drawings are the commit surface.** For any order verb, *call the
  tool* — prose "Confirm: Buy 10 …" is uncommittable.
- **The page assistant** (`services/assist_chat.py`, `POST /chat/assist`) is
  a separate, light engine behind the prompt bar on every page but Chat and
  Chart. The page's data is fetched up front (cached 60s, warmed while the
  user types), the brief is ~400 tokens, each page gets a few read-only
  tools, there are at most two tool rounds, and there is no web search.
  Depth and actions stay in the full chat. See
  `docs/handoffs/2026-09-30-page-assistant.md`.
- **REPLY-CLASS** (injected per turn): `ANALYSIS` (250-450w, sectioned),
  `EXPLAINER` (250-500w), `SHORT-ANALYTICAL`/`CAPABILITY` (≤120w),
  `SMALL-TALK` (1-2 sentences), plus card-driven `DRAFT`/`AUTOMATION`/`BACKTEST`.

---

## 7. Subsystem map

- **Chart engine** (`chart/`, `dataserver/`) — 26k lines of vanilla
  JS over a vendored Lightweight Charts v5 (see `chart/VENDOR_PATCHES.md`),
  against a 497M-row minute store. 26 indicators computed **server-side** (the
  FE fetches, never computes), 34 candle + 22 chart patterns with base-rate
  controls, a 15-tool fib/Gann ratio rail, volume profile, drawings addressable
  by never-recycled D-refs, and the universal `mark` tool. **Every fraction of
  time is measured in BARS, not seconds.**
- **Workflows / strategies** (`api/backend/workflows/`) — a linear, ordered
  list of typed steps (trigger → fetch → condition → action → notify; no
  branching in v1). Idempotent actions, persist-before-external-call, per-step
  retries, approval gating, advisory lock, schema validation at every boundary.
  The DSL evaluator is driven through a **five-method `DataAccessor` Protocol**,
  which is why the dataserver needs one accessor (`strategies.py:299`), not a translator.
- **Backtester** (`backtester/`, `services/backtest/`) — the differentiator is
  the **trust ladder**: Deflated Sharpe, Minimum Track Record Length, block-
  bootstrap Monte Carlo, walk-forward, a no-skill permutation test, a trial
  counter that deflates for multiple testing, and a plain-English verdict
  (`insufficient_data → no_edge → unproven → promising`). Signals fill next-bar
  open.
- **Alerts** (`dataserver/alerts.py`) — composed-expression rules with **no
  `kind` column**; crossing side is persisted per condition; boot catch-up
  fires `late=1`; the tick hook swallows every exception, because an exception
  in `_live_on_tick` loses the minute.
- **Paper trading** (`dataserver/paper.py`, `api/backend/paper/`) — the
  simulated book that armed strategies fill into. The dataserver's tables in
  `users.db` hold the live positions.
- **Options / F&O** (`services/option_strategies.py`, `strategy_builder.py`) —
  15+ templates with live greeks/payoff/margin/POP + rule-based critique.
  **APScheduler jobs must be module-level** (closures kill the scheduler).
- **Shared setups** (`dataserver/shares.py`, `chart/js/setups.js`)
  — a desk published as a frozen read-only link (drawings, indicators, panes,
  optionally the chat); readers "Make it mine" into their own layouts with a
  credit chain. A view session (`?view=`) writes nothing of the reader's. See
  `docs/handoffs/2026-09-30-shared-setups.md`.
- **Research** (`research/`) — filings (577,952 grounded facts over 3,892
  companies), Moneycontrol quarterly, shareholding. Model READS, code COMPUTES,
  three grounding gates.
- **Events** (`api/backend/macro_events/`, `earnings_events/`, `triggers/`) — a
  hardcoded 2026 macro calendar (RBI MPC, CPI, FOMC) that validates drafted
  triggers, and an earnings verifier the scheduler runs. The macro verifier and
  its poll loop were removed: a `trigger.scheduled_macro` (or
  `trigger.polymarket`) workflow saves but nothing fires it.
- **Screener, themes, baskets** (`services/thematic_map.py`,
  `sector_universe.py`, `weighting.py`) — ~19 sectors, ~200 tickers, weighting
  schemes equal/mcap/risk-parity/min-variance/black-litterman/factor.

A new chat-rendered capability = a new `_render_hint` + a new card + (usually)
a deploy path. Every new surface follows that template.

---

## 8. Working conventions

- **Commit freely; ask before pushing** — any branch, any remote.
- Kite is primary for market data; tag the relay when `source != "kite"`.
- Ports: `3000` app · `5174` dataserver · `8000` api · `5175` site.
- **`production-live` is production.** The VM's deployer follows it every
  minute; pushing it deploys. It never applies nginx, systemd or pip changes —
  those are installed by hand from `deploy/nginx/` and `deploy/systemd/`, with a
  backup, a test and a reload. Gate routes with `deploy/check_routes.sh`.
- **The VM still runs the old layout until `deploy/cutover_layout.sh` runs.**
  Never push a commit with the new folders to `production-live` before that.
- Bump `?v=N` in `chart/index.html` on every edited `chart/js` or `chart/css` file.
- `CHARTO_*` environment names still work (read as `PIVOT_*`), and the
  persisted `charto` names listed in `README.md` must not be renamed.
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
2. **Deploy the API beside the dataserver** on `:8000`, additive, nothing moves.
   **Share the session, not the store** — the API reads the dataserver's
   session; the accounts never migrate. (Done.)
3. **One shell** — `app/` mounts `chart/` at `/chart-app`. (Done; `site/` still
   serves `/paper`, `/strategies` and `/brokers` on the VM although `app/` has
   its own copies of those pages.)
4. **One tool surface by deletion** — the dataserver's 47 tools and the API's 99 overlap;
   dedupe rather than wrap. The dataserver owns anything chart- or bar-shaped;
   the API owns anything workflow-, backtest- or fundamentals-shaped.
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
