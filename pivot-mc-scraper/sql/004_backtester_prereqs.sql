-- Backtester prerequisites. Idempotent.
--
-- Adds three things needed for point-in-time-correct backtests:
--   1. availability_date / availability_source on statement_lines
--   2. listing/delisting columns on companies (so universe queries can avoid survivorship bias)
--   3. mc.daily_prices for adjusted closes

-- 1. Filing availability ---------------------------------------------------

ALTER TABLE mc.statement_lines
    ADD COLUMN IF NOT EXISTS availability_date   DATE,
    ADD COLUMN IF NOT EXISTS availability_source TEXT;

DO $$ BEGIN
    ALTER TABLE mc.statement_lines
        ADD CONSTRAINT statement_lines_availability_source_chk
        CHECK (availability_source IN ('exchange_filing','heuristic','manual'));
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

CREATE INDEX IF NOT EXISTS statement_lines_avail_idx
    ON mc.statement_lines (sc_id, statement, basis, availability_date);

-- 2. Listing / delisting --------------------------------------------------

ALTER TABLE mc.companies
    ADD COLUMN IF NOT EXISTS is_active        BOOLEAN NOT NULL DEFAULT TRUE,
    ADD COLUMN IF NOT EXISTS listed_on        DATE,
    ADD COLUMN IF NOT EXISTS delisted_on      DATE,
    ADD COLUMN IF NOT EXISTS delisting_reason TEXT;

CREATE INDEX IF NOT EXISTS companies_active_idx
    ON mc.companies (is_active)
    WHERE is_active = FALSE;

-- 3. Daily prices ---------------------------------------------------------

CREATE TABLE IF NOT EXISTS mc.daily_prices (
    sc_id       TEXT    NOT NULL REFERENCES mc.companies(sc_id) ON DELETE CASCADE,
    trade_date  DATE    NOT NULL,
    open        NUMERIC,
    high        NUMERIC,
    low         NUMERIC,
    close       NUMERIC NOT NULL,           -- adjusted close (split/bonus/div)
    close_raw   NUMERIC,                    -- unadjusted, kept for sanity checks
    volume      BIGINT,
    adj_factor  NUMERIC NOT NULL DEFAULT 1.0,
    source      TEXT    NOT NULL,           -- 'yfinance' | 'jugaad' | 'nse'
    PRIMARY KEY (sc_id, trade_date)
);
CREATE INDEX IF NOT EXISTS daily_prices_date_idx ON mc.daily_prices (trade_date);
