"use client";

/**
 * /paywall-gallery — every billing screen and state, rendered by the real
 * components against fixture data (lib/billing/fixtures.ts). Nothing here
 * talks to the network or to Razorpay: each section runs inside its own demo
 * BillingProvider, and actions that would charge or change a plan resolve
 * locally.
 *
 * It exists so the paywall can be reviewed — in light and dark, at desktop
 * and phone width — without a payment provider, a test account per state, or
 * a dataserver. Open it with `pnpm dev` and visit /paywall-gallery.
 */

import * as React from "react";
import { Moon, Sun } from "lucide-react";
import { SNAPSHOT } from "@/lib/billing/catalog";
import { fixtureInvoices, fixtureMe, FIXTURE_LABELS, type Fixture } from "@/lib/billing/fixtures";
import { meter, quotaView, type PaywallTrigger, type SubState } from "@/lib/billing/entitlements";
import type { BillingMe, PublicCatalog } from "@/lib/billing/types";
import { BillingProvider, useBilling } from "@/components/billing/BillingProvider";
import {
  BillingToggle,
  CreditsPill,
  EmptyState,
  ErrorState,
  LimitMeter,
  LockBadge,
  Notice,
  SkeletonRows,
  SoonTag,
  StatusBadge,
  TrustRow,
  UsageMeter,
} from "@/components/billing/primitives";
import { ComparisonTable, PlanCards, PricingFAQ } from "@/components/billing/pricing";
import { PaywallPanel } from "@/components/billing/PaywallDialog";
import { PlanChangePanel } from "@/components/billing/PlanChange";
import { CancelPanel, type CancelStep } from "@/components/billing/CancelFlow";
import { CheckoutView, type CheckoutPhase } from "@/components/billing/CheckoutView";
import { BillingSettings } from "@/components/billing/BillingSettings";
import {
  CancelingBanner,
  CardExpiryBanner,
  ExpiredBanner,
  IncompleteBanner,
  PastDueBanner,
  RenewalReminder,
  TrialBanner,
  UpgradeBanner,
} from "@/components/billing/Banners";
import { FullPagePaywall, LockedItem, PremiumPreview } from "@/components/billing/gates";
import { Receipt } from "lucide-react";

const DAY = 86400;
const now = Math.floor(Date.now() / 1000);
const cat: PublicCatalog = { ...SNAPSHOT, checkout: true };
const me = (f: Fixture): BillingMe => fixtureMe(f, cat, now);

const SECTIONS = [
  ["tokens", "Foundations"],
  ["pricing", "Pricing page"],
  ["paywalls", "Contextual paywalls"],
  ["preview", "Preview & full page"],
  ["checkout", "Checkout"],
  ["change", "Plan changes"],
  ["cancel", "Cancellation"],
  ["settings", "Plan & billing"],
  ["banners", "Banners & states"],
  ["mobile", "Mobile"],
] as const;

function Demo({ fixture, children, catalog = cat }: { fixture: Fixture; children: React.ReactNode; catalog?: PublicCatalog }): React.ReactElement {
  const m = React.useMemo(() => me(fixture), [fixture]);
  return (
    <BillingProvider demo initial={{ catalog, me: m }} mountDialog={false}>
      {children}
    </BillingProvider>
  );
}

function Item({ label, note, children, stage = true }: { label: string; note?: string; children: React.ReactNode; stage?: boolean }): React.ReactElement {
  return (
    <div className="bl-gal-item">
      <span className="bl-gal-label">{label}</span>
      {note ? <span className="bl-fine" style={{ marginTop: -6 }}>{note}</span> : null}
      {stage ? <div className="bl-gal-stage">{children}</div> : children}
    </div>
  );
}

/** A box that fixed-position pages (checkout, settings) are pinned inside. */
function Frame({ children, height = 760 }: { children: React.ReactNode; height?: number }): React.ReactElement {
  return (
    <div style={{ position: "relative", height, overflow: "hidden", transform: "translateZ(0)", borderRadius: 16, boxShadow: "inset 0 0 0 1px var(--bl-rule)" }}>
      {children}
    </div>
  );
}

