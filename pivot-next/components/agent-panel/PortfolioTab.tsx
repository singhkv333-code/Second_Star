"use client";

/**
 * PortfolioTab — Quartr-design portfolio page.
 *
 * Visuals ported from frontend-quartr/src/pages/Dashboard.jsx (PortfolioTab),
 * with the YieldTable section deliberately excluded per request. The data
 * path still uses pivot's existing API: getPortfolioSummary,
 * getPortfolioHoldings, getPortfolioPerformance, getIndexHistory.
 *
 * Sections (top → bottom):
 *   1. Page title (serif).
 *   2. Performance chart with range pills (1W / 1M / 3M / 6M / 1Y / 5Y / ALL),
 *      portfolio line + dashed NIFTY-50 benchmark, area fill, Y-axis labels,
 *      footer comparison strip (portfolio · benchmark · alpha).
 *   3. Holdings table (sortable, ticker tag with sector subtext).
 *   4. Asset Allocation — donut + legend across Market Cap / Sectors / Stocks.
 *   5. Diversification Score — your score vs community median, narrative line.
 *
 * Theme tokens are pulled from globals.css so light + dark both work.
 */

import { useEffect, useMemo, useRef, useState } from "react";
import {
  AlertCircle,
  ChevronDown,
  ChevronUp,
  ChevronsUpDown,
  RefreshCw,
  Wallet,
} from "lucide-react";
import Link from "next/link";
import { Skeleton } from "@/components/ui/skeleton";
import {
  getPortfolioHoldings,
  getPortfolioSummary,
  type Holding,
  type PortfolioSummary,
} from "@/lib/api";
import { isError } from "@/lib/types";
import { useLiveQuote } from "@/hooks/useLiveQuote";

// ---------------------------------------------------------------------------
// Static reference maps (Quartr parity)
// ---------------------------------------------------------------------------

/** Approximate Indian market-cap classification for the demo tickers.
 *  Mirrors frontend-quartr/.../AssetAllocation.jsx. */
const MARKET_CAP_MAP: Record<string, string> = {
  RELIANCE: "Largecap",
  HDFCBANK: "Largecap",
  INFY: "Largecap",
  TCS: "Largecap",
  AXISBANK: "Largecap",
  ITC: "Largecap",
  ASIANPAINT: "Largecap",
  BAJFINANCE: "Largecap",
  TATASTEEL: "Midcap",
  NIFTYBEES: "Index ETF",
};

/** Light sector mapping so the "Sectors" tab in Asset Allocation has
 *  something to render even though the backend Holding type has no
 *  sector field. Anything not listed lands in "Other". */
const SECTOR_MAP: Record<string, string> = {
  RELIANCE: "Energy",
  HDFCBANK: "Banking",
  AXISBANK: "Banking",
  ICICIBANK: "Banking",
  SBIN: "Banking",
  INFY: "IT Services",
  TCS: "IT Services",
  WIPRO: "IT Services",
  HCLTECH: "IT Services",
  ITC: "FMCG",
  HINDUNILVR: "FMCG",
  ASIANPAINT: "Materials",
  BAJFINANCE: "Financials",
  TATASTEEL: "Materials",
  NIFTYBEES: "Index ETF",
  GOLDBEES: "Commodities",
};

// Vibrant Pivot palette — saturated 500-tier hexes, no violet/indigo/
// fuchsia. Cobalt leads (brand-anchor for the largest slice), then a
// warm/cool rotation of orange · cyan-teal · golden yellow · dark teal
// · red — deliberately avoids any green hue so the donut never reads
// as "profit" against the rest of the surface.
const PALETTE = [
  "#1b7cc7", // cobalt blue
  "#fb8500", // vivid orange
  "#219ebc", // cyan teal
  "#ffb703", // golden yellow
  "#2c666e", // dark teal
  "#d00000", // red (sparingly — last slot)
];

// ---------------------------------------------------------------------------
// Formatters
// ---------------------------------------------------------------------------

function fmtINR(v: number): string {
  if (v >= 1e7) return `${(v / 1e7).toFixed(2)}Cr`;
  if (v >= 1e5) return `${(v / 1e5).toFixed(2)}L`;
  if (v >= 1e3) return `${(v / 1e3).toFixed(1)}k`;
  return `${Math.round(v)}`;
}

function fmtRupee(n: number, opts: { sign?: boolean; max?: number } = {}): string {
  const { sign = false, max = 0 } = opts;
  const abs = Math.abs(n).toLocaleString("en-IN", { maximumFractionDigits: max });
  const s = sign ? (n >= 0 ? "+" : "−") : n < 0 ? "−" : "";
  return `${s}₹${abs}`;
}

function fmtPct(n: number, signed = true): string {
  const s = signed ? (n >= 0 ? "+" : "") : "";
  return `${s}${n.toFixed(2)}%`;
}

function holdingValue(h: Holding): number {
  return h.last_price * h.quantity;
}

// ---------------------------------------------------------------------------
// Outer component
// ---------------------------------------------------------------------------

type FetchState =
  | { kind: "loading" }
  | { kind: "error"; message: string }
  | { kind: "ok"; summary: PortfolioSummary; holdings: Holding[] };

type PortfolioView = "overview" | "history";

