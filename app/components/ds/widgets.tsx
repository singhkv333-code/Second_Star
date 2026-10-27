"use client";

/**
 * Pivot design system — chat widgets.
 *
 * The cards Pivot renders inline in chat, rebuilt with more
 * information per square inch and honest chart geometry:
 *
 *   StockSnapshotWidget — price + area chart + returns ladder + 52w band
 *   PayoffWidget        — option payoff with shaded P/L zones, breakeven,
 *                         strikes, and the max-loss/max-profit ledger
 *   BacktestWidget      — equity vs benchmark, drawdown strip, metrics
 *
 * All charts are inline SVG, monochrome ink with the profit/loss pair
 * doing the only color work. Numbers are set in --font-numeric.
 */

import * as React from "react";
import { cn } from "@/lib/utils";
import { Delta, Figure, MonoTag, Title } from "./primitives";
import { Panel } from "./surfaces";

/* ── shared helpers ────────────────────────────────────────────────── */

function scalePoints(
  data: number[],
  w: number,
  h: number,
  padY = 3,
): { pts: string; min: number; max: number } {
  const min = Math.min(...data);
  const max = Math.max(...data);
  const span = max - min || 1;
  const stepX = w / (data.length - 1);
  const pts = data
    .map(
      (v, i) =>
        `${(i * stepX).toFixed(1)},${(h - padY - ((v - min) / span) * (h - padY * 2)).toFixed(1)}`,
    )
    .join(" ");
  return { pts, min, max };
}

function widgetLabel(text: string): React.ReactElement {
  return (
    <span
      style={{
        fontFamily: "var(--font-mono)",
        fontSize: 9.5,
        fontWeight: 500,
        letterSpacing: "0.1em",
        textTransform: "uppercase",
        color: "var(--text-tertiary)",
      }}
    >
      {text}
    </span>
  );
}

/* ────────────────────────────────────────────────────────────────────
 * Stock snapshot
 * ──────────────────────────────────────────────────────────────────── */

export interface SnapshotReturns {
  label: string;
  pct: number;
}

export function StockSnapshotWidget({
  symbol,
  name,
  exchange = "NSE",
  price,
  changePct,
  series,
  returns,
  week52: { low, high, last },
  className,
}: {
  symbol: string;
  name: string;
  exchange?: string;
  price: string;
  changePct: number;
  /** Closing series for the area chart. */
  series: number[];
  /** Returns ladder (1W/1M/3M/6M/1Y). */
  returns: SnapshotReturns[];
  /** 52-week band; `last` positions the marker. */
  week52: { low: number; high: number; last: number };
  className?: string;
}) {
  const W = 360;
  const H = 96;
  const { pts } = scalePoints(series, W, H);
  const up = (series[series.length - 1] ?? 0) >= (series[0] ?? 0);
  const lineColor = up ? "var(--color-profit)" : "var(--color-loss)";
  const gradId = React.useId();
  const pos52 = Math.min(
    1,
    Math.max(0, (last - low) / Math.max(high - low, 1e-9)),
  );

  return (
    <Panel pad={0} className={cn("max-w-md overflow-hidden", className)}>
      {/* Header */}
      <div className="flex items-start justify-between gap-3 px-5 pt-4">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <Title size={15}>{symbol}</Title>
            <MonoTag tone="fill">{exchange}</MonoTag>
          </div>
          <div
            style={{
              fontFamily: "var(--font-ui)",
              fontSize: 12,
              color: "var(--text-tertiary)",
              marginTop: 2,
            }}
          >
            {name}
          </div>
        </div>
        <div className="shrink-0 text-right">
          <Figure size={21} weight={600}>
            {price}
          </Figure>
          <div className="mt-1 flex justify-end">
            <Delta value={changePct} size={12} />
          </div>
        </div>
      </div>

      {/* Area chart */}
      <svg
        viewBox={`0 0 ${W} ${H}`}
        className="mt-2 block w-full"
        style={{ height: H }}
        aria-label={`${symbol} price chart`}
      >
        <defs>
          <linearGradient id={gradId} x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor={lineColor} stopOpacity="0.16" />
            <stop offset="100%" stopColor={lineColor} stopOpacity="0" />
          </linearGradient>
        </defs>
        <polygon
          points={`0,${H} ${pts} ${W},${H}`}
          fill={`url(#${gradId})`}
        />
        <polyline
          points={pts}
          fill="none"
          stroke={lineColor}
          strokeWidth="1.6"
          strokeLinejoin="round"
          strokeLinecap="round"
          pathLength={1}
          className="ds-draw"
        />
      </svg>

      {/* Returns ladder */}
      <div
        className="grid grid-cols-5"
        style={{ borderTop: "1px solid var(--glass-border)" }}
      >
        {returns.map((r, i) => (
          <div
            key={r.label}
            className="flex flex-col items-center gap-1.5 py-3"
            style={{
              borderLeft: i === 0 ? "none" : "1px solid var(--glass-border)",
            }}
          >
            {widgetLabel(r.label)}
            <Delta value={r.pct} size={12} arrow={false} />
          </div>
        ))}
      </div>

      {/* 52-week band */}
      <div
        className="px-5 py-3.5"
        style={{ borderTop: "1px solid var(--glass-border)" }}
      >
        <div className="mb-2 flex items-center justify-between">
          {widgetLabel("52W range")}
          <Figure size={11.5} muted>
            ₹{low.toLocaleString("en-IN")} — ₹{high.toLocaleString("en-IN")}
          </Figure>
        </div>
        <div
          className="relative"
          style={{
            height: 4,
            borderRadius: 2,
            background: "var(--surface-active)",
          }}
        >
          <div
            aria-hidden
            style={{
              position: "absolute",
              insetBlock: 0,
              left: 0,
              width: `${pos52 * 100}%`,
              borderRadius: 2,
              background: "var(--glass-border-focus)",
            }}
          />
          <span
            aria-hidden
            style={{
              position: "absolute",
              top: "50%",
              left: `${pos52 * 100}%`,
              width: 9,
              height: 9,
              transform: "translate(-50%, -50%)",
              borderRadius: "50%",
              background: "var(--text-primary)",
              border: "2px solid var(--bg-card)",
            }}
          />
        </div>
      </div>
    </Panel>
  );
}

