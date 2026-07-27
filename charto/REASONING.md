# Charto — the reasoning system for "why did it move?"

> Research and design, 2026-07-27. Every number below was measured against the
> live `gpt-5.6-luna` deployment and the local 11.5-year 1-minute store, not
> estimated. Nothing here is built yet; §9 is the build order.
> Companion to `CHARTO.md` (constitution §2, LLM/code split §3, anti-determinism
> doctrine §4, evidence hierarchy §5, feature F 40-42).

---

## 1. The problem, stated honestly

A trader points at a candle and asks *why*. Today Charto answers only the
technical half — levels, patterns, gaps, indicators — because that is all the
tools expose. The other half of the real answer lives outside the chart: a
filing, a sector move, a crude spike, a block deal, an index rebalance, or
nothing at all.

The naive build is to bolt a web search onto the chat and let the model narrate.
That build is worse than not building it, and §3 shows the measurement proving
why. The design problem is not *how do we fetch news* — it is **how do we let
an LLM reason causally without letting it invent causation**.

Three constraints from the user, held throughout: minimal determinism, minimal
token addition, low latency.

---

## 2. What the question actually decomposes into

"Why did it move" is not one question. It is a ladder, and every rung above the
one you can answer makes the answer below it dishonest:

| # | Rung | Where the answer lives | Status |
|---|---|---|---|
| 1 | Is the move even notable, vs this stock's own distribution? | local bars | **missing** |
| 2 | Is it the stock, the sector, or the whole market? | needs a benchmark series | **missing (§7)** |
| 3 | *When* in the session did it happen? | local 1-min bars | **missing** |
| 4 | Was there a scheduled event? | `results` table | built |
| 5 | Was there an unscheduled event? | the web | **missing** |
| 6 | What does the structure say? | levels/patterns/gaps detectors | built |
| 7 | Do moves of this shape persist or revert *on this symbol*? | local bars | **missing** |

Rungs 1, 2, 3, 7 are arithmetic on data we already hold (rung 2 needs one extra
series). Only rung 5 needs the outside world. **Six of seven rungs are local,
free, and fast** — which is the whole reason this can be built without wrecking
latency.

The failure mode of every competitor is answering rung 5 while skipping 1-3.
That produces a confident sentence about Middle East tensions for a move that
was, in fact, an unremarkable Tuesday.

---

## 3. The measurement that decides the architecture

### 3.1 The web will overwrite your own numbers

Asked why Reliance moved on 22 July 2026, with hosted search and **no chart
tools**, the model answered:

> "Reliance fell about **1.16%** on July 22, 2026, closing at roughly
> **₹1,288.6** versus ₹1,303.7 on July 21" — sourced to a data-aggregator page.

Our own Kite bars, which the user is looking at on screen:

| Date | Close | Change |
|---|---:|---:|
| 21 Jul | 1,304.40 | −1.32% |
| 22 Jul | **1,283.60** | **−1.59%** |

The direction was right and every quantity was wrong. It also asserted a cause
— "broad sell-off on Middle East tensions, Brent above $95" — with real
citations, which is exactly what makes it dangerous: a well-sourced sentence
wrapped around fabricated figures.

**Rail, structurally enforced, not prompted:** the web supplies *causes* —
events, dates, sources. Tools supply *quantities*. A price, return, volume or
level may never enter an answer from a search result.

### 3.2 The anatomy tells a different — and better — story

The same move, decomposed from our 1-minute bars:

```
2026-07-22   prev close 1304.40 → close 1283.60   total -1.59%   vol 10,776,456
    gap (overnight)    -0.18%      ← almost nothing happened overnight
    09:15-09:30        -0.17%
    09:30-12:00        -0.81%      ← the bulk, mid-morning
    12:00-14:30        +0.09%
    14:30-close        -0.53%      ← second leg into the close
    first 30m vol 13%    last 30m vol 27%
```

A −0.18% gap **falsifies** the overnight-news story the browsed model told. The
move was made during the session, with 27% of the day's volume in the last
half-hour. That is a flow/positioning signature, not an information shock — and
it was free, instant, and derived from data we already own.

This is the single strongest argument for the design: **the local anatomy is
often a better answer than the search, and it is always the honest gate on it.**

### 3.3 The hosted search is expensive to *carry*, cheap to *use*

Same message ("hi"), one call each, exact `input_tokens`:

