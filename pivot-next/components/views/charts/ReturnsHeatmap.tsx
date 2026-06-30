"use client";

/**
 * ReturnsHeatmap — a tidy grid of per-holding returns. Each cell tints green for
 * a positive return / red for negative, intensity scaled by magnitude (relative
 * to the largest absolute return in the set). Every cell shows the holding name,
 * its signed return, and a tiny LONG / SHORT tag so the position is unmistakable.
 * Cells are rounded, generously padded (not cramped), and column-aligned.
 *
 * Holdings with no realised return (e.g. option legs) are skipped — the heatmap
 * is a returns view; the caller renders AllocationPie for those instead. Empty
 * → a safe border-only empty state.
 *
 * DESIGN LAW: rounded corners, all type >= 13px, tabular numerals, light + dark
 * via useTokenColors.
 */

import * as React from "react";
import { useTokenColors } from "../use-token-color";

/** One basket holding with its realised return, as served by the views API. */
export type Holding = {
  name: string;
  symbol: string;
  return_pct: number;
  position?: "long" | "short" | string | null;
  weight_pct?: number | null;
};

/** Mix a hex color toward transparency by alpha for a soft tint. */
function tint(hex: string, alpha: number): string {
  const h = hex.replace("#", "");
  const r = parseInt(h.slice(0, 2), 16);
  const g = parseInt(h.slice(2, 4), 16);
  const b = parseInt(h.slice(4, 6), 16);
  if (Number.isNaN(r) || Number.isNaN(g) || Number.isNaN(b)) return hex;
  return `rgba(${r}, ${g}, ${b}, ${alpha.toFixed(3)})`;
}

function fmtPct(v: number): string {
  const sign = v > 0 ? "+" : v < 0 ? "−" : "";
  return `${sign}${Math.abs(v).toFixed(1)}%`;
}

function isShort(p?: string | null): boolean {
  return typeof p === "string" && p.toLowerCase() === "short";
}

export function ReturnsHeatmap({
  holdings,
  minCellWidth = 132,
}: {
  holdings?: Holding[] | null;
  minCellWidth?: number;
}): React.ReactElement {
  const c = useTokenColors({
    profit: "--color-profit",
    loss: "--color-loss",
    border: "--glass-border",
    ink: "--text-primary",
    tertiary: "--text-tertiary",
    bgBase: "--bg-base",
  });

  const safe = (Array.isArray(holdings) ? holdings : [])
    .filter((h) => h && typeof h.return_pct === "number")
    .slice()
    .sort((a, b) => b.return_pct - a.return_pct);

  if (safe.length === 0) {
    return (
      <div
        style={{
          border: `1px solid ${c.border}`,
          borderRadius: "var(--radius-lg)",
          padding: "20px",
          textAlign: "center",
          fontFamily: "var(--font-display)",
          fontSize: 13,
          color: c.tertiary,
          background: c.bgBase,
        }}
      >
        No per-holding returns yet
      </div>
    );
  }

  const maxAbs = Math.max(...safe.map((h) => Math.abs(h.return_pct)), 1);

  return (
    <div
      style={{
        display: "grid",
        gridTemplateColumns: `repeat(auto-fill, minmax(${minCellWidth}px, 1fr))`,
        gap: 10,
      }}
    >
      {safe.map((h) => {
        const up = h.return_pct >= 0;
        const base = up ? c.profit : c.loss;
        // Intensity 0.10 (faint) → 0.80 (strong) by magnitude.
        const intensity = 0.1 + (Math.abs(h.return_pct) / maxAbs) * 0.7;
        const strong = intensity > 0.5;
        const short = isShort(h.position);
        const ink = strong ? "#ffffff" : c.ink;
        return (
          <div
            key={`${h.symbol || h.name}-${h.position ?? "long"}`}
            title={`${h.name} (${h.symbol}) ${fmtPct(h.return_pct)}`}
            style={{
              borderRadius: "var(--radius-md)",
              border: `1px solid ${tint(base, 0.32)}`,
              background: tint(base, intensity),
              padding: "13px 15px",
              display: "flex",
              flexDirection: "column",
              gap: 8,
              minHeight: 78,
            }}
          >
            <div
              style={{
                display: "flex",
                alignItems: "center",
                justifyContent: "space-between",
                gap: 8,
              }}
            >
              <span
                style={{
                  fontFamily: "var(--font-display)",
                  fontSize: 13,
                  fontWeight: 500,
                  lineHeight: 1.25,
                  color: ink,
                  whiteSpace: "nowrap",
                  overflow: "hidden",
                  textOverflow: "ellipsis",
                  minWidth: 0,
                }}
              >
                {h.name}
              </span>
              <span
                style={{
                  flexShrink: 0,
                  fontFamily: "var(--font-display)",
                  fontSize: 13,
                  fontWeight: 600,
                  letterSpacing: "0.04em",
                  color: ink,
                  opacity: strong ? 0.85 : 0.6,
                }}
              >
                {short ? "SHORT" : "LONG"}
              </span>
            </div>
            <span
              style={{
                fontFamily: "var(--font-display)",
                fontVariantNumeric: "tabular-nums",
                fontSize: 17,
                fontWeight: 600,
                letterSpacing: "-0.01em",
                color: strong ? "#ffffff" : base,
              }}
            >
              {fmtPct(h.return_pct)}
            </span>
          </div>
        );
      })}
    </div>
  );
}

export default ReturnsHeatmap;