/* ────────────────────────────────────────────────────────────────────
 * Option payoff
 * ──────────────────────────────────────────────────────────────────── */

export interface PayoffSpec {
  /** Strategy display name, e.g. "Bull Call Spread". */
  strategy: string;
  underlying: string;
  /** Piecewise-linear payoff: sorted [spot, pnl] vertices incl. ends. */
  vertices: Array<[number, number]>;
  breakevens: number[];
  maxProfit: string;
  maxLoss: string;
  /** Optional strike markers. */
  strikes?: number[];
  /** Probability-of-profit string, e.g. "61%". */
  pop?: string;
}

export function PayoffWidget({
  spec,
  className,
}: {
  spec: PayoffSpec;
  className?: string;
}) {
  const W = 360;
  const H = 132;
  const PAD = 10;
  const xs = spec.vertices.map((v) => v[0]);
  const ys = spec.vertices.map((v) => v[1]);
  const xMin = Math.min(...xs);
  const xMax = Math.max(...xs);
  const yAbs = Math.max(...ys.map(Math.abs), 1);
  const X = (s: number) => PAD + ((s - xMin) / (xMax - xMin)) * (W - PAD * 2);
  const Y = (p: number) => H / 2 - (p / yAbs) * (H / 2 - PAD);
  const zeroY = Y(0);

  const line = spec.vertices
    .map(([s, p]) => `${X(s).toFixed(1)},${Y(p).toFixed(1)}`)
    .join(" ");
  // Profit region = area between the curve and the zero line, clipped
  // to where pnl > 0; loss region mirrors it. Build closed polygons by
  // walking vertices and inserting zero-crossings.
  const crossed: Array<[number, number]> = [];
  for (let i = 0; i < spec.vertices.length; i++) {
    const cur = spec.vertices[i]!;
    crossed.push(cur);
    const nxt = spec.vertices[i + 1];
    if (nxt && cur[1] * nxt[1] < 0) {
      const t = cur[1] / (cur[1] - nxt[1]);
      crossed.push([cur[0] + t * (nxt[0] - cur[0]), 0]);
    }
  }
  const zone = (sign: 1 | -1) =>
    crossed
      .map(([s, p]) => [s, sign * p > 0 ? p : 0] as [number, number])
      .map(([s, p]) => `${X(s).toFixed(1)},${Y(p).toFixed(1)}`)
      .join(" ");

  return (
    <Panel pad={0} className={cn("max-w-md overflow-hidden", className)}>
      <div className="flex items-start justify-between gap-3 px-5 pt-4 pb-1">
        <div>
          <Title size={15}>{spec.strategy}</Title>
          <div
            style={{
              fontFamily: "var(--font-ui)",
              fontSize: 12,
              color: "var(--text-tertiary)",
              marginTop: 2,
            }}
          >
            {spec.underlying}
          </div>
        </div>
        {spec.pop && (
          <div className="shrink-0 text-right">
            {widgetLabel("POP")}
            <div>
              <Figure size={17} weight={600}>
                {spec.pop}
              </Figure>
            </div>
          </div>
        )}
      </div>

      <svg
        viewBox={`0 0 ${W} ${H}`}
        className="block w-full"
        style={{ height: H }}
        aria-label={`${spec.strategy} payoff diagram`}
      >
        {/* P/L zones */}
        <polygon
          points={`${X(xMin)},${zeroY} ${zone(1)} ${X(xMax)},${zeroY}`}
          fill="var(--color-profit)"
          opacity="0.09"
        />
        <polygon
          points={`${X(xMin)},${zeroY} ${zone(-1)} ${X(xMax)},${zeroY}`}
          fill="var(--color-loss)"
          opacity="0.09"
        />
        {/* zero line */}
        <line
          x1={PAD}
          x2={W - PAD}
          y1={zeroY}
          y2={zeroY}
          stroke="var(--glass-border-focus)"
          strokeWidth="1"
          strokeDasharray="3 4"
        />
        {/* strikes */}
        {spec.strikes?.map((k) => (
          <g key={k}>
            <line
              x1={X(k)}
              x2={X(k)}
              y1={PAD}
              y2={H - PAD}
              stroke="var(--glass-border-hover)"
              strokeWidth="1"
            />
            <text
              x={X(k)}
              y={H - 2}
              textAnchor="middle"
              style={{
                font: "500 8.5px var(--font-mono)",
                fill: "var(--text-tertiary)",
              }}
            >
              {k.toLocaleString("en-IN")}
            </text>
          </g>
        ))}
        {/* breakevens */}
        {spec.breakevens.map((b) => (
          <g key={b}>
            <circle
              cx={X(b)}
              cy={zeroY}
              r="3.2"
              fill="var(--bg-card)"
              stroke="var(--text-primary)"
              strokeWidth="1.4"
            />
            <text
              x={X(b) - 8}
              y={zeroY - 8}
              textAnchor="end"
              style={{
                font: "500 8.5px var(--font-mono)",
                fill: "var(--text-secondary)",
              }}
            >
              BE {b.toLocaleString("en-IN")}
            </text>
          </g>
        ))}
        {/* payoff line */}
        <polyline
          points={line}
          fill="none"
          stroke="var(--text-primary)"
          strokeWidth="1.8"
          strokeLinejoin="round"
          strokeLinecap="round"
          pathLength={1}
          className="ds-draw"
        />
      </svg>

      {/* Ledger */}
      <div
        className="grid grid-cols-3"
        style={{ borderTop: "1px solid var(--glass-border)" }}
      >
        {(
          [
            ["Max profit", spec.maxProfit, "var(--color-profit)"],
            ["Max loss", spec.maxLoss, "var(--color-loss)"],
            [
              "Breakeven",
              spec.breakevens.map((b) => b.toLocaleString("en-IN")).join(" / "),
              "var(--text-primary)",
            ],
          ] as const
        ).map(([label, value, color], i) => (
          <div
            key={label}
            className="flex flex-col items-center gap-1.5 py-3"
            style={{
              borderLeft: i === 0 ? "none" : "1px solid var(--glass-border)",
            }}
          >
            {widgetLabel(label)}
            <span
              style={{
                fontFamily: "var(--font-numeric)",
                fontSize: 12.5,
                fontWeight: 500,
                letterSpacing: "-0.02em",
                color,
              }}
            >
              {value}
            </span>
          </div>
        ))}
      </div>
    </Panel>
  );
}

