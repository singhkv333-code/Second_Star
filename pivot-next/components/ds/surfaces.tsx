"use client";

/**
 * Pivot design system — surfaces.
 *
 * Panels, metric blocks, tables, sparklines, and full-bleed section
 * shells. Everything is monochrome and theme-var driven; the dark
 * treatments mirror the landing page's #0a0a0b sections with
 * rgba(255,255,255,.035) glass cards and faint grid/glow atmosphere.
 */

import * as React from "react";
import { cn } from "@/lib/utils";
import { Delta, Eyebrow, Figure } from "./primitives";

/* ────────────────────────────────────────────────────────────────────
 * Panel — the base card
 * ──────────────────────────────────────────────────────────────────── */

export type PanelVariant = "paper" | "glass" | "ink" | "outline";

/**
 * Base card surface.
 *  - `paper`   subtle-tint card on light surfaces (--bg-card)
 *  - `glass`   the landing dark-section card (white-alpha wash) — also
 *              works on light, where it reads as a faint grey wash
 *  - `ink`     inverted "featured" card (ink fill, paper text) — the
 *              landing carousel's highlighted strategy card
 *  - `outline` border only, fully transparent
 */
export function Panel({
  variant = "paper",
  pad = 20,
  interactive = false,
  className,
  style,
  children,
  ...rest
}: React.HTMLAttributes<HTMLDivElement> & {
  variant?: PanelVariant;
  pad?: number;
  /** Adds hover lift + border emphasis for clickable cards. */
  interactive?: boolean;
}) {
  const variants: Record<PanelVariant, React.CSSProperties> = {
    paper: {
      background: "var(--bg-card)",
      border: "1px solid var(--glass-border)",
    },
    glass: {
      background: "var(--surface-active)",
      border: "1px solid var(--glass-border)",
    },
    ink: {
      background: "var(--text-primary)",
      border: "1px solid var(--text-primary)",
      color: "var(--bg-base)",
    },
    outline: {
      background: "transparent",
      border: "1px solid var(--glass-border-hover)",
    },
  };
  return (
    <div
      className={cn(
        interactive &&
          "cursor-pointer transition-all duration-200 hover:-translate-y-0.5",
        className,
      )}
      style={{
        borderRadius: "var(--radius-lg)",
        padding: pad,
        transitionTimingFunction: "var(--ease-quartr)",
        ...variants[variant],
        ...style,
      }}
      {...rest}
    >
      {children}
    </div>
  );
}

/* ────────────────────────────────────────────────────────────────────
 * Metrics & data
 * ──────────────────────────────────────────────────────────────────── */

/**
 * Analytics stat block — mono label, big tabular figure, optional
 * delta + sparkline. The dashboard/topbar metric unit.
 */
export function MetricStat({
  label,
  value,
  delta,
  spark,
  className,
}: {
  label: string;
  value: string;
  delta?: number;
  /** Normalised 0–1 series; renders a 64×20 sparkline on the right. */
  spark?: number[];
  className?: string;
}) {
  return (
    <div className={cn("flex items-end justify-between gap-4", className)}>
      <div className="min-w-0">
        <div
          style={{
            fontFamily: "var(--font-mono)",
            fontSize: 10.5,
            fontWeight: 500,
            letterSpacing: "0.1em",
            textTransform: "uppercase",
            color: "var(--metric-label)",
            marginBottom: 7,
          }}
        >
          {label}
        </div>
        <div className="flex items-baseline gap-2.5">
          <Figure size={24} weight={550}>
            {value}
          </Figure>
          {delta !== undefined && <Delta value={delta} size={12.5} />}
        </div>
      </div>
      {spark && spark.length > 1 && (
        <SparkLine data={spark} width={64} height={22} className="mb-0.5" />
      )}
    </div>
  );
}

/**
 * Monochrome sparkline. Strokes with --price-line by default so it
 * stays ink-on-paper; pass `signed` to color by first→last direction.
 */
export function SparkLine({
  data,
  width = 96,
  height = 28,
  signed = false,
  className,
}: {
  data: number[];
  width?: number;
  height?: number;
  signed?: boolean;
  className?: string;
}) {
  const min = Math.min(...data);
  const max = Math.max(...data);
  const span = max - min || 1;
  const stepX = width / (data.length - 1);
  const pts = data
    .map(
      (v, i) =>
        `${(i * stepX).toFixed(1)},${(height - 2 - ((v - min) / span) * (height - 4)).toFixed(1)}`,
    )
    .join(" ");
  const up = (data[data.length - 1] ?? 0) >= (data[0] ?? 0);
  const stroke = signed
    ? up
      ? "var(--color-profit)"
      : "var(--color-loss)"
    : "var(--price-line)";
  return (
    <svg
      width={width}
      height={height}
      viewBox={`0 0 ${width} ${height}`}
      className={className}
      aria-hidden
    >
      <polyline
        points={pts}
        fill="none"
        stroke={stroke}
        strokeWidth="1.5"
        strokeLinejoin="round"
        strokeLinecap="round"
        opacity={signed ? 0.9 : 0.65}
      />
    </svg>
  );
}

