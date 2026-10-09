"use client";

/* The pricing / upgrade page for pivot-next.
 *
 * A full-screen overlay (/pricing) on its own backdrop, OVER the app, with a
 * close button and Esc to dismiss. The layout follows Typeform's pricing page:
 * a large serif headline, the billing toggle above the cards, three centred
 * plan cards (name, line, serif price, saving, CTA, then a hairline-ruled
 * checklist) and a full "Compare all plans" matrix with a sticky price
 * header. Every colour, radius and easing comes from the app's
 * tokens (globals.css), so it reads as a room in this product.
 *
 * ONE source of truth: MATRIX holds every limit. The cards reference its rows
 * by id, so a limit changed there moves the card and the comparison together.
 */

import * as React from "react";
import { useState } from "react";
import { useRouter } from "next/navigation";
import { Check, Minus, X } from "lucide-react";
import { cn } from "@/lib/utils";

type PlanId = "free" | "pro" | "proplus";
type Cell = number | string | boolean; // true = included, false = not included

type Row = {
  id: string;
  label: string;
  values: [Cell, Cell, Cell]; // free, pro, pro+
  // How a card states this row, e.g. "150 monthly AI credits". Rows a card
  // never quotes can leave it out.
  phrase?: (v: Cell) => string;
};
type Group = { group: string; rows: Row[] };

const n = (v: Cell): string => (typeof v === "number" ? v.toLocaleString("en-IN") : String(v));

// ── the feature matrix ───────────────────────────────────────
// Transcribed from the source table, in the order the product groups them.
const MATRIX: Group[] = [
  {
    group: "AI",
    rows: [
      { id: "credits", label: "Monthly AI credits", values: [150, 200, 500], phrase: (v) => `${n(v)} AI credits / month` },
      { id: "summaries", label: "AI chart summaries", values: [10, "Unlimited", "Unlimited"], phrase: (v) => `${n(v)} AI chart summaries` },
    ],
  },
  {
    group: "Alerts",
    rows: [
      { id: "fundamental", label: "Fundamental alerts", values: [false, false, 500], phrase: (v) => `${n(v)} fundamental alerts` },
      { id: "technical", label: "Technical alerts", values: [20, 100, 1000], phrase: (v) => `${n(v)} technical alerts` },
      { id: "price", label: "Price alerts", values: [20, 400, 1000], phrase: (v) => `${n(v)} price alerts` },
      { id: "watchlistAlerts", label: "Watchlist alerts", values: [false, true, true], phrase: () => "Watchlist alerts" },
      { id: "multi", label: "Multi-condition alerts", values: [true, true, true], phrase: () => "Multi-condition alerts" },
      { id: "expiry", label: "Alert expiry", values: ["2 months", "6 months", "Never"], phrase: (v) => (v === "Never" ? "Alerts never expire" : `Alerts last ${n(v)}`) },
      { id: "screenAlerts", label: "Screen alerts", values: [3, 50, 75], phrase: (v) => `${n(v)} screen alerts` },
    ],
  },
  {
    group: "Charting",
    rows: [
      { id: "indicators", label: "Indicators", values: [5, 10, "All"], phrase: (v) => (v === "All" ? "Every indicator unlocked" : `${n(v)} indicators`) },
      { id: "perTab", label: "Charts per tab", values: [4, 8, 8], phrase: (v) => `${n(v)} charts per tab` },
      { id: "parallel", label: "Parallel charts", values: [10, 20, 50], phrase: (v) => `${n(v)} parallel charts` },
      { id: "timeframes", label: "Custom time frames", values: [false, true, true], phrase: () => "Custom time frames" },
      { id: "adFree", label: "Ad-free experience", values: [false, true, true], phrase: () => "Ad-free experience" },
    ],
  },
  {
    group: "Watchlists & screening",
    rows: [
      { id: "watchlists", label: "Watchlists", values: ["Unlimited", "Unlimited", "Unlimited"], phrase: () => "Unlimited watchlists" },
      { id: "screens", label: "Saved screens", values: [5, 50, 50], phrase: (v) => `${n(v)} saved screens` },
    ],
  },
];

const ROWS: Record<string, Row> = Object.fromEntries(
  MATRIX.flatMap((g) => g.rows).map((r) => [r.id, r]),
);

