# Model benchmark — overview & quality judgment
### gpt-5.4-nano vs gpt-5.4-mini vs gpt-5.4, at low / medium / high reasoning

_2026-06-21 · 10 prompts × 3 models × 3 reasoning levels = 90 live Azure Foundry calls, run in
parallel over the streaming Responses API. Full answers + per-cell metrics live in
`MODEL_BENCHMARK_2026-06-21.md` (509 KB); raw data in `raw_results.json`._

---

## TL;DR

- **All 90 calls succeeded.** Every answer was on-topic, well-structured, and carried the
  "not financial advice" line. As you said: **the core of every answer is the same across models** —
  the same strategy, the same payoff math, the same recommendation. The differences are in
  **rigor, polish, factual precision, latency, and cost**, not in being right vs wrong.
- **Quality ranking: `gpt-5.4` (full) > `gpt-5.4-mini` ≈ `gpt-5.4-nano`.** Full is consistently the
  most rigorous and data-rich, and it got real-world facts (F&O lot sizes) right more often. nano and
  mini are very close to each other and "good enough" on most prompts; nano is the most
  cost/latency-efficient, mini is the most erratic on cost (see below).
- **Higher reasoning ≠ better answers here. It mostly buys *conciseness* at a large latency/cost
  cost.** For these well-scoped tasks, low→high reasoning made answers **shorter and slightly more
  polished**, but the substance and correctness barely moved. The price of "high" is steep: average
  **TTFT 2.6s → 33s** and **cost ~2.8×**.
- **The one recurring correctness hazard is F&O lot size** (NIFTY/BANKNIFTY/TCS). Models disagree
  (e.g. NIFTY 25 vs 50; TCS 1 vs 150 vs 175). All correctly flag *premiums* as illustrative, but lot
  size is the number that silently varies — verify it before trusting any rupee P&L.

---

## 1. Latency · tokens · cost (the hard numbers)

**Latency is TTFT** — time from request sent → first *visible* token (when the answer starts
streaming), exactly as requested. `total` = to completion.

### By model (avg over all 10 prompts × 3 levels)

| Model | avg TTFT | avg total | avg output tok | reasoning % of output | avg cost/call | total (30 calls) |
|---|--:|--:|--:|--:|--:|--:|
| `gpt-5.4-nano` | **7.9 s** | 17.6 s | 2,413 | 41 % | **$0.00097** | $0.029 |
| `gpt-5.4-mini` | 15.5 s | 22.6 s | 3,979 | 70 % | $0.00800 | $0.240 |
| `gpt-5.4` | 25.8 s | 48.2 s | 4,005 | 53 % | $0.04028 | $1.208 |

### By reasoning level (avg over all models)

| Level | avg TTFT | avg total | avg output tok | reasoning % | avg cost/call |
|---|--:|--:|--:|--:|--:|
| low | **2.6 s** | 17.9 s | 1,904 | 13 % | $0.0091 |
| medium | 13.3 s | 25.1 s | 3,048 | 52 % | $0.0148 |
| high | 33.2 s | 45.4 s | 5,445 | 74 % | $0.0253 |

**Reading these:**
- **TTFT explodes with reasoning** because the model thinks *before* it emits the first visible
  token. Going from low→high multiplies time-to-first-token by **~13×**. For a chat product where
  the user is watching a cursor, this is the single biggest UX lever.
- **`gpt-5.4-mini` at high reasoning is a trap**: it averages **6,971 output tokens with 5,915 (85%)
  spent on reasoning** and a 33 s TTFT — i.e. it over-thinks more than the full model does, for a
  worse answer. mini's sweet spot is **low**.
- **Cost** (nano/full rates are estimates — see caveats): full is ~5× mini and ~40× nano per call.
  Over a 90-cell sweep, full alone was **$1.21 vs nano's $0.03**.

> ⚠️ **Pricing caveat.** The repo only prices `gpt-5.4-mini` ($0.25/$2.00 per 1M in/out). nano
> ($0.05/$0.40) and full ($1.25/$10.00) are **estimates** on the GPT-5 family ratio. Cost scales
> linearly with the real rates, so the *relative* picture holds even if absolute dollars shift.

