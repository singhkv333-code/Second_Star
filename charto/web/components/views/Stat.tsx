"use client";

/**
 * Stat.tsx — the Views-local numeral voice.
 *
 * WHY THIS EXISTS: the shared DS primitives `Figure`/`Delta`
 * (components/ds/primitives.tsx) hardcode the JetBrains-Mono numeral font
 * family and are used app-wide (topbar, dashboard). Editing them would
 * re-skin the whole app. So Views renders ALL numerals through these local
 * primitives instead, which use var(--font-display) (Inter) + tabular-nums.
 *
 *  - Num        : inline Inter-tabular figure span (the Figure replacement).
 *  - Stat       : a labeled metric — Title-case label (13px) + big value.
 *  - StatStrip  : responsive flex/grid wrapper for a row of Stat tiles.
 *
 * DESIGN LAW: hard 13px floor. NO size below 13px exists here. Labels are
 * 13px/500 Title-case tertiary text — never 10px uppercase mono micro-caps.
 * NEVER use the JetBrains-Mono numeral font var anywhere in components/views/*.
 */

import * as React from "react";

const NUM_STYLE: React.CSSProperties = {
  fontFamily: "var(--font-display)",
  fontVariantNumeric: "tabular-nums",
  letterSpacing: "-0.01em",
};

/**
 * Num sizes — every value is >= 13px (the hard floor).
 *   label   13  — metric/eyebrow label (also the minimum legal size)
 *   md      15  — inline body number
 *   lg      18  — card one-liner scale number
 *   value   22  — the standard metric value
 *   hero    30  — card hero number (one per card)
 *   display 48  — detail hero number (one per detail surface)
 */
export type NumSize = "label" | "md" | "lg" | "value" | "hero" | "display";

const NUM_SIZE: Record<NumSize, number> = {
  label: 13,
  md: 15,
  lg: 18,
  value: 22,
  hero: 30,
  display: 48,
};

/**
 * Num — an inline Inter-tabular figure. Use for EVERY number in Views
 * (returns, %, ratios, prices, grades, scores, weights).
 */
export function Num({
  children,
  color,
  size = "md",
  weight = 600,
  className,
  style,
}: {
  children: React.ReactNode;
  /** CSS color (var string). Defaults to inherit. */
  color?: string;
  size?: NumSize;
  weight?: number;
  className?: string;
  style?: React.CSSProperties;
}): React.ReactElement {
  return (
    <span
      className={className}
      style={{
        ...NUM_STYLE,
        fontSize: NUM_SIZE[size],
        fontWeight: weight,
        color,
        ...style,
      }}
    >
      {children}
    </span>
  );
}

/**
 * Stat — a single labeled metric. The calm Views idiom:
 *   Label  (13px/500 Title-case, tertiary — NOT uppercase mono micro-caps)
 *   value  (22px/600 by default, colored by meaning)
 *   sub    (optional caption, 13px tertiary)
 */
export function Stat({
  label,
  value,
  sub,
  valueColor,
  valueSize = "value",
  align = "start",
  className,
  style,
}: {
  label: React.ReactNode;
  value: React.ReactNode;
  sub?: React.ReactNode;
  /** Color for the value (var string). */
  valueColor?: string;
  valueSize?: NumSize;
  align?: "start" | "center" | "end";
  className?: string;
  style?: React.CSSProperties;
}): React.ReactElement {
  return (
    <div
      className={className}
      style={{
        display: "flex",
        flexDirection: "column",
        gap: 4,
        alignItems:
          align === "center"
            ? "center"
            : align === "end"
              ? "flex-end"
              : "flex-start",
        textAlign:
          align === "center" ? "center" : align === "end" ? "right" : "left",
        minWidth: 0,
        ...style,
      }}
    >
      <span
        style={{
          fontFamily: "var(--font-display)",
          fontSize: 13,
          fontWeight: 500,
          color: "var(--text-tertiary)",
          whiteSpace: "nowrap",
          lineHeight: 1.3,
        }}
      >
        {label}
      </span>
      <Num size={valueSize} color={valueColor ?? "var(--text-primary)"}>
        {value}
      </Num>
      {sub != null && sub !== "" && (
        <span
          style={{
            fontFamily: "var(--font-display)",
            fontVariantNumeric: "tabular-nums",
            fontSize: 13,
            color: "var(--text-tertiary)",
            whiteSpace: "nowrap",
          }}
        >
          {sub}
        </span>
      )}
    </div>
  );
}

/**
 * StatStrip — responsive row wrapper for Stat tiles. Wraps on narrow widths;
 * pass `cols` to force an explicit grid, otherwise it flows with gap.
 */
export function StatStrip({
  children,
  cols,
  className,
  style,
}: {
  children: React.ReactNode;
  /** Force an N-column grid; omit for an auto-wrapping flex row. */
  cols?: number;
  className?: string;
  style?: React.CSSProperties;
}): React.ReactElement {
  const gridStyle: React.CSSProperties = cols
    ? {
        display: "grid",
        gridTemplateColumns: `repeat(${cols}, minmax(0, 1fr))`,
        gap: 20,
      }
    : {
        display: "flex",
        flexWrap: "wrap",
        gap: "20px 32px",
      };
  return (
    <div className={className} style={{ ...gridStyle, ...style }}>
      {children}
    </div>
  );
}
