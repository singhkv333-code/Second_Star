"use client";

/**
 * The contextual paywall: one panel whose words follow what triggered it.
 *
 *   feature_locked   a feature the plan does not include
 *   plan_limit       a cap on live objects (alerts, charts per tab, ...)
 *   quota_exhausted  a metered allowance used up (AI credits)
 *   evicted          this chart tab was closed by the parallel-chart limit
 *   signin           signed out: the next step is an account, not a payment
 *   upgrade          opened on purpose (an Upgrade button), no refusal
 *   trial_ended      a trial ran out
 *
 * A plan-limit refusal is the heading, followed by usage and available plans.
 * "Not now" always keeps the user exactly where they were.
 */

import * as React from "react";
import { useRouter } from "next/navigation";
import { ArrowRight, Check, X } from "lucide-react";
import { inr, num } from "@/lib/billing/format";
import { planById, planName, rankOf } from "@/lib/billing/catalog";
import {
  bestSavingPct,
  checkoutHref,
  meter,
  subscriptionView,
  type PaywallTrigger,
} from "@/lib/billing/entitlements";
import { featureCopy, featureLine } from "@/lib/billing/presentation";
import { track } from "@/lib/billing/analytics";
import type { BillingMe, Cycle, PaidPlanId, PlanId, PublicCatalog } from "@/lib/billing/types";
import { useBilling } from "./BillingProvider";
import { BillingToggle, UsageMeter } from "./primitives";
import { PaywallArt } from "./art";
import { PlanChangePanel } from "./PlanChange";
import { BillingModal, PanelDesc, PanelTitle } from "./modal";

function kicker(t: PaywallTrigger, cat: PublicCatalog): string {
  switch (t.kind) {
    case "feature_locked":
      return t.upgradeTo ? `${planName(cat, t.upgradeTo)} feature` : "Not on your plan";
    case "plan_limit":
      return "Plan limit reached";
    case "quota_exhausted":
      return "Monthly allowance used";
    case "evicted":
      return "Chart paused";
    case "signin":
      return "Free account";
    case "trial_ended":
      return "Trial ended";
    default:
      return "Upgrade";
  }
}

function title(t: PaywallTrigger, cat: PublicCatalog): string {
  const copy = t.feature ? featureCopy(cat, t.feature) : null;
  switch (t.kind) {
    case "quota_exhausted":
      return t.feature === "ai.credits" ? "You have used this month's AI credits" : `You have used this period's ${copy?.noun}`;
    case "plan_limit":
      return t.message || (t.limit != null
        ? `Your ${planName(cat, t.plan)} plan allows ${num(t.limit)} ${copy?.noun ?? "items"}`
        : (copy?.title ?? "You are at your plan's limit"));
    case "evicted":
      return "This chart was paused";
    case "signin":
      return "Sign in to keep going";
    case "trial_ended":
      return "Your trial has ended";
    case "feature_locked":
      return copy?.title ?? "Unlock this feature";
    default:
      return copy?.title ?? "Get more out of Pivot";
  }
}

/** Plans worth offering: the one the server named, and everything above it. */
function eligiblePlans(cat: PublicCatalog, t: PaywallTrigger, current: PlanId): PaidPlanId[] {
  const floor = t.upgradeTo ? rankOf(cat, t.upgradeTo) : rankOf(cat, current) + 1;
  return cat.plans
    .filter((p) => p.id !== "free" && p.id !== "anonymous" && rankOf(cat, p.id) >= floor && rankOf(cat, p.id) > rankOf(cat, current))
    .map((p) => p.id as PaidPlanId);
}

export type PaywallPanelProps = {
  trigger: PaywallTrigger;
  catalog: PublicCatalog;
  me: BillingMe | null;
  onClose: (reason?: "dismiss" | "cta") => void;
  /** Start checkout for a person with no live subscription. */
  onCheckout: (plan: PaidPlanId, cycle: Cycle) => void;
  /** Evicted tabs: take the slot back. */
  onReclaim?: () => void;
  onSignIn?: (mode: "signup" | "login") => void;
  onComparePlans?: () => void;
  /** After an in-place plan change succeeded. */
  onChanged?: (me: BillingMe) => void;
  demo?: boolean;
  /** Close button in the corner (off when rendered inline). */
  closable?: boolean;
};

