# GOLD — follow-up-amend (GAN thematic round, 2026-06-10)

Class: iterative amendment of an existing draft/card — "make it 15 shares", "add a 5%
stop", "change RSI to 25", "actually weekly", then "register it".

The single contract of this class: **the card the user is looking at is the card that
mutates.** GREAT = amend the EXISTING draft in place, change ONLY what was asked,
preserve every other field byte-for-byte, lead with a `Changed: … Kept: …` diff
readback whose numbers match the card_digest exactly, and the terminal confirm turn
flips the SAME draft to registered. FAIL = silent condition drop, **notify-only
collapse** (the documented P0: amending one field downgrades `place_order` →
`notify`), rebuild-losing-state, re-asking for a known symbol, or re-emitting a fresh
draft on confirm.

Numbers marked `<live>` are placeholders for real tool values at run time. When the
Kite token is expired, honest `(yfinance EOD, as of <date>)` tagging is correct
behaviour, not a defect — but the *draft fields* never degrade just because a price
fetch did.

---

## Ideal answers

### 1. `amend_chain_qty_stop_threshold` — the canonical 4-amend chain

**T0 — "buy 10 shares of INFY when RSI goes below 30"**

`workflow_draft_card`: `trigger.indicator{symbol:INFY, indicator:rsi, period:14,
op:<, value:30}` + `action.place_order{buy, qty:10, market, CNC}`. Readback table +
live RSI context (it should say whether the agent arms-and-waits or would fire now).

> Drafted — here's what's on the card:
>
> | Field | Value |
> |---|---|
> | Symbol | INFY (NSE) |
> | Trigger | RSI(14) < 30, evaluated on daily close |
> | Action | Buy **10 shares**, market, CNC |
> | Approx outlay | ~₹<live>16,150 @ ₹<live>1,615.00 (<live>Kite live / yfinance EOD, as of <live>date) |
> | Current RSI(14) | <live>43.6 — arms and waits, won't fire today |
>
> This **registers** the buy when it fires — you confirm the actual order in your
> broker app; nothing auto-executes. Edit any field on the card or just tell me.

**T1 — "make it 15 shares"**

Same draft mutated; ONLY `qty` moves; outlay recomputed; diff leads.

> **Changed:** size 10 → **15 shares** (~₹<live>24,225 at ₹<live>1,615.00).
> **Kept:** INFY, RSI(14) < 30 trigger on daily close, market order, CNC.
>
> Card's updated above — same draft, nothing else touched. Ready when you are.

Widget: the SAME `workflow_draft_card` (same draft identity), `action.place_order.qty:
10 → 15`. The trigger node in the card_digest must be **identical** to T0's. Hard
fail if the action collapses to `notify` or the RSI condition vanishes.

**T2 — "add a 5% stop loss"**

Additive amend: a new exit leg appears; trigger + qty untouched. "Stop loss" parses as
an exit-below-entry, NOT a second entry condition and NOT a profit target.

> **Changed:** added an exit — **sell all 15 shares if the position falls 5% below
> your entry price**.
> **Kept:** INFY, RSI(14) < 30 entry, buy 15 shares, market, CNC.
>
> So the full loop is now: RSI(14) dips under 30 → buy 15 registered → if the fill
> then drops 5%, a sell is registered to cut the loss. At today's ₹<live>1,615 a 5%
> stop is roughly ₹<live>80.75 of downside per share (~₹<live>1,211 on the position).
> If you'd rather pair it with a profit target too ("aur 8% pe bech do"), say the word.

Widget: same card, now `entry` + `exit{stop_loss_pct: 5}`. The worked ₹-impact line is
part of the bar — a great amend quantifies what the new field *means* at current
prices, not just echoes the percent.

**T3 — "actually change RSI to 25"**

Disambiguation-by-default: "RSI to 25" means the **threshold** 30 → 25, never the
period 14 → 25 (changing the period silently would be a different indicator). Gold
states the reading inline rather than punting to a question.

> **Changed:** entry threshold RSI(14) < 30 → **RSI(14) < 25** — a stricter,
> rarer trigger (taking the period as 14 still; say "RSI 25-period" if you meant the
> lookback). For context, INFY's daily RSI(14) closed under 25 only <live>3 times in
> the last year, vs <live>9 closes under 30 — expect far fewer fires.
> **Kept:** buy 15 shares market CNC, 5% stop-loss exit.

Widget: same card, `trigger.indicator.value: 30 → 25`, `period: 14` unchanged. The
under-25-vs-under-30 frequency context must come from a real history call (or be
omitted) — never invented.

**T4 — "register it"**

The confirm turn mutates the SAME draft to registered/armed — it does not re-emit a
fresh draft, re-summarise as if new, or go `tools_called=[]` + "click the button".