/* ────────────────────────────────────────────────────────────────────
 * Backtest
 * ──────────────────────────────────────────────────────────────────── */

export interface BacktestMetric {
  label: string;
  value: string;
  signedPct?: number;
}

export function BacktestWidget({
  title,
  period,
  equity,
  benchmark,
  metrics,
  verdict,
  className,
}: {
  title: string;
  period: string;
  /** Strategy equity curve (indexed, e.g. start 100). */
  equity: number[];
  /** Buy-and-hold benchmark, same length/index base. */
  benchmark: number[];
  metrics: BacktestMetric[];
  /** One-line verdict tag, e.g. "Beats buy & hold after costs". */
  verdict?: string;
  className?: string;
}) {
  const W = 360;
  const H = 110;
  const DH = 30; // drawdown strip height
  const all = [...equity, ...benchmark];
  const min = Math.min(...all);
  const max = Math.max(...all);
  const span = max - min || 1;
  const px = (i: number, n: number) => (i / (n - 1)) * W;
  const py = (v: number) => 4 + (1 - (v - min) / span) * (H - 8);
  const linePts = (arr: number[]) =>
    arr.map((v, i) => `${px(i, arr.length).toFixed(1)},${py(v).toFixed(1)}`).join(" ");

  // Drawdown from running peak, rendered as a filled strip below.
  let peak = -Infinity;
  const dd = equity.map((v) => {
    peak = Math.max(peak, v);
    return (v - peak) / peak; // ≤ 0
  });
  const ddMax = Math.min(...dd) || -0.0001;
  const ddPts = dd
    .map(
      (d, i) =>
        `${px(i, dd.length).toFixed(1)},${(2 + (d / ddMax) * (DH - 4)).toFixed(1)}`,
    )
    .join(" ");

  return (
    <Panel pad={0} className={cn("max-w-md overflow-hidden", className)}>
      <div className="flex items-start justify-between gap-3 px-5 pt-4 pb-1">
        <div>
          <Title size={15}>{title}</Title>
          <div
            style={{
              fontFamily: "var(--font-ui)",
              fontSize: 12,
              color: "var(--text-tertiary)",
              marginTop: 2,
            }}
          >
            {period}
          </div>
        </div>
        {verdict && <MonoTag tone="fill">{verdict}</MonoTag>}
      </div>

      {/* Equity vs benchmark */}
      <svg
        viewBox={`0 0 ${W} ${H}`}
        className="block w-full"
        style={{ height: H }}
        aria-label="Equity curve vs benchmark"
      >
        <polyline
          points={linePts(benchmark)}
          fill="none"
          stroke="var(--text-tertiary)"
          strokeWidth="1.2"
          strokeDasharray="2 4"
          strokeLinecap="round"
        />
        <polyline
          points={linePts(equity)}
          fill="none"
          stroke="var(--text-primary)"
          strokeWidth="1.7"
          strokeLinejoin="round"
          strokeLinecap="round"
          pathLength={1}
          className="ds-draw"
        />
      </svg>

      {/* Drawdown strip */}
      <div className="px-0" style={{ borderTop: "1px solid var(--glass-border)" }}>
        <div className="flex items-center justify-between px-5 pt-2">
          {widgetLabel("Drawdown")}
          <Figure size={11} muted>
            {(ddMax * 100).toFixed(1)}% max
          </Figure>
        </div>
        <svg
          viewBox={`0 0 ${W} ${DH}`}
          className="block w-full"
          style={{ height: DH }}
          aria-hidden
        >
          <polygon
            points={`0,0 ${ddPts} ${W},0`}
            fill="var(--color-loss)"
            opacity="0.16"
          />
          <polyline
            points={ddPts}
            fill="none"
            stroke="var(--color-loss)"
            strokeWidth="1"
            opacity="0.55"
          />
        </svg>
      </div>

      {/* Metrics */}
      <div
        className="grid grid-cols-4"
        style={{ borderTop: "1px solid var(--glass-border)" }}
      >
        {metrics.slice(0, 4).map((m, i) => (
          <div
            key={m.label}
            className="flex flex-col items-center gap-1.5 py-3"
            style={{
              borderLeft: i === 0 ? "none" : "1px solid var(--glass-border)",
            }}
          >
            {widgetLabel(m.label)}
            {m.signedPct !== undefined ? (
              <Delta value={m.signedPct} size={12} arrow={false} />
            ) : (
              <Figure size={12.5}>{m.value}</Figure>
            )}
          </div>
        ))}
      </div>

      {/* Legend */}
      <div
        className="flex items-center gap-5 px-5 py-2.5"
        style={{ borderTop: "1px solid var(--glass-border)" }}
      >
        <span className="flex items-center gap-2">
          <span
            aria-hidden
            style={{ width: 14, height: 2, background: "var(--text-primary)" }}
          />
          {widgetLabel("Strategy")}
        </span>
        <span className="flex items-center gap-2">
          <span
            aria-hidden
            style={{
              width: 14,
              height: 0,
              borderTop: "2px dashed var(--text-tertiary)",
            }}
          />
          {widgetLabel("Buy & hold")}
        </span>
      </div>
    </Panel>
  );
}
