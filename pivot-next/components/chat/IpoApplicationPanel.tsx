"use client";

/**
 * IpoApplicationPanel — right-side modal drawer that hosts the IPO
 * application form. Opened from the "Apply" button on an IpoListCard row.
 *
 * Visually identical to the Agent editor (AgentPanel) and Backtest sheet:
 * a full-height scrim + right-anchored panel pinned to the SAME width
 * (`clamp(340px, 25vw, 520px)`) and the same `agentPanelIn-quartr` slide-in.
 * Reuses the `.agent-panel-shell` / `.agent-panel-backdrop` classes so the
 * <lg responsive overrides (100vw / tablet 50vw) apply automatically.
 *
 * The panel is pure chrome — its body renders IpoApplicationCard, which
 * already owns the editable form, validation, and register/withdraw flow.
 */

import { useEffect, useRef } from "react";
import { X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { IpoApplicationCard } from "@/components/chat/IpoApplicationCard";
import type { IpoApplicationPayload } from "@/lib/types";

// Pinned to AgentPanel.PANEL_WIDTH / the Backtest sheet so all three side
// panels are always exactly the same width on any screen.
const PANEL_WIDTH = "clamp(340px, 25vw, 520px)";

export type IpoApplicationPanelProps = {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** The IPO application payload to render inside the panel. */
  payload: IpoApplicationPayload | null;
  /** Forwarded to IpoApplicationCard (open-day reminder CTA). */
  onSetupReminders?: (symbol: string) => void;
  /** Entrance animation. "slide" (default) eases in from the right edge.
   *  "fade" cross-fades in place — used when handing off from the "Know more"
   *  drawer, which already occupies the same slot, so the panel doesn't
   *  appear to slide out and back in. */
  entrance?: "slide" | "fade";
  /** When true, this panel renders WITHOUT its own scrim — used during a
   *  hand-off while the previous drawer's scrim is still up, so the two don't
   *  stack and darken (which reads as a background flicker). */
  suppressBackdrop?: boolean;
};

export function IpoApplicationPanel({
  open,
  onOpenChange,
  payload,
  onSetupReminders,
  entrance = "slide",
  suppressBackdrop = false,
}: IpoApplicationPanelProps): React.ReactElement | null {
  const panelRef = useRef<HTMLElement | null>(null);

  // Esc-to-close, matching AgentPanel.
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

  const isFade = entrance === "fade";

  return (
    <>
      {!suppressBackdrop && (
        <div
          aria-hidden="true"
          onClick={() => onOpenChange(false)}
          className={cn(
            "agent-panel-backdrop fixed inset-0 z-40 bg-black/60",
            // On a handoff the previous drawer's scrim is already up, so skip
            // the fade-in — the scrim swaps seamlessly (same opacity) instead
            // of fading and briefly doubling/darkening.
            !isFade && "animate-in fade-in-0",
          )}
          data-testid="ipo-application-panel-backdrop"
        />
      )}
      <aside
        ref={panelRef}
        role="dialog"
        aria-label="Apply to IPO"
        aria-modal="true"
        style={{
          width: PANEL_WIDTH,
          maxWidth: "100%",
          top: 0,
          animation: isFade
            ? "panelFadeIn-quartr 240ms ease-out both"
            : "agentPanelIn-quartr 300ms cubic-bezier(0.22, 1, 0.36, 1) both",
        }}
        className={cn(
          "agent-panel-shell fixed bottom-0 right-0 z-50 flex border-l bg-background shadow-xl",
        )}
        data-testid="ipo-application-panel"
      >
        <div className="flex h-full w-full flex-col">
          {/* Header — title + close X, matching the Agent panel header. */}
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
              Apply to IPO
            </span>
            <Button
              variant="ghost"
              size="icon"
              aria-label="Close IPO application panel"
              onClick={() => onOpenChange(false)}
              className="rounded-full"
            >
              <X className="h-4 w-4" aria-hidden="true" />
            </Button>
          </div>

          {/* Body — scrolls; hosts the application form full-bleed. The card
              owns its own horizontal padding in panel variant, so no px here. */}
          <div className="flex-1 overflow-y-auto">
            <IpoApplicationCard
              payload={payload}
              onSetupReminders={onSetupReminders}
              variant="panel"
            />
          </div>
        </div>
      </aside>
    </>
  );
}
