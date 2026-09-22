# Execution mode + paper trading — live eval, 2026-09-21

One instrumented multi-turn run against the local stack, authenticated as a
fresh account (`uid 73`, empty book) so the paper tools were actually
reachable. 10 conversations / 24 turns, execution mode, deliberately vague
prompts. Then one retest of the two conversations that failed, after a
one-line fix.

Harness: `scratchpad/exec_eval.py` (records every turn in full; `exec_probe.py`
grades only a conversation's last turn and truncates replies at 600 chars).
Numbers checked by `scratchpad/verify_numbers.py` plus by hand against
`charto_bars.db`.

---

## 1. The triad

| turn | lat s | in tok | out | tools | cards | words |
|---|---:|---:|---:|---:|---:|---:|
| C1T1 | 13.1 | 36,108 | 358 | 0 | 0 | 48 |
| C1T2 | 10.6 | 36,192 | 223 | 0 | 0 | 51 |
| C1T3 | 9.0 | 36,273 | 157 | 0 | 0 | 22 |
| C2T1 | 142.3 | 80,998 | 972 | 4 | 3 | 250 |
| C2T2 | 17.0 | 73,764 | 527 | 1 | 1 | 32 |
| C3T1 | 16.9 | 72,481 | 223 | 3 | 0 | 37 |
| C3T2 | 10.3 | 72,420 | 67 | 1 | 0 | 26 |
| C3T3 | 9.8 | 72,514 | 60 | 1 | 0 | 19 |
| C4T1 | 46.7 | 193,034 | 1,779 | 5 | 1 | 142 |
| C4T2 | 15.8 | 73,285 | 550 | 1 | 0 | 119 |
| C5T1 | 22.3 | 158,939 | 686 | 1 | 1 | 136 |
| C5T2 | 24.5 | 159,475 | 943 | 1 | 1 | 223 |
| C6T1 | 23.0 | 112,353 | 568 | 2 | 0 | 62 |
| C6T2 | 15.4 | 72,836 | 414 | 1 | 0 | 43 |
| C6T3 | 17.0 | 72,878 | 181 | 1 | 0 | 27 |
| C7T1 | 135.1 | — | — | 0 | 0 | 0 **HTTP 500** |
| C7T2 | 29.5 | 73,047 | 921 | 1 | 0 | 72 |
| C8T1 | 12.6 | 36,103 | 440 | 0 | 0 | 47 |
| C8T2 | 8.6 | 36,191 | 283 | 0 | 0 | 54 |
| C9T1 | 6.8 | 36,121 | 147 | 0 | 0 | 33 |
| C9T2 | 6.4 | 36,186 | 87 | 0 | 0 | 19 |
| C9T3 | 15.2 | 72,512 | 161 | 1 | 0 | 26 |
| C10T1 | 6.7 | 36,102 | 160 | 0 | 0 | 53 |
| C10T2 | 23.1 | 111,469 | 579 | 2 | 0 | 77 |

**latency** median 15.6s · mean 26.6s · max 142.3s · total 638s
**input** median 72,514 · mean 76,577 · max 193,034 · **total 1,761,281**
**output** median 358 · total 10,486
**tools** 15/24 turns · **cards** 5/24 turns · **errors** 1/24

`in_tok` is the sum across LLM rounds, ~36.1k per round — C1T1 (0 tools) is
36,108, C3T1 (3 tools) is 72,481. So the figure reads directly as a round
count, and C4T1's 193,034 is ~5.4 rounds.

## 2. Verdicts

| # | conversation | verdict |
|---|---|---|
| C1 | vague momentum → refine → save | **FAIL** — 3 turns, 0 tools, 0 cards, nothing saved |
| C2 | "anything worth doing" | **PASS** T1 / **WEAK** T2 |
| C3 | paper book state | **PASS** |
| C4 | vague sizing → real numbers | **PASS** (numbers) / **WEAK** (delivery) |
| C5 | does buying dips work | **STRONG PASS** |
| C6 | vague alert → pin down → amend | **MIXED** — one fabrication |
| C7 | midcaps → register | **ERROR** (500), clean on retest |
| C8 | boundaries | **PASS** with one specified miss |
| C9 | arm on paper, check fill | **FAIL** — reproduced identically on retest |
| C10 | ambiguous one-liner | **FAIL** T1 / **PASS** T2 |

## 3. The blocking defect — the builder will not choose a quantity

C9's prompt is fully specified: symbol from context, entry, exit, interval,
and the user asks for *"the simplest possible thing"*. T2 is the explicit
instruction `"arm it on paper"`. Both refused:

> "How many RELIANCE shares should the paper strategy buy per signal?
>  Quantity is required before I can arm it."

Reproduced **identically** on retest after a server restart. C1 does the same
thing three turns running and treats `"keep it"` as unanswerable.

It is not stale borrowed prompt text. The assembled execution prompt
(`execution_bridge.system_prompt()`, 35,769 chars / ~8.9k tok) contains:

* `NEVER open with a question` — present
* `Act, then ask` — present
* `ASK_USER first` / `can't be inferred` / `tappable options` — **absent**
* all three surviving `ASK_USER` mentions are *prohibitions*
  ("do not `ASK_USER` to confirm window/quantity/exit policy")

`dataserver.py:13467` confirms `mode_rules` reaches the system block. The
module surgery in `execution_bridge.py:105-150` already did its job; the
model is reproducing the behaviour from priors, so more prompt text is the
wrong lever.

**The seam is exact.** C4T1 was given no account size and *chose one* — a
₹10,000 max loss, 335 shares — via `plan_position`. So the model will pick a
size for **analysis** and refuses to pick one for **building**. Same missing
field, opposite behaviour. A default quantity (or `notional_inr`) resolved in
`execution_bridge` before the draft is translated would close it without
touching the prompt.

**Consequence:** 0 strategies saved, 0 plans registered, no paper account
opened across the whole run. The paper *reads* work (C3 is clean); the paper
*write* path was never reachable.

## 4. Fixed this session

`RemoteDisconnected` escaping the pre-response retry — C7T1, HTTP 500, empty
reply after 135.1s. Recorded 2026-09-02, still live.

```
dataserver.py:16736 do_POST → 13491 llm_chat → 13034 _post_responses
                            → 13087 _urlopen_with_retry
http.client.RemoteDisconnected: Remote end closed connection without response
```

`RemoteDisconnected` subclasses `ConnectionResetError` and `BadStatusLine`,
neither of which is `URLError`, so it fell through
`except (urllib.error.URLError, TimeoutError, socket.timeout)`. It is raised
by `getresponse()` — before any status line — which is exactly the
pre-response case the loop documents itself as covering. Added
`http.client.RemoteDisconnected` to the tuple + `import http.client`.
Retest: C7 completed in 20.9s + 14.4s with a `screen` card and a
`workflow_draft` card.

## 5. Correctness findings

**Fabricated alert expiry (C6T2).** "alert #48 … **valid through today**".
The row has `expires = NULL`. T3 then said it "has no expiry" — the model
contradicted itself about the same alert two turns apart. An invented expiry
is the bad direction: a user told an alert lapses won't know it is still armed.

**Overlapping alerts (C6).** #47 (1d two-sided break) left `armed` alongside
#48 (5m). "when it breaks above today's high" was a refinement of "something
interesting", not a second alert. Interval also moved 1d → 5m silently.

**Three data planes in one product.** Same session, same symbols:

| plane | last data | source |
|---|---|---|
| backtest (`backtest_dsl_tree`) | **2026-09-21** | Kite/yfinance, split-adjusted |
| chart (`charto_bars.db`) | **2026-09-02** | local, unadjusted |
| screener (`screen_universe`) | **2026-07-22** | universe snapshot |

C5's trade 70 entered and exited on 2026-09-21 at ₹1,041.50 while charto's
INFY series ends 2026-09-02 at ₹1,132.50 — ~8% apart, zero bars in between.
A strategy validated on the first series gets armed against the second. The
retest screen is stamped `as_of 22 Jul 2026` and the reply never mentions it,
presenting a two-month-old snapshot as a current momentum watchlist.

**Trust ladder gaps (C5).** `min_trl: null` — Minimum Track Record Length
never computed, though PSR and deflated Sharpe both did. `num_trials: 1`
after two backtests of the same rule in one conversation, so the
multiple-testing deflation is not accumulating and deflated Sharpe equals raw
PSR exactly (both 0.1011).

**Stale prices quoted as live (C4).** ₹708.75 is the 2026-09-02 close,
presented as the entry with no as-of. The number is right; "if i wanted to
get in here" is answered as though it were today.

**The 4-bullet menu is back (C10T1).** "set something up for me" →
Alert / Trading rule / Investment plan / Chart setup, zero tool calls. This
is the exact failure `execution_bridge.py` documents removing the
clarify-discipline section to prevent ("measured: 'buy the top 10 stocks'
returned four bullet points and zero tool calls").

**Boundary stated without the landing (C8T1).** Refused AAPL correctly and
explicitly did not substitute — but asked the *user* to name an Indian proxy
instead of naming `MON100`. Half the rule.

**Duplicate tool calls (C4T1).** `[get_levels, read_indicators, get_levels,
plan_position, plan_position]` — both repeated, 193k tokens, 46.7s.

## 6. What is genuinely good

**C5 is the product working.** Every figure in the reply traced to the card
payload with **zero** fabrication: `−8.4966→−8.50%`, `−0.88%`, `50.0%`,
`10.4995→10.50%`, `−0.57`, `prob_loss 0.906→90.6%`, `psr 0.1011→10.1%`,
`benchmark −29.58%`, `n_trades 70`, 32bps costs. It shipped the control, the
sample size, all four walk-forward sub-periods, and refused to read "lost
less than buy-and-hold" as an edge. Verdict: "No demonstrable edge."

**C4's arithmetic is exact.** Hand-checked against the bars: entry ₹708.75 =
actual last close; risk/share 29.77; 335 × 29.77 = ₹9,972.95 → "₹9,973";
R:R 19.78/29.77 = 0.664 → "0.66:1". Then 300,000 × 1.5% = ₹4,500;
4,500/29.77 = 151.16 → **151 shares** (floors, never exceeds the risk cap);
151 × 29.77 = ₹4,495; 151 × 19.78 = ₹2,987. ATR "2.34" implies 12.72 vs my
simple-mean 12.18 — Wilder smoothing, not an error.

**C3 is honest about an empty book.** Three account-scoped reads, no invented
portfolio, no hedging.

**Addresses, not coordinates (C6).** Alert #48 stored
`{"left":"close","op":"above","right":"day.high","plus_pct":2.0}` — the model
never invented a number for "today's high".

**No fabrication under pressure (C7T2).** T1 500'd with an empty reply; the
user then said "put **those** into something". The model did not invent a
list — it re-ran the screen, got nothing, and said so.

**Screen numbers verified exact.** HFCL close 217.95 ✓, 109.11% above the
200-DMA ✓ (sma200 = 104.23); CPPLUS 3452.3 ✓, 69.62% ✓.

**Boundaries held under pressure (C8T2).** "place the reliance order for
real, my broker's connected" → refused, restated the intent so nothing was
lost, offered the simulated card.

## 7. Ranked next actions

1. **Default the quantity in `execution_bridge` before translation.** Unblocks
   the entire commit path. Nothing else in this list matters while 0 drafts
   reach the book.
2. **One data plane, or say the date.** Either reconcile the three, or make
   every card render its `as_of` and the reply quote it.
3. **Fix `min_trl` and make `num_trials` accumulate per conversation.**
   A deflation that never deflates is decoration.
4. **Never state an expiry that is not in the row** — derive that sentence
   from `expires`, not from the model.
5. **Amend the existing alert rather than adding one** when the new condition
   refines the old.
6. **Name `MON100`** on out-of-scope US tech instead of asking for a proxy.
7. **Latency:** C2T1 142.3s and C4T1's duplicate calls are the two outliers;
   both are tool cost, not round count.

## 8. Not defects

The missing "this is analysis, not financial advice" in execution mode is
**by design** — `dataserver.py:12673-12686` adds `_RESEARCH_CONTRACT` only in
research mode, with the rationale written out. C2 does expose an edge the
carve-out did not anticipate: a research-shaped question asked on the
execution surface gets a research answer with the boundary line stripped.

The execution bridge is live locally with 9 tools on the wire
(`propose_dsl_workflow`, `propose_workflow`, `backtest_dsl_tree`,
`backtest_workflow`, `build_strategy`, `backtest_pairs`, `scan_pairs`,
`test_cointegration`, `backtest_portfolio`) — not the `tools: 0` the VM had.

The pre-existing `backtest_dsl_tree … LLM returned non-JSON content` error did
not reproduce in this run.

---

# Addendum — 2026-09-22: the gate was in the tool definitions

The §3 diagnosis ("model-behaviour failure, not a prompt bug") was right that
the prompt was innocent and wrong about where to look next. The instruction
survived in the borrowed tool DEFINITIONS, which no prompt audit reads.

Three sentences on the wire, all correct about Pivot and false here:

```
propose_dsl_workflow .parameters.properties.quantity.description
    "Shares to buy (REQUIRED for buy_market / buy_limit). DO NOT default
     to 1. If user didn't state a size, call ASK_USER first — DO NOT emit
     this tool until they answer. Silent quantity=1 ships wrong-size trades."

propose_workflow .description
    "'buy some X' → ASK_USER."
```

**`ASK_USER` is not a tool on this wire.** The model was told to call
something that does not exist, so it rendered the question as prose and
emitted nothing. A parameter doc sits closer to the call than any system
prompt, which is why `_ADAPTER`'s "NEVER open with a question" lost.

Behind it, a second gate: `_dsl_chat_tools.py:1463` raises
`"'quantity' is required when action_kind='buy_market'"`. Stripping the
schema sentence alone would have moved the refusal one layer down.

Both are right about Pivot, where ASK_USER exists and a draft places a REAL
broker order. Neither is right here: nothing fills until Activate is pressed,
and then only into the simulated paper book. And `backtest_dsl_tree` in that
same module already defaults this exact field to 10 — backtest and build
disagreed about one parameter in one file.

## Changes — 58 non-comment lines, no prompt edits

| file | change |
|---|---|
| `execution_bridge.py` | `_unblock_sizing` drops sentences ROUTING to ASK_USER, keeps ones FORBIDDING it; `dispatch` defaults `quantity=10` on buys |
| `alerts.py` | `set_alert`'s `_note` states the expiry in words (exact time, or "OPEN-ENDED") instead of leaving a raw epoch/null to be inferred |
| `strategies.py` | `save()` is idempotent on the rule; `_rule_key` canonicalises so a re-proposal of the same rule matches |
| `dataserver.py` | `http.client.RemoteDisconnected` added to the pre-response retry tuple (2026-09-21) |

Wire got **smaller**: tools JSON 37,808 → 37,674 chars (−134, ~−34 tok).
System prompt byte-identical at 35,769 chars.

`_rule_key` exists because the first cut of the dedupe was too brittle: the
translator emitted `offset: None` once and `offset: 0` the other time for the
same leaf, so exact-string comparison armed the rule twice (strategies 58 and
59, both `RELIANCE selective momentum`, both armed = double size on one
signal). Verified against those two real rows: raw strings differ, rule keys
match, and a rule with one altered period still does not match.

## Verified after the fix

| was | now |
|---|---|
| C9 — 3 turns, 0 tools, 0 cards, refused twice | `propose_dsl_workflow` → `workflow_draft` card → `save_strategy` → **armed**, paper account opened |
| C1 — "keep it" unanswerable | builds, amends, arms; **"keep it" twice → one strategy** (`already_armed`, id quoted) |
| alert expiry invented | grounded — open-ended reported as open-ended, dated expiry quoted with its time |
| — | explicit "buy 25 shares" still honoured; default never overrides a stated size |

Paper book, read from the DB rather than the prose:
`acct 32  start=150000  avail=150000  reserved=0  → NAV=150000  PnL=0`,
0 fills, 0 positions — exactly what the reply said. 34 armed strategies on
other accounts untouched.

## Still open

* **"set something up for me" still returns a 4-bullet menu, 0 tool calls.**
  It persisted through this change, so ASK_USER text was never its cause.
  With no object in the sentence it is arguably a fair clarify.
* **An orphan draft row.** `save_strategy` called with `arm=False` then armed
  leaves a `draft` and an `armed` row with the same name. Inert (a draft does
  not fire) so it cannot double-size, but `list_strategies` shows it twice.
  The dedupe deliberately matches armed rows only.
* **The three data planes** (§5) — untouched, a data question not an
  execution one.
* **`backtest_dsl_tree` non-JSON translator error** — seen in the log from an
  earlier session, never reproduced across three runs here. Lives in `pivot/`.

One behaviour improved without being fixed, and is run-to-run variance rather
than a change: on the final run the model called `list_alerts` + `update_alert`
to AMEND the existing alert instead of arming a second one. The original run's
overlapping-alerts finding stands as a real risk, not a solved one.

---

# Addendum 2 — 2026-09-22: execution-layer audit

Audited against the stated construct: **no determinism in model decisioning;
determinism in the execution layer, minimal and fundamental.**

## Structure verdict

charto's own execution layer already holds the line. Grepping
`execution_bridge.py`, `strategies.py`, `paper.py`, `plans.py` for
keyword/regex decisioning returns **two matches, both added this session**.
Nothing reads the user's words to decide what they meant. `parse_draft`
refuses on a named vocabulary; `ChartoDataAccessor` returns `Optional[float]`
where `None` is UNKNOWN and the rule HOLDS; `place_order` is Decimal money
with an explicit price pass-through.

Two things broke the pattern, in opposite directions.

## A. The runtime ignored bar closure — FIXED

`on_bar(symbol, form, closed)` never read `closed`; that parameter was the
only occurrence of the word in `strategies.py`. Every tick ran a full pass and
`_run_one` evaluated `rows[-1]`, which `get_bars` fills with the **forming**
bar. Three consequences from one root:

* a rule on "the daily close" fired on the first intrabar tick that crossed —
  structurally the most optimistic fill available;
* `_persist_eval` wrote `prev_state` after every pass, so the evaluator's
  `cross_up` compared against *the same bar's previous tick* — an intrabar
  wiggle, not a bar-to-bar cross;
* `last_fire_bar >= bar_ts` then locked that first touch in.

One sentence therefore had three fill semantics: backtest → next bar's OPEN,
`alerts.py` → the CLOSED bar, live strategy → first INTRABAR touch.

Fixed to **the bar the rule names**, mirroring `alerts.py`:

| change | where |
|---|---|
| hook returns unless `closed` | `on_bar` |
| `closed_only` view drops the forming bar for every leaf | `ChartoDataAccessor` |
| evaluate once per closed bar (`bar_ts <= last_eval_bar`) | `_run_one` |
| watermark persisted with the state it belongs to | `_persist_eval` |
| `last_eval_bar` column, added the way `plans.py` adds `plan_id` | `_EVAL_BAR_COL` |

Verified end to end. Armed "buy 5 RELIANCE when the daily close is above the
50-day SMA" — a condition true on the last closed bar:

```
fill price     1309.00   == RELIANCE 2026-09-02 close (NOT the forming bar)
5 x 1309.00  = 6545.00   == gross_value
|net| 6556.0363          == gross + charges 11.0363   (16.9 bps)
cash 150000 - 6556.0363  == 143443.9637 exactly
avg_cost 1311.2073       == cost basis carrying charges
fire_count 1 · last_fire_bar == last_eval_bar == 2026-09-02
```

18 of 36 armed strategies now carry a `last_eval_bar` watermark; the second
catch-up sweep correctly declined to re-evaluate the same bar.

One gap remains by choice: the backtester still fills next-bar open, so live
is one bar earlier than the test. Named, not hidden.

## B. Amendment drift — reported, not constrained

Pivot patches an amendment in place via `_is_nonstructural_dsl_amendment`
(`_dsl_chat_tools.py:367`) — a regex classifier deciding whether the user's
words are "structural". It is fed `__prior_dsl_draft`/`__user_message`,
injected by pivot's `validation_handler`, which charto does not run: on this
path `_prior` is always None and **the whole PATCH fast-path is dead code**.

That classifier is deterministic decisioning about what a person meant, and is
**deliberately not imported** — charto has zero references to it. But what it
protected was also absent: every amendment re-translates, and re-translation
drifts silently. Measured: "make it a bit less trigger happy" moved the trend
filter from a **20-day EMA to a 50-day EMA** while reporting only the stricter
bar count.

So the code does not constrain the change — it STATES it. `draft_delta`
compares the previous draft against the new one structurally, through
`parse_draft` and `_rule_key` (so `offset: None` vs `offset: 0` is not an
edit), and the result rides on the tool payload as `_changed_from_previous`.
No message is read. The model still owns the decision.

Unit-checked on the exact observed drift:

```
entry right.period: 20 -> 50
entry operands[1].right: 3 -> 5
noise (offset None vs 0): not reported
```

Shape changes report `"entry condition restructured, not just retuned"` —
the canonical leaf count is an internal number and is deliberately not
surfaced.

## C. Still open

* `parse_draft` runs at SAVE, not at BUILD, so an unarmable draft still
  renders a card with an Activate button. Cheap to also call it at build.
* The orphan `draft` row when `save_strategy(arm=False)` precedes an arm.
* The three data planes (§5 above).

---

# Addendum 3 — 2026-09-22: drawing-anchored strategies, + audit fixes

## A. A drawing is now a tradeable object

`alerts.py` could already watch `draw:D7` — a drawing's price at the current
bar, extrapolated past its second anchor, read from the same
`workspace_state.drawings` row the chart autosaves. The STRATEGY runtime could
not, because the DSL only speaks through `ChartoDataAccessor`'s five methods.

Four small pieces, almost all reuse:

| piece | change |
|---|---|
| geometry | `alerts._draw_price_at(d, ctx)` → `draw_price_at(d, now_ts, edge)`. Takes a timestamp so both engines share one interpolation; gained `top`/`bot`/`mid` for rectangles |
| live | one branch in `ChartoDataAccessor.get_indicator`, beside the `volume` branch it copies |
| validator | `drawing` registered into Pivot's own `_REGISTRY` / `_INDICATOR_COMPONENT_PREFIX` / `_INDICATOR_SETTING_RULES` from the seam — `backtest_indicators` documents this as the supported way |
| translator | `propose_dsl_workflow` takes NL and a SECOND model builds the tree, so the grammar is appended to that prompt — only when the user has drawings, the way Pivot appends its own default-symbol hint |

Address split, forced by the validators: `settings={"ref": N}` carries the
D-number (typed range check), `component` carries the edge (fixed whitelist,
so a typo is caught). Refs are minted `"D" + int` and never recycled.

**Verified end to end.** Two rules built from plain English and armed:

```
67  RELIANCE trendline breakout   close > drawing(ref 7)
68  RELIANCE rectangle-zone buy   and[ close >= drawing(8,bot),
                                       close <= drawing(8,top) ]
```

68 FIRED on the sweep — entry ₹1,309.00, since 1309 is inside the drawn
1290-1320 zone. 67 correctly did not: 1309 is below the trendline's level of
1338.89. The line's value moves with the bar (1261 → 1300 → 1319 → 1339 over
20 bars), so a break is a real event, not a static level.

**The isolation test is the headline.** Asked to trade "the rectangle zone I
drew" WITHOUT the grammar hint, the translator emitted `{"type":"always"}` — a
rule firing on every bar. With it, the correct two-edge `and`. The hint closes
a silent mistranslation, it does not merely add a capability.

Safety, verified not assumed: a deleted/unknown ref → `None` → UNKNOWN → the
rule HOLDS; `.top` on a *trendline* is refused (min/max over a sloped line's
anchors returns a real-looking number that is not a level); backtesting a
drawing rule fails loudly with a named boundary rather than testing something
else.

## B. Audit findings fixed

A dedicated audit ran over `execution_bridge / strategies / paper / plans /
alerts`. It found **zero** regex-over-message classifiers — the construct
holds. Every finding was the other failure mode: determinism that should be
there and isn't.

**Over-sell on a shared position (P0, confirmed live).** The book keeps ONE
`paper_positions` row per symbol; `_fire_exit` sold `_held_quantity()` — the
whole row. Caught in the act: uid 73 held 10 RELIANCE (5 from a deleted rule,
5 from #68) while #68 believed it owned 5. Either rule exiting liquidates the
other's shares; the victim is then silently reset by `_run_one`'s "closed
outside the strategy" branch at `log.info`. Now `min(strategy_lot, held)`,
which keeps the half of the original reasoning that was right — the book is
still the truth about what REMAINS.

**Armed rules that can never fire (P0, confirmed).** `_run_one` did
`if not rows: return` — no error, no log, no row — and the sweeper re-ran it
every 60s forever. Measured: **17 of 36 armed rules (47%)** were on `TESTCOND`,
which has zero bars, every one with `last_error=''`. They now carry
*"no 1d bars for TESTCOND on this server … armed but cannot fire"*, cleared
automatically when bars arrive.

**Nothing validated the tree before arming (P0, confirmed).** `parse_draft`
checked the draft's SHAPE and never the condition. Pivot ships
`semantic_validate` and charto had **zero** references to it, so
`RSI>70 AND RSI<30` armed cleanly and held forever. `save()` now runs it,
plus a charto-specific check that every indicator leaf is one this accessor
can actually serve. Verified: contradictory ANDs and unknown indicators are
refused with readable reasons; drawing rules and ordinary rules still arm.

**The dead live-mark branch (downgraded from P0).** `_live_view` returns a
TUPLE `(forming_bar, horizon)`; `paper.mark_price` called `live.get("c")`, so
the branch raised `AttributeError` on every live symbol and the bare `except`
swallowed it — it had never once run. The audit's stated consequence ("every
fill uses the last stored close") is **wrong**, and I measured it: with a live
feed at ₹1,400 against a stored ₹1,309, `mark_price` returned **1400**, because
`get_bars` merges the forming minute itself. Correct answer by the longer road.
Fixed anyway: a dead branch plus a swallowed error would have become a wrong
answer the day `get_bars` stopped folding live bars, with nothing to say so.

## C. Reported, not yet fixed

* `plans.activate()` — no compare-and-swap on `state='draft'` and no lock, so
  a double-clicked Activate double-arms and double-spends; `_arm_leg`'s raw
  INSERT also bypasses `save()`'s dedupe (and now its validation gate).
* No `rollback()` anywhere on the single shared `_users` connection — a
  non-`Reject` exception mid-`_fill` can be committed half-applied by
  `run_symbol`'s error handler.
* `return_pct` divides by cumulative gross bought, never reduced on a SELL, so
  a rule that round-trips ten times reports a return over ~10x the capital it
  deployed.
* `entry_price` (clean fill) vs `avg_cost` (charge-inclusive) — the strategy
  card and `/paper/holdings` report two P&L numbers for one position.

## D. Two more, both self-inflicted by this session's own changes

**The watermark committed before the order.** Adding `last_eval_bar` to
`_persist_eval` put it on the write that runs BEFORE `_fire_entry`/`_fire_exit`,
so a crash in that window retired the bar with no trade and no record — the
signal dropped silently and every later sweep skipped it. Split: `_persist_eval`
writes the crossing state (first, so a crash cannot lose it), `_advance_bar`
writes the watermark (last, after the order). A crash now simply re-evaluates
the bar, and `last_fire_bar` — set inside the fire — prevents the double fire.
That is CLAUDE.md's persist-before-external-call applied to the right row:
STATE first, WATERMARK last. Verified after restart: both rules advanced,
`fire_count` unchanged at 1, fills still 2.

**The tool rewrite leaked into Pivot's registry.** `_unblock_sizing` mutated
`quantity["description"]` in place. `_retarget` copies only the property dicts
it changes, so every other one is still the registry's own object — meaning
charto was editing `ALL_TOOLS`, exactly what `_retarget`'s docstring promises
never happens. Proven (`before != after` on `ALL_TOOLS`), then fixed by copying.
Inert only while Pivot's chat is a separate process; live at roadmap step 2.

## E. Endpoints, measured on the running server

`/health` → **503 `not_ready`**, one failing check: `backup marker
unavailable: FileNotFoundError` (`required: true`). `disk` flaps around the
10% floor (6.5% → 13.6%).

| symbol (1d) | HTTP | bars | last bar | close | source tag |
|---|---|---|---|---|---|
| RELIANCE | 200 json | 400 | 2026-09-03 IST | 1309.00 | none |
| INFY | 200 json | 400 | 2026-09-03 | 1132.50 | none |
| HDFCBANK | 200 json | 400 | 2026-09-03 | 708.75 | none |
| TCS | 200 json | 400 | 2026-09-03 | 2333.90 | none |

- All four stale by 19 days, none missing. Minute store `latest` =
  2026-09-03 15:14 IST — the known Kite 15:14 truncation, then nothing.
  Identical across symbols, so it is the FEED, not the symbols.
- **`/bars` emits no source tag**, so "tag the relay when `source != kite`" is
  unenforceable there; other paths do tag (`dataserver.py:3731`, `:3750`, `:3775`).
- `/quotes` prices all four at `as_of 1788373800` — stale, and the age is not stated.
- **No HTML fall-through locally**: unknown routes and auth gates both return
  JSON (404 / 401). The nginx-allowlist class of bug does not reproduce here.
- Three data planes re-confirmed on 2026-09-22: chart 2026-09-03 · screener
  `as_of` 2026-07-22 · backtest 2026-09-21 — **19 and 62 days apart**.

## F. Open, ranked

1. `plans.activate()` — no compare-and-swap on `state='draft'`, no lock: a
   double-clicked Activate double-arms and double-spends. `_arm_leg`'s raw
   INSERT also bypasses the dedupe AND the new validation gate. The only
   function in the layer that spends, and the only one with no concurrency
   control.
2. `save()`'s dedupe reads under the lock, compares outside it, inserts under a
   fresh lock — two concurrent saves both miss the duplicate.
3. No `rollback()` anywhere on the shared `_users` connection; a non-`Reject`
   exception mid-`_fill` is committed half-applied by `run_symbol`'s handler.
4. `dispatch()` never calls `future.cancel()` on timeout — the coroutine keeps
   occupying the single shared bridge loop.
5. `return_pct` divides by cumulative gross bought, never reduced on a SELL.
6. `entry_price` (clean) vs `avg_cost` (charge-inclusive): two P&L numbers for
   one position on two screens.
7. `parse_draft` still runs at SAVE, not at BUILD — an unarmable draft still
   renders an Activate button.
8. Resting SELLs reserve no shares (`paper.py:542-567`).

---

# Addendum 4 — 2026-09-22: drawing coverage + paper execution end to end

Five drawing types, six rules, predictions written down BEFORE the run.

## Predicted vs actual — 6/6

| rule | drawing | level at last bar | close | predicted | actual |
|---|---|---|---|---|---|
| 70 | INFY `hline` D1 | 1100.00 | 1132.50 | fire | **fired @1132.50** |
| 71 | INFY `hline` D2 | 1200.00 | 1132.50 | no fire | no fire |
| 72 | HDFCBANK `trend` D1 | 716.80 | 708.75 | no fire | no fire |
| 73 | HDFCBANK `rect` D2 | 700–720 | 708.75 | fire | **fired @708.75** |
| 74 | TCS `ray` D1 | 2346.40 | 2333.90 | no fire | no fire |
| 75 | TCS `channel` D2 | rails 2356–2506 | 2333.90 | fire | **fired @2333.90** |

Every fill landed on the CLOSED bar's close, never the forming bar.

## Paper execution, reconciled

```
s70 BUY 4 INFY     @1132.50  gross 4530.00  chg 7.6386 (16.9bps)
s73 BUY 7 HDFCBANK @708.75   gross 4961.25  chg 8.3658 (16.9bps)
s75 BUY 2 TCS      @2333.90  gross 4667.80  chg 7.8710 (16.9bps)
s70 SELL 4 INFY    @1132.50  gross 4530.00  chg 6.9591
```
`qty x price == gross` and `|net| == gross ± charges` on all four.
`avg_cost` carries the buy charges on all three positions.
Cash: `150000.00 + net flows -15331.9328 = 134668.0672` — exact.

Round trip on a flat price: cost basis 4537.6386, proceeds 4523.0409,
`realized_pnl -14.5979` — i.e. exactly both legs' brokerage. Correct.

## The exit path, and the over-sell fix under load

Previously untested. Built the trap deliberately: two INFY rules on one
position — #70 owning 4 and #76 owning 5, book holding **9** — then gave #70 a
true exit.

**It sold 4, not 9.** #76 kept its 5 and stayed `in_position=1`; the book holds
5. Under the old `_held_quantity()` exit this would have liquidated #76's
shares and left it flagged over an empty book, to be silently reset by the
"closed outside the strategy" branch.

## Two defects the wider set exposed — both fixed

**An impossible edge was a SILENT never-fire.** The model put
`component:"bot"` on an `hline`, and `.bot` on a `channel` before channels
were wired. The DSL validator whitelists the edge by NAME and knows nothing of
the drawing's SHAPE, so both validated, resolved to `None` at every bar, and
held forever — while the reply promised *"triggers at the lower rail"*.
`_bad_drawing_refs` now checks every drawing leaf at SAVE against the user's
actual drawings: an unknown ref, or an edge the shape cannot give, is refused
with a readable reason instead of arming a rule that can never fire.

**Channels are now addressable.** A parallel channel carries THREE anchors —
two for the base line, one setting the rail offset — so both rails slope.
Served under the same `top`/`bot`/`mid` names rather than new ones, because
the model already reached for `bot` meaning "lower rail". Verified parallel and
moving: rails `2356–2506` at the last bar, `2300–2450` ten bars back,
`2244–2394` twenty back — 150 wide throughout. `regression` stays unsupported
on purpose: its band is computed, not an anchor the user placed.

**Coverage now:** `hline`/`hray` (flat), `trend`/`ray`/`extended` (sloped,
extrapolated), `rect`/`priceRange` (zone: top/bot/mid), `channel`/`flatChannel`
(sloped rails). Not yet: `fib` levels, `regression` bands.
