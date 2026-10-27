# Referral programme launch gate

The programme is **off** unless `PIVOT_REFERRALS_ENABLED=1` on the dataserver and
`NEXT_PUBLIC_PIVOT_REFERRALS_ENABLED=1` in the shell build. The existing
`/affiliate` page remains a pre-launch interest page. Do not set either flag
until the commercial terms below are approved and public copy is updated.

## Decisions required before launch

| Decision | Required configuration / publication |
|---|---|
| Commission rate and revenue basis (gross, tax and fees) | `PIVOT_REFERRAL_RATE_BPS`, `PIVOT_REFERRAL_REVENUE_BASIS` |
| Attribution | This implementation supports **last touch** only; choose `PIVOT_REFERRAL_TOUCH=last` and set `PIVOT_REFERRAL_ATTRIBUTION_DAYS` |
| Commission duration | `PIVOT_REFERRAL_COMMISSION_DAYS`, counted from the referred signup |
| Eligible accounts and duplicate account review | `PIVOT_REFERRAL_ELIGIBILITY`; define the review procedure. the email constraint blocks the same email and a reused Razorpay customer ID is flagged, but separate identities cannot be identified automatically |
| Refunds and disputes | `PIVOT_REFERRAL_REFUND_POLICY`; decide post-payout recovery and dispute evidence. Code reverses partial/full refunds proportionally and holds open disputes |
| Payout cadence and minimum | `PIVOT_REFERRAL_PAYOUT_MIN_PAISE`, `PIVOT_REFERRAL_PAYOUT_SCHEDULE`, `PIVOT_REFERRAL_PAYOUT_METHOD`; publish tax documentation and transfer owner |
| Terms changes | `PIVOT_REFERRAL_TERMS_VERSION`, `PIVOT_REFERRAL_EFFECTIVE_DATE`, `PIVOT_REFERRAL_TERMS_CHANGE_POLICY`; include notice and earlier-cohort treatment |

No rate, payout promise or effective date in the current landing illustration is
final. The existing 25% calculator is explicitly labelled proposed. When the
shell flag is enabled, the page reads server-published terms and uses those
values in its copy; it does not show the proposal. The authenticated panel is
compiled only when the shell flag is enabled.

## Data and flow

`users.db` remains the authority for accounts, sessions and Razorpay
subscription ownership. `pivot_referrals.db` is a separate SQLite file beside
it (override with `PIVOT_REFERRALS_DB`). Its schema is created only when the
server flag and required settings are present. Back it up with the user-state
plane. Reversal is disabling both flags; no table in the shared user database
is changed. Archive/export the separate file before deleting it. The terms
version, rate and commission duration are frozen on each attributed signup;
future changes to settings do not rewrite that cohort's economics.

`/r/{code}` in pivot-next asks the dataserver to record a visit, sets a same-site,
HttpOnly `pivot_referral` cookie, then sends the visitor to `/signup`. A new
email or Google the dataserver account consumes that token once. Existing accounts do
not gain attribution. The referrer cannot refer the same account ID. The
link is a stable random code, and the account view exposes counts and money,
not the referred person's name or email.

Razorpay remains the payment source. The signed `/billing/webhook` sends
`payment.captured`, `refund.processed` and dispute created/won/lost events to
the referral ledger. A captured INR payment must match a paid provider invoice
whose subscription belongs to a dataserver account. The provider payment ID and
webhook event ID are unique keys. The provider amount in paise is used; the
client cannot submit an earning. A refund reverses commission proportionally.
An invoice customer ID is HMAC-hashed with `PIVOT_REFERRAL_IDENTITY_KEY`; the
same provider customer paying under two dataserver accounts becomes an exception,
which an admin must clear or reject with an audited reason. Other kinds of
duplicate person remain a manual review risk.
An open dispute holds approval; a lost dispute reverses commission, while a
won dispute restores its earlier status. The admin view lists review rows and
unmatched verified billing events, replays those events on request, approves a
commission, compares up to the latest 100 ledger payments with fresh Razorpay
records, and records an external transfer reference. It does **not** transfer
money.

## Remaining integration gates

1. Confirm the Razorpay live webhook is subscribed to capture, processed
   refund and dispute created/won/lost events. Inspect real signed subscription
   invoice/payment payloads: the existing integration previously listened only
   to subscription state events. A missing invoice link leaves a payment
   unmatched for review.
2. Verify the provider reversal order and dispute recovery on test payments.
   Out-of-order refunds/lost disputes replay when the capture arrives; failed
   provider lookups can be replayed by an admin from the billing event audit.
3. Run reconciliation against the full provider history, not only the latest
   100 ledger entries, and review test/live provider keys separately.
4. Approve and publish the complete terms above, then align the public
   calculator and FAQ with them before building the shell with its flag on.

Until these gates are closed, the referral programme must remain disabled.

## Local visual preview

For UI testing without a payment provider, start pivot-next in development
with `NEXT_PUBLIC_PIVOT_REFERRALS_DEMO=1` and open `/affiliate/referrals` for the account view or `/affiliate/admin` for the admin preview. These routes use sample data only; they do not activate payments or payouts.
The account and admin panels show labelled sample data; approval, exception
review and payout controls change browser state only. Demo links cannot
attribute signups and no Razorpay or referral database action occurs. This
switch is ignored outside development and is separate from the launch flags.
