"use client";

import { StockDetailPage } from "@/components/StockDetailPage";

/**
 * Client wrapper that mounts Pivot's stock detail page — StockDetailPage is
 * copied here unchanged — under charto's own chrome instead of Pivot's
 * AppShell. charto has no sidebar to keep: the only navigation that makes
 * sense from a company page is back to the chart for that symbol.
 */
export function StockSymbolView({ symbol }: { symbol: string }): React.ReactElement {
  return (
    <div className="min-h-screen bg-background">
      <div className="flex h-[52px] items-center gap-3 border-b border-border/40 px-6">
        <a
          href="http://localhost:5173/index.html"
          className="text-[15px] font-semibold tracking-tight"
        >
          Charto<span style={{ color: "#2962ff" }}>.</span>
        </a>
        <div className="flex-1" />
        <a
          href={`http://localhost:5173/index.html?symbol=${encodeURIComponent(symbol)}`}
          className="rounded-md px-3 py-1.5 text-[13px] font-medium text-white"
          style={{ background: "#2962ff" }}
        >
          Open chart →
        </a>
      </div>
      <StockDetailPage symbol={symbol} />
    </div>
  );
}