function Section({ id, title, intro, children }: { id: string; title: string; intro: string; children: React.ReactNode }): React.ReactElement {
  return (
    <section id={id} className="bl-gal-sec">
      <header>
        <h2 className="bl-h2">{title}</h2>
        <p className="bl-p" style={{ maxWidth: 760 }}>{intro}</p>
      </header>
      {children}
    </section>
  );
}

const panelNoop = { onClose: () => {}, onCheckout: () => {}, onReclaim: () => {}, onSignIn: () => {}, onComparePlans: () => {}, closable: false };

function PanelCard({ fixture, trigger }: { fixture: Fixture; trigger: PaywallTrigger }): React.ReactElement {
  return (
    <div className="bl-panel-inline" style={{ maxWidth: 520, margin: "0 auto" }}>
      <PaywallPanel trigger={trigger} catalog={cat} me={me(fixture)} demo {...panelNoop} />
    </div>
  );
}

const TRIGGERS: { label: string; note: string; fixture: Fixture; t: PaywallTrigger }[] = [
  {
    label: "Usage limit: AI credits",
    note: "quota_exhausted. Meter, reset date, and the free way forward.",
    fixture: "free_out",
    t: { kind: "quota_exhausted", feature: "ai.credits", plan: "free", limit: 15, used: 15, resetsAt: now + 12 * DAY, upgradeTo: "pro", message: "You have used all 15 AI credits in your Free plan for this period." },
  },
  {
    label: "Plan limit: price alerts",
    note: "plan_limit on a live-object cap. Freeing a slot is offered beside upgrading.",
    fixture: "free_out",
    t: { kind: "plan_limit", feature: "alerts.price", plan: "free", limit: 20, used: 20, upgradeTo: "pro", message: "Your Free plan allows 20 price alerts." },
  },
  {
    label: "Plan limit: charts per tab",
    note: "Raised from the grid-layout menu.",
    fixture: "free",
    t: { kind: "plan_limit", feature: "chart.panes", plan: "free", limit: 4, used: 4, upgradeTo: "pro", message: "Your Free plan allows 4 charts per tab." },
  },
  {
    label: "Feature locked",
    note: "feature_locked. Shown with custom timeframes, which is still an unbuilt (pending) key in the catalog.",
    fixture: "free",
    t: { kind: "feature_locked", feature: "chart.custom_timeframes", plan: "free", upgradeTo: "pro", message: "Custom timeframes are not included in the Free plan." },
  },
  {
    label: "Tab closed by the parallel-chart limit",
    note: "evicted. 'Use this tab' takes the slot back; the oldest other tab pauses instead.",
    fixture: "free",
    t: { kind: "evicted", feature: "chart.parallel", plan: "free", limit: 10, used: 10, upgradeTo: "pro", message: "This chart was paused because you opened more than 10 charts at once on the Free plan." },
  },
  {
    label: "Signed out",
    note: "A signed-out refusal asks for a free account, not a payment.",
    fixture: "anonymous",
    t: { kind: "signin", feature: "ai.credits", plan: "anonymous", limit: 3, used: 3, upgradeTo: "free", message: "Sign in to get more AI credits." },
  },
  {
    label: "Subscriber: upgrade in place",
    note: "A Pro subscriber at a Pro limit. The CTA changes the plan; no second checkout.",
    fixture: "active",
    t: { kind: "quota_exhausted", feature: "ai.credits", plan: "pro", limit: 200, used: 200, resetsAt: now + 12 * DAY, upgradeTo: "pro_plus", message: "You have used all 200 AI credits in your Pro plan for this period." },
  },
  {
    label: "Already on the top plan",
    note: "No upsell to offer, so none is invented: only the real alternatives.",
    fixture: "pro_plus",
    t: { kind: "quota_exhausted", feature: "ai.credits", plan: "pro_plus", limit: 500, used: 500, resetsAt: now + 9 * DAY, upgradeTo: null, message: "You have used all 500 AI credits in your Pro+ plan for this period." },
  },
  {
    label: "Upgrade modal (opened on purpose)",
    note: "kind upgrade: no refusal, compact plan choice, checkout one step away.",
    fixture: "free",
    t: { kind: "upgrade", feature: "ai.credits", plan: "free", upgradeTo: "pro" },
  },
  {
    label: "Trial ended",
    note: "Only reachable once the catalog sets trial.days; shown for design.",
    fixture: "expired",
    t: { kind: "trial_ended", feature: "ai.credits", plan: "free", upgradeTo: "pro", message: "Your Pro trial ended, and your account is on Free." },
  },
];

