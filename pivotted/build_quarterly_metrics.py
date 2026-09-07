"""Derive analysable metrics from the long quarterly_statement_lines table.

WHY A SECOND TABLE

`quarterly_statement_lines` is long (one row per line_item) and mirrors
Moneycontrol's own sheet, which is right for fidelity and wrong for analysis:
every ratio needs a self-join, and the vocabulary is template-dependent. This
builds the wide, one-row-per-(company, basis, quarter) table that growth,
margin and trend work actually needs.

THE TWO TEMPLATES

Moneycontrol serves a different sheet to banks, and the difference is not
cosmetic — a bank has NO 'Net Sales/Income from operations' row at all, so a
naive revenue query silently drops the entire financial sector.

    standard : Net Sales/Income from operations ... P/L Before Other Inc. ...
    banking  : Interest Earned ... Operating Profit before Provisions ...

`template` records which sheet a row came from so cross-sector comparisons can
exclude or special-case banks deliberately rather than by accident. Revenue for
a bank is mapped to Interest Earned, which is the closest analogue but is NOT
the same concept — do not pool the two in one league table without saying so.

TRAPS ENCODED HERE

  * Depreciation arrives as the truncated key 'depreciat' in the standard
    template and 'Depreciation' in the banking one. Both are read.
  * Growth off a negative or zero base is meaningless, not infinite. Every
    growth column is NULL when the base <= 0 rather than emitting a number
    that will later be averaged into nonsense.
  * YoY is matched on a QUARTER INDEX (qi = year*4 + quarter), never on
    lag(4). ~10% of companies have gaps or non-March fiscal years, and lag(4)
    would silently compare Jun-24 against Dec-22 for them.
  * TTM requires all 4 quarters present. A 3-quarter sum labelled TTM
    understates by ~25% and is invisible downstream.

PERFORMANCE — WHY THIS IS TWO STEPS, NOT ONE QUERY

The obvious formulation puts everything in one statement and computes TTM with

    LEFT JOIN LATERAL (SELECT sum(revenue) FROM calc z
                       WHERE z.sc_id=c.sc_id AND z.qi BETWEEN c.qi-3 AND c.qi)

That is a correlated subquery per row against a CTE, and a CTE carries no index,
so each of ~1.4M rows rescans all 1.4M — O(n^2), which does not finish.

So: materialise the pivot into a real table with an index on (sc_id, basis, qi)
first, then compute TTM with a WINDOW over `RANGE BETWEEN 3 PRECEDING`. RANGE
(not ROWS) is deliberate — it ranges over the qi VALUE, so a company missing a
quarter gets a short window and is correctly refused a TTM, whereas ROWS would
silently reach back an extra quarter and label a 5-quarter span as TTM.

    pivot/.venv/bin/python pivotted/build_quarterly_metrics.py
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

PIVOT = Path(__file__).resolve().parent.parent / "pivot"
sys.path.insert(0, str(PIVOT))

# STEP 1 — materialise the pivot into a REAL, INDEXED table. Doing this in a CTE
# is what makes the naive version quadratic (see module docstring).
SQL_STAGE = r"""
DROP TABLE IF EXISTS quarterly_metrics_stage;
CREATE TABLE quarterly_metrics_stage AS
WITH piv AS (
  SELECT
    sc_id, basis, period_end,
    max(symbol)       AS symbol,
    max(isin)         AS isin,
    max(period_label) AS period_label,
    max(isin_state)   AS isin_state,
    (extract(year from period_end)::int * 4
      + ((extract(month from period_end)::int - 1) / 3)) AS qi,
    -- 'Interest Earned' is a HEADER in the banking sheet: blank on every period,
    -- so it is never stored and cannot detect anything. Detect on rows that
    -- actually carry values and exist only for banks.
    bool_or(line_item IN ('Interest Expended',
                          'Operating Profit before Provisions and contingencies')) AS is_bank,

    max(value_numeric) FILTER (WHERE line_item='Net Sales/Income from operations') AS rev_std,
    -- Interest earned = the four component rows under that blank header.
    -- NOTE: MC emits 'others' and '(d) Others' with the SAME value; summing both
    -- would double-count, so only 'others' is taken.
    max(value_numeric) FILTER (WHERE line_item='(a) Int. /Disc. on Adv/Bills')  AS ie_a,
    max(value_numeric) FILTER (WHERE line_item='(b) Income on Investment')      AS ie_b,
    max(value_numeric) FILTER (WHERE line_item='(c) Int. on balances With RBI') AS ie_c,
    max(value_numeric) FILTER (WHERE line_item='others')                        AS ie_d,
    max(value_numeric) FILTER (WHERE line_item='Total Income From Operations')     AS total_income,
    max(value_numeric) FILTER (WHERE line_item='Other Income')                     AS other_income,
    max(value_numeric) FILTER (WHERE line_item='P/L Before Other Inc. , Int., Excpt. Items & Tax') AS ebit_std,
    max(value_numeric) FILTER (WHERE line_item='Operating Profit before Provisions and contingencies') AS ebit_bank,
    max(value_numeric) FILTER (WHERE line_item IN ('depreciat','Depreciation'))    AS depreciation,
    max(value_numeric) FILTER (WHERE line_item IN ('Interest','Interest Expended')) AS interest,
    max(value_numeric) FILTER (WHERE line_item='Employees Cost')                   AS employee_cost,
    max(value_numeric) FILTER (WHERE line_item='Consumption of Raw Materials')     AS raw_material,
    max(value_numeric) FILTER (WHERE line_item='Other Expenses')                   AS other_expenses,
    max(value_numeric) FILTER (WHERE line_item='Provisions And Contingencies')     AS provisions,
    max(value_numeric) FILTER (WHERE line_item='Exceptional Items')                AS exceptional,
    max(value_numeric) FILTER (WHERE line_item='P/L Before Tax')                   AS pbt,
    max(value_numeric) FILTER (WHERE line_item='Tax')                              AS tax,
    max(value_numeric) FILTER (WHERE line_item='Net Profit/(Loss) For the Period') AS net_profit,
    max(value_numeric) FILTER (WHERE line_item='Basic EPS')                        AS eps_basic,
    max(value_numeric) FILTER (WHERE line_item='Diluted EPS')                      AS eps_diluted,
    max(value_numeric) FILTER (WHERE line_item='Equity Share Capital')             AS equity_capital,
    max(value_numeric) FILTER (WHERE line_item='i) % of Gross NPA')                AS gross_npa_pct,
    max(value_numeric) FILTER (WHERE line_item='ii) % of Net NPA')                 AS net_npa_pct,
    max(value_numeric) FILTER (WHERE line_item='Return on Assets %')               AS roa_pct
  FROM quarterly_statement_lines
  GROUP BY sc_id, basis, period_end
),
base AS (
  SELECT p.*,
    CASE WHEN is_bank THEN 'banking' ELSE 'standard' END AS template,
    -- stays NULL when no component is present, so a non-bank never gets a
    -- fabricated zero revenue out of the COALESCE below
    COALESCE(rev_std,
      CASE WHEN COALESCE(ie_a, ie_b, ie_c, ie_d) IS NOT NULL
           THEN COALESCE(ie_a,0)+COALESCE(ie_b,0)+COALESCE(ie_c,0)+COALESCE(ie_d,0)
      END)                        AS revenue,
    COALESCE(ebit_std, ebit_bank) AS ebit
  FROM piv p
)
  SELECT b.*,
    (ebit + COALESCE(depreciation,0))                        AS ebitda,
    100.0 * ebit       / NULLIF(revenue,0)                   AS operating_margin_pct,
    100.0 * (ebit + COALESCE(depreciation,0))
                       / NULLIF(revenue,0)                   AS ebitda_margin_pct,
    100.0 * net_profit / NULLIF(revenue,0)                   AS net_margin_pct,
    100.0 * pbt        / NULLIF(revenue,0)                   AS pbt_margin_pct,
    100.0 * tax        / NULLIF(pbt,0)                       AS tax_rate_pct,
    ebit               / NULLIF(interest,0)                  AS interest_coverage,
    100.0 * other_income / NULLIF(pbt,0)                     AS other_income_share_pct,
    100.0 * employee_cost / NULLIF(revenue,0)                AS employee_cost_pct,
    100.0 * raw_material  / NULLIF(revenue,0)                AS raw_material_pct
  FROM base b;