type Plan = {
  id: PlanId;
  col: 0 | 1 | 2; // this plan's column in MATRIX
  name: string;
  tagline: string;
  monthly: number; // ₹ per month, billed monthly
  annual: number; // ₹ per month, billed annually
  cta: string;
  featured?: boolean;
  limits: string[]; // MATRIX row ids — the headline numbers
  features: string[]; // MATRIX row ids, or a literal line ("Everything in Free")
};

const PLANS: Plan[] = [
  {
    id: "free",
    col: 0,
    name: "Free",
    tagline: "Everything you need to chart, screen and learn the markets.",
    monthly: 0,
    annual: 0,
    cta: "Your current plan",
    limits: ["credits", "summaries"],
    features: ["indicators", "technical", "price", "multi", "watchlists", "screens"],
  },
  {
    id: "pro",
    col: 1,
    name: "Pro",
    tagline: "Deeper alerts, unlimited AI summaries and more room on the chart.",
    monthly: 499,
    annual: 449,
    cta: "Get Pro",
    featured: true,
    limits: ["credits", "summaries"],
    features: ["Everything in Free", "indicators", "price", "watchlistAlerts", "timeframes", "adFree"],
  },
  {
    id: "proplus",
    col: 2,
    name: "Pro+",
    tagline: "The highest limits across alerts, screening and parallel charts.",
    monthly: 999,
    annual: 899,
    cta: "Get Pro+",
    limits: ["credits", "summaries"],
    features: ["Everything in Pro", "indicators", "fundamental", "technical", "parallel", "expiry"],
  },
];

const inr = (v: number): string => "₹" + v.toLocaleString("en-IN");

function BillingToggle({
  annual,
  onChange,
}: {
  annual: boolean;
  onChange: (annual: boolean) => void;
}): React.ReactElement {
  return (
    <div className="pricing-toggle" role="group" aria-label="Billing period">
      <button
        type="button"
        className={cn("pricing-seg", !annual && "pricing-seg--active")}
        aria-pressed={!annual}
        onClick={() => onChange(false)}
      >
        Monthly
      </button>
      <button
        type="button"
        className={cn("pricing-seg", annual && "pricing-seg--active")}
        aria-pressed={annual}
        onClick={() => onChange(true)}
      >
        Yearly (Save 10%)
      </button>
    </div>
  );
}

// The serif price figure: "₹449/mo". Keyed on the period so the fade restarts.
function Price({ plan, annual, size }: { plan: Plan; annual: boolean; size: "lg" | "sm" }): React.ReactElement {
  const perMonth = annual ? plan.annual : plan.monthly;
  return (
    <span key={annual ? "y" : "m"} className={cn("pricing-price", `pricing-price--${size}`, "pricing-fade")}>
      {inr(perMonth)}
      {size === "lg" ? <span className="pricing-per">/mo</span> : null}
    </span>
  );
}

function priceNote(plan: Plan, annual: boolean): { text: string; save: boolean } {
  if (plan.monthly === 0) return { text: "Free forever", save: false };
  if (annual) return { text: `Save ${inr((plan.monthly - plan.annual) * 12)} /yr`, save: true };
  return { text: "Billed monthly", save: false };
}

function CellValue({ v }: { v: Cell }): React.ReactElement {
  if (v === true) return <Check size={17} strokeWidth={2} aria-label="Included" />;
  if (v === false) return <Minus size={16} strokeWidth={1.8} className="pricing-cell-no" aria-label="Not included" />;
  return <>{n(v)}</>;
}