| Tools attached | input tokens |
|---|---:|
| none | 7 |
| `web_search_preview` only | **4,302** |
| all 15 charto tools | 4,399 |
| 15 charto tools + hosted search | 8,694 |

**Attaching the hosted search costs ~4,295 input tokens — as much as all
fifteen charto tools combined.** It ships a large provider-side instruction
payload whether or not it is used.

And that cost is re-paid on **every hop**, because reported input is the sum
across tool rounds. On a 3-round turn, always-on hosted search adds ~12,900
tokens *even when it never searches*. That is not "minimal token addition" — it
roughly doubles every turn in the product.

### 3.4 But the model does not over-search

With the hosted tool attached and charto's tools present:

| Question | rounds | searches |
|---|---:|---:|
| "what are the support and resistance levels here?" | 3 | **0** |
| "is the RSI oversold on the daily?" | 2 | **0** |
| "hi" | 1 | **0** |
| "why did it move 21-22 July?" | 2 | 1 |

Zero over-firing. The model searches when the question is causal and not
otherwise. **No intent classifier is needed** — which matters, because
`CHARTO.md` §4.1 forbids one. (Pivot's `_hosted_tools_for()` regex lanes are
precisely the pre-LLM interception Charto exists to avoid.)

So the problem is not *when* to offer search. It is **how to offer it without
paying 4,295 tokens a hop to have it on the menu.**

---

## 4. The architecture

### 4.1 Search as an isolated sub-call, not an attached tool

Give the model a thin, ordinary function tool — `search_news` — whose
*implementation* makes a second, throwaway LLM call that carries the hosted
search. The expensive provider payload never enters the main conversation.

```
main loop  ──calls──▶  search_news(query, on_date)
                            │
                            ├─ isolated Responses call: tiny prompt
                            │  + web_search_preview   (~4,300 + retrieved text,
                            │                           billed ONCE, discarded)
                            │
                            └─▶ returns ~300 tokens: dated events + sources,
                                explicitly NO quantities
```

Cost comparison for a 3-hop turn:

| | never searches | searches once |
|---|---:|---:|
| hosted tool always attached | **+12,885** tok | +12,885 + retrieved text re-billed each later round |
| `search_news` sub-call (this design) | **+~360** tok | +~360 + one isolated ~20.5k call |

**36× cheaper on the common path**, and on the searching path the retrieved
20k of web text is billed once in a context that is thrown away, instead of
riding along in the main wire for every subsequent round.

It also enforces §3.1 structurally rather than by instruction: the sub-call is
prompted to return events, dates and sources, so raw price tables from
aggregator pages never reach the main model at all.

Measured sub-call profile: 11.6s, 20,501 input, 3 real citations.

### 4.2 One composite local tool, not six narrow ones

Asked the causal question with today's tools, the model made **9 tool calls
across 3 rounds, 28.3s** — `get_bars`, `get_levels`, `get_indicator` ×3,
`get_patterns`, `get_gaps`, `get_results`, `evaluate_results`. It was
hand-assembling the anatomy one call at a time.

Hop count is the dominant latency and token lever (already established: the
~5,036 floor is re-paid per hop). So the fix is **fewer, richer tools** —
the opposite of adding surface area.

`explain_move(frm, to)` — one call, one round, returns the whole ladder:

- **Abnormality** — the move in units of this symbol's own recent σ and ATR,
  plus its percentile against its own history. *Rung 1. This is the gate: if
  the move is inside the normal band, the honest answer is "nothing to
  explain" and no search should happen.*
- **Attribution split** — benchmark return over the same window and the
  residual. *Rung 2, needs §7.*
- **Intraday decomposition** — gap / opening / midday / close legs, and volume
  concentration in the first and last 30 minutes. *Rung 3, from 1-min bars.*
- **Scheduled events** — results inside or adjacent to the window (reuses the
  built `results` table and `_result_bar_index`).
- **Structure** — nearest levels, any pattern terminating in the window, gap
  status (reuses existing detectors internally; no new math).
- **Shape base rate** — of prior moves on this symbol with this signature, how
  many continued next session, **beside the unconditional base rate**. *Rung 7,
  and it obeys the existing rail: a rate without a control is decoration.*

Everything here is arithmetic over data we hold. No new detectors, no new
dependencies beyond §7.

### 4.3 The prompt block (~120 tokens)

