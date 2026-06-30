"use client";

/**
 * EventReturns — the "when the event happened + how it paid off" list. Each row
 * is one past episode: its label + date on the left, the strategy's own return
 * (signed, coloured, with a small magnitude bar) on the right, and the
 * benchmark's faint return beside it for context. A header line summarises
 * "Positive in N of M past events". Numbers are right-aligned in fixed columns.
 *
 * DESIGN LAW: rounded, border-only, every label >= 13px, tabular numerals,
 * aligned columns, light + dark via useTokenColors. Empty → renders nothing
 * (the caller omits it; a developing view with no episodes shows no list).
 */

import * as React from "react";
import { useTokenColors } from "../use-token-color";

/** One past episode, as served on an expression's `episodes`. */
export type EventEpisode = {
  label: string;
  date: string;
  return_pct: number;
  benchmark_pct?: number | null;
  positive?: boolean | null;
};

const VAL_W = 70;
const BENCH_W = 100;

function signed(v: number, dp = 1): string {
  const s = v > 0 ? "+" : v < 0 ? "−" : "";
  return `${s}${Math.abs(v).toFixed(dp)}%`;
}

export function EventReturns({
  episodes,
  positiveEpisodes,
  benchmarkLabel = "Nifty",
}: {
  episodes?: EventEpisode[] | null;
  positiveEpisodes?: number | null;
  benchmarkLabel?: string;
}): React.ReactElement | null {
  const c = useTokenColors({
    profit: "--color-profit",
    loss: "--color-loss",
    ink: "--text-primary",
    secondary: "--text-secondary",
    tertiary: "--text-tertiary",
    border: "--glass-border",
    bgBase: "--bg-base",
  });

  const safe = (Array.isArray(episodes) ? episodes : []).filter(
    (e) => e && typeof e.return_pct === "number",
  );
  if (safe.length === 0) return null;

  const n = safe.length;
  const pos =
    typeof positiveEpisodes === "number"
      ? positiveEpisodes
      : safe.filter((e) => (e.positive ?? e.return_pct >= 0)).length;

  return (
    <div
      style={{
        border: `1px solid ${c.border}`,
        borderRadius: "var(--radius-lg)",
        background: c.bgBase,
        padding: "16px 18px",
        display: "flex",
        flexDirection: "column",
        gap: 12,
      }}
    >
      <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
        <span
          style={{
            fontFamily: "var(--font-display)",
            fontSize: 14,
            fontWeight: 600,
            color: c.ink,
            letterSpacing: "-0.01em",
          }}
        >
          Positive in {pos} of {n} past events
        </span>
        <span
          style={{
            fontFamily: "var(--font-display)",
            fontSize: 13,
            color: c.tertiary,
          }}
        >
          Return after the event, vs {benchmarkLabel}
        </span>
      </div>

      <div style={{ display: "flex", flexDirection: "column" }}>
        {safe.map((e, i) => {
          const up = e.return_pct >= 0;
          const color = up ? c.profit : c.loss;
          const hasBench = typeof e.benchmark_pct === "number";
          return (
            <div
              key={`${e.label}-${i}`}
              style={{
                display: "flex",
                alignItems: "center",
                gap: 12,
                padding: "9px 0",
                borderTop: i === 0 ? "none" : `1px solid ${c.border}`,
              }}
            >
              {/* Date — the single, un-truncated event label */}
              <span
                style={{
                  flex: 1,
                  minWidth: 0,
                  fontFamily: "var(--font-display)",
                  fontSize: 13,
                  fontWeight: 500,
                  color: c.ink,
                  whiteSpace: "nowrap",
                }}
              >
                {e.date}
              </span>

              {/* Strategy return */}
              <span
                style={{
                  width: VAL_W,
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
                {signed(e.return_pct)}
              </span>

              {/* Benchmark return, faint */}
              <span
                style={{
                  width: BENCH_W,
                  flexShrink: 0,
                  textAlign: "right",
                  fontFamily: "var(--font-display)",
                  fontVariantNumeric: "tabular-nums",
                  fontSize: 13,
                  color: c.tertiary,
                }}
              >
                {hasBench
                  ? `${benchmarkLabel} ${signed(e.benchmark_pct as number)}`
                  : "—"}
              </span>
            </div>
          );
        })}
      </div>
    </div>
  );
}

export default EventReturns;
