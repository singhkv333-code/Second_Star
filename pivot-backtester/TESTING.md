# Pivot backtester — end-to-end test guide

A copy-paste sequence to verify the fundamentals backtester from the lowest
layer (parser unit tests) up to the chat UI.

> Assumptions: macOS, `uv` installed, Postgres reachable at
> `localhost:5432` with user `pivot_user / pivot_password`, the
> `pivot-mc-scraper` repo in the sibling directory. A partial Moneycontrol
> crawl (29k+ jobs `done`) is sufficient — full crawl not required.

---

## 1. Unit + integration tests (fastest, no live data needed)

```bash
cd /Users/karanveersingh/Downloads/Second_Star/pivot-backtester
uv sync
uv run pytest tests/ -q
```

Expected: **31 passed**. These create a per-test scratch DB, apply the
scraper migrations + a tiny seed, and verify:

- Parser correctness (precedence, case insensitivity, SQL injection rejection).
- Compiler output (NULLIF on division, `availability_date <= $1` filter,
  survivorship guard, TTM `HAVING COUNT(*) = 4`).
- **PIT correctness**: company X with FY20 P&L `availability_date = 2020-08-15`
  is invisible at 2020-04-01, invisible at 2020-08-14, visible at 2020-08-16.
- **Survivorship**: a company delisted 2018-06-30 appears in a 2015 universe
  but not in a 2019 universe.

If any of these fail, do **not** trust any further test — the foundation is
wrong.

---

## 2. CLI on the live financials DB

### 2a. One-time prerequisites

```bash
# (a) Make sure the new schema columns / daily_prices table exist.
cd ../pivot-mc-scraper && uv run mc-scraper init

# (b) Heuristic backfill of availability_date for everything already scraped.
#     Annual = period_end + 60 days, Quarterly = +45 days.
#     Slow on a 10M-row table — runs without a client timeout. Wait it out.
uv run mc-scraper backfill-availability
```

### 2b. Field dictionary smoke

```bash
cd ../pivot-backtester

uv run backtester fields list
uv run backtester fields show pe_ratio
uv run backtester fields show roe
```

### 2c. Validate + compile (no DB hit)

```bash
uv run backtester validate "pe_ratio < 10 AND roe > 15"
uv run backtester compile "pe_ratio < 10 AND roe > 15"
# Inspect the generated SQL: every leaf becomes a CTE, division has NULLIF,
# survivorship guard is present, $1 is the backtest date.
```

### 2d. Universe screening at a point in time

Fundamentals-only expressions (no `price`, `pe_ratio`, etc.) work today
without any price backfill:

```bash
# Companies with healthy ROE and low debt as of late 2023.
uv run backtester universe \
  --expr "roe > 15 AND debt_to_equity < 0.5" \
  --as-of 2023-12-31 \
  --limit 20
```

Expressions that reference `price` (and thus `pe_ratio`, `pb_ratio`, etc.)
return **empty** until daily prices are backfilled — see §2e.

### 2e. (Optional) Daily price backfill

Yfinance fetch needs `mc.companies.nse_symbol` populated, which is **not done
yet** in v1. Until that mapping CLI lands, the fastest way to test the
end-to-end pipeline is to UPDATE one company manually:

```bash
uv run python -c "
import asyncio, asyncpg
async def main():
    c = await asyncpg.connect(dsn='postgresql://pivot_user:pivot_password@localhost:5432/financials')
    await c.execute(\"UPDATE mc.companies SET nse_symbol='RELIANCE' WHERE sc_id='RI'\")
    await c.execute(\"UPDATE mc.companies SET nse_symbol='INFY' WHERE sc_id='IT'\")
    await c.execute(\"UPDATE mc.companies SET nse_symbol='TCS' WHERE sc_id='TCS'\")
    print('mapped 3 companies for testing')
    await c.close()
asyncio.run(main())
"
uv run backtester backfill-prices --since 2020-01-01 --sc-ids RI,IT,TCS
```

### 2f. Full backtest with HTML report

```bash
uv run backtester run \
  --expr "roe > 15 AND debt_to_equity < 1" \
  --start 2020-01-01 --end 2024-12-31 \
  --rebalance Q \
  --report ./reports/quality.html
open ./reports/quality.html
```

The report's audit appendix lists every company in the first rebalance with
the actual `roe` and `debt_to_equity` values used to qualify them.

---

## 3. FastAPI endpoints

The backend exposes the new endpoints under `/api/backtest/expr/*`. All
require a Bearer token from `/auth/login` (or whatever your test user is).

