"use client";

/**
 * ReturnsChart — the LEFT column of the View-detail redesign.
 *
 * Visual language borrows the CLEAN Kalshi market-chart look (thin multi-line
 * series, a top-left legend that shows each line's live value, dotted horizontal
 * gridlines, a right-hand axis, timeframe toggles) — but the CONTENT stays
 * Pivot-honest: these are real strategy expected-value paths in ₹, not YES/NO
 * contract prices.
 *
 *   x = "Days in market"  ·  y = ₹ value (right axis)
 *   · dashed grey line   → the Nifty baseline ("own the whole market")
 *   · one thin solid line → each strategy's expected-value path
 *
 * Rescales live off the amount typed into the calculator, and the timeframe
 * chips (1M · 3M · 6M) window the same underlying 6-month path. Light + dark via
 * useTokenColors.
 */

import * as React from "react";
import {
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { useTokenColors } from "@/components/views/use-token-color";
import {
  inr,
  NIFTY_COLOR,
  niftyBaselinePath,
  strategyPath,
  type StrategyConfig,
} from "./strategies";

const HORIZON_DAYS = 180;
const STEPS = 18; // → one point every 10 days, so 30/90/180 land exactly on a point.

const WINDOWS = [
  { key: "1M", days: 30 },
  { key: "3M", days: 90 },
  { key: "6M", days: 180 },
] as const;

const BASELINE_KEY = "nifty";
const stratKey = (id: string): string => `s_${id}`;

type Row = Record<string, number>;

function buildRows(amount: number, strategies: StrategyConfig[]): Row[] {
  const byDay = new Map<number, Row>();
  const base = niftyBaselinePath(amount, HORIZON_DAYS, STEPS);
  for (const p of base) byDay.set(p.day, { day: p.day, [BASELINE_KEY]: p.value });
  for (const s of strategies) {
    const path = strategyPath(amount, s, HORIZON_DAYS, STEPS);
    for (const p of path) {
      const row = byDay.get(p.day) ?? { day: p.day };
      row[stratKey(s.id)] = p.value;
      byDay.set(p.day, row);
    }
  }
  return Array.from(byDay.values()).sort((a, b) => (a.day ?? 0) - (b.day ?? 0));
}

function fmtYTick(v: number): string {
  // Adaptive to magnitude so neighbouring ticks never collide onto the same
  // label: full rupees for tiny amounts, k in the normal range, L for large.
  const a = Math.abs(v);
  if (a >= 1e6) return `₹${(v / 1e5).toFixed(1)}L`;
  if (a >= 1e4) return `₹${Math.round(v / 1000)}k`;
  return `₹${Math.round(v).toLocaleString("en-IN")}`;
}

function fmtPct(frac: number): string {
  const s = frac > 0 ? "+" : frac < 0 ? "−" : "";
  return `${s}${Math.abs(frac * 100).toFixed(1)}%`;
}

/** Kalshi-style legend entry: colored dot · name · live value. */
function LegendItem({
  color,
  dashed,
  label,
  value,
  valueColor,
  secondary,
}: {
  color: string;
  dashed?: boolean;
  label: string;
  value: string;
  valueColor: string;
  secondary: string;
}): React.ReactElement {
  return (
    <span
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: 8,
        fontFamily: "var(--font-display)",
        fontSize: 13,
        whiteSpace: "nowrap",
      }}
    >
      <span
        aria-hidden
        style={{
          width: 16,
          height: 0,
          borderTop: dashed ? `2px dashed ${color}` : `2.5px solid ${color}`,
          borderRadius: 2,
        }}
      />
      <span style={{ color: secondary, fontWeight: 500 }}>{label}</span>
      <span
        style={{
          color: valueColor,
          fontWeight: 700,
          fontVariantNumeric: "tabular-nums",
        }}
      >
        {value}
      </span>
    </span>
  );
}

