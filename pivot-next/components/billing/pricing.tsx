"use client";

/**
 * Pricing building blocks: plan cards, the comparison table and the FAQ.
 * Every number is read from the catalog; every word from presentation.ts.
 */

import * as React from "react";
import { Check, Minus, Plus } from "lucide-react";
import { cn } from "@/lib/utils";
import { fmtValue, inr } from "@/lib/billing/format";
import { annualSaving, subscriptionView } from "@/lib/billing/entitlements";
import { rankOf } from "@/lib/billing/catalog";
import {
  FEATURE_GROUPS,
  FEATURE_NOTES,
  PLAN_COPY,
  RECOMMENDED,
  featureLine,
  isShown,
  isUnbuilt,
} from "@/lib/billing/presentation";
import { FAQS } from "@/lib/billing/policy";
import type { BillingMe, CatalogPlan, Cycle, PlanId, PublicCatalog } from "@/lib/billing/types";
import { PolicyText, RecommendedLabel, SoonTag } from "./primitives";

// ── price ───────────────────────────────────────────────────────────────

export function PlanPrice({
  plan,
  cycle,
  size = "lg",
}: {
  plan: CatalogPlan;
  cycle: Cycle;
  size?: "lg" | "sm";
}): React.ReactElement {
  const p = plan.prices[cycle];
  return (
    <span key={cycle} className={cn("bl-price", size === "sm" && "bl-price--sm", "bl-fade")}>
      {p ? inr(p.per_month) : "₹0"}
      <span className="bl-price-per">/mo</span>
    </span>
  );
}

export function priceNote(cat: PublicCatalog, plan: CatalogPlan, cycle: Cycle): { text: string; save: boolean } {
  const p = plan.prices[cycle];
  if (!p) return { text: "No card required", save: false };
  if (cycle === "annual") {
    const saved = annualSaving(cat, plan.id);
    return {
      text: `${inr(p.amount)} billed yearly${saved ? `, save ${inr(saved)}` : ""}`,
      save: saved > 0,
    };
  }
  return { text: "Billed monthly", save: false };
}

// ── which button a card shows, for this person ──────────────────────────

export type CardAction =
  | { kind: "signup" }
  | { kind: "current" }
  | { kind: "checkout"; label: string }
  | { kind: "change"; label: string }
  | { kind: "cancel"; label: string };

export function cardAction(cat: PublicCatalog, me: BillingMe | null, plan: PlanId, cycle: Cycle): CardAction {
  const v = subscriptionView(me);
  const name = cat.plans.find((p) => p.id === plan)?.name ?? plan;
  if (v.state === "anonymous" || !me) {
    return plan === "free" ? { kind: "signup" } : { kind: "checkout", label: `Get ${name}` };
  }
  const paying = ["active", "trialing", "past_due", "canceling"].includes(v.state);
  const current = paying ? v.subPlan : me.plan;
  if (plan === current && (!paying || v.cycle === cycle || plan === "free")) return { kind: "current" };
  if (plan === "free") return paying ? { kind: "cancel", label: "Switch to Free" } : { kind: "current" };
  if (paying && v.state !== "canceling") {
    if (plan === current) return { kind: "change", label: `Switch to ${cycle === "annual" ? "yearly" : "monthly"}` };
    return { kind: "change", label: rankOf(cat, plan) > rankOf(cat, current ?? "free") ? `Upgrade to ${name}` : `Switch to ${name}` };
  }
  const trial = cat.trial_days && v.state === "free" ? `Start ${cat.trial_days}-day free trial` : null;
  return { kind: "checkout", label: trial ?? `Upgrade to ${name}` };
}

// ── plan cards ──────────────────────────────────────────────────────────

export function PlanCards({
  catalog,
  me,
  cycle,
  onAction,
  onSeeAll,
}: {
  catalog: PublicCatalog;
  me: BillingMe | null;
  cycle: Cycle;
  onAction: (plan: PlanId, action: CardAction) => void;
  onSeeAll?: () => void;
}): React.ReactElement {
  return (
    <div className="bl-plans" style={{ ["--bl-cols" as string]: catalog.plans.length }}>
      {catalog.plans.map((plan, i) => {
        const copy = PLAN_COPY[plan.id as Exclude<PlanId, "anonymous">];
        const note = priceNote(catalog, plan, cycle);
        const action = cardAction(catalog, me, plan.id, cycle);
        const featured = plan.id === RECOMMENDED;
        const isCurrent = action.kind === "current";
        const prev = catalog.plans[i - 1];
        return (
          <article
            key={plan.id}
            className={cn("bl-plan", featured && "bl-plan--featured", isCurrent && !featured && "bl-plan--current")}
            aria-labelledby={`plan-${plan.id}`}
          >
            {featured ? <RecommendedLabel>{isCurrent ? "Your plan" : "Recommended"}</RecommendedLabel> : null}
            <div className="bl-plan-head">
              <h3 id={`plan-${plan.id}`} className="bl-plan-name">
                {plan.name}
              </h3>
              <p className="bl-plan-tag">{copy?.tagline}</p>
              <PlanPrice plan={plan} cycle={cycle} />
              <span className={cn("bl-price-note", note.save && "bl-price-note--save")}>{note.text}</span>
              <CardButton action={action} onClick={() => onAction(plan.id, action)} />
            </div>
            {prev ? <div className="bl-plan-list-title">Everything in {prev.name}, plus</div> : null}
            <ul className="bl-plan-list">
              {(copy?.highlights ?? []).map((key) => {
                const f = catalog.features[key];
                if (!f || !isShown(catalog, key)) return null;
                const v = plan.features[key];
                if (v === undefined || v === false || v === 0) return null;
                return (
                  <li key={key}>
                    <Check size={16} strokeWidth={2} aria-hidden />
                    <span>
                      {featureLine(key, f, v)}
                      {isUnbuilt(key, f) ? <SoonTag /> : null}
                    </span>
                  </li>
                );
              })}
            </ul>
            {onSeeAll ? (
              <button type="button" className="bl-link" style={{ margin: "auto auto 0", paddingTop: 24 }} onClick={onSeeAll}>
                Compare all features
              </button>
            ) : null}
          </article>
        );
      })}
    </div>
  );
}

