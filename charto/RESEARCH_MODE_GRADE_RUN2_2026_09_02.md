# Run 2 — verification grade (charto research mode, 20 turns, RELIANCE 1d)

**Who graded this.** The run-1 grader agent hit its account session limit
(resets 14:10 IST) before reading the brief, so this report was written in the
main session against `GRADE.md`'s rubric and evidence rule. Same standard: no
claim without a quote or a payload check.

**Harness caveats that constrain what can be read from the transcript**
- Turn 7 is the screening prompt resent verbatim by the harness. Labelled as
  such; not counted against the product, and used below as a free consistency
  check.
- `DRAWN` / `PARKED` are snapshots taken at capture time, not turn time. Turns
  0–2 were captured after later turns had run, so their annotation columns are
  the scene as of turn 3. Every annotation claim below is therefore checked
  against turn 3 onward only.
- `latency_s` is the app's own timer and is real.

**Headline.** Four of five payload fixes are clean. The fifth moved its failure
rather than removing it, and the new failure is worse in one specific way: the
reply now claims a level is on the chart when it is not. Nothing else regressed.

Run 2 at a glance: 7 of 20 turns carried a card; latency median 20.1 s
(10.2–39.3); the chart finished with 10 live and 39 parked annotations.

---

## 1. Fix verification

### FIX 1 — `explain_move` dropped the window's last session → **FIXED**

| | |
|---|---|
| run 1 | *"The last notable down day was 21 July, when RELIANCE closed 1.32% lower at 1,304.4."* |
| run 2 | *"For the 22 July down day, the evidence points to intraday selling pressure—not unusually high volume or a fresh same-day headline."* |

22 July is the chart's last bar (`C 1,283.6 · −1.59%`). The card now carries
`Session move −1.59% · 1 session · Size percentile 81` and a per-segment
decomposition **of that day** — overnight −0.18, opening 15m −0.17, morning
−0.81, midday +0.09, last hour −0.53. In run 1 the same block described 21 July
under a fortnight-wide card. Backend check: `range(i0, i1+1)` head-5 + tail-5
now, and the window's last session is asserted present in
`test_explain_move_keeps_the_windows_last_session`.

### FIX 2 — peer ranking derived by the model → **FIXED**

| | |
|---|---|
| run 1 | *"It ranked last among the eight peers measured. It outperformed only BPCL (−10.35%), IOC (−10.10%) and HINDPETRO (−12.36%)."* — false; RELIANCE was −13.04%, behind all three |
| run 2 | *"RELIANCE was the weakest of the nine stocks"* + a rank table 1–9 + *"RELIANCE still lagged IOC, BPCL and Hindustan Petroleum slightly."* |

Every ordering claim is now correct and the two sentences agree. The run-2 table
resolves −10.37 / −10.81 / −11.10 / −12.01 in the right order — a four-way
near-tie among negatives, which is the exact failure mode. A direct payload
check on a fresh call returns `beat: []`, `lost_to: ['MRPL','IOC','BPCL',
'HINDPETRO']`, `rank 5 of 5`; a reply quoting `beat` cannot write run 1's
sentence.

*Note:* run 2 routed through the peer path and emitted **no `compare` card**
where run 1 emitted two. The numbers are right either way, but see New Defect 2.

### FIX 3 — trendline direction word withheld from the model → **FIXED**

| | |
|---|---|
| run 1 | prose *"a **descending** resistance trendline"* vs card *"Rising resistance · 9 touches"* |
| run 2 | five direction words in the reply, all "Rising": *"**Rising** support trendline: broken… **Rising** resistance trendline: intact, with 9 touches, but it is far above the current price"* |

Zero contradictions between prose, card and chart. Independently corroborated by
the annotation's own geometry: `p1 v=1100.5` (2020) → `p2 v=1581.3` (2025).
`get_trend` now ships `direction` per line and the tool note requires that word.

### FIX 4 — aggregates over a different sample than the table → **FIXED**

| | |
|---|---|
| run 1 | *"I measured the last four quarters"* then *"Average absolute results-day move: 2.42%"* over a table averaging 3.21%, and *"42% of reactions were positive"* over a table showing 50% |
| run 2 | *"For broader context, **across 45 measurable results events since February 2015**: Average absolute results-day move: 2.34%…"* |

