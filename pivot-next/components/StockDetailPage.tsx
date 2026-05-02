"use client";

/**
 * StockDetailPage — Phase 3 stock detail surface.
 *
 * Route: /stock/[symbol]
 * Reachable from: Portfolio symbol click, agent step config, chat ticker mention.
 *
 * Header: symbol, name, exchange, sector, price, day change ±%, stats strip.
 * Main chart: Recharts line chart with time range buttons (sparkline endpoint).
 * Automation overlays: horizontal dashed lines from GET /api/stocks/{symbol}/automations
 *   Renders trigger price overlay lines from GET /api/stocks/{symbol}/automations.
 * Side panel tabs: Fundamentals / News / Related Agents.
 */

import { useEffect, useState } from "react";
import {
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { format, parseISO } from "date-fns";
import {
  AlertCircle,
  ArrowLeft,
  ArrowUpRight,
  ArrowDownRight,
  ExternalLink,
} from "lucide-react";
import Link from "next/link";
import { Skeleton } from "@/components/ui/skeleton";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";
import {
  getStockQuote,
  getSparkline,
  getStockAutomations,
  getNews,
  listWorkflows,
  type StockQuote,
  type SparklineResponse,
  type SparklineRange,
  type StockAutomation,
  type NewsItem,
} from "@/lib/api";
import { isError } from "@/lib/types";
import type { WorkflowSummary } from "@/lib/types";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

type QuoteState =
  | { kind: "loading" }
  | { kind: "error"; message: string }
  | { kind: "ok"; quote: StockQuote };

type SparkState =
  | { kind: "loading" }
  | { kind: "error" }
  | { kind: "ok"; data: SparklineResponse };

type SideTab = "fundamentals" | "news" | "agents";

// ---------------------------------------------------------------------------
// Formatters
// ---------------------------------------------------------------------------

const INR = new Intl.NumberFormat("en-IN", {
  style: "currency",
  currency: "INR",
  maximumFractionDigits: 2,
});

function fmtCr(n: number | null): string {
  if (n === null) return "—";
  if (n >= 1e12) return `₹${(n / 1e12).toFixed(2)}L Cr`;
  if (n >= 1e7) return `₹${(n / 1e7).toFixed(2)} Cr`;
  return INR.format(n);
}

// ---------------------------------------------------------------------------
// StockDetailPage
// ---------------------------------------------------------------------------

export function StockDetailPage({ symbol }: { symbol: string }): React.ReactElement {
  const [quoteState, setQuoteState] = useState<QuoteState>({ kind: "loading" });
  const [sparkState, setSparkState] = useState<SparkState>({ kind: "loading" });
  const [range, setRange] = useState<SparklineRange>("1M");
  const [sideTab, setSideTab] = useState<SideTab>("fundamentals");
  const [automations, setAutomations] = useState<StockAutomation[]>([]);
  const [news, setNews] = useState<NewsItem[]>([]);
  const [relatedWorkflows, setRelatedWorkflows] = useState<WorkflowSummary[]>([]);

  // Load quote
  useEffect(() => {
    setQuoteState({ kind: "loading" });
    getStockQuote(symbol)
      .then((result) => {
        if (isError(result)) {
          setQuoteState({ kind: "error", message: result.error.message });
        } else {
          setQuoteState({ kind: "ok", quote: result.data });
        }
      })
      .catch((err: unknown) =>
        setQuoteState({
          kind: "error",
          message: err instanceof Error ? err.message : "Network error",
        }),
      );
  }, [symbol]);

  // Load sparkline
  useEffect(() => {
    setSparkState({ kind: "loading" });
    getSparkline(symbol, range)
      .then((result) => {
        if (isError(result)) {
          setSparkState({ kind: "error" });
        } else {
          setSparkState({ kind: "ok", data: result.data });
        }
      })
      .catch(() => setSparkState({ kind: "error" }));
  }, [symbol, range]);

  // Load automations — trigger price levels, past fires, scheduled runs
  useEffect(() => {
    getStockAutomations(symbol)
      .then((result) => {
        if (!isError(result)) {
          setAutomations(result.data.items);
        }
      })
      .catch(() => {
        // Network error — silently ignore for optional overlay
      });
  }, [symbol]);

  // Load news
  useEffect(() => {
    getNews(symbol)
      .then((result) => {
        if (!isError(result)) setNews(result.data.items.slice(0, 10));
      })
      .catch(() => {});
  }, [symbol]);

  // Load related workflows (already wired — filter by symbol in description)
  useEffect(() => {
    listWorkflows({ status: ["active", "paused", "draft"], limit: 50 })
      .then((result) => {
        if (!isError(result)) {
          const upper = symbol.toUpperCase();
          const related = result.data.items.filter(
            (wf) =>
              wf.name.toUpperCase().includes(upper) ||
              (wf.description ?? "").toUpperCase().includes(upper),
          );
          setRelatedWorkflows(related);
        }
      })
      .catch(() => {});
  }, [symbol]);

  return (
    <div className="flex flex-col min-h-screen bg-background">
      {/* Back nav */}
      <div className="sticky top-0 z-20 border-b bg-background/95 px-6 py-2 backdrop-blur">
        <Link
          href="/"
          className="inline-flex items-center gap-1.5 text-xs text-muted-foreground hover:text-foreground"
        >
          <ArrowLeft className="h-3.5 w-3.5" aria-hidden="true" />
          Back
        </Link>
      </div>

      <div className="flex-1 px-6 py-6">
        {/* Header */}
        {quoteState.kind === "loading" && <QuoteHeaderSkeleton />}
        {quoteState.kind === "error" && (
          <div role="alert" className="flex items-center gap-2 text-sm text-destructive">
            <AlertCircle className="h-4 w-4" aria-hidden="true" />
            {quoteState.message}
          </div>
        )}
        {quoteState.kind === "ok" && (
          <QuoteHeader quote={quoteState.quote} />
        )}

        <div className="mt-6 grid grid-cols-1 gap-6 lg:grid-cols-[1fr_320px]">
          {/* Main chart */}
          <div className="flex flex-col gap-4">
            <ChartCard
              symbol={symbol}
              sparkState={sparkState}
              range={range}
              onRangeChange={setRange}
              automations={automations}
            />
          </div>

          {/* Side panel */}
          <div className="flex flex-col gap-4">
            <SidePanel
              sideTab={sideTab}
              onTabChange={setSideTab}
              quote={quoteState.kind === "ok" ? quoteState.quote : null}
              news={news}
              relatedWorkflows={relatedWorkflows}
            />
          </div>
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Quote header
// ---------------------------------------------------------------------------

function QuoteHeader({ quote }: { quote: StockQuote }): React.ReactElement {
  const positive = quote.change_pct >= 0;
  return (
    <div data-testid="quote-header">
      <div className="flex items-start justify-between gap-4 flex-wrap">
        <div>
          <div className="flex items-center gap-2">
            <h1 className="font-serif text-3xl font-bold tracking-tight text-foreground">
              {quote.symbol}
            </h1>
            <Badge variant="muted" className="text-[10px]">
              {quote.exchange}
            </Badge>
          </div>
          <p className="mt-0.5 text-sm text-muted-foreground">
            {quote.name}
            {quote.sector && (
              <span className="ml-2 text-xs text-muted-foreground/70">· {quote.sector}</span>
            )}
          </p>
        </div>
        <div className="text-right">
          <p className="font-serif text-3xl font-bold tabular-nums">
            {INR.format(quote.ltp)}
          </p>
          <div className="mt-0.5 flex items-center justify-end gap-1">
            {positive ? (
              <ArrowUpRight className="h-4 w-4 text-emerald-500" aria-hidden="true" />
            ) : (
              <ArrowDownRight className="h-4 w-4 text-rose-500" aria-hidden="true" />
            )}
            <span
              className={cn(
                "text-sm font-semibold",
                positive ? "text-emerald-600 dark:text-emerald-400" : "text-rose-600 dark:text-rose-400",
              )}
            >
              {positive ? "+" : ""}{INR.format(quote.change)} ({positive ? "+" : ""}{quote.change_pct.toFixed(2)}%)
            </span>
          </div>
        </div>
      </div>

      {/* Stats strip */}
      <div className="mt-4 grid grid-cols-4 gap-3 rounded-xl border bg-card p-4 text-center">
        <StatCell label="OPEN" value={INR.format(quote.open)} />
        <StatCell label="HIGH" value={INR.format(quote.high)} />
        <StatCell label="LOW" value={INR.format(quote.low)} />
        <StatCell label="VOLUME" value={quote.volume.toLocaleString("en-IN")} />
        <StatCell label="52W HIGH" value={INR.format(quote.week_52_high)} />
        <StatCell label="52W LOW" value={INR.format(quote.week_52_low)} />
        <StatCell label="MKT CAP" value={fmtCr(quote.market_cap)} />
        <StatCell label="P/E" value={quote.pe_ratio !== null ? quote.pe_ratio.toFixed(1) : "—"} />
      </div>
    </div>
  );
}

function StatCell({ label, value }: { label: string; value: string }): React.ReactElement {
  return (
    <div>
      <p className="text-[9px] font-semibold uppercase tracking-wider text-muted-foreground">{label}</p>
      <p className="mt-0.5 text-xs font-semibold tabular-nums text-foreground">{value}</p>
    </div>
  );
}

function QuoteHeaderSkeleton(): React.ReactElement {
  return (
    <div className="space-y-3" data-testid="quote-header-skeleton">
      <Skeleton className="h-8 w-32" />
      <Skeleton className="h-4 w-48" />
      <Skeleton className="h-20 w-full rounded-xl" />
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main chart card
// ---------------------------------------------------------------------------

const RANGE_OPTIONS: SparklineRange[] = ["1D", "1W", "1M", "6M", "1Y", "5Y"];

function ChartCard({
  sparkState,
  range,
  onRangeChange,
  automations,
}: {
  symbol: string;
  sparkState: SparkState;
  range: SparklineRange;
  onRangeChange: (r: SparklineRange) => void;
  automations: StockAutomation[];
}): React.ReactElement {
  const priceLevels = automations.filter(
    (a) => a.overlay_type === "trigger_price" && a.price_level !== undefined,
  );

  return (
    <div className="rounded-xl border bg-card p-4 shadow-sm" data-testid="main-chart">
      {/* Range buttons */}
      <div className="mb-4 flex items-center gap-1.5" role="group" aria-label="Time range">
        {RANGE_OPTIONS.map((r) => (
          <button
            key={r}
            type="button"
            onClick={() => onRangeChange(r)}
            aria-pressed={range === r}
            className={cn(
              "rounded-full px-2.5 py-0.5 text-xs font-medium transition-colors",
              range === r
                ? "bg-primary text-primary-foreground"
                : "text-muted-foreground hover:bg-muted",
            )}
            data-testid={`range-${r}`}
          >
            {r}
          </button>
        ))}
      </div>

      {sparkState.kind === "loading" && (
        <Skeleton className="h-48 w-full" data-testid="chart-skeleton" />
      )}
      {sparkState.kind === "error" && (
        <div className="flex h-48 items-center justify-center text-xs text-muted-foreground">
          Chart unavailable
        </div>
      )}
      {sparkState.kind === "ok" && (
        <ResponsiveContainer width="100%" height={220}>
          <LineChart
            data={sparkState.data.points.map((pt) => ({ t: pt.t, v: pt.v }))}
            margin={{ top: 4, right: 8, bottom: 4, left: 0 }}
          >
            <XAxis
              dataKey="t"
              tickFormatter={(d: string) => {
                try { return format(parseISO(d), "MMM d"); } catch { return d; }
              }}
              tick={{ fontSize: 10 }}
              minTickGap={40}
            />
            <YAxis
              domain={["auto", "auto"]}
              tick={{ fontSize: 10 }}
              tickFormatter={(v: number) => INR.format(v)}
              width={72}
            />
            <Tooltip
              formatter={(v: number) => [INR.format(v), "Price"]}
              labelFormatter={(d: string) => {
                try { return format(parseISO(d), "d MMM yyyy"); } catch { return d; }
              }}
              contentStyle={{ fontSize: 11 }}
            />
            <Line
              type="monotone"
              dataKey="v"
              stroke="hsl(var(--primary))"
              strokeWidth={2}
              dot={false}
            />
            {/* Automation overlays — trigger price levels */}
            {priceLevels.map((a) => (
              <ReferenceLine
                key={`${a.workflow_id}-${a.price_level}`}
                y={a.price_level}
                stroke="hsl(var(--warning))"
                strokeDasharray="4 3"
                label={{ value: a.label ?? a.workflow_name, fontSize: 9, fill: "hsl(var(--warning))" }}
              />
            ))}
          </LineChart>
        </ResponsiveContainer>
      )}

      {priceLevels.length > 0 && (
        <p className="mt-2 text-[10px] text-muted-foreground">
          Dashed lines: trigger price levels from your automations.
        </p>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Side panel
// ---------------------------------------------------------------------------

const SIDE_TABS: { key: SideTab; label: string }[] = [
  { key: "fundamentals", label: "Fundamentals" },
  { key: "news", label: "News" },
  { key: "agents", label: "Related Agents" },
];

function SidePanel({
  sideTab,
  onTabChange,
  quote,
  news,
  relatedWorkflows,
}: {
  sideTab: SideTab;
  onTabChange: (t: SideTab) => void;
  quote: StockQuote | null;
  news: NewsItem[];
  relatedWorkflows: WorkflowSummary[];
}): React.ReactElement {
  return (
    <div className="rounded-xl border bg-card shadow-sm overflow-hidden" data-testid="side-panel">
      {/* Tab strip */}
      <div className="flex border-b" role="tablist">
        {SIDE_TABS.map((t) => (
          <button
            key={t.key}
            type="button"
            role="tab"
            aria-selected={sideTab === t.key}
            onClick={() => onTabChange(t.key)}
            className={cn(
              "flex-1 py-2.5 text-[11px] font-semibold uppercase tracking-wide transition-colors",
              sideTab === t.key
                ? "border-b-2 border-primary text-foreground"
                : "text-muted-foreground hover:text-foreground",
            )}
            data-testid={`side-tab-${t.key}`}
          >
            {t.label}
          </button>
        ))}
      </div>

      <div className="p-4">
        {sideTab === "fundamentals" && <FundamentalsPane quote={quote} />}
        {sideTab === "news" && <NewsPane items={news} />}
        {sideTab === "agents" && <RelatedAgentsPane workflows={relatedWorkflows} />}
      </div>
    </div>
  );
}

function FundamentalsPane({ quote }: { quote: StockQuote | null }): React.ReactElement {
  if (!quote) {
    return <p className="text-xs text-muted-foreground">Loading…</p>;
  }
  const items = [
    { label: "P/E Ratio", value: quote.pe_ratio !== null ? quote.pe_ratio.toFixed(1) : "—" },
    { label: "Market Cap", value: fmtCr(quote.market_cap) },
    { label: "52W High", value: INR.format(quote.week_52_high) },
    { label: "52W Low", value: INR.format(quote.week_52_low) },
    { label: "Sector", value: quote.sector ?? "—" },
    { label: "Exchange", value: quote.exchange },
  ];
  return (
    <div className="space-y-2" data-testid="fundamentals-pane">
      {items.map((item) => (
        <div key={item.label} className="flex items-center justify-between">
          <span className="text-[11px] text-muted-foreground">{item.label}</span>
          <span className="text-[11px] font-medium text-foreground">{item.value}</span>
        </div>
      ))}
    </div>
  );
}

function NewsPane({ items }: { items: NewsItem[] }): React.ReactElement {
  if (items.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center py-8 text-center">
        <p className="text-xs text-muted-foreground">
          No news available for this symbol.
        </p>
      </div>
    );
  }
  return (
    <ul className="space-y-3" data-testid="news-pane">
      {items.map((item) => (
        <li key={item.id}>
          <a
            href={item.url}
            target="_blank"
            rel="noopener noreferrer"
            className="group flex items-start gap-2 rounded-lg hover:bg-muted/50 p-1 -mx-1"
          >
            <div className="min-w-0 flex-1">
              <p className="text-[11px] font-medium text-foreground group-hover:text-primary leading-snug">
                {item.title}
              </p>
              <p className="mt-0.5 text-[10px] text-muted-foreground">
                {item.source} · {format(parseISO(item.published_at), "d MMM")}
              </p>
            </div>
            <ExternalLink className="mt-0.5 h-3 w-3 shrink-0 text-muted-foreground" aria-hidden="true" />
          </a>
        </li>
      ))}
    </ul>
  );
}

function RelatedAgentsPane({ workflows }: { workflows: WorkflowSummary[] }): React.ReactElement {
  if (workflows.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center py-8 text-center">
        <p className="text-xs text-muted-foreground">
          No active workflows for this symbol.
        </p>
      </div>
    );
  }
  return (
    <ul className="space-y-2" data-testid="related-agents-pane">
      {workflows.map((wf) => (
        <li key={wf.id}>
          <a
            href={`/#agents`}
            className="flex items-center justify-between rounded-lg border px-3 py-2 hover:bg-muted/40 transition-colors"
          >
            <div className="min-w-0">
              <p className="truncate text-[11px] font-medium text-foreground">{wf.name}</p>
              <p className="text-[10px] text-muted-foreground capitalize">{wf.status}</p>
            </div>
            <ExternalLink className="ml-2 h-3 w-3 shrink-0 text-muted-foreground" aria-hidden="true" />
          </a>
        </li>
      ))}
    </ul>
  );
}
