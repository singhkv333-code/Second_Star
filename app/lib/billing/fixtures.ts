/**
 * One realistic /billing/me per subscription state, built from the catalog
 * so a limit changed there moves here too. Used by the design gallery
 * (/paywall-gallery) and the tests — never by a live page.
 */

import { SNAPSHOT, planById } from "./catalog";
import type { BillingMe, Cycle, Invoice, InvoiceList, MeFeature, PlanId, PublicCatalog, Subscription } from "./types";

const DAY = 86400;

export type Fixture =
  | "anonymous"
  | "free"
  | "free_low"
  | "free_out"
  | "incomplete"
  | "trialing"
  | "active"
  | "active_annual"
  | "pro_plus"
  | "scheduled"
  | "canceling"
  | "past_due"
  | "expired"
  | "comp";

export const FIXTURE_LABELS: Record<Fixture, string> = {
  anonymous: "Signed out",
  free: "Free",
  free_low: "Free, credits running low",
  free_out: "Free, out of credits",
  incomplete: "Checkout not completed",
  trialing: "Trialing",
  active: "Pro, monthly",
  active_annual: "Pro, yearly",
  pro_plus: "Pro+",
  scheduled: "Downgrade scheduled",
  canceling: "Cancelled, active until period end",
  past_due: "Payment failed (grace)",
  expired: "Expired",
  comp: "Complimentary",
};

function features(cat: PublicCatalog, plan: PlanId, credits: number, alerts: { price: number; technical: number }, now: number): Record<string, MeFeature> {
  const p = planById(cat, plan === "anonymous" ? "free" : plan);
  const out: Record<string, MeFeature> = {};
  for (const [key, f] of Object.entries(cat.features)) {
    const v = plan === "anonymous" && key === "ai.credits" ? 3 : (p?.features[key] ?? null);
    const item: MeFeature = { kind: f.kind, label: f.label, ...(f.pending ? { pending: true } : {}) };
    if (f.kind === "quota") {
      item.limit = typeof v === "number" ? v : null;
      item.used = key === "ai.credits" ? credits : 0;
      item.window = f.window ?? "month";
      item.resets_at = now + 12 * DAY;
    } else {
      item.value = v;
      if (key === "alerts.price") item.used = alerts.price;
      if (key === "alerts.technical") item.used = alerts.technical;
    }
    out[key] = item;
  }
  return out;
}

function sub(plan: PlanId, cycle: Cycle, now: number, s: Partial<Subscription> = {}): Subscription {
  const len = cycle === "annual" ? 365 * DAY : 30 * DAY;
  return {
    plan,
    cycle,
    status: "active",
    period_start: now - 18 * DAY,
    period_end: now - 18 * DAY + len,
    cancel_at_period_end: false,
    grace_until: null,
    provider: "razorpay",
    pending_change: null,
    ...s,
  };
}

export function fixtureMe(f: Fixture, cat: PublicCatalog = SNAPSHOT, now = Math.floor(Date.now() / 1000)): BillingMe {
  const me = (plan: PlanId, credits: number, s: Subscription | null, alerts = { price: 6, technical: 3 }): BillingMe => ({
    plan,
    plan_name: plan === "anonymous" ? "Signed out" : (planById(cat, plan)?.name ?? plan),
    subscription: s,
    features: features(cat, plan, credits, alerts, now),
    trial_days: cat.trial_days,
    paywall_enabled: true,
    checkout: true,
  });
  switch (f) {
    case "anonymous":
      return me("anonymous", 3, null);
    case "free":
      return me("free", 4, null);
    case "free_low":
      return me("free", 13, null, { price: 18, technical: 20 });
    case "free_out":
      return me("free", 15, null, { price: 20, technical: 20 });
    case "incomplete":
      return me("free", 9, sub("pro", "annual", now, { status: "created", period_start: null, period_end: null }));
    case "trialing":
      return me("pro", 22, sub("pro", "monthly", now, { status: "trialing", period_start: now - 4 * DAY, period_end: now + 3 * DAY }));
    case "active":
      return me("pro", 61, sub("pro", "monthly", now), { price: 48, technical: 31 });
    case "active_annual":
      return me("pro", 140, sub("pro", "annual", now), { price: 210, technical: 64 });
    case "pro_plus":
      return me("pro_plus", 212, sub("pro_plus", "monthly", now), { price: 380, technical: 140 });
    case "scheduled":
      return me("pro_plus", 120, sub("pro_plus", "monthly", now, {
        pending_change: { plan: "pro", cycle: "monthly", at: now + 12 * DAY },
      }), { price: 520, technical: 160 });
    case "canceling":
      return me("pro", 88, sub("pro", "monthly", now, { cancel_at_period_end: true }), { price: 64, technical: 40 });
    case "past_due":
      return me("pro", 74, sub("pro", "monthly", now, {
        status: "past_due",
        period_start: now - 31 * DAY,
        period_end: now - DAY,
        grace_until: now + 2 * DAY,
      }));
    case "expired":
      return me("free", 2, sub("pro", "monthly", now, {
        status: "expired",
        period_start: now - 50 * DAY,
        period_end: now - 20 * DAY,
      }), { price: 20, technical: 20 });
    case "comp":
      return me("pro_plus", 30, sub("pro_plus", "monthly", now, { provider: "admin", period_end: now + 60 * DAY }));
  }
}

export function fixtureInvoices(f: Fixture, cat: PublicCatalog = SNAPSHOT, now = Math.floor(Date.now() / 1000)): InvoiceList {
  if (["anonymous", "free", "free_low", "free_out", "incomplete", "comp"].includes(f)) {
    return { invoices: [], payment_method: null, manage_url: null };
  }
  const m = fixtureMe(f, cat, now);
  const s = m.subscription;
  const price = s ? planById(cat, s.plan)?.prices[s.cycle]?.amount ?? null : null;
  const len = s?.cycle === "annual" ? 365 * DAY : 30 * DAY;
  const start = s?.period_start ?? now;
  const invoices: Invoice[] = [0, 1, 2].map((i) => ({
    id: `inv_demo_${i}`,
    date: start - i * len,
    amount: price,
    currency: "INR",
    status: f === "past_due" && i === 0 ? "issued" : "paid",
    period_start: start - i * len,
    period_end: start - (i - 1) * len,
    url: null,
  }));
  return { invoices: s?.cycle === "annual" ? invoices.slice(0, 1) : invoices, payment_method: "card", manage_url: null };
}
