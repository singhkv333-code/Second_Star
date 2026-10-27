"use client";

/**
 * /pricing — the public plans page: the landing page's topbar over the
 * product's own pricing (plan cards, comparison, FAQ) on a white ground.
 *
 * The blocks are pivot-next's (components/billing/pricing.tsx), read from the
 * same catalog snapshot, so no number here is typed by hand. A visitor on
 * this page is signed out, so every plan button leads to sign-up; checkout
 * and plan changes happen inside the app.
 */

import * as React from "react";
import { useRouter } from "next/navigation";
import { SiteChrome } from "@/components/site/SiteChrome";
import { SNAPSHOT } from "@/lib/billing/catalog";
import { bestSavingPct } from "@/lib/billing/entitlements";
import type { Cycle } from "@/lib/billing/types";
import { BillingToggle, TrustRow } from "@/components/billing/primitives";
import { ComparisonTable, PlanCards, PricingFAQ } from "@/components/billing/pricing";
import "@/components/billing/billing.css";
import "./pricing.css";

export function PublicPricing(): React.ReactElement {
  const router = useRouter();
  const [cycle, setCycle] = React.useState<Cycle>("annual");
  const compareRef = React.useRef<HTMLElement>(null);
  const catalog = SNAPSHOT;
  const save = bestSavingPct(catalog);

  return (
    <SiteChrome current="pricing" className="pp">
      <main className="pp-page bl-scope">
        <header className="pp-hero">
          <span className="pp-eyebrow">Plans &amp; pricing</span>
          <h1 className="pp-title">Choose the plan that fits your workflow</h1>
        </header>

        <div className="pp-bar">
          <BillingToggle cycle={cycle} onChange={setCycle} savePct={save} />
        </div>

        <PlanCards
          catalog={catalog}
          me={null}
          cycle={cycle}
          onAction={(plan) => {
            if (plan === "pro" || plan === "pro_plus") {
              window.location.assign(`https://app.pivotnow.in/checkout?${new URLSearchParams({ plan, cycle })}`);
            } else router.push("/signup");
          }}
          onSeeAll={() => compareRef.current?.scrollIntoView({ behavior: "smooth", block: "start" })}
        />

        <p className="pp-aside">Prices in INR, GST included. Change or cancel any time.</p>
        <div className="pp-trust"><TrustRow /></div>

        <section ref={compareRef} className="pp-section" aria-labelledby="pp-compare-title">
          <h2 id="pp-compare-title" className="pp-h2">Compare all plans</h2>
          <div className="pp-bar">
            <BillingToggle cycle={cycle} onChange={setCycle} savePct={save} label="Billing period for the comparison" />
          </div>
          <ComparisonTable catalog={catalog} cycle={cycle} />
        </section>

        <section className="pp-section pp-faq" aria-labelledby="pp-faq-title">
          <h2 id="pp-faq-title" className="pp-h2">Questions</h2>
          <PricingFAQ />
        </section>

        <p className="pp-foot">
          This is plan information, not financial advice. Pivot builds and simulates strategies; it does not place live broker orders.
        </p>
      </main>
    </SiteChrome>
  );
}
