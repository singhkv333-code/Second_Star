# Pivot backtesting — before → now → what's different

*2026-06-01. Companion to `docs/BACKTESTING_PLAN.md` (the build plan),
`docs/DATA_AUDIT.md` (the data), and `STATUS.md` (the day log).*

## TL;DR

Before this initiative, Pivot's backtester was a **single-stock technical/fundamental
backtester that printed a return number** — and that number was quietly inflated by a
look-ahead bug, carried no overfitting checks, and the fundamental screener returned
**zero companies** because its field definitions didn't match the live data.

Now it's a **multi-strategy engine whose default output is a *trust verdict*, not a
return** — every backtest reports overfitting controls (Deflated Sharpe, PSR, MinTRL,
Monte-Carlo, sub-period stability) and honest Indian execution costs, the look-ahead is
fixed, and it can test the strategy classes professionals actually run: single-symbol
signals, **fundamental factor screens, pairs/stat-arb (cointegration), and constrained
momentum portfolios (long-only + dollar-neutral long/short)**.

**What's different about *this* one:** it is built to be *trustworthy, not pretty*. It
**refuses to manufacture an edge** — a cointegrated pair still gets "no edge" if the
causal backtest doesn't hold up; a strategy picked as best-of-20 gets its Sharpe
deflated toward insignificance. No Indian retail platform (Streak / Tradetron / AlgoTest
/ Sensibull) reports a Deflated Sharpe or refuses a flattering-but-overfit result.

---

## Side-by-side

| Capability | Before | Now |
|---|---|---|
| **Look-ahead** | Signal-bar fills (orders filled on the *same* bar the signal fired) → inflated results | Next-bar-open fills across engines; adversarial no-look-ahead tests |
| **Overfitting controls** | none | **PSR · Deflated Sharpe · MinTRL · Monte-Carlo · sub-periods · Trust verdict on every backtest**, with DSR deflated by how many variants you tried |
| **Costs / CAGR** | two divergent conventions; ad-hoc costs | one shared India cost model + calendar-day CAGR |
| **Fundamental factor screens** | **0 companies** (TTM field defs didn't match the live annual-only data) | **~3,500 companies**; 15 pre-computed ratios (RoE/ROCE/margins/…); a live data-contract test |
| **Cross-sectional ranking** | could only *threshold* a factor (`pe < 15`) | `rank`/`decile`/`quantile`/`zscore`/`percentrank` + `winsorize` + `neutralize` (industry), and they **compose** (`decile(neutralize(roe))`) |
| **Position sizing** | fixed quantity only | fixed / %-equity / **vol-target** / **ATR-risk** (causal, no-leverage) |
| **Pairs / stat-arb** | impossible | **Engle-Granger + Johansen** cointegration, causal spread z-score strategy, OU half-life, a pairwise scanner |
| **Multi-name portfolios** | impossible (single-symbol engine) | **constrained momentum portfolio** — max names, gross/net caps, **sector caps**, long-only + **dollar-neutral L/S** |
| **Honesty under data gaps** | would have to guess | says "P/E not available" rather than fabricating; reports "0 trades — never fired" |
| **Chat surface** | simple backtests only | `backtest_pairs`, `scan_pairs`, `test_cointegration`, `backtest_portfolio` + the existing tools, each leading with the verdict |
| **Data DB** | dead scraper tables, broken fundamental mappings, 9 stray price rows | trimmed to reference data; fundamentals re-mapped; OHLCV via yfinance |

---

## The examples (same question, before vs now)

### 1 · A single-stock signal — *"Backtest buying RELIANCE when RSI drops below 30"*

**Before.** The engine filled the buy on the *same bar* the RSI crossed 30 — using a
price the strategy couldn't have traded at yet. The reply was a bare return number with
no sense of whether it was luck. A flattering number looked like a strategy.

**Now.** The fill happens at the **next bar's open**. The reply is verdict-led:
> "Backtested RELIANCE buying when RSI(14) < 30 over 3 years: **11 trades, +14.2%**,
> win rate 64%" — and behind it a Trust verdict + PSR/DSR so you know whether 11 trades
> is enough to believe. *Why it matters:* the look-ahead fix alone can swing a backtest
> from "great" to "mediocre"; you were being shown the great version.

### 2 · A fundamental screen — *"stocks with RoE > 15% and debt-to-equity < 0.5"*

**Before.** **Zero results.** The factor engine's "TTM" fields summed four *quarterly*
rows from a `quarterly_results` statement — but the live data is **annual-only** and has
no such statement, so every ratio resolved to nothing. The screener was a façade.

**Now.** Real names: **SANOFICONR (RoE 62.5%, D/E 0.00), GLOTTIS (57.0%, 0.22), …** —
because the fields were re-mapped to the live schema (TTM falls back to the latest
annual), and 15 Moneycontrol-computed ratios were promoted to first-class fields. A
live contract test now fails CI if this ever silently breaks again.

### 3 · A pairs trade — *"Is TCS cointegrated with INFY? Backtest the spread."*

**Before.** **Not possible** — there was no two-symbol object, no cointegration test.

**Now.** Engle-Granger runs, the spread is traded causally on its z-score, and the reply
is honest:
> "Over 3 years HDFCBANK and ICICIBANK were **not cointegrated**, so the pair has no
> mean-reversion basis. The spread strategy **lost 19.4%** across 24 trades; trust
> verdict: **no edge**."
> 
> *And the punchline:* the scanner found AXISBANK/UNIONBANK cointegrated at the 1% level
> — yet its causal backtest **still** came back "no edge." Full-sample cointegration is
> an in-sample tell; the engine won't sell it to you as a strategy.

### 4 · A factor portfolio — *"Hold the top 5 momentum names, rebalanced monthly"*

**Before.** **Not possible** — the engine could hold one position in one symbol.

**Now.** A constrained multi-name portfolio with the knobs pros use:
> "Long-only momentum top-5, monthly, 5y: **+28.5%, −21% max drawdown**, gross 1.0 /
> net 1.0 — verdict **unproven** (PSR 0.83)." Add `long_short` → dollar-neutral (net
> ≈ 0); add a **40% sector cap** and the book diversifies (a 1-name/sector cap on a
> steel-heavy basket moved the result 31.9% → 60.5%). "Unproven" is the point: a +28%
> number with a sub-0.95 PSR is *not yet* an edge, and it says so.