The aggregate names its sample, is set apart from the four-row table under its
own heading, and the per-quarter rows are no longer implied to be its basis. Run
2 also adds a row run 1 omitted — *"Q1 FY27 · 20 Jul 2026 · Too recent to
evaluate · Not enough bars"* — which is the honest handling of the truncation.
Payload check: `aggregate_sample_n: 45`, `recent_shown: 6`.

### FIX 5 — `draw_ids` unvalidated → **PARTIALLY FIXED, and it moved the failure**

The warning fires: a direct call that skips the nearest support returns
*"WARNING — the set you drew skips the nearest level to price: L1277 (support at
1277.26, 0.49% from price, strong)…"*, and the invented rationale is now
forbidden by name.

What improved in the reply: the nearest-level framing that was entirely absent
in run 1 is now the closing sentence — *"The nearer levels are currently the
most relevant: price is just above 1,277.26 support and below 1,290.47
resistance."* And run 1's false reason (*"was left off the chart to keep it
readable"*) is gone.

**What went wrong.** Turn 3 opens *"Drawn levels that matter most on the daily
chart:"* and lists **four** levels including *"Support at 1,277.26 — Strong"*.
The scene says otherwise:

```
DRAWN : R 1,328.30 · Strong | R 1,290.47 · Moderate | S 1,122.36 · Strong
PARKED: S 1,277.26 · Strong | S 978.06 | S 1,153.09 | R 1,365.40 | R 1,393.77
chip  : 3 on chart
```

So the reply asserts a level is drawn that is parked, under a header that says
"Drawn", beside its own chip reading 3. It then says *"The other four levels are
in the Layers panel"* when five are.

This is a **regression on one axis**: run 1 got the drawn/parked split right and
the reason wrong; run 2 gets the emphasis right and the split wrong. Claiming a
line is on the chart when it is not is the worse of the two, because the user
looks for it and it is not there.

**Cause.** My note offers a choice — *"either draw it too or say in one sentence
that it is parked"* — and the model took neither branch cleanly. The note should
not offer a choice: the nearest level per side should simply be drawn.

---

## 2. New defects

**N1 (P0) — a reply that names a level as drawn when it is parked.** Turn 3,
above. Introduced by fix 5's wording. Fix: drop the choice from the tool note
and enforce it in code — when `draw_ids` omits the nearest level on a side that
has one, append it to `picked` rather than warning about it, and let the note
say it was added. The cap becomes 2 per side *plus a guaranteed nearest*, which
is what "levels that actually matter" means. Keep the warning only for the case
where the caller explicitly asked for a reduced set.

**N2 (P1) — the same question takes two different tool paths on different
runs, and one of them loses the widget.** Run 1's peer question produced two
`compare` cards; run 2's produced a markdown rank table and no card. Both
correct, but the user gets a different product depending on routing. The rank
table run 2 produced is arguably the better answer — which argues for a `peers`
card, not for leaving it to chance.

**N3 (P2) — "what I am looking at" scanned 1,500 bars.** Turn 18: *"I swept the
daily RELIANCE chart across 1,500 bars (8 Jul 2020–22 Jul 2026)"* on a chart
displaying from 2019. `_scan_window` correctly returned the visible count and
the caller clamped it to 1,500, so the label is honest about the scan — but it
is not honest about the *question*, which named the screen. Either raise the cap
when the visible window exceeds it, or say "the most recent 1,500 of the bars on
your screen".

**N4 (P2) — the two identical screens are consistent in data, inconsistent in
presentation.** Turns 6 and 7, same prompt: identical 44 rows and identical
z-scores (good), but different column headers (`ABOVE 20D` vs `>20D SMA`) and a
different opening clause (`close above` vs `price above`). Harmless here;
becomes a defect the moment a user compares two screens.

---

## 3. Still-open items from GRADE.md

- **P0-5 plan_position volatility check — better in run 2, but NOT fixed and not
  by me.** Turn 13 now says *"Risk: ₹12.55 per share, **or 0.52 ATR**"* against
  run 1's silent 0.27 ATR. Checking the source: `plan_position` has emitted
  `stop_distance_atr` and `atr14` all along (`dataserver.py` ~line 3405) — the
  model simply omitted it in run 1 and used it in run 2. That is run-to-run
  variance, not a fix, and it can regress. The stop is still 0.52 ATR with no
  warning, so the grader's recommendation stands: a hard `_note` when
  `stop_distance_atr < 0.75`. Credit where due — run 2 also states the
  breakeven honestly: *"Target 1 requires about 65% to break even at its 0.55R…
  that historical result does not clear its breakeven hurdle."*
