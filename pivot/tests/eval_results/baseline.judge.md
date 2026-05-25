# Judge report — baseline

## Headline
- Overall: 47/100 -> F
- 18 prompts scored, 0 hard-gated (no transport errors, no fallback flags, no contradictory tree readbacks — but see "What's broken" below; many rows are functionally failures even though they don't trip the gate)
- This build can route to the right tool more often than not, but a substantial fraction of the surface area is broken at the data/execution layer — orders cannot place, financial backtests cannot connect, market status returns "rephrase?". Tool selection is the strongest dimension; substance is the weakest.

## What's working (5 bullets)
- **Conversational/educational asks land cleanly with no tool overcalling** — `free_text_explain_sip` and `free_text_what_can_you_do` produce useful prose without unnecessary tool calls, and `free_text_greeting` correctly returns a two-word-style reply with no investing-topic upsell.
- **Live price lookup works end-to-end** — `market_price` calls `get_live_price` and returns a real number with change %, then closes with the right disclaimer. This is one of the very few rows that fully delivers on substance.
- **Portfolio readback is real and well-formatted** — `portfolio_summary` calls `get_holdings`, totals P&L, and renders bulleted positions with LTP and per-position P&L. Latency is high (14.5s) but the answer is acturable.
- **Tool routing is correct on order intents** — every BUY/SELL/GTT/SIP/limit row routes to the matching tool (`place_market_order`, `place_limit_order`, `create_gtt_order`, `create_sip`). System-prompt routing is not the bottleneck; the executors underneath are.
- **Indicator RSI backtest produces a real numbered result** — `indicator_backtest_rsi` returns `+10.1% over 18 trades vs buy-and-hold +61.1%`, which is the only A-grade substance row in the snapshot and shows the deterministic backtest path is alive when the request is in-shape.

## What's broken — systemic patterns (top 3)
1. **Every LogicCard order tool returns "could not be placed" with no diagnostic.**
   - IDs: `order_market_buy`, `order_limit_buy`, `order_gtt`, `order_market_sell`, `sip_create`, `calc_qty` (different tool, same failure shape).
   - Root cause: tool layer is calling Kite (or a pricing dep) without working credentials/instrument lookup in the eval environment, then surfacing a vague "verify the stock code" message. RELIANCE/INFY/WIPRO/HDFCBANK/TCS are obviously valid symbols — telling the user "verify the stock code" is misleading.
   - Next iteration: the order handlers must distinguish *symbol-resolution failure*, *no broker credentials in this session*, and *broker rejection*, and surface them in the LogicCard error field. The current message hides the real failure ("credentials not connected" / "instrument cache empty") behind a fake validation error. At minimum, when the eval user has no Kite session, the LogicCard should say so explicitly.

2. **Financial-DB path is misconfigured for the eval environment.**
   - IDs: `financial_backtest_pe` (intent=ERROR, latency 61ms), `slash_screen` (intent=ERROR, latency 4ms).
   - Root cause: literal error string `invalid DSN: scheme is expected to be either "postgresql" or "postgres", got 'sqlite'` is leaking into the user response. This is an env-config bug — fundamentals/screen code paths require Postgres but the eval is running against SQLite. The fact that the raw DSN error is reaching the user surface is itself a UX failure.
   - Next iteration: (a) make `slash_screen` and `financial_backtest_*` either auto-route to the Postgres financials DB or honestly say "fundamentals DB not connected in this build". (b) Never let `invalid DSN: ...` reach the response — catch and rewrite to a user-grade message. Today the user sees an internal Postgres URL parse error.

3. **The chat router silently misroutes or gives up on requests it should handle.**
   - IDs: `market_status` ("Sorry, I had trouble with that — could you rephrase?" — should be a trivial `get_market_status` call), `workflow_propose_3step` ("Every Monday morning, buy 5 INFY" got bounced into a SIP-amount clarification instead of `propose_workflow`), `indicator_backtest_sma` (golden cross 50/200 reported 0% / 0 trades and silently surfaced it as a result instead of flagging the "0 trades" outcome as suspicious for a 50/200 cross on INFY).
   - Next iteration: (a) `get_market_status` is a deterministic 1-line call; the rephrase fallback for that prompt indicates classifier coverage holes — add it to the MARKET_QUERY hot path. (b) `Every Monday morning, buy 5 INFY` has symbol + qty + schedule + action, all four required fields — system prompt explicitly says to emit and not re-ask, but the LLM upsold to a SIP-amount question. Tighten the system prompt example or add a router shortcut. (c) For backtests that produce 0 trades, the response should explicitly flag "no signals fired in the period" rather than reporting it as a flat 0% return — that mimics the F-grade anchor.

## Per-prompt detail

### `order_market_buy` — 24/100 (F)
- prompt: Buy 10 RELIANCE at market
- Intent match: 4/5 — correct tool routed (`place_market_order`).
- Path reasonableness: 4/5 — right tool, but no symbol pre-validation or credentials check upstream.
- Answer substance: 0/5 — user got "Order placement failed. Please verify the stock code" for a textbook-valid ticker; nothing acturable.
- Honest failure handling: 1/5 — claims the failure is the user's fault (verify stock code) when RELIANCE is unambiguous. Real cause hidden.
- UX polish: 2/5 — quote-wrapped JSON-string artifact in the response; disclaimer present but the prose is canned.
- fix: When `place_market_order` fails, surface the actual rejection (credentials/instrument/broker) in the LogicCard error field instead of "verify the stock code".
- verdict: Tool routed correctly, executor returned a non-answer with misleading attribution.

### `order_limit_buy` — 30/100 (F)
- prompt: Buy 5 INFY at limit price 1400
- Intent match: 5/5 — clean read of symbol/qty/price/side.
- Path reasonableness: 5/5 — `place_limit_order` is correct.
- Answer substance: 0/5 — order didn't place; user has no fill, no draft, no usable artifact.
- Honest failure handling: 2/5 — at least offers to adjust parameters; still vague on *why* it failed.
- UX polish: 3/5 — slightly better than market_buy (asks if user wants to adjust), but the recovery suggestion is "verify the stock code" again.
- fix: Same — distinguish credential failure from validation failure at the executor.
- verdict: Right tool, wrong outcome, same root cause as the other order rows.

### `order_gtt` — 32/100 (F)
- prompt: Set a GTT to buy 3 HDFCBANK if it drops to 1480
- Intent match: 5/5 — GTT semantics understood.
- Path reasonableness: 5/5 — `create_gtt_order` chosen.
- Answer substance: 0/5 — GTT not created; user got a "could not be created" with no real diagnostic.
- Honest failure handling: 2/5 — lists the parameters it had, asks user to confirm retry. Marginally better than market_buy.
- UX polish: 3/5 — disclaimer truncated mid-sentence in preview but full text shows it lands; the "Ensure the stock is active and the price is within trading limits" hint is on the nose for what a real GTT rejection looks like, but doesn't match the actual cause.
- fix: Same as above — wire GTT executor to surface the real reason.
- verdict: Routed correctly, executor doesn't have what it needs to actually place.

### `order_market_sell` — 24/100 (F)
- prompt: Sell 12 WIPRO at market
- Intent match: 5/5 — clean read.
- Path reasonableness: 5/5 — `place_market_order` (sell side).
- Answer substance: 0/5 — same canned "could not be executed".
- Honest failure handling: 1/5 — generic; no real cause.
- UX polish: 2/5 — quote-wrapped JSON string again in the preview.
- fix: Same executor fix; also strip the leading/trailing `"` that turns the response into a JSON-quoted string.
- verdict: Identical failure mode to `order_market_buy`.

### `workflow_propose_5step` — 64/100 (D)
- prompt: Every weekday at 3:55 PM IST, if my buying power is over ₹50,000, buy 10 shares of RELIANCE and notify me by email.
- Intent match: 4/5 — read the schedule, gate, action, notify.
- Path reasonableness: 4/5 — `propose_workflow` is the right tool.
- Answer substance: 3/5 — `render_hint` did NOT come back as `workflow_draft_card` in the snapshot, so the user got prose-only steps with no committable card. The text accurately summarises what would be drafted, but per the system prompt's "After a workflow draft tool call" rule, the card IS the description. Without the card render, this is a half-delivery.
- Honest failure handling: 3/5 — doesn't flag that email isn't wired in v1 (system prompt explicitly requires this). User is told "Email confirmation" without the caveat.
- UX polish: 3/5 — preamble paraphrases what the user said before asking for approval; verbose for a draft acknowledgement.
- fix: Ensure `propose_workflow` returns `render_hint: workflow_draft_card` in the chat envelope, and append the required "Email isn't wired in v1 — used in-app instead." note.
- verdict: Tool called, card missing in payload, email caveat missing.

### `workflow_propose_3step` — 36/100 (F)
- prompt: Every Monday morning, buy 5 INFY
- Intent match: 2/5 — misread as a SIP-amount question. The user gave symbol + qty + schedule + action — all four required fields. System prompt explicitly forbids upselling a schedule-based one-time buy into an amount-based SIP without asking.
- Path reasonableness: 2/5 — no tool called; should have been `propose_workflow`.
- Answer substance: 1/5 — user got a clarification question instead of a draft card.
- Honest failure handling: 3/5 — at least it doesn't fabricate; it asks. But the question is wrong (5 INFY isn't an amount-based SIP).
- UX polish: 3/5 — clean prose, just the wrong question.
- fix: Recognize that `every Monday morning, buy 5 INFY` is `propose_workflow` with `trigger.schedule + action.place_order(qty=5, symbol=INFY)` — qty is given as shares, not ₹. Don't infer an INR notional default.
- verdict: A "complete-on-first-turn" workflow request got routed to a clarification loop that contradicts the system prompt.

