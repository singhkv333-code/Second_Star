"use client";

/**
 * EventReturns — the "when the event happened + how it paid off" list. Each row
 * is one past episode: its label + date on the left, the strategy's own return
 * (signed, coloured, with a thin magnitude bar) on the right. A header line
 * summarises "Positive in N of M past events". Numbers are right-aligned in a
 * fixed column. No benchmark figure is ever rendered here — the only
 * performance number a user sees is the strategy's own return.
 *
 * The list defaults to the first 6 rows; beyond that a rounded, border-only
 * "Show all N occurrences" toggle expands the full list ("Show fewer" to
 * collapse again).
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
const DEFAULT_VISIBLE = 6;

function signed(v: number, dp = 1): string {
  const s = v > 0 ? "+" : v < 0 ? "−" : "";
  return `${s}${Math.abs(v).toFixed(dp)}%`;
}

export function EventReturns({
  episodes,
  positiveEpisodes,
  benchmarkLabel: _benchmarkLabel = "Nifty",
  evidenceBasis = null,
}: {
  episodes?: EventEpisode[] | null;
  positiveEpisodes?: number | null;
  /** Accepted for caller compatibility — no benchmark figure is ever rendered. */
  benchmarkLabel?: string;
  /** "rolling_windows" episodes are history sliced into windows, NOT distinct
      events — the copy must never call them events (doctrine A2). */
  evidenceBasis?: "rolling_windows" | "shock_no_analogs" | null;
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
  const [expanded, setExpanded] = React.useState(false);

  const safe = (Array.isArray(episodes) ? episodes : []).filter(
    (e) => e && typeof e.return_pct === "number",
  );
  if (safe.length === 0) return null;

  const n = safe.length;
  const pos =
    typeof positiveEpisodes === "number"
      ? positiveEpisodes
      : safe.filter((e) => (e.positive ?? e.return_pct >= 0)).length;

  const maxAbs = Math.max(...safe.map((e) => Math.abs(e.return_pct)), 1e-6);
  const hasMore = n > DEFAULT_VISIBLE;
  const visible = expanded ? safe : safe.slice(0, DEFAULT_VISIBLE);

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
          {evidenceBasis === "rolling_windows"
            ? `Positive in ${pos} of ${n} rolling windows`
            : `Positive in ${pos} of ${n} past events`}
        </span>
        <span
          style={{
            fontFamily: "var(--font-display)",
            fontSize: 13,
            color: c.tertiary,
          }}
        >
          {evidenceBasis === "rolling_windows"
            ? "Return over each window — history sliced into windows, not distinct events"
            : "Return after the event"}
        </span>
      </div>

      <div style={{ display: "flex", flexDirection: "column" }}>
        {visible.map((e, i) => {
          const up = e.return_pct >= 0;
          const color = up ? c.profit : c.loss;
          const barPct = Math.max(
            (Math.abs(e.return_pct) / maxAbs) * 100,
            3,
          );
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

              {/* Thin magnitude bar — width proportional to |return|, so the
                  row reads as a mini-viz rather than empty space now that the
                  benchmark column is gone. */}
              <div
                style={{
                  flex: 1,
                  minWidth: 40,
                  maxWidth: 140,
                  height: 6,
                  borderRadius: "var(--radius-pill)",
                  background: c.border,
                  overflow: "hidden",
                }}
              >
                <div
                  style={{
                    width: `${barPct}%`,
                    height: "100%",
                    marginLeft: "auto",
                    background: color,
                    borderRadius: "var(--radius-pill)",
                  }}
                />
              </div>

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
            </div>
          );
        })}
      </div>

      {hasMore && (
        <button
          type="button"
          onClick={() => setExpanded((v) => !v)}
          style={{
            alignSelf: "flex-start",
            fontFamily: "var(--font-display)",
            fontSize: 13,
            fontWeight: 500,
            color: c.secondary,
            background: "transparent",
            border: `1px solid ${c.border}`,
            borderRadius: "var(--radius-pill)",
            padding: "6px 14px",
            cursor: "pointer",
          }}
        >
          {expanded ? "Show fewer" : `Show all ${n} occurrences`}
        </button>
      )}
    </div>
  );
}

export default EventReturns;
