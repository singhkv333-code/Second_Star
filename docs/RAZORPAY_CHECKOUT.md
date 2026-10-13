# Pivot Standard Checkout

The Vercel project `pivotnow` is linked to `charto/web`, a Next.js 15 App
Router app. Its `/checkout` page and same-origin Node.js API routes implement
Razorpay Standard Web Checkout. The landing footer links to Checkout.
Existing Charto subscription billing remains in `charto/data/billing.py`.
This one-time checkout does not grant plans or change those subscriptions.

## Local setup

```sh
cd charto/web
pnpm install
# Fill .env using .env.example; retain any existing environment settings.
pnpm dev --port 3011
```

Open `http://localhost:3011/checkout`, enter an INR amount (minimum ₹1), and
click **Pay with Razorpay**. Test keys display a test-mode notice. Complete a
test payment using Razorpay's test instruments; confirm the payment reference
appears. Also test dismissing the modal and the test failure option.

`RAZORPAY_KEY_ID` and `RAZORPAY_KEY_SECRET` are server environment variables.
`NEXT_PUBLIC_RAZORPAY_KEY_ID` is optional; checkout uses the public key returned
by order creation so the modal always uses the same account as its order.
Never expose the secret through a public variable. `.env` files are already
ignored by the repository; the local credentials are not committed.

## API and verification

- `POST /api/create-order`: `{amount: 100, currency: "INR", receipt?: "..."}`.
  Amount is an integer in paise; receipt is at most 40 characters. Returns
  `order_id`, `amount`, `currency`, and public `key_id`. Issues an authenticated
  HttpOnly receipt cookie valid for one hour, bound to the originating browser.
- `POST /api/verify-payment`: send `razorpay_payment_id`,
  `razorpay_order_id`, `razorpay_signature` with that cookie. Verifies the HMAC
  using the server-issued order ID with a constant-time comparison, then
  fetches the payment and checks the order, amount and currency. Captured
  payments return 200 with `paid: true`; authorized payments return 202 with
  `paid: false`. Missing fields/bad signatures return 400. Missing/expired
  order receipts or provider authentication failures return 401. Other
  provider failures return 500; missing configuration returns 503.

No database writes or fulfillment occur. Payment status is read from Razorpay,
so repeated verification is safe. Verification failure offers a status check
without creating a second order. Keep the tab open to finish verification.
For recoveries after closing the tab, use the Razorpay dashboard. Adding
subscription fulfillment or durable receipts requires an explicit product
mapping and a verified webhook flow, rather than treating this page as a plan
purchase. The entered amount is a one-time payment, not a product price.

## Domain deployment

Set the two server variables in the **pivotnow** Vercel project's desired
environment, then deploy `charto/web` so `https://pivotnow.in/checkout` and
both API routes share the same origin. Local `.env` files are not uploaded.
The domain configuration lists project `pivot`, but the live aliases for
`pivotnow.in` and `www.pivotnow.in` serve a `pivotnow` deployment. Verify the
actual alias with `vercel alias ls` and update both aliases explicitly after
deploying; a project production deployment alone may not advance these domains.
The existing domain configuration redirects `pivotnow.in` to
`www.pivotnow.in` (HTTP 307). The browser follows this before checkout; direct
API clients should POST to `https://www.pivotnow.in/api/...`.
Test keys collect no real money. Before live payments, use live keys and
confirm the approved website/domain and automatic payment capture settings
in Razorpay Dashboard. No live credentials were supplied here.

Checks:

```sh
pnpm typecheck
pnpm exec vitest run
```

Reference: [Razorpay Standard Checkout integration](https://razorpay.com/docs/payments/payment-gateway/web-integration/standard/integration-steps/).

## Validation on 2026-10-10

Production build and typecheck passed; 27 automated backend/frontend checks
passed. On the public domain, a ₹1 test order was created, the Razorpay test
modal opened, invalid signatures returned 400, and invalid amounts/missing
verification fields returned 400. No test payment was completed; finish the
success/failure payment scenarios manually using Razorpay test instruments.

## Files

Created:
- `charto/web/.env` (ignored local test credentials)
- `charto/web/app/api/create-order/route.ts`
- `charto/web/app/api/verify-payment/route.ts`
- `charto/web/app/checkout/page.tsx`
- `charto/web/components/RazorpayCheckout.tsx`
- `charto/web/lib/razorpay-server.ts`
- `charto/web/tests/razorpay-routes.test.ts`
- `charto/web/tests/razorpay-checkout.test.tsx`
- `charto/web/vitest.config.ts`
- `docs/RAZORPAY_CHECKOUT.md`

Modified:
- `charto/web/.env.example`
- `charto/web/components/AppBootstrap.tsx`
- `charto/web/components/landing/PivotLanding.tsx`
- `charto/web/package.json`
- `charto/web/pnpm-lock.yaml`
- `charto/web/package-lock.json`

The existing root `.gitignore` already ignores `.env` and `.env.*`, while
allowing `.env.example`, so no additional ignore rule was needed.
