"use client";
import { useEffect, useState, type ReactNode } from "react";
import { getStatement, type StatementResponse, type StatementType } from "@/lib/api";
import { isError } from "@/lib/types";

export function useResearchStatement(symbol: string, type: StatementType, basis: "consolidated" | "standalone") {
  const [attempt, setAttempt] = useState(0);
  const key = `${symbol}:${type}:${basis}:${attempt}`;
  const [state, setState] = useState<{ key: string; data: StatementResponse | null; error: boolean } | null>(null);
  useEffect(() => {
    let dead = false;
    getStatement(symbol, type, basis, 10).then((r) => {
      if (!dead) setState({ key, data: isError(r) ? null : r.data, error: isError(r) });
    }).catch(() => { if (!dead) setState({ key, data: null, error: true }); });
    return () => { dead = true; };
  }, [symbol, type, basis, key]);
  return { data: state?.key === key ? state.data : null, loading: state?.key !== key, error: state?.key === key && state.error, retry: () => setAttempt((n) => n + 1) };
}
export function ResearchPanel({ id, title, controls, children }: { id: string; title: string; controls?: ReactNode; children: ReactNode }) {
  return <section id={id} className="research-module" aria-labelledby={`${id}-title`}>
    <header className="research-module-head">
      <h2 id={`${id}-title`}>{title}</h2>
      {controls && <div className="research-module-controls">{controls}</div>}
    </header>
    {children}
  </section>;
}
export function ResearchState({ loading, error, retry, message }: { loading: boolean; error: boolean; retry: () => void; message: string }) {
  return <div className={`research-module-empty${loading ? " is-loading" : ""}`} aria-busy={loading} role="status">
    <span>{loading ? "Loading company history…" : error ? "This data could not be loaded." : message}</span>
    {error && <button type="button" onClick={retry}>Try again</button>}
  </div>;
}
export function BasisSelect({ value, onChange }: { value: "consolidated" | "standalone"; onChange: (v: "consolidated" | "standalone") => void }) {
  return <select aria-label="Reporting basis" value={value} onChange={(e) => onChange(e.target.value as "consolidated" | "standalone")}><option value="consolidated">Consolidated</option><option value="standalone">Standalone</option></select>;
}
export function StatementSource({ data, requested }: { data: StatementResponse; requested: string }) {
  return <span>{data.source === "moneycontrol" ? "Moneycontrol" : data.source || "Source unavailable"} · {data.basis === "consolidated" ? "Consolidated" : "Standalone"}{data.basis !== requested ? " (available basis)" : ""}</span>;
}
export function Figure({ label, value, note }: { label: string; value: string; note?: string }) {
  return <div className="research-figure"><span>{label}</span><strong>{value}</strong>{note && <small>{note}</small>}</div>;
}
export const decimal = (value: number, digits = 2): string => new Intl.NumberFormat("en-IN", { maximumFractionDigits: digits, minimumFractionDigits: digits }).format(value);
export const signedPercent = (value: number | null | undefined): string => value == null || !Number.isFinite(value) ? "—" : `${value > 0 ? "+" : ""}${decimal(value)}%`;
export const chartBase = {
  animation: false,
  grid: { left: 12, right: 18, top: 24, bottom: 12, containLabel: true },
  tooltip: { trigger: "axis", renderMode: "richText" },
};
export const valueAxis = { type: "value", splitNumber: 4, axisLine: { show: false }, axisTick: { show: false }, axisLabel: { color: "#84909c", fontSize: 10 }, splitLine: { lineStyle: { color: "rgba(135,145,155,.14)", type: "dashed" } } };
export const categoryAxis = { type: "category", axisLine: { show: false }, axisTick: { show: false }, axisLabel: { color: "#84909c", fontSize: 10, margin: 14 } };
