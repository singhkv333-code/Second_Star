# GAN Round-2 Judge Report — Pivot chat copilot

- **Date:** 2026-06-09
- **Baseline run:** `pivot/tests/eval_results/gan_2026-06-09/latency_recheck/run_20260609_191718.json`
  (post-Round-1 build; 28 sessions / 36 turns; backend `:8000` live, Kite token of the day)
- **Method:** Two discriminator angles (A = execution correctness, B = output quality),
  per-session verified against the real `response` + `card_digest` + the system-prompt /
  `chat_service.py` contracts. Where a discriminator verdict was over-harsh it was refuted and
  the **corrected** verdict is used below (refutations marked ⟲).

---

## 1. Scorecard (verified verdicts)

| Angle | n | PASS | PARTIAL | FAIL | Mean (0–9 raw) | Mean (0–1 norm) |
|---|--:|--:|--:|--:|--:|--:|
| **A — Execution** | 28 | 19 | 2 | 7 | **6.75** | 0.714 |
| **B — Output quality** | 28 | 15 | 7 | 6 | **5.79** | 0.661 |

Quality (B) is again the binding constraint, not latency or cost. Server latencies are healthy
(multi-step DSL builds 7–12s, single-LLM turns 4–8s, deterministic affirmative/skeleton paths
1–15ms); token spend is normal (~38–125k in / 50–865 out). **Every FAIL/PARTIAL is a
quality/correctness defect, never a performance one.**

### Per-category means (raw 0–9)

| Category | A | B | Note |
|---|--:|--:|---|
| ambiguous | 7.50 | 7.50 | Strong; only the Tata signal-blindness B-nit |
| regression | 8.50 | 6.50 | Hot-paths clean; B = bare-echo text floor |
| F&O | 7.00 | 5.25 | Round-1 weakest (was 3.50) **lifted but still lowest B**; iron-condor REGRESSED |
| quality-stress | 6.17 | 7.17 | Bimodal: rich when classified, thin when mis-routed |
| execution-stress | 6.83 | 4.83 | **Two REGRESSED hard gates** (axisbank, bajajauto) drag it down |
| multi-turn | 6.67 | 5.33 | 4/6 strong; two model-behavior regressions |
| edge-honesty | 5.50 | 4.50 | **Lowest A & B** — sentiment-autosell drops the boundary entirely |

---

## 2. Per-session verdicts

`⟲` = discriminator verdict refuted and corrected here.

### F&O
| Session | A | B |
|---|---|---|
| nifty_chain_max_pain_pcr | PASS 9 | PARTIAL 6 |
| banknifty_suggest_bullish | PASS 8 | PASS 6 ⟲ |
| nifty_build_iron_condor | **FAIL 3** | **FAIL 2** |
| critique_naked_put_reliance | PASS 8 | PASS 7 ⟲ |

### ambiguous
| the_tata_one_entity | PASS 9 | PARTIAL 6 |
| hundred_of_eichermot_units | PASS 6 ⟲ | PASS 9 |

### edge-honesty
| us_adr_recurring_buy | PASS 9 | PASS 7 |
| news_sentiment_autosell | **FAIL 2** | **FAIL 2** |

### execution-stress
| bhartiartl_call_chain_stopword | PASS 9 | PARTIAL 6 |
| axisbank_alert_not_order | **FAIL 5** | PARTIAL 2 ⟲ |
| basket_three_symbol_split | PASS 9 | PARTIAL 5 |
| titan_trailing_stop_disclosure | PASS 8 | PASS 8 |
| bajajauto_buy_open_sell_3pct | **FAIL 2** | **FAIL 1** |
| hcltech_gtt_price_level | PASS 8 | PASS 7 |

### regression
| plain_price_kotakbank | PASS 8 | PASS 7 |
| plain_rsi_agent_grasim | PASS 9 | PASS 6 ⟲ |

