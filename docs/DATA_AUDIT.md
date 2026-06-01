# Data / database audit — 2026-06-01

Evidence-based audit of the databases behind Pivot, judged for fitness across the
chat services and **especially the backtester**. All numbers are live queries
against the running Postgres instances on 2026-06-01.

## The three data layers

| Layer | What | Used by |
|---|---|---|
| **`financials` DB · `mc` schema** | Moneycontrol-scraped fundamentals + a little price data | Engine 1 (cross-sectional/factor backtest, `/api/backtest/expr`), the chat `fetch.fundamental` step, the screener |
| **`pivot_db`** | App state (workflows, paper book, orders, news, llm_usage) | Everything operational; not a *data source* for backtests |
| **yfinance (live)** | Daily OHLCV + `Ticker.info` fundamentals, fetched per-run | Engines 2 / 2b (`backtest_workflow`, `backtest_dsl_tree`), live price/indicator reads, the watcher |

The **`mc` schema is the only first-party market database we own.** yfinance is a
live dependency, not a database — so when this audit says "our database," it means
`mc`. That distinction matters: the factor engine (Engine 1 + the Phase-2.1 ranking
I just built) runs **only** on `mc`; the signal engines bypass it entirely.

## Hard numbers (live)

**`mc` schema tables**
| table | rows | note |
|---|---|---|
| `statement_lines` | ~10.7 M | the fundamentals (line items) |
| `scrape_jobs` | ~112,560 | scraper job history — **operational, not data** |
| `daily_prices` | ~12,918 | **9 companies only** |
| `companies` | ~11,256 | the master list |
| `rate_bucket` / `raw_pages` / `appfeeds_probe` | 1 / 0 / 0 | scraper plumbing — **dead** |

**`mc.companies`** (11,256 rows) — metadata coverage is almost entirely empty:
- `nse_symbol`: **10 / 11,256** populated (0.09 %)
- `sector`: **0 / 11,256** · `market_cap`: **0 / 11,256**
- `delisted_on`: **0 / 11,256** · `listed_on`: **0 / 11,256**
- (populated: `company_name`, `company_slug`, `industry_slug`, `bse_code`, `ticker`)

**`mc.daily_prices`** (12,918 rows) — schema is good (`open/high/low/close/close_raw/volume/adj_factor/source`), coverage is not:
- **9 distinct companies**: Reliance, Bharti Airtel, HDFC Bank, ITC, Infosys, Maruti Suzuki (full, 1,567 days, 2020→2026) + Mangalam Ind, Vakrangee, UTI AMC (partial).

**`mc.statement_lines`** (10.7 M rows) — the one genuinely good asset:
- **6,858 distinct companies** · **216 line-items** · 4 statements (P&L 4.55 M, balance-sheet 2.79 M, ratios 2.11 M, cash-flow 1.20 M)
- `availability_date` populated on **~100 %** (10.65 M / 10.7 M) → **real point-in-time** (knowledge-time) integrity for fundamentals
- 3,529 companies have **consolidated** data available by mid-2024; 6,857 standalone
- **period_kind is 100 % `annual`** — there is **no quarterly data at all**
- the `ratios` statement already carries **pre-computed metrics**: Basic/Diluted EPS, Net Profit Margin, Book Value/Share, EV/EBITDA, Earnings Yield, Current Ratio, Asset Turnover, Dividend Payout, …

**`pivot_db`** (32 tables, small + healthy): `llm_usage` 5,309 (unbounded ledger), `workflow_steps` 2,704, `news_article_classifications` 1,771, `trade_logs` 1,754, `workflows` 891, `news_articles` 468, `workflow_runs` 459, `users` 298, `dsl_backtest_runs` 126, the `paper_*` book (single digits), `news_*` (events/polymarket), `apscheduler_jobs` 21.

## Quality verdict (my honest grade)

