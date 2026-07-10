# Paper-trading beta test — setup & verification plan (2026-07-06)

Owner: kvsingh171717@gmail.com (admin, user id **2**). All activity below is in
the **simulated paper book** (no real money, no real broker). Everything was
placed from the admin account.

---

## 1. Changes made this session

| # | Change | Where | Commit |
|---|--------|-------|--------|
| 1 | **Removed the yellow "Paper Trading Mode" banner** (+ layout height var, unused component/imports). The account-menu toggle still shows paper vs real. | `pivot-next/components/AppShell.tsx` | `f4bf2f0` |
| 2 | **Paper budget → ₹5,00,000.** Admin account re-seeded to ₹5L; new accounts seed from `PAPER_SEED_CAPITAL` (deployment `.env` = 500000; default 150000; tests pinned 150000). | `.env`, `config.py`, `paper/money.py`, `paper/accounts.py` | `5729127` |
| 3 | **Market-hours-aware fills.** New setting `paper_respect_market_hours` (ON in this deployment). When the NSE is **closed**, a paper MARKET order **rests ("queued for open")** instead of filling at the stale close; the market-hours evaluator tick (09:15–15:30 IST, Mon–Fri) fills it at the then-live price. LIMIT/SL/GTT already rest and fill when the price crosses their trigger. | `paper/broker.py`, `paper/evaluator.py`, `paper/jobs.py`, `config.py` | `5729127` |
| 4 | **Baskets + opinion deploys no longer require a broker in paper mode** (earlier fix, same test). | `routers/strategy.py`, `routers/views.py` | `6f3b031` |
| 5 | **Portfolio page + graph read the real paper book** (earlier fix, same test). | `services/portfolio_source.py`, `routers/portfolio*.py`, `PortfolioTab.tsx` | `86004b9` |

