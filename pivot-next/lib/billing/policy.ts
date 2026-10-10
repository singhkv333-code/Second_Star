/**
 * Billing terms, as the product states them.
 *
 * Settled facts come from code (billing.py, entitlements.py) and are stated
 * plainly. Anything NOT settled is a `TBD` — rendered with a visible "To be
 * confirmed" tag (`.bl-tbd`) so a placeholder can never ship looking like a
 * decision. Search for `tbd(` before launch.
 */

export type Part = string | { tbd: string };
export const tbd = (text: string): Part => ({ tbd: text });

/** Facts the code enforces today. */
export const FACTS = {
  graceDays: 3, // entitlements.GRACE_DAYS
  provider: "Razorpay",
};

export type Faq = { q: string; a: Part[] };

export const FAQS: Faq[] = [
  {
    q: "What counts as an AI credit?",
    a: [
      "One credit is one question you send to Pivot. Follow-up suggestions, conversation titles and answers that fail are not charged. Credits refresh every month: on your billing date on a paid plan, and on the 1st (IST) on Free.",
    ],
  },
  {
    q: "Can I change or cancel my plan later?",
    a: [
      "Yes, from Plan & billing at any time. An upgrade applies immediately. A downgrade or a switch between monthly and yearly takes effect at your next renewal, so you keep what you paid for until then. Cancelling stops the next renewal; your plan stays active until the end of the period you have paid for.",
    ],
  },
  {
    q: "What happens to my alerts and layouts if I downgrade?",
    a: [
      "Nothing is deleted. If you have more armed alerts than the new plan allows, the newest ones are paused, marked as over the plan limit, and can be re-armed when you upgrade or free a slot. Saved layouts with more charts or indicators than the new plan allows still open; only new saves above the limit are refused.",
    ],
  },
  {
    q: "Do you offer refunds?",
    a: [tbd("Refund policy for monthly and yearly plans, including any window after purchase.")],
  },
  {
    q: "Is there a free trial?",
    a: [
      "The Free plan is free for as long as you use it, with no card required, so you can try charting, alerts and the AI analyst before paying.",
    ],
  },
  {
    q: "How am I billed, and is GST included?",
    a: [
      "Prices are in Indian rupees and include GST. Payments are processed by Razorpay; Pivot never sees or stores your card or UPI details. Paid plans renew automatically until you cancel. Your bank or UPI app notifies you before each automatic debit, as RBI rules require.",
    ],
  },
  {
    q: "What if a renewal payment fails?",
    a: [
      `Your plan stays active for ${FACTS.graceDays} days while the payment is retried. Update your payment method from Plan & billing during that time to avoid any interruption.`,
    ],
  },
  {
    q: "Is Pivot financial advice, or a broker?",
    a: [
      "Neither. Pivot gives you data, analysis and simulated strategy testing. Strategies run in a paper book; Pivot does not place live orders with a broker.",
    ],
  },
];

/** The renewal sentence under every Pay button. */
export function renewalTerms(price: string, cycleWord: "month" | "year"): string {
  return `Renews automatically at ${price} a ${cycleWord} until you cancel. Cancel any time from Plan & billing; you keep access until the end of the paid period.`;
}

export const INVOICE_TBD = tbd("GST invoice details (GSTIN, legal entity name and address) on downloadable invoices.");

/** billing.api_change PATCHes with schedule_change_at=now; how Razorpay
 *  charges for the remainder of the current cycle must be read off a test-mode
 *  upgrade before this sentence is written as fact. */
export const UPGRADE_CHARGE_TBD = tbd("How the amount for the rest of the current period is charged on an immediate upgrade.");
