# charto research mode — grade of a 17-turn live session (RELIANCE 1d, + DATAPATTNS)

Reviewer's frame: a sharp Indian retail investor is sitting in front of this chart.
Did the words, the widget and the chart say the same thing, and could he act without
re-reading? Every claim below is quoted from the transcript, the two screenshots, or
verified in `charto/data/dataserver.py` / `charto/preview/js/cards.js`.

**Headline.** This is a genuinely good research product with a rare and valuable
honesty posture — and four turns in which the reply contradicts its own numbers, its
own chart, or the immediately preceding table. The failures are not tone failures;
they are arithmetic and surface-agreement failures, which is the worst kind for a
product whose pitch is "the chat is how you operate the chart."

Score: **5 strong / 4 adequate / 4 weak / 4 wrong**.

---

## 1. Per-turn verdict

| # | Asked | Fired (tool inferred) | Verdict |
|---|---|---|---|
| 0 | Why has RELIANCE moved over 2 weeks | `explain_move` → **move** card + prose; no drawings | **strong** — index attribution (beta 0.90, residual −0.15pp) is the answer and it is correct. Padded: 4 of 5 "What the evidence shows" bullets read the card aloud. |
| 1 | What drove the last big down day — volume, delivery, or news | `explain_move` again → **move** card *identical to turn 0's* + prose | **wrong** — "The last notable down day was 21 July, when RELIANCE closed 1.32% lower at 1,304.4." The chart's own legend reads `C 1,283.6 · −20.8 (−1.59%)`; 22 July fell **1.59%**, a bigger down day, and turn 9 calls 1,283.60 a "close". Widget is the 8–22 Jul window card attached to a single-day question. |
| 2 | Screen universe: uptrend + volume > 20d avg | `screen_universe` → text only, 44×6 markdown table, 62.7s | **adequate** — filters are stated, as-of stated ("as of 22 Jul 2026"), 500-name universe stated. But this is a wall, ranked by an unbounded z (28.43 → 0.03). |
| 3 | Which of those large caps within 3% of 52w high | filter over prior screen → text, 6-row table | **adequate** — honest that it has no market-cap filter, then classifies six names as large caps anyway. Table is **not sorted**: −0.11, −0.50, −0.66, **−0.54**, −1.09, −1.39. No 52w-high column. |
| 4 | vs sector peers over 6 months | `compare_symbols`/`get_peers` → **compare** ×2 + prose | **weak** — see §4. Two adjacent sentences contradict each other and one is arithmetically false. |
| 5 | Draw the S/R that actually matter | `get_levels(draw)` → 3 drawn, 5 parked, **no card**, chip "3 on chart" | **wrong** — the nearest support (**S 1,277.26 · Strong**, 0.49% below spot) was parked while **S 1,122.36** (12.6% below spot) was drawn, with a support slot free. Never says where price sits between the levels — the only sentence "matter" requires. |
| 6 | Draw the guiding trendline; still valid? | `get_trendlines(draw)` → **trend** card + 2 segments, chip "2 on chart" | **weak** — prose opens "The relevant line is a **descending** resistance trendline"; the card says "Rising resistance · 9 touches · Projects near ₹1,640.79" and the screenshot shows a line rising left-to-right. Saved from *wrong* by the honest follow-up: "a very long-term structural line, not a close guide to the current two-week move." |
| 7 | Daily vs weekly — same thing? | `multi_timeframe` → **timeframes** card | **strong** — best widget/question match in the session. Lede answers it ("aligned bearish, but the weekly picture is more decisive") and a "Bottom line" repeats it 20 lines later. Card duplicated verbatim in the prose beneath it. |
| 8 | Price/momentum divergence — mark it | `get_divergences(draw)` → 2 drawn + 1 parked, **no card**, chip **"4 on chart"** | **weak** — chip is wrong (see §4). "Both marked, resolved divergences … were successful: 2 of 2 bullish and 2 of 2 bearish" describes four observations as "both". Honest close: "only two observations of each type, so it is not a dependable hit rate." |
| 9 | Where has volume traded — value area | `volume_profile(draw)` → 1 annotation, no card, chip "1 on chart" | **strong** — dated window, 93,432 1-min bars stated, price-vs-VAL is the correct lede, and the boundary is drawn exactly right: "volume at price, not order-flow data: it cannot identify whether buyers or sellers were the aggressors." One defect: "Switch to the daily or weekly view" — session state is already `interval: "1d"`. |
| 10 | Last four quarters + post-results drift | `get_results`/`evaluate_results` → text, 4×5 table | **wrong** — the Takeaway's numbers are not computed from the table above it. See §4. |
| 11 | FII/DII flows, bulk/block deals | `get_flows` + `get_deals` → text, 2 paragraphs | **adequate** — correct, dated ("9–22 July 2026 window"), correctly bounded ("it does not rule out smaller transactions"). Dead end: no substitute offered. |
| 12 | If I bought here, stop and R:R | `plan_position(draw)` → 1 annotation, no card, chip "1 on chart" | **wrong** — arithmetic all checks (6.34/1283.60 = 0.49%; 6.87/6.34 = 1.08; 1/2.08 = 48.1%), but a **6.34-point stop is 0.27×ATR** on a stock whose daily ATR the product itself measured at 1.84% (₹23.6) five turns earlier. Nothing says so. |
| 13 | Reversal, or bounce in a downtrend? | `get_trend`/`confirm_reversal` → **trend** card, no new drawings | **strong** — best answer in the session. Direct lede, and it names the falsification test with prices: "reclaim the descending resistance area near 1,310.6 and then break the latest opposing swing high at 1,328.0." Silently contradicts turn 6's trendlines. |
| 14 | What is the news right now | `search_news` → text | **weak** — presents 17–21 July 2026 items under the header "current news" with **no as-of anywhere**. Correctly hedges KG-D6 as "a continuing situation rather than a new event this week." |
| 15 | RSI and MACD right now | `read_indicators` → text, no card | **adequate** — correct, right length, gives the *direction of change* not just the level ("histogram +1.16, down from +3.10"), and every figure matches the chart's own badges. The `indicators` card renderer exists and did not fire. |
| 16 | Why is DATAPATTNS up | `open_chart` + `explain_move`/`get_bars` → text, 3×4 table, second pane opened | **strong** — cleanest table in the session (exactly the 3 rows that carry the argument), exemplary refusal, correct symbol switch. Pane opened at **1h** against a daily answer; the resistance it names (4,955.9) is not drawn. |

