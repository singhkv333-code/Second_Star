# Pivot welcome emails

Three saved drafts, each with an HTML email and a plain-text alternative:

| Email | HTML | Plain text |
| --- | --- | --- |
| Signup | `signup.html` | `signup.txt` |
| Pro payment confirmation | `pro.html` | `pro.txt` |
| Pro+ payment confirmation | `pro-plus.html` | `pro-plus.txt` |

Subjects and inbox preview text are in `metadata.json`. Open `preview.html` to review all three with example values, or open an individual file in `previews/`. The previews are examples, not records of a payment or a sent email.

## Copy and design

White background, black text, a Georgia fallback for Pivot's serif wordmark and headings, and one black action button. No badges, gradients, shadows, decorative icons, or tracking pixels. Layout tables and inline styles keep the emails usable in mail clients, with a responsive inset for phones and an Outlook button fallback.

The signup note is short and informal. The paid notes welcome the subscriber, confirm the captured payment and active plan, and include a plain payment-details table. Benefits follow `dataserver/plans_catalog.json`: Pro has 200 AI credits per month and 10 indicators per chart; Pro+ has 500 credits per month and unlimited indicators per chart. Pending features are omitted.

## Values to fill before sending

| Placeholder | Value |
| --- | --- |
| `first_name` | Recipient's first name, or `there` when unavailable. |
| `app_url` | Verified, absolute HTTPS URL for the user's Pivot app. |
| `amount_paid` | Formatted amount actually captured, including the currency. Use the total charged for annual plans, not the monthly equivalent. |
| `billing_cycle` | Actual billing cycle, such as `Monthly` or `Annual`. |
| `paid_on` | Actual payment date, formatted for the customer. |
| `payment_reference` | Razorpay payment ID associated with the captured payment. |
| `access_until` | Actual end of the activated subscription's current period. |

Escape all inserted HTML values, including URL attributes; validate `app_url` as an HTTPS URL. Plain text uses unescaped values. Match the HTML and plain-text copies when changing wording. Do not send unresolved placeholders.

## Delivery intent

Signup: after successful account creation. Paid: only after the backend confirms a captured payment **and** activates the matching Pro or Pro+ subscription. Signature verification alone does not establish that a payment was captured. Deduplicate delivery by the relevant account-creation event or Razorpay payment ID when integrating.

These are drafting artifacts only; no signup, billing, database, or delivery code was changed. No emails were sent. The confirmation is not a tax invoice, and the drafts make no automatic-renewal claim. Add any invoice link or renewal notice only when the corresponding billing data and document exist.

Use `Pivot` as the sender display name and `info@pivotnow.in` as the reply-to address. Configure the sending service and authenticate its domain before wiring delivery. The contact address and company name are the user's supplied details: Pivot Tech, info@pivotnow.in.
