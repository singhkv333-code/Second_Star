"""Targeted eval for the 2026-07-16 backtest-fallback-message fix
(backend/services/backtest_resolvability.py + workflow_backtester.py):
when a chat-built agent references a runtime value the backtester can't
replay historically (e.g. a live top-movers scan), backtest_workflow's
tool error used to surface raw jargon ("backtester cannot resolve
`{{ context.1.symbols.0 }}` ... Supported refs: ...") repeated once per
list index. Now it's one clean, structured plain-English sentence.
Covers: the exact reported unresolvable-ref shape (top-N movers), a
control case that SHOULD backtest cleanly (named symbol + indicator),
and a control case that's ineligible for an unrelated, already-clean
reason (event trigger) to confirm that path is undisturbed."""
from __future__ import annotations

PROMPTS: list[tuple[str, str]] = [
    ("backtest-unresolvable-ref", "create me a strategy that searches for the top gainer at 10AM every monday and invests in them and sell at the last trading day of the week. 10 shares of top 3 gainer. Backtest this for me."),
    ("backtest-unresolvable-ref", "build an agent that buys the top loser in NIFTY 50 every day at 9:20am, 5 shares, sell at 3:15pm. backtest it"),
    ("backtest-control-eligible", "backtest a strategy that buys 10 shares of RELIANCE whenever RSI drops below 30 and sells when it crosses 70 again"),
    ("backtest-control-ineligible-event", "build me an agent that buys HDFCBANK when the RBI cuts rates, and backtest it"),
]
