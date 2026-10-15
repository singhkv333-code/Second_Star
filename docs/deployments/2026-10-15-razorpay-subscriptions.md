# Razorpay subscription integration — 15 October 2026

## Scope

The owner selected Pro/Pro+ subscription purchases, not arbitrary one-time
payments, and approved pushing only these payment changes to `production-live`.
Unrelated local financial-data/chart edits are excluded from this release.

Public `/checkout` now redirects to authenticated `app.pivotnow.in` checkout,
preserving a valid plan and cycle; an unspecified plan opens pricing. Public
pricing links preserve the selection. Legacy arbitrary-amount order creation
returns 410. Its read-only verification endpoint remains for old receipts.

The existing subscription engine now persists checkout intent before provider
creation, resumes a dismissed checkout rather than creating another mandate,
and blocks ambiguous attempts pending reconciliation. Unknown provider outcomes
must be inspected, never automatically retried as a new charge.

Payment confirmation verifies HMAC, owner, capture, invoice/subscription binding,
amount, currency and the provider's active billing period. An authenticated
mandate alone is not paid. A signed-in reconciliation endpoint recovers closed
browsers without accepting client prices or arbitrary subscription IDs.

Webhook storage failures return 503, unfinished events can replay, and successful
events deduplicate. Each application fetches the provider's current state,
including for conflicting events within one cycle. Recovery of a lost create
response requires a durable checkout token, not a bare user ID in provider notes.
Failed initial purchases receive no renewal grace. Database changes are additive
in the existing whole user-state SQLite database.

## Test configuration

Only test keys were supplied. Configuration is in the existing protected
`pivot-clean-kv-india` secret `razorpay-test-runtime`; no credential is committed,
printed or passed through VM command arguments. `CHARTO_BILLING_TEST_EMAILS`
restricts new test checkout to `billing-e2e-20261015@pivotnow.in`. Ordinary
customers cannot purchase paid access using simulated money. Live mode has
not been configured or approved by this rollout.

Prices come from `plans_catalog.json`, unchanged:

| Plan | Cycle | INR | Test provider plan |
|---|---|---:|---|
| Pro | Monthly | 499 | `plan_To61dcTbBIwYMW` |
| Pro | Annual | 5,388 | `plan_To61drmxkPnET6` |
| Pro+ | Monthly | 999 | `plan_To61e5DQAWBqUb` |
| Pro+ | Annual | 10,788 | `plan_To61eHiIEEuowk` |

Test webhook `To673LwnDWC9E2` points to
`https://app.pivotnow.in/billing/webhook` for subscription lifecycle events.
The v1 merchant API takes an event-name → boolean map (not the partner API's
array). Existing unrelated hooks were not changed.

## First actual provider transaction

At the owner's request, payment testing was prioritized before deployment.
A hosted **test-mode** Pro monthly subscription was created and paid using
Razorpay's `success@razorpay` UPI simulator. The provider API independently
confirmed:

- Subscription `sub_To69as9BOY2e5Q`: `active`, `paid_count: 1`.
- Payment `pay_To6DMF0VzFLB5W`: `captured: true`, `amount: 49900`, `currency: INR`.
- Invoice `inv_To69bekA5rGfUX`: `paid`, `amount_paid: 49900`.
- No real money was charged. This provider smoke test is not proof that the
  website has activated an account; that is a separate deployment/E2E gate.

The screenshot's standard test card was rejected as ineligible for recurring
payments; Razorpay's documented subscription credit card was unavailable for
this merchant. UPI simulation succeeded. Do not claim card subscriptions work
on this account until merchant card eligibility has been resolved.

## Verification / remaining rollout gates

- 68 isolated backend billing/entitlement/HTTP tests pass, including concurrent
  checkout, invalid signature/owner/price, authorized-only payments, replay after
  provider/storage failure, stale events, capture and invoice binding.
- 17 public-checkout/legacy-receipt/redirect UI and API tests pass.
- TypeScript checks pass for both Next apps.
- Production release, protected VM configuration and a signed-in website
  purchase must be verified before claiming the full account journey works.
- Live keys/plans/webhook, business/tax details, explicit refund terms and
  provider activation remain launch prerequisites. No live charge is authorized
  as a test by this change.

Rollback source/builds using the release manager's retained backups. Disable
test checkout by removing its service environment drop-in and restarting only
Charto. Never restore an older whole user database over newer customer state;
retain additive billing tables and the payment audit trail.

References: [Subscription testing](https://razorpay.com/docs/payments/subscriptions/test/),
[test instruments](https://github.com/razorpay/markdown-docs/blob/master/payments/payments/test-card-details.md),
[webhook validation](https://razorpay.com/docs/webhooks/validate-test/).