export function PricingPage(): React.ReactElement {
  const router = useRouter();
  const [annual, setAnnual] = useState(true);
  const compareRef = React.useRef<HTMLElement>(null);

  // Close the overlay → go back to wherever the user opened it from, falling
  // back to home if /pricing was opened directly.
  const close = React.useCallback((): void => {
    if (window.history.length > 1) router.back();
    else router.push("/");
  }, [router]);

  // The checkout gateway is a business decision; this is the single hook for
  // wiring it later. The Free plan simply returns to the app.
  const onUpgrade = (planId: PlanId): void => {
    if (planId === "free") close();
    // else: open the payment gateway here.
  };

  // Esc closes, like any full-screen modal.
  React.useEffect(() => {
    const onKey = (e: KeyboardEvent): void => {
      if (e.key === "Escape") close();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [close]);

  const seeAll = (): void => compareRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });

  const line = (plan: Plan, key: string): string => {
    const row = ROWS[key];
    return row?.phrase ? row.phrase(row.values[plan.col]) : key;
  };

  return (
    <div className="pricing-overlay" role="dialog" aria-modal="true" aria-label="Plans and pricing">
      <button type="button" className="pricing-close" aria-label="Close" onClick={close}>
        <X size={20} strokeWidth={2} />
      </button>
      <div className="pricing-overlay-scroll">
        <div className="pricing-page">
          <h1 className="pricing-title">Choose the plan that fits your workflow</h1>

          {/* the bar above the cards: billing on the left */}
          <div className="pricing-bar">
            <BillingToggle annual={annual} onChange={setAnnual} />
          </div>

          {/* the three plan cards */}
          <div className="pricing-grid">
            {PLANS.map((plan) => {
              const note = priceNote(plan, annual);
              return (
                <article
                  key={plan.id}
                  className={cn("pricing-card", plan.featured && "pricing-card--featured")}
                >
                  {plan.featured ? <span className="pricing-flag">Recommended</span> : null}
                  <div className="pricing-card-head">
                    <h2 className="pricing-name">{plan.name}</h2>
                    <p className="pricing-tag">{plan.tagline}</p>
                    <Price plan={plan} annual={annual} size="lg" />
                    <span className={cn("pricing-note", note.save && "pricing-note--save")}>{note.text}</span>
                    <button
                      type="button"
                      onClick={() => onUpgrade(plan.id)}
                      className={cn("pricing-cta", plan.id === "free" && "pricing-cta--current")}
                      disabled={plan.id === "free"}
                    >
                      {plan.cta}
                    </button>
                  </div>

                  <ul className="pricing-list">
                    {plan.limits.map((k) => (
                      <li key={k}>
                        <Check size={16} strokeWidth={2} />
                        {line(plan, k)}
                      </li>
                    ))}
                  </ul>
                  <div className="pricing-list-heading">Features you&apos;ll love:</div>
                  <ul className="pricing-list">
                    {plan.features.map((k) => (
                      <li key={k}>
                        <Check size={16} strokeWidth={2} />
                        {line(plan, k)}
                      </li>
                    ))}
                  </ul>

                  <button type="button" className="pricing-seeall" onClick={seeAll}>
                    See all features
                  </button>
                </article>
              );
            })}
          </div>

          <p className="pricing-aside">
            Prices in INR, inclusive of applicable taxes. Cancel or change your plan anytime.
          </p>

          {/* ── compare all plans ─────────────────────────────── */}
          <section ref={compareRef} className="pricing-compare" aria-labelledby="pricing-compare-title">
            <h2 id="pricing-compare-title" className="pricing-h2">Compare all plans</h2>

            <div className="pricing-matrix">
              <div className="pricing-matrix-head">
                <div className="pricing-matrix-toggle">
                  <BillingToggle annual={annual} onChange={setAnnual} />
                </div>
                {PLANS.map((plan) => (
                  <div key={plan.id} className="pricing-matrix-plan">
                    <span className="pricing-matrix-name">{plan.name}</span>
                    <Price plan={plan} annual={annual} size="sm" />
                    <span className="pricing-matrix-per">
                      {plan.monthly === 0 ? "free forever" : "per month"}
                    </span>
                    <button
                      type="button"
                      onClick={() => onUpgrade(plan.id)}
                      className="pricing-cta-sm"
                      disabled={plan.id === "free"}
                    >
                      {plan.id === "free" ? "Current plan" : plan.cta}
                    </button>
                  </div>
                ))}
              </div>

              {MATRIX.map((g) => (
                <div key={g.group} className="pricing-matrix-group">
                  <h3 className="pricing-matrix-group-title">{g.group}</h3>
                  {g.rows.map((r) => (
                    <div key={r.id} className="pricing-matrix-row">
                      <div className="pricing-matrix-label">{r.label}</div>
                      {r.values.map((v, i) => (
                        <div key={i} className="pricing-matrix-cell">
                          <CellValue v={v} />
                        </div>
                      ))}
                    </div>
                  ))}
                </div>
              ))}
            </div>
          </section>

          <p className="pricing-foot">
            This is plan information, not financial advice. Pivot builds and simulates strategies; it
            does not place live broker orders.
          </p>
        </div>
      </div>
    </div>
  );
}
