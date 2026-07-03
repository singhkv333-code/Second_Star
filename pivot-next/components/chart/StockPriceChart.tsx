"use client";

/**
 * StockPriceChart — the stock page's price chart, built on TradingView's
 * lightweight-charts v5 (same rendering as in.tradingview.com/lightweight-charts).
 *
 * Modes:
 *   - SINGLE ticker → blue AREA series (gradient fade, dotted last-price
 *     line, last-value axis label) + a muted VOLUME histogram pinned to the
 *     bottom ~18% on its own hidden scale — the TV "Chart type: Area" +
 *     volume look.
 *   - COMPARE (2+ tickers) → one LINE per ticker, each normalised to 100 at
 *     its first in-window point (comparable across price scales), coloured
 *     last-value labels on the axis — the TV "Series compare" look. The
 *     right scale flips to % change; volume is hidden.
 *
 * Range switching / date filtering happen upstream (the card refetches and
 * passes fresh points); this component just renders what it's given.
 * Crosshair, axis labels, and time formatting are the library's own — that's
 * what keeps it as clean as the reference.
 */

import * as React from "react";
import {
  AreaSeries,
  ColorType,
  CrosshairMode,
  HistogramSeries,
  LineSeries,
  LineStyle,
  type IChartApi,
  type Time,
} from "lightweight-charts";
import { LightweightChart } from "@/components/chart/LightweightChart";

export type PricePoint = { t: string; v: number };
export type VolumePoint = { t: string; v: number; up: boolean };
export type PriceSeriesDef = {
  symbol: string;
  color: string;
  points: PricePoint[];
};

// ── Theme (light mirrors the TradingView reference; dark adapts) ──────────
const THEME = {
  light: {
    text: "#64748b",
    grid: "rgba(100,116,139,0.10)",
    crosshairLabel: "#0f172a",
    areaLine: "#2962FF",
    areaTop: "rgba(41,98,255,0.24)",
    areaBottom: "rgba(41,98,255,0.02)",
    volUp: "rgba(38,166,154,0.45)",
    volDown: "rgba(239,83,80,0.45)",
  },
  dark: {
    text: "#94a3b8",
    grid: "rgba(148,163,184,0.10)",
    crosshairLabel: "#334155",
    areaLine: "#4f83ff",
    areaTop: "rgba(79,131,255,0.30)",
    areaBottom: "rgba(79,131,255,0.02)",
    volUp: "rgba(38,166,154,0.40)",
    volDown: "rgba(239,83,80,0.40)",
  },
} as const;

/** Track the app's dark-mode class on <html> (AppShell toggles it). */
export function useIsDark(): boolean {
  const [dark, setDark] = React.useState(false);
  React.useEffect(() => {
    const el = document.documentElement;
    const sync = (): void => setDark(el.classList.contains("dark"));
    sync();
    const obs = new MutationObserver(sync);
    obs.observe(el, { attributes: true, attributeFilter: ["class"] });
    return () => obs.disconnect();
  }, []);
  return dark;
}

// ── Data shaping — LW requires ascending, deduped UTC-second times ────────
function toSeconds(iso: string): number {
  return Math.floor(new Date(iso).getTime() / 1000);
}

function toLineData(
  points: PricePoint[],
): { time: Time; value: number }[] {
  const m = new Map<number, number>();
  for (const p of points) {
    const t = toSeconds(p.t);
    if (Number.isFinite(t) && p.v != null && Number.isFinite(p.v)) {
      m.set(t, p.v);
    }
  }
  return [...m.entries()]
    .sort((a, b) => a[0] - b[0])
    .map(([time, value]) => ({ time: time as Time, value }));
}

const fmtINR = (v: number): string =>
  `₹${v.toLocaleString("en-IN", { maximumFractionDigits: v < 100 ? 2 : 0 })}`;

const fmtPctFrom100 = (v: number): string => {
  const d = v - 100;
  return `${d >= 0 ? "+" : ""}${d.toFixed(Math.abs(d) < 10 ? 1 : 0)}%`;
};

