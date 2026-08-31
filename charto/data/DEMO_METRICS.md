# Charto — launch metrics (demo dataset)

> **Every row behind these numbers is synthetic.** `charto_demo.db` is built by
> `charto/data/seed_demo.py` and contains no real person, signup or conversation.
> The securities are the one real thing in it: symbols come from Pivot's
> `public.company_identity`, so each render points at an instrument that genuinely
> lists on NSE/BSE. **These are not traction figures** — they exist so the product
> can be demoed and load-checked against a populated database.
>
> Built `2026-08-27T20:02:15+05:30` · seed `20260826` · window
> `2026-07-27` → `2026-08-26`
> · symbols from pivot_db company_identity via export_universe.py (5,019 real listed securities)

## The headline

| waitlist_registrations | securities_rendered | render_events | ai_chat_sessions |
|---:|---:|---:|---:|
| 2,148 | 4,459 | 18,420 | 536 |

```sql
SELECT (SELECT COUNT(*) FROM demo_waitlist)                AS waitlist_registrations,
       (SELECT COUNT(DISTINCT symbol)
          FROM demo_security_render)                       AS securities_rendered,
       (SELECT COUNT(*) FROM demo_security_render)         AS render_events,
       (SELECT COUNT(*) FROM demo_chat_session)            AS ai_chat_sessions;
```

---

## Metric 1 — waitlist registrations

```sql
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
```

| metric | value | cities | activation_pct | window_from | window_to | per_day |
|---|---:|---:|---:|---:|---:|---:|
| Waitlist registrations | 2,148 | 36 | 42.2 | 2026-07-27 | 2026-08-26 | 71.8 |

**By week, with channel mix**

| week | signups | organic | twitter | linkedin | referral | activated |
|---|---:|---:|---:|---:|---:|---:|
| 2026-W30 | 754 | 203 | 148 | 110 | 61 | 334 |
| 2026-W31 | 496 | 142 | 98 | 74 | 43 | 200 |
| 2026-W32 | 353 | 86 | 84 | 54 | 19 | 139 |
| 2026-W33 | 363 | 91 | 88 | 50 | 35 | 158 |
| 2026-W34 | 182 | 50 | 40 | 18 | 17 | 76 |

**Top cities**

| city | state | signups | pct |
|---|---|---:|---:|
| Mumbai | Maharashtra | 339 | 15.8 |
| Delhi | Delhi | 227 | 10.6 |
| Bengaluru | Karnataka | 215 | 10.0 |
| Pune | Maharashtra | 148 | 6.9 |
| Hyderabad | Telangana | 144 | 6.7 |
| Chennai | Tamil Nadu | 117 | 5.4 |
| Ahmedabad | Gujarat | 109 | 5.1 |
| Kolkata | West Bengal | 96 | 4.5 |
| Jaipur | Rajasthan | 67 | 3.1 |
| Surat | Gujarat | 64 | 3.0 |

---

## Metric 2 — securities data rendered

The headline is **breadth**: how much of the listed universe was actually put in
front of somebody. `render_events` is the activity count behind it.

```sql
SELECT 'Securities data rendered'                                 AS metric,
       COUNT(DISTINCT symbol)                                     AS value,      -- the headline: BREADTH
       COUNT(*)                                                   AS render_events,
       COUNT(DISTINCT exchange)                                   AS exchanges,
       COUNT(DISTINCT sector)                                     AS sectors,
       COUNT(DISTINCT user_id)                                    AS users_served,
       ROUND(AVG(render_ms))                                      AS avg_ms
FROM   demo_security_render;
```

| metric | value | render_events | exchanges | sectors | users_served | avg_ms |
|---|---:|---:|---:|---:|---:|---:|
| Securities data rendered | 4,459 | 18,420 | 3 | 12 | 855 | 545.0 |

**Coverage by exchange** — 4,482 of the 5,019 listed names in Pivot's universe

| exchange | securities | render_events |
|---|---:|---:|
| NSE | 2,283 | 13,117 |
| BSE | 2,045 | 5,091 |
| NSE_SME | 131 | 212 |

**Most-viewed securities**