export function PortfolioTab(): React.ReactElement {
  const [view, setView] = useState<PortfolioView>("overview");
  const [state, setState] = useState<FetchState>({ kind: "loading" });

  const load = (): void => {
    setState({ kind: "loading" });
    Promise.all([getPortfolioSummary(), getPortfolioHoldings()])
      .then(([sumRes, holdRes]) => {
        if (isError(sumRes)) { setState({ kind: "error", message: sumRes.error.message }); return; }
        if (isError(holdRes)) { setState({ kind: "error", message: holdRes.error.message }); return; }
        setState({ kind: "ok", summary: sumRes.data, holdings: holdRes.data ?? [] });
      })
      .catch((err: unknown) => {
        const msg = err instanceof Error ? err.message : "Network error";
        setState({ kind: "error", message: msg });
      });
  };

  useEffect(() => { load(); }, []);

  return (
    <div data-testid="portfolio-tab" style={{ background: "var(--bg-base)" }}>
      {/* Page title + Overview/History toggle */}
      <div className="flex items-center justify-between" style={{ marginBottom: 18 }}>
        <h1
          className="q-serif"
          style={{
            fontSize: 22,
            letterSpacing: "-0.025em",
            color: "var(--text-primary)",
            margin: 0,
          }}
        >
          Portfolio
        </h1>

        <div
          className="inline-flex"
          style={{
            gap: 2,
            padding: 3,
            background: "var(--bg-base)",
            border: "1px solid var(--glass-border)",
            borderRadius: "var(--radius-pill)",
          }}
        >
          {(["overview", "history"] as const).map((v) => {
            const active = view === v;
            return (
              <button
                key={v}
                type="button"
                onClick={() => setView(v)}
                aria-pressed={active}
                data-testid={`portfolio-view-${v}`}
                style={{
                  padding: "6px 14px",
                  border: "none",
                  cursor: "pointer",
                  borderRadius: "var(--radius-pill)",
                  fontSize: 12,
                  fontFamily: "var(--font-ui)",
                  fontWeight: 500,
                  background: active ? "var(--text-primary)" : "transparent",
                  color: active ? "var(--bg-primary)" : "var(--text-secondary)",
                  transition:
                    "color 0.2s var(--ease-quartr), background-color 0.2s var(--ease-quartr)",
                }}
              >
                {v === "overview" ? "Overview" : "History"}
              </button>
            );
          })}
        </div>
      </div>

      {view === "history" && <TradeHistory />}

      {view === "overview" && state.kind === "loading" && <PortfolioLoading />}

      {view === "overview" && state.kind === "error" && (
        <div
          role="alert"
          className="flex flex-col items-center justify-center py-12 text-center"
          data-testid="portfolio-error"
          style={{
            background: "var(--bg-primary)",
            border: "1px solid var(--glass-border)",
            borderRadius: "var(--radius-md)",
          }}
        >
          <AlertCircle
            className="mb-3 h-6 w-6"
            style={{ color: "var(--color-loss)" }}
            aria-hidden="true"
          />
          <p style={{ fontSize: 14, fontWeight: 500, color: "var(--text-primary)" }}>
            Couldn&apos;t load portfolio
          </p>
          <p style={{ fontSize: 12, color: "var(--text-tertiary)", marginTop: 4 }}>
            {state.message}
          </p>
          <button
            type="button"
            onClick={load}
            className="mt-4 inline-flex items-center"
            style={{
              gap: 8,
              padding: "6px 12px",
              background: "transparent",
              border: "1px solid var(--glass-border-hover)",
              borderRadius: "var(--radius-sm)",
              color: "var(--text-primary)",
              fontSize: 12,
              fontWeight: 500,
              cursor: "pointer",
              transition: "color 0.2s var(--ease-quartr), border-color 0.2s var(--ease-quartr)",
            }}
          >
            <RefreshCw size={13} aria-hidden="true" />
            Retry
          </button>
        </div>
      )}

      {view === "overview" && state.kind === "ok" && (
        <>
          <PerformanceChart summary={state.summary} />

          <Section label="Holdings">
            <Card padding={0} style={{ overflow: "hidden" }}>
              {state.holdings.length === 0 ? (
                <div
                  className="flex flex-col items-center justify-center py-12 text-center"
                  data-testid="portfolio-empty"
                >
                  <Wallet
                    className="mb-3"
                    size={28}
                    aria-hidden="true"
                    style={{ color: "var(--text-tertiary)" }}
                  />
                  <p style={{ fontSize: 14, fontWeight: 500, color: "var(--text-primary)" }}>
                    No holdings yet
                  </p>
                  <p style={{ fontSize: 12, color: "var(--text-tertiary)", marginTop: 4 }}>
                    When you place your first trade, your positions will show here.
                  </p>
                </div>
              ) : (
                <HoldingsTable holdings={state.holdings} />
              )}
            </Card>
          </Section>

          <AssetAllocation holdings={state.holdings} />
          <DiversificationScore holdings={state.holdings} />
        </>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Quartr-style section + card primitives (local copies of GlassCard/GlassSection)
// ---------------------------------------------------------------------------

function Section({
  label,
  children,
}: {
  label?: string;
  children: React.ReactNode;
}): React.ReactElement {
  return (
    <div style={{ marginBottom: 48 }}>
      {label && (
        <div
          style={{
            fontFamily: "var(--font-display)",
            fontWeight: "var(--weight-display)" as unknown as number,
            fontSize: 13,
            letterSpacing: "-0.02em",
            color: "var(--text-primary)",
            marginBottom: 12,
          }}
        >
          {label}
        </div>
      )}
      {children}
    </div>
  );
}

function Card({
  children,
  padding = 20,
  style,
}: {
  children: React.ReactNode;
  padding?: number | string;
  style?: React.CSSProperties;
}): React.ReactElement {
  return (
    <div
      style={{
        padding,
        background: "var(--bg-primary)",
        border: "none",
        borderRadius: "var(--radius-md)",
        ...style,
      }}
    >
      {children}
    </div>
  );
}

// PortfolioValueHead — the hero figure that heads the performance chart.
// The current value sits directly above the green line that visualises it,
// with Invested + Holdings as a quiet sub-line. Lives inside the chart's
// header so there's no separate competing box. Total/Day P&L stay in the
// global top bar, so they're not repeated here.
function PortfolioValueHead({ summary }: { summary: PortfolioSummary }): React.ReactElement {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
      <span
        style={{
          fontFamily: "var(--font-serif)",
          fontWeight: 500,
          fontSize: 36,
          lineHeight: 1,
          letterSpacing: "-0.02em",
          fontVariantNumeric: "tabular-nums",
          color: "var(--text-primary)",
        }}
      >
        {fmtRupee(summary.total_value)}
      </span>
      <span style={{ fontSize: 12.5, color: "var(--text-tertiary)" }}>
        Invested{" "}
        <span style={{ color: "var(--text-secondary)", fontWeight: 500 }}>
          {fmtRupee(summary.invested_value)}
        </span>
        {"  ·  "}
        <span style={{ color: "var(--text-secondary)", fontWeight: 500 }}>
          {summary.num_holdings}
        </span>{" "}
        holdings
      </span>
    </div>
  );
}

// ---------------------------------------------------------------------------
// PerformanceChart — Quartr-style demo chart.
//
// A deterministic seeded series scaled so the LAST point matches the live
// portfolio total. No API is called here: matching Quartr's pattern from
// frontend-quartr/src/components/portfolio/PerformanceChart.jsx.
// ---------------------------------------------------------------------------

const RANGES: { id: string; days: number; label: string; longLabel: string }[] = [
  { id: "1W",  days: 7,    label: "1W",  longLabel: "1 week"   },
  { id: "1M",  days: 30,   label: "1M",  longLabel: "1 month"  },
  { id: "3M",  days: 90,   label: "3M",  longLabel: "3 months" },
  { id: "6M",  days: 180,  label: "6M",  longLabel: "6 months" },
  { id: "1Y",  days: 365,  label: "1Y",  longLabel: "1 year"   },
  { id: "5Y",  days: 1825, label: "5Y",  longLabel: "5 years"  },
  { id: "ALL", days: 2555, label: "ALL", longLabel: "all time" },
];

/** Deterministic pseudo-random walk so the chart is stable across renders.
 *  Mirrors frontend-quartr/.../PerformanceChart.jsx::buildSeries. */
function buildSeries(days: number, seed: number): number[] {
  const out: number[] = [];
  let v = 100;
  for (let i = 0; i < days; i++) {
    const r = Math.sin((i + seed) * 0.31) + Math.cos((i + seed) * 0.13);
    const drift = 0.06;
    const vol = 0.65;
    v = Math.max(60, v + drift + r * vol);
    out.push(v);
  }
  return out;
}

function fmtMonthAt(idx: number, totalDays: number): string {
  const d = new Date();
  d.setDate(d.getDate() - (totalDays - idx - 1));
  return d.toLocaleDateString("en-IN", { month: "short", year: "2-digit" });
}

function PerformanceChart({
  summary,
}: {
  summary: PortfolioSummary;
}): React.ReactElement {
  const [rangeId, setRangeId] = useState<string>("1Y");
  const range = RANGES.find((r) => r.id === rangeId) ?? RANGES[4]!;
  const days = range.days;
  const totalValue = summary.total_value;

  // Build series scaled so the last portfolio point = live total_value.
  const { port, bench } = useMemo(() => {
    const rawP = buildSeries(days, 7);
    const rawB = buildSeries(days, 21).map((v, i) => v * 0.95 + i * 0.005);
    const liveTotal = totalValue || 800000;
    const scaleP = liveTotal / rawP[rawP.length - 1]!;
    const scaleB = (liveTotal * 0.94) / rawB[rawB.length - 1]!;
    const portfolio = rawP.map((v) => v * scaleP);
    const benchmark = rawB.map((v) => v * scaleB);
    return { port: portfolio, bench: benchmark };
  }, [days, totalValue]);

  return (
    <Section>
      {/* Header row lives OUTSIDE the card — the portfolio value heads the
          chart that visualises it (value above its own line), with the range
          pills opposite. Value block + pills stack on phone, sit on one row
          (value left, pills top-right) on sm+. */}
      <div
        className="flex flex-wrap items-start"
        style={{ columnGap: 14, rowGap: 14, marginBottom: 8 }}
      >
        <PortfolioValueHead summary={summary} />
        {/* Pills group: own row on phone (full width, scrolls if needed),
            right-aligned on sm+. */}
        <div
          className="perf-pills flex w-full justify-start sm:ml-auto sm:w-auto sm:justify-end"
          style={{
            overflowX: "auto",
            WebkitOverflowScrolling: "touch",
            scrollbarWidth: "none",
          }}
        >
          <div
            className="inline-flex"
            style={{
              gap: 2,
              padding: 2,
              background: "var(--bg-base)",
              border: "none",
              borderRadius: "var(--radius-pill)",
              flexShrink: 0,
            }}
          >
            {RANGES.map((r) => {
              const active = r.id === rangeId;
              return (
                <button
                  key={r.id}
                  type="button"
                  onClick={() => setRangeId(r.id)}
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
                    transition:
                      "color 0.2s var(--ease-quartr), background-color 0.2s var(--ease-quartr)",
                    whiteSpace: "nowrap",
                  }}
                >
                  {r.label}
                </button>
              );
            })}
          </div>
        </div>
      </div>

      <div style={{ padding: "22px 0 0" }}>
        <PerformanceSvg port={port} bench={bench} />
      </div>
    </Section>
  );
}

/** One legend-dot + label (left) / value (right) row inside the chart tooltip. */
function TipRow({
  color,
  label,
  value,
  strong,
}: {
  color: string;
  label: string;
  value: string;
  strong?: boolean;
}): React.ReactElement {
  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        justifyContent: "space-between",
        gap: 22,
      }}
    >
      <span style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
        <span
          aria-hidden="true"
          style={{ width: 7, height: 7, borderRadius: "50%", background: color, flexShrink: 0 }}
        />
        <span style={{ fontSize: 11, color: "var(--text-tertiary)" }}>{label}</span>
      </span>
      <span
        style={{
          fontSize: 12,
          fontWeight: strong ? 600 : 500,
          color: strong ? "var(--text-primary)" : "var(--text-secondary)",
          fontVariantNumeric: "tabular-nums",
        }}
      >
        {value}
      </span>
    </div>
  );
}

