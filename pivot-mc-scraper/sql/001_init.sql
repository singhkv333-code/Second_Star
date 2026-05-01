-- mc-scraper schema bootstrap. Idempotent: safe to re-run.

CREATE SCHEMA IF NOT EXISTS mc;

DO $$ BEGIN
    CREATE TYPE mc.statement_type AS ENUM (
        'balance_sheet','profit_loss','cash_flow','ratios','quarterly_results'
    );
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
    CREATE TYPE mc.basis AS ENUM ('standalone','consolidated');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
    CREATE TYPE mc.job_status AS ENUM ('pending','in_progress','done','failed','no_data');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

CREATE TABLE IF NOT EXISTS mc.companies (
    sc_id           TEXT PRIMARY KEY,
    company_name    TEXT NOT NULL,
    company_slug    TEXT NOT NULL,
    industry_slug   TEXT,
    home_url        TEXT NOT NULL,
    nse_symbol      TEXT,
    bse_code        TEXT,
    sector          TEXT,
    market_cap      NUMERIC,
    discovered_at   TIMESTAMPTZ DEFAULT now(),
    last_seen_at    TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS companies_slug_idx ON mc.companies (company_slug);

CREATE TABLE IF NOT EXISTS mc.scrape_jobs (
    id              BIGSERIAL PRIMARY KEY,
    sc_id           TEXT NOT NULL REFERENCES mc.companies(sc_id) ON DELETE CASCADE,
    statement       mc.statement_type NOT NULL,
    basis           mc.basis NOT NULL,
    status          mc.job_status NOT NULL DEFAULT 'pending',
    attempts        INT NOT NULL DEFAULT 0,
    locked_by       TEXT,
    locked_at       TIMESTAMPTZ,
    last_error      TEXT,
    pages_fetched   INT DEFAULT 0,
    rows_inserted   INT DEFAULT 0,
    started_at      TIMESTAMPTZ,
    finished_at     TIMESTAMPTZ,
    UNIQUE (sc_id, statement, basis)
);
CREATE INDEX IF NOT EXISTS scrape_jobs_pending_idx
    ON mc.scrape_jobs (id) WHERE status = 'pending';
CREATE INDEX IF NOT EXISTS scrape_jobs_in_progress_idx
    ON mc.scrape_jobs (status, locked_at) WHERE status = 'in_progress';

CREATE TABLE IF NOT EXISTS mc.statement_lines (
    id              BIGSERIAL PRIMARY KEY,
    sc_id           TEXT NOT NULL REFERENCES mc.companies(sc_id) ON DELETE CASCADE,
    statement       mc.statement_type NOT NULL,
    basis           mc.basis NOT NULL,
    period_label    TEXT NOT NULL,
    period_end      DATE,
    period_kind     TEXT,
    section         TEXT,
    line_item       TEXT NOT NULL,
    line_order      INT NOT NULL,
    value_text      TEXT,
    value_numeric   NUMERIC,
    unit            TEXT DEFAULT 'Rs. Cr',
    page_no         INT NOT NULL,
    source_url      TEXT NOT NULL,
    scraped_at      TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS statement_lines_sc_stmt_idx
    ON mc.statement_lines (sc_id, statement, basis);
CREATE INDEX IF NOT EXISTS statement_lines_period_idx
    ON mc.statement_lines (sc_id, statement, basis, period_label);
CREATE INDEX IF NOT EXISTS statement_lines_label_idx
    ON mc.statement_lines (line_item);
CREATE UNIQUE INDEX IF NOT EXISTS uq_statement_cell ON mc.statement_lines
    (sc_id, statement, basis, period_label, line_item, line_order);

CREATE TABLE IF NOT EXISTS mc.raw_pages (
    id              BIGSERIAL PRIMARY KEY,
    sc_id           TEXT NOT NULL,
    statement       mc.statement_type NOT NULL,
    basis           mc.basis NOT NULL,
    page_no         INT NOT NULL,
    url             TEXT NOT NULL,
    fetched_at      TIMESTAMPTZ DEFAULT now(),
    http_status     INT,
    html_gz         BYTEA,
    UNIQUE (sc_id, statement, basis, page_no)
);

-- Cross-process token bucket for global rate limiting.
CREATE TABLE IF NOT EXISTS mc.rate_bucket (
    id              INT PRIMARY KEY DEFAULT 1,
    tokens          DOUBLE PRECISION NOT NULL DEFAULT 0,
    capacity        DOUBLE PRECISION NOT NULL DEFAULT 10,
    refill_per_sec  DOUBLE PRECISION NOT NULL DEFAULT 10,
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (id = 1)
);
INSERT INTO mc.rate_bucket (id, tokens, capacity, refill_per_sec)
VALUES (1, 10, 10, 10)
ON CONFLICT (id) DO NOTHING;
