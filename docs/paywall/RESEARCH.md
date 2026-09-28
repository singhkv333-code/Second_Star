# Paywall research: how SaaS separates free and paid users

Verifier, 2026-09-28. Sources are linked inline. Where a source was only a secondary summary,
it says so. Check the TradingView numbers against the live page before copying them into
marketing copy, because TV changes its tables often.

---

## 1. Entitlements, feature flags and plans

- **Three separate things.**
  - A *plan* is what someone buys (Free, Pro, Pro+).
  - An *entitlement* is what the plan grants: a boolean feature (`custom_timeframes`) or a limit (`alerts.price = 400`).
  - A *feature flag* is a rollout switch (is this code path on at all). It is not a commercial grant.
- **Stripe Entitlements**: a *Feature* is "a monetizable ability or functionality in your system". You attach features to Products. Stripe then keeps *Active Entitlements* per customer and emits `entitlements.active_entitlement_summary.updated` when a subscription changes. Your app provisions or deprovisions access on that event. ([Stripe: Entitlements](https://docs.stripe.com/billing/entitlements), [Feature object](https://docs.stripe.com/api/entitlements/feature), [Active entitlements](https://docs.stripe.com/api/entitlements/active-entitlement/list))
- **Stigg / Schematic**: the plan catalog is data. Code asks `getMeteredEntitlement(customer, featureId, requestedUsage)` and gates on `hasAccess`. Usage reporting is a **backend** operation, separate from the (client-mirrorable) check. Limits can be hard (block) or soft (warn). ([Stigg: gating access](https://docs.stigg.io/guides/quick-start-guides/gating-access-to-features), [Stigg entitlements](https://www.stigg.io/product/entitlements))
- **The pattern all of them share**:
  - A catalog (JSON or DB) maps `plan → {feature_key: bool | int | "unlimited"}`.
  - One resolver answers `entitlement(user, key)`.
  - Code is written against **feature keys**, never `if plan == "pro"`.
  - Changing a price or a limit then means editing data, not code. A grandfathered cohort is a second catalog row.

## 2. Server-side enforcement and the error contract

- **The server is the authority. The client only mirrors it for UX** (greyed buttons, "3 of 5 used"). Every gated endpoint checks the entitlement after auth and before any side effect. A client that lies about its plan changes nothing.
- **Status codes in common practice.** ([Abstract API on 402](https://www.abstractapi.com/guides/http-status-codes/402), [link.sc on 402](https://link.sc/blog/http-402-payment-required-explained); both are secondary summaries)
  - **401**: not signed in.
  - **402 Payment Required**: authenticated and allowed in principle, but the plan does not include this, or the quota is spent. The fix is money, so the client routes it to an upgrade.
  - **403**: an authorization failure that paying will not fix (another user's object, an admin route).
  - **429**: a temporary rate limit (requests per second or minute) with `Retry-After`. It is not a plan quota.
- **Body.** Use a machine-readable body in the spirit of RFC 9457 Problem Details:
  ```json
  {"error": "Free includes 20 price alerts. You have 20.",
   "code": "limit_reached",           // or "feature_not_in_plan"
   "feature": "alerts.price",
   "limit": 20, "used": 20,
   "plan": "free", "upgrade_to": "pro",
   "resets_at": null}                 // epoch for windowed quotas
  ```
  - `error` is the human line. Pivot's other routes already use `{"error": ...}`, so the FE keeps working.
  - `code` and `feature` are what the FE switches on.

## 3. Metering and quotas

- **There are two kinds of limit. Count each one differently.**
  - **Live-object caps** (alerts, saved screens, layouts): count the rows that exist now in a counting state. Do not keep a counter. Deleting one frees a slot, and a counter cannot drift. TradingView counts *active* alerts ([TV pricing](https://www.tradingview.com/pricing/)).
  - **Consumption quotas** (AI credits, AI summaries): an append-only **usage ledger** `(user_id, feature, amount, window_key, idempotency_key UNIQUE, ts)`. "Used" is `SUM(amount)` for the current window.
- **Idempotency.**
  - The client sends an `Idempotency-Key` (a UUID per user action). The server records the debit under a UNIQUE constraint, so a retry returns the first result and does not debit again.
  - Stripe stores the first status and body per key for 24h and replays it ([Stripe idempotency](https://stripe.com/docs/idempotency)).
  - Lago dedupes usage events by `transaction_id` ([Lago: ingest usage](https://getlago.com/docs/guide/events/ingesting-usage)).
- **Reset windows.**
  - The choices are a calendar month, a daily window, or the billing-anchor period (the subscription's own cycle).
  - For a mostly B2C Indian user base, a daily quota should reset at **00:00 IST (Asia/Kolkata)**, not UTC. At UTC it would reset at 05:30 IST, mid-morning pre-market.
  - A monthly quota is fairest on the **billing anchor** for paid users and on the calendar month for Free.
  - Store `window_key` (e.g. `d:2026-09-28` or `m:2026-09`) on each ledger row.
- **Race safety.**
  - The check and the debit must be one atomic step: under the same lock (Pivot already has `ds._users_lock` over `charto_users.db`), or as a conditional write: `INSERT … SELECT … WHERE (SELECT SUM(amount) …) + :n <= :limit`.
  - For live-object caps, do `COUNT(*)` and `INSERT` inside the same lock. The current `alerts.api_create` counts under one lock acquisition and inserts under another, which is a TOCTOU window.
- **When to debit AI.**
  - Debit **before** the expensive call, and reverse on a hard failure where no model output was produced (5xx or a provider outage). Otherwise one retry storm drains a Free user.
  - Stigg's guidance is the same: pass the usage you are about to consume and gate on it before the compute runs ([Stigg governance](https://www.stigg.io/blog-posts/introducing-stigg-2-0-governance-the-first-milliseconds-latency-usage-control-layer-for-ai)).

## 4. Downgrade, cancel and grace

- **Never delete over-limit data.**
  - TradingView keeps extra alerts and layouts linked to the account but disables them past the free limit ("deactivated, not erased"). Re-subscribing restores them ([secondary summary](https://www.tv-hub.org/guide/tradingview-alerts-setup)).
  - Zuora's guidance: pause first, never cancel immediately, so reactivation is one click ([summary](https://dodopayments.com/blogs/involuntary-churn-failed-payments)).
- **Timing.** A TV downgrade "will be activated upon the expiration of your current subscription" ([TV: downgrade](https://www.tradingview.com/support/solutions/43000485437-i-d-like-to-downgrade-my-subscription/)). So cancel means paid entitlements run to `current_period_end`, and only then drop to Free.
- **What to freeze.**
  - Over-limit live objects become read-only or paused. Keep the newest N (or the ones the user picks) running.
  - Everything stays visible, editable and deletable. Creating a new one is refused until the user is under the limit.
  - Deleting always works, because it is the way back under the limit.
- **Dunning and grace.**
  - Keep access through the processor's retry window, then drop to Free.
  - A 3–7 day grace period is common ([Kinde](https://www.kinde.com/learn/billing/churn/dunning-strategies-for-saas-email-flows-and-retry-logic/)).
  - A Razorpay subscription moves to `pending` on a failed charge and retries on "T+3 days" (once a day for 3 days). It then moves to `halted` ([Razorpay retries](https://razorpay.com/docs/payments/subscriptions/payment-retries/)).
  - So: `pending` keeps paid access, and `halted` drops to Free and freezes the extras.

## 5. TradingView: the table the sheet mirrors

**Numbers** (TV pricing page, fetched 2026-09-28,
[tradingview.com/pricing](https://www.tradingview.com/pricing/)). The INR prices are per month,
billed annually.

| | Basic (free) | Essential ₹995 | Plus ₹2,095 | Premium ₹4,195 | Ultimate ₹17,333 |
|---|---|---|---|---|---|
| Charts per tab | 1 | 2 | 4 | 8 | 16 |
| Indicators per chart | 2 | 5 | 10 | 25 | 50 |
| Historical bars | 5K | 10K | 10K | 20K | 40K |
| Active price alerts | 3 | 20 | 100 | 400 | 1,000 |
| Active technical alerts | 20 | 20 | 100 | 400 | 1,000 |
| Active watchlist alerts | — | — | — | 2 | 15 |
| Parallel chart connections | 2 | 10 | 20 | 50 | 200 |

- **Basic lacks** custom time intervals, ad-free, multi-condition alerts and second-based intervals ([in.tradingview.com/pricing](https://in.tradingview.com/pricing/)). Those start at Essential.
- **Alert expiry** is 1 month on Basic and 2 months otherwise. **Open-ended alerts are Premium and Ultimate only**, and are not available for watchlist alerts ([TV support](https://www.tradingview.com/support/solutions/43000688759-i-upgraded-to-the-premium-plan-but-my-alerts-still-expiring-in-2-months/)). One fetch of the INR page listed "alerts that don't expire" from Essential. That contradicts the support article; trust the article.
- Secondary sources list a watchlist size of 30 symbols on Basic and 1,000 on paid plans ([summary](https://www.easytradeweb.com/en/tradingview-watchlist-limits/)).

**Definitions, as TV enforces them**

- **Price alert vs technical alert.**
  - A price alert uses "only a symbol … and a price value" with Crossing, Crossing Up, Crossing Down, Greater Than or Less Than ([TV: price alerts](https://www.tradingview.com/support/solutions/43000763313-how-to-use-price-alerts/)).
  - A technical alert is "set for an indicator, drawing, or strategy". "Entering/Exiting channel",
    "Inside/Outside channel" and "Moving up/down (%)" are **always technical**. **Drawing alerts are technical** ([TV: technical alerts](https://www.tradingview.com/support/solutions/43000763315-getting-started-with-technical-alerts/)).
  - Mapped to Pivot's grammar, this is the classifier in ENDPOINTS.md §4.
- **Watchlist alert.** One alert object over a whole watchlist, with one condition evaluated per symbol. Symbols added to the list later are picked up automatically ([TV: watchlist alerts](https://www.tradingview.com/support/solutions/43000739708-watchlist-alerts-your-trading-edge/)). It counts as one watchlist alert, not N price alerts.
- **Parallel chart connections.**
  - "Each tab in the web version and apps is counted as one connection". A browser tab plus an app is two.
  - At the limit, "the oldest connection (the earliest opened tab) will get closed", with a
    "Restore connection" button.
  - It is not a hard refusal: the oldest connection is evicted ([TV: parallel chart connections](https://www.tradingview.com/support/solutions/43000694474-parallel-chart-connections/)).
  - A multi-chart layout in one tab is **one** connection. For Pivot this means counting per tab/session, not per `/stream`.
- **Charts per tab.** The number of panes in one multi-chart layout (TV's grid: 1/2/4/8/16).
- **Historical bars.**
  - The number of **intraday** bars you can scroll back on one chart: 5,000 on Basic, 10K on Essential and Plus, 20K on Premium, 40K on Ultimate. TV adds "additional bars back to the beginning of the week, month or year".
  - **Daily and above show all history on every plan** ([TV: historical intraday data](https://www.tradingview.com/support/solutions/43000480679-historical-intraday-data-bars-and-limits-explained/)).
- **Indicators per chart.** Studies applied simultaneously on one chart pane.
- **Custom timeframes.** Arbitrary intervals (e.g. 7m, 2h, 3D) beyond the preset list.

**Where the user's sheet departs from TV**

- Charts per tab: Free gets 4, where TV Basic gets 1.
- Parallel charts: Free gets 10, where TV Basic gets 2.
- Price alerts: Free gets 20, where TV Basic gets 3.
- Expiry: 2 months on Free and "never" on Pro+, where TV has 1 month on Basic and open-ended on Premium and above.
- Pivot's Free is roughly TV Essential, at ₹0. That is a deliberate positioning choice, not an error.

## 6. India payments

**Razorpay Subscriptions**

- **Flow**: Plan (`period`, `interval`, `item.amount` in paise) → Subscription (`plan_id`, `total_count`, `customer_notify`) → Checkout with `subscription_id` → first-payment authentication creates the mandate. ([Integration guide](https://razorpay.com/docs/payments/subscriptions/integration-guide/))
- **States**: `created → authenticated → active`, then `pending` (charge failed, retrying), then `halted` (retries exhausted). Also `cancelled` (terminal, "cannot be restarted"), `completed`, `expired` and `paused`. ([States](https://razorpay.com/docs/payments/subscriptions/states/))
- **Webhooks**: `subscription.authenticated`, `.activated`, `.charged` (every successful cycle), `.completed`, `.updated`, `.pending` (can repeat), `.halted`, `.cancelled`, `.paused` and `.resumed`. ([Subscription webhooks](https://razorpay.com/docs/webhooks/subscriptions/))
- **Signature**: `X-Razorpay-Signature` = hex HMAC-SHA256 of the **raw request body**, keyed with the **webhook secret** (not the API key secret).
  - Verify before `json.loads`, and compare in constant time (`hmac.compare_digest`).
  - After rotating the secret, retried old events still carry the old signature. ([Validate webhooks](https://razorpay.com/docs/us/webhooks/validate-test), [dev.to explainer](https://dev.to/eventdock/how-to-verify-razorpay-webhook-signatures-and-why-it-is-not-the-payment-signature-1pei))
- **Delivery**: at-least-once, so duplicates are expected. Dedupe on `x-razorpay-event-id`. Return 2xx quickly. Failures retry with backoff for 24h, after which the webhook is **disabled**. ([Best practices](https://razorpay.com/docs/webhooks/best-practices/))
  - Order is not guaranteed. Drive state from the subscription entity inside the payload (`status`, `current_end`), not from the event name alone.

**RBI e-mandate rules** (Digital Payments – E-Mandate Framework, 2026, published 21 Apr 2026;
it consolidates eight earlier circulars)
([LexOrbis](https://www.lexorbis.com/rbis-digital-payments-e-mandate-framework-2026-consolidated-directions-for-recurring-digital-transactions/),
[RocketPay summary](https://rocketpay.co.in/blog/rbi-e-mandate-recurring-payments-15000))

- Registering the mandate needs one-time AFA (additional factor authentication).
- Recurring debits **up to ₹15,000** run without AFA. Pro ₹499, Pro+ ₹999 and even annual Pro+ (₹899 × 12 = ₹10,788) are all under this limit.
- A **pre-debit notification at least 24h before** each debit, with an opt-out, and a post-debit confirmation. The processor or issuer sends it; Razorpay handles this for Subscriptions.
- The customer can pause or cancel a mandate at any time, so a `cancelled` or `halted` webhook can arrive without any action in-app.

**GST**

- SaaS / OIDAR is **SAC 9983 at 18%**, B2B and B2C alike ([PayPro](https://payproglobal.com/saas-sales-tax/india/), [Lemon Squeezy](https://www.lemonsqueezy.com/blog/indian-sales-tax-gst-saas)).
- For B2C in India, the common practice is to **display prices inclusive of GST**. Indian consumer-pricing norms (MRP inclusive of all taxes) point the same way.
  - Decide whether ₹499 means ₹499 all-in (₹422.88 + ₹76.12 GST) or ₹499 + GST = ₹588.82.
  - Whichever it is, the checkout must show the same number as the pricing page. This is a business and tax-advisor decision.
- The invoice needs GSTIN, SAC 9983, rate and amount.

**Stripe in India**

- New Indian accounts have been **invite-only since May 2024**, prioritised for businesses selling internationally. RBI's payment-aggregator rules add video-KYC liveness from 1 Jan 2026. ([Stripe support](https://support.stripe.com/questions/stripe-accounts-are-invite-only-in-india), [TechPortal](https://thetechportal.com/2024/05/31/stripe-limits-new-sign-ups-in-india-to-invite-only-amid-stringent-regulatory-compliance/))
- Stripe India recurring payments also follow the AFA and pre-debit rules ([Stripe docs](https://docs.stripe.com/india-recurring-payments)).
- **Razorpay is the practical default.**

## 7. AI credits in chat products

- **The models in the market**:
  - Rolling time windows (Claude).
  - Per-model caps with a fallback to a cheaper model at the cap (ChatGPT: "continues on another available reasoning model and shows a reset time").
  - Separate daily and monthly pools per expensive feature (Perplexity: daily searches plus a monthly Deep Research allowance).
  - Dollar-denominated usage (Cursor).
  - Paid top-up credits that expire after 12 months (OpenAI). ([tokenkarma comparison](https://tokenkarma.app/ai-usage-limits-comparison/), [ChatGPT limits 2026](https://www.izzedo.chat/blog/chatgpt-limits); secondary summaries)
- **Common mechanics**:
  1. The debit unit is **one user turn**, not tokens, because a user can reason about a turn. Expensive modes (deep research, execution mode, long tool loops) cost more than 1 by an explicit multiplier.
  2. The remaining balance is visible before sending.
  3. At zero, the answer is never silently worse. Either a clear refusal naming the reset time and the upgrade, or an explicit, announced fallback.
  4. System-generated calls (follow-up suggestions, titles, background summaries) are **not** debited to the user.
  5. A failed turn (provider outage, 5xx with no output) is refunded.

---

## Recommendations for Pivot

1. **Catalog as data**: `plans_catalog.json` with keys such as `ai.credits.daily`, `ai.summaries.daily`, `alerts.price`, `alerts.technical`, `alerts.watchlist`, `alerts.expiry_days`, `screens.saved`, `alerts.screen`, `chart.indicators`, `layout.charts`, `chart.parallel`, `chart.custom_tf`, `bars.intraday_max`, `ads.free`. Use `null`/`"unlimited"` for unlimited. Code checks keys only.
2. **Plan state in `charto_users.db`** (the user plane, one lock, per DATA_MAP): `subscriptions(user_id, plan, status, current_period_end, provider_sub_id)` plus an append-only `usage_ledger`. Never a second DB.
3. **One helper, called after `_auth_user`, before any side effect**. It returns `402 {error, code, feature, limit, used, plan, upgrade_to, resets_at}`. Reserve 429 for the existing capacity 503s and rate limits.
4. **Alerts**: derive price vs technical from the spec with the TV rule (ENDPOINTS §4). Count **armed** rows per class (TV counts *active* alerts), and gate create, re-arm (`state:"armed"`) and any class-changing edit. With armed-only counting, the lead's downgrade auto-pause frees slots, and re-arm is where the limit bites. Today's `MAX_PER_USER` counts `state!='fired'`; pick one rule and use it everywhere. Put the gate inside `alerts.api_create`/`api_patch`, so chat (`set_alert`/`update_alert`) and HTTP share it. Close the COUNT→INSERT lock gap while there.
5. **Expiry: clamp, do not refuse**. On Free and Pro, `expires = min(requested or ∞, now + plan_days)`, including when a patch sends `expires: null`. Say the date in the tool `_note`.
6. **AI credits**: debit 1 per user `/chat` turn, keyed by a client `Idempotency-Key` (fall back to `chat_id` + turn index). Daily window at 00:00 IST. Refund on a turn that produced no output. Do not debit `/suggest`, followups or titles. Decide the anonymous policy: require sign-in for chat, or give anonymous users a small per-IP allowance.
7. **Resolve the sheet's ambiguity before coding the numbers**: whether AI credits are daily or monthly (15/200/500 reads daily; TV-like products run monthly pools of about 30× that); what
   "AI summaries" is (no such endpoint exists: `explain_move`? company-page research?); what the
   "Alerts F" row is; and historical bars for Pro and Pro+ (suggest 20K/40K, mirroring TV).
8. **Historical bars**: follow TV. Cap **intraday** depth only (`/bars`, `/indicator`, chat `get_bars`, clamping both `limit` and the `to` cursor). Daily and above stay unlimited on every plan.
9. **Charts per tab**: enforce on `/layouts` save/copy (`len(spec.charts)`) and in `open_chart(layout=)`. The FE greys out grids above the limit using `/auth/me` entitlements.
10. **Parallel charts**: follow TV semantics (one tab = one connection, evict the oldest, never hard-refuse). This needs `/stream` to carry identity, so it can come after v1.
11. **Build before gating**: watchlist alerts, screen alerts and saved screens have **no backing store in charto**, and custom timeframes do not exist either. Ship these rows as "coming" or build them first. Never show a gate on a feature that is not there.
12. **Downgrade**: entitlements last to `current_period_end`. Over-limit alerts are auto-**paused** (newest kept) with a visible reason, and never deleted. Layouts stay openable. New creation is refused until the user is under the limit.
13. **Razorpay**: verify HMAC over the raw body with the webhook secret, dedupe on `x-razorpay-event-id` in a `billing_events` table, drive state from the payload's subscription `status`, and treat `pending` as paid and `halted`/`cancelled`-past-period-end as Free.
14. **Pricing display**: decide GST-inclusive (recommended for B2C) and show the identical rupee figure on the pricing page, at checkout and on the invoice.
15. **Lock down what is open today** (ENDPOINTS §7): `/live` (admin), anonymous `/chat` and `/suggest`, pivotted `/research/chat/stream`, and anonymous `/execution/backtest`. A paywall over an open LLM route is decoration.
