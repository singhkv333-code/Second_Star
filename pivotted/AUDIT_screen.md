# AUDIT — `backend/services/fundamentals_screen.py`

Read-only audit against the live `mc` (financials) and `enrich` (pivot_enrich) Postgres
databases, 2026-08-06. Every claim below is backed by a query that was actually run or a
line number that was actually read. Nothing was edited.

---

## VERDICT

**Not trustworthy as-shipped for a research product — with two specific, bounded exceptions.**

The SQL in this file is structurally sound: I could not make any join fan out or misalign a
row, and the `mc`-side numbers it selects are real values that exist in the DB. What makes it
untrustworthy is everything *around* the SQL:

1. **It joins a corrupted identity map.** Market cap, trailing P/E and 1-year return come from
   `enrich.company_profile` keyed by `sc_id`. That table was populated by resolving
   `mc.companies.ticker`, and that column holds **another company's symbol** on hundreds of
   rows. Result: 1,553 of 5,017 cap-map entries (31%) share a market-cap value with a
   different `sc_id`, and 158 of the 280 names in the "large cap" tier are in such a duplicate
   group. *"Large-cap textiles" returns exactly one company: a ₹200 Cr micro-cap wearing
   ₹1,13,619 Cr of somebody else's market cap.* (F1)
2. **Three advertised metrics are structurally dead** — `roic`, `gross_margin`,
   `receivables_turnover` return zero rows for every company, always, because line 1444
   excludes the only source that carries them. Adding one to a working screen silently zeroes
   the result with no explanation. (F2)
3. **One metric means something else entirely** — `operating_profit` (alias `ebitda`) resolves
   to *Profit Before Tax* on 100% of rows. (F3)
4. **Every disclosure the module writes is invisible to the user.** `render_screen_markdown`
   (line 1974–1980) deliberately drops `note`, and `chat_service.py:8698` makes that render
   the *entire* reply on a single-screen turn. A `sector="defence"` ask returns a whole-market
   list under a confident "Filters applied: ROE > 20" header. (F7)

The exceptions: the **enrich sector path** (`screen_from_enrich`, lines 352–471) is materially
more correct than the `mc` path — self-consistent names, real P/E, clean industry labels — and
its output for e.g. "cheapest banks by P/E" is genuinely good. And the **raw numeric values**
from `mc.statement_lines` are never fabricated; where a number is wrong it is a *real number
belonging to a different company or a different year*, which is the more dangerous failure but
not an invention.

**On the reported GVPIL/SANOFI anomaly: NOT REPRODUCED as a join defect. See F5 — the real
cause is a different, confirmed bug (basis-before-recency) and the value 48.02 is Sanofi's own
year-stale number.**

---

# FINDINGS BY SEVERITY

## F1 — CONFIRMED · CRITICAL: enrich join attaches another company's market cap / P/E / 1-yr return

**File:** `fundamentals_screen.py:123–232` (`_load_market_caps`, `_load_trailing_pe`,
`_load_52w_change`), consumed at `1150–1215` (caps/yr1/pe_real CTEs) and `1531`/`1554`
(cap-tier and default-floor filters).

**What's wrong.** All three loaders key on `enrich.company_profile.sc_id` and the module
assumes that is "the SAME sc_id as mc.companies" (comment, line 80). It is — but the *content*
of that enrich row often belongs to a different company, because the enrich scraper resolved
yfinance by `mc.companies.ticker`, and that column is corrupted.

**Evidence.**

```sql
-- mc.companies: the ticker column holds OTHER companies' symbols
select sc_id, company_name, nse_symbol, ticker from mc.companies
where nse_symbol in ('LOYALTEX','J&KBANK','GURUNANAK','HDFC','IRBIT-RE');
```
```
sc_id | company_name    | nse_symbol | ticker
JKB   | JK Bank         | J&KBANK    | CANBK       <- Canara Bank's symbol
GAIL  | Gurunanak Agric | GURUNANAK  | GAIL        <- GAIL India's symbol
HDF   | HDFC            | HDFC       | HDFCAMC     <- HDFC AMC's symbol
LTM   | Loyal Textiles  | LOYALTEX   | LTM         <- LATAM Airlines' NYSE ticker
IIT02 | Energy Infra    | IRBIT-RE   | ENRIN
```
```sql
-- enrich then pulled the WRONG company's profile under that sc_id
select sc_id, company_name, ticker, long_name, market_cap/1e7 cap_cr
from enrich.company_profile where sc_id in ('JKB','GAIL','HDF');
```
```
GAIL | Gurunanak Agric | GAIL    | GAIL (India) Limited                  | 114340.63
HDF  | HDFC            | HDFCAMC | HDFC Asset Management Company Limited | 116550.82
JKB  | JK Bank         | CANBK   | Canara Bank                          | 121310.90
```

