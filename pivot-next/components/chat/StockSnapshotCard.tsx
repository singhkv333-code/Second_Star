"use client";

/**
 * StockSnapshotCard — inline stock snapshot rendered in chat when the user
 * types a ticker symbol.
 *
 * Data sources:
 *   GET /api/markets/quote/{symbol}     — price, OHLC, 52w, mcap, PE
 *   GET /api/markets/sparkline/{symbol} — area-fill chart with range chips
 *
 * Recommendation pill: derived from change_pct rule-of-thumb.
 * Buy/Sell buttons: open AgentPanel with a prefilled one-step workflow.
 * Watchlist button: POST action.update_watchlist workflow.
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
import { isError } from "@/lib/types";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export type StockSnapshotCardProps = {
  symbol: string;
  exchange?: "NSE" | "BSE";
  /** Called when user clicks Buy or Sell — parent opens AgentPanel with draft. */
  onTrade?: (symbol: string, side: "buy" | "sell") => void;
  /** Called when user clicks Watchlist. */
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
// (Recommendation pill removed.) Showing BUY / HOLD / SELL on a snapshot
// card crosses Pivot's "no advisory" line — the user asked for a quote,
// not a recommendation. Per PDF report, the pill was misleading and
// based on intraday change_pct only. The card now leads with the
// company name + ticker + sector, and lets price + chart speak for
// themselves.
// ---------------------------------------------------------------------------

