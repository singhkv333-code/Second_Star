"use client";

/**
 * Pivot design system — primitives.
 *
 * The shared vocabulary of the Pivot identity, extracted from the
 * pivotnow.in landing page and the in-app Quartr token set (globals.css):
 *
 *   display  — Newsreader serif, weight 400–550, letter-spacing −0.04em,
 *              italic spans render in muted grey (--text-secondary side)
 *   ui       — Inter, −0.025em on titles, 13–15px body
 *   labels   — JetBrains Mono, 10.5–11px, uppercase, +0.08em tracking
 *   color    — monochrome ink/paper; the ONLY hue on the surface is the
 *              profit/loss/warn semantic set
 *   geometry — pill (9999px) for actions & tags, 16px for cards
 *
 * Every component reads theme CSS variables so it renders correctly in
 * light mode and inside any `.dark` subtree with zero prop changes.
 */

import * as React from "react";
import { cn } from "@/lib/utils";

/* ────────────────────────────────────────────────────────────────────
 * Typography
 * ──────────────────────────────────────────────────────────────────── */

type DisplaySize = "hero" | "section" | "panel" | "card";

const DISPLAY_SIZES: Record<DisplaySize, string> = {
  hero: "clamp(44px, 5vw, 72px)",
  section: "clamp(34px, 4vw, 56px)",
  panel: "clamp(28px, 3vw, 40px)",
  card: "clamp(22px, 2vw, 28px)",
};

/**
 * Serif display heading — the landing-page voice ("One message. That's
 * all investing takes."). Compose accents with <Display.Em>.
 */
export function Display({
  size = "section",
  as: Tag = "h2",
  className,
  style,
  children,
}: {
  size?: DisplaySize;
  as?: "h1" | "h2" | "h3" | "p" | "span";
  className?: string;
  style?: React.CSSProperties;
  children: React.ReactNode;
}) {
  return (
    <Tag
      className={className}
      style={{
        fontFamily: "var(--font-serif)",
        fontWeight: 400,
        fontSize: DISPLAY_SIZES[size],
        letterSpacing: "-0.04em",
        lineHeight: 1.04,
        color: "var(--text-primary)",
        margin: 0,
        ...style,
      }}
    >
      {children}
    </Tag>
  );
}

/** Muted italic accent inside a Display heading. */
function DisplayEm({ children }: { children: React.ReactNode }) {
  return (
    <em
      style={{
        fontStyle: "italic",
        color: "var(--text-secondary)",
        fontWeight: 400,
      }}
    >
      {children}
    </em>
  );
}
Display.Em = DisplayEm;

/** Inter UI title — card headers, panel titles. */
export function Title({
  size = 16,
  className,
  style,
  children,
}: {
  size?: 14 | 15 | 16 | 18 | 20;
  className?: string;
  style?: React.CSSProperties;
  children: React.ReactNode;
}) {
  return (
    <div
      className={className}
      style={{
        fontFamily: "var(--font-ui)",
        fontWeight: 600,
        fontSize: size,
        letterSpacing: "-0.025em",
        color: "var(--text-primary)",
        lineHeight: 1.35,
        ...style,
      }}
    >
      {children}
    </div>
  );
}

/** Body copy — quiet mid-grey, relaxed leading. */
export function Prose({
  size = 14,
  className,
  style,
  children,
}: {
  size?: 13 | 14 | 15;
  className?: string;
  style?: React.CSSProperties;
  children: React.ReactNode;
}) {
  return (
    <p
      className={className}
      style={{
        fontFamily: "var(--font-ui)",
        fontWeight: 400,
        fontSize: size,
        lineHeight: 1.7,
        color: "var(--text-secondary)",
        margin: 0,
        ...style,
      }}
    >
      {children}
    </p>
  );
}

/** Mono uppercase section eyebrow ("HOW IT WORKS", "AGENTS"). */
export function Eyebrow({
  className,
  style,
  children,
}: {
  className?: string;
  style?: React.CSSProperties;
  children: React.ReactNode;
}) {
  return (
    <div
      className={className}
      style={{
        fontFamily: "var(--font-mono)",
        fontSize: 11,
        fontWeight: 500,
        letterSpacing: "0.14em",
        textTransform: "uppercase",
        color: "var(--text-tertiary)",
        ...style,
      }}
    >
      {children}
    </div>
  );
}