### `indicator_backtest_rsi` — 92/100 (A)
- prompt: backtest RELIANCE buying when RSI drops below 30 from 2023-01-01 to 2024-12-31
- Intent match: 5/5 — strategy and window understood.
- Path reasonableness: 5/5 — deterministic backtest route ran in 824ms, no LLM hop.
- Answer substance: 5/5 — concrete numbers: 18 trades, +10.1% strategy vs +61.1% buy-and-hold over 5y. (Minor: user asked for 2023-01-01 to 2024-12-31 specifically; response says "5y", which is a small mismatch — but trades + return are real.)
- Honest failure handling: 4/5 — honestly reports strategy underperformed buy-and-hold, which is the right framing.
- UX polish: 4/5 — one short sentence, no preamble, no upsell. Could have shown drawdown/win rate but the brevity is intentional.
- fix: Honour the user's explicit date range instead of defaulting to 5y.
- verdict: This is the model of what every row in this snapshot should look like.

### `indicator_backtest_sma` — 36/100 (F)
- prompt: backtest INFY golden cross 50 200
- Intent match: 4/5 — recognised 50/200 SMA cross.
- Path reasonableness: 4/5 — `run_backtest` was called.
- Answer substance: 1/5 — reported "0% returns, no trades". A 50/200 golden cross on INFY over real history should fire multiple times; 0 trades almost certainly means the DSL emitted a condition that never evaluates true (e.g. comparing same series, off-by-one, or empty data window). The model surfaces this as a flat result instead of flagging it as suspicious.
- Honest failure handling: 2/5 — adds a "past performance" line but never says "0 trades is anomalous for a 50/200 cross — likely a data or window issue."
- UX polish: 3/5 — succinct, no preamble, but the cheerful framing is wrong for a likely-broken result.
- fix: When backtest returns 0 trades, the response should explicitly say "no signals fired — likely a data window or DSL-encoding issue; want me to widen the window or try EMA?" rather than reporting 0% as the answer.
- verdict: Classic F-anchor: "hid the failure with silent zeros".

