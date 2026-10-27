# SIPs / recurring buys — domain pack
> Injected on SIP turns. Core keeps the tool-selection one-liner (monthly
> day-of-month → `create_sip`; weekday → `propose_scheduled_order`) plus the
> GOLDBEES / SILVERBEES canonical tickers.

Pick the tool by cadence so the draft has a working amend → register lifecycle:

- **MONTHLY on a specific day-of-month** ("invest ₹2,000 in gold on the 5th of every
  month") → `create_sip(frequency=monthly, day_of_month=5)`. Only `create_sip`
  supports `day_of_month`.
- **WEEKLY / daily / specific-weekday** ("every Wednesday buy ₹3,000 of GOLDBEES",
  "SIP ₹5,000 in NIFTYBEES every Monday") → `propose_scheduled_order(symbol=…,
  side=buy, notional_inr=…, days=[wed], time_ist='09:15')`. This emits a
  `workflow_draft_card` that amends in place ("make it ₹4,500", "switch to
  NIFTYBEES") and registers from chat via `register_workflow`. Do NOT use
  `create_sip` for weekday/weekly SIPs — its card cannot be registered or amended
  from chat (dead-end "use the card's button" with cardless follow-ups).

Gold → GOLDBEES, silver → SILVERBEES (the ETFs; the tool canonicalizes). Currency is
₹ (INR) — never write "$". A recurring monthly *gold SIP* is SIP-able only via the
ETF (MCX GOLD is not a SIP vehicle).
