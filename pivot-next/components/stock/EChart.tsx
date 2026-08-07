"use client";

/**
 * ECharts, wrapped so the rest of the page never has to know it exists.
 *
 * Why ECharts at all when Recharts is already here: the stacked segment series
 * below draws eight overlapping bands across twenty-odd years, and Recharts
 * renders one SVG node per point. ECharts paints to a canvas, so the same
 * chart costs a fraction of the DOM. Recharts stays for sparklines and small
 * multiples, where its SSR-safety and JSX API are the better trade.
 *
 * Three things this wrapper exists to get right:
 *
 *   · CLIENT ONLY. ECharts touches `window` at import time, so under the App
 *     Router it must never reach a server render. The parent imports this file
 *     through `next/dynamic` with `ssr: false`; the "use client" directive
 *     alone is not enough.
 *   · TREE-SHAKEN. Importing `echarts` whole pulls every chart type, map and
 *     3D renderer — megabytes for two chart kinds. Registering only what is
 *     used keeps it to the line, bar and pie renderers.
 *   · THEME-AWARE. Chart colours are read from the page's own CSS custom
 *     properties at paint time rather than hardcoded, and re-read when the
 *     theme changes, so a chart cannot be the one element still in light mode.
 */

import * as React from "react";
import { BarChart, LineChart, PieChart } from "echarts/charts";
import {
  DatasetComponent,
  GridComponent,
  LegendComponent,
  TitleComponent,
  TooltipComponent,
} from "echarts/components";
import * as echarts from "echarts/core";
import { CanvasRenderer } from "echarts/renderers";

echarts.use([
  LineChart, BarChart, PieChart,
  GridComponent, TooltipComponent, LegendComponent, TitleComponent,
  DatasetComponent, CanvasRenderer,
]);

/** Read a CSS custom property off the document root. ECharts takes concrete
 *  colour strings, not `var(...)`, so the tokens have to be resolved here. */
function token(name: string, fallback: string): string {
  if (typeof window === "undefined") return fallback;
  const v = getComputedStyle(document.documentElement).getPropertyValue(name);
  return v.trim() || fallback;
}

export type ChartTokens = {
  text: string;
  muted: string;
  border: string;
  surface: string;
  /** Categorical ramp for segment series. Derived from the product's own
   *  accent rather than ECharts' defaults, whose primary blue is not ours. */
  palette: string[];
};

export function readTokens(): ChartTokens {
  const accent = token("--pivot-blue", "#219ebc");
  return {
    text: token("--text-primary", "#0d0d0e"),
    muted: token("--text-tertiary", "#6b7280"),
    border: token("--glass-border", "rgba(15,18,22,0.08)"),
    surface: token("--bg-primary", "#fbfbfc"),
    // A ramp that stays legible stacked: the accent, then hues walked around
    // it at falling saturation so eight bands remain distinguishable without
    // any one of them shouting.
    palette: [
      accent, "#7C9885", "#C08552", "#5C6B87", "#9A6A8F",
      "#4E8098", "#B08968", "#6B8F71", "#8C7A9B", "#A8763E",
    ],
  };
}

export type EChartProps = {
  /** ECharts option object, minus theming — this component injects that. */
  option: Record<string, unknown>;
  height?: number;
  /** Announced to screen readers; a canvas is otherwise opaque to them. */
  ariaLabel: string;
};

export default function EChart({
  option,
  height = 260,
  ariaLabel,
}: EChartProps): React.ReactElement {
  const host = React.useRef<HTMLDivElement | null>(null);
  const inst = React.useRef<echarts.ECharts | null>(null);
  const [tokens, setTokens] = React.useState<ChartTokens | null>(null);

  // Resolve tokens after mount (they need a live document), and again whenever
  // the theme flips — the app toggles a class on <html>, and a chart left on
  // the old palette is the one element that gives the switch away.
  React.useEffect(() => {
    setTokens(readTokens());
    const obs = new MutationObserver(() => setTokens(readTokens()));
    obs.observe(document.documentElement, {
      attributes: true,
      attributeFilter: ["class", "data-theme"],
    });
    return () => obs.disconnect();
  }, []);

  React.useEffect(() => {
    if (!host.current || !tokens) return;
    if (!inst.current) inst.current = echarts.init(host.current, undefined, {
      renderer: "canvas",
    });
    inst.current.setOption(
      {
        color: tokens.palette,
        textStyle: { color: tokens.text, fontFamily: "var(--font-ui)", fontSize: 11 },
        tooltip: {
          backgroundColor: tokens.surface,
          borderColor: tokens.border,
          textStyle: { color: tokens.text, fontSize: 12 },
          // The default shadow reads as a different design system; a hairline
          // border matches every other floating surface in the product.
          extraCssText: "box-shadow:none;border-radius:8px;",
        },
        ...option,
      },
      // `true` — replace rather than merge. Switching between breakdowns
      // changes the series COUNT, and a merge leaves the extra series from the
      // previous breakdown on the canvas.
      true,
    );
  }, [option, tokens]);

  // Charts do not reflow on their own; the panel is resizable and the sidebar
  // collapses, so observe the host rather than listening to window resize.
  React.useEffect(() => {
    if (!host.current) return;
    const ro = new ResizeObserver(() => inst.current?.resize());
    ro.observe(host.current);
    return () => ro.disconnect();
  }, []);

  React.useEffect(() => () => {
    inst.current?.dispose();
    inst.current = null;
  }, []);

  return (
    <div
      ref={host}
      role="img"
      aria-label={ariaLabel}
      style={{ width: "100%", height }}
    />
  );
}