### `financial_backtest_pe` — 8/100 (F)
- prompt: backtest pe_ratio < 15 from 2020-01-01 to 2022-12-31 quarterly
- Intent match: 4/5 — recognised it as a financial backtest.
- Path reasonableness: 1/5 — routed to Postgres-backed financials DB which isn't connected; no graceful degradation.
- Answer substance: 0/5 — raw `invalid DSN ... got 'sqlite'` error string surfaces verbatim. Zero acturable content.
- Honest failure handling: 1/5 — "honest" only in that it shows the error, but exposes internal config details to the user.
- UX polish: 0/5 — leaking a DSN-parse error into the chat surface is a P0 polish bug.
- fix: Catch the DSN failure at the financial backtest entrypoint and replace with "Fundamentals DB isn't connected in this build — fundamental backtests are unavailable right now." Log the DSN error internally.
- verdict: Total failure; should be tagged a hard gate in spirit even if the rubric's literal gate doesn't trip.

### `slash_screen` — 8/100 (F)
- prompt: /screen roe > 18
- Intent match: 4/5 — slash command recognised.
- Path reasonableness: 1/5 — same Postgres misconfig.
- Answer substance: 0/5 — same DSN error string.
- Honest failure handling: 1/5 — same.
- UX polish: 0/5 — same.
- fix: Same as financial_backtest_pe — catch and rewrite.
- verdict: Same systemic env issue as the financial backtest row.

