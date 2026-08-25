/**
 * feature-chart — the product's real chart primitive on the LIGHT theme, plus
 * the coordinate map its level overlay needs.
 *
 * Same construction as `film/film-chart.tsx` — `LightweightChart` (the wrapper
 * the app renders everywhere) handed charto's own palette — with two changes
 * that matter for this section:
 *
 *  · the LIGHT tokens (`:root[data-theme="light"]` in `charto/preview`), because
 *    the film above already owns the dark surface and a second dark island
 *    would flatten the page into one long night;
 *  · the map is published to a CALLER that draws price bands rather than a GSAP
 *    timeline, so a detected level sits on the price it names at any width
 *    instead of on a pixel that was right once.
 *
 * The series is `film-script`'s BARS — one demo instrument for the whole page,
 * so the film's chart and this one are the same RELIANCE.
 */
"use client";

import * as React from "react";
import {
  CandlestickSeries,
  ColorType,
  CrosshairMode,
  type ISeriesApi,
  type Time,
} from "lightweight-charts";
import { LightweightChart } from "@/components/chart/LightweightChart";
import { BARS } from "@/components/landing/film/film-script";

export type FeatureBar = (typeof BARS)[number];

/**
 * `Theme.palette.light` from `charto/preview/js/theme.js`. A canvas cannot read
 * CSS custom properties, so matching the product is a matter of copying values
 * rather than pointing at a token.
 */
const PALETTE = {
  chartBg: "#ffffff",
  grid: "rgba(15,18,22,.055)",
  axisText: "#6b7280",
  border: "#e6e6e6",
  up: "#089981",
  down: "#f23645",
} as const;

/** DATA space → PIXEL space, relative to the chart container's top-left. */
type ChartMap = { y: (price: number) => number; plotRight: number };

export type PriceBand = {
  id: string;
  from: number;
  to: number;
  tone: "resistance" | "support";
  label: string;
};

export function FeatureChart({
  bands,
  bars = BARS,
  height = 260,
  onBandsReady,
}: {
  bands: readonly PriceBand[];
  bars?: readonly FeatureBar[];
  /** A number of pixels, or any CSS length — the demo hands it `100%`. */
  height?: number | string;
  /** Fired once the price→pixel map exists, so a caller animating the bands
   *  knows the moment they are in the DOM to animate. */
  onBandsReady?: () => void;
}) {
  const hostRef = React.useRef<HTMLDivElement | null>(null);
  const [map, setMap] = React.useState<ChartMap | null>(null);

  return (
    <div ref={hostRef} className="cf-chart" style={{ height }}>
      <LightweightChart
        height="100%"
        options={{
          layout: {
            background: { type: ColorType.Solid, color: PALETTE.chartBg },
            textColor: PALETTE.axisText,
            fontFamily: "Inter, ui-sans-serif, -apple-system, sans-serif",
            fontSize: 10,
            attributionLogo: false,
          },
          grid: {
            vertLines: { color: PALETTE.grid },
            horzLines: { color: PALETTE.grid },
          },
          rightPriceScale: {
            borderColor: PALETTE.border,
            scaleMargins: { top: 0.12, bottom: 0.1 },
          },
          timeScale: {
            borderColor: PALETTE.border,
            timeVisible: false,
            secondsVisible: false,
            rightOffset: 2,
          },
          // Nothing here is hoverable, so a crosshair would be a line no
          // pointer is actually casting.
          crosshair: { mode: CrosshairMode.Hidden },
          handleScroll: false,
          handleScale: false,
          localization: { priceFormatter: (p: number) => p.toFixed(0) },
        }}
        onReady={(chart) => {
          const candles: ISeriesApi<"Candlestick"> = chart.addSeries(
            CandlestickSeries,
            {
              upColor: PALETTE.up,
              downColor: PALETTE.down,
              wickUpColor: PALETTE.up,
              wickDownColor: PALETTE.down,
              borderVisible: false,
              priceLineVisible: true,
              priceLineWidth: 1,
              priceLineColor: PALETTE.down,
              lastValueVisible: true,
            },
          );
          candles.setData(
            bars.map(({ time, open, high, low, close }) => ({
              time: time as unknown as Time,
              open,
              high,
              low,
              close,
            })),
          );

          /**
           * CROP, don't compress. `fitContent()` puts all 60 sessions in
           * whatever width there is, which on a phone is a 2px candle — a
           * chart that has been shrunk until it stopped being readable, which
           * is exactly what a real terminal never does. Below roughly 11px a
           * bar the range is trimmed from the LEFT instead, so the candles
           * keep their size and the view keeps the most recent sessions —
           * including both zones, which sit in the last third.
           */
          const fit = () => {
            const ts = chart.timeScale();
            ts.fitContent();
            const w = hostRef.current?.clientWidth ?? 0;
            const room = Math.floor((w - 64) / 11);
            if (w > 0 && room > 12 && room < bars.length) {
              ts.setVisibleLogicalRange({
                from: bars.length - room,
                to: bars.length - 1 + 2,
              });
            }
          };
          fit();

          // Coordinate lookups return null until layout lands, so poll a few
          // frames, publish once, then stop — the film's proven approach.
          let tries = 0;
          let raf = 0;
          const attempt = () => {
            tries += 1;
            // Re-fit on every attempt, not just at creation: the first call
            // above can run before the container has been laid out, and a
            // crop measured against a zero width is no crop at all.
            fit();
            const ts = chart.timeScale();
            const y = candles.priceToCoordinate(bars[0]!.close);
            const xN = ts.timeToCoordinate(
              bars[bars.length - 1]!.time as unknown as Time,
            );
            if (y != null && xN != null) {
              onBandsReady?.();
              setMap({
                y: (price) => {
                  const c = candles.priceToCoordinate(price);
                  return c == null ? 0 : (c as unknown as number);
                },
                plotRight: xN as unknown as number,
              });
              return;
            }
            if (tries < 40) raf = requestAnimationFrame(attempt);
          };
          raf = requestAnimationFrame(attempt);

          // A width change moves every bar; republish so the bands follow.
          const host = hostRef.current;
          let prevW = host?.clientWidth ?? 0;
          const ro = host
            ? new ResizeObserver(() => {
                const w = host.clientWidth;
                if (w === prevW || w <= 0) return;
                prevW = w;
                fit();
                tries = 0;
                cancelAnimationFrame(raf);
                raf = requestAnimationFrame(attempt);
              })
            : null;
          if (host && ro) ro.observe(host);

          return () => {
            cancelAnimationFrame(raf);
            ro?.disconnect();
            setMap(null);
          };
        }}
      />

      {/* The overlay can only exist once the chart has told us where a price
          is. Until then the chart simply reads as a chart. */}
      {map && (
        <div className="cf-chart-ann" aria-hidden="true">
          {bands.map((b) => {
            const top = map.y(Math.max(b.from, b.to));
            const bottom = map.y(Math.min(b.from, b.to));
            const right = `calc(100% - ${map.plotRight}px)`;
            return (
              <React.Fragment key={b.id}>
                <span
                  className={`cf-band ${b.tone}`}
                  data-d-band={b.id}
                  style={{ top, height: Math.max(bottom - top, 3), right }}
                />
                {/* A sibling, not a child: the band opens from its middle on
                    entry and a label riding inside it would squash with it.
                    Sits ON the top edge at the right, where `.scene-chip`
                    puts it in the app. */}
                <i className={`cf-chip ${b.tone}`} data-d-chip={b.id} style={{ top: Math.max(top - 20, 0), right }}>
                  {b.label}
                </i>
              </React.Fragment>
            );
          })}
        </div>
      )}
    </div>
  );
}
