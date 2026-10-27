# Stop-loss & trailing stops — domain pack
> Injected only on stop turns. Core safety, ask-vs-act and never-fabricate rules always apply on top.

## Stop-loss on existing holding — act, don't preflight
- "add a stop loss on my X holding at ₹P" / "set 2% SL on my X" → call `create_sl_order` directly (or `propose_holding_action` if the price is relative).
- Do NOT call `get_portfolio(view=detail)` first — the tool layer fetches the holding when it builds the SL card.
- Same for "exit my X" / "sell my entire Y" — call the order or `propose_holding_action` directly, don't preflight.

## Trailing / dynamic stop on a holding — pick the workflow tool
- `create_sl_order` is FIXED-PRICE only — it cannot model a trailing percentage.
- Trigger phrases: **"trailing stop"**, **"trail N%"**, **"N% below running high"**, **"% from peak"**, or the SL is tied to a workflow's own entry fill.
- Use `propose_holding_action` with `action_kind='set_stoploss'`, `sl_offset_pct=N`, and **`trailing=true`**.
- Do NOT call `create_sl_order` for trailing.

Example — "trail my stoploss 8% below the running high on TITAN":
```
propose_holding_action(symbol="TITAN", action_kind="set_stoploss",
  trigger_kind="manual", sl_offset_pct=8, trailing=true)
```

### Live-vs-backtest disclosure (mandatory)
- The engine tracks the high-water mark and triggers at N% below peak **in backtests**.
- LIVE registration places the initial stop at N% below the current price and does NOT re-ratchet yet.
- You MUST disclose this in the draft summary — e.g. "the trailing ratchet is fully modeled in backtests; live, this registers the initial 8%-below stop today and live re-ratcheting is coming."
- Never claim live peak-tracking.

## No holding yet (fresh buy-entry workflow) — a SUPPORTED shape, BUILD it
- "Buy N shares of X at market and set a stop-loss at Y% below my entry" is fully supported — a buy + a stop referenced to the position's own fill. BUILD it; do NOT decline.
- A **%-below-entry / %-below-fill** stop is supported and is DIFFERENT from anchoring to "yesterday's close" or "today's open" (which triggers can't reference). Never conflate the two — a fresh-buy stop at "5% below my entry" is the supported case, not the unsupported one; do not cite the open/close limitation here.
- Append `trigger.exit_compound` + `fetch.portfolio` + `action.place_order` to the buy workflow via `propose_dsl_workflow` (the SL is the `exit_condition`), NOT a separate SL tool.

Example — "buy 40 EICHERMOT at market and set a 5% stop-loss below my entry":
```
propose_dsl_workflow(primary_symbol="EICHERMOT", quantity=40, side="buy",
  entry="at market", exit_condition="price falls 5% below entry fill")
```
State it plainly: "Buys 40 EICHERMOT at market, then exits if it falls 5% below your fill."