### `portfolio_summary` — 84/100 (B)
- prompt: What's in my portfolio?
- Intent match: 5/5 — clean.
- Path reasonableness: 4/5 — `get_holdings` (system prompt accepts either `get_holdings` or `get_portfolio_summary`). 14.5s latency is high.
- Answer substance: 5/5 — 5 positions with LTP, qty, per-position P&L, and a total. Acturable.
- Honest failure handling: 4/5 — N/A (no failure), but the answer is straightforward.
- UX polish: 4/5 — clean bullet list, but the total "P&L of ₹3,355" sums match the individual lines, good. Slight nit: missing total portfolio market value.
- fix: Add total portfolio value alongside total P&L; investigate why this call takes 14.5s.
- verdict: One of the better rows. Substance and intent both strong.

### `market_price` — 95/100 (A)
- prompt: What's the current price of RELIANCE?
- Intent match: 5/5 — clean.
- Path reasonableness: 5/5 — `get_live_price`.
- Answer substance: 5/5 — price + intraday change + disclaimer in one line.
- Honest failure handling: 5/5 — N/A (no failure, no hiding).
- UX polish: 5/5 — two-sentence answer, no preamble, no upsell.
- fix: none
- verdict: Textbook A. This is what the rest of the surface should feel like.

### `market_status` — 16/100 (F)
- prompt: Is the market open right now?
- Intent match: 1/5 — classifier punted to "could you rephrase?" for a trivially clear question.
- Path reasonableness: 1/5 — `get_market_status` should have fired; no tool called.
- Answer substance: 0/5 — user got nothing.
- Honest failure handling: 2/5 — at least asks for rephrase rather than fabricating.
- UX polish: 3/5 — short and polite, but a "could you rephrase?" for `Is the market open right now?` is a coverage hole.
- fix: Ensure `MARKET_QUERY` intent fires on questions of the form "is market open" / "is the market open" / "are markets open"; route deterministically to `get_market_status`.
- verdict: Embarrassing miss on a baseline market-info question.

