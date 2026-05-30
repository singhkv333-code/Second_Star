# Paper-Trading Plan — Research & Design Appendix

> Raw per-agent outputs behind `docs/PAPER_TRADING_PLAN.md`. Kept for full detail; the main plan de-dupes and resolves contradictions.



---

# Paper-Trading Engine Essentials (raw)

I have everything needed. The web research is complete and I have full grounding in the codebase's existing seams. Here is the deliverable.

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

- **client_request_id dedup** — Pivot **already** generates `client_request_id = sha1(f"{run_id}:{step_index}:{attempts}")` and passes it to actions (see `actions.py` docstring; legs derive per-symbol child ids). The PaperBroker must **persist a unique constraint on `client_request_id`** and, on a duplicate, **return the existing fill** rather than creating a second one. This is the linchpin that makes scheduler retries (`max_retries=1`) safe. Note: `place_order` in `orders.py` does **not currently accept** a `client_request_id` param even though some callers in `actions.py` pass one (`squareoff_all`, `_place_squareoff_legs`) — that's a latent bug to fix when adding the seam.
- **Avoiding double-fills on retries** — A resting-order evaluator must mark an order `filled` atomically (status transition guarded in one transaction) so two overlapping scheduler ticks can't both fill it. Use a row-level `SELECT ... FOR UPDATE` / optimistic version column.
- **Reconciliation** — On startup and each tick, reconcile `Σ fills → positions → cash` so a crashed mid-fill leaves no orphaned reserve (this is exactly the NautilusTrader "duplicate orders on restart" failure mode — guard against it by deriving positions purely from the immutable fills log, never from incrementally-mutated counters).

### 1.6 Where to put the PaperBroker seam (concrete recommendation)

Introduce **`backend/kite/paper_broker.py`** exposing the *exact same signatures* as `orders.py`:

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

### Key decisions for Pivot