| symbol | company | sector | exchange | renders | users |
|---|---|---|---|---:|---:|
| TITAN | Titan Company Limited | Consumer Cyclical | NSE | 494 | 370 |
| RELIANCE | Reliance Industries Limited | Energy | NSE | 475 | 369 |
| MARUTI | Maruti Suzuki India Limited | Consumer Cyclical | NSE | 472 | 350 |
| LT | Larsen & Toubro Limited | Industrials | NSE | 471 | 367 |
| ITC | ITC Limited | Unclassified | NSE | 464 | 363 |
| AXISBANK | Axis Bank Limited | Financial Services | NSE | 458 | 350 |
| WIPRO | Wipro Limited | Technology | NSE | 448 | 341 |
| INFY | Infosys Limited | Technology | NSE | 447 | 349 |
| ADANIENT | Adani Enterprises Limited | Energy | NSE | 437 | 344 |
| BAJFINANCE | Bajaj Finance Limited | Financial Services | NSE | 436 | 335 |

**By sector**

| sector | securities | renders |
|---|---:|---:|
| Unclassified | 894 | 4,281 |
| Financial Services | 465 | 3,125 |
| Consumer Cyclical | 702 | 2,424 |
| Technology | 277 | 1,933 |
| Industrials | 702 | 1,933 |
| Basic Materials | 553 | 1,158 |
| Consumer Defensive | 302 | 1,068 |
| Energy | 44 | 1,002 |

---

## Metric 3 — AI chat sessions

```sql
SELECT 'AI chat sessions'                                         AS metric,
       COUNT(*)                                                   AS value,
       COUNT(DISTINCT user_id)                                    AS users,
       SUM(turns)                                                 AS total_turns,
       ROUND(AVG(turns), 2)                                       AS avg_turns,
       SUM(tools_used)                                            AS tool_calls,
       ROUND(AVG(latency_ms) / 1000.0, 1)                         AS avg_sec
FROM   demo_chat_session;
```

| metric | value | users | total_turns | avg_turns | tool_calls | avg_sec |
|---|---:|---:|---:|---:|---:|---:|
| AI chat sessions | 536 | 394 | 1,561 | 2.91 | 3,156 | 18.5 |

**What people asked for**

| topic | sessions | turns | avg_turns | avg_sec |
|---|---:|---:|---:|---:|
| indicators | 75 | 234 | 3.1 | 18.9 |
| compare | 46 | 114 | 2.5 | 18.2 |
| alerts | 46 | 129 | 2.8 | 18.9 |
| levels | 45 | 143 | 3.2 | 19.0 |
| volatility | 39 | 103 | 2.6 | 17.5 |
| drawing | 37 | 103 | 2.8 | 17.8 |
| backtest | 37 | 128 | 3.5 | 18.4 |
| patterns | 36 | 112 | 3.1 | 17.4 |
| volume | 35 | 98 | 2.8 | 19.0 |
| explain | 34 | 89 | 2.6 | 19.2 |
| fundamentals | 33 | 102 | 3.1 | 19.0 |
| screener | 28 | 77 | 2.8 | 17.2 |
| risk | 26 | 73 | 2.8 | 18.6 |
| trend | 19 | 56 | 2.9 | 19.6 |

---

## Funnel

| stage | users |
|---|---:|
| registered | 2,148 |
| activated | 907 |
| viewed a security | 855 |
| used the chat | 394 |

---

## Sample rows

**`demo_waitlist`**

| id | full_name | email | city | state | source | experience | activated | registered_at |
|---:|---|---|---|---|---|---|---:|---|
| 1 | Dhruv Rane | dhruvrane@gmail.com | Bengaluru | Karnataka | organic | 1-3 years | 1 | 2026-08-04 07:38:55 |
| 2 | Sourav Mondal | sourav.mondal@outlook.com | Chennai | Tamil Nadu | reddit | 3-7 years | 0 | 2026-08-18 04:49:15 |
| 3 | Prasenjit Paul | prasenjitpaul@gmail.com | Pune | Maharashtra | twitter | 7+ years | 0 | 2026-07-31 07:10:48 |
| 4 | Dhruv Rane | dhruv55@icloud.com | Mumbai | Maharashtra | youtube | 1-3 years | 0 | 2026-08-15 17:33:01 |
| 5 | Aditi Saxena | aditi39@outlook.com | Coimbatore | Tamil Nadu | twitter | 7+ years | 1 | 2026-08-22 16:49:26 |
| 6 | Rohit Arora | rohitarora@gmail.com | Delhi | Delhi | organic | 1-3 years | 0 | 2026-08-24 17:08:54 |
| 7 | Nirav Dholakia | niravdholakia@gmail.com | Ranchi | Jharkhand | linkedin | beginner | 1 | 2026-08-01 21:56:55 |
| 8 | Ganesh Chandran | gchandran371@gmail.com | Bengaluru | Karnataka | linkedin | 7+ years | 1 | 2026-08-05 14:14:15 |

