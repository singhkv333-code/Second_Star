# GAN Round 3 — Judge Report (synthesized from verified discriminator findings)

Date: 2026-06-10 · Baseline runs: `baseline/run_20260610_022014.json` (canonical) + `baseline_probes/run_20260610_022804.json` (probes)
Condition note: daily Kite token expired during runs → live prices fell back to yfinance EOD with honest source tags on BOTH baseline and after; honest `(yfinance, EOD)` tagging is **correct behaviour**, not a defect. The open gap is the missing **as-of date** on that tag.
Scores below incorporate the discriminator's own verify-notes where a verdict was REFUTED (bajajauto B → PARTIAL 6.4, swap_symbol B → PASS 8.5, screen_then_dont_understand B → PASS 8.8, analyse_hdfcbank B → PASS 8.0, probe_register-then-status B → PARTIAL 7.0).

---

## 1. Headline (CANONICAL sessions only — 28 per angle; probes reported separately in §3)

| Angle | PASS | PARTIAL | FAIL | Mean /10 | Prior (R2 post-fix) |
|---|---|---|---|---|---|
| **A — Execution** | 17 | 7 | 4 | **7.18** | 7.50 |
| **B — Output quality** | 5 | 11 | 12 | **5.53** | 6.50 |

Read: execution held roughly flat on the canonical set (the R3-regression basket-budget and hcltech-theatre items are FIXED; the iron-condor ask_user collapse is FIXED post-d621699). Output quality is the bottleneck — **F&O B is 0-for-4** (table mandate unmet for the third round) and edge-honesty is the worst bucket overall.

## 2. Per-category (canonical)

| Category | A: P/Pa/F | A mean | B: P/Pa/F | B mean |
|---|---|---|---|---|
| F&O | 2/1/1 | 6.75 | 0/0/4 | 4.00 |
| ambiguous | 0/1/1 | 5.00 | 0/1/1 | 5.00 |
| edge-honesty | 0/2/0 | 5.00 | 0/0/2 | 4.00 |
| execution-stress | 6/0/0 | 8.83 | 0/5/1 | 5.40 |
| multi-turn | 3/1/2 | 5.83 | 2/1/3 | 5.38 |
| quality-stress | 4/2/0 | 7.83 | 2/3/1 | 6.83 |
| regression | 2/0/0 | 9.50 | 1/1/0 | 7.50 |

## 3. Probe outcomes (14 per angle, reported separately)

| Probe | A | B | Class |
|---|---|---|---|
| probe_iv-rank-premium-timing | PARTIAL 6 | PARTIAL 5 | capability-gap (no IV history; over-claims multi-month comparison) |
| probe_roll-losing-short-call | FAIL 4 | FAIL 2 | capability-gap (no roll path; double ask_user, card-less) |
| probe_fuzzy-descriptor-resolution | PARTIAL 6 | PARTIAL 5 | routing-bug (t0 recovery amnesia; t1 resolution WORKS — qty kept, 3% dip fused) |
| probe_unlisted-entity-honesty | PASS 8 | PARTIAL 5 | quality-gap (honest, but muddled prose) |
| probe_mcx-gold-monthly-lot | FAIL 3 | FAIL 4 | routing-bug (monthly→weekday cadence drop via propose_scheduled_order) |
| probe_mf-sip-direct-plan | FAIL 2 | FAIL 2 | routing-bug (FABRICATED ticker + "is set" theatre — worst defect this round) |
| probe_weighted-basket-sector-trigger | FAIL 4 | PARTIAL 5 | routing-bug (t0 ask_user collapse; t1 proves capability exists) |
| probe_scale-out-staged-exit | FAIL 3 | FAIL 2 | routing-bug surface + capability-gap underneath (false "can't anchor to open" claim) |
| probe_register-then-status | FAIL 4 | PARTIAL 7.0 | capability-gap (no chat-side register/activate or status introspection) |
| probe_two-drafts-edit-first | FAIL 2 | FAIL 1 | capability-gap (single active_draft; wrong-target amend + prose/card lie) |
| probe_pe_vs_own_history | PARTIAL 7 | PARTIAL 6 | capability-gap (no 5y P/E series — honest, but proxy delivered one turn late) |
| probe_itc_yield_then_vs_now | PARTIAL 6 | PARTIAL 6 | quality-gap (then-yield computable from fetched series, left "Unavailable") |
| probe_hinglish-price-fastpath | PASS 9 | PARTIAL 7 | quality-gap only (undated EOD tag) |
| probe_weekly-rsi-timeframe | FAIL 3 | FAIL 4 | routing-bug + capability-gap (silent weekly→daily drop, then decorative `timeframe` field = fabricated capability) |

