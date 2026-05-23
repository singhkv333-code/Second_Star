# DSL-backed backtester — Phase B plan

> **Status:** plan, not yet implemented. Companion to
> `docs/dsl_grammar.md` (which shipped the LIVE side of the same
> design) and `docs/news_events_phase0_plan.md` (the same plan-first
> shape).
>
> **Premise:** the same condition tree that the watcher evaluates in
> live mode every 60 s should evaluate identically over historical
> bars. Backtest divergence from live behaviour is the single biggest
> trust-killer in algo platforms; we close it by sharing one
> interpreter.

---

## 1. What's already there — survey

The repo has a non-trivial backtester already. Be honest about what
we're *extending*, not rebuilding:

- **`backend/backtester/`** (Python package, ~80 KB total)
  - `engine.py` — single public entry: `run_backtest(strategy_def: dict)`.
    Day-by-day chronological loop. Pulls yfinance OHLCV, builds entry
    + exit signals via a `composer.py` that walks a fixed
    `SIGNAL_REGISTRY` / `EXIT_REGISTRY`. Realistic Indian costs
    (Zerodha brokerage, slippage, STT, exchange + SEBI + stamp duty).
    Warmup-aware: discards first `warmup_days` bars from the equity
    curve so the indicators have time to settle.
  - `composer.py`, `primitives.py` — the existing "signal as a small
    primitive" system. Encodes a similar idea to the DSL but uses a
    different, pre-DSL JSON shape (`SIGNAL_REGISTRY` keys).
  - `metrics.py` — total return, max drawdown, win rate, etc.
  - `portfolio.py` — `Trade` + `PortfolioSnapshot` dataclasses.
  - `exits.py` — stop-loss, trailing-stop, target, n-day-exit logic.

- **HTTP surface**
  - `POST /backtest/run` (`backend/routers/backtest.py`) — takes
    today's `strategy_def` dict, returns metrics + equity curve.
  - `POST /backtest/parse` — NL → `strategy_def` via the existing
    proposer; produces the legacy SIGNAL_REGISTRY shape.
  - `/api/backtest/expr/*` (`expr_backtest.py`) — separate
    expression-based fundamentals backtester wrapping the
    `pivot-backtester` sibling package. Different scope (financials,
    not technical signals); not part of this plan.

- **No look-ahead** is already a design invariant on the engine:
  > "Signals on day `i-1` (yesterday's close) trigger ENTRIES at
  > day `i`'s OPEN. EXITS triggered intraday by stop/target/trailing
  > fire at the trigger price using the bar's high/low; indicator-
  > driven exits fire at today's OPEN."

  Whatever new evaluator we add MUST respect the same contract or
  we're back to the look-ahead bug class the existing engine guards
  against.

**What's missing (what this plan adds):**

- The engine doesn't speak the new DSL tree (`backend/workflows/dsl/`).
  Right now, a `trigger.compound` workflow lives in the watcher but
  has no backtest equivalent.
- There's no `BacktestDataAccessor` — i.e. the as-of-bar version of
  `LiveDataAccessor` from `backend/workflows/dsl/data_accessor.py`.
- The result schema isn't persisted (results are returned to the
  caller, not saved). A persistence layer would let users re-open a
  backtest, share its URL, and compare two strategies.

---

## 2. The single design principle

> **One evaluator, two data accessors.**

The evaluator in `backend/workflows/dsl/evaluator.py` is already pure
and stateless except for the explicit `prev_state` argument. That's
the whole point of the abstraction.

- **Live mode** (today): the watcher constructs a `LiveDataAccessor`
  and calls `evaluate(tree, accessor=...)` every 60 s.
- **Backtest mode** (this plan): the engine constructs a
  `BacktestDataAccessor` bound to a specific `as_of` bar, walks the
  same tree, gets the same boolean.

The evaluator never sees the bar timeline. The accessor enforces
"as-of-bar" semantics; the evaluator just asks "what's RSI(TCS, 14)
right now?" — whether "right now" means the live tick or the close
of bar 4,237, the accessor answers correctly.

This is the contract that kills the look-ahead bug class at its
root. If the accessor refuses to return future data, no walker can
accidentally peek.

---

## 3. Proposed module structure

