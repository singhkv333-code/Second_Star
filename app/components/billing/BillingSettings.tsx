"use client";

/**
 * Plan & billing — the subscription, its usage, how it is paid and what it
 * has cost. Every state of `subscriptionView()` has a place here:
 *
 *   free / anonymous   the plan, its limits in use, a quiet way up
 *   incomplete         resume the checkout that was never paid
 *   active / trialing  renewal date and amount, change, switch cycle, cancel
 *   canceling          when it ends; nothing to cancel any more
 *   past_due           the failed payment and the date the grace ends
 *   expired            what lapsed, and resubscribe
 *   comp               a granted plan, no billing
 */

import * as React from "react";
import { useRouter } from "next/navigation";
import { ArrowLeft, CreditCard, ExternalLink, FileText, Receipt, Undo2 } from "lucide-react";
import { fmtDate, fmtValue, inr } from "@/lib/billing/format";
import { planById, planName } from "@/lib/billing/catalog";
import { annualSaving, checkoutHref, quotaView, subscriptionView } from "@/lib/billing/entitlements";
import { billingApi } from "@/lib/billing/api";
import { track } from "@/lib/billing/analytics";
import { INVOICE_TBD } from "@/lib/billing/policy";
import type { BillingMe, Cycle, InvoiceList, PaidPlanId, PublicCatalog } from "@/lib/billing/types";
import { useBilling } from "./BillingProvider";
import {
  EmptyState,
  ErrorState,
  LimitMeter,
  Notice,
  PolicyText,
  SkeletonRows,
  StatusBadge,
  UsageMeter,
} from "./primitives";
import { SubscriptionBanner } from "./Banners";
import { BillingModal } from "./modal";
import { PlanChangePanel, PlanPickerPanel } from "./PlanChange";
import { CancelPanel } from "./CancelFlow";

type Modal =
  | { kind: "pick" }
  | { kind: "change"; plan: PaidPlanId; cycle: Cycle }
  | { kind: "cancel" }
  | null;

type InvoiceState = { status: "loading" } | { status: "ready"; data: InvoiceList } | { status: "error"; message: string; unavailable?: boolean };

