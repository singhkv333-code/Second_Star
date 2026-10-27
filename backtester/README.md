# pivot-backtester

Point-in-time-correct fundamentals backtester over the Pivot `financials`
Postgres DB populated by `pivot-mc-scraper`.

## Status — v1 (in progress)

| Component | State |
|-----------|-------|
| Schema prerequisites (`availability_date`, listing/delisting cols, `daily_prices`) | ✅ in `pivot-mc-scraper/sql/004_*.sql` |
| Filing-availability heuristic backfill | ✅ `mc-scraper backfill-availability` |
| Field registry + resolver                                                          | 🚧 |
| Expression parser / validator / SQL compiler                                       | 🚧 |
| PIT correctness + survivorship tests                                               | 🚧 |
| Engine (portfolio, execution, runner)                                              | ⬜ next milestone |
| Metrics + HTML report                                                              | ⬜ next milestone |

**v1 known limitations** (intentional, will be lifted in subsequent milestones):

- `availability_date` is heuristic-only (`period_end + 60d` annual,
  `+45d` quarterly). Rows are tagged `availability_source = 'heuristic'` so a
  later exchange-filing scrape can replace them without changing the schema.
- Delisting columns exist but `is_active=TRUE` for everyone until the
  NSE/BSE backfill lands. **The v1 backtest report header must surface this.**
- Daily prices come from yfinance for companies that have `nse_symbol`
  populated. Companies with no NSE mapping are excluded from the universe.

## Setup

```bash
cd pivot-backtester
cp .env.example .env
uv sync
uv run pytest tests/ -q          # unit tests pass with no DB
PIVOT_PG_DSN=... uv run pytest tests/ -q -m integration   # PIT/survivorship tests
```

## CLI

```bash
backtester fields list
backtester fields show pe_ratio
backtester validate "pe_ratio < 10 AND debt_to_equity < 0.5"
backtester universe --expr "pe_ratio < 10" --as-of 2020-03-31
backtester run --expr "..." --start ... --end ... --rebalance Q --report ...
```
