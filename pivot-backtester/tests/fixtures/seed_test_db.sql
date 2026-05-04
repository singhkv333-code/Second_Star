-- Seed data for PIT-correctness and survivorship tests.
-- Applied AFTER the pivot-mc-scraper migrations (001..004) into a scratch DB.
-- Every value here is referenced by name in the test bodies; do not edit
-- numbers without updating the assertions.

-- ============================================================================
-- Companies
-- ============================================================================

INSERT INTO mc.companies (sc_id, company_name, company_slug, home_url,
                          is_active, listed_on, delisted_on)
VALUES
  -- X: a vanilla company we use for the PIT timeline tests.
  ('X',  'Test Co X', 'test-co-x', 'https://example.com/x',
   TRUE,  '2010-01-01', NULL),

  -- Y: delisted in 2018-06. Used for survivorship tests.
  ('Y',  'Test Co Y', 'test-co-y', 'https://example.com/y',
   FALSE, '2010-01-01', '2018-06-30'),

  -- Z: a company that both PIT and survivorship test will see as "modern" --
  --    listed 2010, still active. Used as a control.
  ('Z',  'Test Co Z', 'test-co-z', 'https://example.com/z',
   TRUE,  '2010-01-01', NULL),

  -- LATE: listed in 2017. A 2015 universe must NOT include it.
  ('LATE','Test Co Late','test-co-late','https://example.com/late',
   TRUE,  '2017-04-01', NULL);

-- ============================================================================
-- Statement lines for PIT correctness
-- ============================================================================
-- Scenario:
--   X — FY2020 P&L (period_end 2020-03-31, availability_date 2020-08-15) net_profit = 100.
--   X — Q1 FY21 (period_end 2020-06-30, availability_date 2020-08-10) net_profit = 30.
-- Universe queries at:
--   2020-04-01 — neither row visible. (FY20 not yet filed.)
--   2020-08-14 — only Q1 FY21 visible (np=30).
--   2020-08-16 — both visible. Annual lookup picks FY20 (most recent annual).

INSERT INTO mc.statement_lines (
  sc_id, statement, basis, period_label, period_end, period_kind, section,
  line_item, line_order, value_text, value_numeric, unit, page_no, source_url,
  availability_date, availability_source, source
) VALUES
  -- X — annual P&L FY2020
  ('X', 'profit_loss', 'consolidated', 'Mar-20', '2020-03-31', 'annual', 'PL',
   'Net Profit', 1, '100.00', 100.0, 'Rs. Cr', 1, 'test://x/pl',
   '2020-08-15', 'heuristic', 'mc_html'),

  -- X — quarterly Q1 FY21
  ('X', 'quarterly_results', 'consolidated', 'Jun-20', '2020-06-30', 'quarterly', 'PL',
   'Net Profit', 1, '30.00', 30.0, 'Rs. Cr', 1, 'test://x/qr',
   '2020-08-10', 'heuristic', 'mc_html'),

  -- X — fill in 3 more historical quarters so net_profit_ttm has 4 values
  --     after 2020-08-16 only — for a TTM-based PIT test.
  ('X', 'quarterly_results', 'consolidated', 'Mar-20', '2020-03-31', 'quarterly', 'PL',
   'Net Profit', 1, '20.00', 20.0, 'Rs. Cr', 1, 'test://x/qr',
   '2020-05-15', 'heuristic', 'mc_html'),
  ('X', 'quarterly_results', 'consolidated', 'Dec-19', '2019-12-31', 'quarterly', 'PL',
   'Net Profit', 1, '25.00', 25.0, 'Rs. Cr', 1, 'test://x/qr',
   '2020-02-15', 'heuristic', 'mc_html'),
  ('X', 'quarterly_results', 'consolidated', 'Sep-19', '2019-09-30', 'quarterly', 'PL',
   'Net Profit', 1, '25.00', 25.0, 'Rs. Cr', 1, 'test://x/qr',
   '2019-11-15', 'heuristic', 'mc_html');

-- ============================================================================
-- Survivorship: Y had pe_ratio < 10 in 2015 then delisted 2018-06.
-- ============================================================================
-- We need: shares (via equity_share_capital), eps_basic_ttm, price for Y in 2015.
-- We don't strictly need realistic numbers — only that the predicate matches.

