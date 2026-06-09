# Judge report — `quality_eval / run_20260609_114659`

## Headline
- **Verdict mix: 17 PASS / 8 PARTIAL / 8 FAIL** across 33 sessions (40 turns).
- **MEAN quality: 6.15 / 10** (verdicts adversarially verified; corrected where a
  panel verify_note overrode the original grade).
- **Quality triad (all three dimensions present):**
  - **Tokens:** 2,587,537 input / 6,870 output total. The input footprint is the
    system-prompt context per turn (60–135K in / 40–235 out is the norm); a
    single-line price lookup (`live_price_reliance`) still burns 66K in over 2 LLM
    calls — heavy but it is prompt footprint, not output blowup.
  - **Latency:** p50 8,564 ms / p95 26,063 ms. One hard outlier: `sunpharma_drawdown
    _exit_buildable` at **129,975 ms** (a 2-minute exit-translation retry/timeout loop
    that then refused). `bank_pe_screen` 26.0 s and `infy_vs_tcs_compare` 15.2 s are
    the next-heaviest, both multi-call fan-outs.
  - **Quality:** mean 6.15/10; bimodal — payload-matched reads and clean first-builds
    are A-grade (9–10), while analysis-depth, confirm/re-size/redirect edges, and two
    documented-buildable agent shapes drag the floor to 1–4.
  - **Anomalies:** two turns logged **zero LLM calls** (`amend_symbol_swap/turn0`,
    `rsi_workflow_kotak/turn0`) — harness replay/cache artifacts, not quality signals;
    the graded OUTPUT for both is correct.
- **One-sentence verdict:** The data plumbing (Kite-grounded numbers, no fabrication)
  and the happy-path workflow builder are genuinely strong, but two seeded defects —
  analysis prompts starved into ≤120-word blurbs, and "buy-at-open" / drawdown-exit
  agents that refuse documented-buildable shapes — plus a confirm-after-amend register
  regression and a degenerate-economics options card, hold this build at a C.

## What's working
- **Numeric honesty and Kite-freshness are solid.** `tcs_returns_breakdown` returns
  all five windows verbatim (1w −11.98% … 1y −37.06%) with no rounding; `live_price_
  reliance` gives a fresh 2026-dated ₹1,266.30 (a stale yfinance answer would read
  ~₹2,900). No turn in the run fabricated a price, ratio, or fair value.
- **Happy-path strategy builder is excellent (7 of 10 A-grade).** `axisbank_ema_cross`,
  `maruti_dip_buy_with_stop`, `macd_signal_crossover_jswsteel` are 10/10: true
  two-leaf `crosses_above` semantics (EMA20/50, MACD-line-vs-signal — NOT the old
  `>0` shortcut), qty honored, never re-asked, clean readbacks.
