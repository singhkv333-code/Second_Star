# Retail chat strengthening — 2026-05-29

Session goal (user redirect): stop polishing exotic strategy prompts; make
the backend concretely strong at the **real retail prompt categories** and
eval them heavily. Branch `Eventtriggers`. Commits local — **not pushed**.

## Method
1. Root-caused the 10 UI-screenshot failures via live reproduction
   (workflow `wxrybt5tb`) → fixed 9 clusters → commit `7a0eb50`.
2. Built a strict multi-turn judge (`scripts/multi_turn_judge_eval.py`)
   that asserts conversation TRUTH (context retained / executed-not-looped /
   symbol resolved / numbers nonzero / no fabrication) + the quality triad.
   The prior ~87% PASS was illusory — it was substring/tool-name matching.
3. Audited each retail category live (workflow `w1ka32nqu`), built 3 new
   modules (workflow `wxbc9zfql`), integrated, then ran a 45-prompt live
   eval (workflow `wt2p6ir3n`).

## Eval result (45 prompts, Hinglish + English)
**31 PASS / 12 PARTIAL / 2 FAIL — zero fabrications.** Both FAILs fixed +
retested green (commit `de730d1`). Latency p50 ~11.6s, p95 ~85s (cold-path
outliers; tool-heavy turns add hops).

| Category | P/~/F |
|---|---|
| Comparison | 5/1/0 |  Screening | 5/2/0 |  IPO | 5/0/0 |
| Oil/MCX decline | 3/0/0 |  RBI events (chat) | 3/1/0 |
| SIP/gold/silver/GTT | 4/1/0* |  Analysis | 2/4/0 |  Dip+profit | 1/2/0 |  Context regression | 3/1/0* |
(*the two FAILs in these were fixed post-eval.)

## What shipped
- **Correctness:** killed the fabricated annualised CAGR (`returns.py` —
  years from calendar span) + the yfinance weekly-downsampling bug
  (`fetch_price_history` respects caller interval). 1y now 249 daily pts.
- **New tools** (`tools.py`+`tool_executor.py`+`tool_router.py`+
  `tool_registry._REAL_TOOLS`+`system.md`): `screen_fundamentals`
  (`services/fundamentals_screen.py`), `fetch_fundamentals`+
  `get_symbol_news` (`services/analysis_chat_tools.py`),
  `list_upcoming_ipos`+`get_ipo_details` (`services/ipo_feed.py`).
- **Routing/behaviour:** removed stale "screening isn't wired" deflection
  (examples + fallback); "JUST DO IT for reads" rule (no pre-clarify);
  compare_performance forced for A-vs-B; silver→SILVERBEES; create_sip
  surfaced for "invest…every month"; screen artifact bounds.
- The 9 original screenshot clusters (context retention, index symbols,
  multi-symbol baskets, dip semantics, confirmation loops) — see `7a0eb50`.

## Round 2 (2026-05-29 PM) — PARTIALs closed + RBI seam built
Workflow `w76u0pxnt` returned tested patch specs; applied + verified.
- **RBI event AUTOFIRE — DONE** (`4f7f6bf`): `scheduler.py` poll now scans
  `trigger.event` (4 existing types byte-for-byte unchanged) →
  `_evaluate_event_trigger` fetches the LIVE RBI press-release RSS
  (`news_events` RSSAdapter; works with `NEWSAPI_KEY` empty) and fires via
  the existing `fire_external_event`. **Specificity guard**: a bare org
  token ("RBI") never fires; a policy keyword (repo rate/MPC/rate cut) must
  hit. Verified live: 0/10 false-fires on today's feed (money-market/penalty/
  VRR/annual-report), fires on a synthetic rate-cut headline. Per-step guid
  dedup. `events_calendar` event_type now derived from keywords.
- **Vague screens — DONE** (`974a5e3`): sort-only screens ("cheap banking"
  → CANBK/FEDERALBNK/AXISBANK by P/E; "best dividend" → payout desc). Bank
  ROE/PE label variants added to FIELD_MAP (banks were returning 0).
- **3y/18mo windows — DONE** (`4f7f6bf`+`5a60cb6`): get_ohlcv slices any
  N-day/week/month/year span exactly; prompt passes the span verbatim.
  Verified live: "compare over 3 years" returns a real 3y window.
- **`get_index_level` — DONE** (`4f7f6bf`): yfinance ^-ticker fallback;
  "why is nifty down" returns a real level + grounds with top movers.
- **find_tool hop / dip drafting — DONE**: fundamentals routing broadened
  ("reliance PE"); simple dip drafts directly; compound dip+profit guidance
  added (profit = entry-relative).

## Still remaining (documented)
1. **Latency on the dip / DSL-translation path** (~40–75s): propose_dsl/
   propose_workflow with a dip + take-profit runs multiple LLM condition
   translations. Functional but slow; worth caching/trimming hops.
2. **Off-market-hours autofire**: the watcher returns early outside market
   hours, so RBI events landing after close fire at next open — acceptable
   for v1, note for later. NewsAPI path remains keyless (RSS is the live one).
3. **Data limits:** fundamentals DB sparse outside large caps (TCS/INFY
   return only EPS); screen names skew small-cap (no market_cap in DB);
   IPO feed live but currently 0 issues (genuine); analysis sector-outlook
   answers still lean on prose when no single ticker is named.
4. **Azure transient errors** under heavy parallel load ("temporarily
   unavailable") — observed only during back-to-back eval bursts, not normal use.

## How to verify
Backend: `cd pivot && .venv/bin/python -m uvicorn backend.main:app --port 8000`.
Strict judge: `.venv/bin/python scripts/multi_turn_judge_eval.py`.
