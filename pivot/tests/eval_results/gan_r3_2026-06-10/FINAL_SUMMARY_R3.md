# GAN FINAL SUMMARY — Round 3 (delta judge, 2026-06-10)

**Run files**
- R3 baseline: `baseline/run_20260610_022014.json` (28 canonical sessions / 36 turns) + `baseline_probes/run_20260610_022804.json` (14 probes / 29 turns)
- R3 after: `after/run_20260610_153234.json` + `after_probes/run_20260610_153928.json` (identical session sets, same harness, fix commit `d621e96…de21e96` = `de21e9631a577858b3d780b1a6f5c73a7b6bd139`)
- Gold bars: `GOLD/` (round-3 composed ideals + mechanical rubrics) — note R3 bars are **stricter** than R1/R2 judging, so cross-round means are not apples-to-apples.
- Condition: Kite token was live for F&O in the after run (`Source: kite` on chains); equity quotes still fell back to yfinance EOD with honest (undated) tags on both sides — tagging itself is correct behaviour.
- One infra event: the after-canonical `infy_vs_tcs_which_better` turn hit a 127.2s Azure outage and returned the static "AI backend temporarily unavailable" fallback. Scored as the FAIL it is, but flagged: not caused by the work-order code.

---

## Scoreboard across ALL GAN rounds (canonical sessions)

| Round | A: P/Pa/F | A mean | B: P/Pa/F | B mean | Bars |
|---|---|---:|---|---:|---|
| R1 baseline | — | 6.04 | — | 5.04 | R1 gold |
| R2 post-fix | 19/5/4 | 7.50 | 16/8/4 | 6.50 | R1/R2 gold |
| R3 baseline | 17/7/4 | 7.18 | 5/11/12 | 5.53 | **new, stricter R3 gold** |
| **R3 after** | **17/5/6** | **6.68** | **11/8/9** | **5.68** | R3 gold |

Probes (reported separately, 14 per angle):

| Round | A: P/Pa/F | A mean | B: P/Pa/F | B mean |
|---|---|---:|---|---:|
| R3 baseline | 2/5/7 | 4.79 | 0/8/6 | 4.36 |
| **R3 after** | **2/8/4** | **5.36** | **1/8/5** | **4.86** |

**Honest read:** Angle B moved up (+0.15 canonical, +0.50 probes; canonical PASS count 5→11 — the targeted output-quality work landed). Angle A canonical moved **down** (−0.50) despite the targeted items all passing, because (a) two execution-stress sessions regressed onto guards that were specced but **never applied this cycle** (F6 basket budget, F7 open-anchor cue), (b) one multi-turn session lost in-context symbol carry, and (c) the Azure outage zeroed one quality-stress session. Excluding the outage turn, canonical A mean is 6.89 — still a net A regression, concentrated in 4 sessions. The F&O category — the round's main target — jumped A 6.75→8.25 and B 4.00→7.25.

Per-category (canonical, after):

| Category | A: P/Pa/F | A mean (base→after) | B: P/Pa/F | B mean (base→after) |
|---|---|---|---|---|
| F&O | 4/0/0 | 6.75 → **8.25** | 3/1/0 | 4.00 → **7.25** |
| quality-stress | 4/1/1 | 7.83 → 6.83 (7.97 excl outage) | 4/1/1 | 6.83 → 6.17 (7.20 excl outage) |
| execution-stress | 4/0/2 | 8.83 → **6.67** | 1/2/3 | 5.40 → 4.83 |
| multi-turn | 3/1/2 | 5.83 → 5.83 | 3/1/2 | 5.38 → 5.50 |
| regression | 2/0/0 | 9.50 → 9.50 | 1/1/0 | 7.50 → 7.50 |
| edge-honesty | 0/2/0 | 5.00 → 5.00 | 0/0/2 | 4.00 → 4.00 |
| ambiguous | 0/1/1 | 5.00 → 4.50 | 0/1/1 | 5.00 → 4.00 |

---

## What each fix achieved (verified against actual after-run outputs)

**LANDED and verified**

