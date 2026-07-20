"use client";

/**
 * PortfolioTab — Quartr-design portfolio page.
 *
 * Visuals ported from frontend-quartr/src/pages/Dashboard.jsx (PortfolioTab),
 * with the YieldTable section deliberately excluded per request. The data
 * path uses pivot's existing API: getPortfolioSummary, getPortfolioHoldings,
 * and getPortfolioPerformance (the REAL per-user historical value series).
 *
 * Sections (top → bottom):
 *   1. Page title (serif).
 *   2. Performance chart with range pills (1M / 3M / 6M / 1Y / 5Y), driven by
 *      the real GET /api/portfolio/performance series — no synthetic line and
 *      no fabricated benchmark; honest loading / error / empty states. The
 *      footer strip below it is per-user (real total return + concentration).
 *   3. Holdings table (sortable, ticker tag with sector subtext).
 *   4. Asset Allocation — donut + legend across Sectors / Stocks.
 *   5. Diversification Score — your score vs community median, narrative line.
 *
 * Theme tokens are pulled from globals.css so light + dark both work.
 */

import { useEffect, useId, useMemo, useRef, useState } from "react";
import { Area, AreaChart, CartesianGrid, XAxis, YAxis } from "recharts";
import {
  ChartContainer,
  ChartTooltip,
  ChartTooltipContent,
  type ChartConfig,
} from "@/components/ui/chart";
import {
  AlertCircle,
  ChevronDown,
  ChevronUp,
  ChevronsUpDown,
  Clock,
  Loader2,
  RefreshCw,
  Wallet,
  X,
} from "lucide-react";
import Link from "next/link";
import { Skeleton } from "@/components/ui/skeleton";
import { Button } from "@/components/ui/button";
import { StockHoverActions } from "@/components/StockHoverActions";
import {
  getPortfolioHoldings,
  getPortfolioSummary,
  getPaperFills,
  getOrderHistory,
  getOpenOrders,
  cancelOrder,
  type Holding,
  type PortfolioSummary,
  type OpenOrder,
} from "@/lib/api";
import { toast } from "sonner";
import { isError } from "@/lib/types";
import {
  getPortfolioScores,
  getPortfolioPerformance,
  type PortfolioScoresResponse,
  type PortfolioPerformance,
  type PerformancePoint,
  type PerformancePeriod,
} from "@/lib/portfolioApi";
import { useTradingMode } from "@/lib/trading-mode";
import { useLiveQuote } from "@/hooks/useLiveQuote";
import { useCompanyLogos } from "@/hooks/useCompanyLogos";

// ---------------------------------------------------------------------------
// Static reference maps (Quartr parity)
// ---------------------------------------------------------------------------

/** The backend now returns a rich `sector` on every Holding (hand-map →
 *  screener universe label → "Other"). This tiny local map is only a last-
 *  resort fallback for the rare row that arrives without one (e.g. a cached
 *  pre-upgrade payload). Prefer `sectorOf(h)` — never read this map directly. */
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
/** Plain grouped number (no ₹ symbol) — matches the broker-style holdings
 *  list, e.g. "18,410.00", "+5,970.00". */
function fmtPlain(n: number, opts: { sign?: boolean } = {}): string {
  const { sign = false } = opts;
  const s = sign ? (n >= 0 ? "+" : "−") : n < 0 ? "−" : "";
  return (
    s +
    Math.abs(n).toLocaleString("en-IN", {
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
    })
  );
}

function holdingValue(h: Holding): number {
  return h.last_price * h.quantity;
}

/** Resolved sector for a holding: the backend's rich label first, then the
 *  small local fallback map, then "Other". Single source of truth so the
 *  holdings table, the concentration stat, and the allocation donut all agree. */
function sectorOf(h: Holding): string {
  return h.sector || SECTOR_MAP[h.tradingsymbol] || "Other";
}

// ---------------------------------------------------------------------------
// Outer component
// ---------------------------------------------------------------------------

type FetchState =
  | { kind: "loading" }
  | { kind: "error"; message: string }
  | { kind: "ok"; summary: PortfolioSummary; holdings: Holding[] };

type PortfolioView = "overview" | "orders" | "history";

