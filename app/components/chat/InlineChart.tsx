"use client";

/**
 * The charts a reply places between its paragraphs (`[[chart:id]]`): bars of
 * a reported figure over time, and where a P/E sits in its own history.
 * Styled after the chat's price chart (Lightweight Charts): Inter at 10px, a
 * quiet right-hand axis, hairline dashed grid, and the same blue and orange,
 * so every chart in a reply reads as one family.
 */

import React, { useMemo } from "react";
import dynamic from "next/dynamic";
import { useInlineChart, type InlineChartPayload } from "@/lib/inlineCharts";
import { SeriesChartCard, type SeriesChartPayload } from "@/components/chat/SeriesChartCard";
import { CompanyLogo } from "@/components/CompanyLogo";
import { useCompanyLogos } from "@/hooks/useCompanyLogos";

const HEIGHT = 196;
const EChart = dynamic(() => import("@/components/stock/EChart"), {
  ssr: false,
  loading: () => <div className="animate-pulse rounded-lg bg-muted/40" style={{ height: HEIGHT }} />,
});

const COLORS = ["#2962FF", "#fb8500", "#16a34a", "#9333ea"];
const FONT = "'Inter', system-ui, sans-serif";
const MUTED = "#8a94a3";
const GRID = "rgba(135,145,155,.18)";
const BASE = {
  animation: false,
  textStyle: { fontFamily: FONT, fontSize: 10 },
  grid: { left: 6, right: 6, top: 10, bottom: 4, containLabel: true },
};
const X_AXIS = {
  type: "category",
  axisLine: { show: false },
  axisTick: { show: false },
  axisLabel: { color: MUTED, fontSize: 10, margin: 10, hideOverlap: true },
};
const Y_AXIS = {
  type: "value",
  position: "right",
  splitNumber: 3,
  axisLine: { show: false },
  axisTick: { show: false },
  axisLabel: { color: MUTED, fontSize: 10 },
  splitLine: { lineStyle: { color: GRID, type: "dashed" } },
};

/** Light: a soft grey panel that sets the chart off the white page. Dark: the card surface. */
export const SURFACE = "border border-black/[0.05] bg-[#f6f7f9] dark:border-border dark:bg-card";

const compact = new Intl.NumberFormat("en-IN", { notation: "compact", maximumFractionDigits: 1 });
const whole = (v: number): string =>
  v.toLocaleString("en-IN", { maximumFractionDigits: Math.abs(v) < 100 ? 2 : 0 });
const pct = (v: number): string => `${v >= 0 ? "+" : "−"}${Math.abs(v).toFixed(1)}%`;
const ordinal = (n: number): string =>
  `${n}${[11, 12, 13].includes(n % 100) ? "th" : ["th", "st", "nd", "rd"][n % 10] ?? "th"}`;

// ── units ───────────────────────────────────────────────────────────────────

type Kind = "cr" | "inr" | "pct" | "x" | "num";
const kindOf = (u: string): Kind =>
  /cr/i.test(u) ? "cr" : /share|^₹$/i.test(u) ? "inr" : u === "%" ? "pct" : /^(x|×)$/i.test(u) ? "x" : "num";
const unitLabel = (u: string): string =>
  ({ cr: "₹ crore", inr: "₹ per share", pct: "%", x: "multiple", num: u })[kindOf(u)];

/** A value as a reader says it: ₹2,67,021 Cr, ₹136, 45.9%, 7.96×. */
function fmtValue(v: number, u: string): string {
  const k = kindOf(u);
  if (k === "cr") return `₹${whole(v)} Cr`;
  if (k === "inr") return `₹${v.toLocaleString("en-IN", { maximumFractionDigits: 2 })}`;
  if (k === "pct") return `${v.toFixed(1)}%`;
  if (k === "x") return `${v.toFixed(2)}×`;
  return whole(v);
}

