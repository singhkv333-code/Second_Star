-- Useful queries against the long-format mc.statement_lines table.
-- Run with: psql -d financials -f sample_queries.sql

-- 1. Latest balance sheet (standalone) for a company, pivoted to a wide view.
\echo '\n=== 1. Reliance latest 5-year standalone balance sheet (pivoted) ==='
SELECT line_item, section,
       max(value_numeric) FILTER (WHERE period_label LIKE '%26') AS "Mar 26",
       max(value_numeric) FILTER (WHERE period_label LIKE '%25') AS "Mar 25",
       max(value_numeric) FILTER (WHERE period_label LIKE '%24') AS "Mar 24",
       max(value_numeric) FILTER (WHERE period_label LIKE '%23') AS "Mar 23",
       max(value_numeric) FILTER (WHERE period_label LIKE '%22') AS "Mar 22"
  FROM mc.statement_lines
 WHERE sc_id = 'RI' AND statement = 'balance_sheet' AND basis = 'standalone'
 GROUP BY line_item, section, line_order
 ORDER BY line_order
 LIMIT 30;

-- 2. 5-year revenue CAGR (P&L "Total Revenue" or "Revenue From Operations") for any sc_id.
\echo '\n=== 2. 5-yr Total Income CAGR for RI (standalone) ==='
WITH r AS (
  SELECT period_end, value_numeric
    FROM mc.statement_lines
   WHERE sc_id = 'RI' AND statement = 'profit_loss' AND basis = 'standalone'
     AND line_item ILIKE 'Total Income%'
     AND value_numeric IS NOT NULL
   ORDER BY period_end DESC NULLS LAST
   LIMIT 6
)
SELECT (max(value_numeric) FILTER (WHERE period_end = (SELECT max(period_end) FROM r))
       / nullif(max(value_numeric) FILTER (WHERE period_end = (SELECT min(period_end) FROM r)), 0))
       ^ (1.0 / 5.0) - 1 AS cagr_5y
  FROM r;

-- 3. All companies whose Debt-to-Equity grew >50% over 3 years.
\echo '\n=== 3. Companies whose Debt/Equity rose >50% over 3 years ==='
WITH d AS (
  SELECT sc_id, period_end, value_numeric
    FROM mc.statement_lines
   WHERE statement = 'ratios' AND basis = 'standalone'
     AND line_item ILIKE 'Debt%Equity%'
     AND value_numeric IS NOT NULL
), latest AS (
  SELECT DISTINCT ON (sc_id) sc_id, period_end AS p_now, value_numeric AS v_now
    FROM d ORDER BY sc_id, period_end DESC
), three_yr AS (
  SELECT sc_id, value_numeric AS v_then
    FROM (
      SELECT sc_id, period_end, value_numeric,
             row_number() OVER (PARTITION BY sc_id ORDER BY period_end DESC) AS rn
        FROM d
    ) z WHERE rn = 4
)
SELECT l.sc_id, l.v_now, t.v_then,
       round(((l.v_now - t.v_then) / nullif(t.v_then, 0) * 100)::numeric, 1) AS pct_change
  FROM latest l JOIN three_yr t USING (sc_id)
 WHERE t.v_then > 0
   AND l.v_now / t.v_then > 1.5
 ORDER BY pct_change DESC
 LIMIT 25;

-- 4. Latest cash-flow "Net Cash from Operations" for every company (consolidated).
\echo '\n=== 4. Latest Net Cash from Operating Activities (consolidated) ==='
SELECT DISTINCT ON (sc_id)
       sc_id, period_label, value_numeric
  FROM mc.statement_lines
 WHERE statement = 'cash_flow' AND basis = 'consolidated'
   AND line_item ILIKE 'Net Cash%Operating%'
   AND value_numeric IS NOT NULL
 ORDER BY sc_id, period_end DESC
 LIMIT 25;

-- 5. Job/data progress dashboard.
\echo '\n=== 5. Crawl progress ==='
SELECT status, count(*) FROM mc.scrape_jobs GROUP BY status ORDER BY status;
SELECT count(DISTINCT sc_id) AS companies_with_data,
       count(*)              AS total_cells
  FROM mc.statement_lines;