---

## 2. Output structure

**The good shape, and it is used consistently.** In every card turn the order is
lede → card → evidence prose. Turn 13 is the model of it: *"It is still a bounce
inside a broader downtrend, not a confirmed reversal."* Turn 7: *"They are aligned
bearish, but the weekly picture is more decisive."* Turn 0: *"The move looks mostly
market-driven and statistically ordinary."* A user who reads one line per turn gets
the answer in 11 of 17 turns. That genuinely clears the bar.

**Where the bottom line is buried.** Turn 1 leads with a restatement of the question's
premise ("The last notable down day was 21 July…") and puts the actual answer —
*"Not a volume shock. Delivery cannot be verified. News is a plausible background
catalyst"* — under a `Verdict` heading **below ~35 lines of card**. That verdict is the
whole reply and it is the third thing on screen. Turn 7 states its answer at the top
*and* again under "Bottom line" at the end; one of those is redundant, and it should
be the second.

**The prose reads the card aloud.** This is the single most consistent structural
defect.

- Turn 0, card: `Window move −1.63% / 11 sessions`, `Typical move 2.96%`,
  `Size percentile 28`, `NIFTY 50 moved −1.65% / Beta 0.90 would explain −1.48% /
  Residual −0.15%`. Prose immediately below: *"RELIANCE fell 1.63% over 11 sessions…
  the move's absolute-size percentile was 28, versus a typical 11-session move of
  2.96%. The NIFTY 50 fell 1.65%… the expected RELIANCE move was −1.48%, leaving only
  −0.15 percentage points unexplained."* **Cut the first three bullets of "What the
  evidence shows" entirely.** Keep bullet 4 (the intraday narrative: +2.23% on 17
  July, then 20–21 July) — that is the only sentence the card cannot hold.