function OpenModalButtons(): React.ReactElement {
  const { openPaywall } = useBilling();
  return (
    <div className="bl-row-flex" style={{ marginBottom: 20 }}>
      <span className="bl-small">Open as a real modal:</span>
      {TRIGGERS.slice(0, 6).map((x) => (
        <button key={x.label} type="button" className="bl-btn bl-btn--secondary bl-btn--sm" onClick={() => openPaywall({ ...x.t, returnTo: "/paywall-gallery" })}>
          {x.label.split(":")[0]}
        </button>
      ))}
    </div>
  );
}

const STATES: SubState[] = ["anonymous", "free", "incomplete", "trialing", "active", "canceling", "past_due", "expired", "comp"];
const VIEWER: Fixture[] = ["anonymous", "free", "free_low", "active", "active_annual", "pro_plus", "scheduled", "canceling", "past_due", "expired", "incomplete", "trialing", "comp"];

/** Fixture dates are relative to "now", which differs between the server
 *  render and the browser, so the gallery renders in the browser only. */
export default function Page(): React.ReactElement | null {
  const [mounted, setMounted] = React.useState(false);
  React.useEffect(() => setMounted(true), []);
  return mounted ? <Gallery /> : null;
}

function Gallery(): React.ReactElement {
  const [viewer, setViewer] = React.useState<Fixture>("free");
  const [dark, setDark] = React.useState(false);
  const [cycle, setCycle] = React.useState<"monthly" | "annual">("annual");

  React.useEffect(() => {
    const el = document.documentElement;
    const had = el.classList.contains("dark");
    setDark(had);
    return () => {
      el.classList.toggle("dark", had);
    };
  }, []);
  const toggleDark = (): void => {
    document.documentElement.classList.toggle("dark", !dark);
    setDark(!dark);
  };

  return (
    <BillingProvider demo initial={{ catalog: cat, me: me(viewer) }}>
      <div className="bl-page bl-scope">
        <nav className="bl-gal-nav" aria-label="Gallery sections">
          {SECTIONS.map(([id, t]) => (
            <a key={id} href={`#${id}`}>
              {t}
            </a>
          ))}
          <span className="bl-spacer" />
          <button type="button" className="bl-btn bl-btn--ghost bl-btn--sm" onClick={toggleDark} aria-label="Toggle dark mode">
            {dark ? <Sun size={15} /> : <Moon size={15} />} {dark ? "Light" : "Dark"}
          </button>
        </nav>
        <div className="bl-wrap" style={{ maxWidth: 1280 }}>
          <header className="bl-stack" style={{ gap: 10 }}>
            <span className="bl-eyebrow" style={{ margin: 0 }}>Design reference</span>
            <h1 className="bl-h1">Paywall &amp; subscriptions</h1>
            <p className="bl-p" style={{ maxWidth: 780 }}>
              Every screen and state of the billing system, drawn by the production components with fixture data. Prices and limits come from the plan
              catalog (charto/data/plans_catalog.json). Nothing on this page charges, changes a plan, or calls the network.
            </p>
            <div className="bl-row-flex" style={{ marginTop: 8 }}>
              <label className="bl-small" htmlFor="viewer">
                Viewer for pricing and settings:
              </label>
              <select
                id="viewer"
                value={viewer}
                onChange={(e) => setViewer(e.target.value as Fixture)}
                className="bl-btn bl-btn--secondary bl-btn--sm"
                style={{ paddingRight: 8 }}
              >
                {VIEWER.map((f) => (
                  <option key={f} value={f}>
                    {FIXTURE_LABELS[f]}
                  </option>
                ))}
              </select>
            </div>
          </header>

          {/* ── foundations ─────────────────────────────────────── */}
          <Section id="tokens" title="Foundations" intro="Ink-on-paper, built on the app's own tokens so light and dark follow the theme. Colour carries state only: green is paid and healthy, amber needs attention, red has failed.">
            <div className="bl-gal-grid">
              <Item label="Type">
                <div className="bl-stack">
                  <span className="bl-eyebrow" style={{ margin: 0 }}>Eyebrow</span>
                  <span className="bl-h1" style={{ fontSize: 40 }}>Serif display</span>
                  <span className="bl-h2">Serif section head</span>
                  <span className="bl-h3">UI heading, Inter 600</span>
                  <span className="bl-p">Body copy is calm and specific, with no urgency and no emoji.</span>
                  <span className="bl-fine">Fine print for terms and notes.</span>
                </div>
              </Item>
              <Item label="Colour tokens">
                <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: 10 }}>
                  {["--bl-ink", "--bl-muted", "--bl-faint", "--bl-rule-strong", "--bl-card", "--bl-ground", "--bl-hover", "--bl-track", "--bl-good", "--bl-warn", "--bl-bad", "--bl-good-wash"].map((v) => (
                    <div key={v} className="bl-stack" style={{ gap: 4 }}>
                      <div style={{ height: 44, borderRadius: 8, background: `var(${v})`, boxShadow: "inset 0 0 0 1px var(--bl-rule)" }} />
                      <code className="bl-fine">{v}</code>
                    </div>
                  ))}
                </div>
              </Item>
              <Item label="Buttons">
                <div className="bl-stack">
                  <div className="bl-row-flex">
                    <button type="button" className="bl-btn bl-btn--primary">Primary</button>
                    <button type="button" className="bl-btn bl-btn--secondary">Secondary</button>
                    <button type="button" className="bl-btn bl-btn--ghost">Ghost</button>
                    <button type="button" className="bl-btn bl-btn--danger">Danger</button>
                  </div>
                  <div className="bl-row-flex">
                    <button type="button" className="bl-btn bl-btn--primary bl-btn--lg">Large</button>
                    <button type="button" className="bl-btn bl-btn--secondary bl-btn--sm">Small</button>
                    <button type="button" className="bl-btn bl-btn--primary" disabled>Disabled</button>
                    <button type="button" className="bl-btn bl-btn--current" disabled>Current plan</button>
                    <button type="button" className="bl-link">Text link</button>
                  </div>
                </div>
              </Item>
              <Item label="Toggle, badges, locks">
                <div className="bl-stack">
                  <BillingToggle cycle={cycle} onChange={setCycle} savePct={10} />
                  <div className="bl-row-flex">
                    {STATES.map((s) => (
                      <StatusBadge key={s} state={s} />
                    ))}
                  </div>
                  <div className="bl-row-flex">
                    <LockBadge plan="Pro" />
                    <LockBadge plan="Pro+" />
                    <span className="bl-small">Custom timeframes</span>
                    <SoonTag />
                  </div>
                </div>
              </Item>
              <Item label="Usage and quota meters">
                <div className="bl-stack bl-stack--lg">
                  <UsageMeter label="AI credits" view={meter(6, 15, now + 12 * DAY)} />
                  <UsageMeter label="AI credits, running low" view={meter(13, 15, now + 12 * DAY)} />
                  <UsageMeter label="AI credits, used up" view={meter(15, 15, now + 12 * DAY)} />
                  <LimitMeter label="Price alerts" used={48} limit={400} />
                  <LimitMeter label="Watchlists" used={7} limit={null} />
                </div>
              </Item>
              <Item label="Credits remaining indicator" note="Under the chat composer; every answer reports what is left.">
                <div className="bl-stack">
                  <CreditsPill view={quotaView(me("free"), "ai.credits")!} />
                  <CreditsPill view={quotaView(me("free_low"), "ai.credits")!} onUpgrade={() => {}} />
                  <CreditsPill view={quotaView(me("free_out"), "ai.credits")!} onUpgrade={() => {}} />
                </div>
              </Item>
              <Item label="Feature-lock rows" note="Grid layouts and indicators above the plan carry the plan that unlocks them.">
                <div className="bl-card" style={{ padding: 8 }}>
                  <LockedItem label="2 × 2 grid (4 charts)" plan={null} onClick={() => {}} />
                  <LockedItem label="2 × 3 grid (6 charts)" plan="Pro" onClick={() => {}} />
                  <LockedItem label="2 × 4 grid (8 charts)" plan="Pro" onClick={() => {}} />
                </div>
              </Item>
              <Item label="Loading, empty, error">
                <div className="bl-stack bl-stack--lg">
                  <SkeletonRows rows={3} />
                  <EmptyState icon={<Receipt size={26} />} title="No invoices yet">Invoices appear here after your first payment.</EmptyState>
                  <ErrorState message="The payment provider did not answer. Try again in a moment." onRetry={() => {}} />
                </div>
              </Item>
              <Item label="Trust row">
                <TrustRow />
              </Item>
            </div>
          </Section>

          {/* ── pricing ─────────────────────────────────────────── */}
          <Section id="pricing" title="Pricing page" intro="Live at /pricing as a full-screen overlay. Buttons follow the viewer: sign up when signed out, checkout on Free, change plan for a subscriber. Switch the viewer above.">
            <Demo fixture={viewer}>
              <ViewerPricing />
            </Demo>
          </Section>

          {/* ── contextual paywalls ─────────────────────────────── */}
          <Section id="paywalls" title="Contextual paywalls" intro="One panel whose words follow the trigger: the server's own sentence, what each plan gives for that feature, and every real way forward. 'Not now' always returns the user to where they were.">
            <OpenModalButtons />
            <div className="bl-gal-grid">
              {TRIGGERS.map((x) => (
                <Item key={x.label} label={x.label} note={x.note} stage={false}>
                  <PanelCard fixture={x.fixture} trigger={x.t} />
                </Item>
              ))}
            </div>
          </Section>

          {/* ── preview & full page ─────────────────────────────── */}
          <Section id="preview" title="Premium preview and full-page paywall" intro="A preview shows the feature working and is labelled as a preview, with the unlock beneath it. The full page is for features that deserve their own argument.">
            <Demo fixture="free">
              <div className="bl-gal-grid">
                <Item label="Premium preview: 8-chart grid" stage={false}>
                  <PremiumPreview feature="chart.panes" caption="Sample layout">
                    <GridSample />
                  </PremiumPreview>
                </Item>
              </div>
              <div style={{ marginTop: 32 }}>
                <Item label="Full-page paywall">
                  <FullPagePaywall feature="chart.panes" returnTo="/" onDismiss={() => {}} />
                </Item>
              </div>
            </Demo>
          </Section>

          {/* ── checkout ────────────────────────────────────────── */}
          <Section id="checkout" title="Checkout" intro="Order summary with the real total, renewal terms before the button, and every outcome: success returns to the feature, a failure says nothing was charged, a closed window can be resumed.">
            <div className="bl-gal-grid" style={{ gridTemplateColumns: "repeat(auto-fill, minmax(min(100%, 620px), 1fr))" }}>
              {(
                [
                  ["Review", "free", "review"],
                  ["Opening the payment window", "free", "opening"],
                  ["Interrupted: window closed", "free", "dismissed"],
                  ["Payment failed", "free", "failed"],
                  ["Confirming (provider pending)", "free", "pending"],
                  ["Success, back to the feature", "free", "success"],
                ] as [string, Fixture, CheckoutPhase][]
              ).map(([label, f, phase]) => (
                <Item key={label} label={label} stage={false}>
                  <Frame>
                    <Demo fixture={f}>
                      <CheckoutView plan="pro" feature="chart.panes" returnTo="/chart" initialPhase={phase} initialError={phase === "failed" ? "Your bank declined the payment." : undefined} />
                    </Demo>
                  </Frame>
                </Item>
              ))}
              <Item label="Signed out" stage={false}>
                <Frame height={640}>
                  <Demo fixture="anonymous">
                    <CheckoutView plan="pro" />
                  </Demo>
                </Frame>
              </Item>
              <Item label="Already on this plan" stage={false}>
                <Frame height={640}>
                  <Demo fixture="active">
                    <CheckoutView plan="pro" initialCycle="monthly" />
                  </Demo>
                </Frame>
              </Item>
              <Item label="Already subscribed, buying another plan" stage={false}>
                <Frame height={640}>
                  <Demo fixture="active">
                    <CheckoutView plan="pro_plus" />
                  </Demo>
                </Frame>
              </Item>
              <Item label="Payments not configured (no Razorpay keys)" stage={false}>
                <Frame height={640}>
                  <Demo fixture="free" catalog={{ ...cat, checkout: false }}>
                    <CheckoutView plan="pro" />
                  </Demo>
                </Frame>
              </Item>
            </div>
          </Section>

          {/* ── plan changes ────────────────────────────────────── */}
          <Section id="change" title="Upgrade and downgrade confirmation" intro="States when it applies and what it costs in features before anything is pressed. Downgrades wait for the renewal, list what gets smaller, and warn which alerts would pause.">
            <div className="bl-gal-grid">
              <Item label="Upgrade (applies now)" stage={false}>
                <div className="bl-panel-inline"><PlanChangePanel catalog={cat} me={me("active")} to="pro_plus" cycle="monthly" demo onBack={() => {}} onDone={() => {}} /></div>
              </Item>
              <Item label="Downgrade (scheduled), with alerts that would pause" stage={false}>
                <div className="bl-panel-inline"><PlanChangePanel catalog={cat} me={me("scheduled")} to="pro" cycle="monthly" demo onBack={() => {}} onDone={() => {}} /></div>
              </Item>
              <Item label="Switch to yearly" stage={false}>
                <div className="bl-panel-inline"><PlanChangePanel catalog={cat} me={me("active")} to="pro" cycle="annual" demo onBack={() => {}} onDone={() => {}} /></div>
              </Item>
              <Item label="Downgrade confirmed" stage={false}>
                <div className="bl-panel-inline"><PlanChangePanel catalog={cat} me={me("pro_plus")} to="pro" cycle="monthly" demo initialPhase="done" onBack={() => {}} onDone={() => {}} /></div>
              </Item>
            </div>
          </Section>

          {/* ── cancellation ────────────────────────────────────── */}
          <Section id="cancel" title="Downgrade and cancellation" intro="Review what changes, see only alternatives that exist (no pause offer, because pausing is not supported), confirm, done. 'Continue to cancel' has equal weight on every step.">
            <div className="bl-gal-grid">
              {(["review", "alternatives", "confirm", "done"] as CancelStep[]).map((s) => (
                <Item key={s} label={`Step: ${s}`} stage={false}>
                  <div className="bl-panel-inline">
                    <CancelPanel catalog={cat} me={me(s === "alternatives" ? "pro_plus" : "active")} demo initialStep={s} onClose={() => {}} onDone={() => {}} onAlternative={() => {}} />
                  </div>
                </Item>
              ))}
            </div>
          </Section>

          {/* ── settings ────────────────────────────────────────── */}
          <Section id="settings" title="Plan & billing" intro="Live at /settings/billing. Switch the viewer above to walk every subscription state: active, trialing, scheduled change, cancelled, payment failed, expired, incomplete checkout, complimentary, free and signed out.">
            <Frame height={1100}>
              <Demo fixture={viewer}>
                <BillingSettings invoices={fixtureInvoices(viewer, cat, now)} />
              </Demo>
            </Frame>
          </Section>

          {/* ── banners ─────────────────────────────────────────── */}
          <Section id="banners" title="Banners and lifecycle states" intro="Each state is said once, with the one action that resolves it. Day counts point at a real date; nothing ticks down in seconds.">
            <div className="bl-stack bl-stack--lg">
              <Item label="Upgrade banner (free user, near a limit)">
                <UpgradeBanner title="You have 2 AI credits left this month" onUpgrade={() => {}} onDismiss={() => {}}>
                  Pro gives you 200 a month. Your credits refill on the 1st either way.
                </UpgradeBanner>
              </Item>
              <Item label="Trial ending"><TrialBanner catalog={cat} me={me("trialing")} onChoose={() => {}} now={now} /></Item>
              <Item label="Renewal reminder (7 days before)"><RenewalReminder catalog={cat} me={renewSoon()} onManage={() => {}} now={now} /></Item>
              <Item label="Payment failed, in grace"><PastDueBanner catalog={cat} me={me("past_due")} onFix={() => {}} now={now} /></Item>
              <Item label="Card expiring" note="Needs card expiry from the provider; not on the wire today."><CardExpiryBanner last4="4242" expires="at the end of this month" onUpdate={() => {}} /></Item>
              <Item label="Cancelled, active to period end"><CancelingBanner catalog={cat} me={me("canceling")} onResubscribe={() => {}} now={now} /></Item>
              <Item label="Checkout not completed"><IncompleteBanner catalog={cat} me={me("incomplete")} onResume={() => {}} /></Item>
              <Item label="Expired"><ExpiredBanner catalog={cat} me={me("expired")} onResubscribe={() => {}} /></Item>
              <Item label="Paused alert (over plan limit)" note="How a paused alert reads in the alert list.">
                <Notice tone="warn" title="Paused: over your plan limit" actions={<button type="button" className="bl-btn bl-btn--primary bl-btn--sm">See plans</button>}>
                  RELIANCE crosses 2,950. Your Free plan arms 20 price alerts; this one is saved and re-arms when a slot frees up or you upgrade.
                </Notice>
              </Item>
            </div>
          </Section>

          {/* ── mobile ──────────────────────────────────────────── */}
          <Section id="mobile" title="Mobile" intro="The same components at 390 px. Layouts respond to their container, so these frames show exactly what a phone renders; real modals become bottom sheets.">
            <div className="bl-gal-phones">
              <div className="bl-gal-phone bl-scope">
                <Demo fixture="free">
                  <div style={{ padding: "40px 16px" }}>
                    <div className="bl-stack" style={{ marginBottom: 24, alignItems: "center" }}>
                      <span className="bl-h2 bl-center">Choose your plan</span>
                      <BillingToggle cycle={cycle} onChange={setCycle} savePct={10} />
                    </div>
                    <PlanCards catalog={cat} me={me("free")} cycle={cycle} onAction={() => {}} />
                  </div>
                </Demo>
              </div>
              <div className="bl-gal-phone">
                <PaywallPanel trigger={TRIGGERS[0]!.t} catalog={cat} me={me("free_out")} demo {...panelNoop} />
              </div>
              <div className="bl-gal-phone" style={{ position: "relative", transform: "translateZ(0)", overflow: "hidden" }}>
                <Demo fixture="past_due">
                  <BillingSettings invoices={fixtureInvoices("past_due", cat, now)} />
                </Demo>
              </div>
              <div className="bl-gal-phone" style={{ position: "relative", transform: "translateZ(0)", overflow: "hidden" }}>
                <Demo fixture="free">
                  <CheckoutView plan="pro" feature="ai.credits" returnTo="/" />
                </Demo>
              </div>
            </div>
          </Section>
        </div>
      </div>
    </BillingProvider>
  );
}

