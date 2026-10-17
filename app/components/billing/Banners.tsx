"use client";

/**
 * Banners: the subscription's state, said once, where it matters, with the
 * one action that resolves it. No timers ticking in seconds and no invented
 * scarcity — a trial counts DAYS to a real end date, and a renewal reminder
 * states the real amount and date (RBI's e-mandate rules also have the bank
 * notify before each debit).
 */

import * as React from "react";
import { fmtDate, inr } from "@/lib/billing/format";
import { planById, planName } from "@/lib/billing/catalog";
import { daysUntil } from "@/lib/billing/format";
import { nowSec, subscriptionView } from "@/lib/billing/entitlements";
import type { BillingMe, PublicCatalog } from "@/lib/billing/types";
import { Notice } from "./primitives";

type Act = { label: string; onClick: () => void; primary?: boolean };

function Actions({ acts }: { acts: Act[] }): React.ReactElement {
  return (
    <>
      {acts.map((a) => (
        <button
          key={a.label}
          type="button"
          className={`bl-btn bl-btn--sm ${a.primary ? "bl-btn--primary" : "bl-btn--secondary"}`}
          onClick={a.onClick}
        >
          {a.label}
        </button>
      ))}
    </>
  );
}

/** A quiet, dismissible prompt for a free user, tied to what they use. */
export function UpgradeBanner({
  title,
  children,
  onUpgrade,
  onDismiss,
  cta = "See plans",
}: {
  title: string;
  children?: React.ReactNode;
  onUpgrade: () => void;
  onDismiss?: () => void;
  cta?: string;
}): React.ReactElement {
  return (
    <Notice tone="ink" title={title} onDismiss={onDismiss} actions={<Actions acts={[{ label: cta, onClick: onUpgrade, primary: true }]} />}>
      {children}
    </Notice>
  );
}

export function TrialBanner({
  catalog,
  me,
  onChoose,
  now = nowSec(),
}: {
  catalog: PublicCatalog;
  me: BillingMe;
  onChoose: () => void;
  now?: number;
}): React.ReactElement | null {
  const v = subscriptionView(me, now);
  if (v.state !== "trialing" || !v.endsAt) return null;
  const d = daysUntil(v.endsAt, now) ?? 0;
  const name = planName(catalog, v.subPlan);
  return (
    <Notice
      tone={d <= 2 ? "warn" : "info"}
      title={d === 0 ? `Your ${name} trial ends today` : `${d} day${d === 1 ? "" : "s"} left in your ${name} trial`}
      actions={<Actions acts={[{ label: "Choose a plan", onClick: onChoose, primary: true }]} />}
    >
      The trial ends on {fmtDate(v.endsAt)}. Choose a plan to keep {name}; otherwise your account moves to Free and nothing you made is deleted.
    </Notice>
  );
}

export function PastDueBanner({
  catalog,
  me,
  onFix,
  now = nowSec(),
}: {
  catalog: PublicCatalog;
  me: BillingMe;
  onFix: () => void;
  now?: number;
}): React.ReactElement | null {
  const v = subscriptionView(me, now);
  if (v.state !== "past_due") return null;
  return (
    <Notice
      tone="bad"
      title="Your last payment did not go through"
      actions={<Actions acts={[{ label: "Update payment method", onClick: onFix, primary: true }]} />}
    >
      {planName(catalog, v.subPlan)} stays active until {fmtDate(v.graceUntil)} while the payment is retried. Update your card or UPI mandate before
      then to avoid moving to Free.
    </Notice>
  );
}

export function CancelingBanner({
  catalog,
  me,
  onResubscribe,
  now = nowSec(),
}: {
  catalog: PublicCatalog;
  me: BillingMe;
  onResubscribe?: () => void;
  now?: number;
}): React.ReactElement | null {
  const v = subscriptionView(me, now);
  if (v.state !== "canceling") return null;
  return (
    <Notice
      title={`${planName(catalog, v.subPlan)} ends on ${fmtDate(v.endsAt)}`}
      actions={onResubscribe ? <Actions acts={[{ label: "See plans", onClick: onResubscribe }]} /> : undefined}
    >
      Your subscription will not renew and you will not be charged again. After that date your account moves to Free. To keep your plan, subscribe
      again once this period ends.
    </Notice>
  );
}