Blast radius (Python cross-DB join, `mc.nse_symbol` vs `enrich.ticker`, both non-null):

| measure | value |
|---|---|
| cap map size (`_load_market_caps`) | 5,017 sc_ids |
| rows where enrich identity ≠ mc identity **and** a market cap is present | **109** |
| …of those, in the large-cap tier (≥₹50k Cr) | **12** |
| …above the default bare-sector floor (≥₹3k Cr) | **44** |
| distinct cap values appearing on **>1** sc_id (i.e. duplicated companies) | **638 groups / 1,553 sc_ids (31%)** |
| large-cap tier sc_ids sharing a duplicated cap value | **158 of 280 (56%)** |

The 12 contaminated large caps, verbatim:

```
Tata Steel Long | nse TATASTLLP | enrich says Tata Steel Limited              | 248,140 Cr | PE 23.03
Hind Industries | nse HINDIND   | enrich says Hindalco Industries Limited     | 225,851 Cr | PE 16.77
Bil Energy      | nse BILENERGY | enrich says Adani Energy Solutions Limited  | 181,081 Cr | PE 80.87
Varun Ind       | nse VARUN     | enrich says Varun Beverages Limited         | 179,093 Cr | PE 56.27
Power and Instr | nse PIGL      | enrich says CG Power and Industrial Sol.    | 151,781 Cr | PE 124.99
Energy Infra    | nse IRBIT-RE  | enrich says Siemens Energy India Limited    | 136,533 Cr | PE 104.15
JK Bank         | nse J&KBANK   | enrich says Canara Bank                     | 121,311 Cr | PE 6.15
HDFC            | nse HDFC      | enrich says HDFC Asset Management Company   | 116,551 Cr | PE 40.91
Gurunanak Agric | nse GURUNANAK | enrich says GAIL (India) Limited            | 114,341 Cr | PE 15.08
Loyal Textiles  | nse LOYALTEX  | enrich says LTM Limited                     | 113,619 Cr | PE 22.67
IDFC            | nse IDFC      | enrich says IDFC First Bank Limited         |  67,769 Cr | PE 41.85
Coromandel Engg | nse COROENGG  | enrich says Coromandel International        |  59,347 Cr | PE 30.39
```

**User-visible consequence** (`screen_by_fundamentals` run live):

```python
screen_by_fundamentals([], sector="textiles", sort_by={"field":"roe","dir":"desc"},
                       market_cap_tier="large", limit=10)
```
```
{'symbol':'LOYALTEX','name':'Loyal Textiles','market_cap_cr':113619,'one_year_pct':-25.6,'roe':-28.93}
note: "...restricted to large-cap (≥ ₹50,000 Cr market cap, ~280 names)..."
```
One row. A ₹200 Cr textile mill presented to a retail user as India's only large-cap textile
company, at ₹1.14 lakh crore.

And a `market_cap > 1,00,000 Cr` screen prints visibly duplicated pairs:

```
GAIL   GAIL             114341        CANBK    Canara Bank    121311
GURUNANAK Gurunanak Agric 114341       J&KBANK  JK Bank        121311
HDFCAMC HDFC AMC         116551        ENRIN    Siemens Energy 136533
HDFC    HDFC             116551        IRBIT-RE Energy Infra   136533
```

**Note the mechanism**, because it matters for the fix: this is *not* a SQL fanout. The
`caps`/`pe_real`/`yr1` CTEs `unnest` Python dicts, so their keys are unique by construction,
and every metric CTE is `DISTINCT ON (sc_id)` or reads `growth_metrics_mat` (verified
7,283 rows / 7,283 distinct sc_ids). **No join in this file can duplicate a company.** The
contamination is entirely inherited from the enrich identity map.

---

## F2 — CONFIRMED · CRITICAL: `roic`, `gross_margin`, `receivables_turnover` are dead fields

**File:** the source predicate `AND (sl.statement <> 'ratios' OR sl.source IN ('mc_html','mc_api'))`
at lines **1283, 1337, 1354, 1410, 1418, 1444, 2087**. Field definitions at **611–636**, whose
comment reads *"Extended ratio set (scraped where present + **pivot-derived backfill**)"*.

**What's wrong.** Those three line items exist **only** under `source='pivot_derived'`, which
that predicate excludes. The comment describes a backfill the query then throws away.