function PerformanceSvg({
  port,
  bench,
}: {
  port: number[];
  bench: number[];
}): React.ReactElement {
  // Groww-style hover state — tracks the downsampled point under the cursor.
  // `null` means the user isn't currently over the chart, so the crosshair
  // and tooltip are hidden.
  const chartColRef = useRef<HTMLDivElement>(null);
  const [hoverIdx, setHoverIdx] = useState<number | null>(null);

  // Static Y / X labels and grid lines removed — the hover tooltip now
  // provides the value/date at any point on the curve, so the chrome
  // was just clutter. Padding shrunk accordingly (no axis labels to
  // accommodate).
  const W = 920, H = 240, padL = 0, padR = 4, padT = 8, padB = 8;
  const innerW = W - padL - padR;
  const innerH = H - padT - padB;

  // Downsample to a fixed number of segments so the polyline reads as
  // discrete straight lines (like a typical stock chart), not a dense
  // ~365-point trace that visually averages into a smooth curve.
  const TARGET_SEGMENTS = 52;
  const downsample = (s: number[]): number[] => {
    if (s.length <= TARGET_SEGMENTS + 1) return s;
    const out: number[] = [];
    for (let i = 0; i <= TARGET_SEGMENTS; i++) {
      const idx = Math.round((i / TARGET_SEGMENTS) * (s.length - 1));
      out.push(s[idx]!);
    }
    return out;
  };
  const portDs = downsample(port);
  const benchDs = downsample(bench);

  const all = [...portDs, ...benchDs];
  const minV = Math.min(...all);
  const maxV = Math.max(...all);
  const span = Math.max(1, maxV - minV);

  const xAt = (i: number): number => padL + (i / (portDs.length - 1)) * innerW;
  const yAt = (v: number): number => padT + (1 - (v - minV) / span) * innerH;

  const buildPath = (s: number[]): string =>
    s.map((v, i) => `${i === 0 ? "M" : "L"} ${xAt(i).toFixed(1)} ${yAt(v).toFixed(1)}`).join(" ");
  const portPath = buildPath(portDs);
  const benchPath = buildPath(benchDs);
  const areaPath = `${portPath} L ${xAt(portDs.length - 1).toFixed(1)} ${(padT + innerH).toFixed(1)} L ${xAt(0).toFixed(1)} ${(padT + innerH).toFixed(1)} Z`;

  const portReturnPct = ((port[port.length - 1]! - port[0]!) / port[0]!) * 100;
  const benchReturnPct = ((bench[bench.length - 1]! - bench[0]!) / bench[0]!) * 100;
  const alphaPct = portReturnPct - benchReturnPct;

  // Green when portfolio is up over the range, red when down. Matches the
  // P/L colors used everywhere else (--color-profit / --color-loss).
  const isUp = portReturnPct >= 0;
  const lineColor = isUp ? "var(--color-profit)" : "var(--color-loss)";

  return (
    <>
      {/* [y-label column | chart column] — the y-labels are physically
          separated from the chart so the SVG can never overlap them
          regardless of container width. */}
      <div style={{ width: "100%" }}>
        <div
          ref={chartColRef}
          style={{ position: "relative", width: "100%" }}
          onMouseMove={(e) => {
            const rect = chartColRef.current?.getBoundingClientRect();
            if (!rect || rect.width === 0) return;
            const px = e.clientX - rect.left;
            // Mouse → viewBox X → portDs index. Chart geometry uses
            // padL=0 so we only have to subtract the right-side padding
            // from the usable width.
            const chartPxW = rect.width * (innerW / W);
            const frac = Math.max(0, Math.min(1, px / chartPxW));
            const idx = Math.round(frac * (portDs.length - 1));
            setHoverIdx(idx);
          }}
          onMouseLeave={() => setHoverIdx(null)}
        >
          <svg
            viewBox={`0 0 ${W} ${H}`}
            width="100%"
            height={H}
            preserveAspectRatio="none"
            style={{ display: "block" }}
          >
            <path
              d={benchPath}
              fill="none"
              stroke="var(--text-disabled)"
              strokeWidth="1"
              strokeDasharray="3 4"
              strokeLinejoin="miter"
              vectorEffect="non-scaling-stroke"
            />
            <path
              d={portPath}
              fill="none"
              stroke={lineColor}
              strokeWidth="1.25"
              strokeLinecap="round"
              strokeLinejoin="round"
              vectorEffect="non-scaling-stroke"
            />
          </svg>

          {/* ── Hover overlay — Groww-style crosshair, dual dots, tooltip.
              Crosshair and dots are rendered as positioned divs (not
              SVG elements) so they stay crisp regardless of how the
              `preserveAspectRatio="none"` SVG above is stretched. */}
          {hoverIdx !== null && (() => {
            const portVal = portDs[hoverIdx]!;
            const benchVal = benchDs[hoverIdx]!;
            // Position the crosshair within the chart's USABLE width
            // (innerW / W of the container, since padR sits to the right).
            const xPctOfChart = (xAt(hoverIdx) / innerW) * (innerW / W) * 100;
            const portYPct = (yAt(portVal) / H) * 100;
            const benchYPct = (yAt(benchVal) / H) * 100;
            // Map the downsampled idx back to the original `port` array
            // (port.length = days) so we can compute the calendar date.
            const originalIdx = Math.round(
              (hoverIdx / (portDs.length - 1)) * (port.length - 1),
            );
            const dateLabel = fmtMonthAt(originalIdx, port.length);
            // Edge-clamp the tooltip so it doesn't disappear off the
            // sides of the chart.
            const tipAnchor: React.CSSProperties =
              xPctOfChart < 10
                ? { left: 0, transform: "translateX(0)" }
                : xPctOfChart > 90
                  ? { left: `${xPctOfChart}%`, transform: "translateX(-100%)" }
                  : { left: `${xPctOfChart}%`, transform: "translateX(-50%)" };
            return (
              <>
                <div
                  aria-hidden="true"
                  style={{
                    position: "absolute",
                    top: padT,
                    bottom: padB,
                    left: `${xPctOfChart}%`,
                    borderLeft: "1px dashed var(--text-tertiary)",
                    opacity: 0.5,
                    pointerEvents: "none",
                  }}
                />
                <div
                  aria-hidden="true"
                  style={{
                    position: "absolute",
                    left: `${xPctOfChart}%`,
                    top: `${portYPct}%`,
                    width: 10,
                    height: 10,
                    marginLeft: -5,
                    marginTop: -5,
                    borderRadius: "50%",
                    background: lineColor,
                    border: "2px solid var(--bg-base)",
                    boxShadow: "0 0 0 1px rgba(0,0,0,0.06)",
                    pointerEvents: "none",
                  }}
                />
                <div
                  aria-hidden="true"
                  style={{
                    position: "absolute",
                    left: `${xPctOfChart}%`,
                    top: `${benchYPct}%`,
                    width: 8,
                    height: 8,
                    marginLeft: -4,
                    marginTop: -4,
                    borderRadius: "50%",
                    background: "var(--text-disabled)",
                    border: "2px solid var(--bg-base)",
                    pointerEvents: "none",
                  }}
                />
                <div
                  role="status"
                  aria-live="polite"
                  style={{
                    position: "absolute",
                    top: 0,
                    marginTop: -10,
                    ...tipAnchor,
                    transform: `${tipAnchor.transform ?? ""} translateY(-100%)`,
                    padding: "9px 11px",
                    background: "var(--bg-base)",
                    border: "1px solid var(--glass-border)",
                    borderRadius: 10,
                    boxShadow:
                      "0 8px 24px -6px rgba(15, 23, 42, 0.18), 0 2px 6px rgba(15, 23, 42, 0.05)",
                    fontFamily: "var(--font-ui)",
                    whiteSpace: "nowrap",
                    pointerEvents: "none",
                    zIndex: 5,
                    minWidth: 150,
                  }}
                >
                  <div
                    style={{
                      fontSize: 10.5,
                      fontWeight: 500,
                      letterSpacing: "0.02em",
                      color: "var(--text-tertiary)",
                      paddingBottom: 7,
                      marginBottom: 7,
                      borderBottom: "1px solid var(--glass-border)",
                    }}
                  >
                    {dateLabel}
                  </div>
                  <TipRow
                    color={lineColor}
                    label="Portfolio"
                    value={fmtRupee(portVal, { max: 0 })}
                    strong
                  />
                  <div style={{ height: 5 }} />
                  <TipRow
                    color="var(--text-disabled)"
                    label="NIFTY 50"
                    value={fmtRupee(benchVal, { max: 0 })}
                  />
                </div>
              </>
            );
          })()}
        </div>
      </div>

      {/* Footer comparison line — tighter gap so it stays on one row at
          most container widths and only wraps to a clean two-row grid on
          the narrowest phones. */}
      <div
        className="flex flex-wrap items-center"
        style={{
          marginTop: 10,
          columnGap: 14,
          rowGap: 6,
          fontSize: 12,
          color: "var(--text-tertiary)",
        }}
      >
        <span>
          vs&nbsp;&nbsp;<span style={{ color: "var(--text-secondary)" }}>NIFTY&nbsp;50</span>
        </span>
        <PerfStat label="portfolio" value={portReturnPct} />
        <PerfStat label="benchmark" value={benchReturnPct} />
        <PerfStat label="alpha" value={alphaPct} />
      </div>
    </>
  );
}

