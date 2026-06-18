# Chat eval — directional options strategies + thematic baskets + clarify flows

**2026-06-17**, live `/chat/stream` on `:8000` (Azure `gpt-5.4-mini`). 20 cases. Cost/token figures are summed from server `llm.usage` log lines; latency is the per-case sum of turn `latency_ms`.

## Verdict: 12 PASS · 2 WEAK · 6 FAIL

## Aggregate stats

- **Total cost:** $0.2602 across 37 LLM calls (~$0.0130/case)
- **Total tokens:** 1,366,980 input (761,216 cached = 55% hit) + 6,154 output
- **Latency (per case, ms):** min 2,286 · p50 10,356 · p90 22,018 · max 126,096

## Per-case

| Case | Verdict | Final card | Lat (ms) | LLM calls | In tok | Cached | Out tok | Cost |
|---|---|---|--:|--:|--:|--:|--:|--:|
| opt_bull_call | PASS | option_strategy_card | 22,018 | 2 | 83,263 | 40,448 | 146 | $0.0160 |
| opt_bear_put | PASS | option_strategy_card | 8,860 | 2 | 83,594 | 40,448 | 133 | $0.0161 |
| opt_iron_condor | PASS | option_strategy_card | 8,031 | 2 | 83,913 | 80,768 | 158 | $0.0112 |
| opt_straddle | PASS | option_strategy_card | 13,830 | 2 | 83,263 | 80,256 | 138 | $0.0111 |
| opt_bear_call | PASS | option_strategy_card | 4,349 | 2 | 83,165 | 80,256 | 141 | $0.0110 |
| opt_protective | PASS | option_strategy_card | 6,724 | 2 | 84,415 | 40,960 | 132 | $0.0163 |
| opt_bull_put | FAIL | None | 126,096 | 0 | 0 | 0 | 0 | $0.0000 |
| opt_short_strangle | FAIL | None | 10,356 | 0 | 0 | 0 | 0 | $0.0000 |
| bsk_ratecut | WEAK | workflow_draft_card | 10,140 | 3 | 138,701 | 88,448 | 439 | $0.0245 |
| bsk_it_bull | FAIL | ask_user | 21,321 | 2 | 46,934 | 0 | 225 | $0.0122 |
| bsk_defence | PASS | strategy_builder_card | 15,265 | 2 | 93,797 | 92,160 | 473 | $0.0129 |
| bsk_dividend | FAIL | ask_user | 20,753 | 2 | 47,159 | 0 | 146 | $0.0121 |
| bsk_ev_green | WEAK | ask_user | 2,927 | 1 | 47,592 | 0 | 59 | $0.0120 |
| bsk_momentum | FAIL | ask_user | 10,580 | 1 | 47,354 | 0 | 104 | $0.0120 |
| clr_opt_play | PASS | option_strategy_card | 4,519 | 2 | 84,886 | 81,920 | 168 | $0.0113 |
| clr_strategy | FAIL | None | 17,185 | 3 | 90,141 | 46,592 | 1,677 | $0.0204 |
| clr_invest | PASS | strategy_builder_card | 6,633 | 2 | 87,908 | 43,008 | 174 | $0.0169 |
| clr_agent_opts | PASS | workflow_draft_card | 12,098 | 4 | 45,464 | 1,408 | 1,207 | $0.0146 |
| agt_topgainer | PASS | workflow_draft_card | 6,134 | 2 | 90,446 | 44,544 | 578 | $0.0182 |
| agt_sip | PASS | workflow_draft_card | 2,286 | 1 | 44,985 | 0 | 56 | $0.0114 |

## Notes per case

- **opt_bull_call** [PASS] — _I'm bullish on RELIANCE over the next month — build me a bull call spread._
  - Bull call spread 1320/1360CE, real ₹ + POP 39.8%. Slow (22s).
- **opt_bear_put** [PASS] — _I think NIFTY will drop 3-4% this week. Set up a bear put spread for me._
  - NIFTY 23500/23250 PE, real premiums, defined risk + table.
- **opt_iron_condor** [PASS] — _Expecting BANKNIFTY to stay range-bound till expiry — build an iron condor._
  - BANKNIFTY condor real strikes, POP 64.1%.
- **opt_straddle** [PASS] — _Big move coming in INFY around its results but I'm unsure of direction — build a long straddle._
  - INFY 1155 straddle, breakevens, uncapped-upside note.
- **opt_bear_call** [PASS] — _I'm mildly bearish on HDFCBANK — build me a bear call spread._
  - HDFCBANK 790/805 CE, POP 76%, fast 4.3s.
- **opt_protective** [PASS] — _I hold 100 TCS shares and want downside protection for a month — set up a protective put._
  - TCS protective put; honestly flags risky/oversized at 2 lots.
- **opt_bull_put** [FAIL] — _Neutral-to-bullish on RELIANCE — I'd rather collect premium, build a bull put spread._
  - Azure 500/timeout -> 126s, no card, 'backend hiccuped'. Transient provider error.
- **opt_short_strangle** [FAIL] — _I think NIFTY volatility is overpriced into expiry — build a short strangle._
  - Azure WriteTimeout on hop 1 -> no card. Transient provider error.