```sql
select line_item, source, count(*) n, count(distinct sc_id) scs, max(period_end)
from mc.statement_lines
where line_item in ('Return on Invested Capital (%)','Gross Profit Margin (%)',
                    'Debtors Turnover Ratio (X)','Interest Coverage Ratios (%)')
group by 1,2;
```
```
Debtors Turnover Ratio (X)     | pivot_derived | 4305 | 3533 | 2026-03-31
Gross Profit Margin (%)        | pivot_derived |10102 | 6671 | 2026-03-31
Return on Invested Capital (%) | pivot_derived | 4397 | 3612 | 2026-03-31
Interest Coverage Ratios (%)   | mc_api        |21868 | 2110 | 2026-03-31
Interest Coverage Ratios (%)   | mc_html       | 7980 |  801 | 2026-03-31
Interest Coverage Ratios (%)   | pivot_derived | 8709 | 6211 | 2026-03-31   <- discarded
```

Live confirmation:
```
roic                   count=0   render=None -> LLM narrates
gross_margin           count=0   render=None -> LLM narrates
receivables_turnover   count=0   render=None -> LLM narrates
interest_coverage      count=5   render=TABLE     (48.7% coverage: 2,505 of 5,141)

roe>15 alone           -> 10 results
roe>15 AND roic>12     ->  0 results
note (identical in both cases): "...data-quality bounds applied; latest filing on/after
2023-01-01 (recency floor); basis: consolidated preferred, else standalone"
```

**Consequence.** All three are in the model-facing enum (`agents/tools.py:783-784`, comment
*"extended ratio set (scraped + pivot-derived backfill)"*). A user asking "high-ROIC compounders"
gets an empty screen, the note gives no reason, and the model narrates *"no companies match
these criteria"* — a confidently wrong statement about 3,612 companies that do have the number.
`interest_coverage` loses its 6,211-company backfill and screens on half the universe.

---

## F3 — CONFIRMED · HIGH: `operating_profit` / `ebitda` is Profit Before Tax, 100% of the time

**File:** `_RAW_ITEM_FIELDS` line **652** (`"operating_profit": ("Operating Profit","cr")`);
alias `"ebitda": "operating_profit"` line **733**; synonym list from
`FIELD_MAP["operating_profit"]` = `('Operating Profit','EBITDA','Profit/Loss Before Exceptional,
ExtraOrdinary Items And Tax','Profit/Loss Before Tax')`.

```sql
select line_item, count(*) n, count(distinct sc_id) scs, max(period_end)
from mc.statement_lines
where line_item in ('Operating Profit','EBITDA',
                    'Profit/Loss Before Exceptional, ExtraOrdinary Items And Tax',
                    'Profit/Loss Before Tax') group by 1;
```
```
Profit/Loss Before Exceptional, ExtraOrdinary Items And Tax | 146836 | 7335 | 2026-03-31
Profit/Loss Before Tax                                      | 146836 | 7335 | 2026-03-31
('Operating Profit' and 'EBITDA': ZERO rows — the line items do not exist in this DB)
```
```sql
-- distinct sc_ids resolving to each synonym, recent window
has 'Operating Profit' = 0     has any synonym = 5975     has 'Profit/Loss Before Tax' = 5975
```

**Consequence.** The column header says "Operating Profit"; the tool description
(`agents/tools.py`) says *"operating_profit (EBITDA)"* and instructs the model *"EBITDA =
operating_profit"* for custom ratios. Every `debt/EBITDA` custom ratio a user asks for is
actually **debt ÷ profit-before-tax** — a materially different, interest-and-D&A-inclusive
denominator. No disclosure anywhere.

---

## F4 — CONFIRMED · HIGH: `pe` inner-joins the Earnings-Yield CTE, excluding 13% of the ₹2L Cr+ universe

**File:** lines **1435–1457**. `pe_real` (the *preferred* enrich trailing P/E) is a
`LEFT JOIN` (1214), but the `m_pe` Earnings-Yield CTE it coalesces onto is an
`INNER JOIN` (1452). A company with a real, loaded trailing P/E but no recent MC Earnings-Yield
row is dropped before the COALESCE can fire.

```
>₹2L Cr names visible to a market_cap screen : 39
…of those visible to ANY pe screen           : 34
MISSING: HDFCBANK, TATASTEEL, TATASTLLP, TITAN, HINDIND

   HDFCBANK  enrich trailingPE = 17.398483   (loaded in memory, unused)
   TATASTEEL enrich trailingPE = 23.027777
   TITAN     enrich trailingPE = 77.190010
```
```sql
-- HDFC Bank has 196 ratios rows but ZERO recent Earnings Yield rows
select nse_symbol, ratios_rows, ey_recent ...  -->  HDFCBANK | 196 | 0
```

