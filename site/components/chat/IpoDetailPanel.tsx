"use client";

/**
 * IpoDetailPanel — read-only "Know more" sidebar opened from the "Know more"
 * button on an IpoListCard row. Shows a qualitative overview of the company —
 * what it does, who founded it and when, and its key strengths & risks — NOT
 * the financial/bid facts (those live on the row and in the apply editor).
 * The sticky bottom CTA is "Apply →", which hands off to the apply editor
 * (IpoApplicationPanel).
 *
 * Visually identical shell to IpoApplicationPanel / the Agent editor: a
 * full-height scrim + right-anchored panel pinned to the same width
 * (`clamp(340px, 25vw, 520px)`) with the same slide-in. Reuses the
 * `.agent-panel-shell` / `.agent-panel-backdrop` classes for the <lg
 * responsive overrides.
 */

import { useEffect, useRef } from "react";
import { AlertTriangle, ArrowRight, Check, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { useExclusiveSidePanel } from "@/lib/sidePanels";
import type { IpoApplicationPayload } from "@/lib/types";

const PANEL_WIDTH = "clamp(340px, 25vw, 520px)";

/** Qualitative company profile shown in the "Know more" drawer. In production
 *  this would come from the backend; the sandbox supplies mock values. */
export type IpoCompanyInfo = {
  about: string;
  founder: string;
  foundedYear: number;
  strengths: string[];
  risks: string[];
};

export type IpoDetailPanelProps = {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** The IPO whose details to show. */
  payload: IpoApplicationPayload | null;
  /** Qualitative company profile. When absent the panel shows a graceful note. */
  info?: IpoCompanyInfo | null;
  /** Called when the bottom "Apply" CTA is tapped — opens the apply editor. */
  onApply: (symbol: string) => void;
};

function statusLabel(status: IpoApplicationPayload["status"]): string {
  return status === "open" ? "Open" : status === "upcoming" ? "Upcoming" : "Closed";
}

function statusColor(status: IpoApplicationPayload["status"]): string {
  return status === "open"
    ? "text-emerald-600 dark:text-emerald-300"
    : status === "upcoming"
      ? "text-blue-600 dark:text-blue-300"
      : "text-slate-500 dark:text-slate-400";
}

// ---------------------------------------------------------------------------
// Panel
// ---------------------------------------------------------------------------

export function IpoDetailPanel({
  open,
  onOpenChange,
  payload,
  info,
  onApply,
}: IpoDetailPanelProps): React.ReactElement | null {
  const panelRef = useRef<HTMLElement | null>(null);
  useExclusiveSidePanel("ipo-detail", open, () => onOpenChange(false));

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent): void => {
      if (e.key === "Escape") {
        e.stopPropagation();
        onOpenChange(false);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onOpenChange]);

  if (!open || !payload) return null;

  const isClosed = payload.status === "closed";

  return (
    <>
      {/* NON-MODAL: transparent, click-through layer — no dark scrim. The chat
          stays visible and interactive; closing is via the X / Esc only. */}
      <div
        aria-hidden="true"
        className="agent-panel-backdrop fixed inset-0 z-40 pointer-events-none"
        data-testid="ipo-detail-panel-backdrop"
      />
      <aside
        ref={panelRef}
        role="dialog"
        aria-label="IPO details"
        aria-modal="false"
        style={{
          width: PANEL_WIDTH,
          maxWidth: "100%",
          top: 0,
          animation: "agentPanelIn-quartr 300ms cubic-bezier(0.22, 1, 0.36, 1) both",
        }}
        className={cn("agent-panel-shell fixed bottom-0 right-0 z-50 flex border-l bg-background shadow-xl")}
        data-testid="ipo-detail-panel"
      >
        <div className="flex h-full w-full flex-col">
          {/* Header */}
          <div className="flex items-center justify-between px-4 py-3">
            <span
              style={{
                fontFamily: "var(--font-display)",
                fontWeight: "var(--weight-display)" as unknown as number,
                fontSize: 18,
                letterSpacing: "-0.02em",
                color: "var(--text-primary)",
              }}
            >
              About the company
            </span>
            <Button
              variant="ghost"
              size="icon"
              aria-label="Close IPO details"
              onClick={() => onOpenChange(false)}
              className="rounded-full"
            >
              <X className="h-4 w-4" aria-hidden="true" />
            </Button>
          </div>

          {/* Scrolling body */}
          <div className="flex-1 overflow-y-auto px-5 pb-5">
            {/* Title block */}
            <div className="flex items-start justify-between gap-3 pt-1">
              <div className="min-w-0">
                <h3 className="text-[16px] leading-[1.25] font-semibold tracking-tight text-foreground">
                  {payload.name}
                </h3>
                <p className="mt-1 flex items-center gap-1.5 text-[11.5px] text-muted-foreground">
                  <span className="font-medium text-foreground/70">{payload.symbol}</span>
                  <span className="text-muted-foreground/40">·</span>
                  <span className="capitalize">{payload.type} IPO</span>
                </p>
              </div>
              <span className={cn("shrink-0 text-[11.5px] font-medium", statusColor(payload.status))}>
                {statusLabel(payload.status)}
              </span>
            </div>

            {info ? (
              <>
                {/* Founder / Founded meta */}
                <div className="mt-4 grid grid-cols-2 gap-3 border-t border-border/40 pt-4">
                  <div className="flex flex-col gap-0.5">
                    <span className="text-[10.5px] font-medium uppercase tracking-[0.08em] text-muted-foreground/70">
                      Founder
                    </span>
                    <span className="text-[12.5px] font-medium text-foreground">{info.founder}</span>
                  </div>
                  <div className="flex flex-col gap-0.5">
                    <span className="text-[10.5px] font-medium uppercase tracking-[0.08em] text-muted-foreground/70">
                      Founded
                    </span>
                    <span className="text-[12.5px] font-medium tabular-nums text-foreground">
                      {info.foundedYear}
                    </span>
                  </div>
                </div>

                {/* About */}
                <div className="mt-4 flex flex-col gap-1.5 border-t border-border/40 pt-4">
                  <span className="text-[11px] font-medium uppercase tracking-[0.08em] text-muted-foreground/80">
                    About
                  </span>
                  <p className="text-[12.5px] leading-relaxed text-muted-foreground">{info.about}</p>
                </div>

                {/* Strengths */}
                {info.strengths.length > 0 && (
                  <div className="mt-4 flex flex-col gap-2 border-t border-border/40 pt-4">
                    <span className="text-[11px] font-medium uppercase tracking-[0.08em] text-muted-foreground/80">
                      Strengths
                    </span>
                    <ul className="flex flex-col gap-1.5">
                      {info.strengths.map((s, i) => (
                        <li key={i} className="flex items-start gap-2 text-[12.5px] leading-snug text-foreground/90">
                          <Check
                            className="mt-0.5 h-3.5 w-3.5 shrink-0 text-emerald-600 dark:text-emerald-300"
                            strokeWidth={2.5}
                            aria-hidden="true"
                          />
                          {s}
                        </li>
                      ))}
                    </ul>
                  </div>
                )}

                {/* Risks */}
                {info.risks.length > 0 && (
                  <div className="mt-4 flex flex-col gap-2 border-t border-border/40 pt-4">
                    <span className="text-[11px] font-medium uppercase tracking-[0.08em] text-muted-foreground/80">
                      Risks
                    </span>
                    <ul className="flex flex-col gap-1.5">
                      {info.risks.map((r, i) => (
                        <li key={i} className="flex items-start gap-2 text-[12.5px] leading-snug text-foreground/90">
                          <AlertTriangle
                            className="mt-0.5 h-3.5 w-3.5 shrink-0 text-amber-600 dark:text-amber-300"
                            strokeWidth={2.25}
                            aria-hidden="true"
                          />
                          {r}
                        </li>
                      ))}
                    </ul>
                  </div>
                )}
              </>
            ) : (
              <p className="mt-4 border-t border-border/40 pt-4 text-[12.5px] leading-relaxed text-muted-foreground">
                Company profile isn&apos;t available for this issue yet.
              </p>
            )}
          </div>

          {/* Sticky footer CTA — Apply → opens the apply editor */}
          <div className="border-t border-border/40 px-5 py-3.5">
            <button
              type="button"
              onClick={() => onApply(payload.symbol)}
              disabled={isClosed}
              data-testid="ipo-detail-apply-button"
              className={cn(
                "inline-flex h-10 w-full items-center justify-center gap-1.5 rounded-full bg-primary text-[13px] font-medium tracking-tight text-primary-foreground transition-all",
                "hover:bg-primary/90 active:scale-[0.98]",
                "disabled:cursor-not-allowed disabled:opacity-40",
              )}
            >
              {isClosed ? "Issue closed" : "Apply"}
              {!isClosed && <ArrowRight className="h-4 w-4 shrink-0" aria-hidden="true" />}
            </button>
          </div>
        </div>
      </aside>
    </>
  );
}
