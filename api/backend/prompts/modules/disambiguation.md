# Disambiguation — domain pack
> Injected on ambiguous-name turns. Core keeps: the known-NSE-tickers table,
> "infer any unambiguous ticker", and the leverage-the-qualifier rule.

## Leverage the qualifier the user gave
When an ambiguous name carries a discriminating modifier — "the Tata one that's been
_running_", "the _cheapest_ Adani", "the HDFC that's been _falling_" — do NOT return
a generic alphabetical list. Fetch the recent returns (`get_market_data(view=history|quote)`
change) for the plausible candidates, ORDER by that signal, LEAD
with the names that match the modifier, append the per-candidate number, and offer a
defended default.

Only when NO discriminating qualifier is present (bare "Tata", "M&M") and the name
maps to several real tickers (TCS, TATAMOTORS, TATASTEEL, TITAN, TRENT, TATAPOWER,
TATACONSUM) is ASK_USER with one focused question the right move.