**Consequence.** "Cheapest large banks by P/E" can never return **HDFC Bank**, India's
second-largest listed company. The result looks complete; nothing in `note` says a name was
dropped for want of a source row.

---

## F5 — CONFIRMED · HIGH: `DISTINCT ON` orders basis BEFORE recency → year-stale values shown as current

**File:** lines **1445–1449** (and identically at 1338–1341, 1411–1412, 1419–1420, 2088–2091):

```sql
ORDER BY sl.sc_id,
         (sl.basis = 'consolidated') DESC,   -- <-- basis first
         sl.period_end DESC NULLS LAST,      -- <-- recency second
         sl.availability_date DESC NULLS LAST, array_position(...)
```

The docstring (lines 21–24) describes this as "picks the LATEST period per sc_id … preferring
the consolidated basis". It does the opposite: it picks the latest **consolidated** period, and
only falls back to standalone when *no* consolidated row exists at all. A company that filed
consolidated through FY2024 and standalone-only since is shown its FY2024 number.

**This is the actual cause of the reported GVPIL/SANOFI anomaly**, and it is not a fanout:

```
AP26 Sanofi India | ratios | standalone   | 2025-12-31 | 43.60   <- true latest
AP26 Sanofi India | ratios | consolidated | 2024-12-31 | 48.02   <- what the screen returns
AP29 GE Power     | ratios | consolidated | 2026-03-31 | 43.36   <- correct
```
Live screen output — GVPIL is **correct**, Sanofi is the stale one:
```
{'symbol':'SANOFI','name':'Sanofi India','roe': 48.02}      <- FY2024, presented as current
{'symbol':'GVPIL', 'name':'GE Power India','roe': 43.36}    <- FY2026, correct
```
The lead's "GVPIL at 48.02 identical to Sanofi" is a transcription of two adjacent rows; the
underlying defect is real but is Sanofi's row, not a cross-attachment.

**Scale** (ROE, 3-year floor, comparing the module's ordering against a recency-first ordering):
```
total sc_ids with an ROE value      : 5809
picked row is an OLDER period       :  167  (2.9%)
…and a materially different value   :  158
mean |difference|                   : 147.86
```
Among names above the ₹3,000 Cr default floor (1,311 names) — i.e. names a retail user
recognises:
```
Sanofi India   (SANOFI)     shows 48.02 (FY24)  vs 43.60 (FY25)
Dhanuka Agritec(DHANUKA)    shows 21.17 (FY25)  vs 17.07 (FY26)
Cera Sanitary  (CERA)       shows 18.20 (FY25)  vs 13.86 (FY26)
Vedant Fashions(MANYAVAR)   shows 25.85 (FY24)  vs 21.74 (FY25)
Jyothy Labs    (JYOTHYLAB)  shows 18.07 (FY25)  vs 20.97 (FY26)
Railtel        (RAILTEL)    shows 11.46 (FY23)  vs 14.99 (FY25)
JTEKT India    (JTEKTINDIA) shows 11.57 (FY23)  vs  8.55 (FY25)
Birla Cotsyn   (BIRLACOT)   shows -6.25 (FY25)  vs -116.05 (FY26)
```

**Consequence.** This is not cosmetic: a `roe > 20` filter **admits Dhanuka (true 17.07) and
Cera (true 13.86)** and **rejects Jyothy Labs (true 20.97)**. The threshold the user set is
evaluated against the wrong year. The `note` says only "basis: consolidated preferred, else
standalone" — which is a true statement about basis and a false implication about recency —
and that note is never shown (F7).

---

## F6 — CONFIRMED · HIGH: the impostor dedup deletes the real Titan and re-labels its symbol

**File:** lines **1629–1632**.

```sql
select sc_id, company_name, nse_symbol, ticker from mc.companies
where nse_symbol='TITAN' or ticker='TITAN';
```
```
TI01  | Titan Company | (null)  | TITAN     <- the real Titan
IAG01 | IAG Company   | TITAN   | TITAN     <- the corrupted row
```
The dedup keeps a row when `nse_symbol IS NOT NULL OR ticker NOT IN (…nse_symbols…)`. `TI01`
has a NULL `nse_symbol` and a ticker that *is* someone's `nse_symbol`, so **the real Titan is
dropped from every screen**; `IAG01` survives and renders as `TITAN`. Live output from a
`market_cap > 50,000 Cr` screen:

```
| TITAN | IAG Company | ₹3,92,065 Cr |
```
The cap is Titan's (enrich resolved `IAG01`'s ticker `TITAN` → *Titan Company Limited*,
₹3,92,065 Cr, PE 77.19). `IAG01` has **zero** recent `statement_lines` rows, so this
Frankenstein row appears in pure size screens and silently vanishes from any screen that also
touches a fundamental metric.