> Done — **registered and armed.**
>
> - **Watching:** INFY RSI(14) on each daily close (next evaluation ~<live>15:45 IST today).
> - **On fire (RSI < 25):** buy 15 shares is *registered* — you get notified and
>   confirm in your broker app. Pivot never auto-executes.
> - **After entry:** the 5% stop-loss leg arms automatically on your fill.
> - **Now:** RSI(14) = <live>43.6 → waiting, no order pending. Pause or edit any time.

Widget: the same `workflow_draft_card` with its status flipped to active/registered.
Only acceptable fallback if chat-side activation is genuinely unwired: ONE honest
sentence ("can't flip it live from chat yet — tap **Save & activate** on the card
above") *plus* the identical armed-state readback. Never both a fresh card and a
button instruction.

---

### 2. `hinglish_schedule_amend` — "actually monthly" on a SIP-style workflow

**T0 — "har Friday NIFTYBEES ₹5,000 ka khareed lo"**

`workflow_draft_card`: `trigger.schedule{weekly, day:FRI, time:<market-open-ish>}` +
`action.place_order{buy, notional_inr:5000, symbol:NIFTYBEES}`. Hinglish-mirroring
readback table, ~units-at-current-price line, register-not-execute in Hindi-friendly
phrasing ("order register hota hai, confirm aap karte ho").

**T1 — "actually weekly nahi, monthly — 1 tarikh ko"**

ONLY the schedule node moves. Amount, symbol, action survive. The diff quantifies the
cadence change (₹/month before vs after) because a schedule amend changes the user's
real outflow.

> **Badla:** har Friday → **har month ki 1 tarikh** (agla run: <live>1 July 2026,
> market khulne ke baad).
> **Waisa hi:** NIFTYBEES, ₹5,000 ka buy, register-only.
>
> Note karo — pehle ye ₹5,000 × ~4.3 = **~₹21,500/month** ja raha tha; ab
> **₹5,000/month** hi jayega. Agar intent "same monthly outlay, bas ek hi din" tha to
> bol do, ₹20,000 kar deta hoon.

Widget: same card, `trigger.schedule{monthly, day_of_month:1}`. The outflow
delta + the offer-to-resize is what separates great from merely-correct: a cadence
amend silently changing the user's monthly investment 4.3× deserves a flag.

**T2 — "haan theek hai, aur 7,500 kar do"**

Affirmative + amend in one breath: "haan theek hai" must NOT trigger
register-on-affirmative; the trailing amend wins, and the card mutates once.

> **Badla:** amount ₹5,000 → **₹7,500** per month (~<live>27 units @ ₹<live>278.40
> aaj ke hisaab se; asli units run ke din ke price pe).
> **Waisa hi:** NIFTYBEES, har month ki 1 tarikh, register-only.
>
> Card ready hai — "register" bolo aur arm kar deta hoon.

**T3 — "register kar do"** → same contract as exemplar 1 T4: same draft flips to
armed, next-run date read back (`agla run: <live>1 July 2026`), no fresh card.

---

### 3. `alert_plus_order_amend` — the notify-only-collapse killer

**T0 — "alert me when TCS crosses 4000 and buy 5 shares when it does"**

This is a compound action: `notify` AND `place_order` on the same trigger. The card
must carry BOTH legs from turn 0 (the historical failure is collapsing to alert-only
at build time or on the first amend).

> | Field | Value |
> |---|---|
> | Symbol | TCS (NSE) |
> | Trigger | price crosses **above ₹4,000** (last: ₹<live>3,612.50, <live>source, as of <live>date — ~<live>10.7% away) |
> | On fire | 1) **notify you**, and 2) **register a buy for 5 shares** (~₹20,000 at the trigger level) |
>
> Both legs are on the card. The buy is register-only — you confirm in your broker
> app. Want a sell side too (target/stop), or arm it as is?

**T1 — "make the level 4100"**

ONLY `trigger.value` moves. The deadly regression here is the buy leg silently
dropping so the card becomes notify-only — that is an automatic class FAIL however
good the prose.

> **Changed:** trigger level ₹4,000 → **₹4,100** (~<live>13.5% above the last
> ₹<live>3,612.50).
> **Kept:** TCS, alert + buy-5-shares on fire (~₹20,500 at the new level), register-only.

Widget: same card, both action legs present in the card_digest, `value: 4000 → 4100`.

**T2 — "and sell those at 8% profit"**

Additive: a profit-exit leg on the bought shares. Pronoun "those" resolves to the 5
shares from the existing draft — no re-ask of symbol or quantity.

> **Changed:** added an exit — **sell the 5 shares when the position is +8%**
> (entry at ~₹4,100 → exit zone ~₹<live>4,428).
> **Kept:** ₹4,100 cross trigger, alert + buy 5 shares, register-only.
>
> Full loop: TCS crosses ₹4,100 → you're pinged + buy registered → on your fill,
> an 8% profit-sell arms. "Register" bolo and it's live (armed, waiting — TCS needs
> a ~13.5% move first, so this is patient money).

