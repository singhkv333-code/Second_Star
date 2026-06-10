# GOLD — category: regression (bread-and-butter must-never-break)

Round-3 GAN gold standard. This category guards the two highest-traffic flows: a plain
live-price ask and a vanilla indicator-buy agent. Both **currently PASS** in
`r3/run_20260609_210102.json` (KOTAKBANK ₹381.70 +1.22% honest-tagged; GRASIM RSI card via
deterministic fast path, 0 LLM calls / 2 ms). The gold bar below is what *perfect* looks
like — the only open gap on the canonical prompts is the **undated** `(yfinance, EOD)` tag
(deferred R14 item), plus the suspected fragile near-neighbours covered in Probes.

External bar (verified 2026-06-10): retail quote surfaces (CNBC, Google Finance,
Robinhood) always pair the price with change, %change, and an explicit **time-of-last-trade
+ source attribution**; Streak by Zerodha — the Indian reference for no-code automations —
pairs every rule build with a plain-English condition readback, an explicit deploy step
(never auto-live), and backtest one click away.

---

## Ideal answers

### 1) "what's KOTAKBANK trading at right now"

**Widget:** none required (inline answer; a compact snapshot card is acceptable but must not
push the text past the brevity bar).
**Tools:** `get_live_price` (Kite primary; yfinance fallback when token expired — fallback is
correct behaviour, not a defect, **but it must be dated**).

Ideal reply — Kite live path:

> **KOTAKBANK ₹<live>2,142.30</live>** — <live>+26.10 (+1.23%)</live> today.
> Day range <live>₹2,108.00–₹2,151.90</live> · prev close <live>₹2,116.20</live>.
> *(Kite, live · <live>14:32 IST, 10 Jun 2026</live>)*

Ideal reply — yfinance fallback path (token expired):

> **KOTAKBANK ₹<live>381.70</live>** — <live>+1.22%</live>.
> *(yfinance, EOD close of <live>Mon 9 Jun 2026</live> — live Kite feed unavailable right now)*

Rules embodied: the number is the tool's number to the paisa; ≤5 lines; **no upsell**
("want an alert on it?" padding fails the session's own bar); source tag carries an
**as_of date/time**, not a bare "(yfinance, EOD)"; never present EOD as "right now" without
the qualifier.

### 2) "make an agent that buys 10 GRASIM when its RSI drops below 30"

**Widget:** `workflow_draft_card` — exactly 2 steps, no `ask_user`, no qty re-ask:

| step | type | params (must match verbatim) |
|---|---|---|
| 1 | `trigger.indicator` | `symbol=GRASIM, indicator=rsi, period=14, operator=<, value=30.0` |
| 2 | `action.place_order` | `symbol=GRASIM, side=buy, quantity=10, order_type=market` |

Ideal reply (≤3 sentences + card):

> Here's your draft — **GRASIM RSI(14) below 30**: when 14-day RSI crosses under 30, it
> registers a market buy of **10 GRASIM** for you to confirm in your broker.
> Hit **Activate** on the card to arm it, or tell me to backtest this rule first to see how
> often it fired historically.

Rules embodied: confident, brief, parse read back exactly (symbol, qty 10, RSI period
defaulted to 14 and *named* as a default in the card rationale); register-not-execute
language ("registers … you confirm" / "arm") — never "placed/order is live"; the one
permitted extra clause is the **backtest offer** (Streak parity; the session is explicitly
"backtestable"). The deterministic fast path producing this card (0 LLM calls) is the gold
latency profile — keep it.

### 3) Near-neighbour (same bread-and-butter intent, Hinglish): "kotak bank ka bhav kya chal raha hai abhi?"

Identical contract to #1 — same tool, same number, same dated source tag; reply may mirror
the register lightly but must not be required to:

> **KOTAKBANK abhi ₹<live>2,142.30</live>** pe hai — aaj <live>+1.23%</live>.
> *(Kite, live · <live>14:32 IST, 10 Jun 2026</live>)*

What must NOT happen (this is the regression-trap, cf. the historical "ME"/stopword
snapshot bugs): no Hindi function word ("KA", "ABHI", "BHAV") treated as a ticker; no
bogus snapshot card for a non-symbol; no clarification re-ask for a symbol that is plainly
KOTAKBANK.

---

## Rubric (judge mechanically, weights sum to 100)

1. **(25) Real number fidelity.** Price/% in the reply text equals the `get_live_price`
   payload value exactly (to the paisa); for the build, card params equal the user's literal
   ask (GRASIM, qty 10, RSI < 30). Any fabricated, rounded-away, or payload-mismatched
   number = 0 on this criterion.
2. **(20) Dated freshness honesty.** Source tag present AND dated/timed: "(Kite, live ·
   HH:MM IST, date)" or "(yfinance, EOD <date>)". Undated "(yfinance, EOD)" = half marks;
   EOD data presented as live/"right now" with no qualifier = 0; any 2024/stale date = 0.
