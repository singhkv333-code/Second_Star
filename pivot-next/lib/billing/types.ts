/**
 * The billing wire, as charto's dataserver speaks it.
 *
 * Every shape here mirrors a Python function in charto/data — the comment on
 * each names it — because the server decides and this side only displays.
 * Money is in PAISE everywhere on the wire (GST-inclusive); format at the edge.
 */

export type PlanId = "anonymous" | "free" | "pro" | "pro_plus";
export type PaidPlanId = "pro" | "pro_plus";
export type Cycle = "monthly" | "annual";
export type FeatureKind = "flag" | "limit" | "quota" | "value";

/** true/false for a flag, a number for a limit or value, null = unlimited. */
export type FeatureValue = boolean | number | null;

export type Price = {
  /** What one charge costs, in paise. */
  amount: number;
  /** The same charge spread per month, in paise. */
  per_month: number;
  saving_pct?: number;
};

export type CatalogFeature = {
  kind: FeatureKind;
  label: string;
  unit?: string;
  window?: "day" | "month";
  /** Agreed key, but no product surface yet. Never sold as live. */
  pending?: boolean;
};

export type CatalogPlan = {
  id: PlanId;
  name: string;
  rank: number;
  prices: Partial<Record<Cycle, Price>>;
  features: Record<string, FeatureValue>;
};

/** entitlements.public_catalog() + `checkout` (GET /billing/plans). */
export type PublicCatalog = {
  currency: "INR";
  version: number;
  gst_inclusive: boolean;
  trial_days: number | null;
  plans: CatalogPlan[];
  features: Record<string, CatalogFeature>;
  /** Razorpay keys are configured; false = checkout answers 503. */
  checkout?: boolean;
};

export type SubStatus =
  | "created"
  | "active"
  | "trialing"
  | "past_due"
  | "cancelled"
  | "expired";

export type PendingChange = { plan: PlanId; cycle: Cycle; at: number | null };

/** The `subscription` block of entitlements.summary(). Unix seconds. */
export type Subscription = {
  plan: PlanId;
  cycle: Cycle;
  status: SubStatus;
  period_start: number | null;
  period_end: number | null;
  cancel_at_period_end: boolean;
  grace_until: number | null;
  provider: string;
  pending_change?: PendingChange | null;
};

export type MeFeature = {
  kind: FeatureKind;
  label: string;
  /** flags, limits and values */
  value?: FeatureValue;
  /** live objects (limits) or metered use (quotas) */
  used?: number;
  /** quotas */
  limit?: number | null;
  window?: "day" | "month";
  resets_at?: number;
  pending?: boolean;
};

/** entitlements.summary() + `checkout` (GET /billing/me). */
export type BillingMe = {
  plan: PlanId;
  plan_name: string;
  subscription: Subscription | null;
  features: Record<string, MeFeature>;
  trial_days: number | null;
  paywall_enabled: boolean;
  checkout?: boolean;
};

export type RefusalCode = "plan_limit" | "feature_locked" | "quota_exhausted" | "evicted";

/** PlanLimit.body() — every HTTP 402 from every service. */
export type Refusal = {
  error: string;
  code: RefusalCode;
  feature: string;
  plan: PlanId;
  limit: number | null;
  used: number | null;
  upgrade_to: PlanId | null;
  resets_at?: number;
};

export type Invoice = {
  id: string;
  date: number | null;
  /** paise */
  amount: number | null;
  currency: string;
  status: string;
  period_start: number | null;
  period_end: number | null;
  url: string | null;
};

export type InvoiceList = {
  invoices: Invoice[];
  payment_method: string | null;
  manage_url: string | null;
};
