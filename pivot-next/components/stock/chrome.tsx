"use client";

/**
 * The small shared parts of the deep sections: a panel heading, a segmented
 * control, a chip, and the skeleton.
 *
 * These exist so the five panels cannot drift apart. They are deliberately
 * thin — the existing page already owns the visual language (12px radius,
 * `--glass-border` hairlines, 14/600 section labels), and this file restates
 * it once rather than five times.
 */

import * as React from "react";

export function PanelHead({
  title,
  sub,
  right,
}: {
  title: string;
  sub?: React.ReactNode;
  right?: React.ReactNode;
}): React.ReactElement {
  return (
    <div
      style={{
        display: "flex",
        alignItems: "flex-end",
        justifyContent: "space-between",
        gap: 14,
        flexWrap: "wrap",
      }}
    >
      <div>
        <h3
          className="m-0"
          style={{
            fontFamily: "var(--font-ui)",
            fontSize: 14,
            fontWeight: 600,
            letterSpacing: "-0.01em",
            color: "var(--text-primary)",
          }}
        >
          {title}
        </h3>
        {sub ? (
          <div style={{ fontSize: 12, color: "var(--text-tertiary)", marginTop: 3 }}>
            {sub}
          </div>
        ) : null}
      </div>
      {right}
    </div>
  );
}

export function Segmented({
  value,
  options,
  onChange,
}: {
  value: string;
  options: { value: string; label: string }[];
  onChange: (v: string) => void;
}): React.ReactElement {
  return (
    <div
      role="tablist"
      style={{
        display: "inline-flex",
        gap: 2,
        padding: 2,
        border: "1px solid var(--glass-border)",
        borderRadius: "var(--radius-pill)",
        background: "var(--bg-secondary)",
      }}
    >
      {options.map((o) => {
        const on = o.value === value;
        return (
          <button
            key={o.value}
            type="button"
            role="tab"
            aria-selected={on}
            onClick={() => onChange(o.value)}
            style={{
              padding: "4px 12px",
              borderRadius: "var(--radius-pill)",
              border: "none",
              cursor: "pointer",
              fontFamily: "var(--font-ui)",
              fontSize: 12,
              fontWeight: on ? 600 : 500,
              color: on ? "var(--text-primary)" : "var(--text-tertiary)",
              background: on ? "var(--bg-primary)" : "transparent",
              boxShadow: on ? "0 1px 2px rgba(0,0,0,0.06)" : "none",
              transition: "color 120ms ease, background 120ms ease",
            }}
          >
            {o.label}
          </button>
        );
      })}
    </div>
  );
}

export type ChipTone = "neutral" | "accent" | "warn";

export function Chip({
  children,
  tone = "neutral",
  title,
  onClick,
  as = "span",
  href,
}: {
  children: React.ReactNode;
  tone?: ChipTone;
  title?: string;
  onClick?: () => void;
  as?: "span" | "a" | "button";
  href?: string;
}): React.ReactElement {
  const style: React.CSSProperties = {
    display: "inline-flex",
    alignItems: "center",
    gap: 4,
    padding: "1.5px 7px",
    borderRadius: "var(--radius-xs)",
    fontFamily: "var(--font-ui)",
    fontSize: 11,
    fontWeight: 550,
    lineHeight: 1.55,
    whiteSpace: "nowrap",
    textDecoration: "none",
    border: "1px solid",
    cursor: onClick || href ? "pointer" : "default",
    ...(tone === "accent"
      ? { color: "var(--pivot-blue)", borderColor: "var(--accent-border)", background: "var(--accent-wash)" }
      : tone === "warn"
        ? { color: "var(--color-warn)", borderColor: "color-mix(in srgb, var(--color-warn) 40%, transparent)", background: "color-mix(in srgb, var(--color-warn) 10%, transparent)" }
        : { color: "var(--text-tertiary)", borderColor: "var(--glass-border)", background: "var(--bg-secondary)" }),
  };
  if (as === "a" && href) {
    return (
      <a href={href} target="_blank" rel="noreferrer" title={title} style={style}>
        {children}
      </a>
    );
  }
  if (as === "button" || onClick) {
    return (
      <button type="button" onClick={onClick} title={title} style={style}>
        {children}
      </button>
    );
  }
  return <span title={title} style={style}>{children}</span>;
}

/** Loading state for a panel. Deliberately shaped like the content it
 *  replaces — bars where rows will be — so the layout does not jump when the
 *  data lands. */
export function PanelSkeleton({ rows = 6 }: { rows?: number }): React.ReactElement {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 8 }} aria-busy="true">
      {Array.from({ length: rows }).map((_, i) => (
        <div
          key={i}
          style={{
            height: i === 0 ? 30 : 20,
            borderRadius: "var(--radius-xs)",
            background: "var(--bg-elevated)",
            opacity: 0.55,
          }}
        />
      ))}
    </div>
  );
}

export function EmptyNote({ children }: { children: React.ReactNode }): React.ReactElement {
  return (
    <div
      style={{
        padding: "18px 16px",
        border: "1px dashed var(--glass-border)",
        borderRadius: "var(--radius-md)",
        color: "var(--text-tertiary)",
        fontSize: 13,
        fontFamily: "var(--font-ui)",
      }}
    >
      {children}
    </div>
  );
}
