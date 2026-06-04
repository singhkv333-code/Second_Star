I have confirmed the architecture. The ASK_USER suppression for F&O defaults relies entirely on the system-prompt directive ("NEVER ASK_USER for expiry/strike when a default exists") — there is **no deterministic guard** forcing the F&O tools when defaults exist, unlike the order/backtest paths which use `tool_choice="required"` narrowing. That is the structural reason F&O ASK_USER friction recurs (6 of the 8 sub-PASS sessions). I have everything I need.

# F&O Chat Eval — Cross-Cutting Failure Analysis (run_20260604_154826, 34 sessions)

**Headline:** 25 PASS / 5 FAIL / 4 PARTIAL. Every single non-PASS reduces to **one root cause: the model emits `ASK_USER` (or a wrong macro) on a turn where a documented default existed and the card/answer should have been produced.** All four categories that failed (suggest, critique, metrics, automation) share this spine. Build/amendment plumbing and honesty are genuinely strong — none of the failures are amendment-routing or fabrication-of-numbers.

---

## Cross-cutting patterns, ranked by impact

### P0 — "ASK_USER when a default exists" friction (the dominant failure mode)
**Sessions:** `volatile_event_play` (FAIL), `oversized_position_warning` (FAIL), `max_pain_pcr` (FAIL), `iv_trigger_strangle_paper` (FAIL, T0), `naked_put_should_i` (PARTIAL), `expiry_day_squareoff_nudge` (PARTIAL), `live_book_register_honesty` (PARTIAL, T1) — **7 of 9 non-PASS sessions.**

Every required slot was supplied or defaultable, yet the bot returned `render_hint=ask_user` with `card_digest={hint: ask_user}` and **no card/number/draft**. This is a by-name prompt violation: `system.md:830-833` says "Defaults — propose, don't interrogate … NEVER ASK_USER for expiry/strike when a default exists." The system relies *purely* on prompt suasion to suppress this — there is **no deterministic guard**. Contrast the order/backtest paths (`chat_service.py:2743-2802`) which narrow tools and set `tool_choice="required"` to force emission. The F&O surface has no equivalent: the router block (`tool_router.py:603-619`) surfaces all five option tools with no post-routing "defaults present → forbid ASK_USER" gate. **Root cause is structural, not per-session.**

### P1 — Capability denial / dishonest "not wired" on option-metric workflows
**Sessions:** `max_pain_alert` (FAIL — "max pain isn't something I can read directly here"), `iv_trigger_strangle_paper` (FAIL — generic brochure instead of draft).

The build *fully supports* these: `max_pain` is in the DSL schema enum (`schema.py:468`), computed in `option_metrics.py:136-148`, and `system.md:858-863` explicitly says "Never claim an IV/expiry trigger 'isn't wired'." Yet the bot denied capability and downgraded to an ATM-strike proxy. This is the **inverse of the honesty the category rewards** and the most damaging class because it teaches the user a false ceiling. **Routing contributor:** the F&O regex bucket (`tool_router.py:603-619`) routes option keywords to the *analysis* tools (chain/suggest/build/critique/greeks) and does **not** co-surface `propose_workflow`/`propose_dsl_workflow`. When a prompt is "alert/automate when [option-metric]…", the automation tools may not be in scope, so the model rationalizes a denial or a brochure.

### P2 — Phantom-draft confabulation (downstream of P0/P1)
**Sessions:** `iv_trigger_strangle_paper` (T1 — "update the … workflow" that was never created), `volatile_event_play` (T1 — silently treated a max-loss question as consent to an alert branch).

Mechanically clean to explain: turn 0 emitted ASK_USER, so nothing entered `_STASH_DRAFT_TOOLS` (`chat_service.py:704-723`). Turn 1's amendment hint then references a draft that does not exist, and the model invents prior state. **This is a *consequence* of P0/P1, not an independent bug** — fix the turn-0 emission and these evaporate. The stash/amendment machinery itself is correct (all genuine amendment sessions PASS).

