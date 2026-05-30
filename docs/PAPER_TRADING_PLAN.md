Confirmed: head migration is `0012_workflow_expires_at`, `place_order` signature ends without `client_request_id`/`order_type`, and `actions.py` passes both at lines 664-665 and 1010-1011 (latent bug). Now producing the final document.

# Pivot Paper-Trading & Forward-Testing — Build Plan

> Single source of truth. Verified against the codebase at branch `Eventtriggers`, head migration `0012_workflow_expires_at`. All file paths are absolute. Quartr token names are exact.

---

## 0. Executive Summary

We are building a **paper-trading system inside Pivot** where every order — whether a chat-confirmed order or a workflow action firing — is filled by a **simulated broker** against live prices, accrues into a **structured, evolving portfolio** (cash ledger, positions, realized + unrealized P&L, an equity/NAV curve), and is rendered on a **Quartr-themed dashboard**.

The core insight is that the orders already flow through **one seam**. Chat (`/Users/karanveersingh/Downloads/Second_Star/pivot/backend/routers/orders.py`), SIP (`/Users/karanveersingh/Downloads/Second_Star/pivot/backend/scheduler.py`), and all eight workflow actions (`/Users/karanveersingh/Downloads/Second_Star/pivot/backend/workflows/steps/actions.py`) call `place_order` / `place_gtt_order` / `get_orders` / `cancel_order` in `/Users/karanveersingh/Downloads/Second_Star/pivot/backend/kite/orders.py`. Today those return a hollow `MOCK####` id with no fill price, no cash effect, and no position. We **intercept that one seam with a `PaperBroker`** that fills against live yfinance/Kite quotes, debits a cash ledger, accrues positions, and snapshots NAV history — with **zero changes to chat or workflow action code**.

Two aims:
1. **A structured paper portfolio** — a real cash ledger → fills → positions → mark-to-market → NAV/equity curve, closing the orders↔portfolio gap that exists today (portfolio reads static `MOCK_HOLDINGS`, disconnected from orders).
2. **Forward-testing of ideas** — attribute every fill to its originating workflow / strategy / chat-idea and score each idea's **live out-of-sample** performance over time, including a **backtest-vs-live degradation panel** that answers "is the edge real out-of-sample, or was it curve-fit?"

Everything reuses Pivot's own assets: `services/trading_costs.py` for all friction (slippage, fees, taxes), `services/backtest_metrics.py` for all ratios (so live and backtest numbers are identical by construction), the existing scheduler to host the resting-order and mark-to-market loops, and the `recharts`-based FE conventions. The build is orchestrated with **Pivot Workflows** themselves (Section 8).

---

## 0.1 Scope & Non-Goals

**In scope (v1):**
- A `PaperBroker` mirroring the exact `orders.py` interface; `orders.py` becomes a per-account broker selector.
- Cash ledger with **reserved cash** for resting BUY orders and **T+1 settlement** modeling.
- **Integer shares only**, **long-only CNC by default** (no fractionals, no live shorts).
- MARKET / LIMIT / STOP (SL / SL-M) / GTT / bracket-OCO fills against a **synthesized spread off LTP**.
- Idempotency via a persisted `client_request_id` unique constraint; positions/cash **derived from the immutable fills log**.
- Scheduler-hosted resting-order evaluator + mark-to-market/NAV snapshotter; after-hours MARKET orders queue to next open (AMO semantics).
- Per-account and per-idea daily NAV snapshots; forward-idea registry + scorecards + backtest-vs-live degradation.
- A Quartr-themed `PaperDashboard` FE tab.

**Explicitly out of scope (v1) — document, don't silently break:**
- **No real broker call.** This is a simulator; `mode="live"` routing is reserved but the live path stays the existing Kite path.
- **Live short-selling and margin/MIS leverage** — shorts remain confined to the research backtester (`backend/backtester/`); matches the existing `allocate_basket` `NotImplementedError` guard on live short legs.
- **Fractional shares** — Indian cash equities don't fractionalize; sub-one-share slices reject as `slice_too_small`.
- **Corporate actions (splits / dividends)** — would silently drift `avg_cost`; acceptable for an MVP, flagged in the methodology note.
- **Partial fills** — OFF by default behind `PAPER_PARTIAL_FILLS`; deterministic fills keep attribution and reconciliation clean.
- **No market-impact / queue-position / liquidity-depth modeling** — retail-size, LTP-only; a paper order larger than real available volume still fills (matches Alpaca).
- **No new WebSocket channel** — per-symbol LTP rides the existing `liveQuoteManager`; account-level NAV polls.

---

## 1. Paper-Trading Engine Essentials

Pivot already has the right seam in two functions — `backend/kite/orders.py::place_order` and `place_gtt_order` — that *every* chat order and *every* `action.*` workflow step funnels through (verified: `actions.py` calls `place_order`/`place_gtt_order` for `place_order`, `allocate_notional`, `allocate_basket`, `set_stoploss`, `set_takeprofit`, `squareoff_*`). Today those return a synthetic `MOCK####` id with no fill price, no cash effect, no position. A real paper-trading engine replaces that hollow mock with a deterministic **PaperBroker** that fills against live yfinance/Kite quotes, debits a cash ledger, and accrues positions. This section specifies that engine, calibrated to how Alpaca, QuantConnect Lean, and Kite/Zerodha actually behave.

### 1.1 Order types and how each fills against live prices