All new code lives under `backend/workflows/dsl/backtest/` to keep
the boundary clean. The existing `backend/backtester/` package is
**not touched** in this plan (a future consolidation might fold them
together, but that's a separate decision).

```
backend/workflows/dsl/backtest/
├── __init__.py
├── data_accessor.py      BacktestDataAccessor — as-of-bar lookups
├── bar_loader.py         Historical OHLCV loader + indicator cache
├── engine.py             Bar loop + tree evaluation per bar
├── orders.py             Entry/exit simulation + cost model
├── metrics.py            Reuse backend.backtester.metrics where possible
├── schema.py             Pydantic models: BacktestRequest, BacktestResult
└── persistence.py        Optional save/load of results

backend/routers/
└── backtest_dsl.py       POST /api/backtest/dsl/run

tests/workflows/dsl/backtest/
├── test_data_accessor.py     The as-of-bar guarantee (the most
│                             important test in this whole plan)
├── test_engine.py            Tree → trades on synthetic OHLCV
├── test_no_lookahead.py      Adversarial — try to evaluate "tomorrow's
│                             price"; must return None
└── test_persistence.py       Round-trip save/load
```

---

## 4. The BacktestDataAccessor — the as-of-bar guarantee

This is the most safety-critical module in the whole plan. Get it
wrong and every subsequent backtest result is silently
contaminated.

### Interface (mirrors the live one exactly)

```python
class BacktestDataAccessor:
    def __init__(self, *, bars: pd.DataFrame, as_of_idx: int):
        # bars is the full history; as_of_idx is "the bar we are
        # currently simulating". The accessor mutates as_of_idx as
        # the engine moves through time.
        ...

    def advance_to(self, idx: int) -> None:
        self._as_of_idx = idx

    def get_price(self, *, symbol, exchange="NSE") -> Optional[float]:
        # Returns the CLOSE of bar as_of_idx. Index past end returns
        # None.
        ...

    def get_indicator(self, *, symbol, indicator, period, exchange) -> Optional[float]:
        # Computes the indicator over bars[0:as_of_idx+1] and returns
        # the latest value. NEVER touches bars[as_of_idx+1:].
        ...

    def get_volume(self, *, symbol, bars, exchange) -> Optional[float]:
        # Volume summed over bars[as_of_idx - bars + 1 : as_of_idx + 1].
        ...
```

### The single invariant

> **No public method may read from `self._bars[self._as_of_idx + 1 :]`.**

This is enforced by:

1. **Per-call slice.** Every method computes its output from a slice
   that explicitly ends at `as_of_idx + 1` (exclusive on the right
   means inclusive of the current bar's close).
2. **A defensive assert in debug builds.** When `PYDANTIC_DEBUG` /
   `DSL_BACKTEST_STRICT` is on, accessor methods raise if the
   underlying pandas operation references any index ≥ `as_of_idx + 1`.
   Off by default in production for perf reasons.
3. **An adversarial test** (`test_no_lookahead.py`) that hand-builds
   a DataFrame where rows past `as_of_idx` contain `NaN`s and asserts
   the engine's reported metrics are identical to a run where those
   rows simply don't exist. Any look-ahead leak surfaces as a metric
   diff.

### Symbol resolution

Live mode resolves symbols by hitting yfinance + Kite at request
time. Backtest mode pre-loads OHLCV for all symbols referenced in
the tree into memory at engine startup, so the accessor's lookups
are O(1) dict reads.

The `bar_loader.py` walks the tree once (`_walk_all` from
`validators.py`), collects every unique `(symbol, exchange)` pair,
fetches each one via yfinance, and stores them in a
`dict[(symbol, exchange), pd.DataFrame]`. The accessor selects the
right DataFrame on each call.

---

## 5. The engine loop

Pseudocode of `engine.run()`:

```
bars_by_symbol = bar_loader.load(tree, date_range)
master_dates   = bar_loader.master_calendar(bars_by_symbol)
accessor       = BacktestDataAccessor(bars_by_symbol)
prev_state     = {}
trades         = []
position       = None   # currently open trade, if any

for idx, current_date in enumerate(master_dates):
    accessor.advance_to(idx)
    if idx < warmup_idx:
        continue                          # respect indicator warmup

    result = evaluate(tree, accessor=accessor, prev_state=prev_state)
    prev_state = result.new_state         # carry crossings forward

    if position is None and result.value is Ternary.TRUE:
        # Engine SIGNALLED entry on this bar's close →
        # Open at NEXT bar's open (no look-ahead).
        entry = orders.open_position(
            symbol=tree.primary_symbol(),
            entry_bar_idx=idx + 1,
            qty=request.quantity,
            cost_model=request.cost_model,
        )
        if entry: position = entry

    elif position is not None and result.value is Ternary.TRUE_EXIT:
        # The plan supports an OPTIONAL exit-tree on the request
        # (Phase B+1). For Phase B, exits are time-based or
        # stop-loss only — see the "Phase boundaries" section.
        trades.append(orders.close_position(position, idx + 1))
        position = None

if position is not None:
    trades.append(orders.force_close(position, len(master_dates) - 1))

return BacktestResult(
    trades=trades,
    equity_curve=portfolio.equity_curve(trades, ...),
    metrics=metrics.calculate(...),
    diagnostics={...},   # warmup bars used, indicator cache hit rate,
                         # any UNKNOWN-result bars (data outages)
)
```

Two things this gets right by construction:

1. **`Ternary.UNKNOWN` is a valid evaluator result and the engine
   handles it cleanly** — UNKNOWN bars are recorded in
   `diagnostics["unknown_bars"]` and treated as "no signal" for entry
   and "hold" for exit. The live watcher does the same thing.

2. **Entry/exit fires at the NEXT bar's open**, not the bar where
   the signal evaluated true. Same as the existing
   `backend/backtester/engine.py` invariant. The
   `BacktestDataAccessor` is at `idx` when the signal is computed;
   `orders.open_position` jumps to `idx + 1`.

---

## 6. Order simulation + cost model

Reuse the existing module. `backend/backtester/engine.py` already has:

- `buy_cost(price, qty)` → applies slippage, returns (fill_price, total_charges)
- `sell_cost(price, qty)` → same on the sell side, plus STT
- Indian-flavoured constants: BROKERAGE_PER_ORDER, SLIPPAGE_PCT,
  STT_SELL_PCT, exchange / SEBI / stamp-duty rates.

The new `backend/workflows/dsl/backtest/orders.py` imports these
constants directly (or factor them into a shared
`backend.backtester.costs` module if we want explicit separation).
**Don't duplicate the cost model** — both engines should report
identical commission for identical trades, or comparing the two
backtests becomes impossible.

---

## 7. Result schema + persistence

A run produces:

```python
class BacktestResult(_Strict):
    request_id: str           # UUID
    requested_at: datetime
    completed_at: datetime
    tree_summary: str         # tree_to_english(tree) — for the audit page
    date_range: tuple[date, date]
    primary_symbol: str       # the symbol entries/exits trade on
    starting_capital: float
    ending_value: float
    trades: list[TradeRow]    # one row per closed position
    equity_curve: list[EquityPoint]
    metrics: BacktestMetrics
    diagnostics: BacktestDiagnostics
```

`TradeRow` covers: entry date, entry price, exit date, exit price,
qty, gross P&L, costs, net P&L, reason ("signal exit" / "force
close" / "stop loss" / "target").

`BacktestMetrics` reuses `backend.backtester.metrics.calculate_metrics`:
total return, CAGR, max drawdown, max drawdown duration, win rate,
Sharpe, Sortino, profit factor, average win, average loss,
benchmark (NIFTY) total return + alpha.

`BacktestDiagnostics` is the new thing — captures what's specific to
the DSL path: number of `UNKNOWN` bars, indicator cache hit rate,
total bars evaluated, total walks per bar (depth × operand count),
LLM-call count (zero for a pure backtest, non-zero if the run also
emitted the tree from NL in the same request).

**Persistence:**

A new table `dsl_backtest_runs`:

| col | type | note |
|---|---|---|
| `id` | `String(36)` | UUID PK |
| `user_id` | `Integer` FK → `users.id` | |
| `tree_json` | `JSON` | the exact tree fed to the engine |
| `request` | `JSON` | full BacktestRequest |
| `result` | `JSON` | full BacktestResult — trades + equity + metrics |
| `tree_summary` | `Text` | tree_to_english at request time |
| `primary_symbol` | `String(32)` | indexed for fast filtering |
| `started_at`, `finished_at` | `TIMESTAMPTZ` | |
| `status` | `String(16)` | `running` / `succeeded` / `failed` / `cancelled` |
| `error_message` | `Text` | populated on `failed` |

Migration: `0011_dsl_backtest_runs`. Strictly additive on a new
table. No touches to `news_events_*` or workflow tables.

This unlocks: shareable URLs (`/backtest/{id}`), "compare two runs"
diffs, the existing chat surface attaching previous backtests to
future context.

---

## 8. API surface

One new endpoint, mirroring the news_events admin pattern:

```
POST /api/backtest/dsl/run
  body: {
    tree:  <DSL Tree JSON>,
    primary_symbol: "TCS",        # the symbol entries/exits trade on
    date_range: ["2022-01-01", "2025-12-31"],
    starting_capital: 100000,
    quantity: 10,
    exit_policy: {                 # optional; Phase B+1 adds tree-driven exits
      "kind": "stop_loss_pct",
      "value": 0.03                # 3% stop loss
    },
    save: true                     # persist to dsl_backtest_runs
  }
  → 200 { id, result: BacktestResult, persisted: bool }
```

Sibling endpoints for completeness:

```
GET  /api/backtest/dsl/runs            list user's runs
GET  /api/backtest/dsl/runs/{id}       single run with full result
DELETE /api/backtest/dsl/runs/{id}     soft-delete (sets status='cancelled')
```

Auth via `require_user`. Cross-user 404 (matches Agent System +
news_events convention).

The existing `/backtest/run` stays unchanged — DSL backtests live
under their own namespace so the legacy flow keeps working.

---

## 9. Integration with the rest of the system

| Surface | Today | After this plan |
|---|---|---|
| Chat: "Backtest this strategy" | Calls legacy `/backtest/run` with the legacy SIGNAL_REGISTRY shape | New chat tool `backtest_dsl_tree` calls `/api/backtest/dsl/run` with the tree the proposer emitted. Same NL prompt; cleaner pipe. |
| Workflows with `trigger.compound` | Activate → watcher fires live | New "Backtest before activating" button on the draft card: POSTs the tree + a default date range, shows metrics. Confidence step before going live. |
| Audit / readback | Live fires write to `workflow_runs.context` | Backtest results persisted with the tree's `tree_to_english` summary — searchable, shareable. |
| Test harness | Layer-1/2 from the simulate-trigger work | Layer 3 — paper-trading mode is just "run a 1-day backtest against today's bars". Same evaluator, same cost model. |

---

## 10. Phase boundaries

To keep this shippable, **strictly cap Phase B's scope**:

### Phase B (this plan)
- `BacktestDataAccessor` + `bar_loader` + the engine loop
- Entry-only on tree TRUE; exit by **stop-loss percentage** or
  **n-day hold** (NOT an exit tree yet — exit tree comes later)
- Persistence + `/api/backtest/dsl/run` endpoint
- Reuse existing cost model + metrics
- The `test_no_lookahead.py` adversarial suite

### Phase B+1 (next sub-phase)
- **Exit tree** — symmetrical to entry tree. `request.exit_tree`
  is itself a Tree. Engine evaluates entry tree to enter, exit tree
  to exit.
- **Multi-symbol portfolios** — the engine today is one-symbol-at-a-
  time. A tree can already reference multiple symbols (P07 in the
  DSL eval) but trades happen on a single `primary_symbol`. Phase
  B+1 lets the user attach a basket and pick "which symbol fires
  the entry" per match.

### Phase B+2 (paper trading)
- Same engine, but bar timeline is "live ticks streaming in." The
  `BacktestDataAccessor` becomes a `PaperTradingDataAccessor` that
  appends bars from the live feed. Trades route to a `MockBroker`
  instead of yfinance-only simulation.

### Explicitly NOT in scope (any phase shown above)
- Multi-timeframe (daily entry vs 15-min exit etc.)
- Options strategies
- Pairs / spread trading (already partly in
  `backend/backtester/expr_backtest.py` — separate path)
- Machine-learning features as DSL primitives

---

## 11. Risks + how each is mitigated

| Risk | Mitigation |
|---|---|
| **Look-ahead bias** — accidentally reading future bars | Strict per-call slicing in `BacktestDataAccessor` + adversarial `test_no_lookahead.py` + optional `DSL_BACKTEST_STRICT` mode that raises on index leaks. |
| **Engine drift from live** — backtest and watcher disagree | Both use the SAME `evaluate(tree, accessor, prev_state)` function. Test: load a fixed tree + a 100-bar synthetic dataset; run it through the watcher's `_evaluate_compound_trigger` (with a stub accessor that returns the relevant bar) AND the new engine; assert the firing bar indices match exactly. |
| **Indicator cost** — recomputing RSI on every bar is slow | `BacktestDataAccessor` caches `(symbol, indicator, period)` → full series once and slices on each call. 100k bars × 5 indicators ≈ 500k slice operations, all O(1). |
| **Cost model drift between engines** | Both backtest paths import the same `backend.backtester.engine.{buy_cost, sell_cost}` functions. Don't duplicate constants. |
| **Persistence storage growth** | Backtest results can be large (full equity curve at daily granularity over 5 years = ~1,300 rows). Cap each persisted run at 50 KB JSON; compress with zlib if needed. Add a daily cleanup job that deletes `status='cancelled'` runs after 30 days. |
| **API misuse** — user runs 100 backtests at once | Per-user concurrency cap (1 backtest in flight at a time). Long-running runs go into the background with a job-id returned immediately. |
| **DSL tree changes invalidate persisted runs** | Tree schema is versioned via `_schema_version` on the persisted JSON. Replay code keeps backwards-compat readers for prior schema versions. Bump on every breaking grammar change. |

---

## 12. Sequencing — concrete build order

If I were doing this work, the order is:

1. **`bar_loader.py` + `BacktestDataAccessor` + the no-lookahead test
   suite** (1 day). Get the safety floor right before anything else.
2. **Engine loop without entries/exits** (0.5 day). Bare bar-by-bar
   walk that evaluates the tree and produces a `signals[]` array.
   Verifies the evaluator runs against historical data end-to-end.
3. **Order simulation + cost model wiring** (0.5 day). Hook into
   existing `buy_cost`/`sell_cost`. Stop-loss + n-day exit only.
4. **Metrics + BacktestResult schema** (0.5 day). Mostly delegate to
   `backend.backtester.metrics`.
5. **Migration 0011 + persistence + the four `/api/backtest/dsl/*`
   endpoints** (1 day).
6. **Tests** (1 day). Engine on synthetic data, no-lookahead
   adversarial, route handlers (cross-user 404, validation 422,
   happy path 200 with assert on result shape).
7. **Cross-engine consistency test — the live watcher and the
   backtester produce the same firing bar from the same tree + bars**
   (0.5 day). The single most important integration test.
8. **Docs (`docs/backtest_dsl_grammar.md` extending the existing
   `dsl_grammar.md`) + commit** (0.5 day).

**Total: ~5 working days for a Phase B that's shippable + correct.**

Phase B+1 (exit tree) adds maybe another 2 days. Paper trading is
its own sprint.

---

## 13. What the user actually does once this ships

```
User: "Backtest: buy TCS when its RSI(14) is below 30 AND NIFTY is
       above 23000, sell after 5 days or on a 3% stop loss, from
       2022-01-01 to 2025-12-31, ₹100k starting capital."

Chat:  → workflows/propose.py emits the entry tree
       → POST /api/backtest/dsl/run with the tree + exit policy
       → engine runs ~1000 bars in ~3s
       → returns BacktestResult

User sees:
  "Total return: +18.4% (vs NIFTY +12.1%)
   CAGR: 4.4%
   Max drawdown: -7.2% (lasted 41 days)
   Win rate: 62% (8 wins / 5 losses)
   Sharpe: 0.91
   Profit factor: 2.14"

  + an equity curve chart
  + the readback: "RSI(14) of TCS < 30 AND price of NIFTY > 23,000"

User: "Activate it for live."

→ workflow flips to status='active'.
→ watcher picks it up on the next tick.
→ same tree, same evaluator, same trades.
```

That's the loop the DSL was always pointed at. Phase B is the work
that closes it.