### `calc_qty` — 28/100 (F)
- prompt: How many shares of TCS can I buy with ₹50,000?
- Intent match: 5/5 — recognised as a qty calculation.
- Path reasonableness: 5/5 — `calculate_order_qty` chosen.
- Answer substance: 0/5 — "current price unavailable" — but `market_price` row a few rows up successfully called `get_live_price` on RELIANCE. TCS price should be just as available; the calculator's price lookup is broken.
- Honest failure handling: 3/5 — does say price unavailable rather than fabricating, which is fine, but `market_price` worked so this is inconsistent.
- UX polish: 2/5 — quote-wrapped JSON string artifact again.
- fix: Wire `calculate_order_qty` through the same `get_live_price` path that `market_price` uses; strip leading/trailing quote-wrap on the response string.
- verdict: Tool routed correctly, executor's price source disagrees with the live-price tool's price source.

### `sip_create` — 28/100 (F)
- prompt: Set up a monthly SIP of ₹5000 in INFY on the 1st
- Intent match: 5/5 — clean read.
- Path reasonableness: 5/5 — `create_sip` chosen.
- Answer substance: 0/5 — same "couldn't be confirmed" pattern.
- Honest failure handling: 2/5 — vague "verify account permissions" — at least gestures at the real cause (broker creds), but doesn't say "Kite isn't connected".
- UX polish: 3/5 — short and clean, just empty.
- fix: Same executor fix; SIP creation should also surface "Kite not connected" or whatever the real cause is.
- verdict: Same systemic order-executor failure.

### `free_text_what_can_you_do` — 80/100 (B)
- prompt: What can you do?
- Intent match: 5/5 — conversational; no tool needed.
- Path reasonableness: 5/5 — no tool called (correct per system prompt).
- Answer substance: 4/5 — lists market data, orders, alerts, portfolio, comparison. Reasonable scope.
- Honest failure handling: 3/5 — claims it can "set up alerts (stop-loss, price triggers)" — alerts may or may not be wired, fine. Doesn't overpromise much.
- UX polish: 4/5 — slightly long for "what can you do?" (355 chars); a tighter 2-bullet answer would be better.
- fix: Tighten to a 3-bullet summary instead of comma-separated prose.
- verdict: Solid baseline answer.

### `free_text_explain_sip` — 88/100 (B)
- prompt: Briefly explain what a SIP is.
- Intent match: 5/5 — educational, no tool.
- Path reasonableness: 5/5 — no tool (correct).
- Answer substance: 5/5 — explains SIPs, rupee cost averaging, mentions both MFs and stocks.
- Honest failure handling: 5/5 — N/A.
- UX polish: 3/5 — 353 chars for "briefly explain" is borderline; system prompt says "≤120 words for conversational asks" and this is close. Pop-up of "Popular in India for ..." is a slight overshare.
- fix: Trim to ≤80 words for "briefly" framing.
- verdict: Educational answer with mild verbosity.

### `free_text_greeting` — 96/100 (A)
- prompt: Hello
- Intent match: 5/5 — recognised as greeting.
- Path reasonableness: 5/5 — no tool (correct).
- Answer substance: 5/5 — "Hi there! How can I assist you today?" is exactly the right size.
- Honest failure handling: 5/5 — N/A.
- UX polish: 5/5 — short, no investing-topic upsell. System prompt explicitly demands this.
- fix: none
- verdict: Perfect minimal greeting.

---

### Score summary

| ID | Score | Letter |
|---|---|---|
| order_market_buy | 24 | F |
| order_limit_buy | 30 | F |
| order_gtt | 32 | F |
| order_market_sell | 24 | F |
| workflow_propose_5step | 64 | D |
| workflow_propose_3step | 36 | F |
| indicator_backtest_rsi | 92 | A |
| indicator_backtest_sma | 36 | F |
| financial_backtest_pe | 8 | F |
| slash_screen | 8 | F |
| portfolio_summary | 84 | B |
| market_price | 95 | A |
| market_status | 16 | F |
| calc_qty | 28 | F |
| sip_create | 28 | F |
| free_text_what_can_you_do | 80 | B |
| free_text_explain_sip | 88 | B |
| free_text_greeting | 96 | A |
| **Average** | **47.2** | **F** |
