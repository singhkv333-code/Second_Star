# GOLD — execution-stress (GAN round 3)

Category intent: the six prompts in this category are *parse traps* — stopword collisions
("call"), notify-vs-order, explicit-budget baskets, capability disclosure, the canonical
open+3% two-leg build, and the price-level/GTT disambiguation. The R3 snapshot
(`r3/run_20260609_210102.json`) shows 4/6 with correct cards; the residual failures are
(a) `basket_three_symbol_split` collapsing to `ask_user` despite an explicit ₹60,000
budget, and (b) `hcltech_gtt_price_level` saying **"GTT placed"** before any confirmation
(execution theatre). A third, softer gap: correct cards shipping with one-line prose
(`bajajauto`: 17 words for a 4-step automation) — the best copilots read the whole
machine back.

Best-in-class bar (researched): Zerodha's own GTT contract is the reference for
price-level orders — trigger + limit pair, **buy limit slightly above trigger** so the
post-trigger limit order actually fills, ~1-year validity, notification on trigger
([Zerodha GTT docs](https://support.zerodha.com/category/trading-and-markets/charts-and-orders/gtt/articles/how-can-i-use-the-gtt-feature),
[Z-Connect GTT intro](https://zerodha.com/z-connect/kite/introducing-gtt-good-till-triggered-orders)).
2025-class AI copilots (TradeZ, TradingView AI Copilot) pair every armed automation with
a full natural-language readback + explicit confirm step before anything touches a broker
([TradeZing launch](https://www.prnewswire.com/news-releases/tradezing-launches-tradez-the-first-ai-trading-copilot-for-retail-investors-with-one-tap-brokerage-execution-302747579.html),
[TradingView AI Chart Copilot](https://www.tradingview.com/blog/en/tradingview-ai-chart-copilot-beta-57730/)).
Pivot's register-not-execute model means the *text* must always live one state behind the
broker: drafted → registered → (user places in broker). Numbers below marked `<live>` are
to be filled from real tool calls at answer time (Kite primary, `(yfinance, EOD as of
<date>)` tag on fallback).

---

## Ideal answers

### 1. `basket_three_symbol_split` — "if nifty falls 1% intraday, split 60000 equally across SUNPHARMA GRASIM and JSWSTEEL and buy"

**Widget:** `workflow_draft_card` with 4 steps —
`trigger.market_move(symbol=NIFTY 50, direction=down, change_pct=1.0, window=intraday)` then
**three** `action.place_order(side=buy, notional_inr=20000)` legs (or one
`action.allocate_notional(symbols=[SUNPHARMA,GRASIM,JSWSTEEL], total_inr=60000,
strategy=equal)` if the engine has it). No leg dropped, ₹60,000 preserved, **zero
quantity re-ask** — the budget *is* the size.

**Ideal reply text:**

> Drafted — a NIFTY dip-buyer that deploys **₹60,000 across three names, ₹20,000 each**, the moment NIFTY 50 falls **1% intraday** (vs today's open).
>
> | Leg | Symbol | Allocation | Last close | ~Shares at fire* |
> |---|---|---:|---:|---:|
> | 1 | SUNPHARMA | ₹20,000 | ₹<live> | ~<live> |
> | 2 | GRASIM | ₹20,000 | ₹<live> | ~<live> |
> | 3 | JSWSTEEL | ₹20,000 | ₹<live> | ~<live> |
>
> *Share counts are indicative at the last close <(source, as_of)> — the agent sizes each leg from the live price when the trigger actually fires, so each leg stays ≈₹20,000.
>
> **How it fires:** one trigger on the index, three buys together — no leg fires alone. As drafted it's **one-shot** (arms again only if you re-enable it); say "every time" if you want it re-arming daily.
>
> **Worth knowing:** a 1% intraday NIFTY fall has happened ~<live> times in the last 12 months, so this is an occasional-dip catcher, not a weekly buyer. There's no exit attached — want me to add a profit target or stop on the basket?
>
> This registers the orders for your confirmation — nothing is bought until you confirm on the card and place in your broker.

**Card must carry:** the index trigger with `change_pct` and intraday basis, all three
symbols literally (no `{{...}}` placeholder symbols), per-leg `notional_inr=20000`.

**Hard NOs:** asking "how many shares of SUNPHARMA" (budget given), collapsing to one
symbol, treating 60000 as a share count, `ask_user` render.

---

### 2. `hcltech_gtt_price_level` — "place a buy for 30 HCLTECH if it falls to 920"

**Widget:** `logic_card` from `create_gtt_order` — trigger ₹920, limit ₹921, buy, qty 30.
(Card was already correct in R3; the *text* is what regressed.)

**Ideal reply text:**

> Drafted a **GTT-style buy for 30 HCLTECH at ₹920** — ready to register, confirm on the card.
>
> | | |
> |---|---|
> | Trigger | price falls to **₹920** |
> | Order after trigger | buy **30** @ ₹921 limit |
> | Current price | ₹<live> <(source)> — trigger is **<live>% below** here |
> | Order value at fill | ≈ **₹27,630** (30 × ~₹921) |
>
> **Why the limit is ₹921, not ₹920:** on a buy GTT the limit sits slightly *above* the trigger so the order actually fills when touched instead of resting unexecuted — standard Kite GTT practice. GTTs stay armed up to a year and you're notified when it triggers.
>
> A ~<live>% fall is a deep trigger from here — it may take a real correction to reach. If you'd rather catch a shallower dip, say a level (e.g. "make it 1,050") and I'll re-draft.
>
> Nothing is placed yet — confirm on the card to register, then the order goes to your broker for you to place.

**Hard NOs:** "GTT **placed**/created/live/active" anywhere before the card's Confirm is
clicked; a fabricated current price (must come from the quote tool, fallback tagged
`(yfinance, EOD as of <date>)`).

---

### 3. `bajajauto_buy_open_sell_3pct` — "set up an agent that buys BAJAJ-AUTO at the open and books out when it rises 3%, 5 shares"

**Widget:** `workflow_draft_card`, the R3 4-step shape (already correct):
`trigger.market_relative_time(anchor=open)` → `place_order(buy, 5, market)` →
`trigger.exit_compound(unrealised_pct ≥ 0.03)` → `place_order(sell, {{context.1.quantity}}, market)`.

**Ideal reply text:**

> Drafted — your open-buy / +3% exit agent for **BAJAJ-AUTO**:
>
> | Step | What happens | Detail |
> |---|---|---|
> | 1 | At market open (09:15) | trigger fires next session |
> | 2 | **Buy 5 BAJAJ-AUTO** | market order at the open print |
> | 3 | Watch the position | waits for unrealised P&L ≥ **+3%** |
> | 4 | **Sell the same 5** | market order, books the gain |
>
> Both legs are wired to the same 5 shares, so the exit always matches the entry. At the last close (₹<live> <(source)>) the entry is ≈ ₹<live> notional and a +3% book-out is roughly **+₹<live>** before costs.
>
> **Two things this draft does *not* do (by design, flag if you want them):**
> - **No stop loss** — if it opens and slides, you hold 5 shares with no downside exit. Want a -2% stop added as a third branch?
> - **One-shot** — it buys at the *next* open after you activate, once. Say "every day" for a recurring version.
>
> Open-price entries carry gap risk: the market order fills at whatever the opening auction prints, which can differ from yesterday's close. Confirm on the card to register — you activate, nothing trades until then.

**Hard NOs:** refusing to tie entry to exit, a 09:30-cron downgrade, quantity re-ask,
"running/active" language pre-confirmation.

---

### Held-pass shapes (do not regress)

- `axisbank_alert_not_order` → `trigger.price(crosses_above,1300)+notify.message`, **no
  order step, no qty ask**; one extra ideal line: "Alert is in-app push — it fires once
  on the cross; say 'every time' for repeated alerts." R3 text is correct, just add the
  once-vs-repeat semantic.
- `titan_trailing_stop_disclosure` → `action.set_stoploss(trailing=true, 7%)` **with the
  live re-ratchet limitation stated in the visible reply** (R3 does this — keep it). Ideal
  adds the anchor numbers: "stop arms at ₹<live> (7% below current ₹<live>)".
- `bhartiartl_call_chain_stopword` → `option_chain_card`, never a stock snapshot. R3 read
  is good; ideal upgrades the OI bullets to the mandated markdown table (top-3 call + top-3
  put OI strikes with OI + IV) and states expiry **date** in the prose, since this is also
  governed by the R9 F&O table mandate.

---

## Rubric

Score each turn 0–10; weighted sum. A criterion scored 0 on (1), (2) or (3) caps the
turn at 4/10 regardless of the rest.

| # | Criterion (mechanical check) | Weight |
|---|---|---:|
| 1 | **Intent fidelity / no parameter re-ask** — every user-given parameter (symbol(s), qty *or* rupee budget, level, %, direction, both legs) appears in the card verbatim; a stated rupee budget NEVER produces a quantity question; no leg/condition silently dropped; totals preserved (₹60,000 → 3×₹20,000). | 25% |
| 2 | **Right artifact, no ask_user collapse** — alert→notify-only `workflow_draft_card`; price-level order→GTT `logic_card`; basket→multi-leg/allocate draft; "call option chain"→`option_chain_card` (not snapshot); `render_hint=ask_user` on a fully-specified prompt = 0 here. | 20% |
| 3 | **Lifecycle honesty (no execution theatre)** — pre-confirmation text uses drafted / ready to register / confirm on the card; the words placed, created, live, active, running about the order = 0 here; unwired capability (live trail re-ratchet) disclosed in visible text when relevant. | 15% |
| 4 | **Numeric grounding** — anchor price from a real tool call with source tag (`(yfinance, EOD as of <date>)` on fallback, undated tag loses points); derived numbers (% distance to trigger, per-leg notional, indicative shares, order value) arithmetically correct; zero fabricated values. | 15% |
| 5 | **Structured readback** — multi-leg/multi-param drafts get a markdown table or labeled step list mirroring the card; baskets show per-leg allocation; amendments use a Changed:/Kept: diff. One-line prose for a ≥3-step card scores ≤3 here. | 10% |
| 6 | **Mechanics education** — 1–3 lines explaining how the instrument behaves for *this* ask (buy-GTT limit-above-trigger, open-auction gap risk, trailing ratchet semantics, one-shot vs recurring trigger, OI-as-resistance). | 10% |
| 7 | **Risk / next-step nudge** — names what the draft deliberately lacks (no stop, no exit, one-shot) with a concrete one-tap offer; "analysis, not financial advice" line whenever a directional view is voiced. | 5% |

---

## Probes

### probe_weighted-basket-sector-trigger

Weighted (non-equal) budget split + **sector index** trigger. The equal-split basket
already regressed to `ask_user` (R3 worst regression: `_USER_QTY_PATTERNS` doesn't read a
bare `60000` as a budget); a weighted split has no obvious `allocate_notional
strategy=equal` escape hatch, and NIFTY IT may not resolve as a trigger symbol the way
NIFTY does. Expected great behaviour: one card, `trigger.market_move(NIFTY IT, -2%,
intraday)` + three notional legs ₹25,000/₹15,000/₹10,000, readback table; honest
fallback if NIFTY IT isn't a supported trigger underlying (offer NIFTY or the top
constituent, don't silently swap).

Turns:
1. `if the NIFTY IT index drops 2% intraday, put 50000 across TCS, INFY and HCLTECH — half in TCS, 30% in INFY and the rest in HCLTECH`
2. `make the HCLTECH leg 25% and shave TCS to 45%, same total`

### probe_scale-out-staged-exit

Partial-quantity staged exits + protective stop in one agent. R3's exit machinery shows
exactly one `trigger.exit_compound` branch with the *whole* `{{context.1.quantity}}`
templated into the sell — selling 5 of 10 at +3%, the rest at +6%, **and** an
everything-out stop at -2% needs three exit branches with split quantities, which the
current DSL has never demonstrated. Expected great behaviour: either a faithful 3-branch
card with explicit qty 5 / qty 5 / qty-remaining legs, or an honest "staged exits aren't
supported yet — closest real thing is a single exit at +3% on all 10 plus a -2% stop;
want that?" (no silent collapse to one exit, no fake card claiming all three).

Turns:
1. `buy 10 BAJAJ-AUTO at the open, sell 5 of them when it's up 3% and the rest when it's up 6%, and if it drops 2% from my buy price dump everything`
2. `actually make that 2% stop a trailing one`