1. **F1 — short-put bounded loss (P0).** `critique_naked_put_reliance`: digest now carries `max_loss: 539575.0` (not None); prose says "max loss around **₹5.4 lakh**" — the word "unlimited" is gone from the put critique. The roll card correctly keeps `max_loss: null` only for the genuinely-open naked short **call**. A 4→8.
2. **F2 — F&O mandated tables via digest enrichment (P0).** All four canonical F&O replies now ship the synthesised tables: chain replies carry the metrics table AND an OI-walls table naming 6 real strikes with OI values + `Source: kite; as of <timestamp>`; suggest carries a **3-candidate** comparison (max loss/profit/POP/net) plus a per-leg table; build carries the per-leg table; critique carries per-leg + a 2-row current-vs-alternative table + the POP line. Expected-move band quoted verbatim from the digest (22,806–23,665 = digest 22806.16/23664.84). F&O B went **0-for-4 FAIL → 3 PASS + 1 PARTIAL**. Residual: the critique's alternative row is qualitative ("capped to the strike gap minus credit") where gold wants the spread's actual rupees — build_option_strategy was called, numbers existed.
3. **F4 — create_sip fail-closed (P0, worst R3 defect killed).** `probe_mf-sip-direct-plan` t0 no longer mints `PARAGPAREKHFLEXICAP` or says "SIP is set": "Direct-plan mutual funds are not supported here… nearest listed proxy is NIFTYBEES." A 2→5. Residual: no prefilled proxy card (still ask-first), and t1 "wahi laga do" still re-asks instead of building.
4. **Teach turn (F8 territory).** `i_dont_understand_then_clarify` t1 now corrects the false premise ("Nothing is set up yet"), explains RSI(14)<30 with a worked example, retains COALINDIA. A 3→8, B 3→7.
5. **Hedge guards + strategy-explanation floor (d621699).** Iron-condor builds a real 4-leg card with leg table, no terminal ask_user — held. No hedge regression observed.
6. **Trailing-stop disclosure** (`titan`) — intact: `trailing: true` in card + honest "live re-ratcheting isn't wired" warning.

**CAPABILITY PROBES — end-to-end outcomes**

| Capability | Outcome (actual after-run behaviour) |
|---|---|
| C1 register_workflow + get_workflow_status | **WORKED** on the probe: "register it for me right now" → real activation (workflow_id, status active); status turn returns grounded readback (60s cadence, market hours, **current RSI 33.65**, register-not-execute) at 146ms/0 LLM calls. **BUT** the canonical phrasing "looks good, go ahead and register it" still hits the 0-token canned "click Save & activate" path — guard cues too narrow. |
| C2 multi-draft store | **FAILED live.** `probe_two-drafts-edit-first` t2: "change the INFY one to 8 shares, WIPRO wala same rehne do" → card shows **WIPRO quantity 8** while prose claims "WIPRO stays the same; INFY is updated to 8 shares". The wrong-target amend + prose/card lie persists exactly as in baseline (A 2, B 2). |
| C3 roll_option_position | **WORKED.** Hinglish roll → 2-leg card (BUY-to-close 23400 CE / SELL-to-open 23450 CE next expiry), net credit ₹4,082, breakeven 23,605, POP 71.9%, naked-call warning, leg table. A 4→6. Residuals: "Rolled:" theatre verb; t1 alert follow-up re-asks an inferable symbol. |
| C4 weekly timeframe | **WORKED at build, destroyed at t1.** t0 deterministically builds `RSI(14, weekly)` with `timeframe: weekly` in config + W-FRI resample rationale (0 LLM calls). Then the clarifying **question** "is that checking the weekly chart or daily?" is treated as an amendment: the draft is silently mutated to daily ("Changed to daily closes"). User intent destroyed by a Q&A turn. A stays FAIL (4). |
| C5 staged scale-out exits | **PARTIAL.** t0 builds the full 8-step multi-branch card (open entry, +3% sell 5, +6% sell 5, stop sell 10) — a refusal in baseline. Defects: the stop is `drawdown_from_peak_pct` when the user said "2% from my buy price" (entry-anchored), prose contradicts the card ("falls 2% from entry"); t1 "make the stop trailing" returns an **identical card** with a fake "Changed:" diff; no per-branch readback table in prose. A 3→5. |

