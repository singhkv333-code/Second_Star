# Backtesting chat eval — ROUND 3 (breadth + edges) — 2026-06-01

18 turns through `chat_service.handle()` in-process on current code + real Azure
`gpt-5.4-mini`. Probes ground runs 1-2 didn't: the wider indicator registry,
pairs/cross-asset, regime filters, volume breakouts, position-aware & aggregator
exits, rupee-SIP, a multi-turn tuning sequence (DSR-deflation validation), and 2
boundary cases. Runner: `/tmp/bt_eval3/run.py`. **10/18 ran with the full battery.**

## Highlights

- **Wide indicator registry works.** ADX, CCI, MFI, VWAP, Keltner, percentrank
  aggregator, position-aware exit (up 8% / down 4%), and a cross-asset
  relative-strength comparison all routed to `backtest_dsl_tree` and ran with PSR/
  DSR/MinTRL · Monte-Carlo · sub-periods · a Trust verdict.
- **The battery discriminates — it's not a rubber stamp of "no edge".**
  **d09 (RELIANCE RSI(14)<40 gated on NIFTY above its 200-DMA) → "promising",
  DSR 0.96** — the first strategy across all three runs to clear the bar. The other
  9 runs correctly came back "no demonstrable edge" / "insufficient data".
- **Boundary cases handled correctly:** "backtest a profitable strategy on a good
  stock" → **asked** for the symbol + rule (the "run with defaults" fix did NOT
  over-correct into running garbage); options short-straddle → **instant decline**
  (0 tokens).

## Per-turn

| # | Probe | Tool | Ran | Verdict / note |
|---|---|---|---|---|
| d01 | ADX(14)>25 + above-50-EMA | dsl_tree | ✅ | no_edge (DSR 0.16) |
| d02 | CCI(20) cross ±100 | dsl_tree | ✅ | no_edge (DSR 0.20) |
| d03 | MFI<20 in / >80 out | dsl_tree | ✅ | no_edge (DSR 0.34) |
| d04 | Aroon-up × Aroon-down | **ASK_USER** | ❌ | symbol glitch — replied about "ETERNAL" for an INFY prompt |
| d05 | close>VWAP + RSI>50 | dsl_tree | ✅ | no_edge (VWAP-on-daily ran, didn't crash) |
| d06 | Keltner upper breakout | dsl_tree | ✅ | no_edge (DSR 0.00) |
| d07 | **Pairs** TCS/INFY z-score | **ASK_USER** | ❌ | recognised the pairs trade but asked "how many shares per leg" |
| d08 | rel-strength HDFCBANK vs ICICIBANK | dsl_tree | ✅ | no_edge |
| d09 | RSI<40 gated on NIFTY>200-DMA | dsl_tree | ✅ | **promising (DSR 0.96)** |
| d10 | volume>2× + 20-day high | dsl_tree | ❌ | **yfinance data miss for TATAMOTORS.NS** (external) |
| d11 | RSI<35, exit pos up8%/down4% | dsl_tree | ✅ | no_edge (position-aware exit) |
| d12 | bottom-10%-of-252d, exit 10d | dsl_tree | ✅ | no_edge (percentrank aggregator) |
| d13 | ₹10k/mo NIFTYBEES SIP | **ASK_USER** | ❌ | asked to confirm the ₹10k notional |
| m1#0 | RSI<30, exit 10d | backtest_workflow | ✅ | 0 trades, reported cleanly (no ASK_USER ✓) |
| m1#1 | "now try RSI<25" | **get_indicator** | ❌ | **mis-route: fetched live RSI instead of re-running** |
| m1#2 | "and RSI<20" | **propose_workflow** | ❌ | **mis-route: drafted an agent instead of re-running** |
| b1 | "profitable strategy on a good stock" | ASK_USER | ✅ correct | asked for symbol + rule |
| b2 | short straddle BANKNIFTY | ∅ instant decline | ✅ correct | F&O decline, 0 tokens |

## New findings (ranked)

### P1 — backtest tuning follow-ups mis-route (NEW, the top issue)
The multi-turn deflation test failed to validate: m1#0 ran, but **"now try RSI<25"
→ `get_indicator`** (fetched the live RSI: *"RELIANCE RSI(14) is 38.5… has not
triggered"*) and **"and RSI<20" → `propose_workflow`** (drafted an agent). Root
cause: a backtest follow-up tweak has **no backtest verb**, so the deterministic
intent classifier doesn't tag it as a backtest and the LLM gets the wrong tool
surface. `system.md` tells the model to re-run on follow-ups, but the classifier
runs *before* the LLM. So the DSR deflation across turns could not be observed
end-to-end (it IS verified in isolation). Fix path: the `_backtest_followup`
detection in `chat_service` needs to catch verb-less tweaks ("now try…", "with
RSI<25 instead", "add a stop") when the prior turn was a backtest.

### P2 — residual over-asking on *sizing/notional* (not window/exit)
d07 (pairs) asked "how many shares per leg"; d13 (₹-SIP) asked to confirm the
₹10k notional. The "run with defaults" rule covers window/qty/exit but the model
still asks about leg-sizing and rupee-notional conversion. Narrow; lower priority.

### P2 — edge glitches
d04 (Aroon) mis-resolved the symbol (answered about "ETERNAL" for an INFY prompt)
and routed to an agent-clarify instead of a crossover backtest — an Aroon-specific
oddity. d10 was purely a **yfinance data miss for TATAMOTORS.NS** (external; stop
using TATAMOTORS in evals).

## Triad
Median latency ~12 s (4.2–19.2); input 31k–103k tokens (~70–95% cached); output
59–242; **no fabrication**; honest verdicts incl. one genuine "promising".

## Net
Indicator **breadth is strong** (9/13 single-turn probes ran, spanning the registry),
the battery **discriminates** (a real "promising" verdict), and boundary handling is
correct. The clear new gap is **multi-turn backtest follow-up routing** (verb-less
tweaks mis-classify) — the one thing worth fixing next. Pairs is *recognised* but
over-asks on leg sizing rather than running with a default.