/** "Eps basic" → "EPS", "Net profit margin" → "Net profit margin", "Roce" → "ROCE". */
const ACRONYMS: Record<string, string> = {
  eps: "EPS", roe: "ROE", roce: "ROCE", roa: "ROA", roic: "ROIC", ebitda: "EBITDA", ebit: "EBIT",
  pe: "P/E", pb: "P/B", ev: "EV", casa: "CASA", npa: "NPA", pbt: "PBT", pat: "PAT",
};
export function metricTitle(t: string): string {
  const words = t.replace(/_/g, " ").trim().split(/\s+/);
  if (/^eps$/i.test(words[0] ?? "") && /^basic$/i.test(words[1] ?? "")) words.splice(1, 1);
  const out = words.map((w) => ACRONYMS[w.toLowerCase()] ?? w.toLowerCase()).join(" ");
  return out.charAt(0).toUpperCase() + out.slice(1);
}

/** Axis ticks: short, but never a bare "3L". */
function fmtAxis(v: number, u: string): string {
  if (v === 0) return "0";
  const k = kindOf(u);
  if (k === "cr") {
    if (Math.abs(v) >= 1e5) return `₹${+(v / 1e5).toFixed(1)}L Cr`;
    if (Math.abs(v) >= 1e3) return `₹${+(v / 1e3).toFixed(1)}K Cr`;
    return `₹${v} Cr`;
  }
  if (k === "inr") return `₹${compact.format(v)}`;
  if (k === "pct") return `${v}%`;
  if (k === "x") return `${v}×`;
  return compact.format(v);
}

/** How the latest period compares: CAGR for amounts, year on year for a
 *  quarter, a change in points for ratios. */
function growth(vals: (number | null)[], labels: string[], u: string, quarterly: boolean):
  { text: string; up: boolean } | null {
  const idx = vals.flatMap((v, i) => (v == null ? [] : [i]));
  if (idx.length < 2) return null;
  const i0 = idx[0]!, i1 = idx[idx.length - 1]!;
  const a = vals[i0]!, b = vals[i1]!;
  const k = kindOf(u);
  if (k === "pct") return { text: `${b - a >= 0 ? "+" : "−"}${Math.abs(b - a).toFixed(1)} pts since ${labels[i0]}`, up: b >= a };
  if (k === "x") return { text: `${b - a >= 0 ? "+" : "−"}${Math.abs(b - a).toFixed(2)}× since ${labels[i0]}`, up: b >= a };
  if (quarterly) {
    const prev = i1 >= 4 ? vals[i1 - 4] : null; // the same quarter a year earlier
    return prev && prev > 0 ? { text: `${pct(((b - prev) / prev) * 100)} year on year`, up: b >= prev } : null;
  }
  if (a <= 0 || b <= 0) return null;
  const cagr = (Math.pow(b / a, 1 / (i1 - i0)) - 1) * 100;
  return { text: `${cagr.toFixed(1)}% CAGR since ${labels[i0]}`, up: cagr >= 0 };
}

const latestIndex = (vals: (number | null)[]): number => {
  for (let i = vals.length - 1; i >= 0; i--) if (vals[i] != null) return i;
  return -1;
};

// ── frame ───────────────────────────────────────────────────────────────────

function Frame({ title, meta, aside, logo, children }: {
  title: string;
  meta?: React.ReactNode;
  aside?: React.ReactNode;
  /** One company's symbol: its logo leads the header. */
  logo?: string;
  children: React.ReactNode;
}): React.ReactElement {
  const logos = useCompanyLogos(useMemo(() => (logo ? [logo] : []), [logo]));
  return (
    // The card sets its own line heights: inside a reply it would otherwise
    // inherit the prose's 28px leading and the header would drift apart.
    <figure className={`!my-5 w-full overflow-hidden rounded-xl leading-normal ${SURFACE}`} data-testid="inline-chart">
      <figcaption className="flex items-center justify-between gap-4 px-4 pt-4">
        <div className="flex min-w-0 items-center gap-3">
          {logo && <CompanyLogo logoUrl={logos[logo] ?? null} name={logo} symbol={logo} size={34} />}
          <div className="min-w-0">
            <div className="truncate text-[14px] font-semibold leading-5 text-foreground">{title}</div>
            {meta && <div className="mt-0.5 text-[12px] leading-4 text-muted-foreground">{meta}</div>}
          </div>
        </div>
        {aside && <div className="shrink-0 leading-5">{aside}</div>}
      </figcaption>
      <div className="px-2 pb-2 pt-5">{children}</div>
    </figure>
  );
}