export function ReturnsChart({
  amount,
  strategies,
  highlightId,
}: {
  amount: number;
  strategies: StrategyConfig[];
  /** Optional: the currently-selected strategy is drawn slightly bolder. */
  highlightId?: string | null;
}): React.ReactElement {
  const [windowDays, setWindowDays] = React.useState<number>(HORIZON_DAYS);

  const c = useTokenColors({
    blue: "--pivot-blue",
    profit: "--color-profit",
    loss: "--color-loss",
    tertiary: "--text-tertiary",
    secondary: "--text-secondary",
    border: "--glass-border",
    borderFocus: "--glass-border-focus",
    bg: "--bg-base",
    ink: "--text-primary",
  });

  const allRows = React.useMemo(
    () => buildRows(amount, strategies),
    [amount, strategies],
  );
  const rows = React.useMemo(
    () => allRows.filter((r) => (r.day ?? 0) <= windowDays),
    [allRows, windowDays],
  );

  // Live value of each series at the visible window's end (drives the legend).
  const endRow = rows[rows.length - 1];
  const valueOf = (key: string): number =>
    (endRow?.[key] as number | undefined) ?? amount;

  const labelById = React.useMemo(() => {
    const m = new Map<string, string>();
    for (const s of strategies) m.set(stratKey(s.id), s.name);
    m.set(BASELINE_KEY, "Nifty");
    return m;
  }, [strategies]);

  return (
    // Kalshi-clean: the chart is NOT boxed — it floats directly on the page.
    // The trade ticket beside it is the only bordered object up here.
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        gap: 14,
        minWidth: 0,
      }}
    >
      {/* title + live legend (top-left, Kalshi-style) */}
      <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
        <span
          style={{
            fontFamily: "var(--font-display)",
            fontSize: 16,
            fontWeight: 600,
            letterSpacing: "-0.01em",
            color: "var(--text-primary)",
          }}
        >
          What ₹{amount.toLocaleString("en-IN")} could become
        </span>
        <div style={{ display: "flex", flexWrap: "wrap", gap: "6px 18px" }}>
          <LegendItem
            color={NIFTY_COLOR}
            dashed
            label="Nifty"
            value={inr(valueOf(BASELINE_KEY))}
            valueColor={c.secondary}
            secondary={c.secondary}
          />
          {strategies.map((s) => {
            const v = valueOf(stratKey(s.id));
            const ret = amount ? v / amount - 1 : 0;
            return (
              <LegendItem
                key={s.id}
                color={s.color}
                label={s.name}
                value={fmtPct(ret)}
                valueColor={ret >= 0 ? c.profit : c.loss}
                secondary={c.secondary}
              />
            );
          })}
        </div>
      </div>

      <div style={{ width: "100%", height: 340 }}>
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={rows} margin={{ top: 8, right: 8, bottom: 0, left: 4 }}>
            <CartesianGrid
              stroke={c.border}
              strokeDasharray="2 5"
              horizontal
              vertical={false}
            />
            <XAxis
              dataKey="day"
              type="number"
              domain={[0, windowDays]}
              tick={{ fontSize: 12, fill: c.tertiary, fontFamily: "var(--font-display)" }}
              tickLine={false}
              axisLine={false}
              tickCount={5}
              tickFormatter={(v: number) => `${v}d`}
            />
            <YAxis
              orientation="right"
              tick={{ fontSize: 12, fill: c.tertiary, fontFamily: "var(--font-display)" }}
              tickFormatter={fmtYTick}
              tickLine={false}
              axisLine={false}
              width={54}
              domain={["auto", "auto"]}
            />
            <Tooltip
              cursor={{ stroke: c.borderFocus, strokeWidth: 1 }}
              contentStyle={{
                borderRadius: 12,
                border: `1px solid ${c.border}`,
                background: c.bg,
                fontFamily: "var(--font-display)",
                fontSize: 13,
                fontVariantNumeric: "tabular-nums",
                color: c.ink,
              }}
              labelFormatter={(v) => `Day ${v} in market`}
              formatter={(value: number, name: string) => [
                inr(value),
                labelById.get(name) ?? name,
              ]}
            />
            <Line
              type="monotone"
              dataKey={BASELINE_KEY}
              stroke={NIFTY_COLOR}
              strokeWidth={1.75}
              strokeDasharray="5 5"
              dot={false}
              isAnimationActive={false}
            />
            {strategies.map((s) => {
              const active = highlightId === s.id;
              return (
                <Line
                  key={s.id}
                  type="monotone"
                  dataKey={stratKey(s.id)}
                  stroke={s.color}
                  strokeWidth={active ? 2.75 : 1.75}
                  strokeOpacity={highlightId && !active ? 0.4 : 1}
                  dot={false}
                  isAnimationActive={false}
                  connectNulls
                />
              );
            })}
          </LineChart>
        </ResponsiveContainer>
      </div>

      {/* footer: context caption (left) + timeframe toggle (right), Kalshi-style */}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          gap: 12,
          borderTop: `1px solid ${c.border}`,
          paddingTop: 12,
          flexWrap: "wrap",
        }}
      >
        <span
          style={{
            fontFamily: "var(--font-display)",
            fontSize: 12.5,
            color: "var(--text-tertiary)",
          }}
        >
          Expected value by days in market · modelled, not guaranteed
        </span>
        <div style={{ display: "flex", gap: 2 }}>
          {WINDOWS.map((w) => {
            const on = windowDays === w.days;
            return (
              <button
                key={w.key}
                onClick={() => setWindowDays(w.days)}
                aria-pressed={on}
                style={{
                  fontFamily: "var(--font-display)",
                  fontSize: 12.5,
                  fontWeight: 600,
                  color: on ? "var(--text-primary)" : "var(--text-tertiary)",
                  background: on ? "var(--surface-active)" : "transparent",
                  border: "none",
                  borderRadius: 8,
                  padding: "4px 10px",
                  cursor: "pointer",
                }}
              >
                {w.key}
              </button>
            );
          })}
        </div>
      </div>
    </div>
  );
}

export default ReturnsChart;
