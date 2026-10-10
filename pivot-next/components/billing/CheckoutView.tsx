"use client";

/**
 * Checkout: the order summary, the hand-off to Razorpay, and every way it can
 * end.
 *
 *   review      what you are buying, the total charged today, how it renews
 *   opening     the server is creating the subscription; Razorpay is loading
 *   verifying   Razorpay answered; the server is checking the signature
 *   success     the plan is live; one click back to where the user was
 *   pending     paid, but the provider has not confirmed yet (HTTP 202)
 *   failed      declined or errored; retry, nothing else changed
 *   dismissed   the user closed Razorpay; nothing was charged; resume
 *
 * And the reasons not to start at all, explained before the provider is
 * asked: signed out, already on this exact plan, already subscribed (change
 * instead of buying twice), payments not configured.
 */

import * as React from "react";
import { useRouter } from "next/navigation";
import { ArrowLeft, Check, Clock, CreditCard, Loader2, LogIn, Settings2, ShieldCheck } from "lucide-react";
import { inr } from "@/lib/billing/format";
import { planById, planName } from "@/lib/billing/catalog";
import {
  annualSaving,
  bestSavingPct,
  checkoutBlock,
  safeReturnPath,
} from "@/lib/billing/entitlements";
import { billingApi } from "@/lib/billing/api";
import { openCheckout } from "@/lib/billing/razorpay";
import { track } from "@/lib/billing/analytics";
import { featureCopy, featureLine, isShown, PLAN_COPY } from "@/lib/billing/presentation";
import { renewalTerms } from "@/lib/billing/policy";
import type { Cycle, PaidPlanId } from "@/lib/billing/types";
import { useBilling } from "./BillingProvider";
import { BillingToggle, Notice, SkeletonRows, TrustRow } from "./primitives";
import { BillingModal } from "./modal";
import { PlanChangePanel } from "./PlanChange";

export type CheckoutPhase = "review" | "opening" | "verifying" | "success" | "pending" | "failed" | "dismissed";