CREATE INDEX qms_key_idx ON quarterly_metrics_stage (sc_id, basis, qi);
ANALYZE quarterly_metrics_stage;
"""

# STEP 2 — growth via equi-joins on the indexed key, TTM via a RANGE window.
# Both are O(n log n); neither rescans the table per row.
SQL_BUILD = r"""
DROP TABLE IF EXISTS quarterly_metrics;
CREATE TABLE quarterly_metrics AS
WITH calc AS (
  SELECT s.*,

    CASE WHEN count(*) OVER w = 4 THEN sum(revenue)    OVER w END AS rev_ttm,
    CASE WHEN count(*) OVER w = 4 THEN sum(net_profit) OVER w END AS np_ttm,
    CASE WHEN count(*) OVER w = 4 THEN sum(eps_basic)  OVER w END AS eps_ttm,
    count(*) OVER w AS n_ttm
  FROM quarterly_metrics_stage s
  WINDOW w AS (PARTITION BY sc_id, basis ORDER BY qi
               RANGE BETWEEN 3 PRECEDING AND CURRENT ROW)
),
-- A quarter index can hold TWO period_ends when a company changes its fiscal
-- year-end (CC09 filed both 2012-02-29 and 2012-03-31 into one quarter; 6 such
-- groups across 4 companies). Joining on qi against the raw set would then match
-- twice and duplicate the driving row, which the PK caught. The comparator side
-- is therefore collapsed to one row per (sc_id, basis, qi) — the later
-- period_end, i.e. the real quarter close. The DRIVING side keeps every row, so
-- no quarter is dropped from the output.
uniq AS (
  SELECT DISTINCT ON (sc_id, basis, qi) *
  FROM calc ORDER BY sc_id, basis, qi, period_end DESC
)
SELECT
  c.sc_id, c.symbol, c.isin, c.basis, c.template, c.period_end, c.period_label,
  c.qi, c.isin_state,
  c.revenue, c.total_income, c.other_income, c.ebit, c.ebitda, c.depreciation,
  c.interest, c.employee_cost, c.raw_material, c.other_expenses, c.provisions,
  c.exceptional, c.pbt, c.tax, c.net_profit, c.eps_basic, c.eps_diluted,
  c.equity_capital, c.gross_npa_pct, c.net_npa_pct, c.roa_pct,

  c.operating_margin_pct, c.ebitda_margin_pct, c.net_margin_pct, c.pbt_margin_pct,
  c.tax_rate_pct, c.interest_coverage, c.other_income_share_pct,
  c.employee_cost_pct, c.raw_material_pct,

  -- growth: base<=0 yields NULL, never a fabricated percentage
  CASE WHEN y.revenue    > 0 THEN 100.0*(c.revenue    - y.revenue)   /y.revenue    END AS revenue_yoy_pct,
  CASE WHEN y.net_profit > 0 THEN 100.0*(c.net_profit - y.net_profit)/y.net_profit END AS net_profit_yoy_pct,
  CASE WHEN y.ebitda     > 0 THEN 100.0*(c.ebitda     - y.ebitda)    /y.ebitda     END AS ebitda_yoy_pct,
  CASE WHEN q.revenue    > 0 THEN 100.0*(c.revenue    - q.revenue)   /q.revenue    END AS revenue_qoq_pct,
  CASE WHEN q.net_profit > 0 THEN 100.0*(c.net_profit - q.net_profit)/q.net_profit END AS net_profit_qoq_pct,

  -- margin change in basis points (the honest unit for a percentage delta)
  100.0 * (c.operating_margin_pct - y.operating_margin_pct) AS operating_margin_yoy_bps,
  100.0 * (c.net_margin_pct       - y.net_margin_pct)       AS net_margin_yoy_bps,

  -- TTM: only when all 4 quarters are present (n_ttm=4 enforced in the window)
  c.rev_ttm, c.np_ttm, c.eps_ttm, c.n_ttm,
  CASE WHEN y.rev_ttm > 0 THEN 100.0*(c.rev_ttm - y.rev_ttm)/y.rev_ttm END AS rev_ttm_yoy_pct,
  CASE WHEN y.np_ttm  > 0 THEN 100.0*(c.np_ttm  - y.np_ttm) /y.np_ttm  END AS np_ttm_yoy_pct,

  now() AS computed_at
