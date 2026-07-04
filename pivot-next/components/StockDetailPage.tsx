"use client";

/**
 * StockDetailPage — Fiscal.ai-inspired individual stock surface.
 *
 * Route: /stock/[symbol]
 *
 * Layout (two-column at xl+, stacked below):
 *   ┌──────────────── header strip ────────────────┐
 *   │ brand glyph + Company Name + bookmark        │
 *   │   exchange:symbol · price · day chip         │
 *   ├──────────────────────┬───────────────────────┤
 *   │ Company Overview     │ Comparison search     │
 *   │   description        │ + range buttons       │
 *   │   Name / CEO / Sector│ + multi-line chart    │
 *   │   Year Founded / etc │                       │
 *   │                      │ Date range summary    │
 *   │ Company Statistics   │ Powered by Pivot      │
 *   │   Profile · Valuation│                       │
 *   │   · Growth grids     │                       │
 *   └──────────────────────┴───────────────────────┘
 *
 * Comparison: the search bar above the chart is multi-select. Picking a
 * peer adds it as a coloured chip and overlays its sparkline on the
 * same axis (normalised so all tickers start at 100). Removable via the
 * × inside each chip. The original symbol is always present.
 *
 * No tab strip below the company name (overview/financials/etc) — that
 * row from the reference is intentionally cut.
 */

