# Data map — every store, who writes it, who reads it, what breaks

> Verified live against Azure and the source tree on **2026-09-05**.
> This file exists because two of our stores are *silently* shared: a break
> upstream keeps serving from a downstream cache, so nothing looks wrong until
> the data is weeks stale. **Read this before deleting, renaming or migrating
> any table, DSN or `.db` file.**

There is exactly **one Postgres server left**: `pivot-db-india`
(Central India, PG 18, `pivot` resource group). It carries three databases.
Everything else is SQLite on the VM.

---

## 1. `pivot_db` — the upstream master (DSN: `DATABASE_URL`)

59 tables in `public`, plus one in `charto_landing`. This is **not** a
placeholder or an eval scratchpad, despite holding eval users. Three distinct
things live here and they have different lifecycles:

### 1a. Reference data charto serves from a cache — **load-bearing**

| Table | Rows | Built by | Reaches the user via |
|---|---:|---|---|
| `quarterly_statement_lines` | 6,641,992 | `pivotted/load_mc_quarterly.py` | `charto/data/sync_quarters.py` → `charto_bars.db.quarters` → company page Quarterly tab |
| `quarterly_metrics` | 335,287 | `pivotted/build_quarterly_metrics.py` | same path |
| `instrument_master` | 75,128 | `refresh_instrument_master` | option chains, symbol resolution |
| `option_universe` | 11,699 | options admin | F&O tools |
| `company_identity` | 5,206 | `pivotted/build_identity.py` | symbol↔ISIN↔sc_id resolution everywhere |
| `result_filings` | 4,210 | filings pipeline | results markers on the chart |

**The trap:** `dataserver.py:3810` reads `quarters` from `charto_bars.db`, not
from Postgres. If `pivot_db` is dropped, moved or its DSN broken, the company
page keeps drawing quarterly data indefinitely from the SQLite cache, and the
only symptom is that it stops advancing. **Do not treat `pivot_db` as
disposable because charto does not connect to it at request time.**

### 1b. Pivot's own application state — real rows, not currently user-facing

`workflows` (161), `workflow_steps` (506), `workflow_runs` (2,218),
`workflow_run_steps` (4,464), `paper_accounts` (48), `paper_orders` (629),
`paper_fills` (244), `paper_positions` (88), `paper_ledger` (311),
`paper_nav_snapshots` (1,432), `paper_idea_nav_snapshots` (1,843),
`conversations` (959), `conversation_messages` (2,972),
`conversation_summaries` (366), `strategies` (18), `llm_usage` (7,650),
`broker_sessions` (2), `broker_audit` (18), `auth_audit` (391),
`apscheduler_jobs` (34), `forward_ideas` (50), `trade_logs` (446).

`users` here is **48 rows, overwhelmingly `@pivoteval.com`** — eval fixtures,
not customers. See §5.

### 1c. The landing waitlist — live, customer-facing

`charto_landing.waitlist_registrations` (9 rows, all `source=landing`).
Written by `charto/web/app/api/waitlist/route.ts` on Vercel via
`WAITLIST_DATABASE_URL`. Unrelated to everything else in this database and
must survive any restructuring of `public`.

### 1d. Opinion-market tables — scheduled for removal

`market_views` (25), `view_expressions` (60), `view_transmission` (12),
`view_confidence` (6), `view_positions` (4), `view_follows` (0),
`view_expectations` (0). Created by migrations `0023_view_markets` and
`0024_view_positions`.

`view_positions`' 4 rows are basket positions from 14–22 Jul 2026: two belong
to eval users (678, 686) and **two belong to user_id 2, a real person**.
**Export all seven tables to JSON before dropping them.**

---

## 2. `financials` — the research corpus (DSN: `FINANCIALS_DSN`)

Read-only to the product; written by the scrapers and the filings pipeline.
Three schemas, and **the schema is not ours — the scraper owns it.**

| Schema.table | Rows |
|---|---:|
| `mc.statement_lines` | 18,278,300 |
| `mc.companies` | 11,256 |
| `mc.growth_metrics_mat` | 75,836 |
| `mc.daily_prices` | 12,918 |
| `filings.facts` | 577,952 |
| `filings.queue` | 4,203 |
| `filings.documents` | 4,060 |
| `filings.remodel` | 3,972 |
| `shp.holder` | 745,680 |
| `shp.category` | 743,935 |
| `shp.sbo` | 45,645 |
| `shp.queue` | 39,630 |
| `shp.filings` | 36,192 |
| `shp.universe` | 5,588 |

**Readers:** `pivot/backend/market/financials_db.py` (via `mc.*`),
`pivotted/fundamentals.py` (which imports pivot's `financials_db` rather than
re-deriving — deliberate, keeps one set of numbers),
`charto/data/sync_financials.py` (copies into `charto_bars.db.financials` and
`.balance_sheet`).

