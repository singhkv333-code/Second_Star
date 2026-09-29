# Pivot plans and paywall

Built 2026-09-28. The background research is in `RESEARCH.md`, every HTTP route is audited in `ENDPOINTS.md`, and the build log between the two workers is in `BOARD.md`.

## How it works

- **The plans are data.** `charto/data/plans_catalog.json` defines the plans (free, pro, pro_plus, plus a signed-out pseudo-plan) and gives a value for every feature key. Code asks what a feature is worth for a user (`alerts.price`, `ai.credits` and so on). It never checks `plan == "pro"`. Changing a number or adding a plan is a catalog edit.
- **One source of truth.** Plans, subscriptions and usage live in `charto_users.db`, next to the users. Pivot's API and pivotted never write that file. They call charto's `POST /billing/consume`.
- **There are four kinds of feature:**
  - `flag`: on or off.
  - `limit`: a cap on live objects, counted when one more is written.
  - `quota`: metered usage over a window, recorded in a ledger.
  - `value`: a parameter, such as an alert's expiry or history depth.
- **The server decides.** Every gated write is checked in the backend. The browser's `Plan` module (`preview/js/plan.js`) only mirrors the plan so the UI can explain a limit at click time.
- **Metering is idempotent.**
  - Each debit carries a key. `UNIQUE(subject, feature, idem_key)` makes a retried request free.
  - A failed AI turn is refunded with a reversing row. Refunds need the service key, so a user can never refund themselves.
- **The effective plan is computed when it is read** from the subscription row and any grant. A lapsed plan lapses without a cron job.
- **A downgrade never deletes anything.**
  - The newest armed alerts over the cap are paused with the note `[paused: over plan limit]`, and expiries are brought within the plan's limit.
  - Layouts still open. Only new writes over the cap are refused.
  - This runs on the webhook, on admin grants, and in an hourly sweep for plans that lapse without any event.

## The refusal contract

Every plan refusal from every service returns HTTP **402**:

```json
{"error": "Your Free plan allows 20 price alerts.", "code": "plan_limit",
 "feature": "alerts.price", "plan": "free", "limit": 20, "used": 20,
 "upgrade_to": "pro", "resets_at": 1790793000}
```

- `code` is one of `plan_limit`, `feature_locked`, `quota_exhausted` or `evicted`.
- `resets_at` appears on quotas only.
- 403 is reserved for authorization that paying cannot fix. 429 and 503 remain rate and capacity limits.

## What is enforced, and where

| Sheet row | Key | Enforced at |
|---|---|---|
| AI credits 15 / 200 / 500 a month, 1 credit = 1 prompt | `ai.credits` (quota, monthly; paid plans reset on the billing date, others on the 1st, IST) | charto `POST /chat`; pivot `POST /chat` and `/chat/stream`; pivotted research chat. Signed out: 3 a month per hashed IP |
| Price alerts 20 / 400 / 1000 | `alerts.price` (armed) | `alerts.api_create` / `api_patch`: HTTP and chat `set_alert` / `update_alert` |
| Technical alerts 20 / 100 / 1000 | `alerts.technical` (armed) | same. TradingView's rule applies: indicator, average, volume, profile, detector, **drawing**, channel and %-move alerts count as technical |
| Multi-condition alerts ✓✓✓ | `alerts.multi_condition` | same |
| Alert expiry 2m / 6m / never | `alerts.expiry_days` 60 / 180 / null | same. The expiry is brought within the limit rather than refused, and the reply states the date |
| Indicators per chart 5 / 10 / all | `chart.indicators` | layout save (server); the indicator menu and chat-added indicators (client) |
| Charts per tab 4 / 8 / 8 | `chart.panes` | layout save and the `open_chart` tool (server); the grid menu (client) |
| Parallel charts 10 / 20 / 50 | `chart.parallel` | `POST /charts/lease` heartbeat. The oldest tab is evicted, as on TradingView |
| Historical bars 10K / – / – | `chart.history_bars` | `/bars` and `/indicator`, intraday only. Daily and longer charts are unlimited on every plan, as on TradingView |
| Watchlists U / U / U | `watchlists` null | nothing to enforce |

