# Vivid Opinions — Beta-Verified Strategy Shortlist (2026-07-10)

Rebuilt from scratch (the original quant scratchpad was cleaned). Method per the
playbook's two-bar rule: an event's top-gainer is only trusted if it clears BOTH
(1) a statistical bar — event-study +21-trading-day return with a one-observation-
per-event t-stat and hit-rate, and, where a natural driver exists, a driver-beta
(does it genuinely track the event, t-stat) — AND (2) a mechanism bar (a causal
story). Data: NSE cache (2,209 names, 2010→Jun-2026) + yfinance (crypto/US).
Event dates web-verified (3 research agents, sources logged). ATH-type dates
derived from price. Returns are RAW total (not alpha).

## Tier A — build these (real/plausible edge + sound mechanism)

| Opinion | Strategy leg | Mean +21d | Hit | t / n | Verdict |
|---|---|---|---|---|---|
| **BTC makes a fresh all-time high** | **ETH** | **+26.6%** | 77% | t=5.94, n=30 | ✅ strong (flagship) |
| **Elon posts bullish on crypto** | SOL / RIOT | +58.6% / +49.8% | 83% / 67% | t≈1.9/1.7, n=6 | plausible, high-variance |
| **MicroStrategy announces a big BTC buy** | MSTR (+RIOT/MARA) | +12.4% | 60% | n=5 | plausible |
| **A foreign app (TikTok-style) gets banned** | SNAP (+META) | +13.8% / +5.3% | 67% | n=3 | plausible |
| **Adani announces a new project abroad** | ADANIPORTS | +7.1% | 67% | n=3 | plausible (cheap INR ticket) |
| **Cat-5 hurricane US landfall** | TRV (insurer) — NOT homebuilders (LEN/DHI −2 to −5%) | +9.1% | 100% | t=2.26, n=3 | ✅ strong mechanism |
| **Court rules against Big Tech (no breakup)** | GOOGL (relief rally) | +8.1% | 67% | n=3 | plausible, counter-intuitive |

## Tier B — "sell the news" FADE (real signal, but YES = exit/avoid, not buy)
- **SEC approves a crypto ETF** → BTC −7.6% / ETH −17.2% (t=−15.7 / −3.34, n=2) — famous priced-in fade.
- **Trump "Strategic Bitcoin Reserve" EO** → XRP −13.6% (t=−3.91), BTC −5.9% — rumor-pop then EO-day dump.
- **Country adopts BTC as legal tender** (El Salvador, n=1) → everything −12 to −18%.

## Tier C — single occurrence (n=1): shown for completeness, NOT an edge (no beta possible)
Binance/CZ guilty → COIN +59% · DeepSeek → BABA +55% · Credit Suisse → UBS +13.7% ·
Israel-Hamas → RTX +13.3% · N.Korea nuke → NOC +9% · Quantum "Willow" → RGTI +99.8% (microcap, reversed).

## Rejected (fail mechanism or confounded)
India Chinese-app ban → RELIANCE (+10% but confounded by the 2020 Jio stake-sale cycle) ·
Budget-day → VOLTAS (+10%, t=2.84 but pure idiosyncratic noise) · US-China chip controls → NVDA (2022-bottom confound).

## Notes / honesty
- The strongest genuinely-new edge is **BTC-ATH → ETH** (t=5.94 over 30 de-clustered ATHs) — stronger than anything in the current 8-pack.
- n=3 events (hurricane, Adani, SNAP, Google) are edges-not-laws; thin sample flagged.
- Data hygiene guards active (bad-tick + unadjusted-split detectors) — the GOODYEAR/GOLDBEES-class bugs the playbook hit are filtered.
- Reproducible: `scratchpad/engine.py` + `events.json` + `run_all.py`.