export function CheckoutView({
  plan,
  initialCycle = "annual",
  returnTo,
  feature,
  surface,
  initialPhase = "review",
  initialError,
}: {
  plan: PaidPlanId;
  initialCycle?: Cycle;
  returnTo?: string | null;
  feature?: string | null;
  surface?: string | null;
  initialPhase?: CheckoutPhase;
  initialError?: string;
}): React.ReactElement {
  const { catalog: cat, me, loading, setMe, refresh, demo } = useBilling();
  const router = useRouter();
  const [cycle, setCycle] = React.useState<Cycle>(initialCycle);
  const [phase, setPhase] = React.useState<CheckoutPhase>(initialPhase);
  const [error, setError] = React.useState<string | null>(initialError ?? null);
  const [changeOpen, setChangeOpen] = React.useState(false);
  const back = safeReturnPath(returnTo, "/");
  const target = planById(cat, plan);
  const price = target?.prices[cycle];
  const block = checkoutBlock(cat, me, plan, cycle);

  React.useEffect(() => {
    track("plan_selected", { plan, cycle, feature: feature ?? undefined, surface: surface ?? undefined });
    // eslint-disable-next-line react-hooks/exhaustive-deps -- once per arrival
  }, []);

  // A 202 means the provider has the payment but has not confirmed it; the
  // webhook will. Poll the plan briefly so the page can say so the moment it lands.
  React.useEffect(() => {
    if (phase !== "pending" || demo) return;
    let tries = 0;
    const id = setInterval(async () => {
      tries += 1;
      const m = await refresh();
      if (m && m.plan === plan) {
        clearInterval(id);
        setPhase("success");
      } else if (tries >= 10) {
        clearInterval(id);
      }
    }, 3000);
    return () => clearInterval(id);
  }, [phase, demo, plan, refresh]);

  const pay = async (): Promise<void> => {
    setError(null);
    setPhase("opening");
    track("checkout_started", { plan, cycle, surface: surface ?? undefined });
    if (demo) {
      setPhase("success");
      return;
    }
    const co = await billingApi.checkout(plan, cycle);
    if (!co.ok) {
      setError(co.error);
      setPhase("failed");
      track("checkout_failed", { plan, cycle, code: co.code ?? String(co.status) });
      return;
    }
    let outcome;
    try {
      outcome = await openCheckout({
        keyId: co.data.key_id,
        subscriptionId: co.data.subscription_id,
        description: `${target?.name} · ${cycle === "annual" ? "yearly" : "monthly"}`,
        email: co.data.prefill?.email,
      });
    } catch (e) {
      setError(e instanceof Error ? e.message : "The payment window could not open.");
      setPhase("failed");
      return;
    }
    if (outcome.kind === "dismissed") {
      track("checkout_dismissed", { plan, cycle });
      setPhase("dismissed");
      return;
    }
    if (outcome.kind === "failed") {
      track("checkout_failed", { plan, cycle, code: "declined" });
      setError(outcome.reason);
      setPhase("failed");
      return;
    }
    setPhase("verifying");
    const v = await billingApi.verify(outcome.ids);
    if (!v.ok) {
      track("checkout_failed", { plan, cycle, code: v.code ?? String(v.status) });
      setError(v.error);
      setPhase("failed");
      return;
    }
    if (v.data.pending) {
      track("checkout_pending", { plan, cycle });
      setPhase("pending");
      return;
    }
    if (v.data.billing) setMe(v.data.billing);
    track("checkout_succeeded", { plan, cycle });
    setPhase("success");
  };

  // ── the outcome screens ─────────────────────────────────────────────
  if (phase === "success") {
    const copy = feature ? featureCopy(cat, feature) : null;
    return (
      <Shell back={back}>
        <div className="bl-card bl-state" style={{ padding: "56px 24px" }}>
          <div className="bl-state-icon bl-state-icon--good">
            <Check size={26} />
          </div>
          <span className="bl-eyebrow" style={{ margin: 0 }}>Payment received</span>
          <h1 className="bl-h2">Welcome to {target?.name}</h1>
          <p className="bl-p" style={{ maxWidth: 460 }}>
            {copy ? `${copy.title} is unlocked, along with everything else in ${target?.name}. ` : `Everything in ${target?.name} is unlocked. `}
            A receipt is on its way to your email, and the invoice is in Plan &amp; billing.
          </p>
          <div className="bl-row-flex" style={{ justifyContent: "center" }}>
            <button type="button" className="bl-btn bl-btn--primary bl-btn--lg" onClick={() => router.push(back)}>
              {back === "/" ? "Go to Pivot" : "Back to where you were"}
            </button>
            <button type="button" className="bl-btn bl-btn--secondary bl-btn--lg" onClick={() => router.push("/settings/billing")}>
              Plan &amp; billing
            </button>
          </div>
        </div>
      </Shell>
    );
  }

  if (phase === "pending") {
    return (
      <Shell back={back}>
        <div className="bl-card bl-state" style={{ padding: "56px 24px" }}>
          <div className="bl-state-icon bl-state-icon--warn">
            <Clock size={24} />
          </div>
          <h1 className="bl-h2">Payment received, confirming</h1>
          <p className="bl-p" style={{ maxWidth: 460 }}>
            Razorpay has your payment and is confirming it with your bank. Your plan switches to {target?.name} as soon as it does, usually within a
            minute. You can leave this page; nothing else is needed from you.
          </p>
          <div className="bl-row-flex" style={{ justifyContent: "center" }}>
            <Loader2 size={16} className="bl-spin" /> <span className="bl-small">Checking</span>
          </div>
          <button type="button" className="bl-btn bl-btn--secondary" onClick={() => router.push(back)}>
            Continue using Pivot
          </button>
        </div>
      </Shell>
    );
  }

  // ── review, and the states that interrupt it ──────────────────────
  const saved = annualSaving(cat, plan);
  const copy = PLAN_COPY[plan];

  return (
    <Shell back={back}>
      <div className="bl-stack" style={{ gap: 6, marginBottom: 28 }}>
        <span className="bl-eyebrow" style={{ margin: 0 }}>Checkout</span>
        <h1 className="bl-h1">Pivot {target?.name}</h1>
      </div>

      {loading && !me ? (
        <div className="bl-card">
          <SkeletonRows rows={5} />
        </div>
      ) : (
        <div className="bl-checkout">
          <section className="bl-card bl-stack bl-stack--lg" aria-label="What you get">
            {feature ? (
              <Notice title={`You were trying to use ${featureCopy(cat, feature).noun}`}>
                It unlocks the moment the payment clears, and we will take you straight back.
              </Notice>
            ) : null}
            <div className="bl-stack">
              <span className="bl-h3">Billing</span>
              <BillingToggle cycle={cycle} onChange={(c) => { setCycle(c); track("billing_cycle_toggled", { cycle: c, surface: "checkout" }); }} savePct={bestSavingPct(cat)} />
            </div>
            <div className="bl-stack">
              <span className="bl-h3">Included in {target?.name}</span>
              <ul className="bl-plan-list">
                {copy.highlights.map((k) => {
                  const f = cat.features[k];
                  const v = target?.features[k];
                  if (!f || v === undefined || !isShown(cat, k)) return null;
                  return (
                    <li key={k}>
                      <Check size={16} aria-hidden />
                      {featureLine(k, f, v)}
                    </li>
                  );
                })}
              </ul>
            </div>
          </section>

          <section className="bl-card bl-stack bl-stack--lg" aria-label="Order summary">
            <span className="bl-h3">Order summary</span>
            <div>
              <div className="bl-line">
                <span>
                  {target?.name}, billed {cycle === "annual" ? "yearly" : "monthly"}
                </span>
                <span>{inr(price?.amount)}</span>
              </div>
              {cycle === "annual" && price ? (
                <div className="bl-line">
                  <span>Works out to</span>
                  <span>{inr(price.per_month)}/mo</span>
                </div>
              ) : null}
              {cycle === "annual" && saved ? (
                <div className="bl-line">
                  <span>Saving against monthly</span>
                  <span style={{ color: "var(--bl-good)" }}>{inr(saved)} a year</span>
                </div>
              ) : null}
              <div className="bl-line">
                <span>GST</span>
                <span>Included</span>
              </div>
              <div className="bl-total">
                <span className="bl-h3">Due today</span>
                <strong>{inr(price?.amount)}</strong>
              </div>
            </div>

            <BlockOrPay
              block={block}
              phase={phase}
              error={error}
              planName={target?.name ?? plan}
              amount={inr(price?.amount)}
              onPay={pay}
              onSignIn={() => router.push(`/login?next=${encodeURIComponent(`/checkout?plan=${plan}&cycle=${cycle}&return=${back}`)}`)}
              onManage={() => router.push("/settings/billing")}
              onChange={() => setChangeOpen(true)}
              currentName={block && block.reason === "use_change" ? planName(cat, block.current) : ""}
            />

            {price ? <p className="bl-fine">{renewalTerms(inr(price.amount), cycle === "annual" ? "year" : "month")}</p> : null}
          </section>
        </div>
      )}

      <div style={{ marginTop: 32 }}>
        <TrustRow />
      </div>

      {me && changeOpen ? (
        <BillingModal open onOpenChange={setChangeOpen} label="Change plan">
          <PlanChangePanel
            catalog={cat}
            me={me}
            to={plan}
            cycle={cycle}
            demo={demo}
            onBack={() => setChangeOpen(false)}
            onDone={(m) => {
              setMe(m);
              setChangeOpen(false);
              router.push(back);
            }}
          />
        </BillingModal>
      ) : null}
    </Shell>
  );
}

