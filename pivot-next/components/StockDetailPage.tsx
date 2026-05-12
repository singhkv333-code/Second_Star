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
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
  CartesianGrid,
} from "recharts";
import { format, parseISO } from "date-fns";
import {
  AlertCircle,
  Bookmark,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  Maximize2,
  Search,
  X,
} from "lucide-react";
import { Skeleton } from "@/components/ui/skeleton";
import {
  getStockQuote,
  getSparkline,
  type StockQuote,
  type SparklineRange,
  type SparklineResponse,
} from "@/lib/api";
import { isError } from "@/lib/types";

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

/** Distinct, color-blind-friendly palette for comparison series. The
 *  base ticker uses --color-profit (green); peers cycle through this
 *  list. Same hues used by the screener category column for continuity. */
const COMPARE_PALETTE = [
  "var(--color-profit)", // primary ticker = profit-green
  "#a78bfa", // violet
  "#f97316", // orange
  "#60a5fa", // blue
  "#ec4899", // pink
  "#facc15", // amber
];

// ---------------------------------------------------------------------------
// Formatters
// ---------------------------------------------------------------------------

const INR = new Intl.NumberFormat("en-IN", {
  style: "currency",
  currency: "INR",
  maximumFractionDigits: 2,
});

function fmtCr(n: number | null): string {
  if (n === null || n === undefined || Number.isNaN(n)) return "—";
  if (n >= 1e12) return `₹${(n / 1e12).toFixed(2)} L Cr`;
  if (n >= 1e9) return `₹${(n / 1e9).toFixed(2)} K Cr`;
  if (n >= 1e7) return `₹${(n / 1e7).toFixed(2)} Cr`;
  return INR.format(n);
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
// StockDetailPage
// ---------------------------------------------------------------------------

export function StockDetailPage({ symbol }: { symbol: string }): React.ReactElement {
  const [quoteState, setQuoteState] = useState<QuoteState>({ kind: "loading" });
  const [range, setRange] = useState<SparklineRange>("5Y");
  const [bookmarked, setBookmarked] = useState(false);

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

  // ── Sparkline series (one per ticker) ──────────────────────────────────
  useEffect(() => {
    let cancelled = false;
    setSeries(tickers.map((s) => ({ symbol: s, state: { kind: "loading" } })));
    Promise.all(
      tickers.map(async (s) => {
        const res = await getSparkline(s, range).catch(() => null);
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
          bookmarked={bookmarked}
          onToggleBookmark={() => setBookmarked((b) => !b)}
        />
      )}

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
            <MergedOverviewCard quote={quoteState.quote} />
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

      {/* Bottom block — Financials and P&L sit side-by-side with the
          screener's bordered-table look; News spans full width below.
          Both tables pad to the longer of the two so they share an
          identical rendered height. */}
      {quoteState.kind === "ok" && (
        <>
          <div
            className="grid grid-cols-1 lg:grid-cols-2"
            style={{ marginTop: 28, gap: 14 }}
          >
            <FinancialsTable
              quote={quoteState.quote}
              minRows={SHARED_TABLE_ROWS}
            />
            <ProfitLossTable
              quote={quoteState.quote}
              minRows={SHARED_TABLE_ROWS}
            />
          </div>
          <div style={{ marginTop: 14 }}>
            <StockNewsColumn quote={quoteState.quote} />
          </div>
        </>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Header — brand glyph, title with bookmark, price strip
// ---------------------------------------------------------------------------

function Header({
  quote,
  bookmarked,
  onToggleBookmark,
}: {
  quote: StockQuote;
  bookmarked: boolean;
  onToggleBookmark: () => void;
}): React.ReactElement {
  const positive = quote.change_pct >= 0;
  const initial = quote.name.trim()[0]?.toUpperCase() ?? quote.symbol[0]?.toUpperCase() ?? "•";
  const hue = brandGlyphHue(quote.sector);

  return (
    <div
      className="flex flex-wrap items-center"
      style={{ gap: 18 }}
      data-testid="quote-header"
    >
      {/* Brand glyph + name + bookmark */}
      <div className="flex items-center" style={{ gap: 14 }}>
        <div
          aria-hidden="true"
          className="flex shrink-0 items-center justify-center"
          style={{
            width: 56,
            height: 56,
            borderRadius: "var(--radius-md)",
            background: `${hue}22`, // 13% alpha tint
            border: `1px solid ${hue}55`,
            color: hue,
            fontFamily: "var(--font-ui)",
            fontSize: 24,
            fontWeight: 600,
            letterSpacing: "-0.02em",
          }}
        >
          {initial}
        </div>
        <div>
          <div className="flex items-center" style={{ gap: 8 }}>
            <h1
              className="m-0"
              style={{
                fontFamily: "var(--font-ui)",
                fontSize: 22,
                fontWeight: 600,
                letterSpacing: "-0.025em",
                color: "var(--text-primary)",
              }}
            >
              {quote.name}
            </h1>
            <button
              type="button"
              onClick={onToggleBookmark}
              aria-label={bookmarked ? "Remove bookmark" : "Add bookmark"}
              data-testid="bookmark-btn"
              className="inline-flex items-center justify-center"
              style={{
                width: 38,
                height: 38,
                background: "transparent",
                border: "none",
                borderRadius: "var(--radius-sm)",
                color: bookmarked ? "var(--text-primary)" : "var(--text-tertiary)",
                cursor: "pointer",
                transition: "color 0.2s var(--ease-quartr), background-color 0.2s var(--ease-quartr)",
              }}
              onMouseEnter={(e) => {
                e.currentTarget.style.background = "var(--surface-active)";
                e.currentTarget.style.color = "var(--text-primary)";
              }}
              onMouseLeave={(e) => {
                e.currentTarget.style.background = "transparent";
                e.currentTarget.style.color = bookmarked ? "var(--text-primary)" : "var(--text-tertiary)";
              }}
            >
              <Bookmark
                size={20}
                strokeWidth={2}
                fill={bookmarked ? "currentColor" : "none"}
                aria-hidden="true"
              />
            </button>
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

      {/* Price + day chip */}
      <div className="flex items-baseline" style={{ gap: 12, marginLeft: "auto" }}>
        <span
          className="tabular-nums"
          style={{
            fontFamily: "var(--font-ui)",
            fontSize: 28,
            fontWeight: 600,
            letterSpacing: "-0.02em",
            color: "var(--text-primary)",
          }}
        >
          {INR.format(quote.ltp)}
        </span>
        <span
          className="inline-flex items-center"
          style={{
            gap: 4,
            padding: "3px 10px",
            borderRadius: "var(--radius-xs)",
            background: positive
              ? "rgba(16, 185, 129, 0.16)"
              : "rgba(239, 68, 68, 0.16)",
            color: positive ? "var(--color-profit)" : "var(--color-loss)",
            fontFamily: "var(--font-mono)",
            fontSize: 12.5,
            fontWeight: 500,
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
  className,
  style,
}: {
  children: React.ReactNode;
  padding?: number | string;
  borderless?: boolean;
  className?: string;
  style?: React.CSSProperties;
}): React.ReactElement {
  return (
    <div
      className={className}
      style={{
        padding,
        background: "var(--bg-primary)",
        border: borderless ? "none" : "1px solid var(--glass-border)",
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
        fontSize: 13,
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
function MergedOverviewCard({ quote }: { quote: StockQuote }): React.ReactElement {
  const profile: { label: string; value: string }[] = [
    { label: "Market Cap", value: fmtCr(quote.market_cap) },
    { label: "52W High", value: INR.format(quote.week_52_high) },
    { label: "52W Low", value: INR.format(quote.week_52_low) },
    { label: "Volume", value: quote.volume.toLocaleString("en-IN") },
  ];
  const valuation: { label: string; value: string }[] = [
    { label: "P/E", value: quote.pe_ratio !== null ? quote.pe_ratio.toFixed(1) : "—" },
    { label: "P/B", value: "—" },
    { label: "EV/Sales", value: "—" },
    { label: "EV/EBITDA", value: "—" },
  ];
  const day: { label: string; value: string }[] = [
    { label: "Open", value: INR.format(quote.open) },
    { label: "High", value: INR.format(quote.high) },
    { label: "Low", value: INR.format(quote.low) },
    { label: "Prev Close", value: INR.format(quote.close) },
  ];

  return (
    <Card
      borderless
      padding="22px 24px"
      className="flex h-full min-h-0 flex-col overflow-y-auto"
    >
      <CompanyOverviewBody quote={quote} />

      {/* Stats — folded into the same card. No section header. Sits
          beneath the Year Founded row separated by a slim gap. */}
      <div
        className="grid grid-cols-1 sm:grid-cols-3"
        style={{ gap: 24, marginTop: 22 }}
      >
        <StatColumn title="Profile" rows={profile} />
        <StatColumn title="Valuation (TTM)" rows={valuation} />
        <StatColumn title="Day's Range" rows={day} />
      </div>
    </Card>
  );
}

function CompanyOverviewBody({ quote }: { quote: StockQuote }): React.ReactElement {
  const [expanded, setExpanded] = useState(false);
  const profile = COMPANY_PROFILES[quote.symbol.toUpperCase()];
  const blurb =
    profile?.blurb ??
    `${quote.name} is publicly listed on ${quote.exchange}. Detailed company description and operating segment breakdown will be loaded from the fundamentals service.`;

  // Match Fiscal.ai field set: Name / CEO / Website / Sector / Year Founded.
  // Anything we don't have falls back to "—" so the row spacing stays
  // consistent regardless of which symbol you land on.
  const facts: { label: string; value: React.ReactNode }[] = [
    { label: "Name", value: quote.name },
    { label: "CEO", value: profile?.ceo ?? "—" },
    {
      label: "Website",
      value: profile?.website ? (
        <a
          href={`https://${profile.website}`}
          target="_blank"
          rel="noopener noreferrer"
          style={{ color: "var(--text-primary)", textDecoration: "none" }}
        >
          {profile.website}
        </a>
      ) : (
        "—"
      ),
    },
    {
      label: "Sector",
      value: profile?.industry ?? quote.sector ?? "—",
    },
    { label: "Year Founded", value: profile?.yearFounded ?? "—" },
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
// Chart card — multi-line comparison
// ---------------------------------------------------------------------------

const METRIC_OPTIONS = [
  "Price",
  "PE Ratio",
  "EV/EBITDA",
  "Sales and Margin",
  "Market Cap",
] as const;
type Metric = (typeof METRIC_OPTIONS)[number];

function ChartCard({
  tickers,
  peerQuotes,
  series,
  range,
  onRangeChange,
  onAddPeer,
  onRemovePeer,
  primaryQuote,
}: {
  tickers: string[];
  peerQuotes: Record<string, StockQuote>;
  series: SeriesEntry[];
  range: SparklineRange;
  onRangeChange: (r: SparklineRange) => void;
  onAddPeer: (s: string) => void;
  onRemovePeer: (s: string) => void;
  primaryQuote: StockQuote | null;
}): React.ReactElement {
  const [searchValue, setSearchValue] = useState("");
  const [metric, setMetric] = useState<Metric>("Price");
  // Min/Max date filters (ISO yyyy-mm-dd from native <input type="date">).
  const [minDate, setMinDate] = useState<string>("");
  const [maxDate, setMaxDate] = useState<string>("");

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

  const handleSearchSubmit = (e: React.FormEvent): void => {
    e.preventDefault();
    if (!searchValue.trim()) return;
    onAddPeer(searchValue);
    setSearchValue("");
  };

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

  const earliestDate = chartData.rows[0]?.t as string | undefined;
  const latestDate = chartData.rows[chartData.rows.length - 1]?.t as string | undefined;

  // Last numeric value per ticker — used to render the price-tag labels
  // pinned to the right edge of the chart (Fiscal.ai pattern).
  const endValues = useMemo(() => {
    const map = new Map<string, number | null>();
    series.forEach((s) => {
      if (s.state.kind !== "ok") {
        map.set(s.symbol, null);
        return;
      }
      const last = s.state.data.points[s.state.data.points.length - 1]?.v ?? null;
      map.set(s.symbol, last);
    });
    return map;
  }, [series]);

  return (
    <Card
      borderless
      padding="0"
      className="flex h-full min-h-0 flex-col"
    >
      {/* ── Row 1: full-width search pill + maximize ──────────────────── */}
      <div
        className="flex items-center"
        style={{
          gap: 10,
          padding: "16px 18px 12px",
        }}
      >
        <form
          onSubmit={handleSearchSubmit}
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
          <input
            type="text"
            value={searchValue}
            onChange={(e) => setSearchValue(e.target.value.toUpperCase())}
            placeholder={tickers.length === 1 ? "Compare to…" : ""}
            aria-label="Add ticker to comparison"
            data-testid="compare-search"
            className="flex-1 outline-none"
            style={{
              minWidth: 80,
              background: "transparent",
              border: "none",
              color: "var(--text-primary)",
              fontFamily: "var(--font-ui)",
              fontSize: 13,
              letterSpacing: "-0.005em",
            }}
          />
        </form>
        <button
          type="button"
          aria-label="Expand chart"
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
          <Maximize2 size={14} strokeWidth={2} aria-hidden="true" />
        </button>
      </div>

      {/* ── Row 2: Min Date | range pills | Max Date | Price selector ── */}
      <div
        className="flex flex-wrap items-center"
        style={{
          gap: 10,
          padding: "0 18px 14px",
        }}
      >
        <DateField
          value={minDate}
          onChange={setMinDate}
          placeholder="Min Date"
          aria-label="Minimum date"
        />
        <div
          className="inline-flex"
          style={{
            gap: 2,
            padding: 2,
            background: "var(--bg-base)",
            border: "1px solid var(--glass-border)",
            borderRadius: "var(--radius-pill)",
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
                style={{
                  padding: "5px 12px",
                  border: "none",
                  borderRadius: "var(--radius-pill)",
                  fontFamily: "var(--font-ui)",
                  fontSize: 11.5,
                  fontWeight: 500,
                  cursor: "pointer",
                  background: active ? "var(--text-primary)" : "transparent",
                  color: active ? "var(--bg-primary)" : "var(--text-secondary)",
                  transition: "color 0.2s var(--ease-quartr), background-color 0.2s var(--ease-quartr)",
                }}
              >
                {r}
              </button>
            );
          })}
        </div>
        <DateField
          value={maxDate}
          onChange={setMaxDate}
          placeholder="Max Date"
          aria-label="Maximum date"
        />
        <MetricSelector value={metric} onChange={setMetric} />
      </div>

      {/* ── Chart with end-label price tags ───────────────────────────── */}
      <div style={{ position: "relative", width: "100%", height: 320, padding: "0 18px" }}>
        {chartData.rows.length === 0 ? (
          <Skeleton style={{ height: 320, width: "100%", borderRadius: "var(--radius-md)" }} />
        ) : (
          <ResponsiveContainer width="100%" height="100%">
            <LineChart
              data={chartData.rows}
              margin={{ top: 8, right: 56, bottom: 8, left: -8 }}
            >
              <CartesianGrid stroke="var(--glass-border)" vertical={false} />
              <XAxis
                dataKey="t"
                tick={{ fontSize: 10, fill: "var(--text-tertiary)" }}
                axisLine={false}
                tickLine={false}
                minTickGap={48}
                tickFormatter={(d: string) => {
                  try { return format(parseISO(d), "MMM yy"); } catch { return d; }
                }}
              />
              <YAxis
                domain={["auto", "auto"]}
                orientation="right"
                tick={{ fontSize: 10, fill: "var(--text-tertiary)", fontFamily: "var(--font-mono)" }}
                axisLine={false}
                tickLine={false}
                width={48}
                tickFormatter={(v: number) => `${Math.round(v)}`}
              />
              <Tooltip
                contentStyle={{
                  background: "var(--bg-primary)",
                  border: "1px solid var(--glass-border)",
                  borderRadius: 8,
                  fontSize: 11,
                  fontFamily: "var(--font-ui)",
                }}
                labelStyle={{ color: "var(--text-tertiary)", fontSize: 10 }}
                formatter={(value: number, name: string) => {
                  const n = Number(value);
                  if (Number.isNaN(n)) return [value, name];
                  return [`${n.toFixed(1)} (idx)`, name];
                }}
                labelFormatter={(d: string) => {
                  try { return format(parseISO(d), "d MMM yyyy"); } catch { return d; }
                }}
              />
              {tickers.map((sym) => (
                <Line
                  key={sym}
                  type="monotone"
                  dataKey={sym}
                  name={sym}
                  stroke={colorFor(sym)}
                  strokeWidth={1.75}
                  dot={false}
                  connectNulls
                  isAnimationActive={false}
                />
              ))}
            </LineChart>
          </ResponsiveContainer>
        )}

        {/* End-label price tags — one per ticker, pinned to top-right
            stack. Stacked rather than precisely y-positioned because
            Recharts' coordinate API is awkward to access from outside;
            the stack reads cleanly as a "current price" legend. */}
        {chartData.rows.length > 0 && tickers.length > 0 && (
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
            {tickers.map((sym) => {
              const v = endValues.get(sym);
              if (v == null) return null;
              const peerSymbol =
                sym === primaryQuote?.symbol ? primaryQuote.symbol : sym;
              const peerLtp =
                sym === primaryQuote?.symbol
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
                  aria-label={`${peerSymbol} latest price`}
                >
                  {peerLtp !== null ? INR.format(peerLtp) : `${v.toFixed(2)}`}
                </span>
              );
            })}
          </div>
        )}
      </div>

      {/* ── Footer summary box, separated by a single hairline ───────── */}
      {earliestDate && latestDate && summaries.length > 0 && (
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
              const peerName =
                s.symbol === primaryQuote?.symbol
                  ? primaryQuote.name
                  : peerQuotes[s.symbol]?.name ?? s.symbol;
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
}: {
  value: string;
  onChange: (v: string) => void;
  placeholder: string;
  "aria-label"?: string;
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
    <div ref={wrapperRef} style={{ position: "relative" }}>
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-haspopup="dialog"
        aria-expanded={open}
        aria-label={ariaLabel}
        style={{
          width: 150,
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
        className="inline-flex items-center"
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
          minWidth: 150,
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
        border: `1px solid ${color}55`,
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

function buildFinancials(quote: StockQuote): FinancialRow[] {
  const rng = mulberry32(symbolSeed(quote.symbol));
  // Anchor the latest year's revenue near 1% of market cap so the
  // numbers feel plausible relative to the price strip.
  const latestRevenue = Math.max(
    quote.market_cap !== null ? quote.market_cap * 0.012 : 1.5e11,
    5e10,
  );
  const growth = 0.07 + rng() * 0.08; // 7–15% YoY
  const revenues = FY_YEARS.map((_, i) => {
    const yearsBack = FY_YEARS.length - 1 - i;
    return latestRevenue / Math.pow(1 + growth, yearsBack);
  });
  const grossMargin = 0.32 + rng() * 0.18;
  const opMargin = grossMargin - (0.06 + rng() * 0.05);
  const netMargin = opMargin - (0.04 + rng() * 0.03);

  return [
    { label: "Revenue", values: revenues.map((r) => fmtCr(r)) },
    {
      label: "Gross Profit",
      values: revenues.map((r) => fmtCr(r * grossMargin)),
    },
    {
      label: "Operating Income",
      values: revenues.map((r) => fmtCr(r * opMargin)),
    },
    {
      label: "Net Income",
      values: revenues.map((r) => fmtCr(r * netMargin)),
    },
    {
      label: "EPS (₹)",
      values: revenues.map((r) =>
        ((r * netMargin) / (4e8 + rng() * 1e8)).toFixed(2),
      ),
    },
    {
      label: "Op. Margin",
      values: FY_YEARS.map(() => fmtPct(opMargin * 100, false)),
    },
  ];
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

  return [
    { label: "Revenue", values: revenues.map((r) => fmtCr(r)) },
    {
      label: "Cost of Revenue",
      values: revenues.map((r) => fmtCr(r * cogsPct)),
    },
    {
      label: "Gross Profit",
      values: revenues.map((r) => fmtCr(r * (1 - cogsPct))),
    },
    {
      label: "Operating Expenses",
      values: revenues.map((r) => fmtCr(r * opexPct)),
    },
    {
      label: "Operating Income",
      values: revenues.map((r) => fmtCr(r * (1 - cogsPct - opexPct))),
    },
    {
      label: "Tax",
      values: revenues.map((r) =>
        fmtCr(r * (1 - cogsPct - opexPct) * taxPct),
      ),
    },
    {
      label: "Net Income",
      values: revenues.map((r) =>
        fmtCr(r * (1 - cogsPct - opexPct) * (1 - taxPct)),
      ),
    },
  ];
}

function FinancialsTable({
  quote,
  minRows,
}: {
  quote: StockQuote;
  minRows: number;
}): React.ReactElement {
  const rows = useMemo(() => buildFinancials(quote), [quote]);
  return <FinancialsLikeTable title="Financials" rows={rows} minRows={minRows} />;
}

function ProfitLossTable({
  quote,
  minRows,
}: {
  quote: StockQuote;
  minRows: number;
}): React.ReactElement {
  const rows = useMemo(() => buildProfitLoss(quote), [quote]);
  return <FinancialsLikeTable title="Profit & Loss" rows={rows} minRows={minRows} />;
}

/** Screener-style table chrome: bordered card, sticky uppercase
 *  header row, hairline bottom borders on every cell except the last
 *  row, mono numerals right-aligned. Title sits above the bordered
 *  table block (matches the screener's "Results" header treatment).
 *
 *  Zebra striping: alternating rows tint with `--surface-active` so
 *  the eye can scan year columns easily. Both Financials and P&L
 *  pad to the same row count via `minRows` so the boxes always end
 *  at the same height. */
function FinancialsLikeTable({
  title,
  rows,
  minRows,
}: {
  title: string;
  rows: FinancialRow[];
  minRows: number;
}): React.ReactElement {
  // Pad the visible row list with empty filler rows so two tables
  // with different metric counts still finish at the same height.
  const paddedRows: (FinancialRow | null)[] = [...rows];
  while (paddedRows.length < minRows) paddedRows.push(null);
  const lastIdx = paddedRows.length - 1;

  return (
    <div className="flex h-full min-h-0 flex-col">
      <h2
        className="m-0"
        style={{
          fontFamily: "var(--font-ui)",
          fontSize: 13,
          fontWeight: 600,
          letterSpacing: "-0.01em",
          color: "var(--text-primary)",
          marginBottom: 12,
        }}
      >
        {title}
      </h2>
      <div
        style={{
          flex: 1,
          minHeight: 0,
          overflowY: "auto",
          background: "var(--bg-primary)",
          border: "1px solid var(--glass-border)",
          borderRadius: "var(--radius-md)",
        }}
      >
        <table
          style={{
            width: "100%",
            borderCollapse: "collapse",
            fontFamily: "var(--font-ui)",
          }}
        >
          <thead
            style={{
              position: "sticky",
              top: 0,
              background: "var(--bg-primary)",
              zIndex: 1,
            }}
          >
            <tr>
              <th
                style={{
                  ...screenerTh,
                  textAlign: "left",
                  color: "var(--text-tertiary)",
                }}
              >
                Metric
              </th>
              {FY_YEARS.map((y) => (
                <th
                  key={y}
                  style={{
                    ...screenerTh,
                    textAlign: "right",
                    color: "var(--text-tertiary)",
                  }}
                >
                  {y}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {paddedRows.map((r, idx) => {
              const isLast = idx === lastIdx;
              const stripe =
                idx % 2 === 1 ? "var(--surface-active)" : "transparent";
              return (
                <tr key={r ? r.label : `__pad_${idx}`} style={{ background: stripe }}>
                  <td
                    style={{
                      ...screenerTd,
                      borderBottom: isLast
                        ? "none"
                        : "1px solid var(--glass-border)",
                      color: "var(--text-secondary)",
                      fontFamily: "var(--font-ui)",
                      textAlign: "left",
                    }}
                  >
                    {r ? r.label : " "}
                  </td>
                  {FY_YEARS.map((_, i) => (
                    <td
                      key={i}
                      className="tabular-nums"
                      style={{
                        ...screenerTd,
                        borderBottom: isLast
                          ? "none"
                          : "1px solid var(--glass-border)",
                        color: "var(--text-primary)",
                        fontFamily: "var(--font-mono)",
                        textAlign: "right",
                      }}
                    >
                      {r ? r.values[i] ?? "—" : " "}
                    </td>
                  ))}
                </tr>
              );
            })}
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
  borderBottom: "1px solid var(--glass-border)",
  whiteSpace: "nowrap",
  userSelect: "none",
  fontFamily: "var(--font-ui)",
};

const screenerTd: React.CSSProperties = {
  padding: "11px 16px",
  fontSize: 12.5,
  whiteSpace: "nowrap",
};

type NewsItem = {
  id: string;
  title: string;
  source: string;
  date: string; // ISO
  /** Optional thumbnail tile. Rendered as a colored gradient block when
   *  no image URL is supplied — keeps the shape from collapsing while
   *  real-image wiring is pending. */
  imageUrl?: string;
  hue: string;
};

/** Deterministic placeholder news set. Real wiring will swap for the
 *  news service; we only need the visual rhythm here. */
function buildNews(quote: StockQuote): NewsItem[] {
  const rng = mulberry32(symbolSeed(quote.symbol) ^ 0xfeedbeef);
  const sources = ["Reuters", "Bloomberg", "Mint", "Economic Times", "MoneyControl"];
  const templates = [
    `${quote.name} posts strong quarterly earnings, beats Street estimates`,
    `Analysts upgrade ${quote.name} citing margin expansion`,
    `${quote.name} announces strategic partnership in core segment`,
    `${quote.name} board approves capex plan for next fiscal`,
    `${quote.symbol} shares hit new 52-week high amid sector rally`,
    `Brokerages divided on ${quote.name} valuations after recent run-up`,
  ];
  // Soft hues used for the thumbnail placeholder gradient. Order is
  // shuffled per-symbol so each stock's news block looks distinct.
  const hues = [
    "linear-gradient(135deg,#60a5fa 0%,#a78bfa 100%)",
    "linear-gradient(135deg,#10b981 0%,#3b82f6 100%)",
    "linear-gradient(135deg,#f97316 0%,#ec4899 100%)",
    "linear-gradient(135deg,#facc15 0%,#f97316 100%)",
    "linear-gradient(135deg,#a78bfa 0%,#ec4899 100%)",
  ];
  const now = Date.now();
  const day = 24 * 60 * 60 * 1000;
  return Array.from({ length: 5 }).map((_, i) => {
    const t = templates[Math.floor(rng() * templates.length)] ?? templates[0]!;
    const s = sources[Math.floor(rng() * sources.length)] ?? sources[0]!;
    const hue = hues[i % hues.length]!;
    return {
      id: `${quote.symbol}-${i}`,
      title: t,
      source: s,
      date: new Date(now - (i * 1.7 + rng() * 0.6) * day).toISOString(),
      hue,
    };
  });
}

function StockNewsColumn({ quote }: { quote: StockQuote }): React.ReactElement {
  const items = useMemo(() => buildNews(quote), [quote]);
  return (
    <div className="flex flex-col">
      {/* Title styled identically to the Financials and Profit & Loss
          headings so the bottom block reads as one consistent set. */}
      <h2
        className="m-0"
        style={{
          fontFamily: "var(--font-ui)",
          fontSize: 13,
          fontWeight: 600,
          letterSpacing: "-0.01em",
          color: "var(--text-primary)",
          marginBottom: 12,
        }}
      >
        {quote.name} Stock News
      </h2>

      <div className="flex flex-col">
        {items.map((it, i) => (
          <a
            key={it.id}
            href="#"
            onClick={(e) => e.preventDefault()}
            className="flex items-center"
            style={{
              gap: 18,
              padding: "16px 0",
              borderTop:
                i === 0 ? "none" : "1px solid var(--glass-border)",
              textDecoration: "none",
              color: "inherit",
              transition: "background-color 0.2s var(--ease-quartr)",
            }}
            onMouseEnter={(e) => {
              e.currentTarget.style.background = "var(--surface-hover)";
            }}
            onMouseLeave={(e) => {
              e.currentTarget.style.background = "transparent";
            }}
          >
            {/* Body */}
            <div className="flex flex-1 flex-col" style={{ minWidth: 0 }}>
              <p
                style={{
                  margin: 0,
                  fontFamily: "var(--font-ui)",
                  fontSize: 14,
                  fontWeight: 600,
                  lineHeight: 1.4,
                  color: "var(--text-primary)",
                  letterSpacing: "-0.005em",
                }}
              >
                {it.title}
              </p>
              <div
                className="flex items-center"
                style={{
                  gap: 8,
                  marginTop: 6,
                  fontFamily: "var(--font-ui)",
                  fontSize: 12,
                  color: "var(--text-tertiary)",
                }}
              >
                <span>{it.source}</span>
                <span aria-hidden="true">•</span>
                <span>{fmtRelative(it.date)}</span>
              </div>
            </div>

            {/* Thumbnail — image when available, otherwise a soft hue
                tile so the row keeps a stable shape. */}
            <div
              aria-hidden="true"
              className="shrink-0"
              style={{
                width: 84,
                height: 56,
                borderRadius: "var(--radius-sm)",
                overflow: "hidden",
                background: it.hue,
                backgroundImage: it.imageUrl
                  ? `url(${it.imageUrl})`
                  : it.hue,
                backgroundSize: "cover",
                backgroundPosition: "center",
              }}
            />
          </a>
        ))}
      </div>
    </div>
  );
}

function fmtRelative(iso: string): string {
  try {
    const t = parseISO(iso).getTime();
    const diff = Date.now() - t;
    const day = 24 * 60 * 60 * 1000;
    const hours = Math.floor(diff / (60 * 60 * 1000));
    if (hours < 1) return "just now";
    if (hours < 24) return `${hours}h ago`;
    const days = Math.floor(diff / day);
    if (days < 7) return `${days}d ago`;
    return format(parseISO(iso), "d MMM");
  } catch {
    return iso;
  }
}