`provision_research.sh` refuses to deploy if `FINANCIALS_DSN` is unset,
because the default is `localhost:5432/financials` and an unset value fails
*silently* — the research chat starts, streams, and apologises its way through
every question about a business.

---

## 3. `pivot_enrich` — supplementary company data (DSN: `ENRICH_DSN`)

`enrich.company_documents` (327,210), `enrich.company_profile` (11,256),
`enrich.tijori_enrichment` (5,800), `enrich.bse_map` (4,846).

Optional by design — `enrich_db.is_enabled()` gates it and it is a late
fallback in `resolve_symbol`. **Its `ticker` column is corrupted** (655 of 670
duplicate groups are different companies): match by NAME, never treat ticker
as an identity key.

---

## 4. `charto_users.db` — SQLite on the VM. **The entire live user-state plane.**

This is the single most dangerous file in the system, because its name
understates it by a wide margin. It is not an auth database.

| Tables | Owner module |
|---|---|
| `users`, `sessions`, `workspace_state`, `layouts`, `conversations` | `dataserver.py:13511-13560` |
| `alerts`, `alert_log` | `charto/data/alerts.py` (`_db()` → `ds._users`) |
| `paper_accounts`, `paper_orders`, `paper_fills`, `paper_positions`, `paper_ledger`, `paper_nav` | `charto/data/paper.py:212` |
| `strategies`, `strategy_log` | `charto/data/strategies.py:172` |
| `journal_trades`, `journal_revisions`, `journal_playbooks` | `charto/data/journal.py:24` |

Every one of those modules reaches it through the **same shared connection and
lock** (`ds._users`, `ds._users_lock`). It holds the **11 real accounts**, 14
sessions and 31 conversations — the only genuine customer data we have.

**Consequence for any identity work:** you cannot migrate "just auth" out of
this file. Auth, alerts, the paper book, armed strategies and the journal are
one transactional unit. Share the *session*, never the *store*.

Backed up to `/data/backup/charto_users.last_success.json` (`dataserver.py:14849`).

---

## 5. The two user tables are disjoint. Do not merge them.

| | `pivot_db.public.users` | `charto_users.db.users` |
|---|---|---|
| Rows | 48 | **11** |
| Who | almost entirely `@pivoteval.com` eval fixtures | the real signups, 4 of them in the last two days |
| Auth | JWT, migration `0022_user_auth_beta` | session cookie + `pw_hash`/`pw_salt`, Google OAuth |
| Reached by | nothing in production — pivot's API is not deployed | everything the user touches |

Separately, 9 people sit in `charto_landing.waitlist_registrations`; 2 of them
also hold charto accounts. **18 unique people across all three stores.**

---

## 6. `charto_bars.db` — SQLite, ~24 GB on the VM (`CHARTO_DB`)

Bars plus a set of **read-caches synced from Postgres**. Sync scripts are
one-directional (PG → SQLite) and are named `charto/data/sync_*.py`.

- Bars: `bars`, `bars_*` (~497M minute rows, 557 symbols)
- Synced caches: `financials`, `balance_sheet` (← `sync_financials.py`),
  `quarters` (← `sync_quarters.py`), `statement`, `results`, `benchmark`,
  `company_profile`, `classification`, `revenue_mix`, `instrument_logo`
- Charto-owned: `screen_meta`, `pattern_stats`, `sync_state`, `news_cache`,
  `deals`, `delivery`, `fut_oi`, `vp_screen`

`sqlite3.connect()` **creates whatever it cannot find**, so a deleted or
mispointed `CHARTO_DB` does not raise — it answers `1`, reports ready, and
serves an empty chart (`dataserver.py:14900`).

`flows_market.db` is a small separate store for market-wide flows
(`dataserver.py:13401`).

---

## 7. Rules that follow from all of the above

1. **Never merge the two user stores.** Share the session token across
   `:3000`/`:5174`/`:8000`; leave `charto_users.db` as the system of record.
2. **`pivot_db` is upstream, not disposable.** Its failure mode is a cache
   that quietly stops advancing, not an error.
3. **Sync direction is always PG → SQLite.** Nothing writes reference data
   back up. Do not introduce a second derivation of an existing number — that
   is a second, silently different set of numbers.
4. **Export before dropping.** Any table with a non-zero row count owned by a
   real `user_id` gets a JSON export first.
5. **A missing DSN is not an error anywhere.** `FINANCIALS_DSN`, `ENRICH_DSN`
   and `CHARTO_DB` all have defaults that fail silently. Probe for a real row,
   never for reachability.
