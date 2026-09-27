"use client";

/**
 * SeriesChartCard — the chart a tool attaches from the series it computed its
 * answer from (`_chart`, `series_chart_card`): a comparison's closes, a line
 * item over the years. The points arrive with the tool result, so it draws
 * the exact data behind the reply's numbers, with nothing fetched.
 */

import React from "react";
import { useRouter } from "next/navigation";
import { CandlestickChart, Building2 } from "lucide-react";
import { StockPriceChart } from "@/components/chart/StockPriceChart";

export type SeriesChartPayload = {
  _render_hint?: "series_chart_card";
  title: string;
  unit?: string;
  normalize?: boolean;
  series: { symbol: string; points: { t: string; v: number }[] }[];
};

const COLORS = ["#2962FF", "#fb8500", "#16a34a", "#9333ea", "#e11d48", "#0891b2"];

const LINK =
  "inline-flex items-center gap-1.5 rounded-md px-2 py-1 text-[12px] text-muted-foreground " +
  "transition-colors hover:bg-muted/70 hover:text-foreground";

const pct = (v: number): string => `${v >= 0 ? "+" : ""}${v.toFixed(2)}%`;

function formatter(unit: string): (v: number) => string {
  const n = (v: number, d = 0): string =>
    v.toLocaleString("en-IN", { maximumFractionDigits: d });
  if (/cr/i.test(unit)) return (v) => `₹${n(v)} Cr`;
  if (unit === "%" || /percent/i.test(unit)) return (v) => `${n(v, 2)}%`;
  if (unit === "inr") return (v) => `₹${n(v, 2)}`;
  return (v) => (unit ? `${n(v, 2)} ${unit}` : n(v, 2));
}

export function SeriesChartCard({ payload }: { payload: SeriesChartPayload }): React.ReactElement {
  const router = useRouter();
  const lines = payload.series.filter((s) => s.points.length >= 2);
  const normalize = payload.normalize !== false;
  const fmt = normalize ? undefined : formatter(payload.unit ?? "");
  const change = (s: SeriesChartPayload["series"][number]): number | null => {
    const a = s.points[0]!.v;
    const b = s.points[s.points.length - 1]!.v;
    return a ? ((b - a) / Math.abs(a)) * 100 : null;
  };
  const single = lines.length === 1 ? lines[0]! : null;
  const last = single?.points[single.points.length - 1];
  const asOf = lines[0]?.points[lines[0].points.length - 1]?.t;
  const openChart = (symbol: string): void => {
    window.dispatchEvent(new CustomEvent("pivot:open-chart", { detail: { symbol } }));
  };

  return (
    <div className="w-full overflow-hidden rounded-xl border border-black/[0.05] bg-[#f6f7f9] dark:border-border dark:bg-card" data-testid="series-chart-card">
      <div className="flex flex-wrap items-start justify-between gap-3 px-4 pt-3.5">
        <div className="min-w-0">
          <div className="truncate text-[15px] font-semibold text-foreground">{payload.title}</div>
          {lines.length > 1 && (
            <div className="mt-1.5 flex flex-wrap gap-x-5 gap-y-1.5">
              {lines.map((s, i) => {
                const c = change(s);
                return (
                  <div key={s.symbol} className="flex items-center gap-2 text-[13px]">
                    <span className="h-2 w-2 rounded-full" style={{ background: COLORS[i] }} />
                    <span className="font-medium text-foreground">{s.symbol}</span>
                    {c !== null && (
                      <span
                        className="tabular-nums"
                        style={{ color: c >= 0 ? "var(--color-profit)" : "var(--color-loss)" }}
                      >
                        {pct(c)}
                      </span>
                    )}
                  </div>
                );
              })}
            </div>
          )}
        </div>
        {single && last && (
          <div className="text-right">
            <div className="text-[18px] font-semibold tabular-nums text-foreground">
              {(fmt ?? formatter("inr"))(last.v)}
            </div>
            {change(single) !== null && (
              <div
                className="text-[12.5px] font-medium tabular-nums"
                style={{ color: change(single)! >= 0 ? "var(--color-profit)" : "var(--color-loss)" }}
              >
                {`${pct(change(single)!)} since ${single.points[0]!.t.slice(0, 4)}`}
              </div>
            )}
          </div>
        )}
      </div>

      {asOf && (
        <div className="px-4 pt-1 text-[11px] text-muted-foreground">
          As of {new Date(asOf).toLocaleDateString("en-IN", { day: "numeric", month: "short", year: "numeric" })}
        </div>
      )}

      <div className="px-1 pt-2" style={{ height: 240 }}>
        <StockPriceChart
          height={240}
          normalize={normalize}
          valueFormatter={fmt}
          seriesDefs={lines.map((s, i) => ({ symbol: s.symbol, color: COLORS[i % COLORS.length]!, points: s.points }))}
        />
      </div>

      {lines.length <= 2 && (
        <div className="flex flex-wrap gap-0.5 px-2 pb-2 pt-1">
          {lines.map((s) => (
            <button key={s.symbol} type="button" className={LINK} onClick={() => openChart(s.symbol)}>
              <CandlestickChart size={13} strokeWidth={1.75} aria-hidden />
              {lines.length === 1 ? "Detailed chart" : `${s.symbol} chart`}
            </button>
          ))}
          {single && (
            <button
              type="button"
              className={LINK}
              onClick={() => router.push(`/stock/${encodeURIComponent(single.symbol)}`)}
            >
              <Building2 size={13} strokeWidth={1.75} aria-hidden />
              Company page
            </button>
          )}
        </div>
      )}
      {lines.length > 2 && <div className="pb-2" />}
    </div>
  );
}
