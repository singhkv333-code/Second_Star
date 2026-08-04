"use client";

/**
 * ViewSurface.tsx — the ONE card primitive for the Views tab.
 *
 * DESIGN LAW (non-negotiable):
 *  - ROUNDED corners. Cards use var(--radius-lg) (~16px); inner chips/cells
 *    use smaller radii. Never square (borderRadius: 0 / rounded-none).
 *  - BORDERS ONLY, NO FILLS. Background is ALWAYS var(--bg-base) (the page
 *    color — effectively transparent). Distinction is ONE 1px hairline:
 *    border 1px solid var(--glass-border).
 *  - Hover / active / selected = change the BORDER COLOR
 *    (var(--glass-border-hover) / --glass-border-focus), NEVER add a fill.
 *  - NO box-shadow, NO glass depth, NO var(--bg-card)/--surface-* anywhere.
 *
 * This REPLACES the DS <Panel> (which hardcodes a fill). Same rounded radius,
 * but border-only with no fill. Sections separate via <Hairline/> +
 * whitespace, never nested filled boxes.
 *
 * Exports:
 *  - ViewSurface : the rounded, border-only container.
 *  - Hairline    : a full-width 1px horizontal rule (var(--glass-border)).
 *  - KpiRow      : label/value pairs split by VERTICAL hairlines.
 */

import * as React from "react";
import { Num } from "@/components/views/Stat";

type SurfaceTag = "div" | "section" | "article" | "li" | "a" | "button";

export function ViewSurface({
  as = "div",
  interactive = false,
  className,
  style,
  children,
  ...rest
}: {
  as?: SurfaceTag;
  /** When true, hover lifts the border color (never a fill). */
  interactive?: boolean;
  className?: string;
  style?: React.CSSProperties;
  children?: React.ReactNode;
} & React.HTMLAttributes<HTMLElement>): React.ReactElement {
  const [hover, setHover] = React.useState(false);

  const base: React.CSSProperties = {
    background: "var(--bg-base)",
    border: `1px solid ${
      interactive && hover
        ? "var(--glass-border-hover)"
        : "var(--glass-border)"
    }`,
    borderRadius: "var(--radius-lg)",
    padding: 20,
    boxShadow: "none",
    transition: "border-color 180ms var(--ease-quartr)",
    ...style,
  };

  const interactionProps = interactive
    ? {
        onMouseEnter: () => setHover(true),
        onMouseLeave: () => setHover(false),
      }
    : {};

  return React.createElement(
    as,
    {
      className,
      style: base,
      ...interactionProps,
      ...rest,
    },
    children,
  );
}

/**
 * Hairline — a full-width 1px horizontal rule, var(--glass-border). No margin
 * surprises: it adds NO vertical margin of its own (caller controls rhythm).
 * The canonical section separator in Views (instead of nested boxes).
 */
export function Hairline({
  className,
  style,
}: {
  className?: string;
  style?: React.CSSProperties;
}): React.ReactElement {
  return (
    <div
      role="separator"
      className={className}
      style={{
        width: "100%",
        height: 1,
        background: "var(--glass-border)",
        border: "none",
        borderRadius: "var(--radius-pill)",
        margin: 0,
        ...style,
      }}
    />
  );
}

export type KpiTone = "default" | "profit" | "loss" | "accent" | "muted";

function kpiToneColor(tone: KpiTone | undefined): string {
  switch (tone) {
    case "profit":
      return "var(--color-profit)";
    case "loss":
      return "var(--color-loss)";
    case "accent":
      return "var(--pivot-blue)";
    case "muted":
      return "var(--text-tertiary)";
    default:
      return "var(--text-primary)";
  }
}

export type KpiItem = {
  label: React.ReactNode;
  value: React.ReactNode;
  tone?: KpiTone;
};

/**
 * KpiRow — a row of label/value pairs separated by VERTICAL 1px hairlines.
 * Value is 22px/600 tabular, right-aligned within each cell; label is 13px/500
 * tertiary. Everything is >= 13px. Wraps gracefully (cells flex-wrap), never
 * overflows. No fills, no radius.
 */
export function KpiRow({
  items,
  className,
  style,
}: {
  items: KpiItem[];
  className?: string;
  style?: React.CSSProperties;
}): React.ReactElement {
  return (
    <div
      className={className}
      style={{
        display: "flex",
        flexWrap: "wrap",
        alignItems: "stretch",
        rowGap: 16,
        ...style,
      }}
    >
      {items.map((item, i) => (
        <div
          key={i}
          style={{
            display: "flex",
            flexDirection: "column",
            alignItems: "flex-end",
            gap: 4,
            minWidth: 0,
            flex: "1 1 auto",
            // VERTICAL hairline separator between cells (not before the first).
            paddingLeft: i === 0 ? 0 : 20,
            paddingRight: i === items.length - 1 ? 0 : 20,
            borderLeft:
              i === 0 ? "none" : "1px solid var(--glass-border)",
          }}
        >
          <span
            style={{
              fontFamily: "var(--font-display)",
              fontSize: 13,
              fontWeight: 500,
              color: "var(--text-tertiary)",
              lineHeight: 1.3,
              textAlign: "right",
              whiteSpace: "nowrap",
            }}
          >
            {item.label}
          </span>
          <Num
            size="value"
            weight={600}
            color={kpiToneColor(item.tone)}
            style={{ textAlign: "right" }}
          >
            {item.value}
          </Num>
        </div>
      ))}
    </div>
  );
}
