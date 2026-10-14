/**
 * Plan logic for DISPLAY. The server decides every gated write and answers a
 * refusal as HTTP 402 (charto/data/entitlements.py); nothing here is a
 * security boundary. It exists so the UI can say the right thing at the
 * moment of the click, and draw the right state on the billing page.
 *
 * Functions that mirror Python name their twin, and keep its semantics, so a
 * rule changed there has an obvious place to change here.
 */

import { planById, rankOf } from "./catalog";
import { isUnbuilt } from "./presentation";
import type {
  BillingMe,
  Cycle,
  FeatureValue,
  PaidPlanId,
  PlanId,
  PublicCatalog,
  Refusal,
  RefusalCode,
  Subscription,
} from "./types";

export const nowSec = (): number => Math.floor(Date.now() / 1000);

// ── which state is this subscription in ─────────────────────────────────

/**
 * Every state the billing page and the banners draw.
 *
 *   anonymous   signed out
 *   free        no subscription row, or one that ended long ago
 *   incomplete  checkout opened but never paid (status `created`)
 *   trialing    paid plan on a trial
 *   active      paid and renewing
 *   canceling   paid through period_end, will not renew
 *   past_due    a renewal failed; the plan holds until grace_until
 *   expired     the paid plan has lapsed (halted, completed, or grace ran out)
 *   comp        a paid plan granted without a payment (admin / comp)
 */
export type SubState =
  | "anonymous"
  | "free"
  | "incomplete"
  | "trialing"
  | "active"
  | "canceling"
  | "past_due"
  | "expired"
  | "comp";

export type SubView = {
  state: SubState;
  plan: PlanId;
  /** The plan the subscription row names (may differ from `plan` once lapsed). */
  subPlan: PlanId | null;
  cycle: Cycle | null;
  renewsAt: number | null;
  endsAt: number | null;
  graceUntil: number | null;
  pending: Subscription["pending_change"];
};

/** entitlements._effective(), without grants (the server folds those in). */
export function effectivePlan(sub: Subscription | null, now: number): PlanId {
  if (!sub) return "free";
  const { plan, status, period_end: pe, grace_until: grace } = sub;
  const paid = status === "active" || status === "trialing";
  if (paid && (!pe || pe > now)) return plan;
  if (paid && pe && pe <= now && grace && grace > now) return plan;
  if (status === "past_due" && grace && grace > now) return plan;
  if (status === "cancelled" && pe && pe > now) return plan;
  return "free";
}

export function subscriptionView(me: BillingMe | null, now: number = nowSec()): SubView {
  const base: SubView = {
    state: "free",
    plan: me?.plan ?? "free",
    subPlan: null,
    cycle: null,
    renewsAt: null,
    endsAt: null,
    graceUntil: null,
    pending: null,
  };
  if (!me) return base;
  if (me.plan === "anonymous") return { ...base, state: "anonymous" };
  const sub = me.subscription;
  if (!sub || sub.plan === "free") {
    return { ...base, state: me.plan !== "free" ? "comp" : "free" };
  }
  const v: SubView = {
    ...base,
    subPlan: sub.plan,
    cycle: sub.cycle,
    pending: sub.pending_change ?? null,
  };
  const live = effectivePlan(sub, now) !== "free";
  if (sub.status === "created") {
    // A checkout that never completed. If they are on a paid plan anyway it
    // came from somewhere else (a comp), which outranks a dangling checkout.
    return { ...v, state: me.plan !== "free" ? "comp" : "incomplete" };
  }
  if (!live) {
    return { ...v, state: me.plan !== "free" ? "comp" : "expired", endsAt: sub.period_end };
  }
  if (sub.status === "past_due") return { ...v, state: "past_due", graceUntil: sub.grace_until };
  if (sub.cancel_at_period_end || sub.status === "cancelled") {
    return { ...v, state: "canceling", endsAt: sub.period_end };
  }
  if (sub.provider !== "razorpay" && sub.provider !== "") {
    return { ...v, state: "comp", endsAt: sub.period_end };
  }
  if (sub.status === "trialing") return { ...v, state: "trialing", endsAt: sub.period_end };
  return { ...v, state: "active", renewsAt: sub.period_end };
}

// ── what a feature is worth ─────────────────────────────────────────────

export function catalogValue(cat: PublicCatalog, key: string, plan: PlanId): FeatureValue | undefined {
  if (plan === "anonymous") return undefined;
  return planById(cat, plan)?.features[key];
}

/** A feature's worth for this user from /billing/me. A quota answers its limit. */
export function meValue(me: BillingMe | null, key: string): FeatureValue | undefined {
  const f = me?.features[key];
  if (!f) return undefined;
  return f.kind === "quota" ? (f.limit ?? null) : (f.value ?? null);
}

