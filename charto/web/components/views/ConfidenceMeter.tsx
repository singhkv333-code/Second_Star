"use client";

/**
 * ConfidenceMeter — ONE clean square horizontal bar.
 *
 * DESIGN LAW (components/views): square corners, borders only, no fills, no
 * jargon, 13px floor. There is no 20-segment decorative track, no grade chip,
 * no radius. We render the grade letter + "NN/100" and a single square bar
 * whose fill width = score%. The fill is the only color (spent on signal).
 *
 * Suppressed / null (no track record yet): we NEVER show "Below MinTRL" or any
 * jargon — just the honest line "Not enough track record to score yet."
 *
 * The raw `evidence` string is jargon-laden (CAAR / MinTRL / p-values), so it
 * is intentionally NOT rendered. The prop is kept for signature compatibility.
 */

import * as React from "react";
import type { Dial } from "@/lib/types";
import { gradeColor } from "./view-format";
import { Num } from "./Stat";

export interface ConfidenceMeterProps {
  score: number | null;
  letter: string | null;
  dial?: Dial | null;
  label: string;
  /** Kept for signature compatibility; intentionally not rendered (jargon). */
  evidence?: string | null;
  /** Force the suppressed presentation. */
  suppressed?: boolean;
  size?: "full" | "compact";
}

export function ConfidenceMeter({
  score,
  letter,
  dial,
  label,
  suppressed,
  size = "full",
}: ConfidenceMeterProps): React.ReactElement {
  const isSuppressed =
    suppressed === true ||
    dial === "SUPPRESSED" ||
    (score === null && letter === null);

  const color = isSuppressed ? "var(--text-tertiary)" : gradeColor(letter);
  const pct =
    isSuppressed || score === null ? 0 : Math.max(0, Math.min(100, score));
  const compact = size === "compact";

  const labelEl = (
    <span
      style={{
        fontFamily: "var(--font-display)",
        fontSize: 13,
        fontWeight: 500,
        color: "var(--text-tertiary)",
        lineHeight: 1.3,
      }}
    >
      {label}
    </span>
  );

  // The grade + score readout, e.g. "B · 79/100".
  const readout = isSuppressed ? null : (
    <span
      style={{ display: "inline-flex", alignItems: "baseline", gap: 6 }}
    >
      <Num size="value" weight={600} color={color}>
        {letter ?? "—"}
      </Num>
      <Num size="md" weight={500} color="var(--text-secondary)">
        {`${Math.round(pct)}/100`}
      </Num>
    </span>
  );

  // The single rounded bar (pill corners, 1px hairline frame, signal fill).
  const bar = (
    <div
      role="img"
      aria-label={`${label} confidence ${
        isSuppressed ? "not scored" : `${Math.round(pct)} of 100`
      }`}
      style={{
        width: "100%",
        height: compact ? 8 : 10,
        border: "1px solid var(--glass-border)",
        borderRadius: "var(--radius-pill)",
        background: "var(--bg-base)",
        overflow: "hidden",
      }}
    >
      {!isSuppressed && (
        <div
          style={{
            width: `${pct}%`,
            height: "100%",
            background: color,
            borderRadius: "var(--radius-pill)",
            transition: "width 200ms var(--ease-quartr)",
          }}
        />
      )}
    </div>
  );

  if (compact) {
    return (
      <div
        style={{
          display: "flex",
          flexDirection: "column",
          gap: 6,
        }}
      >
        <div
          style={{
            display: "flex",
            alignItems: "baseline",
            justifyContent: "space-between",
            gap: 10,
          }}
        >
          {labelEl}
          {isSuppressed ? (
            <span
              style={{
                fontFamily: "var(--font-display)",
                fontSize: 13,
                fontWeight: 500,
                color: "var(--text-tertiary)",
              }}
            >
              Not scored yet
            </span>
          ) : (
            readout
          )}
        </div>
        {bar}
      </div>
    );
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
      <div
        style={{
          display: "flex",
          alignItems: "baseline",
          justifyContent: "space-between",
          gap: 12,
        }}
      >
        {labelEl}
        {readout}
      </div>
      {isSuppressed ? (
        <span
          style={{
            fontFamily: "var(--font-display)",
            fontSize: 15,
            fontWeight: 400,
            lineHeight: 1.5,
            color: "var(--text-secondary)",
          }}
        >
          Not enough track record to score yet.
        </span>
      ) : (
        bar
      )}
    </div>
  );
}