function PerfStat({
  label,
  value,
}: {
  label: string;
  value: number;
}): React.ReactElement {
  const pos = value >= 0;
  const color = pos ? "var(--color-profit)" : "var(--color-loss)";
  return (
    <span className="inline-flex items-center" style={{ gap: 6, fontFamily: "var(--font-mono)" }}>
      <span style={{ color }}>
        {pos ? "+" : ""}
        {value.toFixed(1)}%
      </span>
      <span style={{ color: "var(--text-tertiary)", fontFamily: "var(--font-ui)" }}>{label}</span>
    </span>
  );
}

// ---------------------------------------------------------------------------
// HoldingsTable — Quartr-style sortable table with sector subtext
// ---------------------------------------------------------------------------

type SortKey =
  | "tradingsymbol"
  | "quantity"
  | "average_price"
  | "last_price"
  | "pnl"
  | "day_change_percentage"
  | "value";
type SortDir = "asc" | "desc";

const COLUMNS: { key: SortKey | null; label: string; align: "left" | "right" }[] = [
  { key: "tradingsymbol", label: "Symbol", align: "left" },
  { key: "quantity", label: "Qty", align: "right" },
  { key: "average_price", label: "Avg", align: "right" },
  { key: "last_price", label: "LTP", align: "right" },
  { key: "pnl", label: "P&L", align: "right" },
  { key: "day_change_percentage", label: "Day", align: "right" },
  { key: null, label: "Value", align: "right" },
];