The dedup itself is otherwise doing real work — it drops **789** impostor rows — and is the
right idea. It just has no tiebreak for the case where the impostor is the one holding the
canonical `nse_symbol`.

---

## F7 — CONFIRMED · HIGH: every "silently dropped" disclosure is written to `note`, and `note` is never rendered

**File:** `render_screen_markdown` lines **1974–1980** — the note is deliberately not printed
(*"the italic footer read as noise to users (removed on request 2026-07-17)"*), except the one
"include small caps" line. `chat_service.py:8686–8700` makes that render **the entire reply**
on any single-`screen_fundamentals` turn, skipping the narration hop that would otherwise have
seen the note.

Complete enumeration of drop sites and what the caller actually learns:

| # | drop | line | in `note`? | in `applied_filters`? | user sees? |
|---|---|---|---|---|---|
| 1 | unknown/unmapped `field` | 1031 | yes | no (absent) | **no** |
| 2 | op not in `< <= > >= =` | 1034 | yes | no | **no** |
| 3 | `kind == "unsupported"` | 1038 | yes | no | **no** |
| 4 | unknown `value_field` | 1047 | yes | no | **no** |
| 5 | non-numeric `value` | 1054 | yes | no | **no** |
| 6 | `pe` compared to a value ≤ 0 | 1485 | yes | **filter still listed** | **no** |
| 7 | custom ratio that doesn't resolve | 1009 | yes | n/a | **no** |
| 8 | custom ratio name clashing a built-in | 1015 | yes | n/a | **no** |
| 9 | unknown `sector` → **filter vanishes** | 1514 | yes | no field for it | **no** |
| 10 | `sort_by` field unsortable | 1073 | yes | `sorted_by` shows the substitute | **no** |
| 11 | enrich down → `market_cap` filter dropped **and sort silently switched to `roe`** | 1163–1166 | partially (says "cap constraint skipped", never mentions the ROE re-sort) | filter removed | **no** |
| 12 | `exclude` carve-outs removed rows | 913 | yes | n/a | **no** |
| 13 | recency floor / basis fallback / plausibility bounds | 1618, 1694, 1695 | yes | n/a | **no** |

Live proof, four of these (the "USER SEES" block is literal `render_screen_markdown` output):

```
### sector='defence'
  applied_filters: [{'field':'roe','op':'>','value':20.0}]
  note: "...unknown sector 'defence' (known: auto, autoancillary, ...) — sector filter ignored..."
  --- USER SEES ---
  Filters applied: ROE > 20
  | Rank | Company | Market cap | ROE | Sector | 1-Year Return |
  | 1 | Raymond (`RAYMOND`) | ₹3,903 Cr | 187.80% | — | +0.3% |
  | 2 | Padmanabh Ind (`PADMAIND`) | ₹6 Cr | 125.09% | Chemicals | +2.6% |
```
A user who asked for **defence stocks** is handed Raymond and a ₹6 Cr chemicals shell, under a
header that confidently states which filters were applied. Same for `field='dividend_yield'`,
`op='between'`, `value='20%'`, and a bad `custom_ratios` entry — all four produce a clean,
plausible table with the dropped constraint simply absent from the "Filters applied:" line.

**Correction to the brief:** an unrecognised sector is *not* silently ignored at the function
boundary — line 1514 does record it, and the model-facing JSON schema
(`agents/tools.py:866-870`) constrains `sector` to the 12 valid values, so an LLM caller
rarely triggers it. The silence is in the **rendering**, and it applies to all thirteen drop
paths, not just sector. Non-LLM callers (`strategy_builder`) have no such enum guard.

Also confirmed: `sector="auto ancillary"` — **the exact label this module itself emits** for
those companies (line 762, `_SLUG_SECTOR_RULES` → `"auto ancillary"` with a space) — is not a
key of `_SECTOR_SLUG_PREFIXES` (which uses `"autoancillary"`). Passing back the label the
screen printed drops the filter.

---

## F8 — CONFIRMED · MEDIUM: enrich data is a single 2026-06-19 snapshot, described as "live" and "current"

```sql
select count(*), min(fetched_at), max(fetched_at),
       count(*) filter (where fetch_status='ok') from enrich.company_profile;
-- 11256 | 2026-06-19 14:57:26+00 | 2026-06-19 20:47:34+00 | 5800
select date_trunc('day',fetched_at)::date, count(*) from enrich.company_profile group by 1;
-- 2026-06-19 | 11256      (one day. that's the whole table.)
```

