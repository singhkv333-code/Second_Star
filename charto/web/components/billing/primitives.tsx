"use client";

/**
 * The small, reusable pieces of the billing design system. Every larger
 * surface (pricing, paywalls, checkout, settings) is built from these, so a
 * badge or a meter looks and behaves the same wherever it appears.
 */

import * as React from "react";
import { AlertTriangle, CheckCircle2, Info, Lock, LockKeyhole, RefreshCw, ShieldCheck, X, XCircle } from "lucide-react";
import { cn } from "@/lib/utils";
import { fmtDate, num } from "@/lib/billing/format";
import { meter, type QuotaView, type SubState } from "@/lib/billing/entitlements";
import type { Cycle } from "@/lib/billing/types";
import type { Part } from "@/lib/billing/policy";

// ── billing toggle ──────────────────────────────────────────────────────

export function BillingToggle({
  cycle,
  onChange,
  savePct,
  label = "Billing period",
}: {
  cycle: Cycle;
  onChange: (c: Cycle) => void;
  savePct?: number;
  label?: string;
}): React.ReactElement {
  const opts: { id: Cycle; text: string }[] = [
    { id: "monthly", text: "Monthly" },
    { id: "annual", text: "Yearly" },
  ];
  const onKey = (e: React.KeyboardEvent): void => {
    if (e.key === "ArrowLeft" || e.key === "ArrowRight") {
      e.preventDefault();
      onChange(cycle === "monthly" ? "annual" : "monthly");
    }
  };
  return (
    <div className="bl-toggle" role="radiogroup" aria-label={label} onKeyDown={onKey}>
      {opts.map((o) => (
        <button
          key={o.id}
          type="button"
          role="radio"
          aria-checked={cycle === o.id}
          tabIndex={cycle === o.id ? 0 : -1}
          className="bl-seg"
          onClick={() => onChange(o.id)}
        >
          {o.text}
          {o.id === "annual" && savePct ? <span className="bl-seg-save">Save {savePct}%</span> : null}
        </button>
      ))}
    </div>
  );
}

// ── status badge ────────────────────────────────────────────────────────

const STATUS: Record<SubState, { text: string; tone: "good" | "warn" | "bad" | "neutral" }> = {
  anonymous: { text: "Signed out", tone: "neutral" },
  free: { text: "Free", tone: "neutral" },
  incomplete: { text: "Checkout not completed", tone: "warn" },
  trialing: { text: "Trial", tone: "good" },
  active: { text: "Active", tone: "good" },
  canceling: { text: "Cancelled", tone: "neutral" },
  past_due: { text: "Payment failed", tone: "bad" },
  expired: { text: "Expired", tone: "neutral" },
  comp: { text: "Complimentary", tone: "good" },
};

export function StatusBadge({ state }: { state: SubState }): React.ReactElement {
  const s = STATUS[state];
  return (
    <span className={cn("bl-badge", s.tone !== "neutral" && `bl-badge--${s.tone}`)} data-state={state}>
      {s.text}
    </span>
  );
}

export function RecommendedLabel({ children = "Recommended" }: { children?: React.ReactNode }): React.ReactElement {
  return <span className="bl-plan-flag">{children}</span>;
}

// ── feature-lock indicator ──────────────────────────────────────────────

/** A lock and the plan name ("Pro") next to anything above the user's plan. Purely a label: the
 *  click still goes through and the server still decides. */
export function LockBadge({ plan, title }: { plan: string; title?: string }): React.ReactElement {
  return (
    <span className="bl-lock" title={title ?? `Available on ${plan}`}>
      <LockKeyhole aria-hidden strokeWidth={2.4} />
      {plan}
    </span>
  );
}

export function SoonTag(): React.ReactElement {
  return <span className="bl-soon">Coming soon</span>;
}

// ── usage meter ─────────────────────────────────────────────────────────

export function UsageMeter({
  label,
  view,
  unit,
  verb = "used",
  showReset = true,
}: {
  label: string;
  view: QuotaView;
  unit?: string;
  verb?: string;
  showReset?: boolean;
}): React.ReactElement {
  const unlimited = view.limit === null;
  return (
    <div
      className={cn("bl-meter", `bl-meter--${view.tone}`, unlimited && "bl-meter--unlimited")}
      role="group"
      aria-label={label}
    >
      <div className="bl-meter-head">
        <span className="bl-meter-label">{label}</span>
        <span className="bl-meter-value">
          <strong>{num(view.used)}</strong>
          {unlimited ? ` ${verb}, no limit` : ` of ${num(view.limit as number)}${unit ? ` ${unit}` : ""}`}
        </span>
      </div>
      <div
        className="bl-meter-track"
        role="progressbar"
        aria-valuemin={0}
        aria-valuemax={view.limit ?? undefined}
        aria-valuenow={view.used}
        aria-valuetext={unlimited ? `${view.used} ${verb}` : `${view.used} of ${view.limit} ${verb}`}
      >
        {!unlimited ? <div className="bl-meter-fill" style={{ width: `${view.pct}%` }} /> : null}
      </div>
      {showReset && view.resetsAt ? (
        <div className="bl-meter-foot">
          {view.tone === "out" ? "None left. " : view.left !== null ? `${num(view.left)} left. ` : ""}
          Resets {fmtDate(view.resetsAt)}
        </div>
      ) : null}
    </div>
  );
}