### 5 · The overfitting trap — *"I tested 20 RSI thresholds; the best had a Sharpe of 2"*

**Before.** That Sharpe of 2 would be reported at face value — the most dangerous number
in retail backtesting.

**Now.** The Deflated Sharpe is deflated **for the number of variants you tried** (the
engine counts them across your session), and the chat answers like a quant:
> "Not on its own — if you tried 20 thresholds and kept the best, that result is likely
> inflated by data-snooping. Validate on a holdout / walk-forward, check nearby
> thresholds, and compare after costs." *This is the wedge:* the tool actively argues you
> *down* from a flattering result.

---

## What's genuinely different (the moat, in one place)

1. **The default output is a verdict, not a return.** Every backtest answers "should I
   believe this?" — `no_edge` / `unproven` / `promising` — before it answers "how much?".
2. **It refuses false edges.** Cointegrated-but-fails-causally → no edge. Best-of-N →
   deflated. Concentrated-in-one-year → flagged fragile. Few trades → "insufficient".
3. **It's honest about its data.** "P/E not available", "0 trades — never fired", "rank
   0 — no tradable basket" — instead of a confident fabrication.
4. **It covers the real strategy classes** — single-symbol technical, fundamental factor
   L/S, pairs/baskets (cointegration), momentum portfolios — each through the *same*
   rigor ladder, so they're comparable.
5. **Causality is enforced and tested**, not assumed — every engine fills next-bar and
   ships an adversarial no-look-ahead test.

## What's still limited (so this is honest too)

- **Daily bars only** (yfinance) — no intraday/options yet; survivorship is yfinance's
  "today's ticker".
- **mc fundamentals are annual-only** — slow-moving factors only; no quarterly-revision
  signals; price-dependent ratios (true P/E) lean on yfinance, not mc.
- **Walk-forward / CPCV→PBO** (the rigorous *middle* of the trust ladder) is the next
  build; today we have in-sample rigor + a live paper-trade forward test, not the
  walk-forward in between.
- **Chat** routes the new tools well (eval: 31/32 after fixes) but a dedicated **FE card**
  for pairs/portfolio is still pending (results render as text today), and the earnings-
  calendar prompt still over-clarifies in one phrasing.