---

## 2. Quality judgment by model

Scored on Pivot's two bars — **execution correctness** (right strategy, right math, right facts) and
**output quality** (structured, data-rich, defended, honest). Grounded in full deep-reads of the
bull-call-spread, instrument-comparison, and iron-condor prompts plus structural signals
(length, tables, headers, numbers, disclaimers) across all 90 answers.

### `gpt-5.4` (full) — **8.8 / 10 — best, and clearly so**
- **Most rigorous and most correct on facts.** On the TCS comparison it used the *real* option lot
  size (175), used **delta-adjusted leverage** (the conceptually correct definition), and added an
  expiry-P&L snapshot table. On the bull call spread it added a debit/width ratio sanity check (42.5%
  of width = "reasonable") and a cheaper-variant alternative.
- **Richest output**: most tables, most concrete numbers, clearest "when I'd pick each instead"
  framing. This is the answer a power user wants.
- **Weaknesses:** slowest (26 s avg TTFT, up to **96 s** on one high cell) and most expensive. And
  it **dropped the illustrative-data caveat on the HDFC vs ICICI comparison** (all 3 levels) —
  presenting bank fundamentals as if real, a small honesty gap nano/mini didn't have.

### `gpt-5.4-mini` — **7.6 / 10 — clean and correct, but erratic**
- Answers are **clean, correct, and well-organized** (bull call spread and TCS comparison were both
  right, with sensible capital-efficiency leverage framing and good caveats).
- **Two real downsides:** (1) it **over-reasons** at medium/high — on the TCS comparison at medium it
  burned **5,453 of 6,216 output tokens (88%) on hidden reasoning** for an answer no better than its
  own low-effort one; (2) **inconsistent depth** — the MARUTI swing analysis at medium collapsed to
  442 words / 5 numbers, notably thinner than nano's. Best operated at **low** reasoning.

### `gpt-5.4-nano` — **7.3 / 10 — the efficient workhorse**
- **Surprisingly competitive** on structured tasks: solid tables, correct payoff math, consistently
  good caveats, and the **lowest, most predictable latency and cost**. On the bank comparison it was
  actually *more* honest (kept the illustrative caveat) and data-rich than mini.
- **Where it slips:** factual granularity and definitions. On the TCS comparison it assumed an option
  lot size of **1**, scaled to "833 calls / 1,666 spreads" (unrealistic), and its **"effective
  leverage" column was muddled** (0.10x for shares). It's the model most likely to get a *definition*
  or *instrument detail* subtly wrong while still looking polished.

**Bottom line:** for the same prompt, all three give the same core answer; **full is the one to trust
on numbers and nuance**, nano is the one to reach for when latency/cost matter, and mini is a
middle option that you should pin to **low** reasoning.

---

## 3. The reasoning-level effect (judged on identical prompts)

This is the most interesting finding, and it confirms your intuition. Holding the model and prompt
fixed and only changing low → medium → high:

- **The core answer does not change.** Same strikes, same net credit/debit, same breakevens, same
  recommendation. On the full-model iron condor, all three levels produced the same correct
  structure and payoff math.
- **Higher reasoning makes the *final* answer shorter, not longer.** Every model wrote *fewer*
  visible words at high than at low (full: 1,330 → 1,042 avg words; mini: 872 → 653). The thinking
  happens in hidden reasoning tokens; the visible answer gets tighter and better-triaged. E.g. the
  full-model iron condor at **low** padded in extra "things to check" sections (~1,231 words); at
  **high** it was leaner (~905 words) but added a sharper per-lot rupee table and the crisp framing
  *"short vol wins when actual move < implied move."*
- **The marginal quality gain is small; the marginal cost is large.** Going low→high bought maybe
  +0.5/10 of polish on these tasks, while multiplying TTFT ~13× and cost ~2.8×.
- **Where reasoning *would* matter** (not well-exercised by these single-turn prompts): multi-step
  arithmetic with interacting constraints, ambiguous prompts needing disambiguation, and
  catching its own factual errors. For straightforward "build/compare/analyze" asks, it's overkill.

