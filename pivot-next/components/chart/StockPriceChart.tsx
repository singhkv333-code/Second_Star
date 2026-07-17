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
  type LogicalRange,
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
  refitKey,
}: {
  /** Primary first. Raw prices — normalisation happens here in compare mode. */
  seriesDefs: PriceSeriesDef[];
  /** Primary ticker's volume bars; rendered in single mode only. */
  volume?: VolumePoint[] | null;
  height?: number | string;
  /** Intraday ranges (1D/1W) show clock times on the axis. */
  intraday?: boolean;
  /** Change on any non-data container resize (e.g. fullscreen) to refit. */
  refitKey?: string | number;
}): React.ReactElement {
  const dark = useIsDark();
  const t = THEME[dark ? "dark" : "light"];
  const compare = seriesDefs.length > 1;
  const showVolume = !compare && !!volume && volume.length > 0;
  const wrapRef = React.useRef<HTMLDivElement>(null);
  const tooltipRef = React.useRef<HTMLDivElement>(null);

  const onReady = React.useCallback(
    (chart: IChartApi) => {
      // Track the number of bars in the primary series so we can clamp
      // the visible logical range to [0, numBars−1] — no empty white void.
      let numBars = 0;
      // Series we surface in the hover tooltip. `base` = the first in-window
      // raw price, so a normalized (compare-mode) value can be converted back
      // to a real ₹ price for the tooltip: price = (value / 100) * base.
      const tipSeries: {
        api: ReturnType<IChartApi["addSeries"]>;
        label: string;
        color: string;
        normalized: boolean;
        base?: number;
      }[] = [];

      if (compare) {
        // One normalised line per ticker (100 = its first in-window point).
        for (const def of seriesDefs) {
          const data = toLineData(def.points);
          const base = data[0]?.value;
          if (!base) continue;
          // The primary (first) series drives the time scale bar count.
          if (numBars === 0) numBars = data.length;
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
          tipSeries.push({ api: line, label: def.symbol, color: def.color, normalized: true, base });
        }
      } else {
        const def = seriesDefs[0];
        if (def) {
          const data = toLineData(def.points);
          numBars = data.length;
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
          area.setData(data);
          tipSeries.push({ api: area, label: def.symbol, color: t.areaLine, normalized: false });
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

      // ── Hover tooltip ─────────────────────────────────────────────────────
      // The right-scale crosshair label is easy to miss, so surface a floating
      // box with the hovered date + each series' value (₹ in single mode, %
      // vs the window start in compare mode) — the ask: "at least on hover we
      // can see the price".
      const tipText = dark ? "#e2e8f0" : "#0f172a";
      const tipMuted = dark ? "#94a3b8" : "#64748b";
      const handleCrosshair = (param: {
        time?: Time;
        point?: { x: number; y: number };
        seriesData: Map<unknown, { value?: number } | undefined>;
      }): void => {
        const tip = tooltipRef.current;
        const wrap = wrapRef.current;
        if (!tip || !wrap) return;
        if (
          param.time === undefined ||
          !param.point ||
          param.point.x < 0 ||
          param.point.y < 0
        ) {
          tip.style.opacity = "0";
          return;
        }
        const rows = tipSeries
          .map((s) => {
            const d = param.seriesData.get(s.api);
            const v = d?.value;
            if (v == null || !Number.isFinite(v)) return null;
            // Single mode: v is the raw ₹ price. Compare mode: v is normalized
            // to 100 — show the real ₹ price AND the % change from the window
            // start, so a comparison hover isn't "just the change".
            let val: string;
            if (!s.normalized) {
              val = fmtINR(v);
            } else if (s.base) {
              val = `<span style="color:${tipText}">${fmtINR((v / 100) * s.base)}</span> <span style="color:${v >= 100 ? "#16a34a" : "#dc2626"}">${fmtPctFrom100(v)}</span>`;
            } else {
              val = fmtPctFrom100(v);
            }
            return `<div style="display:flex;align-items:center;gap:7px;white-space:nowrap"><span style="width:7px;height:7px;border-radius:50%;background:${s.color};flex-shrink:0"></span><span style="color:${tipMuted};min-width:64px">${s.label}</span><span style="font-weight:600;margin-left:auto;font-variant-numeric:tabular-nums">${val}</span></div>`;
          })
          .filter(Boolean);
        if (rows.length === 0) {
          tip.style.opacity = "0";
          return;
        }
        const ts = Number(param.time);
        const d = new Date(ts * 1000);
        const dateStr =
          d.toLocaleDateString("en-IN", { day: "numeric", month: "short", year: "2-digit" }) +
          (intraday
            ? ` ${d.toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit" })}`
            : "");
        tip.innerHTML =
          `<div style="color:${tipMuted};font-size:10.5px;margin-bottom:4px">${dateStr}</div>` +
          rows.join("");
        tip.style.opacity = "1";
        const w = wrap.clientWidth;
        const ttW = tip.offsetWidth || 130;
        let left = param.point.x + 14;
        if (left + ttW > w - 4) left = param.point.x - ttW - 14;
        tip.style.left = `${Math.max(4, left)}px`;
        tip.style.top = `${Math.max(4, param.point.y - 8)}px`;
      };
      chart.subscribeCrosshairMove(handleCrosshair as never);

      // ── Pan-bounds clamping ───────────────────────────────────────────────
      // Prevent the user from dragging/panning past the first or last bar.
      // Logical range: 0 = first (oldest) bar, numBars−1 = last (newest) bar.
      // A re-entry guard (`clamping`) breaks the otherwise-infinite loop that
      // setVisibleLogicalRange → rangeChange → setVisibleLogicalRange would cause.
      let clamping = false;
      const handleRangeChange = (range: LogicalRange | null): void => {
        if (!range || clamping || numBars === 0) return;
        const from = range.from as unknown as number;
        const to = range.to as unknown as number;
        const span = to - from;

        let newFrom = from;
        let newTo = to;

        if (newFrom < 0) {
          newFrom = 0;
          newTo = Math.min(span, numBars - 1);
        }
        if (newTo > numBars - 1) {
          newTo = numBars - 1;
          newFrom = Math.max(0, newTo - span);
        }

        if (newFrom !== from || newTo !== to) {
          clamping = true;
          chart.timeScale().setVisibleLogicalRange({ from: newFrom, to: newTo });
          clamping = false;
        }
      };

      chart.timeScale().subscribeVisibleLogicalRangeChange(handleRangeChange);

      return (): void => {
        chart.unsubscribeCrosshairMove(handleCrosshair as never);
        chart.timeScale().unsubscribeVisibleLogicalRangeChange(handleRangeChange);
      };
    },
    // Rebuilt whenever the deps below change (the wrapper drives this).
    [seriesDefs, volume, compare, showVolume, t, dark, intraday],
  );

  return (
    <div ref={wrapRef} style={{ position: "relative", height }}>
    <LightweightChart
      height={height}
      refitKey={refitKey}
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
        // Enable wheel / pinch zoom; pan remains drag-to-scroll.
        handleScale: { mouseWheel: true, axisPressedMouseMove: true },
      }}
      onReady={onReady}
    />
      {/* Floating hover tooltip (populated imperatively in onReady). */}
      <div
        ref={tooltipRef}
        aria-hidden
        style={{
          position: "absolute",
          top: 0,
          left: 0,
          zIndex: 5,
          opacity: 0,
          pointerEvents: "none",
          transition: "opacity 90ms ease",
          background: dark ? "#0f172a" : "#ffffff",
          border: `1px solid ${dark ? "rgba(148,163,184,0.22)" : "rgba(15,23,42,0.10)"}`,
          borderRadius: 8,
          boxShadow: "0 6px 20px rgba(0,0,0,0.16)",
          padding: "8px 10px",
          font: '11.5px/1.35 var(--font-ui)',
          minWidth: 120,
        }}
      />
    </div>
  );
}
