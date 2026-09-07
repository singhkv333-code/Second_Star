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
  /** Change this whenever the container is resized by something other than a
   *  data change (e.g. a fullscreen toggle) to refit the time scale. */
  refitKey?: string | number;
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
  refitKey,
}: Props) {
  const containerRef = React.useRef<HTMLDivElement | null>(null);
  const chartRef = React.useRef<IChartApi | null>(null);

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

    chartRef.current = chart;
    const cleanup = onReady(chart);
    chart.timeScale().fitContent();

    return () => {
      try {
        if (typeof cleanup === "function") cleanup();
      } finally {
        chartRef.current = null;
        chart.remove();
      }
    };
    // onReady is recreated each render by callers; deps drives rebuilds.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);

  // `autoSize` holds bar spacing constant across a resize, so a width change
  // silently grows/shrinks the visible range instead of keeping the same
  // window of data. Refit once the resize has landed: the effect runs before
  // layout, and autoSize's ResizeObserver fires after it, so we wait two
  // frames to refit against the container's new width rather than the old one.
  React.useEffect(() => {
    if (refitKey === undefined) return;
    let inner = 0;
    const outer = requestAnimationFrame(() => {
      inner = requestAnimationFrame(() => {
        chartRef.current?.timeScale().fitContent();
      });
    });
    return () => {
      cancelAnimationFrame(outer);
      cancelAnimationFrame(inner);
    };
  }, [refitKey]);

  return <div ref={containerRef} className={className} style={{ height }} />;
}
