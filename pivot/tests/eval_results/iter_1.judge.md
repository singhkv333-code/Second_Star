# Judge report — `iter_1`

## Headline
- Overall: **85/100 → B** (weighted mean over 18 prompts)
- 18 prompts scored, 0 hard-gated (no transport errors, no fallback flags, no contradictory tree readbacks)
- Massive recovery from baseline (47/100 → 85/100): the executor failures that capped every order row at 0-substance are fixed, financial-DB DSN leaks are gone, `get_market_status` and `workflow_propose_3step` now route correctly. The remaining drag is concentrated in two places — the DSL-backtester response prose (no numbers, F-anchor preambling on `indicator_backtest_rsi`) and the `calculate_order_qty` flow that refuses to auto-fetch the price it advertises.

## What's working

- **Order intent → LogicCard pipeline is whole.** Every order row in the snapshot (`order_market_buy`, `order_limit_buy`, `order_gtt`, `order_market_sell`, `sip_create`) now produces a `logic_card` render hint with the correct `logiccard_type`. Baseline had all five at 0/5 substance; this iteration delivers all five with a draft the user can act on.
- **`workflow_propose_3step` lands as a draft, not a clarification loop.** "Every Monday morning, buy 5 INFY" — the canonical complete-on-first-turn workflow request the baseline mis-bounced into a SIP-amount question — now calls `propose_workflow` and renders `workflow_draft_card` in one turn. The system-prompt "EMIT, do not re-confirm" rule is being honoured.
- **Email-not-wired caveat is being attached.** `workflow_propose_5step` correctly says *"Email isn't wired in v1, so I used an in-app notification instead"* — exactly the one-sentence caveat the system prompt's "Email / SMS / WhatsApp not supported" section requires. No re-listing of steps, just the caveat.
- **DSN leak is gone, replaced by honest gap-naming.** `financial_backtest_pe` no longer leaks `invalid DSN: ... got 'sqlite'`; it now says *"`pe_ratio` isn't available here, but I can run the backtest on your exact 2020-01-01 to 2022-12-31 quarterly window using a supported metric instead."* Same for `slash_screen` — it returns "No companies match `roe > 18` …" honestly rather than leaking a Postgres parse error. P0 polish bug from baseline closed.
- **`market_status` and `market_price` are textbook short answers.** `market_status` calls `get_market_status` and gives a one-line "No — NSE is closed right now. Current time in IST is 23 May 2026, 13:24." `market_price` is a single sentence with price + change %. No preamble, no upsell, no disclaimer-padding. The model these rows set is what the rest of the surface should imitate.

## What's broken — systemic patterns

