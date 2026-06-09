# Pivot — project context for Claude

## What Pivot is

Pivot is a **chat-first investing copilot for Indian retail investors**. The
chat box *is* the product: a user describes what they want in plain English
(or Hinglish), and Pivot either **answers** it with grounded market data or
**builds** the thing they asked for — a trading automation, an options
strategy, a backtest, a paper trade — and renders it as an editable card.

A user can, in one chat surface:
- **Ask** — live price, price history + technicals (SMA/RSI/returns/52w),
  fundamentals (PE/ROE/PB/payout), company news, screens, comparisons, a
  structured single-stock analysis.
- **Automate** — describe a rule ("buy 10 INFY when RSI<30 and sell at 8%
  profit", "every Friday buy NIFTYBEES", "alert me when TCS crosses 4000")
  and get a **workflow/agent card** with the trigger + action laid out.
- **F&O** — real option chains (strikes/OI/IV/greeks/max-pain/PCR/expected
  move), suggest/build/critique option strategies, and option-metric
  automations.
- **Backtest** — simulate a strategy on historical bars with proper metrics.
- **Paper trade** — a simulated portfolio that fills registered ideas.

## What kind of startup we are

Early-stage, fast-moving, **India-first**. The wedge is *chat as the
interface to investing* — replacing fragmented broker tools, screeners, and
option calculators with one conversational copilot a non-expert can actually
use. We ship quickly and iterate on real prompts; correctness and output
quality are the product, not a feature.

## What we're compatible with

- **Broker / data:** **Zerodha Kite Connect is the primary data source** —
  live quotes, historical OHLCV, and **F&O** (NFO options). **yfinance** is
  the automatic fallback (indices, gaps, no-session). Fundamentals come from
  a Moneycontrol DB with a **yfinance fallback**.
- **Markets:** NSE & BSE equities, indices (NIFTY/BANKNIFTY/SENSEX), and NSE
  options (NFO). MCX is **research-only**.
- **Execution model — register-not-execute:** Pivot **registers** orders and
  arms automations; the user confirms/places in their own broker app. There
  is **no live broker auto-execution** (aligned with SEBI's retail-algo
  posture). Paper trading is fully **simulated**. Single-stock/index
  **futures execution is not wired**.

## What Pivot is NOT (keep scope tight)

Not a broker. Not a registered advisor — we give **data and frameworks**, not
personalised buy/sell advice (every analysis ends "this is analysis, not
financial advice"). No auto-execution. Don't invent capabilities or numbers;
when something isn't supported, say so plainly and offer the nearest real
thing.

## The two quality bars we optimise against

Every change should improve at least one of these, without regressing the
other:

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

## Architecture (brief)

- `pivot/` — backend: **FastAPI + SQLAlchemy 2 + Postgres + Redis**. The chat
  agent runs an LLM tool-calling loop; `services/tool_router.py` selects the
  tool set per turn by intent; `chat_service.py` owns reply-class budgets,
  routing/redirects, and affirmative/amendment handling; `prompts/system.md`
  is the agent's behavioural contract; option/workflow/backtest engines live
  under `services/`, `workflows/`, `market/`, `kite/`.
- `pivot-next/` — frontend: **Next.js 15 + shadcn/Tailwind**, strict
  TypeScript. Renders chat + the cards (`workflow_draft_card`,
  `option_chain_card`, `option_strategy_card`, stock snapshot, backtest
  chart, …). Note: it does some local intent shortcuts (e.g. ticker
  snapshots) — keep those from intercepting real backend intents.

## Working conventions

- Kite is primary for market data; never present yfinance/real-world dates as
  live when a Kite path exists.
- Never fabricate numbers — quote the card/tool values.
- Honest boundaries over fake success; never narrate "done/running" on a
  failure path.
- Run the app on `:8000` (backend) and `:3000` (frontend). The daily Kite
  token expires ~6 AM IST — re-login (FE button or `scripts/kite_connect.py`)
  and re-run `refresh_instrument_master` to keep F&O fresh.
- Commit freely; **ask before pushing** unless explicitly told to push.