function BlockOrPay(p: {
  block: ReturnType<typeof checkoutBlock>;
  phase: CheckoutPhase;
  error: string | null;
  planName: string;
  amount: string;
  currentName: string;
  onPay: () => void;
  onSignIn: () => void;
  onManage: () => void;
  onChange: () => void;
}): React.ReactElement {
  const b = p.block;
  if (b?.reason === "signin") {
    return (
      <div className="bl-stack">
        <Notice title="Sign in to subscribe">Your plan is attached to your Pivot account, so the chart and chat both see it.</Notice>
        <button type="button" className="bl-btn bl-btn--primary bl-btn--lg bl-btn--block" onClick={p.onSignIn}>
          <LogIn size={16} /> Sign in to continue
        </button>
      </div>
    );
  }
  if (b?.reason === "same_plan") {
    return (
      <div className="bl-stack">
        <Notice tone="good" title={`You are already on ${p.planName}`}>
          There is nothing to buy. Your subscription and invoices are in Plan &amp; billing.
        </Notice>
        <button type="button" className="bl-btn bl-btn--secondary bl-btn--lg bl-btn--block" onClick={p.onManage}>
          <Settings2 size={16} /> Manage plan &amp; billing
        </button>
      </div>
    );
  }
  if (b?.reason === "use_change") {
    return (
      <div className="bl-stack">
        <Notice title={`You already subscribe to ${p.currentName}`}>
          Change your existing subscription instead of starting a second one, so you are never billed twice.
        </Notice>
        <button type="button" className="bl-btn bl-btn--primary bl-btn--lg bl-btn--block" onClick={p.onChange}>
          Change to {p.planName}
        </button>
      </div>
    );
  }
  if (b?.reason === "unavailable") {
    return (
      <Notice tone="warn" title="Payments are not switched on yet">
        Pivot cannot take payments on this server right now, so nothing can be charged. Your current plan is unaffected.
      </Notice>
    );
  }
  const busy = p.phase === "opening" || p.phase === "verifying";
  return (
    <div className="bl-stack">
      {p.phase === "dismissed" ? (
        <Notice tone="warn" title="Checkout was closed">
          Nothing was charged. Pick up where you left off whenever you are ready.
        </Notice>
      ) : null}
      {p.phase === "failed" ? (
        <Notice tone="bad" title="The payment did not go through">
          {p.error || "Your bank or the payment provider declined it."} Nothing was charged. Try again, or use a different card or UPI app.
        </Notice>
      ) : null}
      <button type="button" className="bl-btn bl-btn--primary bl-btn--lg bl-btn--block" onClick={p.onPay} disabled={busy}>
        {busy ? <Loader2 size={16} className="bl-spin" /> : <CreditCard size={16} />}
        {p.phase === "opening"
          ? "Opening secure payment"
          : p.phase === "verifying"
            ? "Confirming payment"
            : p.phase === "failed" || p.phase === "dismissed"
              ? `Try again: pay ${p.amount}`
              : `Pay ${p.amount} securely`}
      </button>
      <span className="bl-fine bl-row-flex" style={{ justifyContent: "center" }}>
        <ShieldCheck size={14} /> Processed by Razorpay. Card, UPI and bank mandates are handled on their page.
      </span>
    </div>
  );
}

function Shell({ back, children }: { back: string; children: React.ReactNode }): React.ReactElement {
  const router = useRouter();
  return (
    <div className="bl-page bl-scope">
      <div className="bl-topbar">
        <button type="button" className="bl-back" onClick={() => router.push(back)}>
          <ArrowLeft size={16} /> Back
        </button>
        <span className="bl-fine">Secure checkout</span>
      </div>
      <div className="bl-wrap" style={{ paddingTop: 24 }}>
        {children}
      </div>
    </div>
  );
}

export function CheckoutFallback(): React.ReactElement {
  return (
    <div className="bl-page bl-scope">
      <div className="bl-wrap">
        <Notice tone="bad" title="That plan does not exist">
          Choose one from the pricing page.
        </Notice>
      </div>
    </div>
  );
}