FROM calc c
LEFT JOIN uniq y ON y.sc_id=c.sc_id AND y.basis=c.basis AND y.qi = c.qi - 4
LEFT JOIN uniq q ON q.sc_id=c.sc_id AND q.basis=c.basis AND q.qi = c.qi - 1;

ALTER TABLE quarterly_metrics ADD PRIMARY KEY (sc_id, basis, period_end);
CREATE INDEX qm_symbol_idx  ON quarterly_metrics (symbol);
CREATE INDEX qm_period_idx  ON quarterly_metrics (period_end DESC);
CREATE INDEX qm_template_idx ON quarterly_metrics (template);

COMMENT ON TABLE quarterly_metrics IS
 'Wide per-quarter metrics derived from quarterly_statement_lines. One row per '
 '(sc_id, basis, period_end). Rebuild with pivotted/build_quarterly_metrics.py '
 'after any reload — it is a derived snapshot, not a live view.';
COMMENT ON COLUMN quarterly_metrics.template IS
 'standard | banking. Moneycontrol serves banks a DIFFERENT sheet with no '
 '''Net Sales'' row; for those, revenue is mapped to Interest Earned, which is '
 'the closest analogue but NOT the same concept. Never pool the two in one '
 'league table without saying so.';
COMMENT ON COLUMN quarterly_metrics.qi IS
 'Quarter index = year*4 + quarter. YoY/QoQ join on qi-4/qi-1 rather than '
 'lag(), because ~10%% of companies have gaps or non-March fiscal years and a '
 'positional lag would compare the wrong quarters for them.';
COMMENT ON COLUMN quarterly_metrics.revenue_yoy_pct IS
 'NULL when the year-ago base was <= 0. Growth off a negative base is '
 'meaningless, not infinite — emitting a number there poisons any average.';
COMMENT ON COLUMN quarterly_metrics.n_ttm IS
 'Always 4 when rev_ttm is non-NULL: TTM is only computed with all four '
 'quarters present, so a partial sum can never masquerade as a full year.';
"""


def main() -> int:
    try:
        from dotenv import load_dotenv
        load_dotenv(PIVOT / ".env")
    except ImportError:
        pass
    import psycopg2

    db = psycopg2.connect(os.environ["DATABASE_URL"])
    cur = db.cursor()
    cur.execute("SET statement_timeout='3600s'")
    t0 = time.time()
    cur.execute("SELECT to_regclass('quarterly_metrics_stage')")
    have_stage = cur.fetchone()[0] is not None
    if have_stage and "--restage" not in sys.argv:
        cur.execute("SELECT count(*) FROM quarterly_metrics_stage")
        print(f"step 1/2: reusing existing stage ({cur.fetchone()[0]:,} rows); "
              f"pass --restage to rebuild", flush=True)
    else:
        print("step 1/2: staging pivot (indexed) ...", flush=True)
        cur.execute(SQL_STAGE)
        db.commit()
    t1 = time.time()
    print(f"  stage ready in {t1-t0:.0f}s", flush=True)
    print("step 2/2: growth + TTM windows ...", flush=True)
    cur.execute(SQL_BUILD)
    db.commit()
    print(f"  built in {time.time()-t1:.0f}s  (total {time.time()-t0:.0f}s)")
    cur.execute("DROP TABLE IF EXISTS quarterly_metrics_stage")
    db.commit()

    cur.execute("SELECT count(*), count(DISTINCT sc_id) FROM quarterly_metrics")
    print("  rows / companies:", cur.fetchone())
    cur.execute("""SELECT template, basis, count(*) FROM quarterly_metrics
                   GROUP BY 1,2 ORDER BY 3 DESC""")
    print("  by template/basis:", cur.fetchall())
    for col in ("revenue", "net_profit", "operating_margin_pct", "revenue_yoy_pct",
                "rev_ttm", "rev_ttm_yoy_pct", "interest_coverage"):
        cur.execute(f"SELECT count({col}), count(*) FROM quarterly_metrics")
        n, t = cur.fetchone()
        print(f"    {col:24s} {n:>9,} / {t:,}  ({n*100.0/t:5.1f}%)")
    db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
