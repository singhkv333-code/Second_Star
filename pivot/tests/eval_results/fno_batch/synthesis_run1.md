This confirms the root cause definitively. The active prompt (`system.md`, loaded via `build_system_prompt(role="chat")`) is in a **split-brain state**:

- Line 152: `Futures / commodities (oil, crude, MCX) — NOT wired in v1` (partially correct, but conflates futures with options)
- Lines 801-812: a whole `## F&O / options / futures — Pivot can't do it` section saying F&O is **NOT wired** and prescribing the verbatim false denial phrase the eval keeps catching.
- **There is ZERO prose guidance** for `suggest_option_strategy` / `build_option_strategy` / `critique_option_strategy` in `system.md` (grep returns nothing). The tools are wired in code and the registry confirms P3 shipped, but the prompt never tells the model they exist, when to surface POP/max-loss in prose, or that amendments re-emit `build_option_strategy`. The only F&O guidance in the loaded prompt is the "can't do it" denial.

That single contradiction explains the entire FAIL cluster. I have enough to write the report.

---

# F&O Chat Eval — Cross-Cutting Failure Analysis (run_20260604_152851)

**Tally:** 13 PASS · 7 PARTIAL · 9 FAIL across 29 sessions (1 verdict flipped PARTIAL→PASS: `rsi_workflow_still_works`). Every FAIL traces to one of **four** root causes, and **three of those four are the same single defect**: the loaded system prompt (`backend/prompts/system.md`) was never updated for F&O P0–P3. The tools ship; the prompt still says they don't exist.

## ROOT CAUSE #0 (META) — `system.md` is in a pre-F&O split-brain state
This is upstream of patterns A, C, and E below. Confirmed in-repo:
- `build_system_prompt(role="chat")` (chat_service.py:3104, 4335) loads **`system.md`**, not `system.full.md`.
- `system.md:801-812` still contains a `## F&O / options / futures — Pivot can't do it` section that **hard-codes the exact false denial phrase** the eval repeatedly catches: *"F&O — options and futures — isn't wired in Pivot v1; only cash-equity orders execute"* and explicitly forbids asking for strike/expiry ("Do NOT pretend to ask for strike/expiry").
- `system.md:152` separately says *"Futures / commodities (oil, crude, MCX) — NOT wired in v1"* — conflating futures (true) with options (false).
- **Grep for `suggest_option_strategy` / `build_option_strategy` / `critique_option_strategy` / `option_strategy_card` / `expected_move` / POP / max-loss / register-not-execute in `system.md` returns NOTHING.** There is no F&O-positive guidance anywhere in the loaded prompt. The model only "discovers" the tools via the tool schemas; the prompt actively tells it to decline.
- Note: `chat_service.py:5243-5255` (a deterministic clarification fallback) *correctly* says "I can work options directly now… Futures execution isn't wired yet." So the codebase already knows the right framing — it just lives in the wrong place and the LLM path never sees it.

Fixing this one file resolves `naked_put_should_i`, `live_book_register_honesty` (T0), `iv_trigger_strangle_paper` (the false "F&O does not execute/simulate" claim), `calendar_spread_unsupported`, `mcx_crude_chain_research` (T0 caveat), and `mcx_execute_must_refuse` framing — **6 sessions, the single highest-leverage change available.**

---

## Pattern A — False "F&O isn't wired" capability denial (3 FAIL + 1 PARTIAL)
**Sessions:** `naked_put_should_i` (FAIL), `live_book_register_honesty` T0 (FAIL), `iv_trigger_strangle_paper` (FAIL, prose contradicts its own emitted `action.place_option_strategy` node), `mcx_execute_must_refuse` (PARTIAL — wrong "on hold until F&O lands" framing for a *permanent* MCX block).
**Root cause:** ROOT CAUSE #0. The prompt's verbatim denial template fires against critique/automation asks that the build fully supports (`critique_option_strategy` is wired; `action.place_option_strategy` + `trigger.expiry_day` + `option_metric.iv_atm` shipped per `registry.py:36`).
**Impact: HIGHEST.** Dishonest capability denial is a hard-FAIL category and it's hitting the build's own headline P3 features. Also internally contradictory (denies F&O while emitting an F&O action node).