```bash
# Get a token first (replace with your real test creds).
TOKEN=$(curl -s -X POST http://127.0.0.1:8000/auth/login \
  -H "content-type: application/json" \
  -d '{"email":"<your-test-user>","password":"<...>"}' | jq -r .access_token)

# 3a. List fields
curl -s http://127.0.0.1:8000/api/backtest/expr/fields \
  -H "Authorization: Bearer $TOKEN" | jq '.computed_fields[].name'

# 3b. Validate (no DB hit)
curl -s -X POST http://127.0.0.1:8000/api/backtest/expr/validate \
  -H "Authorization: Bearer $TOKEN" \
  -H "content-type: application/json" \
  -d '{"expression":"pe_ratio < 15 AND roe > 12"}' | jq

# 3c. Screen — universe at a single date
curl -s -X POST http://127.0.0.1:8000/api/backtest/expr/screen \
  -H "Authorization: Bearer $TOKEN" \
  -H "content-type: application/json" \
  -d '{"expression":"roe > 15 AND debt_to_equity < 0.5","as_of":"2023-12-31","limit":15}' | jq

# 3d. Run — full backtest (returns metrics + curves; no HTML).
curl -s -X POST http://127.0.0.1:8000/api/backtest/expr/run \
  -H "Authorization: Bearer $TOKEN" \
  -H "content-type: application/json" \
  -d '{
    "expression":"roe > 15 AND debt_to_equity < 1",
    "start":"2020-01-01","end":"2024-12-31",
    "rebalance":"Q"
  }' | jq '.metrics, {n_rebalances:(.rebalances|length), n_trades:.n_trades, warnings:.warnings}'
```

---

## 4. Chat shortcut commands

Both shortcuts are intercepted **before** the LLM round-trip — they're fast
and deterministic.

### 4a. `/screen <expr> [@ YYYY-MM-DD]`

In the Pivot chat, type:

```
/screen roe > 15 AND debt_to_equity < 0.5
/screen current_ratio > 1.5 AND net_profit > 0 @ 2022-12-31
```

The reply renders an inline table of the matching companies with the
field values used for qualification.

### 4b. Natural-language screening

Without the slash prefix, the chat falls back to a regex match. Either of
these triggers the screener:

```
screen for stocks with roe > 15 AND debt_to_equity < 1
find stocks where current_ratio > 1.5 AND net_profit > 0
```

The trigger requires at least one comparison operator (`<`, `>`, `=`) so
casual chat doesn't accidentally fire it.

### 4c. `/expr-backtest <expr> from <start> to <end> [rebalance Q]`

Full backtest with metrics tile in the chat bubble:

```
/expr-backtest roe > 15 AND debt_to_equity < 1 from 2020-01-01 to 2024-12-31
/expr-backtest pe_ratio < 12 from 2018-01-01 to 2023-12-31 rebalance M
```

This needs `daily_prices` populated for the expression to find any
companies (since it's used internally for execution).

---

## 5. Manual UI test

1. `cd pivot && source .venv/bin/activate && uvicorn backend.main:app --reload --port 8000`
2. `cd frontend && npm run dev`
3. Open http://localhost:3000/, sign in as a test user.
4. In the chat, type `/screen roe > 15 AND debt_to_equity < 0.5`. Expect:
   a chat bubble with a small table of qualifying companies and their values.
5. Type `/screen pe_ratio < 10` (without prices backfilled). Expect: a friendly
   "no companies match" message with explanation.
6. Type `/screen roe > 15 AND zorglub > 0`. Expect: a `did you mean` error.
7. Type `/expr-backtest roe > 12 from 2020-01-01 to 2023-12-31 rebalance Q`.
   Expect: a tile grid with CAGR / Total / Max DD / Sharpe and a one-line
   summary. Warnings (if any) appear in a yellow callout.

---

## 6. Known v1 caveats (loud)

- `availability_date` is heuristic-only. Companies that filed late produce
  PIT bias in their favour (universe sees data slightly earlier than the real
  filing date). Mitigation: replace with BSE filing-date scrape later — the
  schema column `availability_source = 'exchange_filing'` is reserved for that.
- `is_active = TRUE` for everyone (no NSE/BSE delisting backfill yet).
  Real survivorship bias is present in the live DB; the test DB and the SQL
  generator handle it correctly.
- `price`-based predicates need yfinance data for companies with
  `nse_symbol` populated. Until the symbol-mapping pass lands, only
  fundamentals-only screens work end-to-end on the live DB.