/** A small logo beside a company's name in a comparison legend. */
function LegendLogo({ symbol, url }: { symbol: string; url: string | null }): React.ReactElement {
  return <CompanyLogo logoUrl={url} name={symbol} symbol={symbol} size={16} />;
}

// ── bars: a reported figure over years or quarters ──────────────────────────

export type FinancialBarsPayload = {
  title: string;
  unit?: string;
  quarterly?: boolean;
  labels: string[];
  series: { symbol: string; values: (number | null)[] }[];
};

function FinancialBars({ p }: { p: FinancialBarsPayload }): React.ReactElement {
  const single = p.series.length === 1;
  const unit = p.unit ?? "";
  const quarterly = p.quarterly ?? p.labels.every((l) => l.startsWith("Q"));

  const option = useMemo(() => ({
    ...BASE,
    tooltip: {
      trigger: "axis",
      axisPointer: { type: "shadow", shadowStyle: { color: "rgba(135,145,155,.08)" } },
      formatter: (items: { dataIndex: number; seriesIndex: number; value: number | null }[]) => {
        const i = items[0]?.dataIndex ?? 0;
        const rows = items.map((it) => {
          const vals = p.series[it.seriesIndex]!.values;
          const back = quarterly ? 4 : 1;
          const prev = i >= back ? vals[i - back] : null;
          const chg = it.value != null && prev ? ((it.value - prev) / Math.abs(prev)) * 100 : null;
          const dot = `<span style="display:inline-block;width:7px;height:7px;border-radius:50%;background:${COLORS[it.seriesIndex]};margin-right:6px"></span>`;
          return `<div style="display:flex;gap:12px;justify-content:space-between;align-items:center">`
            + `<span>${single ? "" : `${dot}${p.series[it.seriesIndex]!.symbol}`}</span>`
            + `<span style="font-weight:600">${it.value == null ? "—" : fmtValue(it.value, unit)}`
            + `${chg == null || kindOf(unit) === "pct" || kindOf(unit) === "x" ? "" : ` <span style="color:${chg >= 0 ? "#16a34a" : "#dc2626"};font-weight:500">${pct(chg)}</span>`}</span></div>`;
        });
        return `<div style="font-family:${FONT};font-size:11.5px;min-width:130px"><div style="color:${MUTED};margin-bottom:4px">${p.labels[i]}</div>${rows.join("")}</div>`;
      },
    },
    xAxis: { ...X_AXIS, data: p.labels },
    yAxis: { ...Y_AXIS, axisLabel: { ...Y_AXIS.axisLabel, formatter: (v: number) => fmtAxis(v, unit) } },
    series: p.series.map((s, si) => ({
      type: "bar",
      name: s.symbol,
      barMaxWidth: single ? 30 : 18,
      barGap: "18%",
      barCategoryGap: single ? "38%" : "28%",
      data: s.values.map((v, i) => ({
        value: v,
        itemStyle: {
          color: v != null && v < 0 ? "#ef5350" : COLORS[si % COLORS.length],
          // One company: the latest period in full, the history softened.
          opacity: single && i !== s.values.length - 1 ? 0.42 : 1,
          borderRadius: v != null && v < 0 ? [0, 0, 4, 4] : [4, 4, 0, 0],
        },
      })),
    })),
  }), [p, single, unit, quarterly]);

  const meta = `${quarterly ? "Quarterly" : "Annual"} · ${unitLabel(unit)}`;
  const title = metricTitle(p.title);
  const logos = useCompanyLogos(useMemo(() => p.series.map((s) => s.symbol), [p.series]));
  if (single) {
    const s = p.series[0]!;
    const li = latestIndex(s.values);
    const g = growth(s.values, p.labels, unit, quarterly);
    return (
      <Frame
        title={`${s.symbol} · ${title}`}
        logo={s.symbol}
        meta={meta}
        aside={li >= 0 && (
          <div className="text-right">
            <div className="text-[15px] font-semibold leading-5 tabular-nums text-foreground">
              {fmtValue(s.values[li]!, unit)}
              <span className="ml-1.5 text-[12px] font-normal text-muted-foreground">{p.labels[li]}</span>
            </div>
            {g && (
              <div className="mt-0.5 text-[12px] font-medium leading-4 tabular-nums"
                style={{ color: g.up ? "var(--color-profit)" : "var(--color-loss)" }}>
                {g.text}
              </div>
            )}
          </div>
        )}
      >
        <EChart option={option} height={HEIGHT} ariaLabel={`${s.symbol} ${p.title} by period`} />
      </Frame>
    );
  }
  return (
    <Frame
      title={title}
      meta={
        <span className="block">
          <span>{meta}</span>
          <span className="mt-2 flex flex-wrap gap-x-5 gap-y-1.5 text-[12px]">
            {p.series.map((s, i) => {
              const li = latestIndex(s.values);
              const g = growth(s.values, p.labels, unit, quarterly);
              return (
                <span key={s.symbol} className="inline-flex items-center gap-1.5 tabular-nums">
                  <span className="h-[7px] w-[7px] rounded-full" style={{ background: COLORS[i] }} />
                  <LegendLogo symbol={s.symbol} url={logos[s.symbol] ?? null} />
                  <span className="font-medium text-foreground">{s.symbol}</span>
                  {li >= 0 && <span className="text-foreground/80">{fmtValue(s.values[li]!, unit)}</span>}
                  {g && <span>· {g.text}</span>}
                </span>
              );
            })}
          </span>
        </span>
      }
    >
      <EChart option={option} height={HEIGHT} ariaLabel={`${title} by period`} />
    </Frame>
  );
}

