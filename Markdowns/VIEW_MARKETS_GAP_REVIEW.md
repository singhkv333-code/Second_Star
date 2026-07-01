# View Markets — Gap & Problem Review (2026-07-01)

> Review-only audit. Compares the **spec** (`Markdowns/VIEW_MARKETS_*`, `Version2.md`)
> against what is **actually built** in the Views tab + `/view-pack`, plus a
> substance audit of the strategies and a spec for a Kalshi-style payoff calculator.
> Produced by four fresh-memory reviewers (spec-intent / built-system / strategy-substance /
> PM-calculator research). No code was changed.

---

## TL;DR verdict

The built surface is an honest, rigorous **"belief → basket → return chart"** — but it is
**not the belief OS the spec describes.** Three things went wrong:

1. **The explanatory layer was dropped, not simplified.** Transmission map, "what's
   priced in / surprise," lifecycle, and the two confidence dials are all *built as
   components but wired to nothing*, and their backing data is empty across every view.
   What remains is the part a layman needs *least* (a metrics table) and drops the part
   they need *most* (why the belief moves markets).
2. **Presentation went the wrong way on simplicity.** The one dense grid the spec
   explicitly forbids — a Strategy/Type/Risk/Max-drop/Avg-profit **table** — is exactly
   what we kept. The spec wants cards, timelines, dials, small charts, progressive
   disclosure.
3. **The strategies are not genuinely differentiated.** Across all three tiers of a view
   the long book is **byte-identical and equal-weighted**; only a hedge overlay changes.
   Options carry no strikes/greeks/payoff. A *real* differentiated engine exists in the
   codebase but is **dormant** — a rewire script flattened everything to `equal_weight`.

Plus two integrity red flags (§6): leaked **monsoon** copy shown verbatim on AI/gold/EV
views, and live basket holdings that **contradict the view's own thesis**.

---

## 1. Structural gap — what a View *should* be vs what it *is*

**Spec view (9 sections + spine)** — `Version2.md` "Suggested View Layout":

| # | Spec section | Purpose |
|---|---|---|
| 1 | Header (title · type · horizon) | orient the belief + when it resolves |
| 2 | Thesis | the belief as a plain-English question you take a side on |
| 3 | **Market Expectations / Surprise** | Expected vs Your-View vs Difference — *"markets react to surprise, not outcome"* |
| 4 | **Transmission Map** | the cause→effect DAG — *"explain causal relationships, not just assets"* |
| 5 | Expressions — Conservative / Balanced / Aggressive | the deployable strategies (the "most important component") |
| 6 | **Two confidence dials** | Outcome confidence vs Expression confidence — *"must remain separate"* |
| 7 | Timing modes | Pre-position / Confirmation / Hybrid (esp. Event views) |
| 8 | Deployment | backtest → deploy → automate (register-not-execute) |
| 9 | Related views | siblings / follow-ups |
| spine | Lifecycle Open→Developing→Consensus→Resolved→Archived + Alignment Score | a living, followable object |

**Built view (actual render):**

- **Card:** meta row → question title → 1-line summary → sign-tinted own-return sparkline
  → **Yes/No stance buttons** → footer (trust word + "Positive in N of M" + follow heart).
- **Detail:** back link → H1 → **"Your call" Yes/No stance block** → own-return **line chart
  + tier switcher (1·2·3) + Compare** → "What this is" description + "If you're wrong" caveat
  → **Strategies table** → "How this strategy behaves" (own-metrics grid) → Similar views
  (renders nothing — always empty).

**Built as components but ORPHANED (imported nowhere) + backing data empty in all views:**