**Practical guidance:** **low or medium is the right default.** Reserve **high** for genuinely hard,
multi-constraint reasoning — not for formatting-heavy strategy/comparison answers where it just adds
latency.

---

## 4. Per-prompt quality (model comparison at a fixed *medium* level)

Scores are correctness + output-quality, 1–10. ✔ = deep-read in full; others scored from structural
signals + sampling.

| Prompt | nano | mini | full | Notes |
|---|:--:|:--:|:--:|---|
| basket_invvol_it | 7.0 | 7.5 | 8.5 | full shows full inverse-vol math + 5-stock table; all correctly flag illustrative prices |
| basket_thematic_defence | 6.5 | 7.0 | 8.0 | full justifies all 6 names + risks; nano/mini sometimes skip the illustrative caveat |
| fno_bull_call_spread ✔ | 7.5 | 8.0 | 9.0 | all math correct; full adds debit/width check + variant; **lot size differs (50/50/25)** |
| fno_covered_call | 7.0 | 7.5 | 8.5 | full most data-rich (yield, assignment, roll rule); all sound |
| fno_iron_condor ✔ | 7.5 | 8.0 | 9.0 | all correct (credit, BE, max loss); full best on IV-crush + skew nuance |
| compare_hdfc_icici | 7.5 | 7.0 | 8.0 | full richest **but omits the illustrative caveat**; nano honest + data-rich |
| compare_instruments_tcs ✔ | 6.5 | 8.0 | 9.0 | full uses real lot 175 + delta-adj leverage; **nano leverage muddled, lot=1 error** |
| exec_rsi_automation | 7.5 | 8.0 | 8.5 | all give clean trigger/order spec + edge cases; full most structured |
| exec_rebalance_plan | 7.5 | 8.0 | 9.0 | full extremely thorough (thresholds, sequencing, tax, slippage) |
| analysis_maruti_swing | 7.5 | 6.5 | 8.5 | full best; **mini thin at medium (442 words)**; nano solid |

**Pattern:** full wins every prompt; nano and mini trade places depending on whether the task rewards
structure (nano holds up) or punishes factual sloppiness (mini edges ahead). No prompt produced a
*wrong* core answer from any model.

---

## 5. Recommendation for Pivot

| Use case | Model + reasoning | Why |
|---|---|---|
| **Default chat turn** (build/compare/analyze) | **`gpt-5.4-mini` low** or **`gpt-5.4-nano` medium** | ~2–6 s TTFT, correct, well-structured, cheap |
| **High-stakes / numbers-must-be-right** (F&O P&L, leverage, multi-leg) | **`gpt-5.4` low or medium** | most factually precise; low keeps TTFT ~3 s |
| **Latency-critical / high-volume** | **`gpt-5.4-nano` low** | fastest + cheapest; verify any instrument-level facts |
| **Avoid** | `gpt-5.4-mini` high | over-thinks (85% reasoning), 33 s TTFT, no quality payoff |

Two product fixes this surfaced regardless of model: (1) **inject real F&O lot sizes** (and spot)
from Kite so the one recurring factual hazard disappears, and (2) **enforce the illustrative-data
caveat** when live data is absent (the full model skipped it on the bank comparison).

---

## 6. Caveats

- **No tools / no live data.** Models used illustrative numbers (correctly disclaimed in ~88% of
  cells). This measures *model behaviour* — reasoning, structure, writing, factual instincts — **not
  data accuracy**. With Pivot's real tool-calling + Kite data, grounded numbers replace the
  illustrative ones.
- **nano/full pricing is estimated** (repo prices only mini). Dollar figures scale linearly with the
  real Azure rates; relative comparisons are unaffected.
- **Single run, single prompt phrasing.** These models vary run-to-run; one sample per cell. The
  patterns above are consistent across 90 cells and 15 full deep-reads, but exact tokens/latency will
  wobble on a re-run.
- **Reasoning levels tested: low / medium / high.** `none` and `xhigh` exist on these deployments and
  weren't swept.