### multi-turn
| amend_qty_then_confirm_register | PASS 9 | PASS 6 ⟲ |
| swap_symbol_then_add_stop | PASS 8 | PARTIAL 5 |
| i_dont_understand_then_clarify | **FAIL 3** | **FAIL 2** |
| hinglish_then_resize_notional | **FAIL 2** | **FAIL 2** |
| analysis_then_build_followup | PASS 9 | PASS 8 |
| screen_then_dont_understand | PASS 9 | PASS 9 |

### quality-stress
| is_reliance_expensive | PASS 8 | PASS 9 |
| itc_dividend_story | PASS 8 | PASS 9 |
| infy_vs_tcs_which_better | PASS 7 ⟲ | PASS 9 |
| analyse_hdfcbank_full | PARTIAL 5 | **FAIL 4** |
| is_nifty_uptrend | PARTIAL 5 | PARTIAL 6 |
| screen_cheap_high_roe_banks | **FAIL 4** | PASS 6 ⟲ |

---

## 3. Ranked RESIDUAL fix list (what is still weak after Round 1)

Highest leverage first. Each carries **where / change / evidence / ideal**.

### R1 — [P0, A] Reply-class MIS-ROUTING starves the (working) ANALYSIS template
- **Where:** `backend/services/chat_service.py:1086-1106` (`_ANALYSIS_INTENT_RE`); fall-through at
  `:1154-1158` → `analytical_short`.
- **Root cause:** the regex matches the analysis **verb** (`analy[sz]e`) but NOT the **noun**
  ("proper analysis of"), and has **no branch** for index-TREND asks (`uptrend`/`moving averages`)
  or SCREEN/RANK asks (`screen me … banks`, `rank …`). Every session that matched fired the rich
  sectioned/table output; every session that didn't shipped thin prose. The Round-1 structure
  hardening WORKS — it just isn't being selected.
- **Change:** broaden the regex at `:1089` to add the noun + trend + screen branches, e.g.
  `analy[sz]e|analy[sz](?:is|es)|deep\s+dive` and a trend alternative
  (`\b(?:up|down)trend\b|moving\s+averages?|\btrend\b.*(?:nifty|banknifty|sensex)`) and a screen
  alternative (`\bscreen\b|\brank\b.*\b(?:banks?|stocks?|by)\b|cheap\s+high-?roe`).
- **Evidence:** `analyse_hdfcbank_full` (B-FAIL 4) — "give me a proper analysis" got the **thinnest**
  of all 6 quality turns (278 tok, 0 tables, no returns ladder, no SMA levels); `is_nifty_uptrend`
  (PARTIAL) SMA stack given as raw levels with no %-distances; `screen_cheap_high_roe_banks`
  (A-FAIL) routed terse + wrong axis.
- **Ideal:** all three route to `analysis`, inheriting the already-enforced returns table + SMA
  %-distance stack + fundamentals table the reliance/itc/infy sessions produce.

### R2 — [P0, A] REGRESSION: "buy at open + sell +3%" collapses to the banned 09:30 downgrade
- **Where:** `backend/services/chat_service.py` ASK_USER/refusal fallback; `backend/workflows/propose.py`
  / `trigger.market_relative_time(anchor='open')` assembly; contract at
  `backend/prompts/system.md:1403-1417`.
- **Root cause:** `propose_workflow` was invoked (4 LLM calls) but the at-open entry branch failed
  to assemble, and the turn collapsed to `render_hint=ask_user` with the exact "every morning at
  09:30 I check the price" downgrade the prompt names a **hard error / capability theatre** — on the
  same BAJAJ-AUTO example the prompt is built around. Buy-at-open was a Round-1 fix; it re-regressed.
- **Change:** verify `trigger.market_relative_time(anchor='open')` is reachable from
  `propose_workflow`; remove the 09:30 fallback path; add a guard so any "at open"/"at the open"
  message can never resolve to `ask_user` or a 09:30 cron — force the two-branch card.
- **Evidence:** `bajajauto_buy_open_sell_3pct` (A-FAIL 2 / B-FAIL 1), qty=5 given, no card emitted.
- **Ideal:** one `workflow_draft_card`: ENTRY `market_relative_time(anchor=open)` → buy 5; EXIT
  `unrealised_pct>=0.03` → sell. No refusal, no 09:30.