INSERT INTO mc.statement_lines (
  sc_id, statement, basis, period_label, period_end, period_kind, section,
  line_item, line_order, value_text, value_numeric, unit, page_no, source_url,
  availability_date, availability_source, source
) VALUES
  -- Y annual P&L FY15 net_profit (also used for roe-style derivations later)
  ('Y', 'profit_loss', 'consolidated', 'Mar-15', '2015-03-31', 'annual', 'PL',
   'Net Profit', 1, '500.00', 500.0, 'Rs. Cr', 1, 'test://y/pl',
   '2015-08-15', 'heuristic', 'mc_html'),

  -- Y quarterly EPS — last 4 quarters before mid-2015 to make eps_basic_ttm = 20.
  ('Y', 'quarterly_results', 'consolidated', 'Mar-15', '2015-03-31', 'quarterly', 'PL',
   'Basic EPS', 1, '5.00', 5.0, 'Rs.', 1, 'test://y/qr',
   '2015-05-15', 'heuristic', 'mc_html'),
  ('Y', 'quarterly_results', 'consolidated', 'Dec-14', '2014-12-31', 'quarterly', 'PL',
   'Basic EPS', 1, '5.00', 5.0, 'Rs.', 1, 'test://y/qr',
   '2015-02-15', 'heuristic', 'mc_html'),
  ('Y', 'quarterly_results', 'consolidated', 'Sep-14', '2014-09-30', 'quarterly', 'PL',
   'Basic EPS', 1, '5.00', 5.0, 'Rs.', 1, 'test://y/qr',
   '2014-11-15', 'heuristic', 'mc_html'),
  ('Y', 'quarterly_results', 'consolidated', 'Jun-14', '2014-06-30', 'quarterly', 'PL',
   'Basic EPS', 1, '5.00', 5.0, 'Rs.', 1, 'test://y/qr',
   '2014-08-15', 'heuristic', 'mc_html');
-- eps_basic_ttm at any T >= 2015-08-15 = 5+5+5+5 = 20.

-- Y price = 100 in 2015 → pe_ratio = 100/20 = 5 < 10 ✓
INSERT INTO mc.daily_prices (sc_id, trade_date, close, source) VALUES
  ('Y', '2015-04-01', 100.0, 'test'),
  ('Y', '2015-06-01', 100.0, 'test'),
  ('Y', '2018-06-29', 100.0, 'test');   -- last quote before delisting

-- ============================================================================
-- Z — control company so the universe isn't only Y.
-- Same TTM EPS structure but a price that flips it in/out.
-- ============================================================================
INSERT INTO mc.statement_lines (
  sc_id, statement, basis, period_label, period_end, period_kind, section,
  line_item, line_order, value_text, value_numeric, unit, page_no, source_url,
  availability_date, availability_source, source
) VALUES
  ('Z', 'quarterly_results', 'consolidated', 'Mar-15', '2015-03-31', 'quarterly', 'PL',
   'Basic EPS', 1, '2.00', 2.0, 'Rs.', 1, 'test://z/qr',
   '2015-05-15', 'heuristic', 'mc_html'),
  ('Z', 'quarterly_results', 'consolidated', 'Dec-14', '2014-12-31', 'quarterly', 'PL',
   'Basic EPS', 1, '2.00', 2.0, 'Rs.', 1, 'test://z/qr',
   '2015-02-15', 'heuristic', 'mc_html'),
  ('Z', 'quarterly_results', 'consolidated', 'Sep-14', '2014-09-30', 'quarterly', 'PL',
   'Basic EPS', 1, '2.00', 2.0, 'Rs.', 1, 'test://z/qr',
   '2014-11-15', 'heuristic', 'mc_html'),
  ('Z', 'quarterly_results', 'consolidated', 'Jun-14', '2014-06-30', 'quarterly', 'PL',
   'Basic EPS', 1, '2.00', 2.0, 'Rs.', 1, 'test://z/qr',
   '2014-08-15', 'heuristic', 'mc_html');
-- eps_basic_ttm = 8

INSERT INTO mc.daily_prices (sc_id, trade_date, close, source) VALUES
  ('Z', '2015-04-01', 200.0, 'test'),    -- pe = 200/8 = 25 > 10
  ('Z', '2019-04-01', 50.0,  'test'),    -- pe = 50/8 = 6.25 < 10
  ('Z', '2020-04-01', 50.0,  'test');
