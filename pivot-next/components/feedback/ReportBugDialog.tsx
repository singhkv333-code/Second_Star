"use client";

/**
 * ReportBugDialog — the "Report a bug" widget, opened from the account menu's
 * Help submenu.
 *
 * A compact, single-screen form: pick a category, give it a one-line title,
 * describe what happened, set severity. We auto-capture lightweight client
 * context (current page/tab, viewport, user agent, app version) so the report
 * is actionable without making the user type it. Submits to POST /feedback via
 * `submitBugReport`; shows honest submitting / success / error states.
 *
 * Mount once near the app root (AppShell) and drive `open` from the menu.
 */

import { useEffect, useMemo, useRef, useState } from "react";
import {
  AlertCircle,
  Bug,
  Check,
  Database,
  Gauge,
  Loader2,
  LayoutDashboard,
  HelpCircle,
} from "lucide-react";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Button } from "@/components/ui/button";
import {
  submitBugReport,
  type BugReportCategory,
  type BugReportSeverity,
  type BugReportContext,
} from "@/lib/api";
import { isError } from "@/lib/types";

const CATEGORIES: {
  value: BugReportCategory;
  label: string;
  icon: React.ComponentType<{ size?: number; strokeWidth?: number; className?: string }>;
}[] = [
  { value: "bug", label: "Bug", icon: Bug },
  { value: "data", label: "Wrong data", icon: Database },
  { value: "ui", label: "UI / layout", icon: LayoutDashboard },
  { value: "performance", label: "Slow", icon: Gauge },
  { value: "other", label: "Other", icon: HelpCircle },
];

const SEVERITIES: { value: BugReportSeverity; label: string }[] = [
  { value: "low", label: "Low" },
  { value: "normal", label: "Normal" },
  { value: "high", label: "High" },
  { value: "critical", label: "Critical" },
];

const MAX_DESC = 4000;

type SubmitState =
  | { kind: "idle" }
  | { kind: "submitting" }
  | { kind: "success"; id: string }
  | { kind: "error"; message: string };