---

## Rubric

Judge each session 0–10 from the actual response text + `tools_called` +
`card_digest` of every turn. Weights sum to 100.

| # | Criterion (mechanical check) | Weight |
|---|---|---:|
| 1 | **Minimal-diff mutation.** On every amend turn, exactly the asked field(s) change in the card_digest and every other node is identical to the prior turn's. Any silent drop of a trigger/condition/leg, any `place_order` → `notify` collapse, any basket-leg loss, or any unrequested default reset = 0/25 for the session. | 25 |
| 2 | **Amend-in-place, never rebuild.** Every turn mutates the SAME draft (same draft identity/lineage); no fresh `workflow_draft_card` is emitted for an amendment, no symbol/param is re-asked while a draft is active ("which stock?" mid-chain = 0 here), and "the first one / those / wala" pronouns resolve to the right draft and leg. | 20 |
| 3 | **`Changed:/Kept:` diff readback.** Every mutation turn leads with an explicit two-part diff; the Changed line names old → new values, the Kept line enumerates the surviving load-bearing fields (symbol, trigger, qty, exits), and every number in the diff matches the card_digest exactly. Missing readback on any amend turn caps this at 5/15. | 15 |
| 4 | **Confirm-to-register mutates the same draft.** "register it / haan kar do" flips the existing card to registered/armed via a real tool call and reads back armed state (what's watched, evaluation cadence, current indicator/price distance, what happens on fire). Re-emitting a fresh draft on confirm = 0; `tools_called=[]` + bare "click Save & activate" ≤ 5; honest unwired-fallback (one click instruction + full armed readback) ≤ 10. | 15 |
| 5 | **Amend-parse fidelity.** The amended value lands in the right slot with the right unit: "15 shares" → qty not percent, "₹12,000 ka" → notional not qty, "RSI to 25" → threshold not period (stated inline), "5% stop" → exit-below-entry not target, "monthly" → schedule node only. Affirmative+amend turns ("haan, aur 7500 kar do") apply the amend without premature registration. | 15 |
| 6 | **Recompute + quantify the consequence.** Each amend that changes economics carries the recomputed number from a real tool value (new outlay at qty change, ₹-impact of a stop, monthly-outflow delta on cadence change, distance-to-trigger on level change) with source + as-of tag; tool failure mid-amend keeps the draft and falls back to last close with the caveat — never abandons or re-asks. | 10 |
| 7 | **Register-not-execute discipline.** No turn claims placed/active/live before the actual state change; drafted states say "drafted/ready"; "registers — you confirm in your broker app" appears at least once per session; post-register readback never invents a pending order when the trigger hasn't fired. | 10 |

Pass ≥ 7.5 weighted; Partial 5.0–7.4; Fail < 5.0.

---

## Probes

Fresh scenarios, not in any prior baseline run.

### probe_compound_arithmetic_amend

1. `buy 20 shares of HAL if it falls 4% in a day, with a 6% stop loss`
2. `halve the quantity and tighten the stop to 3%`
3. `register it`

Why suspected: turn 2 is one utterance touching TWO fields, and "halve" requires
arithmetic **on the card's current state** (20 → 10), not a user-supplied literal. The
known amend path pattern-matches single field=value edits; likely outcomes are
applying only one of the two changes, asking "halve to what?", or a rebuild that drops
the dip-trigger. Gold: one mutation, `Changed: qty 20 → 10 shares, stop 6% → 3%`,
`Kept: HAL, 4% intraday-fall trigger`, then a clean same-draft register.

### probe_amend_after_register_then_revert

1. `every Monday buy ₹3,000 of GOLDBEES`
2. `register it`
3. `wait — make it ₹4,500`
4. `actually no, undo that, keep what we registered`

Why suspected: turn 3 amends an already-REGISTERED workflow (not a draft) — there is
likely no chat-side mutate-active-workflow path, so the plausible failures are a brand
new draft alongside the live one (two competing Monday buys), a false "updated!" with
no state change, or a refusal that loses the thread. Turn 4 then demands a revert to
the prior registered state — state history the draft store likely doesn't keep. Gold:
T3 pauses-or-mutates the live workflow explicitly ("updating the registered agent,
not making a second one"), T4 restores ₹3,000 with a `Changed:/Kept:` diff and
confirms exactly one armed workflow exists.

---

Cross-references: known P0s this gold targets — silent DSL amendment / notify-only
collapse (retail batch eval 2026-05-29), basket collapse regression + confirm-register
boundary (automation eval 2026-06-07), fresh-draft-on-confirm (quality eval
2026-06-09). Prior-round amend gold: `../../gan_r4_2026-06-10/GOLD/multi-turn.md`.