/**
 * Quiet data table for holdings / legs / scenario rows. Mono uppercase
 * header, hairline rows, tabular numerals. Pass cells pre-formatted;
 * `align` defaults to right for every column after the first.
 */
export function MiniTable({
  head,
  rows,
  className,
}: {
  head: string[];
  rows: React.ReactNode[][];
  className?: string;
}) {
  return (
    <table
      className={cn("w-full border-collapse", className)}
      style={{ fontFamily: "var(--font-ui)", fontSize: 13 }}
    >
      <thead>
        <tr>
          {head.map((h, i) => (
            <th
              key={h}
              style={{
                fontFamily: "var(--font-mono)",
                fontSize: 10,
                fontWeight: 500,
                letterSpacing: "0.1em",
                textTransform: "uppercase",
                color: "var(--text-tertiary)",
                textAlign: i === 0 ? "left" : "right",
                padding: "0 10px 9px",
                borderBottom: "1px solid var(--glass-border-hover)",
              }}
            >
              {h}
            </th>
          ))}
        </tr>
      </thead>
      <tbody>
        {rows.map((r, ri) => (
          <tr key={ri}>
            {r.map((cell, ci) => (
              <td
                key={ci}
                style={{
                  padding: "10px 10px",
                  borderBottom:
                    ri === rows.length - 1
                      ? "none"
                      : "1px solid var(--glass-border)",
                  textAlign: ci === 0 ? "left" : "right",
                  color: ci === 0 ? "var(--text-primary)" : "var(--text-secondary)",
                  fontFamily: ci === 0 ? "var(--font-ui)" : "var(--font-numeric)",
                  fontSize: ci === 0 ? 13 : 12.5,
                  letterSpacing: ci === 0 ? undefined : "-0.02em",
                  fontWeight: ci === 0 ? 500 : 400,
                }}
              >
                {cell}
              </td>
            ))}
          </tr>
        ))}
      </tbody>
    </table>
  );
}

/* ────────────────────────────────────────────────────────────────────
 * Section shells — landing-page atmosphere
 * ──────────────────────────────────────────────────────────────────── */

/**
 * Full-bleed section wrapper.
 *  - `paper` plain light section
 *  - `ink`   the landing dark section: #0a0a0b equivalent via tokens,
 *            optional faint blueprint grid + radial glow atmosphere
 *
 * `ink` forces the dark token set by applying the `dark` class, so any
 * DS component nested inside renders correctly without prop changes.
 */
export function SectionShell({
  tone = "paper",
  grid = false,
  glow = false,
  className,
  style,
  children,
}: {
  tone?: "paper" | "ink";
  /** Faint 1px blueprint grid (ink tone only). */
  grid?: boolean;
  /** Soft radial glow bottom-left (ink tone only). */
  glow?: boolean;
  className?: string;
  style?: React.CSSProperties;
  children: React.ReactNode;
}) {
  if (tone === "paper") {
    return (
      <section
        className={className}
        style={{ background: "var(--bg-base)", position: "relative", ...style }}
      >
        {children}
      </section>
    );
  }
  return (
    <section
      className={cn("dark relative isolate overflow-hidden", className)}
      style={{ background: "#0a0a0b", color: "var(--text-primary)", ...style }}
    >
      {grid && (
        <div
          aria-hidden
          className="pointer-events-none absolute inset-0"
          style={{
            backgroundImage:
              "linear-gradient(rgba(255,255,255,0.035) 1px, transparent 1px), linear-gradient(90deg, rgba(255,255,255,0.035) 1px, transparent 1px)",
            backgroundSize: "72px 72px",
            maskImage:
              "radial-gradient(ellipse 90% 70% at 50% 40%, black 55%, transparent 100%)",
            WebkitMaskImage:
              "radial-gradient(ellipse 90% 70% at 50% 40%, black 55%, transparent 100%)",
          }}
        />
      )}
      {glow && (
        <div
          aria-hidden
          className="pointer-events-none absolute inset-0"
          style={{
            background:
              "radial-gradient(560px 320px at 18% 88%, rgba(255,255,255,0.05), transparent 70%), radial-gradient(640px 360px at 85% 100%, rgba(255,255,255,0.035), transparent 70%)",
          }}
        />
      )}
      <div className="relative">{children}</div>
    </section>
  );
}

/**
 * Dark call-to-action band — serif headline with italic accent over an
 * ink section, single pill CTA. The landing's closing move.
 */
export function CTABand({
  eyebrow,
  children,
  action,
  className,
}: {
  eyebrow?: string;
  /** The Display headline content (compose with Display in caller). */
  children: React.ReactNode;
  action?: React.ReactNode;
  className?: string;
}) {
  return (
    <SectionShell tone="ink" glow className={className}>
      <div className="mx-auto flex max-w-3xl flex-col items-center gap-7 px-6 py-20 text-center sm:py-24">
        {eyebrow && <Eyebrow>{eyebrow}</Eyebrow>}
        {children}
        {action}
      </div>
    </SectionShell>
  );
}