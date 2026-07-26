# Charto — the analyst at your chart

> **What this is.** Charto is a proposed product surface for Pivot: a dual-pane
> experience where **chat is the single source of truth and a candlestick chart
> is the renderer of that chat**. This document is the constitution, feature
> inventory, and build guide for it.
>
> **Status: ideation + feasibility complete, nothing built.** As of 2026-07-23
> this is a design module, not shipped code. Two codebase audits (frontend +
> backend) and a three-agent web-research sweep back the claims here; the
> feasibility verdicts and roadmap reflect what the Pivot repo actually
> contained on that date.
>
> **Why this folder is separate.** Charto is a **trial-and-error sandbox**. It
> is deliberately kept out of `pivot/`, `pivot-next/`, `Markdowns/`, and the
> other production trees so that exploratory notes, throwaway prototypes, and
> half-formed specs never get mistaken for the live product or its committed V2
> plan. Nothing in this folder is wired into the running app. When a Charto idea
> graduates to a real build, it moves out of here and follows the normal Pivot
> conventions (new `_render_hint` + FE card + deploy path, migrations, evals).
> Treat everything in `charto/` as provisional until then.
>
> **How this relates to the rest of Pivot.** Charto is the *rendering + judgment*
> layer sitting on top of engines Pivot already owns (chat brain, backtester,
> Kite data, detectors, workflow watcher, orders, thematic map, news pipeline).
> It is mostly assembly, not green-field — see §7. It also subsumes and extends
> the earlier "View Markets V2" chart-vision thread; read `CLAUDE.md` §9 for how
> V2 framed belief→expression→deployment, which Charto renders spatially.

---

## 0. The one-sentence spec

**Charto is the analyst standing at your chart — it measures what you believe,
draws only what it can prove, watches so you don't have to, and remembers what
you did.**

Everything below is a consequence of that sentence.

---

## 1. Vision

Most retail traders live inside a chart they mark up by hand — levels, zones,
trendlines, structure — and a set of tools (screeners, option calculators,
scanners, journals) that never talk to each other or to the chart. Charto
collapses that into one calm conversation with a competent second person at the
same screen: you describe a belief or ask a question in plain language (or
Hinglish), and the chart *becomes* the answer — annotated, evidence-graded, and
deployable as a register-not-execute automation.

