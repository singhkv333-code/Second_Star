# GAN R4 — Gold-Anchored Discriminator Panel (thematic round)

- **Date:** 2026-06-10
- **Snapshot:** `run_20260610_215912.json` (this dir)
- **Gold:** `GOLD/{thematic-thesis,vague,single-session-context,follow-up-amend}.md`
- **Sessions:** 18 (7 thematic-thesis, 4 vague, 4 single-session-context, 3 follow-up-amend) × 2 angles
- **Run triad:** 32 turns, p50 8,494 ms, p95 20,107 ms, 2,161,066 in-tok / 6,453 out-tok
- **Verification:** every verdict adversarially verified against transcript + card_digest + source. Four discriminator verdicts were **overturned or re-scored** on verification (noted inline); the numbers below are the corrected panel.

---

## 1) Headline

| Angle | PASS | PARTIAL | FAIL | MEAN /10 |
|---|---:|---:|---:|---:|
| **A — execution correctness** | **2** | **3** | **13** | **3.83** |
| **B — output quality** | **4** | **2** | **12** | **4.03** |

This is a *baseline* panel for two new classes (thematic-thesis, vague) plus two regression classes. The two new classes are where the floor is: **vague is 0-for-8 across both angles** (no session above 3/10) and **thematic-thesis has zero turn-1 baskets and zero winners/losers tables** in 7 sessions. The regression classes (follow-up-amend especially) are markedly healthier — the propose_* draft lifecycle is genuinely solid; the failures there are confined to one routing dead-end (create_sip) and readback depth.

### Per-class

| Class | n | A: P/Pa/F | A mean | B: P/Pa/F | B mean |
|---|---:|---|---:|---|---:|
| thematic-thesis | 7 | 0/2/5 | 3.29 | 1/0/6 | 3.29 |
| vague | 4 | 0/0/4 | 2.50 | 0/0/4 | 2.25 |
| single-session-context | 4 | 0/1/3 | 4.25 | 1/1/2 | 4.25 |
| follow-up-amend | 3 | 2/0/1 | 6.33 | 2/1/0 | 7.83 |

### Verification corrections applied (vs raw discriminator output)

| Session / angle | Discriminator said | Corrected | Why |
|---|---|---|---|
| vix_gated_defence_agent_probe / B | PARTIAL 6 | **PASS 8** | Discriminator applied the analysis rubric to gold Probe B, a pure conditional-BUILD test; both branches, ₹50k equal-split, armed-not-executed all faithful. |
| cheaper_one_sip_axis_kotak / B | PARTIAL 5 | **PASS 8** | "Empty logic_card" claim is a **harness blind spot** — the full SIP LogicCard ships in the top-level `logiccard` field (chat.py:441-452, tool_executor.py:489-497); auto_batch_eval.py only snapshots `raw_data`. High-weight criteria (referent, carry-forward, one-turn build) all full. |
| hal_compound_arithmetic_amend / B | PARTIAL 6 | **PASS 8.5** | Hardest amend probe aced mechanically ("halve" = arithmetic on card state, two fields one utterance, byte-identical trigger, same-draft register). Only real miss = ₹-recompute flourish (price-dependent, Kite down today). |
| swap_symbol_amount_hinglish / B | PARTIAL 6 | **PASS 9.5** | Minimal-diff two-field amend with byte-identical cron + real register_workflow flip; complaints (units line, Hinglish mirroring, raw cron) are the two lightest criteria; next_run_at IS in the payload. |
| goldbees_register_then_revert / B | FAIL 2 | **PARTIAL 5.5** | "Wrong-widget collapse" claim false (logic_card SIP is the designed widget; cardless affirmative path is by design) — but confirm-register, recompute, and the final one-armed-workflow assertion genuinely missing. |
| vix_gated_defence_agent_probe / A | PARTIAL 7 | **PARTIAL 7** (rationale replaced) | Band survives but the real defect is severe: INDIAVIX is NOT in fetch.quote (system.full.md:467) nor in yfinance INDEX_TICKERS (yfinance_service.py:29) → resolves to dead `INDIAVIX.NS`; the reply narrated a fully-working VIX agent with zero substitution caveat — an honest-boundary violation, not "terseness". |

