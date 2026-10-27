# Pivot Pricing & Billing Spec

Status: v1, agreed by founders, Oct 2026. Implementation spec for Pivot's pricing, free-tier limits and billing.
Audience: engineers and Claude Code working in the Pivot codebase.

---

## 1. Product context (read first)

Pivot is an India-first trading and research platform with an AI chat at the centre.

- **Chart**: live NSE, BSE, MCX and crypto candles; the AI marks the chart.
- **Chat**: one conversation over charts, financials, filings, news, screens. Analysis only, never buy/sell advice.
- **Research**: company pages, screener, option chain, comparisons.
- **Workspace**: user-arranged widgets; the chat can set up a desk.
- **Plays**: trading ideas in plain English, compiled by the AI into structured rules, backtested, then armed. Today they fill only into a **paper book**.

This spec adds **live Plays** (orders routed to the user's own broker via its API) and the pricing around them.

**Pricing principle:** Free to research and paper trade. Pay only when a live Play trades, with a hard monthly ceiling. No subscription plans.

---

## 2. Price list (single source of truth)

All prices are **inclusive of 18% GST**.

| Item | Price | Rule |
|---|---|---|
| Chat, charts, screener, research pages | ₹0 | Quick answers unlimited; deep answers have a daily free allowance (§4) |
| Paper Plays | ₹0 | Unlimited while armed |
| Backtests | ₹0 | Weekly allowance (§4) |
| Building Plays | ₹0 | 3 free builds; arming any Play on paper unlocks 10 more per month (§4) |
| Live Play trade: intraday / F&O / futures / commodity / crypto | **₹10 per trade** | One Play action = one trade (§3) |
| Live Play trade: equity delivery | **min(₹5, 0.1% of order value)** | One Play action = one trade (§3) |
| **Monthly maximum** | **₹499** | Shared across all live trades in a calendar month (IST) |
| Orders placed manually from the chart | ₹0 | Only Play-fired actions are billable |

Put every number in one config module (§11). Nothing hard-coded elsewhere.

---

## 3. What counts as a billable trade

A **billable trade** is one **Play action** that the Play engine sends to a live broker and that gets **at least one fill**.

- **One action, one charge**, regardless of legs, lots or orders. A 2-leg straddle firing once = 1 trade. An order split by the broker for exchange freeze limits = 1 trade.
- **Baskets and rebalances**: one rebalance run across N stocks = 1 trade. For delivery pricing, order value = total value filled in that action.
- **Mixed actions** (delivery + F&O legs in one action): charge the F&O price (₹10).
- **Segment**: decided by the legs' product type. CNC/delivery → delivery price. Everything else (MIS/intraday, NRML/F&O, futures, MCX, crypto) → ₹10.
- **Not billable**: paper fills, backtests, rejected or cancelled orders with zero fills, manual orders, notify-only or propose-only Play actions the user then places by hand.
- **Partial fills** are billable once.
- **Exits fired by a Play** (stop-loss, target, trailing stop, square-off) **are billable** like any other action, but are **never blocked** for any billing reason (§7).
- **Idempotency**: key every charge on `play_action_id`. Retries, webhook replays and duplicate fill events must never produce a second charge.

### Fee calculation (per action)

```
fee = (segment == DELIVERY) ? min(5.00, 0.001 * filled_value) : 10.00
remaining_cap = MONTHLY_CAP - accrued_this_month
charge = max(0, min(fee, remaining_cap))
```

Round to the paise (2 decimals). Store the uncapped `fee` and the actual `charge` separately, so analytics can see the value the cap waived.

---

## 4. Free-tier limits

Limits must always be visible as a meter ("4 of 5 deep answers left · resets in 3h"). Never show a surprise error.

| Limit | Default | Behaviour when exhausted |
|---|---|---|
| Quick chat answers | Unlimited | — |
| Deep chat answers (multi-step tool use, chart drawing, long analysis) | 5 per rolling 24h | **Fall back to quick mode**, don't block. Show the meter. |
| Chat burst limit | 10 requests / minute | Short cool-down message |
| Backtests | 10 per week | Disable "Run backtest" with reset time |
| Backtest history depth | 1 year | — |
| Play builds | 3 lifetime starter builds | Locked until an unlock (below) |
| Play build unlock | Arming any Play **on paper** grants +10 builds for the current calendar month | Re-earned each month by arming a paper Play |
| Armed live Plays at once | 5 | Must disarm one to arm another |
| AI news-judge checks per Play | 20 per day | Play continues without the judge; card shows it |
| Long conversations | Summarise or roll to a new thread after ~20 turns | Keeps the model context small |

Rules:
- The build unlock is earned by **paper** arming, never by live trading. Rewarding live trades could read as inducing users to trade.
- Treat all defaults as tunable config. These are starting values.
- Classify "quick" vs "deep" server-side by what the request actually uses (tools called, tokens). Don't rely on a client flag.

### Cost-control requirements on the AI side

- Route simple questions to a small, cheap model; only deep answers use the large model.
- Cache shared answers and data (e.g., index snapshots, popular tickers) for short TTLs.
- Company research pages are generated in batch once a day and served static.
- **Plays compile once**: the model turns text into a structured Play; the armed Play runs as plain code on each candle with **no model call**. Only the optional news judge calls a model.
- Log AI cost per user per day. Target: average free user under ~₹5–8 per month.

---

## 5. Billing methods

### 5.1 Primary: UPI AutoPay (variable mandate)

- **When to ask**: when the user arms their **first live Play**, not at signup.
- **Mandate**: variable amount, **maximum ₹499**, monthly frequency, via the payment gateway (Razorpay / Cashfree / PhonePe, all support UPI AutoPay).
- **Mandate screen copy**: "You'll never be charged more than ₹499 a month. You only pay for trades your Plays place."
- **Debit cycle**: on the 1st of each month (IST), debit last month's accrued total.
- If the total is ₹0, skip the debit.
- **Pre-debit notification**: RBI's E-Mandate Framework 2026 requires a notification at least 24 hours before each debit (merchant, amount, date, mandate reference, reason). Trigger the gateway's pre-debit notify API on the last day of the month; debit only after 24h.
- Debits up to ₹15,000 need no extra authentication after registration, so ₹499 is always within limits.
- The user can cancel the mandate from their UPI app at any time. Handle the gateway's `mandate.cancelled` webhook and move the account to the wallet flow.

### 5.2 Fallback: prepaid wallet

- Top-up amounts: ₹100, ₹250, ₹500 via one-time UPI.
- Each billable trade deducts `charge` immediately from the balance.
- The monthly ₹499 cap still applies: once `accrued_this_month` hits ₹499, deductions stop until next month.
- When the balance can't cover the next charge → **new live entries pause** (§7). The trade that is mid-flight still completes; if the balance goes slightly negative, recover it from the next top-up.
- No refunds of wallet balance to the bank in v1 (decide later; see §12).

### 5.3 Which one applies

A user has exactly one active billing method: AutoPay if a mandate is active, otherwise wallet. Live arming requires one of them.

---

## 6. Monthly cycle

```
Throughout month M:   accrue charges per billable trade (capped at ₹499)
Last day of M:        close statement for M; send pre-debit notification (AutoPay users)
1st of M+1 (≥24h):    execute debit for statement M
Day 0 fail → Day 1:   automatic retry
Days 1–3:             in-app + WhatsApp/SMS reminders with one-tap UPI pay link
Day 4:                account → RESTRICTED (new live entries paused)
On payment:           immediately → ACTIVE
```

Each statement shows: list of billable trades (time, Play, segment, fee, charge), subtotal, amount waived by the cap, base amount + GST split, and total.

GST split for display/invoice: `base = total / 1.18`, `gst = total - base` (₹10 = ₹8.47 + ₹1.53).

---

## 7. Account billing states and enforcement

| State | Meaning | Live behaviour |
|---|---|---|
| `NO_METHOD` | No mandate, no wallet balance | Can't arm live Plays. Paper and research work. |
| `ACTIVE` | AutoPay active, or wallet has balance | Everything works |
| `GRACE` | Debit failed, days 0–3 | Everything works; reminders shown |
| `RESTRICTED` | Unpaid after day 3, or wallet empty | **New live entries blocked.** Arming new live Plays blocked. |

**Non-negotiable safety rule: exits always run.**
In every state, any action that **reduces or closes** an existing live position (stop-loss, target, trailing stop, time-based square-off, user-pressed halt) must execute. Only actions that **open or add to** a position may be blocked. Implement this as a check in the Play engine before order placement:

```
if billing_state == RESTRICTED and action.increases_exposure:
    skip action, notify user ("Paused: clear ₹X dues to resume new entries")
else:
    execute
```

The halt button must work in every state. Chat, research, paper Plays and backtests are never blocked for billing reasons.

---

## 8. Before-arm cost disclosure (required UI)

When the user arms a live Play, show an estimated all-in cost per trade:

```
Estimated cost per trade
  Broker brokerage      ₹20   (from the connected broker's published rates)
  GST on brokerage      ₹3.60
  Taxes (STT etc.)      ~₹9   (depends on order size)
  Pivot                 ₹10   (max ₹499 this month · ₹128 used so far)
```

Also show: "Pivot charges only when this Play trades. Exits always run, even with unpaid dues."

Keep a per-broker table of brokerage rates in config (Zerodha ₹20, Dhan ₹20, Upstox ₹20, Angel ₹20, mStock ₹5, Kotak Neo Trade Free API ₹0). Label it as an estimate.

---

## 9. Data model (suggested)

```
billing_accounts
  user_id (pk), state, method (AUTOPAY|WALLET|NONE),
  mandate_id, wallet_balance_paise, grace_started_at, updated_at

mandates
  id, user_id, gateway, gateway_mandate_id, max_amount_paise (49900),
  status (PENDING|ACTIVE|PAUSED|CANCELLED), created_at, cancelled_at

billable_trades
  id, user_id, play_id, play_action_id (UNIQUE), broker, segment (FNO|DELIVERY),
  filled_value_paise, fee_paise, charge_paise, cap_waived_paise,
  billing_month (YYYY-MM, IST), executed_at, created_at

monthly_statements
  id, user_id, billing_month, trade_count, fee_total_paise, charge_total_paise,
  base_paise, gst_paise, status (OPEN|CLOSED|NOTIFIED|PAID|FAILED|WAIVED),
  invoice_number, closed_at, paid_at

payments
  id, user_id, statement_id NULL, type (AUTOPAY_DEBIT|WALLET_TOPUP|PAY_LINK),
  amount_paise, gateway_ref, status, attempt, created_at

wallet_ledger
  id, user_id, delta_paise, reason (TOPUP|TRADE_CHARGE|ADJUSTMENT), ref_id, balance_after_paise, created_at

entitlements
  user_id, period_key, deep_answers_used, backtests_used, builds_available,
  paper_unlock_granted (bool), news_judge_checks (json per play/day)
```

Store money in **paise as integers**. Never use floats for money.

Compute `accrued_this_month` from `billable_trades` (or a counter updated in the same transaction) so the cap can't be exceeded under concurrency. Use a row lock or atomic update on the user's month counter when inserting a charge.

---

## 10. Events and webhooks

Internal events:
- `play.action.filled` → create `billable_trade` (idempotent on `play_action_id`), update counters, deduct wallet if applicable.
- `play.paper.armed` → grant monthly build unlock if not already granted.
- `billing.cap.reached` → notify user once per month ("You've hit ₹499. Live trades are free for the rest of the month.").
- `billing.state.changed` → notify user; Play engine reads the new state.

Gateway webhooks to handle: mandate authorised / cancelled / paused, debit success / failure, top-up success / failure. Verify webhook signatures. Make handlers idempotent.

Analytics to log: free-to-live conversion, live traders per month, trades per live trader, share of users at the cap, revenue per live trader, AI cost per user, collection rate, time in RESTRICTED.

---

## 11. Config constants

```ts
export const PRICING = {
  GST_RATE: 0.18,                 // prices are GST-inclusive
  FNO_FEE_PAISE: 1000,            // ₹10
  DELIVERY_FEE_MAX_PAISE: 500,    // ₹5
  DELIVERY_FEE_PCT: 0.001,        // 0.1%
  MONTHLY_CAP_PAISE: 49900,       // ₹499
  MANDATE_MAX_PAISE: 49900,
  WALLET_TOPUPS_PAISE: [10000, 25000, 50000],
  PRE_DEBIT_NOTICE_HOURS: 24,
  GRACE_DAYS: 3,
  DEBIT_DAY_OF_MONTH: 1,
  TIMEZONE: "Asia/Kolkata",
};

export const FREE_LIMITS = {
  DEEP_ANSWERS_PER_24H: 5,
  CHAT_BURST_PER_MIN: 10,
  BACKTESTS_PER_WEEK: 10,
  BACKTEST_HISTORY_YEARS: 1,
  STARTER_BUILDS: 3,
  PAPER_ARM_UNLOCK_BUILDS: 10,   // per calendar month
  MAX_ARMED_LIVE_PLAYS: 5,
  NEWS_JUDGE_CHECKS_PER_PLAY_PER_DAY: 20,
  THREAD_ROLLOVER_TURNS: 20,
};
```

---

## 12. Out of scope for v1 / open decisions

Decide with founders before or during build:

- **Compliance gate (blocking for live)**: live Plays require exchange empanelment as an algo provider, onboarding by each broker, exchange algo-ID tagging on every order and static-IP rules. Ship live execution **behind a feature flag** that stays off until this is done. Black-box strategies or a Play marketplace would also need SEBI RA registration, so keep Plays user-authored in v1.
- **Market data licensing**: confirm the live data source is licensed for this use, especially for the paper book.
- **GST registration and invoicing**: GSTIN, invoice numbering, B2C invoice format.
- **Small-amount debits**: debit any amount above ₹0, or carry totals under a threshold (e.g. ₹50) into the next month.
- **Wallet refunds**: not in v1.
- **Annual prepay** (e.g. ₹4,999/year with no per-trade fees): later.
- **Broker revenue share** (brokers paying Pivot a cut of brokerage instead of users paying ₹10): later, once there is volume.
- **Own brokerage**: long-term plan; out of scope here.

---

## 13. Acceptance tests

1. A 2-leg F&O straddle Play fires once with both legs filled → exactly one ₹10 charge.
2. The same action's fill webhook arrives 3 times → still one charge.
3. A delivery Play buys ₹2,000 of stock → charge ₹2.00. Buys ₹50,000 → charge ₹5.00.
4. A monthly rebalance fills 15 stocks worth ₹3,00,000 in one action → one ₹5 charge.
5. User at ₹495 accrued fires an F&O action → charge ₹4, cap_waived ₹6. The next action → ₹0, and a single "cap reached" notification is sent.
6. Two actions fill concurrently at ₹490 accrued → total accrued never exceeds ₹499.
7. Order rejected by the broker with zero fills → no charge.
8. Manual order placed from the chart → no charge.
9. Paper Play arms → +10 builds granted once for the month; a second paper arm in the same month grants nothing.
10. RESTRICTED user: a Play entry signal is skipped with a notification; a stop-loss on an open position executes.
11. RESTRICTED user presses halt → all open positions square off.
12. AutoPay debit fails → retry next day → reminders → state RESTRICTED on day 4 → user pays via link → state ACTIVE immediately.
13. Wallet balance ₹3 and next charge ₹10 → the in-flight action completes, balance −₹7; new entries pause; next top-up recovers ₹7 first.
14. Free user exhausts deep answers → next deep request is answered in quick mode with a visible meter, not an error.
15. Mandate cancelled from the UPI app → account switches to wallet/NO_METHOD; open positions' exits still run.
16. Statement for month M shows trades, subtotal, cap waived, base + GST, total; pre-debit notice sent ≥24h before debit.

---

## 14. Suggested build order

1. Config module + money utils (paise, GST split, fee function) with unit tests.
2. `billable_trades` ledger with idempotency and cap-safe accrual.
3. Free-tier entitlements and meters (deep answers, backtests, builds, paper-arm unlock).
4. Play engine billing hook + exposure check ("exits always run").
5. Wallet top-ups and deductions.
6. UPI AutoPay mandate, monthly statement, pre-debit notice, debit, retries, dunning states.
7. Before-arm cost disclosure UI, billing page, statements and invoices.
8. Analytics events.
9. Live execution stays behind the compliance feature flag (§12).