The wedge is not detection. Auto-detection of the entire manual playbook is a
**commodity** in 2026 (TrendSpider, LuxAlgo, ChartPrime, Autochartist, and
TradingView's own AI Chart Copilot all ship it). What almost nobody ships is the
**judgment layer above detection**: evidence per annotation, honest confidence,
context-gating, move attribution, and chat-native delivery under India's
register-not-execute reality. That layer is what Charto owns.

North star: a non-expert holds a belief, sees it drawn on live candles with
evidence and a causal map, checks it with a rigorous backtest, and arms it as an
automation — all inside one conversation, never once feeling talked down to or
handed a black box.

---

## 2. The constitution (founding principles)

These are identity-level. Violating one is never "a small bug." Every feature,
every code decision, every prompt edit is checked against these.

1. **One brain, one source.** Chat is the only place meaning is decided. The
   chart never computes, never infers, never holds an opinion — it projects the
   scene. The instant the frontend grows local intelligence, there are two
   brains and they drift. (Pivot chat already learned this: local FE intent
   shortcuts that intercepted real backend intents were a recurring bug class.)

2. **Nothing drawn that isn't computed.** Every annotation carries provenance to
   a tool output. The renderer *cannot* draw a level that no detector or user
   produced. This turns never-fabricate from a prompt instruction into a
   structural guarantee — there is no code path for an invented level-by-role.

3. **Model owns meaning; code owns math.** The load-bearing rule, cutting both
   ways: code never interprets language (no regex deciding what "breakout"
   means), and the model never produces numbers (no LLM-computed RSI, no
   LLM-invented level). Every past Pivot failure is one side of this line being
   crossed.

4. **Honest confidence is a feature, not a disclaimer.** Annotations are graded
   by an evidence hierarchy (§5). Attribution says "no clear catalyst" most days
   and means it. The empirical ceiling (~80% of moves are unexplained) is shown,
   not hidden. Charto is the one chart tool that refuses to oversell its own
   drawings.

5. **Cumulative, not conversational-soup.** One versioned scene object per
   conversation that each turn *edits*, not a stream of disposable cards. Truth
   lives in one place (a Redis scene object); the frontend holds no state it
   didn't receive.

6. **Latency has a hard boundary.** Everything interactive — pan, zoom, replay,
   hover, alert firing — is LLM-free and instant. The LLM is paid only when new
   *meaning* enters (a user utterance, a new claim to draw). A trader forgives a
   slow answer; a trader never forgives a slow chart.

7. **Fewer actions, not more surfaces.** Every feature must *delete* something
   the trader currently does — interval-flipping, nightly scanning, alert
   triage, journaling. A feature that adds a dashboard to check is failing.

8. **Register-not-execute is identity-level.** The bracket on the chart stages;
   the human commits in their own broker app. This is simultaneously the SEBI
   moat and the trust posture. Never wire chat/chart to auto-place.

9. **Calm by default.** Progressive disclosure: a clean chart with 3–5 curated
   objects, everything else one question away. Density, dense tables, and
   terminal-vibes are the failure mode — the analyst doesn't scribble on your
   chart uninvited.

10. **Timeframes are context the system carries, not views the user visits.**
    Higher-timeframe structure is projected onto whatever interval is open; the
    trader should rarely need to switch intervals manually.

---

## 3. Where the LLM is needed — and where it is banned

The boundary in one sentence: **the LLM decides what things mean and which to
show; it never decides what a number is or when a rule fires.**

### LLM required (meaning work)
- Interpreting intent, including vague asks ("mark the important levels") by
  picking from the concept lexicon (§6).
- **Scene curation** — of N detected objects, which few matter at this zoom, for
  this conversation, right now. This is genuine judgment; hardcoding it (top-N
  by score) would recreate the rigid-layer disease (§4).
- Composing narrative — explaining what's drawn, answering click-candle
  questions.
- The attribution *sentence* — given gated, timestamped candidate causes,
  writing the one-liner.
- Labeling claims into annotations (its every claim must compile to a scene
  object with provenance).
- Journal review, discipline coaching tone, per-style adaptation.

### LLM banned (math & time work)
- Computing any indicator, level, series, or statistic.
- Detection itself (S/R, market structure, patterns, OI quadrant, divergences).
- Watcher evaluation, alert firing, automation runtime.
- Rendering, pan/zoom, replay, hover hit-rates (all precomputed).
- Order staging mechanics, bracket math, risk-limit enforcement.
- The attribution *gating* — event-window alignment, abnormal-return test, OI
  classification.

---

## 4. Progressing WITHOUT the deterministic layers that hurt Pivot chat

Name the disease precisely — not all determinism was bad. Two patterns caused
Pivot chat's failures:

- **Code guessing meaning *before* the model saw the message** — pre-LLM intent
  classifiers and special-case detectors. Source of clarify-hijack eating
  freshly-typed intents, "breakout" having three code paths with three
  semantics, regex misrouting "IT"/"MCX".
- **Code editing the model's output *after* the fact** — e.g. the
  `_COMPACT_PROSE_TOOLS` 250-token squeeze that truncated every movers table on
  a false premise.

What *never* caused problems: deterministic **computation** (indicators,
backtests, watchers) and **schema validation**. The model-owned-interpretation
A/B proved the direction — the arm with four distrust layers removed won.

**Charto's doctrine, from day one:**

1. **No pre-LLM interception, period.** Charto surfaces get no intent
   classifiers, no phrase detectors, no special-case routers. Every message
   reaches the model; the scene emission is a tool call the *model* makes, not a
   route code selects. This is far easier to hold as a founding constraint than
   to retrofit — Pivot chat's layers accreted because each seemed individually
   cheap.
2. **Constrain at the boundary, by schema — reject, never rewrite.** The
   annotation vocabulary is a typed enum menu. A malformed emission fails
   validation and the model retries; code never silently "fixes" meaning.
   Validators check *well-formedness* (does this trendline have two anchors),
   never *intent* (should this have been a zone).
3. **Fix errors by fixing what the model sees.** When the model draws the wrong
   thing, the reflex is "what was missing or illegible in the tool return," not
   "add a validator." Rich detector outputs (candidates with scores, touch
   counts, ages) give the model real options, which is what makes free-invention
   unnecessary rather than forbidden.
4. **Consistency through guidance + measurement, not lookup tables.** The concept
   lexicon lives in the system prompt as a menu the model reads, not a code-side
   dictionary that bypasses it. Then *measure* agreement rate on a fixed probe
   set every release — consistency is a tracked metric, not a patch stack.
5. **Hard code-gates only on ACTIONS, never on MEANING.** Code says a flat "no"
   in exactly three places: placing an order, exceeding a risk limit, emitting
   an annotation without provenance. Gates on irreversible actions are safety;
   gates on interpretation are the disease.

---

## 5. The evidence hierarchy (how Charto grades its own drawings)

Charto ranks its annotations by credibility and displays confidence
accordingly — it never presents all drawings as equal. Strong → weak, per the
research sweep:

1. **Relative strength / momentum** — strongest academic support
   (Jegadeesh–Titman cross-sectional momentum).
2. **Support/resistance zones** — real published bounce edge with a decaying
   "memory effect"; method-dependent.
3. **Gaps** — best-quantified folklore (fill rates: common ~90%, exhaustion
   ~75%, continuation ~45%, breakaway ~35%), testable per symbol.
4. **VWAP / volume profile** — structurally sound (real traded volume, real
   institutional benchmark).
5. **Multi-timeframe confluence** — codified, sensible, weak formal proof.
6. **Divergences** — modest; high false-positive rate.
7. **Chart patterns** — weak, pattern-dependent, cherry-picked backtests.
8. **Candlestick patterns** — most-studied, most-mixed; the literature says
   **context is what rescues them** (a hammer only matters at a real zone) — see
   feature #28.
9. **ICT/SMC order blocks & liquidity sweeps** — largely folklore; unverifiable
   institutional-intent claims. Draw if asked, grade honestly as speculative.

**Corollary rail — evidence must exclude its own definition** (learned building
Phase 5, 2026-07-25). The first cut of the level track record reported 100% hold
rates almost everywhere, and the math was *correct*: a pivot is a local extremum
by construction, so "price failed to clear it shortly after" is close to
tautological. A statistic that grades a detector against its own definition will
always flatter it. Two exclusions fixed it and generalise to every future
evidence metric:

- **Drop the defining observation.** The earliest pivot *created* the level; it
  did not test one. Only re-tests are graded — which correctly leaves a
  single-touch level with no record at all, rather than a flattering one.
- **Drop the window that manufactured the pattern.** Each re-test is judged only
  after its own ±5-bar pivot window, because price *cannot* clear a local
  extremum inside it.

Hold rates then spread realistically (46–92% on 5m, 64% overall) and immediately
paid for themselves: the **most-touched** level on the chart (27 touches) had the
**worst** record (broke 14 of 26). Popularity is not reliability, so ranking must
follow net evidence (held − broke), never touch count — otherwise the default
draw leads with the least trustworthy line on the screen. Report counts always,
a percentage only at n ≥ 5: counts disclose their own sample size, rates do not.

Corollary honesty rail for **move attribution**: the empirical ceiling is real.
The best academic LLM work (Koijen & Levy, Chicago Booth) explains ~17% of
earnings-day moves vs ~5% for raw surprise — **~80% stays unexplained.** An
attribution engine that says "no clear catalyst" most days and is right when it
speaks beats one with a story every day. The core failure mode is the post-hoc
narrative fallacy (the same move gets opposite stories by direction); the
defense is to require a timestamped discrete event corroborated across
independent streams, and to run an abnormal-return check first so sympathy moves
are never sold as stock-specific.

**Corollary rail — a rate without a control is decoration** (learned building
`evaluate_fib`, 2026-07-26). "The 0.618 turned price 18% of the time" sounds
like a finding and is not one: price turns *somewhere* in every retracement, so
any level in the zone scores above zero. The claim is never that a ratio works,
it is that it works **more than an arbitrary level would** — which is
unanswerable without measuring an arbitrary level the same way. So every
folklore metric ships with a control:

- **Control by construction, not by sampling.** The fib control is the six
  midpoints between adjacent fib ratios — same price range, same test, provably
  not fibonacci, and fixed, so the answer is reproducible. Anything drawn from
  a random seed makes the number un-repeatable and the verdict arguable.
- **Normalise per level, not in aggregate.** Five fib ratios vs six controls
  would hand the control more chances to catch a turn; rating each level as
  turned/reached and comparing rates removes the imbalance.
- **The denominator is `reached`, never `legs`.** A 0.786 price never traded to
  cannot be said to have failed. Counting untouched levels as misses makes deep
  ratios look weak for a reason that has nothing to do with the ratio.
- **State the null out loud.** On RELIANCE daily the answer was fib 9% vs
  control 9% — the ratios do nothing a nearby arbitrary level wouldn't. That is
  the *product working*, not a disappointing result, and the reply is expected
  to say so plainly rather than lead with the flattering per-level numbers.

The same shape applies to every remaining folklore item in the hierarchy above
(patterns, candlesticks, order blocks): measure it, measure a control, report
the gap. Two more are already built, and each needed its own control because
each had its own way of flattering itself:

- **A user-drawn zone** is flattered by its WIDTH — the wider the band, the
  further price must travel to close outside it, so the hold rate climbs with
  size rather than with the band being real. Control: the same width placed at
  twelve fixed positions across the range, overlapping ones skipped. The
  browser-drawn box scored 86% against an 82% control, i.e. nothing.
- **A planned position** is flattered by its RISK:REWARD — a 3:1 setup can
  look terrible at 30% and still be sound. Control: the break-even hit rate the
  ratio itself implies (`1/(1+R)`). Reporting 12% is noise; reporting "12%
  against the 25% it needs" is a finding.

The pattern generalises: find what the metric is structurally guaranteed to
reward, then measure that thing on its own and subtract it.

---

## 6. The vague-intent pipeline (probabilistic-in, deterministic-out)

Vague asks ("track the trend", "buy on a breakout", "buy the dip") are a
**lexicon problem, not an AI-judgment problem** — each term has 2–3 canonical,
industry-agreed quantifications. The pipeline:

1. **Concept lexicon** — a versioned menu (in the prompt) mapping vague term →
   named canonical default + alternatives + one-line rationale. E.g. Breakout →
   *close > prior 20-day high (Donchian-20)* → 52-week high → prior-day high;
   volume ≥ 1.5× on by default.
2. **Interpretation sweep** — backtest the 2–3 canonical readings *before*
   replying; the user picks with a trust verdict attached. This is the step
   nobody else does; daily-bar backtests take CPU-seconds.
3. **Structural validators** — model interprets freely; the engine guarantees
   soundness (auto-shift self-inclusive windows, mandatory exit on every entry,
   mandatory SL intraday). Well-formedness only, never intent.
4. **Delegated monitoring** — "watch it for me" becomes one bounded daily review
   note, never per-tick LLM judgment.

The card/scene is the disambiguation surface: the chosen reading renders in
plain language ("Momentum = 3-month return > 8% and price above the 50-day
average — tap to change") with the backtest verdict attached, so ambiguity
becomes evidence-backed choice, not a hidden guess.

**Where Charto refuses:** per-tick LLM discretion ("you decide each moment") —
refused on four independent grounds: cost (~2M tokens/agent/day at 60s ticks),
latency, unbacktestability (the trust ladder becomes meaningless), and SEBI
black-box exposure. The line: the LLM writes the rules; the rules run
deterministically. Also refused: true outcome delegation ("just make money"),
news-judgment entries (offer the event-blackout gate instead), and
guaranteed/target returns.

---

## 7. Feature inventory

Grouped by theme. Each line is one distinct feature; feasibility notes reflect
the 2026-07-23 audits. Nothing here is built.

### A. Charto core (chart-as-renderer)
1. **Dual-pane chat+chart** — chat is the single source; a right pane renders
   every reply visually on live candles.
2. **Scene model / scene_patch** — one cumulative, versioned scene object per
   conversation that each turn updates, not throwaway cards.
3. **Claims-become-annotations** — every analysis claim compiles to a drawn
   object with provenance; what can't be drawn from tool data can't be said.
4. **Indicator overlays** — backend-computed SMA/RSI/MACD series as
   overlays/sub-panes, never FE-computed.
5. **Detector-backed S/R levels** — real pivot-cluster support/resistance drawn
   with provenance instead of invented levels-by-role.
6. **Staged-order brackets** — entry/TP/SL drawn as draggable bracket lines that
   write back to `/orders/register` (GTT/OCO).
7. **User drawing tools** — trendlines and zones the user draws directly on the
   chart, persisted into the scene.
8. **Drawn-line → automation** — a user trendline becomes a time-varying level
   the 60s watcher evaluates and arms as register-not-execute.
9. **Click-candle-to-ask** — tapping any candle/annotation drops a context chip
   into the composer for a grounded question.
10. **Replay / forward-test playback** — scrub historical bars and watch a
    strategy's fills play out on the chart.

### B. Fundamentals-on-price
11. **Earnings line** — quarterly EPS × median multiple drawn under price.
12. **PE/PB valuation bands** — historical multiple bands (±1σ) marking
    cheap/expensive zones.
13. **Re-rating decomposition** — splits a move into earnings-growth vs
    multiple-expansion.
14. **Earnings-reaction pins** — per-occurrence event study of how this stock
    historically reacted to results, pinned on candles.
15. **Quarterly results sub-pane** — revenue/profit bars aligned under price.

### C. Vague-intent strategy pipeline (see §6)
16. **Concept lexicon** — vague terms → named canonical defaults + alternatives.
17. **Interpretation sweep** — backtest 2–3 readings before replying.
18. **Structural validators** — engine-guaranteed soundness (windows, exits, SL).
19. **Delegated monitoring** — one bounded daily review note, never per-tick.

### D. Price-action automation
20. **Market-structure labeling** — swing highs/lows, HH/HL/LH/LL, BOS/CHoCH.
21. **Zone detection** — supply/demand zones, order blocks, FVGs as zones.
22. **Auto trendlines/channels** — significant lines drawn and ranked.
23. **CPR/Camarilla/classic pivots** — the India-favorite deterministic day
    levels + trend/range-day read.
24. **VWAP + anchored VWAP** — auto-anchored to gaps, swing extremes, events.
25. **Volume profile** — POC/VAH/VAL from real traded volume.
26. **Gap classification** — gaps typed (breakaway/exhaustion/common) with
    historical fill-rate stats.
27. **Chart-pattern detection** — flags, triangles, H&S etc. with confidence
    scores, honestly graded weak.
28. **Context-gated candlestick signals** — patterns only flagged where they
    matter (hammer at a detected zone) — the literature's rescue condition.
29. **Divergence detection** — price-vs-oscillator divergences with
    false-positive honesty.
30. **Relative strength / rotation** — RS lines vs NIFTY/sector + RRG-style
    leader/laggard rotation.
31. **Confluence scoring** — multiple signals stacking at one price graded into a
    single level score with every input inspectable.
32. **Evidence-hierarchy grading** — Charto ranks its own annotations by
    credibility (§5) instead of presenting all as equal.
33. **Evidence-on-hover** — hover any detected setup to see its historical
    hit-rate on THIS symbol. *The unshipped-anywhere wedge; Pivot owns both
    halves (Kite history + trust-verdict backtester).*

### E. Multi-timeframe (timeframes as carried context, §2.10)
34. **HTF projection** — weekly/daily levels, zones, MAs projected onto whatever
    interval is open.
35. **HTF candle ghosting** — the current weekly/daily candle drawn as a
    translucent box over intraday bars.
36. **Timeframe ladder strip** — an always-on W/D/1h/5m ribbon showing each
    interval's bias + one-word reason (subsumes the old standalone "MTF bias").
37. **Auto-interval scenes** — the chart follows the conversation: swing setup on
    daily, zoom to hourly when talk turns to entry, daily ghosted behind.
38. **Against-the-tide warning** — flag when a setup on the open interval
    contradicts the higher-timeframe trend *before* the user acts. *Highest-value
    of this group — converts the classic MTF mistake into a nudge.*
39. **Cross-interval automations** — "weekly uptrend AND daily pullback AND
    hourly reclaim" as one condition tree; the watcher does the interval-checking.

### F. Move attribution ("why is it moving") — India white space
40. **Event pins** — filings, block deals, results timestamped against the move
    window and pinned on candles.
41. **OI 4-quadrant read** — long buildup / short covering / short buildup / long
    unwinding classified from price×OI arithmetic; gives a positive honest
    no-news answer.
42. **Abnormal-return honesty gate** — beta/sector-expected move computed first,
    so sympathy moves are never sold as stock-specific; "no clear catalyst" is a
    first-class answer.

### G. Style packs (the four-station loop, re-timed per style — §8)
43. **Auto pre-market plan** — gap, CPR-width verdict, ORB levels drawn before
    open (intraday).
44. **VWAP reclaim/rejection detection** — the three canonical scalper setups,
    live, with volume confirmation.
45. **Setup-completion alerts** — fire only on confirmed completion, never
    approach — kills alert fatigue (#1 cross-style complaint).
46. **Thesis-invalidation alerts** — positional holdings flagged when the stated
    reason for owning breaks.
47. **Buy-zone alerts** — investor notification when price enters a
    valuation-derived accumulation zone.
48. **Theta-seller pack** — expected-move range, OI walls, breach alerts,
    auto-profit-exit signals.
49. **Daily-loss circuit breaker** — hard risk limits that warn *before* the rule
    break. *Attacks the 91%-lose problem head-on.*
50. **Discipline score** — rules-followed ÷ rules-total, tracked over time.
51. **Auto-journal + AI review** — every trade auto-logged and pattern-reviewed
    (the Tradezella-for-India gap).

### H. Beyond-chart (longer horizon)
52. **Management-credibility ledger** — concall guidance extracted and scored
    against actual outcomes over years.
53. **Thesis objects** — beliefs stored with confirm/invalidate conditions,
    tracked like positions.
54. **Self-audit claim ledger** — Charto scores its own past analysis claims
    against what actually happened.
55. **Portfolio X-ray with scenario sliders** — drag a macro scenario, watch
    modeled portfolio impact.
56. **Market replay trainer** — practice mode replaying historical sessions to
    train decisions risk-free.
57. **Watchlist radar** — one consolidated, prioritized daily digest across the
    watchlist, not a stream of pings.

---

## 8. The trader's philosophy of use (design backward from this)

The mental model to build for: **"my analyst is standing at my chart."** Not a
terminal, not a scanner, not a bot — a competent second person at the same
screen, who you talk to, who points at things, whose every claim you can tap to
interrogate, who remembers everything, and who never touches your money.

That resolves into a **daily loop of four stations**; every feature maps to
exactly one:

- **Plan** (before open / before the week): pre-market plan, EOD swing scan,
  valuation buy-zones. Charto proposes the day drawn on candles; the trader edits
  the plan, not a form.
- **Watch — which means *don't* watch:** the watcher watches. Completion-only
  alerts, against-the-tide warnings, thesis-invalidation flags. The trader's
  attention is the scarcest resource; the goal is that a swing trader opens the
  app *only when something completed.*
- **Act:** one tap from drawn bracket to registered order; the human commits in
  their broker app. Friction stays at exactly one confirmation — no nagging, no
  auto-exec.
- **Review:** auto-journal, discipline score, self-audit ledger. Charto holds the
  mirror — including to itself.

Design consequences:
- **The chart is calm by default** (constitution §9).
- **Trust is built by being checkable, not by being right.** Tap any drawing →
  why it's there, what data produced it, how it's performed on this symbol. An
  analyst who shows their work survives being wrong; a black box doesn't survive
  being right.
- **The trader brings belief and judgment; Charto brings measurement, memory,
  and discipline.** This is why refusing per-tick discretion doesn't feel
  diminishing — "you decide, I'll measure and remember" is a *stronger* offer
  than "I'll trade for you," especially where 91% of the self-directed lose.
- **Different styles are different loops, same philosophy.** The scalper's loop
  runs in minutes (plan at 9:10, circuit breaker by 10:30); the investor's in
  quarters (results day, buy-zone touch). Style packs are the four stations
  re-timed, not separate products.

---

## 9. Feasibility & the LLM/backend split (2026-07-23 audits)

**Verdict: Charto is assembly-priced, not infrastructure-priced.** The layout,
card pipeline, data feeds, order rail, and even the level-detectors already
exist in the Pivot repo. The genuinely green-field work is confined to three
things.

**Already exists (reuse):**
- Dual-pane layout is shipped — `AgentPanel` is a resizable (360–960px) fixed
  right pane that publishes `--side-panel-width`; the chat column already
  compresses; there's an exclusivity bus. A chart pane is a 4th consumer.
- `lightweight-charts` v5 in production with candles, volume sub-scale, price
  lines, buy/sell markers, crosshair readouts, pan-clamp, zoom. Feed =
  `GET /markets/ohlc/{symbol}` (Kite-primary, cached, source-tagged).
- Level detectors exist but are DORMANT (library code + tests, zero chat
  wiring): `support_resistance_levels()`, `detect_candlestick_patterns()`,
  regime-shift comparison. Wiring them = provenance-backed levels.
- Session store has a clean slot for the scene (`ConversationStore` Redis
  dataclasses); artifact ledger is the provenance hook.
- Orders are ahead of the vision: `/orders/register` already takes
  `gtt_stoploss_pct`/`gtt_target_pct` → GTT/OCO brackets. Register-not-execute
  intact.
- Earnings line is buildable — `get_fundamental_history()` returns PIT-correct
  quarterly EPS.

**Green-field (the real build):**
1. **Scene accumulator + `scene_patch` SSE event** (Low-Med) — structured
   payloads today arrive only at `done`; a mid-stream scene patch is a new SSE
   event type + a scene reducer + a Redis `chat:scene:{id}` key.
2. **Interactive drawing layer** (Med — downgraded from Med-High after the
   library review, §9a) — lightweight-charts v5's
   `ISeriesPrimitive`/`attachPrimitive`/`subscribeClick` APIs are untouched in
   the repo but fully present in the installed 5.2.0, and there is now an MIT
   open-source starting point (see §9a). Still the hardest FE item, but it
   starts from working drag+hit-test+serialize code, not from zero.
3. **DSL time leaf** (Med) — no numeric time/bar-index node exists; a drawn
   trendline (`value = m·t + b`) needs a new `bars_since(anchor)` leaf the
   existing MathNode can consume, plus an evaluator case. The 60s watcher +
   crosses-state persistence carries over.

Also net-new: drawn-object ingestion back into chat (extend the composer's
attachment/quote channel with a `chart_point` kind); rolling PE/PB bands
(assemble from price×EPS — both series solid, no ready-made history);
earnings-reaction event study (green-field but bounded).

**LLM/backend split:** render loop = zero LLM; runtime watcher = zero LLM;
numbers = zero LLM. The LLM's role is one schema-enforced scene-patch emission
per turn (curate + label, never compute). Marginal cost ≈ one extra structured
block per chart-bearing turn, not a new hop.

**Hard constraints to design around:**
- **One card per turn** — the response assembler hoists the first nested hint and
  `break`s; there is no card array. The scene being *one cumulative object*
  sidesteps this, but never design a patch that needs two hints in one turn.
- **`chat_service.py:1317` is NOT a live-exec toggle** — it's F&O tool-scoping;
  register-not-execute is enforced in `orders.py _persist_leg` (paper vs
  connector). Don't mistake it for an auto-exec rail.
- **Keep it numeric-detector-first, not screenshot-vision.** 2025-26 research
  shows vision models reading chart images are competitive-not-superior, and some
  CNNs "learned base rates, not charts." Charto detects from data, not pixels.

---

## 9a. Charting library decision (2026-07-23 review)

**Decision: keep `lightweight-charts` (installed 5.2.0, Apache-2.0). Do not
switch. The choice reinforces the constitution rather than compromising it.**

**The installed 5.2.0 already ships the full drawing *substrate*** (verified from
its own `typings.d.ts`): the primitive API (`ISeriesPrimitive`/`IPanePrimitive`
with custom canvas `renderer()`, `hitTest(x,y) → PrimitiveHoveredItem`, axis
views, `autoscaleInfo`, `attached` lifecycle); both-way coordinate conversion
(`priceToCoordinate`/`coordinateToPrice`, `timeToCoordinate`/`coordinateToTime`,
`logicalToCoordinate`/`coordinateToLogical`); events (`subscribeClick`,
`subscribeDblClick`, `subscribeCrosshairMove`); **native multi-pane**
(`addPane`/`panes`/`moveToPane`/`paneIndex`) for RSI/MACD subpanes; and
`addCustomSeries`. What it lacks: a built-in drawing *toolbar* and built-in
indicators.

**Ecosystem fills the toolbar gap (MIT).**
`deepentropy/lightweight-charts-drawing` — MIT, v5-targeted npm package, **68
tools** (trend lines, 11 Fib variants, Gann, channels, pitchforks, shapes,
annotations) with a `DrawingManager` doing drag-edit, events, selection, and
**JSON export/import**. Young (v0.1.1 Feb 2026, ~8 commits, single-maintainer),
so **vendor/fork as a reference & starting point, don't blind-depend**. Our
drawings must be scene-integrated primitives *with provenance* that serialize
into `scene_patch` (its JSON can't be our source of truth — a drawn trendline
has to become a scene object the backend watcher can arm). Companions:
`lightweight-charts-indicators` (446 indicators — use as a formula *reference*
only), `difurious/lightweight-charts-line-tools`, TradingView official
`plugin-examples`.

**Why not the batteries-included alternatives:**
- **TradingView Advanced Charts** (full toolbar + 100+ indicators native) —
  disqualified 3×: (1) its free license is companies/public-projects only,
  attribution must stay visible, **not for private/paywalled/authenticated use**
  — Pivot is a logged-in product, likely a hard legal blocker; (2) it is a
  *second brain* (owns indicators + drawing state + chart logic internally),
  violating "one brain, one source; chart is a stateless renderer"; (3) heavy
  datafeed-adapter integration, loses the render-from-your-own-data model.
- **KLineChart** (MIT, zero-dep, built-in indicators + overlays) — capable, but
  adopting it discards the 3 existing lightweight-charts components AND its
  built-in indicators are the *two-brains* problem (chart computing its own RSI
  violates "code owns math, backend is the one source").
- **Highcharts Stock** — commercial/paid, heavier; no reason to pay when the
  owned substrate suffices.

**The subtle win:** lightweight-charts having *no built-in indicators* is a
FEATURE for Charto — it structurally enforces "every series comes from the
backend registry" (one-source honesty). Batteries-included libs constantly tempt
FE-computed indicators, the exact drift we design out. Net effect on the plan:
drawing-layer risk drops (green-field → adapt-an-MIT-impl); RSI/MACD subpanes
need nothing new; indicators + levels stay backend-computed, drawn via primitives
from scene objects.

---

## 10. Roadmap (high velocity)

P0 does not change regardless of which "powers" get prioritized after it.

**P0 — prototype, ~1 week (demoable):**
1. Chart pane on the `AgentPanel` pattern + `chat:scene:{id}` Redis slot +
   `scene_patch` SSE event.
2. Scene v1 vocabulary: symbol/range, indicator overlays, horizontal levels,
   candle markers, static TP/SL bracket lines.
3. Wire `support_resistance_levels` as a tool so "show me support on RELIANCE"
   draws real, provenance-backed lines.
4. Click-candle-to-ask via `subscribeClick` → `chart_point` attachment.

That prototype demos the whole thesis: ask in chat, watch it drawn on live
candles, click the chart to ask back.

**Post-P0 priority order (white-space × feasibility):**
1. **Attribution v1** (features 40–42) — filings poller + block-deal EOD file +
   OI quadrant + abnormal-return gate → event pins. Top signals are FREE
   NSE/BSE data; the macro-verifier fail-safe pattern already exists.
2. **Evidence-on-hover** (#33) — per-annotation historical hit-rate via the
   existing backtester. Nobody ships it; Pivot owns both halves.
3. **Confluence-graded levels + context-gated candles** (#28, #31, #32) —
   detector wiring + a scoring pass.
4. **MTF carried-context** (#34–38) — cheap rendering wins (34, 35, 36) first;
   38 (against-the-tide) is high-value assembly; 39 needs the DSL per-node
   interval work.
5. **Style packs** (#43–48) — recombination of the above per segment.
6. **Discipline + journal layer** (#49–51) — the biggest retention bet; the
   anti-91% product.

**v1 (~3–4 weeks cumulative):** interactive trendline drawing (budget a full
week for the `ISeriesPrimitive` build), the `bars_since` DSL leaf +
trendline→watcher→register path, draggable brackets writing to
`/orders/register`, earnings line, replay-lite. The vague-intent lexicon +
interpretation sweep slot in alongside without touching chart work.

**Deliberately post-v1:** PE/PB bands, earnings-reaction pins, thesis objects,
credibility ledger, X-ray sliders, replay trainer — each additive on the scene
contract once it exists.

---

## 11. Market context (why this wins, 2026)

- **Detection is commoditized.** TrendSpider/LuxAlgo/ChartPrime/Autochartist and
  TradingView's own AI Chart Copilot (public beta Apr 2026) auto-detect the
  whole playbook. Copilot deliberately ships NO strategy-gen and NO orders — the
  biggest platform risk, but it leaves Charto's deploy layer open.
- **India move-attribution is unclaimed.** Benzinga "Why Is It Moving" defines
  the US category (human-in-the-loop, licensed B2B into ChartIQ); no India
  product ships automated causal attribution tied to a chart. The top signals
  (filings, block deals, delivery %, OI quadrant, index rebalances) are all free
  NSE/BSE data.
- **The market is shifting under us.** Post-SEBI-Oct-2024 curbs, retail options
  contracts fell ~83%; F&O participants shrank ~20%; the mass segment is now
  long-term/SIP + swing/positional (SIP >₹31,000 cr/month; MTF leveraged-delivery
  book grew ~5× to ₹1.16 lakh cr). The high-intensity core is index-options
  intraday + theta sellers. 91% of F&O traders lost money in FY25 — the
  discipline layer is the product's moral core, not a nice-to-have.
- **Chat-primary + India + register-not-execute is the uncopied combination.**
  Competitors ship detection as separate tools; none fuse belief-in-chat →
  drawn-on-live-candles → register-not-execute automation into one conversational
  surface under India's regulatory reality.

---

## 12. Provenance of this document

Synthesized 2026-07-23 from: the chart-vision ideation arc (2026-07-19), two
codebase feasibility audits (frontend + backend, 2026-07-23), and a three-agent
web-research sweep (price-action automation, trading-style taxonomy, move
attribution). Cross-references in Pivot's memory:
`project_charto_feasibility_2026_07_23`,
`project_charto_powers_research_2026_07_23`,
`project_chart_vision_and_competitive_landscape_2026_07_19`,
`project_strategy_automation_gap_analysis_2026_07_18`,
`project_breakout_path_diagnostic_2026_07_19`.

**When code claims here drift from the Pivot repo, the code wins** — verify
file/flag/tool names before relying on them. This is a design sandbox; treat it
as provisional until a feature graduates out of `charto/`.