- `ViewTransmissionMap.tsx` — the causal map (**§4 of the spec, the #1 differentiator**). Dead. `transmission: []` everywhere.
- `ExpectationsSurprise.tsx` — "what's priced in / surprise" (**§3, the single most differentiated analytical idea**). Dead. `expectations: []` everywhere.
- `ViewLifecycle.tsx` — the Open→Developing→… timeline. Dead.
- Two confidence dials — `outcome_confidence` / `expression_confidence` are `{score:null, letter:null}` on every view; only a per-strategy `historical_alignment` bar renders. The spec's "never collapse the two dimensions" is moot because *neither* renders.
- `RiskReturnPanel` + `PayoffDiagram` + `ReturnDistribution` — the options payoff panel. Dead. `monte_carlo: null` on every expression.

**Net:** we shipped sections 1, 2, 5, 8 (partially) and dropped 3, 4, 6, 7 and the
lifecycle spine. The belief-OS layer — *why* the belief moves markets, *what's already
priced in*, *how confident on two axes*, *how it evolves* — is gone. What's left is
closer to a themed screener with a return chart than a "belief operating system."

---

## 2. Why it isn't "simpler for a layman" — simplified by deletion, not translation

The spec's simplicity target is a **non-expert who has an opinion but no strategy
vocabulary**: *"People have opinions. People do not have strategies. Nobody wakes up
thinking 'Bull Call Spread.'"* The interface should feel like **"What do you believe?"**
— **visual, guided, calm, progressive disclosure.**

The built version got simpler by **removing the explanatory layer** (transmission,
surprise, confidence) — which is precisely the layman-friendly, story-telling half — while
**keeping a numbers table** (the expert half). That's backwards. A layman doesn't need a
5-column drawdown grid; they need the one-sentence causal story ("AI adoption → firms cut
support/IT headcount → these names benefit, these get squeezed") and one clear
"if I put in ₹X, here's the realistic range."

**A layman-simpler restructure (concept):**
belief-as-question → **one causal sentence (mini-transmission)** → **Yes/No stance** →
**"what's priced in" one-liner** → **one recommended expression with the ₹-amount outcome**
(§4) → progressive-disclosure the two other tiers + evidence underneath. Cards, not a grid.

---

## 3. Presentation problems (concrete)

- **The Strategies *table* is the exact "dense grid / terminal vibe" the spec forbids.**
  Columns Strategy | Type | Risk | Max drop | Avg profit | Details. Spec: *"Avoid dense
  tables, numerical grids"; "Prefer cards."* Should be three stacked tier **cards**.
- **Jargon on the surface.** Chart x-axis "Days in market →"; caption "The average single
  occurrence, across N past occurrences … CAAR"; a trust badge with no plain gloss. Fine
  for a quant, opaque for the target user.
- **"How this strategy behaves" is a misnomer + half-empty.** Named `BenchmarkComparison`
  but shows no benchmark; its Monte-Carlo card needs `monte_carlo.terminal_pct` (null
  everywhere → never renders); for option tiers the holdings heatmap is omitted too — so
  the section can collapse to a single card.
- **No weights, no horizon on the strategy rows.** Weights live only inside the donut;
  horizon is a single shared caption. The spec wants capital-intensity + horizon as
  first-class per-expression disclosures.
- **Similar views always renders nothing** (empty array).

---

## 4. The Kalshi/Polymarket payoff calculator — what to build, and we already have the data

**How theirs works (the mechanic to steal):** one input (amount, with quick-add chips
`+$1/+$5/+$10/+$100`), and a headline number that recomputes instantly — Polymarket "To
win $7.58," Kalshi "$1.00 per contract, profit if right." It's delightful because it's
*one input → one instantly-moving outcome.* But it's honest for *them* only because a
binary contract has a genuinely fixed $1 payout and a market-implied probability.

**Why we can't copy the single number:** we invest real money in baskets/options/pairs —
the outcome is a **distribution across historical episodes**, not a fixed $1. Showing one
"To win ₹X" or one probability would fabricate the two things a binary market legitimately
owns. Our differentiator *is* the honest range.

**Spec for Pivot's calculator (per tier — Conservative/Balanced/Aggressive differ):**

- **Input:** one ₹ field + India-sized quick chips `+₹10k · +₹25k · +₹50k · +₹1L` + a
  slider bounded [min ticket → available capital] + the tier selector.
- **Numbers (all rescale live with the ₹ amount):** Capital deployed · **Max you can lose
  (₹)** · **Typical outcome** (median episode, ₹ and %) · **Range worst→best (₹)** ·
  p25–p75 band · **Positive in M of N episodes** · Horizon. Never a lone probability;
  worst-case gets equal weight to best-case.
- **Chart:** for **basket/pair tiers** → a **per-episode outcome dot plot** (each real past
  occurrence = one dot, invested ₹ drawn as a baseline, median marked) with a light
  distribution band; typing ₹X **linearly rescales the ₹ y-axis and every number in real
  time** (x-axis fixed). For the **option tier** → a **payoff curve scaled to ₹X** (the one
  place a Kalshi-style payoff diagram is genuinely accurate — bounded max profit/loss).
- **Persistent honesty caption:** *"Prediction markets show one fixed payout. This shows the
  full range of what actually happened across N comparable past episodes — no single
  guaranteed number."*

**Key enabler:** we already have the data. Every expression carries `episodes[]`
(per-occurrence `return_pct`, `positive`) and a ~127-point `equity_curve`. The calculator
is mostly **(existing episode returns) × (amount)** — a rescale, not new modeling.
For the option tier we'd need the real payoff (see §5 — currently stubbed).

---

## 5. Are the strategies genuinely different? — No. Same basket, different overlay.

**The damning finding.** For a given view, all three tiers share a **byte-identical,
equal-weighted long book**; only the hedge overlay changes.

IT view (live `precomputed_views.json`):

| Tier | Kind | Long members (identical, 20% each) | Short leg |
|---|---|---|---|
| Conservative | basket | REC, Adani Power, JP Power, RVNL, Engineers India | none |
| Balanced | pair | *same 5* | IT factor (short) |
| Aggressive | hedge | *same 5* | Nifty (short) |

Monsoon view: all tiers = Britannia/MRF/Marico/Apollo Tyre/Godrej CP/HUL, equal 16.7%.
`ai_jobs` demo pack: Conservative **and** Balanced both = TCS/Infosys/HCLTech/Persistent/
Coforge/Wipro/TechMahindra at 14.3% each (Balanced just adds a Nifty short).

**Why:** `scripts/strategy_research/v3/rewire_v3.py:301-303` hardcodes
`structure = {"scheme": "equal_weight", ...}`, and `precompute.py` ignores config weights
entirely (`{m: 1.0 for m in present}`, `ew_weight = 100/n`). So the tier "knobs" the spec
describes (risk-parity/mcap/factor weighting, single-name caps 0.10/0.15/0.20, z-bands)
**never reach the served data.** Tiers differ in *wrapper* (long-only / factor-neutral /
market-neutral), not in instruments, holdings, weights, leverage, or capital intensity.

**Options are stubbed.** `plain_copy.py:794-796` hardcodes a `bull_call_spread` regardless
of direction (even for the IT view whose research is a *bear put* spread) — no strike
numbers, no greeks, no premium, no POP, no payoff. The curve just rides the underlying
stock's path (`curve_basis="underlying"`), metrics suppressed → badge **"Priced at deploy."**

**The honest, genuinely-rigorous part:** the returns / drawdown / positive-rate /
Monte-Carlo *are* real — computed as an event-study (CAAR) over real past occurrences (IT:
8 weak-guidance prints; Monsoon: 4 IMD-normal seasons), with real round-trip costs and
block-bootstrap MC. Crude honestly serves empties (no episode window). That rigor is worth
keeping.

**A real engine exists but is dormant.** `view_markets/expressions/builders/` has
`option_builder` → real `resolve_strategy` (net premium/max-loss/POP/greeks/margin/payoff),
`pair_builder` → real cointegration (Engle-Granger ADF + OU half-life), `basket_builder` →
real `weighting.compute_weights_detailed` (risk_parity/mcap/factor/black_litterman). None of
it feeds the 3 curated views — they were seeded by a differentiated hand-authored script
(`persist_views.py`) and then **flattened** by `rewire_v3.py`.

**Distinct strategy archetypes actually served: one** — equal-weight the v3 top-gainers
list, ± an overlay, + a non-modeled option shell. The spec's expression ladder (index
option > single-stock option > cointegrated pair > ETF-vs-index > optimised basket >
equal-weight basket *as fallback only*) is inverted: the fallback is the default.

---

## 6. Integrity red flags (non-negotiable tier — flag, don't ship)

- **Leaked monsoon copy on unrelated views.** `BenchmarkComparison.alignmentWords()`
  hardcodes seasonal language for *every* theme: score ≥80 → *"…lined up strongly with past
  **monsoon seasons**,"* 50–79 → *"past seasons."* This renders verbatim on **AI, gold, EV,
  Mideast, fintech** views. That's a fabricated context on screen — a correctness failure.
- **Holdings contradict the thesis.** The live IT view's basket is REC / Adani Power / JP
  Power / RVNL / Engineers India (power/infra momentum — the event-window top-gainers),
  while the view's own thesis text is a **defensive FMCG rotation.** The belief→expression
  bond — the entire product promise — is broken on the live path.
- **Off-theme raw research strings** (not rendered, but reveal relabelled reuse): the
  AI-jobs conservative expression's raw `label` = "EW rural basket," `capital_intensity`
  cites COROMANDEL/UPL/PIIND, `rationale` cites "NIFTY-commodity collinearity."
- Minor: typo "Own own a bundle of 7 ai/it companies" in `plain_one_liner` (unrendered).

---

## 7. Prioritized problem list

**P0 — integrity (fabrication / broken core promise):**
1. Leaked monsoon "past seasons" copy shown on AI/gold/EV/Mideast/fintech (§6).
2. Live holdings contradict the view thesis; belief→expression bond broken (§5, §6).

**P1 — the product is missing its differentiators:**
3. Strategies are one equal-weight basket per view with a swapped overlay; tiers not
   genuinely different; real builder engine dormant (§5).
4. Transmission map + "what's priced in / surprise" not wired in — the two ideas that make
   this a belief OS rather than a screener (§1).
5. Options carry no real payoff/greeks; the calculator's option tier can't be honest until
   this is modeled (§5).

**P2 — presentation / simplicity:**
6. Replace the strategies **table** with tier **cards**; de-jargon axes/captions (§3).
7. Build the **enter-amount → outcome** calculator (data already exists for basket/pair
   tiers) (§4).
8. Either wire the two confidence dials + lifecycle or formally cut them (right now they're
   dead code implying capability we don't ship) (§1).

**Keep (don't regress):** the real event-study/CAAR + block-bootstrap rigor, honest empties
for no-data views, own-return-only framing (no benchmark), the Yes/No stance grammar, and
the borders-only calm design law.