**48 days stale as of 2026-08-06**, and it is a one-shot scrape, not a refreshing feed. What
depends on it:

- `market_cap` — the **default sort** when no `sort_by` is given (1081, 1097), the cap-tier
  **filter** (1531), the bare-sector **floor** (1554), and a context column on every row.
- `pe` — the **preferred** P/E, used for both filtering and display (1455, 1489).
- `one_year_pct` — the 1-year-return column.

All three are price-derived and move daily. The code says otherwise:
- line 89: *"Real trailing P/E (**live price** ÷ TTM EPS)"*
- line 1491–1493, injected into `note`: *"P/E is trailing (**live price** ÷ TTM EPS) where available"*
- line 1826, the rank framing shown to users: *"Ranked by market cap (₹ crore, **current**)"*
- the 1-hour cache TTLs (`_MCAP_TTL_S`, `_PE_TTL_S`, `_YR1_TTL_S`) re-read a table that has not
  changed in seven weeks.

Only `_load_52w_change`'s note says "may lag" (1198). Market cap and P/E carry **no** staleness
disclosure at all — and per F7 even that one note is never shown.

---

## F9 — CONFIRMED · MEDIUM: sector derivation covers 42% of the universe and misses a third of IT

**File:** `_SLUG_SECTOR_RULES` **754–770**, `_SECTOR_SLUG_PREFIXES` **775–793**,
`_sector_for_slug` **846–852**.

Over the 5,141 active, deduped companies (**1,097 distinct `industry_slug` values**, 12 buckets):

```
None            2962  57.6%     finance   418  8.1%     textiles 306  6.0%
infra            284   5.5%     pharma    257  5.0%     metal    208  4.0%
chemicals        175   3.4%     it        173  3.4%     fmcg     119  2.3%
auto ancillary   102   2.0%     energy     68  1.3%     bank      43  0.8%
auto              26   0.5%
```

Unmapped slugs (`sector` renders as `—`) that plainly belong to a supported bucket:

| bucket | missed slugs (n) | screen returns | should be ~ |
|---|---|---|---|
| `it` | `itservicesconsulting` (47), `software` (40) | 173 | ≥260 (**-33%**) |
| `chemicals` | `dyespigments` (28), `specialitychemicals` (19), `petrochemicals` (17) | 175 | ~239 |
| `fmcg` | `sugar` (43), `consumerfood` (36), `plantationsteacoffee` (26), `edibleoilssolventextraction` (21) | 119 | ~245 |
| `metal` | `ironsteel` (31) | 208 | ~239 |
| `infra` | `engineeringconstruction` (23), `ceramicsgranite` (23) | 284 | ~330 |
| `autoancillary` | `tyres` (15) | 102 | ~117 |

The rules are prefix-anchored (`^steel` misses `ironsteel`; `^computerssoftware|^itconsulting`
misses `itservicesconsulting` and `software`). Also note `auto` = **26 names** total —
"the automobile sector" is 26 companies, which will silently exclude OEMs whose slug the
scraper mangled (the module's own comment at line 253 flags Tata Motors sitting under
`tatamotorscom`).

The docstring's claim that sector is derived from `industry_slug` is **correct**;
`mc.companies.sector` and `.market_cap` are indeed 100% NULL (verified: `sector_pop=0`,
`mcap_pop=0`, `slug_pop=11256`).

---

## F10 — CONFIRMED · MEDIUM: custom ratios mix periods and bases across numerator and denominator

**File:** lines **1402–1423**. The numerator and denominator are two independent
`DISTINCT ON (sc_id)` subqueries, each free to land on its own `period_end` and `basis`.

Reproducing the tool description's own worked example (`debt_ebitda` = `total_debt` /
`operating_profit`):
```
pairs produced                       : 5727
numerator/denominator DIFFERENT year : 1921  (33.5%)
numerator/denominator DIFFERENT basis:  160
```

**Consequence.** One in three user-defined ratios is FY-N debt over FY-N-1 (or FY-N-2)
earnings. Combined with F3, the tool description's headline example "debt/EBITDA" is actually
*mixed-year debt ÷ mixed-year profit-before-tax*. Nothing discloses it. The built-in `peg`
kind (1312–1395) does the alignment correctly — it dedupes to one row per
`(sc_id, basis, period_end)` before pairing — so the pattern for doing this right already
exists ten lines away.

---