export function BillingSettings({
  invoices: injected,
  standalone = true,
}: {
  /** gallery / tests: skip the network */
  invoices?: InvoiceList;
  standalone?: boolean;
}): React.ReactElement {
  const { catalog: cat, me, loading, error, refresh, setMe, demo, signedIn } = useBilling();
  const router = useRouter();
  const [modal, setModal] = React.useState<Modal>(null);
  const [inv, setInv] = React.useState<InvoiceState>(injected ? { status: "ready", data: injected } : { status: "loading" });
  const [undoBusy, setUndoBusy] = React.useState(false);
  const [undoError, setUndoError] = React.useState<string | null>(null);

  const loadInvoices = React.useCallback(async () => {
    if (injected) return;
    setInv({ status: "loading" });
    const r = await billingApi.invoices();
    if (r.ok) setInv({ status: "ready", data: r.data });
    else setInv({ status: "error", message: r.error, unavailable: r.status === 503 });
  }, [injected]);

  React.useEffect(() => {
    if (injected) setInv({ status: "ready", data: injected });
  }, [injected]);

  React.useEffect(() => {
    if (!demo && signedIn) void loadInvoices();
  }, [demo, signedIn, loadInvoices]);

  const view = subscriptionView(me);
  const toPricing = (): void => router.push("/pricing");

  const resumeCheckout = (): void => {
    const p = (view.subPlan && view.subPlan !== "free" && view.subPlan !== "anonymous" ? view.subPlan : "pro") as PaidPlanId;
    router.push(checkoutHref(p, view.cycle ?? "annual", { returnTo: "/settings/billing", surface: "billing" }));
  };

  const fixPayment = (): void => {
    const url = inv.status === "ready" ? inv.data.manage_url : null;
    if (url) window.open(url, "_blank", "noopener");
    else void loadInvoices();
  };

  const undo = async (): Promise<void> => {
    setUndoBusy(true);
    setUndoError(null);
    if (demo) {
      setUndoBusy(false);
      return;
    }
    const r = await billingApi.undoChange();
    setUndoBusy(false);
    if (r.ok) {
      setMe(r.data.billing);
      track("plan_change_undone", { plan: view.subPlan });
    } else setUndoError(r.error);
  };

  const body = (): React.ReactNode => {
    if (loading && !me) {
      return (
        <div className="bl-card">
          <SkeletonRows rows={4} />
        </div>
      );
    }
    if (!me) {
      return <ErrorState message={error ?? "Your plan could not be loaded."} onRetry={() => void refresh()} />;
    }
    if (view.state === "anonymous") {
      return (
        <div className="bl-card">
          <EmptyState
            icon={<CreditCard size={28} />}
            title="Sign in to see your plan"
            action={
              <button type="button" className="bl-btn bl-btn--primary" onClick={() => router.push("/login?next=/settings/billing")}>
                Sign in
              </button>
            }
          >
            Plans, usage and invoices belong to your account.
          </EmptyState>
        </div>
      );
    }
    return (
      <>
        <SubscriptionBanner
          catalog={cat}
          me={me}
          onFixPayment={fixPayment}
          onChoosePlan={toPricing}
          onResumeCheckout={resumeCheckout}
          onManage={() => setModal({ kind: "pick" })}
        />
        <CurrentPlan
          cat={cat}
          me={me}
          onChange={() => setModal({ kind: "pick" })}
          onSwitchAnnual={() => view.subPlan && setModal({ kind: "change", plan: view.subPlan as PaidPlanId, cycle: "annual" })}
          onCancel={() => {
            track("cancel_started", { plan: view.subPlan });
            setModal({ kind: "cancel" });
          }}
          onUpgrade={toPricing}
          onResubscribe={resumeCheckout}
          onUndo={undo}
          undoBusy={undoBusy}
          undoError={undoError}
        />
        <Usage cat={cat} me={me} />
        {view.state !== "free" && view.state !== "comp" ? <Payment inv={inv} onRetry={loadInvoices} /> : null}
        <History inv={inv} onRetry={loadInvoices} hasSub={!!me.subscription && view.state !== "comp"} />
      </>
    );
  };

  const content = (
    <div className="bl-stack bl-stack--lg">
      <div className="bl-stack" style={{ gap: 6 }}>
        <span className="bl-eyebrow" style={{ margin: 0 }}>Settings</span>
        <h1 className="bl-h1" style={{ fontSize: "clamp(30px, 4cqi, 42px)" }}>Plan &amp; billing</h1>
      </div>
      {body()}
      <p className="bl-fine">
        Prices include GST. Payments are processed by Razorpay. Pivot provides analysis and simulated trading, not financial advice.
      </p>

      {me && modal ? (
        <BillingModal open onOpenChange={(o) => !o && setModal(null)} label="Manage plan">
          {modal.kind === "pick" ? (
            <PlanPickerPanel
              catalog={cat}
              me={me}
              onPick={(plan, cycle) => setModal({ kind: "change", plan, cycle })}
              onCancelInstead={() => setModal({ kind: "cancel" })}
              onClose={() => setModal(null)}
            />
          ) : modal.kind === "change" ? (
            <PlanChangePanel
              catalog={cat}
              me={me}
              to={modal.plan}
              cycle={modal.cycle}
              demo={demo}
              onBack={() => setModal({ kind: "pick" })}
              onDone={(m) => {
                setMe(m);
                setModal(null);
              }}
            />
          ) : (
            <CancelPanel
              catalog={cat}
              me={me}
              demo={demo}
              onClose={() => setModal(null)}
              onAlternative={(plan, cycle) => setModal({ kind: "change", plan, cycle })}
              onDone={(m) => {
                if (m) setMe(m);
                setModal(null);
              }}
            />
          )}
        </BillingModal>
      ) : null}
    </div>
  );

  if (!standalone) return <div className="bl-scope">{content}</div>;
  return (
    <div className="bl-page bl-scope">
      <div className="bl-topbar">
        <button type="button" className="bl-back" onClick={() => (window.history.length > 1 ? router.back() : router.push("/"))}>
          <ArrowLeft size={16} /> Back
        </button>
      </div>
      <div className="bl-wrap bl-wrap--narrow" style={{ paddingTop: 16 }}>
        {content}
      </div>
    </div>
  );
}