3. **(20) Card shape, zero re-ask.** `render_hint=workflow_draft_card`; steps ==
   [`trigger.indicator{rsi,14,<,30}`, `action.place_order{buy,10,market}`]; `tools_called`
   contains `propose_workflow`; no `ask_user` anywhere in the turn; no dropped or invented
   condition (e.g. no unsolicited stop-loss step).
4. **(15) Brevity discipline.** Price answer ≤5 lines and ≤~60 words with zero upsell
   sentences; build reply ≤3 sentences outside the card (the single backtest-offer clause
   is allowed, a feature list is not).
5. **(10) Register-not-execute language.** No "placed/executed/order is live/done" before
   confirmation; uses draft/arm/Activate/registers-for-your-confirmation phrasing
   (execution-theatre lint from R3 finding on `hcltech_gtt_price_level`).
6. **(10) Fast-path health.** Vanilla GRASIM build resolves deterministically (llm_calls ≤1,
   server latency ≤1s) — and the deterministic pin does NOT fire on variants that add or
   change a condition (see Probe 2); plain price turn ≤~8s server.

---

## Probes

Realistic near-neighbour asks a great copilot must nail but the snapshot/summary suggest
Pivot may not yet.

### probe_hinglish-price-fastpath
**Why suspected:** repo history of stopword/ticker misparse ("show me … ME priced",
BHARTIARTL call-chain stopword session) and the R3 `hinglish_then_resize_notional` FAIL
showed Hinglish turns lose symbol context; FE also runs local ticker-snapshot shortcuts that
could latch onto Hindi function words. A pure-Hinglish price ask with zero English ticker
casing is the cheapest way to catch it.
**Turns:**
1. `kotak bank ka bhav kya chal raha hai abhi?`
2. `aur sbi ka?`

(Turn 2 additionally checks ellipsis context-carry: "and SBI's?" must price SBIN, not
re-ask or price "AUR".)

### probe_weekly-rsi-timeframe
**Why suspected:** the GRASIM build is served by a deterministic fast path pinned to daily
RSI(14) (R2 fix, 0 LLM calls in the snapshot). A one-word timeframe change ("weekly") risks
the pin firing anyway and **silently dropping the condition** — a dropped qualifier is a
correctness failure per CLAUDE.md — and `trigger.indicator` params in the card digest show
no timeframe field at all, so the engine may not support it; the honest path is to say so.
**Turns:**
1. `make an agent that buys 10 GRASIM when its weekly RSI drops below 30`
2. `is that checking the weekly chart or daily?`

(PASS = either a card with a real weekly timeframe param, or an honest "I can only evaluate
RSI on daily bars — want the daily version?" *before* arming; FAIL = a daily RSI(14) card
presented as fulfilling the weekly ask, or turn-2 prose claiming "weekly" while the card
says otherwise.)
