/**
 * The plan catalog on this side of the wire.
 *
 * `catalog.snapshot.json` is a byte-for-byte copy of
 * charto/data/plans_catalog.json (tests/billing/catalog-drift.test.ts fails
 * the moment they differ). The live `/billing/plans` answer always wins; the
 * snapshot only lets the pricing page render before it arrives, or when the
 * dataserver is down, without a single number typed by hand.
 */

import raw from "./catalog.snapshot.json";
import type { CatalogFeature, CatalogPlan, FeatureValue, PlanId, PublicCatalog } from "./types";

type RawFeature = CatalogFeature & {
  values: Record<string, FeatureValue | { limit: number | null }>;
  pending?: string | boolean;
};
type RawCatalog = {
  version: number;
  currency: "INR";
  trial?: { days: number | null };
  plans: Record<string, { name: string; public: boolean; rank: number; prices: CatalogPlan["prices"] }>;
  features: Record<string, RawFeature>;
};

/** entitlements.public_catalog(), in TypeScript. */
export function fromRawCatalog(cat: RawCatalog): PublicCatalog {
  const entries = Object.entries(cat.plans).sort(([, a], [, b]) => a.rank - b.rank);
  const plans: CatalogPlan[] = entries
    .filter(([, meta]) => meta.public)
    .map(([id, meta]) => {
      const features: Record<string, FeatureValue> = {};
      for (const [key, f] of Object.entries(cat.features)) {
        const v = f.values[id];
        features[key] = v !== null && typeof v === "object" ? v.limit : (v as FeatureValue);
      }
      return { id: id as PlanId, name: meta.name, rank: meta.rank, prices: meta.prices, features };
    });
  const features: Record<string, CatalogFeature> = {};
  for (const [key, f] of Object.entries(cat.features)) {
    features[key] = {
      kind: f.kind,
      label: f.label,
      ...(f.unit ? { unit: f.unit } : {}),
      ...(f.window ? { window: f.window } : {}),
      ...(f.pending ? { pending: true } : {}),
    };
  }
  const days = cat.trial?.days;
  return {
    currency: cat.currency,
    version: cat.version,
    gst_inclusive: true,
    trial_days: typeof days === "number" && days > 0 ? days : null,
    plans,
    features,
  };
}

export const SNAPSHOT: PublicCatalog = fromRawCatalog(raw as unknown as RawCatalog);

export function planById(cat: PublicCatalog, id: PlanId): CatalogPlan | undefined {
  return cat.plans.find((p) => p.id === id);
}

/** Display name for any plan id, including the signed-out pseudo-plan. */
export function planName(cat: PublicCatalog, id: PlanId | null | undefined): string {
  if (!id) return "";
  if (id === "anonymous") return "Signed out";
  return planById(cat, id)?.name ?? id;
}

export function rankOf(cat: PublicCatalog, id: PlanId): number {
  if (id === "anonymous") return -1;
  return planById(cat, id)?.rank ?? -1;
}