// ── valuation band: today's P/E within its own history ──────────────────────

export type ValuationBandPayload = {
  title: string;
  symbol?: string;
  years?: number;
  current: number;
  median: number;
  p25: number;
  p75: number;
  percentile: number;
  points: { t: string; v: number }[];
};

function ValuationBand({ p }: { p: ValuationBandPayload }): React.ReactElement {
  // Two decimals: the reply quotes the tool's figures, and the chart must match them.
  const x = (v: number): string => `${v.toFixed(2)}×`;
  const last = p.points[p.points.length - 1];
  const option = useMemo(() => ({
    ...BASE,
    grid: { ...BASE.grid, right: 8 },
    tooltip: {
      trigger: "axis",
      axisPointer: { type: "line", lineStyle: { color: "rgba(135,145,155,.5)", width: 1 } },
      formatter: (items: { value: [string, number] }[]) => {
        const it = items.find((i) => Array.isArray(i.value));
        if (!it) return "";
        const d = new Date(it.value[0]).toLocaleDateString("en-IN", { day: "numeric", month: "short", year: "numeric" });
        return `<div style="font-family:${FONT};font-size:11.5px"><div style="color:${MUTED}">${d}</div><div style="font-weight:600">${x(it.value[1])}</div></div>`;
      },
    },
    xAxis: {
      ...X_AXIS,
      type: "time",
      splitLine: { show: false },
      axisLabel: { ...X_AXIS.axisLabel, formatter: "{yyyy}" },
      minInterval: 3600 * 24 * 365 * 1000,
    },
    yAxis: { ...Y_AXIS, scale: true, axisLabel: { ...Y_AXIS.axisLabel, formatter: (v: number) => `${v}×` } },
    series: [{
      type: "line",
      data: p.points.map((pt) => [pt.t, pt.v]),
      showSymbol: false,
      lineStyle: { width: 1.75, color: COLORS[0] },
      itemStyle: { color: COLORS[0] },
      // The middle half of the history, and its median.
      markArea: {
        silent: true,
        itemStyle: { color: "rgba(41,98,255,.08)" },
        data: [[{ yAxis: p.p25 }, { yAxis: p.p75 }]],
      },
      markLine: {
        silent: true,
        symbol: "none",
        lineStyle: { color: MUTED, type: "dashed", width: 1 },
        label: { show: false }, // the header states the median
        data: [{ yAxis: p.median }],
      },
    }, {
      // Today: a dot where the line ends; the value is in the header.
      type: "scatter",
      silent: true,
      symbolSize: 7,
      itemStyle: { color: COLORS[0], borderColor: "#fff", borderWidth: 1.5 },
      data: last ? [[last.t, last.v]] : [],
    }],
  }), [p, last]);

  const yrs = `${p.years ?? 5}Y`;
  const rank = p.percentile <= 0 ? `Lowest in ${yrs}` : p.percentile >= 100 ? `Highest in ${yrs}`
    : `${ordinal(p.percentile)} percentile of ${yrs}`;
  const pos = p.current > p.p75 ? "above its usual range" : p.current < p.p25 ? "below its usual range" : "within its usual range";
  return (
    <Frame
      title={p.title}
      logo={p.symbol}
      meta={`${rank} · ${pos}`}
      aside={
        <div className="text-right">
          <div className="text-[15px] font-semibold leading-5 tabular-nums text-foreground">{x(p.current)}</div>
          <div className="mt-0.5 text-[12px] leading-4 tabular-nums text-muted-foreground">{`median ${x(p.median)}`}</div>
        </div>
      }
    >
      <EChart option={option} height={HEIGHT} ariaLabel={`${p.title} history with its middle-50% band and median`} />
    </Frame>
  );
}