/* ────────────────────────────────────────────────────────────────────
 * Tags & status
 * ──────────────────────────────────────────────────────────────────── */

export type MonoTagTone = "outline" | "fill" | "ink";

/**
 * The signature Pivot microlabel — JetBrains Mono, uppercase, pill.
 * Used for intent tags (ALERT / BACKTEST / AGENT), card categories,
 * trigger kinds on agent cards.
 */
export function MonoTag({
  tone = "outline",
  dot = false,
  className,
  children,
}: {
  tone?: MonoTagTone;
  /** Leading 4px status dot (inherits currentColor). */
  dot?: boolean;
  className?: string;
  children: React.ReactNode;
}) {
  const tones: Record<MonoTagTone, React.CSSProperties> = {
    outline: {
      color: "var(--text-secondary)",
      border: "1px solid var(--glass-border-hover)",
      background: "transparent",
    },
    fill: {
      color: "var(--text-primary)",
      border: "1px solid transparent",
      background: "var(--surface-active)",
    },
    ink: {
      color: "var(--bg-base)",
      border: "1px solid transparent",
      background: "var(--text-primary)",
    },
  };
  return (
    <span
      className={cn("inline-flex items-center gap-1.5", className)}
      style={{
        fontFamily: "var(--font-mono)",
        fontSize: 10.5,
        fontWeight: 500,
        letterSpacing: "0.08em",
        textTransform: "uppercase",
        lineHeight: 1,
        padding: "5px 9px",
        borderRadius: "var(--radius-pill)",
        whiteSpace: "nowrap",
        ...tones[tone],
      }}
    >
      {dot && (
        <span
          aria-hidden
          style={{
            width: 4,
            height: 4,
            borderRadius: "50%",
            background: "currentColor",
          }}
        />
      )}
      {children}
    </span>
  );
}

export type AgentState = "armed" | "running" | "paused" | "draft" | "error";

const STATE_STYLES: Record<
  AgentState,
  { label: string; color: string; pulse: boolean }
> = {
  armed: { label: "Armed", color: "var(--color-profit)", pulse: true },
  running: { label: "Running", color: "var(--color-profit)", pulse: true },
  paused: { label: "Paused", color: "var(--color-warn)", pulse: false },
  draft: { label: "Draft", color: "var(--text-tertiary)", pulse: false },
  error: { label: "Error", color: "var(--color-loss)", pulse: false },
};

/** Live-state pill for agents/automations. The dot pulses when live. */
export function StatusPill({
  state,
  label,
  className,
}: {
  state: AgentState;
  /** Override the default state label ("Armed", "Running"…). */
  label?: string;
  className?: string;
}) {
  const s = STATE_STYLES[state];
  return (
    <span
      className={cn("inline-flex items-center gap-1.5", className)}
      style={{
        fontFamily: "var(--font-mono)",
        fontSize: 10.5,
        fontWeight: 500,
        letterSpacing: "0.06em",
        textTransform: "uppercase",
        lineHeight: 1,
        padding: "5px 10px",
        borderRadius: "var(--radius-pill)",
        border: "1px solid var(--glass-border)",
        background: "var(--surface-hover)",
        color: "var(--text-secondary)",
      }}
    >
      <span
        aria-hidden
        style={{
          width: 5,
          height: 5,
          borderRadius: "50%",
          background: s.color,
          animation: s.pulse
            ? "pulse-quartr 2s var(--ease-quartr) infinite"
            : undefined,
        }}
      />
      {label ?? s.label}
    </span>
  );
}

/* ────────────────────────────────────────────────────────────────────
 * Actions
 * ──────────────────────────────────────────────────────────────────── */

type PillVariant = "ink" | "ghost" | "outline";

/**
 * The Pivot action pill. `ink` is the landing CTA (ink fill, paper
 * text); on dark surfaces the same variant automatically inverts via
 * theme vars. `withArrow` adds the → that nudges right on hover.
 */
