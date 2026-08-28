# Pivot — project context for Codex

> The auto-loaded context file for this repo: the essence, the architecture
> with up-to-date mentions, the non-negotiables, and where we are headed
> (V2 — View Markets). Build checklist for V2 lives at
> `Markdowns/VIEW_MARKETS_V2_CHECKLIST.md`. When facts here drift from code,
> **the code wins** — verify file/flag/tool names before relying on them.
> (Last meaningful update: 2026-06-29. Branch `Eventtriggers`, pushed `f4d3bed`.)

---

## 1. The one-paragraph essence

Pivot is a **chat-first investing copilot for Indian retail investors**. The
chat box *is* the product: a user describes what they want in plain English
(or Hinglish), and Pivot either **answers** it with grounded market data or
**builds** the thing they asked for — a trading automation, an options
strategy, a backtest, a paper trade — and renders it as an **editable card**
inline in the conversation. Everything else (routers, engines, schedulers,
data feeds) exists to make that single conversational surface feel correct,
data-rich, and trustworthy.

A user can, in one chat surface:
- **Ask** — live price, price history + technicals (SMA/RSI/returns/52w),
  fundamentals (PE/ROE/PB/payout), company/sector news, screens, comparisons,
  a structured single-stock analysis, index/market overviews.
- **Automate** — describe a rule ("buy 10 INFY when RSI<30 and sell at 8%
  profit", "every Friday buy NIFTYBEES", "alert me when TCS crosses 4000")
  and get a **workflow/agent card** with the trigger + action laid out.
- **F&O** — real option chains (strikes/OI/IV/greeks/max-pain/PCR/expected
  move), suggest/build/critique option strategies, and option-metric
  automations.
- **Backtest** — simulate a strategy on historical bars with a rigorous
  trust battery (§8.2).
- **Paper trade** — a simulated portfolio that fills registered ideas.

---

## 2. What kind of startup we are

Early-stage, fast-moving, **India-first**. The wedge is *chat as the
interface to investing* — replacing fragmented broker tools, screeners, and
option calculators with one conversational copilot a non-expert can actually
use. We ship quickly and iterate on *real prompts*; correctness and output
quality **are** the product, not a feature. The moat is the accreted
behavioural contract (`prompts/system.md`) + the backtest rigor — both hard to
copy because they are accumulated judgement, not any single feature.

---

## 3. The two quality bars we optimise against

Every change should improve at least one of these, without regressing the other:

1. **Execution correctness** — the right *intent classification*, the right
   *tool call*, the right *widget/card*, and a faithful *parse* of the user's
   intent into that card's parameters. (Wrong tool, wrong widget, dropped
   condition, fabricated value, or a buildable agent that loops/refuses = a
   correctness failure.)
2. **Output quality** — given a correct widget + text, is the answer actually
   *good*? Convincing, **data-rich** (use the real numbers we now have),
   **structured** (sections/headers, **markdown tables** for comparisons),
   appropriately long (not a terse blurb), with a defended view where one is
   warranted. A correct-but-thin answer still fails this bar.

Test before shipping any agent behaviour: *"Did we call the right tool, render
the right card, parse every parameter, AND say something a sharp retail
investor would find genuinely useful?"*

---

## 4. The non-negotiables (scope & integrity boundaries)

Identity-level. Violating one is never "a small bug."

- **Register-not-execute.** Pivot **registers** orders and **arms**
  automations; the user confirms/places in their own broker app. **No live
  broker auto-execution** — aligned with SEBI's Feb-2025 retail-algo posture;
  a *product principle*, not a temporary gap. Paper trading is fully
  **simulated**. Single-stock/index **futures execution is not wired**.
- **Not a broker. Not a registered advisor.** We give **data and frameworks**,
  never personalised buy/sell/hold advice (stock/portfolio/trade answers end
  "…this is analysis, not financial advice.").
- **Never fabricate.** Quote the card/tool values. No invented prices, dates,
  GMP, levels-by-role (support/resistance/pivot), PE comparators, or
  capabilities. If a tool returns null, **say it's unavailable** — never
  silence, never guess.
- **Honest boundaries over fake success.** Never narrate "done/running" on a
  failure path. When something isn't supported, state the boundary in one line
  and **name the nearest real thing with a concrete number** (US tech →
  `MON100`; flexi-cap MF → `NIFTYBEES`).
- **India scope.** NSE & BSE equities, indices (NIFTY/BANKNIFTY/SENSEX), NSE
  options (NFO), and **MCX commodities** (crude/gold/silver/metals/natgas —
  **tradeable via register-not-execute**).
  US/foreign equities and off-exchange mutual funds are out of scope → offer
  the listed ETF proxy.