## F11 — CONFIRMED · MEDIUM: `exclude` over-matches by substring and runs after `LIMIT`

**File:** `_matches_exclude_term` **871–894**, `_apply_exclude` **897–915**.

```
exclude='auto'   keeps ['TITAN','ITC']         <- drops Bosch + Motherson (sector "auto ancillary"
                                                   contains "auto" via `t in sec`, line 892)
exclude='it'     keeps ['MARUTI','BOSCHLTD','MOTHERSON']
                                               <- drops TITAN ("it" in "titan", line 884) and ITC
```
`_apply_exclude` is called on `result["results"]`, which is already `LIMIT`-ed (1721, 1138), so
"top 10 excluding Adani" returns 7 rows rather than back-filling to 10. The drop **is** written
to `note` (913) — and per F7, never displayed.

---

## F12 — CONFIRMED · LOW/MEDIUM: `fetch_gate_inputs`' "byte-identical" contract is false in three ways

**File:** docstring **2004–2008**: *"same DB CTEs: latest-per-sc_id, consolidated basis
preferred, **same recency floor**, same P/E = 1/EarningsYield derivation, **same data-quality
bounds** … a pure I/O batching change — the gate decision is unchanged."*

1. **Different floor.** `fetch_gate_inputs` uses `_default_min_period_end()` = 2 years
   (`2024-01-01`); `screen_by_fundamentals` uses `_screen_min_period_end()` = 3 years
   (`2023-01-01`). Verified live.
2. **Different P/E.** The gate computes only `1/EY` (2096); the screen prefers the enrich
   trailing P/E (1455). Same names, materially different numbers:
   ```
   gate  RELIANCE pe=25.0000   |  screen RELIANCE pe=21.95
   gate  TCS      pe=16.6667   |  screen TCS      pe=15.61
   gate  INFY     pe=16.6667   |  screen INFY     pe=13.83
   ```
   The quantized grid (16.6667, 25.0, 33.3333) is exactly the artifact the enrich P/E was
   introduced to fix — the strategy builder's selection gate still runs on it.
3. **No plausibility bounds.** `_PLAUSIBLE` (1571–1597) is applied only in
   `screen_by_fundamentals`. The gate has none, so an ROE-666% shell passes the gate but could
   never appear in the screen.

---

## F13 — CONFIRMED · LOW: docstring "Environment reality (audited 2026-05)" is now wrong in three places

| docstring claim | line | reality |
|---|---|---|
| *"mc.companies.nse_symbol is populated on only **~10** of 11,256 rows"* | 34 | **5,114** rows. `count(nse_symbol)=5114`, `count(ticker)=3020`. The whole "display symbol falls back to ticker" rationale is built on a number that is off by 500×. |
| *"a `market_cap` filter cannot be served from this DB — it is reported in the result `note` and skipped"* | 36–38 | Superseded 2026-07-11: `market_cap` is a real enrich-backed field (`_FIELD_DEFS` 528–540). The module header still tells a reader the opposite. |
| *"mc.companies.sector and .market_cap are 100% NULL"* | 32 | **Correct** — verified. |

Also: `WHERE c.is_active` (line **1463**) is a **no-op** — `count(*) filter (where is_active)`
= 11,256 of 11,256, and `delisted_on` is populated on zero rows. Delisted/merged shells
(HDFC Ltd, Lakshmi Vilas Bank) are `is_active=true` and are excluded only by the recency floor
— HDFC Ltd, merged into HDFC Bank in 2023, is still in the ₹1L Cr+ screen output above.
`_PCT_UNIT_FIELDS` (line **673**) is declared and never referenced anywhere in the codebase.

---

## F14 — CONFIRMED · LOW: silent truncation by plausibility bounds

**File:** `_PLAUSIBLE` **1571–1597**, applied to every metric in play (1616–1617) whether or not
the user filtered on it.

- `de: BETWEEN 0 AND 50` drops every **negative** D/E. `Total Debt/Equity (X)` p05 = **-0.74**,
  so negative-net-worth companies vanish from a "lowest debt" screen without appearing as
  either excluded or zero.
- `payout: BETWEEN 0 AND 100` drops every company paying out more than it earned — a real and
  common state for a "highest dividend payout" screen, which is exactly the screen that asks
  for it.
- Growth `BETWEEN -100 AND 300` (1610) drops genuine 4× growers alongside base-effect shells.
- The blanket note *"data-quality bounds applied (extreme outliers excluded)"* (1618) is
  appended unconditionally and, per F7, is never shown.