The only prose addition. It states the rail, not a procedure:

- Numbers come from tools; the web supplies only what happened, with dates and
  sources. If a headline and a tool disagree, the tool is right.
- Check whether the move is abnormal before looking for a cause.
- A market-wide move is not a stock story — say which it was.
- **"No clear catalyst" is a complete, correct answer** and will often be the
  right one.

That last line is load-bearing and §5 is why.

---

## 5. The honesty ceiling — say it out loud

Koijen & Levy (Chicago Booth, live test over ~2,000 earnings announcements,
late 2025):

| Method | share of same-day moves explained |
|---|---:|
| Earnings surprise vs analyst forecast | ~5% |
| Decades of accumulated academic research | ~8% |
| Best AI models reading calls and news | **~17%** (they lift it to 20%) |

**Roughly 80% of same-day moves have no recoverable explanation.** Any product
that produces a confident cause every day is manufacturing narrative, and every
Indian retail user has been trained by finfluencer content to expect exactly
that.

This is the differentiator, not a limitation. `CHARTO.md` §2.4 already commits
to it — "attribution says 'no clear catalyst' most days and means it." The
measured design makes that commitment cheap to honour, because the abnormality
gate (§4.2) answers "was there even anything to explain" before a search is
ever considered.

---

## 6. The behavioural layer, without the astrology

"Investors panicked" is unfalsifiable and forbidden by the never-fabricate rule.
The honest version infers behaviour **from named observables**, and labels the
inference as an inference:

