-- Convenience views over mc.statement_lines.

CREATE OR REPLACE VIEW mc.v_latest_balance_sheet AS
SELECT DISTINCT ON (sc_id, basis, line_item)
    sc_id, basis, line_item, line_order, section,
    period_label, period_end, value_text, value_numeric
FROM mc.statement_lines
WHERE statement = 'balance_sheet'
ORDER BY sc_id, basis, line_item, period_end DESC NULLS LAST;

CREATE OR REPLACE VIEW mc.v_latest_pl AS
SELECT DISTINCT ON (sc_id, basis, line_item)
    sc_id, basis, line_item, line_order, section,
    period_label, period_end, value_text, value_numeric
FROM mc.statement_lines
WHERE statement = 'profit_loss'
ORDER BY sc_id, basis, line_item, period_end DESC NULLS LAST;

CREATE OR REPLACE VIEW mc.v_job_progress AS
SELECT status, COUNT(*) AS jobs
FROM mc.scrape_jobs
GROUP BY status
ORDER BY status;
