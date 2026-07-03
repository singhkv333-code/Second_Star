"use client";

/**
 * ContentOverlay — a "full screen" surface that fills ONLY the chat/content
 * area: it starts below the top header (`--header-h`) and to the right of the
 * 240px left nav on lg+ (full width below lg, where the nav is a drawer). The
 * top bar and sidebar stay visible and interactive — the overlay never covers
 * them. Opaque, so no scrim is needed; closes on Esc or the caller's button.
 *
 * Used by the full-screen Option Strategy builder and the full-screen Option
 * Chain. Kept dumb on purpose — the caller owns header/body/footer.
 */

import { useEffect } from "react";
import { cn } from "@/lib/utils";

export function ContentOverlay({
  open,
  onClose,
  label,
  children,
}: {
  open: boolean;
  onClose: () => void;
  label: string;
  children: React.ReactNode;
}): React.ReactElement | null {
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent): void => {
      if (e.key === "Escape") {
        e.stopPropagation();
        onClose();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  if (!open) return null;

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label={label}
      style={{ top: "calc(var(--header-h, 56px) + var(--paper-banner-h, 0px))", right: 0, bottom: 0 }}
      className={cn(
        "content-overlay fixed z-40 flex flex-col overflow-hidden bg-background",
        "animate-in fade-in-0 slide-in-from-bottom-2 duration-200",
      )}
    >
      {children}
    </div>
  );
}