export function PillButton({
  variant = "ink",
  size = "md",
  withArrow = false,
  className,
  children,
  ...rest
}: React.ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: PillVariant;
  size?: "sm" | "md" | "lg";
  withArrow?: boolean;
}) {
  const pad =
    size === "sm" ? "7px 14px" : size === "lg" ? "13px 26px" : "10px 20px";
  const font = size === "sm" ? 12.5 : size === "lg" ? 14.5 : 13.5;
  const variants: Record<PillVariant, React.CSSProperties> = {
    ink: {
      background: "var(--text-primary)",
      color: "var(--bg-base)",
      border: "1px solid var(--text-primary)",
      boxShadow: "var(--shadow-cta)",
    },
    outline: {
      background: "transparent",
      color: "var(--text-primary)",
      border: "1px solid var(--glass-border-hover)",
    },
    ghost: {
      background: "transparent",
      color: "var(--text-primary)",
      border: "1px solid transparent",
    },
  };
  return (
    <button
      type="button"
      className={cn(
        "group inline-flex select-none items-center gap-2 transition-all",
        "hover:-translate-y-px active:translate-y-0 disabled:pointer-events-none disabled:opacity-40",
        className,
      )}
      style={{
        fontFamily: "var(--font-ui)",
        fontWeight: 500,
        fontSize: font,
        lineHeight: 1,
        padding: pad,
        borderRadius: "var(--radius-pill)",
        cursor: "pointer",
        transitionTimingFunction: "var(--ease-quartr)",
        transitionDuration: "200ms",
        ...variants[variant],
      }}
      {...rest}
    >
      {children}
      {withArrow && (
        <span
          aria-hidden
          className="transition-transform duration-200 group-hover:translate-x-0.5"
          style={{ fontSize: font + 1, lineHeight: 1 }}
        >
          →
        </span>
      )}
    </button>
  );
}

/* ────────────────────────────────────────────────────────────────────
 * Numbers
 * ──────────────────────────────────────────────────────────────────── */

/**
 * Signed percentage / value badge. The only place outside charts where
 * color appears. Renders tabular numerals.
 */
export function Delta({
  value,
  suffix = "%",
  precision = 2,
  arrow = true,
  size = 13,
  className,
}: {
  value: number;
  suffix?: string;
  precision?: number;
  arrow?: boolean;
  size?: number;
  className?: string;
}) {
  const positive = value >= 0;
  return (
    <span
      className={cn("inline-flex items-center gap-0.5", className)}
      style={{
        fontFamily: "var(--font-ui)",
        fontWeight: 550,
        fontSize: size,
        fontVariantNumeric: "tabular-nums",
        color: positive ? "var(--color-profit)" : "var(--color-loss)",
        lineHeight: 1,
      }}
    >
      {arrow && (
        <span aria-hidden style={{ fontSize: size - 2 }}>
          {positive ? "▲" : "▼"}
        </span>
      )}
      {positive ? "+" : "−"}
      {Math.abs(value).toFixed(precision)}
      {suffix}
    </span>
  );
}

/** Tabular-numeral figure — prices, NAVs, levels. */
export function Figure({
  size = 15,
  weight = 550,
  muted = false,
  className,
  children,
}: {
  size?: number;
  weight?: number;
  muted?: boolean;
  className?: string;
  children: React.ReactNode;
}) {
  return (
    <span
      className={className}
      style={{
        fontFamily: "var(--font-ui)",
        fontWeight: weight,
        fontSize: size,
        fontVariantNumeric: "tabular-nums",
        letterSpacing: "-0.01em",
        color: muted ? "var(--text-secondary)" : "var(--text-primary)",
      }}
    >
      {children}
    </span>
  );
}

/** Hairline divider matching glass borders. */
export function Hairline({
  className,
  style,
}: {
  className?: string;
  style?: React.CSSProperties;
}) {
  return (
    <div
      aria-hidden
      className={className}
      style={{ height: 1, background: "var(--glass-border)", ...style }}
    />
  );
}