-- Track which scraping path produced each row.
-- 'mc_html'     — parsed from /financials/.../<type>VI/<sc_id> HTML pages
-- 'mc_appfeeds' — parsed from appfeeds.moneycontrol.com/jsonapi/stocks/* JSON

ALTER TABLE mc.statement_lines
    ADD COLUMN IF NOT EXISTS source TEXT NOT NULL DEFAULT 'mc_html';

CREATE INDEX IF NOT EXISTS statement_lines_source_idx
    ON mc.statement_lines (source);

-- Probe results: persisted for visibility into which appfeeds endpoints are live.
CREATE TABLE IF NOT EXISTS mc.appfeeds_probe (
    id              BIGSERIAL PRIMARY KEY,
    sc_id           TEXT NOT NULL,
    endpoint        TEXT NOT NULL,
    requested_url   TEXT NOT NULL,
    http_status     INT,
    is_json         BOOLEAN,
    has_data        BOOLEAN,
    sample          TEXT,
    probed_at       TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS appfeeds_probe_sc_idx ON mc.appfeeds_probe (sc_id);
