# Quarterly data — what we compute, what we can't yet, and why

Companion to `load_mc_quarterly.py` (raw) and `build_quarterly_metrics.py`
(derived). Written against the real table, not against a wishlist: every "can't"
below names the specific missing input.

---

## 0. What we actually hold

| | |
|---|---|
| `quarterly_statement_lines` | 6.64M rows, 3,800 companies, Jun-1996 → Jun-2026, 249 distinct period-ends |
| `mc.statement_lines` (existing) | 18.3M rows, **annual only**, FY2000 → FY2026 |
| `bars_1d` / archive | daily + 1-min prices |
| `result_filings` | 4,210 NSE filings with `broadcast_at` + `xbrl_url` (41 symbols — partial) |

The quarterly table is **P&L only**. There is no quarterly balance sheet and no
quarterly cash flow in this feed. That single fact determines most of §3.

---

## 1. Implemented in `quarterly_metrics`

**Levels** — revenue, total income, other income, EBIT, EBITDA, depreciation,
interest, employee cost, raw material, other expenses, provisions, exceptional,
PBT, tax, net profit, basic/diluted EPS, equity capital.

**Margins** — operating, EBITDA, net, PBT, effective tax rate.

**Growth** — revenue/net-profit/EBITDA **YoY**, revenue/net-profit **QoQ**,
**TTM** revenue/net-profit/EPS, **TTM YoY**, margin change in **bps** YoY.

**Quality** — interest coverage, other-income share of PBT, employee-cost and
raw-material intensity.

**Banking** — Gross/Net NPA %, Return on Assets %.

### Three correctness rules baked in

1. **YoY joins on a quarter index, never `lag(4)`.** ~10% of companies have gaps
   or non-March fiscal years; a positional lag compares Jun-24 to Dec-22 for
   them and never tells you.
2. **Growth off a base ≤ 0 is NULL, not a number.** A company swinging from
   −10 to +5 has no meaningful "growth %"; emitting one poisons every average
   that touches it.
3. **TTM requires all 4 quarters.** A 3-quarter sum understates ~25% and is
   invisible downstream, so it is refused rather than approximated.

---

## 2. Cheap additions — inputs already on hand

| Metric | Needs | Note |
|---|---|---|
| **Growth acceleration** (ΔYoY) | nothing | YoY(t) − YoY(t−1). The single best "is the story changing" signal |
| **Growth volatility** (σ of YoY, 8q) | nothing | separates steady compounders from lumpy ones |
| **Margin trend slope** (8q regression) | nothing | direction beats level for re-rating |
| **Seasonality index** | nothing | each quarter's share of TTM; required before *any* QoQ reading |
| **Earnings consistency** streak | nothing | consecutive quarters of YoY growth |
| **P/E TTM, P/S TTM** | `bars_1d` | join TTM EPS to price |
| **Sector-relative growth / percentile rank** | `company_identity` sector | absolute growth is nearly meaningless cross-sector |
| **Annual reconciliation flag** | `mc.statement_lines` | per-company Σ4Q ÷ annual; a permanent data-trust column |

---

## 3. Blocked, with the exact blocker

**Needs a quarterly balance sheet** (not in this feed):
ROE, ROCE, ROIC, Debt/Equity, Net debt/EBITDA, book value per share, P/B,
working-capital cycle, Altman Z. → `appfeeds .../balance_sheet` returned 41 KB
and is the obvious next pull; verify its periodicity first, since MC's *annual*
endpoint was also misleadingly named.

**Needs a quarterly cash flow**: FCF, cash conversion, accruals ratio,
Piotroski F-score (4 of 9 components). Not available anywhere yet.

**Needs an availability date**: any event study, PEAD, earnings-day drift.
`period_end` is a **period**, not a date the market knew the number — using it
as an event date is look-ahead. `result_filings.broadcast_at` is the right
input, but it covers 41 symbols today; the crawl must finish first.

**Needs consensus estimates**: true earnings surprise (actual vs street).
We have none, and no free Indian source. A trend-based "surprise vs own
4-quarter trend" is computable and useful — but it must **never** be labelled
or presented as a beat/miss versus analyst expectations. Different thing.

**Needs a share count**: rigorous per-share work. Summing 4 quarters of EPS is
standard practice but drifts whenever share count changes mid-year.

---

## 4. Traps a query will hit

- **`depreciat`** — the standard template's depreciation key is truncated.
  `WHERE line_item='Depreciation'` silently returns nothing for every
  non-financial company. `quarterly_metrics` reads both spellings.
- **Banks are a different sheet.** No `Net Sales` row at all. In
  `quarterly_metrics`, bank revenue maps to *Interest Earned* — the closest
  analogue, **not** the same concept. Filter on `template` before pooling.
- **`section` is positional, not semantic.** MC opens blocks and never closes
  them. Group on `line_item`.
- **`unit='rs_crore'` does not apply to every row.** EPS and % rows are not in
  crore. 13 rows carry absurd upstream magnitudes (`Share Holding (%)` at 33.4M)
  — filter by `line_item`, never scale blindly.
- **249 distinct period-ends, not ~120.** Non-March fiscal years are common;
  never assume a calendar-quarter grid when aligning companies.
- **`isin_state`** — filter to `verified` for anything analytical. `mismatch`
  (61 companies) is kept deliberately so it is visible, not silently dropped.

---

## 5. Suggested build order

1. Annual reconciliation flag → a permanent trust column, and it audits §1.
2. Acceleration, volatility, seasonality → free, and the highest analytical
   value per line of code.
3. Sector-relative ranks → makes any screen actually meaningful.
4. Balance-sheet pull → unlocks the whole ROE/leverage family at once.
5. Finish `result_filings` → unlocks every event study, and is the only way to
   avoid look-ahead.
