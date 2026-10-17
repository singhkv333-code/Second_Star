"use client";
import { ValuationHistoryPanel } from "./ValuationHistoryPanel";
import { CapitalAllocationPanel } from "./CapitalAllocationPanel";
import { BenchmarkPerformancePanel } from "./BenchmarkPerformancePanel";
import "./research-extensions.css";

export function ResearchExtensions({ symbol, exchange }: { symbol: string; exchange: "NSE" | "BSE" }) {
  return <div className="research-extensions" key={symbol}>
    <ValuationHistoryPanel symbol={symbol} />
    <CapitalAllocationPanel symbol={symbol} />
    <BenchmarkPerformancePanel symbol={symbol} exchange={exchange} />
  </div>;
}
