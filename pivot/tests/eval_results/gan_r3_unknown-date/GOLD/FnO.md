# GOLD — F&O (Round 3)

Composed 2026-06-10 against `r3/run_20260609_210102.json` + live recheck `/tmp/fno_verify_out/run_20260610_022118.json` (post-d621699).

**State check before composing (so the judge doesn't re-litigate fixed items):**
- `nifty_build_iron_condor` — **FIXED post-d621699** (live recheck): real 4-leg `iron_condor` card (23750/22950 shorts, 23950/22700 wings, max loss ₹12,571, POP 67.3%), no `ask_user` collapse. Residual: reply is ~60 words, "Built." prose, **no leg table** — fails the table mandate even though execution now passes.
- `hedge_reliance_protective_put` (d621699 verify) — guard works: protective_put card + lot disclosure. Residual prose bug: says one lot is *"smaller than your 250-share holding"* when RELIANCE lot = 500 > 250 — the over-hedge direction is **inverted** in text (the card is right, the sentence is wrong).
- Still open from R2 summary, confirmed in snapshot: chain reply has **zero markdown table / zero named OI strikes**; suggest names 1 of 3 candidates with **no comparison table**; critique says **"unlimited downside" on a short put** (engine `max_loss=None` mislabel — true bound = (strike − premium) × lot) and omits the POP line despite `pop: 0.9851` sitting in the digest.

**External bar (what best-in-class gives an Indian retail user):** Sensibull/Opstra-class chain views lead with ATM IV + PCR (OI and volume separately) + max pain as a labelled magnet line, and tag the **top OI strikes as support/resistance**; strategy builders auto-compute net credit, max P/L, breakeven, **POP**, and **margin**, per leg in a table, with lot-size-aware sizing; the standard editorial line on naked puts is "same dollar risk as cash-secured, but margin leverage turns it account-destroying — spreads fix the margin and the tail." Pivot's chat answer must compress that into one structured reply + card. Sources: [Sensibull live charts](https://web.sensibull.com/live-options-charts), [Sensibull option chain](https://sensibull.com/option-chain.html), [Sensibull bull put spread](https://web.sensibull.com/learn-options-strategies/bull-put-spread), [Sensibull strategy builder](https://web.sensibull.com/option-strategy-builder), [naked vs cash-secured puts](https://www.daystoexpiry.com/blog/naked-puts-vs-csp), [Option Strategist on credit spreads](https://optionstrategist.com/blog/2022/09/what%E2%80%99s-best-covered-writes-naked-puts-or-credit-spreads-1515), [OI/PCR/max-pain reading](https://optionx.trade/blogs/how-to-read-option-chain-oi-pcr-max-pain).

All numbers below marked `<live …>` are placeholders to be filled from the actual tool payload — the shapes and which numbers MUST appear are the contract.

---

## Ideal answers

### 1) `nifty_chain_max_pain_pcr`
**User:** "show me the NIFTY option chain for this week with max pain, PCR and the expected move, and tell me what each is hinting"

**Widget:** `option_chain_card` (NIFTY, nearest weekly expiry, ATM ± 8 strikes, `rows` populated). The `card_digest` must carry enough for the text to be grounded: max_pain, pcr_oi, pcr_volume, expected_move, **top-3 call-OI and top-3 put-OI strikes with OI values** — the table below is populated from these, never invented.

**Ideal reply text:**

NIFTY weekly chain (`<live expiry 2026-06-11>`, spot `<live 23,352>`, ATM `<live 23,350>`) — full chain on the card; here's the read.

## The three numbers you asked for

| Metric | Value | What it's hinting |
|---|---|---|
| Max pain | **<live 23,250>** | The strike where option writers lose least — expiries tend to drift toward it in the last 1–2 sessions. It sits ~<live 0.4>% *below* spot, a mild downward pull. |
| PCR (OI) | **<live 0.76>** | Call OI outweighs put OI — writers are more comfortable capping upside than defending downside. Below ~0.8 reads cautious-to-bearish. |
| PCR (volume) | **<live 0.87>** | Today's flow is less bearish than the standing OI — fresh activity isn't adding to the downside bet. |
| Expected move | **±<live 86> pts (<live 0.37>%)** → <live 23,261–23,433> | The straddle-implied range into expiry. Tight — the market is pricing a pin, not a trend, absent a catalyst. |

## Where the OI walls are

| Strike | Side | OI | Reads as |
|---|---|---|---|
| **<live 23,500 CE>** | Call | <live 1.42 Cr> | Heaviest call wall — first resistance |
| <live 23,400 CE> | Call | <live 0.98 Cr> | Near-term cap |
| **<live 23,200 PE>** | Put | <live 1.10 Cr> | Strongest support shelf |
| <live 23,000 PE> | Put | <live 0.87 Cr> | Deeper floor |

**Putting it together:** spot <live 23,352> is sandwiched between put support at <live 23,200> and the call wall at <live 23,500>, max pain tugs slightly lower at <live 23,250>, and the expected move says ±<live 86> points. That's a **range-bound, mildly heavy** setup: <live 23,200–23,500> is the box the chain is pricing for this expiry. A close above <live 23,500> with call OI unwinding would invalidate the cautious read.

Data: <live Kite NFO chain / yfinance fallback, as-of timestamp>. This is chain analysis, not financial advice.

*(Why this is the bar: both mandated tables present, every value traceable to the digest, each metric carries a one-line "so what", and the synthesis names the actual box with an invalidation level — the Sensibull dashboard read, in prose.)*

---

### 2) `banknifty_suggest_bullish`
**User:** "I'm bullish on BANKNIFTY for this expiry, suggest me a sensible defined-risk options play, retail size"

**Widget:** `option_strategy_card`, template `bull_put_spread` (or engine's top pick), 1 lot, with `candidates` populated — and the digest must carry **{max_profit, max_loss, pop, net} for each candidate**, not just the winner, so the comparison table is populatable.

**Ideal reply text:**

For a bullish-but-defined-risk view on BANKNIFTY into `<live expiry 2026-06-11>` (spot `<live 54,310>`, lot 30), I compared three structures off the live chain:

| Structure | You're betting | Max profit | Max loss | POP | Net |
|---|---|---|---|---|---|
| **Bull put spread** (sell <live 54,200 PE>, buy <live 53,900 PE>) | "Stays above <live 54,200>" | **₹<live 2,295>** | ₹<live 6,705> | **<live 67.5>%** | +₹<live 76.5>/share credit |
| Bull call spread (buy <live 54,300 CE>, sell <live 54,800 CE>) | "Rallies past <live 54,470>" | ₹<live 9,990> | ₹<live 5,010> | <live 41>% | −₹<live 167>/share debit |
| Long call (<live 54,400 CE>) | "Rallies hard, fast" | Uncapped | ₹<live 8,100> (premium) | <live 33>% | −₹<live 270>/share debit |

**My pick for "sensible, retail size": the bull put spread** — it's on the card. You win in three of four outcomes (up, flat, even mildly down to <live 54,200>), POP is <live 67.5>%, and the worst case is a known ₹<live 6,705>, which is also roughly the margin blocked. Breakeven **<live 54,272>**, so you're profitable anywhere above that at expiry. The trade-off is honest: you cap your upside at ₹<live 2,295> — if you expect a sharp rally rather than "holds up", the bull call spread pays ~4x more but needs the move to actually happen (POP <live 41>%).

Theta works *for* the put spread and *against* the other two — with <live 2> days to expiry, that's the deciding edge for a "bullish but not heroic" view.

The card registers the spread — review the strikes and confirm there; nothing is placed until you do. Risk only what the max-loss line shows. This is a framework, not financial advice.

*(Bar: ≥2-candidate table with real numbers, a defended pick with the trade-off named, breakeven + POP + margin all surfaced, lot-aware sizing, register-not-execute closing.)*

---

### 3) `critique_naked_put_reliance`
**User:** "I want to sell a naked put on RELIANCE for the monthly expiry — tear my idea apart and tell me what's wrong with it"

**Widget:** `option_strategy_card` for the **safer alternative** (bull put spread), with the critiqued naked leg shown via the critique path; verdict `risky`.

**Ideal reply text:**

Happy to tear it apart. Pricing your idea off the live monthly chain (`<live 2026-06-30>`, RELIANCE spot `<live 1,254>`, lot 500): sell the `<live 1,220 PE>` at ~₹<live 11.50> collects **₹<live 5,750>** of premium against margin of roughly **₹<live 1.05L>**.

**What's wrong with it:**

1. **The payoff is asymmetric the wrong way.** Your max loss is *not* unlimited — a put's downside stops at zero — but it is **₹<live (1220 − 11.50) × 500 = 6,04,250>** if RELIANCE somehow went to 0, and a realistic −15% gap costs you ~₹<live 88,000> against that ₹<live 5,750> collected. You're risking ~<live 15>x your reward on the tail.
2. **POP flatters you into oversizing.** POP is **<live 92>%** — you'll win most months, which is exactly how short-put sellers end up too big when the one bad month arrives.
3. **Margin is unstable.** Unlike a spread, naked-put margin is recalculated as the stock falls — the position demands the most capital at the worst moment, and a margin call can force you out at the low.
4. **Assignment risk:** ITM at expiry means delivery obligations on 500 shares (~₹<live 6.1L> notional) — be sure you want that, or it's a forced exit.
5. **Liquidity:** the <live 1,220> strike shows a <live wide> bid-ask — entry and panic-exit both pay the spread.

**The fix — define the loss first:**

| Structure | Max profit | Max loss | POP | Margin |
|---|---|---|---|---|
| Naked <live 1,220 PE> | ₹<live 5,750> | **₹<live 6,04,250>** (to zero) | <live 92>% | ~₹<live 1.05L>, floats up as it falls |
| Bull put spread (sell <live 1,220>, buy <live 1,180>) | ₹<live 4,212> | **₹<live 15,788>** (fixed) | <live 88>% | ₹<live 15,788>, fixed |

You give up ~<live 27>% of the premium and in exchange the worst case shrinks ~<live 38>x and the margin stops moving against you. That's the trade I've drafted on the card.

**Verdict: don't sell it naked.** If you want the premium, sell the spread; if you actually *want* to own RELIANCE at <live 1,220>, then a cash-secured put (full ₹<live 6.1L> set aside) is the honest version of this trade — naked-on-margin is the only variant with no defensible story. Card registers only — you confirm. Analysis, not financial advice.

*(Bar: bounded-loss arithmetic stated correctly with the actual rupee bound — never "unlimited" for a put; POP quoted and weaponised against oversizing; margin-instability + assignment named; mandated 2-row comparison table; a defended verdict that still respects the user's premium-income intent via the CSP branch.)*

---

## Rubric

Judge each F&O reply 0–10; weights sum to 100. Check mechanically against response text + `tools_called` + `render_hint` + `card_digest`.

1. **Route + widget correctness (20%).** Chain ask → `get_option_chain` + `option_chain_card`; suggest → `suggest_option_strategy` + `option_strategy_card`; named build (iron condor etc.) → `build_option_strategy` + a populated multi-leg card; critique → critique path + alternative build. Terminal `render_hint=ask_user` on a fully-specified or defaultable ask = 0/20. Pricing the underlying as an equity snapshot = 0/20.
2. **Mandatory markdown table present and populated (20%).** Chain: a metrics table AND an OI-walls table naming ≥4 real strikes with OI. Suggest: ≥2-candidate comparison table with max P/L + POP per row. Build: a per-leg table (type/side/strike/premium). Critique: ≥2-row current-vs-alternative table. Zero tables on any of these = 0/20; one of two required chain tables = 10/20.
3. **Number fidelity + provenance (15%).** Every figure in prose traceable to `card_digest`/tool payload (strikes, OI, premiums, POP, breakevens, max P/L, margin); expiry date and lot size stated; data source + as-of tagged (Kite vs yfinance fallback). Any fabricated or un-payload-backed number = cap at 5/15.
4. **Risk arithmetic correctness (15%).** Short put loss is **bounded**: (strike − premium) × lot — the word "unlimited" attached to a short put = 0/15. max_loss/max_profit/breakeven/POP mutually consistent with the digest (e.g. spread max_loss = width×lot − credit). Hedge sizing direction correct (lot > holding ⇒ "over-hedge", never "smaller").
5. **Interpretation layer + defended verdict (15%).** Each quoted metric (max pain, PCR, expected move, POP, margin) carries a one-line "so what"; suggest/critique end in an explicit pick/verdict with the trade-off named and an invalidation or alternative branch. Metric values quoted with no interpretation = ≤5/15.
6. **Register-not-execute discipline (10%).** No "placed/executed/live/running" before confirmation; explicit "card registers — you confirm" (or equivalent); sizing expressed in lots with rupee capital/margin; advice disclaimer present. Execution-theatre phrasing = 0/10.
7. **Depth floor (5%).** Chain/suggest/critique replies ≥150 words of substance (tables count); named build ≥100 words including the leg table — "Built." + two sentences fails. No padding: repeated boilerplate doesn't count toward the floor.

---

## Probes

Realistic asks a best-in-class copilot handles that the snapshot suggests Pivot cannot yet.

### probe_iv-rank-premium-timing
**Why suspected missing:** project memory marks IV-history as pending (F&O P4); the chain payload carries point-in-time IV only. A great copilot either answers with IV rank/percentile or honestly says "I have today's IV, not the history" and gives the ATM-IV + India-VIX-level proxy read with a concrete number. Pivot will likely either fabricate a "high/low" judgement with no baseline or dodge entirely.
- Turn 1: "is NIFTY option premium rich or cheap right now compared to the last few months? should I be a buyer or seller of premium this week?"
- Turn 2: "ok then just tell me today's ATM IV and what regime that usually means"

### probe_roll-losing-short-call
**Why suspected missing:** no roll/adjustment path appears anywhere in the session set or tool list — strategies are built fresh, never repaired. Rolling (close existing leg + open further strike/expiry, net the two premiums) is the single most common F&O follow-up for a retail seller in trouble. Expect Pivot to mis-route to a fresh build that ignores the existing position, or to a generic workflow.
- Turn 1: "maine NIFTY ka 23400 CE becha tha pichhle week, market upar bhaag raha hai aur main loss mein hoon — isko roll kar do next expiry mein thoda upar"
- Turn 2: "and set an alert if the new short strike gets within 100 points of spot"