### How execution timing now works (what to expect tomorrow)
- **Simple buys & basket/opinion share-baskets placed while the market is closed** → they show as **resting / queued for open**, and **fill at market open (09:15 IST)** at the live opening price, debiting the paper cash then. (The scheduler's `paper_tick_resting` runs every 5 min during market hours and fills them.)
- **Conditional agents** ("when RSI < 30", "when price crosses X", "if it dips 5%") → **fire as soon as the price reaches the condition** during market hours, then place their order into the paper book.
- **Scheduled agents** ("every weekday 9:20", "every Monday") → fire at their cron time.
- **Event-view opinion strategies** (RBI rate-cut, etc.) → **armed** (register-not-execute); they fire on their resolution event, not at open.
- **Fill price source**: `paper/marks.get_mark_price` uses Kite LTP when a session/websocket is present, else yfinance last close. **Once you open the Kite websocket in the morning, fills automatically use live LTP.**

### Scheduler jobs that will run tomorrow — VERIFIED RUNNING (`/scheduler/status`)
Confirmed live at 01:02 IST 2026-07-06, `running: true`, with these next runs:
- `paper_tick_resting` → **09:00 IST** first tick (skips until the 09:15 open by
  the market-hours gate, then every 5 min fills queued MARKET + crossed
  LIMIT/SL/GTT).
- `execute_due_sips` → **09:15 IST** (fires the scheduled/SIP agents).
- `pivot_workflows_poll` (every 30s) + `pivot_workflows_watcher` (every 60s,
  market-hours-gated) → fire the price/indicator/%-move agents when their
  condition is reached.
- `paper_nav_snapshot` → **15:37 IST** → writes the day's NAV point (equity curve).

---

## 2. What was placed from the admin account

Placed **2026-07-06 ~00:50 IST (market closed)**, so everything is **resting /
queued for open**. Cash still ₹5,00,000 (queued MARKET orders don't reserve
cash — they debit at fill). Live snapshot: **41 resting orders + 9 workflows
(7 active, 2 paused)**. Estimated debit at open ≈ **₹1.3–2.8 lakh**, well under
the ₹5,00,000 budget.

### A. Simple buys (8/8 registered → resting → fill at open)
Each ran through the chat (`place_market_order`) then `/orders/register`:
`15 NIFTYBEES`, `40 GOLDBEES`, `3 INFY`, `2 TCS`, `5 RELIANCE`, `4 HDFCBANK`,
`10 ITC`, `8 SBIN`.

### B. Agents / automations (6/7 armed — active workflows)
| Prompt | Result |
|---|---|
| Buy 10 NIFTYBEES when RSI(14) < 30 | ✅ armed (indicator trigger) |
| Buy 5 RELIANCE when price crosses above 1350 | ✅ armed (price trigger) |
| Buy 20 GOLDBEES every weekday at 9:20 AM | ✅ armed (schedule) |
| Buy 3 TCS if it dips 5% in a day | ✅ armed (%-move trigger) |
| Buy 15 NIFTYBEES every Monday morning | ✅ armed (weekly schedule) |
| Buy 8 ICICIBANK when RSI < 35 | ✅ armed (indicator trigger) |
| **Alert me when INFY crosses 1600, don't buy** | ⚠️ **chat asked a clarifying question instead of arming a notify-only agent** — a routing miss to note (see §4). |

### C. Baskets / strategies (3/4 deployed → paper → fill at open)
Each built via chat `build_strategy` (`strategy_builder_card`), saved, and traded:
| Prompt | Result |
|---|---|
| Basket of auto stocks (~₹40k) | ✅ deployed, 3 legs (BAJAJ-AUTO, HEROMOTOCO, EICHERMOT…) routed=paper |
| Monsoon-winners basket (~₹40k) | ✅ deployed, 4 legs (SHAKTIPUMP, KSB, KIRLOSBROS, JISLJALEQS) routed=paper |
| Momentum strategy (~₹40k) | ✅ deployed, 6 legs (HINDZINC, TCS, NESTLEIND, COALINDIA…) routed=paper |
| **Defensive FMCG basket (~₹35k)** | ⚠️ card rendered but wasn't auto-extracted by the harness (see §4) |

### D. Opinion Markets / Views (5 placed + 3 armed)
4 curated views seeded (RBI rate-cut, Crude/geopolitical, Monsoon, IT-giants).
Equity-basket expressions **placed into paper** (fill at open); option/pair
expressions **armed as register-not-execute workflows** (fire on their event):
- **Placed (fill at open):** Crude [conservative], Crude [aggressive],
  Monsoon [conservative], IT-giants [conservative], IT-giants [aggressive].
- **Armed (event-triggered):** RBI rate-cut [conservative] active; RBI
  [balanced] + [aggressive] paused (their pair/option legs need the F&O deploy
  path, still being wired).
- Pair/option-only expressions returned an honest 422 ("pair/option expression
  is missing legs") — expected; they aren't simple share baskets.

### Live-price note (important for tomorrow)
Until you log into **Kite**, paper fills mark against the **yfinance last
close**. The code now threads your Kite token into the mark source, so **once
you open your Kite session/websocket in the morning, fills switch to live LTP
automatically** (during market hours). No stale-price fills — orders are
queued and only fill after 09:15 IST.

---

## 3. How to verify tomorrow (end of day)

1. **Portfolio tab** → holdings should now show the filled positions (the queued
   buys + basket/opinion legs that filled at open). Portfolio value ≈ ₹5L ± P&L.
2. **Portfolio → History** → the simulated fills for everything that executed.
3. **Agents tab** → the armed agents; conditional ones that triggered should show
   a run/fill; scheduled ones should have fired at their time.
4. **Opinion Markets → My Opinions** → the deployed view strategies.
5. Any agent whose condition was **not** reached stays armed (correct — it waits).
6. Cross-check: total deployed capital stayed **under ₹5,00,000** (see §2 total).

If a queued order is still "resting" after 09:20 IST, the scheduler tick or the
mark price source (websocket) is the thing to check.

---

## 4. Observations / follow-ups (noted, not blocking tomorrow's test)

1. **"Alert me when INFY crosses 1600, don't buy anything"** → the chat asked a
   clarifying question instead of arming a notify-only agent. The notify-only
   routing (`propose_dsl_workflow(action_kind='notify_only')`) didn't fire here.
   Worth a prompt-routing fix so pure alerts arm directly.
2. **"Defensive FMCG basket"** → the `strategy_builder_card` DID render in chat
   (the build worked); the placement harness just didn't auto-extract its
   constituents to deploy it. Not a chat failure — deploy it from the card if
   you want it live.
3. **Opinion pair/option expressions** (balanced tiers, straddles) can't yet be
   placed as a share basket or fully armed as a workflow — they need the F&O /
   pairs deploy path. Only the equity-basket tiers execute in paper today.
4. **Holidays:** `is_market_open()` checks weekday + 09:15–15:30 only (no NSE
   holiday calendar). On a holiday, orders would still try to fill — verify the
   test day is a real trading session.

## 5. Reproduce / re-run

- Placement script: `scratchpad/place_admin_test.py` (drives chat as user 2,
  arms/deploys each result). Raw per-item log: `scratchpad/admin_test_results.json`.
- To reset the admin book and re-run: re-seed the account to ₹5L, clear its
  paper orders/workflows, then re-run the script.