/**
 * Would the server allow `n` of `key`? Mirrors plan.js `allows()`: when the
 * plan has not loaded, or the paywall is switched off, the answer is yes and
 * the server refuses if it must.
 */
export function allows(me: BillingMe | null, key: string, n = 1): boolean {
  if (!me || me.paywall_enabled === false) return true;
  const v = meValue(me, key);
  if (v === undefined || v === null) return true;
  if (typeof v === "boolean") return v;
  return n <= v;
}

/** entitlements.upgrade_for(): the cheapest public plan above `plan` that
 *  would have allowed `needed` of `key`. */
export function upgradeFor(
  cat: PublicCatalog,
  key: string,
  plan: PlanId,
  needed: number | null = null,
): PlanId | null {
  const f = cat.features[key];
  if (!f) return null;
  const current = catalogValue(cat, key, plan);
  for (const p of cat.plans) {
    if (rankOf(cat, p.id) <= rankOf(cat, plan)) continue;
    const v = p.features[key];
    if (f.kind === "flag" && v) return p.id;
    if (f.kind === "limit" || f.kind === "quota") {
      if (v === null || needed === null || (typeof v === "number" && v > needed)) return p.id;
    }
    if (f.kind === "value" && v !== current) return p.id;
  }
  return null;
}

/** Is `key` at `n` above what `plan` allows? Used for lock markers. */
export function overPlan(cat: PublicCatalog, key: string, plan: PlanId, n: number): boolean {
  const v = catalogValue(cat, key, plan === "anonymous" ? "free" : plan);
  if (v === undefined || v === null) return false;
  if (typeof v === "boolean") return !v;
  return n > v;
}

// ── quotas ──────────────────────────────────────────────────────────────

export type QuotaTone = "ok" | "low" | "out";
export type QuotaView = {
  used: number;
  limit: number | null;
  left: number | null;
  pct: number;
  tone: QuotaTone;
  resetsAt: number | null;
};

export function quotaView(me: BillingMe | null, key: string): QuotaView | null {
  const f = me?.features[key];
  if (!f || f.kind !== "quota") return null;
  return meter(f.used ?? 0, f.limit ?? null, f.resets_at ?? null);
}

/** A meter for anything with a used/limit pair. 80% used reads as low. */
export function meter(used: number, limit: number | null, resetsAt: number | null = null): QuotaView {
  if (limit === null) return { used, limit, left: null, pct: 0, tone: "ok", resetsAt };
  const left = Math.max(0, limit - used);
  const pct = limit === 0 ? 100 : Math.min(100, Math.round((used / limit) * 100));
  const tone: QuotaTone = left === 0 ? "out" : pct >= 80 ? "low" : "ok";
  return { used, limit, left, pct, tone, resetsAt };
}

// ── the paywall trigger ─────────────────────────────────────────────────

export type PaywallKind =
  | RefusalCode // plan_limit · feature_locked · quota_exhausted · evicted
  | "signin" // signed out, the feature needs an account
  | "upgrade" // opened by the user, not by a refusal
  | "trial_ended";

export type PaywallTrigger = {
  kind: PaywallKind;
  feature?: string;
  plan: PlanId;
  limit?: number | null;
  used?: number | null;
  resetsAt?: number | null;
  upgradeTo?: PlanId | null;
  /** The server's own sentence, shown verbatim when there is one. */
  message?: string;
  /** Where the user was, so checkout can bring them back. */
  returnTo?: string;
  /** Which surface opened it, for analytics. */
  surface?: string;
};

export function isRefusal(body: unknown): body is Refusal {
  if (!body || typeof body !== "object") return false;
  const b = body as Record<string, unknown>;
  return (
    typeof b.code === "string" &&
    ["plan_limit", "feature_locked", "quota_exhausted", "evicted"].includes(b.code) &&
    typeof b.feature === "string"
  );
}

/** A 402 body → what the paywall should say. Signed-out refusals become a
 *  sign-in prompt, because paying is not the next step for them. */
export function triggerFromRefusal(r: Refusal, extra: Partial<PaywallTrigger> = {}): PaywallTrigger {
  return {
    kind: r.plan === "anonymous" && r.code !== "evicted" ? "signin" : r.code,
    feature: r.feature,
    plan: r.plan,
    limit: r.limit,
    used: r.used,
    resetsAt: r.resets_at ?? null,
    upgradeTo: r.upgrade_to,
    message: r.error,
    ...extra,
  };
}

// ── what a plan change costs the user ───────────────────────────────────