- **bsk_ratecut** [WEAK] — _I believe the RBI will cut rates soon — build me an equal-weight basket of 5 rate-cut beneficiaries, ₹2 lakh._
  - Built allocate_notional basket card BUT generic caption (doesn't name the 5 stocks).
- **bsk_it_bull** [FAIL] — _Bullish on Indian IT for the long term — equal-weight basket of the top 5 IT stocks, ₹1 lakh._
  - build_strategy bailed to PLAIN TEXT: 'needs a split that wasn't set up here'. Should build.
- **bsk_defence** [PASS] — _I think defence stocks keep rallying — build a ₹3 lakh basket of defence names._
  - strategy_builder_card, rich 'why this basket' narration.
- **bsk_dividend** [FAIL] — _Build me a basket of quality dividend-paying stocks for steady income, ₹5 lakh._
  - build_strategy bailed to PLAIN TEXT: 'needs a covariance step'. Should build.
- **bsk_ev_green** [WEAK] — _I want exposure to the EV and green-energy theme — a ₹2 lakh basket please._
  - BARE ASK_USER (plain text) clarify -> should be a structured clarify_card.
- **bsk_momentum** [FAIL] — _Momentum is working — build a ₹1 lakh equal-weight basket of this month's top 5 gainers._
  - get_live_price feed error -> plain-text bail. No basket.
- **clr_opt_play** [PASS] — _make me an options play on RELIANCE_
  - suggest_option_strategy -> RELIANCE iron butterfly, real numbers, 4.5s.
- **clr_strategy** [FAIL] — _build me a strategy_
  - Structured clarify 3Q (good) BUT final build_strategy failed -> plain prose, no card.
- **clr_invest** [PASS] — _I have 2 lakh sitting idle, help me put it to work_
  - Direct strategy_builder_card, risk-parity ₹2L, 6.6s.
- **clr_agent_opts** [PASS] — _make me an agent that buys options in reliance_
  - ask_agent_clarify card -> 2x 1-3ms resume -> valid workflow draft.
- **agt_topgainer** [PASS] — _make me an agent that buys 5 shares of the top gainer 1 hour after open and sells everything at close_
  - 7-step workflow, clean caption (no ref leak), narration skipped, 6.1s.
- **agt_sip** [PASS] — _build an agent that buys ₹10,000 of NIFTYBEES every Friday at 9:30_
  - propose_scheduled_order, narration skipped, 2.3s.

## The structured-question issue (user's note)

Cases that fired a **plain-text question / bail** instead of the structured clarify_card system:
  - `bsk_it_bull` — tools=['build_strategy'] — _Bullish on Indian IT for the long term — equal-weight basket of the to_
  - `bsk_dividend` — tools=['build_strategy'] — _Build me a basket of quality dividend-paying stocks for steady income,_
  - `bsk_ev_green` — tools=['ASK_USER'] — _I want exposure to the EV and green-energy theme — a ₹2 lakh basket pl_
  - `bsk_momentum` — tools=['build_strategy', 'get_live_price'] — _Momentum is working — build a ₹1 lakh equal-weight basket of this mont_

Structured clarify_card WAS used correctly in: `clr_strategy` (ask_user_dynamic, 3Q), `clr_agent_opts` (ask_agent_clarify, 2Q).

## Follow-up — fix applied + precise root causes

The four `ask_user`-hint cases above have TWO distinct causes, only one of which is a
"blank-text ASK_USER" problem:

**(1) Genuine bare-ASK_USER punt — FIXED.** Only `bsk_ev_green` actually called the
`ASK_USER` tool (a blank-text question). Fix shipped in `chat_service._apply_scenario_routing`
(lowest-precedence branch): when the router surfaces a STRUCTURED clarify tool for the turn
(`ask_user_dynamic` for strategy/basket, `ask_agent_clarify` for agents), the bare `ASK_USER`
escape is dropped from scope — so any clarification must render as the one-click clarify_card,
never blank text. Applied identically in `handle()` + `handle_stream()` (no drift). Verified
live: `bsk_ev_green` no longer emits a blank ASK_USER (it now answers honestly in prose with a
nearest-alternative, since EV/green has no wired theme map); `make me an agent that buys options
in reliance` still renders the structured `ask_agent_clarify` card (3.8s); a plain price query is
unaffected (`ASK_USER` retained for non-build turns).

**(2) `build_strategy` hard-fails on user-requested equal-weight ≥5 names — PRE-EXISTING, NOT a
clarify.** `bsk_it_bull`, `bsk_dividend`, and the `clr_strategy` terminal are NOT questions —
they're `build_strategy` raising and the LLM narrating the failure (rendered as `ask_user`). Exact
server error:

> `Tool build_strategy failed: anti-bland #1: equal-weight with 9 names (>4) — the covariance fallback must restate the reason, not silently 1/N`

The "anti-bland" guard (`strategy_builder.py:~1248`) *raises* whenever a basket is equal-weight
with >4 names — even when the **user explicitly asked for equal weight**. So "equal-weight basket
of the top 5 IT stocks" can never build. Recommended fix (your call — this is the in-flight
`strategy_builder.py`): exempt explicit user-requested equal-weight from the anti-bland assertion
(only block *silent* 1/N defaults, not a user's stated choice), or downgrade the assertion to the
equal-weight fallback note. `bsk_momentum` is a separate transient `get_live_price` feed error.

**Other observations:** `opt_bull_put` (126s) + `opt_short_strangle` were transient Azure provider
errors (500 / WriteTimeout) during the run, not logic faults — re-running should pass.
`bsk_ratecut` built a card but the deterministic no-narration caption is generic ("places the
configured order") for `allocate_notional` baskets — it should name the theme/holdings.