/** A meter for a live-object limit (alerts): no reset, a slot frees when one is removed. */
export function LimitMeter({ label, used, limit }: { label: string; used: number; limit: number | null }): React.ReactElement {
  return <UsageMeter label={label} view={meter(used, limit)} verb="in use" showReset={false} />;
}

// ── credits indicator (compact) ─────────────────────────────────────────

export function CreditsPill({ view, onUpgrade }: { view: QuotaView; onUpgrade?: () => void }): React.ReactElement | null {
  if (view.limit === null) return null;
  const r = 6.5;
  const c = 2 * Math.PI * r;
  const leftPct = view.limit ? (view.left ?? 0) / view.limit : 0;
  return (
    <span className={cn("bl-credits", view.tone !== "ok" && `bl-credits--${view.tone}`)} aria-live="polite">
      <svg className="bl-credits-ring" viewBox="0 0 16 16" aria-hidden>
        <circle cx="8" cy="8" r={r} fill="none" stroke="var(--bl-track)" strokeWidth="2.2" />
        <circle
          cx="8" cy="8" r={r} fill="none" stroke="currentColor" strokeWidth="2.2"
          strokeDasharray={`${c * leftPct} ${c}`} strokeLinecap="round" transform="rotate(-90 8 8)"
        />
      </svg>
      {view.tone === "out" ? "No credits left" : `${num(view.left ?? 0)} of ${num(view.limit)} credits left`}
      {onUpgrade && view.tone !== "ok" ? (
        <button type="button" className="bl-link" onClick={onUpgrade}>
          Upgrade
        </button>
      ) : null}
    </span>
  );
}

// ── notices ─────────────────────────────────────────────────────────────

export type NoticeTone = "info" | "warn" | "bad" | "good" | "ink";

const ICON: Record<NoticeTone, React.ComponentType<{ className?: string; "aria-hidden"?: boolean }>> = {
  info: Info,
  warn: AlertTriangle,
  bad: XCircle,
  good: CheckCircle2,
  ink: Lock,
};

export function Notice({
  tone = "info",
  title,
  children,
  actions,
  onDismiss,
  role,
}: {
  tone?: NoticeTone;
  title?: React.ReactNode;
  children?: React.ReactNode;
  actions?: React.ReactNode;
  onDismiss?: () => void;
  role?: "status" | "alert";
}): React.ReactElement {
  const Icon = ICON[tone];
  return (
    <div className={cn("bl-notice", tone !== "info" && `bl-notice--${tone}`)} role={role ?? (tone === "bad" ? "alert" : "status")}>
      <Icon className="bl-notice-icon" aria-hidden />
      <div className="bl-notice-body">
        {title ? <strong>{title}</strong> : null}
        {children}
      </div>
      {actions ? <div className="bl-notice-actions">{actions}</div> : null}
      {onDismiss ? (
        <button type="button" className="bl-x" aria-label="Dismiss" onClick={onDismiss} style={{ width: 28, height: 28 }}>
          <X size={16} />
        </button>
      ) : null}
    </div>
  );
}

// ── trust row ───────────────────────────────────────────────────────────

export function TrustRow(): React.ReactElement {
  return (
    <div className="bl-trust">
      <span>
        <ShieldCheck aria-hidden /> Payments secured by Razorpay
      </span>
      <span>
        <LockKeyhole aria-hidden /> Pivot never stores card or UPI details
      </span>
      <span>
        <RefreshCw aria-hidden /> Cancel any time, keep access to the period end
      </span>
    </div>
  );
}

// ── policy text with visible placeholders ───────────────────────────────

export function PolicyText({ parts }: { parts: Part[] }): React.ReactElement {
  return (
    <>
      {parts.map((p, i) =>
        typeof p === "string" ? (
          <React.Fragment key={i}>{p}</React.Fragment>
        ) : (
          <span key={i} className="bl-tbd">
            {p.tbd}
          </span>
        ),
      )}
    </>
  );
}

// ── loading / empty / error ─────────────────────────────────────────────

export function SkeletonRows({ rows = 3 }: { rows?: number }): React.ReactElement {
  return (
    <div className="bl-stack" aria-busy="true" aria-label="Loading">
      {Array.from({ length: rows }, (_, i) => (
        <span key={i} className="bl-skel" style={{ width: `${88 - i * 14}%` }} />
      ))}
    </div>
  );
}

export function EmptyState({
  icon,
  title,
  children,
  action,
}: {
  icon?: React.ReactNode;
  title: string;
  children?: React.ReactNode;
  action?: React.ReactNode;
}): React.ReactElement {
  return (
    <div className="bl-empty">
      {icon}
      <div className="bl-h3">{title}</div>
      {children ? <p className="bl-small" style={{ maxWidth: 380 }}>{children}</p> : null}
      {action}
    </div>
  );
}

export function ErrorState({ message, onRetry }: { message: string; onRetry?: () => void }): React.ReactElement {
  return (
    <Notice
      tone="bad"
      title="Couldn't load this"
      actions={
        onRetry ? (
          <button type="button" className="bl-btn bl-btn--secondary bl-btn--sm" onClick={onRetry}>
            Try again
          </button>
        ) : undefined
      }
    >
      {message}
    </Notice>
  );
}