import { useEffect, useMemo, useRef, useState } from "react";
import {
  Area,
  ComposedChart,
  Line,
  ReferenceArea,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { format, parseISO } from "date-fns";
import {
  AlertCircle,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  Maximize2,
  Minimize2,
  Search,
  X,
} from "lucide-react";
import { Skeleton } from "@/components/ui/skeleton";
import {
  getStockQuote,
  getSparkline,
  getFinancials,
  getMetricSeries,
  type StockQuote,
  type SparklineRange,
  type SparklineResponse,
  type FinancialsResponse,
  type FinancialsHistoryPoint,
  type MetricSeriesResponse,
} from "@/lib/api";
import { isError, type ApiResult } from "@/lib/types";
import { useLiveQuote } from "@/hooks/useLiveQuote";
import { WatchlistBookmark } from "@/components/WatchlistBookmark";
import { CompanyAutosuggest } from "@/components/CompanyAutosuggest";
import { CompanyLogo } from "@/components/CompanyLogo";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

type QuoteState =
  | { kind: "loading" }
  | { kind: "error"; message: string }
  | { kind: "ok"; quote: StockQuote };

type SeriesEntry = {
  symbol: string;
  state:
    | { kind: "loading" }
    | { kind: "error" }
    | { kind: "ok"; data: SparklineResponse };
};

const RANGE_OPTIONS: SparklineRange[] = ["1D", "1W", "1M", "6M", "1Y", "5Y"];

// ---------------------------------------------------------------------------
// Sparkline cache + adjacent-range prefetch
//
// getSparkline has no client-side cache today, so every range-button click
// (including re-clicking a range already viewed this session) pays a fresh
// ~1.2s network round trip. `sparklineCache` is a module-level map keyed by
// "symbol|range" that persists for the life of the tab — a repeat click is
// then instant. Only successful responses are cached; errors are never
// cached so a later click still retries against the network.
// ---------------------------------------------------------------------------

const sparklineCache = new Map<string, SparklineResponse>();

function sparklineCacheKey(symbol: string, range: SparklineRange): string {
  return `${symbol}|${range}`;
}

function getSparklineCached(
  symbol: string,
  range: SparklineRange,
): Promise<ApiResult<SparklineResponse>> {
  const key = sparklineCacheKey(symbol, range);
  const hit = sparklineCache.get(key);
  if (hit) return Promise.resolve({ data: hit });
  return getSparkline(symbol, range).then((res) => {
    if (!isError(res)) sparklineCache.set(key, res.data);
    return res;
  });
}

/** The 1-2 ranges a user most often clicks next, per current range — used to
 *  warm the cache in the background once the active range finishes loading.
 *  Deliberately NOT "every other range": keep the prefetch lightweight. */
const ADJACENT_RANGES: Record<SparklineRange, SparklineRange[]> = {
  "1D": ["1W"],
  "1W": ["1M", "1D"],
  "1M": ["1W", "6M"],
  "6M": ["1Y", "1M"],
  "1Y": ["6M", "5Y"],
  "5Y": ["1Y", "1M"],
};

/** Fire-and-forget background warm-up of the cache for likely-next ranges.
 *  Never touches component state and swallows failures — this is a pure
 *  optimization, not a user-facing fetch. */
function prefetchAdjacentRanges(tickers: string[], range: SparklineRange): void {
  for (const adjacent of ADJACENT_RANGES[range] ?? []) {
    for (const sym of tickers) {
      void getSparklineCached(sym, adjacent).catch(() => {});
    }
  }
}

/** Distinct, color-blind-friendly palette for comparison series. The
 *  base ticker uses --color-profit (green); peers cycle through the
 *  app accent set so this view stays uniform with the asset-allocation
 *  donut (PortfolioTab.PALETTE). Reds are intentionally omitted —
 *  loss-coding is reserved for actual losses. */
const COMPARE_PALETTE = [
  "var(--color-profit)", // primary ticker = profit-green
  "#1b7cc7", // cobalt blue
  "#fb8500", // vivid orange
  "#219ebc", // cyan teal
  "#ffb703", // golden yellow
  "#2c666e", // dark teal
];

// ---------------------------------------------------------------------------
// Formatters
// ---------------------------------------------------------------------------

const INR = new Intl.NumberFormat("en-IN", {
  style: "currency",
  currency: "INR",
  maximumFractionDigits: 2,
});

/** INR.format guarded against null/NaN — the quotes API returns NaN for
 *  fields a source doesn't carry (52-week, prev close), which otherwise
 *  render as "₹NaN". */
function inrOrDash(n: number | null | undefined): string {
  return n != null && Number.isFinite(n) ? INR.format(n) : "—";
}

function fmtCr(n: number | null): string {
  if (n === null || n === undefined || Number.isNaN(n)) return "—";
  // `n` is in rupees. Indian scale: 1 Cr = 1e7, 1 thousand-Cr = 1e10,
  // 1 lakh-Cr = 1e12. (The K-Cr branch previously used 1e9 — a 10×
  // overstatement that made e.g. a ₹48,057 Cr net profit render as
  // "₹480.57 K Cr" instead of "₹4.81 K Cr".) Work on |n| so losses
  // and negative cash-flows format with a leading minus instead of
  // falling through to the raw-rupee INR path.
  const sign = n < 0 ? "−" : "";
  const abs = Math.abs(n);
  if (abs >= 1e12) return `${sign}₹${(abs / 1e12).toFixed(2)} L Cr`;
  if (abs >= 1e10) return `${sign}₹${(abs / 1e10).toFixed(2)} K Cr`;
  if (abs >= 1e7) return `${sign}₹${(abs / 1e7).toFixed(2)} Cr`;
  return INR.format(n);
}

/** Format a value that is ALREADY in ₹ Crore (unlike fmtCr which expects raw rupees).
 *  Used for Y-axis ticks and tooltip rows for metric=market_cap / metric=sales_margin.
 *  Feeding a ₹-Cr value into fmtCr() would be off by 1e7. */
function fmtCrAxis(v: number): string {
  if (!Number.isFinite(v)) return "—";
  const abs = Math.abs(v);
  const sign = v < 0 ? "-" : "";
  if (abs >= 1e5) return `${sign}₹${(abs / 1e5).toFixed(2)} L Cr`;
  return `${sign}₹${abs.toFixed(0)} Cr`;
}

function fmtPct(n: number, signed = true): string {
  const s = signed ? (n >= 0 ? "+" : "") : "";
  return `${s}${n.toFixed(2)}%`;
}

function fmtDelta(n: number): string {
  const s = n >= 0 ? "+" : "−";
  return `${s}${INR.format(Math.abs(n))}`;
}

/** First-letter brand glyph. Uses sector-derived hue so the page
 *  still feels like a stock-specific surface. */
function brandGlyphHue(sector: string | null): string {
  if (!sector) return "var(--text-secondary)";
  const s = sector.toLowerCase();
  if (s.includes("bank") || s.includes("financ")) return "#60a5fa";
  if (s.includes("tech") || s.includes("it") || s.includes("software")) return "#a78bfa";
  if (s.includes("energy") || s.includes("oil")) return "#f97316";
  if (s.includes("pharma") || s.includes("health")) return "var(--color-profit)";
  if (s.includes("auto")) return "#facc15";
  return "var(--text-secondary)";
}

// ---------------------------------------------------------------------------
// GrowwTooltip — Recharts custom tooltip matching the Groww look:
// soft floating card with the ticker value(s) tabular-numed, a faint
// hairline border, and the date as a sub-row.
// ---------------------------------------------------------------------------

// Metric type is declared here (before GrowwTooltip) so the tooltip can
// receive it as a prop. The METRIC_OPTIONS const in ChartCard references
// this same type.
type Metric = "Price" | "PE Ratio" | "Sales and Margin" | "Market Cap";

type TooltipEntry = {
  dataKey?: string | number;
  name?: string | number;
  value?: number;
  color?: string;
  /** Full data row for the hovered point — needed to read __margin for Sales and Margin. */
  payload?: Record<string, unknown>;
};

function GrowwTooltip({
  active,
  payload,
  label,
  metric,
  rawPriceByDate,
}: {
  active?: boolean;
  payload?: TooltipEntry[];
  label?: string;
  /** Active metric — drives value formatting (price/PE/market-cap/sales-margin). */
  metric: Metric;
  /** Raw (un-normalised) price lookup: symbol → date-string → real price.
   *  Used in Price mode so the tooltip shows the actual ₹ value, not the
   *  100-base-indexed chart value. */
  rawPriceByDate?: Map<string, Map<string, number>>;
}): React.ReactElement | null {
  // Drop the synthetic "__selValue" series — it only exists to paint the
  // drag-selection shaded Area and must not show as a tooltip row.
  // Also drop the __margin shadow keys (written into metricChartData rows for
  // Sales and Margin; they're read via entry.payload below, not as own series).
  const rows = (payload ?? []).filter(
    (e) => e.dataKey !== "__selValue" && !String(e.dataKey ?? "").endsWith("__margin"),
  );
  if (!active || rows.length === 0) return null;
  let dateLabel = label ?? "";
  try {
    dateLabel = format(parseISO(label as string), "d MMM yyyy");
  } catch {
    // keep raw label
  }
  return (
    <div
      role="status"
      aria-live="polite"
      style={{
        background: "var(--bg-primary)",
        border: "1px solid var(--glass-border)",
        borderRadius: 8,
        boxShadow: "0 4px 12px rgba(0,0,0,0.08)",
        padding: "8px 10px",
        fontFamily: "var(--font-ui)",
        fontSize: 11,
        minWidth: 100,
      }}
    >
      <div style={{ color: "var(--text-tertiary)", fontSize: 10, marginBottom: 4 }}>
        {dateLabel}
      </div>
      {rows.map((entry, i) => {
        const v = Number(entry.value);
        let formatted: string;
        if (Number.isNaN(v)) {
          formatted = String(entry.value ?? "");
        } else if (metric === "Price") {
          // Look up the real (un-normalised) price — the plotted value is
          // a 100-base index so showing it raw would be wrong.
          const raw = rawPriceByDate?.get(String(entry.dataKey))?.get(String(label));
          formatted = raw !== undefined ? `₹${raw.toFixed(2)}` : `₹${v.toFixed(1)}`;
        } else if (metric === "PE Ratio") {
          formatted = `${v.toFixed(2)}x`;
        } else if (metric === "Market Cap") {
          formatted = fmtCrAxis(v);
        } else {
          // "Sales and Margin"
          const rev = fmtCrAxis(v);
          const margin = (entry.payload as Record<string, unknown> | undefined)?.[
            `${String(entry.dataKey)}__margin`
          ];
          formatted =
            typeof margin === "number" && Number.isFinite(margin)
              ? `${rev} · ${margin.toFixed(1)}% margin`
              : rev;
        }
        return (
          <div
            key={i}
            style={{
              display: "flex",
              alignItems: "center",
              gap: 6,
              fontVariantNumeric: "tabular-nums",
            }}
          >
            <span
              aria-hidden="true"
              style={{
                width: 7,
                height: 7,
                borderRadius: "50%",
                background: entry.color ?? "var(--text-secondary)",
                flexShrink: 0,
              }}
            />
            <span style={{ color: "var(--text-tertiary)", fontSize: 10 }}>
              {entry.name}
            </span>
            <span
              style={{
                marginLeft: "auto",
                color: "var(--text-primary)",
                fontWeight: 600,
              }}
            >
              {formatted}
            </span>
          </div>
        );
      })}
    </div>
  );
}

// ---------------------------------------------------------------------------
// StockDetailPage
// ---------------------------------------------------------------------------

export function StockDetailPage({ symbol }: { symbol: string }): React.ReactElement {
  const [quoteState, setQuoteState] = useState<QuoteState>({ kind: "loading" });
  const [range, setRange] = useState<SparklineRange>("5Y");
  const [financials, setFinancials] = useState<FinancialsResponse | null>(null);
  // Phone reflows the page: chart on top, then Performance, then a 2-way
  // Overview/Financials switch (desktop keeps the two-column overview+chart row).
  const [isPhone, setIsPhone] = useState(false);
  useEffect(() => {
    const mq = window.matchMedia("(max-width: 639.98px)");
    const sync = (): void => setIsPhone(mq.matches);
    sync();
    mq.addEventListener("change", sync);
    return () => mq.removeEventListener("change", sync);
  }, []);

  // Phase 2: live price overlay via WS (falls back to REST if WS is down).
  const liveQuote = useLiveQuote(symbol);

  // Comparison roster: the page's primary ticker is always at index 0.
  // Peers are appended via the search bar.
  const [tickers, setTickers] = useState<string[]>([symbol.toUpperCase()]);
  // Per-ticker series state. Loaded whenever `range` or `tickers` change.
  const [series, setSeries] = useState<SeriesEntry[]>([]);
  // Per-ticker quote (used for display in compare chips).
  const [peerQuotes, setPeerQuotes] = useState<Record<string, StockQuote>>({});

  // ── Quote (primary symbol) ─────────────────────────────────────────────
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
    setTickers([symbol.toUpperCase()]);
  }, [symbol]);

  // ── Financials (Moneycontrol DB) ──────────────────────────────────────
  // Fetches the company's fundamentals snapshot + history. Falls through
  // to the existing placeholder RNG when `available: false` so the page
  // still renders for symbols outside the MC scrape.
  useEffect(() => {
    let cancelled = false;
    setFinancials(null);
    getFinancials(symbol)
      .then((res) => {
        if (cancelled) return;
        if (isError(res)) {
          setFinancials(null);
        } else {
          setFinancials(res.data);
        }
      })
      .catch(() => {
        if (!cancelled) setFinancials(null);
      });
    return () => { cancelled = true; };
  }, [symbol]);

  // ── Sparkline series (one per ticker) ──────────────────────────────────
  useEffect(() => {
    let cancelled = false;
    setSeries(tickers.map((s) => ({ symbol: s, state: { kind: "loading" } })));
    Promise.all(
      tickers.map(async (s) => {
        const res = await getSparklineCached(s, range).catch(() => null);
        if (cancelled) return null;
        if (!res || isError(res)) {
          return { symbol: s, state: { kind: "error" as const } } as SeriesEntry;
        }
        return {
          symbol: s,
          state: { kind: "ok" as const, data: res.data },
        } as SeriesEntry;
      }),
    ).then((items) => {
      if (cancelled) return;
      setSeries(items.filter((i): i is SeriesEntry => i !== null));
      // Warm the cache for the ranges most likely to be clicked next.
      prefetchAdjacentRanges(tickers, range);
    });
    return () => { cancelled = true; };
  }, [tickers, range]);

  // ── Peer quotes (for chip price display + name) ────────────────────────
  useEffect(() => {
    const peers = tickers.slice(1);
    if (peers.length === 0) return;
    let cancelled = false;
    Promise.all(
      peers.map((p) =>
        getStockQuote(p)
          .then((r) => (isError(r) ? null : r.data))
          .catch(() => null),
      ),
    ).then((quotes) => {
      if (cancelled) return;
      setPeerQuotes((prev) => {
        const next = { ...prev };
        quotes.forEach((q) => {
          if (q) next[q.symbol] = q;
        });
        return next;
      });
    });
    return () => { cancelled = true; };
  }, [tickers]);

  const addPeer = (s: string): void => {
    const norm = s.trim().toUpperCase();
    if (!norm) return;
    if (tickers.includes(norm)) return;
    if (tickers.length >= COMPARE_PALETTE.length) return;
    setTickers((prev) => [...prev, norm]);
  };

  const removePeer = (s: string): void => {
    if (s === tickers[0]) return; // can't remove the primary
    setTickers((prev) => prev.filter((t) => t !== s));
  };

  return (
    <div className="flex flex-col">
      {quoteState.kind === "loading" && <HeaderSkeleton />}
      {quoteState.kind === "error" && (
        <div
          role="alert"
          className="inline-flex items-center"
          style={{ gap: 8, color: "var(--color-loss)", fontSize: 14 }}
        >
          <AlertCircle size={16} aria-hidden="true" />
          {quoteState.message}
        </div>
      )}
      {quoteState.kind === "ok" && (
        <Header
          quote={quoteState.quote}
          liveLtp={liveQuote.ltp}
          isLive={liveQuote.isLive}
          isPhone={isPhone}
        />
      )}

      {/* Phone reflow: chart → Performance → Overview/Financials switch.
          Desktop keeps the original two-column overview+chart layout. */}
      {isPhone ? (
        quoteState.kind === "ok" && (
          <PhoneLayout
            quote={quoteState.quote}
            financials={financials}
            tickers={tickers}
            peerQuotes={peerQuotes}
            series={series}
            range={range}
            onRangeChange={setRange}
            onAddPeer={addPeer}
            onRemovePeer={removePeer}
          />
        )
      ) : (
        <>
          {/* Two-column row — left (merged overview) is the narrower
              column; chart drives the row height via its content. The
              left column uses h-full + overflow-y-auto so when the chart
              stretches (more comparison tickers, longer summary block),
              the overview just scrolls instead of pushing the row taller. */}
          <div
            className="grid grid-cols-1 xl:grid-cols-[1fr_1.4fr] items-stretch"
            style={{ marginTop: 24, gap: 14 }}
          >
            {/* Left column — Overview + Statistics merged */}
            <div className="flex min-h-0 flex-col">
              {quoteState.kind === "ok" && (
                <MergedOverviewCard quote={quoteState.quote} financials={financials} />
              )}
            </div>

            {/* Right column — Comparison chart */}
            <div className="flex min-h-0 flex-col">
              <ChartCard
                tickers={tickers}
                peerQuotes={peerQuotes}
                series={series}
                range={range}
                onRangeChange={setRange}
                onAddPeer={addPeer}
                onRemovePeer={removePeer}
                primaryQuote={
                  quoteState.kind === "ok" ? quoteState.quote : null
                }
              />
            </div>
          </div>

          {/* Performance — daily + 52-week price-within-range bars. Full width so
              the two bars each keep a comfortable size side by side (the left
              overview card was too narrow to fit both). */}
          {quoteState.kind === "ok" && (
            <PerformanceRanges quote={quoteState.quote} />
          )}

          {/* Key Metrics — snapshot tiles from the financials DB. Skipped
              entirely when the symbol has no MC entry. */}
          {quoteState.kind === "ok" && financials && financials.available && (
            <KeyMetricsStrip financials={financials} />
          )}

          {/* Unified Financials panel */}
          {quoteState.kind === "ok" && (
            <FinancialsPanel quote={quoteState.quote} financials={financials} />
          )}
        </>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// PhoneLayout — mobile reflow of the stock page.
//
//   chart (shorter)  →  Performance  →  [ Overview | Financials ] switch
//
// The chart sits up top (above the company overview, matching Groww's mobile
// app), Performance follows, then a two-way tab strip — styled like the
// option-chain view toggle — swaps between the merged Overview/Statistics
// block and the Key Metrics + Financials panels.
// ---------------------------------------------------------------------------

function PhoneLayout({
  quote,
  financials,
  tickers,
  peerQuotes,
  series,
  range,
  onRangeChange,
  onAddPeer,
  onRemovePeer,
}: {
  quote: StockQuote;
  financials: FinancialsResponse | null;
  tickers: string[];
  peerQuotes: Record<string, StockQuote>;
  series: SeriesEntry[];
  range: SparklineRange;
  onRangeChange: (r: SparklineRange) => void;
  onAddPeer: (s: string) => void;
  onRemovePeer: (s: string) => void;
}): React.ReactElement {
  const [tab, setTab] = useState<"overview" | "financials">("overview");

  return (
    <div className="flex flex-col">
      {/* Chart first — shorter on phone so it doesn't dominate the fold. */}
      <div className="flex min-h-0 flex-col" style={{ marginTop: 16 }}>
        <ChartCard
          tickers={tickers}
          peerQuotes={peerQuotes}
          series={series}
          range={range}
          onRangeChange={onRangeChange}
          onAddPeer={onAddPeer}
          onRemovePeer={onRemovePeer}
          primaryQuote={quote}
          chartHeight={264}
        />
      </div>

      {/* Performance — daily + 52-week range bars. */}
      <PerformanceRanges quote={quote} />

      {/* Overview / Financials switch — underline tab strip matching the
          option-strategy Payoff/P&L/Greeks tabs. */}
      <div
        className="flex shrink-0 gap-6 border-b border-border/40"
        role="tablist"
        aria-label="Stock detail view"
        style={{ marginTop: 24, padding: "0 20px" }}
      >
        {([
          { v: "overview" as const, label: "Company Overview" },
          { v: "financials" as const, label: "Financials" },
        ]).map(({ v, label }) => {
          const active = tab === v;
          return (
            <button
              key={v}
              type="button"
              role="tab"
              aria-selected={active}
              onClick={() => setTab(v)}
              className={`relative px-1 py-2.5 text-[13px] font-medium transition-colors ${
                active ? "text-foreground" : "text-muted-foreground hover:text-foreground"
              }`}
            >
              {label}
              {active && (
                <span className="absolute inset-x-0 -bottom-px h-0.5 rounded-full bg-primary" />
              )}
            </button>
          );
        })}
      </div>

      {tab === "overview" ? (
        <div style={{ marginTop: 18 }}>
          <MergedOverviewCard quote={quote} financials={financials} />
        </div>
      ) : (
        <>
          {financials && financials.available && (
            <KeyMetricsStrip financials={financials} />
          )}
          <FinancialsPanel quote={quote} financials={financials} />
        </>
      )}

      {/* logo.dev attribution — required by their free tier wherever the
          company logo is displayed. */}
      <div
        style={{
          marginTop: 20,
          fontSize: 10.5,
          color: "var(--text-secondary)",
          opacity: 0.7,
        }}
      >
        Logos provided by{" "}
        <a
          href="https://logo.dev"
          target="_blank"
          rel="noopener noreferrer"
          style={{ color: "inherit", textDecoration: "underline" }}
        >
          Logo.dev
        </a>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Header — brand glyph, title with bookmark, price strip
// ---------------------------------------------------------------------------

function Header({
  quote,
  liveLtp,
  isLive,
  isPhone = false,
}: {
  quote: StockQuote;
  liveLtp?: number | null;
  isLive?: boolean;
  /** Phone reflow: shrink glyph/name/price and keep the price pinned to the
   *  right of the same row (Groww-style), with the day chip stacked beneath
   *  it instead of inline. */
  isPhone?: boolean;
}): React.ReactElement {
  const displayLtp = liveLtp ?? quote.ltp;
  const positive = quote.change_pct >= 0;
  const hue = brandGlyphHue(quote.sector);

  return (
    // Phone keeps name (left) and price (right) on one row — no flex-wrap so
    // the price can't drop to a second line.
    <div
      className={isPhone ? "flex items-center" : "flex flex-wrap items-center"}
      style={{ gap: isPhone ? 10 : 18 }}
      data-testid="quote-header"
    >
      {/* Brand logo (with monogram fallback) + name + bookmark. min-w-0 lets
          the name truncate rather than shove the price off on a narrow phone. */}
      <div className="flex min-w-0 items-center" style={{ gap: isPhone ? 10 : 14 }}>
        <CompanyLogo
          logoUrl={quote.logo_url}
          name={quote.name}
          symbol={quote.symbol}
          hue={hue}
          size={isPhone ? 46 : 56}
        />
        <div className="min-w-0">
          <div className="flex min-w-0 items-center" style={{ gap: isPhone ? 4 : 8 }}>
            <h1
              className="m-0 truncate"
              style={{
                fontFamily: "var(--font-ui)",
                fontSize: isPhone ? 16 : 22,
                fontWeight: 600,
                letterSpacing: "-0.025em",
                color: "var(--text-primary)",
              }}
            >
              {quote.name}
            </h1>
            <WatchlistBookmark
              symbol={quote.symbol}
              size={20}
              buttonSize={isPhone ? 30 : 38}
            />
          </div>
          <p
            className="m-0"
            style={{
              fontFamily: "var(--font-ui)",
              fontSize: 12.5,
              color: "var(--text-tertiary)",
              marginTop: 2,
              letterSpacing: "0.02em",
            }}
          >
            {quote.exchange}: {quote.symbol}
          </p>
        </div>
      </div>

      {/* Price + day chip. On phone the chip stacks beneath the price (right
          aligned) so the whole block stays narrow and shares the header row
          with the name; on desktop they sit inline on the baseline. */}
      <div
        className={
          isPhone
            ? "flex shrink-0 flex-col items-end"
            : "flex shrink-0 items-baseline"
        }
        style={{ gap: isPhone ? 3 : 12, marginLeft: "auto" }}
      >
        <span
          className="inline-flex items-center tabular-nums"
          style={{
            fontFamily: "var(--font-ui)",
            fontSize: isPhone ? 18 : 28,
            fontWeight: 600,
            letterSpacing: "-0.02em",
            color: "var(--text-primary)",
            gap: isPhone ? 6 : 8,
          }}
        >
          {INR.format(displayLtp)}
          {/* Live/delayed dot */}
          <span
            title={isLive ? "Live price" : "Delayed price"}
            aria-label={isLive ? "Live price" : "Delayed price"}
            style={{
              display: "inline-block",
              width: 8,
              height: 8,
              borderRadius: "50%",
              background: isLive ? "var(--color-profit)" : "var(--text-tertiary)",
              flexShrink: 0,
            }}
            data-testid={isLive ? "live-dot" : "delayed-dot"}
          />
        </span>
        <span
          className="inline-flex items-center"
          style={{
            gap: 4,
            // Phone: bare coloured text (no chip). Desktop keeps the tinted pill.
            padding: isPhone ? 0 : "3px 10px",
            borderRadius: isPhone ? 0 : "var(--radius-xs)",
            background: isPhone
              ? "transparent"
              : positive
                ? "rgba(16, 185, 129, 0.16)"
                : "rgba(239, 68, 68, 0.16)",
            color: positive ? "var(--color-profit)" : "var(--color-loss)",
            fontFamily: "var(--font-mono)",
            fontSize: isPhone ? 11 : 12.5,
            fontWeight: 500,
            whiteSpace: "nowrap",
          }}
        >
          {fmtDelta(quote.change)} ({fmtPct(quote.change_pct)})
        </span>
      </div>
    </div>
  );
}

function HeaderSkeleton(): React.ReactElement {
  return (
    <div className="flex items-center" style={{ gap: 14 }}>
      <Skeleton style={{ width: 56, height: 56, borderRadius: "var(--radius-md)" }} />
      <div className="flex flex-col" style={{ gap: 6 }}>
        <Skeleton style={{ width: 220, height: 24 }} />
        <Skeleton style={{ width: 120, height: 14 }} />
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Card primitive (Quartr GlassCard semantics)
// ---------------------------------------------------------------------------

function Card({
  children,
  padding = 22,
  borderless = false,
  transparent = false,
  className,
  style,
}: {
  children: React.ReactNode;
  padding?: number | string;
  borderless?: boolean;
  /** When true, the card's background fill is removed so it blends into
   *  the page surface. Use for the two big outer panel containers only. */
  transparent?: boolean;
  className?: string;
  style?: React.CSSProperties;
}): React.ReactElement {
  return (
    <div
      className={className}
      style={{
        padding,
        background: transparent ? "transparent" : "var(--bg-primary)",
        border: borderless || transparent ? "none" : "1px solid var(--glass-border)",
        borderRadius: "var(--radius-md)",
        ...style,
      }}
    >
      {children}
    </div>
  );
}

function SectionLabel({ children }: { children: React.ReactNode }): React.ReactElement {
  return (
    <h2
      className="m-0"
      style={{
        fontFamily: "var(--font-ui)",
        fontSize: 14,
        fontWeight: 600,
        letterSpacing: "-0.01em",
        color: "var(--text-primary)",
        marginBottom: 16,
      }}
    >
      {children}
    </h2>
  );
}

// ---------------------------------------------------------------------------
// Company Overview card — Fiscal.ai shape: header + paragraph + Show more
// + key/value rows. All ours in token language; no Fiscal.ai colors/fonts.
// ---------------------------------------------------------------------------

type CompanyProfile = {
  blurb: string;
  ceo?: string;
  website?: string;
  yearFounded?: string;
  industry?: string;
};

/** Per-symbol enrichment for the few names pivot demos. Falls back to a
 *  generic blurb that uses fields we DO have on StockQuote. */
const COMPANY_PROFILES: Record<string, CompanyProfile> = {
  RELIANCE: {
    blurb:
      "Reliance Industries Limited operates across energy, petrochemicals, refining, retail, and digital services in India and abroad. Its Jio Platforms segment offers wireless and broadband telecom services. Through Reliance Retail it operates one of India's largest retail networks across grocery, fashion, and consumer electronics.",
    ceo: "Mr. Mukesh D. Ambani",
    website: "www.ril.com",
    yearFounded: "1973",
    industry: "Diversified",
  },
  TCS: {
    blurb:
      "Tata Consultancy Services Limited provides information technology and business solutions worldwide. The company offers services across banking, financial services and insurance, communications, media and information services, education, energy, resources and utilities, retail, manufacturing, and life sciences and healthcare.",
    ceo: "Mr. K. Krithivasan",
    website: "www.tcs.com",
    yearFounded: "1968",
    industry: "Information Technology Services",
  },
  INFY: {
    blurb:
      "Infosys Limited is a global leader in next-generation digital services and consulting. The company helps clients navigate digital transformation through strategic services across cloud, AI, data, automation, and enterprise applications.",
    ceo: "Mr. Salil Parekh",
    website: "www.infosys.com",
    yearFounded: "1981",
    industry: "Information Technology Services",
  },
  HDFCBANK: {
    blurb:
      "HDFC Bank Limited provides banking and financial services to individuals and businesses across India. The bank's segments include Treasury, Retail Banking, Wholesale Banking, and Other Banking Services.",
    ceo: "Mr. Sashidhar Jagdishan",
    website: "www.hdfcbank.com",
    yearFounded: "1994",
    industry: "Banks",
  },
  NVDA: {
    blurb:
      "NVIDIA Corporation provides graphics, and compute and networking solutions in the United States, Taiwan, China, and internationally. The company's Graphics segment offers GeForce GPUs for gaming and PCs, the GeForce NOW game streaming service and related infrastructure, and solutions for gaming platforms; Quadro/NVIDIA RTX GPUs for enterprise workstation graphics; virtual GPU software for cloud-based visual and virtual computing; automotive platforms for infotainment systems; and Omniverse software for building and operating metaverse and 3D internet applications.",
    ceo: "Mr. Jen-Hsun Huang",
    website: "www.nvidia.com",
    yearFounded: "1993",
    industry: "Semiconductors and Semiconductor Equipment",
  },
};

/** Merged Overview + Statistics card (borderless, fills its grid cell
 *  vertically so the chart drives the row height). The "Company
 *  Statistics" header is intentionally dropped — the three stat
 *  columns sit beneath the facts table as a continuation of the same
 *  block. */
function MergedOverviewCard({
  quote,
  financials,
}: {
  quote: StockQuote;
  financials: FinancialsResponse | null;
}): React.ReactElement {
  // 52-week high/low and the day high/low now live in the Performance range
  // bars below, so they're dropped from these columns (no duplication, and no
  // "₹NaN" when a source omits the 52-week figures).
  const profile: { label: string; value: string }[] = [
    { label: "Market Cap", value: fmtCr(quote.market_cap) },
    { label: "Volume", value: quote.volume.toLocaleString("en-IN") },
  ];

  // Valuation ratios: P/E from the live quote; P/B, EV/Sales, EV/EBITDA from
  // the financials snapshot (Moneycontrol / yfinance fallback). Render "—"
  // when the field is absent — never fabricate.
  const fmtRatio = (v: number | null | undefined): string =>
    v != null && Number.isFinite(v) ? `${v.toFixed(2)}x` : "—";

  const pbValue = financials?.latest?.price_to_book?.value ?? null;
  const evEbitdaValue = financials?.latest?.ev_to_ebitda?.value ?? null;
  const evSalesValue = financials?.latest?.ev_to_sales?.value ?? null;

  const valuation: { label: string; value: string }[] = [
    { label: "P/E", value: quote.pe_ratio !== null ? quote.pe_ratio.toFixed(1) : "—" },
    { label: "P/B", value: fmtRatio(pbValue) },
    { label: "EV/Sales", value: fmtRatio(evSalesValue) },
    { label: "EV/EBITDA", value: fmtRatio(evEbitdaValue) },
  ];
  const day: { label: string; value: string }[] = [
    { label: "Open", value: inrOrDash(quote.open) },
    { label: "Prev Close", value: inrOrDash(quote.prev_close) },
  ];

  return (
    <Card
      transparent
      padding="22px 24px"
      className="flex h-full min-h-0 flex-col overflow-y-auto"
    >
      <CompanyOverviewBody quote={quote} financials={financials} />

      {/* Stats — folded into the same card. No section header. Sits
          beneath the Year Founded row separated by a slim gap. */}
      <div
        className="grid grid-cols-1 sm:grid-cols-3"
        style={{ gap: 24, marginTop: 22 }}
      >
        <StatColumn title="Profile" rows={profile} />
        <StatColumn title="Valuation (TTM)" rows={valuation} />
        <StatColumn title="Today" rows={day} />
      </div>
    </Card>
  );
}

function CompanyOverviewBody({
  quote,
  financials,
}: {
  quote: StockQuote;
  financials: FinancialsResponse | null;
}): React.ReactElement {
  const [expanded, setExpanded] = useState(false);
  // Live profile from the backend enrichment DB (pre-fetched by parent). Prefer
  // this over the hardcoded COMPANY_PROFILES map; the hardcoded entries remain
  // as a last-resort fallback for the 5 curated symbols when the backend has nothing.
  const live = financials?.profile ?? null;
  const fallback = COMPANY_PROFILES[quote.symbol.toUpperCase()];
  const blurb =
    live?.blurb ??
    fallback?.blurb ??
    `${quote.name} is publicly listed on ${quote.exchange}. Detailed company description and operating segment breakdown will be loaded from the fundamentals service.`;

  // Normalize website to a bare domain — the live backend profile stores full
  // URLs (e.g. "https://www.tcs.com"). Prefixing "https://" onto that would
  // break the href ("https://https://…"). Strip any existing scheme first.
  const rawWebsite = live?.website ?? fallback?.website ?? null;
  const bareWebsite = rawWebsite?.replace(/^https?:\/\//, "") ?? null;

  // Match Fiscal.ai field set: Name / CEO / Website / Sector / Year Founded.
  // Anything we don't have falls back to "—" so the row spacing stays
  // consistent regardless of which symbol you land on.
  const facts: { label: string; value: React.ReactNode }[] = [
    { label: "Name", value: quote.name },
    { label: "CEO", value: live?.ceo ?? fallback?.ceo ?? "—" },
    {
      label: "Website",
      value: bareWebsite ? (
        <a
          href={`https://${bareWebsite}`}
          target="_blank"
          rel="noopener noreferrer"
          style={{ color: "var(--text-primary)", textDecoration: "none" }}
        >
          {bareWebsite}
        </a>
      ) : (
        "—"
      ),
    },
    {
      label: "Sector",
      value: live?.sector ?? live?.industry ?? fallback?.industry ?? quote.sector ?? "—",
    },
    {
      label: "Year Founded",
      value: fallback && "yearFounded" in fallback ? (fallback.yearFounded ?? "—") : "—",
    },
  ];

  // Truncate threshold mirrors the reference: ~2-3 lines of body copy
  // before "Show more" appears.
  const PREVIEW_LEN = 220;
  const isLong = blurb.length > PREVIEW_LEN;

  return (
    <>
      <SectionLabel>Company Overview</SectionLabel>

      <p
        style={{
          margin: 0,
          fontFamily: "var(--font-ui)",
          fontSize: 13,
          lineHeight: 1.65,
          color: "var(--text-secondary)",
        }}
      >
        {expanded || !isLong ? blurb : truncate(blurb, PREVIEW_LEN)}
      </p>

      {isLong && (
        <button
          type="button"
          onClick={() => setExpanded((e) => !e)}
          style={{
            display: "block",
            marginTop: 8,
            background: "transparent",
            border: "none",
            padding: 0,
            color: "var(--text-primary)",
            fontFamily: "var(--font-ui)",
            fontSize: 13,
            fontWeight: 500,
            cursor: "pointer",
            textAlign: "left",
          }}
        >
          {expanded ? "Show less" : "Show more"}
        </button>
      )}

      {/* Key/value rows. The reference uses borderless rows with a
          subtle bottom hairline only — no row striping, no tinting. */}
      <div style={{ marginTop: 18 }}>
        {facts.map((f, i) => (
          <div
            key={f.label}
            className="flex items-baseline justify-between"
            style={{
              padding: "10px 0",
              borderTop: "1px solid var(--glass-border)",
              borderBottom: i === facts.length - 1 ? "1px solid var(--glass-border)" : "none",
              fontFamily: "var(--font-ui)",
              fontSize: 13,
            }}
          >
            <span style={{ color: "var(--text-secondary)" }}>{f.label}</span>
            <span
              style={{
                color: "var(--text-primary)",
                textAlign: "right",
                fontWeight: 500,
              }}
            >
              {f.value}
            </span>
          </div>
        ))}
      </div>
    </>
  );
}

function truncate(s: string, n: number): string {
  if (s.length <= n) return s;
  return s.slice(0, n).trimEnd() + "…";
}

function StatColumn({
  title,
  rows,
}: {
  title: string;
  rows: { label: string; value: string }[];
}): React.ReactElement {
  return (
    <div>
      <p
        style={{
          margin: 0,
          marginBottom: 10,
          fontFamily: "var(--font-ui)",
          fontSize: 11,
          fontWeight: 600,
          letterSpacing: "0.06em",
          textTransform: "uppercase",
          color: "var(--text-tertiary)",
        }}
      >
        {title}
      </p>
      {rows.map((r) => (
        <div
          key={r.label}
          className="flex items-baseline justify-between"
          style={{
            padding: "6px 0",
            fontFamily: "var(--font-ui)",
            fontSize: 12.5,
          }}
        >
          <span style={{ color: "var(--text-secondary)" }}>{r.label}</span>
          <span
            className="tabular-nums"
            style={{ color: "var(--text-primary)", fontFamily: "var(--font-mono)" }}
          >
            {r.value}
          </span>
        </div>
      ))}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Performance — where the live price sits inside its daily and 52-week range.
// The two ranges sit side by side. Each shows low/high labels + values with a
// marker on a track at the current price; when a range has no data (e.g. a
// missing 52-week high/low) it shows "—" and omits the marker rather than
// disappearing, so the daily and 52-week bars always read as a pair.
// ---------------------------------------------------------------------------

function RangeBar({
  lowLabel,
  highLabel,
  low,
  high,
  current,
}: {
  lowLabel: string;
  highLabel: string;
  low: number;
  high: number;
  current: number;
}): React.ReactElement {
  const valid = Number.isFinite(low) && Number.isFinite(high) && high > low;
  const showMarker = valid && Number.isFinite(current);
  const pct = showMarker
    ? Math.min(100, Math.max(0, ((current - low) / (high - low)) * 100))
    : 0;

  const labelStyle: React.CSSProperties = {
    fontFamily: "var(--font-ui)",
    fontSize: 11.5,
    color: "var(--text-tertiary)",
  };
  const valueStyle: React.CSSProperties = {
    fontFamily: "var(--font-mono)",
    fontSize: 14,
    fontWeight: 600,
    color: "var(--text-primary)",
  };

  return (
    <div>
      <div className="flex items-baseline justify-between" style={{ marginBottom: 3 }}>
        <span style={labelStyle}>{lowLabel}</span>
        <span style={labelStyle}>{highLabel}</span>
      </div>
      <div className="flex items-baseline justify-between" style={{ marginBottom: 12 }}>
        <span className="tabular-nums" style={valueStyle}>{inrOrDash(low)}</span>
        <span className="tabular-nums" style={valueStyle}>{inrOrDash(high)}</span>
      </div>
      <div style={{ position: "relative", height: 6 }}>
        <div
          style={{
            position: "absolute",
            inset: 0,
            borderRadius: 999,
            background: "var(--bg-secondary)",
          }}
        />
        {/* Up-pointing marker at the current price (only when in-range). */}
        {showMarker && (
          <div
            style={{
              position: "absolute",
              top: -8,
              left: `${pct}%`,
              transform: "translateX(-50%)",
              width: 0,
              height: 0,
              borderLeft: "5px solid transparent",
              borderRight: "5px solid transparent",
              borderBottom: "7px solid var(--text-secondary)",
            }}
          />
        )}
      </div>
    </div>
  );
}

function PerformanceRanges({ quote }: { quote: StockQuote }): React.ReactElement | null {
  const dayValid =
    Number.isFinite(quote.low) && Number.isFinite(quote.high) && quote.high > quote.low;
  const yearValid =
    Number.isFinite(quote.w52_low) &&
    Number.isFinite(quote.w52_high) &&
    quote.w52_high > quote.w52_low;

  if (!dayValid && !yearValid) return null;

  return (
    // Full-width section; 20px inset aligns with Key Metrics / Financial
    // Performance below.
    <div style={{ marginTop: 24, padding: "0 20px" }}>
      <h2
        className="m-0"
        style={{
          fontFamily: "var(--font-ui)",
          fontSize: 14,
          fontWeight: 600,
          letterSpacing: "-0.01em",
          color: "var(--text-primary)",
          marginBottom: 16,
        }}
      >
        Performance
      </h2>
      {/* Daily and 52-week ranges, each a comfortable fixed width, pushed to
          opposite ends so the empty middle becomes the gap between them
          (space-between) rather than dead space on the right. Wraps on narrow
          screens. */}
      <div
        className="flex flex-wrap"
        style={{ gap: 40, justifyContent: "space-between" }}
      >
        <div style={{ flex: "0 1 480px" }}>
          <RangeBar
            lowLabel="Today's low"
            highLabel="Today's high"
            low={quote.low}
            high={quote.high}
            current={quote.ltp}
          />
        </div>
        <div style={{ flex: "0 1 480px" }}>
          <RangeBar
            lowLabel="52 week low"
            highLabel="52 week high"
            low={quote.w52_low}
            high={quote.w52_high}
            current={quote.ltp}
          />
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Chart card — multi-line comparison
// ---------------------------------------------------------------------------

const METRIC_OPTIONS: readonly Metric[] = [
  "Price",
  "PE Ratio",
  "Sales and Margin",
  "Market Cap",
];

function ChartCard({
  tickers,
  peerQuotes,
  series,
  range,
  onRangeChange,
  onAddPeer,
  onRemovePeer,
  primaryQuote,
  chartHeight = 320,
}: {
  tickers: string[];
  peerQuotes: Record<string, StockQuote>;
  series: SeriesEntry[];
  range: SparklineRange;
  onRangeChange: (r: SparklineRange) => void;
  onAddPeer: (s: string) => void;
  onRemovePeer: (s: string) => void;
  primaryQuote: StockQuote | null;
  /** Collapsed (non-fullscreen) chart height in px. Phone passes a shorter
   *  value; defaults to the desktop 320. */
  chartHeight?: number;
}): React.ReactElement {
  const [metric, setMetric] = useState<Metric>("Price");
  // Min/Max date filters (ISO yyyy-mm-dd from native <input type="date">).
  const [minDate, setMinDate] = useState<string>("");
  const [maxDate, setMaxDate] = useState<string>("");
  // Fullscreen overlay: the Maximize2 button lifts the entire card to a
  // fixed surface that covers most of the viewport; the chart's height
  // grows to fill the freed space. Esc dismisses.
  const [expanded, setExpanded] = useState(false);
  useEffect(() => {
    if (!expanded) return;
    const onKey = (e: KeyboardEvent): void => {
      if (e.key === "Escape") setExpanded(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [expanded]);

  // ── Metric series (PE Ratio / EV/EBITDA) ─────────────────────────────
  // Fetched per-ticker when a fundamental metric is selected. In Price
  // mode this state is empty and unused — the parent-supplied `series`
  // prop is used instead.
  type MetricEntry = {
    symbol: string;
    state:
      | { kind: "loading" }
      | { kind: "unavailable" }
      | { kind: "ok"; points: MetricSeriesResponse["points"] };
  };
  const [metricSeries, setMetricSeries] = useState<MetricEntry[]>([]);

  const isMetricMode = metric !== "Price";

  useEffect(() => {
    if (!isMetricMode) {
      setMetricSeries([]);
      return;
    }
    const metricKey = metric === "PE Ratio" ? "pe" : metric === "Market Cap" ? "market_cap" : "sales_margin";
    let cancelled = false;
    setMetricSeries(tickers.map((s) => ({ symbol: s, state: { kind: "loading" } })));
    Promise.all(
      tickers.map(async (sym) => {
        const res = await getMetricSeries(sym, metricKey, range).catch(() => null);
        if (cancelled) return null;
        if (!res || isError(res)) {
          return { symbol: sym, state: { kind: "unavailable" as const } } as MetricEntry;
        }
        if (!res.data.available || res.data.points.length === 0) {
          return { symbol: sym, state: { kind: "unavailable" as const } } as MetricEntry;
        }
        return {
          symbol: sym,
          state: { kind: "ok" as const, points: res.data.points },
        } as MetricEntry;
      }),
    ).then((items) => {
      if (cancelled) return;
      setMetricSeries(items.filter((i): i is MetricEntry => i !== null));
    });
    return () => { cancelled = true; };
  // isMetricMode is derived from metric so not included separately
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [metric, tickers, range]);

  // ── Drag-to-select range state ────────────────────────────────────────
  // Tracks a click-and-drag selection band on the chart. `startIdx` /
  // `endIdx` are indices into `chartData.rows`; `startLabel` / `endLabel`
  // are the corresponding `t` values used as Recharts ReferenceArea x1/x2.
  type DragState = {
    isDragging: boolean;
    startLabel: string;
    startIdx: number;
    endLabel: string;
    endIdx: number;
  };
  const [drag, setDrag] = useState<DragState | null>(null);

  // Minimal type matching what Recharts passes to onMouseDown/Move/Up.
  type ChartMouseState = {
    activeLabel?: string;
    activeTooltipIndex?: number;
  };

  const handleChartMouseDown = (state: ChartMouseState): void => {
    const label = state.activeLabel;
    const idx = state.activeTooltipIndex;
    if (label == null || idx == null) return;
    setDrag({
      isDragging: true,
      startLabel: label,
      startIdx: idx,
      endLabel: label,
      endIdx: idx,
    });
  };

  const handleChartMouseMove = (state: ChartMouseState): void => {
    if (!drag?.isDragging) return;
    const label = state.activeLabel;
    const idx = state.activeTooltipIndex;
    if (label == null || idx == null) return;
    setDrag((prev) =>
      prev ? { ...prev, endLabel: label, endIdx: idx } : prev,
    );
  };

  const handleChartMouseUp = (state: ChartMouseState): void => {
    if (!drag) return;
    const label = state.activeLabel;
    const idx = state.activeTooltipIndex;
    const endLabel = label ?? drag.endLabel;
    const endIdx = idx ?? drag.endIdx;
    // Plain click (no real drag): clear any existing selection.
    if (Math.abs(endIdx - drag.startIdx) < 2) {
      setDrag(null);
      return;
    }
    setDrag((prev) =>
      prev
        ? { ...prev, isDragging: false, endLabel, endIdx }
        : prev,
    );
  };

  const handleChartMouseLeave = (): void => {
    // Cancel an in-progress drag; preserve a finalised selection.
    if (drag?.isDragging) setDrag(null);
  };

  // Escape clears a finalised selection.
  useEffect(() => {
    if (!drag) return;
    const onKey = (e: KeyboardEvent): void => {
      if (e.key === "Escape") setDrag(null);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [drag]);

  // Merge all series into one Recharts dataset, normalised to 100 at the
  // first point of each ticker. This makes performance comparable across
  // very different price scales (e.g. NVDA ~$200 vs ICICI ₹1,100).
  //
  // Date filter: when minDate/maxDate are set we slice the master
  // timeline to that window before building the rows. Baselines are
  // re-anchored on the first surviving point so the normalised series
  // still starts at 100 within the chosen window.
  const chartData = useMemo(() => {
    const okSeries = series.filter(
      (s): s is { symbol: string; state: { kind: "ok"; data: SparklineResponse } } =>
        s.state.kind === "ok",
    );
    if (okSeries.length === 0) return { rows: [], baseline: {} as Record<string, number> };

    // Master timeline = primary series, optionally filtered by the
    // user-picked min/max date window.
    const minTs = minDate ? new Date(minDate).getTime() : -Infinity;
    const maxTs = maxDate ? new Date(maxDate).getTime() : Infinity;
    const master = okSeries[0]!.state.data.points.filter((p) => {
      const t = new Date(p.t).getTime();
      return t >= minTs && t <= maxTs;
    });
    if (master.length === 0) {
      return { rows: [], baseline: {} as Record<string, number> };
    }

    const symMap = new Map<string, Map<string, number>>();
    okSeries.forEach((s) => {
      const m = new Map<string, number>();
      s.state.data.points.forEach((p) => m.set(p.t, p.v));
      symMap.set(s.symbol, m);
    });

    // Re-anchor baselines on the first in-window point so each ticker
    // still starts at 100 within the filtered window.
    const baseline: Record<string, number> = {};
    okSeries.forEach((s) => {
      const firstInWindow = master.find((pt) =>
        symMap.get(s.symbol)?.has(pt.t),
      );
      baseline[s.symbol] = firstInWindow
        ? symMap.get(s.symbol)!.get(firstInWindow.t)!
        : (s.state.data.points[0]?.v ?? 1);
    });

    const rows = master.map((pt) => {
      const row: Record<string, string | number | null> = { t: pt.t };
      okSeries.forEach((s) => {
        const v = symMap.get(s.symbol)?.get(pt.t);
        row[s.symbol] = v !== undefined ? (v / baseline[s.symbol]!) * 100 : null;
      });
      return row;
    });
    return { rows, baseline };
  }, [series, minDate, maxDate]);

  // Raw (un-normalised) price lookup: symbol → date-string → real ₹ price.
  // Used by GrowwTooltip in Price mode so the hover value reflects the actual
  // price rather than the 100-base-indexed chart value.
  const rawPriceByDate = useMemo(() => {
    const map = new Map<string, Map<string, number>>();
    series.forEach((s) => {
      if (s.state.kind !== "ok") return;
      const m = new Map<string, number>();
      s.state.data.points.forEach((p) => m.set(p.t, p.v));
      map.set(s.symbol, m);
    });
    return map;
  }, [series]);

  // Metric-mode chart rows — absolute values, no normalisation. Master timeline = primary ticker.
  const metricChartData = useMemo(() => {
    if (!isMetricMode) return null;
    const okEntries = metricSeries.filter(
      (e): e is MetricEntry & { state: { kind: "ok"; points: MetricSeriesResponse["points"] } } =>
        e.state.kind === "ok",
    );
    if (okEntries.length === 0) return null;
    const primary = okEntries[0]!;
    const primaryPoints = primary.state.points;
    // Build per-symbol lookup maps for both value and margin.
    const symMap = new Map<string, Map<string, number>>();
    const marginMap = new Map<string, Map<string, number | null>>();
    okEntries.forEach((e) => {
      const m = new Map<string, number>();
      const mg = new Map<string, number | null>();
      e.state.points.forEach((p) => {
        m.set(p.t, p.v);
        mg.set(p.t, p.margin ?? null);
      });
      symMap.set(e.symbol, m);
      marginMap.set(e.symbol, mg);
    });
    const rows = primaryPoints.map((pt) => {
      const row: Record<string, string | number | null> = { t: pt.t };
      okEntries.forEach((e) => {
        const v = symMap.get(e.symbol)?.get(pt.t);
        row[e.symbol] = v !== undefined ? v : null;
        // Write the __margin shadow key so the tooltip can annotate revenue
        // with the net-profit-margin % (only meaningful for sales_margin).
        row[`${e.symbol}__margin`] = marginMap.get(e.symbol)?.get(pt.t) ?? null;
      });
      return row;
    });
    return rows;
  }, [isMetricMode, metricSeries]);

  // Tickers actually visible in the current mode:
  // - Price: all tickers that have an ok sparkline series
  // - Metric: only tickers that have ok metric data
  const visibleTickers = useMemo(() => {
    if (!isMetricMode) return tickers;
    return metricSeries
      .filter((e) => e.state.kind === "ok")
      .map((e) => e.symbol);
  }, [isMetricMode, metricSeries, tickers]);

  // Active chart rows — metric data in metric mode, price data otherwise.
  // Wrapped in useMemo so the reference is stable and doesn't bust downstream
  // useMemo hooks that take it as a dependency.
  const activeBaseRows = useMemo(
    () => (isMetricMode ? (metricChartData ?? []) : chartData.rows),
    [isMetricMode, metricChartData, chartData.rows],
  );

  // Map ticker → palette color for chips + lines
  const colorFor = (sym: string): string => {
    const idx = tickers.indexOf(sym);
    return COMPARE_PALETTE[idx >= 0 ? idx % COMPARE_PALETTE.length : 0]!;
  };

  // Last-point summary for each ticker (used in footer)
  const summaries = useMemo(() => {
    return series
      .filter((s) => s.state.kind === "ok")
      .map((s) => {
        const data = (s.state as { kind: "ok"; data: SparklineResponse }).data;
        const first = data.points[0]?.v ?? null;
        const last = data.points[data.points.length - 1]?.v ?? null;
        if (first === null || last === null || first === 0) {
          return { symbol: s.symbol, totalChg: null, cagr: null };
        }
        const total = ((last - first) / first) * 100;
        const yearsByRange: Record<SparklineRange, number> = {
          "1D": 1 / 365,
          "1W": 7 / 365,
          "1M": 30 / 365,
          "6M": 0.5,
          "1Y": 1,
          "5Y": 5,
        };
        const years = yearsByRange[range] ?? 1;
        const cagr = years > 0 ? (Math.pow(last / first, 1 / years) - 1) * 100 : null;
        return { symbol: s.symbol, totalChg: total, cagr };
      });
  }, [series, range]);

  const earliestDate = activeBaseRows[0]?.t as string | undefined;
  const latestDate = activeBaseRows[activeBaseRows.length - 1]?.t as string | undefined;

  // Last numeric value per ticker — used to render the price-tag labels
  // pinned to the right edge of the chart (Fiscal.ai pattern).
  // In metric mode, reads from metricSeries; in price mode, from series.
  const endValues = useMemo(() => {
    const map = new Map<string, number | null>();
    if (isMetricMode) {
      metricSeries.forEach((e) => {
        if (e.state.kind !== "ok") {
          map.set(e.symbol, null);
          return;
        }
        const last = e.state.points[e.state.points.length - 1]?.v ?? null;
        map.set(e.symbol, last);
      });
    } else {
      series.forEach((s) => {
        if (s.state.kind !== "ok") {
          map.set(s.symbol, null);
          return;
        }
        const last = s.state.data.points[s.state.data.points.length - 1]?.v ?? null;
        map.set(s.symbol, last);
      });
    }
    return map;
  }, [isMetricMode, metricSeries, series]);

  // ── Derived selection metrics ─────────────────────────────────────────
  // Normalise indices so left-to-right and right-to-left drags both work.
  // Uses the raw primary-series prices (not normalised-to-100 chart values)
  // so the absolute delta is in real ₹.
  // Disabled in metric mode (delta in PE units wouldn't render correctly
  // with the existing ₹-formatted pill).
  const selectionInfo = useMemo(() => {
    if (isMetricMode) return null;
    if (!drag || drag.startIdx === drag.endIdx) return null;
    const lo = Math.min(drag.startIdx, drag.endIdx);
    const hi = Math.max(drag.startIdx, drag.endIdx);
    const loLabel = lo === drag.startIdx ? drag.startLabel : drag.endLabel;
    const hiLabel = hi === drag.startIdx ? drag.startLabel : drag.endLabel;

    // Raw prices from the primary series.
    const primarySeries = series[0];
    const rawPoints =
      primarySeries?.state.kind === "ok"
        ? primarySeries.state.data.points
        : null;

    let deltaAbs: number | null = null;
    let deltaPct: number | null = null;

    if (rawPoints && rawPoints.length > 0) {
      // Find the raw point whose `t` matches the row label. The rows are
      // filtered by date window so we match by label string, then fall
      // back to index position if no exact match is found.
      const startT = activeBaseRows[lo]?.t as string | undefined;
      const endT = activeBaseRows[hi]?.t as string | undefined;
      const startRaw = rawPoints.find((p) => p.t === startT) ?? rawPoints[lo];
      const endRaw = rawPoints.find((p) => p.t === endT) ?? rawPoints[hi];
      if (startRaw && endRaw && startRaw.v !== 0) {
        deltaAbs = endRaw.v - startRaw.v;
        deltaPct = (deltaAbs / startRaw.v) * 100;
      }
    }

    return { lo, hi, loLabel, hiLabel, deltaAbs, deltaPct };
  }, [isMetricMode, drag, series, activeBaseRows]);

  // Pill position: horizontally centred on the selection midpoint,
  // capped to keep the pill inside the chart wrapper (1–94%).
  const pillLeftPct = useMemo(() => {
    if (!selectionInfo || activeBaseRows.length === 0) return null;
    const { lo, hi } = selectionInfo;
    const midFrac = ((lo + hi) / 2) / (activeBaseRows.length - 1);
    return Math.max(1, Math.min(94, midFrac * 100));
  }, [selectionInfo, activeBaseRows.length]);

  // Composed rows: activeBaseRows with __selValue merged in (price mode only).
  // __selValue = primary ticker's value within [lo, hi], else null.
  // Merging into the same array guarantees x-axis alignment with the Line series.
  const composedRows = useMemo(() => {
    const primaryKey = tickers[0];
    if (!primaryKey || activeBaseRows.length === 0) return activeBaseRows;
    const lo = selectionInfo?.lo ?? -1;
    const hi = selectionInfo?.hi ?? -1;
    return activeBaseRows.map((row, i) => ({
      ...row,
      __selValue:
        selectionInfo && i >= lo && i <= hi
          ? (row[primaryKey] as number | null) ?? null
          : null,
    }));
  }, [activeBaseRows, selectionInfo, tickers]);

  // Range pills — rendered inline in the controls row on desktop, and moved
  // below the chart (full width, each pill flex-1) on phone, matching the
  // PortfolioTab pattern. The same element adapts via responsive classes.
  const rangePills = (
    <div
      className="flex w-full sm:inline-flex sm:w-auto"
      style={{
        gap: 2,
        padding: 2,
        background: "var(--bg-base)",
        border: "1px solid var(--glass-border)",
        borderRadius: "var(--radius-sm)",
        flexShrink: 0,
      }}
    >
      {RANGE_OPTIONS.map((r) => {
        const active = range === r;
        return (
          <button
            key={r}
            type="button"
            onClick={() => onRangeChange(r)}
            aria-pressed={active}
            data-testid={`range-${r}`}
            className="flex-1 sm:flex-none"
            style={{
              padding: "5px 12px",
              border: "none",
              borderRadius: "var(--radius-xs)",
              fontFamily: "var(--font-ui)",
              fontSize: 11.5,
              fontWeight: 500,
              cursor: "pointer",
              background: active ? "var(--text-primary)" : "transparent",
              color: active ? "var(--bg-primary)" : "var(--text-secondary)",
              transition: "color 0.2s var(--ease-quartr), background-color 0.2s var(--ease-quartr)",
              whiteSpace: "nowrap",
            }}
          >
            {r}
          </button>
        );
      })}
    </div>
  );

  return (
    <>
      {expanded && (
        <div
          aria-hidden="true"
          onClick={() => setExpanded(false)}
          style={{
            position: "fixed",
            inset: 0,
            background: "rgba(0,0,0,0.55)",
            zIndex: 60,
          }}
        />
      )}
    <Card
      transparent
      padding="0"
      className="flex h-full min-h-0 flex-col"
      style={
        expanded
          ? {
              position: "fixed",
              top: 24,
              left: 24,
              right: 24,
              bottom: 24,
              // Override Tailwind's `h-full` (height: 100%) which, combined
              // with `top/bottom`, leaves the box over-constrained — CSS
              // honors the height and pushes the bottom edge below the
              // viewport, clipping the footer summary. Using `auto` lets
              // the implicit height (= 100vh - 48px) drive the layout.
              height: "auto",
              zIndex: 61,
              background: "var(--bg-primary)",
              border: "1px solid var(--glass-border)",
              borderRadius: "var(--radius-md)",
              boxShadow: "0 30px 80px rgba(0,0,0,0.45)",
              // Vertical scroll catches the case where the chart aspect
              // forces total content past the available height (e.g. with
              // many comparison tickers in the footer summary).
              overflow: "hidden auto",
            }
          : undefined
      }
    >
      {/* ── Row 1: full-width search pill + maximize ──────────────────── */}
      <div
        className="flex items-center"
        style={{
          gap: 10,
          padding: "16px 18px 12px",
        }}
      >
        <div
          className="flex items-center"
          style={{
            gap: 8,
            flex: 1,
            minWidth: 0,
            height: 40,
            padding: "0 14px",
            background: "var(--bg-base)",
            border: "1px solid var(--glass-border)",
            borderRadius: "var(--radius-md)",
            position: "relative",
          }}
        >
          <Search
            size={14}
            strokeWidth={2}
            style={{ color: "var(--text-tertiary)", flexShrink: 0 }}
            aria-hidden="true"
          />
          {tickers.map((sym, i) => (
            <CompareChip
              key={sym}
              symbol={sym}
              color={colorFor(sym)}
              removable={i > 0}
              onRemove={() => onRemovePeer(sym)}
            />
          ))}
          {/* Only render the autosuggest when there's room for another ticker */}
          {tickers.length < COMPARE_PALETTE.length && (
            <CompanyAutosuggest
              placeholder={tickers.length === 1 ? "Compare to…" : ""}
              onSelect={(sym) => onAddPeer(sym)}
              inputDataTestId="compare-search"
            />
          )}
        </div>
        <button
          type="button"
          onClick={() => setExpanded((v) => !v)}
          aria-label={expanded ? "Collapse chart" : "Expand chart"}
          aria-pressed={expanded}
          data-testid="chart-expand-btn"
          className="inline-flex shrink-0 items-center justify-center"
          style={{
            width: 36,
            height: 36,
            background: "transparent",
            border: "1px solid var(--glass-border)",
            borderRadius: "var(--radius-sm)",
            color: "var(--text-secondary)",
            cursor: "pointer",
            transition: "color 0.2s var(--ease-quartr), border-color 0.2s var(--ease-quartr)",
          }}
          onMouseEnter={(e) => {
            e.currentTarget.style.color = "var(--text-primary)";
            e.currentTarget.style.borderColor = "var(--glass-border-hover)";
          }}
          onMouseLeave={(e) => {
            e.currentTarget.style.color = "var(--text-secondary)";
            e.currentTarget.style.borderColor = "var(--glass-border)";
          }}
        >
          {expanded ? (
            <Minimize2 size={14} strokeWidth={2} aria-hidden="true" />
          ) : (
            <Maximize2 size={14} strokeWidth={2} aria-hidden="true" />
          )}
        </button>
      </div>

      {/* ── Row 2: controls ───────────────────────────────────────────────
          Desktop: Min Date | range pills | Max Date | Price selector (right).
          Phone: Min/Max dates share one line, then the Price selector full
          width below; the range pills move beneath the chart (see below). */}
      <div
        className="flex flex-col sm:flex-row sm:flex-wrap sm:items-center"
        style={{
          gap: 8,
          padding: "0 18px 14px",
        }}
      >
        {/* Dates wrapper: a full-width flex row on phone (Min + Max share the
            line); dissolves into the parent row on desktop via `sm:contents`
            so the original Min | pills | Max ordering is preserved. */}
        <div className="flex w-full gap-2 sm:contents">
          <DateField
            value={minDate}
            onChange={setMinDate}
            placeholder="Min Date"
            aria-label="Minimum date"
            className="flex-1 sm:w-32 sm:flex-none"
          />
          {/* Range pills — desktop in-row slot only (hidden on phone). */}
          <div className="hidden sm:contents">{rangePills}</div>
          <DateField
            value={maxDate}
            onChange={setMaxDate}
            placeholder="Max Date"
            aria-label="Maximum date"
            className="flex-1 sm:w-32 sm:flex-none"
          />
        </div>
        {/* Metric selector — full width on phone, pushed to the right edge on
            desktop (lines up with the expand button above). */}
        <div className="w-full sm:ml-auto sm:w-auto">
          <MetricSelector value={metric} onChange={setMetric} />
        </div>
      </div>

      {/* ── Chart with end-label price tags ───────────────────────────── */}
      <div
        style={{
          position: "relative",
          width: "100%",
          // Expanded: grow to fill the remaining flex space inside the
          // fixed overlay card so the chart consumes the freed area.
          // Collapsed: pinned to a comfortable inline height.
          height: expanded ? "auto" : chartHeight,
          flex: expanded ? 1 : undefined,
          minHeight: expanded ? 0 : undefined,
          padding: "0 18px",
        }}
      >
        {/* Metric-mode: primary unavailable empty state */}
        {isMetricMode &&
          metricSeries.length > 0 &&
          metricSeries[0]?.state.kind === "unavailable" ? (
          <div
            className="flex items-center justify-center"
            style={{
              height: "100%",
              fontFamily: "var(--font-ui)",
              fontSize: 13,
              color: "var(--text-tertiary)",
              textAlign: "center",
              padding: "0 24px",
            }}
            data-testid="metric-unavailable"
          >
            {metric} history isn&apos;t available for {tickers[0] ?? "this symbol"}
          </div>
        ) : isMetricMode && metricSeries.some((e) => e.state.kind === "loading") ? (
          <Skeleton style={{ height: "100%", width: "100%", borderRadius: "var(--radius-md)" }} />
        ) : !isMetricMode && chartData.rows.length === 0 ? (
          <Skeleton style={{ height: "100%", width: "100%", borderRadius: "var(--radius-md)" }} />
        ) : (
          <ResponsiveContainer width="100%" height="100%">
            <ComposedChart
              data={composedRows}
              margin={{ top: 8, right: 16, bottom: 8, left: 8 }}
              onMouseDown={handleChartMouseDown}
              onMouseMove={handleChartMouseMove}
              onMouseUp={handleChartMouseUp}
              onMouseLeave={handleChartMouseLeave}
              style={{ userSelect: "none" }}
            >
              <XAxis
                dataKey="t"
                tick={{ fontSize: 10, fill: "var(--text-tertiary)" }}
                axisLine={{ stroke: "var(--glass-border)" }}
                tickLine={false}
                height={22}
                minTickGap={40}
                tickFormatter={(t: string): string => {
                  try {
                    return format(parseISO(t), "MMM ''yy");
                  } catch {
                    return t;
                  }
                }}
              />
              <YAxis
                domain={["auto", "auto"]}
                tick={{ fontSize: 10, fill: "var(--text-tertiary)" }}
                axisLine={{ stroke: "var(--glass-border)" }}
                tickLine={false}
                width={54}
                tickFormatter={(v: number): string => {
                  if (isMetricMode) {
                    if (metric === "PE Ratio") return `${v.toFixed(0)}x`;
                    // Market Cap or Sales and Margin — values are already in ₹ Crore
                    return fmtCrAxis(v);
                  }
                  // Price mode: convert normalized 100-base index back to real ₹
                  if (tickers.length === 1) {
                    const baseline = chartData.baseline[tickers[0]!];
                    return baseline
                      ? `₹${((v / 100) * baseline).toFixed(0)}`
                      : v.toFixed(0);
                  }
                  // Comparison (2+ tickers): show % change from start of window
                  const delta = v - 100;
                  return `${delta >= 0 ? "+" : ""}${delta.toFixed(0)}%`;
                }}
              />
              <Tooltip
                content={
                  <GrowwTooltip metric={metric} rawPriceByDate={rawPriceByDate} />
                }
                cursor={{
                  stroke: "var(--text-tertiary)",
                  strokeWidth: 1,
                  strokeDasharray: "3 3",
                  opacity: 0.5,
                }}
              />
              {/* Drag-selection shading (price mode only) — Area rendered
                  BEFORE the Line series so the line draws on top. __selValue
                  is merged into composedRows (null outside [lo, hi]) so the
                  fill follows the primary curve and x-alignment is guaranteed. */}
              {selectionInfo && (
                <Area
                  dataKey="__selValue"
                  type="linear"
                  fill={
                    selectionInfo.deltaAbs === null || selectionInfo.deltaAbs >= 0
                      ? "var(--color-profit)"
                      : "var(--color-loss)"
                  }
                  fillOpacity={0.18}
                  stroke="none"
                  connectNulls={false}
                  isAnimationActive={false}
                  dot={false}
                  activeDot={false}
                  legendType="none"
                />
              )}
              {/* Thin dashed boundary lines at the selection edges */}
              {selectionInfo && (
                <ReferenceArea
                  x1={selectionInfo.loLabel}
                  x2={selectionInfo.loLabel}
                  fill="none"
                  stroke={
                    selectionInfo.deltaAbs === null || selectionInfo.deltaAbs >= 0
                      ? "var(--color-profit)"
                      : "var(--color-loss)"
                  }
                  strokeOpacity={0.4}
                  strokeDasharray="3 3"
                  ifOverflow="hidden"
                />
              )}
              {selectionInfo && selectionInfo.loLabel !== selectionInfo.hiLabel && (
                <ReferenceArea
                  x1={selectionInfo.hiLabel}
                  x2={selectionInfo.hiLabel}
                  fill="none"
                  stroke={
                    selectionInfo.deltaAbs === null || selectionInfo.deltaAbs >= 0
                      ? "var(--color-profit)"
                      : "var(--color-loss)"
                  }
                  strokeOpacity={0.4}
                  strokeDasharray="3 3"
                  ifOverflow="hidden"
                />
              )}
              {visibleTickers.map((sym) => (
                <Line
                  key={sym}
                  type="linear"
                  dataKey={sym}
                  name={sym}
                  stroke={colorFor(sym)}
                  strokeWidth={1.75}
                  dot={false}
                  activeDot={
                    drag?.isDragging
                      ? false
                      : {
                          r: 5,
                          fill: colorFor(sym),
                          stroke: "var(--bg-base)",
                          strokeWidth: 2,
                        }
                  }
                  connectNulls
                  isAnimationActive={false}
                />
              ))}
            </ComposedChart>
          </ResponsiveContainer>
        )}

        {/* Range-selection floating pill — price mode only. Shows absolute
            Δ + % for the dragged band. Hidden in metric mode since delta
            units are non-₹. */}
        {selectionInfo && pillLeftPct !== null && (
          <div
            aria-live="polite"
            aria-label="Selected range return"
            style={{
              position: "absolute",
              top: 6,
              left: `${pillLeftPct}%`,
              transform: "translateX(-50%)",
              pointerEvents: "none",
              zIndex: 10,
              display: "flex",
              flexDirection: "column",
              alignItems: "center",
              gap: 2,
            }}
          >
            {/* Main pill — Δ price + Δ % */}
            <div
              style={{
                display: "inline-flex",
                alignItems: "center",
                gap: 6,
                padding: "4px 10px",
                borderRadius: "var(--radius-pill)",
                background: "var(--bg-primary)",
                border: "1px solid var(--glass-border)",
                boxShadow: "0 2px 8px rgba(0,0,0,0.18)",
                fontFamily: "var(--font-mono)",
                fontSize: 12,
                fontWeight: 600,
                whiteSpace: "nowrap",
                color:
                  selectionInfo.deltaAbs === null || selectionInfo.deltaAbs >= 0
                    ? "var(--color-profit)"
                    : "var(--color-loss)",
              }}
            >
              {selectionInfo.deltaAbs !== null && selectionInfo.deltaPct !== null
                ? `${fmtDelta(selectionInfo.deltaAbs)} (${fmtPct(selectionInfo.deltaPct)})`
                : "—"}
            </div>
            {/* Date range sub-label */}
            <div
              style={{
                fontSize: 10,
                fontFamily: "var(--font-ui)",
                color: "var(--text-tertiary)",
                whiteSpace: "nowrap",
              }}
            >
              {fmtDateShort(selectionInfo.loLabel)} – {fmtDateShort(selectionInfo.hiLabel)}
            </div>
          </div>
        )}

        {/* End-label value tags — one per visible ticker, pinned to top-right.
            In price mode shows live LTP; in metric mode shows the raw value. */}
        {activeBaseRows.length > 0 && visibleTickers.length > 0 && (
          <div
            className="flex flex-col items-end"
            style={{
              position: "absolute",
              top: 14,
              right: 28,
              gap: 4,
              pointerEvents: "none",
            }}
          >
            {visibleTickers.map((sym) => {
              const v = endValues.get(sym);
              if (v == null) return null;
              const peerLtp = isMetricMode
                ? null
                : sym === primaryQuote?.symbol
                  ? primaryQuote.ltp
                  : peerQuotes[sym]?.ltp ?? null;
              return (
                <span
                  key={sym}
                  className="inline-flex items-center"
                  style={{
                    gap: 6,
                    padding: "3px 8px",
                    borderRadius: "var(--radius-xs)",
                    background: colorFor(sym),
                    color: "var(--bg-primary)",
                    fontFamily: "var(--font-mono)",
                    fontSize: 11,
                    fontWeight: 600,
                    whiteSpace: "nowrap",
                  }}
                  aria-label={`${sym} latest ${isMetricMode ? metric : "price"}`}
                >
                  {peerLtp !== null
                    ? INR.format(peerLtp)
                    : (metric === "Market Cap" || metric === "Sales and Margin")
                      ? fmtCrAxis(v)
                      : v.toFixed(2)}
                </span>
              );
            })}
          </div>
        )}
      </div>

      {/* ── Range pills below the chart — phone only, full width (scrolls if
          needed). On desktop the pills live in the controls row above. ── */}
      <div
        className="flex w-full sm:hidden"
        style={{
          padding: "12px 18px 0",
          overflowX: "auto",
          WebkitOverflowScrolling: "touch",
          scrollbarWidth: "none",
        }}
      >
        {rangePills}
      </div>

      {/* ── Footer summary box (price mode only), separated by a hairline ── */}
      {!isMetricMode && earliestDate && latestDate && summaries.length > 0 && (
        <div
          style={{
            marginTop: 14,
            padding: "14px 22px 18px",
            borderTop: "1px solid var(--glass-border)",
          }}
        >
          <div
            style={{
              fontSize: 12,
              color: "var(--text-secondary)",
              marginBottom: 8,
              fontFamily: "var(--font-ui)",
            }}
          >
            {fmtDateShort(earliestDate)} – {fmtDateShort(latestDate)} ({range.toLowerCase()})
          </div>
          <div className="flex flex-col" style={{ gap: 4 }}>
            {summaries.map((s) => {
              const peerQuote =
                s.symbol === primaryQuote?.symbol
                  ? primaryQuote
                  : peerQuotes[s.symbol];
              const peerName = peerQuote?.name ?? s.symbol;
              const totalPos = s.totalChg !== null && s.totalChg >= 0;
              return (
                <div
                  key={s.symbol}
                  className="flex items-center"
                  style={{ gap: 10, fontSize: 12, fontFamily: "var(--font-ui)" }}
                >
                  <span
                    aria-hidden="true"
                    style={{
                      width: 8,
                      height: 8,
                      borderRadius: "50%",
                      background: colorFor(s.symbol),
                      flexShrink: 0,
                    }}
                  />
                  <CompanyLogo
                    logoUrl={peerQuote?.logo_url}
                    name={peerName}
                    symbol={s.symbol}
                    size={18}
                  />
                  <span
                    style={{
                      color: "var(--text-primary)",
                      fontWeight: 500,
                      minWidth: 140,
                    }}
                  >
                    {peerName}
                  </span>
                  <span style={{ color: "var(--text-tertiary)" }}>
                    Total Chg{" "}
                    <span
                      className="tabular-nums"
                      style={{
                        color: s.totalChg === null
                          ? "var(--text-tertiary)"
                          : totalPos
                            ? "var(--color-profit)"
                            : "var(--color-loss)",
                        fontFamily: "var(--font-mono)",
                      }}
                    >
                      {s.totalChg === null ? "—" : fmtPct(s.totalChg)}
                    </span>
                  </span>
                  <span style={{ color: "var(--text-tertiary)" }}>
                    CAGR{" "}
                    <span
                      className="tabular-nums"
                      style={{
                        color: s.cagr === null
                          ? "var(--text-tertiary)"
                          : s.cagr >= 0
                            ? "var(--color-profit)"
                            : "var(--color-loss)",
                        fontFamily: "var(--font-mono)",
                      }}
                    >
                      {s.cagr === null ? "—" : fmtPct(s.cagr)}
                    </span>
                  </span>
                </div>
              );
            })}
          </div>
        </div>
      )}
    </Card>
    </>
  );
}

// ── Small chrome bits used inside the chart card ──────────────────────

// ── DatePicker (ported from frontend-quartr SipPreviewCard) ─────────────
// Calendar popover: trigger button shows the formatted date or
// placeholder; clicking opens a 7-column day grid with month nav,
// today outline, and selected highlight. Uses our token palette.

const MONTH_NAMES = [
  "January", "February", "March", "April", "May", "June",
  "July", "August", "September", "October", "November", "December",
];
const DOW_LABELS = ["Su", "Mo", "Tu", "We", "Th", "Fr", "Sa"];

function ymd(d: Date): string {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

function formatDateLabel(iso: string): string {
  if (!iso) return "";
  try {
    const d = new Date(iso);
    return d.toLocaleDateString("en-IN", {
      day: "2-digit",
      month: "short",
      year: "numeric",
    });
  } catch {
    return iso;
  }
}

function DateField({
  value,
  onChange,
  placeholder,
  "aria-label": ariaLabel,
  className,
}: {
  value: string;
  onChange: (v: string) => void;
  placeholder: string;
  "aria-label"?: string;
  /** Width control from the caller (e.g. `flex-1 sm:w-32` so the field is
   *  fluid on phone and a fixed 128px on desktop). The button fills it. */
  className?: string;
}): React.ReactElement {
  const [open, setOpen] = useState(false);
  // "days" = month grid (default), "years" = 12-cell year picker.
  // Clicking the year label in the header swaps to "years"; picking
  // a year drops back to "days" at the same month.
  const [view, setView] = useState<"days" | "years">("days");
  // Top of the year-grid window. Starts at cursor.year - 6 so the
  // current year sits roughly in the middle.
  const [yearGridStart, setYearGridStart] = useState<number>(0);
  const wrapperRef = useRef<HTMLDivElement | null>(null);
  const initial = value ? new Date(value) : new Date();
  const [cursor, setCursor] = useState({
    year: initial.getFullYear(),
    month: initial.getMonth(),
  });

  // Re-anchor month cursor on the chosen value when the popover opens.
  useEffect(() => {
    if (!open) return;
    const d = value ? new Date(value) : new Date();
    setCursor({ year: d.getFullYear(), month: d.getMonth() });
    setView("days");
  }, [open, value]);

  // Whenever we enter the year view, recenter the 12-cell window
  // around the current cursor year.
  useEffect(() => {
    if (view !== "years") return;
    setYearGridStart(cursor.year - 6);
  }, [view, cursor.year]);

  // Click-outside to dismiss
  useEffect(() => {
    if (!open) return;
    const onDocClick = (e: MouseEvent): void => {
      if (wrapperRef.current && !wrapperRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    };
    document.addEventListener("mousedown", onDocClick);
    return () => document.removeEventListener("mousedown", onDocClick);
  }, [open]);

  const cells = useMemo(() => {
    const first = new Date(cursor.year, cursor.month, 1);
    const lead = first.getDay();
    const start = new Date(cursor.year, cursor.month, 1 - lead);
    const out: { date: Date; key: string; inMonth: boolean }[] = [];
    for (let i = 0; i < 42; i++) {
      const d = new Date(start);
      d.setDate(start.getDate() + i);
      out.push({
        date: d,
        key: ymd(d),
        inMonth: d.getMonth() === cursor.month,
      });
    }
    return out;
  }, [cursor]);

  const goPrev = (): void =>
    setCursor((c) => {
      const m = c.month - 1;
      return m < 0 ? { year: c.year - 1, month: 11 } : { year: c.year, month: m };
    });
  const goNext = (): void =>
    setCursor((c) => {
      const m = c.month + 1;
      return m > 11 ? { year: c.year + 1, month: 0 } : { year: c.year, month: m };
    });

  const todayKey = ymd(new Date());
  const hasValue = value.length > 0;
  const label = hasValue ? formatDateLabel(value) : placeholder;

  return (
    <div ref={wrapperRef} className={className} style={{ position: "relative" }}>
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-haspopup="dialog"
        aria-expanded={open}
        aria-label={ariaLabel}
        style={{
          width: "100%",
          display: "inline-flex",
          alignItems: "center",
          justifyContent: "space-between",
          gap: 8,
          height: 38,
          padding: "0 12px",
          background: "var(--bg-base)",
          border: `1px solid ${open ? "var(--glass-border-focus, var(--text-secondary))" : "var(--glass-border)"}`,
          borderRadius: "var(--radius-sm)",
          fontFamily: "var(--font-ui)",
          fontSize: 12,
          color: hasValue ? "var(--text-primary)" : "var(--text-tertiary)",
          cursor: "pointer",
          outline: "none",
          transition: "border-color 0.18s var(--ease-quartr)",
        }}
      >
        <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
          {label}
        </span>
        <ChevronDown
          size={14}
          strokeWidth={2}
          style={{
            color: "var(--text-tertiary)",
            transform: open ? "rotate(180deg)" : "none",
            transition: "transform 0.18s var(--ease-quartr)",
            flexShrink: 0,
          }}
          aria-hidden="true"
        />
      </button>

      {open && (
        <div
          role="dialog"
          style={{
            position: "absolute",
            top: "calc(100% + 6px)",
            left: 0,
            zIndex: 20,
            width: 244,
            padding: 12,
            background: "var(--bg-primary)",
            border: "1px solid var(--glass-border)",
            borderRadius: "var(--radius-md)",
            boxShadow: "0 12px 30px rgba(0,0,0,0.25)",
          }}
        >
          <div
            style={{
              display: "flex",
              alignItems: "center",
              justifyContent: "space-between",
              marginBottom: 8,
            }}
          >
            <CalNavBtn
              onClick={
                view === "days"
                  ? goPrev
                  : () => setYearGridStart((s) => s - 12)
              }
              label={
                view === "days" ? "Previous month" : "Previous 12 years"
              }
            >
              <ChevronLeft size={14} strokeWidth={2} aria-hidden="true" />
            </CalNavBtn>

            {/* Header label: month + year in days view, or the year-range
                in years view. Clicking the year (days view) opens the year
                grid; clicking the range (years view) closes it. */}
            {view === "days" ? (
              <div
                style={{
                  display: "inline-flex",
                  alignItems: "center",
                  gap: 6,
                  fontFamily: "var(--font-ui)",
                  fontWeight: 500,
                  fontSize: 13,
                  color: "var(--text-primary)",
                  letterSpacing: "-0.01em",
                }}
              >
                <span>{MONTH_NAMES[cursor.month]}</span>
                <button
                  type="button"
                  onClick={() => setView("years")}
                  aria-label="Pick year"
                  style={{
                    background: "transparent",
                    border: "none",
                    padding: "2px 6px",
                    borderRadius: "var(--radius-xs)",
                    color: "var(--text-primary)",
                    fontFamily: "var(--font-ui)",
                    fontWeight: 500,
                    fontSize: 13,
                    letterSpacing: "-0.01em",
                    cursor: "pointer",
                    transition: "background-color 0.15s var(--ease-quartr)",
                  }}
                  onMouseEnter={(e) => {
                    e.currentTarget.style.background = "var(--surface-hover)";
                  }}
                  onMouseLeave={(e) => {
                    e.currentTarget.style.background = "transparent";
                  }}
                >
                  {cursor.year}
                </button>
              </div>
            ) : (
              <button
                type="button"
                onClick={() => setView("days")}
                aria-label="Back to month view"
                style={{
                  background: "transparent",
                  border: "none",
                  padding: "2px 6px",
                  borderRadius: "var(--radius-xs)",
                  color: "var(--text-primary)",
                  fontFamily: "var(--font-ui)",
                  fontWeight: 500,
                  fontSize: 13,
                  letterSpacing: "-0.01em",
                  cursor: "pointer",
                  transition: "background-color 0.15s var(--ease-quartr)",
                }}
                onMouseEnter={(e) => {
                  e.currentTarget.style.background = "var(--surface-hover)";
                }}
                onMouseLeave={(e) => {
                  e.currentTarget.style.background = "transparent";
                }}
              >
                {yearGridStart} – {yearGridStart + 11}
              </button>
            )}

            <CalNavBtn
              onClick={
                view === "days"
                  ? goNext
                  : () => setYearGridStart((s) => s + 12)
              }
              label={
                view === "days" ? "Next month" : "Next 12 years"
              }
            >
              <ChevronRight size={14} strokeWidth={2} aria-hidden="true" />
            </CalNavBtn>
          </div>

          {view === "days" && (
            <>
              <div
                style={{
                  display: "grid",
                  gridTemplateColumns: "repeat(7, 1fr)",
                  gap: 2,
                  marginBottom: 4,
                }}
              >
                {DOW_LABELS.map((d) => (
                  <div
                    key={d}
                    style={{
                      textAlign: "center",
                      fontSize: 10,
                      fontFamily: "var(--font-ui)",
                      color: "var(--text-tertiary)",
                      fontWeight: 500,
                      padding: "4px 0",
                      letterSpacing: "0.04em",
                    }}
                  >
                    {d}
                  </div>
                ))}
              </div>

              <div
                style={{
                  display: "grid",
                  gridTemplateColumns: "repeat(7, 1fr)",
                  gap: 2,
                }}
              >
                {cells.map((c) => {
                  const selected = c.key === value;
                  const today = c.key === todayKey;
                  return (
                    <button
                      key={c.key}
                      type="button"
                      onClick={() => {
                        onChange(c.key);
                        setOpen(false);
                      }}
                      style={{
                        height: 28,
                        background: selected ? "var(--text-primary)" : "transparent",
                        border:
                          today && !selected
                            ? "1px solid var(--glass-border-focus, var(--text-secondary))"
                            : "1px solid transparent",
                        borderRadius: "var(--radius-sm)",
                        color: selected
                          ? "var(--bg-primary)"
                          : c.inMonth
                            ? "var(--text-primary)"
                            : "var(--text-tertiary)",
                        fontFamily: "var(--font-ui)",
                        fontSize: 12,
                        fontWeight: selected ? 600 : 500,
                        cursor: "pointer",
                        transition:
                          "background-color 0.15s var(--ease-quartr), color 0.15s var(--ease-quartr)",
                      }}
                      onMouseEnter={(e) => {
                        if (!selected)
                          e.currentTarget.style.background = "var(--surface-hover)";
                      }}
                      onMouseLeave={(e) => {
                        if (!selected)
                          e.currentTarget.style.background = "transparent";
                      }}
                    >
                      {c.date.getDate()}
                    </button>
                  );
                })}
              </div>
            </>
          )}

          {view === "years" && (
            <div
              style={{
                display: "grid",
                gridTemplateColumns: "repeat(3, 1fr)",
                gap: 4,
              }}
            >
              {Array.from({ length: 12 }).map((_, i) => {
                const yr = yearGridStart + i;
                const thisYear = new Date().getFullYear();
                // Future years are not selectable — historical price
                // data only exists up to today, so anything past the
                // current year is rendered as a disabled placeholder.
                const isFuture = yr > thisYear;
                const selected = yr === cursor.year;
                const isToday = yr === thisYear;
                return (
                  <button
                    key={yr}
                    type="button"
                    disabled={isFuture}
                    onClick={() => {
                      if (isFuture) return;
                      setCursor((c) => ({ ...c, year: yr }));
                      setView("days");
                    }}
                    style={{
                      height: 40,
                      background: selected ? "var(--text-primary)" : "transparent",
                      border:
                        isToday && !selected
                          ? "1px solid var(--glass-border-focus, var(--text-secondary))"
                          : "1px solid transparent",
                      borderRadius: "var(--radius-sm)",
                      color: isFuture
                        ? "var(--text-tertiary)"
                        : selected
                          ? "var(--bg-primary)"
                          : "var(--text-primary)",
                      fontFamily: "var(--font-ui)",
                      fontSize: 12.5,
                      fontWeight: selected ? 600 : 500,
                      cursor: isFuture ? "not-allowed" : "pointer",
                      opacity: isFuture ? 0.4 : 1,
                      transition:
                        "background-color 0.15s var(--ease-quartr), color 0.15s var(--ease-quartr)",
                    }}
                    onMouseEnter={(e) => {
                      if (!selected && !isFuture)
                        e.currentTarget.style.background = "var(--surface-hover)";
                    }}
                    onMouseLeave={(e) => {
                      if (!selected && !isFuture)
                        e.currentTarget.style.background = "transparent";
                    }}
                  >
                    {yr}
                  </button>
                );
              })}
            </div>
          )}

          {hasValue && (
            <button
              type="button"
              onClick={() => {
                onChange("");
                setOpen(false);
              }}
              style={{
                marginTop: 10,
                width: "100%",
                padding: "6px 10px",
                background: "transparent",
                border: "1px solid var(--glass-border)",
                borderRadius: "var(--radius-sm)",
                color: "var(--text-secondary)",
                fontFamily: "var(--font-ui)",
                fontSize: 11.5,
                cursor: "pointer",
                transition:
                  "color 0.18s var(--ease-quartr), border-color 0.18s var(--ease-quartr)",
              }}
              onMouseEnter={(e) => {
                e.currentTarget.style.color = "var(--text-primary)";
              }}
              onMouseLeave={(e) => {
                e.currentTarget.style.color = "var(--text-secondary)";
              }}
            >
              Clear
            </button>
          )}
        </div>
      )}
    </div>
  );
}

function CalNavBtn({
  children,
  onClick,
  label,
}: {
  children: React.ReactNode;
  onClick: () => void;
  label: string;
}): React.ReactElement {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-label={label}
      style={{
        width: 24,
        height: 24,
        display: "inline-flex",
        alignItems: "center",
        justifyContent: "center",
        background: "transparent",
        border: "none",
        borderRadius: "var(--radius-sm)",
        color: "var(--text-secondary)",
        cursor: "pointer",
        transition:
          "color 0.2s var(--ease-quartr), background-color 0.2s var(--ease-quartr)",
      }}
      onMouseEnter={(e) => {
        e.currentTarget.style.color = "var(--text-primary)";
        e.currentTarget.style.background = "var(--surface-hover)";
      }}
      onMouseLeave={(e) => {
        e.currentTarget.style.color = "var(--text-secondary)";
        e.currentTarget.style.background = "transparent";
      }}
    >
      {children}
    </button>
  );
}

function MetricSelector({
  value,
  onChange,
}: {
  value: Metric;
  onChange: (m: Metric) => void;
}): React.ReactElement {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement | null>(null);

  // Click-outside to dismiss
  useEffect(() => {
    if (!open) return;
    const onDocClick = (e: MouseEvent): void => {
      if (ref.current && !ref.current.contains(e.target as Node)) {
        setOpen(false);
      }
    };
    document.addEventListener("mousedown", onDocClick);
    return () => document.removeEventListener("mousedown", onDocClick);
  }, [open]);

  return (
    <div ref={ref} style={{ position: "relative" }}>
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-haspopup="listbox"
        aria-expanded={open}
        className="inline-flex w-full items-center sm:w-auto"
        style={{
          gap: 8,
          height: 38,
          padding: "0 14px",
          background: "var(--bg-base)",
          border: "1px solid var(--glass-border)",
          borderRadius: "var(--radius-sm)",
          color: "var(--text-primary)",
          fontFamily: "var(--font-ui)",
          fontSize: 12,
          cursor: "pointer",
          minWidth: 124,
          justifyContent: "space-between",
        }}
      >
        {value}
        <ChevronDown
          size={14}
          strokeWidth={2}
          style={{
            color: "var(--text-tertiary)",
            transform: open ? "rotate(180deg)" : "rotate(0deg)",
            transition: "transform 0.18s var(--ease-quartr)",
          }}
          aria-hidden="true"
        />
      </button>

      {open && (
        <ul
          role="listbox"
          style={{
            position: "absolute",
            top: "calc(100% + 6px)",
            right: 0,
            margin: 0,
            padding: 6,
            listStyle: "none",
            background: "var(--bg-primary)",
            border: "1px solid var(--glass-border)",
            borderRadius: "var(--radius-sm)",
            boxShadow: "0 8px 24px rgba(0,0,0,0.18)",
            minWidth: 180,
            zIndex: 10,
          }}
        >
          {METRIC_OPTIONS.map((m) => {
            const active = m === value;
            return (
              <li key={m} role="option" aria-selected={active}>
                <button
                  type="button"
                  onClick={() => {
                    onChange(m);
                    setOpen(false);
                  }}
                  className="flex items-center w-full"
                  style={{
                    width: "100%",
                    padding: "8px 12px",
                    background: active ? "var(--surface-active)" : "transparent",
                    border: "none",
                    borderRadius: "var(--radius-xs)",
                    color: "var(--text-primary)",
                    fontFamily: "var(--font-ui)",
                    fontSize: 12.5,
                    cursor: "pointer",
                    textAlign: "left",
                    transition: "background-color 0.15s var(--ease-quartr)",
                  }}
                  onMouseEnter={(e) => {
                    if (!active) e.currentTarget.style.background = "var(--surface-hover)";
                  }}
                  onMouseLeave={(e) => {
                    if (!active) e.currentTarget.style.background = "transparent";
                  }}
                >
                  {m}
                </button>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}

function CompareChip({
  symbol,
  color,
  removable,
  onRemove,
}: {
  symbol: string;
  color: string;
  removable: boolean;
  onRemove: () => void;
}): React.ReactElement {
  return (
    <span
      className="inline-flex items-center"
      style={{
        gap: 4,
        padding: "3px 4px 3px 10px",
        background: `${color}26`,
        borderRadius: "var(--radius-sm)",
        color,
        fontFamily: "var(--font-ui)",
        fontSize: 12,
        fontWeight: 500,
        letterSpacing: "-0.005em",
        whiteSpace: "nowrap",
      }}
    >
      {symbol}
      {removable && (
        <button
          type="button"
          onClick={onRemove}
          aria-label={`Remove ${symbol}`}
          className="inline-flex items-center justify-center"
          style={{
            width: 16,
            height: 16,
            background: "transparent",
            border: "none",
            color,
            cursor: "pointer",
            opacity: 0.7,
            padding: 0,
          }}
          onMouseEnter={(e) => { e.currentTarget.style.opacity = "1"; }}
          onMouseLeave={(e) => { e.currentTarget.style.opacity = "0.7"; }}
        >
          <X size={12} strokeWidth={2.5} aria-hidden="true" />
        </button>
      )}
    </span>
  );
}

function fmtDateShort(iso: string): string {
  try {
    return format(parseISO(iso), "dd MMM, yyyy");
  } catch {
    return iso;
  }
}

// ---------------------------------------------------------------------------
// Bottom row — Financials | Profit & Loss | <Company> Stock News
// ---------------------------------------------------------------------------

/** Five most recent fiscal years used as the column header for the
 *  financials and P&L tables. Computed once at module load so all
 *  cells share the same axis. */
const FY_YEARS: string[] = (() => {
  const y = new Date().getFullYear();
  return [y - 4, y - 3, y - 2, y - 1, y].map((n) => `FY${String(n).slice(2)}`);
})();

/** Both Financials and P&L pad to this row count so the boxes
 *  always align at the bottom regardless of metric mix. The P&L
 *  walkdown has 7 rows currently (Revenue → COGS → Gross Profit →
 *  Opex → Operating Income → Tax → Net Income), so we anchor on 7. */
const SHARED_TABLE_ROWS = 7;

type FinancialRow = { label: string; values: (string | null)[] };

/** Deterministic mock generator so each symbol shows a stable but
 *  symbol-specific set of figures. Real wiring will swap this for the
 *  fundamentals service. */
function mulberry32(seed: number): () => number {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

function symbolSeed(s: string): number {
  let h = 2166136261;
  for (let i = 0; i < s.length; i++) {
    h ^= s.charCodeAt(i);
    h = Math.imul(h, 16777619);
  }
  return h >>> 0;
}

function buildProfitLoss(quote: StockQuote): FinancialRow[] {
  const rng = mulberry32(symbolSeed(quote.symbol) ^ 0xa1b2c3d4);
  const latestRevenue = Math.max(
    quote.market_cap !== null ? quote.market_cap * 0.012 : 1.5e11,
    5e10,
  );
  const growth = 0.07 + rng() * 0.08;
  const revenues = FY_YEARS.map((_, i) => {
    const yearsBack = FY_YEARS.length - 1 - i;
    return latestRevenue / Math.pow(1 + growth, yearsBack);
  });
  const cogsPct = 0.55 + rng() * 0.1;
  const opexPct = 0.18 + rng() * 0.05;
  const taxPct = 0.22 + rng() * 0.05;

  const grossProfit = revenues.map((r) => r * (1 - cogsPct));
  const ebit       = revenues.map((r) => r * (1 - cogsPct - opexPct));
  const tax        = ebit.map((e) => e * taxPct);
  const netProfit  = ebit.map((e) => e * (1 - taxPct));

  return [
    { label: "Revenue",            values: revenues.map((r) => fmtCr(r)) },
    { label: "Cost of Goods Sold", values: revenues.map((r) => fmtCr(r * cogsPct)) },
    { label: "Gross Profit",       values: grossProfit.map((v) => fmtCr(v)) },
    { label: "Operating Expenses", values: revenues.map((r) => fmtCr(r * opexPct)) },
    { label: "Tax Expense",        values: tax.map((v) => fmtCr(v)) },
    { label: "Net Profit",         values: netProfit.map((v) => fmtCr(v)) },
  ];
}

function FinancialsTable({
  quote,
  minRows,
  financials,
}: {
  quote: StockQuote;
  minRows: number;
  financials: FinancialsResponse | null;
}): React.ReactElement {
  const rows = useMemo(() => {
    if (financials?.available) return buildBalanceSheetFromDB(financials);
    return buildBalanceSheetEstimate();
  }, [financials]);
  const source = financials?.available ? "Moneycontrol" : "placeholder";
  return (
    <FinancialsLikeTable
      title="Balance Sheet"
      subtitle={source}
      rows={rows}
      minRows={minRows}
    />
  );
}

function ProfitLossTable({
  quote,
  minRows,
  financials,
}: {
  quote: StockQuote;
  minRows: number;
  financials: FinancialsResponse | null;
}): React.ReactElement {
  const rows = useMemo(() => {
    if (financials?.available) return buildProfitLossFromDB(financials);
    return buildProfitLoss(quote);
  }, [quote, financials]);
  const source = financials?.available ? "Moneycontrol" : "placeholder";
  return (
    <FinancialsLikeTable
      title="Profit and Loss"
      subtitle={source}
      rows={rows}
      minRows={minRows}
    />
  );
}

// ── Real-data builders ────────────────────────────────────────────────
// Convert /api/financials/{symbol} response into the FinancialRow shape
// the existing table renderer expects. We display the last 5 fiscal
// years, descending (most recent on the left).

function fmtCrFromMC(valueInCr: number | null): string {
  if (valueInCr === null) return "—";
  // MC publishes most P&L lines in Rs. Cr already, so we forward as-is.
  return fmtCr(valueInCr * 1e7); // fmtCr expects rupees → ₹Cr handled inside
}

function yearLabel(periodEnd: string | null): string {
  if (!periodEnd) return "—";
  const y = periodEnd.slice(0, 4);
  return `FY${y.slice(2)}`;
}

// Shared period picker — match a history series to a fiscal-year label so
// every series aligns to the FY_YEARS header (oldest → newest). No header
// row is injected; the table renders the years itself.
function pickByFY(
  arr: FinancialsHistoryPoint[],
  fy: string,
): number | null {
  const hit = arr.find((r) => r.period_end && yearLabel(r.period_end) === fy);
  return hit?.value ?? null;
}

function buildBalanceSheetFromDB(f: FinancialsResponse): FinancialRow[] {
  const equity = f.history["total_equity"] ?? [];
  const reserves = f.history["reserves"] ?? [];
  const debt = f.history["total_debt"] ?? [];
  const bvps = f.history["book_value_per_share"] ?? [];

  return [
    {
      label: "Total Equity",
      values: FY_YEARS.map((fy) => fmtCrFromMC(pickByFY(equity, fy))),
    },
    {
      label: "Reserves",
      values: FY_YEARS.map((fy) => fmtCrFromMC(pickByFY(reserves, fy))),
    },
    {
      label: "Total Debt",
      values: FY_YEARS.map((fy) => fmtCrFromMC(pickByFY(debt, fy))),
    },
    {
      label: "Book Value / Share",
      values: FY_YEARS.map((fy) => {
        const v = pickByFY(bvps, fy);
        return v === null ? "—" : `₹${v.toFixed(2)}`;
      }),
    },
  ];
}

// Estimated fallback for the Balance Sheet tab. We don't fabricate balance
// sheets when the financials DB has no data — the line items show as
// unavailable rather than inventing numbers.
function buildBalanceSheetEstimate(): FinancialRow[] {
  const dashes = FY_YEARS.map(() => "—");
  return [
    { label: "Total Equity", values: dashes },
    { label: "Reserves", values: dashes },
    { label: "Total Debt", values: dashes },
    { label: "Book Value / Share", values: dashes },
  ];
}

function buildProfitLossFromDB(f: FinancialsResponse): FinancialRow[] {
  const revenue = f.history["revenue"] ?? [];
  const op = f.history["operating_profit"] ?? [];
  const net = f.history["net_profit"] ?? [];
  const interest = f.history["interest_expense"] ?? [];
  const eps = f.history["eps_basic"] ?? [];

  return [
    {
      label: "Revenue",
      values: FY_YEARS.map((fy) => fmtCrFromMC(pickByFY(revenue, fy))),
    },
    {
      label: "Operating Profit",
      values: FY_YEARS.map((fy) => fmtCrFromMC(pickByFY(op, fy))),
    },
    {
      label: "Interest Expense",
      values: FY_YEARS.map((fy) => fmtCrFromMC(pickByFY(interest, fy))),
    },
    {
      label: "Net Profit",
      values: FY_YEARS.map((fy) => fmtCrFromMC(pickByFY(net, fy))),
    },
    {
      label: "EPS (₹)",
      values: FY_YEARS.map((fy) => {
        const v = pickByFY(eps, fy);
        return v === null ? "—" : v.toFixed(2);
      }),
    },
  ];
}

// ── Key Metrics tile strip ────────────────────────────────────────────
// Eight at-a-glance ratios from the latest fiscal snapshot. Each tile
// is a tiny stat with the named metric, its value, and the period.
// Skipped entirely when the symbol has no MC entry — the page falls
// back to its existing chart + placeholder tables.

const _METRIC_TILES: Array<{ key: string; label: string; suffix?: string; decimals?: number }> = [
  { key: "roe",            label: "ROE",            suffix: "%", decimals: 2 },
  { key: "roce",           label: "ROCE",           suffix: "%", decimals: 2 },
  { key: "roa",            label: "ROA",            suffix: "%", decimals: 2 },
  { key: "debt_to_equity", label: "D/E",            suffix: "x", decimals: 2 },
  { key: "current_ratio",  label: "Current Ratio",  suffix: "x", decimals: 2 },
  { key: "ev_to_ebitda",   label: "EV/EBITDA",      suffix: "x", decimals: 2 },
  { key: "price_to_book",  label: "P/B",            suffix: "x", decimals: 2 },
  { key: "net_profit_margin", label: "Net Margin",  suffix: "%", decimals: 2 },
];

/** Honest provenance label for a set of financial values — yfinance-filled
 *  metrics must not read "Moneycontrol". */
function sourceLabel(sources: (string | null | undefined)[]): string {
  const set = new Set(sources.filter(Boolean));
  const mc = set.has("moneycontrol");
  const yf = set.has("yfinance");
  if (mc && yf) return "Moneycontrol + yfinance";
  if (yf) return "yfinance";
  if (mc) return "Moneycontrol";
  return "—";
}

function KeyMetricsStrip({
  financials,
}: {
  financials: FinancialsResponse;
}): React.ReactElement {
  const period = (() => {
    // All tiles come from the same fiscal year — surface it once.
    for (const t of _METRIC_TILES) {
      const v = financials.latest[t.key];
      if (v) return v.period_label;
    }
    return null;
  })();
  const metricSource = sourceLabel(
    _METRIC_TILES.map((t) => financials.latest[t.key]?.source),
  );

  return (
    // Horizontal padding matches the Financial Performance panel below so the
    // heading + tiles line up with it (instead of sitting flush-left).
    <div style={{ marginTop: 36, padding: "0 20px" }}>
      <div
        style={{
          display: "flex",
          alignItems: "baseline",
          justifyContent: "space-between",
          marginBottom: 10,
        }}
      >
        <h2
          className="m-0"
          style={{
            fontFamily: "var(--font-ui)",
            fontSize: 14,
            fontWeight: 600,
            letterSpacing: "-0.01em",
            color: "var(--text-primary)",
          }}
        >
          Key Metrics
        </h2>
        {period && (
          <span style={{ fontSize: 11, color: "var(--text-tertiary)" }}>
            As of {period} · {metricSource}
          </span>
        )}
      </div>
      <div
        className="grid grid-cols-2 sm:grid-cols-4 lg:grid-cols-8"
        style={{ gap: 8 }}
      >
        {_METRIC_TILES.map((t) => {
          const v = financials.latest[t.key];
          return (
            <div
              key={t.key}
              style={{
                padding: "12px 14px",
                background: "var(--bg-secondary)",
                border: "none",
                borderRadius: "var(--radius-md)",
                display: "flex",
                flexDirection: "column",
                gap: 6,
                transition: "background-color 0.2s var(--ease-quartr)",
              }}
              onMouseEnter={(e) => {
                e.currentTarget.style.background = "var(--bg-elevated)";
              }}
              onMouseLeave={(e) => {
                e.currentTarget.style.background = "var(--bg-secondary)";
              }}
            >
              <div
                style={{
                  fontFamily: "var(--font-ui)",
                  fontSize: 12,
                  fontWeight: "var(--weight-medium)" as unknown as number,
                  color: "var(--text-primary)",
                }}
              >
                {t.label}
              </div>
              <div
                style={{
                  fontFamily: "var(--font-display)",
                  fontWeight: "var(--weight-medium)" as unknown as number,
                  fontSize: 15,
                  color: "var(--text-primary)",
                  fontVariantNumeric: "tabular-nums",
                  letterSpacing: "-0.01em",
                }}
              >
                {v && v.value !== null
                  ? `${v.value.toFixed(t.decimals ?? 2)}${t.suffix ?? ""}`
                  : "—"}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// FinancialsPanel — bar chart (left) + data table (right)
// ---------------------------------------------------------------------------

type FinPanelTab = "financials" | "pl";

function parseFinVal(v: string | null | undefined): number | null {
  if (!v || v === "—") return null;
  const num = parseFloat(v.replace(/[^0-9.\-]/g, ""));
  if (Number.isNaN(num)) return null;
  // Re-apply the magnitude fmtCr() encoded as a suffix, normalised to ₹ Cr,
  // so series with different suffixes (L Cr / K Cr / Cr) stay on one
  // comparable scale — otherwise "7.00 L Cr" and "67.57 K Cr" parse to bare
  // 7 vs 67.57 and the chart shows revenue as smaller than profit.
  if (/L\s*Cr/.test(v)) return num * 1e5; // lakh crore → crore
  if (/K\s*Cr/.test(v)) return num * 1e3; // thousand crore → crore
  if (/Cr/.test(v)) return num; // already crore
  return num; // plain number (EPS / %, not charted)
}

function fmtShort(n: number): string {
  if (Math.abs(n) >= 1e5) return `${(n / 1e5).toFixed(1)}L`;
  if (Math.abs(n) >= 1e3) return `${(n / 1e3).toFixed(1)}k`;
  return n.toFixed(0);
}

function FinBarChart({
  periods,
  metricA,
  metricB,
}: {
  periods: string[];
  metricA: { label: string; values: (number | null)[]; color: string };
  metricB: { label: string; values: (number | null)[]; color: string };
}): React.ReactElement {
  const [hover, setHover] = useState<number | null>(null);
  const allVals = [...metricA.values, ...metricB.values].filter((n): n is number => n !== null);
  const maxVal = allVals.length ? Math.max(...allVals) : 1;

  return (
    <div style={{ width: "100%" }}>
      {/* Legend */}
      <div style={{ display: "flex", gap: 16, marginBottom: 12 }}>
        {[metricA, metricB].map((m) => (
          <span key={m.label} style={{ display: "inline-flex", alignItems: "center", gap: 6, fontSize: 11, fontFamily: "var(--font-ui)", color: "var(--text-secondary)", fontWeight: 500, textTransform: "uppercase", letterSpacing: "0.05em" }}>
            <span style={{ width: 10, height: 10, borderRadius: "50%", background: m.color, flexShrink: 0 }} />
            {m.label} (Cr)
          </span>
        ))}
      </div>

      {/* Hover stats */}
      <div style={{ minHeight: 56, marginBottom: 8 }}>
        {hover !== null ? (
          <div>
            <div style={{ fontSize: 11, fontWeight: 600, color: "var(--text-tertiary)", textTransform: "uppercase", letterSpacing: "0.05em", marginBottom: 4 }}>
              {periods[hover]}
            </div>
            <div style={{ display: "flex", gap: 28 }}>
              {[metricA, metricB].map((m) => {
                const v = hover !== null ? (m.values[hover] ?? null) : null;
                return (
                  <div key={m.label}>
                    <span style={{ fontFamily: "var(--font-mono)", fontSize: 20, fontWeight: 700, color: "var(--text-primary)" }}>
                      ₹{v !== null ? fmtShort(v) : "—"}
                    </span>
                    <span style={{ fontSize: 10, color: "var(--text-tertiary)", fontFamily: "var(--font-ui)", marginLeft: 4 }}>{m.label}</span>
                  </div>
                );
              })}
            </div>
          </div>
        ) : (
          <div style={{ fontSize: 12, color: "var(--text-tertiary)", fontFamily: "var(--font-ui)" }}>
            Hover a bar to see details
          </div>
        )}
      </div>

      {/* Bar chart */}
      {/* Chart area — generous left padding keeps bars away from gridline labels */}
      <div style={{ position: "relative", height: 180, paddingRight: 32 }}>
        {/* Horizontal dotted gridlines + y-labels */}
        {[0.25, 0.5, 0.75, 1].map((f) => (
          <div key={f} style={{ position: "absolute", left: 0, right: 32, bottom: `${f * 100}%`, borderTop: "1px dotted var(--glass-border)", pointerEvents: "none" }}>
            <span style={{ position: "absolute", right: -30, top: -8, fontSize: 9, color: "var(--text-tertiary)", fontFamily: "var(--font-mono)" }}>
              {fmtShort(maxVal * f)}
            </span>
          </div>
        ))}

        {/* Bars row */}
        <div style={{ display: "flex", alignItems: "flex-end", gap: 18, height: "100%", position: "relative" }}>
          {periods.map((period, i) => {
            const aVal = metricA.values[i] ?? null;
            const bVal = metricB.values[i] ?? null;
            const aH = aVal !== null ? (aVal / maxVal) * 100 : 0;
            const bH = bVal !== null ? (bVal / maxVal) * 100 : 0;
            const isHov = hover === i;
            return (
              <div key={period} onMouseEnter={() => setHover(i)} onMouseLeave={() => setHover(null)}
                style={{ flex: 1, display: "flex", flexDirection: "column", alignItems: "center", height: "100%", cursor: "default" }}>
                <div style={{ flex: 1, width: "100%", display: "flex", alignItems: "flex-end", justifyContent: "center", gap: 4 }}>
                  {/* Revenue bar — thin, grey, darker on hover */}
                  <div style={{
                    width: 18,
                    height: `${aH}%`,
                    background: isHov ? "#475569" : "#cbd5e1",
                    borderRadius: "3px 3px 0 0",
                    transition: "background 180ms, height 300ms var(--ease-quartr)",
                    minHeight: aVal !== null ? 2 : 0,
                  }} />
                  {/* Profit bar — teal, always coloured */}
                  <div style={{
                    width: 18,
                    height: `${bH}%`,
                    background: metricB.color,
                    opacity: isHov ? 1 : 0.75,
                    borderRadius: "3px 3px 0 0",
                    transition: "opacity 180ms, height 300ms var(--ease-quartr)",
                    minHeight: bVal !== null ? 2 : 0,
                  }} />
                </div>
                <span style={{
                  fontSize: 10,
                  color: isHov ? "var(--text-primary)" : "var(--text-tertiary)",
                  fontFamily: "var(--font-ui)",
                  whiteSpace: "nowrap",
                  marginTop: 6,
                  fontWeight: isHov ? 600 : 400,
                  transition: "color 180ms",
                }}>
                  {period}
                </span>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}

function FinancialsPanel({
  quote,
  financials,
}: {
  quote: StockQuote;
  financials: FinancialsResponse | null;
}): React.ReactElement {
  const [tab, setTab] = useState<FinPanelTab>("financials");

  // `financials` tab = Balance Sheet, `pl` tab = Profit and Loss.
  const bsRows = useMemo(
    () => financials?.available ? buildBalanceSheetFromDB(financials) : buildBalanceSheetEstimate(),
    [financials],
  );
  const plRows = useMemo(
    () => financials?.available ? buildProfitLossFromDB(financials) : buildProfitLoss(quote),
    [quote, financials],
  );

  const rows = tab === "financials" ? bsRows : plRows;
  const source = financials?.available ? "Moneycontrol" : "Estimated";

  const getMetric = (label: string): (number | null)[] =>
    rows.find((r) => r.label === label)?.values.map(parseFinVal) ?? FY_YEARS.map(() => null);

  const cfg = tab === "financials"
    ? { a: "Total Equity", b: "Total Debt",  colorA: "#64748b", colorB: "#f59e0b" }
    : { a: "Revenue",      b: "Net Profit",  colorA: "#64748b", colorB: "#1b7cc7" };

  return (
    <div style={{ marginTop: 28 }}>
      <div style={{ background: "transparent", border: "none", borderRadius: "var(--radius-lg, 16px)", overflow: "hidden" }}>

        {/* Header: title row + tabs row */}
        <div style={{ padding: "18px 20px 0", borderBottom: "1px solid var(--glass-border)" }}>
          {/* Title + source badge */}
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 10 }}>
            <span style={{ fontFamily: "var(--font-ui)", fontSize: 14, fontWeight: 600, color: "var(--text-primary)" }}>
              Financial Performance
            </span>
          </div>
          {/* Tabs */}
          <div style={{ display: "flex", gap: 0 }}>
            {(["financials", "pl"] as const).map((t) => {
              const active = tab === t;
              return (
                <button key={t} type="button" onClick={() => setTab(t)} style={{
                  padding: "6px 14px", border: "none", background: "transparent",
                  fontSize: 12.5, fontFamily: "var(--font-ui)",
                  fontWeight: active ? 600 : 400,
                  color: active ? "var(--pivot-blue, #1b7cc7)" : "var(--text-secondary)",
                  borderBottom: active ? "2px solid var(--pivot-blue, #1b7cc7)" : "2px solid transparent",
                  cursor: "pointer", marginBottom: -1, transition: "color 0.15s, border-color 0.15s",
                }}>
                  {t === "financials" ? "Balance Sheet" : "Profit and Loss"}
                </button>
              );
            })}
          </div>
        </div>

        {/* Body: chart left | table right (table gets a bit more room) */}
        <div className="grid grid-cols-1 lg:grid-cols-[5fr_6fr]">

          {/* Left — bar chart */}
          <div style={{ padding: "24px 24px 20px", borderRight: "1px solid var(--glass-border)" }}>
            <FinBarChart
              periods={FY_YEARS}
              metricA={{ label: cfg.a, values: getMetric(cfg.a), color: cfg.colorA }}
              metricB={{ label: cfg.b, values: getMetric(cfg.b), color: cfg.colorB }}
            />
          </div>

          {/* Right — data table */}
          <div style={{ overflow: "hidden" }}>
            <table style={{ width: "100%", tableLayout: "fixed", borderCollapse: "collapse", fontFamily: "var(--font-ui)" }}>
              <thead>
                <tr style={{ background: "var(--bg-base, #f8fafc)", borderBottom: "1px solid var(--glass-border)" }}>
                  <th style={{ width: "26%", padding: "12px 12px", fontSize: 10.5, fontWeight: 600, textTransform: "uppercase", letterSpacing: "0.06em", color: "var(--text-tertiary)", textAlign: "left", whiteSpace: "nowrap" }}>
                    Metric
                  </th>
                  {FY_YEARS.map((y, i) => (
                    <th key={y} style={{
                      padding: "12px 8px", fontSize: 10.5, fontWeight: 600,
                      textTransform: "uppercase", letterSpacing: "0.06em",
                      textAlign: "right", whiteSpace: "nowrap",
                      color: i === FY_YEARS.length - 1 ? "var(--pivot-blue, #1b7cc7)" : "var(--text-tertiary)",
                    }}>
                      {y}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {rows.map((r, idx) => (
                  <tr key={r.label || idx}
                    style={{ borderBottom: idx < rows.length - 1 ? "1px solid var(--glass-border)" : "none", transition: "background 120ms" }}
                    onMouseEnter={(e) => { e.currentTarget.style.background = "var(--bg-base, #f8fafc)"; }}
                    onMouseLeave={(e) => { e.currentTarget.style.background = "transparent"; }}
                  >
                    <td style={{ padding: "11px 12px" }}>
                      <span style={{ display: "inline-flex", alignItems: "center", gap: 7 }}>
                        <span style={{
                          width: 7, height: 7, borderRadius: "50%", flexShrink: 0,
                          background: r.label === cfg.a ? cfg.colorA : r.label === cfg.b ? cfg.colorB : "transparent",
                        }} />
                        <span style={{ fontSize: 12, fontWeight: 500, color: "var(--text-primary)" }}>
                          {r.label}
                        </span>
                      </span>
                    </td>
                    {r.values.map((v, i) => (
                      <td key={i} className="tabular-nums" style={{
                        padding: "11px 8px", textAlign: "right",
                        fontSize: 11.5, fontFamily: "var(--font-mono)",
                        fontWeight: i === FY_YEARS.length - 1 ? 600 : 400,
                        color: i === FY_YEARS.length - 1 ? "var(--text-primary)" : "var(--text-secondary)",
                      }}>
                        {v ?? "—"}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      </div>
    </div>
  );
}


/** Mini bar sparkline — one bar per FY, scaled to max value in row. */
function RowSparkline({ values }: { values: (string | null)[] }): React.ReactElement {
  const nums = values.map((v) => {
    if (!v || v === "—") return null;
    const n = parseFloat(v.replace(/[₹,KCrL%x]/g, "").trim());
    return isNaN(n) ? null : n;
  });
  const valid = nums.filter((n): n is number => n !== null);
  if (valid.length === 0) return <span style={{ width: 56 }} />;
  const max = Math.max(...valid);
  const min = Math.min(0, Math.min(...valid));
  const span = max - min || 1;
  return (
    <span style={{ display: "inline-flex", alignItems: "flex-end", gap: 2, height: 22, width: 56, flexShrink: 0 }}>
      {nums.map((n, i) => {
        const h = n === null ? 2 : Math.max(2, ((n - min) / span) * 20);
        const isLast = i === nums.length - 1;
        return (
          <span key={i} style={{ flex: 1, height: h, borderRadius: 2,
            background: isLast ? "var(--pivot-blue, #1b7cc7)" : "var(--glass-border-hover, #cbd5e1)",
            opacity: n === null ? 0.2 : 1 }} />
        );
      })}
    </span>
  );
}

function FinancialsLikeTable({ title, subtitle, rows, minRows }: {
  title: string; subtitle?: string; rows: FinancialRow[]; minRows: number;
}): React.ReactElement {
  const paddedRows: (FinancialRow | null)[] = [...rows];
  while (paddedRows.length < minRows) paddedRows.push(null);
  const latestIdx = FY_YEARS.length - 1;

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 10 }}>
        <h2 className="m-0" style={{ fontFamily: "var(--font-ui)", fontSize: 14, fontWeight: 600, letterSpacing: "-0.01em", color: "var(--text-primary)" }}>
          {title}
        </h2>
        {subtitle && (
          <span style={{ fontSize: 10, fontWeight: 500, color: "var(--text-tertiary)", background: "var(--bg-elevated, #f1f5f9)", padding: "2px 7px", borderRadius: 99, letterSpacing: "0.02em", textTransform: "uppercase" }}>
            {subtitle}
          </span>
        )}
      </div>
      <div style={{ flex: 1, minHeight: 0, background: "var(--bg-primary)", borderRadius: "var(--radius-md)", overflow: "hidden", border: "1px solid var(--glass-border)" }}>
        <table style={{ width: "100%", borderCollapse: "collapse", fontFamily: "var(--font-ui)" }}>
          <thead>
            <tr style={{ borderBottom: "1px solid var(--glass-border)" }}>
              <th style={{ padding: "9px 14px", fontSize: 10.5, fontWeight: 600, textTransform: "uppercase", letterSpacing: "0.06em", color: "var(--text-tertiary)", textAlign: "left", whiteSpace: "nowrap" }}>
                Metric
              </th>
              <th style={{ padding: "9px 8px", width: 64 }} />
              {FY_YEARS.map((y, i) => {
                const isLatest = i === latestIdx;
                return (
                  <th key={y} style={{ padding: "9px 14px", fontSize: 10.5, fontWeight: 600, textTransform: "uppercase", letterSpacing: "0.06em", textAlign: "right", whiteSpace: "nowrap", color: isLatest ? "var(--pivot-blue, #1b7cc7)" : "var(--text-tertiary)" }}>
                    {y}
                  </th>
                );
              })}
            </tr>
          </thead>
          <tbody>
            {paddedRows.map((r, idx) => (
              <tr
                key={r ? r.label : `__pad_${idx}`}
                style={{ borderBottom: idx < paddedRows.length - 1 ? "1px solid var(--glass-border)" : "none", transition: "background 120ms" }}
                onMouseEnter={(e) => { e.currentTarget.style.background = "var(--bg-secondary, #f8fafc)"; }}
                onMouseLeave={(e) => { e.currentTarget.style.background = "transparent"; }}
              >
                <td style={{ padding: "10px 14px", fontSize: 12.5, fontWeight: 500, color: "var(--text-secondary)", whiteSpace: "nowrap" }}>
                  {r ? r.label : ""}
                </td>
                <td style={{ padding: "10px 8px", textAlign: "center" }}>
                  {r && <RowSparkline values={r.values} />}
                </td>
                {FY_YEARS.map((_, i) => {
                  const isLatest = i === latestIdx;
                  return (
                    <td key={i} className="tabular-nums" style={{ padding: "10px 14px", fontSize: 12.5, textAlign: "right", fontFamily: "var(--font-mono)", fontWeight: isLatest ? 600 : 400, color: isLatest ? "var(--text-primary)" : "var(--text-secondary)", whiteSpace: "nowrap" }}>
                      {r ? (r.values[i] ?? "—") : ""}
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}


const screenerTh: React.CSSProperties = {
  padding: "12px 16px",
  fontSize: 10,
  letterSpacing: "0.08em",
  textTransform: "uppercase",
  fontWeight: 500,
  whiteSpace: "nowrap",
  userSelect: "none",
  fontFamily: "var(--font-ui)",
};

const screenerTd: React.CSSProperties = {
  padding: "11px 16px",
  fontSize: 12.5,
  whiteSpace: "nowrap",
};