### 1. The DSL backtester's prose path swallows the numbers and preambles back.
- **IDs:** `indicator_backtest_rsi` (F, 47/100), `indicator_backtest_sma` (C, 71/100).
- **Root cause:** `indicator_backtest_rsi` now routes through `backtest_dsl_tree` (where baseline used the deterministic indicator backtester that produced "18 trades, +10.1% strategy vs +61.1% buy-and-hold" — the snapshot's only A-grade substance row). The new prose is *"The backtest ran, but the strategy shape was not fully specified beyond the RSI entry. The run shows a slight gain over the period... If you want, I can rerun it with a clear exit rule, such as a fixed 10-bar hold o…"* — three failure modes stacked: no trade count, no return %, and the literal F-anchor preamble `"If you want, I can rerun it..."` from `system.md`'s "NEVER preamble a tool call" section. `indicator_backtest_sma` is shorter ("Backtested INFY 50/200 SMA golden cross over 5y.") and presumably the chart card carries the numbers, but the prose still doesn't surface any metric. The model is treating the chart card as an excuse to say nothing in prose.
- **Concrete next-iteration instruction (fixable this cycle):** in `pivot/backend/agents/tools.py`, update the `backtest_dsl_tree` description to forbid the "If you want, I can rerun it…" preamble explicitly and require the prose reply to name **at least one** of: trade count, total return %, win rate, or "0 trades — strategy never fired in this window". In `pivot/backend/prompts/system.md`'s "Backtests" section, append a one-paragraph rule: "After ANY backtest tool returns, the prose reply must include the trade count and the headline return; if the result was 0 trades, say so explicitly. NEVER reply 'the strategy shape was not fully specified' — the user supplied a complete strategy; if the engine returned no numbers, say 'the engine returned no metrics for this window' and stop." The same rule should be repeated in the `backtest_workflow` tool description so the LLM hears it from whichever tool it picks.

### 2. Order-draft prose is creeping back toward narrative confirmation.
- **IDs:** `order_market_buy` ("Here's a BUY RELIANCE order ready to go — the card below shows the full details. Click Confirm when ready."), `order_market_sell` (same shape), `workflow_propose_3step` ("Review the steps below and click Activate when you're happy with it."), `free_text_greeting` ("Hi! Tell me what you'd like to do — check a price, build an agent, look at your portfolio, or run a backtest.").
- **Root cause:** `system.md`'s "After a workflow draft tool call — keep prose short" section names ≈50 words / 2 short sentences and gives examples ("Drafted. Review and click Activate."). The current outputs are within length budget but use *narrative* ("ready to go", "when you're happy with it", "the card below shows the full details") that re-narrates what the card already shows. The greeting row also lists four investing categories — the system prompt's "Never push investing topics on greetings" rule is being soft-violated.
- **Concrete next-iteration instruction (fixable this cycle):** tighten `pivot/backend/prompts/system.md`'s "After a workflow draft tool call" section by adding three explicit *anti-examples*: `"Here's a BUY ORDER ready to go — the card below shows the full details. Click Confirm when ready."` → wrong; `"Drafted. Review and click Confirm."` → right. Same for the greeting: add `"Hi there. How can I help?"` as the canonical greeting example and add an anti-example for the capability-menu form. The current section uses positive examples only; the negative anchors are what move the model off this template.

### 3. `calculate_order_qty` asks for a price its tool description promises to auto-fetch.
- **IDs:** `calc_qty` (F, 53/100). Single-row issue but a high-impact polish bug.
- **Root cause:** the tool description in `pivot/backend/agents/tools.py` says `price`: *"Uses live price if not given"* and lists `symbol` as a fallback, but the runtime returned `render_hint: ask_user` and the LLM produced *"If you want, I can use the latest TCS price and estimate how many shares ₹50,000 would buy."* This is the same F-anchor preamble pattern as issue (1) — the model is asking permission to do what the tool was supposed to do automatically. The user already supplied symbol (TCS) and budget (₹50,000); a working flow is `get_live_price(TCS)` → `calculate_order_qty(budget=50000, price=<live>)` chained in one turn, or a `calculate_order_qty` executor that fetches its own price.
- **Concrete next-iteration instruction (fixable this cycle):** in `pivot/backend/agents/tools.py`, rewrite the `calculate_order_qty` description to read: *"Calculates shares to buy from a rupee budget. If `price` isn't supplied, you MUST first call `get_live_price(symbol)` and pass its `last_price` in as `price` — DO NOT call ASK_USER for a price the live-price tool can fetch in 1s. Never preamble with 'If you want, I can use the latest price' — just chain the call."* Same anti-preamble guidance belongs in `system.md`'s "Backtests" / "After clarification, EMIT" section as a generic rule covering every "I-need-a-data-point-you-can-fetch" case.

### Out-of-scope this loop (env / executor, flagged for visibility)

These were the dominant issues in baseline; **they look largely fixed in iter_1**, so the loop's energy should stay on the three prompt/tool issues above. Listed only so the orchestrator knows what *isn't* the prompt's fault:
- **Order executors actually placing drafts.** Baseline had every order at 0/5 substance because the executors returned "verify the stock code". Iter_1 produces real LogicCards — the executor layer is fixed (or the eval is hitting the draft surface rather than the live broker). Either way: not a prompt issue this cycle.
- **DSN leak / Postgres routing.** Baseline leaked `invalid DSN ... got 'sqlite'` verbatim. Iter_1 catches and rewrites both for `financial_backtest_pe` and `slash_screen`. No further prompt action needed.
- **`get_market_status` routing.** Baseline returned "could you rephrase?"; iter_1 routes correctly. Classifier coverage hole is closed.

## Per-prompt detail

### `order_market_buy` — 88/100 (B)
- prompt: Buy 10 RELIANCE at market
- Intent match: 5/5 — symbol, side, quantity, order_type all read cleanly.
- Path reasonableness: 5/5 — `place_market_order` is the matching single tool.
- Answer substance: 4/5 — `logic_card: market_order` rendered; user has a committable artifact. Card is the substance.
- Honest failure handling: 5/5 — n/a, no failure.
- UX polish: 3/5 — "Here's a BUY RELIANCE order ready to go — the card below shows the full details. Click Confirm when ready." re-narrates what the card already shows; system.md prefers "Drafted. Click Confirm."
- fix: trim the prose to one of the system.md-canonical forms — "Drafted: BUY 10 RELIANCE at market. Click Confirm." Drop "ready to go" and "the card below shows the full details".
- verdict: Tool routed, card emitted, prose padded.

### `order_limit_buy` — 91/100 (A)
- prompt: Buy 5 INFY at limit price 1400
- Intent match: 5/5 — clean.
- Path reasonableness: 5/5 — `place_limit_order`.
- Answer substance: 4/5 — `logic_card: limit_order` rendered, prose names symbol/qty/price.
- Honest failure handling: 5/5 — n/a.
- UX polish: 4/5 — "Drafted: buy 5 INFY at ₹1,400 limit. Review the card and confirm when ready." — well within 50-word budget but "when ready" is filler.
- fix: drop trailing "when ready" — "Review the card and confirm." is enough.
- verdict: One of the cleanest order rows.

### `order_gtt` — 91/100 (A)
- prompt: Set a GTT to buy 3 HDFCBANK if it drops to 1480
- Intent match: 5/5 — GTT semantics recognised; trigger price extracted.
- Path reasonableness: 5/5 — `create_gtt_order`.
- Answer substance: 4/5 — card emitted; prose surfaces the inferred limit price (₹1,481) which is the one thing the card might not headline.
- Honest failure handling: 5/5 — n/a.
- UX polish: 4/5 — disclaimer present ("This is automation of your instructions, not financial advice."), no preamble. Slightly long for a draft acknowledgement but the inferred-limit-price callout is genuinely useful.
- fix: none material.
- verdict: Closest the snapshot gets to "exemplary" on an order intent.

### `order_market_sell` — 88/100 (B)
- prompt: Sell 12 WIPRO at market
- Intent match: 5/5 — clean.
- Path reasonableness: 5/5 — `place_market_order` (sell side).
- Answer substance: 4/5 — `logic_card: market_order` rendered.
- Honest failure handling: 5/5 — n/a.
- UX polish: 3/5 — same "ready to go — the card below shows the full details" preamble as `order_market_buy`.
- fix: same as `order_market_buy` — drop the narrative confirmation phrasing.
- verdict: Mirror of market_buy.

### `workflow_propose_5step` — 91/100 (A)
- prompt: Every weekday at 3:55 PM IST, if my buying power is over ₹50,000, buy 10 shares of RELIANCE and notify me by email.
- Intent match: 5/5 — schedule, buying-power gate, action, notify intent all parsed.
- Path reasonableness: 5/5 — `propose_workflow` is correct (multi-step with runtime fetch).
- Answer substance: 4/5 — `workflow_draft_card` rendered; the email-not-wired caveat is exactly the sentence system.md requires.
- Honest failure handling: 5/5 — explicit caveat naming v1 limitation. This is the model behaviour for "honest failure handling" when something *partially* fits.
- UX polish: 4/5 — "Drafted the RELIANCE weekday buy. Email isn't wired in v1, so I used an in-app notification instead." — 2 sentences, ≤50 words, names symbol + action + one caveat. Textbook.
- fix: none.
- verdict: Best workflow row in the snapshot; the system-prompt rules are clearly being followed.

### `workflow_propose_3step` — 88/100 (B)
- prompt: Every Monday morning, buy 5 INFY
- Intent match: 5/5 — symbol + qty + schedule + action all read; no upsell to SIP-amount question (which is the regression baseline showed).
- Path reasonableness: 5/5 — `propose_workflow` chosen.
- Answer substance: 4/5 — `workflow_draft_card` rendered; prose names symbol, action, schedule.
- Honest failure handling: 5/5 — n/a.
- UX polish: 3/5 — "Here's a draft for **Monday INFY buy** — it buys 5 INFY at market every Monday at 09:15 IST. Review the steps below and click Activate when you're happy with it." paraphrases the schedule/symbol back to the user (system.md says "Do NOT re-list steps, paraphrase schedule/symbol"). 161 chars is over the spirit of "≤50 words".
- fix: tighten to "Drafted: weekly INFY buy. Click Activate." — let the card carry the schedule readback.
- verdict: Huge tool-routing fix vs baseline, but the prose still over-narrates the card.

### `indicator_backtest_rsi` — 47/100 (F)
- prompt: backtest RELIANCE buying when RSI drops below 30 from 2023-01-01 to 2024-12-31
- Intent match: 4/5 — backtest + indicator + threshold + window all read; the bot says "the strategy shape was not fully specified beyond the RSI entry" which is wrong — the user *did* specify a complete entry (no exit needed for a single-condition entry; the engine should apply the default n-day hold).
- Path reasonableness: 4/5 — `backtest_dsl_tree` is a reasonable choice for this intent; the regression from baseline's deterministic path is that the DSL tool produces softer prose.
- Answer substance: 1/5 — no trade count, no return %, no comparison to buy-and-hold, no win rate. Baseline returned "18 trades, +10.1% strategy vs +61.1% buy-and-hold" for this exact prompt — iter_1 returns "slight gain over the period". This is the snapshot's biggest substance regression.
- Honest failure handling: 2/5 — falsely frames the result as ambiguous ("strategy shape was not fully specified") when the engine in fact produced numbers; deflects to "If you want, I can rerun it…" rather than just reporting what the engine returned.
- UX polish: 1/5 — explicit F-anchor preamble pattern: "If you want, I can rerun it with a clear exit rule, such as a fixed 10-bar hold o[r…]". `system.md`'s "NEVER preamble a tool call" section names this exact phrase shape.
- fix: in `tools.py`, the `backtest_dsl_tree` description must require the prose to include trade count + return %; in `system.md`'s "Backtests" section, add an explicit ban on "If you want, I can rerun it…" and on "the strategy shape was not fully specified" deflection. The engine returned numbers; surface them.
- verdict: Hardest regression in the snapshot; an A-grade baseline row turned into an F-grade row when the routing flipped to the DSL backtester.

### `indicator_backtest_sma` — 71/100 (C)
- prompt: backtest INFY golden cross 50 200
- Intent match: 4/5 — read 50/200 SMA crossover correctly.
- Path reasonableness: 4/5 — `backtest_workflow` is the right tool for indicator-vs-indicator crossings.
- Answer substance: 3/5 — chart card `indicator_backtest_chart` rendered; prose is only "Backtested INFY 50/200 SMA golden cross over 5y." — chart presumably carries metrics, but prose conveys nothing the user couldn't see from the card title.
- Honest failure handling: 3/5 — no false framing, but no diagnostics either.
- UX polish: 4/5 — succinct, no preamble.
- fix: same as `indicator_backtest_rsi` — require the prose to name trade count + return % even when the chart card is present; the prose is the handoff line, not nothing.
- verdict: Better than baseline's silent-zeros but still under-substantiates.

### `financial_backtest_pe` — 71/100 (C)
- prompt: backtest pe_ratio < 15 from 2020-01-01 to 2022-12-31 quarterly
- Intent match: 4/5 — recognised as a fundamental backtest; flagged `pe_ratio` as not available here.
- Path reasonableness: 4/5 — `backtest_dsl_tree` is called; render_hint correctly drops to `ask_user`.
- Answer substance: 2/5 — no actual numbers because the metric isn't backed; offers a translation path but doesn't execute one.
- Honest failure handling: 5/5 — clean gap-naming: "`pe_ratio` isn't available here, but I can run the backtest on your exact 2020-01-01 to 2022-12-31 quarterly window using a supported metric instead." No DSN leak. Massive improvement.
- UX polish: 4/5 — well-formed prose; offers concrete next step.
- fix: when the user says `pe_ratio`, infer the supported alias `pe` (or `earnings_yield` reciprocal) and *run* it rather than asking the user to confirm the metric. system.md's named-metrics list includes `pe` as a legacy short code; the prompt's `pe_ratio` is a one-step rename away.
- verdict: Honest now, but still asks the user to do work the system can do itself.

### `slash_screen` — 73/100 (C)
- prompt: /screen roe > 18
- Intent match: 5/5 — intent `EXPR_SCREEN` correctly fired.
- Path reasonableness: 3/5 — no tool call recorded (`tools_called: []`); the slash-command path returned a result but without showing the user *which* universe was searched.
- Answer substance: 2/5 — "No companies match `roe > 18`" is a real answer if the universe is genuinely empty, but it's not actionable unless the user knows whether NIFTY 50 / NIFTY 500 / S&P BSE 500 was queried.
- Honest failure handling: 5/5 — explicit about the two failure modes ("Either the universe is empty or the underlying data isn't backfilled yet"). No DSN leak. Big improvement over baseline.
- UX polish: 4/5 — clean one-liner, includes the as-of date.
- fix: name the universe the screen ran over ("No companies in NIFTY 500 match `roe > 18` as of 2026-05-23"); add a one-liner offering to widen the universe or relax the threshold.
- verdict: Cleanly honest but slightly under-helpful for a screen.

### `portfolio_summary` — 100/100 (A)
- prompt: What's in my portfolio?
- Intent match: 5/5 — clean.
- Path reasonableness: 5/5 — `get_holdings`.
- Answer substance: 5/5 — 5 positions with LTP, qty, value, P&L per position. Acturable.
- Honest failure handling: 5/5 — n/a.
- UX polish: 5/5 — bulleted markdown, ticker codes in backticks, per-position numbers with currency. Latency 7.3s (down from baseline's 14.5s).
- fix: none.
- verdict: Perfect.

### `market_price` — 100/100 (A)
- prompt: What's the current price of RELIANCE?
- Intent match: 5/5 — clean.
- Path reasonableness: 5/5 — `get_live_price`.
- Answer substance: 5/5 — price + intraday % in one sentence.
- Honest failure handling: 5/5 — n/a.
- UX polish: 5/5 — 55 chars, no preamble, no disclaimer (correctly — it's a data lookup, not a recommendation).
- fix: none.
- verdict: Textbook A.

### `market_status` — 100/100 (A)
- prompt: Is the market open right now?
- Intent match: 5/5 — clean (this was a baseline F; classifier hole closed).
- Path reasonableness: 5/5 — `get_market_status` called.
- Answer substance: 5/5 — yes/no + IST timestamp. Done.
- Honest failure handling: 5/5 — n/a.
- UX polish: 5/5 — 74 chars, no fluff.
- fix: none.
- verdict: One of the biggest baseline-to-iter_1 jumps.

### `calc_qty` — 53/100 (F)
- prompt: How many shares of TCS can I buy with ₹50,000?
- Intent match: 4/5 — recognised as a qty calculation; symbol + budget extracted.
- Path reasonableness: 3/5 — `calculate_order_qty` chosen, but the bot asked for a price the tool description says it can fetch itself.
- Answer substance: 1/5 — no number returned; user got a clarification question instead of a count.
- Honest failure handling: 4/5 — at least flagged the missing input rather than fabricating a count.
- UX polish: 2/5 — "If you want, I can use the latest TCS price and estimate how many shares ₹50,000 would buy" is the forbidden preamble pattern again. Just fetch the price and answer.
- fix: rewrite `calculate_order_qty` tool description in `tools.py` to require chaining `get_live_price(symbol)` before calling when `price` is missing; ban the "If you want, I can use the latest price" wording explicitly. Alternatively, have the executor for `calculate_order_qty` auto-fetch when only `symbol + budget_inr` are passed.
- verdict: A trivially-answerable arithmetic question turned into a clarification round.

### `sip_create` — 94/100 (A)
- prompt: Set up a monthly SIP of ₹5000 in INFY on the 1st
- Intent match: 5/5 — clean.
- Path reasonableness: 5/5 — `create_sip`.
- Answer substance: 4/5 — `logic_card: sip_create` rendered; prose names symbol/cadence/amount/day.
- Honest failure handling: 5/5 — n/a.
- UX polish: 5/5 — "Drafted: monthly INFY SIP of ₹5,000 on the 1st. Click Activate to confirm." Two sentences, ≤50 words, ticker in backticks, ₹ formatted Indian-style. Reads like the canonical example in system.md.
- fix: none.
- verdict: This is the "after a workflow draft" prose template the order rows should imitate.

### `free_text_what_can_you_do` — 91/100 (A)
- prompt: What can you do?
- Intent match: 5/5 — conversational, no tool.
- Path reasonableness: 5/5 — no tool (correct).
- Answer substance: 4/5 — four-bullet capability summary covering prices, agents, backtests, portfolio + concrete example. Useful but 357 chars is on the long side for a "what can you do?" framing.
- Honest failure handling: 5/5 — n/a; doesn't overpromise specific products.
- UX polish: 4/5 — bullets are well-formed; the truncated example prompt at the end is good UX.
- fix: optional — tighten to three bullets to stay under 250 chars.
- verdict: Solid capability blurb.

### `free_text_explain_sip` — 97/100 (A)
- prompt: Briefly explain what a SIP is.
- Intent match: 5/5 — educational, no tool.
- Path reasonableness: 5/5 — no tool (correct).
- Answer substance: 5/5 — explains SIP, intervals, rupee-cost-averaging, names instrument types (stock/ETF/MF).
- Honest failure handling: 5/5 — n/a.
- UX polish: 4/5 — 235 chars, well-formed; "briefly" framing could trim by ~30 chars but content is right.
- fix: none material.
- verdict: Clean educational answer.

### `free_text_greeting` — 88/100 (B)
- prompt: Hello
- Intent match: 5/5 — recognised as greeting.
- Path reasonableness: 5/5 — no tool (correct).
- Answer substance: 4/5 — greets and orients the user.
- Honest failure handling: 5/5 — n/a.
- UX polish: 3/5 — "Hi! Tell me what you'd like to do — check a price, build an agent, look at your portfolio, or run a backtest." lists four investing surfaces. `system.md` explicitly says "Never push investing topics on greetings, thank-yous, or off-topic messages — reply briefly and let the user lead the next turn." Baseline's "Hi there! How can I assist you today?" was the right shape; iter_1 regressed slightly.
- fix: in `system.md`, add a one-line negative anchor under "Voice": *Greetings: just greet. Do NOT list capabilities — capability questions come on their own ("what can you do?").*
- verdict: A small soft-violation of an explicit prompt rule.

---

### Score summary

| ID | Score | Letter |
|---|---|---|
| order_market_buy | 88 | B |
| order_limit_buy | 91 | A |
| order_gtt | 91 | A |
| order_market_sell | 88 | B |
| workflow_propose_5step | 91 | A |
| workflow_propose_3step | 88 | B |
| indicator_backtest_rsi | 47 | F |
| indicator_backtest_sma | 71 | C |
| financial_backtest_pe | 71 | C |
| slash_screen | 73 | C |
| portfolio_summary | 100 | A |
| market_price | 100 | A |
| market_status | 100 | A |
| calc_qty | 53 | F |
| sip_create | 94 | A |
| free_text_what_can_you_do | 91 | A |
| free_text_explain_sip | 97 | A |
| free_text_greeting | 88 | B |
| **Average** | **84.6** | **B** |
