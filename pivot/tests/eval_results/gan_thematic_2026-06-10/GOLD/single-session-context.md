# GOLD — single-session-context (composed 2026-06-10)

Class: referent/parameter resolution WITHIN one session. After discussing a symbol, thesis,
comparison, or draft, the user says "is it expensive?", "build a strategy for it", "the cheaper
one", "the second option", "what about the other one" — and a great copilot resolves the referent
silently, carries every already-known parameter forward, and never re-asks what was just said.

GREAT = (a) pronoun/superlative/ordinal resolved to the right object from session history,
(b) ticker/timeframe/capital/thresholds carried into the new card or answer, (c) round-trips
minimised — lead with a fully-built sensible default and offer the variant inline, instead of a
blocking binary `ask_user`, (d) the answer *names* the resolved referent so a misresolution is
catchable.

FAIL = "which stock do you mean?" after one was just analysed; a fresh blank card that drops the
capital/qty discussed two turns ago; an `ask_user` punt where a default exists; silently resolving
to the WRONG referent without naming it.

All numbers in the ideal answers below are **style exemplars** — the live build must quote real
Kite/tool values (or say plainly that the Kite token is expired and it is using fallback data,
never present stale data as live).

---

## 1) Exemplar prompts with fully-composed IDEAL answers

### Exemplar A — pronoun after analysis: "is it expensive?" → "build a strategy for it"

**Session:**
- T1: `analyse Tata Motors`
- T2: `is it expensive?`
- T3: `ok build a dip-buying strategy for it, around 1 lakh`

**T1 ideal (already covered by the structured-analysis gold; assumed correct):** full
structured analysis of TATAMOTORS — snapshot widget + 350–450 words with price, 52w range,
SMA50/200, RSI, PE/PB/ROE, news, defended view, "analysis, not financial advice" close.

**T2 ideal answer (text-only, no widget needed; ~200–300 words):**

> **Is Tata Motors expensive? On most measures, no — it's priced for cyclical doubt, not growth.**
>
> Continuing from the analysis above (TATAMOTORS, ₹1,012):
>
> | Measure | TATAMOTORS | Context |
> |---|---|---|
> | Trailing PE | 9.8x | NIFTY Auto ~22x; M&M ~27x; Maruti ~28x |
> | PB | 2.4x | vs own 5-yr median ~3.1x |
> | ROE | 24.1% | top quartile of NIFTY Auto |
> | Dist. from 52w high | −18% | high ₹1,179 |
>
> The single-digit PE is the headline: a 24% ROE business at under 10x earnings is cheap on
> paper. The market's discount is about **earnings durability**, not the multiple — JLR margin
> dependence on Europe/China demand and the EV capex cycle. So "expensive" is the wrong axis;
> the real question is whether FY26 earnings hold. If they do, the stock re-rates; if JLR rolls
> over, the cheap PE was a value trap.
>
> **My read: valuation is undemanding versus both peers and its own history — the risk is
> cyclical, not price.** Want me to compare it line-by-line against M&M and Maruti, or set an
> alert if it breaks the 52-week high?
>
> *This is analysis, not financial advice.*

**What the judge checks at T2:** no "which stock?"; "it" resolved to TATAMOTORS and **named**;
valuation framed with a markdown table of real multiples + peer/history anchors; a defended
verdict (not "depends"); a forward offer that itself stays in-context.

**T3 ideal answer — widget = `workflow_draft_card`, prefilled, single turn:**

Card: BUY TATAMOTORS, ₹1,00,000 allocation (≈98 shares at ₹1,012), trigger `RSI(14) < 32`
on daily bars, exit `+8% profit target OR RSI > 65`, status DRAFT — register-not-execute.

Accompanying text (~120–180 words):