**Deferred-infra (unchanged, correctly bounded):** IV-history → IV rank/percentile; live trailing-stop peak ratchet; historical-multiples (5y avg P/E) series; per-payment dividend calendar.

---

## Regressions (baseline R3 → after R3)

1. **`basket_three_symbol_split` A 9→3.** Baseline built the 3-leg ₹60k card; after collapses to the qty interrogation ("How many shares of SUNPHARMA…"). F6 was specced but **not applied**; behaviour is LLM-flaky without the deterministic guard.
2. **`bajajauto_buy_open_sell_3pct` A 9→2.** The banned "can't anchor to today's open / 09:30 checkpoint" denial returned — while the staged-exit probe in the *same run* builds `trigger.market_relative_time(anchor=open)`. F7 cue reorder not applied; plain single-exit open builds aren't caught by the new staged guard.
3. **`hcltech_gtt_price_level` B 6→4.** Execution theatre returned: "**Placed as a GTT**" (baseline said "GTT drafted"). The theatre lint landed only on the SIP path.
4. **`analysis_then_build_followup` A 8→4.** t1 re-asks "Which symbol?" for the JSWSTEEL just analysed — in-context symbol carry lost (possible interaction with the new draft-selection code; baseline built directly).
5. **`probe_unlisted-entity-honesty` A 8→3.** Baseline honestly said no listed Tata airline exists; after suggests the apparently fabricated ticker "`TATAPROD`". Worse than baseline on the round's core honesty axis.
6. **`hinglish_then_resize_notional` t0 3→2.** Now fails to draft at all (live-price fetch failure → "double-check the ticker spelling" on a valid TATAMOTORS); t1 still the context-amnesiac "tell me the NSE ticker" (F3 not applied).
7. **`hundred_of_eichermot_units` A 6→5, B 7→5.** Lost the live anchor: no ₹7,203/₹7.2-lakh stakes quantification, and "₹100 worth" is offered without the impossible-reading callout.
8. **`probe_itc_yield_then_vs_now` B 6→4.** After stopped calling get_price_history — the then-vs-now table's entire "1 year ago" column is "Unavailable" where baseline at least grounded then-price ₹403.35.
9. **`infy_vs_tcs_which_better` A 9→1, B 8→1.** Azure backend outage (127s) — infra, re-run before drawing product conclusions.

---

## Still open (carry-forward + new)

- **F6 basket-budget guard and F7 open-anchor cue reorder — specced in JUDGE_REPORT_R3 §6, never landed; both caused live regressions this run. Top of next work order.**
- Multi-draft amend targeting (C2) broken live; amend symbol-mismatch guard (F16) still needed.
- Register guard cue coverage ("looks good, go ahead and register it" → canned 0-token line).
- Clarifying-question-as-amendment: "is that weekly or daily?" mutates the draft (weekly probe); needs a question-vs-amendment classifier in front of the amend path.
- Staged-exit stop anchor (entry vs peak) + no-op "Changed:" diffs + missing per-branch readback table.
- Boundary rails still card-less: MON100/NIFTYBEES/GOLDBEES all end in ask_user; MCX monthly→weekday cadence drop persists (now self-confessed: "I used a weekday schedule because the follow-up did not restate the 1st").
- Disambiguation fetch still not enforced (`the_tata_one_entity`: menu with zero tool calls); unlisted-entity template (TATAPROD fabrication).
- Context-aware fetch-failure recovery (F3) unbuilt; undated yfinance EOD tags (F12); execution-theatre lint not generalised (GTT "Placed", roll "Rolled:"); IV "vs last few months" answered without naming the no-IV-history bound (F18); DABUR fast-path "drops 4% from previous close" still parses to "price < ₹4" at t0.
- Critique alternative row should quote the built spread's actual rupees (numbers are fetched, not surfaced).

---

## Latency

Provided aggregates (canonical): before p50 9,204 / p95 11,719.8 / mean 7,986.7 ms → after p50 8,387 / p95 14,178.2 / mean 11,185.7 ms.

