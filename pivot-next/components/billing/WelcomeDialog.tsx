"use client";

/**
 * The welcome after a payment: what the new plan unlocked, once.
 *
 * Raised two ways, because a payment can be confirmed in two places:
 *   - checkout's success phase calls `celebrate(plan)` the moment
 *     /billing/verify answers active;
 *   - the provider sees a Razorpay subscription that went active in the last
 *     two days and has not been welcomed in this browser — a payment the
 *     webhook confirmed after the page closed, or one finished in another tab.
 *
 * The benefits are the plan's own catalog values (PLAN_COPY highlights), so
 * the dialog never promises a number the server does not enforce.
 */

import * as React from "react";
import { useRouter } from "next/navigation";
import { Check, X } from "lucide-react";
import { planById } from "@/lib/billing/catalog";
import { featureLine, isShown, isUnbuilt, PLAN_COPY } from "@/lib/billing/presentation";
import { PIVOT_ALIEN } from "@/lib/publicAssets";
import type { BillingMe, PaidPlanId, PublicCatalog } from "@/lib/billing/types";
import { BillingModal, PanelDesc, PanelTitle } from "./modal";

const SEEN = "pivot-welcome-seen:";
const RECENT = 2 * 86400;

export function welcomeSeen(plan: PaidPlanId): boolean {
  try { return localStorage.getItem(SEEN + plan) === "1"; } catch { return true; }
}

export function markWelcomed(plan: PaidPlanId): void {
  try { localStorage.setItem(SEEN + plan, "1"); } catch { /* private mode: it may show again */ }
}

/** A paid plan this browser has not welcomed yet, if the payment is recent. */
export function unwelcomedPlan(me: BillingMe | null): PaidPlanId | null {
  const s = me?.subscription;
  if (!s || s.provider !== "razorpay" || s.status !== "active") return null;
  if (me.plan !== "pro" && me.plan !== "pro_plus") return null;
  if (!s.period_start || Date.now() / 1000 - s.period_start > RECENT) return null;
  return welcomeSeen(me.plan) ? null : me.plan;
}

const date = (unix: number): string => new Date(unix * 1000).toLocaleDateString("en-IN",
  { day: "numeric", month: "short", year: "numeric", timeZone: "Asia/Kolkata" });

export function WelcomePanel({
  plan,
  catalog,
  me,
  onClose,
  onBilling,
}: {
  plan: PaidPlanId;
  catalog: PublicCatalog;
  me: BillingMe | null;
  onClose: () => void;
  onBilling: () => void;
}): React.ReactElement {
  const target = planById(catalog, plan);
  const name = target?.name ?? (plan === "pro" ? "Pro" : "Pro+");
  const benefits = PLAN_COPY[plan].highlights.flatMap((k) => {
    const f = catalog.features[k];
    const v = target?.features[k];
    if (!f || v === undefined || isUnbuilt(k, f) || !isShown(catalog, k)) return [];
    return [featureLine(k, f, v)];
  });
  const s = me?.subscription;
  const renews = s && s.plan === plan && s.period_end && !s.cancel_at_period_end ? s.period_end : null;

  return (
    <div className="bl-panel bl-scope">
      <button type="button" className="bl-x bl-modal-close" aria-label="Close" onClick={onClose}>
        <X size={18} />
      </button>
      <div className="bl-welcome-art" aria-hidden>
        <div className="bl-welcome-badge">
          {/* eslint-disable-next-line @next/next/no-img-element -- a bundled asset path, see publicAssets.ts */}
          <img src={PIVOT_ALIEN} alt="" />
        </div>
      </div>

      <div className="bl-panel-body">
        <div className="bl-stack" style={{ gap: 8 }}>
          <span className="bl-panel-kicker">Payment successful</span>
          <PanelTitle>Welcome to Pivot {name}</PanelTitle>
          <PanelDesc>Your plan is active now. Everything below is unlocked on your account, in the chart and in chat.</PanelDesc>
        </div>
        <ul className="bl-benefits bl-benefits--welcome">
          {benefits.map((b) => (
            <li key={b}>
              <Check size={16} aria-hidden />
              {b}
            </li>
          ))}
        </ul>
        {renews ? <p className="bl-fine" style={{ margin: 0 }}>Renews on {date(renews)}. Invoices and cancellation are in Plan &amp; billing.</p> : null}
      </div>

      <div className="bl-panel-foot">
        <button type="button" className="bl-btn bl-btn--primary bl-btn--lg bl-btn--block" onClick={onClose}>
          Start using {name}
        </button>
        <div className="bl-row-flex">
          <button type="button" className="bl-btn bl-btn--ghost" onClick={onBilling}>
            Plan &amp; billing
          </button>
        </div>
      </div>
    </div>
  );
}

export function WelcomeDialog({
  plan,
  catalog,
  me,
  onClose,
}: {
  plan: PaidPlanId | null;
  catalog: PublicCatalog;
  me: BillingMe | null;
  onClose: () => void;
}): React.ReactElement | null {
  const router = useRouter();
  if (!plan) return null;
  return (
    <BillingModal open onOpenChange={(o) => !o && onClose()} label={`Welcome to ${plan === "pro" ? "Pro" : "Pro+"}`}>
      <WelcomePanel
        plan={plan}
        catalog={catalog}
        me={me}
        onClose={onClose}
        onBilling={() => {
          onClose();
          router.push("/settings/billing");
        }}
      />
    </BillingModal>
  );
}
