# pivot-mc-scraper

Production-grade scraper for **Moneycontrol financial statements**, persisting to a
new `financials` PostgreSQL database that lives in the same Postgres instance as
Pivot. Built per the spec in `Second_Star/Readme.md`.

## What it captures

For each company on Moneycontrol it fetches all available years of:

| statement type    | URL slug                       | parsing | status |
|-------------------|--------------------------------|---------|--------|
| Balance Sheet     | `balance-sheetVI`              | `mctable1` | ✅ working |
| Profit & Loss     | `profit-lossVI`                | `mctable1` | ✅ working |
| Cash Flow         | `cash-flowVI`                  | `mctable1` | ✅ working |
| Ratios            | `ratiosVI`                     | `mctable1` | ✅ working |
| Quarterly Results | `results/quarterly-results/`   | JS-rendered | ⚠ deferred — see below |

Each is captured for **standalone** and **consolidated** views, paginated until
the table stops returning new period windows. Raw row labels are preserved
exactly as Moneycontrol writes them.

### Quarterly results — known limitation

As of April 2026, the legacy quarterly URL redirects to a Next.js page that
loads the data table client-side. The spec forbids Selenium/Playwright, so the
scraper detects the JS-rendered shell and records those jobs as `no_data` with
the note `"quarterly is JS-rendered"`. Plumbing is in place — see
`src/mc_scraper/parse/quarterly.py` — to drop in a parser as soon as a
JSON/HTTP endpoint is identified.

## Setup

Requires Python 3.11+ and `uv`. Connection details live in `.env`
(`PIVOT_PG_DSN` is the Postgres maintenance DSN — defaults to the Pivot dev DB).

```bash
cd pivot-mc-scraper
cp .env.example .env          # tweak DSN if needed
uv sync                        # creates .venv, installs deps
uv run mc-scraper init         # creates the `financials` DB and runs migrations
uv run mc-scraper test-one RI  # ~30s end-to-end on Reliance
```

`mc-scraper init` connects to `PIVOT_PG_DSN`, creates the `financials`
database if it doesn't exist, then applies every `sql/*.sql` migration. It is
idempotent.

## Running the full crawl

```bash
# 1. One-time discovery: ~5,000 companies across A–Z + others, ~30s.
uv run mc-scraper discover

# 2. Run as many workers as you like (each in its own terminal):
uv run mc-scraper work --concurrency 16
uv run mc-scraper work --concurrency 16

# 3. Watch progress in another terminal:
uv run mc-scraper status --watch
```

At the default 10 req/s global rate (across all workers), the full crawl
finishes in ~1.5–3 hours.

## How the parallelism works

All workers share one `mc.scrape_jobs` table. To pick work, each worker runs:

```sql
SELECT id FROM mc.scrape_jobs
 WHERE status = 'pending'
 ORDER BY id LIMIT 8
 FOR UPDATE SKIP LOCKED;
```

`SKIP LOCKED` means N workers atomically claim N **disjoint** batches with
zero coordination outside Postgres. Each worker's `locked_by` column shows its
hostname/PID so you can confirm parallelism is active:

```sql
SELECT locked_by, count(*) FROM mc.scrape_jobs
 WHERE status='in_progress' GROUP BY locked_by;
```

The global rate limit (default 10 req/s) is enforced cross-process by a
single token-bucket row in `mc.rate_bucket` — every worker checks out tokens
under a brief `SELECT FOR UPDATE`.

If a worker is killed mid-job, its rows stay `in_progress` until the next
worker startup runs `unstick_stale()` (or you run `mc-scraper unstick`),
which flips any lock older than 15 minutes back to `pending`.

## Schema cheat-sheet

- `mc.companies` — master list discovered from listing pages.
- `mc.scrape_jobs` — one row per `(sc_id, statement, basis)`. The job queue.
- `mc.statement_lines` — long-format facts: one row per
  `(sc_id, statement, basis, period_label, line_item, line_order)`. Idempotent
  upserts via the `uq_statement_cell` unique index.
- `mc.raw_pages` — gzip'd HTML for re-parse.
- `mc.rate_bucket` — global rate-limiter state.

## Adding a new statement type

1. Add a value to the `mc.statement_type` enum (`ALTER TYPE`).
2. Map the type in `_VI_SLUG` in `src/mc_scraper/fetch.py`.
3. Add it to the `_STATEMENTS` list in `src/mc_scraper/discover.py` so new
   discoveries seed jobs for it.
4. If the page layout differs from `mctable1`, add a parser in
   `src/mc_scraper/parse/` and dispatch to it from `fetch_all_pages`.

## Tests

```bash
uv run pytest tests/ -q
```

Period and balance-sheet parser tests run against saved HTML fixtures in
`tests/fixtures/`.

## CLI reference

| command            | purpose                                            |
|--------------------|----------------------------------------------------|
| `init`             | create `financials` DB and run migrations          |
| `discover`         | crawl listing pages, populate companies + jobs     |
| `work`             | run a worker (run N times in N terminals for N parallel workers) |
| `status [--watch]` | progress dashboard (one-shot or live)              |
| `unstick`          | reset jobs locked > 15 minutes                     |
| `retry-failed`     | move failed jobs back to pending                   |
| `test-one <sc_id>` | run all 10 statements for a single company         |

## Acceptance run (Apr 30, 2026)

Performed against the Pivot Postgres on macOS:

```
mc-scraper init        → ok database 'financials' ready
mc-scraper test-one RI → 8 statements done, 2 marked no_data (quarterly)
                         5,658 rows in mc.statement_lines for sc_id='RI'
                         period coverage: Mar-26 back to Mar-04 (depending on
                         statement), preserved raw labels & sections.
```

The full discover + work pass was not run during build; see "Running the full
crawl" above.