- **Honest degradation over fabrication.** When backend reads fail (`tatamotors`,
  `hdfcbank_is_a_buy` thin fundamentals) the bot names the gap ("only EPS 50.0
  resolved", "price history did not resolve cleanly") instead of inventing a P/E —
  exactly what system.md L86 demands.
- **First-build and pure-substitution amendments nail context-carry.** `amend_symbol_
  swap` and `clarify_then_build_basket` preserved qty/trigger across turns and built
  sane cards (equal-weight ₹12,500×4 basket on NIFTY −1%).
- **Refused the lazy "both are good" trap.** `infy_vs_tcs_compare` and `adanient_risk
  _read` pick a defended winner / land a sharp risk contrast (ADANIENT's *better*
  Sharpe reflects RELIANCE's weak return, not lower risk — the best reasoning in the
  set), both off real `compare_performance` numbers.
- **Pure-prose edge case handled cleanly.** `nps_tax_advice` redirects an out-of-scope
  personal-tax ask without fabricating a tool or flat-refusing.

## What's broken — systemic patterns

### 1. [P0] Analysis prompts are misrouted to SHORT-ANALYTICAL and starved (SEEDED ISSUE A)
- **Exhibited by:** every analysis session — `reliance_deep_dive`, `hdfcbank_is_a_buy`,
  `tatamotors`, `sbin_expensive_or_cheap`, `infy_vs_tcs_compare`, `adanient_risk_read`,
  `itc_dividend_play` (mean quality 4.3/10, the worst category).
- **Root cause:** `_classify_reply_class` (`chat_service.py:1094-1096`) only escalates to
  EXPLAINER when `_EXPLAINER_INTENT_RE` (`:1037-1048`) matches literal
  "explain/compare/business model/fundamentals/which is better". Natural retail analysis
  phrasings — "deep dive on X", "is X a buy", "what do you think of X", "expensive or
  cheap", "how risky is X", "X vs Y" (uses "vs", not "compare"), "good dividend play" —
  ALL miss the regex and fall through to `analytical_short`, whose budget hint (`:1128-
  1132`) hard-directs "≤120 words of plain prose. No `##` headings." The model OBEYS;
  measured outputs are 71–117 words, 0 headings. system.md L89-100 *tells* the model to
  do a full sectioned "analyse X" — the classifier overrides its own prompt.
- **Next-iteration instruction:** In `backend/services/chat_service.py`, broaden
  `_EXPLAINER_INTENT_RE` to catch `deep\s+dive`, `is\s+\w+\s+a\s+buy`, `what\s+do\s+you
  \s+think`, `expensive|cheap|over\s?valued|under\s?valued|risky|how\s+risky`, `\bvs\.?\b`,
  `dividend\s+play`, `analyse|analyze`; OR add a dedicated `analysis` reply-class keyed off
  `intent_kind=='analysis'` / the structured-read tool set (`get_price_history +
  fetch_fundamentals + get_symbol_news`). Give that class a 250–450-word budget that
  *encourages* `## Snapshot / ## Technicals / ## Fundamentals / ## News / ## What to
  watch / ## View` AND explicitly demands a defended synthesis (bull/bear, what-would-
  change-my-mind), not a data echo.

### 2. [P0] Documented-buildable agent shapes refuse / loop (SEEDED ISSUE B + drawdown-exit)
- **Exhibited by:** `reliance_open_buy_3pct_sell` ("at open" → self-comparison refusal,
  FAIL 2/10), `sunpharma_drawdown_exit_buildable` (drawdown-from-peak exit refusal +
  **130 s** hang, FAIL 1/10). Both emit `render_hint=ask_user`, no card.
- **Root cause:** "at open"/"at the open" is a SESSION-ANCHOR schedule the DSL grammar
  can't express, so it collapses to `open==open` (tautology). The redirect backstop
  `_RECURRING_SCHEDULE_RE` (`chat_service.py:346-352`) catches "every friday" but NOT
  "at open"/"at close"/"market open", so `_redirect_target_for_failure` (`:361-385`)
  never fires the dsl→propose_workflow redirect and it refuses. Separately,
  exit-translation failures fall back to `ask_user` instead of the SUPPORTED
  `drawdown_from_peak_pct` leaf — even though system.md L692 says "**NEVER refuse this
  shape**" and `icicibank_rsi_buy_trailing` (S3) built the *identical* `drawdown_from_peak
  _pct >= 0.06` exit successfully in the SAME run.
- **Next-iteration instruction:** (a) Add `at\s+(?:the\s+)?(?:open|close)|market\s+open|
  market\s+close` to `_RECURRING_SCHEDULE_RE` (or a sibling `_SESSION_ANCHOR_RE`) so the
  dsl→workflow redirect fires; route bare "at open"/"at close" entries through
  `propose_workflow` + `trigger.market_relative_time` per system.md L1498. (b) In
  `workflows/dsl/llm_translate.py`, make the position-exit translation deterministically
  fall back to a `drawdown_from_peak_pct` leaf instead of `ask_user`, and **cap
  exit-translation latency / fail fast** so it never burns 130 s on a simple build.

### 3. [P0/P1] Confirm-after-amend register breaks; amendment re-invocation drops args + leaks internals
- **Exhibited by:** `amend_then_confirm_register/turn2` ("go ahead and register it" →
  "I hit a build issue, nothing was registered", no card — the exact P0-2 regression the
  session exists to catch); `hinglish_then_clarify_size/turn1` ("10000 ka kharido" →
  re-called `propose_dsl_workflow`, lost the prior symbol/condition, and **leaked raw
  JSON-schema field text** "the Natural-language entry condition. Pass verbatim and the
  Symbol the action fires on" into the chat reply, violating system.md L218).
- **Root cause:** there is no register/activate chat tool (register is button-only by
  design), yet the model treats "register it" as a re-BUILD trigger and errors out; the
  amendment path drops previously-established required args instead of carrying them over
  (system.md L938-950); and the failure path echoes tool-schema plumbing instead of a
  clean user-facing fallback. Compounding capability gap: `propose_dsl_workflow` has NO
  rupee-notional param (`tools.py:1543`, integer qty only), so a Hinglish "buy 10000
  worth" on an exit-strategy shape is unbuildable and fails silently.
- **Next-iteration instruction:** On "register/activate it" over a complete valid draft,
  do NOT re-invoke `propose_*` — reply that it's drafted and ready, tap Activate (chat
  can't flip it live). On amendment re-invocation, carry over all prior args; on any
  mid-flight tool failure emit a clean fallback string (never echo schema descriptions).
  Add a notional fallback for exit-strategy shapes ("rupee sizing isn't supported on this
  exit-strategy shape — ~14 shares at the current price, set that?").

## Per-category scores

| Category | n | PASS / PARTIAL / FAIL | mean Q (0-10) | headline |
|---|---|---|---|---|
| analysis | 7 | 3 / 2 / 2 | **4.3** | worst category — SHORT-ANALYTICAL misroute (Issue A) |
| comparison_screen | 2 | 1 / 1 / 0 | 6.0 | screen tool under-used; per-symbol fan-out instead of `screen_fundamentals` |
| data_interp | 4 | 2 / 2 / 0 | 6.8 | bimodal: payload-matched 9–8, structure/recency asks 6–4 |
| strategy_build | 10 | 6 / 2 / 2 | 7.1 | strong happy path; 2 P0 refusals at the boundaries |
| multi_turn | 6 | 2 / 1 / 3 | 5.7 | first-build great, confirm/re-size/redirect edges collapse |
| edge_honesty | 2 | 1 / 0 / 1 | 5.5 | prose edge clean; card edge fabricates success |
| regression | 2 | 2 / 0 / 0 | **9.0** | both known-issue regressions held |

## Ranked fix list (P0 first)

### P0-1 — Structured-analysis output (SEEDED ISSUE A)
- **Where:** `backend/services/chat_service.py` — `_EXPLAINER_INTENT_RE` (:1037),
  `_classify_reply_class` (:1073), `_REPLY_BUDGETS["analytical_short"]` (:1128).
- **Change:** Broaden the EXPLAINER regex / add an `analysis` reply-class so single-name
  reads get a 250–450-word budget that encourages `## Snapshot/Technicals/Fundamentals/
  News/What-to-watch/View` and demands a defended synthesis, not a data restatement.
- **Evidence:** all 7 analysis responses are 71–117 words, 0 headings, 0 bullets; the
  model is obeying the injected "≤120 words, no `##`" directive. `reliance_deep_dive`
  fetched the right data (RSI 32.7, P/E 25.0, ROE 8.93%, D/E 0.41) and still delivered a
  108-word blurb. system.md L89-100 asks for the opposite.
- **Ideal shape:** "## Snapshot — last close + 1w/1m/3m/6m/1y returns · ## Technicals —
  below SMA20/50/200 (downtrend), RSI 32.7 soft-not-washed-out, near 52w low · ##
  Fundamentals — P/E 25 / ROE 8.93% / D/E 0.41 vs RIL's own history · ## News — actual
  headlines · ## View — weak tape vs fair-not-cheap quality, what would change my mind",
  ~300–400 words.

### P0-2 — "Buy at open, sell +3%" must BUILD (SEEDED ISSUE B)
- **Where:** `backend/services/chat_service.py` — `_RECURRING_SCHEDULE_RE` (:346),
  `_redirect_target_for_failure` (:361); `backend/workflows/dsl/llm_translate.py`.
- **Change:** Add `at\s+(?:the\s+)?(?:open|close)|market\s+(?:open|close)` to the
  schedule/anchor backstop so the dsl→`propose_workflow` redirect fires; route bare
  "at open"/"at close" entries to `trigger.market_relative_time(anchor='open')` +
  `trigger.exit_compound unrealised_pct>=0.03` + sell, per system.md L1498.
- **Evidence:** `reliance_open_buy_3pct_sell` → "couldn't be built as a single rule
  because the exit condition collapsed into a self-comparison", `render_hint=ask_user`,
  no card. `_RECURRING_SCHEDULE_RE` matches "every friday" but not "at open", so the
  redirect at :383 never fires.
- **Ideal shape:** a `workflow_draft_card`: "Drafted: buy RELIANCE at the open, sell when
  it's up 3%. Review and activate." — never a refusal.

### P0-3 — Drawdown-from-peak exit must BUILD; cap exit-translation latency
- **Where:** `backend/workflows/dsl/llm_translate.py` (position-exit translation +
  fallback); exit-translation timeout.
- **Change:** Deterministically fall back to a `position_field.drawdown_from_peak_pct`
  leaf when exit translation fails, instead of `ask_user`; cap exit-translation latency
  and fail fast to that leaf rather than looping.
- **Evidence:** `sunpharma_drawdown_exit_buildable` refused ("the exit … couldn't be
  translated cleanly") after a **129,975 ms** hang, while `icicibank_rsi_buy_trailing`
  built the identical `drawdown_from_peak_pct>=0.06` exit in ~13 s in the same run.
  system.md L692 mandates "NEVER refuse this shape".
- **Ideal shape:** "Drafted: buy 10 SUNPHARMA on RSI(14)<35, exit if it falls 6% from its
  post-entry peak. Review and activate." built in <15 s.

### P0-4 — Confirm-after-amend must register, not re-build and error
- **Where:** `backend/services/chat_service.py` confirm/register handling.
- **Change:** On "register/activate it" over a complete valid draft, do NOT re-invoke
  `propose_*`; reply that it's drafted and ready ("tap Activate — chat can't flip it
  live"), or activate directly if a register path exists. Never "re-build then error to
  nothing".
- **Evidence:** `amend_then_confirm_register/turn2` → "I hit a build issue, so nothing
  was registered", `render_hint=ask_user`, no card, on a fully-confirmed valid draft.
- **Ideal shape:** "Registered — NESTLEIND buy 8 shares when RSI(14)<30 is now live" or
  "It's drafted and ready — tap Activate on the card to register."

### P0-5 — Degenerate options card narrated as success (iron condor)
- **Where:** `backend/.../option_strategies.py` (duplicate-leg guard :371-377, strike
  resolver) + the card-prose gate in `chat_service` / system.md L894.
- **Change:** Reject same-strike opposite-side collapse (re-walk the wing or raise
  `StrategyResolutionError`); never render a card whose max_loss/max_profit/pop/capital
  are all zero/null. Gate the prose: if economics are zero/null, the reply MUST say the
  structure failed to resolve and offer the nearest workable alternative — never claim
  "wing protection" that doesn't exist.
- **Evidence:** `fno_strategy_iron_condor` card legs are SELL CE 23950 + BUY CE 23950
  (same strike) and SELL PE 22700 + BUY PE 22700; every economic field = 0, yet the prose
  claims "the card shows the breakeven range and the wing protection". The (type, side,
  strike)-keyed duplicate guard lets a SELL+BUY same-strike pair pass as "distinct".
- **Ideal shape:** a real iron condor with four distinct strikes, non-zero net credit,
  real POP and defined max-loss, prose stating those exact figures + the expiry-day gamma
  warning — OR an honest "I can't place clean wings here, nearest workable is a wider
  condor / short strangle".

### P1-1 — Valuation/dividend asks must call fetch_fundamentals before answering
- **Where:** routing in `chat_service` (valuation-word → force `fetch_fundamentals`).
- **Change:** Words like "expensive/cheap/value/dividend play/overvalued" must force a
  `fetch_fundamentals` call before answering; a "dividend play" ask must pull the actual
  yield, not answer from memory.
- **Evidence:** `sbin_expensive_or_cheap` (FAIL) called `get_price_history +
  get_live_price + get_portfolio_summary` but NOT `fetch_fundamentals`, answered on the
  tape, then punted "If you want, I can also check fundamentals" (offer-instead-of-do).
  `itc_dividend_play` (FAIL) called ZERO tools and gave "~4% vibes" with no real yield.
- **Ideal shape:** "PB ~X.Xx vs the bank median, so valuation is full and RSI ~77 says
  don't chase here" (SBIN); "yield ~X% on ₹Y, payout Z%, post-demerger leans on
  cigarettes/FMCG/paper" (ITC).

### P1-2 — Harden fetch_fundamentals coverage for large caps
- **Where:** `backend/.../analysis_chat_tools.py` (:25-26 large-cap coverage) /
  fundamentals data layer + fallback.
- **Change:** Populate / fallback PE/ROE/PB/yield for large caps (HDFCBANK, TCS, INFY,
  the bank universe) so valuation asks resolve; until then keep surfacing the gap
  honestly (which the bot already does well).
- **Evidence:** `hdfcbank_is_a_buy` got only EPS 50.0; `infy_vs_tcs_compare` only EPS
  72.0/135.7; `bank_pe_screen` could price only 1 of 5 banks. Data-layer limit, not a
  response failure — but it caps multiple analysis answers.
- **Ideal shape:** real PE/PB/ROE/yield populate, so "is X a buy" / "cheapest on PE" get
  the valuation layer they require.

### P1-3 — Index trend asks need price-history, not a single-day level
- **Where:** system.md routing rule + `chat_service` tool selection for trend asks.
- **Change:** "uptrend/sideways/topping on an index" → `get_price_history`/`get_indicator`
  on the index (SMA stack + RSI + multi-window returns); NEVER judge a multi-week trend
  off `get_index_level`'s single-day change.
- **Evidence:** `nifty_trend_read` (PARTIAL) called `get_index_level` and judged the
  trend off "flat on the day" — no SMA20/50/200 stack, the exact math the question needs.
- **Ideal shape:** "NIFTY 23,187.45; above/below its 50 & 200 SMA in [rising/falling]
  order, RSI ~X, N% from 50-DMA — that's an [uptrend/range/topping] read."

### P1-4 — News asks: direct get_symbol_news, recency-filtered, no detour/offer
- **Where:** system.md news-path rule + `chat_service`.
- **Change:** "recent news on X" → a direct `get_symbol_news(X)` (no `find_tool` detour,
  no `get_live_price`); lead with items inside the user's window ("last few days"), drop
  the trailing "if you want, I can pull…" offer on a satisfied read.
- **Evidence:** `kotakbank_news_check` (PARTIAL) routed `get_live_price → find_tool →
  get_symbol_news` (4 LLM calls, 17.1 s), led with a SoftBank/Lenskart item it admits
  isn't a Kotak event, dated bank items to "the last month" vs the asked "last few days".

### P1-5 — Comparison screens should use screen_fundamentals, not N×fetch_fundamentals
- **Where:** routing in `chat_service` for "cheapest/which of N on PE".
- **Change:** "which of these N is cheapest on PE" → one `screen_fundamentals(sector,
  sort_by pe asc)` ranked call, not a per-symbol fan-out; for banks steer to P/B.
- **Evidence:** `bank_pe_screen` (PARTIAL) ran N×`fetch_fundamentals`, could price only
  AXISBANK (14.29), called it "the cheapest" — a near-non-answer — and burned 26.0 s /
  6 LLM calls / 205K tokens.

### P1-6 — i-dont-understand follow-up must adapt, not repeat the menu
- **Where:** follow-up handling in `chat_service` (clarification re-entry).
- **Change:** On "i dont understand what you proposed — which indicator and why", correct
  the false premise (nothing was proposed yet) and TEACH one option in plain language;
  do not re-emit the identical three-option menu.
- **Evidence:** `i_dont_understand_followup/turn1` (FAIL) replied with the same RSI<30 /
  price<50-EMA / MACD menu verbatim, answering neither "which" nor "why".

### P1-7 — Reply-class regex should also catch "comparison"/"which one is better"
- **Where:** `_EXPLAINER_INTENT_RE` (:1037).
- **Change:** Add `\bcomparison\b` and `which\s+(?:one\s+)?(?:is|has)\s+(?:the\s+)?better`
  so head-to-head asks get the EXPLAINER table budget.
- **Evidence:** `infy_vs_tcs_compare` was routed to SHORT-ANALYTICAL (87 words, no table)
  because "vs" and "which one is the better" miss the current regex — though it still
  PASSED on substance.

### P1-8 — Built-card logic must be validated against the natural-language ask
- **Where:** `workflows/dsl/llm_translate.py` numeric-condition translation.
- **Change:** "dips 3% from day high" must encode `ltp <= rolling_high * 0.97`, not
  `ltp <= rolling_high` (fires near the high, ~always); "falls 4% from open" must encode
  `open*0.96`, not absolute `< ₹4`.
- **Evidence:** `analysis_then_build/turn1` built `ltp <= rolling_high` directly;
  `amend_symbol_swap/turn0` translated "falls 4% from open" to `trigger.price < ₹4`
  (nonsensical for a stock priced in thousands). Structurally valid cards, semantically
  wrong — a silent FAIL that looks like a tool-name PASS.

## The two SEEDED issues — addressed explicitly

**KNOWN ISSUE A — analysis output quality (CONFIRMED, P0-1).** Every one of the 7
analysis prompts came back as a 71–117-word, headingless, bullet-less blurb. This is NOT
a model-capability failure — `reliance_deep_dive` *fetched* RSI 32.7 / P/E 25.0 / ROE
8.93% / D/E 0.41 and `adanient_risk_read` produced the sharpest caveat in the set. The
model is being *starved* by `_classify_reply_class` falling through to `analytical_short`
("≤120 words, no `##` headings", `chat_service.py:1128-1132`) because the natural
phrasings ("deep dive", "is X a buy", "what do you think", "expensive or cheap", "how
risky", "X vs Y", "dividend play") miss `_EXPLAINER_INTENT_RE` (:1037). Fix in P0-1:
broaden the regex / add an `analysis` class with a 250–450-word sectioned budget that
*demands* synthesis. This single change lifts the worst category (mean 4.3) the most.

**KNOWN ISSUE B — simplest agent fails to build (CONFIRMED, P0-2).** "build me an agent
that buys reliance at open and sells when it rises 3%" still refused: "the exit condition
collapsed into a self-comparison", `render_hint=ask_user`, no card. Root cause exactly as
documented: "at open" is passed as a `condition` to `propose_dsl_workflow`, the grammar
can't express the session anchor so it becomes `open==open`, the error carries no "use
propose_workflow" hint, and `_RECURRING_SCHEDULE_RE` (:346) catches "every friday" but
NOT "at open"/"at close" — so `_redirect_target_for_failure` (:383) never fires the
dsl→workflow redirect and it loops/refuses. system.md L1498 already says "at open" must
ALWAYS use `trigger.market_relative_time(anchor='open')`; the routing code doesn't honor
it. Fix in P0-2: add the session-anchor pattern to the backstop and route to
`propose_workflow` + `trigger.market_relative_time` + `trigger.exit_compound
unrealised_pct>=0.03`. (P0-3 is the sibling refusal: the drawdown-from-peak exit, also
documented-buildable at system.md L692, also refused — same "fail to ask_user instead of
the supported leaf" class.)

## What excellent output looks like
Distilled from the run's A-grade replies and the panel's ideal_shapes:

- **A pure-data ask gets the numbers verbatim, nothing else.** `tcs_returns_breakdown`
  (100/A): all five windows with units, no rounding to "about X%", no padding, no
  follow-up offer — because the user said "just give me".
- **A "room/overbought" ask gets the real number + the correct interpretation.**
  `infosys_overbought_check` (91/A): "not overbought, RSI(14) ~46.7, neutral, still
  room" — one tool, correct neutral call, ideally weaving in the 52w-high distance.
- **A single-name analysis is sectioned and argued, not echoed.** `## Snapshot` (close +
  multi-window returns) → `## Technicals` (SMA stack labelled as a trend, RSI in context,
  range position) → `## Fundamentals/Valuation` (real PE/ROE/PB framed vs the name's own
  history or sector) → `## News` (actual headlines) → `## What to watch` → `## View` (a
  defended stance weighing tape against quality, with "what would change my mind"),
  ~300–400 words, honest about any missing metric, standard disclaimer.
- **A comparison picks a winner with risk-adjusted reasons.** `infy_vs_tcs_compare`:
  declares INFY, cites 1y return −25.3% vs −37.2%, Sharpe −1.19 vs −2.05, smaller
  drawdown — ideally as a side-by-side table when the EXPLAINER budget is granted.
- **A strategy build emits a card on the first turn and names every term in the
  readback.** `maruti_dip_buy_with_stop` (10/10): `pct_change<=-0.03` + buy 20 +
  `set_stoploss 5%`, one-liner naming buy 20 / 3% dip / 5% stop. The fix for the bare
  readbacks ("Drafted. Review and activate it.") is to surface the card's symbol + amount
  + trigger in the text, per system.md L1034.
- **A live-price ask is one fresh line.** `live_price_reliance` (9/A): "RELIANCE is
  trading at ₹1,266.30, up 0.28% today." — correct ₹, intraday %, no preamble, no offer.
- **An out-of-scope ask is redirected honestly, no fabricated tool.** `nps_tax_advice`
  (8/A): names the decision shape, offers to think it through by horizon/tax slab — would
  be a clean A with an explicit scope-boundary line + the standard disclaimer.
- **An impossible/degenerate structure is disclosed, never narrated as success.** The
  iron-condor anti-pattern teaches the rule: if a card's economics are all zero/null, SAY
  it failed to resolve and offer the nearest workable structure — do not claim "wing
  protection" that the numbers don't show.