### P3 — Wrong-shape: one-shot card vs. recurring workflow (and inverse)
**Sessions:** `live_book_register_honesty` (PARTIAL — "every monthly expiry" automation answered with a one-shot `option_strategy_card`), `volatile_event_play` (notify-workflow for a strategy question), `calendar_spread_unsupported` (FAIL — `build_option_strategy` called against a non-existent template + false "I can build the calendar spread" claim).

Two distinct mis-mappings: recurring-automation → one-shot card, and unsupported-template → false build. The scope rule "calendar/diagonal NOT in v1; say so + offer nearest single-expiry" (`system.md:861-862`) was inverted into a capability claim.

### P4 — Missing prose numbers / muddled risk anatomy (polish, lowest impact)
**Sessions:** `covered_call_holding_context` (PARTIAL — inverted "short call creates uncapped downside" + no "assuming you hold ~250 shares" probe), `live_book_register_honesty` (T0 — POP/max-loss/breakevens on the card but absent from prose, violating the "Card prose contract" at `system.md:837-842`).

These are the only failures where the *card was correct* but the prose under-delivered. Real, but cosmetic relative to P0-P3.

---

## What is genuinely strong (do not regress)

- **Amendment re-emit is rock-solid.** `bearish_two_weeks_amend_lots`, `iron_condor_build_amend_strike`, `hinglish_casual_suggest`, `bull_call_spread_explicit_strikes` all amend via `build_option_strategy` with no ASK_USER confirm loop, numbers recomputed cleanly. The `_option_draft_spec` compaction (`chat_service.py:754+`) and `_MACRO_AMENDMENT_TOOLS` inclusion of `build_option_strategy` work as designed.
- **Multi-turn context retention.** `chain_then_greeks_followup` carried the exact contract `NIFTY26JUN23500CE` across turns; `portfolio_greeks_flow` retained the equity-only context.
- **Honesty boundaries (when the bot doesn't deny capability).** `straddle_then_register_ask`, `futures_execution_honest`, `mcx_execute_must_refuse`, `mcx_crude_chain_research`, `ivp_honesty`, `guaranteed_profit_pushback` — register-not-execute, MCX research-only, no fabricated IVP, SEBI-appropriate pushback all nailed.
- **Expiry realism.** `banknifty_chain_next_expiry` resolved to the correct post-Sep-2025 monthly (2026-07-28), no fabricated weekly Thursday.
- **No number fabrication anywhere.** Every card's max-loss/POP/breakeven is internally consistent. The only `fabrication:true` flags are *state* confabulation (P2), which is downstream of P0/P1.

---

## Next-iteration instruction list for the engineer

**1. [P0, highest ROI] Add a deterministic F&O ASK_USER suppressor.** In `chat_service.py`, mirror the order/backtest `tool_choice="required"` narrowing (pattern at `chat_service.py:2743-2802`) for the F&O surface. When the router has selected the option tools AND the prompt carries underlying+view OR underlying+structure (defaults cover the rest), strip `ASK_USER` from the offered set / force `tool_choice="required"` onto `{get_option_chain, suggest_option_strategy, build_option_strategy, critique_option_strategy, get_portfolio_greeks}`. This is the single change that recovers `volatile_event_play`, `oversized_position_warning`, `max_pain_pcr`, `naked_put_should_i`, `expiry_day_squareoff_nudge`, and turn-0 of `iv_trigger_strangle_paper`.

**2. [P1+P3] Co-surface workflow tools in the F&O router bucket when the ask is automation-shaped.** `tool_router.py:603-619` routes option keywords only to the five analysis tools. Add a sibling rule: if an option keyword co-occurs with an automation cue (`alert|automate|notify|when … (iv|max pain|pcr|expected move|dte|expiry)|every (monthly|weekly) expiry`), include `propose_dsl_workflow`/`propose_workflow` in scope. This kills the dishonest "max pain isn't wired" denial (`max_pain_alert`) and the brochure-instead-of-draft (`iv_trigger_strangle_paper`) and gives `live_book_register_honesty`'s recurring ask the right shape.

**3. [P1] Tighten the tool descriptions in `agents/tools.py` and the prompt block.** The `propose_dsl_workflow` / `propose_workflow` schema description should *explicitly enumerate* the supported `option_metric` triggers (`iv_atm, pcr_oi, pcr_volume, max_pain, expected_move_pct, straddle_price, rr_25d, fly_25d, term_slope, vrp, greeks, dte`) and `trigger.expiry_day`, so the model never rationalizes a denial at tool-selection time. The schema is the source the model trusts more than prose; the prompt's "never say not wired" line is being overridden by an underspecified tool description.

**4. [P3] Add a critique-vs-build router discriminator + unsupported-template guard.** "should I / is this smart / critique this" must route to `critique_option_strategy` (regex at `tool_router.py:346` exists for risky/safe but `naked_put_should_i` and `oversized_position_warning` still hit `build`/`ask_user`). Separately, before `build_option_strategy` dispatch, validate the template against the v1 set in `option_strategy_service.py`; if `calendar|diagonal|ratio`, deterministically return the "not supported + nearest single-expiry" decline instead of letting the LLM claim "I can build it" (`calendar_spread_unsupported`).

**5. [P0/critique] Prompt: route screaming-risk specs straight to the card, warning-first.** Reinforce `system.md:823-827` with a hard rule: a fully-specified critique target (template+underlying+size, e.g. "short straddle 50 lots") must emit `critique_option_strategy` with the sizing/unlimited-loss flag SURFACED FIRST — never an ASK_USER asking whether the stated size "is the size you want." This is `oversized_position_warning`'s exact failure.

**6. [P2, no code if 1+2 land] Phantom-draft guard as a safety net.** In the amendment-hint builder (`chat_service.py:726-742`), assert the active draft actually exists before injecting an amendment hint; if the prior turn was ASK_USER (no stash), do NOT phrase the next turn as "update the existing X." Cheap defensive check; should become unnecessary once P0/P1 stop the turn-0 ASK_USER.

**7. [P4] Covered-call holding probe + prose-number lint.** For `covered_call`, the suggest/build handler should name the assumed share count in prose ("assuming you hold ~250 RELIANCE shares per lot") and the risk text must attribute downside to the stock leg, upside-cap to the short call. Optionally add a post-generation lint: if `render_hint=option_strategy_card` and prose lacks max-loss/max-profit/POP/breakeven/capital, force a regeneration (enforces the `system.md:837-842` prose contract — recovers `live_book_register_honesty` T0).

**Sequencing:** Items **1 and 2 alone** flip 4 FAILs and 3 PARTIALs (≈80% of the lost ground) because P2 is downstream of them. Do those first, re-run once per the no-repeat-eval-runs rule, then layer 3-7.

Relevant files: `/Users/karanveersingh/Downloads/Second_Star/pivot/backend/services/chat_service.py` (ASK_USER gating ~L405/L930/L2743-2802; stash/amendment L700-770), `/Users/karanveersingh/Downloads/Second_Star/pivot/backend/services/tool_router.py` (F&O bucket L603-619), `/Users/karanveersingh/Downloads/Second_Star/pivot/backend/agents/tools.py` (workflow tool schemas), `/Users/karanveersingh/Downloads/Second_Star/pivot/backend/services/option_strategy_service.py` (template-set guard), `/Users/karanveersingh/Downloads/Second_Star/pivot/backend/prompts/system.md` (L805-870 F&O scope), `/Users/karanveersingh/Downloads/Second_Star/pivot/backend/market/option_metrics.py` + `/Users/karanveersingh/Downloads/Second_Star/pivot/backend/workflows/dsl/schema.py` (confirm max_pain/iv_atm support — they are wired).