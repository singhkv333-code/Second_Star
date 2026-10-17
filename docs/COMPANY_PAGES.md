# Pivot company pages

Created 11 October 2026 for Pivot Tech. Contact: info@pivotnow.in, as supplied by the owner.

Public routes: `/about`, `/terms`, `/privacy`. The landing footer, signup form, checkout and app settings link to the relevant pages. These pages render without sign-in and use a scrollable surface, short sections, accessible headings and mobile layouts.

Content and styles live in `components/company/` in site (pivotnow.in website) and pivot-next (app). Matching pages are available in the active local merge checkout. Keep the content aligned until these frontends are consolidated.

## References

- [Monzo: Big news about small print](https://monzo.com/blog/2018/05/22/big-news-about-small-print): short headings and approachable terms.
- [Monzo privacy notice](https://monzo.com/legal/privacy-notice): group information by what users want to know.
- [Linear terms](https://linear.app/terms) and [privacy](https://linear.app/privacy): section navigation and a visible revision date.
- [Ghost About](https://ghost.org/about/) and [Linear About](https://linear.app/about): purpose and product before company history.
- [GOV.UK privacy notice guidance](https://www.gov.uk/service-manual/design/collecting-personal-information-from-users): readable legal content.

Original Pivot wording; no copied clauses, company statistics or team biographies. Following owner feedback: existing Newsreader/Inter typography, black text on white, one reading column and paragraphs only. No contents sidebar, summary panels, decorative labels or grey text.

## Facts used

- Project context: charts, research, strategies, backtests and simulated paper trading; no live broker execution or personalised investment advice.
- `api/backend/posthog_client.py` and `api/backend/auth/router.py`: backend analytics and error capture when configured; user identifiers and email profile properties. The host defaults to the US ingestion endpoint. No promise of active analytics on every deployment or browser session replay.
- Auth/workspace clients: saved prompts, layouts, strategies and paper records, with browser storage for sessions and interface state.
- Razorpay Standard Checkout: third-party payment collection, order/payment verification and a temporary HttpOnly order cookie. Test checkout does not collect real funds or upgrade subscriptions.

## Publication notes

Local implementation only; no push or deployment performed. The website and app need their usual release steps. On the legacy Azure nginx deployment, add these routes to the allowlist before deploying there; otherwise nginx serves the preview homepage.

The copy avoids unsupported claims about certifications, fixed retention periods, AI-provider training exclusions, no-sale guarantees, a registered office or refund deadlines. Before selling a plan, show its actual price, access period and cancellation/refund conditions at purchase. The current checkout demo is not a plan purchase.

The operator name and email were confirmed by the owner. No registered address, designated grievance contact or response timetable was supplied. Confirm any additional required disclosures before publication, and ensure info@pivotnow.in receives support/privacy mail. The pages do not certify compliance or implement a deletion/export backend.