// Screener-style brand glyph — a small rounded tile holding the symbol's
// initial, tinted by sector. Mirrors ScreenerPage's BrandGlyph so the
// holdings table reads as the same product.
function brandGlyphHue(key?: string): string {
  if (!key) return "#94a3b8";
  const s = key.toLowerCase();
  if (s.includes("bank") || s.includes("financ") || s.includes("nbfc")) return "#60a5fa";
  if (s.includes("tech") || s.includes("it ") || s.includes("software")) return "#a78bfa";
  if (s.includes("energy") || s.includes("oil")) return "#f97316";
  if (s.includes("pharma") || s.includes("health")) return "#10b981";
  if (s.includes("auto")) return "#facc15";
  if (s.includes("fmcg") || s.includes("consumer")) return "#34d399";
  if (s.includes("material") || s.includes("metal")) return "#f472b6";
  if (s.includes("telecom")) return "#22d3ee";
  if (s.includes("gold") || s.includes("commod")) return "#eab308";
  if (s.includes("etf") || s.includes("index")) return "#38bdf8";
  return "#94a3b8";
}

function HoldingGlyph({ symbol, hueKey }: { symbol: string; hueKey?: string }): React.ReactElement {
  const initial = symbol.trim()[0]?.toUpperCase() ?? "•";
  const hue = brandGlyphHue(hueKey);
  return (
    <div
      aria-hidden="true"
      style={{
        width: 34,
        height: 34,
        flexShrink: 0,
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        borderRadius: "var(--radius-sm)",
        background: `${hue}22`,
        color: hue,
        fontFamily: "var(--font-ui)",
        fontSize: 14,
        fontWeight: 500,
        letterSpacing: "-0.02em",
      }}
    >
      {initial}
    </div>
  );
}

function HoldingsTable({ holdings }: { holdings: Holding[] }): React.ReactElement {
  const [sort, setSort] = useState<{ key: SortKey; dir: SortDir }>({
    key: "pnl",
    dir: "desc",
  });

  const sorted = useMemo(() => {
    const items = [...holdings];
    items.sort((a, b) => {
      const av = sort.key === "value" ? holdingValue(a) : a[sort.key];
      const bv = sort.key === "value" ? holdingValue(b) : b[sort.key];
      const cmp =
        typeof av === "number" && typeof bv === "number"
          ? av - bv
          : String(av).localeCompare(String(bv));
      return sort.dir === "asc" ? cmp : -cmp;
    });
    return items;
  }, [holdings, sort]);

  const cycle = (key: SortKey): void => {
    setSort((s) =>
      s.key === key
        ? { key, dir: s.dir === "asc" ? "desc" : "asc" }
        : { key, dir: key === "tradingsymbol" ? "asc" : "desc" },
    );
  };

  return (
    <div style={{ overflowX: "auto" }}>
      <table style={{ width: "100%", borderCollapse: "collapse" }} data-testid="holdings-table">
        <thead>
          <tr>
            {COLUMNS.map((col) => {
              const active = col.key && sort.key === col.key;
              const Icon = !active
                ? ChevronsUpDown
                : sort.dir === "asc"
                  ? ChevronUp
                  : ChevronDown;
              return (
                <th
                  key={col.label}
                  scope="col"
                  onClick={() => col.key && cycle(col.key)}
                  style={{
                    padding: "13px 18px",
                    fontSize: 10,
                    letterSpacing: "0.1em",
                    textTransform: "uppercase",
                    fontWeight: "var(--weight-display)" as unknown as number,
                    color: active ? "var(--text-primary)" : "var(--text-tertiary)",
                    cursor: col.key ? "pointer" : "default",
                    userSelect: "none",
                    whiteSpace: "nowrap",
                    textAlign: col.align,
                    borderBottom: "1.5px solid var(--glass-border)",
                    transition: "color 180ms",
                  }}
                  onMouseEnter={(e) => {
                    if (!active && col.key) e.currentTarget.style.color = "var(--text-secondary)";
                  }}
                  onMouseLeave={(e) => {
                    if (!active && col.key) e.currentTarget.style.color = "var(--text-tertiary)";
                  }}
                  data-testid={col.key ? `sort-${col.key}` : undefined}
                >
                  <span
                    className="inline-flex items-center"
                    style={{ gap: 5, flexDirection: col.align === "right" ? "row-reverse" : "row" }}
                  >
                    {col.label}
                    {col.key && (
                      <Icon size={12} aria-hidden="true" style={{ opacity: active ? 1 : 0.45 }} />
                    )}
                  </span>
                </th>
              );
            })}
          </tr>
        </thead>
        <tbody>
          {sorted.map((h) => (
            <HoldingRow key={`${h.exchange}:${h.tradingsymbol}`} holding={h} />
          ))}
        </tbody>
      </table>
    </div>
  );
}

// ---------------------------------------------------------------------------
// HoldingRow — one <tr> per holding, wired to useLiveQuote for LTP.
// ---------------------------------------------------------------------------

