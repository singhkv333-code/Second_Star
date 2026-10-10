"use client";

/* The pricing / upgrade page for pivot-next.
 *
 * A full-screen overlay (/pricing) on its own backdrop, OVER the app, with a
 * close button and Esc to dismiss. Under the hero: the billing toggle, the
 * plan cards, the full comparison, the FAQ and the trust row.
 *
 * Every number comes from the plan catalog (GET /billing/plans, or its
 * snapshot before that answers) — nothing on this page is typed by hand, so
 * it cannot drift from what the server enforces. The buttons know who is
 * looking: a signed-out visitor is asked to sign up, a Free user goes to
 * checkout, a subscriber changes plan instead of buying a second one.
 */

import * as React from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { X } from "lucide-react";
import { bestSavingPct, checkoutHref, safeReturnPath, subscriptionView } from "@/lib/billing/entitlements";
import { track } from "@/lib/billing/analytics";
import type { Cycle, PaidPlanId, PlanId } from "@/lib/billing/types";
import { useBilling } from "@/components/billing/BillingProvider";
import { BillingToggle, TrustRow } from "@/components/billing/primitives";
import { ComparisonTable, PlanCards, PricingFAQ, type CardAction } from "@/components/billing/pricing";
import { BillingModal } from "@/components/billing/modal";
import { PlanChangePanel } from "@/components/billing/PlanChange";

export function PricingPage(): React.ReactElement {
  const router = useRouter();
  const params = useSearchParams();
  const { catalog, me, setMe, demo } = useBilling();
  const view = subscriptionView(me);
  const [cycle, setCycle] = React.useState<Cycle>(view.cycle ?? "annual");
  const [change, setChange] = React.useState<{ plan: PaidPlanId; cycle: Cycle } | null>(null);
  const compareRef = React.useRef<HTMLElement>(null);
  const returnTo = safeReturnPath(params?.get("return"), "/");
  const feature = params?.get("feature") ?? undefined;

  React.useEffect(() => {
    track("pricing_viewed", { feature, surface: params?.get("from") ?? "direct" });
    // eslint-disable-next-line react-hooks/exhaustive-deps -- once per visit
  }, []);

  // Close the overlay → back to wherever the user opened it from.
  const close = React.useCallback((): void => {
    if (window.history.length > 1) router.back();
    else router.push(returnTo);
  }, [router, returnTo]);

  React.useEffect(() => {
    const onKey = (e: KeyboardEvent): void => {
      if (e.key === "Escape" && !change) close();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [close, change]);

  const onAction = (plan: PlanId, a: CardAction): void => {
    track("plan_selected", { plan, cycle, surface: "pricing" });
    if (a.kind === "signup") router.push("/signup");
    else if (a.kind === "checkout") router.push(checkoutHref(plan as PaidPlanId, cycle, { returnTo, feature, surface: "pricing" }));
    else if (a.kind === "change") setChange({ plan: plan as PaidPlanId, cycle });
    else if (a.kind === "cancel") router.push("/settings/billing");
  };

  const seeAll = (): void => compareRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
  const onCycle = (c: Cycle): void => {
    setCycle(c);
    track("billing_cycle_toggled", { cycle: c, surface: "pricing" });
  };

  return (
    <div className="pricing-overlay" role="dialog" aria-modal="true" aria-label="Plans and pricing">
      <button type="button" className="pricing-close" aria-label="Close" onClick={close}>
        <X size={20} strokeWidth={2} />
      </button>
      <div className="pricing-overlay-scroll bl-scope">
        <div className="pricing-page">
          <header className="pricing-hero">
            <span className="pricing-eyebrow">Plans &amp; pricing</span>
            <h1 className="pricing-title">Choose the plan that fits your workflow</h1>
          </header>

          <div className="pricing-bar" style={{ justifyContent: "center" }}>
            <BillingToggle cycle={cycle} onChange={onCycle} savePct={bestSavingPct(catalog)} />
          </div>

          <PlanCards catalog={catalog} me={me} cycle={cycle} onAction={onAction} onSeeAll={seeAll} />

          <p className="pricing-aside">Prices in INR, GST included. Change or cancel any time.</p>
          <div style={{ marginTop: 20 }}>
            <TrustRow />
          </div>

          <section ref={compareRef} className="pricing-compare" aria-labelledby="pricing-compare-title">
            <h2 id="pricing-compare-title" className="pricing-h2">
              Compare all plans
            </h2>
            <div className="pricing-bar" style={{ justifyContent: "center" }}>
              <BillingToggle cycle={cycle} onChange={onCycle} savePct={bestSavingPct(catalog)} label="Billing period for the comparison" />
            </div>
            <ComparisonTable catalog={catalog} cycle={cycle} currentPlan={me && me.plan !== "anonymous" ? me.plan : null} />
          </section>

          <section className="pricing-compare" aria-labelledby="pricing-faq-title" style={{ maxWidth: 820, marginLeft: "auto", marginRight: "auto" }}>
            <h2 id="pricing-faq-title" className="pricing-h2">
              Questions
            </h2>
            <PricingFAQ />
          </section>

          <p className="pricing-foot">
            This is plan information, not financial advice. Pivot builds and simulates strategies; it does not place live broker orders.
          </p>
        </div>
      </div>

      {change && me ? (
        <BillingModal open onOpenChange={(o) => !o && setChange(null)} label="Change plan">
          <PlanChangePanel
            catalog={catalog}
            me={me}
            to={change.plan}
            cycle={change.cycle}
            demo={demo}
            onBack={() => setChange(null)}
            onDone={(m) => {
              setMe(m);
              setChange(null);
            }}
          />
        </BillingModal>
      ) : null}
    </div>
  );
}
