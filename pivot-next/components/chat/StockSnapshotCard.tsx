"use client";

/**
 * StockSnapshotCard — inline stock snapshot rendered in chat when the user
 * types a ticker symbol.
 *
 * Data sources:
 *   GET /api/markets/quote/{symbol}     — price, OHLC, 52w, mcap, PE
 *   GET /api/markets/sparkline/{symbol} — area-fill chart with range chips
 */

import { useEffect, useState } from "react";
import {
  AlertCircle,
  BookmarkPlus,
  Loader2,
  Minus,
  TrendingDown,
  TrendingUp,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { getSparkline, getStockQuote, type SparklineRange, type StockQuote } from "@/lib/api";
import { CandlestickChart } from "@/components/chart/CandlestickChart";
import { isError } from "@/lib/types";
import { useLiveQuote } from "@/hooks/useLiveQuote";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export type StockSnapshotCardProps = {
  symbol: string;
  exchange?: "NSE" | "BSE";
  onTrade?: (symbol: string, side: "buy" | "sell") => void;
  onWatchlist?: (symbol: string) => void;
};

type QuoteState =
  | { kind: "loading" }
  | { kind: "ok"; quote: StockQuote }
  | { kind: "error"; message: string };

type SparklineState =
  | { kind: "loading" }
  | { kind: "ok"; points: { t: string; v: number }[] }
  | { kind: "hidden" };

const RANGES: SparklineRange[] = ["1D", "1W", "1M", "6M", "1Y", "5Y"];

// ---------------------------------------------------------------------------
// Formatters
// ---------------------------------------------------------------------------

function fmtINR(n: number | null | undefined): string {
  if (n == null) return "—";
  return new Intl.NumberFormat("en-IN", {
    style: "currency",
    currency: "INR",
    maximumFractionDigits: 2,
  }).format(n);
}

function fmtNum(n: number | null | undefined, digits = 2): string {
  if (n == null) return "—";
  return new Intl.NumberFormat("en-IN", {
    maximumFractionDigits: digits,
    minimumFractionDigits: digits,
  }).format(n);
}

function fmtLarge(n: number | null | undefined): string {
  if (n == null) return "—";
  if (n >= 1e12) return `₹${(n / 1e12).toFixed(2)}T`;
  if (n >= 1e9) return `₹${(n / 1e9).toFixed(2)}B`;
  if (n >= 1e7) return `₹${(n / 1e7).toFixed(2)}Cr`;
  return fmtINR(n);
}

// ---------------------------------------------------------------------------
// StockSnapshotCard
// ---------------------------------------------------------------------------

export function StockSnapshotCard({
  symbol,
  exchange = "NSE",
  onTrade,
  onWatchlist,
}: StockSnapshotCardProps): React.ReactElement {
  const [quoteState, setQuoteState] = useState<QuoteState>({ kind: "loading" });
  const [sparkState, setSparkState] = useState<SparklineState>({ kind: "loading" });
  const [range, setRange] = useState<SparklineRange>("1Y");
  const [chartMode, setChartMode] = useState<"line" | "candles">("line");

  // Phase 2: WS live price overlay. Called unconditionally (Rules of Hooks).
  const liveData = useLiveQuote(symbol);

  // window.location instead of useRouter so the card mounts cleanly under
  // testing-library (no app-router context).
  const navigate = (path: string): void => {
    if (typeof window !== "undefined") {
      window.location.assign(path);
    }
  };
  const handleTrade = (sym: string, side: "buy" | "sell"): void => {
    if (onTrade) {
      onTrade(sym, side);
      return;
    }
    navigate(`/stock/${encodeURIComponent(sym)}?action=${side}`);
  };
  const handleWatchlist = (sym: string): void => {
    if (onWatchlist) {
      onWatchlist(sym);
      return;
    }
    navigate(`/stock/${encodeURIComponent(sym)}?watchlist=1`);
  };

  useEffect(() => {
    setQuoteState({ kind: "loading" });
    getStockQuote(symbol, exchange)
      .then((result) => {
        if (isError(result)) {
          setQuoteState({ kind: "error", message: result.error.message });
        } else {
          setQuoteState({ kind: "ok", quote: result.data });
        }
      })
      .catch((err: unknown) => {
        setQuoteState({ kind: "error", message: err instanceof Error ? err.message : "Network error" });
      });
  }, [symbol, exchange]);

  useEffect(() => {
    setSparkState({ kind: "loading" });
    getSparkline(symbol, range)
      .then((result) => {
        if (isError(result)) {
          setSparkState({ kind: "hidden" });
        } else {
          setSparkState({ kind: "ok", points: result.data.points });
        }
      })
      .catch(() => setSparkState({ kind: "hidden" }));
  }, [symbol, range]);

  if (quoteState.kind === "loading") {
    return (
      <div
        className="flex w-full max-w-md items-center justify-center rounded-[14px] border border-border/70 bg-card p-10"
        data-testid="stock-snapshot-loading"
        aria-label="Loading stock snapshot"
      >
        <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" aria-hidden={true} />
      </div>
    );
  }

  if (quoteState.kind === "error") {
    return (
      <div
        className="flex w-full max-w-md items-center gap-2 rounded-[14px] border border-border/70 bg-card px-4 py-3"
        role="alert"
        data-testid="stock-snapshot-error"
      >
        <AlertCircle className="h-4 w-4 shrink-0 text-destructive" aria-hidden={true} />
        <p className="text-xs text-destructive">{quoteState.message}</p>
      </div>
    );
  }

  const { quote } = quoteState;
  const displayLtp = liveData.ltp ?? quote.ltp;
  const displayIsLive = liveData.isLive || quote.live === true;

  const positive = quote.change >= 0;
  // Color the chart by the selected period's direction, not today's tick —
  // a 5Y up trend that's red on the day shouldn't render the chart red.
  let periodPositive = positive;
  if (sparkState.kind === "ok" && sparkState.points.length >= 2) {
    const first = sparkState.points[0]?.v;
    const last = sparkState.points[sparkState.points.length - 1]?.v;
    if (typeof first === "number" && typeof last === "number") {
      periodPositive = last >= first;
    }
  }
  const timeStr = new Date().toLocaleTimeString("en-IN", {
    hour: "2-digit",
    minute: "2-digit",
    timeZone: "Asia/Kolkata",
    hour12: false,
  });

  return (
    <div
      className="w-full max-w-md overflow-hidden rounded-[14px] border border-border/70 bg-card"
      data-testid="stock-snapshot-card"
    >
      {/* Header — eyebrow chip + serif company name + price block */}
      <div className="flex items-start justify-between gap-4 px-5 pt-4 pb-3">
        <div className="min-w-0">
          <div className="flex items-center gap-1.5">
            <span className="q-uppercase-label !text-[10px]">
              {quote.exchange} · {quote.sector ?? "Equity"}
            </span>
          </div>
          <h3 className="mt-1.5 truncate text-[17px] leading-tight font-semibold tracking-tight text-foreground">
            {quote.name || quote.symbol}
          </h3>
          <p className="mt-0.5 font-mono text-[10.5px] uppercase tracking-wider text-muted-foreground">
            {quote.symbol}
          </p>
        </div>

        <div className="text-right shrink-0">
          <p className="text-[20px] leading-none font-semibold tabular-nums text-foreground tracking-tight">
            {fmtINR(displayLtp)}
          </p>
          <div className="mt-1.5 flex items-center justify-end gap-1">
            {positive ? (
              <TrendingUp className="h-3.5 w-3.5 text-emerald-600 dark:text-emerald-400" aria-hidden={true} />
            ) : (
              <TrendingDown className="h-3.5 w-3.5 text-rose-600 dark:text-rose-400" aria-hidden={true} />
            )}
            <span
              className={cn(
                "text-[11.5px] font-medium tabular-nums",
                positive ? "text-emerald-600 dark:text-emerald-400" : "text-rose-600 dark:text-rose-400",
              )}
            >
              {positive ? "+" : ""}{fmtINR(quote.change)} ({positive ? "+" : ""}{fmtNum(quote.change_pct)}%)
            </span>
          </div>
          <p className="mt-0.5 text-[10px] text-muted-foreground/80">{timeStr} IST</p>
          {/* Phase 2 — live/delayed source badge */}
          {displayIsLive ? (
            <div
              className="mt-1 inline-flex items-center gap-1 text-[9.5px] font-medium text-emerald-600 dark:text-emerald-400"
              data-testid="live-badge"
            >
              <span className="h-1.5 w-1.5 rounded-full bg-emerald-500" aria-hidden={true} />
              Live
            </div>
          ) : (
            <div
              className="mt-1 text-[9.5px] font-medium text-muted-foreground/70"
              data-testid="delayed-badge"
            >
              Delayed
            </div>
          )}
        </div>
      </div>

      {/* Price chart — area sparkline or full candlesticks */}
      <div className="px-5">
        <div className="mb-1 flex items-center justify-end">
          <div className="inline-flex overflow-hidden rounded-md border border-border/60">
            {(["line", "candles"] as const).map((m) => (
              <button
                key={m}
                type="button"
                onClick={() => setChartMode(m)}
                className={cn(
                  "px-2 py-0.5 text-[9.5px] font-semibold uppercase tracking-wide transition-colors",
                  chartMode === m
                    ? "bg-foreground text-background"
                    : "text-muted-foreground hover:text-foreground",
                )}
                aria-pressed={chartMode === m}
                data-testid={`chartmode-${m}`}
              >
                {m === "line" ? "Line" : "Candles"}
              </button>
            ))}
          </div>
        </div>

        {chartMode === "candles" ? (
          <div className="pb-3">
            <CandlestickChart
              symbol={symbol}
              exchange={quote.exchange === "BSE" ? "BSE" : "NSE"}
              initialRange={range === "1D" || range === "1W" ? "1M" : range}
              height={220}
            />
          </div>
        ) : (
          <>
            <div className="h-[88px]">
              {sparkState.kind === "loading" && (
                <div className="flex h-full items-center justify-center">
                  <Loader2 className="h-4 w-4 animate-spin text-muted-foreground/60" aria-hidden={true} />
                </div>
              )}
              {sparkState.kind === "ok" && sparkState.points.length > 0 && (
                <SparkAreaChart points={sparkState.points} positive={periodPositive} />
              )}
              {(sparkState.kind === "hidden" || (sparkState.kind === "ok" && sparkState.points.length === 0)) && (
                <div className="flex h-full items-center justify-center">
                  <Minus className="h-4 w-4 text-muted-foreground/30" aria-hidden={true} />
                </div>
              )}
            </div>

            {/* Range chips — flat, segmented */}
            <div className="mt-2 flex items-center justify-between gap-1 pb-3">
              {RANGES.map((r) => (
                <button
                  key={r}
                  type="button"
                  onClick={() => setRange(r)}
                  className={cn(
                    "flex-1 rounded-md py-1 text-[10.5px] font-medium tracking-wide transition-colors",
                    r === range
                      ? "bg-foreground text-background"
                      : "text-muted-foreground hover:text-foreground",
                  )}
                  aria-pressed={r === range}
                  data-testid={`range-${r}`}
                >
                  {r}
                </button>
              ))}
            </div>
          </>
        )}
      </div>

      {/* Stat grid — hairline dividers, no fills */}
      <div className="grid grid-cols-4 border-t border-border/60">
        <StatCell label="Open" value={fmtINR(quote.open)} />
        <StatCell label="High" value={fmtINR(quote.high)} />
        <StatCell label="Low" value={fmtINR(quote.low)} />
        <StatCell label="Volume" value={fmtNum(quote.volume, 0)} last />
      </div>
      <div className="grid grid-cols-4 border-t border-border/60">
        <StatCell label="52w high" value={fmtINR(quote.week_52_high)} />
        <StatCell label="52w low" value={fmtINR(quote.week_52_low)} />
        <StatCell label="Mkt cap" value={fmtLarge(quote.market_cap)} />
        <StatCell label="P/E" value={quote.pe_ratio != null ? fmtNum(quote.pe_ratio, 1) : "—"} last />
      </div>

      {/* Action bar */}
      <div className="flex items-center gap-1.5 border-t border-border/60 px-3 py-3">
        <Button
          size="sm"
          className="h-8 flex-1 rounded-full bg-primary text-primary-foreground text-[12px] font-medium hover:bg-primary/90"
          onClick={() => handleTrade(symbol, "buy")}
          data-testid="buy-btn"
        >
          Buy
        </Button>
        <Button
          size="sm"
          variant="outline"
          className="h-8 flex-1 rounded-full text-[12px] font-medium border-border/70"
          onClick={() => handleTrade(symbol, "sell")}
          data-testid="sell-btn"
        >
          Sell
        </Button>
        <Button
          variant="ghost"
          size="sm"
          className="h-8 rounded-full px-3 text-[12px] font-medium text-muted-foreground hover:text-foreground"
          onClick={() => handleWatchlist(symbol)}
          data-testid="watchlist-btn"
          aria-label="Add to watchlist"
        >
          <BookmarkPlus className="h-3.5 w-3.5" aria-hidden={true} />
        </Button>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Sparkline area-fill SVG chart
// ---------------------------------------------------------------------------

function SparkAreaChart({
  points,
  positive,
}: {
  points: { t: string; v: number }[];
  positive: boolean;
}): React.ReactElement {
  const values = points.map((p) => p.v);
  const min = Math.min(...values);
  const max = Math.max(...values);
  const range = max - min || 1;

  const W = 400;
  const H = 88;
  const PADDING_X = 2;
  const PADDING_Y = 6;

  const normalize = (v: number): number =>
    H - PADDING_Y - ((v - min) / range) * (H - PADDING_Y * 2);

  const xs = points.map((_, i) => PADDING_X + (i / (points.length - 1)) * (W - PADDING_X * 2));
  const ys = values.map(normalize);

  const linePoints = xs.map((x, i) => `${x},${ys[i]}`).join(" ");
  const areaPoints = [
    `${xs[0]},${H}`,
    ...xs.map((x, i) => `${x},${ys[i]}`),
    `${xs[xs.length - 1]},${H}`,
  ].join(" ");

  const color = positive ? "#10b981" : "#f43f5e";

  return (
    <svg
      viewBox={`0 0 ${W} ${H}`}
      className="h-full w-full"
      aria-hidden={true}
      preserveAspectRatio="none"
    >
      <defs>
        <linearGradient id={`spark-grad-${positive ? "up" : "dn"}`} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor={color} stopOpacity={0.18} />
          <stop offset="100%" stopColor={color} stopOpacity={0} />
        </linearGradient>
      </defs>
      <polygon
        points={areaPoints}
        fill={`url(#spark-grad-${positive ? "up" : "dn"})`}
      />
      <polyline
        points={linePoints}
        fill="none"
        stroke={color}
        strokeWidth={1.5}
        strokeLinejoin="round"
        strokeLinecap="round"
      />
    </svg>
  );
}

// ---------------------------------------------------------------------------
// Stat cell
// ---------------------------------------------------------------------------

function StatCell({
  label,
  value,
  last,
}: {
  label: string;
  value: string;
  last?: boolean;
}): React.ReactElement {
  return (
    <div
      className={cn(
        "flex flex-col gap-0.5 px-3 py-2.5",
        !last && "border-r border-border/60",
      )}
    >
      <span className="text-[9.5px] font-medium uppercase tracking-wider text-muted-foreground">
        {label}
      </span>
      <span className="text-[12px] font-medium tabular-nums text-foreground">
        {value}
      </span>
    </div>
  );
}
