-- Data trim — 2026-06-01  (see docs/DATA_AUDIT.md)
-- Target DB: `financials`  (the mc warehouse), NOT pivot_db.
--
-- Drops scraper-operational tables that are dead weight in the data warehouse:
--   * the scraper (pivot-mc-scraper) was deleted in the repo cleanup, so the mc
--     data is now STATIC — these tables never get written again;
--   * zero references in pivot/backend or pivot-backtester/src;
--   * no inbound foreign keys (statement_lines carries only a `scraped_at`
--     timestamp, not a job reference).
--
-- What goes, and why:
--   mc.scrape_jobs     112,560 rows / 34 MB — scraper job history (audit log)
--   mc.rate_bucket           1 row  / 144 kB — scraper rate-limiter state
--   mc.raw_pages             0 rows / 24 kB  — scraper raw-HTML cache (empty)
--   mc.appfeeds_probe        0 rows / 24 kB  — scraper feed-probe scratch (empty)
--
-- Reversible only by re-running the (deleted) scraper migrations. The dropped
-- content is operational history, not market data — nothing downstream reads it.

BEGIN;

-- Scraper job-progress monitoring view (depends on scrape_jobs). The two other
-- mc views — v_latest_balance_sheet, v_latest_pl — are data views over
-- statement_lines and are intentionally KEPT.
DROP VIEW IF EXISTS mc.v_job_progress;

DROP TABLE IF EXISTS mc.scrape_jobs;
DROP TABLE IF EXISTS mc.rate_bucket;
DROP TABLE IF EXISTS mc.raw_pages;
DROP TABLE IF EXISTS mc.appfeeds_probe;

COMMIT;

-- After this, mc holds only reference market data + its data views:
--   companies · daily_prices · statement_lines
--   v_latest_balance_sheet · v_latest_pl

-- ===========================================================================
-- DEFERRED — app DB (pivot_db) trims. NOT executed here: these touch the live
-- database the :8000 backend uses, and are judgment calls, so they need an
-- explicit go-ahead first. Recorded so the decision isn't lost.
-- ===========================================================================
--
-- 1) llm_usage retention. The cost ledger grows unbounded (only ~5.3k rows
--    today, so not yet urgent — left intact). When it matters, roll up to daily
--    per-model aggregates and drop raw rows older than 90 days, e.g.:
--
--      DELETE FROM llm_usage WHERE created_at < now() - interval '90 days';
--
--    Better: a small APScheduler job that aggregates first, then prunes.
--
-- 2) news_* / Polymarket subsystem (news_articles, news_article_classifications,
--    news_event_specs, news_fired_events, news_source_health,
--    news_disambiguation_sessions). A feature subsystem, not dead weight — the
--    polymarket_ws worker errors in tests but the tables back live chat/event
--    features. Dropping them is a PRODUCT decision; not done unilaterally.
