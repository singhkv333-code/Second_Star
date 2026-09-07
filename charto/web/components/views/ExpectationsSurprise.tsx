"use client";

/**
 * ExpectationsSurprise — "What's priced in vs your view".
 *
 * DESIGN LAW (components/views): square corners, borders only (no fills), no
 * jargon, 13px floor. Numeric columns are right-aligned under their headers.
 * The source label routes through sourceLabel() (closed map; unknown tokens
 * become "Market estimate", never a raw slug). Renders NOTHING when empty.
 */

import * as React from "react";
import { ArrowDown, ArrowUp, Minus } from "lucide-react";
import { Num } from "@/components/views/Stat";
import { Hairline } from "@/components/views/ViewSurface";
import { fmtPct, sourceLabel } from "@/components/views/view-format";
import type { ViewExpectationRow } from "@/lib/types";

interface ExpectationsSurpriseProps {
  rows: ViewExpectationRow[];
}

type SurpriseSign = "positive" | "negative" | "inline" | null;

const GRID = "minmax(0, 1.4fr) minmax(0, 1fr) minmax(0, 1fr) minmax(0, 1fr)";

function SurpriseIndicator({ sign }: { sign: SurpriseSign }) {
  if (!sign) {
    return (
      <Num size="md" color="var(--text-tertiary)" style={{ textAlign: "right" }}>
        —
      </Num>
    );
  }

  const config: Record<
    "positive" | "negative" | "inline",
    { label: string; Icon: typeof ArrowUp; color: string }
  > = {
    positive: { label: "Positive", Icon: ArrowUp, color: "var(--color-profit)" },
    negative: { label: "Negative", Icon: ArrowDown, color: "var(--color-loss)" },
    inline: { label: "Inline", Icon: Minus, color: "var(--text-tertiary)" },
  };

  const c = config[sign];
  const Icon = c.Icon;

  return (
    <span
      style={{
        display: "inline-flex",
        alignItems: "center",
        justifyContent: "flex-end",
        gap: 5,
        color: c.color,
        width: "100%",
      }}
    >
      <Icon size={14} aria-hidden strokeWidth={2.2} style={{ flexShrink: 0 }} />
      <span
        style={{
          fontFamily: "var(--font-display)",
          fontSize: 13,
          fontWeight: 500,
          lineHeight: 1.2,
        }}
      >
        {c.label}
      </span>
    </span>
  );
}

function HeaderCell({
  children,
  align = "left",
}: {
  children: React.ReactNode;
  align?: "left" | "right";
}) {
  return (
    <span
      style={{
        fontFamily: "var(--font-display)",
        fontSize: 13,
        fontWeight: 500,
        color: "var(--text-tertiary)",
        lineHeight: 1.3,
        textAlign: align,
      }}
    >
      {children}
    </span>
  );
}

function ExpectationRow({ row }: { row: ViewExpectationRow }) {
  return (
    <div
      style={{
        display: "grid",
        gridTemplateColumns: GRID,
        gap: 12,
        alignItems: "center",
        padding: "12px 0",
      }}
    >
      <span
        style={{
          fontFamily: "var(--font-display)",
          fontSize: 15,
          fontWeight: 400,
          color: "var(--text-secondary)",
          lineHeight: 1.4,
          overflowWrap: "anywhere",
        }}
      >
        {sourceLabel(row.source_label ?? row.source)}
      </span>

      <Num
        size="md"
        color={row.expected_value !== null ? "var(--text-primary)" : "var(--text-tertiary)"}
        style={{ textAlign: "right" }}
      >
        {fmtPct(row.expected_value)}
      </Num>

      <Num
        size="md"
        color={row.user_view_value !== null ? "var(--text-primary)" : "var(--text-tertiary)"}
        style={{ textAlign: "right" }}
      >
        {fmtPct(row.user_view_value)}
      </Num>

      <div style={{ textAlign: "right" }}>
        <SurpriseIndicator sign={row.surprise_sign} />
      </div>
    </div>
  );
}

export function ExpectationsSurprise({ rows }: ExpectationsSurpriseProps) {
  if (!rows || rows.length === 0) return null;

  return (
    <div>
      <p
        style={{
          fontFamily: "var(--font-display)",
          fontSize: 15,
          fontWeight: 400,
          lineHeight: 1.5,
          color: "var(--text-secondary)",
          margin: "0 0 16px",
        }}
      >
        Markets move on surprise, not outcome. The gap between what is priced in
        and your view is the signal.
      </p>

      <div
        style={{
          display: "grid",
          gridTemplateColumns: GRID,
          gap: 12,
          paddingBottom: 10,
        }}
      >
        <HeaderCell>Source</HeaderCell>
        <HeaderCell align="right">Expected</HeaderCell>
        <HeaderCell align="right">Your view</HeaderCell>
        <HeaderCell align="right">Surprise</HeaderCell>
      </div>

      <Hairline />

      {rows.map((row, i) => (
        <React.Fragment key={`${row.source}-${row.market_id ?? i}`}>
          <ExpectationRow row={row} />
          {i < rows.length - 1 && <Hairline />}
        </React.Fragment>
      ))}
    </div>
  );
}
