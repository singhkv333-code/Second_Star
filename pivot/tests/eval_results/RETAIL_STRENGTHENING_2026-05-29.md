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

## Remaining (documented, NOT done)
1. **RBI event AUTOFIRE** — chat drafts good `trigger.event` agents, but the
   scheduler (`workflows/scheduler.py:_poll_watch_triggers`) doesn't scan
   `trigger.event`, and detection needs the RBI RSS pipeline
   (`news_events/`, `NEWSAPI_KEY` empty) wired to fire. Deliberately
   deferred — scheduler sub-project, risk to live triggers, not verifiable
   end-to-end without an event. This is the `Eventtriggers` branch's headline TODO.
2. **PARTIALs (real, not broken):** vague screens ("cheap banking", "best
   dividend") gate behind a clarifier instead of a default screen; 3y window
   silently → 2y; sector-outlook answers ungrounded (no tool); occasional
   wasted `find_tool` hop; `get_index_level` returns None for NIFTY50
   (index level data path, separate from the quote path fix).
3. **Data limits:** fundamentals DB sparse outside large caps (TCS/INFY
   return only EPS); screen names skew small-cap (no market_cap in DB);
   IPO feed live but currently 0 issues (genuine).

## How to verify
Backend: `cd pivot && .venv/bin/python -m uvicorn backend.main:app --port 8000`.
Strict judge: `.venv/bin/python scripts/multi_turn_judge_eval.py`.
