"use client";

/**
 * Changing a live subscription: the confirmation step for an upgrade, a
 * downgrade or a monthly/yearly switch, and the plan picker that leads to it.
 *
 * The confirmation states WHEN the change applies and WHAT it costs in
 * features before the button is pressed: an upgrade applies now; a downgrade
 * or cycle switch waits for the renewal date, so nobody loses days they paid
 * for (billing.api_change). A downgrade lists exactly what gets smaller and
 * which live alerts would pause — nothing is ever deleted.
 */

import * as React from "react";
import { ArrowLeft, ArrowRight, Check, Loader2 } from "lucide-react";
import { fmtDate, fmtValue, inr, num } from "@/lib/billing/format";
import { planById, planName } from "@/lib/billing/catalog";
import { changeKind, losses, overAfterDowngrade, subscriptionView } from "@/lib/billing/entitlements";
import { billingApi } from "@/lib/billing/api";
import { track } from "@/lib/billing/analytics";
import type { BillingMe, Cycle, PaidPlanId, PublicCatalog } from "@/lib/billing/types";
import { BillingToggle, Notice, PolicyText } from "./primitives";
import { UPGRADE_CHARGE_TBD } from "@/lib/billing/policy";
import { PanelDesc, PanelTitle } from "./modal";

export function PlanChangePanel({
  catalog: cat,
  me,
  to,
  cycle,
  onBack,
  onDone,
  demo,
  initialPhase = "confirm",
}: {
  catalog: PublicCatalog;
  me: BillingMe;
  to: PaidPlanId;
  cycle: Cycle;
  onBack: () => void;
  onDone: (me: BillingMe) => void;
  demo?: boolean;
  initialPhase?: "confirm" | "done";
}): React.ReactElement {
  const view = subscriptionView(me);
  const from = view.subPlan ?? me.plan;
  const kind = from === to ? "cycle" : changeKind(cat, from, to);
  const target = planById(cat, to);
  const price = target?.prices[cycle];
  const renewal = view.renewsAt ?? me.subscription?.period_end ?? null;
  const lost = kind === "downgrade" ? losses(cat, from, to) : [];
  const over = kind === "downgrade" ? overAfterDowngrade(cat, me, to) : [];
  const [phase, setPhase] = React.useState<"confirm" | "busy" | "done">(initialPhase);
  const [error, setError] = React.useState<string | null>(null);
  const [result, setResult] = React.useState<BillingMe | null>(null);

  const confirm = async (): Promise<void> => {
    setPhase("busy");
    setError(null);
    track("plan_change_requested", { from_plan: from, to_plan: to, cycle, kind });
    if (demo) {
      setPhase("done");
      return;
    }
    const r = await billingApi.change(to, cycle);
    if (!r.ok) {
      setError(r.error);
      setPhase("confirm");
      return;
    }
    track("plan_change_confirmed", { from_plan: from, to_plan: to, cycle, kind });
    setResult(r.data.billing);
    setPhase("done");
  };

  if (phase === "done") {
    const now = kind === "upgrade";
    return (
      <div className="bl-panel bl-scope">
        <div className="bl-state">
          <div className="bl-state-icon bl-state-icon--good">
            <Check size={24} />
          </div>
          <PanelTitle>{now ? `You are on ${target?.name}` : "Change scheduled"}</PanelTitle>
          <PanelDesc>
            {now
              ? `${target?.name} is active now. Everything it includes is unlocked.`
              : `You will move to ${target?.name}, billed ${cycle === "annual" ? "yearly" : "monthly"}, on ${fmtDate(renewal)}. Until then nothing changes. You can undo this from Plan & billing.`}
          </PanelDesc>
          <button type="button" className="bl-btn bl-btn--primary bl-btn--lg" onClick={() => onDone(result ?? me)}>
            Done
          </button>
        </div>
      </div>
    );
  }

  const headline =
    kind === "upgrade"
      ? `Upgrade to ${target?.name}`
      : kind === "downgrade"
        ? `Switch to ${target?.name}`
        : `Switch to ${cycle === "annual" ? "yearly" : "monthly"} billing`;

  return (
    <div className="bl-panel bl-scope">
      <div className="bl-panel-body">
        <button type="button" className="bl-back" onClick={onBack} style={{ alignSelf: "flex-start", marginLeft: -10 }}>
          <ArrowLeft size={16} /> Back
        </button>
        <div className="bl-stack" style={{ gap: 8 }}>
          <span className="bl-panel-kicker">
            {planName(cat, from)} <ArrowRight size={12} /> {target?.name}
          </span>
          <PanelTitle>{headline}</PanelTitle>
          <PanelDesc>
            {kind === "upgrade"
              ? <>Applies immediately. <PolicyText parts={[UPGRADE_CHARGE_TBD]} /></>
              : `Takes effect at your next renewal on ${fmtDate(renewal)}. You keep ${planName(cat, from)} until then, so you lose none of the time you have paid for.`}
          </PanelDesc>
        </div>

        <div className="bl-card bl-card--outline" style={{ padding: "4px 16px" }}>
          <div className="bl-line">
            <span>New plan</span>
            <span>
              {target?.name}, {cycle === "annual" ? "yearly" : "monthly"}
            </span>
          </div>
          <div className="bl-line">
            <span>{kind === "upgrade" ? "Then renews at" : "From " + fmtDate(renewal)}</span>
            <span>
              {price ? inr(price.amount) : "—"} / {cycle === "annual" ? "year" : "month"}
            </span>
          </div>
          <div className="bl-line">
            <span>Tax</span>
            <span>GST included</span>
          </div>
        </div>

        {lost.length ? (
          <div className="bl-stack" style={{ gap: 6 }}>
            <span className="bl-h3" style={{ fontSize: 15 }}>What gets smaller</span>
            <div className="bl-diff" role="table" aria-label="Limits that change">
              <span className="bl-diff-head" role="columnheader">Feature</span>
              <span className="bl-diff-head" role="columnheader" style={{ textAlign: "right" }}>Now</span>
              <span aria-hidden />
              <span className="bl-diff-head" role="columnheader" style={{ textAlign: "right" }}>After</span>
              {lost.map((l) => {
                const f = cat.features[l.key]!;
                return (
                  <React.Fragment key={l.key}>
                    <span role="cell">{f.label}</span>
                    <span className="bl-diff-from" role="cell">{fmtValue(l.key, f, l.from)}</span>
                    <ArrowRight size={13} aria-hidden style={{ color: "var(--bl-faint)" }} />
                    <span className="bl-diff-to" role="cell">{fmtValue(l.key, f, l.to)}</span>
                  </React.Fragment>
                );
              })}
            </div>
          </div>
        ) : null}

        {over.length ? (
          <Notice tone="warn" title="Some alerts will pause">
            {over
              .map((o) => `${num(o.used - o.limit)} of your ${num(o.used)} ${cat.features[o.key]?.label.toLowerCase()}`)
              .join(" and ")}{" "}
            will be paused on {fmtDate(renewal)}, newest first. Nothing is deleted; you can re-arm them any time you are within the limit.
          </Notice>
        ) : null}

        {error ? <Notice tone="bad" title="The change did not go through">{error} Your plan was not changed.</Notice> : null}
      </div>
      <div className="bl-panel-foot">
        <button type="button" className="bl-btn bl-btn--primary bl-btn--lg bl-btn--block" onClick={confirm} disabled={phase === "busy"}>
          {phase === "busy" ? <Loader2 size={16} className="bl-spin" /> : null}
          {kind === "upgrade" ? `Upgrade now` : "Schedule the change"}
        </button>
        <div className="bl-row-flex">
          <button type="button" className="bl-btn bl-btn--ghost bl-btn--sm" onClick={onBack}>
            Keep {planName(cat, from)}
          </button>
        </div>
      </div>
    </div>
  );
}

