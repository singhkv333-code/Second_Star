"use client";

/**
 * Cancellation, without dark patterns.
 *
 *   1. review       what changes, and on which date; nothing is deleted
 *   2. alternatives only the ones that really exist for this person (a
 *                   cheaper plan, yearly billing) — skipped when there are none
 *   3. confirm      one clear button; "Keep my plan" beside it, same weight
 *   4. done         when access ends, and how to come back
 *
 * Rules kept here: "Continue to cancel" is visible on every step and never
 * styled as the lesser choice; there is no guilt copy and no countdown; the
 * reason question is optional and never blocks; at most three clicks from
 * the settings page to a cancelled subscription.
 */

import * as React from "react";
import { ArrowDownRight, CalendarCheck, Check, Loader2, PiggyBank } from "lucide-react";
import { fmtDate, fmtValue, inr, num } from "@/lib/billing/format";
import { planById, planName, rankOf } from "@/lib/billing/catalog";
import { annualSaving, losses, overAfterDowngrade, subscriptionView } from "@/lib/billing/entitlements";
import { billingApi } from "@/lib/billing/api";
import { track } from "@/lib/billing/analytics";
import type { BillingMe, Cycle, PaidPlanId, PublicCatalog } from "@/lib/billing/types";
import { Notice } from "./primitives";
import { PanelDesc, PanelTitle } from "./modal";

export type CancelStep = "review" | "alternatives" | "confirm" | "done";

const REASONS = [
  { id: "price", text: "Too expensive" },
  { id: "unused", text: "Not using it enough" },
  { id: "missing", text: "Missing something I need" },
  { id: "temporary", text: "Only needed it for a while" },
  { id: "other", text: "Something else" },
];

type Alt = { id: string; title: string; body: string; plan: PaidPlanId; cycle: Cycle; icon: React.ReactNode };

function alternatives(cat: PublicCatalog, me: BillingMe): Alt[] {
  const v = subscriptionView(me);
  const plan = v.subPlan;
  if (!plan || plan === "free" || plan === "anonymous") return [];
  const out: Alt[] = [];
  // a cheaper paid plan, if there is one below this
  const lower = cat.plans
    .filter((p) => p.id !== "free" && rankOf(cat, p.id) < rankOf(cat, plan))
    .sort((a, b) => b.rank - a.rank)[0];
  if (lower && v.cycle) {
    const pr = lower.prices[v.cycle];
    out.push({
      id: "lower",
      title: `Switch to ${lower.name} instead`,
      body: `${pr ? inr(pr.per_month) : ""}/mo, from your renewal on ${fmtDate(v.renewsAt)}. Keeps ${num(Number(lower.features["ai.credits"] ?? 0))} AI credits a month and your larger alert limits.`,
      plan: lower.id as PaidPlanId,
      cycle: v.cycle,
      icon: <ArrowDownRight size={18} />,
    });
  }
  // yearly billing, if they pay monthly and yearly is actually cheaper
  const saved = annualSaving(cat, plan);
  if (v.cycle === "monthly" && saved > 0) {
    const pr = planById(cat, plan)?.prices.annual;
    out.push({
      id: "annual",
      title: "Pay yearly and save",
      body: `${pr ? inr(pr.per_month) : ""}/mo billed ${pr ? inr(pr.amount) : ""} a year, ${inr(saved)} less than monthly. Starts at your renewal.`,
      plan: plan as PaidPlanId,
      cycle: "annual",
      icon: <PiggyBank size={18} />,
    });
  }
  return out;
}

