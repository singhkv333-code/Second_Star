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
    // ── the ramp ──────────────────────────────────────────────────────────
    // The old one walked hues around the accent at FALLING saturation, which
    // is what made the chart look dusty: eight bands all landing near the
    // same low chroma, so the stack read as one muddy mass with seams.
    //
    // This one holds chroma roughly CONSTANT and spaces hue evenly around the
    // wheel, starting at the product's blue. Equal chroma is what makes the
    // bands feel like one family; even hue spacing is what keeps eight of
    // them apart. Lightness rises very slightly along the run so the upper
    // bands — which sit against white — do not close up.
    //
    // `accent` deliberately stays first: the largest segment is drawn at the
    // bottom of a stack, and that band should be the page's own blue.
    palette: [
      accent,     // the product blue
      "#2E9AA8",  // teal
      "#4FA46B",  // green
      "#8FA83E",  // olive
      "#D0A02C",  // gold
      "#DB7F3C",  // amber
      "#CE5F55",  // terracotta
      "#B85D86",  // rose
      "#8C63AE",  // violet
      "#5B6FB5",  // indigo
    ],
  };
}

function sameTokens(a: ChartTokens, b: ChartTokens): boolean {
  return a.text === b.text && a.muted === b.muted && a.border === b.border
    && a.surface === b.surface
    && a.palette.length === b.palette.length
    && a.palette.every((c, i) => c === b.palette[i]);
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
    // Replace the token object only when a VALUE actually changed. readTokens()
    // returns a fresh object every call, so keying the paint effect on it made
    // any unrelated class flip on <html> re-run setOption(…, true) — which
    // rebuilds the chart under the pointer and strands whatever tooltip was
    // open at the time. That was half of "the popup never goes away".
    const next = readTokens();
    setTokens((prev) => (prev && sameTokens(prev, next) ? prev : next));
    const obs = new MutationObserver(() => {
      const t = readTokens();
      setTokens((prev) => (prev && sameTokens(prev, t) ? prev : t));
    });
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
    // `...option` is spread LAST so a caller can override any default — which
    // is right for every key except `tooltip`, because a caller that sets
    // `tooltip: { trigger: "axis" }` replaces the whole object and silently
    // drops the theming, `confine`, `enterable` and `hideDelay` set here. That
    // is exactly what happened: the mix chart was running ECharts' stock
    // tooltip (default white, default shadow, unconfined) while this file
    // looked like it was styling it. The tooltip is merged key-by-key instead,
    // caller wins per key.
    const { tooltip: callerTip, legend: callerLegend, ...restOption } = option as {
      tooltip?: Record<string, unknown>;
      legend?: Record<string, unknown>;
    } & Record<string, unknown>;

    // Legend needs the same key-by-key treatment as the tooltip, and for the
    // same reason: ECharts does NOT cascade the root `textStyle.color` into
    // legend labels — they fall back to its own #333, which is invisible on
    // the dark ground. Any caller that sets `legend` at all was replacing the
    // themed one wholesale, so the merge happens here rather than in every
    // panel that draws a legend.
    const legendTextStyle = (callerLegend?.textStyle ?? {}) as Record<string, unknown>;

    inst.current.setOption(
      {
        color: tokens.palette,
        textStyle: { color: tokens.text, fontFamily: "var(--font-ui)", fontSize: 11 },
        tooltip: {
          backgroundColor: tokens.surface,
          borderColor: tokens.border,
          textStyle: { color: tokens.text, fontSize: 12 },
          // `confine` keeps the box inside the chart's own box. Left free it
          // can be drawn past the edge, under a pointer that has already left
          // the canvas — so the element the user is looking at is the one
          // element that never got the mouseout.
          confine: true,
          // Not enterable: a tooltip the pointer can move ONTO is a tooltip
          // that keeps itself alive.
          enterable: false,
          hideDelay: 0,
          transitionDuration: 0.15,
          extraCssText: "box-shadow:0 4px 16px rgba(15,18,22,.10);border-radius:10px;",
          ...(callerTip ?? {}),
        },
        ...(callerLegend
          ? {
              legend: {
                ...callerLegend,
                textStyle: { color: tokens.text, ...legendTextStyle },
                inactiveColor: tokens.muted,
                pageTextStyle: { color: tokens.muted },
                pageIconColor: tokens.muted,
                pageIconInactiveColor: tokens.border,
              },
            }
          : {}),
        ...restOption,
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

  // ── put the tooltip away ────────────────────────────────────────────────
  // An axis-trigger tooltip is hidden by ECharts' own mouseout handling, and
  // that handling is on the CANVAS. Any frame where the pointer leaves without
  // the canvas seeing it — off the edge of the panel, out of the window, over
  // the tooltip itself, or through a re-init — leaves the tip painted with no
  // pointer under it. Then it sits there until the next hover.
  //
  // So the leave is stated rather than inferred: hideTip puts the box away and
  // the `leave` axis-pointer update takes the crosshair with it, which is the
  // pair ECharts itself dispatches internally.
  React.useEffect(() => {
    const el = host.current;
    if (!el) return;
    const away = (): void => {
      const c = inst.current;
      if (!c) return;
      c.dispatchAction({ type: "hideTip" });
      c.dispatchAction({ type: "updateAxisPointer", currTrigger: "leave" });
    };
    el.addEventListener("mouseleave", away);
    el.addEventListener("pointerleave", away);
    // Scrolling the section out from under a stationary pointer is the other
    // way to leave a chart without a mouseout ever firing.
    //
    // On DOCUMENT, in the CAPTURE phase — not on window. This page does not
    // scroll the document: it scrolls an inner container, and a scroll event
    // does not bubble, so a window listener never hears it and the tooltip
    // rode the page up still painted. Capture on document sees a scroll from
    // whichever element is actually doing the scrolling.
    document.addEventListener("scroll", away, { passive: true, capture: true });
    window.addEventListener("blur", away);
    return () => {
      el.removeEventListener("mouseleave", away);
      el.removeEventListener("pointerleave", away);
      document.removeEventListener("scroll", away, { capture: true } as EventListenerOptions);
      window.removeEventListener("blur", away);
    };
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