**`demo_security_render`**

| id | user_id | symbol | company | sector | exchange | surface | interval | render_ms |
|---:|---:|---|---|---|---|---|---|---:|
| 1 | 356 | NCCBLUE | NCC Bluewater Products Ltd. | Unclassified | BSE | company page | 15m | 66 |
| 2 | 1,183 | TCS | Tata Consultancy Services Limited | Technology | NSE | chart | 1d | 1,025 |
| 3 | 1,665 | AXISBANK | Axis Bank Limited | Financial Services | NSE | chart | 1d | 550 |
| 4 | 250 | KALAMANDIR | Sai Silks (Kalamandir) Limited | Consumer Cyclical | NSE | screener | 1w | 560 |
| 5 | 1,479 | HIPOLIN | Hipolin Ltd. | Consumer Defensive | BSE | chart | 1d | 73 |
| 6 | 1,329 | AXISBANK | Axis Bank Limited | Financial Services | NSE | chart | 1d | 959 |
| 7 | 900 | RELIANCE | Reliance Industries Limited | Energy | NSE | chart | 1d | 579 |
| 8 | 604 | HDFCBANK | HDFC Bank Limited | Financial Services | NSE | chart | 1d | 720 |

**`demo_chat_session`**

| id | user_id | chat_id | title | symbols | topic | turns | tools_used |
|---:|---:|---|---|---|---|---:|---:|
| 1 | 1,694 | c_20260826_00001 | why did BNRSEC move like that | BNRSEC | explain | 2 | 4 |
| 2 | 1,696 | c_20260826_00002 | draw a trendline on SMLT | SMLT | drawing | 2 | 2 |
| 3 | 2,011 | c_20260826_00003 | why did ADANIENT move like that | ADANIENT | explain | 3 | 9 |
| 4 | 1,291 | c_20260826_00004 | show me RSI and MACD on INFY | INFY | indicators | 1 | 1 |
| 5 | 1,032 | c_20260826_00005 | is TCS overbought right now | TCS | indicators | 4 | 12 |
| 6 | 891 | c_20260826_00006 | compare RELIANCE with its sector | RELIANCE | compare | 4 | 8 |
| 7 | 1,780 | c_20260826_00007 | is ICSL overbought right now | ICSL | indicators | 4 | 12 |
| 8 | 1,030 | c_20260826_00008 | compare EMMBI with its sector | EMMBI | compare | 5 | 5 |

---

## Schema and rebuild

| table | rows | holds |
|---|---:|---|
| `demo_waitlist` | 2,148 | one row per registration |
| `demo_security_render` | 18,420 | one row per securities-data render |
| `demo_chat_session` | 536 | one row per AI chat session |
| `demo_chat_message` | 3,122 | the turns inside those sessions |
| `demo_meta` | 7 | provenance — says the data is synthetic |

Everything lives in **`charto/data/charto_demo.db`**, a file of its own. It is not
`charto_users.db`: that is the live user store which `charto-backup.timer` copies
off the VM nightly, and inventing 2,148 users inside it would put 2,148 invented
users into every backup from then on. Deleting the demo data is deleting the file.

```bash
# refresh the securities universe from Pivot's Azure Postgres (needs pivot/.env)
../../pivot/.venv/bin/python export_universe.py

# rebuild the demo dataset — deterministic for a given seed
python3 seed_demo.py                     # defaults
python3 seed_demo.py --seed 7            # a different, still-reproducible set

# read the metrics
sqlite3 -header -column charto_demo.db < demo_metrics.sql
```