Probe means: A 4.79 (2P/5Pa/7F) · B 4.36 (0P/8Pa/6F). The probes are exactly where the residual classes live; canonical bread-and-butter is largely solid.

## 4. What is FIXED vs R2 (do not re-litigate)

- basket_three_symbol_split — 3-leg ₹20k card, no qty re-ask (R3's worst regression, resolved).
- hcltech GTT execution theatre — now "GTT drafted", not "placed".
- nifty_build_iron_condor — real 4-leg card, no terminal ask_user (d621699 verified).
- screen rank-vs-pick incoherence — rank #1 SBIN now equals the View's pick.
- Hedge guards / strategy-explanation floor (d621699) — protective-put flow holds; no regression observed.
- No silent-guess card was ever built on an ambiguous symbol/unit (rubric #1 holds across all ambiguous sessions).
- Fuzzy descriptor resolution retains qty across ask_user round-trips and fuses new conditions into one card.

## 5. Ranked findings (leverage order)

### P0
1. **Fabricated SIP ticker + execution theatre** — `_create_sip` (backend/agents/tool_executor.py:392-410) does zero instrument-master validation; minted `PARAGPAREKHFLEXICAP` and said "SIP **is set**". Triple auto-FAIL (fabrication + theatre + no boundary). Gap vs gold: gold names the AMC/RTA off-exchange boundary and pre-fills a listed-ETF proxy card. → Track F #4.
2. **Short-put max_loss=None → "unlimited loss" prose** — option_strategies.py:449-456 open-edge slope test fires on a bounded short put; real bound = (strike−premium)×lot ≈ ₹5.59L. Gold rubric hard-zeros "unlimited" on a put. → Track F #1.
3. **F&O card_digest thinness starves the mandated tables** — 0 tables across all 4 canonical F&O sessions for the 2nd round; digest lacks top-3 CE/PE OI strikes, max_pain/PCR, per-candidate econ quads, leg premiums; prose re-derives numbers the digest already holds (band floor 23,148 vs digest 22,888.88). Prompt pressure has failed twice — fix at digest + post-render assertion. → Track F #2.
4. **Fetch-failure recovery is context-amnesiac** (R2 P0, NOT landed) — `_format_recoverable_failure_question` (chat_service.py:6663-6832; call sites 4609/5932) reads only the current message; abandons active draft ("tell me the NSE ticker" mid-resize) and abandons disambiguation candidate sets. → Track F #3.
5. **Disambiguation fetch not enforced** — the_tata_one_entity ran `tools_called=['ASK_USER']` with zero fetches on a "running lately" qualifier; system.md:489-500 is prose-only (and carries a stale TRENT exemplar). Gold hard-zeros comparative selection with no fetch. → Track F #5.
6. **Wrong-target amend + prose/card contradiction** — single active_draft slot makes "the INFY one" mutate WIPRO while prose claims INFY. Needs the mismatch guard now (Track F #16) and multi-draft store (Track C #2).

### P1
7. **Terminal confirm/register turns do no work** — affirm short-circuit (chat_service.py:3267) returns 0-token "click Save & activate"; "register it" re-emits the draft; status answers part-fabricated. Interim readback fix (Track F #15) + register/status tool (Track C #1).
8. **ask_user collapse on fully-specified weighted basket** — probe t0 re-asks alert-vs-buy despite explicit "and buy" + budget + weights; t1 proves `allocate_notional` works. Needs the deterministic basket guard (mirror of the line-2055 notify-only guard). → Track F #6.
9. **False capability denial: 09:30-cron downgrade on open-anchored builds** — `_conversational_unsupported_reply` cue 'the open' (chat_service.py:6284) fires before the open-anchor path that bajajauto proves works; staged-exit ask never honestly addressed. → Track F #7.
10. **Teach-routing regression** — "which indicator and why" re-emits propose_dsl_workflow + size ask_user (R6 fix reverted). → Track F #8.
11. **Boundary rails never ship the pre-filled proxy card** — MON100 SIP, sentiment-autosell full-holding sell, GOLDBEES monthly all dead-end in ask_user; MCX follow-up silently downgrades monthly→every-weekday-09:20 via propose_scheduled_order (tool_router.py:479-486 verb-adjacency regex). → Track F #9.
12. **Weekly qualifier silently dropped, then decorative `timeframe` field claimed as real** — workflow_skeleton `_COMPLEXITY_RE` (line 722) has no timeframe bail; IndicatorNode has no timeframe; scheduler hardcodes '1d'. → Track F #14 (honesty now) + Track C #4 (real weekly).
13. **Multi-leg draft readback is 1-2 lines** — correct ≥3-step cards ship with ~25-55 word prose, no per-leg table, no Changed:/Kept: enumeration (diff contract regressed from R2 exemplar). → Track F #10.
14. **Trajectory-as-snapshot** — then-price fetched but then-yield never computed (itc B FAIL; probe leaves the load-bearing cell "Unavailable"); 1Y return printed "n/a" when derivable. → Track F #11.

### P2
15. **Undated yfinance EOD tags everywhere** (3/4 regression turns, GTT/stop/resolution anchors untagged) — single formatter fix lifts many PARTIALs. → Track F #12.
16. **Own-history valuation frame absent** on "expensive?" asks; honest-bound + SMA200/5y-range proxy should fire in one turn. → Track F #13.
17. **Filler sections + missing composite Score** on screens/index reads. → Track F #13b.
18. **Impossible-reading callout missing** on shares-vs-rupees (₹100 < 1 share offered as viable); resolution cards anchor-less. → Track F #17.
19. **IV "rich vs months" over-claim** on the informational path (guard exists only on automation rail, chat_service.py:1999). → Track F #18 interim; Track C #6 for real IVR/IVP.
20. **Unlisted-entity prose muddle** ("AI exposure" ambiguity). → Track F #19.

## 6. TRACK F — fixes (routing-bug + quality-gap; every chat_service guard MUST be mirrored into `handle()` (~:3107) AND `handle_stream()` (~:4712))

| # | Fix | Files | Sev |
|---|---|---|---|
| F1 | Short-put bounded-loss: floor payoff grid at price=0; max_loss=None only for naked short CALLS; forbid "unlimited" when digest max_loss finite; inject POP + 2-row current-vs-alt table into critique digest | backend/services/option_strategies.py:449-456 | P0 |
| F2 | F&O digest enrichment + table assertions: chain digest += max_pain/pcr_oi/pcr_volume/spot/as_of/source/top-3 CE+PE OI {strike,oi,iv}; suggest digest += per-candidate {max_profit,max_loss,pop,net,breakeven}; build digest += per-leg premiums; post-render assert (chain: metrics+OI-walls tables; suggest: ≥2-row candidate table; build: per-leg table, ≥100w; critique: 2-row table); expected-move band quoted verbatim from digest | backend/market/option_chain.py:331-365, backend/services/option_strategies.py:598-644, backend/services/chat_service.py (assert in handle+handle_stream) | P0 |
| F3 | Context-aware failure recovery: thread active_draft + candidate set into `_format_recoverable_failure_question`; retry draft symbol → last close → keep draft (notional resize with basis named, Hinglish mirror, Changed:/Kept:); disambiguation context falls back to candidate menu, never "tell me the NSE ticker" | backend/services/chat_service.py:6663-6832 + call sites 4609, 5932 | P0 |
| F4 | create_sip fail-closed validation: resolve symbol against live instrument master; MF phrasing guard (flexi cap/direct growth/AMC names) → off-exchange boundary + nearest listed-ETF proxy prefill; add MF row to system.md boundary table; lint "is set" → "drafted — confirm on the card" | backend/agents/tool_executor.py:392-410, backend/prompts/system.md:341-349 | P0 |
| F5 | Disambiguation-fetch hard precondition: order/build intent + ambiguous entity + comparative qualifier → batch get_price_history(1M/3M) on ≤5 candidates BEFORE ASK_USER; menu turn renders ranked table + leader's rupee stakes + escape hatch; honest candidate-naming fallback on fetch failure; strip stale TRENT exemplar | backend/services/chat_service.py (new guard, both paths), backend/prompts/system.md:489-500 | P0 |
| F6 | Basket/build guard: rupee budget + ≥2 symbols + split/across/weight cue + buy verb → force propose_workflow(allocate_notional), strip ASK_USER; widen `_USER_QTY_PATTERNS` to read bare 4-7-digit notional after put/split/across | backend/services/chat_service.py (mirror of :2055 guard, both paths), backend/services/validation_handler.py | P1 |
| F7 | Unsupported-reply cue reorder: 'the open' must NOT fire runtime_relative 09:30-cron text when open entry is buildable (route to trigger.compound basis=open — proven by bajajauto); add staged-exit cue (≥2 partial-qty exits) → honest "not wired; nearest: single exit + stop" offer | backend/services/chat_service.py:6284-6294 | P1 |
| F8 | Teach/confusion guard: post-ask_user "I don't understand / which X and why / explain" → teach reply-class, strip build tools + ASK_USER, correct false premise, ≥120w with worked example using the prior turn's actual numbers | backend/services/chat_service.py (both paths) | P1 |
| F9 | Boundary rails ship pre-filled cards + cadence carry: US-proxy → create_sip(MON100, monthly, day=1, ₹5k editable) + MON100/MAFANG facts table; sentiment-autosell → keyword workflow + full-holding sell default + keyword-vs-price-floor table, soft −5% offer AFTER card; MCX gold → create_sip(GOLDBEES) preserving monthly day-1; carry prior cadence on affirmative/verb-less follow-ups (never propose_scheduled_order for monthly cadence) | backend/services/chat_service.py (~:743 rails), backend/services/tool_router.py:475-486 | P1 |
| F10 | Multi-step draft readback floor: ≥2 legs or ≥3 steps → per-leg/per-step markdown table (alloc, indicative shares at last close, source tag) + how-it-fires + no-exit/one-shot nudge; Changed:/Kept: rendered mechanically from prior-vs-current card_digest on EVERY mutation | backend/services/chat_service.py (reply composition, both paths), backend/prompts/system.md | P1 |
| F11 | Then-vs-now synthesis: trajectory asks → ≥2-timepoint table; then-yield = trailing DPS / then-price from fetched series; never blank-cell a derivable value; compute windowed returns (1Y) from the series, never "n/a — not reported" | backend/services/analysis_chat_tools.py, backend/prompts/system.md | P1 |
| F12 | Dated source tags: stamp last-bar trade date into yfinance live-price payload; templates render "(yfinance, EOD <date>)"; apply to GTT/stop/resolution anchors too; populate logic_card digest with {trigger, limit, side, qty, order_value} | backend/agents/tool_executor.py:1214-1229, reply templates | P2 |
| F13 | Valuation own-history bound + screen polish: "expensive?" → current P/E + "no historical-multiples series" + price-vs-SMA200/5y-range proxy in ONE turn (no ask_user stall); screens get ROE÷P/B Score + per-row Read; suppress empty sections (no "Not applicable" stubs) | backend/services/analysis_chat_tools.py, backend/services/fundamentals_screen.py, backend/prompts/system.md | P2 |
| F14 | Timeframe honesty: add weekly|monthly|hourly|timeframe bail tokens to `_COMPLEXITY_RE`; strip/reject decorative `timeframe` in propose_workflow card builder; honest "RSI evaluates on daily bars only — want daily?"; forbid "updated to weekly" prose until C4 ships | backend/services/workflow_skeleton.py:722, card builder | P1 |
| F15 | Affirm/confirm armed-state readback (interim): replace the 0-token canned line at the affirm short-circuit with click instruction + full readback (symbol, indicator+period, real 60s market-hours cadence, register-not-execute fire behaviour) | backend/services/chat_service.py:3267-3283, 4873-4877 (both paths) | P1 |
| F16 | Amend mismatch guard: named symbol ≠ active draft symbol → never silently amend; re-instantiate named draft or state honestly that one draft is held; prose diff always derived from card_digest | backend/services/chat_service.py (amendment handling, both paths) | P0-adjacent |
| F17 | Units guard: force get_live_price before units-vs-rupees ask; floor(budget/price)<1 → impossible-reading callout + plausible-budget re-anchor + worked example; resolution cards carry rupee trigger anchor + outlay + tag | backend/services/chat_service.py (unit-disambiguation template) | P2 |
| F18 | IV info-path honest degrade: extend the :1999 automation-rail guard to informational asks ("rich/cheap vs months", "buyer or seller of premium") → "no IV history yet" + ATM-IV + 12-18% NIFTY-band proxy; never assert multi-month comparisons | backend/services/chat_service.py:1999 (both paths) | P2 |
| F19 | Unlisted-entity template: plain "Air India is unlisted (private Tata Sons subsidiary)"; drop "X is not airline" filler and ambiguous "AI exposure"; offer labeled non-Tata listed aviation alternative | backend/services/chat_service.py / backend/prompts/system.md | P3 |
| F20 | Fast-path reply polish: append sanctioned backtest-offer clause + "period 14 = default" readback to the deterministic RSI skeleton string (0-LLM, static) | backend/services/workflow_skeleton.py | P3 |

## 7. TRACK C — capabilities (see structured output for full specs)

| # | Capability | Feasibility |
|---|---|---|
| C1 | register_workflow/activate + get_workflow_status chat tools (armed-state introspection) | build-now |
| C2 | Addressable multi-draft store (per-symbol drafts; "the INFY one" resolves) | build-now |
| C3 | Option roll/adjustment tool (close+reopen, net premiums, roll card) | build-now |
| C4 | Weekly timeframe through IndicatorNode → scheduler (interval='1wk') | build-now |
| C5 | Staged/partial scale-out exits (multi-branch exit DSL with per-leg qty) | build-now |
| C6 | IV-history capture → IV rank/percentile | defer-infra |
| C7 | Live trailing-stop peak ratchet | defer-infra |
| C8 | Historical-multiples (5y avg P/E) series | defer-infra |
| C9 | Per-payment dividend calendar (ordinary vs special DPS split) | defer-infra |

C1, C2, C5 all touch backend/services/chat_service.py — each is specced as an independent guard/intent + tool so they can be built and landed separately.
