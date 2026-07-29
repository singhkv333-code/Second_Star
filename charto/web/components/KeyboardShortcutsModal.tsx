"use client";

/**
 * KeyboardShortcutsModal — Claude-style "Keyboard shortcuts" panel.
 *
 * A controlled Radix Dialog (same overlay/scrim/animation pattern as
 * CommandPalette) listing every shortcut grouped by context. The shortcut
 * data comes from lib/shortcuts.ts so the panel and the real handlers never
 * drift. Opened via the account menu's "Keyboard shortcuts" item or the
 * global Ctrl/⌘ + / hotkey (both wired in AppShell). Esc / click-outside
 * close.
 */

import { useEffect, useMemo, useState } from "react";
import { X } from "lucide-react";
import { Dialog } from "@/components/ui/dialog";
import * as DialogPrimitive from "@radix-ui/react-dialog";
import { cn } from "@/lib/utils";
import {
  CHORD_GLUE,
  getShortcutGroups,
  isMacPlatform,
  type Shortcut,
} from "@/lib/shortcuts";

export type KeyboardShortcutsModalProps = {
  open: boolean;
  onOpenChange: (open: boolean) => void;
};

export function KeyboardShortcutsModal({
  open,
  onOpenChange,
}: KeyboardShortcutsModalProps): React.ReactElement {
  // Platform is only known client-side; default to non-mac for SSR and
  // re-resolve on mount so the glyphs are correct before the user opens it.
  const [mac, setMac] = useState(false);
  useEffect(() => setMac(isMacPlatform()), []);

  const groups = useMemo(() => getShortcutGroups(mac), [mac]);

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogPrimitive.Portal>
        <DialogPrimitive.Overlay
          className={cn(
            "fixed inset-0 z-50 bg-black/40 backdrop-blur-sm",
            "data-[state=open]:animate-in data-[state=closed]:animate-out",
            "data-[state=closed]:fade-out-0 data-[state=open]:fade-in-0",
          )}
        />
        <DialogPrimitive.Content
          data-testid="keyboard-shortcuts-modal"
          className={cn(
            "fixed left-1/2 top-1/2 z-50 flex max-h-[85vh] w-full max-w-md -translate-x-1/2 -translate-y-1/2 flex-col",
            "overflow-hidden rounded-2xl border bg-popover shadow-2xl",
            "data-[state=open]:animate-in data-[state=closed]:animate-out",
            "data-[state=closed]:fade-out-0 data-[state=open]:fade-in-0",
            "data-[state=closed]:zoom-out-95 data-[state=open]:zoom-in-95",
          )}
          aria-describedby={undefined}
        >
          {/* Header */}
          <div
            className="flex shrink-0 items-center justify-between"
            style={{ padding: "22px 28px 18px" }}
          >
            <DialogPrimitive.Title
              style={{
                fontFamily: "var(--font-display, var(--font-ui))",
                fontSize: 20,
                fontWeight: 700,
                letterSpacing: "-0.01em",
                color: "var(--text-primary)",
              }}
            >
              Keyboard shortcuts
            </DialogPrimitive.Title>
            <DialogPrimitive.Close
              aria-label="Close"
              className="inline-flex items-center justify-center"
              style={{
                width: 30,
                height: 30,
                marginRight: -4,
                background: "transparent",
                border: "none",
                borderRadius: "999px",
                color: "var(--text-tertiary)",
                cursor: "pointer",
                transition:
                  "color 0.18s var(--ease-quartr), background-color 0.18s var(--ease-quartr)",
              }}
              onMouseEnter={(e) => {
                e.currentTarget.style.background = "var(--surface-hover)";
                e.currentTarget.style.color = "var(--text-primary)";
              }}
              onMouseLeave={(e) => {
                e.currentTarget.style.background = "transparent";
                e.currentTarget.style.color = "var(--text-tertiary)";
              }}
            >
              <X size={18} strokeWidth={2} aria-hidden={true} />
            </DialogPrimitive.Close>
          </div>

          {/* Groups — single roomy column, divider under every row. */}
          <div
            className="min-h-0 flex-1 overflow-y-auto"
            style={{ padding: "0 28px 24px" }}
          >
            {groups.map((group) => (
              <section key={group.heading}>
                <h3
                  style={{
                    margin: "18px 0 4px",
                    fontFamily: "var(--font-ui)",
                    fontSize: 15,
                    fontWeight: 600,
                    letterSpacing: "-0.005em",
                    color: "var(--text-primary)",
                  }}
                >
                  {group.heading}
                </h3>
                {group.items.map((item) => (
                  <ShortcutRow key={item.id} item={item} />
                ))}
              </section>
            ))}
          </div>
        </DialogPrimitive.Content>
      </DialogPrimitive.Portal>
    </Dialog>
  );
}

function ShortcutRow({ item }: { item: Shortcut }): React.ReactElement {
  return (
    <div
      className="flex items-center justify-between"
      style={{
        gap: 16,
        padding: "13px 4px",
        borderBottom: "1px solid var(--glass-border)",
      }}
    >
      <span
        style={{
          fontFamily: "var(--font-ui)",
          fontSize: 14,
          color: "var(--text-secondary)",
        }}
      >
        {item.label}
      </span>
      <span className="inline-flex shrink-0 items-center" style={{ gap: 5 }}>
        {item.keys.map((k, i) =>
          k === CHORD_GLUE ? (
            <span
              key={i}
              style={{ fontSize: 12.5, color: "var(--text-tertiary)", padding: "0 2px" }}
            >
              then
            </span>
          ) : (
            <Kbd key={i}>{k}</Kbd>
          ),
        )}
      </span>
    </div>
  );
}

function Kbd({ children }: { children: React.ReactNode }): React.ReactElement {
  return (
    <kbd
      className="inline-flex items-center justify-center tabular-nums"
      style={{
        minWidth: 30,
        height: 30,
        padding: "0 9px",
        background: "var(--bg-elevated)",
        border: "none",
        borderRadius: "8px",
        color: "var(--text-primary)",
        fontFamily: "var(--font-ui)",
        fontSize: 13,
        fontWeight: 500,
        lineHeight: 1,
      }}
    >
      {children}
    </kbd>
  );
}