function CardButton({ action, onClick }: { action: CardAction; onClick: () => void }): React.ReactElement {
  if (action.kind === "current") {
    return (
      <button type="button" className="bl-btn bl-btn--lg bl-btn--current bl-plan-cta" disabled aria-disabled="true">
        Current plan
      </button>
    );
  }
  const label = action.kind === "signup" ? "Get started free" : action.label;
  const primary = action.kind === "checkout" || (action.kind === "change" && label.startsWith("Upgrade"));
  return (
    <button
      type="button"
      className={cn("bl-btn bl-btn--lg bl-plan-cta", primary ? "bl-btn--primary" : "bl-btn--secondary")}
      onClick={onClick}
    >
      {label}
    </button>
  );
}

// ── comparison table ────────────────────────────────────────────────────

export function ComparisonTable({
  catalog,
  cycle,
  currentPlan,
}: {
  catalog: PublicCatalog;
  cycle: Cycle;
  currentPlan?: PlanId | null;
}): React.ReactElement {
  return (
    <div className="bl-compare-wrap bl-scope">
      <table className="bl-compare">
        <caption className="bl-sr">Feature comparison across plans</caption>
        <thead>
          <tr>
            <th scope="col">
              <span className="bl-sr">Feature</span>
            </th>
            {catalog.plans.map((p) => (
              <th key={p.id} scope="col" className={cn(p.id === currentPlan && "bl-compare-col--current")}>
                <span className="bl-compare-plan">
                  {p.name}
                  <PlanPrice plan={p} cycle={cycle} size="sm" />
                  {p.id === currentPlan ? <span className="bl-fine">Your plan</span> : null}
                </span>
              </th>
            ))}
          </tr>
        </thead>
        {FEATURE_GROUPS.map((g) => {
          const keys = g.keys.filter((k) => catalog.features[k] && isShown(catalog, k));
          if (!keys.length) return null;
          return (
            <tbody key={g.title}>
              <tr className="bl-compare-group">
                <th scope="colgroup" colSpan={catalog.plans.length + 1}>
                  {g.title}
                </th>
              </tr>
              {keys.map((key) => {
                const f = catalog.features[key]!;
                return (
                  <tr key={key}>
                    <th scope="row">
                      {f.label}
                      {isUnbuilt(key, f) ? <SoonTag /> : null}
                      {FEATURE_NOTES[key] ? <span className="bl-compare-note">{FEATURE_NOTES[key]}</span> : null}
                    </th>
                    {catalog.plans.map((p) => (
                      <td key={p.id} className={cn("bl-num", p.id === currentPlan && "bl-compare-col--current")}>
                        <Cell kind={f.kind} text={fmtValue(key, f, p.features[key] ?? null)} on={p.features[key]} />
                      </td>
                    ))}
                  </tr>
                );
              })}
            </tbody>
          );
        })}
      </table>
    </div>
  );
}

function Cell({ kind, text, on }: { kind: string; text: string; on: unknown }): React.ReactElement {
  if (kind === "flag") {
    return on ? (
      <Check size={17} strokeWidth={2} aria-label="Included" />
    ) : (
      <Minus size={16} className="bl-no" aria-label="Not included" />
    );
  }
  if (on === 0) return <Minus size={16} className="bl-no" aria-label="Not included" />;
  return <>{text}</>;
}

// ── FAQ ─────────────────────────────────────────────────────────────────

export function PricingFAQ(): React.ReactElement {
  return (
    <div className="bl-faq">
      {FAQS.map((f) => (
        <details key={f.q}>
          <summary>
            {f.q}
            <Plus size={18} aria-hidden />
          </summary>
          <div className="bl-faq-a">
            <PolicyText parts={f.a} />
          </div>
        </details>
      ))}
    </div>
  );
}