The fill engine is driven by the **live LTP** (Pivot's `market_data.get_live_quote` / `yfinance_service`), since Pivot has no real bid/ask book. Synthesize a spread from LTP (e.g. `half_spread = max(0.05, LTP * spread_bps)`, default `spread_bps ≈ 3 bps` large-cap) so marketability has meaning. `ask = LTP + half_spread`, `bid = LTP - half_spread`.

| Order type | Fill rule (paper) | Maps to Pivot action |
|---|---|---|
| **MARKET** | Fill immediately at `ask` (buy) / `bid` (sell), then apply slippage. The default in Alpaca/Lean is "immediate and complete at NBBO." | `action.place_order` (order_type=market), `allocate_notional`, `allocate_basket`, all `squareoff_*` (always MARKET) |
| **LIMIT** | **Rest** until marketable: buy fills when `ask ≤ limit`, sell when `bid ≥ limit`. Fill at the **better of limit and touch** (price-improve to `ask`/`bid` if it crossed through). | `action.place_order` (order_type=limit, `limit_price`) |
| **STOP (SL-M)** | Rest until LTP crosses `trigger_price` (buy: LTP ≥ trigger; sell/stop-loss: LTP ≤ trigger), then becomes a MARKET order and fills at touch ± slippage. | Not yet a distinct action — today SL routes through GTT |
| **STOP-LIMIT (SL)** | On trigger cross, convert to a resting LIMIT at `limit_price` (may then sit unfilled if it gaps through). | Future `action.place_order` extension |
| **GTT** | Pivot's stop/take-profit primitive. Rests indefinitely (Kite: 1-year validity). On trigger cross → place the embedded LIMIT sell. **One leg = single GTT; OCO = two-legged GTT** (Kite supports both). | `action.set_stoploss` (SELL GTT below entry), `action.set_takeprofit` (SELL GTT above entry) |
| **Bracket / OCO** | Entry order + a paired SL-GTT and TP-GTT where **filling one cancels the other**. Model as a `parent_order_id` group; on any child fill, cancel siblings. | Compose `place_order` → `set_stoploss` + `set_takeprofit` in one workflow (the engine already chains these via run-context `executed_price`) |

Key behaviors to mirror from the references:
- **No queue-position / no liquidity cap** (Alpaca explicitly): a paper order can be larger than real available volume and still fills. Adopt this — Pivot is retail-size and modeling depth is out of scope.
- **Lean default = no slippage**; Alpaca = no slippage but does partial-fill 10% of the time. Pivot should *add* slippage (see below) because it already owns a calibrated cost model and "frictionless is a lie" is a stated repo principle (`trading_costs.py` docstring).

### 1.2 Fill model: slippage, partials, fees, integer shares, market-hours gating

- **Slippage** — Do **not** invent a new number. Reuse `services/trading_costs.py::SLIPPAGE_PCT` (0.05%/leg) applied adversely: buy fills at `touch * (1 + SLIPPAGE_PCT)`, sell at `touch * (1 - SLIPPAGE_PCT)`. This keeps live-paper and backtest on the *same* friction model (the repo just fixed live↔backtest parity — keep it).
- **Fees / taxes** — Compute via `trading_costs.buy_cost(price, qty)` / `sell_cost(price, qty)` which already return `(net_cashflow, total_charges)` including STT, exchange, SEBI, GST, stamp, brokerage. The cash ledger debits `net_debit` on buys and credits `net_credit` on sells; store `charges` per fill for transparency. This is the single source of truth — reuse, don't fork.
- **Partial fills** — Recommend **OFF by default** for v1 (deterministic fills are easier to reconcile and to attribute to ideas). Provide an opt-in `PAPER_PARTIAL_FILLS` flag implementing Alpaca's "10% chance, random slice, re-evaluate remainder" only if needed for realism demos. Resting LIMIT/GTT then need a "partially_filled" status.
- **Bid/ask vs LTP** — Pivot has only LTP, so synthesize the spread (1.1). Document this as a known simplification; market-impact and price-improvement are explicitly out of scope (matches Alpaca's stated non-goals).
- **Fractional vs integer** — **Integer only.** Indian cash equities don't fractionalize; the existing `int(notional // ltp)` floor logic in `allocate_notional`/`place_order` is already correct. Reject/zero-out sub-one-share slices (already done — "slice_too_small").
- **Market-hours gating + queued fills** — Add an `is_market_open()` gate (NSE 09:15–15:30 IST, Mon–Fri, holiday calendar). Behavior:
  - MARKET order placed **in-hours** → fill now.
  - MARKET placed **after-hours/weekend** → status `queued`, fills at **next session open** against the opening quote (mirrors Kite AMO semantics; Alpaca queues to next session too).
  - LIMIT/STOP/GTT → always rest; the **scheduler loop** (see 1.5) evaluates them each tick *only during market hours*.

### 1.3 Simulated cash & settlement model

- **Starting capital** — Seed each paper account with a configurable opening balance (reuse the existing `MOCK_MARGINS` figure ₹150,000 as the default, or make it user-set). Store as a `PaperAccount.starting_cash`.
- **Buying power** — Long-only CNC: `buying_power = cash_available` (no leverage). Even though `backend/backtester/` supports shorts, **live paper mirrors CNC long-only by default** (consistent with `allocate_basket` already raising `NotImplementedError` on live short legs). Margin/MIS intraday can be a later flag.
- **Reserved cash for resting orders** — When a BUY LIMIT/STOP rests, **reserve** `est_cost = limit_price * qty + est_charges` so a user can't double-spend the same cash on two resting orders. Release the reserve on fill (replaced by actual debit) or cancel. This is the single most important correctness rule the references gloss over and that Pivot needs because it lets agents place baskets of resting orders.
- **T+1 settlement (India)** — Model a settlement ledger: SELL proceeds post to `cash_available` on **T+1**, but (matching Zerodha's real rule) **CNC sell proceeds are immediately reusable for fresh CNC buys same-day**. Practical implementation: keep `cash_settled` and `cash_available` where `cash_available = cash_settled + today_unsettled_sell_credits − today_buy_debits`; roll unsettled into settled at EOD. For a v1 demo this can be simplified to "proceeds usable immediately" with a `settles_at = T+1` field stamped for display honesty.
- **Short-selling / margin** — Out of scope for live paper v1; note that the backtester path keeps shorts for research only.

### 1.4 Mark-to-market

- **When** — Revalue on (a) every dashboard read (lazy MTM against latest quote), and (b) a periodic scheduler tick (e.g. every 1–5 min in-hours) that snapshots an **equity/NAV point** for the curve. Reuse the existing scheduler (`backend/workflows/scheduler.py`) — it already wakes on `next_run_at`; add a `mark_to_market` job.
- **Day P&L vs Total P&L** —
  - *Total/unrealized* per position = `qty * (LTP − avg_cost)`.
  - *Realized* = booked on sells = `sell_proceeds_net − qty * avg_cost`.
  - *Day P&L* = `qty * (LTP − prev_close)` for held qty + realized P&L booked today. Needs a stored **previous-day close** per symbol (snapshot at EOD), exactly the `day_change`/`day_change_percentage` fields the mock holdings already expose — now computed for real.
- **NAV** = `cash_available + cash_settled_pending + Σ(qty * LTP)`. Persist to an equity-curve table for the Quartr dashboard.
- **Corporate actions (splits/dividends)** — **Out of scope for v1** (Alpaca's paper engine explicitly skips dividends too). Document the gap; a split would silently break avg_cost until handled. Acceptable for a forward-test MVP.
- **Stale-price / market-closed** — If the quote is older than N minutes or market is closed, MTM against **last known close** and flag the holding `stale=true` so the UI can show "as of close" rather than a fake live tick. Never fabricate a moving price when the market is shut.

### 1.5 Idempotency & correctness

- **client_request_id dedup** — Pivot **already** generates `client_request_id = sha1(f"{run_id}:{step_index}:{attempts}")` and passes it to actions (see `actions.py` docstring; legs derive per-symbol child ids). The PaperBroker must **persist a unique constraint on `client_request_id`** and, on a duplicate, **return the existing fill** rather than creating a second one. This is the linchpin that makes scheduler retries (`max_retries=1`) safe. Note: `place_order` in `orders.py` does **not currently accept** a `client_request_id` param even though some callers in `actions.py` pass one (`actions.py:665`, `actions.py:1011`) — that's a **verified latent bug** to fix when adding the seam.
- **Avoiding double-fills on retries** — A resting-order evaluator must mark an order `filled` atomically (status transition guarded in one transaction) so two overlapping scheduler ticks can't both fill it. Use a row-level `SELECT ... FOR UPDATE` / optimistic version column.
- **Reconciliation** — On startup and each tick, reconcile `Σ fills → positions → cash` so a crashed mid-fill leaves no orphaned reserve (this is exactly the NautilusTrader "duplicate orders on restart" failure mode — guard against it by deriving positions purely from the immutable fills log, never from incrementally-mutated counters).

### 1.6 Where to put the PaperBroker seam (decision)

Introduce **`backend/paper/broker.py`** (a new `backend/paper/` package, sibling of `backend/kite/`) exposing the *exact same signatures* as `orders.py`:

```
place_order(access_token, tradingsymbol, exchange, transaction_type, quantity,
            order_type, price, product, trigger_price, tag, variety,
            client_request_id) -> {order_id, status, average_price, ...}
place_gtt_order(...) -> {trigger_id, status, ...}
get_orders(...) / cancel_order(...)
```

Then make `orders.py` a **thin router** that selects the broker **per account** based on a mode resolved from the account/user:

```
PaperBroker  ← account.mode == "paper"   (default for dev user, KITE_MOCK_MODE)
KiteBroker   ← account.mode == "live"    (real token present)
```

Because `actions.py` and the chat `/orders/register` path already import `place_order`/`place_gtt_order` from `orders.py`, **nothing downstream changes** — chat orders and all eight workflow actions automatically flow through the PaperBroker the moment the router selects it. The PaperBroker writes a `TradeLog` fill row (the model already has `average_price`, `filled_quantity`, `status`, `source`, `source_id`, `placed_at`), debits the new cash ledger, and upserts positions. Resting LIMIT/STOP/GTT rows are persisted and drained by a new scheduler job that reuses `get_live_quote` for marketability checks — the scheduler "can host a resting-order / mark-to-market loop" as the architecture note anticipates.

This keeps the blast radius to: one new module, a small `orders.py` router shim, the `client_request_id` plumbing fix, and new tables (account/cash-ledger/positions/equity-curve/resting-orders) — with **zero changes to chat or workflow action code**.

### Key decisions for Pivot (Section 1 summary)

- **One seam, mirror the existing interface.** Add `backend/paper/broker.py` with byte-identical `place_order`/`place_gtt_order` signatures; turn `orders.py` into a per-account broker selector.
- **Reuse `trading_costs.py` for everything** — slippage (`SLIPPAGE_PCT`) and all fees/taxes via `buy_cost`/`sell_cost`. No new cost numbers; keeps live-paper ↔ backtest parity that was just fixed.
- **Integer shares, CNC long-only by default.** No fractionals, no live shorts; shorts stay confined to the research backtester.
- **Synthesize a spread from LTP** (no real book): MARKET fills at touch ± slippage; LIMIT/STOP rest and fill on marketability; GTT is the stop/take-profit primitive; bracket = entry + OCO GTT pair.
- **Reserve cash for resting BUY orders** and model **T+1 settlement** (CNC sell proceeds immediately reusable, formally settled T+1).
- **Idempotency via persisted `client_request_id` unique constraint** — return the existing fill on duplicate; derive positions/cash purely from the immutable fills log. (Fix the latent bug: `orders.py::place_order` doesn't yet accept the `client_request_id`/`order_type` some callers already pass.)
- **Scheduler hosts two loops** in-hours: a resting-order/GTT evaluator and a mark-to-market/NAV snapshotter; after-hours MARKET orders queue to next-open.
- **Partial fills OFF by default**, behind a flag.
- **Corporate actions explicitly out of scope for v1** — MTM against last close when market closed/stale and flag `stale`, never fabricate a moving price.

**Sources:** [Alpaca Paper Trading](https://docs.alpaca.markets/us/docs/paper-trading), [Alpaca Order Types](https://alpaca.markets/learn/13-order-types-you-should-know-about), [QuantConnect Paper Trading / DefaultBrokerageModel](https://www.quantconnect.com/docs/v2/cloud-platform/live-trading/brokerages/quantconnect-paper-trading), [QuantConnect Slippage](https://www.quantconnect.com/docs/v2/writing-algorithms/reality-modeling/slippage/key-concepts), [QuantConnect Reconciliation](https://www.quantconnect.com/docs/v2/cloud-platform/live-trading/reconciliation), [Zerodha GTT](https://zerodha.com/z-connect/kite/introducing-gtt-good-till-triggered-orders), [Kite Orders manual](https://kite.trade/docs/kite/orders/), [Zerodha T+1 / MTF settlement](https://support.zerodha.com/category/trading-and-markets/margins/margin-trading-facility/articles/margin-trading-facility-mtf-faqs), [NautilusTrader duplicate-order-on-restart issue](https://github.com/nautechsystems/nautilus_trader/issues/3176).

---

## 2. Dashboard UI Element Catalog (Quartr-themed)

This catalog maps a best-in-class paper-trading / portfolio / forward-test dashboard onto Pivot's existing Quartr design tokens (from `pivot-next/app/globals.css`) and shadcn/ui primitives already vendored in `components/ui`. Survey base: Alpaca paper dashboard, Composer.trade (per-symphony scorecards), Koyfin (exposure analysis, drawdown chart + top drawdowns), Sharesight (contribution/attribution), Zerodha Console (P&L calendar heatmap), TradingView/lightweight-charts. Sources at the end.

> **Token convention used below:** all colors are CSS custom properties; reference them as `hsl(var(--…))` only where the token is HSL (shadcn base tokens like `--border`, `--muted-foreground`), and as the raw var `var(--…)` for the Quartr hex tokens (`--text-primary`, `--color-profit`, `--bg-card`, etc.). Profit is **always** `--color-profit`, loss **always** `--color-loss`, never raw green/red. All numerics use `font-mono` + `.tabular-nums`. All cards: `background: var(--bg-card)`, `border: 1px solid var(--glass-border)`, `border-radius: var(--radius-lg)`, hover → `border-color: var(--glass-border-hover)` + 1px translate, transitioned with `--ease-quartr`.

### Tier 1 — Portfolio core (build first)

#### 1. KPI / Stat Card Strip
**Data:** From a new `paper_account` + `paper_position` derivation. Fields: NAV (cash + Σ mkt value), total P&L (NAV − net deposits), day P&L (NAV − prior-close NAV), realized vs unrealized P&L, buying power (cash − reserved margin on open orders), win rate (% closed lots with realized P&L > 0). Each needs a current value + delta + an intraday sparkline (last N marks from the mark-to-market loop).
**Element:** 5–6 horizontally-scrolling stat cards mirroring the existing `DashboardTab` index strip. Each card = uppercase label (`.q-uppercase-label`, `--metric-label`), large mono value (`.q-display` / `--font-mono`, `--text-primary`, `.tabular-nums`), a signed delta pill, and an inline 40px sparkline.
**Quartr render:** Card = `--bg-card` / `--glass-border` / `--radius-lg`. Label `--metric-label` + `--font-ui`. Value `--font-mono`, `--weight-display` 550, `--text-primary`. Delta colored `--color-profit` / `--color-loss`; delta-pill background = same color at low alpha (`color-mix(in srgb, var(--color-profit) 12%, transparent)`). Day-P&L card is the hero — `--bg-elevated` on hover, `--shadow-cta`. Number ticker animates on each mark with `--ease-quartr`.
**shadcn:** `Card` + `CardHeader`/`CardContent`; `Badge` (token-overridden) for the delta pill; `Tooltip` on hover ("Buying power = cash − reserved margin").

#### 2. Equity / NAV Curve with Benchmark Overlay + Range Selector
**Data:** Time series of NAV marks (persisted to `paper_nav_snapshots`: `as_of_date, nav, cash, invested, day_pnl`). Benchmark = NIFTY 50 normalized to the same start value (reuse `yfinance_service`/`market_data`). Range selector drives the window: 1D / 1W / 1M / 3M / 1Y / ALL.
**Element:** Area chart (gradient fill under the line) for NAV + a thin comparison line for the benchmark. Crosshair tooltip showing NAV, benchmark, and spread on hover.
**Quartr render:** Primary line stroke = `--price-line`; area gradient = `--pivot-blue` fading to transparent. Benchmark line = `--text-tertiary` dashed. Crosshair line = `--glass-border-focus`. Range selector = pill segmented control: active pill `--bg-elevated` + `--text-primary`, inactive `--text-secondary`, container `--radius-pill`, `--ease-quartr` slide. Axis labels `--text-tertiary`, `--font-mono`, `.tabular-nums`. Gridlines `--glass-border`.
**shadcn:** `ToggleGroup`/`Tabs` for the range selector; `Card` wrapper.

#### 3. Holdings / Positions Table
**Data:** Per `paper_position`: symbol, qty, avg cost, LTP (live), mkt value, unrealized P&L (abs + %), day change, sector, and **source attribution** (which workflow/strategy/chat-idea opened it — joined via `source`/`source_id`/`idea_id`). The linchpin closing the orders↔portfolio gap.
**Element:** Dense sortable table with per-row sparkline, colored unrealized-P&L cell, sector chip, source chip. Row click → existing `StockDetailPage`. Sticky header, right-aligned mono numbers.
**Quartr render:** Header row `.q-uppercase-label` / `--metric-label`, border-bottom `--glass-border`. Cells `--font-mono` `.tabular-nums`, primary `--text-primary`, secondary (avg cost) `--text-secondary`. P&L cells `--color-profit`/`--color-loss`; P&L% cell gets a faint inline heat tint at low alpha. Row hover `--surface-hover`, active `--surface-active`. Sector + source chips = `Badge` with `--bg-secondary` bg, `--text-secondary`, `--radius-pill`. Negative qty (shorts) flagged with a `--color-loss` left border.
**shadcn:** `Table` family; `Badge`; `DropdownMenu` (close position, set SL/TP → `action.set_stoploss`); `Tooltip` for source provenance.

#### 4. Open-Orders Blotter (Resting LIMIT / GTT / SL / TP)
**Data:** `TradeLog`/`paper_orders` where `status IN (registered, pending, trigger_pending, resting)` plus GTT legs. Fields: symbol, side, type, qty, limit/trigger price, distance-to-trigger %, age, source. Cancel routes to existing `action.cancel_orders`/orders router.
**Element:** Live blotter with a status dot, distance-to-trigger micro-gauge, age timer, and a Cancel button per row.
**Quartr render:** Status dot — registered `--info`, pending `--warning`, near-trigger pulse `--color-warn`. Distance gauge fill `--pivot-blue`. Side buy `--color-profit`/sell `--color-loss`. Cancel = ghost `Button`, hover `--color-loss` border. Card `--bg-card`/`--radius-lg`.
**shadcn:** `Table`; `Button` (ghost/destructive); `AlertDialog` to confirm; `Badge` for type.

#### 5. Trade / Fill Journal (Blotter)
**Data:** All filled rows: timestamp, symbol, side, qty, fill price (`average_price`), value, fees/slippage (from `trading_costs.py`), realized P&L on closing lots, source. Paginated, filterable by symbol/date/source.
**Element:** Chronological journal grouped by day with day-subtotal rows; expandable row showing cost breakdown (brokerage / STT / slippage).
**Quartr render:** Day group header `.q-uppercase-label`, sticky, `--bg-secondary`. Buy/sell icon tinted profit/loss. Realized P&L mono colored. Expanded cost panel `--bg-elevated`, `--radius-md`. Fees `--text-tertiary`.
**shadcn:** `Table` + `Collapsible` rows; `Popover`/native date inputs for filters; `Tabs` to switch Journal ↔ Blotter.

### Tier 2 — Analysis & attribution

#### 6. Allocation Donut (Sector + Per-Idea)
**Data:** Σ mkt value grouped by (a) sector and (b) source idea/strategy + a cash slice.
**Element:** Two-ring donut (inner = sector, outer = per-idea) or a toggle between views, with a legend table (weight %, value, drift vs target). Center label = total NAV.
**Quartr render:** Categorical palette from `--pivot-blue`, `--info`, `--color-warn`, `--success`, plus neutral `--text-tertiary` for the cash slice — desaturated to stay on-brand. Segment hover lifts + dims siblings via opacity, `--ease-quartr`. Legend `--font-mono` `.tabular-nums`. Center NAV `.q-display`. Over-concentration (>X%) flagged `--color-warn`.
**shadcn:** `Tabs` (Sector / By Idea); `Card`; legend = `Table`. Chart = recharts `PieChart` donut (or reuse the hand-rolled `PortfolioTab` donut).

#### 7. Per-Strategy / Per-Agent Forward-Test Scorecards
**Data:** The second core aim. For each originating workflow/strategy/chat-idea (a `forward_ideas` rollup): live OOS return since first fill, realized + unrealized P&L, win rate, # trades, avg hold, max drawdown, Sharpe (reuse `services/backtest_metrics.py`), and — the differentiator — **backtest-vs-live divergence**.
**Element:** Grid of compact scorecards: idea name, mini equity sparkline, headline live return, a stat row (win rate / trades / Sharpe / max DD), and a "live vs backtest" delta chip. Sortable by live return. Click → drill-in.
**Quartr render:** Card `--bg-card`/`--radius-lg`; headline return mono colored. Sparkline by sign. "Beating backtest" chip `--color-profit` bg-tint, "lagging" `--color-loss`. Stat labels `--metric-label`. Winner card gets `--glass-border-hover` + `--shadow-cta`.
**shadcn:** `Card` grid; `Badge` chips; `Tooltip`; `HoverCard`/`Tooltip` for the methodology note. Sparkline = recharts mini `AreaChart`.

#### 8. Drawdown Chart
**Data:** Derived from the NAV series — running peak and `(nav − peak)/peak`. Plus a "Top Drawdowns" table (start, trough, recovery, depth %, duration).
**Element:** Underwater area chart (always ≤ 0, filled downward) beneath the equity curve, sharing the range selector. Companion top-5 table.
**Quartr render:** Fill `--color-loss` at ~14% alpha, line `--color-loss`. Zero baseline `--glass-border`. Current-drawdown readout pinned top-right in mono. Table depth cells colored by severity (alpha ramp of `--color-loss`).
**shadcn:** `Card`; `Table` for top drawdowns.

#### 9. P&L Attribution / Contribution
**Data:** Sharesight-style breakdown of total return into components per holding and per idea: price gain (capital), realized vs unrealized split, fees drag. Top contributors and detractors.
**Element:** Horizontal diverging bar chart (contributors right in profit color, detractors left in loss) + a winners/losers treemap. A small waterfall from start-NAV → end-NAV showing each idea's contribution.
**Quartr render:** Bars `--color-profit`/`--color-loss`, labels `--text-secondary`, values mono. Treemap tiles tinted by P&L magnitude (alpha ramp); tile label `--text-primary`. Waterfall connectors `--glass-border`.
**shadcn:** `Card` + `Tabs` (Contributors / Treemap / Waterfall). Diverging bars + waterfall = recharts `BarChart`; treemap = nivo `ResponsiveTreeMap`, lazy-loaded client-only (only if treemap is built).

### Tier 3 — Context, watch, and feel

#### 10. Calendar / Heatmap of Daily Returns
**Data:** Day P&L (abs or %) per calendar day from the NAV series.
**Element:** GitHub-style month/quarter heatmap; cell hover → tooltip with date, day P&L, # trades. Can live in the existing `CalendarTab.tsx`.
**Quartr render:** Cell color = alpha ramp on `--color-profit` (gains)/`--color-loss` (losses), zero = `--bg-secondary`. Grid gaps reveal `--bg-base`. Labels `--text-tertiary` `.q-uppercase-label`. Today cell ringed `--glass-border-focus`.
**shadcn:** `Tooltip` per cell; custom CSS grid (no chart lib needed).

#### 11. Watchlist
**Data:** `WatchlistItem` rows + live quote, day change %, sparkline, "has open position / open order" flag linking to the blotter.
**Element:** Compact list: symbol, LTP, day %, sparkline, quick-add-order button.
**Quartr render:** Day % colored; sparkline by sign; row hover `--surface-hover`. Quick-buy `Button` ghost → order preview. Symbol `--font-ui` medium, price `--font-mono`.
**shadcn:** `Table`/list; `Button`; `Sheet` for the quick-order drawer.

#### 12. Activity Feed
**Data:** Unified event stream: order registered → triggered → filled, GTT hit, workflow run executed (`WorkflowRun`/`WorkflowRunStep`), SL/TP fired, idea opened/closed.
**Element:** Vertical timeline with typed event icons, relative timestamps, inline links.
**Quartr render:** Timeline rail `--glass-border`; event dots by type (fill `--color-profit`/`--color-loss`, system `--info`, trigger `--color-warn`). Relative time `--text-tertiary`. Card `--bg-card`. New events fade-in via `--ease-quartr`.
**shadcn:** `ScrollArea`; `Avatar`/icon; `Separator`; `HoverCard` for detail.

### Charting library recommendation

| Library | Bundle | SSR / Next 15 | Theming via CSS tokens | Financial fit |
|---|---|---|---|---|
| **lightweight-charts** (TradingView) | ~45KB, Canvas | Client-only (`"use client"` + dynamic import, `ssr:false`) | Themed in JS via `applyOptions`; read tokens with `getComputedStyle(...).getPropertyValue('--…')` | **Best** for area/line/candlestick equity curves, crosshair, dense intraday |
| **recharts** | Heavier, SVG | SSR-friendly; SVG styles with `var(--…)` directly | **Easiest** | Great for donut, diverging bars, waterfall, sparklines; weak candlesticks |
| **visx** | ~15KB, SVG | Best for static SSR | Full control, high effort | Overkill here |
| **nivo** | Per-package | Needs client boundary | Theme object via JS | **Treemap** standout; otherwise redundant |

**Decision (reconciled with Section 7):** **Reuse `recharts@2.15.3`, already in `package.json` and already wired for portfolio performance.** recharts SVG covers every Paper surface (area equity curve, underwater drawdown, donut, diverging bars, sparklines) and themes by accepting `var(--token)` directly in `stroke`/`fill` — the existing repo pattern. **Do not add lightweight-charts for v1** (it would only pay off for high-density intraday candlesticks on `StockDetailPage`, out of scope here). nivo (treemap) is the *only* optional extra, lazy-loaded client-only, and only if the P&L treemap (#9) is built. Centralize a `getChartTheme(themeTick)` helper that reads `--price-line`, `--pivot-blue`, `--color-profit`, `--color-loss`, `--glass-border`, `--glass-border-focus`, `--text-tertiary` once; subscribe to the `.dark` class toggle via a `MutationObserver` for the gradient `<stop>` exception (see Section 7c).

### Micro-interactions & polish ("premium fintech")

- **Number tickers:** animate KPI and NAV values on each mark (count-up/roll) eased with `--ease-quartr`; never reflow — reserve width with `.tabular-nums` + `--font-mono`.
- **Tabular-nums everywhere numeric**; right-align all money columns.
- **Sparklines** in every KPI card, position row, watchlist row, scorecard — stroke by sign, no axes.
- **Skeletons** (shadcn `Skeleton`) shaped like the real module using `--bg-secondary` shimmer — not spinners.
- **Empty states:** before any paper trade exists, each module shows a Quartr-serif (`--font-serif`) one-liner + a CTA pill ("Register your first idea in chat").
- **P&L color semantics are absolute:** profit `--color-profit`, loss `--color-loss`, warn `--color-warn`; zero/neutral `--text-secondary`. Never hardcode hex.
- **Hover lifts:** cards translate `-1px` and shift `--glass-border` → `--glass-border-hover` over ~160ms `--ease-quartr`; pressable rows `--surface-hover` → `--surface-active`.
- **Live pulse:** a subtle dot on "live" modules during market hours; when stale, dim to `--text-disabled` with an "as of HH:MM" stamp.
- **Flash-on-update:** position/blotter rows briefly tint `--color-profit`/`--color-loss` at low alpha when value changes on a new mark, then fade.
- **Range-selector and tab transitions** use the pill-slide pattern already in the codebase; respect `prefers-reduced-motion`.

### Top 10 must-have elements, ranked

1. **KPI/Stat card strip** — NAV, total P&L, day P&L, realized/unrealized, buying power, win rate.
2. **Equity / NAV curve with benchmark overlay + range selector.**
3. **Holdings / positions table with source attribution** — closes the orders↔portfolio gap.
4. **Open-orders blotter (resting LIMIT/GTT/SL/TP) with cancel.**
5. **Trade / fill journal with fee+slippage breakdown.**
6. **Per-strategy / per-agent forward-test scorecards (live vs backtest)** — the unique differentiator.
7. **P&L attribution / contribution (contributors-detractors + treemap).**
8. **Allocation donut (sector + per-idea).**
9. **Drawdown chart + top-drawdowns table** — reusing `backtest_metrics.py`.
10. **Calendar / daily-returns heatmap.**

*(Watchlist, activity feed, and number-ticker polish are strong Tier-3 follow-ons.)*

**Data prerequisites this catalog implies (DB):** `paper_accounts` (cash ledger, buying power), `paper_positions` (derived from fills, with `source`/`source_id`/`idea_id` provenance), `paper_nav_snapshots` (mark-to-market time series for equity/drawdown/calendar), and a `forward_ideas` rollup keyed by originating workflow/strategy/chat-idea — all fed by the scheduler's mark-to-market loop and the existing `TradeLog`/`WorkflowRun` tables.

Sources: [Alpaca paper trading dashboard](https://alpaca.markets/learn/start-paper-trading), [Composer backtesting](https://www.composer.trade/learn/backtesting-basics), [Koyfin portfolio tools](https://www.koyfin.com/help/portfolio-tools-functionality/), [Sharesight contribution analysis](https://help.sharesight.com/contribution-analysis-report/), [Zerodha Console](https://zerodha.com/products/console/), [LogRocket React chart libraries 2025](https://blog.logrocket.com/best-react-chart-libraries-2025/).

---

## 3. Forward-Testing Methodology & Idea Scorecards

Forward testing is paper trading treated as **out-of-sample (OOS) validation**: instead of replaying history, you let each idea trade forward against live prices in Pivot's simulated broker and measure whether the edge that showed up in the backtest survives in unseen data. Paper trading and forward testing are *not* the same thing — paper trading just simulates execution; forward testing adds attribution, a fixed observation window, and a backtest-vs-live comparison so you can answer one question per idea: **"is the edge real out-of-sample, or was it curve-fit?"**

### 3.1 Attributing every paper fill to its originating idea

Every simulated fill must carry a stable **idea identity** so P&L can be sliced per idea. Pivot already has the raw materials: `TradeLog.source` + `TradeLog.source_id`, `WorkflowRun.id` / `Workflow.id`, `Strategy.id`, `Conversation.id`. Today `source` is `"chat"`/`"chat-confirm"`/`"sip"`; workflow actions route through `place_order`. We normalize this into a single attribution key.

**Idea taxonomy (the `origin_kind` of every fill):**

| origin_kind | Pivot anchor | How the fill gets tagged |
|---|---|---|
| `workflow` | `Workflow.id` (durable idea) + `WorkflowRun.id` (specific firing) | Action steps already know the run; stamp both onto the fill |
| `chat` | `Conversation.id` + message id; an LLM-named idea label | `/orders/register` & `/orders/confirm` already set `source="chat*"`; add conv id as `source_id` + human label |
| `strategy` | `Strategy.id` | SIP / structured-product / saved-strategy legs |
| `manual` | none | user-placed paper order with no idea behind it; still attributed to a synthetic "Manual" idea so NAV reconciles |

**The `idea` is the unit of forward-testing, distinct from a single run.** A workflow that fires 40 times over a quarter is **one** idea accumulating 40 runs' worth of fills. Introduce a thin **`forward_ideas`** registry row that owns: `origin_kind`, the originating id, a `label`, `inception_date` (first paper fill), `status` (`paper` → `candidate` → `promoted`/`retired`), and an optional `backtest_run_id` FK into `dsl_backtest_runs`. Each paper fill gets a nullable `idea_id` FK (back-filled at fill time).

**Why a registry and not just `GROUP BY source_id`:** an idea's identity is stable while triggering run-ids churn; promotion/retirement is a property of the idea, not the run; chat ideas need a human label the raw ids don't carry.

### 3.2 Per-idea scorecard (computed over the live window)

For each `forward_ideas` row we keep a **per-idea daily NAV series** (its slice of the simulated portfolio — cash committed to its open lots + mark-to-market of those lots). Every scorecard metric is derived from that series and the idea's fills, **reusing the single-source metrics module** so live numbers are computed identically to backtest numbers:

| Scorecard metric | Definition | How computed (reuse) |
|---|---|---|
| Cumulative return | idea NAV end/start − 1 | from idea daily-NAV snapshots |
| Annualized return (CAGR) | (end/start)^(365.25/days) − 1 | `backtest_metrics.calendar_cagr_pct()` |
| Sharpe / Sortino | annualized, rf = 6.5% G-Sec | `backtest_metrics.sharpe_sortino(daily_returns_from_equity(nav))` |
| **Alpha vs NIFTY** | idea return − β·(NIFTY return); **Information Ratio** = active return / tracking error | snapshot NIFTY daily alongside NAV |
| Max drawdown | worst peak-to-trough on idea NAV | from idea daily-NAV |
| Win rate, avg win / avg loss | per closed round-trip lot | from fills grouped into lots per idea |
| Exposure / turnover | avg gross exposure ÷ idea NAV; traded notional ÷ avg NAV | from positions + fills |
| **Hit rate of triggers** | fraction of trigger firings that produced a profitable position | `WorkflowRun` firings joined to the lot they opened |
| **Slippage vs intended price** | filled avg_price − intended/quoted price at decision, bps; vs the `slippage_bps()` the backtest assumed | `average_price` vs the quote captured at registration |
| **PSR / min-track-record flag** | probability true Sharpe > 0 given sample length + skew/kurtosis; MinTRL needed | new helper (Bailey–López de Prado); gates promotion |

All P&L is **after the same realistic costs** the backtester uses (`trading_costs.round_trip_bps()`) — the #1 reason paper results overstate live edge.

### 3.3 Backtest vs forward: "is the edge real out-of-sample?"

The headline view. Because the backtest already lives in `dsl_backtest_runs` (migration 0011: `result`, `total_return_pct`, window, tree) and the forward scorecard reuses the *same* metric functions, the two are directly comparable. For each idea with a linked `backtest_run_id`, render a side-by-side **degradation panel**:

| | Backtest (in-sample, `dsl_backtest_runs`) | Forward / paper (out-of-sample) | Decay |
|---|---|---|---|
| CAGR | 31% | 14% | −17pp |
| Sharpe | 1.8 | 0.7 | −1.1 |
| Max DD | −9% | −16% | worse |
| Win rate | 58% | 51% | −7pp |
| Realized slippage | assumed ~`slippage_bps()` | measured bps | drift |

**Interpretation rules (the OOS verdict):**
- **Healthy:** forward Sharpe within ~1 std-error band of backtest Sharpe, same sign of alpha, slippage near assumed bps. The edge generalizes.
- **Decayed:** forward Sharpe materially below backtest and below its own MinTRL threshold → curve-fit / regime-shift suspect.
- **Execution problem (not signal):** returns drop but the **slippage gap** explains most of it → implementation-shortfall issue, distinct from signal decay. Keep separate — the fix differs.

This mirrors mature platforms: QuantConnect scores a strategy by its **one-year OOS Sharpe with an explicit penalty proportional to how little OOS data exists** (6 months OOS → Sharpe halved). Pivot applies the same maturity discount so young paper ideas can't claim a high score on three weeks of luck.

### 3.4 Cohorting, lifecycle & statistical significance

**Lifecycle states** (on `forward_ideas.status`): `paper` → `candidate` → `promoted` (eligible for live) → or `retired`.

**Graduation gate (`paper` → `candidate` → `promoted`):** flag "promote to live" only when **all** hold:
1. **Minimum observation window met.** A calendar floor (≥ 60–90 trading days) *and* a **minimum sample of trades/trigger firings** (≥ 20–30 round-trips).
2. **MinTRL satisfied.** The **Probabilistic Sharpe Ratio** exceeds the confidence threshold (PSR > 0.95 that true Sharpe > 0) — lengthens automatically for noisier, fatter-tailed ideas.
3. **OOS consistency.** Forward Sharpe ≥ a fraction of backtest Sharpe (maturity penalty), and forward alpha vs NIFTY > 0.
4. **Execution sane.** Realized slippage not wildly above assumed `slippage_bps()`.

**Decay flagging (`promoted`/`candidate` → review/`retired`):** continuously monitor **alpha decay** and the **realized-vs-expected slippage gap**; trip a flag when rolling forward Sharpe falls below its MinTRL threshold or the slippage gap blows out during stress. Auto-flag for review (canary/rollback semantics), don't silently kill — surface it with the degradation panel as evidence.

**Cohorting for the dashboard:** group ideas by `origin_kind` (workflow vs chat vs strategy), by inception vintage (monthly cohorts), and by lifecycle state — so the dashboard answers "what % of paper ideas survive to candidate?"

**Pitfalls to encode (so the scorecard isn't self-deceiving):**
- **Look-ahead / data-snooping:** intended price and benchmark must be snapshotted **at decision time**, never recomputed later from adjusted history.
- **Selection bias / multiple testing:** the best idea's Sharpe is inflated by luck across the cohort — the **Deflated Sharpe Ratio** corrects this. Track the number of ideas trialed (`cohort_trial_count`) and deflate before promoting.
- **Survivorship:** the backtest carries a "current ticker only, no survivorship adjustment" caveat; the forward window is inherently point-in-time so it's clean — keep the caveat visible.
- **Paper-fill optimism:** paper trading systematically under-states slippage and ignores market impact/latency, so a paper edge is an **upper bound** on live; the explicit cost model + the slippage-gap metric are the guardrails.

### 3.5 Data you must snapshot (daily, per account AND per idea)

Mark-to-market via `kite/market_data.get_live_quote`/`yfinance_service`, run on the scheduler loop at market close.

- **Per-account daily NAV snapshot:** date, cash ledger balance, total positions market value, total equity/NAV, realized P&L to date, unrealized P&L, **NIFTY close**. One row/account/day.
- **Per-idea daily NAV snapshot:** date, `idea_id`, committed capital (open-lot cost basis), mark-to-market value of that idea's lots, idea NAV, realized + unrealized P&L, open exposure. One row/idea/day.
- **At-decision snapshot on each fill:** intended/quoted price at registration, quote timestamp, `idea_id`, `origin_kind`, originating `workflow_run_id`/`conversation_id`.
- **Per-idea backtest linkage:** `forward_ideas.backtest_run_id` → `dsl_backtest_runs.id`.

### Metrics to persist vs compute-on-read

**Persist (immutable facts you cannot reconstruct):**
- Daily **per-account NAV snapshot** (cash, positions MV, NAV, realized/unrealized, NIFTY close).
- Daily **per-idea NAV snapshot**.
- Every **fill** with its **at-decision intended price + quote timestamp**, `idea_id`, `origin_kind`, originating run/conv id, and the cost components charged.
- The **`forward_ideas`** registry row: `inception_date`, `origin_kind`, originating ids, `label`, `status`, `status_changed_at`, `backtest_run_id`, and `cohort_trial_count` (for DSR deflation).
- A small **point-in-time scorecard cache** written at each daily close (cumulative return, Sharpe, alpha, PSR, drawdown) — same pattern `dsl_backtest_runs` already uses with `total_return_pct`/`total_trades`.

**Compute-on-read (cheap, deterministic — never store):**
- Sharpe/Sortino, CAGR, max drawdown, win rate, avg win/loss, exposure, turnover, trigger hit-rate.
- Alpha/beta/Information Ratio vs the persisted NIFTY series.
- Realized slippage in bps and the slippage-vs-assumed gap.
- PSR / MinTRL / Deflated Sharpe.
- The **backtest-vs-forward degradation panel** (join persisted forward scorecard against `dsl_backtest_runs.result`).

**Rule of thumb:** persist the raw daily NAV and fill facts + the cohort trial count; **compute every ratio on read** using the existing `backtest_metrics.py` so backtest and forward numbers are guaranteed apples-to-apples — with one denormalized scorecard cache per idea per day for list-view performance.

**Key implementation anchors (absolute):**
- Reuse metrics: `/Users/karanveersingh/Downloads/Second_Star/pivot/backend/services/backtest_metrics.py` (`sharpe_sortino`, `daily_returns_from_equity`, `calendar_cagr_pct`, `methodology_note`) and `/Users/karanveersingh/Downloads/Second_Star/pivot/backend/services/trading_costs.py` (`round_trip_bps`, `slippage_bps`).
- Backtest store: `/Users/karanveersingh/Downloads/Second_Star/pivot/migrations/versions/0011_dsl_backtest_runs.py`.
- Attribution sources: `/Users/karanveersingh/Downloads/Second_Star/pivot/backend/models.py`; `/Users/karanveersingh/Downloads/Second_Star/pivot/backend/routers/orders.py`; `/Users/karanveersingh/Downloads/Second_Star/pivot/backend/workflows/steps/actions.py`.

**Sources:** [Forward vs Backtesting (PickMyTrade)](https://blog.pickmytrade.trade/forward-testing-vs-backtesting-2025-guide/), [Walk-Forward Optimization (QuantInsti)](https://blog.quantinsti.com/walk-forward-optimization-introduction/), [When is a strategy good enough to go live? (QuantConnect)](https://www.quantconnect.com/forum/discussion/15725/when-is-a-strategy-quot-good-enough-quot-to-go-live/), [PSR & MinTRL (Portfolio Optimizer)](https://portfoliooptimizer.io/blog/the-probabilistic-sharpe-ratio-hypothesis-testing-and-minimum-track-record-length-for-the-difference-of-sharpe-ratios/), [Deflated Sharpe Ratio — Bailey & López de Prado](https://www.davidhbailey.com/dhbpapers/deflated-sharpe.pdf), [TCA / alpha decay (KX)](https://kx.com/blog/drift-detections-blind-spot-how-live-tca-insights-help-firms-win-the-race-against-alpha-decay/).

---

## 4. Code Seams & Integration Points (verified)

### Call Sites: place_order, place_gtt_order, cancel_order, get_orders

**`backend/kite/orders.py`** (all public APIs):
- `place_order(access_token, tradingsymbol, exchange, transaction_type, quantity, order_type, price=None, product="CNC", trigger_price=None, tag="pivot", variety="regular") → dict` — **signature ends at `variety`; no `client_request_id`, no `order_type` kwarg (verified L17)**. Mock → `{"order_id":"MOCK{counter}","status":"COMPLETE","message":...}`; Real → `kite.place_order(...)`.
- `place_gtt_order(access_token, tradingsymbol, exchange, transaction_type, quantity, trigger_price, limit_price, last_price) → dict`. Mock → `{"trigger_id":int,"status":"active","message":...}`.
- `get_orders(access_token) → list` — Mock returns `MOCK_ORDERS`.
- `cancel_order(access_token, order_id, variety="regular") → dict` — Mock returns `{"order_id":...,"status":"CANCELLED"}`.
- **No `get_positions` in orders.py** (portfolio reconciliation is separate; see `backend/kite/portfolio.py`).

**Call Sites in Routers:**
1. `backend/routers/orders.py:135` — POST /orders/confirm → `place_order(...)`; logs `TradeLog(status=result["status"], source="chat")`.
2. `backend/routers/orders.py:338` — POST /orders/gtt → `place_gtt_order(...)`.

**Call Sites in Scheduler:**
- `backend/scheduler.py:196` — Job `execute_due_sips` → `place_order(..., tag=f"sip_{sip.id}")`; logs `TradeLog(status=result["status"], source="sip", source_id=sip.id)`.

**Call Sites in Workflows (`backend/workflows/steps/actions.py`):**
1. `:244` — `execute_action_place_order` → `place_order(...)`. Returns `{order_id, status, client_request_id, symbol, side, executed_price, quantity, executed_value_inr, notional_inr_used}` (reads `result["average_price"]` → `executed_price`).
2. `:312` — `execute_action_cancel_orders` → `get_orders` → filter pending → `cancel_order`. Returns `{cancelled_count, order_ids}`.
3. `:411` / `:488` — `execute_action_set_stoploss` / `set_takeprofit` → `place_gtt_order(...)`.
4. `:581` — `execute_action_allocate_basket` → per-leg `place_order` (live short raises `NotImplementedError`).
5. `:664-665` and `:1010-1011` — squareoff legs → `place_order(..., order_type="MARKET", client_request_id=leg_req)` — **verified: passes `client_request_id` and `order_type` kwargs that `place_order` does not accept → latent `TypeError` whenever squareoff is reached.**

### Mock Mode Switch

**`backend/kite/auth.py`:** `KITE_MOCK_MODE: bool = not bool(settings.kite_api_key)` (L19), set at import, runtime-flippable via `set_kite_credentials`/`clear_kite_credentials`, propagated to `["backend.kite.orders","backend.routers.kite","backend.kite.ticker"]`. Every `orders.py` public fn checks `if KITE_MOCK_MODE:` first. Token fallback: callers with no KiteSession pass `"mock_token"` → same mock branch. **Default dev user always mock-fills.**

### Workflow Context

`backend/workflows/engine.py`: `client_request_id = sha1(f"{run_id}:{step_index}:{attempts}")` (deterministic idempotency), passed as `ctx.client_request_id`. `actions.py` reads `ctx.workflow.user_id`, `ctx.client_request_id` (40-char hex, used as `tag=f"wf_{...[:16]}"`), `ctx.db` (sync Session), `ctx.run.id` (UUID), `ctx.run.context` (inter-step bag).

### Scheduler

`backend/scheduler.py`: APScheduler, jobs `execute_due_sips`, `check_strategy_triggers`, `refresh_kite_tokens`, `send_daily_summary` (registered in `_register_jobs` ~L79), all IST. `backend/workflows/scheduler.py`: polls every 30s (`_POLL_INTERVAL_SECONDS=30`), `register_workflow_scheduler(scheduler)` adds cron job `"pivot_workflows_poll"`; fires `Workflow`/`WorkflowStep` rows where `status=="active"`, not expired, `step_type IN ("trigger.schedule","trigger.market_relative_time")`, `next_run_at <= fired_at`; `compute_next_run_at(cron, tz, after=None) → datetime(UTC)`. **A PaperBroker can register recurring jobs here** to drain resting orders and snapshot NAV.

### TradeLog usage

`backend/models.py:196–217`. Source values written today: `"chat"` (confirm), `"chat-confirm"` (register), `"sip"` (scheduler). **Gap: workflow `action.place_order` does NOT write TradeLog** — the broker result only lands in `ctx.run.context`. This is the PaperBroker's hook: intercept and log a fill row per execution.

| Site | Line | File | Source | Status |
|------|------|------|--------|--------|
| POST /orders/confirm | 147–161 | routers/orders.py | "chat" | result["status"] |
| POST /orders/register | 234–249 | routers/orders.py | "chat-confirm" | "registered" |
| SIP job | 209–221 | scheduler.py | "sip" | result["status"] |
| action.place_order | **NOT YET** | workflows/steps/actions.py | "workflow" | — (the gap) |

### services/trading_costs.py — Public API
`buy_cost(price, qty) → (net_debit, total_charges)`; `sell_cost(price, qty) → (net_credit, total_charges)` (no stamp on sell); `leg_bps(side) → float`; `round_trip_bps() → float` (~35–40 bps); `slippage_bps() → float`. Constants (env-overridable): `BROKERAGE_PER_ORDER` (₹20), `SLIPPAGE_PCT` (0.0005), `STT_PCT`, etc.

### services/backtest_metrics.py — Public API
`sharpe_sortino(daily_returns, rf_annual=0.065) → (sharpe, sortino)` (√252); `daily_returns_from_equity(equity) → list[float]`; `calendar_cagr_pct(start_value, end_value, start, end) → float` (calendar_days/365.25); `methodology_note(*, start, end, period_label) → dict`. Constants: `DEFAULT_RF_ANNUAL=0.065`, `_TRADING_DAYS=252`.

### Router Registration Pattern (`main.py:77–116`)
`from backend.routers.<module> import router as <alias>_router; app.include_router(<alias>_router)`. **Order matters** — `scheduled_router` mounts before `workflows_router` so `/api/workflows/scheduled-runs` isn't caught by the `/api/workflows/{id}` glob. New paper router: `app.include_router(paper_router)` after `orders_router` (~L78).

### Summary: PaperBroker Integration Seams

| Layer | Module | Seam | Signature |
|-------|--------|------|-----------|
| Orders | backend/kite/orders.py | place_order | `(token, symbol, exchange, tx_type, qty, order_type, price=None, product="CNC", trigger_price=None, tag="pivot", variety="regular") → {order_id, status, ...}` |
| Orders | backend/kite/orders.py | place_gtt_order | `(token, symbol, exchange, tx_type, qty, trigger_price, limit_price, last_price) → {trigger_id, status, ...}` |
| Orders | backend/kite/orders.py | cancel_order / get_orders | `(token, order_id, variety="regular")` / `(token)` |
| Mock Gate | backend/kite/auth.py | KITE_MOCK_MODE | bool (runtime-flippable) |
| WF Context | backend/workflows/engine.py | client_request_id | `sha1(run_id:step_index:attempts)` |
| WF Context | backend/workflows/steps/actions.py | execute_action_place_order | `(ctx) → {order_id, client_request_id, executed_price, ...}` |
| Scheduler | backend/workflows/scheduler.py | register_workflow_scheduler | polls 30s, fires due `next_run_at` rows |
| Costs | backend/services/trading_costs.py | buy_cost/sell_cost/leg_bps | `(price, qty) → (net, charges)` |
| Metrics | backend/services/backtest_metrics.py | sharpe_sortino/calendar_cagr_pct/methodology_note | — |
| Logging | backend/models.py | TradeLog | symbol, exchange, tx_type, order_type, qty, price, trigger_price, status, average_price, filled_qty, source, source_id, placed_at |
| Router | backend/main.py | app.include_router | `from backend.routers.<m> import router; app.include_router(router)` |

---

## 5. DB Conventions, Portfolio Endpoints & FE Wiring (verified)

### A. Alembic Migration Conventions
- Location: `/Users/karanveersingh/Downloads/Second_Star/pivot/migrations/versions/`. **Latest = `0012_workflow_expires_at` (verified head).** Next = `0013_*.py` with `down_revision = "0012_workflow_expires_at"`.
- **JSON/JSONB dual-dialect** (from 0011):
  ```python
  def _json_type(bind):
      if bind.dialect.name == "postgresql":
          return postgresql.JSONB(astext_type=sa.Text())
      return sa.JSON()
  ```
- **DateTime default**, dialect-branched: `sa.text("now()")` on PG / `sa.text("CURRENT_TIMESTAMP")` on SQLite.
- Snake_case table names; composite indexes named with table prefix (`ix_dsl_backtest_runs_user_started`); enums as `CheckConstraint` named `ck_*` on SQLite.

### B. `dsl_backtest_runs` Schema (Migration 0011 + Model)
ORM: `backend/workflows/dsl/backtest/models.py`. Columns: `id` String(36) PK `_uuid_str`; `user_id` Int FK; `tree`/`request`/`result` JSON/JSONB; `tree_summary` Text; `primary_symbol` String(32) indexed; `start_date`/`end_date` Date; `status` String(16) CHECK(running|succeeded|failed|cancelled); `error_message` Text; `started_at`/`finished_at` DateTime(tz); `total_return_pct` Float / `total_trades` Int (list-view convenience copies). Index `ix_dsl_backtest_runs_user_started (user_id, started_at DESC)`. `result` capped ~50KB JSON/row.

### C. Backend Model Conventions (`backend/models.py`)
- **PK style:** older user-facing tables use **Integer PK** (`User`, `Strategy`, `TradeLog`); workflow tables (≥Day 1) use **String(36) UUID** via `_uuid_str()` (L26–29).
- **Timestamps:** `Column(DateTime(timezone=True), server_default=func.now(), nullable=False)` + `onupdate=func.now()`.
- **Relationships:** `back_populates` both sides.
- **Enums:** `SQLEnum(SomeEnum, name="...", native_enum=False)` → PG ENUM / SQLite CHECK.
- **`TradeLog`** (L196–217): `id`(Int PK), `user_id`, `kite_order_id`(String(50), None for registered), `symbol`, `exchange`, `transaction_type`, `order_type`, `quantity`, `price`, `trigger_price`, `status`, `average_price`, `filled_quantity`, `source`(String(50)), `source_id`(Integer), `placed_at`, `updated_at`.

### D. Portfolio Endpoints & Response Shapes
Routers `backend/routers/portfolio.py`; services `backend/services/portfolio.py`; cache `backend/services/portfolio_cache.py` (30s Redis TTL); perf `backend/routers/portfolio_perf.py`.

| Endpoint | Response |
|---|---|
| `GET /portfolio/summary` | `PortfolioSummary {total_value, invested_value, total_pnl, total_pnl_pct, day_pnl, num_holdings}` |
| `GET /portfolio/holdings` | `Holding[] {tradingsymbol, exchange, quantity, average_price, last_price, pnl, day_change, day_change_percentage, sector}` |
| `GET /portfolio/sector` | `{sectors:[{sector,value,pct}], total_value, is_concentrated}` (flag >40%) |
| `GET /portfolio/products` | `ProductPosition[]` (active) |
| `GET /portfolio/yields` | `[{instrument, key, gross_yield_pct, after_tax_yield_pct, tax_slab_used, is_best}]` |
| `GET /api/portfolio/performance?period=1M..5Y` | `{period, points:[{t,v}], starting_value, ending_value, total_return, total_return_pct}` |

Caching seam: `get_summary_cached(user_id, token)` / `get_holdings_cached(...)` (Redis 30s); `invalidate(user_id)` exists but is **not wired post-order today**. Service `get_user_portfolio(user_id, db)` returns the workflow `fetch.portfolio` shape `{holdings, buying_power, total_value}`.

### E. Frontend Wiring (`pivot-next/`)

**Tab declaration (`AppShell.tsx` ~L73–90):**
```ts
type TabKey = "chat" | "portfolio" | "agents" | "calendar" | "screener";
const NAV_ITEMS = [{key:"chat",...},{key:"portfolio",...},{key:"agents",...},{key:"calendar",...},{key:"screener",...}];
const DEFAULT_TAB: TabKey = "chat";
```
**Hash router:** `readHashTab()` reads `window.location.hash`; render slot ~L513 (`{active === "portfolio" && <PortfolioTab />}`).

**API client (`lib/api.ts`):** `request<T>` (base `/api`) and `requestLegacy<T>` (strips `/api`, for `/portfolio`, `/orders`, `/auth`); `ApiResult<T>` discriminated union + `isError`; `setAuthTokenProvider`. Portfolio types `Holding`/`PortfolioSummary` (L480–511) + getters `getPortfolioSummary`/`getPortfolioHoldings` (via `requestLegacy`).

**Quartr theme tokens (`app/globals.css`):** light root + `.dark` override; exact tokens per the architecture note. Helper classes `.q-display`, `.q-mono`, `.q-greeting`, `.q-uppercase-label`.

### F. Charting library — `recharts@2.15.3` (already installed, `package.json` L44). No new dependency for v1.

### G. New Tables for Paper Trading
Authoritative schema is **Section 6(a)**. Summary: `paper_accounts`, `paper_orders`, `paper_fills`, `paper_positions`, `paper_ledger`, `forward_ideas`, `paper_nav_snapshots`, `paper_idea_nav_snapshots`. All follow the workflow-table convention (String(36) UUID PK via `_uuid_str`, JSON→JSONB, `DateTime(tz)` + `server_default=func.now()`).

> **Convention reconciliation (decision):** Sections 5 and 6 differed on table names and PK style for the new tables. **We adopt Section 6's schema and names verbatim** (e.g. `paper_accounts` not `paper_trading_account`; `paper_nav_snapshots` not `paper_trading_equity_curve`), because it is the more complete, FK-correct, forward-test-aware design and uses the String(36) UUID convention consistently. Section 5's `paper_trading_*` names are superseded.

---

## 6. Backend & Database Design

This synthesizes the verified seams into a build-ready plan. Guiding principles, all confirmed against the codebase:

1. **One broker seam.** `backend/kite/orders.py::place_order`/`place_gtt_order`/`get_orders`/`cancel_order` are the *only* functions chat (`routers/orders.py:135`), SIP (`scheduler.py:196`), and all eight workflow actions (`actions.py`) call. Make `orders.py` a per-account router that dispatches to a new `PaperBroker` or the existing Kite path. Zero changes downstream.
2. **Positions and cash are derived from the immutable fills log, never incrementally mutated counters** — the NautilusTrader restart-double-fill guard. `paper_positions` is a *cache* rebuildable from `paper_fills`.
3. **Reuse `trading_costs.py` for every cost** and `backtest_metrics.py` for every ratio. No new numbers; preserves the live↔backtest parity just fixed.
4. **Idempotency via a persisted `client_request_id` unique constraint.** Fix the latent bug first: `place_order` must accept `client_request_id` and `order_type` keyword args (squareoff already passes them — `actions.py:1011`, verified).

### (a) DATA MODEL

All new tables use the **workflow-table conventions** (`String(36)` UUID PK via `_uuid_str`, `JSON` → JSONB on PG, `DateTime(timezone=True)` + `server_default=func.now()`, `SQLEnum(..., native_enum=False)` → CHECK on SQLite / ENUM on PG). They live in `backend/models.py` after `TradeLog`. FK targets are the **integer** `users.id`/`conversations.id`/`strategies.id` and existing string-UUID workflow/backtest PKs.

#### Table 1 — `paper_accounts`

| Column | Type | Null | FK / Index | Default | Notes |
|---|---|---|---|---|---|
| `id` | String(36) | no | PK | `_uuid_str` | |
| `user_id` | Integer | no | FK→`users.id`, **unique index** | | one default book per user |
| `label` | String(64) | no | | `"default"` | future multi-book |
| `currency` | String(3) | no | | `"INR"` | |
| `starting_capital` | Float | no | | `150000.0` | seed = `MOCK_MARGINS` figure |
| `cash_settled` | Float | no | | `=starting_capital` | T+1-settled cash |
| `cash_available` | Float | no | | `=starting_capital` | `settled + unsettled_sell_credits − today_buy_debits` |
| `cash_reserved` | Float | no | | `0.0` | held against resting BUY orders |
| `mode` | SQLEnum(`PaperAccountMode`: `paper`/`live`) | no | | `paper` | broker-routing switch |
| `is_active` | Boolean | no | | `True` | |
| `created_at`/`updated_at` | DateTime(tz) | no | | `func.now()` / onupdate | |

`User.paper_account = relationship(..., uselist=False)`. **Buying power** (long-only CNC): `cash_available − cash_reserved`; no leverage; live shorts rejected.

#### Table 2 — `paper_orders` (order lifecycle, incl. resting LIMIT/GTT/SL/TP)

| Column | Type | Null | FK / Index | Default | Notes |
|---|---|---|---|---|---|
| `id` | String(36) | no | PK | `_uuid_str` | the synthetic `order_id` returned to callers |
| `account_id` | String(36) | no | FK→`paper_accounts.id`, index | | |
| `user_id` | Integer | no | FK→`users.id`, index | | |
| `client_request_id` | String(80) | yes | **UNIQUE index** | | idempotency; squareoff legs use `…:legN:SYM` (>40 chars → 80) |
| `symbol` | String(50) | no | index | | |
| `exchange` | String(10) | no | | `"NSE"` | |
| `transaction_type` | String(10) | no | | | BUY/SELL |
| `order_type` | String(16) | no | | | MARKET/LIMIT/SL/SL-M/GTT |
| `product` | String(8) | no | | `"CNC"` | |
| `variety` | String(16) | no | | `"regular"` | regular/amo |
| `quantity` | Integer | no | | | |
| `limit_price` | Float | yes | | | |
| `trigger_price` | Float | yes | | | |
| `intended_price` | Float | yes | | | **LTP at decision** (slippage-vs-intended) |
| `intended_quote_at` | DateTime(tz) | yes | | | quote timestamp (look-ahead guard) |
| `status` | SQLEnum(`PaperOrderStatus`) | no | index | `pending` | `pending`/`queued`/`resting`/`partially_filled`/`filled`/`cancelled`/`rejected` |
| `reserved_cash` | Float | no | | `0.0` | released on fill/cancel |
| `filled_quantity` | Integer | no | | `0` | |
| `reject_reason` | String(200) | yes | | | `insufficient_buying_power`, `slice_too_small` |
| `gtt_oco_group` | String(36) | yes | index | | OCO siblings; one fill cancels the other |
| `parent_order_id` | String(36) | yes | FK→`paper_orders.id` | | bracket entry → SL/TP children |
| `source` | String(50) | yes | index | | mirrors `TradeLog.source` |
| `origin_kind` | String(16) | yes | | | `workflow`/`chat`/`strategy`/`manual` |
| `workflow_id` | String(36) | yes | FK→`workflows.id` | | durable idea |
| `workflow_run_id` | String(36) | yes | FK→`workflow_runs.id` | | the firing |
| `conversation_id` | Integer | yes | FK→`conversations.id` | | chat idea |
| `strategy_id` | Integer | yes | FK→`strategies.id` | | SIP / saved strategy |
| `idea_id` | String(36) | yes | FK→`forward_ideas.id`, index | | resolved at insert |
| `created_at`/`updated_at` | DateTime(tz) | no | | `func.now()` / onupdate | |

`place_gtt_order` writes an `order_type="GTT"` row, `trigger_price` set, `status="resting"`. SL+TP share a `gtt_oco_group` (OCO); scheduler cancels the sibling on first fill.

#### Table 3 — `paper_fills` (immutable executions — source of truth)

| Column | Type | Null | FK / Index | Default | Notes |
|---|---|---|---|---|---|
| `id` | String(36) | no | PK | `_uuid_str` | |
| `order_id` | String(36) | no | FK→`paper_orders.id`, index | | |
| `account_id` | String(36) | no | FK→`paper_accounts.id`, index | | |
| `user_id` | Integer | no | FK→`users.id`, index | | |
| `symbol` | String(50) | no | index | | |
| `transaction_type` | String(10) | no | | | BUY/SELL |
| `quantity` | Integer | no | | | |
| `fill_price` | Float | no | | | touch ± slippage (post-`SLIPPAGE_PCT`) |
| `gross_value` | Float | no | | | `fill_price * quantity` |
| `charges` | Float | no | | | from `buy_cost`/`sell_cost` (2nd tuple elt) |
| `net_cashflow` | Float | no | | | signed: − on buy, + on sell |
| `slippage_bps` | Float | yes | | | `(fill_price/intended_price−1)*1e4` |
| `realized_pnl` | Float | yes | | | booked on SELLs via avg-cost; null on BUYs |
| `settles_at` | DateTime(tz) | yes | | | T+1 for SELL proceeds |
| `idea_id` | String(36) | yes | FK→`forward_ideas.id`, index | | copied from order |
| `trade_log_id` | Integer | yes | FK→`trade_logs.id` | | link to existing audit row |
| `filled_at` | DateTime(tz) | no | index | `func.now()` | |

> **Cost wiring:** BUY → `(net_debit, charges) = buy_cost(fill_price, qty)`; `net_cashflow = −net_debit`. SELL → `(net_credit, charges) = sell_cost(fill_price, qty)`; `net_cashflow = +net_credit`; `realized_pnl = net_credit − qty*avg_cost`. **No new cost code.**

#### Table 4 — `paper_positions` (open lots — derived cache)

| Column | Type | Null | FK / Index | Default | Notes |
|---|---|---|---|---|---|
| `id` | String(36) | no | PK | `_uuid_str` | |
| `account_id` | String(36) | no | FK→`paper_accounts.id`, index | | |
| `user_id` | Integer | no | FK→`users.id`, index | | |
| `symbol` | String(50) | no | **unique(account_id,symbol)** | | |
| `quantity` | Integer | no | | `0` | long-only ≥0 |
| `avg_cost` | Float | no | | `0.0` | incl. buy charges |
| `realized_pnl` | Float | no | | `0.0` | cumulative for this symbol |
| `last_price` | Float | yes | | | from mark-to-market |
| `last_mark_at` | DateTime(tz) | yes | | | |
| `prev_close` | Float | yes | | | EOD snapshot for Day-P&L |
| `stale` | Boolean | no | | `False` | quote old / market shut → mark vs close |
| `updated_at` | DateTime(tz) | no | | onupdate | |

`unrealized_pnl = quantity*(last_price − avg_cost)`; `day_pnl = quantity*(last_price − prev_close)` — both **compute-on-read**.

#### Table 5 — `paper_ledger` (cash transactions — audit trail)

| Column | Type | Null | FK / Index | Notes |
|---|---|---|---|---|
| `id` | String(36) | no | PK `_uuid_str` | |
| `account_id` | String(36) | no | FK→`paper_accounts.id`, index | |
| `fill_id` | String(36) | yes | FK→`paper_fills.id` | null for seed/deposit |
| `kind` | String(24) | no | | `seed`/`buy_debit`/`sell_credit`/`reserve`/`release`/`settlement` |
| `amount` | Float | no | | signed |
| `balance_after` | Float | no | | running `cash_available` |
| `note` | String(200) | yes | | |
| `recorded_at` | DateTime(tz) | no | index, `func.now()` | |

#### Table 6 — `forward_ideas` (idea-attribution registry — the forward-test unit)

| Column | Type | Null | FK / Index | Default | Notes |
|---|---|---|---|---|---|
| `id` | String(36) | no | PK | `_uuid_str` | |
| `user_id` | Integer | no | FK→`users.id`, index | | |
| `account_id` | String(36) | no | FK→`paper_accounts.id`, index | | |
| `origin_kind` | String(16) | no | | | `workflow`/`chat`/`strategy`/`manual` |
| `workflow_id` | String(36) | yes | FK→`workflows.id`, index | | |
| `conversation_id` | Integer | yes | FK→`conversations.id` | | |
| `strategy_id` | Integer | yes | FK→`strategies.id` | | |
| `label` | String(140) | no | | | LLM/user-named |
| `inception_date` | Date | yes | | | first paper fill |
| `status` | SQLEnum(`ForwardIdeaStatus`: `paper`/`candidate`/`promoted`/`retired`) | no | | `paper` | lifecycle |
| `status_changed_at` | DateTime(tz) | yes | | | |
| `backtest_run_id` | String(36) | yes | FK→`dsl_backtest_runs.id` | | degradation-panel join |
| `cohort_trial_count` | Integer | no | | `1` | # ideas trialed (DSR deflation) |
| `scorecard_cache` | JSON | yes | | | list-view copy (cum_return, sharpe, alpha, psr, mdd) |
| `created_at`/`updated_at` | DateTime(tz) | no | | `func.now()` / onupdate | |

**Uniqueness** to dedup idea creation: enforce one idea per `(user_id, workflow_id)` / per `(user_id, conversation_id, label)` **in the resolver**, not a DB partial index (SQLite-friendly).

#### Table 7 — `paper_nav_snapshots` (account-grain daily equity curve)

| Column | Type | Null | FK / Index | Notes |
|---|---|---|---|---|
| `id` | String(36) | no | PK | |
| `account_id` | String(36) | no | FK, **unique(account_id, as_of_date)** | |
| `user_id` | Integer | no | FK, index | |
| `as_of_date` | Date | no | index | |
| `cash_available` / `cash_settled` | Float | no | | |
| `positions_mv` | Float | no | | Σ qty·LTP |
| `nav` | Float | no | | `cash_available + positions_mv` |
| `realized_pnl_cum` / `unrealized_pnl` | Float | no | | |
| `nifty_close` | Float | yes | | benchmark for alpha/IR |
| `is_stale` | Boolean | no | | marked-vs-close flag |
| `created_at` | DateTime(tz) | no | `func.now()` | |

#### Table 8 — `paper_idea_nav_snapshots` (idea-grain daily curve — forward scorecard series)

| Column | Type | Null | FK / Index | Notes |
|---|---|---|---|---|
| `id` | String(36) | no | PK | |
| `idea_id` | String(36) | no | FK→`forward_ideas.id`, **unique(idea_id, as_of_date)** | |
| `account_id` | String(36) | no | FK, index | |
| `as_of_date` | Date | no | index | |
| `committed_capital` | Float | no | | open-lot cost basis |
| `positions_mv` | Float | no | | MV of this idea's lots |
| `idea_nav` | Float | no | | committed-cash slice + MV |
| `realized_pnl` / `unrealized_pnl` | Float | no | | |
| `nifty_close` | Float | yes | | shared benchmark |
| `created_at` | DateTime(tz) | no | `func.now()` | |

> **Idea-grain lot accounting:** an idea owns the *lots its fills opened*. Tag each open lot via `paper_fills.idea_id`; on a SELL, decrement lots FIFO per idea. Store implicitly via FIFO over fills (no extra table for v1; add `paper_lots` only if FIFO-over-fills is too slow).

**Derive-on-read, never store:** Sharpe/Sortino, CAGR, max-drawdown, win rate, exposure/turnover, alpha/β/IR, realized-vs-assumed slippage gap, PSR/MinTRL/Deflated-Sharpe, and the backtest-vs-forward degradation panel. The only denormalized copy is `forward_ideas.scorecard_cache`, refreshed at each daily close.

### (b) MIGRATIONS

Three additive migrations chained off `0012_workflow_expires_at`. Pure `create_table` + one tiny `add_column` — **no destructive ALTERs**. Follow the 0011 pattern exactly.

| Rev | File | down_revision | Creates |
|---|---|---|---|
| `0013_paper_accounts_orders_fills` | `0013_paper_accounts_orders_fills.py` | `0012_workflow_expires_at` | `paper_accounts`, `paper_orders`, `paper_fills`, `paper_positions`, `paper_ledger` |
| `0014_forward_ideas` | `0014_forward_ideas.py` | `0013_…` | `forward_ideas`; **add `trade_logs.idea_id String(36) nullable` + index** (the one ALTER — additive nullable column, safe on PG & SQLite batch mode) |
| `0015_paper_nav_snapshots` | `0015_paper_nav_snapshots.py` | `0014_…` | `paper_nav_snapshots`, `paper_idea_nav_snapshots` |

Cross-dialect specifics, all proven in 0011:
- **JSONB/JSON:** `JSON_T = _json_type(op.get_bind())` for `forward_ideas.scorecard_cache`.
- **ENUMs:** `postgresql.ENUM("paper","candidate","promoted","retired", name="forward_idea_status").create(bind, checkfirst=True)` on PG; on SQLite `sa.String(16)` + `CheckConstraint("status IN (...)", name="ck_forward_ideas_status")`. Same for `paper_order_status`, `paper_account_mode`.
- **Indexes:** `create_index("ix_paper_orders_account_status", "paper_orders", ["account_id","status"])`; `create_unique_constraint("uq_paper_positions_acct_sym", "paper_positions", ["account_id","symbol"])`; `create_index("ux_paper_orders_client_req", "paper_orders", ["client_request_id"], unique=True, postgresql_where=sa.text("client_request_id IS NOT NULL"))` (partial on PG; plain unique on SQLite tolerates multiple NULLs).
- **`downgrade()`** drops in reverse dependency order; drop PG ENUM types last with `.drop(bind, checkfirst=True)`.
- **Backfill (data migration in 0014, optional, idempotent):** one pass over historical `TradeLog` where `source LIKE 'chat%'`/`'workflow'`/`'sip'` → create `forward_ideas` rows and stamp `trade_logs.idea_id`. Safe to skip on a fresh DB.

### (c) PAPER BROKER SERVICE

New package `backend/paper/` (sibling of `backend/kite/`):

```
backend/paper/
  __init__.py
  broker.py      # public API mirroring kite/orders.py — the drop-in
  accounts.py    # get_or_create_account, cash math, reserve/release, settlement roll
  fills.py       # synth-spread, fill-price, cost wiring (trading_costs), position upsert
  marks.py       # mark-to-market, NAV snapshot (acct+idea), reconcile-from-fills
  resting.py     # resting-order evaluator (LIMIT/STOP/GTT marketability on tick)
  ideas.py       # attribution resolver: order ctx → forward_ideas row, scorecard cache
  market_hours.py# is_market_open(IST, NSE calendar), next_session_open
```

**`broker.py` — byte-identical signatures (the drop-in):**
```python
def place_order(access_token, tradingsymbol, exchange, transaction_type,
                quantity, order_type, price=None, product="CNC",
                trigger_price=None, tag="pivot", variety="regular",
                client_request_id=None, *, db=None, user_id=None) -> dict
def place_gtt_order(access_token, tradingsymbol, exchange, transaction_type,
                    quantity, trigger_price, limit_price, last_price,
                    client_request_id=None, *, db=None, user_id=None) -> dict
def get_orders(access_token, *, db=None, user_id=None) -> list
def cancel_order(access_token, order_id, variety="regular", *, db=None, user_id=None) -> dict
```
Return shapes match today's mock: `place_order → {"order_id","status":"COMPLETE","average_price","filled_quantity","message"}` (`average_price` is what `actions.py:259` reads); `place_gtt_order → {"trigger_id","status":"active","message"}`.

**The account-routing switch — make `kite/orders.py` a thin shim:**
```python
def place_order(access_token, tradingsymbol, ..., client_request_id=None, order_type="MARKET", **kw):
    if _use_paper_broker(access_token):          # account.mode=="paper" OR KITE_MOCK_MODE OR token in (None,"","mock_token")
        from backend.paper import broker as paper
        return paper.place_order(access_token, tradingsymbol, ..., client_request_id=client_request_id, ...)
    # ... existing real-Kite body unchanged ...
```
**First, fix the signature** to accept `client_request_id` and `order_type` keyword args (the verified latent `TypeError` — `actions.py:665`, `:1011` already pass both). Thread `db`/`user_id` via a contextvar set by the request/run scope (chat router and engine both have `db` + `user_id`).

**Fill mechanics** (`fills.py`, all friction from `trading_costs.py`):
- Synthesize spread off LTP: `half_spread = max(0.05, LTP*0.0003)`; `ask=LTP+half`, `bid=LTP−half`.
- **MARKET, in-hours** → fill at `ask`(buy)/`bid`(sell), adverse slippage `×(1±SLIPPAGE_PCT)`; cost via `buy_cost`/`sell_cost`; ledger debit/credit; upsert position; status `filled`.
- **MARKET, after-hours/weekend** → `queued`, fills next open against opening quote (AMO).
- **LIMIT** → `resting`; reserve `limit_price*qty + est_charges` on BUY; filled by `resting.py` when `ask≤limit`(buy)/`bid≥limit`(sell), price-improved to touch.
- **SL/SL-M** → `resting`; on LTP crossing `trigger_price`, convert to MARKET (SL-M) or resting LIMIT (SL).
- **GTT** → `resting`, `order_type="GTT"`; SL+TP pair share `gtt_oco_group`; first fill cancels the sibling.
- **Idempotency:** `client_request_id` unique; on duplicate, **return the existing order's fill**. Transitions guarded by `SELECT … FOR UPDATE` (PG) / per-row status-guarded UPDATE in one transaction (SQLite).
- **Integer shares only**; sub-one-share → `rejected:slice_too_small`.

Every fill also writes a `TradeLog` row (`source` carried through, `kite_order_id=None`, `status="filled"`, `average_price`, `filled_quantity`) and stores `paper_fills.trade_log_id` — closing the "workflow actions don't log TradeLog" gap.

### (d) ENGINE INTEGRATION

**Nothing in `actions.py`, `routers/orders.py`, or `scheduler.py` (SIP) changes** — they import from `orders.py`, which now routes to the paper broker.

| Seam | File:line | Path | Change required |
|---|---|---|---|
| `action.place_order` | actions.py:244 | → paper; reads `result["average_price"]` | none |
| `action.allocate_notional` | actions.py (~229) | `qty=int(notional//ltp)` then `place_order` | none |
| `action.allocate_basket` | actions.py:581 | per-leg `place_order`; live short still raises | none |
| `action.set_stoploss` | actions.py:411 | → `place_gtt_order` (SELL GTT below entry) | none |
| `action.set_takeprofit` | actions.py:488 | → `place_gtt_order` (SELL GTT above entry) | none |
| `action.squareoff_*` | actions.py:1011 | `place_order(..., client_request_id=, order_type=)` | **fix `place_order` signature** (latent `TypeError`) |
| `action.cancel_orders` | actions.py:312 | `get_orders`→filter→`cancel_order` | none |
| chat `/orders/confirm` | routers/orders.py:135 | → paper `place_order` | none (or drop the TradeLog write to avoid double-log — broker writes it; pass `skip_trade_log`) |
| chat `/orders/register` | routers/orders.py:252 | keep register-not-execute; optionally enqueue if `execute=true` | additive flag only |
| SIP job | scheduler.py:196 | → paper `place_order` (`source="sip"`) | none |

**Attribution wiring (the only substantive addition):** add an **optional** `origin: dict|None` kwarg to `place_order` that `actions.py` and the chat router populate (`{"origin_kind","workflow_id","workflow_run_id","conversation_id","strategy_id","label"}`); the broker calls `ideas.resolve(...)` at insert time. Callers that omit it get `origin_kind="manual"`. Backward-compatible.

### (e) SCHEDULER JOBS

Two new APScheduler jobs in `backend/scheduler.py::_register_jobs()` (reuse the `IST`/`CronTrigger` pattern); they call into `backend/paper/`.

| Job id | Trigger (IST) | Function | Work |
|---|---|---|---|
| `paper_drain_resting` | every 1 min, `hour="9-15", minute="15-59"`, `mon-fri` | `resting.drain_due()` | For each `paper_orders.status IN (resting,queued)`: fetch LTP, test marketability/trigger, fill atomically, release/convert reserves, cancel OCO siblings. Skips when `is_market_open()` False. |
| `paper_eod_snapshot` | `hour=15, minute=35, mon-fri` | `marks.snapshot_all()` | Mark positions vs close, write `paper_nav_snapshots` (+ `nifty_close`), set `prev_close`; write `paper_idea_nav_snapshots`; refresh `forward_ideas.scorecard_cache`; run `reconcile()` (Σ fills → positions/cash) to heal orphaned reserves. |

Queued-AMO drain at 09:15 is covered by the first job's first tick. Lazy MTM on dashboard reads means the curve doesn't depend on perfectly-timely snapshots. Register with `replace_existing=True`; update the "Registered N scheduler jobs" log.

### (f) API SURFACE

New router `backend/routers/paper.py` → `APIRouter(prefix="/paper", tags=["Paper Trading"])`, registered after `orders_router`. **Mirrors `/portfolio` shapes** so the FE can reuse `Holding`/`PortfolioSummary` types (legacy base, no `/api` prefix — call via `requestLegacy`).

| Method · Path | Response shape | Notes |
|---|---|---|
| `GET /paper/account` | `{id, mode, currency, starting_capital, cash_available, cash_reserved, cash_settled, buying_power}` | new |
| `GET /paper/summary` | `PortfolioSummary` | **identical to `/portfolio/summary`** |
| `GET /paper/holdings` | `Holding[]` (+`sector`) | **identical to `/portfolio/holdings`**; `day_change*` now real |
| `GET /paper/positions` | `PaperPosition[] {id, symbol, quantity, avg_cost, last_price, unrealized_pnl, realized_pnl, day_pnl, stale, idea_id}` | richer |
| `GET /paper/orders?status=` | `PaperOrder[]` (lifecycle, resting filter) | new |
| `GET /paper/fills?since=` | `PaperFill[] {symbol, side, quantity, fill_price, charges, slippage_bps, realized_pnl, filled_at, idea_id}` | new |
| `GET /paper/equity-curve?range=1D..ALL` | `{range, start_nav, points:[{t, nav, benchmark, cash, invested, day_pnl}]}` | **same shape as `/api/portfolio/performance`** + benchmark series |
| `GET /paper/ideas` | `ForwardIdea[] {id, label, origin_kind, status, inception_date, scorecard:{...}}` | reads `scorecard_cache` |
| `GET /paper/ideas/{id}/scorecard` | full scorecard computed-on-read + `degradation:{backtest, forward, decay}` + `psr`, `min_trl`, `deflated_sharpe`, `methodology_note(...)` | reuses `backtest_metrics.*` + `round_trip_bps()` |
| `POST /paper/account/reset` | reseed to `starting_capital`, wipe positions/orders/fills | dev convenience |

All ratio endpoints call `sharpe_sortino`/`calendar_cagr_pct`/`methodology_note` so paper numbers are **identical-by-construction** to backtest numbers.

> **Endpoint-path reconciliation (decision):** Sections 4/6 used `/paper/equity-curve`; Section 7's FE client uses `getPaperEquityCurve` → `/paper/equity-curve` and `getPaperOpenOrders` → `/paper/orders/open`. **Canonical paths:** `GET /paper/equity-curve?range=`, `GET /paper/orders/open` (a convenience alias for `GET /paper/orders?status=open`), `POST /paper/orders/{id}/cancel`. Build both the `?status=` filter and the `/open` alias.

### (g) RISKS, EDGE CASES, TEST PLAN

**Risks / edge cases (and the guard):**
- **Latent `TypeError` (must fix first):** `place_order` lacks `client_request_id`/`order_type` kwargs that squareoff passes (`actions.py:665`, `:1011`) — squareoff currently raises whenever reached. Fix in the same PR as the shim.
- **Double-fill on retry / restart:** derive positions/cash from immutable `paper_fills`; unique `client_request_id`; status-guarded atomic transition; `reconcile()` on each EOD tick.
- **Double cash-spend across resting BUYs:** `cash_reserved` held at placement; `buying_power = cash_available − cash_reserved`; released on fill/cancel.
- **Double TradeLog rows:** broker writes TradeLog; chat `/orders/confirm` also writes one — pass `skip_trade_log` (or move the write entirely into the broker).
- **Stale / market-closed pricing:** quote age > N min or market shut → mark vs last close, set `stale=True`; never fabricate a moving tick.
- **T+1 settlement display:** CNC sell proceeds immediately reusable; `settles_at=T+1` on the fill; EOD roll moves unsettled → `cash_settled`.
- **Look-ahead in forward scores:** snapshot `intended_price`/`intended_quote_at` and `nifty_close` **at decision/close**, never recomputed.
- **Out of scope v1 (document, don't silently break):** splits/dividends, live shorts, partial fills.
- **Selection bias:** `forward_ideas.cohort_trial_count` feeds Deflated-Sharpe before any promote.

**Test plan (pytest, cross-dialect — SQLite in-memory like existing tests; mirror 0011's dialect-branch test):**
1. **Migrations:** `alembic upgrade head` then `downgrade -3` on SQLite + a PG test URL; assert tables/enums/indexes create and drop cleanly; assert the `trade_logs.idea_id` add-column is reversible.
2. **Broker drop-in parity:** `orders.place_order(...)` with `mock_token` → asserts `paper_orders`+`paper_fills`+`TradeLog` rows, correct `cash_available` debit = `buy_cost(...)`, position upsert.
3. **Idempotency:** same `client_request_id` twice → one fill, second returns existing. Two concurrent `drain_due()` on one resting order → exactly one fill.
4. **Cost parity:** `paper_fills.charges == buy_cost(price,qty)[1]`; round-trip ≈ `round_trip_bps()`.
5. **Resting fills:** LIMIT below market → `resting`; crossing LTP → fills at touch; reserve released. GTT OCO pair → one fills, sibling auto-cancelled.
6. **Reserve/buying-power:** two resting BUYs exceeding cash → second `rejected:insufficient_buying_power`.
7. **Settlement:** SELL → `cash_available` up immediately, `settles_at=T+1`; EOD roll → `cash_settled`.
8. **MTM/NAV:** seed fills, run `snapshot_all()`, assert `paper_nav_snapshots.nav == cash + Σ qty·LTP`; stale quote → `is_stale=True`.
9. **Attribution + scorecard:** workflow run → fills carry `workflow_id`/`workflow_run_id`/`idea_id`; `forward_ideas` row created once; `GET /paper/ideas/{id}/scorecard` returns Sharpe via `sharpe_sortino` + a degradation panel joined to a seeded `dsl_backtest_runs` row; `methodology_note` present.
10. **Squareoff regression:** `execute_action_squareoff_all` no longer raises `TypeError` and books a SELL fill + realized P&L.

**Build order for an engineer:** (1) fix `place_order` signature + shim in `orders.py`; (2) migration 0013 + `paper/accounts.py`,`fills.py`,`broker.py`; (3) route chat+actions through it and verify squareoff; (4) migration 0014/0015 + `ideas.py`,`marks.py`,`resting.py` + 2 scheduler jobs; (5) `routers/paper.py`; (6) FE `PaperDashboard`.

---

## 7. Frontend Dashboard Design (Quartr-themed)

Build-ready FE design for the Paper-Trading surface in `pivot-next/`. It mirrors verified codebase conventions: inline-style components keyed to Quartr CSS tokens (as in `components/agent-panel/PortfolioTab.tsx`), the `ApiResult<T>` + `request`/`requestLegacy` fetch pattern in `lib/api.ts`, the `useLiveQuote` hook over the `liveQuoteManager` WS singleton, `recharts@2.15.3`, and `lucide-react` icons. **Every color is a token — never a hardcoded hex (the one carve-out is recharts `<stop>`/gradient fills; see §(c)).**

### (a) IA / Navigation & Routing

Add one top-level tab to `AppShell.tsx`. The Paper-Trading surface is its own destination, distinct from the existing Kite-mock `portfolio` tab (which stays for real-broker holdings).

```ts
type TabKey = "chat" | "portfolio" | "paper" | "agents" | "calendar" | "screener";
const NAV_ITEMS = [
  { key: "chat",      label: "Chat",      Icon: MessageSquare },
  { key: "paper",     label: "Paper",     Icon: FlaskConical },   // NEW — flask = forward-test
  { key: "portfolio", label: "Portfolio", Icon: PieChart },
  { key: "agents",    label: "Agents",    Icon: Settings },
  { key: "calendar",  label: "Calendar",  Icon: CalendarDays },
  { key: "screener",  label: "Screener",  Icon: BarChart2 },
];
```
```tsx
{active === "paper" && <PaperDashboard />}
```
Routing reuses the existing **hash router** (`readHashTab()`). Deep-link `#paper`. Sub-views use the vendored shadcn `Tabs` as a sub-router, mirrored into a **second hash segment** (`#paper/positions`, `#paper/ideas`):

| Sub-view | Label | Purpose | Primary endpoint(s) |
|---|---|---|---|
| `overview` | Overview | KPI strip + equity/NAV curve + drawdown + allocation + top positions + open orders | `/paper/summary`, `/paper/equity-curve`, `/paper/positions`, `/paper/orders/open` |
| `positions` | Positions | Full holdings table w/ source attribution + close/SL/TP actions | `/paper/positions` |
| `orders` | Orders | Open-orders blotter + cancel | `/paper/orders/open` |
| `journal` | Journal | Filled-trade journal grouped by day, fee/slippage breakdown | `/paper/fills` |
| `ideas` | Forward-Test | Per-idea scorecards + backtest-vs-live degradation drill-in | `/paper/ideas`, `/paper/ideas/{id}` |

Row clicks route to the existing `StockDetailPage` via `next/link` to `/stock/<symbol>`. The idea drill-in opens an in-page detail panel (Sheet), not a route change.

### (b) Component Tree

All under `pivot-next/components/paper/`, all `"use client"`. Each leaf takes already-fetched data as props; **only `PaperDashboard` fetches** (single-owner, like `PortfolioTab`'s `FetchState`). Shared primitives in `paper/_shared.tsx`.

```
components/paper/
├── PaperDashboard.tsx        ← owner: fetch + sub-tab router + poll loop
├── _shared.tsx               ← Card, Section, PaperEmpty, PaperError, LivePulse, NumberTicker, DeltaPill, SourceChip, fmt*
├── KpiStatCards.tsx
├── EquityCurveChart.tsx
├── DrawdownChart.tsx
├── HoldingsTable.tsx
├── OpenOrdersBlotter.tsx
├── TradeJournal.tsx
├── AllocationDonut.tsx
├── IdeaScorecards.tsx
└── IdeaDetailPanel.tsx       ← backtest-vs-live degradation drill-in
```

For each component (props · data · shadcn/tokens · states):

- **`PaperDashboard.tsx`** — owns a `FetchState` union (`loading|error|ok`) like `PortfolioTab`. On mount + 15s poll, `Promise.all`s `getPaperSummary()`, `getPaperEquityCurve(range)`, `getPaperPositions()`, `getPaperOpenOrders()`. `ideas`/`journal` lazy-fetch on first activation. Outer `background: var(--bg-base)`; serif `<h1 className="q-serif">`; shadcn `Tabs` pill-slide (active `--bg-elevated` + `--glass-border-hover` + `--text-primary`). Delegates to `PaperLoading` (skeleton tree), `PaperError` (`role="alert"` + Retry), per-module `PaperEmpty`.
- **`KpiStatCards.tsx`** — `{summary, loading}`; `GET /paper/summary` → `nav, total_pnl(_pct), day_pnl(_pct), realized_pnl, unrealized_pnl, buying_power, win_rate, nav_spark`. 6 scrolling `Card`s; `.q-uppercase-label` on `--metric-label`; value `.q-display .tabular-nums --font-mono --text-primary`; `DeltaPill` colored, bg `color-mix(in srgb, var(--color-profit) 12%, transparent)`; 40px sparkline; **Day-P&L hero** → `--bg-elevated` hover + `--shadow-cta`; `Tooltip` on buying-power; `NumberTicker` eased `--ease-quartr`. Loading → 6 `Skeleton`; empty → CTA card "Register your first idea in chat".
- **`EquityCurveChart.tsx`** — `{range, onRangeChange, points, loading}`; `GET /paper/equity-curve?range=…`. recharts `AreaChart`: NAV stroke `--price-line`, area gradient `--pivot-blue`→transparent, benchmark `<Line>` dashed `--text-tertiary`; `<Tooltip contentStyle={{background:"var(--bg-card)",border:"1px solid var(--glass-border)",borderRadius:"var(--radius-md)"}}>`. Range pills reuse `PortfolioTab` markup (active `background:var(--text-primary);color:var(--bg-primary)`). Loading → chart-shaped `Skeleton`; empty → serif one-liner.
- **`DrawdownChart.tsx`** — `{points, topDrawdowns}` derived client-side from the same `points`. Underwater recharts `AreaChart` ≤ 0, fill `--color-loss`@14%, line `--color-loss`, zero baseline `--glass-border`. Companion top-5 `Table`. Hidden when < 2 NAV points.
- **`HoldingsTable.tsx`** — `{positions}`; `GET /paper/positions`. LTP live via `useLiveQuote(symbol)` per row. Sortable `<table>` reusing `PortfolioTab.HoldingsTable` markup; P&L `--color-profit`/`--color-loss`; sector + **source chip** = `Badge` on `--bg-secondary`; source `Tooltip` ("Workflow: RBI rate-cut · run #1287"); shorts get `--color-loss` left border; row → `/stock/<symbol>`; per-row `DropdownMenu` (Close / Set SL / Set TP) gated by `AlertDialog`. Empty → `PaperEmpty`.
- **`OpenOrdersBlotter.tsx`** — `{orders, onCancel}`; `GET /paper/orders/open`. Status dot (registered `--info`, pending `--warning`, near-trigger `--color-warn`), distance gauge fill `--pivot-blue`, age timer, side colored, Cancel ghost `Button` gated by `AlertDialog`. Empty → "No resting orders."
- **`TradeJournal.tsx`** — `{filters, onFilter}`; `GET /paper/fills?…`. Day-grouped `<table>`, sticky day headers `.q-uppercase-label`/`--bg-secondary`, day-subtotals; expandable cost panel via `Collapsible` on `--bg-elevated`; filters `Select` + native `<input type="date">`. Empty → "No fills yet."; cursor pagination "Load more".
- **`AllocationDonut.tsx`** — `{positions, cash}`. shadcn `Tabs` Sector/By-Idea; reuse `PortfolioTab` SVG donut + `arcPath`; palette `--pivot-blue, --info, --color-warn, --success` + `--text-tertiary` cash; over-concentration `--color-warn`. Empty → "No allocation data".
- **`IdeaScorecards.tsx`** — `{ideas, sort, onSort, onOpen}`; `GET /paper/ideas`. Grid of `Card`s; headline return mono colored; sparkline by sign; "Beating backtest" / "lagging" `Badge` tints; lifecycle pill (`paper`→`--info`, `candidate`→`--warning`, `promoted`→`--color-profit`, `retired`→`--text-disabled`); winner `--glass-border-hover` + `--shadow-cta`; `Tooltip` methodology. Empty → serif one-liner.
- **`IdeaDetailPanel.tsx`** — `{ideaId, onClose}`; `GET /paper/ideas/{id}`. Opens as a shadcn `Sheet` from the right. Side-by-side degradation `Table` (`backtest` from `dsl_backtest_runs`, `forward` from live, `Decay` colored); verdict `Badge` (`healthy|decayed|execution-problem`); per-idea `EquityCurveChart` reused; slippage-gap rows get an `--info` "execution, not signal" annotation; PSR/MinTRL flag.

### (c) Charts

**Decision: reuse `recharts@2.15.3`** (already installed, already wired). No new dependency; lightweight-charts is **not** added for v1. Theming rules:
1. **Strokes & text → CSS vars directly** (`stroke="var(--price-line)"`, grid `stroke="var(--glass-border)"`) — re-resolve automatically on `.dark` toggle.
2. **Gradient `<stop>` fills are the one exception** — `stop-color` doesn't reliably inherit a CSS var, so read the token once at runtime:
   ```ts
   export function readToken(name: string): string {
     if (typeof window === "undefined") return "";
     return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
   }
   export function useThemeTick(): number { /* MutationObserver on .dark → bumps a counter → charts re-render */ }
   ```
3. **Profit/loss absolute** — `--color-profit`/`--color-loss`/`--color-warn`/neutral `--text-secondary`.
4. **Tooltip chrome:** `contentStyle={{background:"var(--bg-card)",border:"1px solid var(--glass-border)",borderRadius:"var(--radius-md)",fontFamily:"var(--font-mono)"}}`.
5. **`<Sparkline>` helper:** one shared recharts mini `AreaChart`, no axes/grid/tooltip, `isAnimationActive={false}`, 40px tall, stroke by sign.

Centralize `getChartTheme(themeTick)` in `paper/_shared.ts` returning `{navLine, areaTop, benchmark, profit, loss, grid, axis, crosshair}`.

### (d) Text Wireframes

**Overview page (`#paper/overview`)**
```
┌──────────────────────────────────────────────────────────────────────────────┐
│  Paper Trading                                                  ● live · 15:42 │  q-serif h1 + LivePulse
│  ┌Overview┐ Positions  Orders  Journal  Forward-Test            (sub-tabs)     │  shadcn Tabs, pill-slide
├──────────────────────────────────────────────────────────────────────────────┤
│  KPI STRIP  (scroll-x, 6 cards; Day-P&L is hero/elevated)                      │
│  ┌─────────┐┌─────────┐┌═════════┐┌─────────┐┌─────────┐┌─────────┐           │
│  │ NAV     ││ TOTAL   ││ DAY P&L ││ REALIZED││ BUYING  ││ WIN RATE│           │
│  │₹8,42,150││ +12.4%  ││ +₹3,210 ││ +₹18.4k ││ ₹1.20L  ││  58%    │           │  q-display mono + DeltaPill + spark
│  └─────────┘└─────────┘└═════════┘└─────────┘└─────────┘└─────────┘           │
├──────────────────────────────────────────────────────────────────────────────┤
│  EQUITY / NAV vs NIFTY 50                       [1D][1W][1M][3M][1Y][ALL]      │  range pills (--text-primary active)
│  8.4L │                                       ╱‾╲___╱‾‾‾‾‾╲╱                   │  NAV  = --price-line, area --pivot-blue
│  8.0L │                ____╱‾‾╲___╱‾‾‾‾╲___╱                                   │  NIFTY= --text-tertiary dashed
│       └────────────────────────────────────────────────────────────────────  │
│    vs NIFTY 50 ·  portfolio +12.4%   benchmark +6.1%   alpha +6.3%            │  mono, profit/loss colored
├───────────────────────────────────────┬──────────────────────────────────────┤
│  DRAWDOWN (underwater)   curr −3.2%    │  ALLOCATION   ┌Sector┐ By Idea        │
│   0 ─────────────────────────────────  │        ╭───────────╮                  │
│      ▼▼▼          ▼▼▼▼▼                 │       │   ₹8.42L   │  ■ Banking 28%   │  donut center=NAV (q-display)
│    −8% (--color-loss @14% fill)        │        ╰───────────╯  ■ Cash    14%   │
├───────────────────────────────────────┴──────────────────────────────────────┤
│  TOP POSITIONS (5)                                          [View all →]       │
│  SYMBOL    QTY   AVG     LTP●   UNREAL P&L   DAY    SECTOR    SOURCE            │  uppercase-label header
│  RELIANCE   40  2,840  2,910●  +₹2,800 +2.4% +0.6% [Energy]  [WF: RBI-cut]     │  P&L colored, chips=Badge
├──────────────────────────────────────────────────────────────────────────────┤
│  OPEN ORDERS (resting)                                                         │
│  ◐ HDFCBANK BUY   LIMIT  20  1,640    −1.2%▕▏ 2h 14m  [WF: dip]     [ Cancel ] │  dot=--warning, gauge=--pivot-blue
└──────────────────────────────────────────────────────────────────────────────┘
```

**Forward-Test / Ideas page (`#paper/ideas`)**
```
┌──────────────────────────────────────────────────────────────────────────────┐
│  Paper Trading ›  Forward-Test            Sort: [Live return ▾]  Cohort:[All ▾]│
├──────────────────────────────────────────────────────────────────────────────┤
│  ┌══════════════════════┐ ┌──────────────────────┐ ┌──────────────────────┐   │
│  │ RBI rate-cut buy   ★ │ │ EMA200 dip-buy       │ │ Gold SIP             │   │  winner ★ = --glass-border-hover
│  │ [workflow] [promoted]│ │ [chat] [candidate]   │ │ [strategy] [paper]   │   │  origin + lifecycle Badges
│  │  +18.6%  since 12 Mar│ │  +4.1%   since 28 Apr│ │  +1.2%  since 02 May │   │  headline mono, profit color
│  │  Win 61% · 18 trades │ │  Win 50% · 6 trades  │ │  Win 100% · 2 trades │   │  --metric-label stats
│  │  ▲ Beating backtest  │ │  ▼ Lagging −1.1 Shp  │ │  ⌁ Too few trades    │   │  delta chip profit/loss/warn
│  └══════════════════════┘ └──────────────────────┘ └──────────────────────┘   │
│  ── Drill-in (Sheet): RBI rate-cut buy ──────────────────────────────────────  │
│        | metric    | Backtest (IS) | Forward (OOS) | Decay   |  verdict        │
│        | CAGR      |     31%       |     14%       | −17pp   |  ┌───────────┐   │
│        | Sharpe    |     1.8       |     1.2       | −0.6    |  │  HEALTHY  │   │  verdict Badge (--color-profit)
│        | Slippage  |  ~8 bps (assm)|   11 bps      | +3 bps  |  PSR 0.91·MinTRL │  within band
└──────────────────────────────────────────────────────────────────────────────┘
```

### (e) `lib/api.ts` Additions + Live Updates

Append to `lib/api.ts`, mirroring the portfolio block. New `/paper/*` routes are legacy (alongside `/portfolio/*`, `/orders/*`), so all use `requestLegacy`. Types: `PaperRange`, `SparkPoint`, `PaperSummary {nav, cash, invested, buying_power, total_pnl(_pct), day_pnl(_pct), realized_pnl, unrealized_pnl, win_rate, nav_spark, as_of, is_live}`, `IdeaSource {origin_kind, idea_id, idea_label}`, `PaperPosition {id, symbol, exchange, quantity, avg_cost, ltp, mkt_value, unrealized_pnl(_pct), day_change_pct, sector, source, spark}`, `NavPoint {t, nav, benchmark, cash, invested, day_pnl}`, `PaperEquityCurve {range, start_nav, points}`, `PaperOpenOrder {id, symbol, side, order_type, quantity, limit_price, trigger_price, distance_pct, status, age_seconds, source}`, `PaperFill {id, ts, symbol, side, quantity, average_price, value, brokerage, stt, slippage, realized_pnl, source}`, `IdeaScorecard {id, label, origin_kind, status, inception_date, live_return_pct, realized_pnl, unrealized_pnl, win_rate, trades, sharpe, max_dd_pct, vs_backtest_sharpe_delta, verdict, spark}`, `IdeaDetail {scorecard, nav_points, degradation, psr, min_trl_met, methodology_note}`.

Fetchers (mirror `getPortfolioSummary`): `getPaperSummary`, `getPaperPositions`, `getPaperEquityCurve(range)`, `getPaperOpenOrders`, `cancelPaperOrder(id)` (POST), `getPaperFills(params)`, `getPaperIdeas(sort)`, `getPaperIdeaDetail(id)` — all via `requestLegacy`, consumed with `if (!isError(r)) setX(r.data)`.

**Live updates — hybrid, reusing existing plumbing:**
- **Per-row LTP is already live** via the WS path: `HoldingsTable`/`OpenOrdersBlotter` rows call `useLiveQuote(symbol)` over the shared `liveQuoteManager` singleton — no new WS. Green dot = `liveQuote.isLive`.
- **Account-level NAV / P&L / equity curve poll** (15s `setInterval`, cleared on unmount, paused when `document.hidden`) re-`Promise.all`s the four account endpoints. Matches the scheduler mark-to-market cadence and the 30s portfolio cache TTL. KPI values feed `NumberTicker`.
- **No new WS endpoint** for v1.

### (f) Accessibility, Number Formatting & Motion

- **Number formatting (en-IN, tabular-nums):** reuse `fmtRupee`, `fmtPct`, `fmtINR` (Cr/L/k) from `PortfolioTab` (L102–119) + the `Intl.NumberFormat("en-IN",{style:"currency",currency:"INR"})` pattern; lift into `paper/_shared.ts`. Every numeric cell `.tabular-nums` + `--font-mono`, money/percent right-aligned, minus as figure-dash `−` (U+2212).
- **Accessibility:** error cards `role="alert"`; live-pulse dot `aria-label="Live/Delayed price"`; sortable `<th scope="col">` buttons with `aria-sort`; color never the only signal (P&L pairs hue with `+`/`−` + arrow; chips carry text labels — WCAG 1.4.1); charts get `aria-label` summaries + visually-hidden data `<table>` fallback; Radix focus-trap/ESC inherited; focus rings `--glass-border-focus`.
- **Motion (`--ease-quartr`):** card hover translate `-1px` + border shift over ~160ms; pill/sub-tab slide; `NumberTicker` count-up; **flash-on-update** tints rows on value change then fades; **live pulse** dot driven by `summary.is_live`, stale → dim `--text-disabled` + "as of HH:MM"; **`prefers-reduced-motion`** kills tickers/flashes/hover-lift, keeps instant updates.

**Key reference files (absolute):** `/Users/karanveersingh/Downloads/Second_Star/pivot-next/components/agent-panel/PortfolioTab.tsx` (tab + table + donut + range pills + formatters to clone); `/Users/karanveersingh/Downloads/Second_Star/pivot-next/components/AppShell.tsx` (`TabKey`/`NAV_ITEMS`/render slot/`readHashTab`); `/Users/karanveersingh/Downloads/Second_Star/pivot-next/lib/api.ts`; `/Users/karanveersingh/Downloads/Second_Star/pivot-next/hooks/useLiveQuote.ts` over `/Users/karanveersingh/Downloads/Second_Star/pivot-next/lib/liveQuoteManager.ts`; `/Users/karanveersingh/Downloads/Second_Star/pivot-next/components/ui/` (`tabs`, `card`, `badge`, `dropdown-menu`, `alert-dialog`, `sheet`, `tooltip`, `skeleton`, `collapsible`); `recharts@2.15.3` in `package.json`; `/Users/karanveersingh/Downloads/Second_Star/pivot-next/app/globals.css`.

> **Note:** `components/ui/` has **no `table` primitive vendored** — the existing portfolio table is a hand-rolled `<table>` keyed to tokens; reuse that approach. `HoverCard` and `Calendar`/`DatePicker` are also **not** vendored — fall back to `Tooltip` for methodology notes and native `<input type="date">` for journal filters.

---

## 8. Phased Delivery Roadmap (orchestrated with Pivot Workflows)

The build is split into seven phases. Each is itself a candidate for orchestration via **Pivot's own Workflow engine** — we spawn fan-out agent steps per table / endpoint / component, gate state transitions on `action.*`-style approvals, and run an **adversarial verify** step before each phase exits. Dependencies are strictly sequential at the phase level; within a phase, fan-out is parallel.

> **Orchestration primitive used throughout:** a `Workflow` whose steps are `trigger.manual` → N parallel build sub-tasks (one agent each) → a `join` → an **adversarial-verify** sub-task that tries to break the deliverable → an `action`-style "promote phase" approval. This mirrors how the engine fans out `allocate_basket` legs under per-leg `client_request_id` — same fan-out/join shape, applied to build tasks.

### Phase P0 — Schema + Migrations
- **Goal:** All eight tables exist and round-trip on SQLite + PG; the `place_order` latent bug is fixed.
- **Deliverables:** ORM models in `backend/models.py` (Tables 1–8); migrations `0013`, `0014` (+ `trade_logs.idea_id` ALTER), `0015`; the `place_order` signature fix (accept `client_request_id`, `order_type` kwargs) with a no-op body change (still real-Kite for now).
- **Files:** `backend/models.py`; `migrations/versions/0013_paper_accounts_orders_fills.py`, `0014_forward_ideas.py`, `0015_paper_nav_snapshots.py`; `backend/kite/orders.py` (signature only).
- **DB migration:** yes — 0013/0014/0015.
- **Exit/acceptance:** `alembic upgrade head` then `downgrade -3` clean on both dialects; enums render ENUM (PG) / CHECK (SQLite); `pytest` model round-trip test green; squareoff no longer raises `TypeError` (signature accepts the kwargs even before the shim).
- **Workflow orchestration:** one workflow, **fan-out one agent per migration file** (3 parallel) + one for the model edits + one for the signature fix; `join`; **adversarial-verify** step runs `upgrade/downgrade` on a scratch PG container and on SQLite and asserts no orphan ENUM types. Promote-phase approval only when both dialect runs are green.
- **Depends on:** nothing.

### Phase P1 — Paper Broker + Fills (synchronous MARKET path)
- **Goal:** A `mock_token` `place_order` produces a real fill, cash debit, and position — derived from the immutable fills log.
- **Deliverables:** `backend/paper/` package (`broker.py`, `accounts.py`, `fills.py`, `market_hours.py`); MARKET in-hours fill + cost wiring via `trading_costs.buy_cost`/`sell_cost`; `paper_ledger` reserve/release scaffolding; `TradeLog` write per fill with `trade_log_id` back-link.
- **Files:** `backend/paper/broker.py`, `accounts.py`, `fills.py`, `market_hours.py`.
- **DB migration:** none (uses P0 tables).
- **Exit/acceptance:** Test 2 (drop-in parity), Test 4 (cost parity), Test 3 (idempotency — same `client_request_id` returns existing fill). `cash_available` debit == `buy_cost(...)`.
- **Workflow orchestration:** **fan-out per module** (`accounts`/`fills`/`broker`/`market_hours` = 4 agents) with `fills.py` and `accounts.py` as a join-barrier before `broker.py`. Adversarial-verify step hammers idempotency (concurrent duplicate `client_request_id`) and asserts a single fill + single debit.
- **Depends on:** P0.

### Phase P2 — Engine/Route Interception (the shim)
- **Goal:** Chat + all eight workflow actions + SIP route through the paper broker with **zero downstream diffs** beyond the signature.
- **Deliverables:** `orders.py` per-account router shim (`_use_paper_broker`, contextvar `db`/`user_id` plumbing); optional `origin: dict` kwarg + `ideas.resolve(...)` skeleton; `skip_trade_log` flag on the chat confirm path to avoid double-logging.
- **Files:** `backend/kite/orders.py` (shim body), `backend/paper/ideas.py` (resolver stub), `backend/routers/orders.py` (origin + skip_trade_log), `backend/workflows/steps/actions.py` (populate `origin` only).
- **DB migration:** none.
- **Exit/acceptance:** Test 10 (squareoff end-to-end books a fill, no `TypeError`); a live `execute_action_place_order` run produces `paper_orders`+`paper_fills`+`TradeLog`; no double TradeLog on chat confirm.
- **Workflow orchestration:** **fan-out per call site** (place_order / allocate_basket / set_stoploss / squareoff / cancel_orders / chat-confirm / SIP = 7 verify agents), each replaying its action against the paper broker and asserting the expected row deltas. Adversarial-verify replays a multi-step workflow (entry → SL-GTT → TP-GTT) and asserts OCO + idea attribution.
- **Depends on:** P1.

### Phase P3 — Resting Orders + NAV Snapshots + Scheduler
- **Goal:** LIMIT/STOP/GTT rest and drain on a tick; daily NAV/equity curve snapshots persist; reconciliation heals orphaned reserves.
- **Deliverables:** `resting.py` (marketability/trigger evaluator, OCO sibling cancel, reserve release), `marks.py` (mark-to-market, account + idea NAV snapshot, `reconcile()`), two APScheduler jobs (`paper_drain_resting`, `paper_eod_snapshot`); AMO queued-fill at next open.
- **Files:** `backend/paper/resting.py`, `backend/paper/marks.py`, `backend/scheduler.py` (`_register_jobs`).
- **DB migration:** none (P0 tables).
- **Exit/acceptance:** Tests 5 (resting + OCO), 6 (reserve/buying-power), 7 (settlement roll), 8 (MTM/NAV == cash + Σ qty·LTP, stale flag).
- **Workflow orchestration:** **fan-out resting-evaluator vs snapshotter vs reconciler** (3 agents). Adversarial-verify simulates two overlapping `drain_due()` ticks (assert one fill), a crash mid-fill (assert `reconcile()` releases the orphaned reserve), and a market-closed snapshot (assert `is_stale`).
- **Depends on:** P2 (needs fills flowing) and P0 (snapshot tables).

### Phase P4 — REST API
- **Goal:** All `/paper/*` endpoints serve real data, mirroring `/portfolio` shapes; ratios computed-on-read via `backtest_metrics`.
- **Deliverables:** `backend/routers/paper.py` (account/summary/holdings/positions/orders[/open]/fills/equity-curve/ideas/ideas-scorecard/reset); registration in `main.py`.
- **Files:** `backend/routers/paper.py`, `backend/main.py`.
- **DB migration:** none.
- **Exit/acceptance:** Test 9 (scorecard endpoint returns Sharpe via `sharpe_sortino` + degradation panel joined to a seeded `dsl_backtest_runs`); `/paper/summary` byte-compatible with `PortfolioSummary`; `/paper/equity-curve` shape matches `portfolio/performance` + benchmark.
- **Workflow orchestration:** **fan-out one agent per endpoint group** (account+summary+holdings / positions+orders+fills / equity-curve / ideas+scorecard = 4 agents), each writing the FastAPI handler + a contract test. Adversarial-verify fuzzes query params (bad `range`, missing idea) and asserts 4xx not 500.
- **Depends on:** P3 (needs snapshots for curves/scorecards).

### Phase P5 — FE Dashboard
- **Goal:** The Quartr-themed `PaperDashboard` tab renders the Tier-1 surface end-to-end against live endpoints.
- **Deliverables:** `AppShell.tsx` tab wiring; `lib/api.ts` paper types + fetchers; `components/paper/` (`PaperDashboard`, `_shared`, `KpiStatCards`, `EquityCurveChart`, `DrawdownChart`, `HoldingsTable`, `OpenOrdersBlotter`, `TradeJournal`, `AllocationDonut`); 15s poll + `useLiveQuote` rows.
- **Files:** `pivot-next/components/AppShell.tsx`, `pivot-next/lib/api.ts`, `pivot-next/components/paper/*`.
- **DB migration:** none.
- **Exit/acceptance:** tab loads, KPI strip + equity curve + holdings + blotter render with live LTP dots; dark/light token parity; empty/loading/error states present; `prefers-reduced-motion` respected.
- **Workflow orchestration:** **fan-out one agent per component** (8 leaf components + the `_shared`/`api.ts` foundation as a join-barrier first). Adversarial-verify runs a Playwright pass (deep-link `#paper/overview`, toggle dark mode, assert no hardcoded hex via computed-style spot-checks, assert `role="alert"` on a forced error).
- **Depends on:** P4 (needs endpoints).

### Phase P6 — Forward-Test Scorecards (the differentiator)
- **Goal:** Per-idea scorecards + the backtest-vs-live degradation drill-in, with lifecycle/cohorting and statistical gates.
- **Deliverables:** `ideas.py` full resolver (dedup, label, inception, cohort_trial_count) + scorecard-cache refresh; PSR/MinTRL/Deflated-Sharpe helpers (new, next to `backtest_metrics`); `IdeaScorecards.tsx` + `IdeaDetailPanel.tsx`; promotion-gate logic (`paper`→`candidate`→`promoted`/`retired`).
- **Files:** `backend/paper/ideas.py`, `backend/services/forward_stats.py` (new — PSR/MinTRL/DSR), `pivot-next/components/paper/IdeaScorecards.tsx`, `IdeaDetailPanel.tsx`; `backend/routers/paper.py` (scorecard fields).
- **DB migration:** none (uses `forward_ideas` + idea snapshots from P0).
- **Exit/acceptance:** degradation panel renders backtest vs forward with decay + verdict; PSR gate fires; cohort survival view; attribution dedup (one idea per workflow across 40 runs).
- **Workflow orchestration:** **fan-out backend-stats vs resolver vs FE-scorecards vs FE-drill-in** (4 agents); the stats helper is a join-barrier before the API + FE. Adversarial-verify constructs a known overfit idea (high backtest Sharpe, flat forward) and asserts it scores `decayed`, and a slippage-dominated idea scores `execution-problem`, and a 3-week idea is flagged `insufficient-data` (maturity penalty).
- **Depends on:** P5 (FE shell) + P4 (API).

**Phase dependency graph:** `P0 → P1 → P2 → P3 → P4 → P5 → P6`, strictly sequential. Within each phase the listed sub-tasks fan out in parallel under one orchestrating workflow, joined before the adversarial-verify + promote-phase gate.

---

## 9. Open Questions / Decisions for the user

1. **One shared paper account, or per-idea sub-accounts?** The design defaults to **one `paper_accounts` book per user** with per-idea *attribution* (idea-grain NAV via `paper_idea_nav_snapshots`), not separate cash pools. Per-idea sub-accounts (each with its own starting capital) would give cleaner isolation but complicate buying-power and reconciliation. **Decision needed:** ship single-book v1 (recommended), or invest in sub-accounts now via the existing `label` column?
2. **Starting capital default.** The design seeds **₹150,000** (the existing `MOCK_MARGINS` figure). Keep that, pick a rounder number (₹100,000 / ₹1,000,000), or make it user-settable on first account creation?
3. **Intraday mark cadence.** v1 snapshots **once at EOD (15:35 IST)** for the persisted curve + lazy MTM on reads. Do you want an **intraday NAV point every 1–5 min** during market hours (richer 1D curve, more scheduler load and snapshot rows), or is EOD + lazy-on-read sufficient?
4. **T+1 settlement realism.** Model **full T+1** (`cash_settled` vs `cash_available` with an EOD roll) or the **simplified "proceeds usable immediately, `settles_at` stamped for display"**? The simplified path is faster to ship; full T+1 is more honest for forward-test fidelity.
5. **Promote-to-live flow.** When an idea hits the graduation gate (`promoted`), what *actually* happens? Options: (a) **flag only** — dashboard surfaces "ready to promote," no execution change; (b) flip `paper_accounts.mode="live"` for *that idea's* future fills (requires per-idea routing); (c) export the idea as a new live Workflow. **Recommendation:** (a) for v1.
6. **Keep kite-mock alongside paper, or replace it?** Today `KITE_MOCK_MODE` returns hollow `MOCK####` fills. The shim makes paper the default for the dev user. Do we **fully retire the hollow mock** (paper becomes the only non-live path) or **keep both** (hollow mock for unit tests that don't want a cash/position side-effect)? Retiring is cleaner; keeping is lower-risk for existing tests.
7. **Partial fills.** Ship **deterministic full fills only** (recommended, `PAPER_PARTIAL_FILLS=off`), or enable the Alpaca-style 10%-chance partial model for realism demos? Partials add a `partially_filled` status and complicate idea-grain lot accounting.
8. **Benchmark choice.** NAV is benchmarked against **NIFTY 50**. Is that the right default for retail equity ideas, or should the benchmark be **per-idea configurable** (e.g. sector index for a sector workflow)?

---

## 10. Risks & Mitigations

| # | Risk | Likelihood / Impact | Mitigation |
|---|---|---|---|
| 1 | **Latent `place_order` `TypeError`** (squareoff passes `client_request_id`/`order_type` the signature rejects — verified `actions.py:665`, `:1011`) | High likelihood (any squareoff) / breaks square-off | Fix the signature **first** in P0; regression Test 10 in CI. |
| 2 | **Double-fill on scheduler retry or process restart** (NautilusTrader failure mode) | Medium / corrupts cash + positions | Positions/cash **derived from immutable `paper_fills`**; unique `client_request_id`; status-guarded atomic transition (`SELECT … FOR UPDATE` PG / guarded UPDATE SQLite); `reconcile()` each EOD tick. |
| 3 | **Double cash-spend across multiple resting BUYs** (agents place baskets of resting orders) | Medium / overstates buying power | `cash_reserved` held at placement; `buying_power = cash_available − cash_reserved`; release on fill/cancel. |
| 4 | **Double TradeLog rows** (broker writes one; chat confirm path also writes one) | Medium / duplicate audit + inflated journals | `skip_trade_log` flag on chat path, or move the write entirely into the broker. |
| 5 | **Fabricated prices when market is shut / quote stale** | Medium / misleading P&L | Mark vs last close, set `stale`/`is_stale`; "as of HH:MM" stamp; never animate a fake tick. |
| 6 | **Look-ahead bias poisons forward scores** (recomputing intended price / benchmark from adjusted history) | Medium / fictional alpha + slippage | Snapshot `intended_price`/`intended_quote_at` + `nifty_close` **at decision/close**, never later. |
| 7 | **Cost-model drift** (live-paper friction diverges from backtest) | Low / breaks the just-fixed parity | **Single source**: all friction via `trading_costs.py`; Test 4 asserts `paper_fills.charges == buy_cost(...)[1]` and round-trip ≈ `round_trip_bps()`. |
| 8 | **Selection bias inflates the "best" idea's Sharpe** across a parallel cohort | Medium / false promotions | Track `cohort_trial_count`; apply **Deflated Sharpe Ratio** + maturity penalty before any promote. |
| 9 | **Corporate actions (splits) silently break `avg_cost`** | Low likelihood / silent wrong P&L | Explicitly out of scope v1; surfaced in `methodology_note`; revisit before any real-money promotion. |
| 10 | **Migration breaks on dialect divergence** (PG ENUM vs SQLite CHECK, JSONB vs JSON) | Low / blocks deploy | Follow the proven 0011 pattern exactly; P0 adversarial-verify runs `upgrade/downgrade` on **both** dialects; additive-only ALTERs. |
| 11 | **Scheduler load from intraday marks** (if intraday cadence chosen) | Low / DB growth + tick contention | Default EOD-only + lazy MTM; intraday is opt-in (Q3); snapshots are `unique(account_id, as_of_date)` so re-runs are idempotent. |
| 12 | **FE hardcodes hex / breaks dark mode** | Low / off-brand, dark-mode bugs | Token-only rule enforced in P5 adversarial-verify (computed-style spot-checks); the one carve-out (gradient `<stop>`) reads tokens via `getComputedStyle` + `MutationObserver` re-render. |
| 13 | **Idea attribution duplicates** (a workflow firing 40× spawns 40 ideas) | Medium / fragments scorecards | Dedup in `ideas.resolve` (one idea per `(user_id, workflow_id)` / `(user_id, conversation_id, label)`), enforced in the resolver not a DB partial index. |
| 14 | **Paper edge overstates live edge** (no market impact/latency) | Inherent / over-optimistic promotions | Treat paper as an **upper bound**; the slippage-gap metric + execution-vs-signal verdict + promotion gates are the guardrails; document the caveat on every scorecard. |


---

## 11. P0 Build Log — Schema (shipped 2026-05-30, branch `Eventtriggers`)

**Status: COMPLETE & VERIFIED.** Phase P0 of §8 is built. Additive-only;
no ALTER on any existing table.

**Artifacts**
| File | What |
|---|---|
| `pivot/backend/models.py` | 8 ORM models (`PaperAccount`, `PaperOrder`, `PaperFill`, `PaperPosition`, `PaperLedgerEntry`, `ForwardIdea`, `PaperNavSnapshot`, `PaperIdeaNavSnapshot`) + 4 frozensets (`PAPER_ACCOUNT_MODES`, `PAPER_ORDER_STATUSES`, `PAPER_LEDGER_KINDS`, `FORWARD_IDEA_STATUSES`) + `User.paper_account` |
| `pivot/migrations/versions/0013_paper_trading.py` | Alembic `0013` (revises `0012_workflow_expires_at`), FK-dependency-ordered, dialect-aware JSONB/timestamps, clean `downgrade()` |
| `pivot/tests/test_paper_trading_models.py` | 23 tests (self-contained in-memory SQLite, `PRAGMA foreign_keys=ON`) |

**Verification**
- 23/23 tests pass; ruff clean; mypy adds 0 new errors (paper block uses `String + CheckConstraint`, not `SQLEnum`).
- Migration `0013` proven **byte-equivalent to `Base.metadata.create_all`** across all 8 tables (cols/types/nullable, indexes, unique, check, FK + `ondelete`); verified in isolation because the full chain `0001..0012` is Postgres-only.
- App + all routers + all mappers import cleanly with the new models.

**Deliberate decisions / deviations from the §6(a) prose spec** (also in the `models.py` block header):
1. `conversation_id` is `String(36)` (conversations.id is a UUID), not `Integer`.
2. `forward_ideas.backtest_run_id` is a **soft reference** (plain `String(36)`, no FK) — `dsl_backtest_runs` lives outside `backend.models`' metadata; degradation panel joins by value.
3. Reconciled-money columns are **`Numeric(18,4)`** (cash balances, ledger amounts, fill economics, accrued P&L, NAV figures) — paise precision, mirrors `llm_usage.cost_usd`; binary `Float` would drift cents across a replay chain. Market prices (`fill_price`, `last_price`, `limit/trigger/intended_price`, `nifty_close`) + ratios (`slippage_bps`) stay `Float`. **P1 broker must do money math in `Decimal`, cast to `float()` only at the JSON edge.**
4. `client_request_id` is **`String(120)`** (was 80, which overflowed on `sqoff_sym:…:legN:SYM` ≈ 57 + 2×len(symbol) for symbols ≥ 12 chars → idempotency key lost on Postgres).
5. `scorecard_cache` is dialect-aware (`JSON().with_variant(JSONB,'postgresql')`) so `create_all` and the migration agree (JSONB on PG).
6. Added composite index `ix_paper_orders_account_status` (resting-order drain + open-orders blotter hot path).
7. `paper_idea_nav_snapshots.account_id` is `ondelete=CASCADE` (symmetry with every other account-child).

**Deferred (documented, NOT in P0):**
- **Idea-dedup race**: resolver must be race-proof (SELECT…FOR UPDATE / advisory lock, or partial UNIQUE indexes) — built in the forward-test phase. Natural keys recorded in `ForwardIdea` docstring.
- **`trade_logs.idea_id`** column + the §3.5 historical backfill — a separate later migration (P0 is additive-only). Live attribution already flows via `paper_fills.idea_id` + `paper_fills.trade_log_id`.
- **Deploy contract** (noted in the `0013` docstring): like every migration since `0001`, `0013` FKs base tables it doesn't create (now incl. `strategies`/`conversations`/`trade_logs`) → deploy order is `create_all` THEN `alembic upgrade head`.

**Adversarially verified** by a 5-agent review workflow (plan-parity, Postgres-correctness, schema/idempotency integrity, test-adequacy, completeness critic); all MAJOR findings applied above.

**Next: P1 — Paper Broker + Fills (synchronous MARKET path).** Awaiting go-ahead.


---

## 12. P1 Build Log — Paper Broker + Fills (shipped 2026-05-30, branch `Eventtriggers`)

**Status: COMPLETE & VERIFIED.** Phase P1 of §8 — the synchronous MARKET fill path — plus the latent `place_order` TypeError fix.

**Artifacts**
| File | What |
|---|---|
| `pivot/backend/paper/broker.py` | `PaperBroker.place_order` / `place_gtt_order` — idempotent, MARKET fills synchronously, LIMIT/SL/GTT rest, accepts the Kite kwarg set (drop-in for P2) |
| `pivot/backend/paper/fills.py` | `execute_market_fill` — fills at clean LTP, friction from `trading_costs` (no slippage double-count), avg-cost, realized P&L, cash + ledger, all `Decimal` |
| `pivot/backend/paper/accounts.py` | `get_or_create_account` — ₹1.5L seed + seed ledger, race-safe (SAVEPOINT) |
| `pivot/backend/paper/marks.py` | injectable mark price (Kite live → yfinance fallback; lazy imports) |
| `pivot/backend/paper/money.py` | `Decimal` quantize to paise (`Numeric(18,4)`) |
| `pivot/backend/kite/orders.py` | **TypeError fix** — `place_order` accepts `client_request_id` (squareoff legs `actions.py:665/:1011` now work) |
| `pivot/tests/test_paper_broker.py` | 25 tests |

**Verification:** 48 paper tests pass (25 broker + 23 P0 models); 534 backend tests pass with zero regressions (the 5 failing are pre-existing — stale step-types catalog + date-dependent calendar/chat — confirmed identical with P1 stashed); ruff clean; 0 genuine mypy errors (only the ambient `Column[T]` noise the whole backend carries).

**Adversarially verified** by a 5-agent review (accounting, idempotency/txn-safety, integration-readiness, edge/tests, completeness critic). Fixes applied:
- **BLOCKER** — the idempotency-collision handler called `db.rollback()`, which discards the *caller's entire* uncommitted transaction (the workflow engine does multi-step work in one txn). Now wrapped in a **SAVEPOINT** (`begin_nested`) so only the failed insert rolls back. Same fix for `get_or_create_account`. Directly tested.
- **MAJOR** — resting LIMIT BUY reserved cash with no buying-power check (drove cash negative) → now rejects; `qty<=0` (DivisionByZero / phantom short) → rejected pre-persist; non-positive mark price (credited cash on a BUY) → rejected.
- **MAJOR (integration)** — `place_gtt_order` now returns `trigger_id` and accepts Kite kwargs (`access_token`/`exchange`/`last_price`); `place_order` accepts `access_token`/`tag` (true P2 drop-in); GTT persists its `limit_price`.
- **MINOR** — idempotency lookup user-scoped; `cash_settled == cash_available + cash_reserved` invariant corrected.

**Documented simplifications (P1):** sub-2-paise avg-cost/realized rounding residue (bounded, never touches cash, guard-tested ≤ ₹0.02); `cash_settled` moved without its own ledger row; `settles_at` = calendar +1 day; AMO/variety recorded but not honored; engine crid includes `attempts` (P2 shim should pass a retry-stable key for true at-most-once).

**Next: P2 — route chat + workflow actions through the PaperBroker by account `mode`** (the shim), and fold in the retry-stable crid. Awaiting go-ahead.


---

## 13. P2 Build Log — Order Routing Shim (shipped 2026-05-30, branch `Eventtriggers`)

**Status: COMPLETE & VERIFIED.** Triggered + chat orders now land in the paper portfolio (by account `mode`).

**Artifacts**
| File | What |
|---|---|
| `pivot/backend/paper/routing.py` | `should_use_paper` (flag + mode); `submit_order`/`submit_gtt` (workflow ctx + retry-stable crid + attribution + `leg_key`); `submit_order_for_user`/`submit_gtt_for_user` (chat); `paper_position_qty` |
| `pivot/backend/workflows/steps/actions.py` | 5 entry/GTT sites routed; squareoff_*/cancel_orders guarded to skip in paper mode; SL/TP size from paper position; basket legs carry `leg_key` |
| `pivot/backend/routers/orders.py` | `/orders/confirm` + `/orders/gtt` routed; **`/orders/gtt` now commits + writes a TradeLog**; `conversation_id` threaded |
| `pivot/backend/scheduler.py` | SIP autofire routed through the shim |
| `pivot/backend/config.py` | `paper_trading_enabled` flag (default on; pinned off in tests) |
| `pivot/tests/test_paper_routing.py` | 13 tests incl. a real engine `_ExecutorContext → paper fill` + an HTTP `/orders/gtt` persistence test |

**Routing decision:** paper ⟸ `paper_trading_enabled AND account.mode=='paper'` (the default); else Kite (mock in dev). Kite is called via the **canonical module** (`backend.kite.orders.*`) so it stays the patchable test seam. Crid is **retry-stable** (`wf:{run}:{step}:{side}:{symbol}[:{leg}]`, excludes the engine's `attempts`).

**Verification:** 61 paper tests pass; full backend regression **8 failed / 574 passed with zero P2-caused regressions** (the 8 are pre-existing — proven identical with P2 stashed); ruff/mypy clean on new surfaces; 3 existing tests that mocked the order seam were repointed at the canonical Kite seam without weakening their assertions.

**Adversarially verified** by a 4-agent review (routing, integration, idempotency, completeness critic). Fixes applied:
- **BLOCKER** — `/orders/gtt` never committed → a chat paper GTT was flushed then rolled back (phantom success). Now commits + writes a TradeLog; HTTP test added.
- **MAJOR** — SIP autofire bypassed the shim → routed through `submit_order_for_user`.
- **MAJOR** — squareoff_*/cancel_orders in paper mode silently acted on Kite mock positions (lying run cards) → now return an explicit `paper_mode_unsupported` skip.
- **MAJOR** — a duplicate symbol+side in one basket step collapsed via the crid → silent under-fill → added a `leg_key` ordinal so each leg fills (position aggregates).
- **MAJOR** — paper SL/TP sized quantity from the Kite holding → now sizes from the paper position (`paper_position_qty`).
- **MINOR** — `/orders/gtt` idempotency crid; `conversation_id` attribution for chat; `should_use_paper` logs the swallowed exception; broker crid-namespacing docstring clarified.

**Documented (deferred):** squareoff/cancel coherence + paper position/order reads → P4; the confirm/gtt double-write (paper book + TradeLog audit log) is intentional (TradeLog = append-only history).

**Next: P3 — resting-order fill evaluator + mark-to-market + daily NAV snapshots** (scheduler jobs); then P4 (REST API + paper position reads), P5 (FE dashboard), P6 (forward-test scorecards). Awaiting go-ahead.


---

## 14. P3 Build Log — Resting Fills + Mark-to-Market + NAV Equity Curve (shipped 2026-05-30, branch `Eventtriggers`)

**Status: COMPLETE & VERIFIED.** Resting orders now fill on a price tick, positions mark-to-market, and each paper account writes a daily NAV row — the equity curve. **Built with a parallel agent workflow** (4 modules concurrently) + lead integration.

**Artifacts**
| File | Role | Built by |
|---|---|---|
| `backend/paper/valuation.py` | `mark_positions`, `compute_account_nav` (+ position MV/unrealized/day-P&L) | agent |
| `backend/paper/fills.py` (+`fill_resting_order`/`cancel_resting_order`/`_release_reserve`) | fill a resting order + the `release` ledger row | agent |
| `backend/paper/evaluator.py` | `should_fill` (LIMIT/SL/GTT cross) + `evaluate_resting_orders` (+ OCO) | agent |
| `backend/paper/snapshots.py` | `snapshot_account_nav` upsert + `latest_nav`/`nav_series` | agent |
| `backend/paper/jobs.py` | `tick_paper_accounts` + `snapshot_all_navs` orchestrators (SAVEPOINT per account) | lead |
| `backend/scheduler.py` | resting tick (every 5m, market hours) + EOD NAV (15:37 IST), gated on the flag | lead |
| 5 test files + integration test | — | mixed |

**Verified:** 99 paper tests pass (incl. an end-to-end: resting LIMIT → tick fills + releases reserve → NAV snapshot → equity curve grows over 2 days) · ruff/mypy clean · full regression 8 pre-existing fails / 619 passed (zero P3 regressions).

**Adversarially verified** by a 4-agent review of the agent-written logic. Fixes applied:
- **BLOCKER** — the evaluator discarded `fill_resting_order`'s return, so a REJECTED resting fill was reported as "filled" AND cancelled its OCO sibling (silent order loss). Now captures the return; only marks filled + runs OCO when the fill succeeds; rejects go to a `rejected` bucket. Regression-tested.
- **MAJOR** — trigger direction was inferred from the *first scheduler tick*, so a stop placed in a falling market misclassified (fired backwards). Now `intended_price` is captured at **placement** (decision-time reference). Regression-tested.
- **MAJOR** — the resting-BUY reserve excluded charges, so a near-max order self-rejected at fill after releasing → reserve is now charges-inclusive (`buy_cost`).
- **MAJOR** — NAV omitted `cash_reserved` (equity curve dipped when an order rested, jumped on fill) → `nav = cash_available + cash_reserved + positions_mv`. Regression-tested.
- **MAJOR** — batch orchestrators had no per-account isolation → SAVEPOINT + try/except per account so one bad account can't abort the pass.
- **NIT** — `is not None` price checks; EOD snapshot offset to 15:37 (off the tick boundary).

**Documented (deferred):** the OCO consumer is correct but has no producer yet (bracket orders land later); in mock/dev marks use yfinance daily close, not intraday LTP (prod should thread the Kite token — a follow-up); per-idea NAV + strict T+1 are P6/later.

**Next: P4 — REST API for the paper portfolio + paper position/order reads** (which also unblocks squareoff coherence). Then P5 (Quartr dashboard), P6 (forward-test scorecards). Awaiting go-ahead.