function HoldingRow({ holding: h }: { holding: Holding }): React.ReactElement {
  const liveQuote = useLiveQuote(h.tradingsymbol);
  const ltp = liveQuote.ltp ?? h.last_price;
  const value = ltp * h.quantity;
  const sector = SECTOR_MAP[h.tradingsymbol];
  const pnlPos = h.pnl >= 0;
  const dayPos = h.day_change_percentage >= 0;

  return (
    <tr
      style={{
        borderBottom: "1px solid var(--glass-border)",
        transition: "background 150ms",
      }}
      onMouseEnter={(e) => { e.currentTarget.style.background = "var(--bg-secondary)"; }}
      onMouseLeave={(e) => { e.currentTarget.style.background = "transparent"; }}
      data-testid={`holding-${h.tradingsymbol}`}
    >
      <td style={{ padding: "16px 18px" }}>
        <div className="inline-flex items-center" style={{ gap: 12 }}>
          <HoldingGlyph symbol={h.tradingsymbol} hueKey={sector} />
          <div style={{ display: "flex", flexDirection: "column", gap: 3 }}>
            <Link
              href={`/stock/${encodeURIComponent(h.tradingsymbol)}`}
              className="inline-flex items-baseline"
              style={{
                gap: 6,
                fontFamily: "var(--font-ui)",
                fontSize: 13,
                fontWeight: 500,
                color: "var(--text-primary)",
                textDecoration: "none",
              }}
            >
              <span
                style={{
                  color: "var(--text-tertiary)",
                  fontSize: 10,
                  fontWeight: 400,
                }}
              >
                {h.exchange || "NSE"}
              </span>
              {h.tradingsymbol}
            </Link>
            {sector && (
              <span
                style={{
                  fontSize: 11,
                  color: "var(--text-tertiary)",
                }}
              >
                {sector}
              </span>
            )}
          </div>
        </div>
      </td>
      <NumCell>{h.quantity}</NumCell>
      <NumCell>{fmtRupee(h.average_price, { max: 2 })}</NumCell>
      {/* LTP cell — green dot when live, grey when REST/stale */}
      <td
        style={{
          padding: "16px 18px",
          fontFamily: "var(--font-mono)",
          fontSize: 12.5,
          fontWeight: 500,
          color: "var(--text-secondary)",
          textAlign: "right",
          fontVariantNumeric: "tabular-nums",
        }}
      >
        <span className="inline-flex items-center" style={{ gap: 5 }}>
          <span
            title={liveQuote.isLive ? "Live price" : "Delayed price"}
            aria-label={liveQuote.isLive ? "Live price" : "Delayed price"}
            style={{
              display: "inline-block",
              width: 6,
              height: 6,
              borderRadius: "50%",
              background: liveQuote.isLive ? "var(--color-profit)" : "var(--text-tertiary)",
              flexShrink: 0,
            }}
          />
          {fmtRupee(ltp, { max: 2 })}
        </span>
      </td>
      <td
        style={{
          padding: "16px 18px",
          fontFamily: "var(--font-mono)",
          fontSize: 12.5,
          textAlign: "right",
          fontVariantNumeric: "tabular-nums",
          color: pnlPos ? "var(--color-profit)" : "var(--color-loss)",
        }}
      >
        {fmtRupee(h.pnl, { sign: true, max: 0 })}
      </td>
      <td
        style={{
          padding: "16px 18px",
          fontFamily: "var(--font-mono)",
          fontSize: 12.5,
          textAlign: "right",
          fontVariantNumeric: "tabular-nums",
          color: dayPos ? "var(--color-profit)" : "var(--color-loss)",
        }}
      >
        {fmtPct(h.day_change_percentage)}
      </td>
      <NumCell strong>{fmtRupee(value)}</NumCell>
    </tr>
  );
}

function NumCell({
  children,
  strong,
}: {
  children: React.ReactNode;
  strong?: boolean;
}): React.ReactElement {
  return (
    <td
      style={{
        padding: "16px 18px",
        fontFamily: "var(--font-mono)",
        fontSize: 12.5,
        textAlign: "right",
        fontVariantNumeric: "tabular-nums",
        color: strong ? "var(--text-primary)" : "var(--text-secondary)",
        fontWeight: 500,
      }}
    >
      {children}
    </td>
  );
}

// ---------------------------------------------------------------------------
// AssetAllocation — donut + legend across Market Cap / Sectors / Stocks
// ---------------------------------------------------------------------------

const ALLOC_TABS: { id: "marketcap" | "sectors" | "stocks"; label: string }[] = [
  { id: "marketcap", label: "Market Cap" },
  { id: "sectors", label: "Sectors" },
  { id: "stocks", label: "Stocks" },
];

type AllocRow = { label: string; value: number; pct: number; color: string };

function aggregate(holdings: Holding[], keyFn: (h: Holding) => string): { total: number; rows: AllocRow[] } {
  const total = holdings.reduce((s, h) => s + holdingValue(h), 0);
  if (total === 0) return { total: 0, rows: [] };
  const map = new Map<string, number>();
  for (const h of holdings) {
    const v = holdingValue(h);
    const k = keyFn(h);
    map.set(k, (map.get(k) ?? 0) + v);
  }
  const rows = Array.from(map.entries())
    .map(([label, value]) => ({ label, value, pct: (value / total) * 100, color: "" }))
    .sort((a, b) => b.pct - a.pct)
    .map((row, i) => ({ ...row, color: PALETTE[i % PALETTE.length]! }));
  return { total, rows };
}

function arcPath(cx: number, cy: number, rOuter: number, rInner: number, startA: number, endA: number): string {
  const polar = (r: number, a: number): [number, number] => [cx + r * Math.cos(a), cy + r * Math.sin(a)];
  const [x1, y1] = polar(rOuter, startA);
  const [x2, y2] = polar(rOuter, endA);
  const [x3, y3] = polar(rInner, endA);
  const [x4, y4] = polar(rInner, startA);
  const largeArc = endA - startA > Math.PI ? 1 : 0;
  return [
    `M ${x1} ${y1}`,
    `A ${rOuter} ${rOuter} 0 ${largeArc} 1 ${x2} ${y2}`,
    `L ${x3} ${y3}`,
    `A ${rInner} ${rInner} 0 ${largeArc} 0 ${x4} ${y4}`,
    "Z",
  ].join(" ");
}

