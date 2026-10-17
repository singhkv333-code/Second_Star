# site/

Two things share this Next.js app:

- **www.pivotnow.in**, the marketing site, on Vercel: the landing page, about,
  pricing, checkout, the waitlist, legal pages (terms, privacy, refunds,
  cancellation, contact), `robots`, `sitemap` and `llms.txt`. It carries no
  stock links; `/stock` and `/stocks` redirect to the platform. It is released by
  hand from a prebuilt copy (`docs/deployments/2026-10-13-pivotnow-domain.md`).
- **`/paper`, `/strategies`, `/brokers` and `/api/waitlist` on the VM**, served
  by the `pivot-site` unit on :5175 behind nginx. These overlap with pages in
  `app/`. They stay until nginx sends those routes to the app instead.

## Running it

```sh
cd site && npm install && npx next dev -p 5175
```

`.env.local` (not in git, and never shipped by the deployer) sets:

| Variable | What |
|---|---|
| `NEXT_PUBLIC_PIVOT_API_BASE` | Relative `/api`. nginx proxies it to the dataserver on the VM, and `next.config.ts` rewrites it in dev. |
| `NEXT_PUBLIC_PIVOT_WS_BASE` | Empty. The dataserver has no quote socket at this path. |
| `NEXT_PUBLIC_GOOGLE_CLIENT_ID` | The same Google OAuth client the app uses. |
| `WAITLIST_DATABASE_URL` / `DATABASE_URL` | Postgres for `charto_landing.waitlist_registrations`. The schema name predates the rename and was kept. |
| `RAZORPAY_KEY_ID` / `RAZORPAY_KEY_SECRET` | Checkout (`/api/create-order`, `/api/verify-payment`). |
| `GOOGLE_SITE_VERIFICATION` / `BING_SITE_VERIFICATION` | Search-console tags. |

## Tests

```sh
npx tsc --noEmit && npx vitest run
```