---

## 2) Per-session table (corrected)

| Class | Session | A | A/10 | B | B/10 | Dominant defect |
|---|---|---|---:|---|---:|---|
| thematic | monsoon_deficit_single_turn | FAIL | 3 | FAIL | 3 | Option spread instead of irrigation basket; no winners/losers table; staples/fert mis-mapped as plays (gold marks them LOSERS) |
| thematic | india_pak_conflict_refusal_calibration | FAIL | 1 | FAIL | 1 | Bare ask_user ("buy, sell, hedge or alert? which symbol?") — the exact 0-score punt the gold targets |
| thematic | rupee_depreciation_hinglish | FAIL | 2 | FAIL | 1 | Misread as single-ticker price lookup; dead-ended on a quote failure that the thesis never needed |
| thematic | rbi_rate_cut_cycle | PARTIAL | 5 | FAIL | 4 | Right widget family, wrong parse: invented daily 09:20 schedule, screener placeholders instead of named beneficiaries |
| thematic | crude_spike_hedge_probe | FAIL | 3 | FAIL | 4 | Protective put presented as the whole answer; no ONGC/OIL long leg, no Brent confirm/kill |
| thematic | vix_gated_defence_agent_probe | PARTIAL | 7 | PASS | 8 | Two-branch gated basket built correctly — but INDIAVIX trigger is unwired and undisclosed (A defect) |
| thematic | el_nino_basket_surgery | FAIL | 2 | FAIL | 2 | Turn 0 competent prose, NO card → turn-1 basket surgery impossible, ask_user cascade |
| vague | first_salary_where_to_start | FAIL | 3 | FAIL | 3 | 71-word blurb, no widget, interrogation before value, out-of-scope emergency-fund/debt advice |
| vague | idle_two_lakh_scared | FAIL | 2 | FAIL | 2 | Scope inversion: recommends FD/liquid/G-Sec products Pivot can't render or register; no card |
| vague | one_percent_a_day | FAIL | 2 | FAIL | 1 | 22-word ASK_USER agent-menu; treats 1%/day as a buildable spec instead of refuting the math |
| vague | what_to_buy_this_week | FAIL | 3 | FAIL | 3 | Names tickers from memory, OFFERS to screen instead of running screen_fundamentals |
| ssc | lt_draft_backtest_amend_chain | FAIL | 3 | FAIL | 3 | "backtest that" mis-routed as draft amendment; T3 backtest returns no metrics + flat ₹100k curve |
| ssc | pronoun_after_analysis_bhartiartl | FAIL | 5 | PARTIAL | 4 | T3 dip-buy punts to ask_user with symbol AND ₹1,00,000 both in context |
| ssc | cheaper_one_sip_axis_kotak | PARTIAL | 8 | PASS | 8 | Referent + carry-forward + one-turn build all correct; residual = evidence-anchor + counter-view polish |
| ssc | the_other_one_pharma_dipbuy | FAIL | 1 | FAIL | 2 | 50s _llm_unavailable fallback on the decisive context turn — referent (CIPLA) and ₹50k never attempted |
| fua | hal_compound_arithmetic_amend | PASS | 8 | PASS | 8.5 | Clean compound amend + same-draft register; missing ₹-recompute only |
| fua | goldbees_register_then_revert | FAIL | 3 | PARTIAL | 5.5 | create_sip dead-end: un-registerable from chat, cardless amends, no final one-armed-workflow assertion |
| fua | swap_symbol_amount_hinglish | PASS | 8 | PASS | 9.5 | Byte-identical schedule across swap; real register flip; thin readback only |

---

## 3) What excellent looks like (distilled from the gold files)