export function PaywallPanel(props: PaywallPanelProps): React.ReactElement {
  const { trigger: t, catalog: cat, me, onClose, onCheckout } = props;
  const view = subscriptionView(me);
  const paying = ["active", "trialing", "past_due"].includes(view.state);
  const current: PlanId = paying ? (view.subPlan ?? t.plan) : (me?.plan ?? t.plan);
  const plans = eligiblePlans(cat, t, current === "anonymous" ? "free" : current);
  const [cycle, setCycle] = React.useState<Cycle>(paying && view.cycle ? view.cycle : "annual");
  const [pick, setPick] = React.useState<PaidPlanId | null>(plans[0] ?? null);
  const [changing, setChanging] = React.useState(false);
  const copy = t.feature ? featureCopy(cat, t.feature) : null;
  const f = t.feature ? cat.features[t.feature] : undefined;

  if (changing && pick && me) {
    return (
      <PlanChangePanel
        catalog={cat}
        me={me}
        to={pick}
        cycle={cycle}
        demo={props.demo}
        onBack={() => setChanging(false)}
        onDone={(next) => {
          props.onChanged?.(next);
          onClose("cta");
        }}
      />
    );
  }

  const usage =
    (t.kind === "quota_exhausted" || t.kind === "plan_limit" || t.kind === "evicted") && t.limit != null && t.used != null
      ? meter(Math.max(t.used, t.kind === "quota_exhausted" ? t.limit : t.used), t.limit, t.resetsAt ?? null)
      : null;

  const chosen = pick ? planById(cat, pick) : undefined;
  const price = chosen?.prices[cycle];

  const primary = (): void => {
    if (!pick) return;
    track("paywall_cta_clicked", { kind: t.kind, feature: t.feature, plan: pick, cycle, surface: t.surface });
    if (paying) {
      setChanging(true);
      return;
    }
    onClose("cta");
    onCheckout(pick, cycle);
  };

  const isSignin = t.kind === "signin";

  return (
    <div className="bl-panel bl-scope">
      {props.closable !== false ? (
        <button type="button" className="bl-x bl-modal-close" aria-label="Close" onClick={() => onClose("dismiss")}>
          <X size={18} />
        </button>
      ) : null}
      <div className="bl-panel-art" aria-hidden>
        <PaywallArt feature={t.feature} kind={t.kind} />
      </div>

      <div className="bl-panel-body">
        <div className="bl-stack" style={{ gap: 8 }}>
          <span className="bl-panel-kicker">{kicker(t, cat)}</span>
          <PanelTitle>{title(t, cat)}</PanelTitle>
          {t.kind !== "plan_limit" && t.kind !== "quota_exhausted" && t.message ? <PanelDesc>{t.message}</PanelDesc> : null}
        </div>

        {usage && t.feature ? (
          <UsageMeter
            label={f?.label ?? copy?.title ?? ""}
            view={usage}
            verb={t.kind === "quota_exhausted" ? "used" : "in use"}
            showReset={t.kind === "quota_exhausted"}
          />
        ) : null}

        {isSignin ? (
          <ul className="bl-benefits">
            {[
              t.feature === "ai.credits" && cat.plans[0]
                ? `${num(Number(cat.plans[0].features["ai.credits"] ?? 0))} AI questions a month, free`
                : "Your own alerts, layouts and conversations, saved",
              "Alerts that reach you when you are away from the chart",
              "No card required",
            ].map((s) => (
              <li key={s}>
                <Check size={16} aria-hidden />
                {s}
              </li>
            ))}
          </ul>
        ) : null}

        {!isSignin && plans.length ? (
          <div className="bl-stack">
            <div className="bl-row-flex" style={{ justifyContent: "space-between" }}>
              <span className="bl-h3" style={{ fontSize: 15 }}>
                {plans.length > 1 ? "Choose a plan" : `${planName(cat, plans[0])} gives you`}
              </span>
              {!paying ? <BillingToggle cycle={cycle} onChange={setCycle} savePct={bestSavingPct(cat)} /> : null}
            </div>
            <div className="bl-choices" role="radiogroup" aria-label="Plans">
              {plans.map((id) => {
                const p = planById(cat, id)!;
                const pr = p.prices[cycle];
                const line = t.feature && f ? featureLine(t.feature, f, p.features[t.feature] ?? null) : null;
                return (
                  <button
                    key={id}
                    type="button"
                    role="radio"
                    aria-checked={pick === id}
                    className="bl-choice"
                    onClick={() => setPick(id)}
                  >
                    <span className="bl-choice-radio" aria-hidden />
                    <span className="bl-choice-main">
                      <span className="bl-choice-name">
                        {p.name}
                      </span>
                      <span className="bl-choice-sub">{line}</span>
                    </span>
                    <span className="bl-choice-price">
                      <strong>{pr ? inr(pr.per_month) : "—"}</strong>
                      <span>/mo{cycle === "annual" ? ", billed yearly" : ""}</span>
                    </span>
                  </button>
                );
              })}
            </div>
          </div>
        ) : null}

        {!isSignin && !plans.length ? (
          <p className="bl-small">
            You are on the highest limit available for this.
          </p>
        ) : null}
      </div>

      <div className="bl-panel-foot">
        {isSignin ? (
          <>
            <button type="button" className="bl-btn bl-btn--primary bl-btn--lg bl-btn--block" onClick={() => props.onSignIn?.("signup")}>
              Create a free account
            </button>
            <div className="bl-row-flex">
              <span className="bl-small">Already have one?</span>
              <button type="button" className="bl-link" onClick={() => props.onSignIn?.("login")}>
                Sign in
              </button>
            </div>
          </>
        ) : t.kind === "evicted" ? (
          <>
            <button type="button" className="bl-btn bl-btn--primary bl-btn--lg bl-btn--block" onClick={() => { props.onReclaim?.(); onClose("cta"); }}>
              Use this tab
            </button>
            <p className="bl-fine bl-center">The oldest other tab will pause instead.</p>
            {plans.length ? (
              <button type="button" className="bl-btn bl-btn--secondary bl-btn--block" onClick={primary}>
                {paying ? `Upgrade to ${chosen?.name}` : `Get ${chosen?.name} for more charts`}
              </button>
            ) : null}
          </>
        ) : plans.length ? (
          <>
            <button type="button" className="bl-btn bl-btn--primary bl-btn--lg bl-btn--block" onClick={primary} disabled={!pick}>
              {paying ? `Upgrade to ${chosen?.name}` : "Continue to checkout"}
              <ArrowRight size={16} aria-hidden />
            </button>
            {price && !paying ? (
              <p className="bl-fine bl-center bl-num">
                {cycle === "annual"
                  ? `${inr(price.amount)} billed yearly (${inr(price.per_month)}/mo). GST included. Cancel any time.`
                  : `${inr(price.amount)} billed monthly. GST included. Cancel any time.`}
              </p>
            ) : null}
          </>
        ) : null}
        <div className="bl-row-flex">
          <button type="button" className="bl-btn bl-btn--ghost bl-btn--sm" onClick={() => onClose("dismiss")}>
            Not now
          </button>
          {props.onComparePlans && !isSignin ? (
            <button type="button" className="bl-btn bl-btn--ghost bl-btn--sm" onClick={props.onComparePlans}>
              Compare all plans
            </button>
          ) : null}
        </div>
      </div>
    </div>
  );
}

/** The app-wide paywall, driven by BillingProvider's trigger. */
export function PaywallDialog(): React.ReactElement | null {
  const { trigger, catalog, me, closePaywall, setMe, demo } = useBilling();
  const router = useRouter();
  if (!trigger) return null;
  const back = trigger.returnTo || "/";
  return (
    <BillingModal open onOpenChange={(o) => !o && closePaywall("dismiss")} label="Upgrade">
      <PaywallPanel
        key={`${trigger.kind}:${trigger.feature ?? ""}`}
        trigger={trigger}
        catalog={catalog}
        me={me}
        demo={demo}
        onClose={closePaywall}
        onChanged={setMe}
        onCheckout={(plan, cycle) =>
          router.push(checkoutHref(plan, cycle, { returnTo: back, feature: trigger.feature, surface: trigger.surface }))
        }
        onReclaim={() => window.dispatchEvent(new CustomEvent("pivot:reclaim-tab"))}
        onSignIn={(mode) => router.push(`/${mode}?next=${encodeURIComponent(back)}`)}
        onComparePlans={() => {
          closePaywall("cta");
          router.push("/pricing");
        }}
      />
    </BillingModal>
  );
}