// ── current plan ────────────────────────────────────────────────────────

function CurrentPlan(p: {
  cat: PublicCatalog;
  me: BillingMe;
  onChange: () => void;
  onSwitchAnnual: () => void;
  onCancel: () => void;
  onUpgrade: () => void;
  onResubscribe: () => void;
  onUndo: () => void;
  undoBusy: boolean;
  undoError: string | null;
}): React.ReactElement {
  const { cat, me } = p;
  const v = subscriptionView(me);
  const shown = v.state === "expired" || v.state === "incomplete" ? me.plan : (v.subPlan ?? me.plan);
  const plan = planById(cat, shown);
  const price = v.cycle && plan ? plan.prices[v.cycle] : undefined;
  const paying = ["active", "trialing", "past_due"].includes(v.state);
  const saving = v.subPlan ? annualSaving(cat, v.subPlan) : 0;

  return (
    <section className="bl-card bl-stack bl-stack--lg" aria-labelledby="bl-current">
      <div className="bl-card-head">
        <div className="bl-stack" style={{ gap: 6 }}>
          <span className="bl-small">Current plan</span>
          <div className="bl-row-flex" style={{ gap: 12 }}>
            <h2 id="bl-current" className="bl-h2">
              {planName(cat, shown)}
            </h2>
            <StatusBadge state={v.state} />
          </div>
        </div>
        {paying || v.state === "canceling" ? (
          <div style={{ textAlign: "right" }}>
            <div className="bl-price bl-price--sm">
              {price ? inr(price.amount) : "—"}
              <span className="bl-price-per">/{v.cycle === "annual" ? "yr" : "mo"}</span>
            </div>
            <div className="bl-fine">GST included</div>
          </div>
        ) : null}
      </div>

      <div className="bl-rows">
        {paying ? (
          <>
            <div className="bl-kv">
              <span className="bl-kv-label">Billing</span>
              <span className="bl-kv-value">{v.cycle === "annual" ? "Yearly" : "Monthly"}</span>
            </div>
            <div className="bl-kv">
              <span className="bl-kv-label">{v.state === "trialing" ? "Trial ends" : v.state === "past_due" ? "Plan held until" : "Next renewal"}</span>
              <span className="bl-kv-value">{fmtDate(v.state === "past_due" ? v.graceUntil : (v.renewsAt ?? v.endsAt))}</span>
            </div>
          </>
        ) : null}
        {v.state === "canceling" ? (
          <div className="bl-kv">
            <span className="bl-kv-label">Active until</span>
            <span className="bl-kv-value">{fmtDate(v.endsAt)}</span>
          </div>
        ) : null}
        {v.state === "comp" ? (
          <div className="bl-kv">
            <span className="bl-kv-label">Billing</span>
            <span className="bl-kv-value">Granted by Pivot, nothing is charged</span>
          </div>
        ) : null}
      </div>

      {v.pending ? (
        <Notice
          title={`Changing to ${planName(cat, v.pending.plan)}, ${v.pending.cycle === "annual" ? "yearly" : "monthly"}, on ${fmtDate(v.pending.at)}`}
          actions={
            <button type="button" className="bl-btn bl-btn--secondary bl-btn--sm" onClick={p.onUndo} disabled={p.undoBusy}>
              <Undo2 size={14} /> Undo
            </button>
          }
        >
          Until then you keep {planName(cat, v.subPlan)}.{p.undoError ? ` ${p.undoError}` : ""}
        </Notice>
      ) : null}

      <div className="bl-row-flex">
        {paying ? (
          <>
            <button type="button" className="bl-btn bl-btn--primary" onClick={p.onChange}>
              Change plan
            </button>
            {v.state === "active" && v.cycle === "monthly" && saving > 0 && !v.pending ? (
              <button type="button" className="bl-btn bl-btn--secondary" onClick={p.onSwitchAnnual}>
                Switch to yearly, save {inr(saving)}
              </button>
            ) : null}
            <span className="bl-spacer" />
            <button type="button" className="bl-btn bl-btn--ghost" onClick={p.onCancel}>
              Cancel subscription
            </button>
          </>
        ) : v.state === "canceling" || v.state === "expired" ? (
          <button type="button" className="bl-btn bl-btn--primary" onClick={v.state === "expired" ? p.onResubscribe : p.onUpgrade}>
            {v.state === "expired" ? `Resubscribe to ${planName(cat, v.subPlan)}` : "See plans"}
          </button>
        ) : v.state === "free" || v.state === "incomplete" ? (
          <button type="button" className="bl-btn bl-btn--primary" onClick={p.onUpgrade}>
            Compare plans
          </button>
        ) : null}
      </div>
    </section>
  );
}