> **Dip-buy draft for Tata Motors is on the card — ₹1,00,000, as discussed.**
>
> Defaults I chose (all editable on the card):
> - **Entry:** RSI(14) < 32 on daily close — "oversold" without being so rare it never fires.
>   On the last 2 years of TATAMOTORS daily bars this triggered ~5 times.
> - **Size:** full ₹1,00,000 in one tranche ≈ 98 shares at today's ₹1,012.
> - **Exit:** +8% target or RSI back above 65, whichever first.
>
> Variants if you'd rather: **(a)** three ₹33k tranches at RSI 35/30/25 instead of one shot,
> **(b)** a simple price-dip trigger (−7% from 52w high) if you don't want an indicator.
> Say "make it tranches" or "use the price dip" and I'll amend this card — or hit **Backtest**
> to see how the RSI version did historically. This registers the order; you confirm in your
> broker app.

**What the judge checks at T3:** card rendered FIRST TURN with symbol=TATAMOTORS and
capital=₹1,00,000 both carried from context (never re-asked); defaults stated and justified;
variants offered as *amendments to this card*, not as a pre-card question; register-not-execute
stated.

---

### Exemplar B — superlative referent after a comparison: "the cheaper one"

**Session:**
- T1: `compare HDFC Bank and ICICI Bank`
- T2: `set up a monthly SIP in the cheaper one, 10k a month`

**T1 ideal:** comparison answer with a head-to-head markdown table (price, PE, PB, ROE, NIM,
1y return, 52w position) + 250–350 words and a defended pick. Crucially the table makes "cheaper"
*determinable* — e.g. HDFCBANK PE 19.2x / PB 2.6x vs ICICIBANK PE 20.8x / PB 3.3x.

**T2 ideal answer — widget = `workflow_draft_card` (recurring/SIP), single turn:**