## API

| Route | Auth | Purpose |
|---|---|---|
| `GET /billing/plans` | public | The pricing table (`gst_inclusive: true`) and whether checkout is live |
| `GET /billing/me` | optional | Plan, subscription and every feature with its current usage |
| `POST /billing/checkout {plan, cycle}` | user | Creates a Razorpay subscription and returns `{subscription_id, key_id}` for Checkout.js |
| `POST /billing/verify {razorpay_payment_id, razorpay_subscription_id, razorpay_signature}` | user | HMAC check, then fetches the subscription from Razorpay and activates it |
| `POST /billing/cancel` | user | Cancels at the end of the cycle; the user keeps the plan until `period_end` |
| `POST /billing/webhook` | HMAC | Uses the raw-body signature and deduplicates on `x-razorpay-event-id`. Stale events for an older cycle are ignored |
| `POST /billing/consume {feature, idem_key, refund?}` | user bearer or `X-Internal-Key` | Metering for other services. Refund requires the service key |
| `POST /admin/billing/grant {email, plan or feature+value, days, reason}` | `CHARTO_ADMIN_EMAILS` | Comps and custom deals, each with a reason and an end date |
| `POST /charts/lease {tab_id, release?, reclaim?}` | optional | Parallel-chart heartbeat |

## Switching on payments (nothing charges until this is done)

1. In the Razorpay dashboard, create 4 plans: Pro monthly ₹499, Pro annual ₹5,388, Pro+ monthly ₹999 and Pro+ annual ₹10,788. Prices are GST-inclusive.
2. Set these on the VM:
   - `RAZORPAY_KEY_ID`
   - `RAZORPAY_KEY_SECRET`
   - `RAZORPAY_WEBHOOK_SECRET`
   - `RAZORPAY_PLAN_PRO_MONTHLY`, `…_PRO_ANNUAL`, `…_PRO_PLUS_MONTHLY`, `…_PRO_PLUS_ANNUAL`
3. Add a webhook to `https://<host>/billing/webhook` for the `subscription.*` events.
4. Set the same random `CHARTO_INTERNAL_KEY` for charto, pivot and pivotted. Without it:
   - refunds from pivot and pivotted are refused;
   - pivot accounts that have no charto session are not metered. The turn is allowed and a warning is logged.
5. Set `CHARTO_ADMIN_EMAILS` for the grant route.

Until step 2 is done, checkout, verify, cancel and webhook answer 503 `billing_unavailable`, which is an honest boundary. Admin grants work regardless.

## Open, to settle last

Settled 2026-09-29:
- AI credits are monthly for every plan, including signed out (3 a month per hashed IP).
- 1 credit = 1 prompt. Follow-ups, titles and failed turns cost nothing.
- AI summaries are an upcoming feature for financial documents. The key and numbers are ready; nothing consumes them yet.

Still open:
1. **The "Alerts F" row.** It has no numbers, and no fundamental alert exists. The key `alerts.fundamental` is set to 0 for everyone.
2. **Historical bars for Pro and Pro+.** These are blank on the sheet and built as unlimited. TradingView uses 20K / 40K.
3. **Not built yet, so nothing is gated:**
   - watchlist alerts (Pro ✓)
   - screener alerts (3 / 50 / 75)
   - saved screens (5 / 50 / 50)
   - custom timeframes (Pro ✓)
   - ads (Ad-free Pro ✓; Pivot shows no ads)

   The keys and numbers are in the catalog, ready.
4. **Grandfathering.** Real accounts that already hold more than 20 armed price or technical alerts are paused by the next hourly sweep once this ships. The options are a launch comp grant, or a one-off `entitlement_grants` row per existing account.
5. **Unmetered model routes:**
   - `POST /audio/transcribe` (paid speech-to-text, signed in)
   - `POST /suggest` (legacy, anonymous)
6. **Security, unrelated to the paywall:**
    - `GET /live` is an unauthenticated admin route that starts and stops venue drivers.
    - `POST /execution/backtest` is anonymous.
