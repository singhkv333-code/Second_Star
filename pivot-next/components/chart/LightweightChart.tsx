/**
 * LightweightChart — thin, SSR-safe React wrapper over TradingView's
 * lightweight-charts v5 (`addSeries(SeriesType, opts)` API).
 *
 * It owns the chart lifecycle (create → configure → dispose) and built-in
 * autosize; callers create series + set data inside `onReady`, which re-runs
 * whenever `deps` change (e.g. fresh data). Defaults to a light, borderless
 * theme that blends into Pivot's card surfaces; override via `options`.
 *
 * Time-series only — the x-axis is a time scale, so this is for price/candle
 * and equity-curve charts, NOT for price-on-x payoff diagrams (those stay on
 * recharts, which has a numeric x-axis).
 */
"use client";

import * as React from "react";
import {
  createChart,
  ColorType,
  CrosshairMode,
  type IChartApi,
  type DeepPartial,
  type ChartOptions,
  type Time,
} from "lightweight-charts";

export type ChartReady = (chart: IChartApi) => (() => void) | void;

type Props = {
  className?: string;
  /** Number = fixed px height; a string (e.g. "100%") fills the parent —
   *  `autoSize` tracks the container either way. */
  height?: number | string;
  options?: DeepPartial<ChartOptions>;
  /** Create series + set data here. Return an optional cleanup run before dispose. */
  onReady: ChartReady;
  /** Re-run onReady (rebuild series) when any of these change. */
  deps?: React.DependencyList;
};

// ISO string / date → UTC seconds, the time form lightweight-charts accepts
// for both daily and intraday data. Callers must pre-sort ascending + dedupe.
export function toTime(iso: string): Time {
  return Math.floor(new Date(iso).getTime() / 1000) as unknown as Time;
}

const FONT =
  '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif';

export function LightweightChart({
  className,
  height = 260,
  options,
  onReady,
  deps = [],
}: Props) {
  const containerRef = React.useRef<HTMLDivElement | null>(null);

  React.useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    const chart = createChart(el, {
      autoSize: true,
      ...(typeof height === "number" ? { height } : {}),
      layout: {
        background: { type: ColorType.Solid, color: "transparent" },
        textColor: "#64748b",
        fontFamily: FONT,
        fontSize: 11,
        attributionLogo: false,
      },
      grid: {
        vertLines: { visible: false },
        horzLines: { color: "rgba(100,116,139,0.10)" },
      },
      rightPriceScale: {
        borderVisible: false,
        scaleMargins: { top: 0.12, bottom: 0.12 },
      },
      timeScale: {
        borderVisible: false,
        timeVisible: true,
        secondsVisible: false,
      },
      crosshair: {
        mode: CrosshairMode.Magnet,
        vertLine: { labelBackgroundColor: "#0f172a", width: 1, style: 3 },
        horzLine: { labelBackgroundColor: "#0f172a" },
      },
      handleScroll: { mouseWheel: false },
      handleScale: { mouseWheel: false },
      ...options,
    });

    const cleanup = onReady(chart);
    chart.timeScale().fitContent();

    return () => {
      try {
        if (typeof cleanup === "function") cleanup();
      } finally {
        chart.remove();
      }
    };
    // onReady is recreated each render by callers; deps drives rebuilds.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);

  return <div ref={containerRef} className={className} style={{ height }} />;
}