// ── usage ───────────────────────────────────────────────────────────────

function Usage({ cat, me }: { cat: PublicCatalog; me: BillingMe }): React.ReactElement {
  const credits = quotaView(me, "ai.credits");
  const price = me.features["alerts.price"];
  const tech = me.features["alerts.technical"];
  const plan = planById(cat, me.plan === "anonymous" ? "free" : me.plan);
  const fixed = ["chart.indicators", "chart.panes", "chart.parallel", "alerts.expiry_days", "chart.history_bars"];
  return (
    <section className="bl-section" aria-labelledby="bl-usage">
      <div className="bl-section-title">
        <h2 id="bl-usage" className="bl-h3">Usage this period</h2>
        {!me.paywall_enabled ? <span className="bl-fine">Limits are not enforced yet</span> : null}
      </div>
      <div className="bl-card bl-grid-2">
        {credits ? <UsageMeter label="AI credits" view={credits} /> : null}
        {price && typeof price.used === "number" ? (
          <LimitMeter label="Price alerts" used={price.used} limit={(price.value as number | null) ?? null} />
        ) : null}
        {tech && typeof tech.used === "number" ? (
          <LimitMeter label="Technical alerts" used={tech.used} limit={(tech.value as number | null) ?? null} />
        ) : null}
        <div className="bl-rows">
          {fixed.map((k) => {
            const f = cat.features[k];
            const v = plan?.features[k];
            if (!f || v === undefined) return null;
            return (
              <div key={k} className="bl-kv" style={{ padding: "9px 0" }}>
                <span className="bl-kv-label">{f.label}</span>
                <span className="bl-kv-value">{fmtValue(k, f, v)}</span>
              </div>
            );
          })}
        </div>
      </div>
    </section>
  );
}

// ── payment method ──────────────────────────────────────────────────────

const METHOD: Record<string, string> = {
  card: "Card",
  upi: "UPI Autopay",
  emandate: "Bank mandate",
  nach: "NACH mandate",
};