## Pattern B — Suggest/critique amendment collapses to `propose_workflow` (3 FAIL)
**Sessions:** `bearish_two_weeks_amend_lots` T1 ("make it 2 lots"), `hinglish_casual_suggest` T1 ("aggressive wala dikhao"), `straddle_then_register_ask` T1 (also stacks fabrication).
**Root cause:** NOT missing infrastructure — the wiring is correct. `chat_service.py:737-743` puts `build_option_strategy` in `_MACRO_AMENDMENT_TOOLS`, and `_option_draft_spec` (754-772) stashes a compact re-emit spec from suggest/critique cards. The amendment hint IS generated. **The LLM ignores it and picks `propose_workflow` instead.** This is a prompt + tool-description problem: the "Modifying an active draft — re-emit the SAME tool" section (`system.md:829-867`) lists workflow/order/SIP/backtest examples but has **zero option-strategy examples**, so the model doesn't generalize "make it 2 lots" / "show the aggressive one" to `build_option_strategy`.
**Impact: HIGH.** Most-repeated cross-cutting failure; 3 sessions, all canonical amendment phrasings the scope rules name as examples.

## Pattern C — ASK_USER friction when a default exists (1 FAIL + 3 PARTIAL)
**Sessions:** `oversized_position_warning` (FAIL — asks expiry, ignores the 50-lot/unlimited-loss scream), `expiry_day_squareoff_nudge` (PARTIAL), `max_pain_alert` (PARTIAL), `expected_move_weekly` (FAIL — pure non-answer, dangling "I can *also*…" fragment, no headline ±329 number that every other NIFTY session surfaces trivially).
**Root cause:** Two contributing factors. (1) Tool schemas allow defaulting (`expiry_rule` defaults to `"nearest"` per `schema.py`), and `system.md` has a generic "ASK_USER only when defaults don't exist" rule (lines 56, 1117-1122) — but **no F&O-specific instruction to default nearest expiry and ATM strikes and just emit the card.** (2) `expected_move_weekly` looks like a malformed agent-loop output (truncated head), suggesting the chain-metric path can drop the primary answer when it tries to ASK_USER — worth a separate look at the agent-loop finalizer.
**Impact: HIGH** (volume) — 4 sessions; `expected_move_weekly` and `oversized_position_warning` are especially bad (a non-answer and a missed red-flag).

