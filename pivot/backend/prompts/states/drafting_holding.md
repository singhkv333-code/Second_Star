## State: DRAFTING(holding)
A holding-action draft is on screen ("sell my entire INFY position"). Tool palette: `propose_holding_action`. Re-emit on amendment.

### Schema
```
{
  symbol: "INFY"             # the actual NSE ticker, NEVER a qualifier word
  action: "sell" | "set_stoploss" | "trim"
  quantity_mode: "full" | "fraction" | "shares"
  quantity_value: 0.5         # when fraction; integer when shares
  trigger_indicator: optional { rsi/sma/ema/macd, period, op, value }
  trigger_price: optional { op, value }
  notify: bool
}
```

### Hard rules
- `symbol` is the actual ticker. "Exit my entire INFY" → `INFY`. "Sell all my RELIANCE" → `RELIANCE`. The schema validator will reject `ENTIRE`/`ALL`/`WHOLE`/`FULL`/`POSITION`/`HOLDING`.
- "Half" is genuinely ambiguous (half the share count vs half the holding value) — ask once if the user hasn't been explicit.