| Observable (computed) | Behavioural reading (model's inference) |
|---|---|
| Move is in the gap, not the session | Overnight information; the market repriced before anyone traded |
| Move made during the session, small gap | Flow and positioning, not an information shock |
| Volume concentrated in the last 30 min | Institutional / index / MTF-unwind footprint |
| Opening drive then full fade | Early positioning trapped; the move lacked follow-through |
| Range large but volume ordinary | Thin-liquidity move — weaker evidence than it looks |
| This shape historically reverts on this symbol | Liquidity event, not information |

The rail: **every behavioural sentence must name the observable under it.** "27%
of the day's volume traded in the last half hour" is a fact from our bars;
"that looks like an institutional unwind" is an inference the model states as
one. This is the same discipline as the evidence hierarchy in `CHARTO.md` §5,
applied to intent rather than to drawings.

The base-rate column (§4.2, rung 7) is what makes this more than vibes: with
11.5 years of 1-minute bars we can answer "when RELIANCE has done this before,
what happened next" — with a control — rather than asserting a motive.

---

## 7. The one real dependency: a benchmark series

The local store holds exactly one symbol: **RELIANCE, 1,060,508 bars,
2015-02-02 → 2026-07-23**. No index, no peers.

Without a benchmark, rung 2 is impossible, and rung 2 is the honesty gate that
stops a market-wide selloff being sold as a Reliance story. On 22 July the
browsed model itself claimed "broad market sell-off" — we currently have no way
to confirm or refute that from our own data.

**Required: NIFTY 50 daily (and ideally 5-minute) bars.** One series. The Kite
backfill path is already built and proven (`backfill_1min.py`).

This is *not* the multi-symbol charting feature declined earlier — nothing new
gets rendered, no symbol switcher, no second chart. It is one reference series
read by one tool. Worth stating plainly so the scope call is yours to make.
Sector peers (a handful of large caps) would sharpen "sector vs stock" later,
but the index alone unlocks the gate.

---

## 8. Why this respects the constitution

| Doctrine | How this design holds it |
|---|---|
| §4.1 no pre-LLM interception | No regex, no classifier, no lane selection. Two tools on the menu; the model chooses. Measured: zero over-firing (§3.4). |
| §3 model owns meaning, code owns math | Code computes abnormality, residual, decomposition, base rates. The model decides which cause is plausible and writes the sentence. |
| §4.2 constrain at the boundary | `search_news` returns typed events; a result carrying no dated event returns "nothing found", never prose to be trusted. |
| §4.3 fix what the model sees | The whole design is a richer tool return, not a validator. |
| §2.4 honest confidence | The abnormality gate makes "no clear catalyst" the cheap default rather than an apology. |
| §2.6 latency boundary | One composite call replaces ~6; search costs time only when the model elects it. |

Nothing in this design edits the model's output after the fact, and nothing
decides meaning before the model sees the message.

---

## 9. Build order

1. **`explain_move` (local, composite).** Rungs 1, 3, 4, 6, 7 — everything that
   needs no new data. Biggest answer-quality gain per token, and it *reduces*
   round count. Ship alone and the causal answer is already better than today's.
2. **NIFTY benchmark backfill + rung 2.** One series; turns attribution honest.
3. **`search_news` sub-call.** The outside world, isolated, ~360 tokens on the
   main loop.
4. **Prompt block + the base-rate control.** Cheap; do with 1 and 3.
5. **Measure**: a fixed probe set of causal questions, scored on the triad
   (tokens, latency, quality) plus a fourth column this feature needs —
   *catalyst claimed vs catalyst real* — and the share of turns answered "no
   clear catalyst". If that share is far below ~80%, the system is
   manufacturing narrative and the gate is too loose.

Deferred deliberately: OI 4-quadrant and delivery % (real signals per the
earlier research, but they need an F&O/bhavcopy feed charto does not have);
sector peers; block/bulk deals.

---

## 10. Cost and latency summary (measured)

| Path | added input tokens | added latency |
|---|---:|---:|
| `explain_move` schema, carried every hop | ~250-350 | 0 |
| `search_news` schema, carried every hop | ~120 | 0 |
| Prompt block, carried every turn | ~120 | 0 |
| `explain_move` fired | ~1-2k (its result) | <100ms local, and **removes a round** |
| `search_news` fired | ~300 into main loop; ~20.5k in a discarded sub-call | ~11s |
| *(rejected)* hosted search attached always | **+4,295 per hop** | 0 |

Expected net effect on a typical causal turn: **fewer rounds than today**
(one composite call replacing the 9-call, 3-round fan-out measured in §4.2),
with search bought only when the question is genuinely about the outside world.

---

## 11. Build result (2026-07-27 — §9 items 1-4 SHIPPED)

Built: `explain_move` (composite, ~650-token return), NIFTY benchmark via
`sync_benchmark.py` (yfinance EOD, tagged, 2,846 sessions, `--kite` upgrade
path), `search_news` (isolated sub-call, effort=low, (symbol,window)-keyed
SQLite cache), `CAUSAL_RULES` rail (~140 tok), and the `mult` crash fix.
Carried cost measured: bare-floor 5,020 vs 5,036 before with the chart
envelope in place of the new schemas — net ~+490/hop for the whole feature.

Live multi-turn eval through `/chat` (one conversation, triad per turn):

| Turn | s | in | out | tools | verdict |
|---|---:|---:|---:|---|---|
| why did it fall 21-22 Jul | 26.0 | 17.3k | 671 | explain_move, search_news | market third vs stock two-thirds via beta split; anatomy cited; inference labelled |
| any news behind it? | 17.0 | 18.4k | 523 | both (search cached) | dated events + sources; widened window itself to include results day |
| daily RSI oversold? | 12.7 | 11.8k | 181 | get_indicator | regression clean — no causal machinery fired |
| why move on 2 Jun (flat day) | 12.3 | 12.3k | 382 | explain_move only | "didn't materially move… no clear catalyst" — did NOT search |

Baseline for the same causal question before the build: 28.3s · 32k · 3
rounds · 9 calls, and no honest way to separate market from stock.

One legibility fix from the run (single sanctioned retest, passed): the
results field is now `first_reactable_session` — a bare `date` got the
release day and the reactable day conflated. Emergent behaviour worth
keeping: the model widened the search window on its own after explain_move
showed results one session pre-window — the aimed-search loop working as
designed, unprompted.

Still open from §9: item 5's fixed probe set with the *catalyst claimed vs
real* column tracked release-over-release; block/bulk + delivery % + OI
doors (need feeds); Kite re-login to upgrade the benchmark rows.

---

## Appendix — bug found while measuring

`get_indicator` forwards `mult` to every indicator function, but only
`bbands` / `keltner` / `supertrend` accept it. In the §4.2 probe the model
passed `mult` to `rsi`, `macd`, `adx` and `obv` and **four of nine tool calls
crashed** (`_f_rsi() got an unexpected keyword argument 'mult'`,
`dataserver.py:2753`). Wasted hops are the dominant cost driver, so this is
worth fixing regardless of this feature: pass `mult` only to the functions
whose signature accepts it.
