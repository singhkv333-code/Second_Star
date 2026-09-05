"use client";
import dynamic from "next/dynamic";
import { useState } from "react";
import type { CustomSeriesRenderItem } from "echarts";
import { cashBridge, type CashStep } from "./researchMath";
import { BasisSelect, Figure, ResearchPanel, ResearchState, StatementSource, useResearchStatement, decimal, chartBase, categoryAxis, valueAxis } from "./ResearchPanel";
const EChart = dynamic(() => import("./EChart"), { ssr: false, loading: () => <div className="research-chart-loading" /> });
const short = (v: number): string => new Intl.NumberFormat("en-IN", { maximumFractionDigits: 1, notation: "compact" }).format(v);

/** Custom rectangles preserve waterfall geometry even when cash crosses zero.
 * Stacking positive/negative helper bars would place those steps incorrectly.
 */
function waterfall(steps: CashStep[]): CustomSeriesRenderItem {
  return (_params, api) => {
    const index = Number(api.value(0));
    const item = steps[index]!;
    const start = api.coord([index, item.start]), end = api.coord([index, item.end]);
    const categoryWidth = (api.size!([1, 0]) as number[])[0]!;
    const width = Math.min(categoryWidth * .5, 54);
    const color = item.total ? "#5385db" : item.label === "Unclassified" ? "#b29a66" : item.value >= 0 ? "#469a87" : "#c48363";
    return { type: "group", children: [
      { type: "rect", shape: { x: start[0]! - width / 2, y: Math.min(start[1]!, end[1]!), width, height: Math.max(1, Math.abs(end[1]! - start[1]!)), r: 3 }, style: { fill: color } },
      ...(index < steps.length - 2 ? [{ type: "line" as const, shape: { x1: start[0]! + width / 2, y1: end[1]!, x2: start[0]! + categoryWidth - width / 2, y2: end[1]! }, style: { stroke: "rgba(135,145,155,.4)", lineDash: [3, 3], lineWidth: 1 } }] : []),
    ] };
  };
}
export function CapitalAllocationPanel({ symbol }: { symbol: string }) {
  const [basis, setBasis] = useState<"consolidated" | "standalone">("consolidated");
  const [period, setPeriod] = useState("");
  const [mode, setMode] = useState<"bridge" | "history">("bridge");
  const state = useResearchStatement(symbol, "cash_flow", basis);
  const grid = state.data;
  const active = grid?.periods.includes(period) ? period : grid?.periods[0] ?? "";
  const bridge = grid ? cashBridge(grid, active) : null;
  const unit = grid?.unit?.trim() || "reported units";
  const isCrore = /^(Rs\.?\s*Cr\.?|INR\s*Crore|₹\s*Cr\.?)$/i.test(unit);
  const unitLabel = isCrore ? "₹ crore" : unit;
  const value = (n: number | null | undefined): string => n == null ? "Unavailable" : `${isCrore ? "₹" : ""}${decimal(n)}${isCrore ? " Cr" : ""}`;
  const rows = grid ? [...grid.periods].reverse().map((p) => ({ period: p, ...cashBridge(grid, p).values })) : [];
  const usable = !!grid?.available && rows.some((r) => r.operating !== null || r.investing !== null || r.financing !== null);
  return <ResearchPanel id="stock-capital" title="Capital allocation" controls={<BasisSelect value={basis} onChange={setBasis} />}>
    {!usable || !grid || !bridge ? <ResearchState {...state} message="Reported cash-flow history is unavailable for this company." /> : <>
      <div className="research-module-toolbar"><div className="research-choice" aria-label="Cash flow chart"><button type="button" aria-pressed={mode === "bridge"} onClick={() => setMode("bridge")}>Cash bridge</button><button type="button" aria-pressed={mode === "history"} onClick={() => setMode("history")}>Across the years</button></div>{mode === "bridge" ? <select aria-label="Cash flow reporting period" value={active} onChange={(e) => setPeriod(e.target.value)}>{grid.periods.map((p) => <option key={p}>{p}</option>)}</select> : <span className="research-meta">{rows.length} annual periods</span>}</div>
      <div className="research-chart-layout">
        <div className="research-chart-main">
          <div className="research-chart-caption"><span>{mode === "bridge" ? `Cash movement · ${active}` : "Sources and uses of cash"}</span><span>{unitLabel}</span></div>
          {mode === "bridge" && !bridge.steps.length ? <ResearchState loading={false} error={false} retry={state.retry} message="Incomplete cash bridge. Annual values are available under Across the years." /> : <EChart height={240} ariaLabel={mode === "bridge" ? `Cash flow waterfall for ${active}, ${unitLabel}` : `Annual cash flow by activity, ${unitLabel}`} option={mode === "bridge" ? {
            ...chartBase,
            tooltip: { trigger: "item", renderMode: "richText", formatter: (p: { dataIndex: number }) => { const s = bridge.steps[p.dataIndex]!; return `${s.label}\n${value(s.value)}`; } },
            xAxis: { ...categoryAxis, data: bridge.steps.map((s) => s.label), axisLabel: { ...categoryAxis.axisLabel, interval: 0, formatter: (s: string) => s.replace(" ", "\n") } },
            yAxis: { ...valueAxis, axisLabel: { ...valueAxis.axisLabel, formatter: short } },
            series: [{ type: "custom", renderItem: waterfall(bridge.steps), dimensions: ["step", "start", "end", "movement"], encode: { x: 0, y: [1, 2], tooltip: [3] }, data: bridge.steps.map((s, i) => [i, s.start, s.end, s.value]) }],
          } : {
            ...chartBase, tooltip: { ...chartBase.tooltip, valueFormatter: (n: number | null) => value(n) },
            xAxis: { ...categoryAxis, data: rows.map((r) => r.period) }, yAxis: { ...valueAxis, axisLabel: { ...valueAxis.axisLabel, formatter: short } },
            series: ([['operating', 'Operating', '#469a87'], ['investing', 'Investing', '#c48363'], ['financing', 'Financing', '#8d91b8']] as const).map(([key, name, color]) => ({ name, type: "bar", barMaxWidth: 16, itemStyle: { color, borderRadius: 2 }, data: rows.map((r) => r[key]) })),
          }} />}
          <div className="research-chart-key">{(mode === "bridge" ? [["#5385db", "Cash balance"], ["#469a87", "Cash inflow"], ["#c48363", "Cash outflow"]] : [["#469a87", "Operating"], ["#c48363", "Investing"], ["#8d91b8", "Financing"]]).map(([color, label]) => <span key={label}><i style={{ background: color }} />{label}</span>)}</div>
        </div>
        <aside className="research-chart-aside">
          <Figure label="Operating cash flow" value={value(bridge.values.operating)} note={active} />
          <Figure label="Closing cash" value={value(bridge.values.closing)} note={active} />
          <div className="research-ledger"><span>Investing activities</span><strong>{value(bridge.values.investing)}</strong><span>Financing activities</span><strong>{value(bridge.values.financing)}</strong></div>
          <p className="research-aside-note">Activity totals; detailed allocation unavailable.</p>
        </aside>
      </div>
      <footer className="research-module-foot"><StatementSource data={grid} requested={basis} /><span>{unitLabel} · negative values are cash outflows</span></footer>
      <details className="research-method"><summary>Reported figures & reconciliation</summary><p>The bridge connects reported opening and closing cash through signed operating, investing and financing cash flows. FX and other adjustments appear only when reported. Any remaining difference above 0.05 reported units is labelled Unclassified; smaller differences can reflect statement rounding. Investing cash flow includes more than capex; financing cash flow includes more than shareholder distributions.</p>{bridge.residual !== null && <p>Reconciliation difference for {active}: {value(bridge.residual)}.</p>}<div className="research-data-scroll"><table><caption className="sr-only">Annual cash movements in {unitLabel}</caption><thead><tr><th>Period</th><th>Operating</th><th>Investing</th><th>Financing</th><th>Closing cash</th></tr></thead><tbody>{rows.map((r) => <tr key={r.period}><th scope="row">{r.period}</th>{([r.operating, r.investing, r.financing, r.closing]).map((n, i) => <td key={i}>{value(n)}</td>)}</tr>)}</tbody></table></div></details>
    </>}
  </ResearchPanel>;
}
