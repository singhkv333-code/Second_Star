"use client";

/**
 * StrategyTable — the LEFT half of the strategies section.
 *
 * A clean table of the 3 strategies (Strategy · Description · Risk · Min amount).
 * Rows are clickable; the selected row is highlighted and drives the
 * StrategyExplanation panel beside it.
 */

import * as React from "react";
import { useTokenColors } from "@/components/views/use-token-color";
import { inrCompact, type Risk, type StrategyConfig } from "./strategies";

function riskColor(risk: Risk): string {
  switch (risk) {
    case "Low":
      return "var(--color-profit)";
    case "Moderate":
      return "var(--color-warn)";
    case "High":
      return "var(--color-loss)";
  }
}

function RiskPill({ risk }: { risk: Risk }): React.ReactElement {
  const col = riskColor(risk);
  // Plain colored text + dot — no pastel chip fill (design-law: color is for
  // data, containers stay neutral).
  return (
    <span
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: 6,
        fontFamily: "var(--font-display)",
        fontSize: 12.5,
        fontWeight: 600,
        color: col,
        whiteSpace: "nowrap",
      }}
    >
      <span
        aria-hidden
        style={{
          width: 6,
          height: 6,
          borderRadius: "50%",
          background: col,
          flexShrink: 0,
        }}
      />
      {risk}
    </span>
  );
}

export function StrategyTable({
  strategies,
  selectedId,
  onSelect,
}: {
  strategies: StrategyConfig[];
  selectedId: string;
  onSelect: (id: string) => void;
}): React.ReactElement {
  const c = useTokenColors({
    blue: "--pivot-blue",
    border: "--glass-border",
    bg: "--bg-base",
    tertiary: "--text-tertiary",
  });

  const th: React.CSSProperties = {
    fontFamily: "var(--font-display)",
    fontSize: 11,
    fontWeight: 600,
    textTransform: "uppercase",
    letterSpacing: "0.05em",
    color: "var(--text-tertiary)",
    textAlign: "left",
    padding: "0 14px 10px",
    whiteSpace: "nowrap",
  };

  return (
    // Borderless, editorial: column headers + row hairlines only — no outer
    // box (matches the floating chart; the trade ticket is the only card).
    <div>
      <div style={{ overflowX: "auto" }}>
        <table style={{ width: "100%", borderCollapse: "collapse", minWidth: 460 }}>
          <thead>
            <tr style={{ borderBottom: `1px solid ${c.border}` }}>
              <th style={th}>Strategy</th>
              <th style={th}>What it is</th>
              <th style={th}>Risk</th>
              <th style={{ ...th, textAlign: "right" }}>Min</th>
            </tr>
          </thead>
          <tbody>
            {strategies.map((s) => {
              const selected = selectedId === s.id;
              return (
                <tr
                  key={s.id}
                  onClick={() => onSelect(s.id)}
                  aria-selected={selected}
                  style={{
                    cursor: "pointer",
                    borderBottom: `1px solid ${c.border}`,
                    background: selected ? "var(--surface-hover)" : "transparent",
                    boxShadow: selected
                      ? `inset 3px 0 0 0 ${s.color}`
                      : "inset 3px 0 0 0 transparent",
                  }}
                >
                  <td
                    style={{
                      padding: "14px",
                      fontFamily: "var(--font-display)",
                      fontSize: 14,
                      fontWeight: 600,
                      color: "var(--text-primary)",
                      verticalAlign: "top",
                    }}
                  >
                    <span style={{ display: "inline-flex", alignItems: "center", gap: 8 }}>
                      <span
                        aria-hidden
                        style={{
                          width: 10,
                          height: 10,
                          borderRadius: 3,
                          background: s.color,
                          flexShrink: 0,
                        }}
                      />
                      {s.name}
                    </span>
                  </td>
                  <td
                    style={{
                      padding: "14px",
                      fontFamily: "var(--font-display)",
                      fontSize: 13,
                      lineHeight: 1.45,
                      color: "var(--text-secondary)",
                      verticalAlign: "top",
                      minWidth: 200,
                    }}
                  >
                    {s.oneLiner}
                  </td>
                  <td style={{ padding: "14px", verticalAlign: "top" }}>
                    <RiskPill risk={s.risk} />
                  </td>
                  <td
                    style={{
                      padding: "14px",
                      fontFamily: "var(--font-display)",
                      fontVariantNumeric: "tabular-nums",
                      fontSize: 13,
                      fontWeight: 600,
                      color: "var(--text-primary)",
                      textAlign: "right",
                      verticalAlign: "top",
                      whiteSpace: "nowrap",
                    }}
                  >
                    {inrCompact(s.minAmount)}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}

export default StrategyTable;
