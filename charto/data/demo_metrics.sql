-- Charto — the three launch metrics, read from charto_demo.db
--
-- EVERY ROW BEHIND THESE NUMBERS IS SYNTHETIC. charto_demo.db is built by
-- charto/data/seed_demo.py and contains no real person, signup or conversation.
-- The securities are the only real thing in it: symbols come from Pivot's
-- public.company_identity (5,019 listed NSE/BSE/NSE_SME names), so every render
-- points at an instrument that really lists. Do not present these figures as
-- traction — they are demo data.
--
--   sqlite3 charto_demo.db < demo_metrics.sql
--
-- ══════════════════════════════════════════════════════════════════════════
-- METRIC 1 — waitlist registrations in the campaign month
-- ══════════════════════════════════════════════════════════════════════════
SELECT 'Waitlist registrations'                                   AS metric,
       COUNT(*)                                                   AS value,
       COUNT(DISTINCT city)                                       AS cities,
       ROUND(100.0 * SUM(activated) / COUNT(*), 1)                AS activation_pct,
       DATE(MIN(registered_at), 'unixepoch', '+330 minutes')      AS window_from,
       DATE(MAX(registered_at), 'unixepoch', '+330 minutes')      AS window_to,
       ROUND(COUNT(*) * 1.0 /
             (JULIANDAY(MAX(registered_at), 'unixepoch')
            - JULIANDAY(MIN(registered_at), 'unixepoch')), 1)     AS per_day
FROM   demo_waitlist;

-- ══════════════════════════════════════════════════════════════════════════
-- METRIC 2 — securities data rendered to that user base
--   `securities` counts DISTINCT instruments touched; `value` counts renders.
-- ══════════════════════════════════════════════════════════════════════════
SELECT 'Securities data rendered'                                 AS metric,
       COUNT(DISTINCT symbol)                                     AS value,
       COUNT(*)                                                   AS render_events,
       COUNT(DISTINCT exchange)                                   AS exchanges,
       COUNT(DISTINCT sector)                                     AS sectors,
       COUNT(DISTINCT user_id)                                    AS users_served,
       ROUND(AVG(render_ms))                                      AS avg_ms
FROM   demo_security_render;

-- ══════════════════════════════════════════════════════════════════════════
-- METRIC 3 — AI chat sessions
-- ══════════════════════════════════════════════════════════════════════════
SELECT 'AI chat sessions'                                         AS metric,
       COUNT(*)                                                   AS value,
       COUNT(DISTINCT user_id)                                    AS users,
       SUM(turns)                                                 AS total_turns,
       ROUND(AVG(turns), 2)                                       AS avg_turns,
       SUM(tools_used)                                            AS tool_calls,
       ROUND(AVG(latency_ms) / 1000.0, 1)                         AS avg_sec
FROM   demo_chat_session;

-- ══════════════════════════════════════════════════════════════════════════
-- ALL THREE ON ONE LINE — the headline, as one row
-- ══════════════════════════════════════════════════════════════════════════
SELECT (SELECT COUNT(*) FROM demo_waitlist)                AS waitlist_registrations,
       (SELECT COUNT(DISTINCT symbol)
          FROM demo_security_render)                       AS securities_rendered,
       (SELECT COUNT(*) FROM demo_security_render)         AS render_events,
       (SELECT COUNT(*) FROM demo_chat_session)            AS ai_chat_sessions;

-- ── supporting breakdowns ────────────────────────────────────────────────

-- registrations per week, with the acquisition channel mix
SELECT STRFTIME('%Y-W%W', registered_at, 'unixepoch', '+330 minutes') AS week,
       COUNT(*)                                                       AS signups,
       SUM(source = 'organic')                                        AS organic,
       SUM(source = 'twitter')                                        AS twitter,
       SUM(source = 'linkedin')                                       AS linkedin,
       SUM(source = 'referral')                                       AS referral,
       SUM(activated)                                                 AS activated
FROM   demo_waitlist
GROUP  BY week
ORDER  BY week;

-- where the user base is
SELECT city, state, COUNT(*) AS signups,
       ROUND(100.0 * COUNT(*) / (SELECT COUNT(*) FROM demo_waitlist), 1) AS pct
FROM   demo_waitlist
GROUP  BY city, state
ORDER  BY signups DESC
LIMIT  10;

-- coverage of the listed universe, by exchange
SELECT exchange,
       COUNT(DISTINCT symbol) AS securities,
       COUNT(*)               AS render_events
FROM   demo_security_render
GROUP  BY exchange
ORDER  BY securities DESC;

-- which securities the user base actually looked at
SELECT r.symbol, r.company, r.sector, r.exchange,
       COUNT(*)                  AS renders,
       COUNT(DISTINCT r.user_id) AS users
FROM   demo_security_render r
GROUP  BY r.symbol, r.company, r.sector, r.exchange
ORDER  BY renders DESC
LIMIT  10;

-- what people asked the chat to do
SELECT topic,
       COUNT(*)                                        AS sessions,
       SUM(turns)                                      AS turns,
       ROUND(AVG(turns), 1)                            AS avg_turns,
       ROUND(AVG(latency_ms) / 1000.0, 1)              AS avg_sec
FROM   demo_chat_session
GROUP  BY topic
ORDER  BY sessions DESC;

-- the funnel, end to end
SELECT 'registered'      AS stage, COUNT(*) AS users FROM demo_waitlist
UNION ALL
SELECT 'activated',      COUNT(*) FROM demo_waitlist WHERE activated = 1
UNION ALL
SELECT 'viewed a security', COUNT(DISTINCT user_id) FROM demo_security_render
UNION ALL
SELECT 'used the chat',   COUNT(DISTINCT user_id) FROM demo_chat_session;