**Thematic-thesis** (`GOLD/thematic-thesis.md`): every answer = (1) 1–2-line **thesis decode** (scenario → macro channel → sector earnings); (2) **winners & losers markdown table**, ≥2 real NSE tickers each side with a causal WHY per row (avoid leg named even though shorting isn't wired); (3) **turn-1 `workflow_draft_card` basket** with ₹ allocation and splits (e.g. monsoon: SHAKTIPUMP/KSB/KIRLOSBROS/JISLJALEQS 30/30/25/15; conflict: HAL 25/BEL 25/BDL 15/MAZDOCK 10/GOLDBEES 25); (4) **confirmation + invalidation** in checkable data (IMD %-of-LPA, India VIX > 20, ceasefire, USDINR level, Brent level) with an offer to arm it; (5) "thesis-driven, timing uncertain; analysis not advice"; (6) at most ONE sharpening question, AFTER the proposal. **Refusal calibration:** a conflict hedge is a lawful ask — decode and propose; never moralise-then-list. Pass bar ≥70/100 with turn-1-proposal ≥15/25 and refusal-calibration ≥10/15 — a punt or over-refusal cannot pass on prose.

**Vague** (`GOLD/vague.md`): value FIRST. Honest no-guarantees reframe → 2–3 concrete named paths each with a real-number one-liner (₹5,000/mo NIFTYBEES, RSI<30 dip rule, ROE>15% screen) in a markdown table → **at least one tappable artifact on turn 1** (prefilled, draft/not-armed NIFTYBEES SIP `workflow_draft_card` is the universal default; or a populated screen widget from a REAL screen run) → exactly ONE compound narrowing question (horizon + risk + capital). Use stated capital, never re-ask it. FDs/debt/liquid funds = named out-of-scope plainly. "1% a day" = refute the compounding math without mockery, then convert ambition into a backtest artifact + SIP fallback.

**Single-session-context** (`GOLD/single-session-context.md`): resolve pronoun/superlative/ordinal/antonym referents silently from session history, **name the resolution with evidence** ("the cheaper one → HDFCBANK, PE 19.2x vs 20.8x"), carry every known param (symbol, ₹, thresholds) into the new card without re-asking, ship a fully-built default + inline variant instead of a blocking ask_user, "backtest that" runs the engine on the exact just-drafted card and the amend-rerun narrates the delta ("7%: 4 trades vs 9").

**Follow-up-amend** (`GOLD/follow-up-amend.md`): the card the user is looking at is the card that mutates. Minimal-diff (only asked fields change, byte-identical everything else), `Changed:/Kept:` readback whose numbers match the card_digest, recompute the ₹ consequence of every economic amend, confirm-to-register flips the SAME draft via a real tool call with an armed-state readback (watched condition, cadence, next-run, register-not-execute). Notify-only collapse or fresh-draft-on-confirm = automatic class fail.

---

## 4) Cross-cutting patterns

1. **The thematic-scenario class is unmodeled, period.** No rule in system.md or tool_router.py maps "strategy that profits from / position me for <macro scenario>" to a decode-and-propose path; the only scenario keyword handling is the event-alert regex (tool_router.py:771). Every prompt degrades into the nearest generic shape: option spread (monsoon, crude), ask_user (india-pak, rupee), sector-rotation schedule (rbi), prose-only (el_nino).
2. **Zero winners/losers tables and zero turn-1 thematic baskets across all 7 thematic sessions** — the gold's two highest-weight criteria (25+20) score 0 everywhere. Yet **vix_gated proves the DSL can already express gated thematic baskets** (two-branch INDIAVIX card, equal-split ₹50k HAL/BEL/BDL): the gap is routing + contract, not engine.
3. **Zero tappable artifacts in the entire vague class** (8/8 angle-instances FAIL, word counts 22–89 vs gold 200–350). Three distinct leaks: explainer/'other' fallthrough (first_salary, what_to_buy), yield-tool scope inversion (idle_two_lakh), ASK_USER agent-menu swallow (one_percent).
4. **ask_user where a default exists** is the single most repeated execution defect (india_pak, rupee, one_percent, el_nino T1, bhartiartl dip-buy) — despite system.md:84-89 already mandating ₹÷price sizing with no refusal.
5. **Drafts that aren't propose_\*-shaped fall out of the lifecycle.** create_sip is in none of _STASH_DRAFT_TOOLS / _MACRO_AMENDMENT_TOOLS / _REGISTERABLE_DRAFT_TOOLS (chat_service.py:1196-1245) → register punts to the FE button and every amend goes cardless. The identical ask via propose_scheduled_order (swap session) ran the full amend→register lifecycle perfectly.
6. **Honest-boundary violation on unwired primitives:** INDIAVIX narrated as a working trigger with no caveat, while system.full.md:467 declares it unwired and yfinance resolves it to a dead ticker.
7. **Recompute is fetched and discarded:** amend turns call get_live_price and never print units/outlay; readbacks are one-liners; no ₹-consequence anywhere.
8. **Two infra reliability holes judged separately from reasoning:** the 50s `_llm_unavailable` fallback that wipes a context turn, and the backtester returning "no usable metrics" + flat ₹100k curve instead of an honest zero-trade report.
9. **Harness blind spot:** auto_batch_eval.py snapshots `raw_data` only and misses the top-level `logiccard` field → produced one false "empty card" verdict. Fix before R5 or SIP-class sessions will keep mis-grading.

---

## 5) TRACK F — fixes (ranked)

| # | Title | Where | Change | Evidence |
|---|---|---|---|---|
| F1 | Thematic bare-binary punt + conflict over-refusal | services/chat_service.py (ask_user suppression) + prompts/system.md (refusal carve-out) | On scenario-positioning intents, forbid bare ask_user as the whole turn; add explicit carve-out: lawful conflict/drought/FX positioning = decode-and-propose with caveat; refuse only insider/manipulation. (Detector itself = C1.) | india_pak: 48-tok ask_user, score 1/1; rupee: ask_user on quote failure; el_nino T1: no card → T2 punt |
| F2 | Vague zero-spec → no widget | chat_service routing + system.md VAGUE-ONBOARDING rule | "where do I start / make money / first salary / what should I do" with no verb/symbol/trigger → prefilled NIFTYBEES ₹5,000/mo SIP draft (register-not-execute) + 3-path table + ONE compound question. Never explainer-text-only. | All 4 vague sessions: card_digest null or ask_user; gold crit 1 (wt 25) = 0 every time |
| F3 | Backtest-verb-first routing hole | services/chat_service.py:2110 (_INDEPENDENT_INTENT_RE) | Regex only matches `(run\|do\|start) backtest`; add verb-first branch `\bbacktest(?:\s+(?:that\|this\|it\|the\s+(?:strategy\|draft\|rule)))?\b` so "backtest that" evicts the draft and forces the backtest surface. | lt chain T2: tools=[propose_dsl_workflow], "Backtest draft updated" — re-draft, no engine call |
| F4 | Idle-cash yield route scope inversion | services/tool_router.py:601-616 | compare_yields/get_yield_recommendation actively recommend FD/liquid/G-Sec — out of scope per CLAUDE.md. Gate behind explicit "compare yields/where to park" asks; "scared idle cash, do something" → scope-honesty line + phased NIFTYBEES SIP card (riskable slice) + GOLDBEES + paper-mode offer. | idle_two_lakh: "short FD… 6.5% after tax… compare FD/liquid/overnight/arbitrage/G-Sec", no widget, 2/2 |
| F5 | Unrealistic-return decode | services/chat_service.py (before ASK_USER agent-menu) | Detector for impossible targets ("1% a day", "double in a month", "guaranteed N%") → no-mockery math refutation (>3,600%/yr) + run_backtest of a real RSI mean-reversion strategy rendering real return/drawdown + SIP fallback. Never the buy/dip/sell/alert menu. | one_percent_a_day: 22-word ASK_USER menu, 2/1 — gold's named worst case |
| F6 | Dip-buy ₹-sizing ask_user punt | prompts/system.md:84-89 + no-ask anchors ~405-412 | The rupee-sizing rule is not applied to dip-buy: when capital + in-context symbol exist, ALWAYS draft create_dip_buy with shares = round(₹ ÷ live price), state the conversion, offer override inline. Add dip-buy to the explicit never-repackage-sizing-as-ask_user list. | bhartiartl T3: "how many shares… or size from ₹1,00,000?" with both already known |
| F7 | create_sip draft dead-end | services/chat_service.py:1196-1245 + services/tool_router.py | Route recurring single-symbol ETF/equity buys to propose_scheduled_order (proven full lifecycle in swap session) OR make create_sip first-class: add to _STASH/_MACRO sets + a SIP register path. Today: register → "use the card's button", amends → cardless prose. | goldbees: T1/T2/T3 all tools=[], render_hint=None; nothing ever armed |
| F8 | Vague what-to-buy must RUN the screen | tool_router/system.md:71 screen rule extension | Fire screen_fundamentals (sort-only quality screen) on "what should I buy / something solid / bas batao" incl. Hinglish; render rows; forbid naming tickers from memory; degraded fallback = prefilled SIP/dip-buy card with reason. | what_to_buy: tools=[], names HDFCBANK/INFY/RELIANCE from memory, only OFFERS to screen |
| F9 | INDIAVIX undisclosed-substitution | market/yfinance_service.py:29 (INDEX_TICKERS) + prompts/system.full.md:467 + kite mapping | Either wire INDIAVIX (yfinance ^INDIAVIX + Kite "INDIA VIX") into fetch.quote, or enforce the existing disclosure contract — never narrate a VIX-gated agent as working without the substitution caveat. Wiring preferred (unlocks C1 confirm-triggers). | vix_gated: confident "when India VIX closes above 20, the agent buys…", zero caveat; resolves to dead INDIAVIX.NS |
| F10 | Backtest empty-metrics honesty + delta narration | backtester (pct_change relative-drop entry) + reply contract | Flat ₹100k curve + "no usable metrics" for the LT 5-bar-drop rule: fix the compound-entry/sell-leg evaluation in backtest mode; when genuinely zero trades, report trade count 0 + nearest-miss; on amend-rerun, narrate before/after (trades, CAGR, DD). | lt chain T3: "engine returned no usable performance metrics", flat 100000.0 curve, no 5%-vs-7% delta |
| F11 | _llm_unavailable wipes the context turn | chat_service.py error path (~line 166 fallback string) | 50s single-call failure → generic menu that loses referent + ₹. Add retry/backoff; degraded path must echo the parsed intent ("the other one → CIPLA, ₹50,000 dip-buy — retry?") so the user doesn't re-type. | the_other_one_pharma T2: raw_keys=[_llm_unavailable], 50,486 ms, 1/2 |
| F12 | Amend readback depth + recompute contract | prompts/system.md (amend/build reply contract) | Force on every economic amend: two-line `Changed:/Kept:` + recomputed ₹ consequence USING the already-fetched price (post-swap symbol, not stale); human next-run date instead of raw cron; Hinglish mirroring on Hinglish input; T0 readback table. | swap T1 fetched JUNIORBEES (old symbol) price, printed nothing; hal amend: no outlay/stop ₹-impact; "15 9 * * 3" echoed raw |
| F13 | Harness logiccard blind spot | scripts/auto_batch_eval.py:201,209 | Snapshot body["logiccard"] alongside raw_data so SIP-class cards are visible to discriminators; prevents false "empty card" verdicts. | cheaper_one B verdict overturned on exactly this |

## 6) TRACK C — capabilities

| # | Capability | Feasibility | Spec | Where |
|---|---|---|---|---|
| C1 | **KEYSTONE: thematic-strategy path** (thesis → winners/losers → real NSE instruments → basket card → trigger+invalidation) | **build-now** | (a) Deterministic detector in chat_service: regex over "profits from / benefits from / hedge against / position me for / play on <scenario>" + scenario nouns (war, conflict, ceasefire, monsoon, drought, el niño, rupee/INR, crude, rate cut, slowdown) + Hinglish cues ("gir raha hai", "jeetenge", "banao") → forces toolset {propose_basket_allocation, propose_workflow, get_live_price}, FORBIDS bare ask_user, never gates on a live-quote success. (b) system.md "## Thematic scenario strategies" contract: thesis decode (1–2 lines) → winners/losers markdown table (≥2/side + WHY, losers as avoid list) → turn-1 ₹-split basket card → what-confirms/what-kills → caveat → ≤1 question after. Applies even when an option tool also fires. (c) Refusal carve-out (lawful scenario positioning ≠ refusable; insider/manipulation still refused). (d) Seed sector map: monsoon/drought→{long: SHAKTIPUMP,KSB,KIRLOSBROS,JISLJALEQS; avoid: M&M,ESCORTS,HINDUNILVR,DABUR,COROMANDEL,HEROMOTOCO}; conflict→{HAL,BEL,BDL,MAZDOCK,GOLDBEES; avoid: INDIGO,IRCTC,INDHOTEL,BAJFINANCE}; INR-fall→{INFY,TCS,SUNPHARMA,CIPLA; avoid: IOC,BPCL,INDIGO,NESTLEIND}; crude→{ONGC,OIL; avoid: IOC,BPCL,HPCL,ASIANPAINT,INDIGO,MRF,APOLLOTYRE}; rate-cut→{banks/NBFC/auto/realty leaders; avoid: NIM-compression lenders}. vix_gated proves the DSL already expresses the resulting baskets. | services/chat_service.py + services/tool_router.py + prompts/system.md + new services/thematic_map.py |
| C2 | Vague-onboarding value-first (prefilled SIP + 3-path table) | build-now | Zero-spec detector → propose_scheduled_order/create_sip NIFTYBEES ₹5,000/mo draft (not armed) + 3-path markdown table with real numbers + exactly one compound question; stated capital (₹50k/₹2L) is USED for the split, never re-asked; out-of-scope products named plainly. | chat_service routing + system.md (pairs with F2/F4) |
| C3 | Composite thematic basket + option overlay in one turn | build-now | Both tools exist; allow propose_workflow + build_option_strategy in the same toolset for cross-asset scenarios; system.md template: LEAD with the equity basket + table, ADD the NIFTY put as an explicit optional overlay (5–10% of capital) — never let the option tool short-circuit the decode. | tool_router.py toolset composition + system.md (crude probe) |
| C4 | Unrealistic-return → backtest artifact | build-now | run_backtest is wired; route the F5 detector's artifact leg to an RSI mean-reversion backtest on a liquid large-cap and render the chart + honest return band, then SIP fallback. | chat_service + backtest tools (pairs with F5) |
| C5 | INDIAVIX as a real trigger/confirmation source | build-now | Add `"INDIAVIX": "^INDIAVIX"` (+ "INDIA VIX"/"VIX" aliases) to INDEX_TICKERS; map Kite "INDIA VIX" instrument; update system.full.md:467 contract; unlocks gold's event-armed thematic agents (fire if VIX > 20) and conflict-thesis confirmation triggers. | market/yfinance_service.py:29 + kite service + prompts/system.full.md:467 |
| C6 | Prose-basket reconstruction on amend | build-now | When turn-0 text named candidate instruments but emitted no card, the amend turn ("drop the weakest, size to 50k") treats the prior assistant message's named candidates as the editable set and proposes a card — never ask_user to re-name the basket. | system.md amendment rules + chat_service amendment-hint path (el_nino) |
| C7 | Mutate an already-registered workflow from chat | defer-infra | Needs a workflow mutation/pause-and-replace API + "updating the registered agent, not making a second one" semantics; until then, honest one-liner per gold. | workflows engine + chat_service |
| C8 | Undo / draft state history | defer-infra | Draft store keeps no version history; "undo that" needs a per-conversation draft version stack (and post-register revert needs C7). | chat_service draft store |
| C9 | Macro-data trigger sources (USDINR, IMD rainfall) | defer-infra | No data feed for FX/rainfall; until wired, gold behaviour = say plainly unsupported + offer nearest real trigger (price triggers on basket names). Keep the honest-substitution line enforced. | workflows/steps/fetches.py + new data sources |
| C10 | Multi-leg SIP in one card (NIFTYBEES + GOLDBEES) | defer-infra | Card schema/engine change; gold explicitly accepts single-leg card + text mention of the second leg, so low urgency. | workflows DSL + FE card |

---

## 7) Bottom line

Both new classes fail at the **routing layer before quality is even testable**: thematic and vague prompts never reach the draft/basket machinery that the follow-up-amend class proves works well (hal 8/8.5, swap 8/9.5 with byte-identical minimal-diff mutation and real register flips). The keystone C1 detector + contract + seed map, plus F1–F5, would convert an estimated 9 of the 13 Angle-A FAILs into the proven propose_* path. Re-probe after landing: monsoon, india_pak, rupee (thematic); all 4 vague; lt chain T2; goldbees lifecycle.
