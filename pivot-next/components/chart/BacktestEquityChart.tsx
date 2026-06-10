/**
 * BacktestEquityChart — the equity curve as a TradingView baseline series:
 * green above starting capital, red below, with a dashed break-even line at
 * the starting capital, an optional benchmark overlay (rebased to the same
 * start), buy/sell trade markers, and a live crosshair readout of NAV +
 * return-since-start. This is the hero of every backtest result ("the test").
 */
"use client";

import * as React from "react";
import {
  BaselineSeries,
  LineSeries,
  LineStyle,
  createSeriesMarkers,
  type IChartApi,
  type MouseEventParams,
  type SeriesMarker,
  type Time,
} from "lightweight-charts";
import { LightweightChart, toTime } from "./LightweightChart";
import { cn } from "@/lib/utils";

export type CurvePoint = { t: string; v: number };
export type TradeSignal = { t: string; side: "buy" | "sell" };

const UP = "#16a34a";
const DOWN = "#dc2626";
const BENCH = "#94a3b8";

type Props = {
  equity: CurvePoint[];
  /** Starting capital — the break-even baseline the curve is shaded around. */
  baseline: number;
  /** Optional benchmark curve (absolute values); rebased to `baseline`. */
  benchmark?: CurvePoint[] | null;
  signals?: TradeSignal[] | null;
  height?: number;
  className?: string;
};

export function BacktestEquityChart({
  equity,
  baseline,
  benchmark,
  signals,
  height = 240,
  className,
}: Props) {
  const [hover, setHover] = React.useState<{ nav: number; pct: number } | null>(null);

  const last = equity.length ? equity[equity.length - 1] : null;
  const endPct = last && baseline ? ((last.v - baseline) / baseline) * 100 : 0;
  const shown = hover ?? (last ? { nav: last.v, pct: endPct } : null);
  const up = (shown?.pct ?? 0) >= 0;

  return (
    <div className={cn("w-full", className)}>
      <div className="mb-1.5 flex items-baseline gap-2 font-mono text-[11px] text-slate-500">
        {shown ? (
          <>
            <span>
              NAV{" "}
              <b className="text-slate-900">
                ₹{shown.nav.toLocaleString("en-IN", { maximumFractionDigits: 0 })}
              </b>
            </span>
            <span className={up ? "text-green-600" : "text-red-600"}>
              {up ? "▲" : "▼"} {Math.abs(shown.pct).toFixed(1)}%
            </span>
            {benchmark && benchmark.length ? (
              <span className="text-slate-400">· vs benchmark (dashed)</span>
            ) : null}
          </>
        ) : (
          <span>Equity curve</span>
        )}
      </div>

      <div style={{ height }}>
        <LightweightChart
          height={height}
          deps={[equity, benchmark, signals, baseline]}
          onReady={(chart: IChartApi) => {
            const series = chart.addSeries(BaselineSeries, {
              baseValue: { type: "price", price: baseline },
              topLineColor: UP,
              topFillColor1: "rgba(22,163,74,0.22)",
              topFillColor2: "rgba(22,163,74,0.02)",
              bottomLineColor: DOWN,
              bottomFillColor1: "rgba(220,38,38,0.02)",
              bottomFillColor2: "rgba(220,38,38,0.22)",
              lineWidth: 2,
              priceLineVisible: false,
              priceFormat: { type: "price", precision: 0, minMove: 1 },
            });
            const seen = new Set<number>();
            const data = equity
              .map((p) => ({ time: toTime(p.t), value: p.v }))
              .filter((d) => {
                const k = d.time as unknown as number;
                if (seen.has(k)) return false;
                seen.add(k);
                return true;
              });
            series.setData(data);

            // Break-even line at starting capital.
            series.createPriceLine({
              price: baseline,
              color: "rgba(100,116,139,0.55)",
              lineWidth: 1,
              lineStyle: LineStyle.Dashed,
              axisLabelVisible: true,
              title: "start",
            });

            // Benchmark overlay, rebased so it starts at `baseline` too.
            if (benchmark && benchmark.length) {
              const b0 = benchmark[0]!.v || 1;
              const bline = chart.addSeries(LineSeries, {
                color: BENCH,
                lineWidth: 1,
                lineStyle: LineStyle.Dashed,
                priceLineVisible: false,
                lastValueVisible: false,
                crosshairMarkerVisible: false,
              });
              const bseen = new Set<number>();
              bline.setData(
                benchmark
                  .map((p) => ({
                    time: toTime(p.t),
                    value: (p.v / b0) * baseline,
                  }))
                  .filter((d) => {
                    const k = d.time as unknown as number;
                    if (bseen.has(k)) return false;
                    bseen.add(k);
                    return true;
                  }),
              );
            }

            // Buy/sell markers from the trade log — but ONLY when sparse.
            // Markers are a price-action device; on an equity curve they read
            // as accents, and past ~40 trades they pack into solid bands that
            // smother the line (a 2,466-trade SCHEDULE backtest looked awful).
            // Above the cap we drop them and let the legend carry the count.
            const MARKER_CAP = 40;
            if (signals && signals.length && signals.length <= MARKER_CAP) {
              const markers: SeriesMarker<Time>[] = signals
                .map((s) => ({
                  time: toTime(s.t),
                  position: s.side === "buy" ? ("belowBar" as const) : ("aboveBar" as const),
                  color: s.side === "buy" ? UP : DOWN,
                  shape: s.side === "buy" ? ("arrowUp" as const) : ("arrowDown" as const),
                  text: s.side === "buy" ? "B" : "S",
                }))
                .sort(
                  (a, b) => (a.time as unknown as number) - (b.time as unknown as number),
                );
              createSeriesMarkers(series, markers);
            }

            const onCross = (param: MouseEventParams) => {
              const d = param.seriesData.get(series) as { value: number } | undefined;
              if (!d) {
                setHover(null);
                return;
              }
              setHover({
                nav: d.value,
                pct: baseline ? ((d.value - baseline) / baseline) * 100 : 0,
              });
            };
            chart.subscribeCrosshairMove(onCross);
            return () => chart.unsubscribeCrosshairMove(onCross);
          }}
        />
      </div>
    </div>
  );
}