// ---------------------------------------------------------------------------
// INR formatter
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

  // Default actions when the parent didn't pass callbacks. Buy/Sell
  // both route to the stock detail page (which already has order
  // entry); Watchlist nudges to the same page until a dedicated
  // endpoint exists. Previously these buttons were dead clicks when
  // the card was rendered from chat (PDF report: "buttons on the
  // widgets don't do anything").
  //
  // We use window.location instead of next/navigation's useRouter()
  // because the card is rendered both inside the Next App Router (so
  // a router IS mounted) and inside testing-library tests that mount
  // the component bare. Calling useRouter() unconditionally throws
  // "invariant expected app router to be mounted" in the test env;
  // window.location works in both.
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
        className="flex w-full max-w-lg items-center justify-center rounded-xl border bg-card p-8"
        data-testid="stock-snapshot-loading"
        aria-label="Loading stock snapshot"
      >
        <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" aria-hidden={true} />
      </div>
    );
  }

  if (quoteState.kind === "error") {
    return (
      <div
        className="flex w-full max-w-lg items-center gap-2 rounded-xl border bg-card px-4 py-3"
        role="alert"
        data-testid="stock-snapshot-error"
      >
        <AlertCircle className="h-4 w-4 shrink-0 text-destructive" aria-hidden={true} />
        <p className="text-xs text-destructive">{quoteState.message}</p>
      </div>
    );
  }

  const { quote } = quoteState;
  const positive = quote.change >= 0;
  // Period-relative direction for the chart color: green if the
  // selected range CLOSED higher than it opened, red otherwise. The
  // header ▲/▼ pill stays driven by today's change. Without this,
  // a stock that's up over 5Y but down today rendered the chart in
  // red — confusing for users looking at the long-term shape (PDF
  // report: "graph should be green for growth according to the
  // timeline").
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
      className="w-full max-w-sm rounded-xl border bg-card shadow-sm overflow-hidden"
      data-testid="stock-snapshot-card"
    >
      {/* Header */}
      <div className="flex items-start justify-between gap-3 px-4 pt-4">
        <div>
          <div className="flex items-center gap-2">
            <span className="rounded-md bg-muted px-1.5 py-0.5 font-mono text-[11px] font-semibold text-foreground">
              {quote.symbol}
            </span>
          </div>
          <h3 className="mt-1 font-serif text-base font-semibold text-foreground">
            {quote.name || quote.symbol}
          </h3>
          <p className="text-[11px] text-muted-foreground">
            {quote.symbol} · {quote.exchange} · {quote.sector ?? "EQUITY"}
          </p>
        </div>

        {/* Price */}
        <div className="text-right shrink-0">
          <p className="font-serif text-xl font-semibold tabular-nums text-foreground">
            {fmtINR(quote.ltp)}
          </p>
          <div className="flex items-center justify-end gap-1 mt-0.5">
            {positive ? (
              <TrendingUp className="h-3.5 w-3.5 text-emerald-500" aria-hidden={true} />
            ) : (
              <TrendingDown className="h-3.5 w-3.5 text-rose-500" aria-hidden={true} />
            )}
            <span
              className={cn(
                "text-xs font-medium tabular-nums",
                positive ? "text-emerald-600 dark:text-emerald-400" : "text-rose-600 dark:text-rose-400",
              )}
            >
              {positive ? "+" : ""}{fmtINR(quote.change)} ({positive ? "+" : ""}{fmtNum(quote.change_pct)}%)
            </span>
          </div>
          <p className="text-[10px] text-muted-foreground mt-0.5">Today · {timeStr} IST</p>
        </div>
      </div>

      {/* Sparkline */}
      <div className="px-4 pt-3">
        {/* Range chips */}
        <div className="flex items-center gap-1 mb-2">
          {RANGES.map((r) => (
            <button
              key={r}
              type="button"
              onClick={() => setRange(r)}
              className={cn(
                "rounded-md px-2 py-0.5 text-[10px] font-medium transition-colors",
                r === range
                  ? "bg-primary text-primary-foreground"
                  : "text-muted-foreground hover:bg-muted hover:text-foreground",
              )}
              aria-pressed={r === range}
              data-testid={`range-${r}`}
            >
              {r}
            </button>
          ))}
        </div>

        {/* Chart area */}
        <div className="h-24">
          {sparkState.kind === "loading" && (
            <div className="flex h-full items-center justify-center">
              <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" aria-hidden={true} />
            </div>
          )}
          {sparkState.kind === "ok" && sparkState.points.length > 0 && (
            <SparkAreaChart
              points={sparkState.points}
              positive={periodPositive}
            />
          )}
          {(sparkState.kind === "hidden" || (sparkState.kind === "ok" && sparkState.points.length === 0)) && (
            <div className="flex h-full items-center justify-center">
              <Minus className="h-4 w-4 text-muted-foreground/40" aria-hidden={true} />
            </div>
          )}
        </div>
      </div>

      {/* Stat grid */}
      <div className="grid grid-cols-4 gap-px border-y bg-border mx-4 my-3 rounded-lg overflow-hidden">
        <StatCell label="OPEN" value={fmtINR(quote.open)} />
        <StatCell label="DAY HIGH" value={fmtINR(quote.high)} />
        <StatCell label="DAY LOW" value={fmtINR(quote.low)} />
        <StatCell label="VOLUME" value={fmtNum(quote.volume, 0)} />
        <StatCell label="52W HIGH" value={fmtINR(quote.week_52_high)} />
        <StatCell label="52W LOW" value={fmtINR(quote.week_52_low)} />
        <StatCell label="MKT CAP" value={fmtLarge(quote.market_cap)} />
        <StatCell label="P/E" value={quote.pe_ratio != null ? fmtNum(quote.pe_ratio, 1) : "—"} />
      </div>

      {/* Action buttons */}
      <div className="flex items-center gap-2 px-4 pb-4">
        <Button
          size="sm"
          className="flex-1 h-8 rounded-full bg-emerald-600 hover:bg-emerald-700 text-white text-xs"
          onClick={() => handleTrade(symbol, "buy")}
          data-testid="buy-btn"
        >
          Buy
        </Button>
        <Button
          size="sm"
          className="flex-1 h-8 rounded-full bg-rose-600 hover:bg-rose-700 text-white text-xs"
          onClick={() => handleTrade(symbol, "sell")}
          data-testid="sell-btn"
        >
          Sell
        </Button>
        <Button
          variant="outline"
          size="sm"
          className="h-8 rounded-full px-3 text-xs"
          onClick={() => handleWatchlist(symbol)}
          data-testid="watchlist-btn"
          aria-label="Add to watchlist"
        >
          <BookmarkPlus className="h-3.5 w-3.5 mr-1" aria-hidden={true} />
          Watchlist
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
  const H = 80;
  const PADDING_X = 2;
  const PADDING_Y = 4;

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

  const color = positive ? "#10b981" : "#f43f5e"; // emerald-500 / rose-500

  return (
    <svg
      viewBox={`0 0 ${W} ${H}`}
      className="h-full w-full"
      aria-hidden={true}
      preserveAspectRatio="none"
    >
      <defs>
        <linearGradient id={`spark-grad-${positive ? "up" : "dn"}`} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor={color} stopOpacity={0.25} />
          <stop offset="100%" stopColor={color} stopOpacity={0.02} />
        </linearGradient>
      </defs>
      <polygon
        points={areaPoints}
        fill={`url(#spark-grad-${positive ? "up" : "dn"})`}
        className="dark:opacity-70"
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

function StatCell({ label, value }: { label: string; value: string }): React.ReactElement {
  return (
    <div className="flex flex-col gap-0.5 bg-background px-2.5 py-2">
      <span className="text-[9px] font-semibold uppercase tracking-wider text-muted-foreground">
        {label}
      </span>
      <span className="text-[11px] font-medium tabular-nums text-foreground">{value}</span>
    </div>
  );
}
