"use client";

/**
 * HoldingsReturns — a sorted horizontal bar chart of each holding's return.
 * Bars grow from a centered zero baseline: positive bars to the right in
 * var(--color-profit), negative to the left in var(--color-loss). Each row is
 * labelled with the holding name and a signed value; bars are rounded; all type
 * >= 13px. Sorted best → worst.
 *
 * Empty holdings → caller omits; a safe empty state is rendered if handed
 * nothing. Light + dark via useTokenColors. Pure CSS (no recharts).
 */

import * as React from "react";
import type { Holding } from "./ReturnsHeatmap";
import { useTokenColors } from "../use-token-color";

const NAME_W = 116;
const VALUE_W = 70;

function fmtPct(v: number): string {
  const sign = v > 0 ? "+" : v < 0 ? "−" : "";
  return `${sign}${Math.abs(v).toFixed(1)}%`;
}

export function HoldingsReturns({
  holdings,
}: {
  holdings?: Holding[] | null;
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
          borderRadius: 16,
          padding: "20px",
          textAlign: "center",
          fontFamily: "var(--font-display)",
          fontSize: 13,
          color: c.tertiary,
          background: c.bgBase,
        }}
      >
        No holdings yet
      </div>
    );
  }

  // Symmetric scale so positive and negative bars share one zero axis.
  const maxAbs = Math.max(...safe.map((h) => Math.abs(h.return_pct)), 1);

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
      {safe.map((h) => {
        const pos = h.return_pct >= 0;
        const color = pos ? c.profit : c.loss;
        const widthPct = (Math.abs(h.return_pct) / maxAbs) * 50; // half-track each side
        return (
          <div
            key={h.symbol || h.name}
            style={{ display: "flex", alignItems: "center", gap: 12 }}
          >
            <span
              title={`${h.name} (${h.symbol})`}
              style={{
                width: NAME_W,
                flexShrink: 0,
                fontFamily: "var(--font-display)",
                fontSize: 13,
                fontWeight: 500,
                color: c.ink,
                whiteSpace: "nowrap",
                overflow: "hidden",
                textOverflow: "ellipsis",
              }}
            >
              {h.name}
            </span>

            {/* Two-half track sharing a centered zero baseline. */}
            <div
              style={{
                flex: 1,
                minWidth: 0,
                display: "flex",
                alignItems: "center",
                height: 22,
              }}
            >
              <div
                style={{
                  width: "50%",
                  display: "flex",
                  justifyContent: "flex-end",
                  paddingRight: 1,
                }}
              >
                {!pos && (
                  <div
                    style={{
                      width: `${widthPct * 2}%`,
                      height: 22,
                      minWidth: 3,
                      background: color,
                      borderRadius: "8px 0 0 8px",
                      transition: "width 320ms var(--ease-quartr)",
                    }}
                  />
                )}
              </div>
              <div style={{ width: 1, height: 22, background: c.border }} />
              <div
                style={{
                  width: "50%",
                  display: "flex",
                  justifyContent: "flex-start",
                  paddingLeft: 1,
                }}
              >
                {pos && (
                  <div
                    style={{
                      width: `${widthPct * 2}%`,
                      height: 22,
                      minWidth: 3,
                      background: color,
                      borderRadius: "0 8px 8px 0",
                      transition: "width 320ms var(--ease-quartr)",
                    }}
                  />
                )}
              </div>
            </div>

            <span
              style={{
                width: VALUE_W,
                flexShrink: 0,
                textAlign: "right",
                fontFamily: "var(--font-display)",
                fontVariantNumeric: "tabular-nums",
                fontSize: 14,
                fontWeight: 600,
                letterSpacing: "-0.01em",
                color,
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

export default HoldingsReturns;
