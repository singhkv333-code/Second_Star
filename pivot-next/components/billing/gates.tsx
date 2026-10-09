"use client";

/**
 * Gating helpers for feature screens.
 *
 *   useGate(key)     ask before acting; opens the right paywall when the plan
 *                    would refuse, so the click explains itself instead of
 *                    failing on the server. The server still decides.
 *   PremiumPreview   show a premium feature working — clearly labelled as a
 *                    preview — with the way to unlock it underneath.
 *   FullPagePaywall  a dedicated page for a feature worth one.
 *   LockedItem       a menu row with a lock and the plan that unlocks it.
 */

import * as React from "react";
import { useRouter } from "next/navigation";
import { ArrowRight, Check, Eye } from "lucide-react";
import { cn } from "@/lib/utils";
import { inr } from "@/lib/billing/format";
import { planById, planName } from "@/lib/billing/catalog";
import { allows, catalogValue, checkoutHref, overPlan, upgradeFor } from "@/lib/billing/entitlements";
import { featureCopy, featureLine } from "@/lib/billing/presentation";
import { track } from "@/lib/billing/analytics";
import type { PaidPlanId, PlanId } from "@/lib/billing/types";
import { useBilling } from "./BillingProvider";
import { LockBadge } from "./primitives";
import { HeroArt } from "./art";

export function useGate(key: string, surface?: string) {
  const { me, catalog, openPaywall } = useBilling();
  const plan: PlanId = me?.plan ?? "free";
  return {
    /** Mirrors the server: true when not loaded or the paywall is off. */
    allowed: (n = 1) => allows(me, key, n),
    /** Above the plan by the catalog, regardless of the paywall switch. For labels. */
    locked: (n = 1) => overPlan(catalog, key, plan, n),
    requiredPlan: (n: number | null = null) => upgradeFor(catalog, key, plan === "anonymous" ? "free" : plan, n),
    /** Call before the action. false = the paywall opened; do not proceed. */
    require: (n = 1): boolean => {
      if (allows(me, key, n)) return true;
      const f = catalog.features[key];
      const limit = catalogValue(catalog, key, plan);
      openPaywall({
        kind: plan === "anonymous" ? "signin" : f?.kind === "flag" ? "feature_locked" : "plan_limit",
        feature: key,
        plan,
        limit: typeof limit === "number" ? limit : null,
        used: typeof limit === "number" ? Math.max(0, n - 1) : null,
        upgradeTo: upgradeFor(catalog, key, plan === "anonymous" ? "free" : plan, n - 1),
        surface,
      });
      return false;
    },
  };
}

export function LockedItem({
  label,
  plan,
  onClick,
  icon,
}: {
  label: string;
  plan: string | null;
  onClick: () => void;
  icon?: React.ReactNode;
}): React.ReactElement {
  return (
    <button
      type="button"
      onClick={onClick}
      className="bl-btn bl-btn--ghost"
      style={{ justifyContent: "space-between", width: "100%", color: plan ? "var(--bl-muted)" : undefined }}
      aria-label={plan ? `${label}, requires ${plan}` : label}
    >
      <span className="bl-row-flex">
        {icon}
        {label}
      </span>
      {plan ? <LockBadge plan={plan} /> : null}
    </button>
  );
}

export function PremiumPreview({
  feature,
  plan = "pro",
  children,
  caption,
  locked = true,
  onUnlock,
}: {
  feature: string;
  plan?: PaidPlanId;
  children: React.ReactNode;
  /** What the user is looking at: "Sample data", "Last week's result" ... */
  caption?: string;
  locked?: boolean;
  onUnlock?: () => void;
}): React.ReactElement {
  const { catalog, openPaywall, me } = useBilling();
  const copy = featureCopy(catalog, feature);
  React.useEffect(() => {
    track("preview_opened", { feature });
  }, [feature]);
  const unlock =
    onUnlock ??
    (() =>
      openPaywall({
        kind: "feature_locked",
        feature,
        plan: me?.plan ?? "free",
        upgradeTo: plan,
        surface: "preview",
      }));
  return (
    <figure className={cn("bl-preview", locked && "bl-preview--locked")} style={{ margin: 0 }}>
      <span className="bl-preview-ribbon">
        <Eye size={13} aria-hidden /> Preview
      </span>
      <div className="bl-preview-content" aria-hidden={locked}>
        {children}
      </div>
      <figcaption className="bl-preview-bar">
        <span className="bl-small">
          <strong style={{ color: "var(--bl-ink)" }}>{copy.title}.</strong> {caption ? `${caption}. ` : ""}
          Included in {planName(catalog, plan)}.
        </span>
        <button type="button" className="bl-btn bl-btn--primary bl-btn--sm" onClick={unlock}>
          Unlock with {planName(catalog, plan)}
        </button>
      </figcaption>
    </figure>
  );
}

export function FullPagePaywall({
  feature,
  plan = "pro",
  points,
  returnTo,
  onDismiss,
}: {
  feature: string;
  plan?: PaidPlanId;
  /** Outcome statements. Defaults to the plan's values for the feature. */
  points?: string[];
  returnTo?: string;
  onDismiss?: () => void;
}): React.ReactElement {
  const { catalog } = useBilling();
  const router = useRouter();
  const copy = featureCopy(catalog, feature);
  const p = planById(catalog, plan);
  const f = catalog.features[feature];
  const v = p?.features[feature];
  const lines = points ?? [
    ...(f && v !== undefined ? [featureLine(feature, f, v)] : []),
    ...(p ? [featureLine("ai.credits", catalog.features["ai.credits"]!, p.features["ai.credits"] ?? null)] : []),
    "Change or cancel any time from Plan & billing",
  ];
  const price = p?.prices.annual;
  return (
    <div className="bl-scope">
      <div className="bl-hero">
        <div className="bl-stack bl-stack--lg">
          <span className="bl-eyebrow" style={{ margin: 0 }}>
            {planName(catalog, plan)} feature
          </span>
          <h1 className="bl-h1">{copy.title}</h1>
          <p className="bl-p" style={{ fontSize: 17 }}>
            {copy.benefit}
          </p>
          <ul className="bl-benefits">
            {lines.map((l) => (
              <li key={l}>
                <Check size={17} aria-hidden />
                {l}
              </li>
            ))}
          </ul>
          <div className="bl-row-flex" style={{ gap: 12 }}>
            <button
              type="button"
              className="bl-btn bl-btn--primary bl-btn--lg"
              onClick={() => {
                track("paywall_cta_clicked", { kind: "full_page", feature, plan });
                router.push(checkoutHref(plan, "annual", { returnTo, feature, surface: "full_page" }));
              }}
            >
              Get {planName(catalog, plan)} <ArrowRight size={16} />
            </button>
            <button type="button" className="bl-btn bl-btn--ghost bl-btn--lg" onClick={onDismiss ?? (() => router.push("/pricing"))}>
              {onDismiss ? "Not now" : "Compare plans"}
            </button>
          </div>
          {price ? (
            <p className="bl-fine bl-num">
              From {inr(price.per_month)}/mo billed yearly ({inr(price.amount)}), or {inr(p?.prices.monthly?.amount)} monthly. GST included.
            </p>
          ) : null}
        </div>
        <div className="bl-hero-art" aria-hidden>
          <HeroArt feature={feature} />
        </div>
      </div>
    </div>
  );
}
