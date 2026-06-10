# GOLD — multi-turn (GAN round 3)

Category: conversations where the value is in turns 2..N — amendments, confirmations,
resizes, teach-backs, analysis→build carryover. The single biggest differentiator vs a
single-shot bot is **state**: every later turn must prove it still holds the full draft /
analysis context, and every change must be visible as a diff.

Best-in-class bar (from current copilot/terminal practice): assistants carry prior
messages + variables across exchanges and pre-fill forms/cards from the dialogue rather
than re-asking ([Copilot Studio multi-turn guidance](https://dellenny.com/multi-turn-conversations-and-context-management-in-copilot-studio/),
[AI copilot memory systems](https://www.nadcab.com/blog/ai-copilot-memory-systems-automation));
trading chatbots require explicit confirmation before anything fires and act as co-pilots
that pre-fill the order form from the conversation ([Digiqt — chatbots in stock trading](https://digiqt.com/blog/chatbots-in-stock-trading/)).
Pivot already has the best amend pattern in the set (`Changed:/Kept:` diff) — gold is to
make that contractual on *every* mutation turn, never lose the draft on a tool failure,
and make the terminal "register it" turn do real work instead of bouncing the user to a
button.

Numbers marked `<live>` are placeholders for real tool values at run time. Honest
`(yfinance, EOD, as of <date>)` tagging when the Kite token is expired is correct
behaviour, not a defect.

---

## Ideal answers

### 1. `amend_qty_then_confirm_register` (NESTLEIND RSI agent → resize → register)

**T0 — "build an agent that buys NESTLEIND when RSI drops under 30"**

Asking for size is correct (never default to 1 share) — but the gold ask is *grounded*:
it quotes the live price and current RSI so the user can size intelligently, and it
already restates the parsed rule so the user sees nothing was dropped.

> Got the rule — **buy NESTLEIND when RSI(14) drops below 30** (checked on daily closes).
>
> One thing I won't guess: position size. NESTLEIND is at **₹<live>2,365** right now
> (<live>Kite/yfinance EOD, as of <live>date), and its RSI(14) is currently **<live>47.2** — so
> this would arm and wait, not fire immediately.
>
> Tell me either:
> - a share count — e.g. *"5 shares"* (≈ ₹<live>11,825 at today's price), or
> - a rupee budget — e.g. *"₹10,000 ka"* and I'll size it at trigger time.

Widget: none yet (ask_user) — but the ask must carry the parsed trigger back, and the
price/RSI context must come from a real tool call, not memory.

**T1 — "make it 8 shares"**

> Drafted. Here's exactly what's on the card:
>
> | Field | Value |
> |---|---|
> | Symbol | NESTLEIND (NSE) |
> | Trigger | RSI(14) < 30, evaluated on daily close |
> | Action | Buy **8 shares**, market order, CNC |
> | Approx outlay | ~₹<live>18,920 at today's ₹<live>2,365 |
> | Current RSI(14) | <live>47.2 — agent arms and waits |
>
> **Changed:** size set to 8 shares. **Kept:** NESTLEIND, RSI(14) < 30 trigger.
>
> This registers the order when it fires — you confirm the actual buy in your broker
> app; nothing executes on its own. Ready to register whenever you say so, or edit any
> field on the card.

Widget: `workflow_draft_card` — `trigger.indicator{symbol:NESTLEIND, indicator:rsi,
period:14, op:<, value:30}` + `action.place_order{buy, qty:8, market, CNC}`. Prose
numbers must match the card exactly.

**T2 — "looks good, go ahead and register it"**

The user gave explicit consent in chat. Gold = the assistant **performs the
registration** (workflow activation tool call) and reads back the armed state. A dead
turn (`tools_called=[]`, "click Save & activate") fails the bar — the current R3 output
does exactly this.

> Done — **registered and armed.** ✅
>
> - **Watching:** NESTLEIND RSI(14), evaluated on each daily close (next check: today
>   ~<live>15:45 IST).
> - **When it fires:** a buy order for 8 shares is *registered* for you — you'll get a
>   notification and confirm the order in your broker app. Pivot never auto-executes.
> - **Current state:** RSI(14) = <live>47.2, so it's waiting — no order pending.
> - You can pause or edit it any time from the card or by telling me.

Widget: the same `workflow_draft_card` flipped to an **active/registered** state (status
badge), or an honest fallback: if chat-side activation genuinely isn't wired, the *only*
acceptable alternative is a single, explicit "I can't flip it to active from chat yet —
tap **Save & activate** on the card above and it's live" + the same armed-state readback.
Never imply registration happened when it didn't, and never leave the turn empty.

---

### 2. `hinglish_then_resize_notional` (TATAMOTORS dip-buy → "12000 ka kharido")

**T0 — "TATAMOTORS 5% gir jaye to 15 share kharid lo aur 7% upar bech do"**

> Ho gaya — draft ready:
>
> | Field | Value |
> |---|---|
> | Symbol | TATAMOTORS (NSE) |
> | Entry | close ≤ 95% of previous close (5% dip) |
> | Buy | 15 shares, market, CNC (~₹<live>10,690 @ ₹<live>712.40) |
> | Exit | sell when unrealised profit ≥ 7% |
>
> Abhi TATAMOTORS **₹<live>712.40** pe hai (<live>yfinance EOD, as of <live>date), to 5%
> dip ka matlab entry zone **~₹<live>676.80**. Order register hota hai — execute aap apne
> broker app me confirm karke karte ho. Card pe koi bhi field edit kar sakte ho.

Widget: `workflow_draft_card` with the dip-entry compound trigger + buy 15 + 7%
exit_compound (exactly what R3 already builds — the gold adds the readback table, the
computed entry level, and a Hinglish-mirroring register).

**T1 — "actually 15 share nahi, 12000 ka kharido"** *(this is the R3 FAIL: live quote
failed → draft abandoned → "Tell me the NSE ticker")*

Gold behaviour under the same tool failure: the draft holds the symbol, so recovery uses
**the draft's symbol and the last close** — the draft is never abandoned and the ticker
is never re-asked.

> Updated — ab size **₹12,000 ka budget** hai, fixed 15 share nahi.
>
> | Field | Value |
> |---|---|
> | Buy | **₹12,000 notional** → ~**<live>16 shares** @ last close ₹<live>712.40 (live
> quote abhi nahi mila, isliye <live>date ka close use kiya — exact share count trigger
> ke time ke price pe recompute hoga) |
> | Entry | 5% dip from previous close — unchanged |
> | Exit | +7% profit — unchanged |
>
> **Changed:** 15 shares → ₹12,000 budget (~<live>16 shares indicative).
> **Kept:** TATAMOTORS, 5% dip entry, 7% profit exit.
>
> Register hota hai, aap activate/confirm karte ho.

Widget: same `workflow_draft_card`, `action.place_order` now carrying
`notional_inr: 12000` (or recomputed `quantity` with the basis price named in prose).
Hard requirements: (a) no re-ask for the ticker, (b) draft retained with all prior
params, (c) the price-source caveat stated honestly, (d) `Changed:/Kept:` diff present.

---

### 3. `analysis_then_build_followup` (JSWSTEEL read → "build me a dip-buy agent")

T0 (the sectioned `## Snapshot / ## Technicals / ## View` analysis with the MA table) is
already at bar in R3. The gold delta is **T1 — "ok build me an agent that buys 8 shares
whenever it dips 3% from the day's high"** — the build turn must *use* the analysis it
just gave, not amnesia-build a generic card:

> Drafted — and it connects to the read above:
>
> | Field | Value |
> |---|---|
> | Symbol | JSWSTEEL (NSE) |
> | Trigger | price ≤ 97% of the **same day's high** (intraday dip) |
> | Action | Buy **8 shares**, market, CNC (~₹<live>10,070 @ ₹<live>1,258.80) |
>
> Two things worth knowing before you arm it:
> - **"Day's high" is intraday** — on a volatile day this can fire early in the session.
>   With JSWSTEEL consolidating below its 20-DMA (₹<live>1,286) and RSI at <live>41,
>   3% intraday dips are plausible in the current tape, so expect this to trigger more
>   often than a 3%-from-close rule would.
> - It re-arms each day; if you want at-most-one-buy-per-week or a stop loss after entry,
>   say so and I'll add it.
>
> **Registers only** — you activate from the card, and each fired buy still needs your
> confirmation. Want me to backtest how often this rule would have fired over the last 6
> months before you turn it on?

Widget: `workflow_draft_card` with the day's-high compound trigger + buy 8 (R3's parse is
already correct). The gold adds: outlay estimate, the carryover sentence referencing the
T0 numbers (20-DMA, RSI), the intraday-basis caveat, and the proactive backtest offer
(a real Pivot capability — never a fabricated fire-count).

---

## Rubric

Score each session 0–10; weights sum to 100. A criterion is judged from the *actual*
response text + `tools_called` + `card_digest` of the relevant turns.

| # | Criterion (mechanical check) | Weight |
|---|---|---:|
| 1 | **Context retention across turns.** No turn ≥1 re-asks for, drops, or mutates any in-scope parameter (symbol, qty/budget, trigger, exit) that wasn't explicitly changed. Pronouns/ellipsis ("it", "the first one", "same thing") resolve to the right draft. Any re-ask of a known symbol = automatic FAIL on this criterion. | 25 |
| 2 | **Amend diff contract.** Every mutation turn carries an explicit `Changed: … Kept: …` (or equivalent two-part) readback enumerating what moved and what held; the readback matches the card_digest values exactly. | 15 |
| 3 | **Draft persistence under failure.** A tool failure mid-amendment (e.g. `get_live_price` empty) never abandons the active draft: recovery retries/falls back on the draft's symbol or last close, keeps all prior params, and states the data caveat. The context-amnesiac "tell me the NSE ticker" while a draft is active = 0/15. | 15 |
| 4 | **Terminal confirm turn does real work.** On "register it / go ahead", either a registration/activation tool is called and the armed state is read back (what's watched, evaluation cadence, what happens on fire, current indicator value), or — only if activation is genuinely unwired — one honest, explicit click instruction *plus* the same armed-state readback. `tools_called=[]` with a bare "click the button" = ≤5/15. | 15 |
| 5 | **No execution theatre.** Pre-confirmation text never claims "placed/registered/active/live/running" unless the corresponding state change actually happened that turn; drafted states say "drafted / ready to register"; "registers, you confirm in your broker app" discipline appears at least once per session. | 10 |
| 6 | **Teach turns are grounded and corrective.** "I don't understand / why X not Y" turns: (a) explicitly correct any false premise ("nothing has been set up yet"), (b) answer with the *actual* numbers from earlier turns (e.g. the P/B–ROE pairs from the table), (c) ≥120 words with one concrete worked example, (d) end with a forward step. | 10 |
| 7 | **Register/language mirroring + card-prose consistency.** Hinglish in → Hinglish-friendly out (not formal-English-only); every number in prose (qty, levels, outlay) is identical to the card_digest / tool payload; data source + as-of tagged when on fallback. | 10 |

Pass ≥ 7.5 weighted; Partial 5.0–7.4; Fail < 5.0.

---

## Probes

Realistic multi-turn asks a great copilot handles that Pivot likely cannot yet, based on
the R3 snapshot + summary.

### probe_register-then-status

Why suspected: R3 `amend_qty_then_confirm_register` T2 ran `tools_called=[]` and punted
to "Click Save & activate"; the automation-eval work order lists a chat-side
`register_workflow` tool as still-open P0. Turn 3 stresses the post-registration status
contract — without real armed-state introspection the answer will be vague or fabricated
(evaluation cadence, current RSI, pending-order state).

1. `make an agent that buys 10 TATAPOWER when RSI(14) goes under 30`
2. `register it for me right now`
3. `is it actually live? when exactly do you check the RSI and what happens the moment it fires?`

### probe_two-drafts-edit-first

Why suspected: per-session chat state holds a single *active* draft (independent prompts
evict it — see per-session isolation memory), so a second build likely evicts the first;
turn 3's reference to "the INFY one" should then either amend the wrong (WIPRO) draft,
re-ask for context, or rebuild from scratch with dropped params. Gold behaviour is two
addressable drafts and a correctly-targeted `Changed:/Kept:` amend.

1. `buy 5 INFY when it dips 3% from the previous close`
2. `nice, make the same thing for WIPRO also`
3. `change the INFY one to 8 shares, WIPRO wala same rehne do`

---

Sources: [Multi-Turn Conversations and Context Management in Copilot Studio](https://dellenny.com/multi-turn-conversations-and-context-management-in-copilot-studio/), [How AI Copilot Memory Systems Improve Automation 2026](https://www.nadcab.com/blog/ai-copilot-memory-systems-automation), [Chatbots in Stock Trading — Digiqt](https://digiqt.com/blog/chatbots-in-stock-trading/)
