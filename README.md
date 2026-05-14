# pivot-waitlist

Standalone deployable Next.js 15 app for the Pivot waitlist landing page.
Extracted from `pivot-next/app/waitlist` so it can ship independently of
the authenticated product.

## Stack

- Next.js 15 (App Router) + React 19
- Tailwind 3.4 + custom keyframe animations in `app/globals.css`
- `lucide-react` for icons
- No backend dependency — the email form is a self-contained
  client-side success state, ready to be wired to an API of your choice.

## Run locally

```bash
pnpm install
pnpm dev
```

Open http://localhost:3000.

## Build

```bash
pnpm install
pnpm build
pnpm start
```

## Deploy

The app is a vanilla Next.js project — drop it on Vercel, Netlify, or
any Node host with no extra configuration. No environment variables are
required.

## Wiring the waitlist form

`components/waitlist/Sections.tsx` → `WaitlistFormBlock` currently flips
to a success state on submit. To persist emails, replace the
`setSubmitted(true)` line with a `fetch()` to your collection endpoint.
