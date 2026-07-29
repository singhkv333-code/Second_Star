"use client";

/**
 * StrategyExplanation — the RIGHT half of the strategies section, beside the
 * table. A plain-English "what actually happens in this strategy" write-up that
 * updates when a table (or calculator) row is selected. Defaults to the first
 * strategy on load (owned by the parent's selection state).
 */

import * as React from "react";
import { inrCompact, type StrategyConfig } from "./strategies";

export function StrategyExplanation({
  strategy,
}: {
  strategy: StrategyConfig;
}): React.ReactElement {
  return (
    // Borderless editorial panel; the parent page's `.vd-explain` class draws
    // a vertical hairline beside the table (top hairline when stacked).
    <div
      className="vd-explain"
      style={{
        display: "flex",
        flexDirection: "column",
        gap: 12,
        height: "100%",
      }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
        <span
          aria-hidden
          style={{
            width: 12,
            height: 12,
            borderRadius: 3,
            background: strategy.color,
            flexShrink: 0,
          }}
        />
        <span
          style={{
            fontFamily: "var(--font-display)",
            fontSize: 16,
            fontWeight: 700,
            color: "var(--text-primary)",
          }}
        >
          {strategy.name}
        </span>
      </div>

      <div style={{ display: "flex", gap: "8px 16px", flexWrap: "wrap" }}>
        <MetaChip label="Risk" value={strategy.risk} />
        <MetaChip label="Minimum" value={inrCompact(strategy.minAmount)} />
        <MetaChip
          label="Expected"
          value={`${strategy.expReturn >= 0 ? "+" : "−"}${Math.abs(
            strategy.expReturn * 100,
          ).toFixed(0)}%`}
        />
      </div>

      <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
        {strategy.explanation.map((para, i) => (
          <p
            key={i}
            style={{
              margin: 0,
              fontFamily: "var(--font-display)",
              fontSize: 14,
              lineHeight: 1.62,
              color: "var(--text-secondary)",
            }}
          >
            {para}
          </p>
        ))}
      </div>
    </div>
  );
}

function MetaChip({ label, value }: { label: string; value: string }): React.ReactElement {
  return (
    <span
      style={{
        display: "inline-flex",
        alignItems: "baseline",
        gap: 6,
        fontFamily: "var(--font-display)",
        fontVariantNumeric: "tabular-nums",
        fontSize: 13,
      }}
    >
      <span style={{ color: "var(--text-tertiary)" }}>{label}</span>
      <span style={{ color: "var(--text-primary)", fontWeight: 600 }}>{value}</span>
    </span>
  );
}

export default StrategyExplanation;