/** Choose the plan to change to, from settings. Free is a cancellation and
 *  routes there, with its own review of what changes. */
export function PlanPickerPanel({
  catalog: cat,
  me,
  onPick,
  onCancelInstead,
  onClose,
}: {
  catalog: PublicCatalog;
  me: BillingMe;
  onPick: (plan: PaidPlanId, cycle: Cycle) => void;
  onCancelInstead: () => void;
  onClose: () => void;
}): React.ReactElement {
  const view = subscriptionView(me);
  const [cycle, setCycle] = React.useState<Cycle>(view.cycle ?? "monthly");
  return (
    <div className="bl-panel bl-scope">
      <div className="bl-panel-body">
        <div className="bl-stack" style={{ gap: 8 }}>
          <span className="bl-panel-kicker">Change plan</span>
          <PanelTitle>Choose a plan</PanelTitle>
          <PanelDesc>
            Upgrades apply now. Downgrades and billing switches apply at your renewal on {fmtDate(view.renewsAt)}.
          </PanelDesc>
        </div>
        <BillingToggle cycle={cycle} onChange={setCycle} savePct={Math.max(0, ...cat.plans.map((p) => p.prices.annual?.saving_pct ?? 0))} />
        <div className="bl-choices">
          {cat.plans.map((p) => {
            const isCurrent = p.id === view.subPlan && cycle === view.cycle;
            const pr = p.prices[cycle];
            return (
              <button
                key={p.id}
                type="button"
                className="bl-choice"
                aria-checked={isCurrent}
                role="radio"
                disabled={isCurrent}
                onClick={() => (p.id === "free" ? onCancelInstead() : onPick(p.id as PaidPlanId, cycle))}
              >
                <span className="bl-choice-radio" aria-hidden />
                <span className="bl-choice-main">
                  <span className="bl-choice-name">
                    {p.name}
                    {isCurrent ? <span className="bl-badge bl-badge--plain">Current</span> : null}
                  </span>
                  <span className="bl-choice-sub">
                    {p.id === "free" ? "Cancel your subscription" : `${num(Number(p.features["ai.credits"] ?? 0))} AI credits a month`}
                  </span>
                </span>
                <span className="bl-choice-price">
                  <strong>{pr ? inr(pr.per_month) : "₹0"}</strong>
                  <span>/mo{pr && cycle === "annual" ? ", billed yearly" : ""}</span>
                </span>
              </button>
            );
          })}
        </div>
      </div>
      <div className="bl-panel-foot">
        <div className="bl-row-flex">
          <button type="button" className="bl-btn bl-btn--ghost bl-btn--sm" onClick={onClose}>
            Close
          </button>
        </div>
      </div>
    </div>
  );
}