export function CancelPanel({
  catalog: cat,
  me,
  onClose,
  onDone,
  onAlternative,
  demo,
  initialStep = "review",
}: {
  catalog: PublicCatalog;
  me: BillingMe;
  onClose: () => void;
  onDone: (me: BillingMe | null) => void;
  onAlternative: (plan: PaidPlanId, cycle: Cycle) => void;
  demo?: boolean;
  initialStep?: CancelStep;
}): React.ReactElement {
  const v = subscriptionView(me);
  const from = v.subPlan ?? me.plan;
  const ends = v.renewsAt ?? me.subscription?.period_end ?? null;
  const lost = losses(cat, from, "free");
  const over = overAfterDowngrade(cat, me, "free");
  const alts = alternatives(cat, me);
  const [step, setStep] = React.useState<CancelStep>(initialStep);
  const [reason, setReason] = React.useState<string | null>(null);
  const [busy, setBusy] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);
  const [after, setAfter] = React.useState<BillingMe | null>(null);
  const steps: CancelStep[] = alts.length ? ["review", "alternatives", "confirm"] : ["review", "confirm"];

  React.useEffect(() => {
    track("cancel_step", { step, plan: from });
  }, [step, from]);

  const next = (): void => {
    const i = steps.indexOf(step);
    setStep(steps[i + 1] ?? "confirm");
  };

  const keep = (): void => {
    track("cancel_abandoned", { step, plan: from });
    onClose();
  };

  const cancel = async (): Promise<void> => {
    setBusy(true);
    setError(null);
    if (demo) {
      setBusy(false);
      setStep("done");
      return;
    }
    const r = await billingApi.cancel();
    setBusy(false);
    if (!r.ok) {
      setError(r.error);
      return;
    }
    track("cancel_confirmed", { plan: from, kind: reason ?? "none" });
    setAfter(r.data.billing);
    setStep("done");
  };

  if (step === "done") {
    return (
      <div className="bl-panel bl-scope">
        <div className="bl-state">
          <div className="bl-state-icon">
            <CalendarCheck size={24} />
          </div>
          <PanelTitle>Your subscription is cancelled</PanelTitle>
          <PanelDesc>
            {planName(cat, from)} stays active until {fmtDate(ends)}. You will not be charged again. After that your account moves to Free, and
            everything you have made stays where it is. You can subscribe again at any time.
          </PanelDesc>
          <button type="button" className="bl-btn bl-btn--primary bl-btn--lg" onClick={() => onDone(after)}>
            Done
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="bl-panel bl-scope">
      <div className="bl-panel-body">
        <div className="bl-steps" aria-hidden>
          {steps.map((s) => (
            <span key={s} data-on={steps.indexOf(s) <= steps.indexOf(step)} />
          ))}
        </div>

        {step === "review" ? (
          <>
            <div className="bl-stack" style={{ gap: 8 }}>
              <span className="bl-panel-kicker">Cancel subscription</span>
              <PanelTitle>What changes on {fmtDate(ends)}</PanelTitle>
              <PanelDesc>
                You keep {planName(cat, from)} until the end of the period you have paid for. Then your account moves to Free. Nothing you have
                made is deleted.
              </PanelDesc>
            </div>
            {lost.length ? (
              <div className="bl-diff" role="table" aria-label="Limits after cancelling">
                <span className="bl-diff-head" role="columnheader">Feature</span>
                <span className="bl-diff-head" role="columnheader" style={{ textAlign: "right" }}>{planName(cat, from)}</span>
                <span aria-hidden />
                <span className="bl-diff-head" role="columnheader" style={{ textAlign: "right" }}>Free</span>
                {lost.map((l) => {
                  const f = cat.features[l.key]!;
                  return (
                    <React.Fragment key={l.key}>
                      <span role="cell">{f.label}</span>
                      <span className="bl-diff-from" role="cell">{fmtValue(l.key, f, l.from)}</span>
                      <span aria-hidden style={{ color: "var(--bl-faint)" }}>→</span>
                      <span className="bl-diff-to" role="cell">{fmtValue(l.key, f, l.to)}</span>
                    </React.Fragment>
                  );
                })}
              </div>
            ) : null}
            {over.length ? (
              <Notice tone="warn" title="Some alerts will pause">
                {over.map((o) => `${num(o.used - o.limit)} ${cat.features[o.key]?.label.toLowerCase()}`).join(" and ")} above the Free limit
                will pause on {fmtDate(ends)}, newest first. They stay saved and can be re-armed.
              </Notice>
            ) : null}
          </>
        ) : null}

        {step === "alternatives" ? (
          <>
            <div className="bl-stack" style={{ gap: 8 }}>
              <span className="bl-panel-kicker">Before you go</span>
              <PanelTitle>Would one of these suit you better?</PanelTitle>
              <PanelDesc>Only if they help. Cancelling is one click away either way.</PanelDesc>
            </div>
            <div className="bl-choices">
              {alts.map((a) => (
                <button
                  key={a.id}
                  type="button"
                  className="bl-choice"
                  style={{ gridTemplateColumns: "auto minmax(0, 1fr)" }}
                  onClick={() => {
                    track("cancel_alternative_chosen", { kind: a.id, to_plan: a.plan, cycle: a.cycle });
                    onAlternative(a.plan, a.cycle);
                  }}
                >
                  <span aria-hidden>{a.icon}</span>
                  <span className="bl-choice-main">
                    <span className="bl-choice-name">{a.title}</span>
                    <span className="bl-choice-sub">{a.body}</span>
                  </span>
                </button>
              ))}
            </div>
          </>
        ) : null}

        {step === "confirm" ? (
          <>
            <div className="bl-stack" style={{ gap: 8 }}>
              <span className="bl-panel-kicker">Confirm</span>
              <PanelTitle>Cancel {planName(cat, from)}?</PanelTitle>
              <PanelDesc>
                Your plan stays active until {fmtDate(ends)} and will not renew. You will not be charged again.
              </PanelDesc>
            </div>
            <div className="bl-stack" style={{ gap: 10 }}>
              <span className="bl-small">What is the main reason? Optional.</span>
              <div className="bl-chips">
                {REASONS.map((r) => (
                  <button
                    key={r.id}
                    type="button"
                    className="bl-chip"
                    aria-pressed={reason === r.id}
                    onClick={() => setReason(reason === r.id ? null : r.id)}
                  >
                    {r.text}
                  </button>
                ))}
              </div>
            </div>
            {error ? (
              <Notice tone="bad" title="The cancellation did not go through">
                {error}
              </Notice>
            ) : null}
          </>
        ) : null}
      </div>

      <div className="bl-panel-foot">
        <div className="bl-row-flex" style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10 }}>
          <button type="button" className="bl-btn bl-btn--secondary bl-btn--lg" onClick={keep}>
            Keep my plan
          </button>
          {step === "confirm" ? (
            <button type="button" className="bl-btn bl-btn--danger bl-btn--lg" onClick={cancel} disabled={busy}>
              {busy ? <Loader2 size={16} className="bl-spin" /> : <Check size={16} />}
              Cancel subscription
            </button>
          ) : (
            <button type="button" className="bl-btn bl-btn--primary bl-btn--lg" onClick={next}>
              Continue to cancel
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