// ── dispatch ────────────────────────────────────────────────────────────────

export function InlineChartView({ chart }: { chart: InlineChartPayload }): React.ReactElement | null {
  if (chart._render_hint === "financial_bars") return <FinancialBars p={chart as unknown as FinancialBarsPayload} />;
  if (chart._render_hint === "valuation_band") return <ValuationBand p={chart as unknown as ValuationBandPayload} />;
  if (chart._render_hint === "series_chart_card") {
    return <div className="!my-5"><SeriesChartCard payload={chart as unknown as SeriesChartPayload} /></div>;
  }
  return null;
}

/** The card's frame while the reply is still being written. */
export function InlineChartSkeleton(): React.ReactElement {
  return (
    <div className={`!my-5 w-full animate-pulse overflow-hidden rounded-xl px-4 pb-3 pt-3 ${SURFACE}`}
      data-testid="inline-chart-skeleton" aria-label="Loading chart">
      <div className="flex items-start justify-between">
        <div className="space-y-1.5">
          <div className="h-3 w-28 rounded bg-muted" />
          <div className="h-2.5 w-40 rounded bg-muted/70" />
        </div>
        <div className="h-4 w-14 rounded bg-muted" />
      </div>
      <div className="mt-3 rounded-lg bg-muted/40" style={{ height: HEIGHT }} />
    </div>
  );
}

/** A marker in the reply: a skeleton while the reply streams, then the chart.
 *  An id that never resolves draws nothing. */
export function InlineChart({ id, pending }: { id: string; pending?: boolean }): React.ReactElement | null {
  const chart = useInlineChart(id);
  if (pending) return <InlineChartSkeleton />;
  return chart ? <InlineChartView chart={chart} /> : null;
}
