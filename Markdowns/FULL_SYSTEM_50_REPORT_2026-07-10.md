# Full-system 51-prompt evaluation — 2026-07-10
_All systems, easy→hard: reads, analysis, comparisons, fundamentals-history, screeners, F&O, backtests, agents, constructions, lifecycle, calc, stupid/integrity, Hinglish, ambiguous. Post-chat-kernel-rebuild build. Snapshot `tests/eval_results/full_system_50/run_20260710_140153.json`; per-prompt verdicts in `JUDGE_REPORT_FULL50_2026-07-10.md`._

## Headline
- **Quality: 33/51 PASS (64.7%), 7 PARTIAL, 11 FAIL — mean 7.59/10**
- **Latency: median 8.6s | mean 11.9s (one 100.7s cold-start outlier; ex-outlier mean ~10.1s) | p90 18.5s | p95 21.8s**
- **Tokens/prompt: mean 65,254 in / 640 out (median 76,676 / 460)**
- **Cost: $0.0141/prompt, $0.72 total | 2.82 LLM calls/prompt**

## Where the rebuild shows (9-10/10 clusters)
agents 9.2 avg (easy 9.5 / med 9.67 / hard 8.33) · constructions-med 9.5 ·
comparisons 9.0 · Hinglish 9.5 · integrity/boundary/edge 9.6 · calc 10 ·
backtest-easy 10 · ambiguous (bare Tata → 7 candidates) 10.
Gated cases all held: notify-only alerts (zero order steps in both drafts),
one-time-at-open has valid_until+expires (not a silent daily), the 3-symbol
basket agent fans all three, TSLA declined with MON100 proxy, guaranteed-50%
refused without a pitch.

## Failure clusters (the next work order, ranked)
1. **P0 — `list_agents` FABRICATED 3 agents with tools_called=[]** — a
   never-fabricate violation on a read intent; `portfolio_summary` clarified
   instead of reading. Fix: gate lifecycle/portfolio intents on a mandatory
   tool call (structural, not prompt).
2. **Clarify-when-unnecessary reflex** — roe_series / portfolio_summary /
   longterm_portfolio / astro_pick asked instead of doing (core args present).
3. **DSL translator + backtester brittleness** — dow_backtest leaks "AND with
   only one item"; seeded 1400-cost-basis backtest refused initial_position;
   compound backtest 0-trades with no per-leg diagnostic; critique refused
   rule-based math without live premiums.
4. **Options mock feed ~700pts off live spot, not tagged in strategy cards**
   (chain says 23,456 while NIFTY prints 24,186 in the same run). Add a MOCK
   banner + refuse strategy builds when spot diverges >0.5%.
5. **Analysis routing regressions** — analyse_itc mis-picked
   compare_performance then bailed; sector_outlook returned a bare ROE table
   with no view. (These passed in the construction-suite phrasing — the
   looser phrasings here escape the ANALYSIS reply-class regex.)

## Per-category table (n · verdicts · mean quality · median latency · median in-tok)
| cat | n | P/PA/F | q | lat | in |
|---|--:|---|--:|--:|--:|
| read | 5 | 4/0/1 | 7.0 | 8.4s | 76.9K |
| analysis | 4 | 2/0/2 | 6.0 | 9.9s | 59.4K |
| compare | 3 | 3/0/0 | 9.0 | 8.9s | 79.0K |
| fin-history | 4 | 3/0/1 | 7.3 | 9.3s | 99.3K |
| screen | 3 | 3/0/0 | 8.0 | 19.0s | 40.7K |
| fno | 4 | 1/3/1* | 5.1 | 6.6s | 76.5K |
| backtest | 5 | 2/1/2 | 6.4 | 15.7s | 86.3K |
| agent | 8 | 7/1/0 | 9.2 | 8.5s | 41.9K |
| construct | 4 | 3/1/0 | 8.0 | 17.0s | 81.1K |
| lifecycle | 2 | 0/0/2 | 2.5 | 10.5s | 79.0K |
| calc | 1 | 1/0/0 | 10 | 14.5s | 81.2K |
| stupid | 5 | 4/1/0 | 9.7 | 5.9s | 40.7K |
| hinglish | 2 | 2/0/0 | 9.5 | 7.3s | 60.6K |
| ambiguous | 1 | 1/0/0 | 10 | 4.1s | 35.2K |

*fno verdicts constrained by the stale mock feed, not routing.

## Ops incident (recorded)
Azure PG ran out of connection slots mid-first-attempt (dev-session
accumulation: server pool + scheduler + pytest + background tasks). Server
restart freed them. ADD TO BETA P0s: pool_size/max_overflow review + a
connection-count alarm.

## Fix round (same day, commit aecbe54) — retested once each
| finding | status |
|---|---|
| P0 list_agents fabrication | **FIXED** — "None are running right now" via forced manage_automation |
| portfolio_summary over-clarify | **FIXED** — real numbers via forced get_portfolio |
| roe_series clarify-reflex | **FIXED** — direct 5-year series |
| analyse_itc mispick | **FIXED** — 4-tool sectioned ANALYSIS |
| longterm_portfolio clarify | **FIXED** — builds the ₹5L basket directly |
| sector_outlook bare table | **FIXED** — outlook asks skip the deterministic render; defended stance delivered |
| compound_backtest no diagnostic | **FIXED** — 0-trades explained + looser-variant offer |
| nifty_chain / build_condor mock spot | **FIXED** — mock warning now LEADS both replies (root cause: stale_note appended past the 6000-char tool-result truncation; fields now prepended + readback honesty exception) |
| critique_strangle | **IMPROVED** — honest feed-miss + qualitative offer (was flat refusal) |
| price_simple "check the ticker" | **DATA ISSUE** — TATAMOTORS genuinely unavailable on the fallback feed (post-demerger rename); feed-honest phrasing shipped; symbol-catalog refresh needed |
| dow_backtest / seeded_holding_bt | **KNOWN ISSUE** — AND-collapse + arg-repair shipped (raw errors no longer leak) but the DSL translator fails deeper on these phrasings; the translator workstream is attended-session work |