function ViewerPricing(): React.ReactElement {
  const { catalog, me: m } = useBilling();
  const [cycle, setCycle] = React.useState<"monthly" | "annual">("annual");
  return (
    <div className="bl-stack bl-stack--lg">
      <div className="bl-row-flex" style={{ justifyContent: "center" }}>
        <BillingToggle cycle={cycle} onChange={setCycle} savePct={10} />
      </div>
      <PlanCards catalog={catalog} me={m} cycle={cycle} onAction={() => {}} onSeeAll={() => {}} />
      <TrustRow />
      <h3 className="bl-h2 bl-center" style={{ marginTop: 32 }}>Compare all plans</h3>
      <ComparisonTable catalog={catalog} cycle={cycle} currentPlan={m && m.plan !== "anonymous" ? m.plan : null} />
      <h3 className="bl-h2 bl-center" style={{ marginTop: 32 }}>Questions</h3>
      <div style={{ maxWidth: 820, margin: "0 auto", width: "100%" }}>
        <PricingFAQ />
      </div>
    </div>
  );
}

function renewSoon(): BillingMe {
  const m = me("active");
  return { ...m, subscription: m.subscription ? { ...m.subscription, period_end: now + 5 * DAY } : null };
}

function GridSample(): React.ReactElement {
  return (
    <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: 6, padding: "52px 12px 12px", height: 260 }}>
      {Array.from({ length: 8 }, (_, i) => (
        <svg key={i} viewBox="0 0 100 70" style={{ background: "var(--bl-raised)", borderRadius: 6, width: "100%", height: "100%" }}>
          <polyline
            fill="none"
            stroke="var(--bl-ink)"
            strokeWidth="1.4"
            points={Array.from({ length: 12 }, (_, j) => `${j * 9},${35 + Math.sin(j * 0.9 + i) * 14 - j * (i % 2 ? -1 : 1)}`).join(" ")}
          />
        </svg>
      ))}
    </div>
  );
}
