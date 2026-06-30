"use client";

/**
 * TrustLadder — a 4-step verdict stepper:
 *   INSUFFICIENT_DATA → NO_EDGE → UNPROVEN → PROMISING
 *
 * Segments up to and including the current verdict fill with the verdict color;
 * future segments stay muted. The current verdict is named once, in plain words
 * (>= 13px). Null verdict → all muted + "Not yet evaluated".
 *
 * DESIGN LAW: SQUARE segments (radius 0), no fills beyond the signal color, all
 * text >= 13px, no raw confidence token (the de-jargoned word carries it).
 */

import type { TrustVerdict } from "@/lib/types";
import {
  TRUST_STEPS,
  verdictColor,
  verdictLabel,
  verdictStepIndex,
} from "../view-format";

export function TrustLadder({
  verdict,
  // trustConf is accepted to keep the call site stable, but never rendered
  // (the de-jargoned verdict word carries the meaning; a raw 0-100 conf token
  // is banned on screen).
  trustConf: _trustConf,
}: {
  verdict: TrustVerdict | null;
  trustConf?: number | null;
}): React.ReactElement {
  const idx = verdictStepIndex(verdict);
  const color = verdictColor(verdict);
  const evaluated = idx >= 0;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
      <div
        style={{
          display: "flex",
          alignItems: "baseline",
          justifyContent: "space-between",
          gap: 8,
        }}
      >
        <span
          style={{
            fontFamily: "var(--font-display)",
            fontSize: 13,
            fontWeight: 500,
            color: "var(--text-tertiary)",
          }}
        >
          Track record
        </span>
        <span
          style={{
            fontFamily: "var(--font-display)",
            fontSize: 15,
            fontWeight: 600,
            color: evaluated ? color : "var(--text-tertiary)",
          }}
        >
          {evaluated ? verdictLabel(verdict) : "Not yet evaluated"}
        </span>
      </div>

      <div style={{ display: "flex", gap: 4 }}>
        {TRUST_STEPS.map((step, i) => {
          const reached = evaluated && i <= idx;
          return (
            <div
              key={step}
              title={verdictLabel(step)}
              className="rounded-full"
              style={{
                flex: 1,
                height: 6,
                borderRadius: "var(--radius-pill)",
                background: reached ? color : "var(--glass-border)",
                transition: "background 240ms var(--ease-quartr)",
              }}
            />
          );
        })}
      </div>
    </div>
  );
}