- **P0-6 no as-of stamp — still open, and still uneven.** Turn 15 answers *"news
  around this stock right now"* under the header *"RELIANCE news currently in
  focus"* with 17–21 July items and no as-of. Turns 2, 6, 8 and 11 all state
  theirs. Unchanged from run 1.
- **P1-8 chip over-count — improved, possibly fixed.** Turn 10 drew one
  divergence and the chip reads `2 on chart` (a divergence is two legs, so this
  is defensible where run 1's `4 on chart` beside two bands was not). Turn 18
  reads `6 on chart` against 2 new pattern rows — needs its own check before
  calling this closed.
- **P1-9 screen_universe has no widget — unchanged.** Turn 6 is 44 unsorted,
  unfilterable, unclickable markdown rows at 33 s. Turn 8 is still the user
  typing the filter the widget should have provided, and still ungrounded:
  *"'Large cap' here is a conventional classification; the universe screen
  itself filters… not by a market-cap field."*
- **P1-10 annotation lifecycle — unchanged and now demonstrably worse at
  scale.** The chart finished with 10 live and **39 parked** annotations from
  eight turns, including a hypothetical trade plan still on screen five turns
  later during the news question.
- **P1-11 no auto-zoom / pane interval — not retested (no `open_chart` in this
  run).**
- **P1-12 prose reads the card aloud — improved.** Turn 9 no longer repeats the
  card's four readings as four bullets; it interprets them. Turn 14 still
  restates both trendline projections verbatim.
- **P2-14 annotation legibility — unchanged.** `run2_chart.jpg`: eleven
  right-aligned label rows over the 2024–26 candles, two of which read
  *"Falling Wedge · Moderate"* / *"Double Top · Moderate"* directly on top of
  the price action they describe.
- **P2-15 refusals name the gap, not the substitute — improved by accident.**
  Turn 12 still opens with the unavailable flows table, but then produces a real
  block-deal table (24 Jun 2026, GPIF trustee, matched buy and sell legs) and
  correctly declines to net them: *"I'm reporting them as published rather than
  treating them as net buying or selling."* Run 1 said no deals existed at all.

---

## 4. Per-turn verdict

| # | Asked | Fired | Verdict |
|---|---|---|---|
| 0 | Why moved, 2 weeks | `explain_move` → **move** | **strong** — beta attribution leads, bottom line stated, delivery/OI honestly unavailable |
| 1 | What drove the last big down day | `explain_move` → **move** | **strong** — correct day (was wrong in run 1), card scoped to that session, segment decomposition |
| 2 | vs sector peers | peers → rank table, no card | **strong** on content, **weak** on surface — see N2 |
| 3 | Draw S/R that matter | `get_levels` → 3 drawn / 5 parked | **wrong** — names 1,277.26 as drawn while it is parked; "other four" when five are |
| 4 | Draw the guiding trendline | `get_trend` → **trend** | **strong** — direction consistent everywhere, and it volunteers that the intact line is not the one guiding the move |
| 5 | Last four quarters | `evaluate_results` | **strong** — sample named, too-recent quarter declared |
| 6 | Screen: uptrend + volume | `screen_universe` | **adequate** — filters and as-of stated; still a 44-row wall |
| 7 | (same prompt resent) | `screen_universe` | consistency check — data identical, headers differ (N4) |
| 8 | Large caps within 3% of 52w high | filter over prior screen | **adequate** — honest that market cap is not a field, still classifies |
| 9 | Daily vs weekly | `multi_timeframe` → **timeframes** | **strong** — best widget/question match; interprets rather than repeats |
| 10 | Divergence, mark it | `get_divergences` → 1 drawn | **strong** — leads with "not current", 93 bars ago, refuses the 2-of-2 rate as a hit rate |
| 11 | Value area | `volume_profile` → 1 drawn | **strong** — dated, 93,432 1-min bars, price-vs-VAL is the lede, order-flow boundary drawn |
| 12 | FII/DII + deals | `get_flows` + `get_deals` | **strong** — refuses the flows, delivers the deals, declines to net matched legs |
| 13 | Stop and R:R | `plan_position` → 1 drawn | **adequate** — ATR ratio and breakeven now stated; 0.52 ATR still unflagged |
| 14 | Reversal or bounce | `get_trend` → **trend** | **strong** — names the falsification test, quantifies the gap to the swing low |
| 15 | News right now | `search_news` | **weak** — "currently in focus", 17–21 Jul items, no as-of |
| 16 | RSI and MACD | `read_indicators`, no card | **adequate** — exact against the chart badges; `indicators` renderer still never fires |
| 17 | Unfilled gaps | `get_gaps` | **strong** — one gap, dated, sized, and refuses the 3-of-4 fill rate as a percentage |
| 18 | Every pattern on screen | `get_patterns` → **patterns** | **adequate** — density system works (6 drawn, 14 folded); "1,500 bars" against a wider screen (N3) |
| 19 | RELIANCE vs TCS, 1 year | `compare_symbols` → **compare** | **strong** — answers the "which would I rather have owned" directly, two windows, correlation, closes as descriptive |

Tally: **12 strong / 5 adequate / 2 weak / 1 wrong**, against run 1's
5 / 4 / 4 / 4.

---

## 5. Ranked list for the next iteration

**P0-1 — the nearest level must be DRAWN, not warned about.** (New; replaces
run-1 P0-4, which is otherwise closed.) Turn 3 says a parked level is drawn.
Fix: in `tool_get_levels`, when `draw_ids` omits the nearest level on a side
that has one, append it to `picked` instead of emitting a warning; reserve the
warning for an explicitly reduced set. Assert in a test that the nearest level
per side is always in the drawn set when `draw=True`.

**P0-2 — reconcile the reply's level list against the scene before it ships.**
(New, general form of P0-1.) The tool already knows exactly what it drew and
parked. `_drawn_ledger()` should state both lists explicitly and the note should
require the reply's "drawn" list to be a subset of it. This is the same class of
bug as the five already fixed: a fact the backend has, that the model is left to
restate from memory.

**P0-3 — carried from run 1 (P0-5). `plan_position` needs a volatility gate.**
Still open; run 2's improvement was variance, not a fix. Add a `_note` when
`stop_distance_atr < 0.75` requiring the reply to lead with the plan being too
tight for the timeframe and to offer the next level out.

**P0-4 — carried from run 1 (P0-6). A universal as-of stamp.** Turn 15 still
answers "right now" with six-week-old data and no date. Render the data as-of in
the per-turn meta row beside latency and the chip, sourced from the scene's last
bar time, not from the model.

**P1-5 — carried from run 1 (P1-9). Build the `screen` card.** Turn 6 (33 s,
44 rows) and turn 8 together are the strongest case in either run: the follow-up
exists only because the first surface cannot be sorted or filtered, and its
answer is ungrounded because market cap is not in the matrix. Add market cap and
52-week-high distance to the universe matrix at the same time.

**P1-6 — a `peers` card, and one routing path per question.** (New, N2.) The
same question produced two `compare` cards in run 1 and a bare table in run 2.
Pick the rank table as the canonical answer and give it a card.

**P1-7 — carried from run 1 (P1-10). Annotation lifecycle.** 10 live / 39
parked from eight turns, with a hypothetical trade plan still drawn five turns
later. Classify structural vs episodic; supersede on re-scan; store the
originating turn on every scene row.

**P1-8 — verify the chip against visible-count deltas.** (Carried, P1-8,
partially improved.) Turn 18 reads `6 on chart` against 2 new pattern rows.
Derive the chip from `scene.visible.length` before and after the patch and
assert `chip === after − before` for every drawing tool.

**P2-9 — scan label vs screen label.** (New, N3.) When the visible window
exceeds the 1,500-bar cap, say the scan covered the most recent 1,500 of the
bars on screen, or raise the cap for an explicit "everything on my screen".

**P2-10 — carried from run 1 (P2-14). Annotation legibility.** Eleven label rows
over the candles. Move the legend out of the plot area or reduce it to dots with
hover.

**P2-11 — stabilise screen presentation.** (New, N4.) Two identical screens
returned different column headers. Fix the header set in the tool, not the reply.

**P2-12 — route `read_indicators` to the `indicators` card.** (Carried.) The
renderer exists and has never fired in 40 graded turns.