export type Loss = { key: string; from: FeatureValue; to: FeatureValue };

/** Every visible feature that gets WORSE moving from `from` to `to`, for the
 *  downgrade and cancellation review. Pending features are left out: losing
 *  something that does not exist yet is not a loss. */
export function losses(cat: PublicCatalog, from: PlanId, to: PlanId): Loss[] {
  const a = planById(cat, from);
  const b = planById(cat, to);
  if (!a || !b) return [];
  const out: Loss[] = [];
  for (const [key, f] of Object.entries(cat.features)) {
    if (isUnbuilt(key, f)) continue;
    const x = a.features[key];
    const y = b.features[key];
    if (x === undefined || y === undefined || x === y) continue;
    const worse =
      f.kind === "flag"
        ? x === true && y !== true
        : (x === null && y !== null) || (typeof x === "number" && typeof y === "number" && y < x);
    if (worse) out.push({ key, from: x, to: y });
  }
  return out;
}

/** Usage that would be over the new plan's caps: these pause, never delete. */
export function overAfterDowngrade(
  cat: PublicCatalog,
  me: BillingMe | null,
  to: PlanId,
): { key: string; used: number; limit: number }[] {
  if (!me) return [];
  const out: { key: string; used: number; limit: number }[] = [];
  for (const [key, f] of Object.entries(me.features)) {
    if (f.kind !== "limit" || f.used == null) continue;
    const lim = catalogValue(cat, key, to);
    if (typeof lim === "number" && f.used > lim) out.push({ key, used: f.used, limit: lim });
  }
  return out;
}

// ── checkout guards ─────────────────────────────────────────────────────

export type CheckoutBlock =
  | { reason: "signin" }
  | { reason: "same_plan" }
  | { reason: "use_change"; current: PlanId } // already paying: change, don't buy twice
  | { reason: "unavailable" } // Razorpay not configured
  | null;

/** Why a checkout for (plan, cycle) must not start, mirroring the 401 / 409 /
 *  503 billing.api_checkout would answer — so the page explains before it
 *  asks the provider. */
export function checkoutBlock(
  cat: PublicCatalog,
  me: BillingMe | null,
  plan: PaidPlanId,
  cycle: Cycle,
  now: number = nowSec(),
): CheckoutBlock {
  if (!me || me.plan === "anonymous") return { reason: "signin" };
  const v = subscriptionView(me, now);
  if (["active", "trialing", "past_due", "canceling"].includes(v.state)) {
    if (v.subPlan === plan && v.cycle === cycle) return { reason: "same_plan" };
    return { reason: "use_change", current: v.subPlan ?? me.plan };
  }
  if (cat.checkout === false || me.checkout === false) return { reason: "unavailable" };
  return null;
}

export type ChangeKind = "upgrade" | "downgrade" | "cycle";

export function changeKind(cat: PublicCatalog, from: PlanId, to: PlanId): ChangeKind {
  const a = rankOf(cat, from);
  const b = rankOf(cat, to);
  return b > a ? "upgrade" : b < a ? "downgrade" : "cycle";
}

// ── prices ──────────────────────────────────────────────────────────────

/** Paise saved a year by paying annually rather than monthly. */
export function annualSaving(cat: PublicCatalog, plan: PlanId): number {
  const p = planById(cat, plan)?.prices;
  if (!p?.monthly || !p.annual) return 0;
  return Math.max(0, p.monthly.amount * 12 - p.annual.amount);
}

/** The best "save N%" any plan offers, for the billing toggle label. */
export function bestSavingPct(cat: PublicCatalog): number {
  return Math.max(0, ...cat.plans.map((p) => p.prices.annual?.saving_pct ?? 0));
}

// ── navigation ──────────────────────────────────────────────────────────

/** Only same-origin app paths survive as a return target. */
export function safeReturnPath(p: string | null | undefined, fallback = "/"): string {
  if (!p || typeof p !== "string") return fallback;
  if (!p.startsWith("/") || p.startsWith("//") || p.startsWith("/\\")) return fallback;
  if (/^\/(checkout|pricing)(\/|\?|$)/.test(p)) return fallback; // never loop back into billing
  return p;
}

export function checkoutHref(
  plan: PaidPlanId,
  cycle: Cycle,
  opts: { returnTo?: string; feature?: string; surface?: string } = {},
): string {
  const q = new URLSearchParams({ plan, cycle });
  if (opts.returnTo) q.set("return", safeReturnPath(opts.returnTo));
  if (opts.feature) q.set("feature", opts.feature);
  if (opts.surface) q.set("from", opts.surface);
  return `/checkout?${q.toString()}`;
}