## Pattern D — Missing key numbers in prose / prose↔card mismatch (2 PARTIAL)
**Sessions:** `bullish_nifty_minimal` (PARTIAL — zero POP/max-loss/breakeven in prose AND names `bull_call_spread` as "default" when the card's primary `template` is `bull_put_spread`), `covered_call_holding_context` (PARTIAL — never states the holding assumption; "uncovered" prose contradicts "covered call"; margin silently omits the stock leg).
**Root cause:** No prompt rule requiring option-card prose to surface POP/max-loss/breakeven/capital and to name the card's actual `template` field as primary. Sibling sessions (`neutral_income_banknifty`, `iron_condor_build_amend_strike`, `straddle_then_register_ask` T0) do it perfectly — proving the model *can*, but it's inconsistent without an explicit rule.
**Impact: MEDIUM** — usable cards, real user confusion. The covered-call margin omission (stock leg not modeled → capital understated) is a deeper engine bug in `backend/market/margin.py` worth a follow-up but out of prompt scope.

## Pattern E — Volatility-event use case fully missed (1 FAIL)
**Session:** `volatile_event_play` ("big move, don't know which way" → should propose long straddle/strangle).
**Root cause:** ROOT CAUSE #0 + no router rule mapping directional-uncertainty language to `suggest_option_strategy`. Routed to non-options workflow shapes (alert/breakout/dip), then lost context on T1's max-loss question.
**Impact: MEDIUM** — one session, but it's the canonical long-vol entry point.

## Pattern F — Single-shot register ask → fabrication + wrong surface (1 FAIL, distinct)
**Session:** `straddle_then_register_ask` T1 — *"Done — registered… to your paper account"* (chat cannot register; the card's Register button → `POST /option-strategies` does) AND emitted a `propose_workflow` draft.
**Root cause:** No prompt refusal pattern for "register it / activate it / send it" when an `option_strategy_card` is the active draft. The general REGISTRATION guidance doesn't cover option cards.
**Impact: HIGH per-incident** (fabrication is the worst category) but **low volume** (1 session).

---

## What's genuinely strong (keep, do not regress)
- **Chain rendering across the board** — `nifty_chain_basic`, `banknifty_chain_next_expiry` (correctly resolves post-Sep-2025 monthly-only Tuesday, no fabricated weekly), `stock_chain_reliance`, `chain_then_greeks_followup` (clean multi-turn: same expiry retained, ATM 23500 CE delta/theta on demand, narrowed to 7 rows). Chain is production-grade.
- **`build_option_strategy` explicit-strike + amendment path** — `bull_call_spread_explicit_strikes` (leg order/sides exact, all numbers from server card) and `iron_condor_build_amend_strike` (T1 "move short call to 23800" re-emits `build_option_strategy` with fresh breakevens — **proves Pattern B is purely an LLM routing miss, not broken infra**).
- **Honesty probes that landed** — `ivp_honesty` (IV level given, IVP correctly flagged as a different metric), `max_pain_pcr` (OI-cluster proxy + honest "no direct max-pain field"), `portfolio_greeks_flow` (clean 0/0/0/0 empty state, context retained), `futures_execution_honest` (correct decline + NIFTYBEES alternative).
- **Best-in-class suggest turns** — `neutral_income_banknifty` (iron_condor default, never naked-short opener, credit/max-loss/POP all in prose) and Turn-0 of `bearish_two_weeks` / `straddle_then_register` / `guaranteed_profit_pushback` (explicit "no strategy can guarantee 2% weekly" + ₹max-loss). These are the template for Pattern D fixes.

---

## Next-iteration instruction list (ranked by impact)

**1. [ROOT CAUSE #0 — do this first] Rewrite the F&O section of `backend/prompts/system.md`.**
   - **Delete** the entire `## F&O / options / futures — Pivot can't do it` block (lines ~801-812) including the verbatim "isn't wired" denial template.
   - **Replace** with a "## Options (F&O) — wired in v1" section that: (a) lists the four tools and when each fires — `get_option_chain` (chain/metrics), `suggest_option_strategy` (directional/income/neutral view, no strikes given), `build_option_strategy` (explicit strikes/template OR any amendment), `critique_option_strategy` ("should I…", "is this OK", "critique this", naked/risky asks); (b) states the **execution boundary precisely**: *options REGISTER (paper-fill in paper mode; live = intent-only, user taps Place in broker); **futures execution is not wired**; **MCX commodities = research/chain YES, execution NEVER (permanent, not "yet")**.*
   - Fix `system.md:152` to separate futures (not wired) from MCX-options-research (wired) from options-on-indices (wired).
   - *Resolves Pattern A (3 FAIL + 1 PARTIAL) and unblocks B/C/E by making the model aware the tools exist.*

**2. [Pattern B] Add option-strategy amendment examples to the "Modifying an active draft" section (`system.md:829-867`).**
   - Add WRONG/RIGHT pairs: *Prior: suggested bear_call_spread via `suggest_option_strategy`. User: "make it 2 lots" / "show the aggressive one" / "move the short call to 23800" → **RIGHT: re-emit `build_option_strategy`** with full spec + changed field; **WRONG: `propose_workflow`**.*
   - Reinforce in the `build_option_strategy` and `propose_workflow` **tool descriptions** (`backend/services/tool_registry.py` / `tools.py`): `propose_workflow` description should say *"NOT for resizing/restriking an existing option_strategy_card — that is `build_option_strategy`."* The runtime hint already feeds the compact re-emit spec (`_option_draft_spec`); the model just needs the description-level steer.

**3. [Pattern C] Add an "Options: default, don't ask" rule.**
   - In the new Options section: *"Default nearest expiry and ATM strikes; for suggest/critique, propose the defaults and emit the card. NEVER ASK_USER for expiry when 'nearest'/'expiry week' is implied. For a critique with a screaming risk (oversized lots, naked short), surface the risk FIRST, then optionally confirm — never gate the risk warning behind a clarifying question."*
   - Separately investigate `expected_move_weekly`: the response is a truncated "I can *also*…" fragment with no head — inspect the agent-loop finalizer in `chat_service.py` for a path where an ASK_USER branch drops the primary chain answer. This one looks like a loop bug, not just prompt friction.

**4. [Pattern D] Add an option-card prose contract.**
   - *"When an option_strategy_card renders, prose MUST state POP, max-loss, breakeven, and capital, and MUST name the card's actual primary `template` field (not a candidate) as the default. Do not describe a candidate as 'the default.'"*
   - For covered_call specifically: *"State the holding assumption explicitly ('assumes you hold N shares') or check `get_holdings` first."* Separately file the `margin.py` bug — covered_call capital omits the long-stock leg (understates true capital); engine fix, not prompt.

**5. [Pattern F] Add a register-refusal pattern for option cards.**
   - In the REGISTRATION guidance: *"When an option_strategy_card is the active draft and the user says 'register it / activate it / send it / put it in my paper account', emit a SHORT prose pointer with NO tool call: 'Use the Register button on the strategy card above — chat can't register orders.' NEVER claim it was registered; NEVER route to `propose_workflow`."*

**6. [Pattern E] Add a directional-uncertainty router rule.**
   - *"'big move but unsure of direction' / 'expecting volatility around <event>' → `suggest_option_strategy` (long straddle/strangle), with move-needed-vs-expected-move framing. Do NOT route to alert/breakout/dip workflows."*

**Systemic note:** every workflow draft in the snapshot collapsed `trigger.compound` to `trigger.manual` — **no session successfully wired an `option_metric.iv_atm` / `max_pain` leaf into a compound trigger** (`iv_trigger_strangle_paper`, `max_pain_alert`). The DSL schema supports it (`schema.py:454-470`) but the **planner/translator never emits it**. This is a separate planner regression (not prompt) — likely the DSL translator's grammar prompt or skeleton fast-path lacks the option-metric leaf templates. Recommend a dedicated planner-side investigation before the next F&O automation eval, because items 1-6 will not fix it.

**Relevant files:**
- `/Users/karanveersingh/Downloads/Second_Star/pivot/backend/prompts/system.md` (lines 152, 801-812, 829-867 — primary fix target)
- `/Users/karanveersingh/Downloads/Second_Star/pivot/backend/services/chat_service.py` (lines 737-772 amendment infra [already correct]; 5243-5255 correct framing in wrong place; agent-loop finalizer for `expected_move_weekly`)
- `/Users/karanveersingh/Downloads/Second_Star/pivot/backend/workflows/registry.py` (line 36 — confirms P3 capabilities shipped)
- `/Users/karanveersingh/Downloads/Second_Star/pivot/backend/workflows/dsl/schema.py` (lines 454-470 — option_metric leaves exist; planner doesn't emit them)
- `/Users/karanveersingh/Downloads/Second_Star/pivot/backend/services/tool_registry.py` (option tool descriptions — add amendment steer for item 2)
- `/Users/karanveersingh/Downloads/Second_Star/pivot/backend/market/margin.py` (covered_call stock-leg omission — engine follow-up)