function AssetAllocation({ holdings }: { holdings: Holding[] }): React.ReactElement {
  const [tab, setTab] = useState<"marketcap" | "sectors" | "stocks">("marketcap");
  const [hover, setHover] = useState<AllocRow | null>(null);

  const data = useMemo(() => {
    if (!holdings || holdings.length === 0) return { total: 0, rows: [] as AllocRow[] };
    if (tab === "marketcap") return aggregate(holdings, (h) => MARKET_CAP_MAP[h.tradingsymbol] ?? "Other");
    if (tab === "sectors") return aggregate(holdings, (h) => SECTOR_MAP[h.tradingsymbol] ?? "Other");
    return aggregate(holdings, (h) => h.tradingsymbol);
  }, [holdings, tab]);

  const segments = useMemo(() => {
    let cursor = -Math.PI / 2;
    return data.rows.map((row) => {
      const angle = (row.pct / 100) * Math.PI * 2;
      const start = cursor;
      const end = cursor + angle;
      cursor = end;
      return { ...row, start, end };
    });
  }, [data]);

  const cx = 110, cy = 110, rOuter = 96, rInner = 64;
  const tabLabel = ALLOC_TABS.find((t) => t.id === tab)?.label ?? "";

  return (
    <Section label="Asset Allocation">
      <Card style={{ background: "transparent", padding: 0 }}>
        {/* Tabs */}
        <div
          className="flex"
          style={{
            gap: 4,
            marginBottom: 18,
            borderBottom: "1px solid var(--glass-border)",
            paddingBottom: 12,
          }}
        >
          {ALLOC_TABS.map((t) => {
            const active = tab === t.id;
            return (
              <button
                key={t.id}
                type="button"
                onClick={() => setTab(t.id)}
                style={{
                  padding: "6px 14px",
                  background: active ? "var(--bg-elevated)" : "transparent",
                  border: "none",
                  borderRadius: "var(--radius-sm)",
                  color: active ? "var(--text-primary)" : "var(--text-secondary)",
                  fontFamily: "var(--font-ui)",
                  fontSize: 12.5,
                  fontWeight: 500,
                  cursor: "pointer",
                  transition:
                    "color 0.25s var(--ease-quartr), background-color 0.25s var(--ease-quartr)",
                }}
              >
                {t.label}
              </button>
            );
          })}
        </div>

        {data.rows.length === 0 ? (
          <div
            style={{
              padding: 32,
              textAlign: "center",
              color: "var(--text-secondary)",
              fontSize: 13,
            }}
          >
            No allocation data.
          </div>
        ) : (
          <div className="flex flex-wrap items-center" style={{ gap: 28 }}>
            {/* Donut */}
            <div style={{ position: "relative", width: 220, height: 220, flexShrink: 0 }}>
              <svg width={220} height={220} viewBox="0 0 220 220">
                {segments.map((seg, i) => (
                  <path
                    key={i}
                    d={arcPath(cx, cy, rOuter, rInner, seg.start, seg.end)}
                    fill={seg.color}
                    stroke="var(--bg-primary)"
                    strokeWidth={1.25}
                    onMouseEnter={() => setHover(seg)}
                    onMouseLeave={() => setHover(null)}
                    onClick={() =>
                      setHover((curr) => (curr?.label === seg.label ? null : seg))
                    }
                    style={{
                      cursor: "pointer",
                      transition: "opacity 180ms var(--ease-quartr)",
                      opacity: hover && hover.label !== seg.label ? 0.4 : 1,
                    }}
                  />
                ))}
              </svg>
              <div
                style={{
                  position: "absolute",
                  inset: 0,
                  display: "flex",
                  flexDirection: "column",
                  alignItems: "center",
                  justifyContent: "center",
                  pointerEvents: "none",
                  textAlign: "center",
                }}
              >
                {hover ? (
                  <>
                    <div className="q-uppercase-label" style={{ marginBottom: 6, maxWidth: 140 }}>
                      {hover.label}
                    </div>
                    <div
                      className="q-display"
                      style={{ fontSize: 22, color: "var(--text-primary)" }}
                    >
                      {hover.pct.toFixed(2)}%
                    </div>
                  </>
                ) : (
                  <>
                    <div className="q-uppercase-label" style={{ marginBottom: 6 }}>
                      {tabLabel}
                    </div>
                    <div
                      className="q-display"
                      style={{ fontSize: 18, color: "var(--text-primary)" }}
                    >
                      ₹{Math.round(data.total).toLocaleString("en-IN")}
                    </div>
                  </>
                )}
              </div>
            </div>

            {/* Legend / list */}
            <div
              className="flex flex-col"
              style={{ flex: 1, minWidth: 220, gap: 2, maxHeight: 260, overflowY: "auto" }}
            >
              {segments.map((seg) => {
                const active = hover?.label === seg.label;
                return (
                  <div
                    key={seg.label}
                    role="button"
                    tabIndex={0}
                    onMouseEnter={() => setHover(seg)}
                    onMouseLeave={() => setHover(null)}
                    onClick={() =>
                      setHover((curr) => (curr?.label === seg.label ? null : seg))
                    }
                    onKeyDown={(e) => {
                      if (e.key === "Enter" || e.key === " ") {
                        e.preventDefault();
                        setHover((curr) => (curr?.label === seg.label ? null : seg));
                      }
                    }}
                    style={{
                      display: "grid",
                      gridTemplateColumns: "12px minmax(0, 1fr) auto",
                      alignItems: "center",
                      gap: 10,
                      padding: "8px 10px",
                      borderRadius: "var(--radius-sm)",
                      background: active ? "var(--bg-elevated)" : "transparent",
                      cursor: "pointer",
                      transition: "background-color 0.18s var(--ease-quartr)",
                    }}
                  >
                    <span
                      style={{
                        width: 10,
                        height: 10,
                        borderRadius: 3,
                        background: seg.color,
                        flexShrink: 0,
                      }}
                    />
                    <span
                      style={{
                        fontSize: 13,
                        color: "var(--text-primary)",
                        fontWeight: 500,
                        whiteSpace: "nowrap",
                        overflow: "hidden",
                        textOverflow: "ellipsis",
                      }}
                    >
                      {seg.label}
                    </span>
                    <span
                      style={{
                        fontFamily: "var(--font-mono)",
                        fontSize: 12.5,
                        color: "var(--text-secondary)",
                        minWidth: 56,
                        textAlign: "right",
                      }}
                    >
                      {seg.pct.toFixed(2)}%
                    </span>
                  </div>
                );
              })}
            </div>
          </div>
        )}
      </Card>
    </Section>
  );
}

// ---------------------------------------------------------------------------
// DiversificationScore — sector-HHI based, vs community median
// ---------------------------------------------------------------------------

const COMMUNITY_SCORE = 58;

function computeScore(holdings: Holding[]): number {
  if (!holdings || holdings.length === 0) return 0;
  const total = holdings.reduce((s, h) => s + holdingValue(h), 0);
  if (total <= 0) return 0;
  const bySector = new Map<string, number>();
  for (const h of holdings) {
    const sector = SECTOR_MAP[h.tradingsymbol] ?? "Other";
    bySector.set(sector, (bySector.get(sector) ?? 0) + holdingValue(h));
  }
  const weights = Array.from(bySector.values()).map((v) => v / total);
  const hhi = weights.reduce((s, w) => s + w * w, 0);
  const n = bySector.size;
  const minHHI = 1 / Math.max(1, n);
  const norm = (hhi - minHHI) / (1 - minHHI || 1);
  return Math.round((1 - norm) * 100);
}

function DiversificationScore({ holdings }: { holdings: Holding[] }): React.ReactElement {
  const score = useMemo(() => computeScore(holdings), [holdings]);
  const diff = score - COMMUNITY_SCORE;
  const aboveMedian = diff >= 0;

  return (
    <Section label="Diversification">
      <Card padding="22px 24px">
        <div className="flex flex-col" style={{ gap: 12, marginBottom: 16 }}>
          <ScoreBar
            label="Your Portfolio Score"
            value={score}
            color={aboveMedian ? "var(--pivot-blue)" : "var(--color-loss)"}
          />
          <ScoreBar
            label="Community Score"
            value={COMMUNITY_SCORE}
            color="var(--text-secondary)"
          />
        </div>
        <div style={{ fontSize: 12.5, color: "var(--text-secondary)", lineHeight: 1.55 }}>
          Your portfolio&apos;s diversification score is{" "}
          <strong style={{ color: "var(--text-primary)", fontWeight: 550 }}>{score}%</strong>, while
          the community median is{" "}
          <strong style={{ color: "var(--text-primary)", fontWeight: 550 }}>{COMMUNITY_SCORE}%</strong>,
          meaning your holdings are{" "}
          <strong
            style={{
              color: aboveMedian ? "var(--pivot-blue)" : "var(--color-loss)",
              fontWeight: 550,
            }}
          >
            {aboveMedian ? "more" : "less"} diversified
          </strong>
          {" "}compared to the broader community.
        </div>
      </Card>
    </Section>
  );
}