function Payment({ inv, onRetry }: { inv: InvoiceState; onRetry: () => void }): React.ReactElement {
  return (
    <section className="bl-section" aria-labelledby="bl-pay">
      <h2 id="bl-pay" className="bl-h3">Payment method</h2>
      <div className="bl-card">
        {inv.status === "loading" ? (
          <SkeletonRows rows={1} />
        ) : inv.status === "error" ? (
          inv.unavailable ? (
            <p className="bl-small">Payments are not switched on for this server, so there is no payment method on file.</p>
          ) : (
            <ErrorState message={inv.message} onRetry={onRetry} />
          )
        ) : (
          <div className="bl-row-flex" style={{ justifyContent: "space-between" }}>
            <span className="bl-row-flex">
              <CreditCard size={18} aria-hidden />
              <span>
                {inv.data.payment_method ? (METHOD[inv.data.payment_method] ?? inv.data.payment_method) : "Not on file yet"}
                <span className="bl-fine" style={{ display: "block" }}>
                  Held by Razorpay. Pivot never sees the details.
                </span>
              </span>
            </span>
            {inv.data.manage_url ? (
              <a className="bl-btn bl-btn--secondary bl-btn--sm" href={inv.data.manage_url} target="_blank" rel="noopener noreferrer">
                Update <ExternalLink size={13} />
              </a>
            ) : null}
          </div>
        )}
      </div>
    </section>
  );
}

// ── billing history ─────────────────────────────────────────────────────

function History({ inv, onRetry, hasSub }: { inv: InvoiceState; onRetry: () => void; hasSub: boolean }): React.ReactElement {
  return (
    <section className="bl-section" aria-labelledby="bl-history">
      <div className="bl-section-title">
        <h2 id="bl-history" className="bl-h3">Billing history</h2>
      </div>
      <div className="bl-card bl-card--flush bl-scope">
        {!hasSub ? (
          <EmptyState icon={<Receipt size={26} />} title="No invoices yet">
            Invoices appear here after your first payment.
          </EmptyState>
        ) : inv.status === "loading" ? (
          <div style={{ padding: 20 }}>
            <SkeletonRows rows={3} />
          </div>
        ) : inv.status === "error" ? (
          <div style={{ padding: 16 }}>
            {inv.unavailable ? (
              <p className="bl-small">Payments are not switched on for this server, so there are no invoices.</p>
            ) : (
              <ErrorState message={inv.message} onRetry={onRetry} />
            )}
          </div>
        ) : inv.data.invoices.length === 0 ? (
          <EmptyState icon={<Receipt size={26} />} title="No invoices yet">
            Invoices appear here after your first payment.
          </EmptyState>
        ) : (
          <InvoiceTable list={inv.data} />
        )}
      </div>
      <p className="bl-fine">
        <PolicyText parts={[INVOICE_TBD]} />
      </p>
    </section>
  );
}

const INV_STATUS: Record<string, { text: string; tone: string }> = {
  paid: { text: "Paid", tone: "good" },
  issued: { text: "Due", tone: "warn" },
  partially_paid: { text: "Partly paid", tone: "warn" },
  cancelled: { text: "Cancelled", tone: "" },
  expired: { text: "Expired", tone: "" },
};

export function InvoiceTable({ list }: { list: InvoiceList }): React.ReactElement {
  return (
    <table className="bl-table">
      <thead>
        <tr>
          <th scope="col">Date</th>
          <th scope="col">Period</th>
          <th scope="col">Status</th>
          <th scope="col">Amount</th>
          <th scope="col">
            <span className="bl-sr">Invoice</span>
          </th>
        </tr>
      </thead>
      <tbody>
        {list.invoices.map((i) => {
          const s = INV_STATUS[i.status] ?? { text: i.status, tone: "" };
          return (
            <tr key={i.id}>
              <td data-k="date">{fmtDate(i.date)}</td>
              <td data-k="period">{i.period_start && i.period_end ? `${fmtDate(i.period_start)} – ${fmtDate(i.period_end)}` : "—"}</td>
              <td data-k="status">
                <span className={`bl-badge ${s.tone ? `bl-badge--${s.tone}` : ""}`}>{s.text}</span>
              </td>
              <td data-k="amount">{inr(i.amount)}</td>
              <td data-k="link">
                {i.url ? (
                  <a className="bl-link" href={i.url} target="_blank" rel="noopener noreferrer">
                    <FileText size={13} style={{ verticalAlign: -2, marginRight: 4 }} />
                    Invoice
                  </a>
                ) : (
                  <span className="bl-fine">Not available</span>
                )}
              </td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}
