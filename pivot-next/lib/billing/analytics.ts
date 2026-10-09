/**
 * Conversion events, with nothing personal in them.
 *
 * Only the props named in ALLOWED survive, and none of them can identify a
 * person: no email, no user id, no free text. That is enforced here rather
 * than trusted at call sites, so a careless `track("x", { email })` drops the
 * field instead of shipping it.
 *
 * Sinks: a DOM event (`pivot:billing-event`) any analytics adapter can listen
 * to, plus an optional beacon to NEXT_PUBLIC_BILLING_EVENTS_URL. Nothing is
 * sent anywhere by default.
 */

export type BillingEvent =
  | "pricing_viewed"
  | "billing_cycle_toggled"
  | "plan_selected"
  | "paywall_viewed"
  | "paywall_cta_clicked"
  | "paywall_dismissed"
  | "preview_opened"
  | "checkout_started"
  | "checkout_dismissed"
  | "checkout_succeeded"
  | "checkout_pending"
  | "checkout_failed"
  | "plan_change_requested"
  | "plan_change_confirmed"
  | "plan_change_undone"
  | "cancel_started"
  | "cancel_step"
  | "cancel_alternative_chosen"
  | "cancel_confirmed"
  | "cancel_abandoned";

const ALLOWED = new Set(["surface", "feature", "plan", "cycle", "from_plan", "to_plan", "code", "step", "kind", "status"]);

export type EventProps = Partial<Record<string, string | number | boolean | null | undefined>>;

export function clean(props: EventProps): Record<string, string | number | boolean> {
  const out: Record<string, string | number | boolean> = {};
  for (const [k, v] of Object.entries(props)) {
    if (!ALLOWED.has(k) || v === undefined || v === null) continue;
    // Short enums only: a long string is free text, and free text can carry anything.
    if (typeof v === "string" && v.length > 48) continue;
    out[k] = v;
  }
  return out;
}

export function track(event: BillingEvent, props: EventProps = {}): void {
  if (typeof window === "undefined") return;
  const detail = { event, props: clean(props), ts: Date.now() };
  try {
    window.dispatchEvent(new CustomEvent("pivot:billing-event", { detail }));
  } catch {
    /* an old browser without CustomEvent: nothing is lost but the event */
  }
  const url = process.env.NEXT_PUBLIC_BILLING_EVENTS_URL;
  if (url && typeof navigator !== "undefined" && navigator.sendBeacon) {
    try {
      navigator.sendBeacon(url, JSON.stringify(detail));
    } catch {
      /* analytics never breaks a purchase */
    }
  }
}