- **Calm, professional voice.** No slang, no emoji. Match the user's
  *brevity*, not their register. Decline off-domain asks in one line.

---

## 5. Architecture at a glance

### Backend — `pivot/`
**FastAPI + SQLAlchemy 2 (sync, psycopg2) + Postgres + Redis.**
- **Postgres** is on **Azure (Central India, PG v18)**; latency is RTT-bound.
  Creds in gitignored `.env` / `~/.pgpass`. Latest Alembic migration:
  **`0022_user_auth_beta`** (a new feature's migration starts at 0023).
- **LLM provider is Azure (GPT-5.x-mini)** — `config.llm_provider="azure"`
  default, OpenAI as the alt provider. *(USERHELP.md's "Sarvam / GPT-4o mini"
  is stale — ignore it.)*
- The chat agent runs an LLM tool-calling loop; `services/tool_router.py`
  selects the per-turn tool set by intent; `chat_service.py` owns reply-class
  budgets, routing/redirects, and affirmative/amendment handling;
  `prompts/system.md` (~2,300 lines) is the agent's behavioural contract.
  Engines live under `services/`, `workflows/`, `backtester/`,
  `services/backtest/`, `market/`, `kite/`, `macro_events/`, `news_events/`.

### Frontend — `pivot-next/`
**Next.js 15 (app router) + shadcn/ui + Tailwind, strict TypeScript.**
- Renders chat + ~24 card types keyed off a backend `_render_hint`
  (`workflow_draft_card`, `option_chain_card`, `option_strategy_card`,
  `strategy_builder_card`, `clarify_card`, the IPO cards, backtest charts,
  `logic_card`, …).
- Top-level nav today (`AppShell.tsx` `NAV_ITEMS`): **Chat · Portfolio ·
  Agents · Calendar · Screener**. Plus `/login`, `/signup` (production auth),
  `/stock/[symbol]`, `/waitlist`, `/design`.
- Does some local intent shortcuts (e.g. ticker snapshots) — keep those from
  intercepting real backend intents. *(Legacy Vite `frontend/` is retired.)*

### Data sources (the Kite-primary contract)
- **Zerodha Kite Connect is PRIMARY** — live quotes, historical OHLCV, and
  **F&O** (NFO options). **yfinance is the automatic fallback** (indices, gaps,
  no-session). When `source != "kite"`, **tag the relay** (e.g. "(yfinance,
  EOD)"). **Fundamentals** come from a **Moneycontrol DB** with a yfinance
  fallback (PE/ROE/ROCE/D-E/payout/sector/business summary/promoter %).

---

## 6. The chat brain (how a turn flows)

```
user message
  → deterministic pre-LLM layer (intent classify, reply-class, special-case
     detectors: thematic scenario, vague onboarding, idle-cash, unrealistic
     return, backtest-tweak follow-up …)            services/chat_service.py
  → tool_router selects the per-turn tool SUBSET by intent (≈90 tools → ~8-12)
                                                     services/tool_router.py
  → LLM tool-calling loop with system.md as contract + a per-turn
     REPLY-CLASS directive pinning length/structure
  → tool_executor dispatches the chosen tool        agents/tool_executor.py
  → tool returns data + a `_render_hint`            services/tool_registry.py
  → reply text (markdown) + an inline editable CARD
```

- **`system.md` is the product surface.** It encodes the routing rules:
  automation-vs-agent shape; *alert verbs route to notify-not-order* (hard
  gate); *no-trade markers override everything*; *time phrasing means schedule,
  not price*; never-invent-a-level-by-role; never-fabricate-a-disconnect;
  JUST-DO-IT-for-reads; ASK_USER only when a required arg is genuinely missing
  (unit/size ambiguity outranks a soft threshold). Changing agent behaviour is
  almost always editing this file.
- **REPLY-CLASS** (injected per turn): `ANALYSIS` (250-450w, sectioned
  Snapshot/Technicals/Fundamentals/News/What-to-watch/View), `EXPLAINER`
  (250-500w), `SHORT-ANALYTICAL`/`CAPABILITY` (≤120w), `SMALL-TALK` (1-2
  sentences), plus card-driven `DRAFT`/`AUTOMATION`/`BACKTEST`.
- **Single-shot tool calls.** The pipeline does **not** retry the LLM's tool
  call on validation failure — a wrong guess shows the wrong card. Hence the
  heavy pre-LLM determinism + ASK_USER discipline.
- **Cards are the commit surface.** For any order verb, *call the tool* — don't
  write the confirmation in prose. Prose "Confirm: Buy 10 …" is uncommittable.

---

## 7. Subsystem map (with current state)

- **Workflows / "Agent System"** (`workflows/`) — a **linear, ordered list of
  typed steps** (trigger → fetch → condition → action → notify/control; no
  branching/loops in v1). Engine invariants: idempotent actions
  (`client_request_id = sha1(run:step:attempt)`), **persist-to-DB before any
  external call**, per-step retries+backoff, **approval gating** (pause→resume),
  single-instance advisory lock, 30-min time budget, schema validation at every
  boundary. Scheduler: cron poll (30s) + price/indicator watcher (60s in market
  hours) + event watcher (5m). `propose_workflow` translates NL → a draft card
  (mock fallback if the draft fails validation). Traces: `docs/ARCHITECTURE.md`
  + `docs/SYSTEM_WALKTHROUGH.md`.
- **Backtester** (`backtester/`, `services/backtest/`, `workflow_backtester.py`)
  — multiple engines (single-symbol tree, expr/cross-sectional, pairs,
  portfolio). The differentiator is the **"trust ladder"**: Probabilistic /
  **Deflated Sharpe** / Minimum Track Record Length, Monte-Carlo (block
  bootstrap), walk-forward, no-skill **permutation test**, a **trial counter**
  deflating for multiple-testing, and a plain-English **Trust verdict**
  (`insufficient_data → no_edge → unproven → promising`). Look-ahead fixed
  (signal fills next-bar open). *No Indian retail platform deflates for trials.*
  Plan: `docs/BACKTESTING_PLAN.md`.
- **Options / F&O** (`services/option_strategies.py`, `option_strategy_service.py`,
  `strategy_builder.py`) — 15+ templates with live greeks/payoff/margin/POP +
  rule-based critique; chain/suggest/build/critique tools + cards; paper
  multi-leg fills; portfolio greeks. **GOTCHA: APScheduler jobs must be
  module-level** (closures kill the scheduler).
- **Events / macro / prediction markets** (`macro_events/`, `news_events/`,
  `triggers/`) — a hardcoded **2026 macro calendar** (RBI MPC, CPI, FOMC) + a
  **verifier** that reads the real outcome (RSS → LLM → prediction-market
  fallback) before firing (fail-safe, never false-fires); a full news pipeline
  with **Polymarket + Kalshi** adapters/workers as a "what's priced in"
  cross-check (`prediction_market.py`). Triggers: `trigger.event`,
  `trigger.scheduled_macro`, `trigger.polymarket`, `trigger.kalshi`. Flags
  (`macro_events_enabled`, `kalshi_rest_enabled`, `polymarket_ws_enabled`)
  default **OFF**.
- **Themes / sectors** (`services/thematic_map.py`, `sector_universe.py`,
  `weighting.py`) — **six** frozen macro scenarios (monsoon-drought,
  conflict/war, INR depreciation, crude spike, RBI rate-cut, slowdown) each
  with thesis, winners/losers (real NSE tickers + WHY), confirm/invalidate,
  default basket weights — **the seed for V2 View Markets**. ~19 sectors /
  ~200 tickers; weighting schemes equal/mcap/risk-parity/min-variance/
  black-litterman/factor.
- **IPO** (`services/ipo_feed.py`, `ipo_application_service.py`,
  `trendlyne_ipo.py`) — chat-native editable IPO widget + reminder automation;
  NSE feed enriched with Trendlyne; register-not-execute. *(Uncommitted local
  WIP as of f4d3bed.)*
- **Paper trading** (`paper/`) — simulated-broker portfolio + forward-testing;
  registered ideas fill into a paper book, NAV/P&L tracked.
- **Brokers / auth** — `BrokerConnector` over Kite/Dhan/Fyers (`brokers/`,
  `/brokers` router) + auto-exec gating + `broker_audit`; production JWT
  login/signup + per-user chat-state isolation + persisted chat summaries
  (migration `0022_user_auth_beta`).

The **card system**: a new chat-rendered capability = a new `_render_hint` + a
new FE card + (usually) a deploy path (`createWorkflow` → `activate` → `run`).
Every V2 surface follows this template.

---

## 8. Working conventions

- Kite is primary for market data; never present yfinance/real-world dates as
  live when a Kite path exists — tag the relay when `source != "kite"`.
- Never fabricate numbers — quote the card/tool values.
- Honest boundaries over fake success; never narrate "done/running" on a
  failure path.
- Run the app on `:8000` (backend) and `:3000` (frontend). The daily Kite
  token expires ~6 AM IST — re-login (FE button or `scripts/kite_connect.py`)
  and re-run `refresh_instrument_master` to keep F&O fresh.
- **Commit freely; ask before pushing** unless explicitly told to push.
- Evals: **one instrumented multi-turn live run**, fix, retest at most once —
  no restart-and-rerun loops. Every eval/quality report carries the **triad**:
  tokens + latency + quality verdict per item.

---

## 9. V2 direction — **View Markets** (the next big bet)

> Full spec: `Markdowns/Version2.md`. Build checklist:
> `Markdowns/VIEW_MARKETS_V2_CHECKLIST.md`.

**The idea — Belief → Expression → Deployment.** Most investors think in
*opinions*, not instruments: "RBI cuts rates", "Gold beats equities", "India
enters a manufacturing upcycle", "IT beats the market over six months." They do
**not** naturally think "buy a call spread" or "build a pair trade." **View
Markets is the belief operating system** that bridges that gap — a curation +
presentation layer *on top of* the existing automation/options/backtest
engines. Success metric: *"I may not know which instrument to buy, but I know
what I believe"* — and Pivot turns the belief into an evidence-backed,
deployable expression.

- **IS:** a belief OS, a strategy-discovery engine, a capital-expression layer.
  **IS NOT:** a prediction exchange, a betting/binary YES-NO market, a community
  voting market, an advisory product claiming certainty. *(We may *read*
  Polymarket/Kalshi to show "what's priced in" — we never *become* one.)*
- **Three view types:** **Event** (objective outcome + resolution date — "RBI
  cuts at the next MPC"), **Relative** (A beats B over T — "IT beats Nifty 6m"),
  **Theme** (long-duration structural narrative — "defence supercycle"; express
  as baskets, never binary contracts). A belief without a measurable outcome +
  defined benchmark + time horizon is not actionable.
- **Differentiated components:** a **transmission map** (visual cause→effect
  chain — "US strikes Iran → oil up → inflation up → rates up → energy benefits,
  airlines weaken"); **market-expectations & surprise** (markets move on
  surprise, not outcome — Expected vs User-View vs Difference); **expressions**
  in **Conservative/Balanced/Aggressive** tiers (baskets, option structures,
  relative/pair trades, hedges) each with why/risk/capital-intensity/historical-
  strength/horizon; **two confidence dimensions** (outcome vs expression);
  **timing modes** Pre-position / Confirmation / Hybrid; a **lifecycle**
  Open→Developing→Consensus→Resolved→Archived; **backend-generated, evidence-
  backed** views (never hand-typed opinions).
- **V1 scope = curated views only.** OUT: user-created beliefs, custom belief
  builders, prediction exchanges, binary contracts, community voting, trading
  outcome contracts (user-authored beliefs are a *future* direction only).
- **Design language:** visual, guided, calm, trustworthy — cards, timelines,
  confidence dials, small charts, transmission diagrams, progressive disclosure.
  Avoid dense tables / terminal vibes / data overload.

**Why it's mostly *assembly*, not green-field** — the engine largely exists:
- Event resolution → `macro_events/` (calendar + verifier) + `news_events/`.
- "What's priced in" → Polymarket/Kalshi (`prediction_market.py`).
- Transmission + theme→winners/losers → `thematic_map.py` + `sector_universe.py`.
- Relative/pair views → `get_correlation_matrix`, `compare_performance`,
  `services/backtest/pairs/`.
- Expressions → option templates, `propose_basket_allocation` + `weighting.py`,
  workflow allocate actions.
- Deploy/automate/backtest → the workflow card→create/activate/run pattern +
  the trust-verdict backtest battery.
- **Net-new:** the `market_views` / `view_expressions` / `view_transmission` /
  `view_confidence` / `view_expectations` data model (migration **0023**), a
  view-generation/curation pipeline, the surprise/expectations aggregator, the
  transmission DAG (move `thematic_map` thesis prose → machine-readable nodes),
  a new **"Views" FE tab** + View cards, and the chat tools/routing/render-hints
  to expose it. Reuse `thematic_map.detect_thematic_scenario` + `_POSITIONING_RE`
  as the chat routing seed. Ship behind `view_markets_enabled` (default OFF).

---

## 10. North star

Pivot wins when a non-expert can hold a belief, see it explained with evidence
and a causal map, choose a risk-appropriate expression, sanity-check it with a
*rigorous* backtest, and deploy it as a register-not-execute automation — all
inside one calm conversation. **Correctness and output quality are the
product.** Build toward that; keep the boundaries honest; never fabricate.