- **One seam, mirror the existing interface.** Add `backend/kite/paper_broker.py` with byte-identical `place_order`/`place_gtt_order` signatures; turn `orders.py` into a per-account broker selector. Chat + all `action.*` steps flow through unchanged.
- **Reuse `trading_costs.py` for everything** — slippage (`SLIPPAGE_PCT`) and all fees/taxes via `buy_cost`/`sell_cost`. No new cost numbers; keeps live-paper ↔ backtest parity that was just fixed.
- **Integer shares, CNC long-only by default.** No fractionals, no live shorts (matches the existing `allocate_basket` guard); shorts stay confined to the research backtester.
- **Synthesize a spread from LTP** (no real book): MARKET fills at touch ± slippage; LIMIT/STOP rest and fill on marketability; GTT is the stop/take-profit primitive; bracket = entry + OCO GTT pair.
- **Reserve cash for resting BUY orders** and model **T+1 settlement** (CNC sell proceeds immediately reusable, formally settled T+1). This is the correctness gap the public docs ignore.
- **Idempotency via persisted `client_request_id` unique constraint** — return the existing fill on duplicate; derive positions/cash purely from the immutable fills log so scheduler retries and restarts never double-fill. (Fix the latent bug: `orders.py::place_order` doesn't yet accept the `client_request_id` some callers already pass.)
- **Scheduler hosts two loops** in-hours: a resting-order/GTT evaluator and a mark-to-market/NAV snapshotter; after-hours MARKET orders queue to next-open (AMO semantics).
- **Partial fills OFF by default**, behind a flag; deterministic fills make idea-attribution and reconciliation clean for the forward-test goal.
- **Corporate actions (splits/dividends) explicitly out of scope for v1** — MTM against last close when market closed/stale and flag `stale`, never fabricate a moving price.

**Sources:** [Alpaca Paper Trading](https://docs.alpaca.markets/us/docs/paper-trading), [Alpaca Order Types](https://alpaca.markets/learn/13-order-types-you-should-know-about), [QuantConnect Paper Trading / DefaultBrokerageModel](https://www.quantconnect.com/docs/v2/cloud-platform/live-trading/brokerages/quantconnect-paper-trading), [QuantConnect Slippage](https://www.quantconnect.com/docs/v2/writing-algorithms/reality-modeling/slippage/key-concepts), [QuantConnect Reconciliation](https://www.quantconnect.com/docs/v2/cloud-platform/live-trading/reconciliation), [Zerodha GTT](https://zerodha.com/z-connect/kite/introducing-gtt-good-till-triggered-orders), [Kite Orders manual](https://kite.trade/docs/kite/orders/), [Zerodha T+1 / MTF settlement](https://support.zerodha.com/category/trading-and-markets/margins/margin-trading-facility/articles/margin-trading-facility-mtf-faqs), [NautilusTrader duplicate-order-on-restart issue](https://github.com/nautechsystems/nautilus_trader/issues/3176).

---

Notes for the calling script: research used live web (available). Load-bearing codebase facts: the broker seam is `/Users/karanveersingh/Downloads/Second_Star/pivot/backend/kite/orders.py` (`place_order`, `place_gtt_order`); all workflow actions route through it via `/Users/karanveersingh/Downloads/Second_Star/pivot/backend/workflows/steps/actions.py`; cost source of truth is `/Users/karanveersingh/Downloads/Second_Star/pivot/backend/services/trading_costs.py`; `client_request_id` is already generated by the engine but `orders.py::place_order` does not yet accept it (latent bug). Default mock balance ₹150,000 lives in `/Users/karanveersingh/Downloads/Second_Star/pivot/backend/kite/mock_data.py`.


---

# Dashboard UI Element Catalog (raw)

I have sufficient knowledge of lightweight-charts (Canvas-based, ~45KB, requires client boundary in Next.js, themed via `applyOptions` with JS values — so CSS vars must be read via `getComputedStyle`). I have everything needed. Writing the catalog now.

---

## 2. Dashboard UI Element Catalog (Quartr-themed)

This catalog maps a best-in-class paper-trading / portfolio / forward-test dashboard onto Pivot's existing Quartr design tokens (from `pivot-next/app/globals.css`) and shadcn/ui primitives already vendored in `components/ui`. Survey base: Alpaca paper dashboard (portfolio value, buying power, top positions, recent orders, daily/monthly/overall equity), Composer.trade (per-symphony scorecards, historical allocation graph, fees/slippage/final value, benchmark compare), Koyfin (exposure analysis, drawdown chart + top drawdowns, summary snapshot with weight drift), Sharesight (contribution/attribution report splitting return into capital gains vs dividends vs FX, treemap winners/losers), Zerodha Console (P&L calendar heatmap, holdings-as-of-date), TradingView/lightweight-charts (candles + area). Sources listed at the end.

> **Token convention used below:** all colors are CSS custom properties; reference them as `hsl(var(--…))` only where the token is HSL (shadcn base tokens like `--border`, `--muted-foreground`), and as the raw var `var(--…)` for the Quartr hex tokens (`--text-primary`, `--color-profit`, `--bg-card`, etc.). Profit is **always** `--color-profit`, loss **always** `--color-loss`, never raw green/red. All numerics use `font-mono` + `.tabular-nums`. All cards: `background: var(--bg-card)`, `border: 1px solid var(--glass-border)`, `border-radius: var(--radius-lg)`, hover → `border-color: var(--glass-border-hover)` + 1px translate, transitioned with `--ease-quartr`.

---

### Tier 1 — Portfolio core (build first)

#### 1. KPI / Stat Card Strip
**Data:** From a new `paper_account` + `paper_position` derivation (cash ledger + fills → positions). Fields: NAV (cash + Σ mkt value), total P&L (NAV − net deposits), day P&L (NAV − prior-close NAV), realized vs unrealized P&L, buying power (cash − reserved margin on open orders), win rate (% closed lots with realized P&L > 0). Each needs a current value + a delta + an intraday sparkline series (last N marks from the NAV/mark-to-market loop).
**Element:** 5–6 horizontally-scrolling stat cards mirroring the existing `DashboardTab` index strip. Each card = uppercase label (`.q-uppercase-label`, `--metric-label`), large mono value (`.q-display` / `--font-mono`, `--text-primary`, `.tabular-nums`), a signed delta pill, and an inline 40px sparkline.
**Quartr render:** Card = `--bg-card` / `--glass-border` / `--radius-lg`. Label `--metric-label` + `--font-ui`. Value `--font-mono`, `--weight-display` 550, `--text-primary`. Delta colored `--color-profit` / `--color-loss`; delta-pill background = same color at low alpha (`color-mix(in srgb, var(--color-profit) 12%, transparent)`). Day-P&L card is the hero — slightly elevated (`--bg-elevated` on hover, `--shadow-cta`). Number ticker animates value on each mark with `--ease-quartr`.
**shadcn:** `Card` + `CardHeader`/`CardContent`; `Badge` (variant overridden via inline token) for the delta pill; `Tooltip` on hover explaining the metric ("Buying power = cash − reserved margin").

#### 2. Equity / NAV Curve with Benchmark Overlay + Range Selector
**Data:** Time series of NAV marks (from the scheduler mark-to-market loop, persisted to a `paper_nav_point` table: `ts, nav, cash, invested, day_pnl`). Benchmark = NIFTY 50 normalized to the same start value (reuse `yfinance_service` / `market_data`). Range selector drives the query window: 1D / 1W / 1M / 3M / 1Y / ALL.
**Element:** Area chart (gradient fill under the line) for NAV + a thin comparison line for the benchmark — exactly Alpaca's "equity curve tells the visual story relative to benchmark." Crosshair tooltip showing NAV, benchmark, and spread on hover.
**Quartr render:** Primary line stroke = `--price-line`; area gradient = `--pivot-blue` fading to transparent. Benchmark line = `--text-tertiary` dashed. Up-day shading optional via `--color-profit` at 6% alpha. Crosshair line = `--glass-border-focus`. Range selector = pill segmented control: active pill `--bg-elevated` + `--text-primary`, inactive `--text-secondary`, container `--radius-pill`, `--ease-quartr` slide. Axis labels `--text-tertiary`, `--font-mono`, `.tabular-nums`. Gridlines `--glass-border` (barely visible).
**shadcn:** `ToggleGroup` for the range selector; `Card` wrapper. Chart = **lightweight-charts** (see library rec) for the area + crosshair.

#### 3. Holdings / Positions Table
**Data:** Per `paper_position`: symbol, qty, avg cost, LTP (live from quote), mkt value, unrealized P&L (abs + %), day change, sector (from a symbol→sector map), and **source attribution** (which workflow/strategy/chat-idea opened it — joined via `source` / `source_id` on `TradeLog`). This is the linchpin that closes the orders↔portfolio gap.
**Element:** Dense sortable table with a per-row sparkline of the position's price, a colored unrealized-P&L cell, a sector chip, and a source chip. Row click → existing `StockDetailPage`. Sticky header, right-aligned mono numbers.
**Quartr render:** Header row `.q-uppercase-label` / `--metric-label`, border-bottom `--glass-border`. Cells `--font-mono` `.tabular-nums`, primary text `--text-primary`, secondary (avg cost) `--text-secondary`. P&L cells colored `--color-profit` / `--color-loss`; the P&L% cell gets a faint inline bar/heat tint at low alpha. Row hover `--surface-hover`, active `--surface-active`. Sector + source chips = `Badge` with `--bg-secondary` bg, `--text-secondary`, `--radius-pill`. Negative qty (shorts) flagged with a `--color-loss` left border.
**shadcn:** `Table` family; `Badge` for chips; `DropdownMenu` for per-row actions (close position, set SL/TP → routes to existing `action.set_stoploss`); `Tooltip` for source provenance.

#### 4. Open-Orders Blotter (Resting LIMIT / GTT / SL / TP)
**Data:** `TradeLog` rows where `status IN (registered, PENDING, trigger_pending)` plus GTT legs from `action.set_stoploss` / `action.set_takeprofit`. Fields: symbol, side, type (LIMIT/GTT/SL/TP), qty, limit/trigger price, distance-to-trigger %, age, source. Cancel routes to existing `action.cancel_orders` / orders router.
**Element:** Live blotter table with a status dot, distance-to-trigger micro-gauge, age timer, and a Cancel button per row. "Recent Orders" in Alpaca; resting-order awareness from TradingView.
**Quartr render:** Status dot — registered `--info`, pending `--warning`, near-trigger pulse `--color-warn`. Distance gauge = thin bar, fill `--pivot-blue`. Side = buy `--color-profit` / sell `--color-loss` text. Cancel = ghost `Button`, hover `--color-loss` border. Card `--bg-card` / `--radius-lg`.
**shadcn:** `Table`; `Button` (ghost/destructive) for cancel; `AlertDialog` to confirm cancel; `Badge` for type.

#### 5. Trade / Fill Journal (Blotter)
**Data:** All filled `TradeLog` rows: timestamp, symbol, side, qty, fill price (`average_price`), value, fees/slippage (from `services/trading_costs.py`), realized P&L on closing lots, and source. Paginated, filterable by symbol/date/source.
**Element:** Chronological journal grouped by day with day-subtotal rows; expandable row showing cost breakdown (brokerage / STT / slippage) — Composer surfaces "fees, slippage and final value."
**Quartr render:** Day group header = `.q-uppercase-label`, sticky, `--bg-secondary`. Buy/sell side icon tinted profit/loss. Realized P&L mono colored. Expanded cost panel `--bg-elevated`, `--radius-md`. Fees shown `--text-tertiary`.
**shadcn:** `Table` + `Collapsible` rows; `Select`/`DatePicker`-style `Popover`+`Calendar` for filters; `Tabs` to switch Journal ↔ Blotter.

---

### Tier 2 — Analysis & attribution

#### 6. Allocation Donut (Sector + Per-Idea)
**Data:** Σ mkt value grouped by (a) sector and (b) source idea/strategy. Includes a cash slice. Mirrors Koyfin Exposure Analysis + weight-drift.
**Element:** Two-ring donut (inner = sector, outer = per-idea) or a toggle between the two views, with a legend table (weight %, value, drift vs target if a target model exists). Center label shows total NAV.
**Quartr render:** Categorical palette built from `--pivot-blue`, `--info`, `--color-warn`, `--success`, plus neutral `--text-tertiary` for the cash slice — kept desaturated to stay on-brand (avoid rainbow). Segment hover lifts + dims siblings via opacity, `--ease-quartr`. Legend `--font-mono` `.tabular-nums`. Center NAV `.q-display`. Over-concentration (>X%) flagged `--color-warn`.
**shadcn:** `Tabs` (Sector / By Idea); `Card`; legend = `Table`. Chart = recharts `PieChart` (donut is recharts' sweet spot; SVG themes cleanly with tokens).

#### 7. Per-Strategy / Per-Agent Forward-Test Scorecards
**Data:** The second core aim. For each originating workflow/strategy/chat-idea (group `TradeLog` by `source`/`source_id` → a new `idea_attribution` rollup): live out-of-sample return since first fill, realized + unrealized P&L, win rate, # trades, avg hold, max drawdown, Sharpe (reuse `services/backtest_metrics.py`), and — the differentiator — **backtest-vs-live divergence** (expected vs realized). Composer's saved-symphony scorecards are the model.
**Element:** Grid of compact scorecards, each = idea name, a mini equity sparkline, headline live return, a stat row (win rate / trades / Sharpe / max DD), and a "live vs backtest" delta chip. Sortable by live return. Click → drill-in.
**Quartr render:** Card `--bg-card` / `--radius-lg`; headline return mono colored profit/loss. Sparkline stroke profit/loss by sign. "Beating backtest" chip `--color-profit` bg-tint, "lagging" `--color-loss`. Stat labels `--metric-label`. Winner card gets `--glass-border-hover` + subtle `--shadow-cta`.
**shadcn:** `Card` grid; `Badge` chips; `Tooltip` for metric definitions; `HoverCard` for the methodology note (reuse the backtest methodology text). Sparkline = recharts mini `AreaChart` or lightweight-charts.

#### 8. Drawdown Chart
**Data:** Derived from the NAV series — running peak and `(nav − peak)/peak`. Plus a "Top Drawdowns" table (start, trough, recovery, depth %, duration) — straight from Koyfin.
**Element:** Underwater area chart (always ≤ 0, filled downward) beneath the equity curve, sharing the range selector. Companion top-5-drawdowns table.
**Quartr render:** Fill = `--color-loss` at ~14% alpha, line `--color-loss`. Zero baseline `--glass-border`. Current-drawdown readout pinned top-right in mono. Table depth cells colored by severity (alpha ramp of `--color-loss`).
**shadcn:** `Card`; `Table` for top drawdowns. Chart = lightweight-charts area (shares the equity-curve instance/range).

#### 9. P&L Attribution / Contribution
**Data:** Sharesight-style breakdown of total return into components per holding and per idea: price gain (capital), realized vs unrealized split, fees drag. (Dividends/FX optional later.) Top contributors and detractors.
**Element:** Horizontal diverging bar chart (contributors right in profit color, detractors left in loss color) + a winners/losers treemap (Sharesight heat-mapped treemap). A small waterfall from start-NAV → end-NAV showing each idea's contribution.
**Quartr render:** Bars `--color-profit` / `--color-loss`, labels `--text-secondary`, values mono. Treemap tiles tinted by P&L magnitude (alpha ramp of profit/loss tokens), tile label `--text-primary` on light tiles / inverse on dark. Waterfall connectors `--glass-border`.
**shadcn:** `Card` + `Tabs` (Contributors / Treemap / Waterfall). Diverging bars + waterfall = recharts `BarChart`; treemap = nivo `ResponsiveTreeMap` (best-in-class treemap; lazy-loaded client-only).

---

### Tier 3 — Context, watch, and feel

#### 10. Calendar / Heatmap of Daily Returns
**Data:** Day P&L (abs or %) per calendar day from the NAV series. Zerodha Console's P&L heatmap.
**Element:** GitHub-style month/quarter heatmap grid; cell hover → tooltip with date, day P&L, # trades. Optional month-strip totals.
**Quartr render:** Cell color = alpha ramp on `--color-profit` (gains) / `--color-loss` (losses), zero = `--bg-secondary`. Grid gaps reveal `--bg-base`. Weekday/month labels `--text-tertiary` `.q-uppercase-label`. Today cell ringed `--glass-border-focus`. This can live in the existing `CalendarTab.tsx`.
**shadcn:** `Tooltip` per cell; custom CSS grid (no chart lib needed — pure divs + tokens).

#### 11. Watchlist
**Data:** `WatchlistItem` rows + live quote, day change %, sparkline, and a "has open position / open order" flag linking to the blotter.
**Element:** Compact list: symbol, LTP, day %, sparkline, quick-add-order button. Reuses existing watchlist plumbing.
**Quartr render:** Day % colored profit/loss; sparkline stroke by sign; row hover `--surface-hover`. Quick-buy `Button` ghost → opens order preview. Symbol `--font-ui` medium, price `--font-mono`.
**shadcn:** `Table`/list; `Button`; `Sheet` for the quick-order drawer.

#### 12. Activity Feed
**Data:** Unified event stream: order registered → triggered → filled, GTT hit, workflow run executed (`WorkflowRun`/`WorkflowRunStep`), SL/TP fired, idea opened/closed. Pulls from `TradeLog`, workflow run tables, and the mark-to-market loop.
**Element:** Vertical timeline with typed event icons, relative timestamps, and inline links to the relevant order/idea. Echoes Zerodha "recent transactions" + Console timeline.
**Quartr render:** Timeline rail `--glass-border`; event dots colored by type (fill `--color-profit`/`--color-loss`, system `--info`, trigger `--color-warn`). Relative time `--text-tertiary`. Card `--bg-card`. New events fade-in via `--ease-quartr`.
**shadcn:** `ScrollArea`; `Avatar`/icon; `Separator`; `HoverCard` for event detail.

---

### Charting library recommendation

| Library | Bundle | SSR / Next 15 | Theming via CSS tokens | Financial fit |
|---|---|---|---|---|
| **lightweight-charts** (TradingView) | ~45KB, Canvas | Client-only (needs `"use client"` + dynamic import, `ssr:false`) | Themed in JS via `applyOptions`; read tokens with `getComputedStyle(document.documentElement).getPropertyValue('--…')` and pass hex/HSL through | **Best** — purpose-built for area/line/candlestick equity curves, crosshair, range, time axis; buttery with many points |
| **recharts** | Heavier, SVG | SSR-friendly; SVG styles with plain CSS/vars directly | **Easiest** — SVG accepts `var(--…)` in `fill`/`stroke` inline | Great for donut, diverging bars, waterfall, mini sparklines; weak/no native candlesticks |
| **visx** | Smallest (~15KB, tree-shaken), SVG | Best for static SSR charts | Full control, but you compose everything | Powerful but high-effort; overkill here |
| **nivo** | Per-package, SVG/Canvas | Needs client boundary | Theme object; tokens injected via JS | **Treemap** is its standout; otherwise redundant |

**Primary pick: lightweight-charts for the time-series surfaces (equity/NAV curve, drawdown underwater, position sparklines, candlesticks on `StockDetailPage`)** — it's the only one of the four that natively nails financial charts and stays performant on dense intraday mark series, and it themes cleanly by reading the Quartr CSS vars at runtime via `getComputedStyle` (re-read on light/dark toggle). **Secondary: recharts** for the SVG categorical/statistical pieces (allocation donut, P&L diverging bars, waterfall, scorecard sparklines) because SVG accepts `var(--token)` directly with zero theming glue and SSRs well. **nivo, lazy-loaded client-only, only for the treemap.** Skip visx — its low-level flexibility isn't worth the build cost here. The calendar heatmap needs no library (CSS-grid divs + tokens).

Theming note: centralize a `getChartTheme()` helper that reads `--price-line`, `--pivot-blue`, `--color-profit`, `--color-loss`, `--glass-border`, `--glass-border-focus`, `--text-tertiary` once and feeds both libraries; subscribe to the `.dark` class toggle to re-apply for lightweight-charts (recharts re-reads automatically since it's inline CSS vars).

---

### Micro-interactions & polish ("premium fintech")

- **Number tickers:** animate KPI and NAV values on each mark (count-up/roll) eased with `--ease-quartr`; never reflow layout — reserve width with `.tabular-nums` + `--font-mono`.
- **Tabular-nums everywhere numeric** so digits don't jitter as values tick; right-align all money columns.
- **Sparklines** in every KPI card, position row, watchlist row, and scorecard — stroke colored by sign (`--color-profit`/`--color-loss`), no axes.
- **Skeletons** (shadcn `Skeleton`) shaped like the real module (card/table/chart) using `--bg-secondary` shimmer — not spinners.
- **Empty states:** before any paper trade exists, each module shows a Quartr-serif (`--font-serif`) one-liner + a CTA pill ("Register your first idea in chat") matching `DashboardTab` action pills.
- **P&L color semantics are absolute:** profit `--color-profit`, loss `--color-loss`, warn `--color-warn`; zero/neutral `--text-secondary`. Never hardcode hex; always the token (so dark mode's brighter `#10b981/#ef4444` apply automatically).
- **Hover lifts:** cards translate `-1px` and shift `--glass-border` → `--glass-border-hover` over ~160ms `--ease-quartr`; pressable rows go `--surface-hover` → `--surface-active`.
- **Live pulse:** a subtle dot on "live" modules during market hours (mark-to-market loop active); when stale, dim to `--text-disabled` with a "as of HH:MM" stamp.
- **Flash-on-update:** position/blotter rows briefly tint `--color-profit`/`--color-loss` at low alpha when their value changes on a new mark, then fade via `--ease-quartr`.
- **Range-selector and tab transitions** use the pill-slide pattern already in the codebase; respect `prefers-reduced-motion` (kill tickers/flashes, keep instant updates).

---

### Top 10 must-have elements, ranked

1. **KPI/Stat card strip** — NAV, total P&L, day P&L, realized/unrealized, buying power, win rate. The at-a-glance truth of the paper account.
2. **Equity / NAV curve with benchmark overlay + range selector** — the headline "is this idea working" visual; needs the mark-to-market loop + NIFTY benchmark.
3. **Holdings / positions table with source attribution** — closes the orders↔portfolio gap; the `source`/`source_id` join is what makes forward-testing possible.
4. **Open-orders blotter (resting LIMIT/GTT/SL/TP) with cancel** — makes registered/triggered orders visible and actionable, wired to existing actions.
5. **Trade / fill journal with fee+slippage breakdown** — auditable ledger using `trading_costs.py`; trust + transparency.
6. **Per-strategy / per-agent forward-test scorecards (live vs backtest)** — the second core aim; the unique differentiator vs every reference product.
7. **P&L attribution / contribution (contributors-detractors + treemap)** — answers "what's driving returns," per holding and per idea.
8. **Allocation donut (sector + per-idea)** — concentration and exposure at a glance, with over-weight warnings.
9. **Drawdown chart + top-drawdowns table** — risk story under the equity curve, reusing `backtest_metrics.py`.
10. **Calendar / daily-returns heatmap** — Zerodha-style consistency view; cheap to build (no chart lib) and high perceived polish.

*(Watchlist, activity feed, and number-ticker polish are strong Tier-3 follow-ons but rank below the ten above.)*

**Data prerequisites this catalog implies (DB):** a `paper_account` (cash ledger, net deposits, buying power), `paper_position` (derived from fills, with `source`/`source_id` provenance), `paper_nav_point` (mark-to-market time series for equity/drawdown/calendar), and an `idea_attribution` rollup keyed by originating workflow/strategy/chat-idea — all fed by the scheduler's mark-to-market loop and the existing `TradeLog`/`WorkflowRun` tables.

Sources: [Alpaca paper trading dashboard](https://alpaca.markets/learn/start-paper-trading), [Alpaca portfolio tracker review](https://pinklion.xyz/blog/alpaca-trading-portfolio-tracker-2025-guide/), [Composer backtesting](https://www.composer.trade/learn/backtesting-basics), [Composer allocation/fees](https://www.composer.trade/blog/backtest-portfolio), [Koyfin portfolio tools](https://www.koyfin.com/help/portfolio-tools-functionality/), [Koyfin model portfolios](https://www.koyfin.com/features/model-portfolios/), [Sharesight contribution analysis](https://help.sharesight.com/contribution-analysis-report/), [Sharesight performance](https://www.sharesight.com/us/investment-portfolio-performance/), [Zerodha Console P&L heatmap](https://zerodha.com/products/console/), [LogRocket React chart libraries 2025](https://blog.logrocket.com/best-react-chart-libraries-2025/), [PkgPulse recharts vs visx vs nivo 2026](https://www.pkgpulse.com/guides/recharts-vs-chartjs-vs-nivo-vs-visx-react-charting-2026).


---

# Forward-Testing Methodology (raw)

I now have everything I need: the existing `backtest_metrics.py` functions to reuse, the `dsl_backtest_runs` schema (migration 0011), the `TradeLog.source`/`source_id` convention, and `WorkflowRun` IDs, plus solid web research on forward-testing methodology. Here is the deliverable.

---

## 3. Forward-Testing Methodology & Idea Scorecards

Forward testing is paper trading treated as **out-of-sample (OOS) validation**: instead of replaying history, you let each idea trade forward against live prices in Pivot's simulated broker and measure whether the edge that showed up in the backtest survives in unseen data. Paper trading and forward testing are *not* the same thing — paper trading just simulates execution without capital; forward testing adds the discipline of attribution, a fixed observation window, and a backtest-vs-live comparison so you can answer one question per idea: **"is the edge real out-of-sample, or was it curve-fit?"** This section specifies how Pivot attributes fills to ideas, the per-idea scorecard (reusing `services/backtest_metrics.py`), the backtest↔forward comparison, the promote/decay lifecycle, and exactly what to snapshot.

### 3.1 Attributing every paper fill to its originating idea

Every simulated fill must carry a stable **idea identity** so P&L can be sliced per idea, not just per account. Pivot already has the raw materials: `TradeLog.source` + `TradeLog.source_id`, `WorkflowRun.id` / `Workflow.id`, `Strategy.id`, and `Conversation.id`. Today `source` is populated with `"chat"` / `"chat-confirm"` (orders.py:157,245) and workflow actions route through `place_order`. We normalize this into a single attribution key.

**Idea taxonomy (the `origin_kind` of every fill):**

| origin_kind | Pivot anchor | How the fill gets tagged |
|---|---|---|
| `workflow` | `Workflow.id` (the durable idea) + `WorkflowRun.id` (the specific firing) | Action steps in `workflows/steps/actions.py` already know the run; stamp both onto the TradeLog at fill time |
| `chat` | `Conversation.id` + message id; an LLM-named idea label | `/orders/register` & `/orders/confirm` already set `source="chat*"`; add the conv id as `source_id` and a human idea label |
| `strategy` | `Strategy.id` | for SIP / structured-product / saved-strategy legs |
| `manual` | none | user-placed paper order with no idea behind it; still attributed to a synthetic "Manual" idea so account NAV reconciles |

**The `idea` is the unit of forward-testing, distinct from a single run.** A workflow that fires 40 times over a quarter is **one** idea accumulating 40 runs' worth of fills; a backtest-and-paper chat idea is one idea. So introduce a thin **`ForwardIdea`** registry row (the "symphony" in Composer's vocabulary, the "strategy" in QuantConnect's) that owns: `origin_kind`, the originating id (`workflow_id` / `conversation_id` / `strategy_id`), a label, `inception_date` (first paper fill), `status` (`paper` → `candidate` → `promoted` / `retired`), and an optional `backtest_run_id` FK into `dsl_backtest_runs` so the forward result can be compared to the stored backtest. Each `TradeLog` paper fill gets a nullable `idea_id` FK to this registry (back-filled from `source`/`source_id` at fill time). This is the join that makes per-idea scorecards possible without re-deriving attribution on every read.

**Why a registry and not just `GROUP BY source_id`:** an idea's identity is stable while its triggering run-ids churn; promotion/retirement is a property of the idea, not the run; and chat ideas need a human label the raw ids don't carry. The registry is the durable handle the dashboard cohorts on.

### 3.2 Per-idea scorecard (computed over the live window)

For each `ForwardIdea` we keep a **per-idea daily NAV series** (its slice of the simulated portfolio — cash committed to that idea's open lots + mark-to-market of those lots against live yfinance/Kite quotes). Every scorecard metric is derived from that series and the idea's fills, **reusing the single-source metrics module** so live numbers are computed identically to backtest numbers (this parity is the whole point — same `sharpe_sortino`, same `calendar_cagr_pct`, same cost basis):

| Scorecard metric | Definition | How computed (reuse) |
|---|---|---|
| Cumulative return | idea NAV end/start − 1 | from idea daily-NAV snapshots |
| Annualized return (CAGR) | (end/start)^(365.25/days) − 1 | `backtest_metrics.calendar_cagr_pct()` — identical convention to backtest |
| Sharpe / Sortino | annualized, rf = 6.5% G-Sec | `backtest_metrics.sharpe_sortino(daily_returns_from_equity(nav))` |
| **Alpha vs NIFTY** | idea return − β·(NIFTY return); plus **Information Ratio** = active return / tracking error | snapshot NIFTY daily alongside NAV; IR is the benchmark-relative score Composer surfaces |
| Max drawdown | worst peak-to-trough on idea NAV | from idea daily-NAV (add a `max_drawdown_pct` helper next to `sharpe_sortino` if not present) |
| Win rate, avg win / avg loss | per closed round-trip lot | from `TradeLog` fills grouped into lots per idea |
| Exposure / turnover | avg gross exposure ÷ idea NAV; traded notional ÷ avg NAV | from positions + fills |
| **Hit rate of triggers** | fraction of trigger firings that produced a profitable resulting position (forward-test–specific) | from `WorkflowRun` firings joined to the lot they opened |
| **Slippage vs intended price** | filled avg_price − intended/quoted price at decision, in bps; realized vs the `slippage_bps()` the backtest assumed | from `TradeLog.average_price` vs the quote captured at registration; compare to `trading_costs.slippage_bps()` |
| **PSR / min-track-record flag** | probability the true Sharpe > 0 given sample length + skew/kurtosis; and the MinTRL needed to call it real | new helper (Bailey–López de Prado) — see 3.4; gates promotion |

All P&L is **after the same realistic costs** the backtester uses (`trading_costs.round_trip_bps()`), so a forward result is never flattered by zero-cost fills — the #1 reason paper results overstate live edge.

### 3.3 Backtest vs forward: "is the edge real out-of-sample?"

This is the headline view. Because the backtest already lives in `dsl_backtest_runs` (migration 0011: `result`, `total_return_pct`, window, tree) and the forward scorecard reuses the *same* metric functions, the two are directly comparable. For each idea with a linked `backtest_run_id`, render a side-by-side **degradation panel**:

| | Backtest (in-sample, `dsl_backtest_runs`) | Forward / paper (out-of-sample) | Decay |
|---|---|---|---|
| CAGR | 31% | 14% | −17pp |
| Sharpe | 1.8 | 0.7 | −1.1 |
| Max DD | −9% | −16% | worse |
| Win rate | 58% | 51% | −7pp |
| Realized slippage | assumed ~`slippage_bps()` | measured bps | drift |

**Interpretation rules (the OOS verdict):**
- **Healthy:** forward Sharpe within ~1 std-error band of backtest Sharpe, same sign of alpha, slippage near the assumed bps. The edge generalizes.
- **Decayed:** forward Sharpe materially below backtest and below its own MinTRL threshold → curve-fit / regime-shift suspect. Most backtest→live failures are exactly this: overfitting, regime shifts, and parameter sensitivity not captured historically.
- **Execution problem (not signal):** returns drop but the **slippage gap** explains most of it → it's an implementation-shortfall issue (paper underestimates real fills), distinct from signal decay. Keep these separate in the panel because the fix differs (sizing/routing vs kill the idea).

This mirrors what mature platforms do: QuantConnect scores a strategy by its **one-year OOS Sharpe with an explicit penalty proportional to how little OOS data exists** — a strategy with 6 months OOS has its Sharpe halved. Pivot should apply the same maturity discount so young paper ideas can't claim a high score on three weeks of luck.

### 3.4 Cohorting, lifecycle & statistical significance

**Lifecycle states** (on `ForwardIdea.status`): `paper` → `candidate` → `promoted` (eligible for live) → or `retired`. Composer/QuantConnect both gate "go live" on accumulated OOS evidence, never on the backtest alone.

**Graduation gate (`paper` → `candidate` → `promoted`):** an idea may be flagged "promote to live" only when **all** hold:
1. **Minimum observation window met.** A fixed calendar floor (e.g. ≥ 60–90 trading days) *and* a **minimum sample of trades/trigger firings** (e.g. ≥ 20–30 round-trips) — daily-floor alone is meaningless for a low-frequency idea.
2. **MinTRL satisfied.** The **Probabilistic Sharpe Ratio** exceeds the confidence threshold (e.g. PSR > 0.95 that true Sharpe > 0), i.e. the realized track record is at least the **Minimum Track Record Length** that Bailey–López de Prado require given the idea's sample length, skew and kurtosis. This is the rigorous answer to "minimum sample" — it lengthens automatically for noisier, fatter-tailed ideas.
3. **OOS consistency.** Forward Sharpe ≥ a fraction of backtest Sharpe (apply the QuantConnect-style maturity penalty), and forward alpha vs NIFTY > 0.
4. **Execution sane.** Realized slippage not wildly above the assumed `slippage_bps()`.

**Decay flagging (`promoted`/`candidate` → review/`retired`):** continuously monitor **alpha decay** (expected return per trade falling over successive live days) and the **realized-vs-expected slippage gap**; trip a flag when rolling forward Sharpe falls below its MinTRL threshold or the slippage gap blows out during volatility/liquidity stress. Auto-flag for review (canary/rollback semantics), don't silently kill — surface it on the dashboard with the degradation panel as evidence.

**Cohorting for the dashboard:** group ideas by `origin_kind` (workflow vs chat vs strategy), by inception vintage (monthly cohorts, so you can see "ideas born in March" age together), and by lifecycle state. This lets the dashboard answer "what % of paper ideas survive to candidate?" — the survival curve that tells you whether the idea-generation process itself has edge.

**Pitfalls to encode (so the scorecard isn't self-deceiving):**
- **Look-ahead / data-snooping:** the intended price and benchmark must be snapshotted **at decision time**, never recomputed later from adjusted history — otherwise forward "slippage" and alpha are fictional. Forward testing's value is that it's genuinely OOS; capturing the live quote at registration is what preserves that.
- **Selection bias / multiple testing:** if many ideas run in parallel, the best one's Sharpe is inflated by luck across the cohort — this is exactly what the **Deflated Sharpe Ratio** corrects (selection bias under multiple testing + non-normal returns). Track the number of ideas trialed and deflate before promoting.
- **Survivorship:** the backtest already carries a "current ticker only, no survivorship adjustment" caveat (`methodology_note`); the forward window is inherently point-in-time so it's clean — keep the caveat visible so users don't read the backtest column as equally OOS.
- **Paper-fill optimism:** paper trading systematically under-states slippage and ignores market impact/latency, so a paper edge is an **upper bound** on live; the explicit cost model + the slippage-gap metric are the guardrails.

### 3.5 Data you must snapshot (daily, per account AND per idea)

The entire scorecard is derivable only if you persist a daily time-series at two grains. Mark-to-market via `kite/market_data.get_live_quote` / `yfinance_service`, run on the existing scheduler loop at market close.

- **Per-account daily NAV snapshot:** date, cash ledger balance, total positions market value, total equity/NAV, realized P&L to date, unrealized P&L, **NIFTY close** (benchmark). One row/account/day.
- **Per-idea daily NAV snapshot:** date, `idea_id`, capital committed (open-lot cost basis), mark-to-market value of that idea's lots, idea NAV, realized + unrealized P&L for the idea, open exposure. One row/idea/day — this is the series every per-idea metric reads.
- **At-decision snapshot on each fill (already mostly in `TradeLog`):** intended/quoted price at registration, quote timestamp, `idea_id`, `origin_kind`, originating `workflow_run_id` / `conversation_id`. Needed for slippage-vs-intended and trigger hit-rate.
- **Per-idea backtest linkage:** `ForwardIdea.backtest_run_id` → `dsl_backtest_runs.id`, so the degradation panel needs no recomputation of the in-sample side.

### Metrics to persist vs compute-on-read

**Persist (immutable facts you cannot reconstruct later — snapshot or you lose them):**
- Daily **per-account NAV snapshot** (cash, positions MV, NAV, realized/unrealized, NIFTY close).
- Daily **per-idea NAV snapshot** (committed capital, MV, idea NAV, realized/unrealized).
- Every **fill** with its **at-decision intended price + quote timestamp**, `idea_id`, `origin_kind`, originating run/conv id, and the cost components actually charged.
- The **`ForwardIdea`** registry row: `inception_date`, `origin_kind`, originating ids, `label`, `status`, `status_changed_at`, `backtest_run_id`, and the **number of ideas trialed in its cohort** (for DSR deflation).
- A small **point-in-time scorecard cache** written at each daily close (cumulative return, Sharpe, alpha, PSR, drawdown) so dashboard lists and cohort survival curves don't recompute across thousands of NAV rows — a "list-view convenience copy," exactly the pattern `dsl_backtest_runs` already uses with `total_return_pct`/`total_trades`.

**Compute-on-read (cheap, deterministic from persisted series — never store, to avoid staleness):**
- Sharpe / Sortino via `sharpe_sortino(daily_returns_from_equity(nav))`.
- CAGR via `calendar_cagr_pct(start, end, …)`.
- Max drawdown, win rate, avg win/loss, exposure, turnover, trigger hit-rate.
- Alpha / beta / Information Ratio vs the persisted NIFTY series.
- Realized slippage in bps (filled avg_price vs persisted intended price) and the slippage-vs-assumed gap.
- PSR / MinTRL / Deflated Sharpe (functions of the persisted return series + trial count).
- The **backtest-vs-forward degradation panel** (joins persisted forward scorecard against `dsl_backtest_runs.result`).

Rule of thumb: **persist the raw daily NAV and fill facts + the cohort trial count; compute every ratio on read** using the existing `backtest_metrics.py` so backtest and forward numbers are guaranteed apples-to-apples — with one denormalized scorecard cache per idea per day purely for list-view performance.

---

**Key implementation files/anchors for this section** (all absolute):
- Reuse metrics: `/Users/karanveersingh/Downloads/Second_Star/pivot/backend/services/backtest_metrics.py` (`sharpe_sortino`, `daily_returns_from_equity`, `calendar_cagr_pct`, `methodology_note`) and `/Users/karanveersingh/Downloads/Second_Star/pivot/backend/services/trading_costs.py` (`round_trip_bps`, `slippage_bps`).
- Backtest store to compare against: `/Users/karanveersingh/Downloads/Second_Star/pivot/migrations/versions/0011_dsl_backtest_runs.py` (`dsl_backtest_runs.result`, `total_return_pct`).
- Attribution sources: `/Users/karanveersingh/Downloads/Second_Star/pivot/backend/models.py` (`TradeLog.source`/`source_id` at L212–213, `Workflow` L258, `WorkflowRun` L325, `Strategy` L103, `Conversation` L450); `/Users/karanveersingh/Downloads/Second_Star/pivot/backend/routers/orders.py` (`source="chat"`/`"chat-confirm"` at L157/L245); workflow fills via `/Users/karanveersingh/Downloads/Second_Star/pivot/backend/workflows/steps/actions.py`.
- New DB objects implied: a `forward_ideas` registry table + `idea_id` FK on `TradeLog`, a `daily_nav_snapshots` table (account grain), and a `daily_idea_nav_snapshots` table (idea grain) — all additive, following the migration-0011 pattern (String(36) UUID PKs, `_json_type` for JSON/JSONB).

**Sources:**
- [Forward Testing vs Backtesting (PickMyTrade)](https://blog.pickmytrade.trade/forward-testing-vs-backtesting-2025-guide/)
- [Forward Testing vs Backtesting / Market Regime Validation (Meridian)](https://meridianmarkets.substack.com/p/forward-testing-vs-backtesting-in)
- [Walk-Forward Analysis vs Backtesting (Surmount)](https://surmount.ai/blogs/walk-forward-analysis-vs-backtesting-pros-cons-best-practices)
- [Walk-Forward Optimization (QuantInsti)](https://blog.quantinsti.com/walk-forward-optimization-introduction/)
- [When is a strategy "good enough" to go live? (QuantConnect)](https://www.quantconnect.com/forum/discussion/15725/when-is-a-strategy-quot-good-enough-quot-to-go-live/)
- [QuantConnect community strategies scoring (OOS Sharpe penalty)](https://www.quantconnect.com/docs/v2/cloud-platform/community/strategies)
- [Probabilistic Sharpe Ratio & Minimum Track Record Length (Portfolio Optimizer)](https://portfoliooptimizer.io/blog/the-probabilistic-sharpe-ratio-hypothesis-testing-and-minimum-track-record-length-for-the-difference-of-sharpe-ratios/)
- [The Deflated Sharpe Ratio — Bailey & López de Prado (PDF)](https://www.davidhbailey.com/dhbpapers/deflated-sharpe.pdf)
- [Deflated Sharpe ratio (Wikipedia)](https://en.wikipedia.org/wiki/Deflated_Sharpe_ratio)
- [TCA: Detecting drift in live trading / alpha decay (KX)](https://kx.com/blog/drift-detections-blind-spot-how-live-tca-insights-help-firms-win-the-race-against-alpha-decay/)
- [The Reliability of Paper Trading (TradersPost)](https://blog.traderspost.io/article/the-reliability-of-paper-trading-insights-and-best-practices)
- [What 20 Weeks of Live Trading Revealed About Slippage (Quanta72)](https://quanta72.substack.com/p/what-20-weeks-of-live-trading-revealed)
- [Composer live trading & benchmarked performance dashboards](https://www.composer.trade/learn/quantconnect-vs-composer-which-is-the-better-platform-to-create-a-stock-trading-bot)
- [Composer Symphony Database (Sharpe, Drawdown filters)](https://www.composer.trade/trading-strategies)


---

# Code Seams & Integration Points (raw)

Now I have enough detail. Let me compile the comprehensive seams document:

## 4. Code Seams & Integration Points (verified)

### Call Sites: place_order, place_gtt_order, cancel_order, get_orders, get_positions

**backend/kite/orders.py** (all public APIs):
- `place_order(access_token, tradingsymbol, exchange, transaction_type, quantity, order_type, price=None, product="CNC", trigger_price=None, tag="pivot", variety="regular") → dict` (lines 17–65)
  - Mock: returns `{"order_id": "MOCK{counter}", "status": "COMPLETE", "message": "..."}`
  - Real: calls `kite.place_order(...)` → returns `{"order_id": str, "status": "PENDING", "variety": str, "message": str}`
- `place_gtt_order(access_token, tradingsymbol, exchange, transaction_type, quantity, trigger_price, limit_price, last_price) → dict` (lines 68–101)
  - Mock: returns `{"trigger_id": int, "status": "active", "message": f"Mock GTT {gtt_id} created"}`
  - Real: calls `kite.place_gtt(...)` → returns `{"trigger_id": int, "status": "active", "message": str}`
- `get_orders(access_token) → list` (lines 104–109)
  - Mock: returns `MOCK_ORDERS` (from backend/kite/mock_data.py)
  - Real: returns `kite.orders()` (today's orders list)
- `cancel_order(access_token, order_id, variety="regular") → dict` (lines 112–118)
  - Mock: returns `{"order_id": order_id, "status": "CANCELLED"}`
  - Real: calls `kite.cancel_order(variety=variety, order_id=order_id)` → returns `{"order_id": order_id, "status": "CANCELLED", "variety": variety}`
- **No `get_positions` in orders.py** (portfolio reconciliation is separate; see backend/kite/portfolio.py)

**Call Sites in Routers:**
1. **backend/routers/orders.py:135** — POST /orders/confirm
   ```python
   result = kite_orders.place_order(
       access_token=kite_token,
       tradingsymbol=req["tradingsymbol"],
       exchange=req["exchange"],
       transaction_type=req["transaction_type"],
       quantity=req["quantity"],
       order_type=req["order_type"],
       price=req.get("price"),
       product=req.get("product", "CNC"),
   )
   ```
   Returns result dict; logs TradeLog(status=result["status"], source="chat", placed_at=now_ist()).

2. **backend/routers/orders.py:338** — POST /orders/gtt
   ```python
   return kite_orders.place_gtt_order(
       access_token=kite_token,
       tradingsymbol=request.tradingsymbol,
       exchange=request.exchange,
       transaction_type=request.transaction_type,
       quantity=request.quantity,
       trigger_price=request.trigger_price,
       limit_price=request.limit_price,
       last_price=request.last_price,
   )
   ```

3. **backend/scheduler.py:196** — Job 1: execute_due_sips (lines 140–303)
   ```python
   result = place_order(
       access_token=kite_token,
       tradingsymbol=sip.symbol,
       exchange="NSE",
       transaction_type="BUY",
       quantity=quantity,
       order_type="MARKET",
       product="CNC",
       tag=f"sip_{sip.id}",
   )
   ```
   Logs TradeLog(status=result["status"], source="sip", source_id=sip.id).

**Call Sites in Workflows:**
1. **backend/workflows/steps/actions.py:244** — execute_action_place_order (lines 145–281)
   ```python
   result = place_order(
       access_token=token,
       tradingsymbol=str(cfg["symbol"]),
       exchange="NSE",
       transaction_type=transaction_type,
       quantity=quantity,
       order_type=order_type,
       price=price,
       product=str(cfg.get("product", "CNC")).upper(),
       tag=f"wf_{ctx.client_request_id[:16]}",
   )
   ```
   Returns `{"order_id": str, "status": str, "client_request_id": ctx.client_request_id, "symbol": str, "side": str, "executed_price": float|None, "quantity": int, "executed_value_inr": float|None, "notional_inr_used": float|None}`.

2. **backend/workflows/steps/actions.py:312** — execute_action_cancel_orders (lines 302–343)
   ```python
   orders = get_orders(token) or []
   # ... filter for pending orders ...
   for o in pending:
       cancel_order(token, order_id)
   ```
   Returns `{"cancelled_count": int, "order_ids": list[str]}`.

3. **backend/workflows/steps/actions.py:350+** — execute_action_set_stoploss / execute_action_set_takeprofit (not fully shown)
   - Uses `place_gtt_order(...)` for stop-loss GTT placement.

---

### Mock Mode Switch: KITE_MOCK_MODE, token=="mock_token"

**backend/kite/auth.py** (lines 1–187):
- `KITE_MOCK_MODE: bool = not bool(settings.kite_api_key)` (line 19)
  - Set at module import time; reflects whether `settings.kite_api_key` is configured.
  - Can flip at runtime via `set_kite_credentials(api_key, api_secret)` (lines 66–92) or `clear_kite_credentials()` (lines 95–103).
  - Propagated to mirrored modules: `["backend.kite.orders", "backend.routers.kite", "backend.kite.ticker"]` (line 32–36).

**backend/kite/orders.py** (lines 36–40, 81–84, 106–107, 114–115):
- Every public function checks `if KITE_MOCK_MODE:` and returns synthetic data (MOCK{counter} order IDs, MOCK_ORDERS list, etc.) before calling live Kite.

**Token fallback** (backend/routers/orders.py:131–133, backend/scheduler.py:194):
- If user has no KiteSession or no access_token, callers pass `"mock_token"` to place_order, which triggers the same KITE_MOCK_MODE branch.
- Result: default dev users (no Kite session) always mock-fill.

---

### Workflow Context: user_id, client_request_id

**backend/workflows/engine.py** (lines 98–103):
- `client_request_id = sha1(f"{run_id}:{step_index}:{attempts}")` — deterministic hash for idempotency.
- Passed to executor as `ctx.client_request_id`.

**backend/workflows/steps/actions.py** (lines 43–60, 52, 198, 253, 272):
- `ctx.workflow.user_id` — integer user_id from the Workflow row.
- `ctx.client_request_id` — 40-char hex SHA-1 (used as tag in place_order call, returned in action output).
- `ctx.db` — sync SQLAlchemy Session for DB queries.
- `ctx.run.id` — string UUID of the WorkflowRun row.
- `ctx.run.context` — dict[str, Any] (inter-step data bag, keyed by stringified step_index).

**Executor pattern** (e.g., lines 145–281):
```python
async def execute_action_place_order(ctx: Any) -> Optional[dict[str, Any]]:
    cfg = ctx.config  # step config (validated Pydantic)
    requires_approval = bool(cfg.get("requires_approval", False))
    # ... approval logic ...
    token = _kite_token_for_run(ctx)  # resolve token from ctx.workflow.user_id
    result = place_order(access_token=token, ..., tag=f"wf_{ctx.client_request_id[:16]}")
    # Return dict with client_request_id for audit trail
    return {"order_id": ..., "client_request_id": ctx.client_request_id, ...}
```

---

### Scheduler: next_run_at, workflow trigger polling

**backend/scheduler.py** (lines 1–560):
- APScheduler instance with job store (SQL-backed in prod, memory in dev).
- Jobs: execute_due_sips, check_strategy_triggers, refresh_kite_tokens, send_daily_summary (lines 79–130).
- All times in IST (Asia/Kolkata).

**backend/workflows/scheduler.py** (lines 1–450+):
- **Polling every 30s** (line 58: `_POLL_INTERVAL_SECONDS = 30`).
- **Job registration** (lines 300+): `register_workflow_scheduler(scheduler)` adds a cron job with id `"pivot_workflows_poll"`.
- **Fire logic** (lines 269–410):
  ```python
  # Scan Workflow/WorkflowStep rows WHERE:
  # - workflow.status == "active"
  # - workflow.expires_at is None OR expires_at > now
  # - WorkflowStep.step_type IN ("trigger.schedule", "trigger.market_relative_time")
  # - WorkflowStep.next_run_at <= fired_at
  # → Create a WorkflowRun, fire engine.execute_run(run_id)
  # → Update WorkflowStep.next_run_at to next cron tick
  # → Update Workflow.next_run_at to the earliest next_run_at across all steps
  ```
- **next_run_at computation** (lines 183–210):
  - `compute_next_run_at(cron, tz_str, after=None) → datetime` — uses pytz + CronTrigger to compute next fire time in UTC.
  - Raises `InvalidCronError` on malformed cron (caught by API as 422).

**Integration**: A PaperBroker can register a recurring job via APScheduler's `add_job()` to:
1. Poll TradeLog rows (source="chat" or source="workflow", status="registered"/"pending") every 60s at market hours.
2. Fill resting orders against live prices.
3. Snapshot mark-to-market NAV at 15:30 IST close.

---

### TradeLog: source, source_id, status usage

**Models** (backend/models.py:196–217):
```python
class TradeLog(Base):
    __tablename__ = "trade_logs"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    kite_order_id = Column(String(50), nullable=True, index=True)
    symbol = Column(String(50), nullable=False)
    exchange = Column(String(10), nullable=False)
    transaction_type = Column(String(10), nullable=False)
    order_type = Column(String(20), nullable=False)
    quantity = Column(Integer, nullable=False)
    price = Column(Float, nullable=True)
    trigger_price = Column(Float, nullable=True)
    status = Column(String(20), nullable=False)
    average_price = Column(Float, nullable=True)
    filled_quantity = Column(Integer, nullable=True)
    source = Column(String(50), nullable=True)
    source_id = Column(Integer, nullable=True)
    placed_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
```

**Source values written today**:
- `"chat"` — from POST /orders/confirm (backend/routers/orders.py:157)
- `"chat-confirm"` — from POST /orders/register (backend/routers/orders.py:245)
- `"sip"` — from scheduler SIP job (backend/scheduler.py:218)
- `"workflow"` — NOT YET written (Day 2 feature; actions.py doesn't log TradeLog directly; that's the gap)

**Status values written**:
- `"registered"` — POST /orders/register (intent, not broker-placed)
- `result["status"]` from place_order → typically `"COMPLETE"` (mock) or `"PENDING"` (real)
- No reads back from TradeLog today; portfolio tables are static MOCK_HOLDINGS.

**Gap**: Workflow action.place_order does NOT write TradeLog. The broker result is only returned in ctx.run.context["steps"][step_index] (per-run output bag). This is the PaperBroker's hook: intercept place_order and log a TradeLog row per execution.

---

### services/trading_costs.py: Public API

**File**: /Users/karanveersingh/Downloads/Second_Star/pivot/backend/services/trading_costs.py

**Functions**:
1. `buy_cost(price: float, qty: float) → tuple[float, float]`
   - Returns (net_debit_including_charges, total_charges_in_rupees).
   - Assumes CNC (delivery) equity. Includes brokerage, slippage, STT, exchange, SEBI, GST, stamp duty.

2. `sell_cost(price: float, qty: float) → tuple[float, float]`
   - Returns (net_credit_after_charges, total_charges_in_rupees).
   - No stamp duty on sell (asymmetric vs. buy).

3. `leg_bps(side: str) → float`
   - Effective per-leg cost as a fraction of notional (e.g., 0.00385 = 38.5 bps for buy).
   - Used by multiplier-based backtester engines.

4. `round_trip_bps() → float`
   - Total round-trip (buy + sell) cost in basis points, ~35–40 bps live.

5. `slippage_bps() → float`
   - Slippage alone in bps (configurable via `PIVOT_SLIPPAGE_PCT` env var, default 5 bps).

**Constants** (env-overridable):
- `BROKERAGE_PER_ORDER` (default ₹20 per order)
- `SLIPPAGE_PCT` (default 0.0005 = 0.05%)
- `STT_PCT = 0.001` (0.1%, both buy & sell)
- Others: EXCHANGE_PCT, SEBI_PCT, GST_PCT, STAMP_BUY_PCT (hardcoded per Indian regulations).

---

### services/backtest_metrics.py: Public API

**File**: /Users/karanveersingh/Downloads/Second_Star/pivot/backend/services/backtest_metrics.py

**Functions**:
1. `sharpe_sortino(daily_returns: Sequence[float], rf_annual: float = 0.065) → tuple[Optional[float], Optional[float]]`
   - Returns (sharpe_annualized, sortino_annualized) from a sequence of daily returns (fractions, not %).
   - Uses √252 annualization.
   - Default risk-free rate = 6.5% (10Y G-Sec proxy).
   - Returns (None, None) if <2 data points or zero dispersion.

2. `daily_returns_from_equity(equity: Sequence[float]) → list[float]`
   - Converts an equity curve (sequence of portfolio values) to period-over-period fractional returns.

3. `calendar_cagr_pct(start_value: float, end_value: float, start: date|datetime|str, end: date|datetime|str) → float`
   - CAGR (%) on a CALENDAR-year basis: (end/start)^(1/years) - 1.
   - years = calendar_days / 365.25 (professional convention, not trading-days/252).

4. `methodology_note(*, start: object = None, end: object = None, period_label: str = "") → dict`
   - Returns a dict with `window`, `costs`, `basis`, `caveat` strings for rendering on backtest cards.
   - Pulls live round_trip_bps() from trading_costs so numbers stay in sync.

**Constants**:
- `DEFAULT_RF_ANNUAL = 0.065` (6.5% annual risk-free)
- `_TRADING_DAYS = 252`

---

### Router Registration Pattern (main.py:77–116)

**Pattern**:
```python
from backend.routers.<module> import router as <alias>_router
app.include_router(<alias>_router)
```

**Order matters**:
- Line 94: `scheduled_router` MUST mount before `workflows_router` so `/api/workflows/scheduled-runs` is caught by the more-specific scheduled_router, not the `/api/workflows/{id}` glob catch in workflows_router.

**To add a new PaperBroker router**:
```python
from backend.routers.paper_broker import router as paper_broker_router
app.include_router(paper_broker_router)  # Insert around line 80, after orders_router
```

Standard pattern: `APIRouter(prefix="/paper-broker", tags=["Paper Broker"])`.

---

### TradeLog Write Sites (Grep Summary)

| Site | Line | File | Source | Status | Notes |
|------|------|------|--------|--------|-------|
| POST /orders/confirm | 147–161 | routers/orders.py | "chat" | result["status"] | User-confirmed chat order |
| POST /orders/register | 234–249 | routers/orders.py | "chat-confirm" | "registered" | Intent only, no broker call |
| SIP job | 209–221 | scheduler.py | "sip" | result["status"] | Scheduled SIP execution |
| action.place_order | **NOT YET** | workflows/steps/actions.py | "workflow" | — | Day 2 gap: action returns dict, doesn't log TradeLog |

---

### Workflow Expiry (R4b): expires_at Column

**Model** (backend/models.py:278):
```python
expires_at = Column(DateTime(timezone=True), nullable=True)  # NULL = no expiry
```

**Engine check** (backend/workflows/scheduler.py:~305–310):
- Before firing a workflow run, scheduler verifies:
  ```python
  if wf.expires_at is not None and wf.expires_at <= now():
      # Transition workflow to paused, skip run
  ```
- Allows auto-deactivation on a specified datetime (e.g., end-of-backtest window).

---

### Summary: PaperBroker Integration Seams

| Layer | Module | Seam | Signature |
|-------|--------|------|-----------|
| **Orders** | backend/kite/orders.py | place_order | `(token, symbol, exchange, tx_type, qty, order_type, price=None, product="CNC", trigger_price=None, tag="pivot", variety="regular") → {order_id, status, ...}` |
| **Orders** | backend/kite/orders.py | place_gtt_order | `(token, symbol, exchange, tx_type, qty, trigger_price, limit_price, last_price) → {trigger_id, status, ...}` |
| **Orders** | backend/kite/orders.py | cancel_order | `(token, order_id, variety="regular") → {order_id, status, ...}` |
| **Orders** | backend/kite/orders.py | get_orders | `(token) → list[dict]` (today's orders) |
| **Mock Gate** | backend/kite/auth.py | KITE_MOCK_MODE | bool (module-level flag, runtime-flippable) |
| **Workflow Context** | backend/workflows/engine.py | _client_request_id | `(run_id, step_index, attempts) → sha1_hex` |
| **Workflow Context** | backend/workflows/steps/actions.py | execute_action_place_order | `(ctx: EngineContext) → {order_id, client_request_id, symbol, side, executed_price, quantity, ...}` |
| **Scheduler** | backend/workflows/scheduler.py | compute_next_run_at | `(cron, tz, after=None) → datetime(UTC)` |
| **Scheduler** | backend/workflows/scheduler.py | register_workflow_scheduler | `(scheduler) → None` (polls every 30s, fires due WorkflowStep.next_run_at rows) |
| **Costs** | backend/services/trading_costs.py | buy_cost, sell_cost | `(price, qty) → (net, charges)` |
| **Costs** | backend/services/trading_costs.py | leg_bps | `(side) → float` (fractional cost) |
| **Metrics** | backend/services/backtest_metrics.py | sharpe_sortino | `(daily_returns, rf_annual=0.065) → (sharpe, sortino)` |
| **Metrics** | backend/services/backtest_metrics.py | calendar_cagr_pct | `(start_val, end_val, start_date, end_date) → float(%)` |
| **Metrics** | backend/services/backtest_metrics.py | methodology_note | `(start, end, period_label) → dict` |
| **Logging** | backend/models.py | TradeLog | id, user_id, symbol, exchange, tx_type, order_type, qty, price, trigger_price, status, average_price, filled_qty, source, source_id, placed_at |
| **Router** | backend/main.py | app.include_router | Pattern: `from backend.routers.<module> import router; app.include_router(router)` |


---

# DB Conventions / Portfolio Endpoints / FE Wiring (raw)

Perfect! Now I have comprehensive understanding. Let me create the final markdown report:

## 5. DB Conventions, Portfolio Endpoints & FE Wiring (verified)

### A. Alembic Migration Conventions

**File Structure & Pattern:**
- Location: `/Users/karanveersingh/Downloads/Second_Star/pivot/migrations/versions/`
- Latest: `0012_workflow_expires_at.py` (rev ID: `0012_workflow_expires_at`, down_revision: `0011_dsl_backtest_runs`)
- Next migration will be `0013_*.py` with `down_revision = "0012_workflow_expires_at"`

**Revision Chain Pattern** (from 0011 & 0012):
```python
revision: str = "0011_dsl_backtest_runs"  # or "0012_workflow_expires_at"
down_revision: Union[str, None] = "0010_news_retraction_tracking"  # or "0011_dsl_backtest_runs"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None
```

**JSON/JSONB Handling** (dual-dialect pattern in 0011):
```python
def _json_type(bind):
    if bind.dialect.name == "postgresql":
        return postgresql.JSONB(astext_type=sa.Text())
    return sa.JSON()

# In upgrade():
bind = op.get_bind()
JSON_T = _json_type(bind)
op.create_table("table_name", sa.Column("field", JSON_T, ...))
```
- **Postgres production:** renders as `JSONB` (binary, indexed)
- **SQLite tests:** renders as `JSON` (text, cross-compatible)

**DateTime/TIMESTAMP Handling** (from 0011):
```python
sa.Column(
    "started_at", sa.DateTime(timezone=True),
    nullable=False,
    server_default=(
        sa.text("now()") if bind.dialect.name == "postgresql"
        else sa.text("CURRENT_TIMESTAMP")
    ),
)
```

**Table Naming & Indexing** (from 0011):
- Snake_case table names: `dsl_backtest_runs`, `workflow_runs`, `workflow_steps`
- Composite indexes named with table prefix: `ix_dsl_backtest_runs_user_started` = `["user_id", sa.text("started_at DESC")]`
- CheckConstraints for enums (SQLite compatibility): `ck_dsl_backtest_runs_status`

---

### B. dsl_backtest_runs Table Schema (from Migration 0011 + Model)

**Location:** `/Users/karanveersingh/Downloads/Second_Star/pivot/backend/workflows/dsl/backtest/models.py` (ORM model)

**Columns (21 total):**
1. `id` - String(36), PK, default=_uuid_str (UUID v4 string)
2. `user_id` - Integer, FK→users.id, indexed, not null
3. `tree` - JSON/JSONB, not null (the DSL tree AST)
4. `request` - JSON/JSONB, not null (full BacktestRequest payload)
5. `result` - JSON/JSONB, nullable (EquityPoint[] + trades on success)
6. `tree_summary` - Text, not null (tree_to_english() for list views)
7. `primary_symbol` - String(32), not null, indexed (filter by symbol)
8. `start_date` - Date, not null
9. `end_date` - Date, not null
10. `status` - String(16), not null, default="running" (CHECK: running|succeeded|failed|cancelled)
11. `error_message` - Text, nullable (populated on failed)
12. `started_at` - DateTime(timezone=True), not null, server_default=func.now()
13. `finished_at` - DateTime(timezone=True), nullable
14. `total_return_pct` - Float, nullable (list-view convenience copy)
15. `total_trades` - Integer, nullable (list-view convenience copy)

**Storage note:** result column capped at ~50 KB JSON per row (5-year daily curve ≈ 1,300 EquityPoint + trade list).

**Index:** `ix_dsl_backtest_runs_user_started` on (user_id, started_at DESC)

---

### C. Backend Model Conventions

**Location:** `/Users/karanveersingh/Downloads/Second_Star/pivot/backend/models.py` (lines 1–530)

**PK Style:**
- Older user-facing tables: Integer PK with index (`User`, `Strategy`, `ProductPosition`, `TradeLog`), all with `server_default=func.now()` timestamps
- Workflow tables (≥Day 1): String(36) UUID via `_uuid_str()` default factory (lines 26–29):
  ```python
  def _uuid_str() -> str:
      """Cross-dialect UUID v4 string default. Postgres-compatible (TEXT/UUID),
      SQLite-compatible (TEXT). All workflow tables use 36-char string PKs."""
      return str(_uuid.uuid4())
  ```

**Timestamp Convention:**
```python
created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
```

**Relationship + back_populates:**
```python
# In User (line 46):
kite_session = relationship("KiteSession", back_populates="user", uselist=False)
strategies = relationship("Strategy", back_populates="user")
trade_logs = relationship("TradeLog", back_populates="user")

# In related model:
user = relationship("User", back_populates="kite_session")
```

**Enum Handling** (native_enum=False for SQLite):
```python
status = Column(
    SQLEnum(WorkflowStatus, name="workflow_status", native_enum=False),
    nullable=False,
    default=WorkflowStatus.draft,
)
```
- Postgres: creates true ENUM type
- SQLite: generates CHECK constraint `ck_workflow_status`

**TradeLog Schema** (lines 196–217):
```python
id (Integer, PK)
user_id (Integer, FK→users.id)
kite_order_id (String(50), nullable, indexed)  # "registered" orders get None here
symbol (String(50), not null)
exchange (String(10), not null)
transaction_type (String(10), not null)  # "BUY" | "SELL"
order_type (String(20), not null)  # "MARKET" | "LIMIT" | "GTT" | "SL" | "OCO"
quantity (Integer, not null)
price (Float, nullable)
trigger_price (Float, nullable)
status (String(20), not null)  # "registered" | "pending" | "filled" | "cancelled" | "failed"
average_price (Float, nullable)
filled_quantity (Integer, nullable)
source (String(50), nullable)  # "chat-confirm", "workflow", "backtest", etc.
source_id (Integer, nullable)  # strategy.id, workflow_run.id, etc. (store as String(36) UUID once model grows)
placed_at (DateTime(timezone=True), server_default=func.now())
updated_at (DateTime(timezone=True), onupdate=func.now())
```

---

### D. Portfolio Endpoints & Response Shapes

**Location:** 
- Routers: `/Users/karanveersingh/Downloads/Second_Star/pivot/backend/routers/portfolio.py` (lines 1–116)
- Services: `/Users/karanveersingh/Downloads/Second_Star/pivot/backend/services/portfolio.py` (78 lines)
- Cache: `/Users/karanveersingh/Downloads/Second_Star/pivot/backend/services/portfolio_cache.py` (106 lines)
- Perf: `/Users/karanveersingh/Downloads/Second_Star/pivot/backend/routers/portfolio_perf.py` (172 lines)

**Endpoints:**

1. **GET /portfolio/summary** (line 50–55)
   - Returns: `PortfolioSummary` dict with keys: `total_value`, `invested_value`, `total_pnl`, `total_pnl_pct`, `day_pnl`, `num_holdings`
   - Source: `get_summary_cached(user_id, token)` → Kite mock → `MOCK_HOLDINGS`
   - TTL: 30 seconds (Redis)

2. **GET /portfolio/holdings** (line 58–67)
   - Returns: `Holding[]` array with fields: `tradingsymbol`, `exchange`, `quantity`, `average_price`, `last_price`, `pnl`, `day_change`, `day_change_percentage`, `sector`
   - Source: `get_holdings_cached(user_id, token)` → enriched with `SECTOR_MAP` (KV: symbol → sector string)
   - TTL: 30 seconds

3. **GET /portfolio/sector** (line 70–87)
   - Returns: `{ sectors: [{sector, value, pct}, ...], total_value, is_concentrated }`
   - Aggregates holdings by sector; flags concentration if any sector > 40%

4. **GET /portfolio/products** (line 90–97)
   - Returns: `ProductPosition[]` from DB (active only)
   - Fields: `id`, `product_type`, `display_name`, `capital_deployed`, `maturity_date`, `status`

5. **GET /portfolio/yields** (line 100–115)
   - Returns: `[{instrument, key, gross_yield_pct, after_tax_yield_pct, tax_slab_used, is_best}]`
   - Async call to `get_all_yields()` → applies `calculate_after_tax_yield()`

6. **GET /api/portfolio/performance?period=1M|3M|6M|1Y|5Y** (perf.py line 76–166)
   - Returns: `PerformanceResponse` with:
     ```python
     {
       "period": str,
       "points": [{"t": datetime, "v": float}, ...],
       "starting_value": float,
       "ending_value": float,
       "total_return": float,
       "total_return_pct": float
     }
     ```
   - Computes historical portfolio value by yfinance per-symbol close × quantity, summed daily

**Caching Seam** (portfolio_cache.py lines 62–90):
```python
def get_summary_cached(user_id: int, kite_token: str) -> dict:
    key = f"portfolio:summary:{user_id}"
    cached = _read(key)  # Redis GET
    if cached is not None:
        return cached
    fresh = get_portfolio_summary(kite_token)  # Kite call or mock
    _write(key, fresh)  # Redis SET with 30s TTL
    return fresh

def get_holdings_cached(user_id: int, kite_token: str) -> list[dict]:
    # Same pattern with holdings
    ...

def invalidate(user_id: int) -> None:
    # Called post-order (not wired today); manual 30s TTL is practical invalidation
    redis_client.delete(f"portfolio:summary:{user_id}")
    redis_client.delete(f"portfolio:holdings:{user_id}")
```

**Service Interface** (portfolio.py lines 35–77):
```python
def get_user_portfolio(user_id: int, db: Session) -> dict[str, Any]:
    """Returns workflow fetch.portfolio shape:
    {
      "holdings": [{tradingsymbol, exchange, quantity, average_price, last_price}, ...],
      "buying_power": float,
      "total_value": float,
    }
    """
    # Pulls holdings + summary + margins, strips to columns engine cares about
```

---

### E. Frontend Wiring (pivot-next/)

**Location:** `/Users/karanveersingh/Downloads/Second_Star/pivot-next/`

#### E.1. Tab Declaration (AppShell.tsx lines 73–90)

**TabKey type:**
```typescript
type TabKey = "chat" | "portfolio" | "agents" | "calendar" | "screener";

const NAV_ITEMS: {
  key: TabKey;
  label: string;
  Icon: React.ComponentType<{ className?: string; "aria-hidden"?: boolean }>;
}[] = [
  { key: "chat", label: "Chat", Icon: MessageSquare },
  { key: "portfolio", label: "Portfolio", Icon: PieChart },
  { key: "agents", label: "Agents", Icon: Settings },
  { key: "calendar", label: "Calendar", Icon: CalendarDays },
  { key: "screener", label: "Screener", Icon: BarChart2 },
];

const DEFAULT_TAB: TabKey = "chat";
```

**Tab Switching** (AppShell.tsx lines 95–99):
```typescript
function readHashTab(): TabKey {
  if (typeof window === "undefined") return DEFAULT_TAB;
  const raw = window.location.hash.replace(/^#/, "");
  const valid: TabKey[] = NAV_ITEMS.map((t) => t.key);
  return valid.includes(raw as TabKey) ? (raw as TabKey) : DEFAULT_TAB;
}
// useState<TabKey> -> reacts to window.location.hash
```

**Tab Rendering** (AppShell.tsx lines 484–513):
```typescript
{active === "chat" && (
  <DashboardTab onOpenWorkflow={openWorkflow} onOpenCalendar={openCalendar} onChatActiveChange={setChatActive} />
)}
{active === "portfolio" && <PortfolioTab />}
{active === "calendar" && <CalendarTab onOpenWorkflow={openWorkflowById} />}
{active === "screener" && <ScreenerPage />}
{active === "agents" && <AgentsTab onOpenWorkflow={openWorkflow} />}
```

#### E.2. API Client (lib/api.ts lines 1–250+)

**Base URL & Auth** (lines 40–89):
```typescript
const DEFAULT_BASE = "/api";
function getBaseUrl(): string {
  return (
    (typeof process !== "undefined" && process.env.NEXT_PUBLIC_PIVOT_API_BASE) ||
    DEFAULT_BASE
  );
}
type AuthTokenProvider = () => string | null | Promise<string | null>;
let authTokenProvider: AuthTokenProvider = () => null;
export function setAuthTokenProvider(provider: AuthTokenProvider): void {
  authTokenProvider = provider;
}

// Legacy routes (portfolio, orders, auth) NOT under /api:
function getLegacyBase(): string {
  return getBaseUrl().replace(/\/api\/?$/, "");
}

async function request<T>(path: string, options: RequestOptions = {}): Promise<ApiResult<T>> {
  return _doRequest<T>(getBaseUrl(), path, options);
}
async function requestLegacy<T>(path: string, options: RequestOptions = {}): Promise<ApiResult<T>> {
  return _doRequest<T>(getLegacyBase(), path, options);
}
```

**Portfolio Types** (lines 480–511):
```typescript
export type Holding = {
  tradingsymbol: string;
  exchange: string;
  quantity: number;
  average_price: number;
  last_price: number;
  pnl: number;
  day_change: number;
  day_change_percentage: number;
};

export type PortfolioSummary = {
  total_value: number;
  invested_value: number;
  total_pnl: number;
  total_pnl_pct: number;
  day_pnl: number;
  num_holdings: number;
};

/** GET /portfolio/summary — backed by Kite (mock when KITE_API_KEY is empty). */
export function getPortfolioSummary(): Promise<ApiResult<PortfolioSummary>> {
  return requestLegacy<PortfolioSummary>("/portfolio/summary");
}

/** GET /portfolio/holdings — list of Holdings (mock data in test mode). */
export function getPortfolioHoldings(): Promise<ApiResult<Holding[]>> {
  return requestLegacy<Holding[]>("/portfolio/holdings");
}
```

**Portfolio Performance** (implied in api.ts, routers/portfolio_perf.py):
```typescript
export type PerfPoint = { t: datetime; v: float };
export type PortfolioPerformanceResponse = {
  period: str;
  points: PerfPoint[];
  starting_value: float;
  ending_value: float;
  total_return: float;
  total_return_pct: float;
};
export function getPortfolioPerformance(period: "1M"|"3M"|"6M"|"1Y"|"5Y"): Promise<ApiResult<PortfolioPerformanceResponse>> {
  return request<PortfolioPerformanceResponse>("/portfolio/performance", { query: { period } });
}
```

**Pattern for Adding New Endpoints:**
1. Define TypeScript type (e.g., `export type PaperTradingSummary = { ... }`)
2. Create getter function: `export function getPaperTradingSummary(...): Promise<ApiResult<PaperTradingSummary>> { return requestLegacy|request(...) }`
3. Import in component via `import { getPaperTradingSummary } from "@/lib/api"`
4. Call with `const result = await getPaperTradingSummary(...)` + check `isError(result)`

#### E.3. Quartr Theme Tokens (app/globals.css lines 7–226)

**Light Mode Root Variables** (lines 8–129):
```css
--bg-base: #ffffff;          /* page bg — paper white */
--bg-primary: #fbfbfc;       /* card surface — very subtle tint */
--bg-secondary: #f6f6f8;
--bg-card: #fbfbfc;
--bg-elevated: #ececee;      /* hover / active — the warm grey */

--glass-bg: #fbfbfc;
--glass-border: rgba(15, 18, 22, 0.08);
--glass-border-hover: rgba(15, 18, 22, 0.16);
--glass-border-focus: rgba(15, 18, 22, 0.32);

--surface-hover: rgba(15, 18, 22, 0.03);
--surface-active: rgba(15, 18, 22, 0.06);

--text-primary: #0d0d0e;     /* near-black ink */
--text-secondary: #4d555c;   /* mid-grey */
--text-tertiary: #6b7280;
--text-disabled: #9aa1a8;
--metric-label: var(--text-secondary);

--color-profit: #059669;
--color-loss: #dc2626;
--color-warn: #d97706;
--pivot-blue: #051650;
--price-line: #0f172a;

--font-display: 'Inter', system-ui, sans-serif;
--font-mono: 'JetBrains Mono', 'SF Mono', Menlo, monospace;
--font-ui: 'Inter', system-ui, sans-serif;
--font-serif: 'Newsreader', 'Times New Roman', Georgia, serif;
--font-experiment: 'Newsreader', Georgia, serif;  /* Pivot wordmark + greeting only */

--weight-display: 550;
--weight-medium: 500;
--weight-bold: 700;

--radius-xs: 4px;
--radius-sm: 8px;
--radius-md: 12px;
--radius-lg: 16px;
--radius-xl: 24px;
--radius-pill: 9999px;

--ease-quartr: cubic-bezier(0.22, 1, 0.36, 1);
```

**Dark Mode Override** (lines 131–200):
```css
.dark {
  --bg-base: #0d0d0e;
  --bg-primary: #111212;
  --bg-secondary: #15161a;
  --bg-card: #181a1f;
  --bg-elevated: #1f2127;
  
  --text-primary: #fbfcfc;
  --text-secondary: #8f98a1;
  --text-tertiary: #6b7280;
  
  --surface-hover: rgba(255, 255, 255, 0.02);
  --surface-active: rgba(255, 255, 255, 0.04);
  /* ... rest mirrored */
}
```

**Helper Classes** (lines 278–307):
```css
.q-display {
  font-family: var(--font-display);
  font-weight: var(--weight-display);
  letter-spacing: -0.025em;
}

.q-mono { font-family: var(--font-mono); }

.q-greeting {
  font-family: var(--font-experiment);
  font-weight: var(--weight-display);
  font-size: clamp(36px, 4vw, 46px);
  letter-spacing: -0.04em;
  line-height: 1.05;
  color: var(--text-primary);
  text-align: center;
}

.q-uppercase-label {
  font-size: 11px;
  font-weight: var(--weight-medium);
  letter-spacing: 0.02em;
  text-transform: uppercase;
  color: var(--text-tertiary);
}
```

#### E.4. Adding a "Paper Trading" Tab

**Step 1: Update AppShell.tsx NAV_ITEMS** (line 80–90):
```typescript
type TabKey = "chat" | "portfolio" | "agents" | "calendar" | "screener" | "paper-trading";

const NAV_ITEMS = [
  { key: "chat", label: "Chat", Icon: MessageSquare },
  { key: "portfolio", label: "Portfolio", Icon: PieChart },
  { key: "paper-trading", label: "Paper Trading", Icon: TrendingUp },  // NEW
  { key: "agents", label: "Agents", Icon: Settings },
  { key: "calendar", label: "Calendar", Icon: CalendarDays },
  { key: "screener", label: "Screener", Icon: BarChart2 },
];
```

**Step 2: Add Tab Rendering** (line 513):
```typescript
{active === "paper-trading" && <PaperTradingTab />}
```

**Step 3: Create lib/api.ts Types** (parallel to lines 480–511):
```typescript
export type PaperTradingSummary = {
  nav_value: number;
  cash_balance: number;
  total_invested: number;
  realized_pnl: number;
  unrealized_pnl: number;
  total_pnl_pct: number;
};

export type PaperPosition = {
  id: string;
  symbol: string;
  quantity: number;
  entry_price: number;
  current_price: number;
  pnl: number;
  pnl_pct: number;
};

export type PaperEquityCurve = {
  points: [{ t: datetime; nav: float }, ...];
};

export function getPaperTradingSummary(): Promise<ApiResult<PaperTradingSummary>> {
  return requestLegacy<PaperTradingSummary>("/paper-trading/summary");
}

export function getPaperPositions(): Promise<ApiResult<PaperPosition[]>> {
  return requestLegacy<PaperPosition[]>("/paper-trading/positions");
}

export function getPaperEquityCurve(): Promise<ApiResult<PaperEquityCurve>> {
  return request<PaperEquityCurve>("/paper-trading/equity-curve");
}
```

**Step 4: Create components/PaperTradingTab.tsx** (mirror DashboardTab/PortfolioTab structure):
```typescript
import { useEffect, useState } from "react";
import { getPaperTradingSummary, getPaperPositions, getPaperEquityCurve, type PaperTradingSummary, type PaperPosition } from "@/lib/api";

export function PaperTradingTab(): React.ReactElement {
  const [summary, setSummary] = useState<PaperTradingSummary | null>(null);
  const [positions, setPositions] = useState<PaperPosition[]>([]);
  
  useEffect(() => {
    void (async () => {
      const s = await getPaperTradingSummary();
      if (!isError(s)) setSummary(s.data);
      const p = await getPaperPositions();
      if (!isError(p)) setPositions(p.data);
    })();
  }, []);
  
  return (
    <div className="p-6">
      {/* Render summary metrics using Quartr tokens */}
      {/* Render positions table using recharts for equity curve */}
    </div>
  );
}
```

---

### F. Frontend Charting Library

**Location:** `/Users/karanveersingh/Downloads/Second_Star/pivot-next/package.json` line 44

**Installed:** `recharts: 2.15.3`

- Used for portfolio performance chart (already wired in routers/portfolio_perf.py)
- Supports responsive line/bar/area charts with legend, tooltip, grid
- React component library (no canvas/SVG manipulation needed)

**Usage Pattern (from existing code):**
```typescript
import { LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer } from "recharts";

<ResponsiveContainer width="100%" height={300}>
  <LineChart data={points}>
    <CartesianGrid stroke="var(--border)" />
    <XAxis dataKey="t" stroke="var(--text-secondary)" />
    <YAxis stroke="var(--text-secondary)" />
    <Tooltip contentStyle={{ background: "var(--bg-card)", border: "1px solid var(--glass-border)" }} />
    <Line type="monotone" dataKey="v" stroke="var(--pivot-blue)" strokeWidth={2} dot={false} />
  </LineChart>
</ResponsiveContainer>
```

---

### G. New Tables for Paper Trading

**To Add via 0013_paper_trading.py:**

1. **paper_trading_account** (per user, singular)
   - user_id (FK, PK)
   - nav_value (Float)
   - cash_balance (Float)
   - total_invested (Float)
   - start_nav (Float, for % calc)
   - created_at (DateTime, server_default=func.now())
   - updated_at (DateTime, onupdate=func.now())

2. **paper_trading_positions** (per symbol, per account)
   - id (String(36), PK, default=_uuid_str)
   - user_id (FK, indexed)
   - symbol (String(50), indexed)
   - quantity (Integer)
   - entry_price (Float)
   - current_price (Float, updated by mark-to-market loop)
   - pnl (Float, derived: (current_price - entry_price) * quantity)
   - source (String(50), e.g. "chat", "workflow:workflow_id")
   - created_at, updated_at (DateTime + onupdate)

3. **paper_trading_ledger** (cash transactions)
   - id (String(36), PK)
   - user_id (FK)
   - trade_log_id (FK→trade_logs.id, the fill that triggered this)
   - description (Text)
   - amount (Float, signed: +deposit, -buycost, +fillproceeds)
   - balance_after (Float, running balance)
   - recorded_at (DateTime, server_default)

4. **paper_trading_equity_curve** (daily NAV snapshots)
   - id (String(36), PK)
   - user_id (FK)
   - timestamp (DateTime, indexed)
   - nav (Float)
   - total_pnl (Float)
   - cash_balance (Float)

**Note:** These follow the Pivot convention of String(36) UUID PKs for new system tables, server_default timestamps, and FK relationships mirroring `backend/models.py`.


---

# Backend & Database Design (raw)

Confirmed — `_place_squareoff_legs` passes `client_request_id=leg_req` AND `order_type` as a keyword, but `place_order` in `orders.py` accepts neither (its signature ends at `variety` and has no `client_request_id`; squareoff also passes `order_type="MARKET"` positionally-safe but `client_request_id` would raise `TypeError`). This is the latent bug — currently masked only because squareoff actions are reached rarely. I now have full ground truth. Here is the design.

---

## 6. Backend & Database Design

This synthesizes the verified seams into a build-ready plan. Guiding principles, all confirmed against the codebase:

1. **One broker seam.** `backend/kite/orders.py::place_order` / `place_gtt_order` / `get_orders` / `cancel_order` are the *only* functions chat (`routers/orders.py:135`), SIP (`scheduler.py:196`), and all eight workflow actions (`actions.py`) call. Make `orders.py` a per-account router that dispatches to a new `PaperBroker` or the existing Kite path. Zero changes downstream.
2. **Positions and cash are derived from the immutable fills log, never incrementally mutated counters** — the NautilusTrader restart-double-fill guard. `paper_positions` is a *cache* rebuildable from `paper_fills`.
3. **Reuse `trading_costs.py` for every cost** (`buy_cost`/`sell_cost`/`slippage_bps`) and `backtest_metrics.py` for every ratio. No new numbers; preserves the live↔backtest parity just fixed.
4. **Idempotency via a persisted `client_request_id` unique constraint.** Fix the latent bug first: `place_order` must accept `client_request_id` and `order_type` keyword args (squareoff already passes them — `actions.py:1011`).

---

### (a) DATA MODEL

All new tables use the **workflow-table conventions** (`String(36)` UUID PK via `_uuid_str`, `JSON` type that renders JSONB on PG, `DateTime(timezone=True)` with `server_default=func.now()`, `SQLEnum(..., native_enum=False)` → CHECK on SQLite / ENUM on PG). They live in `backend/models.py` after `TradeLog`. FK targets are the **integer** `users.id` and existing string-UUID workflow/conversation/backtest PKs.

#### Table 1 — `paper_accounts` (per-user simulated broker account)

One row per user (per "book"; allow multiple later via a `label`). Holds the cash ledger head and settlement buckets. **Persist** — it is the authoritative cash state; recomputing from the full ledger on every order is wasteful, but it must be *reconcilable* against `paper_ledger`.

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
| `mode` | SQLEnum(`PaperAccountMode`: `paper`/`live`) | no | | `paper` | broker-routing switch (see c) |
| `is_active` | Boolean | no | | `True` | |
| `created_at` | DateTime(tz) | no | | `func.now()` | |
| `updated_at` | DateTime(tz) | no | | `func.now()`, onupdate | |

Relationships: `user = relationship("User", back_populates="paper_account")`; `User.paper_account = relationship(..., uselist=False)`. Children: `orders`, `fills`, `positions`, `nav_snapshots`, `ledger_entries`.

**Buying-power rule** (long-only CNC): `buying_power = cash_available − cash_reserved`. No leverage; live shorts rejected (matches `allocate_basket` `NotImplementedError` at `actions.py:554`).

#### Table 2 — `paper_orders` (order lifecycle, incl. resting LIMIT/GTT/SL/TP)

One row per order intent. MARKET fills synchronously then flips to `filled`; LIMIT/STOP/GTT **rest** and are drained by the scheduler. **Persist** — the lifecycle, resting state, and at-decision intended price are facts you cannot reconstruct.

| Column | Type | Null | FK / Index | Default | Notes |
|---|---|---|---|---|---|
| `id` | String(36) | no | PK | `_uuid_str` | the synthetic `order_id` returned to callers |
| `account_id` | String(36) | no | FK→`paper_accounts.id`, index | | |
| `user_id` | Integer | no | FK→`users.id`, index | | denormalized for fast filters |
| `client_request_id` | String(80) | yes | **UNIQUE index** | | idempotency key; squareoff legs use `…:legN:SYM` (>40 chars → 80) |
| `symbol` | String(50) | no | index | | |
| `exchange` | String(10) | no | | `"NSE"` | |
| `transaction_type` | String(10) | no | | | BUY / SELL |
| `order_type` | String(16) | no | | | MARKET / LIMIT / SL / SL-M / GTT |
| `product` | String(8) | no | | `"CNC"` | |
| `variety` | String(16) | no | | `"regular"` | regular / amo |
| `quantity` | Integer | no | | | requested qty |
| `limit_price` | Float | yes | | | LIMIT/SL-limit |
| `trigger_price` | Float | yes | | | STOP/GTT trigger |
| `intended_price` | Float | yes | | | **LTP/quote at decision** (slippage-vs-intended) |
| `intended_quote_at` | DateTime(tz) | yes | | | quote timestamp (look-ahead guard) |
| `status` | SQLEnum(`PaperOrderStatus`) | no | index | `pending` | `pending`/`queued`/`resting`/`partially_filled`/`filled`/`cancelled`/`rejected` |
| `reserved_cash` | Float | no | | `0.0` | reserve released on fill/cancel |
| `filled_quantity` | Integer | no | | `0` | |
| `reject_reason` | String(200) | yes | | | e.g. `insufficient_buying_power`, `slice_too_small` |
| `gtt_oco_group` | String(36) | yes | index | | OCO sibling group; on one fill, cancel siblings |
| `parent_order_id` | String(36) | yes | FK→`paper_orders.id` | | bracket entry → SL/TP children |
| **attribution ↓** | | | | | |
| `source` | String(50) | yes | index | | `chat`/`chat-confirm`/`sip`/`workflow`/`manual` (mirrors `TradeLog.source`) |
| `origin_kind` | String(16) | yes | | | `workflow`/`chat`/`strategy`/`manual` |
| `workflow_id` | String(36) | yes | FK→`workflows.id` | | durable idea |
| `workflow_run_id` | String(36) | yes | FK→`workflow_runs.id` | | the specific firing |
| `conversation_id` | Integer | yes | FK→`conversations.id` | | chat idea |
| `strategy_id` | Integer | yes | FK→`strategies.id` | | SIP / saved strategy |
| `idea_id` | String(36) | yes | FK→`forward_ideas.id`, index | | resolved idea (back-filled at insert) |
| `created_at` / `updated_at` | DateTime(tz) | no | | `func.now()` / onupdate | |

**GTT modeling:** `place_gtt_order` writes a `paper_orders` row with `order_type="GTT"`, `trigger_price` set, `status="resting"`. SL and TP are two such rows sharing a `gtt_oco_group` (OCO); the scheduler cancels the sibling on first fill.

#### Table 3 — `paper_fills` (immutable executions — the source of truth)

One row per execution (a partial fill emits multiple). **Never updated.** Positions and cash derive from this; this is the reconciliation anchor.

| Column | Type | Null | FK / Index | Default | Notes |
|---|---|---|---|---|---|
| `id` | String(36) | no | PK | `_uuid_str` | |
| `order_id` | String(36) | no | FK→`paper_orders.id`, index | | |
| `account_id` | String(36) | no | FK→`paper_accounts.id`, index | | |
| `user_id` | Integer | no | FK→`users.id`, index | | |
| `symbol` | String(50) | no | index | | |
| `transaction_type` | String(10) | no | | | BUY / SELL |
| `quantity` | Integer | no | | | this fill's qty |
| `fill_price` | Float | no | | | touch ± slippage (post-`SLIPPAGE_PCT`) |
| `gross_value` | Float | no | | | `fill_price * quantity` |
| `charges` | Float | no | | | from `buy_cost`/`sell_cost` (2nd tuple elt) |
| `net_cashflow` | Float | no | | | signed: − on buy (`net_debit`), + on sell (`net_credit`) |
| `slippage_bps` | Float | yes | | | `(fill_price/intended_price−1)*1e4` for TCA |
| `realized_pnl` | Float | yes | | | booked on SELLs via avg-cost; null on BUYs |
| `settles_at` | DateTime(tz) | yes | | | T+1 for SELL proceeds (display honesty) |
| `idea_id` | String(36) | yes | FK→`forward_ideas.id`, index | | copied from order |
| `trade_log_id` | Integer | yes | FK→`trade_logs.id` | | link to the existing audit row |
| `filled_at` | DateTime(tz) | no | index | `func.now()` | |

> **Cost wiring:** BUY → `(net_debit, charges) = buy_cost(fill_price, qty)`; `net_cashflow = −net_debit`. SELL → `(net_credit, charges) = sell_cost(fill_price, qty)`; `net_cashflow = +net_credit`; `realized_pnl = net_credit − qty*avg_cost`. **No new cost code.**

#### Table 4 — `paper_positions` (open lots — a derived cache)

One row per (account, symbol) with non-zero qty. **Derivable-on-read** from `paper_fills`, but cached for dashboard/buying-power speed and rebuildable by `marks.reconcile()`. Avg-cost convention (matches the backtester).

| Column | Type | Null | FK / Index | Default | Notes |
|---|---|---|---|---|---|
| `id` | String(36) | no | PK | `_uuid_str` | |
| `account_id` | String(36) | no | FK→`paper_accounts.id`, index | | |
| `user_id` | Integer | no | FK→`users.id`, index | | |
| `symbol` | String(50) | no | **unique(account_id,symbol)** | | |
| `quantity` | Integer | no | | `0` | long-only ≥0 |
| `avg_cost` | Float | no | | `0.0` | cost basis incl. buy charges |
| `realized_pnl` | Float | no | | `0.0` | cumulative booked P&L for this symbol |
| `last_price` | Float | yes | | | from mark-to-market |
| `last_mark_at` | DateTime(tz) | yes | | | |
| `prev_close` | Float | yes | | | EOD snapshot for Day-P&L |
| `stale` | Boolean | no | | `False` | quote older than N min / market shut → mark vs close |
| `updated_at` | DateTime(tz) | no | | onupdate | |

`unrealized_pnl = quantity*(last_price − avg_cost)`, `day_pnl = quantity*(last_price − prev_close)` — both **compute-on-read** (the `Holding.day_change`/`day_change_percentage` shape becomes real).

#### Table 5 — `paper_ledger` (cash transactions — audit trail)

Append-only signed cash movements with running balance. **Persist** — the reconciliation trail; `paper_accounts.cash_*` is its head.

| Column | Type | Null | FK / Index | Notes |
|---|---|---|---|---|
| `id` | String(36) | no | PK | `_uuid_str` |
| `account_id` | String(36) | no | FK→`paper_accounts.id`, index | |
| `fill_id` | String(36) | yes | FK→`paper_fills.id` | the fill that caused this (null for seed/deposit) |
| `kind` | String(24) | no | | `seed`/`buy_debit`/`sell_credit`/`reserve`/`release`/`settlement` |
| `amount` | Float | no | | signed |
| `balance_after` | Float | no | | running `cash_available` |
| `note` | String(200) | yes | | |
| `recorded_at` | DateTime(tz) | no | index, `func.now()` | |

#### Table 6 — `forward_ideas` (idea-attribution registry — the forward-test unit)

The durable handle the dashboard cohorts on; **not** a `GROUP BY source_id`, because identity is stable while run-ids churn and chat ideas need a human label. **Persist** (registry + the immutable cohort trial count for Deflated-Sharpe).

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
| `cohort_trial_count` | Integer | no | | `1` | # ideas trialed in cohort (DSR deflation) |
| `scorecard_cache` | JSON | yes | | | list-view convenience copy (cum_return, sharpe, alpha, psr, mdd) — same pattern as `dsl_backtest_runs.total_return_pct` |
| `created_at` / `updated_at` | DateTime(tz) | no | | `func.now()` / onupdate | |

**Uniqueness** to dedup idea creation: partial-unique on the populated origin id (e.g. one idea per `(user_id, workflow_id)`; one per `(user_id, conversation_id, label)`). Enforce in the resolver, not a DB partial index (SQLite-friendly).

#### Table 7 — `paper_nav_snapshots` (account-grain daily equity curve)

One row/account/day. **Persist** — you lose the curve otherwise; ratios compute-on-read from it.

| Column | Type | Null | FK / Index | Notes |
|---|---|---|---|---|
| `id` | String(36) | no | PK | |
| `account_id` | String(36) | no | FK, **unique(account_id, as_of_date)** | |
| `user_id` | Integer | no | FK, index | |
| `as_of_date` | Date | no | index | |
| `cash_available` | Float | no | | |
| `cash_settled` | Float | no | | |
| `positions_mv` | Float | no | | Σ qty·LTP |
| `nav` | Float | no | | `cash_available + positions_mv` |
| `realized_pnl_cum` | Float | no | | to date |
| `unrealized_pnl` | Float | no | | |
| `nifty_close` | Float | yes | | benchmark for alpha/IR |
| `is_stale` | Boolean | no | | marked-vs-close flag |
| `created_at` | DateTime(tz) | no | `func.now()` | |

#### Table 8 — `paper_idea_nav_snapshots` (idea-grain daily curve — forward scorecard series)

One row/idea/day; the series every per-idea metric reads. **Persist.**

| Column | Type | Null | FK / Index | Notes |
|---|---|---|---|---|
| `id` | String(36) | no | PK | |
| `idea_id` | String(36) | no | FK→`forward_ideas.id`, **unique(idea_id, as_of_date)** | |
| `account_id` | String(36) | no | FK, index | |
| `as_of_date` | Date | no | index | |
| `committed_capital` | Float | no | | open-lot cost basis for this idea |
| `positions_mv` | Float | no | | MV of this idea's lots |
| `idea_nav` | Float | no | | committed-cash slice + MV |
| `realized_pnl` | Float | no | | |
| `unrealized_pnl` | Float | no | | |
| `nifty_close` | Float | yes | | shared benchmark |
| `created_at` | DateTime(tz) | no | `func.now()` | |

> **Idea-grain lot accounting:** an idea owns the *lots its fills opened*. Tag each open lot with `idea_id`; on a SELL, decrement lots FIFO per idea so committed-capital/MV/realized split cleanly. Store this lot ledger implicitly via `paper_fills.idea_id` + FIFO over fills (no extra table needed for v1; add `paper_lots` only if FIFO-over-fills proves too slow).

**Derive-on-read, never store:** Sharpe/Sortino (`sharpe_sortino(daily_returns_from_equity(nav_series))`), CAGR (`calendar_cagr_pct`), max-drawdown, win rate, exposure/turnover, alpha/β/IR vs persisted `nifty_close`, realized-vs-assumed slippage gap, PSR/MinTRL/Deflated-Sharpe, and the whole backtest-vs-forward degradation panel (join `forward_ideas.backtest_run_id` → `dsl_backtest_runs.result`). The only denormalized copy is `forward_ideas.scorecard_cache`, refreshed at each daily close for list views.

---

### (b) MIGRATIONS

Three additive migrations, chained off `0012_workflow_expires_at`. Pure `create_table` + one tiny `add_column` — **no destructive ALTERs**. Follow the 0011 pattern exactly: `_json_type(bind)` helper, dialect-branched `server_default` (`now()` on PG / `CURRENT_TIMESTAMP` on SQLite), enums as `postgresql.ENUM(...)` on PG / plain `String` + `CheckConstraint` named `ck_*` on SQLite.

| Rev | File | down_revision | Creates |
|---|---|---|---|
| `0013_paper_accounts_orders_fills` | `0013_paper_accounts_orders_fills.py` | `0012_workflow_expires_at` | `paper_accounts`, `paper_orders`, `paper_fills`, `paper_positions`, `paper_ledger` |
| `0014_forward_ideas` | `0014_forward_ideas.py` | `0013_…` | `forward_ideas`; **add `trade_logs.idea_id String(36) nullable` + index** (the one ALTER — additive nullable column, safe on PG & SQLite batch mode) |
| `0015_paper_nav_snapshots` | `0015_paper_nav_snapshots.py` | `0014_…` | `paper_nav_snapshots`, `paper_idea_nav_snapshots` |

Cross-dialect specifics, all proven in 0011:
- **JSONB/JSON**: `JSON_T = _json_type(op.get_bind())` for `paper_orders` (none needed today, but `forward_ideas.scorecard_cache` uses it).
- **ENUMs**: build `postgresql.ENUM("paper","candidate","promoted","retired", name="forward_idea_status")` and `.create(bind, checkfirst=True)` on PG; on SQLite use `sa.String(16)` + `sa.CheckConstraint("status IN (...)", name="ck_forward_ideas_status")`. Same for `paper_order_status` and `paper_account_mode`.
- **Composite/unique indexes**: `op.create_index("ix_paper_orders_account_status", "paper_orders", ["account_id","status"])`; `op.create_unique_constraint("uq_paper_positions_acct_sym", "paper_positions", ["account_id","symbol"])`; `op.create_index("ux_paper_orders_client_req", "paper_orders", ["client_request_id"], unique=True, postgresql_where=sa.text("client_request_id IS NOT NULL"))` (partial on PG; plain unique on SQLite tolerates multiple NULLs natively).
- **`downgrade()`** drops in reverse dependency order (snapshots → idea fk → fills/positions/ledger → orders → accounts; drop the PG ENUM types last with `.drop(bind, checkfirst=True)`).
- **Backfill (data migration in 0014, optional):** one pass over historical `TradeLog` where `source LIKE 'chat%'`/`'workflow'`/`'sip'` → create `forward_ideas` rows and stamp `trade_logs.idea_id`. Idempotent; safe to skip on a fresh DB.

---

### (c) PAPER BROKER SERVICE

New package `backend/paper/` (a sibling of `backend/kite/`), so kite stays the real-broker home and `paper/` owns simulation.

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

**`broker.py` — byte-identical signatures to `kite/orders.py` (the drop-in):**

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

Return shapes match today's mock so callers don't change: `place_order → {"order_id","status":"COMPLETE","average_price","filled_quantity","message"}` (`average_price` is what `actions.py:259` reads for `executed_price`); `place_gtt_order → {"trigger_id","status":"active","message"}`.

**The account-routing switch — make `kite/orders.py` a thin shim.** This is the minimal-diff core. `orders.py::place_order` keeps its identity but, before the `KITE_MOCK_MODE` branch, resolves the account mode and delegates:

```python
def place_order(access_token, tradingsymbol, ..., client_request_id=None, order_type="MARKET", **kw):
    if _use_paper_broker(access_token):          # account.mode=="paper" OR KITE_MOCK_MODE OR token=="mock_token"
        from backend.paper import broker as paper
        return paper.place_order(access_token, tradingsymbol, ..., client_request_id=client_request_id, ...)
    # ... existing real-Kite body unchanged ...
```

`_use_paper_broker()` returns `True` when `KITE_MOCK_MODE`, when `access_token in (None,"","mock_token")`, or when the resolved `paper_accounts.mode == "paper"`. Default dev user (no Kite session) → always paper. **First, fix the signature** to accept `client_request_id` and `order_type` keyword args (latent `TypeError` — squareoff at `actions.py:1011` already passes both). Thread `db`/`user_id` via a contextvar set by the request/run scope (chat router and engine both have `db` + `user_id`), so `broker.py` can write rows without changing call sites.

**Fill mechanics** (`fills.py`, all friction from `trading_costs.py`):
- Synthesize spread off LTP (`market_data.get_live_quote`/`yfinance_service`): `half_spread = max(0.05, LTP*0.0003)`; `ask=LTP+half`, `bid=LTP−half`.
- **MARKET, in-hours** → fill at `ask`(buy)/`bid`(sell), then adverse slippage `×(1±SLIPPAGE_PCT)`; cost via `buy_cost`/`sell_cost`; debit/credit ledger; upsert position; status `filled`.
- **MARKET, after-hours/weekend** → `status="queued"`, fills next session open against opening quote (AMO).
- **LIMIT** → `status="resting"`; reserve `limit_price*qty + est_charges` on BUY; filled by `resting.py` when `ask≤limit`(buy)/`bid≥limit`(sell), price-improved to touch.
- **SL / SL-M** → `resting`; on LTP crossing `trigger_price`, convert to MARKET (SL-M) or resting LIMIT (SL).
- **GTT** (`place_gtt_order`) → `resting`, `order_type="GTT"`; SL+TP pair share `gtt_oco_group`; first fill cancels the sibling.
- **Idempotency:** `client_request_id` unique; on duplicate, **return the existing order's fill** (the linchpin making `max_retries=1` safe). Fill transitions are guarded by `SELECT … FOR UPDATE` (PG) / per-row status-guarded UPDATE in one transaction (SQLite) so two scheduler ticks can't double-fill.
- **Integer shares only**; sub-one-share slices → `rejected:slice_too_small` (already the engine's behavior).

Every fill also writes a `TradeLog` row (`source` carried through, `kite_order_id=None`, `status="filled"`, `average_price`, `filled_quantity`) and stores `paper_fills.trade_log_id` — so the existing `/orders/history` endpoint and audit trail keep working, closing the "workflow actions don't log TradeLog" gap noted in the research.

---

### (d) ENGINE INTEGRATION

Minimal-diff: **nothing in `actions.py`, `routers/orders.py`, or `scheduler.py` (SIP) changes**, because they all import `place_order`/`place_gtt_order`/`cancel_order`/`get_orders` from `orders.py`, which now routes to the paper broker. Concretely, per call site:

| Seam | File:line | Path through paper broker | Change required |
|---|---|---|---|
| `action.place_order` | actions.py:244 | → `orders.place_order` → paper; reads `result["average_price"]` for `executed_price` | none (broker returns `average_price`) |
| `action.allocate_notional` | actions.py (notional branch ~229) | computes `qty=int(notional//ltp)` then same `place_order` | none |
| `action.allocate_basket` | actions.py:581 | per-leg `place_order` under per-leg `client_request_id`; live short still raises | none |
| `action.set_stoploss` | actions.py:411 | → `place_gtt_order` (SELL GTT below entry) | none |
| `action.set_takeprofit` | actions.py:488 | → `place_gtt_order` (SELL GTT above entry) | none |
| `action.squareoff_all/_intraday/_symbol` | actions.py:1011 | `_place_squareoff_legs` → `place_order(..., client_request_id=, order_type=)` | **fix `place_order` signature** to accept these (latent `TypeError`) |
| `action.cancel_orders` | actions.py:312 | `get_orders`→filter→`cancel_order` | none (paper `get_orders`/`cancel_order` mirror shapes) |
| chat `/orders/confirm` | routers/orders.py:135 | → paper `place_order`; existing TradeLog write stays | none (or drop it — broker now writes TradeLog; avoid double-log by passing a flag) |
| chat `/orders/register` | routers/orders.py:252 | **keep register-not-execute**; optionally enqueue a paper order if `execute=true` | additive flag only |
| SIP job | scheduler.py:196 | → paper `place_order` (`source="sip"`) | none |

**Attribution wiring (the only substantive addition):** the broker resolves the originating idea at insert time via `ideas.resolve(ctx)` using fields already available — `tag=f"wf_{client_request_id[:16]}"` and the contextvar `(user_id, db)`; the engine additionally passes `workflow_id`/`workflow_run_id` (from `ctx.workflow.id`/`ctx.run.id`) and chat passes `conversation_id`. To avoid parsing the `wf_` tag, add an **optional** `origin: dict|None` kwarg to `place_order` that `actions.py` and the chat router populate (`{"origin_kind","workflow_id","workflow_run_id","conversation_id","strategy_id","label"}`). This is a small, backward-compatible param add — callers that omit it get `origin_kind="manual"`.

---

### (e) SCHEDULER JOBS

Two new APScheduler jobs registered in `backend/scheduler.py::_register_jobs()` (same module as the 4 existing jobs; reuse the `IST`/`CronTrigger` pattern at line 82). They call into `backend/paper/`.

| Job id | Trigger (IST) | Function | Work |
|---|---|---|---|
| `paper_drain_resting` | every 1 min, `hour="9-15", minute="15-59"`, `mon-fri` (mirrors `check_strategy_triggers`) | `resting.drain_due()` | For each `paper_orders.status IN (resting,queued)`: fetch LTP, test marketability/trigger, fill atomically, release/convert reserves, cancel OCO siblings. Skips when `is_market_open()` is False. |
| `paper_eod_snapshot` | `hour=15, minute=35, mon-fri` (just after 15:30 close) | `marks.snapshot_all()` | For each active `paper_accounts`: mark positions vs close, write `paper_nav_snapshots` (+ `nifty_close`), set `prev_close`; for each `forward_ideas`: write `paper_idea_nav_snapshots` and refresh `forward_ideas.scorecard_cache`; run `reconcile()` (Σ fills → positions/cash) to heal any orphaned reserve. |

A queued-AMO drain at `09:15` is already covered by the first job's first tick. Lazy MTM on dashboard reads (compute-on-read against latest quote) means the curve doesn't depend on the snapshot job being perfectly timely. Register with `replace_existing=True` exactly like the others; update the "Registered N scheduler jobs" log count.

---

### (f) API SURFACE

New router `backend/routers/paper.py` → `APIRouter(prefix="/paper", tags=["Paper Trading"])`, registered in `main.py` right after `orders_router` (line 78). It deliberately **mirrors the `/portfolio` response shapes** so the FE can reuse `Holding`/`PortfolioSummary` types in `lib/api.ts` (legacy base, no `/api` prefix — call via `requestLegacy`).

| Method · Path | Response shape | Reuse / notes |
|---|---|---|
| `GET /paper/account` | `{id, mode, currency, starting_capital, cash_available, cash_reserved, cash_settled, buying_power}` | new |
| `GET /paper/summary` | `PortfolioSummary` `{total_value, invested_value, total_pnl, total_pnl_pct, day_pnl, num_holdings}` | **identical to `/portfolio/summary`** → drop-in for FE |
| `GET /paper/holdings` | `Holding[]` `{tradingsymbol, exchange, quantity, average_price, last_price, pnl, day_change, day_change_percentage, sector}` | **identical to `/portfolio/holdings`**; `day_change*` now real |
| `GET /paper/positions` | `PaperPosition[]` `{id, symbol, quantity, avg_cost, last_price, unrealized_pnl, realized_pnl, day_pnl, stale, idea_id}` | richer than holdings |
| `GET /paper/orders?status=` | `PaperOrder[]` (lifecycle, resting filter) | new |
| `GET /paper/fills?since=` | `PaperFill[]` `{symbol, side, quantity, fill_price, charges, slippage_bps, realized_pnl, filled_at, idea_id}` | new |
| `GET /paper/nav-curve?period=1M..5Y` | `{period, points:[{t,v}], starting_value, ending_value, total_return, total_return_pct, nifty_points:[{t,v}]}` | **same shape as `/api/portfolio/performance`** (perf.py) + benchmark series; recharts-ready |
| `GET /paper/ideas` | `ForwardIdea[]` `{id, label, origin_kind, status, inception_date, scorecard:{cum_return, cagr, sharpe, sortino, alpha, info_ratio, max_dd, win_rate}}` | reads `scorecard_cache`; cohort list |
| `GET /paper/ideas/{id}/scorecard` | full scorecard computed-on-read + `degradation:{backtest:{…from dsl_backtest_runs}, forward:{…}, decay:{…}}` + `psr`, `min_trl`, `deflated_sharpe`, `methodology_note(...)` | reuses `backtest_metrics.*` + `trading_costs.round_trip_bps()` |
| `POST /paper/account/reset` | reseed to `starting_capital`, wipe positions/orders/fills (idea history optional) | dev convenience |

All ratio endpoints call `sharpe_sortino`/`calendar_cagr_pct`/`methodology_note` so paper numbers are **identical-by-construction** to backtest numbers. FE: add a `paper-trading` `TabKey` in `AppShell.tsx` and a `PaperTradingTab.tsx` reusing `recharts` (2.15.3) and the Quartr tokens; types parallel the existing `Holding`/`PortfolioSummary` in `lib/api.ts`.

---

### (g) RISKS, EDGE CASES, TEST PLAN

**Risks / edge cases (and the guard):**
- **Latent `TypeError` (must fix first):** `place_order` lacks `client_request_id`/`order_type` kwargs that squareoff already passes (`actions.py:1011`) — squareoff currently raises whenever reached. Fix the signature in the same PR as the shim.
- **Double-fill on scheduler retry / restart:** derive positions/cash from immutable `paper_fills`; unique `client_request_id`; status-guarded atomic transition (`SELECT … FOR UPDATE` PG / guarded UPDATE SQLite); `reconcile()` on each EOD tick heals orphaned reserves (NautilusTrader failure mode).
- **Double cash-spend across resting BUYs:** `cash_reserved` held at placement; `buying_power = cash_available − cash_reserved`; reservation released on fill/cancel.
- **Double TradeLog rows:** broker now writes TradeLog; the chat `/orders/confirm` path also writes one — pass a `skip_trade_log` flag (or move the write entirely into the broker) to avoid duplicates.
- **Stale / market-closed pricing:** if quote age > N min or market shut → mark vs last close, set `stale=True`; never fabricate a moving tick. NAV snapshot flags `is_stale`.
- **T+1 settlement display:** CNC sell proceeds immediately reusable (`cash_available`), formally `settles_at=T+1` on the fill; EOD roll moves unsettled → `cash_settled`.
- **Look-ahead in forward scores:** snapshot `intended_price`/`intended_quote_at` and `nifty_close` **at decision/close**, never recomputed from adjusted history — otherwise slippage and alpha are fictional.
- **Out of scope v1 (document, don't silently break):** splits/dividends (avg_cost drifts post-split), live shorts (research backtester only), partial fills (flag `PAPER_PARTIAL_FILLS`, off by default).
- **Selection bias:** `forward_ideas.cohort_trial_count` feeds Deflated-Sharpe before any promote.

**Test plan (pytest, cross-dialect — SQLite in-memory like existing tests; mirror the dialect-branch checks in 0011's test):**
1. **Migrations:** `alembic upgrade head` then `downgrade -3` on both SQLite and a PG test URL; assert tables/enums/indexes create and drop cleanly; assert the `trade_logs.idea_id` add-column is reversible.
2. **Broker drop-in parity:** call `orders.place_order(...)` with a `mock_token` → asserts a `paper_orders`+`paper_fills`+`TradeLog` row, correct `cash_available` debit = `buy_cost(...)`, position upsert; identical signature exercised by a direct `actions.execute_action_place_order` run.
3. **Idempotency:** same `client_request_id` twice → one fill, second returns existing (assert no second debit). Simulate two concurrent `drain_due()` on one resting order → exactly one fill.
4. **Cost parity:** assert `paper_fills.charges == buy_cost(price,qty)[1]` and round-trip ≈ `round_trip_bps()` — guards live↔backtest parity.
5. **Resting fills:** place LIMIT below market → `resting`; feed a crossing LTP → fills at touch; reserve released. GTT OCO pair → one fills, sibling auto-cancelled.
6. **Reserve/buying-power:** two resting BUYs that together exceed cash → second `rejected:insufficient_buying_power`.
7. **Settlement:** SELL → `cash_available` up immediately, `settles_at=T+1`; EOD roll moves it to `cash_settled`.
8. **MTM/NAV:** seed fills, run `snapshot_all()`, assert `paper_nav_snapshots.nav == cash + Σ qty·LTP`; stale quote → `is_stale=True`, marks vs close.
9. **Attribution + scorecard:** workflow run → fills carry `workflow_id`/`workflow_run_id`/`idea_id`; `forward_ideas` row created once (dedup); `GET /paper/ideas/{id}/scorecard` returns Sharpe via `sharpe_sortino` and a degradation panel joined to a seeded `dsl_backtest_runs` row; assert `methodology_note` strings present.
10. **Squareoff regression:** end-to-end `execute_action_squareoff_all` no longer raises `TypeError` and books a SELL fill + realized P&L.

---

**Build order for an engineer:** (1) fix `place_order` signature + shim in `orders.py`; (2) migration 0013 + `paper/accounts.py`,`fills.py`,`broker.py`; (3) route chat+actions through it (no diffs beyond the signature) and verify squareoff; (4) migration 0014/0015 + `ideas.py`,`marks.py`,`resting.py` + 2 scheduler jobs; (5) `routers/paper.py` reusing `/portfolio` shapes + `backtest_metrics`; (6) FE `PaperTradingTab`.

**Verified anchors (absolute):** broker seam `/Users/karanveersingh/Downloads/Second_Star/pivot/backend/kite/orders.py` (signature ends at `variety` — no `client_request_id`, confirming the latent bug at `actions.py:1011`); action call sites `/Users/karanveersingh/Downloads/Second_Star/pivot/backend/workflows/steps/actions.py` (place_order:244, set_stoploss:411, set_takeprofit:488, allocate_basket:581, squareoff legs:1011); chat writes `/Users/karanveersingh/Downloads/Second_Star/pivot/backend/routers/orders.py` (confirm:135/source="chat":157, register:252/source="chat-confirm":245); SIP `/Users/karanveersingh/Downloads/Second_Star/pivot/backend/scheduler.py` (`_register_jobs`:79, place_order:196); costs `/Users/karanveersingh/Downloads/Second_Star/pivot/backend/services/trading_costs.py` (`buy_cost`/`sell_cost`/`slippage_bps`/`round_trip_bps`); metrics `/Users/karanveersingh/Downloads/Second_Star/pivot/backend/services/backtest_metrics.py`; migration template `/Users/karanveersingh/Downloads/Second_Star/pivot/migrations/versions/0011_dsl_backtest_runs.py` (head=`0012_workflow_expires_at`); router order `/Users/karanveersingh/Downloads/Second_Star/pivot/backend/main.py:77-116`; model conventions `/Users/karanveersingh/Downloads/Second_Star/pivot/backend/models.py` (`_uuid_str`:26, `TradeLog`:196).


---

# Frontend Dashboard Design (raw)

I now have all the patterns confirmed: `ApiResult<T>`, `request`/`requestLegacy`, `query` param, `isError`, the inline-style Quartr-token convention, the `useLiveQuote` hook, `recharts` available, and the en-IN formatters. Here is the deliverable.

---

## 7. Frontend Dashboard Design (Quartr-themed)

This is a build-ready FE design for the Paper-Trading surface in `pivot-next/`. It mirrors the verified codebase conventions: inline-style components keyed to Quartr CSS tokens (as in `components/agent-panel/PortfolioTab.tsx`), the `ApiResult<T>` + `request`/`requestLegacy` fetch pattern in `lib/api.ts`, the `useLiveQuote` hook over the `liveQuoteManager` WS singleton, `recharts@2.15.3` for charts, and `lucide-react` icons. **Every color is a token — never a hardcoded hex (the one carve-out is recharts `<stop>`/gradient fills where a token cannot resolve to a `stop-color`; see §(c)).**

---

### (a) IA / Navigation & Routing

**Tab slot.** Add one top-level tab to `AppShell.tsx` following the verified pattern (`AppShell.tsx` §E.1). The Paper-Trading surface is its own destination, distinct from the existing Kite-mock `portfolio` tab (which stays for real-broker holdings).

```ts
// AppShell.tsx — TabKey union + NAV_ITEMS
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
// AppShell.tsx — render slot (mirrors line ~513)
{active === "paper" && <PaperDashboard />}
```

Routing reuses the existing **hash router** (`readHashTab()` reads `window.location.hash`, `AppShell.tsx` §E.1) — no Next route segment is added, consistent with every other tab. The deep-link is `#paper`.

**Sub-views.** Inside `PaperDashboard` use the vendored shadcn `Tabs` (`components/ui/tabs.tsx`) as a sub-router, with its active value mirrored into a **second hash segment** so sub-views are deep-linkable (`#paper/positions`, `#paper/ideas`). Five sub-views, in priority order:

| Sub-view value | Label | Purpose | Primary endpoint(s) |
|---|---|---|---|
| `overview` | Overview | KPI strip + equity/NAV curve + drawdown + allocation + top positions + open orders | `/paper/summary`, `/paper/equity-curve`, `/paper/positions`, `/paper/orders/open` |
| `positions` | Positions | Full holdings table w/ source attribution + close/SL/TP actions | `/paper/positions` |
| `orders` | Orders | Open-orders blotter (resting LIMIT/GTT/SL/TP) + cancel | `/paper/orders/open` |
| `journal` | Journal | Filled-trade journal grouped by day, fee/slippage breakdown | `/paper/fills` |
| `ideas` | Forward-Test | Per-idea scorecards + backtest-vs-live degradation drill-in | `/paper/ideas`, `/paper/ideas/{id}` |

```tsx
function useHashSubview(): [SubView, (v: SubView) => void] {
  // reads "#paper/<sub>"; defaults to "overview"; writes back on change.
  // Pattern copied from AppShell.readHashTab, scoped to the 2nd segment.
}
```

Row clicks in any table route to the existing `StockDetailPage` via `next/link` to `/stock/<symbol>` (same as `PortfolioTab` line 767). The **idea drill-in** (`#paper/ideas` → click a scorecard) opens an in-page detail panel, not a route change, to keep the cohort context.

---

### (b) Component Tree

All files under `pivot-next/components/paper/`. All are `"use client"` (they read live quotes / animate). Each leaf takes already-fetched data as props; **only `PaperDashboard` fetches** (single-owner pattern, like `PortfolioTab`'s `FetchState`). Shared loading/empty/error primitives live in `paper/_shared.tsx`.

```
components/paper/
├── PaperDashboard.tsx        ← owner: fetch + sub-tab router + poll loop
├── _shared.tsx               ← Card, Section, PaperEmpty, PaperError, LivePulse, NumberTicker, DeltaPill, SourceChip
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

For each component: **props · data source · shadcn primitive + Quartr tokens · loading/empty/error**.

---

**`PaperDashboard.tsx`** — surface owner.
- **Props:** none.
- **Data:** owns a `FetchState` discriminated union (`loading | error | ok`) exactly like `PortfolioTab` (line 129). On mount and on a 15 s poll (see §e) it `Promise.all`s `getPaperSummary()`, `getPaperEquityCurve(range)`, `getPaperPositions()`, `getPaperOpenOrders()`. The `ideas`/`journal` sub-views lazy-fetch on first activation.
- **shadcn/tokens:** outer `<div style={{ background: "var(--bg-base)" }}>`; serif `<h1 className="q-serif">Paper Trading</h1>` (matches `PortfolioTab` line 157); shadcn `Tabs`/`TabsList`/`TabsTrigger` styled to the pill-slide pattern (active `--bg-elevated` + `--glass-border-hover` + `--text-primary`, inactive `--text-secondary`).
- **States:** delegates to `PaperLoading` (skeleton tree shaped like the real modules, per `PortfolioLoading` line 1252), `PaperError` (role="alert" card + Retry, per line 170), and per-module `PaperEmpty`.

**`KpiStatCards.tsx`** — Tier-1 #1.
- **Props:** `{ summary: PaperSummary; loading?: boolean }`.
- **Data:** `GET /paper/summary` → `nav`, `total_pnl`/`total_pnl_pct`, `day_pnl`/`day_pnl_pct`, `realized_pnl`, `unrealized_pnl`, `buying_power`, `win_rate`, and a `spark` series per card.
- **shadcn/tokens:** horizontally-scrolling strip of `Card`/`CardContent` (6 cards), mirroring `DashboardTab`'s index strip. Each card = `.q-uppercase-label` label on `--metric-label`; value `.q-display .tabular-nums` `--font-mono` `--text-primary`; `DeltaPill` colored `--color-profit`/`--color-loss`, pill bg `color-mix(in srgb, var(--color-profit) 12%, transparent)`; 40 px recharts `<Sparkline>`. Card = `--bg-card` / `1px var(--glass-border)` / `--radius-lg`; **Day-P&L is the hero** → `--bg-elevated` on hover + `--shadow-cta`. `Tooltip` (`components/ui/tooltip.tsx`) on buying-power: "cash − reserved margin on open orders". `NumberTicker` animates on each poll, eased `--ease-quartr`, width reserved with `.tabular-nums`.
- **States:** loading → 6 `Skeleton` cards (`--bg-secondary`); empty (no account) → single CTA card "Register your first idea in chat" (`--font-serif` one-liner + action pill).

**`EquityCurveChart.tsx`** — Tier-1 #2.
- **Props:** `{ range: PaperRange; onRangeChange: (r: PaperRange) => void; points: NavPoint[]; loading?: boolean }`.
- **Data:** `GET /paper/equity-curve?range=1D|1W|1M|3M|1Y|ALL` → `{ points: [{ t, nav, benchmark, cash, invested, day_pnl }], start_nav }`. Benchmark = NIFTY normalized to the same start (computed backend).
- **shadcn/tokens:** recharts `ResponsiveContainer` + `AreaChart`. NAV line stroke `--price-line`, area gradient from `--pivot-blue` → transparent (recharts `<linearGradient>`). Benchmark `<Line>` dashed, stroke `--text-tertiary`. Crosshair = recharts `<Tooltip>` with `contentStyle={{ background: "var(--bg-card)", border: "1px solid var(--glass-border)", borderRadius: "var(--radius-md)" }}`. Range selector = shadcn-styled segmented pills reusing the exact `RANGES`-pill markup from `PortfolioTab` (lines 396–431): active `background: var(--text-primary)` / `color: var(--bg-primary)`, inactive `--text-secondary`, container `--radius-pill`, transitions `--ease-quartr`. Axis labels `--font-mono .tabular-nums` `--text-tertiary`; gridlines `var(--glass-border)`.
- **States:** loading → chart-shaped `Skeleton` (height 240, per `PortfolioLoading` line 1255); empty → serif "No NAV history yet — your equity curve appears after the first mark."; error → inline `PaperError`.

**`DrawdownChart.tsx`** — Tier-2 #8.
- **Props:** `{ points: NavPoint[]; topDrawdowns: Drawdown[] }` (derived client-side from the **same** `points` the equity curve uses — running peak → `(nav−peak)/peak`; or read precomputed `/paper/drawdowns`).
- **shadcn/tokens:** underwater recharts `AreaChart` (always ≤ 0), fill `--color-loss` @ ~14% alpha, line `--color-loss`, zero baseline `var(--glass-border)`. Shares the equity-curve range. Current-DD readout pinned top-right `--font-mono`. Companion top-5 `Table` (depth cells alpha-ramp of `--color-loss`).
- **States:** empty when < 2 NAV points → hidden (no card).

**`HoldingsTable.tsx`** — Tier-1 #3 (the orders↔portfolio linchpin).
- **Props:** `{ positions: PaperPosition[] }`.
- **Data:** `GET /paper/positions` → each row: `symbol, qty, avg_cost, ltp, mkt_value, unrealized_pnl, unrealized_pnl_pct, day_change_pct, sector, source` where `source = { origin_kind, idea_id, idea_label }` (joined from `TradeLog.source/source_id`). LTP overridden live via `useLiveQuote(symbol)` per row, exactly as `HoldingRow` (line 748).
- **shadcn/tokens:** sortable `<table>` reusing `PortfolioTab.HoldingsTable` markup (sticky header `.q-uppercase-label`/`--metric-label`, `--glass-border` bottom). P&L cells `--color-profit`/`--color-loss`; P&L% gets a faint inline heat tint at low alpha. Per-row `Sparkline`. **Sector chip** + **Source chip** = `Badge` (`components/ui/badge.tsx`) on `--bg-secondary`/`--text-secondary`/`--radius-pill`; source chip `Tooltip` shows provenance ("Workflow: RBI rate-cut · run #1287"). Shorts (`qty<0`) get a `--color-loss` left border. Row hover `--bg-secondary` (per line 761); row → `next/link` `/stock/<symbol>`. Per-row `DropdownMenu` (`components/ui/dropdown-menu.tsx`): Close position / Set SL / Set TP → POST to existing actions; **Cancel/close confirmation via `AlertDialog`**.
- **States:** empty → `PaperEmpty` Wallet icon + "No open positions — registered ideas fill here" (per line 221).

**`OpenOrdersBlotter.tsx`** — Tier-1 #4.
- **Props:** `{ orders: PaperOpenOrder[]; onCancel: (id) => Promise<void> }`.
- **Data:** `GET /paper/orders/open` → `TradeLog` where `status IN (registered, pending, trigger_pending)` + GTT legs. Fields: `symbol, side, type (LIMIT|GTT|SL|TP), qty, limit_price, trigger_price, distance_pct, age, source`.
- **shadcn/tokens:** `Table` with a status dot (`registered`→`--info`, `pending`→`--warning`, near-trigger→pulsing `--color-warn`), a thin distance-to-trigger gauge (fill `--pivot-blue`), an age timer, side text buy `--color-profit`/sell `--color-loss`. Cancel = ghost `Button` (hover `--color-loss` border) gated by `AlertDialog`. Type chip = `Badge`.
- **States:** empty → "No resting orders."; cancel-in-flight → button `Skeleton`/disabled.

**`TradeJournal.tsx`** — Tier-1 #5.
- **Props:** `{ filters: JournalFilters; onFilter }`.
- **Data:** `GET /paper/fills?symbol=&from=&to=&source=` → filled `TradeLog` rows with `average_price`, cost components (`brokerage/stt/slippage` from `trading_costs.py`), `realized_pnl` on closing lots, `source`.
- **shadcn/tokens:** day-grouped `<table>` with sticky day headers (`.q-uppercase-label`, `--bg-secondary`) + day-subtotal rows; expandable cost panel via shadcn `Collapsible` (or `accordion.tsx`) on `--bg-elevated`/`--radius-md`, fees `--text-tertiary`. Filters: `Select` (source) + `Popover`-less date inputs (no calendar primitive vendored — use native `<input type="date">` styled to tokens, or reuse `CalendarTab` cell logic). Buy/sell side icon tinted profit/loss; realized P&L `--font-mono` colored.
- **States:** empty → "No fills yet."; paginated (cursor) with a "Load more" ghost `Button`.

**`AllocationDonut.tsx`** — Tier-2 #6.
- **Props:** `{ positions: PaperPosition[]; cash: number }`.
- **Data:** Σ `mkt_value` grouped by sector and by idea + a cash slice (computed client-side from `positions`, same as `PortfolioTab.aggregate` line 892).
- **shadcn/tokens:** shadcn `Tabs` to toggle **Sector / By Idea**. Reuse `PortfolioTab`'s SVG donut + `arcPath` (line 908) — already token-themed, hover lifts + dims siblings (`--ease-quartr`), center label `.q-display` shows total NAV, legend `--font-mono .tabular-nums`. Categorical palette built from `--pivot-blue, --info, --color-warn, --success` + `--text-tertiary` for cash (the desaturated brand set; **not** the rainbow). Over-concentration (>X%) flag `--color-warn`.
- **States:** empty → "No allocation data" (per line 990).

**`IdeaScorecards.tsx`** — Tier-2 #7 (the differentiator).
- **Props:** `{ ideas: IdeaScorecard[]; sort; onSort; onOpen: (id) => void }`.
- **Data:** `GET /paper/ideas` → per `ForwardIdea`: `id, label, origin_kind, status, live_return_pct, realized_pnl, unrealized_pnl, win_rate, trades, sharpe, max_dd_pct, vs_backtest_delta_pp, spark[]`.
- **shadcn/tokens:** responsive grid of `Card`s. Headline return `--font-mono` colored profit/loss; mini `<Sparkline>` stroke by sign. **"Beating backtest"** chip = `Badge` `color-mix(... var(--color-profit) 12% ...)`; **"lagging"** = loss-tint. Stat-row labels `--metric-label`. Lifecycle pill (`paper`→`--info`, `candidate`→`--warning`, `promoted`→`--color-profit`, `retired`→`--text-disabled`). Winner card → `--glass-border-hover` + `--shadow-cta`. `HoverCard` (if vendored; else `Tooltip`) carries the reused `methodology_note`. Sortable by live return.
- **States:** empty → serif "No forward-tested ideas yet — register a workflow or chat idea to start scoring it."

**`IdeaDetailPanel.tsx`** — drill-in.
- **Props:** `{ ideaId: string; onClose }`.
- **Data:** `GET /paper/ideas/{id}` → per-idea NAV series + the **backtest-vs-forward degradation table** (`backtest` column from `dsl_backtest_runs`, `forward` from the live series; CAGR/Sharpe/MaxDD/WinRate/Slippage + decay). Plus the OOS verdict (`healthy | decayed | execution-problem`) and PSR/MinTRL flag.
- **shadcn/tokens:** opens as a shadcn `Sheet` (`components/ui/sheet.tsx`) from the right. Side-by-side `Table` with a `Decay` column colored by sign; verdict `Badge`; a per-idea `EquityCurveChart` reused with the idea's slice. Degradation rows where slippage-gap dominates get an `--info` "execution, not signal" annotation.
- **States:** loading → `Skeleton` table; error → inline retry.

---

### (c) Charts

**Library: reuse `recharts@2.15.3`** (already in `package.json`, already wired for portfolio perf — research §5/§F). No new dependency. lightweight-charts is **not** added — recharts SVG covers every Paper surface (area equity curve, underwater drawdown, donut, diverging bars, sparklines) and themes by accepting `var(--token)` directly in `stroke`/`fill`, which is exactly the existing pattern. (The hand-rolled SVG donut/line in `PortfolioTab` is also fine to reuse where recharts is overkill — e.g. the donut.)

**Theming rules:**

1. **Strokes & text → CSS vars directly.** recharts SVG accepts `stroke="var(--price-line)"`, `stroke="var(--color-profit)"`, axis `stroke="var(--text-tertiary)"`, grid `stroke="var(--glass-border)"`. These re-resolve automatically on `.dark` toggle — **zero glue**, because they're live CSS custom properties on the SVG.
2. **Gradient `<stop>` fills are the one exception.** SVG `stop-color` does not reliably inherit a CSS var across all engines, so for the area fill read the token once at runtime and pass the resolved value:

```ts
// paper/_shared.ts
export function readToken(name: string): string {
  if (typeof window === "undefined") return "";
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}
// re-read on theme change: a MutationObserver on document.documentElement `class`
// bumps a state counter so charts re-render with the dark-mode token values.
export function useThemeTick(): number { /* observes .dark toggle → returns tick */ }
```
Equity fill gradient = `readToken("--pivot-blue")` → transparent; up/down line picks `--color-profit`/`--color-loss` by sign (same logic as `PortfolioTab` line 497).
3. **Profit/loss are absolute semantics** — `--color-profit` for gains, `--color-loss` for losses, `--color-warn` for warnings, `--text-secondary` for neutral/zero. Dark mode's brighter `#10b981/#ef4444` apply automatically through the tokens.
4. **Tooltip chrome:** `contentStyle={{ background: "var(--bg-card)", border: "1px solid var(--glass-border)", borderRadius: "var(--radius-md)", fontFamily: "var(--font-mono)" }}` (matches research §F snippet).
5. **`<Sparkline>` helper:** one shared `recharts` mini `AreaChart`, no axes/grid/tooltip, `isAnimationActive={false}`, stroke = sign color, 40 px tall — used in KPI cards, position rows, watchlist, scorecards.

**Centralize** a `getChartTheme(themeTick)` in `paper/_shared.ts` returning `{ navLine, areaTop, benchmark, profit, loss, grid, axis, crosshair }` (reading `--price-line, --pivot-blue, --text-tertiary, --color-profit, --color-loss, --glass-border, --text-tertiary, --glass-border-focus`), consumed by every chart so dark/light stays consistent.

---

### (d) Text Wireframes (ASCII)

**Overview page (`#paper/overview`)**

```
┌──────────────────────────────────────────────────────────────────────────────┐
│  Paper Trading                                                  ● live · 15:42 │  q-serif h1 + LivePulse
│  ┌Overview┐ Positions  Orders  Journal  Forward-Test            (sub-tabs)     │  shadcn Tabs, pill-slide
├──────────────────────────────────────────────────────────────────────────────┤
│  KPI STRIP  (scroll-x, 6 cards; Day-P&L is hero/elevated)                      │
│  ┌─────────┐┌─────────┐┌═════════┐┌─────────┐┌─────────┐┌─────────┐           │
│  │ NAV     ││ TOTAL   ││ DAY P&L ││ REALIZED││ BUYING  ││ WIN RATE│           │
│  │₹8,42,150││ +12.4%  ││ +₹3,210 ││ +₹18,4k ││ ₹1,20k  ││  58%    │           │
│  │ ╱╲╱‾╲   ││ ▲ +0.4% ││ ▲ +0.38%││ ▲       ││  ⌁ tip  ││ 23 lots │           │  q-display mono + DeltaPill + spark
│  └─────────┘└─────────┘└═════════┘└─────────┘└─────────┘└─────────┘           │
├──────────────────────────────────────────────────────────────────────────────┤
│  EQUITY / NAV vs NIFTY 50                       [1D][1W][1M][3M][1Y][ALL]      │  range pills (--text-primary active)
│  ₹                                                                  ╱‾‾        │
│  8.4L │                                             ╱‾╲___╱‾‾‾‾‾╲╱            │  NAV  = --price-line, area --pivot-blue
│  8.0L │                        ____╱‾‾╲___╱‾‾‾‾╲___╱                          │  NIFTY= --text-tertiary dashed
│  7.6L │ ___╱‾‾‾╲____╱‾‾‾‾                                                     │
│       └──────────────────────────────────────────────────────────────────    │
│        Jan'26   Feb'26   Mar'26   Apr'26   May'26                              │
│     vs NIFTY 50 ·  portfolio +12.4%   benchmark +6.1%   alpha +6.3%           │  mono, profit/loss colored
├───────────────────────────────────────┬──────────────────────────────────────┤
│  DRAWDOWN (underwater)   curr −3.2%    │  ALLOCATION   ┌Sector┐ By Idea        │
│   0 ─────────────────────────────────  │        ╭───────────╮                  │
│      ▼▼▼          ▼▼▼▼▼                 │       │   ₹8.42L   │  ■ Banking 28%   │  donut center=NAV (q-display)
│    −8% (--color-loss @14% fill)        │       │    NAV     │  ■ IT      22%   │  legend mono .tabular-nums
│  Top DD: −8.1% · 12d · recovered       │        ╰───────────╯  ■ Cash    14%   │
├───────────────────────────────────────┴──────────────────────────────────────┤
│  TOP POSITIONS (5)                                          [View all →]       │
│  SYMBOL    QTY   AVG     LTP●   UNREAL P&L   DAY    SECTOR    SOURCE            │  uppercase-label header
│  RELIANCE   40  2,840  2,910●  +₹2,800 +2.4% +0.6% [Energy]  [WF: RBI-cut]     │  P&L colored, chips=Badge
│  TCS        15  3,610  3,702●  +₹1,380 +2.5% −0.2% [IT]      [Chat: dip-buy]   │  ● live dot (useLiveQuote)
├──────────────────────────────────────────────────────────────────────────────┤
│  OPEN ORDERS (resting)                                                         │
│  ● SYMBOL   SIDE  TYPE  QTY  TRIGGER   DIST   AGE     SOURCE        [Cancel]    │
│  ◐ HDFCBANK BUY   LIMIT  20  1,640    −1.2%▕▏ 2h 14m  [WF: dip]     [ Cancel ]  │  dot=--warning, gauge=--pivot-blue
└──────────────────────────────────────────────────────────────────────────────┘
```

**Forward-Test / Ideas page (`#paper/ideas`)**

```
┌──────────────────────────────────────────────────────────────────────────────┐
│  Paper Trading ›  Forward-Test            Sort: [Live return ▾]  Cohort: [All ▾]│
├──────────────────────────────────────────────────────────────────────────────┤
│  ┌══════════════════════┐ ┌──────────────────────┐ ┌──────────────────────┐   │
│  │ RBI rate-cut buy   ★ │ │ EMA200 dip-buy       │ │ Gold SIP             │   │  winner ★ = --glass-border-hover
│  │ [workflow] [promoted]│ │ [chat] [candidate]   │ │ [strategy] [paper]   │   │  origin + lifecycle Badges
│  │  +18.6%  since 12 Mar│ │  +4.1%   since 28 Apr│ │  +1.2%  since 02 May │   │  headline mono, profit color
│  │  ╱‾╲___╱‾‾‾‾╲╱‾‾     │ │  ___╱‾╲___           │ │  ‾‾╲___              │   │  sparkline by sign
│  │  Win 61% · 18 trades │ │  Win 50% · 6 trades  │ │  Win 100% · 2 trades │   │  --metric-label stats
│  │  Sharpe 1.2 · DD −6% │ │  Sharpe 0.4 · DD −9% │ │  Sharpe — · DD −1%   │   │
│  │  ▲ Beating backtest  │ │  ▼ Lagging −1.1 Shp  │ │  ⌁ Too few trades    │   │  delta chip profit/loss/warn
│  └══════════════════════┘ └──────────────────────┘ └──────────────────────┘   │
│                                                                                │
│  ── Drill-in (Sheet from right): RBI rate-cut buy ───────────────────────────  │
│        | metric    | Backtest (IS) | Forward (OOS) | Decay   |  verdict        │
│        | CAGR      |     31%       |     14%       | −17pp   |  ┌───────────┐   │
│        | Sharpe    |     1.8       |     1.2       | −0.6    |  │  HEALTHY  │   │  verdict Badge
│        | Max DD    |     −9%       |     −6%       | better  |  └───────────┘   │  (--color-profit)
│        | Win rate  |     58%       |     61%       | +3pp    |  PSR 0.91·MinTRL │
│        | Slippage  |  ~8 bps (assm)|   11 bps      | +3 bps  |  within band     │
└──────────────────────────────────────────────────────────────────────────────┘
```

---

### (e) `lib/api.ts` Additions + Live Updates

Append to `lib/api.ts`, mirroring the existing portfolio block (lines 483–510) — `request` for `/api/*` routes, `requestLegacy` for non-`/api` legacy routes. New paper routes live under `/paper/*` (legacy, alongside `/portfolio/*` and `/orders/*`), so all use `requestLegacy`.

```ts
// ── Paper-trading types ──────────────────────────────────────────────────────
export type PaperRange = "1D" | "1W" | "1M" | "3M" | "1Y" | "ALL";

export type SparkPoint = { t: string; v: number };

export type PaperSummary = {
  nav: number;
  cash: number;
  invested: number;
  buying_power: number;
  total_pnl: number;
  total_pnl_pct: number;
  day_pnl: number;
  day_pnl_pct: number;
  realized_pnl: number;
  unrealized_pnl: number;
  win_rate: number;            // 0..1
  nav_spark: SparkPoint[];     // intraday marks for the hero sparkline
  as_of: string;               // ISO ts of last mark (drives "as of HH:MM")
  is_live: boolean;            // mark-to-market loop active (market hours)
};

export type IdeaSource = {
  origin_kind: "workflow" | "chat" | "strategy" | "manual";
  idea_id: string | null;
  idea_label: string | null;
};

export type PaperPosition = {
  id: string;
  symbol: string;
  exchange: string;
  quantity: number;            // negative = short
  avg_cost: number;
  ltp: number;
  mkt_value: number;
  unrealized_pnl: number;
  unrealized_pnl_pct: number;
  day_change_pct: number;
  sector: string | null;
  source: IdeaSource;
  spark: SparkPoint[];
};

export type NavPoint = {
  t: string;
  nav: number;
  benchmark: number;           // NIFTY normalized to start_nav
  cash: number;
  invested: number;
  day_pnl: number;
};
export type PaperEquityCurve = {
  range: PaperRange;
  start_nav: number;
  points: NavPoint[];
};

export type PaperOpenOrder = {
  id: number;
  symbol: string;
  side: "BUY" | "SELL";
  order_type: "LIMIT" | "GTT" | "SL" | "TP";
  quantity: number;
  limit_price: number | null;
  trigger_price: number | null;
  distance_pct: number | null; // signed % from LTP to trigger
  status: "registered" | "pending" | "trigger_pending";
  age_seconds: number;
  source: IdeaSource;
};

export type PaperFill = {
  id: number;
  ts: string;
  symbol: string;
  side: "BUY" | "SELL";
  quantity: number;
  average_price: number;
  value: number;
  brokerage: number;
  stt: number;
  slippage: number;
  realized_pnl: number | null; // on closing lots
  source: IdeaSource;
};
export type PaperFillsPage = { fills: PaperFill[]; next_cursor: string | null };

export type IdeaScorecard = {
  id: string;
  label: string;
  origin_kind: IdeaSource["origin_kind"];
  status: "paper" | "candidate" | "promoted" | "retired";
  inception_date: string;
  live_return_pct: number;
  realized_pnl: number;
  unrealized_pnl: number;
  win_rate: number;
  trades: number;
  sharpe: number | null;
  max_dd_pct: number;
  vs_backtest_sharpe_delta: number | null;  // forward − backtest
  verdict: "healthy" | "decayed" | "execution-problem" | "insufficient-data";
  spark: SparkPoint[];
};

export type IdeaDegradationRow = {
  metric: string;              // "CAGR" | "Sharpe" | ...
  backtest: number | null;
  forward: number | null;
  decay_label: string;         // "−17pp" | "better" | ...
};
export type IdeaDetail = {
  scorecard: IdeaScorecard;
  nav_points: NavPoint[];
  degradation: IdeaDegradationRow[];
  psr: number | null;
  min_trl_met: boolean;
  methodology_note: string;    // reuse backtest_metrics.methodology_note
};

// ── Fetchers (mirror getPortfolioSummary / getPortfolioHoldings) ─────────────
export function getPaperSummary(): Promise<ApiResult<PaperSummary>> {
  return requestLegacy<PaperSummary>("/paper/summary");
}
export function getPaperPositions(): Promise<ApiResult<PaperPosition[]>> {
  return requestLegacy<PaperPosition[]>("/paper/positions");
}
export function getPaperEquityCurve(range: PaperRange): Promise<ApiResult<PaperEquityCurve>> {
  return requestLegacy<PaperEquityCurve>("/paper/equity-curve", { query: { range } });
}
export function getPaperOpenOrders(): Promise<ApiResult<PaperOpenOrder[]>> {
  return requestLegacy<PaperOpenOrder[]>("/paper/orders/open");
}
export function cancelPaperOrder(id: number): Promise<ApiResult<{ id: number; status: "cancelled" }>> {
  return requestLegacy(`/paper/orders/${id}/cancel`, { method: "POST" });
}
export function getPaperFills(params: {
  symbol?: string; from?: string; to?: string; source?: string; cursor?: string;
}): Promise<ApiResult<PaperFillsPage>> {
  return requestLegacy<PaperFillsPage>("/paper/fills", { query: params });
}
export function getPaperIdeas(sort = "live_return"): Promise<ApiResult<IdeaScorecard[]>> {
  return requestLegacy<IdeaScorecard[]>("/paper/ideas", { query: { sort } });
}
export function getPaperIdeaDetail(id: string): Promise<ApiResult<IdeaDetail>> {
  return requestLegacy<IdeaDetail>(`/paper/ideas/${id}`);
}
```

Consume with the verified guard: `const r = await getPaperSummary(); if (!isError(r)) setSummary(r.data);` (`isError` from `@/lib/types`).

**Live updates — hybrid, reusing existing plumbing:**
- **Per-row LTP is already live** via the **WS** path: `HoldingsTable` / `OpenOrdersBlotter` rows call `useLiveQuote(symbol)` (`hooks/useLiveQuote.ts`), which rides the shared `/api/ws/quotes` `liveQuoteManager` singleton (`lib/liveQuoteManager.ts`) — no new WS. The green dot = `liveQuote.isLive` (per `HoldingRow` line 819).
- **Account-level NAV / P&L / equity curve poll** because they're derived server-side by the scheduler mark-to-market loop, not pushed per-symbol. `PaperDashboard` runs a **15 s `setInterval`** (cleared on unmount, paused when `document.hidden`) that re-`Promise.all`s `getPaperSummary` + `getPaperEquityCurve` + `getPaperPositions` + `getPaperOpenOrders`. This matches the brief's "mark-to-market loop" cadence and the existing 30 s portfolio cache TTL. KPI values feed `NumberTicker` so each poll animates rather than snaps.
- **No new WS endpoint.** A future server-pushed NAV channel could slot in behind the same `liveQuoteManager` pattern, but polling is correct for v1.

---

### (f) Accessibility, Number Formatting & Motion

**Number formatting (en-IN, tabular-nums).** Reuse the existing formatters — do **not** reinvent: `fmtRupee(n, { sign, max })`, `fmtPct(n, signed)`, `fmtINR(v)` (Cr/L/k compaction) from `PortfolioTab` (lines 102–119), and the `Intl.NumberFormat("en-IN", { style:"currency", currency:"INR" })` instance pattern from `AppShell.tsx` line 173 / `DashboardTab.tsx` line 179. Lift these into `paper/_shared.ts` so all paper modules share one source.
- Every numeric cell carries `.tabular-nums` + `--font-mono` so digits don't jitter on tick (matches `StockDetailPage` usage).
- All money/percent columns **right-aligned**; reserve width with `.tabular-nums` so `NumberTicker` count-up never reflows layout.
- Lakh/crore grouping via `toLocaleString("en-IN")`; minus rendered as the figure-dash `−` (U+2212), as `fmtRupee` already does (line 112).

**Accessibility.**
- Error cards `role="alert"` (per `PortfolioTab` line 172); the live-pulse dot has `aria-label="Live price"`/`"Delayed price"` (per line 820) and a tooltip.
- Sortable headers are `<th scope="col">` buttons with `aria-sort` reflecting `asc|desc|none`; sort icons `aria-hidden`.
- Color is never the only signal: P&L pairs the `--color-profit/--color-loss` color with an explicit `+`/`−` sign and an arrow glyph; lifecycle/verdict chips carry text labels, not just hue (covers color-blind users + WCAG 1.4.1).
- Donut/legend rows are keyboard-focusable and announce `label, pct%`; charts get an `aria-label` summary ("Equity curve, NAV ₹8.42L, up 12.4% vs NIFTY 6.1%") and a visually-hidden data `<table>` fallback for screen readers.
- `Tooltip`/`DropdownMenu`/`AlertDialog`/`Sheet` inherit Radix focus-trap + ESC + arrow-key semantics from the vendored shadcn primitives.
- Focus rings use `--glass-border-focus`; tab order follows visual order (KPI → chart → tables).

**Motion polish (`--ease-quartr`).**
- Card hover: translate `-1px` + `--glass-border` → `--glass-border-hover` over ~160 ms `--ease-quartr`; pressable rows `--surface-hover` → `--surface-active`.
- Range-pill and sub-tab transitions use the existing pill-slide (`PortfolioTab` lines 396–431) with `--ease-quartr`.
- `NumberTicker` count-up on KPI/NAV updates; **flash-on-update** tints a position/blotter row `--color-profit`/`--color-loss` at low alpha on value change then fades via `--ease-quartr`.
- **Live pulse** dot on "live" modules during market hours (driven by `summary.is_live`); when stale, dim to `--text-disabled` with an "as of HH:MM" stamp from `summary.as_of`.
- **`prefers-reduced-motion`:** kill tickers, flashes, and the donut hover-lift; keep instant value updates and pill/tab state changes. Gate via a `useReducedMotion()` check (CSS `@media (prefers-reduced-motion: reduce)` for declarative transitions, JS guard for the count-up/flash).

---

**Key reference files (all absolute):**
- Pattern to clone for the tab + table + donut + range pills + formatters: `/Users/karanveersingh/Downloads/Second_Star/pivot-next/components/agent-panel/PortfolioTab.tsx`
- Tab registration: `/Users/karanveersingh/Downloads/Second_Star/pivot-next/components/AppShell.tsx` (`TabKey`/`NAV_ITEMS` ~L73–90, render slot ~L513, `readHashTab` ~L95)
- API client to extend: `/Users/karanveersingh/Downloads/Second_Star/pivot-next/lib/api.ts` (portfolio block L483–510; `request`/`requestLegacy` L135–150)
- Live quotes: `/Users/karanveersingh/Downloads/Second_Star/pivot-next/hooks/useLiveQuote.ts` over `/Users/karanveersingh/Downloads/Second_Star/pivot-next/lib/liveQuoteManager.ts`
- shadcn primitives: `/Users/karanveersingh/Downloads/Second_Star/pivot-next/components/ui/` (`tabs`, `card`, `badge`, `table`-less → hand-rolled `<table>`, `dropdown-menu`, `alert-dialog`, `sheet`, `tooltip`, `skeleton`, `collapsible`/`accordion`)
- Charts: `recharts@2.15.3` (`/Users/karanveersingh/Downloads/Second_Star/pivot-next/package.json` L44) — no new dependency
- Theme tokens: `/Users/karanveersingh/Downloads/Second_Star/pivot-next/app/globals.css`

**Note:** `components/ui/` has **no `table` primitive vendored** — the existing portfolio table is a hand-rolled `<table>` keyed to tokens; reuse that approach (don't assume a shadcn `Table` import exists). `HoverCard` and a `Calendar`/`DatePicker` are also **not** vendored — fall back to `Tooltip` for methodology notes and native `<input type="date">` (or `CalendarTab` cell logic) for journal date filters.
