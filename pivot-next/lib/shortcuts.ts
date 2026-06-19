/**
 * shortcuts.ts — single source of truth for Pivot's keyboard shortcuts.
 *
 * The same registry drives BOTH the visible KeyboardShortcutsModal and the
 * documentation contract for the global handlers wired in AppShell /
 * ChatDemo. Keep this list in lock-step with those handlers — if a shortcut
 * is shown here it must actually fire, and vice-versa.
 *
 * Key tokens render as <kbd> caps EXCEPT the literal "then" (chord glue,
 * rendered as muted text — e.g. "G then C").
 */

export const CHORD_GLUE = "then";

export type Shortcut = {
  /** Stable id (also used as React key). */
  id: string;
  /** Human-readable action. */
  label: string;
  /** Ordered key caps. Use CHORD_GLUE between sequential presses. */
  keys: string[];
};

export type ShortcutGroup = {
  heading: string;
  items: Shortcut[];
};

/** True on macOS — picks ⌘/⇧ glyphs over Ctrl/Shift words. */
export function isMacPlatform(): boolean {
  if (typeof navigator === "undefined") return false;
  const p =
    (navigator as Navigator & { userAgentData?: { platform?: string } })
      .userAgentData?.platform ||
    navigator.platform ||
    "";
  return /mac|iphone|ipad|ipod/i.test(p);
}

/**
 * Build the grouped shortcut list for the current platform. `mac` flips the
 * modifier glyphs; everything else is platform-agnostic.
 */
export function getShortcutGroups(mac: boolean): ShortcutGroup[] {
  const mod = mac ? "⌘" : "Ctrl";
  // Shift always renders as the ⇧ glyph (matches the reference layout and
  // keeps the key caps uniform-width next to the longer "Ctrl" cap).
  const shift = "⇧";

  return [
    {
      heading: "General",
      items: [
        { id: "palette", label: "Command palette / search", keys: [mod, "K"] },
        { id: "shortcuts", label: "Keyboard shortcuts", keys: [mod, "/"] },
        { id: "sidebar", label: "Toggle sidebar", keys: [mod, "B"] },
        { id: "new-chat", label: "New chat", keys: [mod, shift, "O"] },
        { id: "close", label: "Close dialog / menu", keys: ["Esc"] },
      ],
    },
    {
      heading: "Navigation",
      items: [
        { id: "go-chat", label: "Go to Chat", keys: ["G", CHORD_GLUE, "C"] },
        { id: "go-portfolio", label: "Go to Portfolio", keys: ["G", CHORD_GLUE, "P"] },
        { id: "go-agents", label: "Go to Agents", keys: ["G", CHORD_GLUE, "A"] },
        { id: "go-screener", label: "Go to Screener", keys: ["G", CHORD_GLUE, "S"] },
        { id: "go-calendar", label: "Go to Calendar", keys: ["G", CHORD_GLUE, "L"] },
      ],
    },
    {
      heading: "Chat",
      items: [
        { id: "send", label: "Send message", keys: ["Enter"] },
        { id: "newline", label: "New line", keys: [shift, "Enter"] },
        { id: "focus", label: "Focus composer", keys: ["/"] },
        { id: "stop", label: "Stop response", keys: ["Esc"] },
      ],
    },
    {
      heading: "Workflow editor",
      items: [
        { id: "save", label: "Save step / draft", keys: [mod, "Enter"] },
        { id: "close-editor", label: "Close editor", keys: ["Esc"] },
      ],
    },
  ];
}

/** Hash-tab targets for the "G then <key>" navigation chord. */
export const CHORD_NAV_MAP: Record<string, string> = {
  c: "chat",
  p: "portfolio",
  a: "agents",
  s: "screener",
  l: "calendar",
};
