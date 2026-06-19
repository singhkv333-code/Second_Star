"use client";

/**
 * PayoffChart — the single canonical strategy payoff chart, rendered in BOTH
 * the compact OptionStrategyCard sparkline and the full OptionStrategyPanel
 * Payoff tab so the two never diverge. It draws:
 *   - the expiry P&L as a zero-split area (green above 0, red below),
 *   - the smooth theoretical "today" (T+0) curve in blue on top,
 *   - and, in full mode only, a faint per-strike Call/Put OI histogram on a
 *     right-hand axis, ±1/±2 SD bands, breakevens, target + current-price
 *     markers, axes and a zoom toggle.
 *
 * `compact` strips everything but the two payoff curves + zero line for the
 * in-card sparkline. Gradient ids are per-instance (useId) so multiple charts
 * on one page don't collide.
 */

import { useId, useState } from "react";
import { ZoomIn, ZoomOut } from "lucide-react";
import {
  Area,
  Bar,
  CartesianGrid,
  ComposedChart,
  Line,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { OptionChainSlice } from "@/lib/types";

// Pivot's UI font (Inter) so the tooltip matches the rest of the app/chart
// instead of falling back to the OS system font.
const SANS_FONT = "var(--font-ui)";

type Point = { s: number; pnl: number };

function inr(n: number): string {
  return new Intl.NumberFormat("en-IN", {
    style: "currency",
    currency: "INR",
    maximumFractionDigits: 0,
  }).format(n);
}

/** Compact Open-Interest tick (3Cr / 1.2Cr / 45L). */
export function fmtOi(n: number): string {
  const abs = Math.abs(n);
  if (abs >= 1e7) return `${(n / 1e7).toFixed(abs >= 1e8 ? 0 : 1)}Cr`;
  if (abs >= 1e5) return `${(n / 1e5).toFixed(0)}L`;
  if (abs >= 1e3) return `${(n / 1e3).toFixed(0)}K`;
  return `${n}`;
}

/** Linear interpolation of a payoff curve at an arbitrary underlying price. */
function interp(arr: Point[], s: number): number | undefined {
  if (arr.length === 0) return undefined;
  if (s <= arr[0]!.s) return arr[0]!.pnl;
  if (s >= arr[arr.length - 1]!.s) return arr[arr.length - 1]!.pnl;
  for (let i = 1; i < arr.length; i++) {
    const a = arr[i - 1]!;
    const b = arr[i]!;
    if (s <= b.s) {
      const t = (s - a.s) / (b.s - a.s || 1);
      return a.pnl + t * (b.pnl - a.pnl);
    }
  }
  return arr[arr.length - 1]!.pnl;
}

export function PayoffChart({
  data,
  now,
  breakevens,
  forward,
  target,
  sd,
  chain,
  height = 140,
  compact = false,
}: {
  data: Point[];
  /** Theoretical T+0 curve (smooth blue line), interpolated onto the x-axis. */
  now?: Point[];
  breakevens: number[];
  forward: number;
  target?: number | null;
  /** 1-SD move (₹) around the forward; null/absent hides the SD bands. */
  sd?: number | null;
  /** Live chain — supplies the per-strike OI histogram (full mode only). */
  chain?: OptionChainSlice | null;
  height?: number;
  /** Card sparkline mode — only the two payoff curves + zero line. */
  compact?: boolean;
}): React.ReactElement {
  const rawId = useId();
  const gid = `pf-${rawId.replace(/[^a-zA-Z0-9]/g, "")}`;
  const [zoomed, setZoomed] = useState(true);

  if (data.length === 0) {
    return (
      <div
        className="flex items-center justify-center text-[11.5px] text-muted-foreground"
        style={{ height }}
      >
        No payoff data
      </div>
    );
  }

  const sMin = data[0]!.s;
  const sMax = data[data.length - 1]!.s;
  const showZoom = !compact && !!sd && sd > 0;
  const domain: [number, number] =
    showZoom && zoomed
      ? [Math.max(sMin, forward - 2.2 * sd!), Math.min(sMax, forward + 2.2 * sd!)]
      : [sMin, sMax];
  const inView = (x: number): boolean => x >= domain[0] && x <= domain[1];

  const hasNow = Array.isArray(now) && now.length > 0;

  // Per-strike OI (full mode only).
  const strikeMap = new Map<number, { ce: number; pe: number }>();
  if (!compact && chain) {
    for (const r of chain.rows) {
      strikeMap.set(r.strike, { ce: r.ce?.oi ?? 0, pe: r.pe?.oi ?? 0 });
    }
  }
  const hasOi = strikeMap.size > 0;

  // Merge onto one numeric x-axis; interpolate both curves at strike x's so the
  // lines stay smooth where OI bars are inserted.
  const xs = new Set<number>(data.map((p) => p.s));
  for (const k of strikeMap.keys()) if (inView(k)) xs.add(k);
  const merged = [...xs]
    .filter(inView)
    .sort((a, b) => a - b)
    .map((s) => {
      const oi = strikeMap.get(s);
      return {
        s,
        pnl: interp(data, s) ?? 0,
        now: hasNow ? interp(now!, s) : undefined,
        ceOi: oi?.ce,
        peOi: oi?.pe,
      };
    });

  // Zero-split gradient offset.
  const pnls = merged.map((d) => d.pnl);
  const maxP = Math.max(...pnls, 0);
  const minP = Math.min(...pnls, 0);
  const zeroOffset = maxP - minP === 0 ? 0.5 : maxP / (maxP - minP);

  const sdLines =
    !compact && sd && sd > 0
      ? [
          { x: forward - 2 * sd, label: "-2 SD" },
          { x: forward - sd, label: "-1 SD" },
          { x: forward + sd, label: "1 SD" },
          { x: forward + 2 * sd, label: "2 SD" },
        ].filter((l) => inView(l.x))
      : [];

  return (
    <div className="relative" style={{ height }} data-testid="option-payoff-chart">
      {showZoom && (
        <button
          type="button"
          onClick={() => setZoomed((z) => !z)}
          className="absolute right-1 top-0 z-10 inline-flex items-center gap-1 rounded-md border border-border/60 bg-background/80 px-2 py-1 text-[10.5px] font-medium text-muted-foreground backdrop-blur hover:bg-muted hover:text-foreground"
        >
          {zoomed ? <ZoomOut className="h-3 w-3" aria-hidden="true" /> : <ZoomIn className="h-3 w-3" aria-hidden="true" />}
          {zoomed ? "Zoom out" : "Zoom in"}
        </button>
      )}
      <ResponsiveContainer width="100%" height="100%">
        <ComposedChart
          data={merged}
          margin={
            compact
              ? { top: 8, right: 12, left: 0, bottom: 0 }
              : { top: 18, right: hasOi ? 46 : 12, left: 0, bottom: 0 }
          }
        >
          <defs>
            <linearGradient id={`${gid}-fill`} x1="0" y1="0" x2="0" y2="1">
              <stop offset={0} stopColor="var(--color-profit)" stopOpacity={0.35} />
              <stop offset={zeroOffset} stopColor="var(--color-profit)" stopOpacity={0.05} />
              <stop offset={zeroOffset} stopColor="var(--color-loss)" stopOpacity={0.05} />
              <stop offset={1} stopColor="var(--color-loss)" stopOpacity={0.35} />
            </linearGradient>
            <linearGradient id={`${gid}-stroke`} x1="0" y1="0" x2="0" y2="1">
              <stop offset={0} stopColor="var(--color-profit)" />
              <stop offset={zeroOffset} stopColor="var(--color-profit)" />
              <stop offset={zeroOffset} stopColor="var(--color-loss)" />
              <stop offset={1} stopColor="var(--color-loss)" />
            </linearGradient>
          </defs>

          {!compact && <CartesianGrid strokeDasharray="2 4" opacity={0.12} vertical={false} />}

          <XAxis
            dataKey="s"
            type="number"
            domain={domain}
            allowDataOverflow
            hide={compact}
            tick={{ fontSize: 9, fill: "rgb(107 114 128)" }}
            axisLine={false}
            tickLine={false}
            tickFormatter={(v: number) => v.toLocaleString("en-IN")}
            minTickGap={44}
          />
          <YAxis
            yAxisId="pnl"
            hide={compact}
            tick={{ fontSize: 9, fill: "rgb(107 114 128)" }}
            axisLine={false}
            tickLine={false}
            width={48}
            tickFormatter={(v: number) =>
              Math.abs(v) >= 1000
                ? `${v >= 0 ? "" : "-"}₹${(Math.abs(v) / 1000).toFixed(0)}K`
                : `₹${v.toFixed(0)}`
            }
          />
          {!compact && hasOi && (
            <YAxis
              yAxisId="oi"
              orientation="right"
              tick={{ fontSize: 9, fill: "rgb(148 163 184)" }}
              axisLine={false}
              tickLine={false}
              width={40}
              tickFormatter={(v: number) => fmtOi(v)}
            />
          )}

          {!compact && (
            <Tooltip
              contentStyle={{
                fontSize: "11px",
                borderRadius: "8px",
                padding: "5px 10px",
                border: "1px solid rgba(0,0,0,0.08)",
                fontFamily: SANS_FONT,
              }}
              formatter={(value: number, name: string) => {
                if (name === "ceOi") return [fmtOi(value), "Call OI"];
                if (name === "peOi") return [fmtOi(value), "Put OI"];
                if (name === "now") return [inr(value), "P&L today"];
                return [inr(value), "P&L @ expiry"];
              }}
              labelFormatter={(label: number) => `Underlying: ${label.toLocaleString("en-IN")}`}
            />
          )}

          {/* OI histogram behind the payoff (full mode) */}
          {!compact && hasOi && (
            <Bar yAxisId="oi" dataKey="ceOi" fill="var(--color-profit)" fillOpacity={0.16} barSize={5} />
          )}
          {!compact && hasOi && (
            <Bar yAxisId="oi" dataKey="peOi" fill="var(--color-loss)" fillOpacity={0.16} barSize={5} />
          )}

          {/* Expiry payoff — kinked, zero-split fill */}
          <Area
            yAxisId="pnl"
            type="monotone"
            dataKey="pnl"
            stroke={`url(#${gid}-stroke)`}
            strokeWidth={hasNow ? 1.5 : 2}
            strokeOpacity={hasNow ? 0.6 : 1}
            fill={`url(#${gid}-fill)`}
            dot={false}
            activeDot={compact ? false : { r: 3 }}
            isAnimationActive={false}
          />
          {/* Theoretical "today" (T+0) curve — smooth blue */}
          {hasNow && (
            <Line
              yAxisId="pnl"
              type="monotone"
              dataKey="now"
              stroke="#2563eb"
              strokeWidth={2}
              dot={false}
              activeDot={compact ? false : { r: 3 }}
              isAnimationActive={false}
              connectNulls
            />
          )}

          <ReferenceLine yAxisId="pnl" y={0} stroke="rgb(107 114 128)" strokeDasharray="3 3" strokeOpacity={0.5} />

          {sdLines.map((l) => (
            <ReferenceLine
              key={l.label}
              yAxisId="pnl"
              x={l.x}
              stroke="rgb(148 163 184)"
              strokeOpacity={0.45}
              strokeDasharray="2 3"
              label={{ value: l.label, position: "top", fontSize: 9, fill: "rgb(148 163 184)" }}
            />
          ))}

          {!compact &&
            breakevens.filter(inView).map((be) => (
              <ReferenceLine
                key={be}
                yAxisId="pnl"
                x={be}
                stroke="#f59e0b"
                strokeWidth={1}
                strokeDasharray="3 3"
                label={{ value: "BE", position: "insideTopRight", fontSize: 9, fill: "#f59e0b" }}
              />
            ))}

          {target != null && inView(target) && (
            <ReferenceLine
              yAxisId="pnl"
              x={target}
              stroke="#0ea5e9"
              strokeWidth={1.5}
              strokeDasharray={compact ? "4 2" : undefined}
              label={compact ? undefined : { value: "Target", position: "insideBottomRight", fontSize: 9, fill: "#0ea5e9" }}
            />
          )}

          {/* Current price (forward) */}
          <ReferenceLine
            yAxisId="pnl"
            x={forward}
            stroke={compact ? "#6366f1" : "#475569"}
            strokeWidth={1.5}
            strokeDasharray={compact ? "4 2" : undefined}
            label={
              compact
                ? undefined
                : {
                    value: `Current ${Math.round(forward).toLocaleString("en-IN")}`,
                    position: "top",
                    fontSize: 9.5,
                    fill: "#334155",
                    fontWeight: 600,
                  }
            }
          />
        </ComposedChart>
      </ResponsiveContainer>
    </div>
  );
}
