"use client";

import dynamic from "next/dynamic";
import * as React from "react";
import type { MixChart, MixResponse } from "@/lib/api";
import { EmptyNote, PanelHead } from "./chrome";
import { Select, SelectTrigger, SelectValue, SelectContent, SelectGroup, SelectLabel, SelectItem } from "@/components/ui/select";
import "./mix-panel.css";

const EChart = dynamic(() => import("./EChart"), { ssr: false, loading: () => <div style={{ height: 260 }} /> });
const COLORS = ["#347f91", "#6b9aab", "#94b8bc", "#a5ab85", "#b99c73", "#967f85", "#767f9b", "#79978a", "#bbac98", "#869da6"];
const percent = (value: number) => Number.isFinite(value) ? value.toFixed(1) + "%" : "—";
const dateLabel = (time: number) => new Date(time).toLocaleDateString("en-IN", { month: "short", year: "2-digit", timeZone: "UTC" });
const shortTitle = (title: string) => title.replace(/Product Wise Break-Up/gi, "Products").replace(/Location Wise Break-Up/gi, "Geography").replace(/Operating Profit Break-Up/gi, "Operating profit").replace(/Asset Break-Up/gi, "Assets").replace(/Capex - Segment Wise/gi, "Capital expenditure");
const category = (title: string) => /profit|capex|asset/i.test(title) ? "Profit & investment" : "Business & geography";

export function MixPanel({ data }: { data: MixResponse }): React.ReactElement {
  const charts = data.charts ?? [];
  const [selection, setSelection] = React.useState<{ symbol: string; index: number }>();
  const index = selection?.symbol === data.symbol ? selection.index : 0;
  const chart = charts[index] ?? charts[0];
  const [mode, setMode] = React.useState<"latest" | "history">("latest");
  const names = React.useMemo(() => Array.from(new Set([...(chart?.series.map(s => s.name) ?? []), ...(chart?.current.map(s => s.name) ?? [])])), [chart]);
  const color = (name: string) => COLORS[names.indexOf(name) % COLORS.length]!;
  const rows = React.useMemo(() => [...(chart?.current ?? [])].sort((a,b) => b.pct - a.pct), [chart]);
  const option = React.useMemo(() => chart ? historyOption(chart, names) : null, [chart, names]);
  if (!chart) return <EmptyNote>No segment breakdown available for this company.</EmptyNote>;
  const groups = ["Business & geography", "Profit & investment"];
  const periods = [...new Set(chart.series.flatMap(s => s.points.map(p => p.t)))].sort((a,b) => a-b);
  return <div className="segment-mix">
    <PanelHead title="Segment mix" right={charts.length > 1 ? <Select value={String(index)} onValueChange={value => setSelection({ symbol: data.symbol, index: Number(value) })}>
      <SelectTrigger className="mix-select-trigger" aria-label="Segment breakdown"><SelectValue>{shortTitle(chart.title)}</SelectValue></SelectTrigger>
      <SelectContent className="mix-select-menu" align="end" sideOffset={6}>
        {groups.map(group => <SelectGroup key={group}><SelectLabel className="mix-select-label">{group}</SelectLabel>
          {charts.map((item,i) => category(item.title) === group ? <SelectItem className="mix-select-item" key={i} value={String(i)} title={item.title}>{shortTitle(item.title)}</SelectItem> : null)}
        </SelectGroup>)}
      </SelectContent>
    </Select> : undefined} />
    <div className="mix-toolbar">
      <div className="mix-view-switch" aria-label="Segment chart view">{(["latest", "history"] as const).map(view => <button type="button" key={view} aria-pressed={mode === view} onClick={() => setMode(view)}>{view === "latest" ? "Latest split" : "Over time"}</button>)}</div>
      <span>{mode === "latest" ? shortTitle(chart.title) : periods.length ? dateLabel(periods[0]!) + " – " + dateLabel(periods.at(-1)!) : "History unavailable"}</span>
    </div>
    {mode === "latest" ? <div className="mix-latest" aria-label="Latest reported segment split">
      <div className="mix-table-head"><span>Segment</span><span>Share of total</span></div>
      {rows.length ? rows.map(row => <div className="mix-share-row" key={row.name}>
        <div className="mix-segment-name"><i style={{ background: color(row.name) }} /><span>{row.name}</span></div>
        <div className="mix-share-track" aria-hidden="true"><span style={{ width: Math.min(100,Math.max(0,row.pct)) + "%", background: color(row.name) }} /></div>
        <strong>{percent(row.pct)}</strong>
      </div>) : <EmptyNote>No current split reported.</EmptyNote>}
    </div> : periods.length && option ? <div className="mix-history">
      <EChart option={option} height={260} ariaLabel={shortTitle(chart.title) + ": reported segment shares over time"} />
      <div className="mix-legend">{names.map(name => <span key={name}><i style={{ background: color(name) }} />{name}</span>)}</div>
    </div> : <EmptyNote>Segment history unavailable.</EmptyNote>}
    <footer className="mix-source">{data.source_name || "Company segment disclosures"}<span>{mode === "latest" ? "Latest reported · filing date not supplied" : "Reported shares · missing observations left blank"}</span></footer>
  </div>;
}

function historyOption(chart: MixChart, names: string[]): Record<string, unknown> {
  const times = [...new Set(chart.series.flatMap(s => s.points.map(p => p.t)))].sort((a,b) => a-b);
  return {
    grid: { left: 8, right: 8, top: 16, bottom: 8, containLabel: true },
    tooltip: { trigger: "item", confine: true, renderMode: "richText", formatter: (p: { seriesName: string; name: string; value: number }) => p.name + "\n" + p.seriesName + "   " + percent(p.value), padding: 12 },
    xAxis: { type: "category", data: times.map(dateLabel), axisLine: { show: false }, axisTick: { show: false }, axisLabel: { fontSize: 10, hideOverlap: true } },
    yAxis: { type: "value", axisLabel: { fontSize: 10, formatter: "{value}%" }, splitLine: { lineStyle: { opacity: .2 } } },
    series: chart.series.map(s => {
      const values = new Map(s.points.map(p => [p.t, p.pct]));
      return { name: s.name, type: "bar", stack: "segments", barMaxWidth: 38, itemStyle: { color: COLORS[names.indexOf(s.name) % COLORS.length] }, emphasis: { focus: "series" }, data: times.map(t => values.get(t) ?? null) };
    }),
  };
}