- Turn 7, card: `Daily RSI 43.19 / MACD hist 1.16 / ADX 12.73 / −DI>+DI / below EMA
  50`. Prose: *"Leaning bearish: price is below the 50-day EMA. RSI is weak at 43.2.
  +DI is below −DI. MACD histogram is slightly positive… ADX 12.7."* **Cut the whole
  five-bullet "Daily" block**; keep only the sentence that judges ("bearish pressure,
  but currently in a weak or possibly ranging move").
- Turn 13, card: `Intact Descending resistance 6 touches Projects near ₹1,310.58 /
  Intact Descending support 7 touches Projects near ₹1,267.11`. Prose: *"the falling
  resistance line remains intact and projects near 1,310.58… The descending support
  line also remains intact and projects near 1,267.11."* Verbatim. **Cut the
  "Trendlines:" bullet**; the paragraph after it already uses 1,310.6 to make the
  argument, which is the correct use of a card number in prose.

The rule this implies: **prose may re-use a card number only inside a judgement.**
Restating it as a fact is dead text, and the reply is 30–40% shorter without it.

**The markdown tables.** Four turns fell back to markdown tables:

- **Turn 2 (44 rows × 6 cols)** — the worst. This is a widget, not a table. It cannot
  be sorted, filtered or clicked, which is why turn 3 exists at all: the user's next
  message is a filter he had to type because the surface had none.
- **Turn 3 (6 × 3)** — a table is the right size here, but it is unsorted and the
  column that would justify the word "large cap" is absent; the classification lives
  in prose and is ungrounded.
- **Turn 10 (4 × 5)** — should be a widget. The question ("what did the stock do in
  the days *after* each") is a shape over time; the table makes the user hold four
  sign-patterns in his head, and it is precisely where the reply's arithmetic broke.
- **Turn 16 (3 × 4)** — **correct use of a table.** Three rows, four columns, and every
  cell is load-bearing for the argument (597,706 → 1,173,896 → 6,957,730). Praise it.

**Length.** Right-sized: turns 3, 5, 11, 15, 16. Padded: turn 0 (cut ~40% per above),
turn 4 (cut the second compare card and the entire "Relative performance" recap
block, which restates card 1), turn 7 (cut the per-timeframe bullet blocks). Too
thin for the data available: **turn 5** — three prices and a Layers-panel disclaimer,
with no distance-from-spot on any of them, answering a question whose whole content
is "which ones matter."

---

## 3. Widget audit

### Cards that fired

**`move` (turn 0) — right widget, wrong ordering.** The question is *why*, and the
block that answers it — `Index attribution: NIFTY 50 moved −1.65% / Beta 0.90 would
explain −1.48% / Residual −0.15%` — is the **third** section, below a levels block
that has nothing to do with causation. Reorder: attribution → size-in-context →
levels. Two fields the user cannot interpret: *"Size percentile 28 — of its own past
moves"* (percentile of *what* distribution, over how many windows, absolute or
signed?) and *"Volume vs 20d avg 0.98×"* with no window attached — and note that the
same 0.98× is then quoted in turn 1 as a **single-day** figure ("12.67 million shares,
or 0.98× the 20-day average"). Identical to two decimals across a window statistic and
a day statistic: either coincidence or the window figure relabelled. Assert it in a
test. Also `₹1,283.60 / Last close` is filed *inside* "Levels around price" between two
levels, so it reads as a fourth level.

**`move` (turn 1) — decoration.** Byte-identical to turn 0's card, window 08–22 Jul,
attached to a question about one session. A widget that does not move when the
question moves teaches the user to stop reading widgets.

**`compare` (turn 4, first card) — right widget, three defects.**
1. **Ordering is alphabetical, not ranked.** Transcript order: RELIANCE, AEGISLOG,
   BPCL, CASTROLIND, CHENNPETRO, HINDPETRO, IOC, MRPL. The question is "how has
   RELIANCE done against peers"; the answer is a rank, and the card makes the user
   scan eight numbers to compute it. Verified in `cards.js:1977` — `syms.map(...)`, no
   sort.
2. **The colour system silently fails at n>4.** `cards.js:1980`:
   `tone: "s" + Math.min(i + 1, 4)`. With 8 symbols, five of them share tone `s4`.
   The card's own comment says "the same symbol keeps its colour through every
   section" — that guarantee is void here, across four sections.
3. **Uninterpretable without a size column.** AEGISLOG +81.73% and CHENNPETRO +67.73%
   sit next to RELIANCE's ₹2,223 cr average turnover and CASTROLIND's ₹28 cr. The
   turnover row is in the card and the prose never uses it to qualify the peer set.

**`compare` (turn 4, second card) — decoration.** A full four-section card (Return,
Drawdown, ATR, Turnover) rendered to add **one** name, PETRONET, that was missing from
card 1 and mentioned in prose. Append a row to card 1; do not fire a second card.

**`trend` (turns 6 and 13) — right widget, one unstable field.** `Structure events` is
the best block in this build: dated, priced, typed ("04 Mar · Bearish break of
structure · ₹1,307.00"). Make those rows click-to-jump on the chart. But `Range` and
`% of range` change meaning between the two firings — `₹851.45–1,611.8 / 56.8%` on 1500
bars, `₹1,114.85–1,611.8 / 34%` on 400 bars — with the lookback disclosed only in a
footer at the bottom of the card. Two cards in one conversation showing the same stock
at "56.8% of range" and "34% of range" is a contradiction to anyone not reading
footers. Either put the bar count inline with the field or drop `% of range`.

**`timeframes` (turn 7) — the best widget in the session, with two flaws.** Ordering is
correct for the question (agreement verdict first → per-timeframe rows → shared
levels). Flaws: (a) the section headed **"Levels several timeframes share"** contains
four entries tagged **Daily** only (₹1,461.4–1,473.4; ₹1,265.6–1,277.6; ₹1,247.2–1,259.2;
and the Weekly/Daily split on ₹1,197.67–1,227.43 is ambiguous) — the heading is false for
half its contents; (b) the touch arithmetic does not close: *"4 weekly touches: 2 held,
1 broke"* (3 of 4) and *"4 daily touches: 1 held, 0 broke"* (1 of 4). The ungraded
remainder is a real and defensible concept — `dataserver.py:977` documents it — but the
card shows the reader a subtraction that does not work and no third number.
(c) It is the **only** card kind with no date footer.

### Gap list — text-only answers that most deserve a widget, ranked

1. **`screen_universe` (turns 2 & 3). Highest value by a distance.**
   *Widget:* a virtualised ranked table — symbol, close, %-vs-SMA20/50/200, vol z,
   **market cap**, **distance from 52w high** — with sortable headers, a market-cap band
   filter, and row-click → `open_chart` in a second pane (turn 16 proves the pane
   mechanism works). *Why prose fails:* 44 rows the user cannot sort, cannot filter and
   cannot act on, delivered in 62.7 s. Turn 3 is the proof — the user's next message is
   a filter he had to type, and the answer to it was ungrounded because the column
   needed ("large cap") was never in the data. One widget with a market-cap column
   removes both the wall and turn 3's honesty problem.

2. **`get_levels` (turn 5). The only drawing tool with no card, and the one that most
   needs one.**
   *Widget:* one row per level ordered **by price with spot inserted in place**;
   columns role / price / **distance % from spot** / strength / held-of-graded / bars
   since last touch; each row's eye bound to the same scene row the Layers panel
   toggles. *Why prose fails:* a level list's entire meaning is its geometry around
   price, and prose flattened eight levels into three sentences that omit every
   distance. It is why the reply never noticed it had parked the level 0.49% away and
   drawn the one 12.6% away.

3. **`get_results` / `evaluate_results` (turn 10).**
   *Widget:* four small multiples, T−1…T+5 indexed to 100, with the gap / reaction-day
   / 5-session numbers as annotations. *Why prose fails:* the question asks for a
   shape and the table gives scalars — and a widget computed from the four rows
   **cannot disagree with the four rows**, which is exactly the failure that occurred.

4. **`plan_position` (turn 12).**
   *Widget:* the risk box plus the two comparisons the prose omits — stop distance
   against **1×ATR** as a bar, and the observed hit rate against the breakeven hit rate
   as a tick on a track (the `bars(..., {tick})` helper already exists,
   `cards.js:1087`), plus position size for a stated ₹ risk. *Why prose fails:*
   "Reward:risk 1.08:1" reads as a finding when the number that kills the trade is
   0.27 ATR, and nothing in prose puts those side by side.

5. **`get_divergences` (turn 8).**
   *Widget:* one row per divergence — type, date span, price endpoints, RSI endpoints,
   resolved/failed, drawn-or-parked eye. *Why prose fails:* the model had to hand-write
   the population summary and produced *"Both marked, resolved divergences… were
   successful: 2 of 2 bullish and 2 of 2 bearish"* — four observations called "both",
   in a reply that also says three marks exist and a chip that says four.

6. **`read_indicators` (turn 15).** Lowest gain, cheapest fix: the `indicators`
   renderer already exists and simply did not fire. This is a routing miss, not a
   missing widget.

**Correctly left as prose:** turn 11 (flows/deals), turn 14 (news), turn 16's causal
paragraph. These are arguments, not tables, and a card would be decoration.

---

## 4. Chart / reply agreement

Every disagreement found, with evidence.

**Turn 1 — the reply contradicts the chart's own last bar.** Reply: *"The last notable
down day was 21 July, when RELIANCE closed 1.32% lower at 1,304.4."* The chart legend
in `chart_after_16_turns.jpg` reads `C 1,283.6 … −20.8 (−1.59%)`, i.e. the newest bar
fell **1.59%** from 1,304.4. Turn 0 states the window ended "to 1,283.6" and turn 9
calls 1,283.60 "the latest close". So the product named the second-largest recent down
day as the largest, while the larger one was the rightmost candle on screen. If 22 July
were a partial bar the reply must say so; it does not, and two other turns treat it as
a close.

**Turn 4 — two adjacent sentences contradict each other, and one is arithmetically
false.** *"It ranked last among the eight peers measured. It outperformed only BPCL
(−10.35%), IOC (−10.10%) and HINDPETRO (−12.36%)."* RELIANCE is −13.04%, which is worse
than all three. Ranked descending: AEGISLOG +81.73 > CHENNPETRO +67.73 > MRPL +28.84 >
CASTROLIND +1.36 > PETRONET −2.92 > IOC −10.10 > BPCL −10.35 > HINDPETRO −12.36 >
**RELIANCE −13.04**. The first sentence is right, the second is false, and the next one
compounds it: *"CASTROLIND (+1.36%) and PETRONET (−2.92%) **also** did better."*

**Turn 5 — the chart shows the wrong support, and there was a free slot.** Drawn:
`R 1,328.30 · Strong`, `R 1,290.47 · Moderate`, `S 1,122.36 · Strong`. Parked:
`S 1,277.26 · Strong`, `S 1,153.09 · Moderate`, `S 978.06 · Moderate`,
`R 1,365.40 · Strong`, `R 1,393.77 · Moderate`. Spot is 1,283.60.

- The cap is **two per side** (`dataserver.py:1483`, and the ranker is
  `(-strength_score, -(held-broke), abs(distance_pct))` per side). Resistance used
  both slots; **support used one of two**. So the cap did not park `S 1,277.26` — the
  selection did. Since the documented ranker would have taken two supports, this
  turn's picks did not come from the ranker; the model chose them (`draw_ids`).
- Consequence: the nearest support, **0.49% below spot and graded Strong**, is invisible,
  while a Strong support **12.6% below spot** is drawn. For a question that says "the
  levels that *actually matter*," that is backwards.
- The reply then invents a rationale the tool never supplied: *"was left off the chart
  to keep it readable."* The tool's own note says parked levels "scored lower"
  (`dataserver.py:1590`); `S 1,277.26` is graded Strong, so neither reason is true.
  With three lines on a 750-bar chart, "readable" is not a constraint.
- It also breaks the tool contract two ways: the note says **"never list them
  individually in the reply"** and "END your reply with exactly one sentence"; the reply
  names 1,277.26 individually and then says *"The other 4 levels are in the Layers
  panel"* when there are **5** parked. The sentence is defensible only if you already
  counted the one named above it.

**Turn 6 — the word contradicts the widget and the chart.** Prose: *"The relevant line
is a **descending** resistance trendline, and it has been drawn."* Card: *"Intact /
**Rising** resistance / 9 touches / Projects near ₹1,640.79."* Screenshot: the 9-touch
line is the orange line rising left-to-right from the 2024 lows. Three surfaces, two
directions. The reply then confirms the card is right — *"fitted through swing highs
from 7 Oct 2020 to 28 Nov 2025 … Current price is about 27.83% below its projected
level"* — so the opening word is simply wrong, and it is the first word the user reads.

**Turn 8 — the chip is wrong, and the reply contradicts it in the same turn.**
Chip: **"4 on chart"**. The scene gained `segment:bullish divergence` and
`segment:bearish divergence` (2 visible) plus one parked bearish. The reply itself
says: *"The third recent historical mark was added to Layers rather than displayed."*
Independent confirmation from `layers_panel.jpg`: the header reads **9/15**, and the
drawn list after all 17 turns is 9 items — the per-turn chips sum to 3+2+**4**+1+1 = 11.
Replace 4 with 2 and the sum is 9. So the chip over-counts by exactly 2 here and
nowhere else. Note that `chat.js:1081` already carries a comment naming this exact
symptom ("made the footer read '4 on chart' beside two visible bands") and skips
`a.hidden` items — filtering the one parked item would give **3**, not 2, so there is a
second over-count source: `get_divergences` emits more patch items than visible
annotations.

**Turn 9 — a navigation instruction for a state the user is already in.** *"Switch to
the daily or weekly view to see the entire profile."* `session.json` records
`interval: "1d"` on every turn and the pane header reads `RELIANCE · 1D`.

**Turn 10 — the takeaway is not computed from the table above it.** Table:
+2.97, −3.28, +3.39, −3.18.
- *"Average absolute results-day move: 2.42%"* — the table's four values average
  **3.21%**.
- *"Direction after results was mixed: 42% of reactions were positive"* — the table is
  **2 of 4 = 50%**. (42% ≈ 5/12, so the statistic almost certainly comes from twelve
  quarters.)
- *"Average five-session post-results move: +0.82%"* — this one **does** reconcile
  (+6.71 −2.07 +1.50 −2.93)/4 = +0.80%.
So one of three headline numbers comes from the displayed sample and two come from a
larger, undisclosed one, in a reply that opens *"I measured the last four quarters."*

**Turn 12 — the plan's stop is a level the user cannot see.** *"Stop: 1,277.26, just
below the detected support."* `S 1,277.26` is in the **parked** list from turn 5. The
`position` annotation draws its own support line, so something is at that price — but
the level layer that names and grades it is switched off, and the reply does not say
"this is the level I told you was in the Layers panel." Second and larger: the plan
puts a **₹6.34 stop and a ₹6.87 target** on a stock whose ATR the product measured at
**1.84% ≈ ₹23.6** (turn 4's compare card). Stop = 0.27 ATR, target = 0.29 ATR. Both sit
inside a single bar's ordinary range; the trade is noise. And at the zoom in the
screenshot the whole plan is sub-pixel — it cannot be seen at all.

**Turns 0 vs 5 vs 12 — the levels engines disagree and nobody reconciles them.** Turn 0:
*"Next resistance above ₹1,310.20, 3 touches"* and *"Next support below ₹1,253.20."*
Turn 5's eight levels contain **neither** 1,310.20 nor 1,253.20; its nearest resistance
is 1,290.47. Turn 12 then targets 1,290.47. Turn 13 says the falling line "projects
near 1,310.58." So across one session the "next resistance" is 1,310.20, then 1,290.47,
then 1,310.58. Also: **1,290 is called support in turn 0** ("Prior support, crossed in
this window") **and resistance in turn 5** — a legitimate role flip after price closed
below it, and no turn says so.

**Turn 16 — the pane opened does not match the answer given.** The reply quotes daily
bars (20/21/22 Jul closes, daily volumes, SMA20 4,385.9); the pane it opened is headed
`DATAPATTNS · 1h` in both screenshots, carries **no annotations**, and does not show the
4,955.9 resistance the reply names.

**What genuinely agrees.** Turn 15's figures are exact against the chart: RSI 43.19 =
the RSI pane badge `43.19`; ADX 12.73 / +DI 20.16 / −DI 24.77 = the ADX pane badges
`12.73 / 20.16 / 24.77`; and turns 7 and 13 quote the same ADX. That is the one place
in the session where words and chart are provably identical, and it should be the
standard.

---

## 5. Annotation lifecycle

**The facts.** After turn 16 the chart carries 9 live annotations and 6 parked, from
**five** questions (not seven): turn 5 (3 levels), turn 6 (2 trendlines), turn 8 (2
divergences), turn 9 (1 volume profile), turn 12 (1 trade plan). None expires, none is
superseded, none carries the question that produced it.

**The verdict: a fixed TTL is the wrong fix, and so is the current no-TTL.** Two of the
nine are structural facts about the instrument that *should* persist; four answer a
question the conversation has left; and one is a hypothetical the user never took. The
rule must be by kind, not by age.

**1. Structural annotations persist, but *supersede*.** Levels, trendlines and volume
profile describe the instrument, not the turn. They should live until the symbol or
interval changes — **and until the same tool scans the same symbol+interval again, at
which point the new result replaces the old one.** This session shows exactly why:
turn 6 drew a **rising** 9-touch resistance projecting ₹1,640.79 (27.8% above price);
turn 13 computed **descending** lines projecting ₹1,310.58 and ₹1,267.11 — 2.1% and 1.3%
from price, the ones that actually matter — and drew nothing. The result: by turn 16
the most prominent line on the chart is one the product's own latest answer had
superseded, and the two useful ones are invisible. That is the strongest argument in
the session and it is visible in the screenshot.

**2. Episodic annotations are provisional.** Divergence marks, the trade plan, gap
marks and pattern marks answer one question. Rule: **dim at the next turn, auto-park
(not delete) after three turns, with an undo chip in the Layers panel.** The turn-12
plan is the clearest case — a hypothetical long the user never took, still on the chart
four turns later while he reads the news, with a 0.5% target that is invisible at the
prevailing zoom. It should expire at end of turn unless the user acts on it, and carry
a **"hypothetical"** badge while it lives.

**3. Every annotation carries provenance.** The Layers panel shows no timestamp and no
originating question. After 17 turns the user cannot answer "why is this line here?"
for any of the 15 rows. One line of metadata per row — turn number, the question's
first six words, the time — makes the whole panel auditable.

**4. Separate the model's marks from the user's.** The panel merges them into one
undifferentiated 15-row list with a single bulk delete. There must be a
"clear the assistant's annotations" action that does not touch the user's own drawings.

**5. Auto-frame, or say you can't.** Seven turns discussed 8–22 July 2026 — about 11
bars, roughly 5 px at the screenshot's zoom. The chart never framed the window the
answer was about, so several annotations are technically present and practically
unobservable.

---

## 6. Trust and honesty

**This is the best thing about the build, and it should be protected.** The refusals
are well-placed, specific about *what* is missing, and — unusually — bounded about what
the available evidence can and cannot support:

- T0/T1: *"Delivery, futures open-interest and bulk/block-deal data were unavailable,
  so there is no reliable basis here to say whether the selling was delivery-backed or
  short-driven."*
- T1: *"the available event record does not establish that the results caused the 21
  July decline."* — a genuinely hard distinction (timing vs causation) held correctly.
- T3: *"I would not classify those as large caps without a separate market-cap filter."*
- T8: *"only two observations of each type, so it is not a dependable hit rate."*
- T9: *"volume at price, not order-flow data: it cannot identify whether buyers or
  sellers were the aggressors."*
- T10: *"not yet five complete sessions to assess its post-results drift."*
- T12: *"too close to call a reliable edge."*
- T16: *"tested only once and has no re-test record yet, so its historical reliability
  cannot be judged"* and *"I would not attribute the move to news without confirmation."*

That is a consistent discipline across nine turns and three different failure modes
(missing table, insufficient sample, timing-not-causation). It clears the bar.

**Where the refusals stop short.** Every one of them says *no* and none names the
nearest real thing. Turn 11 is two sentences and a dead end. The product had at least
three participation proxies in hand at that moment — volume 0.98× the 20-day average
(turn 0's card), the intraday path decomposition (turn 1), and average turnover per bar
(turn 4's card). The turn should read: *"FII/DII tables aren't synced. What I can show
instead: volume ran 0.98× its 20-day average and the 21 July decline was spread evenly
through the session rather than front-loaded — which is the participation question
delivery data would otherwise settle."* One sentence, and the turn stops being a wall.

**Where a refusal is ambiguous.** Turn 16: *"I cannot verify a specific news catalyst
from the available data."* But turn 14 produced dated news for RELIANCE (Jefferies,
postal ballot, KG-D6), so the product *has* a news path. The user cannot tell whether
turn 16 means "I searched DATAPATTNS and found nothing" or "I have no news source for
this name." A refusal must name the source it queried and the window it covered.

**As-of disclosure — and why it is structural, not behavioural.**

*Discloses:* T0 (card footer `08 Jul 2026 → 22 Jul 2026`), T2 (*"End-of-day data as of
22 Jul 2026"*), T3, T4 (card footer), T6 (card footer `1500 1d bars · 08 Jul 2020 → 22
Jul 2026 IST`), T9 (*"covering 17 Jul 2025–22 Jul 2026"*), T10 (dates in table), T11
(*"the recent 9–22 July 2026 window"*), T13 (card footer), T16 (dates in table).

*Silently answers "right now":* **T5, T7, T8, T12, T14** (T15 partially — it dates the
prior reading, "down from 48.61 on 21 July", but never stamps the current one).

The pattern is not model discipline; it is **tool-note discipline.** Every disclosing
turn is one where a card footer or a tool `_note` forced it — `screen_universe`
literally injects *"Every value is an end-of-day figure as of {as_of}"*
(`dataserver.py:5634`). Where no note fires, no date appears. That makes this a
one-line UI fix, not a prompt problem.

Two of the silent turns are the dangerous ones:
- **T14** was asked *"What is the news around this stock right now?"* and answered under
  the header **"RELIANCE: current news"** with 17–21 July items and a forward-looking
  *"a 2026 tribunal verdict is awaited"*, on 2 September. Nothing on screen says how old
  this is.
- **T12** is the only turn whose output looks like an instruction — *"If I bought here"*,
  entry 1,283.60 — and it carries no date at all. A stale entry price rendered as a live
  trade plan is the highest-consequence silent staleness in the session.

---

## 7. Ranked improvement list

Ranked by user impact.

---

**P0-1 — `compare` summary states a false ranking.**
*Defect:* the prose ranking is computed wrong and contradicts the sentence before it.
*Evidence:* T4 — *"It ranked last among the eight peers measured. It outperformed only
BPCL (−10.35%), IOC (−10.10%) and HINDPETRO (−12.36%)."* RELIANCE is −13.04%, worse than
all three.
*Cost:* the peer comparison is the entire answer, and a user who trusts sentence two
walks away believing RELIANCE beat three peers it lost to. One provably false ranking
sentence discredits every other number in the session.
*Fix:* do not let the model rank. Have `compare_symbols` return a precomputed
`rank` (1..n), `beat: [...]`, `lost_to: [...]` in the tool payload, and add a tool
`_note` in the style already used by `get_levels`: *"Quote rank and the beat/lost_to
lists verbatim; do not derive an ordering yourself."* Add a unit test asserting
`rank == 1 + count(peers with higher return)`.

---

**P0-2 — the results takeaway is computed on a different sample than the table it sits
under.**
*Defect:* two of three headline statistics do not reconcile with the four rows above
them.
*Evidence:* T10 — table gives +2.97/−3.28/+3.39/−3.18; reply claims *"Average absolute
results-day move: 2.42%"* (table = 3.21%) and *"42% of reactions were positive"*
(table = 50%; 42% ≈ 5/12), under the opening line *"I measured the last four quarters."*
*Cost:* the user can do this subtraction in five seconds. Once he catches it, every
other unverifiable statistic in the product is suspect.
*Fix:* `evaluate_results` must return `sample_n` alongside every aggregate and the tool
note must require it be stated: either display all 12 rows, or label the aggregates
*"across the last 12 quarters"* while the table shows 4. Assert in a test that any
aggregate whose `sample_n` differs from `len(rows_displayed)` carries an explicit
sample label.

---

**P0-3 — "the last big down day" is not the last big down day, and the card is for the
wrong window.**
*Defect:* the down-day selector ignores the newest bar, and `explain_move` returned its
cached window card for a single-session question.
*Evidence:* T1 — *"The last notable down day was 21 July, when RELIANCE closed 1.32%
lower at 1,304.4"*, while the chart legend reads `C 1,283.6 · −20.8 (−1.59%)`. The card
above it is byte-identical to turn 0's `08 Jul 2026 → 22 Jul 2026` card.
*Cost:* the user asked about one day and got the wrong day plus a widget about a
fortnight. The intraday decomposition beneath it (gap −0.07 / first 15m −0.44 / …) is
correct *for the wrong session*.
*Fix:* (a) the down-day selector must include the last completed bar and rank by
absolute return, and must say "as of the last complete session, DD MMM"; (b) when
`explain_move` is called with a single-session scope, the card must render that
session's window — a card whose window does not match the question's scope should not
render at all.

---

**P0-4 — `get_levels` parked the nearest Strong support into an empty slot.**
*Defect:* level selection ignored proximity to spot and did not fill the second support
slot, then the reply invented a reason.
*Evidence:* T5 — drawn `S 1,122.36 · Strong` (12.6% below spot 1,283.60); parked
`S 1,277.26 · Strong` (0.49% below spot). Cap is 2 per side (`dataserver.py:1483`) and
only 1 support was drawn. Reply: *"was left off the chart to keep it readable"* — the
tool's note says parked levels "scored lower"; this one is graded Strong.
*Cost:* this is the flagship "draw what matters" turn, and it hides the only level that
mattered — the one that becomes the stop in turn 12 seven turns later.
*Fix:* three parts. (a) Add a **proximity guarantee**: whatever the score ranking says,
the nearest support and nearest resistance to spot are always drawn — bump the cap to 3
on that side if needed. (b) When `draw_ids` is supplied, validate that the nearest level
per side is included, and return a `_note` rejecting the set if not. (c) Forbid the
readability rationale: the tool note should say *"if asked why a level is parked, say
it scored lower on held-of-graded evidence; never cite chart readability."*

---

**P0-5 — `plan_position` produces a sub-noise trade with no volatility check.**
*Defect:* stop and target are chosen from nearest detected levels with no reference to
realised volatility.
*Evidence:* T12 — *"Risk: 6.34 points per share, or 0.49% … Reward: 6.87 points, or
0.54% … Reward:risk 1.08:1"* on a stock whose daily ATR the product itself measured at
**1.84% (₹23.6)** in T4. Stop = 0.27 ATR.
*Cost:* this is the only output in the session shaped like an instruction, and following
it stops the user out on ordinary intrabar noise, roughly at random, with costs. It is
the one turn that can lose the user money.
*Fix:* compute `stop_atr = risk_points / ATR14`. If `stop_atr < 0.75`, the tool must
either widen the stop to the next level out or return a hard `_note`: *"This stop is
0.27× ATR — inside one session's ordinary range. Lead the reply by saying the plan is
too tight to be tradeable on this timeframe, and offer the next level out or a higher
interval."* Surface `stop_atr` and the breakeven-vs-observed hit rate in a
`plan_position` card (P1-9's sibling; the `bars(..., {tick})` helper at `cards.js:1087`
already renders exactly this comparison).

---

**P0-6 — no universal as-of stamp.**
*Defect:* date disclosure depends on whether a tool happened to inject a note.
*Evidence:* T14 answers *"news right now"* under the header **"current news"** with
17–21 July items and no date anywhere, on 2 Sep. T12 gives an entry price with no date.
T5, T7, T8 likewise. Every disclosing turn had a card footer or a tool note forcing it
(`dataserver.py:5634`).
*Cost:* the product's central promise is that it does not fabricate. Presenting 6-week-
old bars as "right now" is the same failure by omission, and it is invisible to the user.
*Fix:* render the data as-of in the per-turn meta row that already carries latency and
the "N on chart" chip (`chat.js` ~1255), sourced from the scene's last bar time, not
from the model — `Data to 22 Jul 2026 · 20.8s · 1 on chart`. Frontend-only, one row,
every turn. Additionally: when the last bar is more than 3 sessions old, style the stamp
as a warning.

---

**P1-7 — trendlines contradict in direction and are never superseded.**
*Defect:* the reply's direction word disagrees with the card and the chart, and a later,
better trendline scan does not replace the earlier one.
*Evidence:* T6 prose *"a **descending** resistance trendline"* vs card *"Rising
resistance · 9 touches · Projects near ₹1,640.79"* and the visibly rising orange line in
`chart_after_16_turns.jpg`. T13 then finds *"Descending resistance · 6 touches ·
Projects near ₹1,310.58"* and *"Descending support · 7 touches · ₹1,267.11"* and draws
neither.
*Cost:* by turn 16 the chart's most prominent annotation is one the product's own latest
answer replaced, projecting 27.8% away, while the two lines within 2% of price are
invisible.
*Fix:* (a) `get_trendlines` must return `direction: "rising"|"descending"` per line and
the tool note must require the reply use that word verbatim; (b) a second scan by the
same tool on the same symbol+interval **supersedes** the first in the scene — replace
the rows, do not append; (c) when a projected level is more than ~10% from spot, the
tool note should require the reply to say so in the first sentence, not the fourth.

---

**P1-8 — the "N on chart" chip over-counts.**
*Defect:* the chip counts patch items, not visible annotations.
*Evidence:* T8 chip reads **"4 on chart"**; two segments became visible and the reply
itself says *"The third recent historical mark was added to Layers rather than
displayed."* `layers_panel.jpg` reads **9/15**, and the per-turn chips sum to
3+2+4+1+1 = 11; with 2 in place of 4 they sum to 9.
*Cost:* the chip's only job is to reconcile the reply to the chart. A chip that
disagrees with both the chart and the sentence above it does the opposite.
*Fix:* `chat.js:1081` already skips `a.hidden`, which would give 3 — so
`get_divergences` emits an extra non-hidden patch item per scan. Derive the chip from
`scene.visible.length` before and after the patch rather than from the patch itself, and
add a test asserting `chip === visibleAfter - visibleBefore` for every drawing tool.

---

**P1-9 — `screen_universe` has no widget (and its follow-up filter is ungrounded).**
*Defect:* 44 rows × 6 columns of markdown, no sort, no filter, no click-through; and the
market-cap column that the next question needs does not exist, so the model classified
by memory.
*Evidence:* T2, 62.7 s, 44 rows ranked by an unbounded z (28.43 → 0.03) with the filter
set at `vol_z20 > 0`. T3 — *"the clear large-cap names"*, then *"I would not classify
those as large caps without a separate market-cap filter."* T3's table is also unsorted
(−0.11, −0.50, −0.66, **−0.54**, −1.09, −1.39).
*Cost:* the user's follow-up exists only because the first surface could not be
filtered, and the answer to it is a model guess dressed as a screen result.
*Fix:* build a `screen` card — virtualised sortable table; columns symbol / close /
vs-SMA20/50/200 / vol z / **market cap** / **distance from 52w high**; header sort;
market-cap band filter chips; row click → `open_chart` in a second pane. Add market cap
and 52w-high distance to the 17-feature universe matrix so no classification is ever
asserted from model memory. Add a `vol_z20 >= 1.0` default floor (30 of 44 rows scored
below 1) and state the floor in the reply.

---

**P1-10 — annotations have no lifecycle.**
*Defect:* nothing expires, nothing supersedes, nothing records why it exists.
*Evidence:* 9 live annotations from 5 turns after 17 turns; the T12 hypothetical trade
plan is still on the chart during the T14 news question; `layers_panel.jpg` carries no
timestamp and no originating question on any of 15 rows.
*Cost:* the chart stops being an answer surface and becomes an archive, and the user
cannot audit any mark.
*Fix:* classify each annotation as `structural` (levels, trendlines, volume profile) or
`episodic` (divergences, plans, gaps, patterns). Structural: persist until symbol or
interval changes, and supersede on re-scan by the same tool. Episodic: dim at the next
turn, auto-park after 3 turns, undo chip in the panel. Every scene row stores
`{turn, question_excerpt, created_at}` and the Layers panel renders it. Add a
"clear assistant annotations" action distinct from the existing bulk delete.

---

**P1-11 — the chart never frames the window the answer is about, and a new pane inherits
the wrong interval.**
*Defect:* no auto-zoom to the discussed range; `open_chart` picks its own interval.
*Evidence:* `chart_after_16_turns.jpg` spans ~2023–2026 while seven turns discuss 8–22
July 2026 (~11 bars, ~5 px). T12's ₹6.34 stop and ₹6.87 target are sub-pixel and
invisible. T16 opened `DATAPATTNS · 1h` while its answer quotes daily closes, daily
volumes and a 20-day SMA.
*Cost:* the annotations the user paid a turn for cannot be seen, and the second pane
does not show the data the sentence beside it is describing.
*Fix:* when a tool returns a `window`, animate the visible range to it with ~15%
padding (never destroying the user's manual zoom without a one-click "restore view").
`open_chart` inherits the calling pane's interval unless the tool payload names one.

---

**P1-12 — the prose reads the card aloud.**
*Defect:* the paragraph beneath a card restates the card's fields as facts.
*Evidence:* T0 (window move / typical move / percentile / index attribution all repeated
verbatim), T7 (five card fields repeated as five bullets), T13 (both trendline
projections repeated word for word).
*Cost:* the reply is 30–40% longer than the answer, and the padding is exactly where the
user's attention would otherwise land on the judgement.
*Fix:* a reply-shape rule in the system prompt, enforced per card kind: *"A number
already on the card may appear in the prose only inside a judgement or a comparison,
never as a standalone restatement."* Add an eval check that flags any reply in which
>50% of the card's numeric fields recur as bare statements.

---

**P1-13 — the `compare` card is ordered alphabetically and runs out of colours.**
*Defect:* peers are rendered in input order (`cards.js:1977`, no sort) and the symbol
palette clamps at four (`cards.js:1980`, `"s" + Math.min(i + 1, 4)`).
*Evidence:* T4 — RELIANCE, AEGISLOG, BPCL, CASTROLIND, CHENNPETRO, HINDPETRO, IOC, MRPL
across four sections; five of the eight share tone `s4`, breaking the card's own stated
guarantee that "the same symbol keeps its colour through every section."
*Cost:* the answer to "how did it do against peers" is a rank, and the card makes the
user compute it from eight unsorted bars he cannot track between sections.
*Fix:* sort descending by the primary metric with the subject symbol pinned and
highlighted, keep the sort stable across all four sections, and extend the palette to
10 with a deterministic symbol→tone hash. Add a rank badge on the subject's bar.
Separately: never render a second `compare` card to add one name — append the row.

---

**P2-14 — the annotation surfaces are hard to read.**
*Defect:* the on-chart legend and the Layers panel both fail at the scale this session
reached.
*Evidence:* `chart_after_16_turns.jpg` — a 9-row right-aligned legend column sits on top
of the 2025–26 candles and crosses the trendline it describes; rows are ~11 px, low
contrast; two rows differ by one word (*"bullish divergence · resolved 2/2"* /
*"bearish divergence · resolved 2/2"*); POC/VAH/VAL have no axis badges while `1296.14`
and `1283.60` collide near the 1300 gridline; the price pane is ~45% of pane height with
ATR/ADX/RSI taking the rest. `layers_panel.jpg` — genuinely good grouping (DIVERGENCES /
LEVELS / PLANS / TREND / VOLUME PROFILE), clear hidden state, honest `9/15` count; but
two rows read *"bearish divergence · resolved …"* identically with the disambiguating
dates truncated away; LEVELS is sorted visible-then-hidden rather than by price
(R 1,290.47, R 1,328.30, S 1,122.36, R 1,365.40, R 1,393.77, S 1,153.09, **S 1,277.26**,
S 978.06 — the nearest support to spot is 7th of 8); the right-hand type chip
("Level", "Line") repeats both the icon and the group header and eats the width the
truncated labels needed; the panel covers the left third of the price pane.
*Cost:* the parked levels are only recoverable through this panel, and the level the
user most needs (T5, T12) is the hardest one to find in it.
*Fix:* move the annotation legend out of the plot area into a collapsible strip under
the OHLC legend, or reduce it to coloured dots with hover. Give volume-profile lines
axis badges. Sort LEVELS by price with a spot marker inserted and add a **distance-%**
column. Drop the type chip (the group header carries it) and spend the width on
untruncated labels including dates.

---

**P2-15 — refusals name the gap but never the substitute.**
*Defect:* an unavailable data path ends the turn instead of redirecting it.
*Evidence:* T11 in full — *"I can't verify recent FII/DII buying or selling because the
flows tables for RELIANCE are not currently synced… there are no bulk or block deals
recorded."* Two sentences, no alternative, though volume-vs-20d-avg, the intraday path
decomposition and average turnover per bar were all already computed in this session.
T16 — *"I cannot verify a specific news catalyst from the available data"* without saying
whether a news source was queried, when T14 shows one exists.
*Cost:* an honest dead end still costs the user a turn and teaches him not to ask.
*Fix:* every unavailable-data `_note` gains a `substitutes: [...]` list naming tools
already answered this session, and the note requires one sentence of the form
*"What I can show instead: X, which is the part of your question Y would otherwise
settle."* Every refusal must also name the source queried and the window covered, so
"searched and found nothing" is never confusable with "no source exists."