export function StockPriceChart({
  seriesDefs,
  volume,
  height = 320,
  intraday = false,
}: {
  /** Primary first. Raw prices — normalisation happens here in compare mode. */
  seriesDefs: PriceSeriesDef[];
  /** Primary ticker's volume bars; rendered in single mode only. */
  volume?: VolumePoint[] | null;
  height?: number | string;
  /** Intraday ranges (1D/1W) show clock times on the axis. */
  intraday?: boolean;
}): React.ReactElement {
  const dark = useIsDark();
  const t = THEME[dark ? "dark" : "light"];
  const compare = seriesDefs.length > 1;
  const showVolume = !compare && !!volume && volume.length > 0;

  const onReady = React.useCallback(
    (chart: IChartApi) => {
      if (compare) {
        // One normalised line per ticker (100 = its first in-window point).
        for (const def of seriesDefs) {
          const data = toLineData(def.points);
          const base = data[0]?.value;
          if (!base) continue;
          const line = chart.addSeries(LineSeries, {
            color: def.color,
            lineWidth: 2,
            priceLineVisible: false,
            lastValueVisible: true,
            crosshairMarkerVisible: true,
          });
          line.setData(
            data.map((d) => ({ time: d.time, value: (d.value / base) * 100 })),
          );
        }
      } else {
        const def = seriesDefs[0];
        if (def) {
          const area = chart.addSeries(AreaSeries, {
            lineColor: t.areaLine,
            topColor: t.areaTop,
            bottomColor: t.areaBottom,
            lineWidth: 2,
            priceLineVisible: true,
            priceLineStyle: LineStyle.Dotted,
            priceLineColor: t.areaLine,
            lastValueVisible: true,
            crosshairMarkerVisible: true,
            crosshairMarkerRadius: 4,
          });
          area.setData(toLineData(def.points));
        }
        if (showVolume && volume) {
          const vol = chart.addSeries(HistogramSeries, {
            priceScaleId: "volume",
            priceFormat: { type: "volume" },
            priceLineVisible: false,
            lastValueVisible: false,
          });
          const m = new Map<number, { value: number; color: string }>();
          for (const p of volume) {
            const ts = toSeconds(p.t);
            if (Number.isFinite(ts) && p.v > 0) {
              m.set(ts, { value: p.v, color: p.up ? t.volUp : t.volDown });
            }
          }
          vol.setData(
            [...m.entries()]
              .sort((a, b) => a[0] - b[0])
              .map(([time, d]) => ({ time: time as Time, ...d })),
          );
          // Pin volume to the bottom ~18%; keep its own scale invisible.
          chart.priceScale("volume").applyOptions({
            scaleMargins: { top: 0.82, bottom: 0 },
            visible: false,
          });
        }
      }
      chart.timeScale().fitContent();
    },
    // Rebuilt whenever the deps below change (the wrapper drives this).
    [seriesDefs, volume, compare, showVolume, t],
  );

  return (
    <LightweightChart
      height={height}
      deps={[seriesDefs, volume, compare, showVolume, dark, intraday]}
      options={{
        // NOTE: the wrapper merges these SHALLOWLY over its defaults, so any
        // top-level key set here must be complete (a partial `layout` would
        // silently drop attributionLogo:false / the transparent background).
        layout: {
          background: { type: ColorType.Solid, color: "transparent" },
          textColor: t.text,
          fontFamily:
            '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif',
          fontSize: 11,
          attributionLogo: false,
        },
        grid: {
          vertLines: { visible: false },
          horzLines: { color: t.grid },
        },
        rightPriceScale: {
          borderVisible: false,
          // Leave head-room above + clear the volume band below in single
          // mode; compare mode uses the full pane.
          scaleMargins: showVolume
            ? { top: 0.08, bottom: 0.22 }
            : { top: 0.1, bottom: 0.1 },
        },
        timeScale: {
          borderVisible: false,
          timeVisible: intraday,
          secondsVisible: false,
        },
        crosshair: {
          mode: CrosshairMode.Magnet,
          vertLine: {
            labelBackgroundColor: t.crosshairLabel,
            width: 1,
            style: LineStyle.Dashed,
          },
          horzLine: { labelBackgroundColor: t.crosshairLabel },
        },
        localization: {
          priceFormatter: compare ? fmtPctFrom100 : fmtINR,
        },
      }}
      onReady={onReady}
    />
  );
}