export function ReportBugDialog({
  open,
  onOpenChange,
  /** Active tab label, captured into the report context. */
  currentTab,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  currentTab?: string;
}): React.ReactElement {
  const [category, setCategory] = useState<BugReportCategory>("bug");
  const [severity, setSeverity] = useState<BugReportSeverity>("normal");
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [submit, setSubmit] = useState<SubmitState>({ kind: "idle" });
  const titleRef = useRef<HTMLInputElement | null>(null);

  // Reset the form whenever the dialog opens fresh.
  useEffect(() => {
    if (!open) return;
    setCategory("bug");
    setSeverity("normal");
    setTitle("");
    setDescription("");
    setSubmit({ kind: "idle" });
    // Focus the title shortly after the open animation settles.
    const t = setTimeout(() => titleRef.current?.focus(), 80);
    return () => clearTimeout(t);
  }, [open]);

  // Auto-captured client context (recomputed only while the dialog is open).
  const context = useMemo<BugReportContext>(() => {
    if (typeof window === "undefined") return {};
    return {
      page: window.location.pathname + window.location.search,
      tab: currentTab,
      user_agent: window.navigator.userAgent,
      app_version: process.env.NEXT_PUBLIC_APP_VERSION || undefined,
      viewport: `${window.innerWidth}x${window.innerHeight}`,
    };
  }, [currentTab, open]); // eslint-disable-line react-hooks/exhaustive-deps

  const canSubmit =
    title.trim().length >= 3 &&
    description.trim().length > 0 &&
    submit.kind !== "submitting";

  const handleSubmit = async (): Promise<void> => {
    if (!canSubmit) return;
    setSubmit({ kind: "submitting" });
    const res = await submitBugReport({
      category,
      severity,
      title: title.trim(),
      description: description.trim(),
      context,
    });
    if (isError(res)) {
      setSubmit({ kind: "error", message: res.error.message });
      return;
    }
    setSubmit({ kind: "success", id: res.data.id });
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        className={
          // Desktop: centered floating card.
          "sm:max-w-[520px] sm:rounded-2xl " +
          // Mobile: don't take the whole screen — slide up as a bottom sheet
          // (anchored to the bottom, rounded top, capped height, internal scroll).
          // `max-sm:!` overrides the shared dialog's full-screen `inset-0`/`border-0`.
          "max-sm:!inset-x-0 max-sm:!top-auto max-sm:!bottom-0 " +
          "max-sm:h-auto max-sm:max-h-[90svh] " +
          "max-sm:rounded-t-2xl max-sm:!border-t " +
          "max-sm:data-[state=open]:animate-in max-sm:data-[state=open]:slide-in-from-bottom-4"
        }
        data-testid="report-bug-dialog"
      >
        {submit.kind === "success" ? (
          <SuccessState id={submit.id} onClose={() => onOpenChange(false)} />
        ) : (
          <div className="flex flex-col gap-4">
            <DialogHeader className="gap-1 space-y-0 text-left">
              <DialogTitle
                style={{
                  fontFamily: "var(--font-display)",
                  fontSize: 17,
                  fontWeight: 600,
                  letterSpacing: "-0.015em",
                }}
              >
                Report a bug
              </DialogTitle>
              <DialogDescription
                style={{ fontSize: 12.5, lineHeight: 1.5, color: "var(--text-tertiary)" }}
              >
                Tell us what went wrong. We attach your current screen and
                device automatically — no need to include it.
              </DialogDescription>
            </DialogHeader>

            {/* Category */}
            <Field label="What kind of issue?">
              <div className="flex flex-wrap gap-1.5">
                {CATEGORIES.map(({ value, label, icon: Icon }) => {
                  const active = category === value;
                  return (
                    <button
                      key={value}
                      type="button"
                      onClick={() => setCategory(value)}
                      aria-pressed={active}
                      className="inline-flex items-center gap-1.5 transition-colors"
                      style={{
                        padding: "5px 10px",
                        borderRadius: "var(--radius-pill)",
                        fontSize: 12,
                        fontWeight: 500,
                        cursor: "pointer",
                        border: `1px solid ${active ? "var(--text-primary)" : "var(--glass-border)"}`,
                        background: active ? "var(--text-primary)" : "transparent",
                        color: active ? "var(--bg-base)" : "var(--text-secondary)",
                      }}
                    >
                      <Icon size={13} strokeWidth={2} />
                      {label}
                    </button>
                  );
                })}
              </div>
            </Field>

            {/* Title */}
            <Field label="Title">
              <Input
                ref={titleRef}
                value={title}
                onChange={(e) => setTitle(e.target.value)}
                placeholder="e.g. RSI workflow didn't trigger a buy when TCS dropped below 30"
                maxLength={160}
                data-testid="report-bug-title"
              />
            </Field>

            {/* Description */}
            <Field
              label="What happened?"
              hint={`${description.length}/${MAX_DESC}`}
            >
              <Textarea
                value={description}
                onChange={(e) => setDescription(e.target.value.slice(0, MAX_DESC))}
                placeholder={
                  "What did you do, what did you expect, and what happened instead?\nInclude the ticker / strategy / steps if relevant."
                }
                className="min-h-[110px] resize-y"
                data-testid="report-bug-description"
              />
            </Field>

            {/* Severity */}
            <Field label="How bad is it?">
              <div
                className="inline-flex w-full"
                role="radiogroup"
                aria-label="Severity"
                style={{
                  gap: 2,
                  padding: 2,
                  background: "var(--bg-base)",
                  border: "1px solid var(--glass-border)",
                  borderRadius: "var(--radius-pill)",
                }}
              >
                {SEVERITIES.map(({ value, label }) => {
                  const active = severity === value;
                  return (
                    <button
                      key={value}
                      type="button"
                      role="radio"
                      aria-checked={active}
                      onClick={() => setSeverity(value)}
                      className="flex-1 transition-colors"
                      style={{
                        padding: "5px 10px",
                        border: "none",
                        borderRadius: "var(--radius-pill)",
                        fontSize: 12,
                        fontWeight: 500,
                        cursor: "pointer",
                        background: active ? "var(--text-primary)" : "transparent",
                        color: active ? "var(--bg-base)" : "var(--text-secondary)",
                      }}
                    >
                      {label}
                    </button>
                  );
                })}
              </div>
            </Field>

            {/* Auto-captured context */}
            <ContextPreview context={context} />

            {submit.kind === "error" && (
              <p
                role="alert"
                className="flex items-start gap-2 rounded-lg px-3 py-2"
                style={{
                  fontSize: 12,
                  background: "color-mix(in srgb, var(--color-loss) 10%, transparent)",
                  color: "var(--color-loss)",
                }}
              >
                <AlertCircle size={14} strokeWidth={2} className="mt-0.5 shrink-0" aria-hidden />
                Couldn&apos;t send your report: {submit.message}. Please try again.
              </p>
            )}

            {/* Actions */}
            <div className="flex items-center justify-end gap-2 pt-1">
              <Button
                type="button"
                variant="ghost"
                onClick={() => onOpenChange(false)}
                disabled={submit.kind === "submitting"}
              >
                Cancel
              </Button>
              <Button
                type="button"
                onClick={() => void handleSubmit()}
                disabled={!canSubmit}
                data-testid="report-bug-submit"
              >
                {submit.kind === "submitting" ? (
                  <>
                    <Loader2 className="animate-spin" aria-hidden />
                    Sending…
                  </>
                ) : (
                  "Send report"
                )}
              </Button>
            </div>
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}

// ---------------------------------------------------------------------------
// Bits
// ---------------------------------------------------------------------------

function Field({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: string;
  children: React.ReactNode;
}): React.ReactElement {
  return (
    <div className="flex flex-col gap-1.5">
      <div className="flex items-baseline justify-between">
        <span
          style={{
            fontSize: 12,
            fontWeight: 600,
            color: "var(--text-secondary)",
            letterSpacing: "-0.005em",
          }}
        >
          {label}
        </span>
        {hint && (
          <span style={{ fontSize: 11, color: "var(--text-tertiary)", fontVariantNumeric: "tabular-nums" }}>
            {hint}
          </span>
        )}
      </div>
      {children}
    </div>
  );
}

function ContextPreview({ context }: { context: BugReportContext }): React.ReactElement {
  const chips = [
    context.tab && `Tab: ${context.tab}`,
    context.page && `Page: ${context.page}`,
    context.viewport && `Viewport: ${context.viewport}`,
  ].filter(Boolean) as string[];

  return (
    <div className="flex flex-wrap items-center gap-x-2 gap-y-1.5">
      <span
        style={{
          fontSize: 11,
          fontWeight: 600,
          color: "var(--text-tertiary)",
          letterSpacing: "0.02em",
        }}
      >
        Attached
      </span>
      {chips.map((c) => (
        <span
          key={c}
          className="inline-flex items-center"
          style={{
            color: "var(--text-secondary)",
            fontSize: 11,
            fontWeight: 500,
            maxWidth: "100%",
            overflow: "hidden",
            textOverflow: "ellipsis",
            whiteSpace: "nowrap",
          }}
        >
          {c}
          {c !== chips[chips.length - 1] && (
            <span aria-hidden style={{ color: "var(--text-tertiary)", marginLeft: 8, opacity: 0.5 }}>
              ·
            </span>
          )}
        </span>
      ))}
    </div>
  );
}

function SuccessState({ id, onClose }: { id: string; onClose: () => void }): React.ReactElement {
  return (
    <div className="flex flex-col items-center gap-3 py-6 text-center" data-testid="report-bug-success">
      <span
        aria-hidden
        className="inline-flex h-12 w-12 items-center justify-center rounded-full"
        style={{
          background: "color-mix(in srgb, var(--color-profit) 14%, transparent)",
          color: "var(--color-profit)",
        }}
      >
        <Check size={24} strokeWidth={2.5} />
      </span>
      <DialogTitle
        style={{ fontFamily: "var(--font-display)", fontSize: 17, fontWeight: 600 }}
      >
        Thanks — report sent
      </DialogTitle>
      <DialogDescription style={{ fontSize: 12.5, color: "var(--text-tertiary)", maxWidth: 340 }}>
        We logged it as{" "}
        <span style={{ fontFamily: "var(--font-mono)", color: "var(--text-secondary)" }}>
          #{id}
        </span>
        . If we need more detail we&apos;ll reach out on your account email.
      </DialogDescription>
      <Button type="button" onClick={onClose} className="mt-1">
        Done
      </Button>
    </div>
  );
}