function ScoreBar({
  label,
  value,
  color,
}: {
  label: string;
  value: number;
  color: string;
}): React.ReactElement {
  return (
    <div
      className="portfolio-meter-row"
      style={{
        width: "100%",
        display: "grid",
        gridTemplateColumns: "200px minmax(0, 1fr) 64px",
        alignItems: "center",
        gap: 18,
      }}
    >
      <span style={{ fontSize: 12, color: "var(--text-secondary)", fontWeight: 500 }}>{label}</span>
      <div
        style={{
          position: "relative",
          height: 8,
          background: "var(--bg-elevated)",
          borderRadius: 999,
          overflow: "hidden",
        }}
      >
        <div
          style={{
            position: "absolute",
            inset: 0,
            width: `${Math.max(0, Math.min(100, value))}%`,
            background: color,
            borderRadius: 999,
            transition: "width 0.6s var(--ease-quartr)",
          }}
        />
      </div>
      <span
        style={{
          textAlign: "right",
          fontFamily: "var(--font-mono)",
          fontSize: 12.5,
          color: "var(--text-primary)",
        }}
      >
        {value}/100
      </span>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Loading skeleton
// ---------------------------------------------------------------------------

function PortfolioLoading(): React.ReactElement {
  return (
    <div className="flex flex-col" style={{ gap: 28 }} data-testid="portfolio-loading">
      <Card padding="22px 24px">
        <Skeleton style={{ height: 240, width: "100%" }} />
      </Card>
      <Card padding={0} style={{ overflow: "hidden" }}>
        <Skeleton style={{ height: 40, width: "100%" }} />
        {[0, 1, 2, 3, 4].map((i) => (
          <Skeleton key={i} style={{ height: 56, width: "100%", marginTop: 1 }} />
        ))}
      </Card>
      <Card>
        <Skeleton style={{ height: 220, width: 220 }} />
      </Card>
      <Card padding="22px 24px">
        <Skeleton style={{ height: 8, width: "100%", marginBottom: 12 }} />
        <Skeleton style={{ height: 8, width: "100%" }} />
      </Card>
    </div>
  );
}

// ---------------------------------------------------------------------------
// TradeHistory — trade log table
// ---------------------------------------------------------------------------

type TradeRow = {
  id: string;
  symbol: string;
  side: "BUY" | "SELL";
  quantity: number;
  price: number;
  amount: number;
  datetime: string;
  agent: string;
};

function generateMockTrades(): TradeRow[] {
  const agents: string[] = ["INFY weekly dip-buy", "RELIANCE 3:55 PM buy", "TCS monthly SIP"];
  const symbols: string[] = ["INFY", "RELIANCE", "TCS", "HDFC", "WIPRO"];
  const trades: TradeRow[] = [];
  const now = Date.now();
  for (let i = 0; i < 20; i++) {
    const symbol = symbols[i % symbols.length]!;
    const side: "BUY" | "SELL" = i % 2 === 0 ? "BUY" : "SELL";
    const qty = (i % 5 + 1) * 5;
    const price = 1000 + ((i * 137 + 42) % 3000);
    const agent = agents[i % agents.length]!;
    trades.push({
      id: `trade-${i}`,
      symbol,
      side,
      quantity: qty,
      price,
      amount: qty * price,
      datetime: new Date(now - i * 3_600_000 * 8).toISOString(),
      agent,
    });
  }
  return trades;
}

const MOCK_TRADES = generateMockTrades();

function TradeHistory(): React.ReactElement {
  const fmt = (iso: string): { date: string; time: string } => {
    const d = new Date(iso);
    return {
      date: d.toLocaleDateString("en-IN", { day: "2-digit", month: "short", year: "numeric" }),
      time: d.toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit", hour12: true }),
    };
  };

  return (
    <div className="flex flex-col" style={{ gap: 12 }}>
      <div
        className="overflow-hidden rounded-2xl border border-border/50 bg-card"
        style={{ boxShadow: "0 1px 2px rgba(15,23,42,0.04)" }}
      >
        <table className="w-full" style={{ borderCollapse: "collapse", fontSize: 12 }}>
          <thead>
            <tr style={{ borderBottom: "1px solid var(--glass-border)" }}>
              {["Symbol", "Side", "Qty", "Price (₹)", "Amount (₹)", "Date", "Time", "Agent"].map((h) => (
                <th
                  key={h}
                  style={{
                    padding: "10px 14px",
                    fontWeight: 500,
                    color: "var(--text-tertiary)",
                    textAlign: "left",
                    whiteSpace: "nowrap",
                  }}
                >
                  {h}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {MOCK_TRADES.map((t, idx) => {
              const { date, time } = fmt(t.datetime);
              const isBuy = t.side === "BUY";
              return (
                <tr
                  key={t.id}
                  style={{
                    borderBottom:
                      idx < MOCK_TRADES.length - 1 ? "1px solid var(--glass-border)" : "none",
                    background: idx % 2 === 0 ? "transparent" : "rgba(0,0,0,0.015)",
                  }}
                >
                  <td style={{ padding: "10px 14px", fontWeight: 600, color: "var(--text-primary)" }}>
                    {t.symbol}
                  </td>
                  <td style={{ padding: "10px 14px" }}>
                    <span
                      style={{
                        padding: "2px 8px",
                        borderRadius: 4,
                        fontSize: 11,
                        fontWeight: 600,
                        background: isBuy ? "rgba(16,185,129,0.1)" : "rgba(239,68,68,0.1)",
                        color: isBuy ? "#10b981" : "#ef4444",
                      }}
                    >
                      {t.side}
                    </span>
                  </td>
                  <td style={{ padding: "10px 14px", color: "var(--text-secondary)" }}>{t.quantity}</td>
                  <td style={{ padding: "10px 14px", color: "var(--text-secondary)" }}>
                    {t.price.toLocaleString("en-IN")}
                  </td>
                  <td style={{ padding: "10px 14px", fontWeight: 500, color: "var(--text-primary)" }}>
                    ₹{t.amount.toLocaleString("en-IN")}
                  </td>
                  <td style={{ padding: "10px 14px", color: "var(--text-secondary)", whiteSpace: "nowrap" }}>
                    {date}
                  </td>
                  <td style={{ padding: "10px 14px", color: "var(--text-secondary)", whiteSpace: "nowrap" }}>
                    {time}
                  </td>
                  <td
                    style={{
                      padding: "10px 14px",
                      color: "var(--text-tertiary)",
                      maxWidth: 160,
                      overflow: "hidden",
                      textOverflow: "ellipsis",
                      whiteSpace: "nowrap",
                    }}
                  >
                    {t.agent}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <p style={{ fontSize: 11, color: "var(--text-tertiary)", textAlign: "center" }}>
        Showing last 20 trades · Live data requires Kite Connect
      </p>
    </div>
  );
}