### R3 — [P0, A] REGRESSION: notify-only alert bounces to ASK_USER on a fully-specified prompt
- **Where:** `backend/services/chat_service.py` ASK_USER gate; contract at
  `backend/prompts/system.md:598-624` (the **verbatim** AXISBANK example at :617).
- **Root cause:** "just alert me when AXISBANK crosses 1300, don't buy anything" fires TWO stacked
  hard gates (ALERT-VERBS-ROUTE-TO-NOTIFY + NO-TRADE-MARKERS-OVERRIDE) yet the model emitted a
  spurious ASK_USER ("in-app alert only?") — in-app is the **only** channel (`:1301`), so there is no
  ambiguity to clarify. ASK_USER is firing as an over-eager escape hatch when the build is fully
  specified. (Quantity is NOT re-asked — that part of the Round-1 fix held; this is a narrower
  regression than the original order-misroute.)
- **Change:** tighten the ASK_USER gate so an alert verb + price + a no-trade marker never resolves
  to `ask_user`; emit `propose_dsl_workflow(action_kind='notify_only')` and disclose the channel in
  read-back text.
- **Evidence:** `axisbank_alert_not_order` (A-FAIL 5 / B-PARTIAL 2), empty `ask_user` card.
- **Ideal:** notify_only card + "Watching AXISBANK — I'll alert you the moment it crosses above
  ₹1,300. No order is placed (in-app alert)."

### R4 — [P0, A] REGRESSION: vague named-template build (iron condor) refuses → ASK_USER
- **Where:** `backend/services/chat_service.py:1527-1539` (ask_user escape-hatch always appended);
  `backend/agents/tool_executor.py:1697` (`_build_option_strategy` already supports zero-strike
  delta-default build); contract verbatim at `backend/prompts/system.md:1123-1140`.
