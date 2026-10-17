"use client";
import dynamic from "next/dynamic";
import { useState } from "react";
import { RATIO_METRICS, ratioHistory, type RatioKey } from "./researchMath";
import { BasisSelect, Figure, ResearchPanel, ResearchState, StatementSource, useResearchStatement, decimal, chartBase, categoryAxis, valueAxis } from "./ResearchPanel";
const EChart = dynamic(() => import("./EChart"), { ssr: false, loading: () => <div className="research-chart-loading" /> });

export function ValuationHistoryPanel({ symbol }: { symbol: string }) {
  const [basis, setBasis] = useState<"consolidated" | "standalone">("consolidated");
  const [metric, setMetric] = useState<RatioKey>("pb");
  const state = useResearchStatement(symbol, "ratios", basis);
  const available = state.data ? RATIO_METRICS.filter((m) => ratioHistory(state.data!, m.key).valid.length >= 2) : [];
  const active = available.some((m) => m.key === metric) ? metric : available[0]?.key;
  const history = state.data && active ? ratioHistory(state.data, active) : null;
  const stats = history && history.latest ? history : null;
  return <ResearchPanel id="stock-valuation" title="Valuation history" controls={<BasisSelect value={basis} onChange={setBasis} />}>
    {!stats || !state.data ? <ResearchState {...state} message="Not enough reported valuation history for this company. At least two comparable annual observations are needed." /> : <>
      <div className="research-module-toolbar"><div className="research-choice" aria-label="Valuation metric">{available.map((m) => <button type="button" key={m.key} aria-pressed={active === m.key} onClick={() => setMetric(m.key)}>{m.label}</button>)}</div><span className="research-meta">Annual observations · {stats.valid.length} periods</span></div>
      <div className="research-chart-layout">
        <div className="research-chart-main">
          <div className="research-chart-caption"><span>{stats.metric.name}</span><span>Multiple (×)</span></div>
          <EChart height={240} ariaLabel={`${stats.metric.label} annual history with median and interquartile range`} option={{
            ...chartBase,
            xAxis: { ...categoryAxis, data: stats.points.map((p) => p.period), boundaryGap: false },
            yAxis: { ...valueAxis, scale: true, axisLabel: { ...valueAxis.axisLabel, formatter: (v: number) => `${decimal(v, 1)}×` } },
            tooltip: { ...chartBase.tooltip, valueFormatter: (v: number | null) => v == null ? "Unavailable" : `${decimal(v)}×` },
            series: [{ type: "line", name: stats.metric.label, data: stats.points.map((p) => p.value), smooth: false, connectNulls: false, showSymbol: true, symbolSize: 6,
              lineStyle: { width: 2.5, color: "#5385db" }, itemStyle: { color: "#5385db", borderWidth: 2 },
              markArea: { silent: true, itemStyle: { color: "rgba(83,133,219,.09)" }, data: [[{ yAxis: stats.q1 }, { yAxis: stats.q3 }]] },
              markLine: { silent: true, symbol: "none", lineStyle: { color: "#8996a6", type: "dashed", width: 1 }, label: { show: false }, data: [{ yAxis: stats.median }] },
            }],
          }} />
          <div className="research-chart-key"><span><i style={{ background: "#5385db" }} />Reported multiple</span><span><i className="is-band" />Middle 50% of observations</span><span><i className="is-dashed" />Median</span></div>
        </div>
        <aside className="research-chart-aside">
          <Figure label="Latest reported" value={`${decimal(stats.latest!.value)}×`} note={stats.latest!.period} />
          <Figure label="Historical median" value={`${decimal(stats.median)}×`} note={`${stats.valid[0]!.period} – ${stats.latest!.period}`} />
          <div className="research-range-label"><span>Observed range</span><strong>{decimal(stats.min)}–{decimal(stats.max)}×</strong></div>
          <div className="research-distribution" role="img" aria-label={`Latest multiple ${decimal(stats.latest!.value)} times; observed range ${decimal(stats.min)} to ${decimal(stats.max)}`}>
            <span style={{ left: `${stats.max === stats.min ? 50 : (stats.latest!.value - stats.min) / (stats.max - stats.min) * 100}%` }} />
          </div>
          <p className="research-aside-note">{stats.latest!.value === stats.median ? "At median" : `${decimal(Math.abs(stats.latest!.value / stats.median - 1) * 100, 1)}% ${stats.latest!.value > stats.median ? "above" : "below"} median`}</p>
        </aside>
      </div>
      <footer className="research-module-foot"><StatementSource data={state.data} requested={basis} /><span>Latest observation: {stats.latest!.period} · not a live valuation</span></footer>
      <details className="research-method"><summary>Data & methodology</summary><p>The shaded band spans the 25th to 75th percentiles of positive reported annual multiples. It is not a fair-value estimate. Missing and non-positive multiples are excluded from statistics and left as chart gaps. Annual observations do not describe the full daily trading range.</p><p>Source row: {stats.row?.line_item}. Rounded earnings yields are not inverted to estimate P/E.</p><div className="research-data-scroll"><table><caption className="sr-only">Reported valuation observations</caption><thead><tr><th>Period</th><th>{stats.metric.label}</th></tr></thead><tbody>{stats.points.map((p) => <tr key={p.period}><th scope="row">{p.period}</th><td>{p.value === null ? "Unavailable" : `${decimal(p.value)}×`}</td></tr>)}</tbody></table></div></details>
    </>}
  </ResearchPanel>;
}
