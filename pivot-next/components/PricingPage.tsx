"use client";

/* The pricing / upgrade page for pivot-next.
 *
 * A real route (/pricing) that renders inside the app's own AppShell — the
 * topbar and sidebar stay, the content scrolls in the main pane. NOT an
 * overlay. The layout follows the ChatGPT model: each plan is a tall card that
 * carries its OWN full feature list, one icon per row, under a short group
 * label — no separate comparison table. Every colour, radius and easing comes
 * from the app's tokens (globals.css), so it reads as a room in this product.
 *
 * ONE source of truth: PLANS holds the prices and the per-card feature lists.
 * Change a limit here and the card moves with it; nothing is written twice.
 */

import * as React from "react";
import { useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import {
  Check,
  Sparkles,
  Zap,
  Crown,
  ArrowUpRight,
  Brain,
  Sparkle,
  Bell,
  BellRing,
  TrendingUp,
  LineChart,
  Layers,
  Columns3,
  Clock,
  ShieldOff,
  Infinity as InfinityIcon,
  Filter,
  Database,
  ListChecks,
  Star,
  X,
} from "lucide-react";
import { cn } from "@/lib/utils";

type IconType = React.ComponentType<{ size?: number; strokeWidth?: number; className?: string }>;

// A feature line inside a card: an icon, a label, and the value this plan gets
// (a string shown after the label, or a plain included-tick when there is no
// number to show).
type Feature = { icon: IconType; label: string; value?: string };
type FeatureGroup = { heading: string; items: Feature[] };

type Plan = {
  id: "free" | "pro" | "proplus";
  name: string;
  icon: IconType;
  headline: string; // the big in-card line, e.g. "Try Pivot"
  tagline: string;
  monthly: number; // ₹ per month, billed monthly
  annual: number; // ₹ per month, billed annually
  cta: string;
  featured?: boolean;
  groups: FeatureGroup[];
};

// ── the three plans, each with its own feature list ──────────
// Transcribed from the source table. A value of undefined = a plain tick
// ("included"); a string = the limit shown after the label.
const PLANS: Plan[] = [
  {
    id: "free",
    name: "Free",
    icon: Sparkles,
    headline: "Try Pivot",
    tagline: "Everything you need to chart, screen and learn the markets.",
    monthly: 0,
    annual: 0,
    cta: "Your current plan",
    groups: [
      {
        heading: "Start with the basics",
        items: [
          { icon: Brain, label: "Monthly AI credits", value: "150" },
          { icon: Sparkle, label: "AI chart summaries", value: "10" },
          { icon: LineChart, label: "Indicators", value: "5" },
          { icon: Bell, label: "Price & technical alerts", value: "20 each" },
          { icon: ListChecks, label: "Multi-condition alerts" },
          { icon: Clock, label: "Alert expiry", value: "2 months" },
        ],
      },
      {
        heading: "Charting & screening",
        items: [
          { icon: Columns3, label: "Charts per tab", value: "4" },
          { icon: Layers, label: "Parallel charts", value: "10" },
          { icon: Database, label: "Historical bars", value: "10K" },
          { icon: Filter, label: "Screen alerts", value: "3" },
          { icon: Star, label: "Saved screens", value: "5" },
          { icon: InfinityIcon, label: "Unlimited watchlists" },
        ],
      },
    ],
  },
  {
    id: "pro",
    name: "Pro",
    icon: Zap,
    headline: "Your edge, upgraded",
    tagline: "Deeper alerts, unlimited AI summaries and the full indicator set.",
    monthly: 499,
    annual: 449,
    cta: "Upgrade to Pro",
    featured: true,
    groups: [
      {
        heading: "Everything in Free, plus",
        items: [
          { icon: Brain, label: "Monthly AI credits", value: "200" },
          { icon: Sparkle, label: "AI chart summaries", value: "Unlimited" },
          { icon: LineChart, label: "Indicators", value: "10" },
          { icon: BellRing, label: "Technical alerts", value: "100" },
          { icon: Bell, label: "Price alerts", value: "400" },
          { icon: Clock, label: "Alert expiry", value: "6 months" },
        ],
      },
      {
        heading: "More power",
        items: [
          { icon: Columns3, label: "Charts per tab", value: "8" },
          { icon: Layers, label: "Parallel charts", value: "20" },
          { icon: Filter, label: "Screen alerts", value: "50" },
          { icon: Star, label: "Saved screens", value: "50" },
          { icon: Check, label: "Watchlist alerts" },
          { icon: ShieldOff, label: "Ad-free experience" },
        ],
      },
    ],
  },
  {
    id: "proplus",
    name: "Pro+",
    icon: Crown,
    headline: "Maximum limits",
    tagline: "The highest limits across alerts, screening and parallel charts.",
    monthly: 999,
    annual: 899,
    cta: "Upgrade to Pro+",
    groups: [
      {
        heading: "Everything in Pro, plus",
        items: [
          { icon: Brain, label: "Monthly AI credits", value: "500" },
          { icon: LineChart, label: "All indicators", value: "Unlocked" },
          { icon: TrendingUp, label: "Fundamental alerts", value: "500" },
          { icon: BellRing, label: "Technical alerts", value: "1,000" },
          { icon: Bell, label: "Price alerts", value: "1,000" },
          { icon: Clock, label: "Alerts never expire" },
        ],
      },
      {
        heading: "The ceiling, raised",
        items: [
          { icon: Columns3, label: "Charts per tab", value: "8" },
          { icon: Layers, label: "Parallel charts", value: "50" },
          { icon: Filter, label: "Screen alerts", value: "75" },
          { icon: Star, label: "Saved screens", value: "50" },
          { icon: Check, label: "Watchlist alerts" },
          { icon: ShieldOff, label: "Ad-free experience" },
        ],
      },
    ],
  },
];

const inr = (n: number): string => "₹" + n.toLocaleString("en-IN");
const savePct = (p: Plan): number =>
  p.monthly > 0 ? Math.round((1 - p.annual / p.monthly) * 100) : 0;

function PriceBlock({ plan, annual }: { plan: Plan; annual: boolean }): React.ReactElement {
  if (plan.monthly === 0) {
    return (
      <div key="free" className="pricing-price">
        <div className="pricing-amount">
          <span className="pricing-cur">₹</span>
          <span className="pricing-num">0</span>
          <span className="pricing-per">/ month</span>
        </div>
        <div className="pricing-period">Free forever</div>
      </div>
    );
  }
  const perMonth = annual ? plan.annual : plan.monthly;
  const sub = annual
    ? `${inr(plan.annual * 12)} billed yearly`
    : `Billed monthly · ${inr(plan.annual)}/mo annually`;
  return (
    // key on the period so React remounts → the CSS fade-in restarts on switch
    <div key={annual ? "annual" : "monthly"} className="pricing-price pricing-fade">
      <div className="pricing-amount">
        <span className="pricing-cur">₹</span>
        <span className="pricing-num">{perMonth.toLocaleString("en-IN")}</span>
        <span className="pricing-per">/ month</span>
      </div>
      <div className="pricing-period">{sub}</div>
    </div>
  );
}

export function PricingPage(): React.ReactElement {
  const router = useRouter();
  const [annual, setAnnual] = useState(false);

  // Close the overlay → go back to wherever the user opened it from, falling
  // back to home if /pricing was opened directly.
  const close = React.useCallback((): void => {
    if (window.history.length > 1) router.back();
    else router.push("/");
  }, [router]);

  // The checkout gateway is a business decision; this is the single hook for
  // wiring it later. The Free plan simply returns to the app.
  const onUpgrade = React.useCallback(
    (planId: Plan["id"]): void => {
      if (planId === "free") close();
      // else: open the payment gateway here.
    },
    [close],
  );

  // Esc closes, like any full-screen modal.
  React.useEffect(() => {
    const onKey = (e: KeyboardEvent): void => {
      if (e.key === "Escape") close();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [close]);

  const cards = useMemo(
    () =>
      PLANS.map((plan) => {
        const Icon = plan.icon;
        const pct = savePct(plan);
        return (
          <article
            key={plan.id}
            className={cn("pricing-card", plan.featured && "pricing-card--featured")}
          >
            {/* head: name + optional "recommended" flag */}
            <div className="pricing-card-top">
              <span className="pricing-mark" data-plan={plan.id}>
                <Icon size={16} strokeWidth={2} />
              </span>
              <span className="pricing-name">{plan.name}</span>
              {plan.featured ? <span className="pricing-reco">Recommended</span> : null}
            </div>

            <h2 className="pricing-headline">{plan.headline}</h2>
            <p className="pricing-tag">{plan.tagline}</p>

            <PriceBlock plan={plan} annual={annual} />
            {annual && pct > 0 ? (
              <span className="pricing-save">Save {pct}% billed annually</span>
            ) : (
              <span className="pricing-save pricing-save--ghost" aria-hidden="true" />
            )}

            <button
              type="button"
              onClick={() => onUpgrade(plan.id)}
              className={cn(
                "pricing-cta",
                plan.id === "free"
                  ? "pricing-cta--ghost"
                  : plan.featured
                    ? "pricing-cta--primary"
                    : "pricing-cta--dark",
              )}
              disabled={plan.id === "free"}
            >
              {plan.cta}
              {plan.id !== "free" ? <ArrowUpRight size={15} strokeWidth={2.2} /> : null}
            </button>

            {/* the feature list, grouped, one icon per row */}
            <div className="pricing-features">
              {plan.groups.map((grp) => (
                <React.Fragment key={grp.heading}>
                  <div className="pricing-group-heading">{grp.heading}</div>
                  <ul className="pricing-feature-list">
                    {grp.items.map((f) => {
                      const FI = f.icon;
                      return (
                        <li key={f.label}>
                          <span className="pricing-feature-icon">
                            <FI size={17} strokeWidth={1.9} />
                          </span>
                          <span className="pricing-feature-label">{f.label}</span>
                          {f.value ? (
                            <span className="pricing-feature-value">{f.value}</span>
                          ) : null}
                        </li>
                      );
                    })}
                  </ul>
                </React.Fragment>
              ))}
            </div>
          </article>
        );
      }),
    [annual, onUpgrade],
  );

  return (
    <div className="pricing-overlay" role="dialog" aria-modal="true" aria-label="Plans and pricing">
      <button type="button" className="pricing-close" aria-label="Close" onClick={close}>
        <X size={20} strokeWidth={2} />
      </button>
      <div className="pricing-overlay-scroll">
        <div className="pricing-page">
          {/* hero */}
          <header className="pricing-hero">
        <span className="pricing-eyebrow">Plans &amp; pricing</span>
        <h1 className="pricing-title">Choose the plan that fits your workflow</h1>
        <p className="pricing-sub">
          Start free and upgrade when you need more alerts, AI and room on the chart. This is plan
          information, not financial advice.
        </p>
        <div className="pricing-toggle" role="group" aria-label="Billing period">
          <button
            type="button"
            className={cn("pricing-seg", !annual && "pricing-seg--active")}
            aria-pressed={!annual}
            onClick={() => setAnnual(false)}
          >
            Monthly
          </button>
          <button
            type="button"
            className={cn("pricing-seg", annual && "pricing-seg--active")}
            aria-pressed={annual}
            onClick={() => setAnnual(true)}
          >
            Annual
            <span className="pricing-seg-badge">Save 10%</span>
          </button>
        </div>
      </header>

          {/* the three cards, each carrying its own feature list */}
          <div className="pricing-grid">{cards}</div>

          <p className="pricing-foot">
            Prices in INR, inclusive of applicable taxes. Cancel or change your plan anytime. Pivot
            builds and simulates — it does not place live broker orders.
          </p>
        </div>
      </div>
    </div>
  );
}
