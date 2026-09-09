"use client";
import dynamic from "next/dynamic";
import { useEffect, useMemo, useState } from "react";
import { getResearchPrices, type OhlcResponse } from "@/lib/api";
import { isError } from "@/lib/types";
import { alignPrices, performance, RETURN_WINDOWS, windowPrices, type ReturnWindow } from "./researchMath";
import { Figure, ResearchPanel, ResearchState, signedPercent, decimal, chartBase, categoryAxis, valueAxis } from "./ResearchPanel";
const EChart = dynamic(() => import("./EChart"), { ssr: false, loading: () => <div className="research-chart-loading" /> });
const BENCHMARKS = [{ symbol: "NIFTY 50", name: "Nifty 50", exchange: "NSE" }, { symbol: "SENSEX", name: "Sensex", exchange: "BSE" }, { symbol: "NIFTY BANK", name: "Nifty Bank", exchange: "NSE" }] as const;
const dateLabel = (v: string): string => new Date(`${v}T00:00:00Z`).toLocaleDateString("en-IN", { day: "numeric", month: "short", year: "numeric", timeZone: "UTC" });

export function BenchmarkPerformancePanel({ symbol, exchange }: { symbol: string; exchange: "NSE" | "BSE" }) {
  const [benchmark, setBenchmark] = useState<string>("NIFTY 50");
  const [range, setRange] = useState<ReturnWindow>("1Y");
  const [mode, setMode] = useState<"growth" | "drawdown">("growth");
  const [attempt, setAttempt] = useState(0);
  const peer = BENCHMARKS.find((b) => b.symbol === benchmark)!;
  const key = `${symbol}:${exchange}:${benchmark}:${attempt}`;
  const [state, setState] = useState<{ key: string; data?: [OhlcResponse, OhlcResponse]; error?: boolean }>();
  useEffect(() => {
    let dead = false;
    async function load(): Promise<void> {
      let results = await Promise.all([getResearchPrices(symbol, exchange), getResearchPrices(peer.symbol, peer.exchange)]);
      if (dead) return;
      const [a, b] = results;
      if (!a || !b || isError(a) || isError(b) || a.data.source !== b.data.source) {
        // Kite first for both. If only one resolves, compare two yfinance
        // series rather than silently mixing corporate-action conventions.
        results = await Promise.all([getResearchPrices(symbol, exchange, "yfinance"), getResearchPrices(peer.symbol, peer.exchange, "yfinance")]);
      }
      const [stock, index] = results;
      if (!stock || !index || isError(stock) || isError(index)) throw new Error("History unavailable");
      if (stock.data.price_basis !== "unadjusted" || index.data.price_basis !== "unadjusted" || stock.data.source !== index.data.source || stock.data.interval !== "1d" || index.data.interval !== "1d") throw new Error("Comparable daily price history unavailable");
      if (!dead) setState({ key, data: [stock.data, index.data] });
    }
    void load().catch(() => { if (!dead) setState({ key, error: true }); });
    return () => { dead = true; };
  }, [key, symbol, exchange, peer.symbol, peer.exchange]);
  const current = state?.key === key ? state : undefined;
  const aligned = useMemo(() => current?.data ? alignPrices(...current.data) : [], [current?.data]);
  const selected = useMemo(() => windowPrices(aligned, range), [aligned, range]);
  const stock = performance(selected, "stock"), index = performance(selected, "benchmark");
  const rows = RETURN_WINDOWS.map((r) => { const points = windowPrices(aligned, r); return { range: r, points, stock: performance(points, "stock"), index: performance(points, "benchmark") }; });
  const relative = stock && index ? stock.total - index.total : null;
  return <ResearchPanel id="stock-benchmarks" title="Benchmark comparison" controls={<select aria-label="Performance benchmark" value={benchmark} onChange={(e) => setBenchmark(e.target.value)}>{BENCHMARKS.map((b) => <option key={b.symbol} value={b.symbol}>{b.name}</option>)}</select>}>
    {!current?.data || aligned.length < 2 ? <ResearchState loading={!current} error={!!current?.error} retry={() => setAttempt((n) => n + 1)} message="There are not enough matching daily prices to compare this stock with the selected benchmark." /> : <>
      <div className="research-module-toolbar"><div className="research-choice" aria-label="Performance chart mode"><button type="button" aria-pressed={mode === "growth"} onClick={() => setMode("growth")}>Growth of 100</button><button type="button" aria-pressed={mode === "drawdown"} onClick={() => setMode("drawdown")}>Drawdown</button></div><div className="research-choice research-periods" aria-label="Benchmark time range">{RETURN_WINDOWS.map((r) => <button type="button" aria-pressed={range === r} key={r} onClick={() => setRange(r)}>{r}</button>)}</div></div>
      <div className="research-chart-layout">
        <div className="research-chart-main"><div className="research-chart-caption"><span>{mode === "growth" ? "Indexed closing prices" : "Decline from the running peak"}</span><span>{selected.length ? `${dateLabel(selected[0]!.date)} – ${dateLabel(selected.at(-1)!.date)}` : range}</span></div>
          {!stock || !index ? <ResearchState loading={false} error={false} retry={() => {}} message={`A full ${range} comparison is unavailable. Choose a shorter period.`} /> : <EChart height={240} ariaLabel={`${symbol} versus ${peer.name}, ${mode === "growth" ? "indexed to 100" : "drawdown"}, ${range}`} option={{
            ...chartBase,
            tooltip: { ...chartBase.tooltip, valueFormatter: (n: number) => `${decimal(n)}${mode === "drawdown" ? "%" : ""}` },
            xAxis: { ...categoryAxis, boundaryGap: false, data: selected.map((p) => p.date), axisLabel: { ...categoryAxis.axisLabel, hideOverlap: true, formatter: (v: string) => new Date(`${v}T00:00:00Z`).toLocaleDateString("en-IN", { month: "short", year: "2-digit", timeZone: "UTC" }) } },
            yAxis: { ...valueAxis, scale: true, ...(mode === "drawdown" ? { max: 0 } : {}), axisLabel: { ...valueAxis.axisLabel, formatter: (n: number) => `${decimal(n, 0)}${mode === "drawdown" ? "%" : ""}` } },
            series: ([['stock', symbol, '#5385db'], ['benchmark', peer.name, '#b49a69']] as const).map(([field, name, color]) => {
              let peak = selected[0]![field];
              const values = selected.map((p) => { peak = Math.max(peak, p[field]); return mode === "growth" ? p[field] / selected[0]![field] * 100 : (p[field] / peak - 1) * 100; });
              return { type: "line", name, data: values, showSymbol: false, smooth: false, lineStyle: { color, width: field === "stock" ? 2.4 : 1.8, type: field === "stock" ? "solid" : "dashed" }, itemStyle: { color }, ...(mode === "drawdown" && field === "stock" ? { areaStyle: { color, opacity: .07 } } : {}) };
            }),
          }} />}
          <div className="research-chart-key"><span><i style={{ background: "#5385db" }} />{symbol}</span><span><i style={{ background: "#b49a69" }} />{peer.name}</span></div>
        </div>
        <aside className="research-chart-aside">
          <Figure label={`${symbol} · ${range} price return`} value={signedPercent(stock?.total)} />
          <Figure label={`${peer.name} · ${range} price return`} value={signedPercent(index?.total)} />
          <div className="research-ledger"><span>Return difference</span><strong>{relative === null ? "—" : `${relative > 0 ? "+" : ""}${decimal(relative)} pp`}</strong><span>Stock maximum drawdown</span><strong>{signedPercent(stock?.drawdown)}</strong>{stock?.cagr != null && <><span>Stock annualised return</span><strong>{signedPercent(stock.cagr)}</strong></>}</div>
        </aside>
      </div>
      <div className="research-return-table research-data-scroll"><table><caption>Trailing price returns <span>Same dates for both series</span></caption><thead><tr><th>Period</th>{RETURN_WINDOWS.map((r) => <th key={r}>{r}</th>)}</tr></thead><tbody>{[[symbol, "stock"], [peer.name, "index"], ["Difference (pp)", "difference"]].map(([label, field]) => <tr key={field}><th scope="row">{label}</th>{rows.map((r) => <td key={r.range} title={r.points.length ? `${dateLabel(r.points[0]!.date)} – ${dateLabel(r.points.at(-1)!.date)}` : "Insufficient history"}>{field === "difference" ? r.stock && r.index ? `${r.stock.total - r.index.total > 0 ? "+" : ""}${decimal(r.stock.total - r.index.total)}` : "—" : signedPercent(field === "stock" ? r.stock?.total : r.index?.total)}</td>)}</tr>)}</tbody></table></div>
      <footer className="research-module-foot"><span>Data through {dateLabel(aligned.at(-1)!.date)} · dividends not reinvested</span></footer>
      <details className="research-method"><summary>How this comparison is calculated</summary><p>Both series use the same provider and matching Indian trading dates. Each begins at 100 on the first shared date of the chosen period. Return differences are percentage points, not risk-adjusted alpha. Maximum drawdown measures the largest decline from a running closing-price peak inside that period.</p><p>We use provider closing prices with no additional dividend adjustment. Corporate actions can affect historical prices; this is not a total-return or execution simulation. Kite is preferred; if only one instrument has Kite history, both series use yfinance. Windows without enough history show a dash. Returns of one year or longer can be annualised using actual elapsed days.</p></details>
    </>}
  </ResearchPanel>;
}