> **SIP draft: ₹10,000/month into HDFC Bank — the cheaper of the two from the comparison
> above (PE 19.2x vs ICICI's 20.8x, PB 2.6x vs 3.3x).**
>
> Card is set to: BUY HDFCBANK worth ₹10,000 on the **1st trading day of each month** at
> market open. At today's ₹1,648 that's ~6 shares per instalment; ₹1.2L deployed over a year.
>
> One thing to flag honestly: "cheaper" on multiples ≠ better SIP. ICICI's premium reflects
> higher ROE (18.4% vs 16.9%) — if you'd rather SIP the *better-performing* one, say "switch
> it to ICICI" and I'll amend this card. Date and amount are editable; this registers each
> month's order for you to confirm in your broker app.

**What the judge checks:** "the cheaper one" resolved by actually reading the comparison just
produced (correct symbol per the table's own numbers — judge must verify consistency between T1
table and T2 resolution); resolution **named with the evidence** ("PE 19.2x vs 20.8x") so the
user can catch an error; amount + frequency parsed onto the card; the legitimate counter-view
(cheap ≠ best) raised as a one-line amendment offer, NOT a blocking question; no re-ask of
anything stated in T1/T2.

---

### Exemplar C — picking a variant of a prior offer: "the second one"

**Session:**
- T1: `alert me when INFY crosses 1600`
- T1 assistant: builds the alert card and offers: "(a) keep it alert-only, or (b) I can make it
  buy 10 shares on the cross instead."
- T2: `do the second one, but make it 25 shares`

**T2 ideal answer — widget = amended `workflow_draft_card`:**

Card mutates from notify-only → BUY 25 INFY when price crosses ₹1,600 (trigger unchanged),
status DRAFT.

> **Done — upgraded the alert to a buy: 25 shares of INFY when it crosses ₹1,600.**
>
> Same trigger as before (daily price cross above ₹1,600; INFY is at ₹1,512 now, so ~5.8%
> away); the action is now a registered BUY of 25 shares ≈ ₹40,000 at the trigger price
> instead of just a notification. You'll still get the alert — plus the order queued for your
> confirmation in your broker app. Want a profit-target/stop pair on it too? Say e.g.
> "add 8% target, 4% stop" and I'll extend this card.

**What the judge checks:** ordinal "the second one" resolved to variant (b); the **existing
draft is amended in place** (same trigger value ₹1,600 carried, not re-asked, not a fresh
blank card); the inline amendment "25 shares" overrides the offered 10; the diff is narrated
("alert → buy 25"); known regression to watch: silent collapse back to notify-only counts as a
hard FAIL on this exemplar.

---

## 2) RUBRIC (weights sum to 1.00; each checkable per-turn against the transcript)

| # | Criterion | Weight |
|---|---|---|
| 1 | **Referent resolution correctness** — pronoun ("it"), superlative ("the cheaper one"), ordinal ("the second one"), and "the other one" resolve to the right session object; zero `ask_user`/"which stock?" when context determines it uniquely; resolving to the WRONG object = automatic ≤2/10 on the turn. | 0.25 |
| 2 | **Parameter carry-forward** — ticker, capital/qty, thresholds, timeframe stated in earlier turns appear in the new card/answer without being re-asked; amendments mutate the EXISTING draft (trigger preserved, only the changed field diffs) rather than spawning a blank card or silently dropping a leg/action. | 0.20 |
| 3 | **Round-trip minimisation: default + inline variant** — where a judgement call exists (tranches vs one-shot, cheaper vs better), the answer ships a fully-built default and offers the alternative as a one-line amendment ("say X and I'll change it"); `render_hint=ask_user` or a thin binary punt where a default exists = FAIL on this criterion. | 0.15 |
| 4 | **Correct widget, prefilled** — the resolved intent maps to the right card (workflow_draft_card for rules/SIP/alert-upgrades, comparison table for compares, no bogus snapshot for stopwords) and the card's params match the resolved context exactly. | 0.15 |
| 5 | **Data-richness & structure** — real tool numbers quoted (price, PE/PB/ROE, RSI, ₹-sizing math like "≈98 shares at ₹1,012"); markdown table for any valuation/comparison turn; analysis-class turns 250–450 words, action-class turns 120–250 words + card; degraded Kite token disclosed honestly, never presented as live; zero fabricated values. | 0.15 |
| 6 | **Resolution named (catchability)** — the answer states what it resolved to and why ("the cheaper one → HDFCBANK, PE 19.2x vs 20.8x") so the user can catch a misresolution in one glance. | 0.05 |
| 7 | **No context bleed** — a genuinely fresh, independent prompt mid-session does NOT drag the stale referent or active draft into the answer (per-session draft-eviction behaviour); stale-symbol contamination of a new topic = FAIL on this criterion. | 0.05 |

Scoring: weighted mean of 0–10 per criterion. Any hard-FAIL marker (wrong referent, silent
notify-only collapse, fabricated number) caps the exemplar at 4/10 overall.

---

## 3) Capability PROBES (suspected current failures)

**Probe 1 — chained referent across object types (draft → backtest → amend):**
```
T1: buy 15 LT whenever it falls 5% in a week, sell at 10% profit
T2: backtest that over the last 3 years
T3: hmm, make the drop 7% and run it again
```
GREAT = T2 backtests the *exact card just drafted* (symbol LT, 5%-weekly-drop entry, 10% exit,
qty 15) on 3y daily bars and renders the backtest chart with metrics; T3 amends one param on the
same strategy and re-runs, narrating the delta ("7% trigger: 4 trades vs 9, CAGR 11.2% vs 8.9%").
Suspected failures: T2 re-asks for symbol/strategy or backtests a generic template; T3 forgets
the 10% exit or re-runs the unamended 5% version.

**Probe 2 — fresh thematic scenario + in-context basket surgery (NOT in baseline):**
```
T1: position me for a severe El Niño drought hitting Indian agriculture next year
T2: drop the weakest name from that basket, replace it with a better play, and size the whole thing to 50k
```
GREAT T1 = thesis decoded turn-one with named instruments and NO ask_user punt: a
workflow_draft_card basket of reasoned beneficiaries (e.g. drip/micro-irrigation, agrochem/seeds
that gain from pest pressure, sugar on supply squeeze) AND a stated losers/avoid leg (rural-demand
plays: tractors, 2-wheelers, rural NBFCs), plus an invalidation/confirmation trigger (e.g. "arm
only if IMD's Apr forecast confirms El Niño; alert card available"). GREAT T2 = identifies the
weakest leg from its OWN T1 basket with a stated reason, swaps it in place, re-splits ₹50,000
across the surviving legs with per-leg ₹ and share counts, and shows a before/after table of the
basket. Suspected failures: T1 punts to "bullish or bearish?"; T2 rebuilds a fresh basket from
scratch (losing T1's legs), can't rank its own legs, or drops the sizing to a default.
