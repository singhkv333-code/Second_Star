"use client";

/**
 * ContentOverlay — a full-height content surface whose desktop left edge
 * follows the live sidebar width. On mobile it spans the viewport because
 * navigation is a drawer. Opaque, with no scrim needed.
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
      style={{ top: "calc(var(--safe-top, 0px) + var(--header-h, 56px) + var(--paper-banner-h, 0px))", right: 0, bottom: 0 }}
      className={cn(
        "content-overlay fixed z-40 flex flex-col overflow-hidden bg-background",
        "animate-in fade-in-0 slide-in-from-bottom-2 duration-200",
      )}
    >
      {children}
    </div>
  );
}
