/**
 * film-chart — the product's real chart primitive, wearing the dark theme,
 * plus the coordinate map the annotation overlay needs.
 *
 * This is `LightweightChart` (the same wrapper the app renders everywhere) with
 * charto's dark tokens passed as options. The only thing added is `onMap`: once
 * the series has data and the time scale has settled, we hand the caller two
 * functions that turn DATA space (bar index, price) into PIXEL space, taken
 * from the chart's own `timeToCoordinate` / `priceToCoordinate`.
 *
 * That indirection is the whole point — annotations are declared against prices
 * in `film-script`, so a support band sits on ₹2,404 at every viewport width
 * instead of on a pixel that was right once on one screen.
 */
"use client";

import * as React from "react";
import {
  CandlestickSeries,
  ColorType,
  CrosshairMode,
  HistogramSeries,
  type ISeriesApi,
  type Time,
} from "lightweight-charts";
import { LightweightChart } from "@/components/chart/LightweightChart";
import { BARS } from "./film-script";

/**
 * `Theme.palette.dark` from `charto/preview/js/theme.js`, verbatim.
 *
 * A canvas cannot read CSS custom properties, so the product keeps its chart
 * colours in that JS palette rather than in tokens — which means matching the
 * product here is a matter of copying these values, not of pointing at a
 * variable. Only the keys this chart paints with are carried over.
 */
const PALETTE = {
  chartBg: "#0d0e12",
  grid: "rgba(255,255,255,.075)",
  axisText: "#b2b5be",
  border: "#22252d",
  up: "#089981",
  down: "#f23645",
  volUp: "rgba(8,153,129,.42)",
  volDown: "rgba(242,54,69,.42)",
} as const;

/** DATA space → PIXEL space, relative to the chart container's top-left. */
export type ChartMap = {
  x: (barIndex: number) => number;
  y: (price: number) => number;
  /** Right edge of the plot area, i.e. where the price scale starts. */
  plotRight: number;
  height: number;
};

export function FilmChart({
  onMap,
  narrow,
}: {
  onMap: (map: ChartMap | null) => void;
  narrow: boolean;
}) {
  const hostRef = React.useRef<HTMLDivElement | null>(null);
  const seriesRef = React.useRef<ISeriesApi<"Candlestick"> | null>(null);
  const onMapRef = React.useRef(onMap);
  onMapRef.current = onMap;

  return (
    <div ref={hostRef} className="film-chart-host">
      <LightweightChart
        height="100%"
        // The margins differ between compositions, and this wrapper reads
        // `options` only at creation — so the breakpoint has to rebuild it.
        deps={[narrow]}
        options={{
          layout: {
            background: { type: ColorType.Solid, color: PALETTE.chartBg },
            textColor: PALETTE.axisText,
            fontFamily: "Inter, ui-sans-serif, -apple-system, sans-serif",
            fontSize: 11,
            attributionLogo: false,
          },
          grid: {
            vertLines: { color: PALETTE.grid },
            horzLines: { color: PALETTE.grid },
          },
          rightPriceScale: {
            borderColor: PALETTE.border,
            // The narrow band is ~250px tall, so the product's 0.08 headroom
            // puts the highs — and any annotation drawn near them — straight
            // under the readout. More top margin buys the legend its strip
            // back without moving a single annotation off its price.
            scaleMargins: { top: narrow ? 0.26 : 0.08, bottom: narrow ? 0.2 : 0.24 },
          },
          timeScale: {
            borderColor: PALETTE.border,
            timeVisible: true,
            secondsVisible: false,
            rightOffset: 4,
          },
          // The product runs a Normal crosshair because a pointer is really
          // there. Nothing here is hoverable, so a crosshair would only be a
          // stray line the film cursor is not actually casting.
          crosshair: { mode: CrosshairMode.Hidden },
          handleScroll: false,
          handleScale: false,
          localization: {
            priceFormatter: (p: number) => p.toFixed(2),
          },
        }}
        onReady={(chart) => {
          const candles = chart.addSeries(CandlestickSeries, {
            upColor: PALETTE.up,
            downColor: PALETTE.down,
            wickUpColor: PALETTE.up,
            wickDownColor: PALETTE.down,
            borderVisible: false,
            // The product keeps the last-price line and its axis tag; they are
            // most of what makes a still frame read as a live chart.
            priceLineVisible: true,
            priceLineWidth: 1,
            priceLineColor: PALETTE.down,
            lastValueVisible: true,
          });
          candles.setData(
            BARS.map(({ time, open, high, low, close }) => ({
              time: time as unknown as Time,
              open,
              high,
              low,
              close,
            })),
          );
          seriesRef.current = candles;

          const vol = chart.addSeries(HistogramSeries, {
            priceScaleId: "vol",
            priceFormat: { type: "volume" },
            priceLineVisible: false,
            lastValueVisible: false,
          });
          vol.setData(
            BARS.map((b) => ({
              time: b.time as unknown as Time,
              value: b.volume,
              color: b.close >= b.open ? PALETTE.volUp : PALETTE.volDown,
            })),
          );
          chart.priceScale("vol").applyOptions({
            scaleMargins: { top: 0.86, bottom: 0 },
            visible: false,
          });

          chart.timeScale().fitContent();

          // The pane is not measurable on the frame the series is created —
          // coordinate lookups return null until layout lands. Poll a few
          // frames, publish once, then stop.
          let tries = 0;
          let raf = 0;
          const attempt = () => {
            tries += 1;
            const ts = chart.timeScale();
            const y = candles.priceToCoordinate(BARS[0]!.close);
            const x0 = ts.timeToCoordinate(BARS[0]!.time as unknown as Time);
            const xN = ts.timeToCoordinate(
              BARS[BARS.length - 1]!.time as unknown as Time,
            );
            if (y != null && x0 != null && xN != null) {
              const host = hostRef.current;
              const height = host?.clientHeight ?? 0;
              onMapRef.current({
                x: (i) => {
                  const t = BARS[Math.max(0, Math.min(BARS.length - 1, i))]!.time;
                  const c = ts.timeToCoordinate(t as unknown as Time);
                  return c == null ? 0 : (c as unknown as number);
                },
                y: (price) => {
                  const c = candles.priceToCoordinate(price);
                  return c == null ? 0 : (c as unknown as number);
                },
                plotRight: xN as unknown as number,
                height,
              });
              return;
            }
            if (tries < 40) raf = requestAnimationFrame(attempt);
          };
          raf = requestAnimationFrame(attempt);

          // Width changes move every bar. Republish so the overlay follows.
          const host = hostRef.current;
          let prevW = host?.clientWidth ?? 0;
          const ro = host
            ? new ResizeObserver(() => {
                const w = host.clientWidth;
                if (w === prevW || w <= 0) return;
                prevW = w;
                chart.timeScale().fitContent();
                tries = 0;
                cancelAnimationFrame(raf);
                raf = requestAnimationFrame(attempt);
              })
            : null;
          if (host && ro) ro.observe(host);

          return () => {
            cancelAnimationFrame(raf);
            ro?.disconnect();
            seriesRef.current = null;
            onMapRef.current(null);
          };
        }}
      />
    </div>
  );
}