export function PortfolioTab(): React.ReactElement {
  const [view, setView] = useState<PortfolioView>("overview");
  // Count of open (cancellable) orders — drives the badge on the Orders pill.
  // Fetched independently of the tab so the badge is visible from Overview.
  const [openOrderCount, setOpenOrderCount] = useState<number>(0);
  const [state, setState] = useState<FetchState>({ kind: "loading" });
  // Bumped on mode-changes and retries so PerformanceChart + PortfolioScores
  // re-fetch in lockstep with the summary + holdings. NOT bumped on initial
  // mount — children fire their own useEffect([reloadKey]) on mount, so
  // bumping here on mount caused a redundant second fetch (the 3x regression).
  const [scoresReloadKey, setScoresReloadKey] = useState(0);
  // Re-fetch whenever the global trading mode flips: getPortfolioSummary /
  // getPortfolioHoldings are mode-aware, so this swaps the page between real
  // and paper data with no other change.
  const mode = useTradingMode();
  // Tracks the mode value from the previous effect run so we can distinguish
  // a genuine mode change (needs a key bump) from the initial mount (where
  // prevMode === mode and we must NOT bump — children already fetch at key=0).
  const prevModeRef = useRef(mode);

  // Stamp of the last successful/attempted fetch — drives the stale-on-return
  // refetch below (keep-alive tabs stay mounted, so "came back to this tab"
  // never remounts the component).
  const lastFetchAtRef = useRef(0);

  // Fetches summary + holdings only; does NOT bump scoresReloadKey. Called by
  // the mode-change effect on every run (including initial mount) and indirectly
  // by `load()` below for full reloads (Retry button).
  const loadSummary = (): void => {
    lastFetchAtRef.current = Date.now();
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

  // Full reload: bumps scoresReloadKey so PerformanceChart + PortfolioScores
  // re-fetch in lockstep. Used by the Retry button only (not by the effect).
  const load = (): void => {
    setScoresReloadKey((k) => k + 1);
    loadSummary();
  };

  useEffect(() => {
    // On initial mount prevModeRef.current === mode (both hold the initial
    // value), so changed=false and we only call loadSummary(). Under React
    // StrictMode the effect fires twice with the same mode, so both runs also
    // see changed=false — no spurious key bump. On a genuine mode flip
    // changed=true and we bump so children re-fetch under the new mode.
    const changed = prevModeRef.current !== mode;
    prevModeRef.current = mode;
    loadSummary();
    if (changed) {
      setScoresReloadKey((k) => k + 1);
    }
  }, [mode]);

  // Keep the ref pointing at the latest `load` so the mount-once listeners
  // below never call a stale closure.
  const loadRef = useRef(load);
  loadRef.current = load;
  const rootRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    // 1. A trade/deploy anywhere in the app (order ticket, chat confirm,
    //    basket/opinion deploy, agent launch) broadcasts this event from
    //    lib/api — refetch everything so positions show up immediately.
    const onDirty = (): void => loadRef.current();
    window.addEventListener("pivot:portfolio-dirty", onDirty);
    // 2. Keep-alive tabs never remount, so returning to Portfolio shows
    //    whatever was fetched last. When this tab becomes visible again and
    //    the data is older than 15s, refetch.
    const el = rootRef.current;
    let observer: IntersectionObserver | null = null;
    if (el && typeof IntersectionObserver !== "undefined") {
      observer = new IntersectionObserver((entries) => {
        for (const e of entries) {
          if (e.isIntersecting && Date.now() - lastFetchAtRef.current > 15_000) {
            loadRef.current();
          }
        }
      });
      observer.observe(el);
    }
    return () => {
      window.removeEventListener("pivot:portfolio-dirty", onDirty);
      observer?.disconnect();
    };
  }, []);

  // Open-order count for the Orders pill badge. Kept separate from the tab's
  // own fetch so the badge shows from any tab. Re-runs on mode flips and on
  // scoresReloadKey bumps (Retry / cancel-triggered reloads).
  useEffect(() => {
    let alive = true;
    getOpenOrders().then((r) => {
      if (!alive) return;
      setOpenOrderCount(isError(r) ? 0 : r.data.length);
    });
    return () => {
      alive = false;
    };
  }, [mode, scoresReloadKey]);

  return (
    <div ref={rootRef} data-testid="portfolio-tab" style={{ background: "var(--bg-base)" }}>
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
            borderRadius: "var(--radius-sm)",
          }}
        >
          {(["overview", "orders", "history"] as const).map((v) => {
            const active = view === v;
            const label =
              v === "overview" ? "Overview" : v === "orders" ? "Orders" : "History";
            const showBadge = v === "orders" && openOrderCount > 0;
            return (
              <button
                key={v}
                type="button"
                onClick={() => setView(v)}
                aria-pressed={active}
                data-testid={`portfolio-view-${v}`}
                style={{
                  display: "inline-flex",
                  alignItems: "center",
                  gap: 6,
                  padding: "6px 14px",
                  border: "none",
                  cursor: "pointer",
                  borderRadius: "var(--radius-xs)",
                  fontSize: 12,
                  fontFamily: "var(--font-ui)",
                  fontWeight: 500,
                  background: active ? "var(--text-primary)" : "transparent",
                  color: active ? "var(--bg-primary)" : "var(--text-secondary)",
                  transition:
                    "color 0.2s var(--ease-quartr), background-color 0.2s var(--ease-quartr)",
                }}
              >
                {label}
                {showBadge && (
                  <span
                    aria-label={`${openOrderCount} pending`}
                    style={{
                      display: "inline-flex",
                      alignItems: "center",
                      justifyContent: "center",
                      minWidth: 16,
                      height: 16,
                      padding: "0 4px",
                      borderRadius: 8,
                      fontSize: 10,
                      fontWeight: 600,
                      lineHeight: 1,
                      background: active
                        ? "var(--bg-primary)"
                        : "rgba(245,158,11,0.16)",
                      color: active ? "var(--text-primary)" : "#b45309",
                    }}
                  >
                    {openOrderCount}
                  </span>
                )}
              </button>
            );
          })}
        </div>
      </div>

      {view === "orders" && (
        <PendingOrders
          onCountChange={setOpenOrderCount}
          onCancelled={() => setScoresReloadKey((k) => k + 1)}
        />
      )}

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

      {/* Performance + Scores are independent GETs (getPortfolioPerformance /
          getPortfolioScores) — mount them unconditionally so their own fetch
          effects fire in the same tick as `load()`'s summary+holdings
          request, instead of waiting for `state` to become "ok" first. That
          conditional-mount gate was a frontend-side sequential dependency:
          scores/performance only started once summary+holdings had already
          round-tripped. `summary`/`holdings` are only used for header/footer
          display here, so a null summary renders a lightweight skeleton in
          their place until the top-level fetch resolves. */}
      {view === "overview" && (
        <PerformanceChart
          summary={state.kind === "ok" ? state.summary : null}
          holdings={state.kind === "ok" ? state.holdings : []}
          reloadKey={scoresReloadKey}
        />
      )}

      {view === "overview" && state.kind === "ok" && (
        <>
          {/* Mobile-only P&L strip above the holdings (on desktop these
              figures already live in the top bar). */}
          <PnlStripMobile summary={state.summary} />

          <Section label="Holdings">
            <Card padding={0} style={{ overflow: "hidden", background: "var(--bg-base)" }}>
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
                <>
                  {/* Desktop: sortable table. Mobile: broker-style stacked
                      cards (Qty·Avg + return% / symbol + P&L / invested + LTP). */}
                  <div className="hidden lg:block">
                    <HoldingsTable holdings={state.holdings} />
                  </div>
                  <div className="lg:hidden">
                    <HoldingsListMobile holdings={state.holdings} />
                  </div>
                </>
              )}
            </Card>
          </Section>

          <AssetAllocation holdings={state.holdings} />
        </>
      )}

      {view === "overview" && <PortfolioScores reloadKey={scoresReloadKey} />}
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
//
// `summary` is `null` while the parent's summary+holdings fetch is still in
// flight — PerformanceChart now mounts immediately (in parallel with that
// fetch) rather than waiting for it, so this renders a skeleton in place of
// the real figure until it resolves.
function PortfolioValueHead({
  summary,
}: {
  summary: PortfolioSummary | null;
}): React.ReactElement {
  if (!summary) {
    return (
      <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
        <Skeleton style={{ height: 36, width: 160 }} />
        <Skeleton style={{ height: 14, width: 200 }} />
      </div>
    );
  }
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
// PerformanceChart — real per-user portfolio value series.
//
// The line is driven entirely by GET /api/portfolio/performance (qty × close
// history summed across holdings, computed server-side). NO synthetic series
// and NO fabricated benchmark — empty/failed fetches render honest states.
// ---------------------------------------------------------------------------

// Only the periods the backend actually serves (yfinance-backed). 1W and ALL
// are intentionally absent: the endpoint can't produce them, so we don't fake
// a label for a window it never computed.
const RANGES: { id: PerformancePeriod; label: string; longLabel: string }[] = [
  { id: "1M", label: "1M", longLabel: "1 month"  },
  { id: "3M", label: "3M", longLabel: "3 months" },
  { id: "6M", label: "6M", longLabel: "6 months" },
  { id: "1Y", label: "1Y", longLabel: "1 year"   },
  { id: "5Y", label: "5Y", longLabel: "5 years"  },
];

type PerfState =
  | { kind: "loading" }
  | { kind: "error"; message: string }
  | { kind: "empty" }
  | { kind: "ok"; perf: PortfolioPerformance };

function PerformanceChart({
  summary,
  holdings,
  reloadKey,
}: {
  summary: PortfolioSummary | null;
  holdings: Holding[];
  reloadKey: number;
}): React.ReactElement {
  const [rangeId, setRangeId] = useState<PerformancePeriod>("1Y");
  const [perfState, setPerfState] = useState<PerfState>({ kind: "loading" });
  // True while a range switch is fetching BEHIND an already-visible chart.
  // The chart stays mounted (dimmed) so the incoming series MORPHS from the
  // old shape instead of flashing through a skeleton.
  const [switching, setSwitching] = useState(false);
  // Bumped by Retry to force a re-fetch of the same range.
  const [retryKey, setRetryKey] = useState(0);

  // Per-range series cache. Revisiting a range morphs instantly with no
  // fetch. Cleared on reloadKey/retryKey (trading-mode flips, Retry) so a
  // mode switch never shows the other book's curve.
  const cacheRef = useRef<Map<PerformancePeriod, PortfolioPerformance>>(
    new Map(),
  );

  useEffect(() => {
    cacheRef.current.clear();
  }, [reloadKey, retryKey]);

  // Fetch the real series on range change and in lockstep with the parent
  // load()/trading-mode flips (via reloadKey) and Retry (via retryKey). A
  // stale guard drops responses from a superseded request so fast range
  // toggles can't race.
  useEffect(() => {
    let active = true;
    const cached = cacheRef.current.get(rangeId);
    if (cached) {
      setPerfState({ kind: "ok", perf: cached });
      setSwitching(false);
      return;
    }
    // Keep an existing curve on screen while the new range loads — that
    // persistent mount is what lets recharts animate old shape → new shape.
    setPerfState((prev) => {
      if (prev.kind === "ok") {
        setSwitching(true);
        return prev;
      }
      return { kind: "loading" };
    });
    getPortfolioPerformance(rangeId)
      .then((res) => {
        if (!active) return;
        setSwitching(false);
        if (isError(res)) {
          // 404 == "no holdings" / no history → honest empty state, not a wall.
          if (res.error.code === "http_404" || res.error.code === "not_found") {
            setPerfState({ kind: "empty" });
            return;
          }
          setPerfState({ kind: "error", message: res.error.message });
          return;
        }
        const pts = res.data.points ?? [];
        // A single point can't draw a line; treat as empty (honest).
        if (pts.length < 2) {
          setPerfState({ kind: "empty" });
          return;
        }
        cacheRef.current.set(rangeId, res.data);
        setPerfState({ kind: "ok", perf: res.data });
      })
      .catch((err: unknown) => {
        if (!active) return;
        setSwitching(false);
        const msg = err instanceof Error ? err.message : "Network error";
        setPerfState({ kind: "error", message: msg });
      });
    return () => {
      active = false;
    };
  }, [rangeId, reloadKey, retryKey]);

  const retry = (): void => setRetryKey((k) => k + 1);

  // Range pills — rendered top-right of the header on sm+, and below the
  // chart on phone (see the two breakpoint-gated wrappers below).
  const rangePills = (
    <div
      className="flex w-full sm:inline-flex sm:w-auto"
      style={{
        gap: 2,
        padding: 2,
        background: "var(--bg-base)",
        border: "none",
        borderRadius: "var(--radius-sm)",
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
  );

  return (
    <Section>
      {/* Header row lives OUTSIDE the card — the portfolio value heads the
          chart that visualises it (value above its own line). On sm+ the range
          pills sit opposite (top-right); on phone they move below the chart. */}
      <div
        className="flex flex-wrap items-start"
        style={{ columnGap: 14, rowGap: 14, marginBottom: 8 }}
      >
        <PortfolioValueHead summary={summary} />
        {/* Pills in header — sm+ only (hidden on phone). */}
        <div className="perf-pills hidden sm:flex sm:ml-auto sm:w-auto sm:justify-end">
          {rangePills}
        </div>
      </div>

      <div style={{ padding: "22px 0 0" }}>
        {perfState.kind === "loading" && <PerformanceChartSkeleton />}
        {perfState.kind === "error" && (
          <PerformanceChartError message={perfState.message} onRetry={retry} />
        )}
        {perfState.kind === "empty" && <PerformanceChartEmpty />}
        {perfState.kind === "ok" && (
          <div
            aria-busy={switching}
            style={{
              opacity: switching ? 0.55 : 1,
              transition: "opacity 0.25s var(--ease-quartr)",
            }}
          >
            <PerformanceAreaChart points={perfState.perf.points} />
          </div>
        )}
      </div>

      {/* Footer — dynamic, per-user. Driven entirely by the live summary +
          holdings (never the seeded series): total return, holding count,
          and the user's largest single-name / sector concentration. */}
      <PerformanceFooter summary={summary} holdings={holdings} />

      {/* Pills below chart — phone only (full width, scrolls if needed). */}
      <div
        className="perf-pills flex w-full justify-start sm:hidden"
        style={{
          marginTop: 14,
          overflowX: "auto",
          WebkitOverflowScrolling: "touch",
          scrollbarWidth: "none",
        }}
      >
        {rangePills}
      </div>
    </Section>
  );
}

// Shared chart geometry so the loading skeleton, empty/error placeholders, and
// the real chart all occupy exactly the same box (no layout shift on resolve).
// 216 = ~190px plot area + the area chart's date-tick row underneath.
const CHART_H = 216;

/** Loading skeleton — same height as the chart so resolving doesn't jump. */
function PerformanceChartSkeleton(): React.ReactElement {
  return (
    <div data-testid="portfolio-perf-loading" style={{ width: "100%" }}>
      <Skeleton style={{ width: "100%", height: CHART_H, borderRadius: 10 }} />
    </div>
  );
}

/** Honest empty state — no history to draw, no fabricated line. */
function PerformanceChartEmpty(): React.ReactElement {
  return (
    <div
      data-testid="portfolio-perf-empty"
      className="flex flex-col items-center justify-center text-center"
      style={{
        height: CHART_H,
        border: "1px dashed var(--glass-border)",
        borderRadius: 10,
        padding: 16,
      }}
    >
      <p style={{ fontSize: 13, fontWeight: 500, color: "var(--text-primary)" }}>
        No performance history yet
      </p>
      <p style={{ fontSize: 12, color: "var(--text-tertiary)", marginTop: 4 }}>
        Once your holdings have price history, your portfolio value over time
        will chart here.
      </p>
    </div>
  );
}

/** Error state with a Retry that re-fetches the same range. */
function PerformanceChartError({
  message,
  onRetry,
}: {
  message: string;
  onRetry: () => void;
}): React.ReactElement {
  return (
    <div
      role="alert"
      data-testid="portfolio-perf-error"
      className="flex flex-col items-center justify-center text-center"
      style={{
        height: CHART_H,
        border: "1px solid var(--glass-border)",
        borderRadius: 10,
        padding: 16,
      }}
    >
      <AlertCircle
        className="mb-2 h-5 w-5"
        style={{ color: "var(--color-loss)" }}
        aria-hidden="true"
      />
      <p style={{ fontSize: 13, fontWeight: 500, color: "var(--text-primary)" }}>
        Couldn&apos;t load performance
      </p>
      <p style={{ fontSize: 12, color: "var(--text-tertiary)", marginTop: 4 }}>
        {message}
      </p>
      <button
        type="button"
        onClick={onRetry}
        className="mt-3 inline-flex items-center"
        style={{
          gap: 8,
          padding: "5px 11px",
          background: "transparent",
          border: "1px solid var(--glass-border-hover)",
          borderRadius: "var(--radius-sm)",
          color: "var(--text-primary)",
          fontSize: 12,
          fontWeight: 500,
          cursor: "pointer",
          transition:
            "color 0.2s var(--ease-quartr), border-color 0.2s var(--ease-quartr)",
        }}
      >
        <RefreshCw size={13} aria-hidden="true" />
        Retry
      </button>
    </div>
  );
}

// ---------------------------------------------------------------------------
// PerformanceAreaChart — the portfolio value curve as a gradient area chart
// (recharts + the shadcn chart wrapper; the ui.shadcn.com "Area Chart —
// Interactive" treatment). Replaces the hand-rolled PerformanceSvg.
//
// The parent keeps this MOUNTED across range switches and swaps `points` in
// place — recharts then morphs the old path into the new one, which is the
// whole point of the port. Never key this component by range: a remount
// kills the morph.
// ---------------------------------------------------------------------------

/** Cap on rendered points — 5Y of daily history is ~1,250 raw points, which
 *  drags both the natural-curve fit and the morph animation. Downsampling
 *  keeps REAL points only (value + timestamp paired), never synthesises. */
const AREA_MAX_POINTS = 160;

function PerformanceAreaChart({
  points,
}: {
  points: PerformancePoint[];
}): React.ReactElement {
  // useId → stable per-instance SVG gradient id (colons stripped: they're
  // invalid inside url(#…) references).
  const gradientId = `perf-fill-${useId().replace(/:/g, "")}`;

  const data = useMemo(() => {
    let ds = points;
    if (points.length > AREA_MAX_POINTS) {
      const out: PerformancePoint[] = [];
      for (let i = 0; i < AREA_MAX_POINTS; i++) {
        out.push(
          points[Math.round((i / (AREA_MAX_POINTS - 1)) * (points.length - 1))]!,
        );
      }
      ds = out;
    }
    return ds.map((p) => ({ t: p.t, value: p.v }));
  }, [points]);

  // Tight Y domain (±6% pad): recharts' default area domain starts at 0,
  // which would flatten a ₹-lakh portfolio curve into a ribbon.
  const [yMin, yMax] = useMemo(() => {
    let lo = Infinity;
    let hi = -Infinity;
    for (const d of data) {
      if (d.value < lo) lo = d.value;
      if (d.value > hi) hi = d.value;
    }
    const pad = Math.max(1, (hi - lo) * 0.06);
    return [lo - pad, hi + pad];
  }, [data]);

  // Stroke + gradient follow the window's real drift — same profit-green /
  // loss-red semantic the old line used.
  const isUp =
    data.length > 1 && data[data.length - 1]!.value >= data[0]!.value;
  const color = isUp ? "var(--color-profit)" : "var(--color-loss)";
  const chartConfig = {
    value: { label: "Portfolio", color },
  } satisfies ChartConfig;

  // Window span decides the tick style: short windows → "5 Jun",
  // multi-month/-year windows → "Jun '25".
  const spanDays = useMemo(() => {
    if (data.length < 2) return 0;
    const first = new Date(data[0]!.t).getTime();
    const last = new Date(data[data.length - 1]!.t).getTime();
    return (last - first) / 86_400_000;
  }, [data]);
  const fmtTick = (iso: string): string => {
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return "";
    if (spanDays <= 120) {
      return d.toLocaleDateString("en-IN", { day: "numeric", month: "short" });
    }
    return d
      .toLocaleDateString("en-IN", { month: "short", year: "2-digit" })
      .replace(" ", " '");
  };
  const fmtTipLabel = (label: React.ReactNode): React.ReactNode => {
    const d = new Date(String(label));
    if (Number.isNaN(d.getTime())) return label;
    return d.toLocaleDateString("en-IN", {
      day: "numeric",
      month: "short",
      year: "numeric",
    });
  };

  return (
    <ChartContainer
      config={chartConfig}
      style={{ height: CHART_H }}
      data-testid="portfolio-perf-area"
    >
      <AreaChart data={data} margin={{ top: 6, right: 4, bottom: 0, left: 4 }}>
        <defs>
          <linearGradient id={gradientId} x1="0" y1="0" x2="0" y2="1">
            <stop offset="5%" stopColor="var(--color-value)" stopOpacity={0.55} />
            <stop offset="95%" stopColor="var(--color-value)" stopOpacity={0.04} />
          </linearGradient>
        </defs>
        <CartesianGrid vertical={false} stroke="var(--glass-border)" />
        <XAxis
          dataKey="t"
          tickLine={false}
          axisLine={false}
          tickMargin={10}
          minTickGap={48}
          tick={{
            fontSize: 11,
            fill: "var(--text-tertiary)",
            fontFamily: "var(--font-ui)",
          }}
          tickFormatter={fmtTick}
        />
        <YAxis hide domain={[yMin, yMax]} />
        <ChartTooltip
          cursor={{
            stroke: "var(--text-tertiary)",
            strokeDasharray: "3 3",
            strokeOpacity: 0.5,
          }}
          content={
            <ChartTooltipContent
              labelFormatter={fmtTipLabel}
              formatter={(value) => (
                <>
                  <span style={{ color: "var(--text-tertiary)" }}>
                    Portfolio
                  </span>
                  <span
                    style={{
                      marginLeft: "auto",
                      fontWeight: 600,
                      letterSpacing: "-0.01em",
                    }}
                  >
                    {fmtRupee(Number(value), { max: 0 })}
                  </span>
                </>
              )}
            />
          }
        />
        <Area
          dataKey="value"
          type="natural"
          fill={`url(#${gradientId})`}
          stroke="var(--color-value)"
          strokeWidth={1.5}
          animationDuration={600}
          animationEasing="ease-in-out"
          activeDot={{ r: 4, strokeWidth: 2, stroke: "var(--bg-base)" }}
        />
      </AreaChart>
    </ChartContainer>
  );
}

// ---------------------------------------------------------------------------
// PerformanceFooter — dynamic caption under the chart.
//
// Every figure here is REAL, drawn from the live portfolio summary +
// holdings: total return %, holding count, and the user's largest single
// position / sector by market value. No seeded series, no hardcoded names.
// Honest empty state when the book is empty.
// ---------------------------------------------------------------------------

function PerformanceFooter({
  summary,
  holdings,
}: {
  summary: PortfolioSummary | null;
  holdings: Holding[];
}): React.ReactElement {
  const stats = useMemo(() => {
    const total = holdings.reduce((s, h) => s + holdingValue(h), 0);
    if (holdings.length === 0 || total <= 0) {
      return null;
    }
    // Largest single holding by market value.
    let topHolding = holdings[0]!;
    for (const h of holdings) {
      if (holdingValue(h) > holdingValue(topHolding)) topHolding = h;
    }
    const topHoldingPct = (holdingValue(topHolding) / total) * 100;

    // Largest sector by market value (backend sector, falling back to "Other"
    // — same convention as Asset Allocation).
    const bySector = new Map<string, number>();
    for (const h of holdings) {
      const sector = sectorOf(h);
      bySector.set(sector, (bySector.get(sector) ?? 0) + holdingValue(h));
    }
    let topSector = "Other";
    let topSectorVal = -1;
    for (const [sector, val] of bySector) {
      if (val > topSectorVal) {
        topSectorVal = val;
        topSector = sector;
      }
    }
    const topSectorPct = (topSectorVal / total) * 100;

    return {
      topSymbol: topHolding.tradingsymbol,
      topHoldingPct,
      topSector,
      topSectorPct,
    };
  }, [holdings]);

  // Parent summary+holdings fetch still in flight (distinct from "no
  // holdings" below, which is a real, resolved empty state) — a slim
  // skeleton instead of the real footer or a premature empty message.
  if (!summary) {
    return <Skeleton style={{ marginTop: 10, height: 13, width: 260 }} />;
  }

  if (!stats) {
    return (
      <div
        style={{
          marginTop: 10,
          fontSize: 11,
          color: "var(--text-tertiary)",
          whiteSpace: "nowrap",
        }}
      >
        No holdings yet — your performance summary will appear here once you
        place a trade.
      </div>
    );
  }

  const returnPos = summary.total_pnl_pct >= 0;

  return (
    <div
      className="flex flex-wrap items-center"
      style={{
        marginTop: 10,
        columnGap: 14,
        rowGap: 6,
        fontSize: 11,
        color: "var(--text-tertiary)",
      }}
      data-testid="portfolio-perf-footer"
    >
      <FooterStat
        label="total return"
        value={fmtPct(summary.total_pnl_pct)}
        valueColor={returnPos ? "var(--color-profit)" : "var(--color-loss)"}
      />
      <FooterStat
        label={summary.num_holdings === 1 ? "holding" : "holdings"}
        value={String(summary.num_holdings)}
      />
      <FooterStat
        label={`top — ${stats.topSymbol}`}
        value={`${stats.topHoldingPct.toFixed(1)}%`}
      />
      <FooterStat
        label={`sector — ${stats.topSector}`}
        value={`${stats.topSectorPct.toFixed(1)}%`}
      />
    </div>
  );
}

function FooterStat({
  label,
  value,
  valueColor,
}: {
  label: string;
  value: string;
  valueColor?: string;
}): React.ReactElement {
  return (
    <span
      className="inline-flex items-center"
      style={{ gap: 4, whiteSpace: "nowrap" }}
    >
      <span
        style={{
          fontFamily: "var(--font-mono)",
          color: valueColor ?? "var(--text-secondary)",
        }}
      >
        {value}
      </span>
      <span style={{ color: "var(--text-tertiary)" }}>{label}</span>
    </span>
  );
}

// ---------------------------------------------------------------------------
// PnlStripMobile — two-cell Total P&L / Today's P&L summary shown above the
// holdings on phones (on desktop these figures live in the global top bar).
// ---------------------------------------------------------------------------

function PnlStripMobile({
  summary,
}: {
  summary: PortfolioSummary;
}): React.ReactElement {
  const totalPos = summary.total_pnl >= 0;
  const dayPos = summary.day_pnl >= 0;
  const labelStyle: React.CSSProperties = {
    fontSize: 11,
    fontWeight: 500,
    letterSpacing: "0.04em",
    textTransform: "uppercase",
    color: "var(--text-tertiary)",
  };
  const totalColor = totalPos ? "var(--color-profit)" : "var(--color-loss)";
  const dayColor = dayPos ? "var(--color-profit)" : "var(--color-loss)";
  // Phone-only. No background fills — Total P&L as the hero on the left,
  // Today's P&L beside it on the right (bottom-aligned). The hide class
  // lives on this wrapper (which has NO inline `display`) so it never
  // leaks onto desktop.
  return (
    <div className="sm:hidden" style={{ marginBottom: 28 }}>
      <div
        data-testid="portfolio-pnl-strip"
        style={{
          display: "flex",
          alignItems: "flex-start",
          justifyContent: "space-between",
          gap: 16,
        }}
      >
        {/* Total P&L — hero */}
        <div>
          <div style={labelStyle}>Total P&amp;L</div>
          <div
            style={{
              marginTop: 6,
              display: "flex",
              alignItems: "baseline",
              gap: 10,
              color: totalColor,
            }}
          >
            <span
              style={{
                fontSize: 24,
                fontWeight: 650,
                letterSpacing: "-0.02em",
                lineHeight: 1,
              }}
            >
              {fmtRupee(summary.total_pnl, { sign: true, max: 0 })}
            </span>
            <span style={{ fontSize: 14, fontWeight: 600 }}>
              {fmtPct(summary.total_pnl_pct)}
            </span>
          </div>
        </div>
        {/* Today's P&L — beside it, right-aligned */}
        <div style={{ textAlign: "right" }}>
          <div style={labelStyle}>Today&apos;s P&amp;L</div>
          <div
            style={{
              marginTop: 6,
              fontSize: 24,
              fontWeight: 650,
              letterSpacing: "-0.02em",
              lineHeight: 1,
              color: dayColor,
            }}
          >
            {fmtRupee(summary.day_pnl, { sign: true, max: 0 })}
          </div>
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// HoldingsListMobile — broker-style stacked cards (Groww layout): a row of
// Qty·Avg + total return %, the symbol + absolute P&L, and Invested + live LTP
// with the day move. Shown only on phones; desktop keeps the sortable table.
// ---------------------------------------------------------------------------

function HoldingsListMobile({
  holdings,
}: {
  holdings: Holding[];
}): React.ReactElement {
  const sorted = useMemo(
    () => [...holdings].sort((a, b) => b.pnl - a.pnl),
    [holdings],
  );
  return (
    <div data-testid="holdings-list-mobile">
      {sorted.map((h, i) => (
        <HoldingCardMobile
          key={`${h.exchange}:${h.tradingsymbol}`}
          holding={h}
          last={i === sorted.length - 1}
        />
      ))}
    </div>
  );
}

function HoldingCardMobile({
  holding: h,
  last,
}: {
  holding: Holding;
  last: boolean;
}): React.ReactElement {
  const liveQuote = useLiveQuote(h.tradingsymbol);
  const ltp = liveQuote.ltp ?? h.last_price;
  const invested = h.average_price * h.quantity;
  const pnlPct = invested > 0 ? (h.pnl / invested) * 100 : 0;
  const pnlColor = h.pnl >= 0 ? "var(--color-profit)" : "var(--color-loss)";
  const dayColor =
    h.day_change_percentage >= 0 ? "var(--color-profit)" : "var(--color-loss)";
  const muted: React.CSSProperties = { fontSize: 11.5, color: "var(--text-tertiary)" };

  return (
    <Link
      href={`/stock/${encodeURIComponent(h.tradingsymbol)}`}
      className="block"
      data-testid={`holding-m-${h.tradingsymbol}`}
      style={{
        textDecoration: "none",
        padding: "18px 4px",
        borderBottom: last ? "none" : "1px solid var(--glass-border)",
      }}
    >
      {/* Row 1 — quantity · average cost  |  total return % */}
      <div className="flex items-center justify-between" style={{ marginBottom: 8 }}>
        <span style={muted}>
          Qty. {h.quantity} · Avg. {fmtPlain(h.average_price)}
        </span>
        <span style={{ fontSize: 12.5, fontWeight: 600, color: pnlColor }}>
          {fmtPct(pnlPct)}
        </span>
      </div>
      {/* Row 2 — symbol  |  absolute P&L */}
      <div className="flex items-center justify-between" style={{ marginBottom: 8 }}>
        <span
          style={{
            fontSize: 15.5,
            fontWeight: 600,
            color: "var(--text-primary)",
            letterSpacing: "-0.015em",
          }}
        >
          {h.tradingsymbol}
        </span>
        <span style={{ fontSize: 14.5, fontWeight: 600, color: pnlColor }}>
          {fmtPlain(h.pnl, { sign: true })}
        </span>
      </div>
      {/* Row 3 — invested  |  live LTP (day move) */}
      <div className="flex items-center justify-between">
        <span style={muted}>Invested {fmtPlain(invested)}</span>
        <span style={muted}>
          LTP {fmtPlain(ltp)}{" "}
          <span style={{ color: dayColor }}>
            ({fmtPct(h.day_change_percentage, false)})
          </span>
        </span>
      </div>
    </Link>
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

function HoldingGlyph({
  symbol,
  hueKey,
  logoUrl,
}: {
  symbol: string;
  hueKey?: string;
  logoUrl?: string | null;
}): React.ReactElement {
  const initial = symbol.trim()[0]?.toUpperCase() ?? "•";
  const hue = brandGlyphHue(hueKey);
  const [errored, setErrored] = useState(false);

  // Real company logo (img.logo.dev, resolved by the backend) when we have a
  // URL and it loads; otherwise the sector-tinted first-letter monogram.
  if (logoUrl && !errored) {
    return (
      <img
        src={logoUrl}
        alt=""
        aria-hidden="true"
        width={34}
        height={34}
        loading="lazy"
        onError={() => setErrored(true)}
        style={{
          width: 34,
          height: 34,
          flexShrink: 0,
          borderRadius: "var(--radius-sm)",
          objectFit: "contain",
          background: "var(--surface-1, #fff)",
          border: "1px solid var(--glass-border)",
          padding: 4,
        }}
      />
    );
  }

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

  // Batch-resolve a real company logo per holding (cached + de-duped across
  // renders). A miss falls back to the sector-tinted monogram in HoldingGlyph.
  const logos = useCompanyLogos(holdings.map((h) => h.tradingsymbol));

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
                    background: "var(--bg-secondary)",
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
                    style={{ gap: 5 }}
                  >
                    {col.key && (
                      <Icon
                        size={12}
                        aria-hidden="true"
                        style={{ opacity: active ? 1 : 0.45, lineHeight: 0 }}
                      />
                    )}
                    {col.label}
                  </span>
                </th>
              );
            })}
          </tr>
        </thead>
        <tbody>
          {sorted.map((h) => (
            <HoldingRow
              key={`${h.exchange}:${h.tradingsymbol}`}
              holding={h}
              logoUrl={logos[h.tradingsymbol.toUpperCase()]}
            />
          ))}
        </tbody>
      </table>
    </div>
  );
}

// ---------------------------------------------------------------------------
// HoldingRow — one <tr> per holding, wired to useLiveQuote for LTP.
// ---------------------------------------------------------------------------

function HoldingRow({
  holding: h,
  logoUrl,
}: {
  holding: Holding;
  logoUrl?: string | null;
}): React.ReactElement {
  const liveQuote = useLiveQuote(h.tradingsymbol);
  const ltp = liveQuote.ltp ?? h.last_price;
  const value = ltp * h.quantity;
  // Backend-resolved sector (was a tiny hardcoded map that left most names
  // blank). "Other" is suppressed in the subtext below to avoid noise.
  const sector = sectorOf(h);
  // Total P&L and Day P&L are re-derived from the live LTP rather than the
  // one-time snapshot fields — otherwise these cells sit frozen at whatever
  // they were on page load while the LTP cell next to them keeps moving.
  // Mirrors the backend's own formulas: unrealized_pnl = qty*(mark-avg_cost);
  // day_pnl = qty*(mark-prev_close), with prev_close backed out from the
  // snapshot's per-share day_change (last_price - prev_close = day_change).
  const invested = h.average_price * h.quantity;
  const livePnl = (ltp - h.average_price) * h.quantity;
  const prevClose = h.last_price - h.day_change;
  const liveDayPnlPerShare = ltp - prevClose;
  const liveDayChangePct = invested ? ((liveDayPnlPerShare * h.quantity) / invested) * 100 : h.day_change_percentage;
  const pnlPos = livePnl >= 0;
  const dayPos = liveDayChangePct >= 0;
  // Kite-style quick-action bar, revealed while the row is hovered.
  const [hovered, setHovered] = useState(false);

  return (
    <tr
      style={{
        borderBottom: "1px solid var(--glass-border)",
        transition: "background 150ms",
      }}
      onMouseEnter={(e) => {
        e.currentTarget.style.background = "var(--bg-secondary)";
        setHovered(true);
      }}
      onMouseLeave={(e) => {
        e.currentTarget.style.background = "transparent";
        setHovered(false);
      }}
      data-testid={`holding-${h.tradingsymbol}`}
    >
      <td style={{ padding: "16px 18px", position: "relative" }}>
        <div className="inline-flex items-center" style={{ gap: 12 }}>
          <HoldingGlyph symbol={h.tradingsymbol} hueKey={sector} logoUrl={logoUrl} />
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
            {sector && sector !== "Other" && (
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
        {/* Kite-style quick actions — pinned to the symbol cell's right
            edge (same X every row), absolute so the row never grows. */}
        {hovered && (
          <StockHoverActions
            symbol={h.tradingsymbol}
            logoUrl={logoUrl}
            className="absolute"
            style={{
              // Pinned to the symbol column's right edge — one constant
              // axis for every row, never crossing into the qty column.
              right: 10,
              top: "50%",
              marginTop: -14,
              zIndex: 5,
            }}
          />
        )}
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
        {fmtRupee(livePnl, { sign: true, max: 0 })}
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
        {fmtPct(liveDayChangePct)}
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
// AssetAllocation — donut + legend across Sectors / Stocks
// ---------------------------------------------------------------------------

// Market-cap allocation was removed — holdings carry no reliable market-cap
// tier (the fundamentals source has market_cap 100% NULL), so it only ever
// showed "Unclassified". Sectors + Stocks are the honest breakdowns.
const ALLOC_TABS: { id: "sectors" | "stocks"; label: string }[] = [
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
  const [tab, setTab] = useState<"sectors" | "stocks">("sectors");
  const [hover, setHover] = useState<AllocRow | null>(null);
  const chartRef = useRef<HTMLDivElement>(null);

  // Clicking/tapping anywhere outside the donut + legend clears the
  // selection (mirrors moving the mouse away on desktop). pointerdown
  // covers both mouse and touch.
  useEffect(() => {
    if (!hover) return;
    function onDocPointerDown(e: PointerEvent): void {
      if (chartRef.current && !chartRef.current.contains(e.target as Node)) {
        setHover(null);
      }
    }
    document.addEventListener("pointerdown", onDocPointerDown);
    return () => document.removeEventListener("pointerdown", onDocPointerDown);
  }, [hover]);

  const data = useMemo(() => {
    if (!holdings || holdings.length === 0) return { total: 0, rows: [] as AllocRow[] };
    if (tab === "sectors") return aggregate(holdings, (h) => sectorOf(h));
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
          <div
            ref={chartRef}
            className="flex flex-wrap items-center justify-center lg:justify-start"
            style={{ gap: 28 }}
          >
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
                    onPointerEnter={(e) => {
                      if (e.pointerType === "mouse") setHover(seg);
                    }}
                    onPointerLeave={(e) => {
                      if (e.pointerType === "mouse") setHover(null);
                    }}
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
                    onPointerEnter={(e) => {
                      if (e.pointerType === "mouse") setHover(seg);
                    }}
                    onPointerLeave={(e) => {
                      if (e.pointerType === "mouse") setHover(null);
                    }}
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
// PortfolioScores — diversification + portfolio + community score panel.
//
// Driven entirely by GET /portfolio/scores (real, on-read math). Renders three
// 0-100 gauge cards with the sub-components + explainers the endpoint returns.
// All three scores are null (reason "no_holdings") when the book is empty →
// honest empty state, never fabricated gauges.
// ---------------------------------------------------------------------------

type ScoresState =
  | { kind: "loading" }
  | { kind: "error"; message: string }
  | { kind: "ok"; data: PortfolioScoresResponse };

function PortfolioScores({ reloadKey }: { reloadKey: number }): React.ReactElement {
  const [state, setState] = useState<ScoresState>({ kind: "loading" });

  const load = (): void => {
    setState({ kind: "loading" });
    getPortfolioScores()
      .then((res) => {
        if (isError(res)) {
          setState({ kind: "error", message: res.error.message });
          return;
        }
        setState({ kind: "ok", data: res.data });
      })
      .catch((err: unknown) => {
        const msg = err instanceof Error ? err.message : "Network error";
        setState({ kind: "error", message: msg });
      });
  };

  // Re-fetch when the trading mode / holdings change (reloadKey is driven by
  // the same `mode` that re-loads the summary + holdings above).
  useEffect(() => {
    load();
  }, [reloadKey]);

  return (
    <Section label="Portfolio Scores">
      {state.kind === "loading" && (
        <div
          className="grid"
          style={{
            gridTemplateColumns: "repeat(auto-fit, minmax(240px, 1fr))",
            gap: 16,
          }}
          data-testid="portfolio-scores-loading"
        >
          {[0, 1, 2].map((i) => (
            <Card key={i} padding="22px 24px">
              <Skeleton style={{ height: 14, width: "55%", marginBottom: 16 }} />
              <Skeleton style={{ height: 36, width: "40%", marginBottom: 16 }} />
              <Skeleton style={{ height: 8, width: "100%", marginBottom: 14 }} />
              <Skeleton style={{ height: 12, width: "90%" }} />
            </Card>
          ))}
        </div>
      )}

      {state.kind === "error" && (
        <Card padding="22px 24px">
          <div
            className="flex flex-col items-center justify-center text-center"
            role="alert"
            data-testid="portfolio-scores-error"
            style={{ gap: 8, padding: "12px 0" }}
          >
            <AlertCircle
              size={20}
              aria-hidden="true"
              style={{ color: "var(--color-loss)" }}
            />
            <p style={{ fontSize: 13, fontWeight: 500, color: "var(--text-primary)" }}>
              Couldn&apos;t load your scores
            </p>
            <p style={{ fontSize: 12, color: "var(--text-tertiary)" }}>{state.message}</p>
            <button
              type="button"
              onClick={load}
              className="mt-2 inline-flex items-center"
              style={{
                gap: 6,
                padding: "6px 12px",
                background: "transparent",
                border: "1px solid var(--glass-border-hover)",
                borderRadius: "var(--radius-sm)",
                color: "var(--text-primary)",
                fontSize: 12,
                fontWeight: 500,
                cursor: "pointer",
              }}
            >
              <RefreshCw size={13} aria-hidden="true" />
              Retry
            </button>
          </div>
        </Card>
      )}

      {state.kind === "ok" && <ScoresPanel data={state.data} />}
    </Section>
  );
}

function ScoresPanel({ data }: { data: PortfolioScoresResponse }): React.ReactElement {
  const empty =
    data.reason === "no_holdings" ||
    (!data.diversification_score &&
      !data.portfolio_score &&
      !data.community_score);

  if (empty) {
    return (
      <Card padding="22px 24px">
        <div
          className="flex flex-col items-center justify-center py-8 text-center"
          data-testid="portfolio-scores-empty"
        >
          <Wallet
            size={26}
            aria-hidden="true"
            style={{ color: "var(--text-tertiary)", marginBottom: 10 }}
          />
          <p style={{ fontSize: 14, fontWeight: 500, color: "var(--text-primary)" }}>
            Add holdings to see your scores
          </p>
          <p style={{ fontSize: 12, color: "var(--text-tertiary)", marginTop: 4, maxWidth: 320 }}>
            Once you hold positions, we&apos;ll score your diversification,
            overall portfolio quality, and how it stacks up against a benchmark.
          </p>
        </div>
      </Card>
    );
  }

  const div = data.diversification_score;
  const pf = data.portfolio_score;
  const comm = data.community_score;

  return (
    <div
      className="grid"
      style={{
        gridTemplateColumns: "repeat(auto-fit, minmax(240px, 1fr))",
        gap: 16,
      }}
      data-testid="portfolio-scores-panel"
    >
      {/* Diversification */}
      {div && (
        <ScoreCard
          title="Diversification"
          score={div.score}
          color="var(--pivot-blue)"
          rows={[
            { label: "Holdings", value: String(div.components.n_holdings) },
            { label: "Sectors", value: String(div.components.n_sectors) },
            {
              label: "Top sector",
              value: `${div.components.top_sector_pct.toFixed(1)}%`,
            },
            {
              label: "Top holding",
              value: `${div.components.top_holding_pct.toFixed(1)}%`,
            },
            { label: "HHI", value: div.components.hhi.toFixed(3) },
          ]}
        />
      )}

      {/* Portfolio score */}
      {pf && (
        <ScoreCard
          title="Portfolio Score"
          score={pf.score}
          color="var(--color-profit)"
          rows={[
            {
              label: "Diversification",
              value: pf.components.subscores.diversification.toFixed(0),
            },
            {
              label: "Concentration",
              value: pf.components.subscores.concentration_penalty.toFixed(0),
            },
            ...(pf.components.performance_available &&
            pf.components.subscores.performance !== undefined
              ? [
                  {
                    label: "Performance",
                    value: pf.components.subscores.performance.toFixed(0),
                  },
                ]
              : []),
            ...(pf.components.total_return_pct !== null
              ? [
                  {
                    label: "Total return",
                    value: fmtPct(pf.components.total_return_pct),
                    valueColor:
                      pf.components.total_return_pct >= 0
                        ? "var(--color-profit)"
                        : "var(--color-loss)",
                  },
                ]
              : [
                  {
                    label: "Total return",
                    value: "no NAV history",
                  },
                ]),
          ]}
        />
      )}

      {/* Community score */}
      {comm && (
        <ScoreCard
          title="Community Score"
          score={comm.score}
          color="var(--text-secondary)"
          rows={[
            {
              label: "Percentile",
              value: `${comm.percentile.toFixed(0)}th`,
            },
            { label: "Basis", value: comm.basis, wrap: true },
          ]}
        />
      )}
    </div>
  );
}

type ScoreRow = {
  label: string;
  value: string;
  valueColor?: string;
  /** When true, allow the value to wrap onto multiple lines (e.g. "basis"). */
  wrap?: boolean;
};

function ScoreCard({
  title,
  score,
  color,
  rows,
}: {
  title: string;
  score: number;
  color: string;
  rows: ScoreRow[];
}): React.ReactElement {
  const clamped = Math.max(0, Math.min(100, score));
  return (
    <Card padding="22px 24px">
      <div className="flex items-baseline justify-between" style={{ marginBottom: 14 }}>
        <span
          style={{
            fontFamily: "var(--font-display)",
            fontWeight: "var(--weight-display)" as unknown as number,
            fontSize: 13,
            letterSpacing: "-0.02em",
            color: "var(--text-primary)",
          }}
        >
          {title}
        </span>
        <span
          style={{
            fontFamily: "var(--font-serif)",
            fontWeight: 500,
            fontSize: 26,
            lineHeight: 1,
            letterSpacing: "-0.02em",
            fontVariantNumeric: "tabular-nums",
            color: "var(--text-primary)",
          }}
        >
          {clamped}
          <span style={{ fontSize: 13, color: "var(--text-tertiary)" }}>/100</span>
        </span>
      </div>

      {/* 0-100 meter */}
      <div
        style={{
          position: "relative",
          height: 8,
          background: "var(--bg-elevated)",
          borderRadius: 999,
          overflow: "hidden",
          marginBottom: 16,
        }}
      >
        <div
          style={{
            position: "absolute",
            inset: 0,
            width: `${clamped}%`,
            background: color,
            borderRadius: 999,
            transition: "width 0.6s var(--ease-quartr)",
          }}
        />
      </div>

      {/* Sub-components */}
      <div className="flex flex-col" style={{ gap: 7, marginBottom: 14 }}>
        {rows.map((r) => (
          <div
            key={r.label}
            className="flex items-baseline justify-between"
            style={{ gap: 14 }}
          >
            <span
              style={{ fontSize: 11.5, color: "var(--text-tertiary)", flexShrink: 0 }}
            >
              {r.label}
            </span>
            <span
              style={{
                fontFamily: r.wrap ? "var(--font-ui)" : "var(--font-mono)",
                fontSize: 11.5,
                fontWeight: 500,
                color: r.valueColor ?? "var(--text-secondary)",
                textAlign: "right",
                whiteSpace: r.wrap ? "normal" : "nowrap",
              }}
            >
              {r.value}
            </span>
          </div>
        ))}
      </div>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Loading skeleton
// ---------------------------------------------------------------------------

// Covers only the Holdings + Asset Allocation sections — both are derived
// from `state` (summary/holdings) with no independent fetch of their own, so
// they still gate on `state.kind === "ok"`. Performance and Scores are no
// longer part of this skeleton: they mount unconditionally (in parallel with
// this fetch, not after it) and render their own loading state.
function PortfolioLoading(): React.ReactElement {
  return (
    <div className="flex flex-col" style={{ gap: 28 }} data-testid="portfolio-loading">
      <Card padding={0} style={{ overflow: "hidden" }}>
        <Skeleton style={{ height: 40, width: "100%" }} />
        {[0, 1, 2, 3, 4].map((i) => (
          <Skeleton key={i} style={{ height: 56, width: "100%", marginTop: 1 }} />
        ))}
      </Card>
      <Card>
        <Skeleton style={{ height: 220, width: 220 }} />
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


// Shared cell rhythm for the Orders + History tables, matched to the
// HoldingsTable: hairline glass-border dividers, 16×18 padding, 12.5px text —
// so all three portfolio tables read as one clean, consistent surface.
const HIST_TD: React.CSSProperties = {
  padding: "16px 18px",
  fontSize: 12.5,
  borderBottom: "1px solid var(--glass-border)",
  whiteSpace: "nowrap",
};

// Numeric cell — mono, tabular, right-aligned — mirroring HoldingsTable's
// NumCell so quantities/prices line up column-clean across every table.
const HIST_TD_NUM: React.CSSProperties = {
  ...HIST_TD,
  fontFamily: "var(--font-mono)",
  fontWeight: 500,
  textAlign: "right",
  fontVariantNumeric: "tabular-nums",
  color: "var(--text-secondary)",
};

// Shared header cell for the Orders + History tables — matches HoldingsTable's
// <th> (10px uppercase, display weight, 1.5px underline, 13×18 padding).
function TxnHeadCell({
  label,
  align = "left",
}: {
  label: string;
  align?: "left" | "right";
}): React.ReactElement {
  return (
    <th
      style={{
        padding: "13px 18px",
        fontSize: 10,
        letterSpacing: "0.1em",
        textTransform: "uppercase",
        fontWeight: "var(--weight-display)" as unknown as number,
        color: "var(--text-tertiary)",
        textAlign: align,
        whiteSpace: "nowrap",
        // Same elevated header bar as HoldingsTable's <th> — lighter than the
        // rows so the head reads as a distinct band, not a blended first row.
        background: "var(--bg-secondary)",
        borderBottom: "1.5px solid var(--glass-border)",
      }}
    >
      {label}
    </th>
  );
}

// Symbol cell shared by the Orders + History tables — the same brand
// glyph + exchange-prefixed, stock-linked symbol + sector subtext as
// HoldingRow, so the first column reads identically across all three.
function TxnSymbolCell({
  symbol,
  exchange,
  logoUrl,
}: {
  symbol: string;
  exchange?: string;
  logoUrl?: string | null;
}): React.ReactElement {
  const sector = SECTOR_MAP[symbol];
  return (
    <td style={HIST_TD}>
      <div className="inline-flex items-center" style={{ gap: 12 }}>
        <HoldingGlyph symbol={symbol} hueKey={sector} logoUrl={logoUrl} />
        <div style={{ display: "flex", flexDirection: "column", gap: 3 }}>
          <Link
            href={`/stock/${encodeURIComponent(symbol)}`}
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
            <span style={{ color: "var(--text-tertiary)", fontSize: 10, fontWeight: 400 }}>
              {exchange || "NSE"}
            </span>
            {symbol}
          </Link>
          {sector && (
            <span style={{ fontSize: 11, color: "var(--text-tertiary)" }}>{sector}</span>
          )}
        </div>
      </div>
    </td>
  );
}

// Buy/Sell pill shared by the Orders + History tables.
function TxnSideBadge({ side }: { side: string }): React.ReactElement {
  const isBuy = side.toUpperCase() !== "SELL";
  return (
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
      {isBuy ? "BUY" : "SELL"}
    </span>
  );
}

// ---------------------------------------------------------------------------
// PendingOrders — the "Orders" tab. Lists still-open (cancellable) orders:
// AMOs queued while the market was closed, resting LIMIT / trigger orders, and
// anything the broker still reports as not-yet-complete. Each row can be
// cancelled before it executes. Mode-aware via getOpenOrders / cancelOrder.
// ---------------------------------------------------------------------------

function fmtOrderDateTime(iso: string): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso; // backend sends "… IST" strings
  return d.toLocaleString("en-IN", {
    day: "2-digit",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
    hour12: true,
  });
}

/** Normalise the backend's free-form status into a compact display label. */
function orderStatusLabel(o: OpenOrder): string {
  if (o.queued) return "Queued";
  const s = o.status.toLowerCase();
  if (s === "trigger pending") return "Trigger pending";
  if (s === "open" || s === "resting") return "Open";
  if (s === "registered" || s === "pending") return "Pending";
  return o.status.charAt(0).toUpperCase() + o.status.slice(1);
}

function PendingOrders({
  onCountChange,
  onCancelled,
}: {
  onCountChange: (n: number) => void;
  onCancelled: () => void;
}): React.ReactElement {
  const mode = useTradingMode();
  const [rows, setRows] = useState<OpenOrder[] | null>(null);
  const [errored, setErrored] = useState(false);
  // ids currently being cancelled — disables their button + dims the row.
  const [cancelling, setCancelling] = useState<Set<string>>(new Set());
  const logos = useCompanyLogos((rows ?? []).map((o) => o.symbol));

  useEffect(() => {
    let alive = true;
    setRows(null);
    setErrored(false);
    getOpenOrders().then((r) => {
      if (!alive) return;
      if (isError(r)) {
        setErrored(true);
        return;
      }
      setRows(r.data);
      onCountChange(r.data.length);
    });
    return () => {
      alive = false;
    };
    // onCountChange is a stable setState updater; excluded to avoid needless
    // refetches.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mode]);

  const handleCancel = async (o: OpenOrder): Promise<void> => {
    setCancelling((prev) => new Set(prev).add(o.id));
    const res = await cancelOrder(o.id);
    if (isError(res)) {
      toast.error(`Couldn't cancel ${o.symbol}`, {
        description: res.error.message,
      });
      setCancelling((prev) => {
        const next = new Set(prev);
        next.delete(o.id);
        return next;
      });
      return;
    }
    // Drop the cancelled row and republish the count.
    setRows((prev) => {
      const next = (prev ?? []).filter((x) => x.id !== o.id);
      onCountChange(next.length);
      return next;
    });
    onCancelled();
    const note = res.data.broker_note;
    if (note) {
      toast.warning(`${o.symbol} order cancelled`, { description: note });
    } else {
      toast.success(`Cancelled ${o.transaction_type} ${o.quantity} ${o.symbol}`);
    }
  };

  if (errored) {
    return (
      <div
        className="flex flex-col items-center justify-center rounded-2xl bg-card"
        style={{ gap: 6, padding: "40px 16px", textAlign: "center" }}
        role="alert"
      >
        <p style={{ fontSize: 13, fontWeight: 600, color: "var(--text-primary)" }}>
          Couldn&apos;t load orders
        </p>
        <p style={{ fontSize: 12, color: "var(--text-tertiary)", maxWidth: 340 }}>
          Something went wrong fetching your pending orders. Try switching tabs
          to retry.
        </p>
      </div>
    );
  }

  if (rows === null) {
    return (
      <div className="flex flex-col" style={{ gap: 10 }}>
        {[0, 1, 2].map((i) => (
          <Skeleton key={i} className="h-14 w-full rounded-2xl" />
        ))}
      </div>
    );
  }

  if (rows.length === 0) {
    return (
      <div
        className="flex flex-col items-center justify-center rounded-2xl bg-card"
        style={{ gap: 6, padding: "40px 16px", textAlign: "center" }}
        data-testid="pending-orders-empty"
      >
        <Clock
          className="h-5 w-5"
          style={{ color: "var(--text-tertiary)" }}
          aria-hidden="true"
        />
        <p style={{ fontSize: 13, fontWeight: 600, color: "var(--text-primary)" }}>
          No pending orders
        </p>
        <p style={{ fontSize: 12, color: "var(--text-tertiary)", maxWidth: 360 }}>
          Orders you place while the market is closed — or resting limit and
          trigger orders — appear here until they execute, and can be cancelled
          before then.
        </p>
      </div>
    );
  }

  return (
    <div className="flex flex-col" style={{ gap: 12 }} data-testid="pending-orders">
      <div
        className="overflow-x-auto"
        style={{
          WebkitOverflowScrolling: "touch",
          background: "var(--bg-base)",
          borderRadius: "var(--radius-md)",
        }}
      >
        <table
          className="w-full"
          style={{
            borderCollapse: "collapse",
            fontFamily: "var(--font-ui)",
            minWidth: 720,
          }}
        >
          <thead>
            <tr>
              <TxnHeadCell label="Symbol" />
              <TxnHeadCell label="Side" />
              <TxnHeadCell label="Type" />
              <TxnHeadCell label="Qty" align="right" />
              <TxnHeadCell label="Price (₹)" align="right" />
              <TxnHeadCell label="Placed" />
              <TxnHeadCell label="Status" />
              <TxnHeadCell label="" align="right" />
            </tr>
          </thead>
          <tbody>
            {rows.map((o) => {
              const busy = cancelling.has(o.id);
              const priceShown =
                o.price != null && o.price > 0
                  ? o.price.toLocaleString("en-IN")
                  : o.trigger_price != null && o.trigger_price > 0
                    ? `${o.trigger_price.toLocaleString("en-IN")} (trig)`
                    : "Market";
              return (
                <tr
                  key={o.id}
                  style={{ opacity: busy ? 0.5 : 1, transition: "background 150ms" }}
                  onMouseEnter={(e) => {
                    e.currentTarget.style.background = "var(--bg-secondary)";
                  }}
                  onMouseLeave={(e) => {
                    e.currentTarget.style.background = "transparent";
                  }}
                >
                  <TxnSymbolCell
                    symbol={o.symbol}
                    exchange={o.exchange}
                    logoUrl={logos[o.symbol.toUpperCase()]}
                  />
                  <td style={HIST_TD}>
                    <TxnSideBadge side={o.transaction_type} />
                  </td>
                  <td style={{ ...HIST_TD, color: "var(--text-secondary)" }}>
                    {o.order_type}
                  </td>
                  <td style={HIST_TD_NUM}>
                    {o.quantity}
                  </td>
                  <td style={HIST_TD_NUM}>
                    {priceShown}
                  </td>
                  <td style={{ ...HIST_TD, color: "var(--text-secondary)" }}>
                    {fmtOrderDateTime(o.placed_at)}
                  </td>
                  <td style={HIST_TD}>
                    <span
                      style={{
                        display: "inline-flex",
                        alignItems: "center",
                        gap: 5,
                        padding: "2px 8px",
                        borderRadius: 999,
                        fontSize: 11,
                        fontWeight: 600,
                        background: o.queued
                          ? "rgba(245,158,11,0.12)"
                          : "var(--bg-secondary)",
                        color: o.queued ? "#b45309" : "var(--text-secondary)",
                      }}
                    >
                      {o.queued && <Clock className="h-3 w-3" aria-hidden="true" />}
                      {orderStatusLabel(o)}
                    </span>
                  </td>
                  <td style={{ ...HIST_TD, textAlign: "right" }}>
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={() => void handleCancel(o)}
                      disabled={busy}
                      data-testid={`cancel-order-${o.id}`}
                      aria-label={`Cancel ${o.transaction_type} ${o.quantity} ${o.symbol}`}
                      className="h-7 gap-1.5 rounded-md border-border/70 px-2.5 text-xs font-medium text-muted-foreground transition-colors hover:bg-destructive/10 hover:text-destructive focus-visible:ring-destructive/40 [&_svg]:size-3.5"
                    >
                      {busy ? (
                        <>
                          <Loader2 className="animate-spin" aria-hidden="true" />
                          Cancelling
                        </>
                      ) : (
                        <>
                          <X aria-hidden="true" />
                          Cancel
                        </>
                      )}
                    </Button>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <p style={{ fontSize: 11, color: "var(--text-tertiary)", textAlign: "center" }}>
        Pending orders execute at the next market open. Cancel anytime before
        then.
      </p>
    </div>
  );
}

function TradeHistory(): React.ReactElement {
  // Real trade history — the paper fills journal in paper mode (the active
  // default), the registered/executed order history in live mode. Both come
  // through paper-aware `lib/api` helpers, so the same table reflects every
  // buy/sell, basket, opinion-market expression and armed agent the user ran.
  const mode = useTradingMode();
  const [rows, setRows] = useState<TradeRow[] | null>(null);
  const [errored, setErrored] = useState(false);
  // Lazy pagination: pages of PAGE fills, appended as the sentinel at the
  // bottom of the table scrolls into view. hasMore stays true while the
  // last page came back full.
  const PAGE = 20;
  const [hasMore, setHasMore] = useState(true);
  const loadingRef = useRef(false);
  const sentinelRef = useRef<HTMLDivElement | null>(null);
  const logos = useCompanyLogos((rows ?? []).map((t) => t.symbol));

  const fetchPage = async (offset: number): Promise<TradeRow[] | null> => {
    if (mode === "paper") {
      const r = await getPaperFills(PAGE, offset);
      if (isError(r)) return null;
      return r.data.map((f) => ({
        id: f.id,
        symbol: f.symbol,
        side: (f.side.toUpperCase() === "SELL" ? "SELL" : "BUY") as "BUY" | "SELL",
        quantity: f.quantity,
        price: f.fill_price,
        amount: Math.abs(f.gross_value),
        datetime: f.filled_at ?? "",
        agent: "Paper",
      }));
    }
    const r = await getOrderHistory(PAGE, offset);
    if (isError(r)) return null;
    return r.data.map((o) => ({
      id: String(o.id),
      symbol: o.symbol,
      side: (o.action.toUpperCase() === "SELL" ? "SELL" : "BUY") as "BUY" | "SELL",
      quantity: o.quantity,
      price: 0,
      amount: 0,
      datetime: o.placed_at,
      agent: o.status,
    }));
  };

  useEffect(() => {
    let alive = true;
    setRows(null);
    setErrored(false);
    setHasMore(true);
    void fetchPage(0).then((page) => {
      if (!alive) return;
      if (page === null) {
        setErrored(true);
        return;
      }
      setRows(page);
      setHasMore(page.length === PAGE);
    });
    return () => {
      alive = false;
    };
    // fetchPage closes over `mode`; re-running on mode is exactly the reset we want.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mode]);

  // Auto-load the next page when the sentinel enters the viewport.
  useEffect(() => {
    const el = sentinelRef.current;
    if (!el || !hasMore || rows === null) return;
    const obs = new IntersectionObserver((entries) => {
      if (!entries.some((e) => e.isIntersecting)) return;
      if (loadingRef.current) return;
      loadingRef.current = true;
      void fetchPage(rows.length).then((page) => {
        loadingRef.current = false;
        if (page === null) return; // transient error — sentinel retries on next scroll
        setRows((prev) => {
          const seen = new Set((prev ?? []).map((t) => t.id));
          return [...(prev ?? []), ...page.filter((t) => !seen.has(t.id))];
        });
        setHasMore(page.length === PAGE);
      });
    }, { rootMargin: "200px" });
    obs.observe(el);
    return () => obs.disconnect();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [rows, hasMore, mode]);

  const fmt = (iso: string): { date: string; time: string } => {
    if (!iso) return { date: "—", time: "—" };
    const d = new Date(iso);
    return {
      date: d.toLocaleDateString("en-IN", { day: "2-digit", month: "short", year: "numeric" }),
      time: d.toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit", hour12: true }),
    };
  };

  // Empty / loading / error states — never a fabricated row.
  if (rows !== null && rows.length === 0 && !errored) {
    return (
      <div
        className="flex flex-col items-center justify-center rounded-2xl bg-card"
        style={{ gap: 6, padding: "40px 16px", textAlign: "center" }}
      >
        <p style={{ fontSize: 13, fontWeight: 600, color: "var(--text-primary)" }}>
          No trades yet
        </p>
        <p style={{ fontSize: 12, color: "var(--text-tertiary)", maxWidth: 340 }}>
          {mode === "paper"
            ? "Your simulated trades appear here — place a buy, deploy a basket or an opinion, or arm an agent to get started."
            : "Registered and executed orders will appear here."}
        </p>
      </div>
    );
  }

  return (
    <div className="flex flex-col" style={{ gap: 12 }}>
      <div
        className="overflow-x-auto"
        style={{
          WebkitOverflowScrolling: "touch",
          background: "var(--bg-base)",
          borderRadius: "var(--radius-md)",
        }}
      >
        <table
          className="w-full"
          style={{
            borderCollapse: "collapse",
            fontFamily: "var(--font-ui)",
            minWidth: 760,
          }}
        >
          <thead>
            <tr>
              <TxnHeadCell label="Symbol" />
              <TxnHeadCell label="Side" />
              <TxnHeadCell label="Qty" align="right" />
              <TxnHeadCell label="Price (₹)" align="right" />
              <TxnHeadCell label="Amount (₹)" align="right" />
              <TxnHeadCell label="Date" />
              <TxnHeadCell label="Time" />
              <TxnHeadCell label="Agent" />
            </tr>
          </thead>
          <tbody>
            {(rows ?? []).map((t) => {
              const { date, time } = fmt(t.datetime);
              return (
                <tr
                  key={t.id}
                  style={{
                    background: "transparent",
                    transition: "background 150ms",
                  }}
                  onMouseEnter={(e) => {
                    e.currentTarget.style.background = "var(--bg-secondary)";
                  }}
                  onMouseLeave={(e) => {
                    e.currentTarget.style.background = "transparent";
                  }}
                >
                  <TxnSymbolCell symbol={t.symbol} logoUrl={logos[t.symbol.toUpperCase()]} />
                  <td style={HIST_TD}>
                    <TxnSideBadge side={t.side} />
                  </td>
                  <td style={HIST_TD_NUM}>{t.quantity}</td>
                  <td style={HIST_TD_NUM}>
                    {t.price > 0 ? t.price.toLocaleString("en-IN") : "—"}
                  </td>
                  <td style={{ ...HIST_TD_NUM, fontWeight: 500, color: "var(--text-primary)" }}>
                    {t.amount > 0 ? `₹${t.amount.toLocaleString("en-IN")}` : "—"}
                  </td>
                  <td style={{ ...HIST_TD, color: "var(--text-secondary)" }}>{date}</td>
                  <td style={{ ...HIST_TD, color: "var(--text-secondary)" }}>{time}</td>
                  <td
                    style={{
                      ...HIST_TD,
                      color: "var(--text-tertiary)",
                      maxWidth: 160,
                      overflow: "hidden",
                      textOverflow: "ellipsis",
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
      {/* Lazy-load sentinel — observing it appends the next page. */}
      <div ref={sentinelRef} style={{ height: 1 }} />
      <p style={{ fontSize: 11, color: "var(--text-tertiary)", textAlign: "center" }}>
        {hasMore
          ? `${rows?.length ?? 0} ${mode === "paper" ? "simulated fills" : "orders"} — scroll for more`
          : `All ${rows?.length ?? 0} ${mode === "paper" ? "simulated fills" : "orders"} loaded`}
      </p>
    </div>
  );
}