Interpretation — the median got **~9% faster** while mean/p95 look worse for two separable reasons:
1. The 127.2s Azure-outage turn alone accounts for the whole mean blow-up: excluding it, after-canonical mean is **~7,851 ms — better than baseline's 7,986.7**.
2. The p95 lift (11.7s→14.2s) is real but concentrated in two heavy 4-LLM-call builds (staged-exit t0 17.1s, bajajauto's failed attempt 15.2s).
Meanwhile the new deterministic paths post 0–146 ms turns (register, status, weekly fast-path), and the F&O tables are synthesised server-side at **zero added LLM latency** — chain turns actually got faster (8.2–8.7s vs 7.5–9.7s) while tripling content. Probes runs: mean 8,473.9→7,635.8 ms, p95 13,251.8→12,732.2 — strictly better. No systemic latency regression; one outage + two heavy builds.

---

## Tokens + cost

**Product (chat backend, Azure `gpt-5.4-mini`)** — per-turn snapshot fields, cross-checked against the `llm_usage` table over the exact run windows (DB cost matches snapshot to the cent on baseline):

| Run | LLM calls | Input tok | (cached) | Output tok | Cost (Azure, priced via llm_usage.cost_usd) |
|---|---:|---:|---:|---:|---:|
| Before canonical | 71 | 2,632,515 | 1,684,608 | 9,900 | $0.4674 |
| Before probes | 57 | 2,042,587 | 1,698,304 | 6,487 | $0.3113 |
| **Before total** | 128 | **4,675,102** | 3,382,912 | **16,387** | **$0.7787** |
| After canonical | 59* | 2,299,871 | 1,409,024* | 9,106 | $0.4121 |
| After probes | 54 | 2,090,755 | 1,600,384 | 5,570 | $0.3287 |
| **After total** | 113 | **4,390,626** | ~3,009,408 | **14,676** | **$0.7409** |

*DB window for after-canonical logs 59 calls / 2,259,342 in / 8,683 out / $0.4061 — slightly below the snapshot because the timed-out outage call isn't fully attributed. Net: the after run is **~6% cheaper on input, ~10% on output, ~5% on dollars** while producing materially richer F&O replies — the deterministic table synthesis adds zero LLM tokens, and the new 0-LLM register/status/weekly paths remove whole calls.

**Eval harness (this GAN workflow's Claude agents)** — ESTIMATE, output-tokens only (input not individually metered):
~1,354,274 output tokens spent across agents. Formula: `1.354274 MTok × (0.70 × $25/MTok [Opus 4.8 judging fan-out] + 0.30 × $50/MTok [Fable 5 composers/synthesis/builders/delta]) = 1.354274 × $32.50` ≈ **$44.01** (≈ $23.70 Opus share + $20.31 Fable share). Actual bill will differ with the true model split and unmetered input/cache tokens.

---

## Next work order (priority)

1. **P0 — land F6 (basket-budget deterministic guard) and F7 (open-anchor cue reorder).** Both were specced in JUDGE_REPORT_R3 §6 and skipped; both produced live A-regressions this run (basket 9→3, bajajauto 9→2). These are the two cheapest +6-point swings available.
2. **P0 — make C2 multi-draft targeting actually fire on the amend path** (probe proves `_select_active_draft` isn't reached or is overridden in handle_stream): "the INFY one" must never mutate WIPRO; add the F16 mismatch guard as backstop.
3. **P0 — widen register-guard cues** to affirm+register phrasings ("looks good, go ahead and register it") so the canonical confirm turn arms the workflow like the probe does.
4. **P1 — question-vs-amendment classifier** in front of draft mutation: interrogatives ("is that checking weekly or daily?") answer from the card digest, never rewrite the card. Re-verify the weekly probe after.
5. **P1 — staged-exit fidelity:** stop branch = `unrealised_pct <= -2%` when anchored to buy price; trailing only on request; suppress "Changed:" when the card is byte-identical; add the per-branch readback table.
6. **P1 — generalise the execution-theatre lint** (forbid Placed/Rolled/Activated-before-state-change across GTT/roll/SIP/order paths) and the boundary-rail prefilled proxy cards + cadence carry (F9).
7. **P2 — F3 context-aware failure recovery, F5 disambiguation fetch + unlisted-entity template (TATAPROD!), F12 dated EOD tags, F17 impossible-reading callout, DABUR fast-path % parse.**
8. **Re-run `infy_vs_tcs_which_better`** off-outage before counting it against quality-stress.