export function IncompleteBanner({
  catalog,
  me,
  onResume,
}: {
  catalog: PublicCatalog;
  me: BillingMe;
  onResume: () => void;
}): React.ReactElement | null {
  const v = subscriptionView(me);
  if (v.state !== "incomplete") return null;
  return (
    <Notice
      tone="warn"
      title="Your checkout was not completed"
      actions={<Actions acts={[{ label: "Resume checkout", onClick: onResume, primary: true }]} />}
    >
      You started {planName(catalog, v.subPlan)} ({v.cycle === "annual" ? "yearly" : "monthly"}) but the payment was not finished. Nothing was
      charged.
    </Notice>
  );
}

export function ExpiredBanner({
  catalog,
  me,
  onResubscribe,
}: {
  catalog: PublicCatalog;
  me: BillingMe;
  onResubscribe: () => void;
}): React.ReactElement | null {
  const v = subscriptionView(me);
  if (v.state !== "expired") return null;
  return (
    <Notice
      title={`Your ${planName(catalog, v.subPlan)} plan ended on ${fmtDate(v.endsAt)}`}
      actions={<Actions acts={[{ label: `Resubscribe to ${planName(catalog, v.subPlan)}`, onClick: onResubscribe, primary: true }]} />}
    >
      You are on Free now. Alerts above the Free limit were paused, not deleted, and come back when you resubscribe.
    </Notice>
  );
}

/** Shown in the week before an automatic renewal. */
export function RenewalReminder({
  catalog,
  me,
  onManage,
  now = nowSec(),
  windowDays = 7,
}: {
  catalog: PublicCatalog;
  me: BillingMe;
  onManage: () => void;
  now?: number;
  windowDays?: number;
}): React.ReactElement | null {
  const v = subscriptionView(me, now);
  if (v.state !== "active" || !v.renewsAt || v.pending) return null;
  const d = daysUntil(v.renewsAt, now);
  if (d === null || d > windowDays) return null;
  const pr = v.subPlan && v.cycle ? planById(catalog, v.subPlan)?.prices[v.cycle] : undefined;
  return (
    <Notice title={`Renews on ${fmtDate(v.renewsAt)}`} actions={<Actions acts={[{ label: "Manage", onClick: onManage }]} />}>
      {planName(catalog, v.subPlan)} renews automatically for {pr ? inr(pr.amount) : "your plan price"} (GST included).
    </Notice>
  );
}

/** Card expiry is not on the subscription wire today; render this when a
 *  provider integration supplies the expiry month. */
export function CardExpiryBanner({
  last4,
  expires,
  onUpdate,
}: {
  last4?: string;
  expires: string;
  onUpdate: () => void;
}): React.ReactElement {
  return (
    <Notice tone="warn" title="Your card expires soon" actions={<Actions acts={[{ label: "Update card", onClick: onUpdate, primary: true }]} />}>
      The card {last4 ? `ending ${last4} ` : ""}on your subscription expires {expires}. Update it before your next renewal to avoid a failed
      payment.
    </Notice>
  );
}

/** The one banner a state needs, in priority order. */
export function SubscriptionBanner(props: {
  catalog: PublicCatalog;
  me: BillingMe;
  onFixPayment: () => void;
  onChoosePlan: () => void;
  onResumeCheckout: () => void;
  onManage: () => void;
}): React.ReactElement | null {
  const { catalog, me } = props;
  const s = subscriptionView(me).state;
  if (s === "past_due") return <PastDueBanner catalog={catalog} me={me} onFix={props.onFixPayment} />;
  if (s === "trialing") return <TrialBanner catalog={catalog} me={me} onChoose={props.onChoosePlan} />;
  if (s === "incomplete") return <IncompleteBanner catalog={catalog} me={me} onResume={props.onResumeCheckout} />;
  if (s === "canceling") return <CancelingBanner catalog={catalog} me={me} onResubscribe={props.onChoosePlan} />;
  if (s === "expired") return <ExpiredBanner catalog={catalog} me={me} onResubscribe={props.onResumeCheckout} />;
  return <RenewalReminder catalog={catalog} me={me} onManage={props.onManage} />;
}
