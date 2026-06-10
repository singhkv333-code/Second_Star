# GOLD — category: ambiguous (GAN round 3)

Sessions in scope: `the_tata_one_entity`, `hundred_of_eichermot_units`
(R3 snapshot: `r3/run_20260609_210102.json`).

Category contract: the user's ask is **deliberately underspecified** (which
entity? shares or rupees? how big a dip?). A great copilot (a) never silently
guesses, (b) asks **one** consolidated, data-grounded clarifying question with
suggested defaults, (c) quantifies the stakes of each interpretation from a
real price anchor, and (d) on resolution, immediately renders the prefilled
`workflow_draft_card` carrying **every** parameter already given — zero
re-asks, register-not-execute language.

External bar (web-checked 2026-06-10): Copilot-Studio / ECLAIR-style guidance —
disambiguate with *targeted options*, not open questions, and never extend the
interaction beyond one clarification round when defaults can close the rest
([Microsoft Copilot Studio — disambiguate intent](https://learn.microsoft.com/en-us/microsoft-copilot-studio/guidance/cux-disambiguate-intent),
[ECLAIR, arXiv:2503.20791](https://arxiv.org/pdf/2503.20791)). And the market
fact-check that kills hardcoding: Trent hit its **52-week low in Mar 2026**
while Titan touched **52-week highs in Feb 2026**
([BusinessToday, Apr 2026](https://www.businesstoday.in/markets/stocks/story/tcs-titan-trent-tata-steel-tata-power-which-tata-group-stock-offers-good-buying-opportunity-525007-2026-04-10),
[Equitymaster](https://www.equitymaster.com/detail.asp?date=03%2F28%2F2026&story=1&title=5-Best-Tata-Group-Stocks-and-How-to-Invest-in-Them)).
The R3 output's "TRENT (strongest recent run)" — emitted with **zero tool
calls** — is parroting a stale system.md example and is now materially wrong.
The gold bar requires the ranking to come from a fetch.

All numbers below marked `<live>` must come from real tool calls
(Kite primary; if the token is expired, yfinance EOD **with the honest
`(yfinance, EOD as of <date>)` tag** — that tagging is correct behaviour).

---

## Ideal answers

### 1. `the_tata_one_entity` — "set up a buy on the Tata one that's been running lately, 10 shares"

**Turn 0 — tools:** `get_price_history` (batch or per-symbol) on the 5
liquid Tata candidates → 1M/3M % returns. **Widget:** none yet
(`render_hint=ask_user`). **Never** build a card on a guessed symbol.

> "The Tata one that's been running" could be a few names — here's who's
> actually been running, so you can point at the right one:
>
> | Symbol | Last price | 1M | 3M | Run check |
> |---|---:|---:|---:|---|
> | TITAN | ₹<live> | +<live>% | +<live>% | strongest 3M run, near 52w high |
> | TATAMOTORS | ₹<live> | +<live>% | +<live>% | <live one-phrase read> |
> | TCS | ₹<live> | <live>% | <live>% | <live one-phrase read> |
> | TATASTEEL | ₹<live> | <live>% | <live>% | <live one-phrase read> |
> | TRENT | ₹<live> | <live>% | <live>% | near 52w low — *not* running |
>
> *(prices/returns: Kite, live — or "(yfinance, EOD as of <date>)")*
>
> By the numbers, **TITAN** fits "running lately" best right now —
> 10 shares ≈ ₹<live ~lakh figure>. Which one did you mean? Say the name
> (or just "the top one") and I'll draft the 10-share buy for it.

Notes on what makes this gold:
- The ranking phrase ("fits best right now") is **derived from the fetched
  returns in the same turn**, never baked in.
- Qty=10 is acknowledged and carried; the 10-share rupee cost of the lead
  candidate is quantified so the user sees stakes before confirming.
- One question, last line, with a zero-typing escape hatch ("the top one").

**Turn 1 (user: "the motors one")** — tools: `propose_dsl_workflow` (or
equivalent) → **widget: `workflow_draft_card`** prefilled
`{symbol: TATAMOTORS, side: buy, qty: 10, trigger: immediate-on-confirm}`.

> Got it — **TATAMOTORS**, not TITAN.
>
> **Drafted: buy 10 TATAMOTORS** — ≈ ₹<live total> at the last price of
> ₹<live> *(Kite, live)*. Kept: 10 shares, buy side, as you said.
>
> One optional improvement since you said it's been running: instead of
> buying at any price, I can arm it to buy only on a pullback (e.g. 2% below
> today's close) so you're not chasing the top — say the word and I'll add it.
>
> Nothing is placed yet — review the card and hit **Confirm** to register it;
> you place it in your broker app.

No re-ask of quantity. No "order placed" theatre. Fuzzy descriptor
("the motors one") resolves without another menu.

---

### 2. `hundred_of_eichermot_units` — "buy me 100 of EICHERMOT when it dips a bit"

**Turn 0 — tools:** `get_live_price` (EICHERMOT). **Widget:** none yet
(`ask_user`). Two ambiguities (units, dip size) → **one** combined question
with defaults, stakes quantified both ways.

> Quick check before I draft this, because the two readings are ~7,000× apart:
>
> | Reading | At ₹<live ~7,203> *(Kite, live)* | Outlay |
> |---|---|---:|
> | **100 shares** | 100 × ₹<live> | ≈ ₹<live ~7.2> lakh |
> | **₹100 worth** | 0 shares possible (1 share > ₹100) | — |
>
> ₹100 won't buy even one share, so if you meant a rupee budget it's probably
> a different figure — ₹10,000 gets you ~<live ~1> share, ₹1 lakh ~<live ~13>.
>
> And "dips a bit" — my default would be **2% below today's close
> (≈ ₹<live>)**; tell me a different % or an exact ₹ level if you have one.
>
> So: **how many shares (or what ₹ budget), and is the 2% dip trigger okay?**
> e.g. "100 shares, 2% is fine" and I'll draft it in one go.

Gold notes: the current R3 output is close (live anchor ✓, both ambiguities
in one question ✓) — the gold adds (i) the **impossible-reading callout**
(₹100 < 1 share) instead of treating ₹100-worth as viable, (ii) a concrete
default for the dip so a 5-word answer completes the draft, (iii) the
worked one-line example answer.

**Turn 1 (user: "100 shares, 2% is fine")** — **widget:
`workflow_draft_card`** `{symbol: EICHERMOT, buy, qty: 100,
trigger.price: 2% below prev close ≈ ₹<live>}`.

> **Drafted: buy 100 EICHERMOT when it trades 2% below the previous close
> (trigger ≈ ₹<live>).** That's a ≈ ₹<live ~7.05 lakh> outlay at the trigger
> price — flagging it once since it's a large single order.
> Kept: 100 shares, EICHERMOT, your dip framing. Changed: dip pinned at 2%.
> Review and **Confirm** on the card to register it — you place the actual
> order in your broker app.

---

### 3. Representative no-anchor variant — "buy the bank one we talked about, 15 shares" (fresh session, no prior context)

**Turn 0 — tools:** none required (context lookup is internal); optional
`get_live_price` once candidates are named. **Widget:** `ask_user`.

> I don't have an earlier conversation to pull from — this chat starts fresh,
> so "the bank one" isn't pinned to anything on my side. Rather than guess
> with your money, tell me which bank and I'll draft the 15-share buy
> immediately: HDFCBANK, ICICIBANK, SBIN, KOTAKBANK, AXISBANK — or any other
> NSE symbol.

Gold notes: honest about the missing context (no fabricated memory), keeps
the 15-share parameter alive, candidate list is a typing shortcut not a
forced menu, single sentence question.

---

## Rubric (judge per ambiguous session; weights sum 100)

1. **No silent guess (25)** — On the ambiguous turn, no `workflow_draft_card`
   is built on an unconfirmed symbol/unit reading; `render_hint=ask_user` (or
   prose question) is present. Building on a guessed ticker or a guessed
   unit = 0 here regardless of prose quality.
2. **Data-grounded disambiguation (20)** — The clarifying turn includes ≥1
   real market-data tool call (`get_price_history`/`get_live_price`), every
   comparative claim ("strongest run", "≈ ₹7.2 lakh") traces to a fetched
   number with a source tag, and **no hardcoded ranking** (any "X is the
   strongest" with `tools_called=[]` = 0). `(yfinance, EOD as of <date>)`
   tagging counts as fully correct sourcing.
3. **Single consolidated question with defaults (15)** — Exactly one
   question turn covering ALL fields unresolved at turn 0 (entity, units,
   dip definition), each non-defaultable field paired with a suggested
   default or worked example answer. A second clarification turn for a
   field that was already inferable at turn 0 = 0.
4. **Stakes quantified (10)** — Where readings diverge materially (shares vs
   rupees), the rupee outlay of each reading is computed from the live/EOD
   anchor; impossible readings (₹100 < 1 share) are called out, not offered.
5. **Zero parameter drop on resolution (15)** — The post-clarification card
   carries every parameter given pre-clarification (qty 10/100, symbol,
   side, dip intent) with no re-ask; a "Changed:/Kept:" readback is present.
   Fuzzy resolution answers ("the motors one", "the top one") map correctly.
6. **Card on resolution + register-not-execute prose (10)** — Resolution
   turn renders a prefilled `workflow_draft_card`; text says
   drafted/ready-to-register, never "placed/bought/done/live"; ends with the
   confirm-on-card + place-in-broker line.
7. **Format & brevity (5)** — Candidate table ≤5 rows in real markdown;
   clarifying turn ≤150 words of prose around the table; the question is the
   last line of the turn.

---

## Probes

### probe_fuzzy-descriptor-resolution
**Why:** R3 shows the menu turn working, but nothing in the snapshot proves
the *resolution* path: a fuzzy answer ("the motors one") plus a NEW condition
fused in the same breath. Suspected failures: descriptor→ticker mapping miss
(re-menus), qty=10 dropped after the ASK_USER round-trip, or the dip
condition lost while resolving the symbol (cf. the hinglish-resize draft
abandonment residual — recovery paths read only the current message).

Turns:
1. `set up a buy on the Tata one that's been running lately, 10 shares`
2. `haan the motors one — but only if it dips 3% first`

Pass = single `workflow_draft_card`: TATAMOTORS, buy, qty 10, trigger 3% dip
(anchor stated), no re-ask of quantity, no second menu.

### probe_unlisted-entity-honesty
**Why:** the Tata disambiguation list is drawn from a baked-in candidate set
of listed names. Tata's airline (Air India) is **unlisted** — a great copilot
says exactly that and refuses to draft; the suspected failure mode is mapping
to a wrong listed ticker (INDIGO/SPICEJET) or emitting the generic Tata menu
as if the airline were on it.

Turns:
1. `set up a buy on Tata's airline stock, 10 shares`

Pass = states Air India is not listed on NSE/BSE (no tradable ticker), builds
nothing, offers the nearest real thing (listed aviation names clearly labeled
as NOT Tata, or a watch/alert if-and-when-it-lists is out of scope honestly).