Scale check on the bounds themselves — they are correctly calibrated to the DB's units
(`Asset Turnover Ratio (%)` p50 = 0.69 despite the "%" in its name, so `BETWEEN 0 AND 50` is
right; `Interest Coverage Ratios (%)` p50 = 6.19, p95 = 187, so `-100..2000` is right).

---

## VERIFIED-CORRECT (things I tried to break and could not)

- **No join can fan out or misalign a row.** Every metric CTE is `DISTINCT ON (sc_id)`; `caps`,
  `pe_real` and `yr1` are `unnest` of Python dicts (keys unique by construction);
  `growth_metrics_mat` is one row per `(metric, gy, sc_id)` — verified 7,283 rows / 7,283
  distinct sc_ids. `.mappings()` (1654) makes column access alias-based, so the prepended
  `caps` CTE cannot shift positional indices. **The GVPIL/SANOFI anomaly is not here.**
- **The `peg` kind is genuinely correct**: `1/EY ÷ YoY-EPS-growth`, dedup'd to one row per
  `(sc_id, basis, period_end)` *before* pairing periods (1347), guarded to positive growth and
  positive P/E, capped at 50. It is the best-built metric in the file.
- **The EY→P/E inversion** (`_OP_INVERT`, 746) is never actually used in the WHERE clause —
  1480–1489 filters on the *displayed* P/E value directly, which is the safer behaviour and
  guarantees filter and display agree at the boundary. `_OP_INVERT` is now dead but harmless.
- **`growth_metrics_mat` is fresh and complete**: `refreshed_at` current, `gy` shards 1–5
  populated (6,678–7,283 sc_ids), `max(latest_end)=2026-03-31`. `gy>5` correctly falls open to
  the live CTE (`mat_shard_fresh('revenue_growth',7) = False`).
- **`line_item` strings never span two `statement` values** for any field in `_FIELD_DEFS` —
  the CTEs' omission of a `statement` filter is safe today (though it is an unguarded
  assumption, not an enforced invariant).
- **Units are consistent**: absolutes are `Rs. Cr.` throughout `balance_sheet`/`profit_loss`
  (146k rows `Rs. Cr.` vs 14 rows `rsCr`), matching the `"cr"` unit tags.
- **The enrich sector path is good.** `screen_from_enrich` dedups by `UPPER(ticker)`, so the
  contaminated rows collapse into one self-consistent row (right name + right numbers).
  `sector="bank", sort pe asc` returns Canara / BoI / Central Bank / PNB / BoB — correct,
  recognisable, real P/Es. It is the `mc` path that needs the work.
- **The impostor dedup earns its place**: 789 rows dropped, and the RELIANCE collision it was
  written for is genuinely fixed.

---

## COULD NOT DETERMINE

- **Whether the enrich `ticker` corruption is repairable in place.** The corruption is in
  `mc.companies.ticker` (the scraper's column) and was propagated into `enrich.company_profile`
  at scrape time. Both are scraper-owned and I was read-only, so I could not test whether
  re-resolving by `company_name` (per the standing "match by NAME, never ticker" rule) recovers
  the 109 mismatches — only that `long_name` in enrich is usually the *yfinance* name, which is
  the wrong side of the join to repair from.
- **The true magnitude of the duplicate-cap problem.** 1,553 sc_ids share a cap value with
  another sc_id, but I could only prove *identity mismatch* for the 109 where `mc.nse_symbol`
  is non-null and differs from `enrich.ticker`. The remaining ~1,400 may be contaminated,
  may be legitimate dual-series listings (`-RE`, `-SM` suffixes), or may be scrapes of the same
  ticker under two sc_ids. Resolving that needs a name-similarity pass I did not run.
- **Whether `roic`/`gross_margin`/`receivables_turnover` were ever intentionally gated off.**
  The `pivot_derived` exclusion at line 1444 predates the extended ratio set (whose comment at
  611 assumes the backfill is readable), so this looks like two changes that never met — but I
  found no commit or comment stating an intent to exclude derived ratios, so I cannot say
  whether widening the predicate is a fix or a policy change.
- **`interest_coverage` semantics.** MC labels it `(%)` but p50 = 6.19 and p95 = 187, which
  reads as a multiple, not a percent. Without MC's own definition I could not confirm whether
  the `-100..2000` bound and the "Interest Cover" label are describing the same quantity.
- **Live end-to-end user impact.** I exercised `screen_by_fundamentals` and
  `render_screen_markdown` directly and read the `chat_service` call site, but did not run a
  live chat turn, so I cannot state what fraction of real screen turns hit the deterministic
  renderer versus the narration hop (the gate at 8686–8697 excludes construction intents and
  multi-tool turns).