- **Root cause:** the system prompt mandates the exact behavior verbatim ("VAGUE MODIFIERS ARE NOT
  MISSING INPUTS", the near-identical iron-condor example, "NEVER ASK_USER for a center strike or
  wing width") yet the model still emitted ask_user. **Prose guidance alone is insufficient.** The
  SUGGEST and CRITIQUE paths silent-default correctly; only the explicit BUILD path bounces.
- **Change:** deterministic guard — when intent is OPTIONS/ORDER_FNO and the message names a known
  template (iron_condor/iron_butterfly/straddle/strangle/spread/condor) **with** an underlying,
  suppress the ask_user escape hatch for that turn OR auto-convert a model ask_user into
  `build_option_strategy(template, underlying, expiry=<this>)` — mirror the existing critique
  silent-default.
- **Evidence:** `nifty_build_iron_condor` (A-FAIL 3 / B-FAIL 2): `render_hint=ask_user`,
  `raw_keys=['_render_hint']`, no card.
- **Ideal:** four delta-default legs in an `option_strategy_card` + "0.20-delta shorts, 0.10-delta
  wings, 1 lot — say widen / next expiry to change" + credit/max-profit/max-loss/breakevens.

### R5 — [P0, A+B] Unsupported-rail boundary silently dropped (sentiment auto-sell)
- **Where:** `backend/services/chat_service.py` + `tool_router.py`; contract at
  `backend/prompts/system.md:335-345` (line 345 is the exact "sell if sentiment turns negative" row);
  the only real news trigger is `backend/workflows/schemas.py:204 TriggerEventConfig` (keyword/event,
  not sentiment polarity — there is **zero** sentiment code in `backend/`).
- **Root cause:** asymmetric obedience — the richly-imperative US-equity row (`:349`) is followed
  perfectly (us_adr PASS) but the terse sentiment row (`:345`) is dropped entirely. The
  value-collecting reflex (ask quantity) overrode the honesty contract, **affirming a fabricated
  capability** on an auto-execute path.
- **Change:** gate ASK_USER behind a rail-validity check — never ask for a field before confirming
  the rail is supported. On detecting sentiment/"mood"/"tone" polarity triggers, FIRST emit the
  boundary ("Pivot doesn't run sentiment NLP"), then offer the keyword-event trigger and ask for
  `keyword_set`/`event_description`. Add a per-turn eval assertion: sentiment/ADR/UPI prompts MUST
  contain a boundary phrase + a named alternative BEFORE any value question.
- **Evidence:** `news_sentiment_autosell` (A-FAIL 2 / B-FAIL 2): "How many ADANIENT shares should I
  sell when the news turns negative?" — no boundary, no alternative.
- **Ideal:** "Pivot doesn't run news-sentiment NLP. The closest real thing is a keyword-headline
  trigger: I watch ADANIENT headlines for terms you choose (SEBI, probe, downgrade…) and register a
  sell you confirm. Which keywords, and how many shares?"

### R6 — [P1, A+B] REGRESSION: confusion turn after an ASK_USER MENU re-dumps the menu verbatim
- **Where:** routing in `backend/services/tool_router.py` / `chat_service.py`; contract at
  `backend/prompts/system.md:455-462` (the "I don't understand → TEACH, don't repeat" clause).
- **Root cause:** the clause is honored on the **answer/table** path (`screen_then_dont_understand`
  teaches beautifully) but **not** on the **ASK_USER-menu** path — when T0 was itself a menu and T1
  is "I don't understand … which did you use and why", the model re-asks the identical 3-option menu
  with zero explanation and a false premise ("what you just set up" — nothing was set up).
- **Change:** deterministic pre-LLM intercept — when the prior assistant turn was an ASK_USER menu
  AND the user's message matches a confusion/meta pattern (`i don't understand|what do you mean|
  which .* did you use|why that`), route to a TEACH reply class that forbids re-emitting the same
  ASK_USER (reject duplicate-question output), and force (1) "nothing is set up yet", (2) explain
  one option with an example, (3) one yes/no.
- **Evidence:** `i_dont_understand_then_clarify` (A-FAIL 3 / B-FAIL 2).
- **Ideal:** "Good question — I haven't picked one yet. RSI(14)<30 means the stock has fallen hard
  and may be oversold; below the 50-day means weak vs ~2 months… For a simple dip-buy, RSI(14)<30 is
  the common start. Use that?"

### R7 — [P1, A+B] Rupee-notional → share-count conversion unbuilt; bot deflects on AMEND turns
- **Where:** `backend/services/chat_service.py:1406-1457` (`_DEPENDENT_INTENT_RE` has no Hinglish
  amendment verbs or "NNNN ka" cue); contract at `backend/prompts/system.md:84-89` (mandates the
  conversion, "Do NOT refuse the build over sizing").
- **Root cause:** two layers — (a) the Hinglish resize ("nahi"/"kharido"/"12000 ka") isn't caught by
  `_DEPENDENT_INTENT_RE`, so the amendment isn't forced; (b) even on the re-emit the model didn't
  apply notional→shares and **punted to manual card editing while narrating a false "Updated draft
  is on the card"** (honesty/no-fake-success violation). This is a standard broker feature (Kite
  Amount toggle) and Pivot already fetches live price.
- **Change:** (1) extend `_DEPENDENT_INTENT_RE` with `nahi(?:\s+\d+)?`, `kharid(?:o|lo)`,
  `bech(?:\s+do)?`, `\d+\s*(?:ka|ki|ke)\b`; (2) on a rupee-notional resize compute
  `shares=round(amount/get_live_price)` and inject; (3) post-emit guard: if a resize was requested
  but quantity is unchanged, treat as a failed amend and retry — never let the model say "Updated"
  when the field didn't change.
- **Evidence:** `hinglish_then_resize_notional` (A-FAIL 2 / B-FAIL 2): card still `quantity:15`.
- **Ideal:** "₹12,000 ÷ ~₹950 = 12 shares. Updated: buy 12 TATAMOTORS on a 5% dip, exit +7%."

### R8 — [P1, A+B] Bank screen ranked & framed on the wrong axis (P/E-led)
- **Where:** response columns; contract at `backend/prompts/system.md:164-165` + `:209-223`
  (banks lead P/B + ROE; render `Rank | Name | P/B | ROE | P/E`).
- **Root cause:** mis-routed to `analytical_short` (see R1) AND axis-blind: columns lead with P/E
  and the rank is not P/B-led (ICICIBANK ranked #2 despite the worst P/B 2.52). The shown order does
  match a ROE/PE composite, so it is **defensible** but the **stated bank contract is P/B-led** and
  no sort key is declared.
- **Change:** route bank/screen asks to the structured budget (R1) and add a screen sub-hint: "for a
  bank screen, RANK and COLUMN-ORDER on P/B then ROE; render `Rank|Name|P/B|ROE|P/E`; state the sort
  key; add a Cheap+Quality flag column and a defended single pick."
- **Evidence:** `screen_cheap_high_roe_banks` (A-FAIL 4 / B-PASS 6).
- **Ideal:** P/B-led ranked table with a Flag column ("Cheap+Quality") and a one-line defended pick.

### R9 — [P2, B] F&O prose is widget-dependent and thin; no ATM-band table, one-sided OI read
- **Where:** `backend/prompts/system.md` option-chain branch (`:1095` ATM-band table) + the
  card-prose contract (`:1142-1146`).
- **Root cause:** all 4 F&O text replies are short (49–136 words) with **zero markdown tables** even
  though the chain branch mandates a `Strike | Call OI | Put OI | Read` table. Chain reads quote
  max_pain/PCR/expected-move (good, grounded) but never show WHICH strikes carry the OI, ignore
  total_call_oi/total_put_oi/pcr_volume in the payload, and give no best-in-class caveats (low
  predictive power, needs high OI+volume, current-expiry-only). Suggest/critique name a defined-risk
  alternative but never quantify it (no concrete spread strikes/credit/max-loss). POP can silently
  drop from the caption even when in the card.
- **Change:** make the ATM-band table MANDATORY (surface top-3 call-OI + top-3 put-OI strikes from
  the payload), quote pcr_oi AND pcr_volume, add a required closing caveat line; enforce a POP floor
  in the card-prose validator (assert prose contains POP when `card_digest.pop` is non-null); when
  suggest emits ≥2 candidates, render a 2-row comparison table.
- **Evidence:** `nifty_chain_max_pain_pcr` (B-PARTIAL 6), `bhartiartl_call_chain_stopword`
  (B-PARTIAL 6), `banknifty_suggest_bullish`/`critique_naked_put_reliance` (no leg/comparison table).
- **Ideal:** 5–7 row ATM-band table + a bold two-sided read (resistance = top call OI, support = top
  put OI, max-pain, expected move) + caveat; suggest/critique quantify the alternative side-by-side.

### R10 — [P2, B] Multi-leg workflow drafts get a one-line text wrap (no per-leg table, no diff)
- **Where:** the post-draft text floor (applied to compares/screens but NOT to workflow drafts);
  card title regeneration in the draft-name builder.
- **Root cause:** when a `workflow_draft_card` builds correctly (basket, swap+stop, hinglish T0,
  analysis T1) the text degenerates to "Drafted — … activate the card": no total notional, no
  trigger value restated, no per-leg allocation table, no "Changed/Kept/Added" diff on amend turns.
  Stale legacy card TITLES also survive DSL mutations ("AXISBANK price below ₹4" frozen from the
  original "4%"→"₹4" mis-render).
- **Change:** raise the post-draft floor for workflow cards — baskets/multi-branch require a per-leg
  table + a trigger-restating lead sentence; on amend turns lead with "Changed: … / Kept: … /
  Added: …"; regenerate card title/name from the current DSL readback on each `propose_dsl_workflow`
  (never inherit the legacy `propose_workflow` name).
- **Evidence:** `basket_three_symbol_split` (B-PARTIAL 5, no ₹60k total/table),
  `swap_symbol_then_add_stop` (B-PARTIAL 5, title "AXISBANK price below ₹4").
- **Ideal:** 3-row allocation table (Symbol | Notional | Side, Total ₹60,000) + lead trigger
  sentence; amend turns show the explicit delta.

### R11 — [P2, B] Confirm/affirmative turns under-state the armed contract (within the floor)
- **Where:** affirmative confirm-turn text floor (`chat_service.py` `_PURE_AFFIRMATIVE_RE` path) —
  honoring the ≤2-sentence post-draft floor (`system.md:1271-1283`).
- **Root cause:** correctness is clean (no fresh-card re-emit, no loop) and the prompt rightly
  forbids re-listing steps; the residual is only that the confirm turn ("Click Save & activate") and
  the entry-only RSI draft don't add the one value the widget can't (what fires when the condition
  hits, or "this only ENTERS — add a stop?"). Polish-tier, not a grade-driver.
- **Change:** raise the text floor from echo → VALUE: whenever a widget/number is shown, require ≥1
  thing the widget cannot carry (1-line interpretation, honest missing-leg nudge, or a backtest
  next-step) — without violating buy-only-means-buy-only (`system.md:1308-1318`).
- **Evidence:** `amend_qty_then_confirm_register` (B-PASS 6 ⟲), `plain_rsi_agent_grasim` (B-PASS 6 ⟲).
- **Ideal:** "Drafted — GRASIM RSI(14)<30 → buy 10. Heads-up: it only enters; want a stop or a
  backtest first?"

### R12 — [P3, A] Frozen-prompt-number anchor on order-sizing clarifications
- **Where:** `backend/prompts/system.md:405-408` (hardcoded "EICHERMOT ~₹7,100" few-shot);
  `get_live_price` available in ORDER_CONDITIONAL (`tools.py:15`).
- **Root cause:** the ASK_USER question parrots the frozen few-shot price verbatim with **no**
  `get_live_price` call (`tools_called==['ASK_USER']`, `llm_calls==1`). It passes only because the
  constant happens to track LTP today; on a gap day it mis-anchors. Low severity (hedged, correct
  today, sanity-anchor not execution price) — kept as hardening, not a demote.
- **Change:** de-hardcode the few-shot to a placeholder (`<SYMBOL> ~₹<LTP>, so 100 shares ~₹<LTP*100>`)
  and add a fetch-then-ask directive: when bundling a ₹ size into an ASK_USER for a high-priced name,
  fetch LTP first; if unavailable, ask the unit question with no ₹ figure.
- **Evidence:** `hundred_of_eichermot_units` (A-PASS 6 ⟲).

### R13 — [P3, B] Ambiguity clarifiers ignore the discriminating qualifier the user gave
- **Where:** `backend/prompts/system.md:392-410` / example at `:481-482`.
- **Root cause:** "the Tata one that's been RUNNING lately" returns a generic list that **leads with
  TCS** (the worst 2026 Tata name — the antithesis of "running") and omits TITAN/TRENT (the actual
  outperformers), with no per-candidate return number. The number-anchoring discipline that makes
  the EICHERMOT clarifier excellent is not applied to entity-disambiguation.
- **Change:** when the user attaches a discriminating modifier ("running/up/falling/cheapest"), the
  ASK_USER MUST (1) order candidates by that signal via a quick recent-return fetch, (2) lead with
  the matching names, (3) append a per-candidate number, (4) offer a defended default. Replace the
  static "TCS, Tata Motors, Tata Steel" example.
- **Evidence:** `the_tata_one_entity` (B-PARTIAL 6).

### R14 — [P3, A] yfinance fallback payload has no `as_of` date; price freshness un-datable
- **Where:** `backend/agents/tool_executor.py:1206-1229` (`_get_live_price` yfinance branch returns
  `{symbol, ltp, change_pct, source}` with no date); relay format `system.md:229-235`.
- **Change:** add `as_of`/`as_of_date` (+ session flag) to the yfinance payload and surface it, so a
  frozen EOD is visibly dated rather than silently stale — without violating anti-fabrication.
- **Evidence:** `plain_price_kotakbank` (A-PASS 8) carries only "(yfinance, EOD)", no date. (The
  ₹381.70 vs real-world ~₹1900 is a verified yfinance.NS artifact, not a build fabrication.)

### R15 — [P3, A/harness] `## News`/empty-section leak + lossy card_digest
- **Where:** add a `_post_process` guard next to `_strip_internal_tool_leaks` (`chat_service.py:~191`)
  to strip a `## News` body matching `did(n't| not) (pull|fetch)|not using any headline`; harness:
  unpack `logic_card`/`option_chain_card` payloads in `card_digest` (currently `{_render_hint}` only),
  so widget faithfulness is directly assertable (false negatives avoided on chain max_pain/PCR/legs).
- **Evidence:** `infy_vs_tcs_which_better` prints a `## News` header with the banned "I did not pull
  news" phrasing (cross-listed B defect, `system.md:140-143,156-157`); chain & logic_card sessions
  serialise empty digests.

---

## 4. What is genuinely HOLDING from Round 1 (do not re-touch)
- max_pain/PCR/expected-move **computed and surfaced**, grounded, no fabrication
  (`OptionChainCard.tsx:332-339`, present in raw_keys).
- Named-symbol valuation TABLES, head-to-head TABLE + defended winner, depth floor, lead-with-number
  fire correctly **when the analysis class is selected** (reliance/itc/infy = best-in-class).
- Hinglish FIRST build, stacked-affirmative register (no fresh-card re-emit), %-from-ref multiplier,
  multi-leg basket split with preserved total, trailing-stop honest disclosure, price-level-vs-time
  GTT disambiguation, option-chain stopword routing, vanilla RSI build (0-LLM skeleton path).
- Confusion→teach on the **answer/table** path (`screen_then_dont_understand`).
- US-equity unsupported rail (boundary + named MON100 proxy + buildable SIP).
- Quantity is **never** silently defaulted (eichermot, amend_qty T0, axisbank holds qty gate).

## 5. What "excellent" looks like (updated for Round 2)
1. **Classify, then the template does the rest.** Any single-name read, index-trend read, or screen
   must route to the `analysis`/`screen` budget so it inherits the returns ladder + SMA %-distance
   stack + fundamentals/ranked table. Bimodal depth is the #1 quality residual — close the
   classifier gap (R1) and 1 FAIL + 2 PARTIAL flip toward PASS.
2. **Buildable named shapes BUILD, never clarify.** Iron-condor, buy-at-open, notify-only-alert are
   all documented canonical buildables; a deterministic guard (not prose) must suppress the ASK_USER
   escape hatch when the named structure + required params are present (R2–R4).
3. **Honest boundary FIRST on every unsupported rail.** Each rail row needs the same imperative
   weight: state the boundary, name the nearest real thing with a concrete field, then (only if the
   user opts in) collect values. Never ask a field that presupposes the missing capability (R5).
4. **F&O text earns its own keep beside the card.** Mandatory ATM-band table with the OI strikes
   that drive max-pain, two-sided support/resistance, pcr_oi + pcr_volume, a caveat line, POP in the
   caption, and a quantified defined-risk alternative for suggest/critique (R9).
5. **Workflow drafts get a structured handoff, not an echo.** Per-leg allocation table + restated
   trigger + total for baskets; a "Changed/Kept/Added" diff on amend turns; card titles regenerated
   from the live DSL readback; the text adds ≥1 thing the widget can't (R10–R11).
6. **Conversions and amendments are first-class.** Rupee-notional→shares is computed (not punted),
   Hinglish amendment verbs are recognised, and the bot never narrates "Updated" when nothing
   changed (R7).
7. **Clarifiers leverage the signal the user gave** — qualifier-ordered candidates with anchored
   numbers (R13); every ₹ anchor in a question is fetched, never parroted (R12).
