# PostHog Data Warehouse Setup Report

**Date:** 2026-07-05  
**PostHog Project:** Default project (ID: 498532)

---

## Sources Created

### 1. Postgres — Connected ✅

**Source ID:** `019f31ae-fa93-0000-5edc-e1bffe705f24`  
**Host:** `pivot-db-india.postgres.database.azure.com`  
**Database:** `pivot_db`  
**Schema:** `public`  
**Access mode:** `warehouse` (import + live-queryable)

The following 8 tables are syncing incrementally into PostHog:

| Table | Sync type | Incremental field |
|---|---|---|
| `users` | incremental | `id` (integer) |
| `conversations` | incremental | `updated_at` (datetime) |
| `conversation_messages` | incremental | `created_at` (datetime) |
| `llm_usage` | incremental | `id` (integer) |
| `workflows` | incremental | `updated_at` (datetime) |
| `workflow_runs` | incremental | `started_at` (datetime) |
| `market_views` | incremental | `created_at` (datetime) |
| `dsl_backtest_runs` | incremental | `started_at` (datetime) |

These tables cover Pivot's core analytics surfaces: user accounts, chat activity, LLM token usage, workflow creation/execution, Opinion Markets views, and backtesting runs.

---

### 2. Sentry — Browser setup required ⚠️

Credentials were not provided during setup. To connect Sentry:

1. Open this URL: **[Connect Sentry to PostHog](https://us.posthog.com/project/498532/data-warehouse/new-source?kind=Sentry)**
2. In Sentry, go to **Settings → Developer Settings → New Internal Integration** and create a token with these scopes:
   - `alerts:read`, `event:read`, `member:read`, `org:read`, `project:read`, `team:read`
3. Enter the token and your organization slug in the PostHog form.

---

## Files Modified or Created

- **`posthog-warehouse-report.md`** (this file) — created as a summary of setup actions.

No application source code was modified.

---

## Manual Steps Next

1. **Wait for the first sync** — PostHog will begin syncing the 8 Postgres tables. Check progress at:  
   [PostHog Data Warehouse](https://us.posthog.com/project/498532/data-warehouse)

2. **Connect Sentry** — Use the deep-link above when you have an internal integration token ready.

3. **Build insights** — You can now query your Postgres tables in PostHog using HogQL. Example:
   ```sql
   SELECT u.email, count(cm.id) as message_count
   FROM pivot_db_public_users u
   JOIN pivot_db_public_conversation_messages cm ON cm.conversation_id IN (
     SELECT id FROM pivot_db_public_conversations WHERE user_id = u.id
   )
   GROUP BY u.email
   ORDER BY message_count DESC
   ```

4. **Allowlist PostHog egress IPs** — If the Azure Postgres firewall is locked down, make sure PostHog's egress IPs are allowlisted. See the [PostHog docs](https://posthog.com/docs/cdp/sources/postgres) for the current IP list.