| Asset | Grade | Why |
|---|---|---|
| `statement_lines` (fundamentals) | **B** content, **D** as wired | Great breadth (6,858 cos, 216 items) + real PIT via `availability_date`. But annual-only, and the engine's field definitions **don't match this schema** (see below) so most of it is unreachable today. |
| `daily_prices` | **F** coverage, **A** schema | 9 companies makes the factor engine a toy. The columns themselves are exactly right. |
| `companies` metadata | **F** | sector / market-cap / listing dates / NSE symbol all empty → no survivorship, no sector/size neutralisation, no ticker mapping. |
| `pivot_db` (app) | **B+** | Small, normalised, healthy. Minor: unbounded `llm_usage`, and a `news_*`/polymarket subsystem that the worker errors on and that isn't core to chat/backtest. |
| yfinance layer | **C** | Works, but daily-only, **survivorship-biased** (today's ticker), rate-limited, and **re-downloaded every run** (no cache). |

**Bottom line: the database is NOT currently fit for serious factor backtesting.**
Single-stock signal backtests work (via yfinance), but the cross-sectional/factor
engine — and the Phase-2.1 ranking just built — is **data-starved** (9 prices) and
**mis-wired** (fundamental field defs don't match the live schema). It's a good
skeleton sitting on a thin, partly-disconnected dataset.

## Critical limitations (ranked by backtest impact)

1. **Prices cover 9 companies.** The cross-sectional engine can only rank/screen
   9 names — `decile(momentum)` over 9 stocks is meaningless. This single gap
   makes factor backtesting unusable. *(It's also why the Phase-2.1 demo could only
   verify the partition math, not a real strategy.)*

2. **Fundamental field defs ↔ live schema mismatch.** The compiler's TTM CTE filters
   `statement = 'quarterly_results'` and sums 4 quarters — but the live data has **no
   `quarterly_results` statement and is 100 % annual**. So every TTM field (e.g.
   `eps_basic_ttm`, hence `pe_ratio`, `roe`) resolves to **zero rows**. Engine 1's
   fundamental screens only ever worked against the seeded *test* fixture, not the
   real `mc` data. The engine is correct; the data contract drifted.

3. **No survivorship protection in practice.** The compiler's survivorship guard
   (`delisted_on > T …`) is real, but `delisted_on`/`listed_on` are **all NULL**, so
   it filters nothing → the universe is "whatever's in `companies` today" →
   survivorship-biased despite the guard.

4. **No sector / market-cap.** Both columns are empty → sector-neutral and
   size-neutral factors (a headline Phase-2.1 goal, `neutralize(sector|size)`) are
   impossible, and there's no way to restrict to a liquid universe (top-500).

5. **No quarterly fundamentals.** Annual-only means slow-moving signals only; no
   earnings-revision / quarterly-momentum factors, and the "TTM" concept can't exist.

6. **yfinance is the de-facto backtest DB for the signal engines**, with all its
   problems: survivorship bias, daily-only, rate limits, and a re-download every run
   (no bars cache).

## Suggestions

### Trim
- **Move scraper-operational tables out of the data DB.** `scrape_jobs` (112 k rows),
  `rate_bucket`, `raw_pages`, `appfeeds_probe` are scraper plumbing — and the scraper
  (`pivot-mc-scraper`) was deleted in the repo cleanup, so the `mc` data is now
  **static**. These tables are pure dead weight in `financials`; archive/drop them (or
  relocate to an ops DB if the scraper ever returns). Keeps the data DB lean and its
  intent clear ("this is reference market data").
- **Cap `llm_usage`.** It grows unbounded (5,309 and counting). Add a retention job:
  roll up to daily/per-model aggregates, drop raw rows older than ~90 days.
- **Decide on the `news_*` / Polymarket subsystem.** Several tables + the
  `polymarket_ws` worker (which errors on `news_event_specs` during tests) for a
  feature that isn't part of the chat/backtest focus. If it's parked, drop the tables
  + disable the worker to cut noise and schema surface.

### Restructure
- **Re-map the fundamental fields to the live schema (the highest-leverage fix).**
  Point the field definitions at the actual statements (`profit_loss` / `balance_sheet`
  / `ratios`) with `period_kind = 'annual'`, not `statement = 'quarterly_results'`.
  Either redefine the "TTM" leaves as **latest-annual ≤ T**, or (better) **read the
  pre-computed `ratios` statement directly** (Basic EPS, Net Profit Margin, Book
  Value/Share, EV/EBITDA, Earnings Yield, …). This single change makes Engine 1's
  fundamental screens work on **~3,500 companies** instead of zero — without scraping
  anything new. *(Keep the `availability_date <= T` PIT discipline, which is the
  schema's best feature.)*
- **Promote the `ratios` statement to first-class fields** in the registry — they're
  already computed by Moneycontrol and more reliable than re-deriving from raw line
  items.
- **Add a thin `mc` data-contract test** that runs the compiled SQL for the headline
  fields against the *live* `mc` schema (not just the seed fixture) and asserts non-zero
  results — so this drift can never silently return again.

### Add (the roadmap that actually unlocks backtest)
1. **Daily OHLCV for a real universe (the #1 unlock).** Backfill ~500–1,000 liquid NSE
   names (NIFTY 500). This single addition: (a) makes the cross-sectional factor engine
   + Phase-2.1 ranking usable, (b) lets Engines 2/2b read prices **from the DB instead
   of yfinance** (fast, cached, no rate limits — the P3 "bars store"), and (c) enables
   a survivorship-free universe if delisted names' history is kept. Source: a bulk
   provider (NSE bhavcopy, or a one-time yfinance/stooq backfill keyed to the
   `companies` list).
2. **Sector / industry classification** — populate `sector` (or a GICS-style map keyed
   to `industry_slug`, which *is* populated). Unlocks `neutralize(sector)` + sector screens.
3. **Market cap + shares-outstanding** (per date, or shares + price → mcap). Unlocks
   size filters, a top-N-by-mcap liquid universe, and size-neutralisation.
4. **Point-in-time index membership** (NIFTY 50/500 constituents by date) → survivorship-
   free universe construction + a real benchmark, not "today's list."
5. **Corporate-actions table** (splits/dividends) → auditable adjustment + dividend-
   reinvested total return (`daily_prices` already has `adj_factor`/`close_raw`, so this
   formalises what's implicit).
6. **A bars cache for yfinance data** even before the full backfill — persist fetched
   daily bars so Engines 2/2b stop re-downloading every run.
7. **Quarterly fundamentals** (later) — enables earnings-revision / quarterly-momentum
   factors and a genuine TTM.
8. **Lagged-price momentum field** becomes trivial once #1 lands (`price(T)/price(T−12m)`)
   — the Phase-2.1 follow-up.

### The one-paragraph priority
Fix the **fundamental field re-mapping** (free, makes 3,500 companies' fundamentals
queryable today) and backfill **daily prices for ~500 names** (the real unlock).
Those two turn the factor engine from a 9-stock toy into a credible Indian-equity
factor backtester, and let the signal engines stop hammering yfinance. Everything
else (sector, mcap, index membership, corporate actions) compounds on top.

---

## Actions taken — 2026-06-01 (trimming + restructuring)

The "adding" (price backfill, sector, mcap) is deferred — it's a data-sourcing job.
The free, high-leverage trimming + restructuring is **done and proven on live data**:

**Restructuring — fundamental field re-mapping (`pivot-backtester`):**
- **TTM fix.** `compiler._emit_one_cte` no longer filters a phantom
  `statement='quarterly_results'`. TTM now sums the last 4 `period_kind='quarterly'`
  rows on the field's own statement, **falling back to the latest annual value**
  (which already spans 12 months) — so on the annual-only mc data, TTM fields
  resolve. `net_profit_ttm > 0`: **0 → 2,574** companies (consolidated); `roe > 0`:
  **0 → 652**. The annual leg also gained a `period_kind IS DISTINCT FROM 'quarterly'`
  guard so quarterly rows can't leak into annual lookups if ever scraped.
- **Stale line_items fixed.** `revenue` (→ "Total Operating Revenues" …; was 0, now
  3,348) and `cash_from_operations` (→ "Net CashFlow From Operating Activities").
- **Pre-computed `ratios` promoted to first-class fields** (15 new): `return_on_equity`,
  `return_on_assets`, `return_on_capital_employed`, `net_profit_margin`, `ebit_margin`,
  `ebitda_margin`, `interest_coverage` (now reads "Interest Coverage Ratios (%)" — the
  old derived version was broken, `operating_profit` has no line item), `debt_to_equity_ratio`,
  `quick_ratio`, `price_to_book`, `ev_to_ebitda`, `earnings_yield`, `dividend_payout`,
  `asset_turnover`, `inventory_turnover`. A real quality-value screen
  `return_on_equity > 15 AND debt_to_equity_ratio < 0.5 AND net_profit_margin > 10`
  now returns **115** companies (was 0).
- **`line_items` lists are now authoritative preference order** — a CASE rank makes the
  first listed synonym win ties (previously `line_order` decided, silently).
- **Guard test added:** `pivot/tests/test_mc_field_contract.py` runs the compiled SQL
  for the headline fields against the *live* `financials` DB and asserts non-trivial
  universes, so this drift fails CI instead of shipping silently. The
  `pivot-backtester` PIT/survivorship seed was realigned to the live schema
  (`profit_loss` + `period_kind`, not `quarterly_results`).

**Trimming — `financials` DB (the static mc warehouse):**
- Dropped the dead scraper-operational objects (no code refs, no inbound FKs, scraper
  deleted): `mc.scrape_jobs` (112,560 rows / 34 MB), `mc.rate_bucket`, `mc.raw_pages`,
  `mc.appfeeds_probe`, and the `mc.v_job_progress` monitoring view. `mc` now holds only
  `companies` · `daily_prices` · `statement_lines` (+ the `v_latest_*` data views).
  Migration: `docs/data_trim_2026-06-01.sql`.
- **Deferred (needs go-ahead — touches the live `:8000` app DB):** `llm_usage`
  retention (only ~5.3k rows today, not urgent) and the `news_*`/Polymarket subsystem
  (a product decision, not dead weight). Both recorded in the trim migration.
