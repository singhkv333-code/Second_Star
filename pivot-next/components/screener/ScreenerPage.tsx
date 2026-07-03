"use client";
// Screener tab. The STOCKS screen is fully LIVE (server-filtered/sorted via
// lib/screenerApi) with a real search box + real company logos. The ETF /
// Index / Mutual-Fund screens remain mock (screenerData.ts) and unchanged in
// look — no live endpoints back them yet, so they stay client-filtered.
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { createPortal } from "react-dom";
import {
  SlidersHorizontal,
  X,
  Search,
  Plus,
  ChevronUp,
  ChevronDown,
  ChevronsUpDown,
} from "lucide-react";
import {
  STOCKS,
  MARKET_CAP_TIERS,
  ETFS,
  ETF_CATEGORIES,
  INDICES,
  INDEX_CATEGORIES,
  MUTUAL_FUNDS,
  FUND_CATEGORIES,
} from "./screenerData";
import { CompanyLogo } from "@/components/CompanyLogo";
import { StockHoverActions } from "@/components/StockHoverActions";
import {
  useWatchlists,
  setActiveWatchlist,
  addToWatchlist,
  removeFromWatchlist,
} from "@/lib/watchlists";
import { isError } from "@/lib/types";
import {
  getScreenerStocks,
  getScreenerSectors,
  type ScreenerStock,
  type ScreenerStocksParams,
  type ScreenerStocksResponse,
  type ScreenerSector,
  type ScreenerMcapTier,
  type ScreenerSortBy,
} from "@/lib/screenerApi";

// ── Formatters ───────────────────────────────────────────
function fmtCr(v: number | null | undefined): string {
  if (v == null) return "—";
  if (v >= 1e5) return `${(v / 1e5).toFixed(2)} L Cr`;
  if (v >= 1e3) return `${(v / 1e3).toFixed(2)} K Cr`;
  return `${v.toFixed(0)} Cr`;
}
const fmtPct = (v: number | null | undefined): string =>
  v == null ? "—" : `${v.toFixed(2)}%`;
const fmtNum1 = (v: number | null | undefined): string =>
  v == null ? "—" : v.toFixed(1);
const fmtINR = (v: number | null | undefined): string =>
  v == null
    ? "—"
    : `₹${v.toLocaleString("en-IN", { maximumFractionDigits: 2 })}`;

// Sector-hued tint for the monogram fallback (mirrors the legacy BrandGlyph).
function sectorHue(key: string | null | undefined): string {
  if (!key) return "#94a3b8";
  const s = String(key).toLowerCase();
  if (s.includes("bank") || s.includes("financ") || s.includes("nbfc"))
    return "#60a5fa";
  if (s.includes("tech") || s.includes("it") || s.includes("software"))
    return "#a78bfa";
  if (s.includes("energy") || s.includes("oil")) return "#f97316";
  if (s.includes("pharma") || s.includes("health")) return "#10b981";
  if (s.includes("auto")) return "#facc15";
  if (s.includes("fmcg") || s.includes("consumer")) return "#34d399";
  if (s.includes("metal") || s.includes("steel")) return "#f472b6";
  if (s.includes("telecom")) return "#22d3ee";
  if (s.includes("cement")) return "#a8a29e";
  if (s.includes("defence")) return "#fb7185";
  if (s.includes("gold")) return "#eab308";
  if (s.includes("debt")) return "#38bdf8";
  return "#94a3b8";
}

// Pretty-print a canonical sector key when the /sectors label isn't to hand
// (e.g. a search result that returns a raw key).
function prettySector(key: string | null | undefined): string {
  if (!key) return "—";
  return key
    .split("_")
    .map((w) => (w.length > 0 ? w[0]!.toUpperCase() + w.slice(1) : w))
    .join(" ");
}

// ─────────────────────────────────────────────────────────
// Mock-screen scaffolding (ETFs / Indices / Funds) — unchanged behaviour.
// ─────────────────────────────────────────────────────────
type Align = "left" | "right";
type MockRow = Record<string, unknown>;
type MockColumn = {
  id: string;
  label: string;
  align: Align;
  type: "ticker" | "ticker_plain" | "fund" | "text" | "pct" | "num";
  fmt?: (v: number) => string;
};
type MockFilters = Record<string, string | string[]>;
type MockPreset = { id: string; label: string; set: () => Partial<MockFilters> };
type MockScreen = {
  id: string;
  label: string;
  universe: MockRow[];
  presets: MockPreset[];
  columns: MockColumn[];
  defaultSort: string;
  filterShape: MockFilters;
  filterFn: (row: MockRow, f: MockFilters) => boolean;
};

const ETF_PRESETS = [
  { id: "all", label: "All", set: () => ({}) },
  { id: "broad", label: "Broad equity", set: () => ({ categories: ["Equity — Broad"] }) },
  { id: "sector", label: "Sector", set: () => ({ categories: ["Equity — Sector"] }) },
  { id: "debt", label: "Debt", set: () => ({ categories: ["Debt"] }) },
  { id: "gold", label: "Gold", set: () => ({ categories: ["Gold"] }) },
  { id: "cheap", label: "Low expense (< 0.20%)", set: () => ({ exp_max: "0.20" }) },
  { id: "large", label: "Large AUM (> ₹5K Cr)", set: () => ({ aum_min: "5000" }) },
];

const INDEX_PRESETS = [
  { id: "all", label: "All", set: () => ({}) },
  { id: "broad", label: "Broad", set: () => ({ categories: ["Broad"] }) },
  { id: "sector", label: "Sector", set: () => ({ categories: ["Sector"] }) },
  { id: "strat", label: "Strategy", set: () => ({ categories: ["Strategy"] }) },
  { id: "gainers", label: "Today's gainers", set: () => ({ day_min: "0" }) },
  { id: "losers", label: "Today's losers", set: () => ({ day_max: "0" }) },
];

const FUND_PRESETS = [
  { id: "all", label: "All", set: () => ({}) },
  { id: "equity", label: "Equity only", set: () => ({ categories: FUND_CATEGORIES.filter((c) => c.startsWith("Equity")) }) },
  { id: "debt", label: "Debt only", set: () => ({ categories: FUND_CATEGORIES.filter((c) => c.startsWith("Debt")) }) },
  { id: "elss", label: "ELSS", set: () => ({ categories: ["ELSS"] }) },
  { id: "hybrid", label: "Hybrid", set: () => ({ categories: ["Hybrid"] }) },
  { id: "cheap", label: "Low expense (< 1%)", set: () => ({ exp_max: "1.0" }) },
  { id: "top_5y", label: "5Y CAGR > 20%", set: () => ({ five_y_min: "20" }) },
];

const num = (v: unknown): number => (typeof v === "number" ? v : Number(v) || 0);

// Mock-filter accessors — narrow Record index results (noUncheckedIndexedAccess).
const fs = (f: MockFilters, key: string): string => {
  const v = f[key];
  return typeof v === "string" ? v : "";
};
const fa = (f: MockFilters, key: string): string[] => {
  const v = f[key];
  return Array.isArray(v) ? v : [];
};

const MOCK_SCREENS: Record<string, MockScreen> = {
  etfs: {
    id: "etfs",
    label: "ETFs",
    universe: ETFS as MockRow[],
    presets: ETF_PRESETS,
    columns: [
      { id: "ticker", label: "Symbol", align: "left", type: "ticker" },
      { id: "category", label: "Category", align: "left", type: "text" },
      { id: "last", label: "NAV", align: "right", type: "num", fmt: fmtINR },
      { id: "day_change_pct", label: "Day", align: "right", type: "pct" },
      { id: "aum", label: "AUM", align: "right", type: "num", fmt: fmtCr },
      { id: "expense_ratio", label: "Expense", align: "right", type: "num", fmt: (v) => `${v.toFixed(2)}%` },
      { id: "tracking_error", label: "Track Err.", align: "right", type: "num", fmt: (v) => `${v.toFixed(2)}%` },
      { id: "one_year_pct", label: "1-Y Return", align: "right", type: "pct" },
      { id: "three_year_pct", label: "3-Y Return", align: "right", type: "pct" },
    ],
    defaultSort: "aum",
    filterShape: { categories: [], aum_min: "", exp_max: "", ret_min: "" },
    filterFn: (row, f) => {
      const cats = fa(f, "categories");
      if (cats.length > 0 && !cats.includes(String(row.category))) return false;
      if (fs(f, "aum_min") !== "" && num(row.aum) < +fs(f, "aum_min")) return false;
      if (fs(f, "exp_max") !== "" && num(row.expense_ratio) > +fs(f, "exp_max")) return false;
      if (fs(f, "ret_min") !== "" && num(row.one_year_pct) < +fs(f, "ret_min")) return false;
      return true;
    },
  },
  indices: {
    id: "indices",
    label: "Indices",
    universe: INDICES as MockRow[],
    presets: INDEX_PRESETS,
    columns: [
      { id: "ticker", label: "Index", align: "left", type: "ticker_plain" },
      { id: "category", label: "Category", align: "left", type: "text" },
      { id: "last", label: "Last", align: "right", type: "num", fmt: (v) => v.toLocaleString("en-IN", { maximumFractionDigits: 2 }) },
      { id: "day_change_pct", label: "Day", align: "right", type: "pct" },
      { id: "one_year_pct", label: "1-Y", align: "right", type: "pct" },
      { id: "ytd_pct", label: "YTD", align: "right", type: "pct" },
      { id: "pe", label: "P/E", align: "right", type: "num", fmt: fmtNum1 },
      { id: "dy", label: "Div Yield", align: "right", type: "num", fmt: fmtPct },
      { id: "members", label: "Members", align: "right", type: "num", fmt: (v) => (v == null ? "—" : String(v)) },
    ],
    defaultSort: "one_year_pct",
    filterShape: { categories: [], day_min: "", day_max: "", ytd_min: "" },
    filterFn: (row, f) => {
      const cats = fa(f, "categories");
      if (cats.length > 0 && !cats.includes(String(row.category))) return false;
      if (fs(f, "day_min") !== "" && num(row.day_change_pct) < +fs(f, "day_min")) return false;
      if (fs(f, "day_max") !== "" && num(row.day_change_pct) > +fs(f, "day_max")) return false;
      if (fs(f, "ytd_min") !== "" && num(row.ytd_pct) < +fs(f, "ytd_min")) return false;
      return true;
    },
  },
  funds: {
    id: "funds",
    label: "Mutual Funds",
    universe: MUTUAL_FUNDS as MockRow[],
    presets: FUND_PRESETS,
    columns: [
      { id: "name", label: "Fund", align: "left", type: "fund" },
      { id: "category", label: "Category", align: "left", type: "text" },
      { id: "nav", label: "NAV", align: "right", type: "num", fmt: fmtINR },
      { id: "aum", label: "AUM", align: "right", type: "num", fmt: fmtCr },
      { id: "expense_ratio", label: "Expense", align: "right", type: "num", fmt: (v) => `${v.toFixed(2)}%` },
      { id: "one_year_pct", label: "1-Y", align: "right", type: "pct" },
      { id: "three_year_pct", label: "3-Y CAGR", align: "right", type: "pct" },
      { id: "five_year_pct", label: "5-Y CAGR", align: "right", type: "pct" },
    ],
    defaultSort: "aum",
    filterShape: { categories: [], aum_min: "", exp_max: "", three_y_min: "", five_y_min: "" },
    filterFn: (row, f) => {
      const cats = fa(f, "categories");
      if (cats.length > 0 && !cats.includes(String(row.category))) return false;
      if (fs(f, "aum_min") !== "" && num(row.aum) < +fs(f, "aum_min")) return false;
      if (fs(f, "exp_max") !== "" && num(row.expense_ratio) > +fs(f, "exp_max")) return false;
      if (fs(f, "three_y_min") !== "" && num(row.three_year_pct) < +fs(f, "three_y_min")) return false;
      if (fs(f, "five_y_min") !== "" && num(row.five_year_pct) < +fs(f, "five_y_min")) return false;
      return true;
    },
  },
};

const SCREEN_ORDER = ["stocks", "etfs", "indices", "funds"] as const;
type ScreenId = (typeof SCREEN_ORDER)[number];
const SCREEN_LABELS: Record<ScreenId, string> = {
  stocks: "Stocks",
  etfs: "ETFs",
  indices: "Indices",
  funds: "Mutual Funds",
};

// ─────────────────────────────────────────────────────────
// Stocks (LIVE) state shapes
// ─────────────────────────────────────────────────────────
// All columns are sortable client-side over the fetched page (the universe
// fits in one request), so the sort key spans the display columns — not just
// the server's sort fields.
type StockSortKey =
  | "symbol"
  | "market_cap_cr"
  | "price"
  | "change_pct"
  | "pe"
  | "one_year_pct";
type StockSort = { key: StockSortKey; dir: 1 | -1 };

type StockFilters = {
  sector: string; // canonical key or "" for all
  mcap_tier: ScreenerMcapTier | "";
  pe_max: string;
  roe_min: string;
};

const EMPTY_STOCK_FILTERS: StockFilters = {
  sector: "",
  mcap_tier: "",
  pe_max: "",
  roe_min: "",
};

const STOCK_PRESETS: {
  id: string;
  label: string;
  set: () => Partial<StockFilters>;
}[] = [
  { id: "all", label: "All", set: () => ({}) },
  { id: "large", label: "Large Cap", set: () => ({ mcap_tier: "large" }) },
  { id: "mid", label: "Mid Cap", set: () => ({ mcap_tier: "mid" }) },
  { id: "small", label: "Small Cap", set: () => ({ mcap_tier: "small" }) },
  { id: "high_roe", label: "High ROE", set: () => ({ roe_min: "20" }) },
  { id: "profitable", label: "Profitable", set: () => ({ roe_min: "12" }) },
  { id: "cheap", label: "Cheap (P/E < 20)", set: () => ({ pe_max: "20" }) },
];

// ─────────────────────────────────────────────────────────
// Session caches (stale-while-revalidate)
//
// The Screener tab UNMOUNTS when you switch away — AppShell mounts non-chat
// tabs only while active (unlike Chat, which stays mounted and hidden). Without
// a cache, every return trip re-fires the stocks + sectors fetch and shows a
// skeleton for the whole round-trip, so tab switching never feels instant.
//
// These module-level caches survive tab switches (same pattern as the stock
// page's `sparklineCache` and `useCompanyLogos`): a revisit paints the last
// result INSTANTLY and silently revalidates in the background, swapping in the
// fresh rows only when they arrive. Sectors come from a static universe (no DB)
// and never change within a session, so they're fetched once.
// ─────────────────────────────────────────────────────────
const _stocksCache = new Map<string, ScreenerStocksResponse>();
let _sectorsCache: ScreenerSector[] | null = null;

/** The grid now serves the WHOLE market (~4.6k names) incrementally — pages
 *  of PAGE_SIZE, appended by an infinite-scroll sentinel. Filters and the
 *  server-sortable keys run over the full universe on the backend; the
 *  price-ish columns (no full-universe source) sort client-side over the
 *  loaded rows. */
const PAGE_SIZE = 60;

/** Sort keys the backend can order the FULL universe by. The rest
 *  (price / change / 1-Y) only exist for warmed symbols, so they sort
 *  client-side over what's loaded. */
const SERVER_SORT_KEYS: Partial<Record<StockSortKey, ScreenerSortBy>> = {
  symbol: "symbol",
  market_cap_cr: "market_cap_cr",
  pe: "pe",
};

function buildStockParams(
  filters: StockFilters,
  sort?: StockSort,
  offset = 0,
): ScreenerStocksParams {
  const serverKey = sort ? SERVER_SORT_KEYS[sort.key] : undefined;
  return {
    sector: filters.sector || undefined,
    mcap_tier: filters.mcap_tier || undefined,
    pe_max: filters.pe_max !== "" ? Number(filters.pe_max) : undefined,
    roe_min: filters.roe_min !== "" ? Number(filters.roe_min) : undefined,
    sort_by: serverKey ?? "market_cap_cr",
    sort_dir: serverKey && sort ? (sort.dir === 1 ? "asc" : "desc") : undefined,
    limit: PAGE_SIZE,
    offset,
  };
}

function stocksCacheKey(p: ScreenerStocksParams): string {
  return JSON.stringify([
    p.sector ?? "",
    p.mcap_tier ?? "",
    p.pe_max ?? "",
    p.roe_min ?? "",
    p.sort_by ?? "",
    p.sort_dir ?? "",
    p.offset ?? 0,
    p.limit ?? 0,
  ]);
}

// ── Component ────────────────────────────────────────────
export function ScreenerPage(): React.ReactElement {
  const [screenId, setScreenId] = useState<ScreenId>("stocks");
  const [mobileFiltersOpen, setMobileFiltersOpen] = useState(false);

  return (
    <div
      className="screener-root"
      style={{
        flex: 1,
        minWidth: 0,
        display: "flex",
        flexDirection: "column",
        height: "100%",
        overflow: "hidden",
        background: "var(--bg-base)",
      }}
    >
      {screenId === "stocks" ? (
        <StocksScreen
          screenId={screenId}
          onSwitchScreen={setScreenId}
          mobileFiltersOpen={mobileFiltersOpen}
          setMobileFiltersOpen={setMobileFiltersOpen}
        />
      ) : (
        <MockScreenView
          screenId={screenId}
          onSwitchScreen={setScreenId}
          mobileFiltersOpen={mobileFiltersOpen}
          setMobileFiltersOpen={setMobileFiltersOpen}
        />
      )}
    </div>
  );
}

// ─────────────────────────────────────────────────────────
// Shared chrome: title row + screen switch + mobile toggle
// ─────────────────────────────────────────────────────────
function TitleRow({
  screenId,
  onSwitchScreen,
  subhead,
  activeFilterCount,
  mobileFiltersOpen,
  setMobileFiltersOpen,
}: {
  screenId: ScreenId;
  onSwitchScreen: (id: ScreenId) => void;
  subhead: React.ReactNode;
  activeFilterCount: number;
  mobileFiltersOpen: boolean;
  setMobileFiltersOpen: (fn: (o: boolean) => boolean) => void;
}): React.ReactElement {
  return (
    <div
      className="screener-title-row"
      style={{
        padding: "24px 32px 14px",
        display: "flex",
        alignItems: "center",
        gap: 16,
        flexShrink: 0,
      }}
    >
      <div>
        <h1
          style={{
            margin: 0,
            fontFamily: "var(--font-serif)",
            fontWeight: "var(--weight-display)" as React.CSSProperties["fontWeight"],
            fontSize: 22,
            letterSpacing: "-0.025em",
            color: "var(--text-primary)",
          }}
        >
          Screener
        </h1>
        <div style={{ marginTop: 4, fontSize: 12.5, color: "var(--text-tertiary)" }}>
          {subhead}
        </div>
      </div>

      <div style={{ flex: 1 }} />

      <button
        type="button"
        className="screener-filter-toggle"
        onClick={() => setMobileFiltersOpen((o) => !o)}
        aria-expanded={mobileFiltersOpen}
        aria-controls="screener-filter-rail"
        aria-label={mobileFiltersOpen ? "Hide filters" : "Show filters"}
        style={{
          position: "relative",
          width: 38,
          height: 38,
          display: "none",
          alignItems: "center",
          justifyContent: "center",
          background: mobileFiltersOpen ? "var(--surface-active)" : "var(--bg-primary)",
          border: "1px solid var(--glass-border)",
          borderRadius: "var(--radius-pill)",
          color: "var(--text-primary)",
          cursor: "pointer",
          transition:
            "background-color 0.2s var(--ease-quartr), border-color 0.2s var(--ease-quartr)",
        }}
      >
        <SlidersHorizontal size={16} strokeWidth={2} aria-hidden="true" />
        {activeFilterCount > 0 && (
          <span
            aria-hidden="true"
            style={{
              position: "absolute",
              top: -2,
              right: -2,
              minWidth: 16,
              height: 16,
              padding: "0 4px",
              display: "inline-flex",
              alignItems: "center",
              justifyContent: "center",
              background: "var(--text-primary)",
              color: "var(--bg-primary)",
              borderRadius: 999,
              fontFamily: "var(--font-ui)",
              fontSize: 10,
              fontWeight: "var(--weight-medium)" as React.CSSProperties["fontWeight"],
              lineHeight: 1,
            }}
          >
            {activeFilterCount}
          </span>
        )}
      </button>

      <div
        style={{
          display: "inline-flex",
          gap: 2,
          padding: 3,
          background: "var(--bg-base)",
          border: "1px solid var(--glass-border)",
          borderRadius: "var(--radius-pill)",
        }}
      >
        {SCREEN_ORDER.map((id) => {
          const active = screenId === id;
          return (
            <button
              key={id}
              onClick={() => onSwitchScreen(id)}
              style={{
                padding: "6px 14px",
                border: "none",
                borderRadius: "var(--radius-pill)",
                fontFamily: "var(--font-ui)",
                fontSize: 12,
                fontWeight: "var(--weight-medium)" as React.CSSProperties["fontWeight"],
                cursor: "pointer",
                background: active ? "var(--text-primary)" : "transparent",
                color: active ? "var(--bg-primary)" : "var(--text-secondary)",
                transition:
                  "color 0.2s var(--ease-quartr), background-color 0.2s var(--ease-quartr)",
              }}
            >
              {SCREEN_LABELS[id]}
            </button>
          );
        })}
      </div>
    </div>
  );
}

// ─────────────────────────────────────────────────────────
// LIVE Stocks screen
// ─────────────────────────────────────────────────────────
function StocksScreen({
  screenId,
  onSwitchScreen,
  mobileFiltersOpen,
  setMobileFiltersOpen,
}: {
  screenId: ScreenId;
  onSwitchScreen: (id: ScreenId) => void;
  mobileFiltersOpen: boolean;
  setMobileFiltersOpen: (fn: (o: boolean) => boolean) => void;
}): React.ReactElement {
  const [filters, setFilters] = useState<StockFilters>({ ...EMPTY_STOCK_FILTERS });
  const [activePreset, setActivePreset] = useState<string | null>("all");
  const [sort, setSort] = useState<StockSort>({ key: "market_cap_cr", dir: -1 });

  // Seed state from the session cache so a return trip to the tab paints the
  // last-seen rows on the FIRST frame (no skeleton). The initial params match
  // the effect's first run exactly (empty filters, default sort, page 0).
  const _initialStocks = _stocksCache.get(
    stocksCacheKey(
      buildStockParams(EMPTY_STOCK_FILTERS, { key: "market_cap_cr", dir: -1 }),
    ),
  );
  const [rows, setRows] = useState<ScreenerStock[]>(
    () => _initialStocks?.results ?? [],
  );
  const [note, setNote] = useState(() => _initialStocks?.note ?? "");
  const [loading, setLoading] = useState(() => _initialStocks === undefined);
  const [error, setError] = useState<string | null>(null);
  // Whole-universe row count for the active filters ("Showing N of M" +
  // the infinite-scroll cutoff).
  const [total, setTotal] = useState(() => _initialStocks?.total ?? 0);
  const [loadingMore, setLoadingMore] = useState(false);
  // Bumped by the "metrics warming" poll to re-run the fetch effect (and its
  // background revalidation) until live price/change/1-Y columns fill in.
  const [reloadTick, setReloadTick] = useState(0);

  const [sectors, setSectors] = useState<ScreenerSector[]>(
    () => _sectorsCache ?? [],
  );

  // ── Load sectors once per session (static universe → cache forever) ──
  useEffect(() => {
    if (_sectorsCache) return; // already have them — no refetch on remount
    const ctrl = new AbortController();
    getScreenerSectors(ctrl.signal).then((res) => {
      if (ctrl.signal.aborted) return;
      if (!isError(res)) {
        _sectorsCache = res.data.sectors;
        setSectors(res.data.sectors);
      }
    });
    return () => ctrl.abort();
  }, []);

  // ── Load page 0 whenever filters / server-sort change (SWR) ──
  // On a cache hit we paint immediately and refresh silently in the background;
  // on a miss we show the skeleton. Changing to a server-sortable key refetches
  // (the ORDER spans the whole universe); the price-ish keys re-sort loaded
  // rows client-side and don't appear in the params, so they never refetch.
  const serverSortKey = SERVER_SORT_KEYS[sort.key] ?? null;
  const serverSortDir = serverSortKey ? sort.dir : null;
  useEffect(() => {
    const params = buildStockParams(filters, sort, 0);
    const key = stocksCacheKey(params);
    const cached = _stocksCache.get(key);

    if (cached) {
      setRows(cached.results);
      setTotal(cached.total ?? cached.results.length);
      setNote(cached.note || "");
      setError(null);
      setLoading(false);
    } else {
      setLoading(true);
      setError(null);
    }

    const ctrl = new AbortController();
    getScreenerStocks(params, ctrl.signal).then((res) => {
      if (ctrl.signal.aborted) return;
      if (isError(res)) {
        if (res.error.code === "aborted") return;
        // Only surface an error if we had nothing cached to fall back on.
        if (!cached) {
          setError(res.error.message || "Could not load stocks.");
          setRows([]);
          setTotal(0);
          setNote("");
        }
      } else {
        _stocksCache.set(key, res.data);
        setRows(res.data.results);
        setTotal(res.data.total ?? res.data.results.length);
        setNote(res.data.note || "");
        setError(null);
      }
      setLoading(false);
    });
    return () => ctrl.abort();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [filters, serverSortKey, serverSortDir, reloadTick]);

  // ── Infinite scroll: append the next page when the sentinel shows ──
  const loadMore = useCallback((): void => {
    if (loadingMore || loading) return;
    setLoadingMore(true);
    const params = buildStockParams(filters, sort, rows.length);
    getScreenerStocks(params).then((res) => {
      if (!isError(res)) {
        setRows((prev) => {
          // Dedupe on append — a filter change racing a slow page fetch
          // must not duplicate symbols.
          const seen = new Set(prev.map((r) => r.symbol));
          return [...prev, ...res.data.results.filter((r) => !seen.has(r.symbol))];
        });
        setTotal(res.data.total ?? total);
        if (res.data.note) setNote(res.data.note);
      }
      setLoadingMore(false);
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [filters, sort, rows.length, loadingMore, loading, total]);

  // Metrics warming poll — the backend serves price/change/1-Y from a cache it
  // fills on a background thread, so a cold grid arrives with those columns
  // empty. While they're missing, re-revalidate a few times (5s apart) so they
  // fill in without the user having to refresh. Self-limiting: stops the moment
  // prices land, and hard-caps at 6 tries so a truly dead source can't loop.
  const warmTriesRef = useRef(0);
  useEffect(() => {
    const warming = rows.length > 0 && rows.every((r) => r.price == null);
    if (!warming) {
      warmTriesRef.current = 0;
      return;
    }
    if (warmTriesRef.current >= 6) return;
    const t = setTimeout(() => {
      warmTriesRef.current += 1;
      // Drop the cache entry so the revalidation actually hits the server.
      _stocksCache.delete(stocksCacheKey(buildStockParams(filters, sort, 0)));
      setReloadTick((x) => x + 1);
    }, 5000);
    return () => clearTimeout(t);
  }, [rows, filters, sort]);

  // Sort: the server orders the whole universe for its supported keys (the
  // loaded pages are already in order); the price-ish columns sort
  // client-side over the LOADED rows. Missing values always sink to the
  // bottom so an unpriced/unrated row never floats to the top.
  const displayRows = useMemo(() => {
    const { key, dir } = sort;
    if (SERVER_SORT_KEYS[key]) return rows;
    const valued = rows.filter((r) => sortValue(r, key) != null);
    const nulls = rows.filter((r) => sortValue(r, key) == null);
    valued.sort((a, b) => {
      const av = sortValue(a, key)!;
      const bv = sortValue(b, key)!;
      const cmp =
        typeof av === "string" || typeof bv === "string"
          ? String(av).localeCompare(String(bv))
          : (av as number) - (bv as number);
      return dir === 1 ? cmp : -cmp;
    });
    return [...valued, ...nulls];
  }, [rows, sort]);

  const setFilter = useCallback(
    <K extends keyof StockFilters>(key: K, value: StockFilters[K]) => {
      setFilters((prev) => ({ ...prev, [key]: value }));
      setActivePreset(null);
    },
    [],
  );

  const reset = useCallback(() => {
    setFilters({ ...EMPTY_STOCK_FILTERS });
    setActivePreset("all");
  }, []);

  const applyPreset = useCallback(
    (p: (typeof STOCK_PRESETS)[number]) => {
      setFilters({ ...EMPTY_STOCK_FILTERS, ...p.set() });
      setActivePreset(p.id);
    },
    [],
  );

  const toggleSort = useCallback((key: StockSortKey) => {
    setSort((prev) =>
      prev.key === key
        ? { key, dir: prev.dir === -1 ? 1 : -1 }
        : { key, dir: -1 },
    );
  }, []);

  const activeFilterCount =
    (filters.sector ? 1 : 0) +
    (filters.mcap_tier ? 1 : 0) +
    (filters.pe_max !== "" ? 1 : 0) +
    (filters.roe_min !== "" ? 1 : 0);

  const sectorLabel = (key: string): string =>
    sectors.find((s) => s.sector === key)?.label ?? prettySector(key);

  const rail = (
    <StockFilterRail
      filters={filters}
      setFilter={setFilter}
      reset={reset}
      sectors={sectors}
      mobileOpen={mobileFiltersOpen}
      onMobileClose={() => setMobileFiltersOpen(() => false)}
      resultCount={total}
    />
  );

  return (
    <>
      <TitleRow
        screenId={screenId}
        onSwitchScreen={onSwitchScreen}
        activeFilterCount={activeFilterCount}
        mobileFiltersOpen={mobileFiltersOpen}
        setMobileFiltersOpen={setMobileFiltersOpen}
        subhead={
          loading ? (
            <span>Loading stocks…</span>
          ) : error ? (
            <span style={{ color: "var(--color-loss)" }}>Couldn&apos;t load stocks</span>
          ) : (
            <>
              Showing{" "}
              <span
                style={{
                  color: "var(--text-primary)",
                  fontWeight: "var(--weight-medium)" as React.CSSProperties["fontWeight"],
                }}
              >
                {rows.length}
              </span>{" "}
              of {total.toLocaleString("en-IN")} stock{total === 1 ? "" : "s"}
              {activeFilterCount > 0 &&
                ` · ${activeFilterCount} filter${activeFilterCount === 1 ? "" : "s"} active`}
            </>
          )
        }
      />

      {/* Watchlist — stocks-only, sits between the title row and preset
          chips. Five numbered slots, medium cards (ticker · last ₹ · day Δ). */}
      <WatchlistStrip />

      {/* Preset chips */}
      <div
        className="screener-presets"
        style={{
          padding: "0 32px 14px",
          display: "flex",
          flexWrap: "wrap",
          gap: 6,
          flexShrink: 0,
        }}
      >
        {STOCK_PRESETS.map((p) => {
          const active = activePreset === p.id;
          return (
            <button
              key={p.id}
              onClick={() => applyPreset(p)}
              style={{
                padding: "6px 12px",
                background: active ? "var(--text-primary)" : "transparent",
                border: `1px solid ${active ? "var(--text-primary)" : "var(--glass-border)"}`,
                borderRadius: "var(--radius-pill)",
                color: active ? "var(--bg-primary)" : "var(--text-secondary)",
                fontFamily: "var(--font-ui)",
                fontSize: 12,
                fontWeight: "var(--weight-medium)" as React.CSSProperties["fontWeight"],
                cursor: "pointer",
                transition: "all 0.2s var(--ease-quartr)",
              }}
            >
              {p.label}
            </button>
          );
        })}
      </div>

      <div
        className="screener-body"
        style={{
          flex: 1,
          minHeight: 0,
          display: "grid",
          gridTemplateColumns: "260px minmax(0, 1fr)",
          gap: 16,
          padding: "0 32px 24px",
        }}
      >
        {mobileFiltersOpen && (
          <div
            className="screener-filter-backdrop"
            onClick={() => setMobileFiltersOpen(() => false)}
            aria-hidden="true"
          />
        )}
        {rail}

        <StockResultsTable
          rows={displayRows}
          loading={loading}
          error={error}
          note={note}
          sort={sort}
          onSort={toggleSort}
          sectorLabel={sectorLabel}
          onResetFilters={reset}
          hasFilters={activeFilterCount > 0}
          total={total}
          hasMore={rows.length < total}
          loadingMore={loadingMore}
          onLoadMore={loadMore}
        />
      </div>
    </>
  );
}

function sortValue(row: ScreenerStock, key: StockSortKey): number | string | null {
  switch (key) {
    case "symbol":
      return row.symbol;
    case "price":
      return row.price;
    case "change_pct":
      return row.change_pct;
    case "pe":
      return row.pe;
    case "one_year_pct":
      return row.one_year_pct;
    case "market_cap_cr":
    default:
      return row.market_cap_cr;
  }
}

// ── Watchlist strip ──────────────────────────────────────
// Five fixed numbered watchlists (Kite-style), sitting between the title
// row and the preset chips. The user toggles between slots 1–5 with a
// compact numbered switch on the right; each slot renders a row of medium
// cards (ticker · last ₹ · day Δ) and can be grown via the "+ Add" menu.
// An empty slot shows just the Add tile. State lives in the shared watchlist
// store (lib/watchlists) so it stays in sync with the stock-page bookmark;
// card numbers come from the static STOCKS universe.
type WatchStock = (typeof STOCKS)[number];

const WEIGHT_MEDIUM = "var(--weight-medium)" as React.CSSProperties["fontWeight"];
const WEIGHT_DISPLAY = "var(--weight-display)" as React.CSSProperties["fontWeight"];

function WatchlistStrip(): React.ReactElement {
  const { lists: watchlists, activeId } = useWatchlists();

  const active = watchlists.find((w) => w.id === activeId) || watchlists[0]!;

  const items = useMemo(
    () =>
      (active?.tickers || [])
        .map((t) => STOCKS.find((s) => s.ticker === t))
        .filter((s): s is WatchStock => Boolean(s)),
    [active],
  );

  return (
    <div
      className="screener-watchlist"
      style={{
        padding: "0 32px 14px",
        flexShrink: 0,
        display: "flex",
        flexDirection: "column",
        gap: 10,
      }}
    >
      {/* Header: label on the left, numbered 1–5 slot switch on the right. */}
      <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
        <div
          style={{
            fontFamily: "var(--font-display)",
            fontWeight: WEIGHT_DISPLAY,
            fontSize: 13,
            letterSpacing: "-0.01em",
            color: "var(--text-primary)",
          }}
        >
          Watchlist {activeId}
        </div>

        <div style={{ flex: 1 }} />

        {/* Borderless segmented switch — active slot is a solid pill, the
            rest are plain numerals (matches the chart range toggle). */}
        <div style={{ display: "inline-flex", alignItems: "center", gap: 4 }}>
          {watchlists.map((w) => {
            const isActive = w.id === activeId;
            const count = w.tickers.length;
            return (
              <button
                key={w.id}
                onClick={() => setActiveWatchlist(w.id)}
                title={count ? `${count} stock${count === 1 ? "" : "s"}` : "Empty"}
                style={{
                  padding: "6px 14px",
                  border: "none",
                  borderRadius: "var(--radius-pill)",
                  fontFamily: "var(--font-ui)",
                  fontSize: 12,
                  fontWeight: 500,
                  fontVariantNumeric: "tabular-nums",
                  cursor: "pointer",
                  background: isActive ? "var(--text-primary)" : "transparent",
                  color: isActive
                    ? "var(--bg-primary)"
                    : count
                      ? "var(--text-secondary)"
                      : "var(--text-tertiary)",
                  transition:
                    "color 0.2s var(--ease-quartr), background-color 0.2s var(--ease-quartr)",
                }}
                onMouseEnter={(e) => {
                  if (!isActive) e.currentTarget.style.color = "var(--text-primary)";
                }}
                onMouseLeave={(e) => {
                  if (!isActive)
                    e.currentTarget.style.color = count
                      ? "var(--text-secondary)"
                      : "var(--text-tertiary)";
                }}
              >
                {w.id}
              </button>
            );
          })}
        </div>
      </div>

      {/* Cards for the active slot + the inline add-stock menu. An empty
          slot shows only the Add tile. */}
      <div
        className="screener-watchlist-cards"
        style={{ display: "flex", gap: 10, flexWrap: "wrap", alignItems: "stretch" }}
      >
        {items.map((s) => (
          <WatchCard
            key={s.ticker}
            s={s}
            onRemove={() => removeFromWatchlist(s.ticker, activeId)}
          />
        ))}

        <AddStockMenu
          existing={active?.tickers || []}
          onAdd={(t) => addToWatchlist(t, activeId)}
        />
      </div>
    </div>
  );
}

// A single watchlist stock card: ticker, full last price (₹), and day Δ
// coloured profit/loss. Subtle hover lift; a remove (×) appears on hover so
// the card stays clean at rest.
function WatchCard({
  s,
  onRemove,
}: {
  s: WatchStock;
  onRemove: () => void;
}): React.ReactElement {
  const [hover, setHover] = useState(false);
  const pos = s.day_change_pct >= 0;
  return (
    <Link
      href={`/stock/${encodeURIComponent(s.ticker)}`}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      style={{
        position: "relative",
        minWidth: 160,
        padding: "12px 14px",
        background: hover ? "var(--bg-elevated)" : "var(--bg-secondary)",
        border: "none",
        borderRadius: "var(--radius-md)",
        display: "flex",
        flexDirection: "column",
        gap: 6,
        cursor: "pointer",
        textDecoration: "none",
        transition: "background-color 0.2s var(--ease-quartr)",
      }}
    >
      <button
        type="button"
        aria-label={`Remove ${s.ticker} from watchlist`}
        title="Remove"
        onClick={(e) => {
          e.preventDefault();
          e.stopPropagation();
          onRemove();
        }}
        style={{
          position: "absolute",
          top: 8,
          right: 8,
          display: "inline-flex",
          alignItems: "center",
          justifyContent: "center",
          width: 14,
          height: 14,
          padding: 0,
          background: "transparent",
          border: "none",
          color: "var(--text-tertiary)",
          cursor: "pointer",
          opacity: hover ? 1 : 0,
          pointerEvents: hover ? "auto" : "none",
          transition:
            "opacity 0.15s var(--ease-quartr), color 0.15s var(--ease-quartr)",
        }}
        onMouseEnter={(e) => {
          e.currentTarget.style.color = "var(--text-primary)";
        }}
        onMouseLeave={(e) => {
          e.currentTarget.style.color = "var(--text-tertiary)";
        }}
      >
        <X size={13} strokeWidth={2.25} aria-hidden="true" />
      </button>
      <div
        style={{
          fontFamily: "var(--font-ui)",
          fontSize: 12,
          fontWeight: WEIGHT_MEDIUM,
          color: "var(--text-primary)",
          paddingRight: 16,
        }}
      >
        {s.ticker}
      </div>
      <div
        style={{
          display: "flex",
          alignItems: "baseline",
          justifyContent: "space-between",
          gap: 8,
        }}
      >
        <span
          style={{
            fontFamily: "var(--font-display)",
            fontWeight: WEIGHT_MEDIUM,
            fontSize: 15,
            color: "var(--text-primary)",
            fontVariantNumeric: "tabular-nums",
            letterSpacing: "-0.01em",
          }}
        >
          {fmtINR(s.last)}
        </span>
        <span
          style={{
            fontFamily: "var(--font-ui)",
            fontSize: 11.5,
            fontWeight: WEIGHT_MEDIUM,
            color: pos ? "var(--color-profit)" : "var(--color-loss)",
            fontVariantNumeric: "tabular-nums",
          }}
        >
          {pos ? "+" : ""}
          {s.day_change_pct.toFixed(2)}%
        </span>
      </div>
    </Link>
  );
}

// "+ Add" tile that opens a small searchable popover of the STOCKS universe
// (minus what's already in the active list) so the user can grow the list.
function AddStockMenu({
  existing,
  onAdd,
}: {
  existing: string[];
  onAdd: (ticker: string) => void;
}): React.ReactElement {
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState("");
  const ref = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) {
        setOpen(false);
        setQ("");
      }
    };
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, [open]);

  const candidates = useMemo(() => {
    const have = new Set(existing);
    const term = q.trim().toUpperCase();
    return STOCKS.filter((s) => !have.has(s.ticker))
      .filter(
        (s) =>
          !term ||
          s.ticker.includes(term) ||
          (s.sector || "").toUpperCase().includes(term),
      )
      .slice(0, 50);
  }, [existing, q]);

  return (
    <div ref={ref} style={{ position: "relative", display: "flex" }}>
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        aria-label="Add stock to watchlist"
        title="Add stock"
        style={{
          // Match a stock card's footprint so the tile stays the same size
          // whether or not the slot has cards next to it.
          minWidth: 92,
          minHeight: 67,
          alignSelf: "stretch",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          padding: "12px 14px",
          background: "transparent",
          border: "1px dashed var(--glass-border-focus)",
          borderRadius: "var(--radius-md)",
          color: "var(--text-tertiary)",
          cursor: "pointer",
          transition:
            "color 0.2s var(--ease-quartr), border-color 0.2s var(--ease-quartr)",
        }}
        onMouseEnter={(e) => {
          e.currentTarget.style.color = "var(--text-primary)";
          e.currentTarget.style.borderColor = "var(--text-primary)";
        }}
        onMouseLeave={(e) => {
          e.currentTarget.style.color = "var(--text-tertiary)";
          e.currentTarget.style.borderColor = "var(--glass-border-focus)";
        }}
      >
        <Plus size={24} strokeWidth={2} aria-hidden="true" />
      </button>

      {open && (
        <div
          style={{
            position: "absolute",
            top: "calc(100% + 6px)",
            left: 0,
            zIndex: 20,
            width: 260,
            background: "var(--bg-primary)",
            border: "1px solid var(--glass-border)",
            borderRadius: "var(--radius-md)",
            boxShadow:
              "0 1px 2px rgba(0,0,0,0.06), 0 12px 30px -8px rgba(0,0,0,0.22)",
            padding: 8,
            display: "flex",
            flexDirection: "column",
            gap: 8,
          }}
        >
          <div
            style={{
              display: "flex",
              alignItems: "center",
              gap: 8,
              padding: "7px 10px",
              background: "var(--bg-base)",
              border: "1px solid var(--glass-border)",
              borderRadius: "var(--radius-sm)",
            }}
          >
            <Search
              size={14}
              strokeWidth={2}
              aria-hidden="true"
              style={{ color: "var(--text-tertiary)", flexShrink: 0 }}
            />
            <input
              autoFocus
              value={q}
              onChange={(e) => setQ(e.target.value)}
              placeholder="Search symbol or sector"
              style={{
                flex: 1,
                minWidth: 0,
                background: "transparent",
                border: "none",
                outline: "none",
                color: "var(--text-primary)",
                fontFamily: "var(--font-ui)",
                fontSize: 12.5,
              }}
            />
          </div>

          <div
            className="quartr-no-scrollbar"
            style={{
              maxHeight: 240,
              overflowY: "auto",
              display: "flex",
              flexDirection: "column",
            }}
          >
            {candidates.length === 0 ? (
              <div
                style={{
                  padding: "14px 10px",
                  textAlign: "center",
                  fontFamily: "var(--font-ui)",
                  fontSize: 12,
                  color: "var(--text-tertiary)",
                }}
              >
                No matches
              </div>
            ) : (
              candidates.map((s) => (
                <button
                  key={s.ticker}
                  type="button"
                  onClick={() => {
                    onAdd(s.ticker);
                    setQ("");
                  }}
                  style={{
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "space-between",
                    gap: 8,
                    padding: "8px 10px",
                    background: "transparent",
                    border: "none",
                    borderRadius: "var(--radius-sm)",
                    cursor: "pointer",
                    textAlign: "left",
                  }}
                  onMouseEnter={(e) => {
                    e.currentTarget.style.background = "var(--bg-secondary)";
                  }}
                  onMouseLeave={(e) => {
                    e.currentTarget.style.background = "transparent";
                  }}
                >
                  <span
                    style={{
                      display: "flex",
                      flexDirection: "column",
                      gap: 1,
                      minWidth: 0,
                    }}
                  >
                    <span
                      style={{
                        fontFamily: "var(--font-ui)",
                        fontSize: 12.5,
                        fontWeight: WEIGHT_MEDIUM,
                        color: "var(--text-primary)",
                      }}
                    >
                      {s.ticker}
                    </span>
                    <span
                      style={{
                        fontFamily: "var(--font-ui)",
                        fontSize: 10.5,
                        color: "var(--text-tertiary)",
                        whiteSpace: "nowrap",
                        overflow: "hidden",
                        textOverflow: "ellipsis",
                      }}
                    >
                      {s.sector || "—"}
                    </span>
                  </span>
                  <Plus
                    size={14}
                    strokeWidth={2.5}
                    aria-hidden="true"
                    style={{ color: "var(--text-tertiary)", flexShrink: 0 }}
                  />
                </button>
              ))
            )}
          </div>
        </div>
      )}
    </div>
  );
}

// ── Stock filter rail (single-select sector + mcap tier + valuation) ──
type StockColumn = {
  id: StockSortKey;
  label: string;
  align: Align;
  sortable: boolean;
};

// Column order per product spec: Symbol · Mkt Cap · Price · Change · P/E · 1-Y.
const STOCK_COLUMNS: StockColumn[] = [
  { id: "symbol", label: "Symbol", align: "left", sortable: true },
  { id: "market_cap_cr", label: "Mkt Cap", align: "right", sortable: true },
  { id: "price", label: "Price", align: "right", sortable: true },
  { id: "change_pct", label: "Change", align: "right", sortable: true },
  { id: "pe", label: "P/E", align: "right", sortable: true },
  { id: "one_year_pct", label: "1-Y Return", align: "right", sortable: true },
];

function StockFilterRail({
  filters,
  setFilter,
  reset,
  sectors,
  mobileOpen,
  onMobileClose,
  resultCount,
}: {
  filters: StockFilters;
  setFilter: <K extends keyof StockFilters>(key: K, value: StockFilters[K]) => void;
  reset: () => void;
  sectors: ScreenerSector[];
  mobileOpen: boolean;
  onMobileClose: () => void;
  resultCount: number;
}): React.ReactElement {
  const [mounted, setMounted] = useState(false);
  useEffect(() => setMounted(true), []);

  const headerEl = (
    <div
      className="screener-filter-header"
      style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}
    >
      <div
        style={{
          fontFamily: "var(--font-display)",
          fontWeight: "var(--weight-display)" as React.CSSProperties["fontWeight"],
          fontSize: 15,
          color: "var(--text-primary)",
          letterSpacing: "-0.01em",
        }}
      >
        Filters
      </div>
      <div style={{ display: "inline-flex", alignItems: "center", gap: 10 }}>
        <button
          onClick={reset}
          style={{
            background: "transparent",
            border: "none",
            padding: 0,
            cursor: "pointer",
            fontFamily: "var(--font-ui)",
            fontSize: 12,
            color: "var(--text-tertiary)",
          }}
          onMouseEnter={(e) => {
            e.currentTarget.style.color = "var(--text-primary)";
          }}
          onMouseLeave={(e) => {
            e.currentTarget.style.color = "var(--text-tertiary)";
          }}
        >
          Reset
        </button>
        <button
          type="button"
          className="screener-filter-close"
          onClick={onMobileClose}
          aria-label="Hide filters"
          style={{
            display: "none",
            width: 28,
            height: 28,
            alignItems: "center",
            justifyContent: "center",
            background: "transparent",
            border: "none",
            borderRadius: "var(--radius-sm)",
            color: "var(--text-secondary)",
            cursor: "pointer",
          }}
        >
          <X size={16} strokeWidth={2} aria-hidden="true" />
        </button>
      </div>
    </div>
  );

  const groupsEl = (
    <>
      <FilterGroup label="Sector">
        {sectors.length === 0 ? (
          <div style={{ fontSize: 11.5, color: "var(--text-tertiary)" }}>
            Loading sectors…
          </div>
        ) : (
          <div style={{ display: "flex", flexWrap: "wrap", gap: 5 }}>
            {sectors.map((s) => {
              const active = filters.sector === s.sector;
              return (
                <ChipButton
                  key={s.sector}
                  active={active}
                  onClick={() => setFilter("sector", active ? "" : s.sector)}
                >
                  {s.label}
                  <span
                    style={{
                      marginLeft: 5,
                      opacity: 0.6,
                      fontVariantNumeric: "tabular-nums",
                    }}
                  >
                    {s.count}
                  </span>
                </ChipButton>
              );
            })}
          </div>
        )}
      </FilterGroup>

      <FilterGroup label="Market cap">
        <div style={{ display: "flex", gap: 4 }}>
          {MARKET_CAP_TIERS.map((tierDef) => {
            const id = String(tierDef[0]);
            const label = String(tierDef[1]);
            const tier = id as ScreenerMcapTier;
            const active = filters.mcap_tier === tier;
            return (
              <button
                key={id}
                onClick={() => setFilter("mcap_tier", active ? "" : tier)}
                style={{
                  ...tierBtnStyle,
                  background: active ? "var(--text-primary)" : "transparent",
                  borderColor: active ? "var(--text-primary)" : "var(--glass-border)",
                  color: active ? "var(--bg-primary)" : "var(--text-tertiary)",
                }}
              >
                {label}
              </button>
            );
          })}
        </div>
      </FilterGroup>

      <FilterGroup label="Valuation">
        <Row label="Max P/E">
          <NumInput
            value={filters.pe_max}
            onChange={(v) => setFilter("pe_max", v)}
            placeholder="—"
          />
        </Row>
      </FilterGroup>

      <FilterGroup label="Quality">
        <Row label="Min ROE %">
          <NumInput
            value={filters.roe_min}
            onChange={(v) => setFilter("roe_min", v)}
            placeholder="—"
          />
        </Row>
      </FilterGroup>

      <div
        style={{
          fontSize: 10.5,
          lineHeight: 1.5,
          color: "var(--text-tertiary)",
          marginTop: 2,
        }}
      >
        Dividend yield and 1-year return aren&apos;t served on the screener grid
        — use the company page for those.
      </div>
    </>
  );

  if (mobileOpen && mounted) {
    return createPortal(
      <div
        className="screener-sheet-overlay"
        role="dialog"
        aria-modal="true"
        aria-label="Filters"
      >
        <div className="screener-filter-backdrop" onClick={onMobileClose} aria-hidden="true" />
        <aside className="screener-filter-sheet">
          <div className="screener-sheet-handle" aria-hidden="true" />
          {headerEl}
          <div className="screener-sheet-body quartr-no-scrollbar">{groupsEl}</div>
          <button type="button" className="screener-sheet-apply" onClick={onMobileClose}>
            Show {resultCount} {resultCount === 1 ? "result" : "results"}
          </button>
        </aside>
      </div>,
      document.body,
    );
  }

  return (
    <aside
      id="screener-filter-rail"
      className="screener-filter-rail quartr-no-scrollbar"
      style={{
        minHeight: 0,
        overflowY: "auto",
        background: "var(--bg-primary)",
        border: "none",
        borderRadius: "var(--radius-md)",
        padding: 16,
        display: "flex",
        flexDirection: "column",
        gap: 13,
      }}
    >
      {headerEl}
      {groupsEl}
    </aside>
  );
}

// ── Stock results table ──────────────────────────────────
function StockResultsTable({
  rows,
  loading,
  error,
  note,
  sort,
  onSort,
  sectorLabel,
  onResetFilters,
  hasFilters,
  total,
  hasMore,
  loadingMore,
  onLoadMore,
}: {
  rows: ScreenerStock[];
  loading: boolean;
  error: string | null;
  note: string;
  sort: StockSort;
  onSort: (key: StockSortKey) => void;
  sectorLabel: (key: string) => string;
  onResetFilters: () => void;
  hasFilters: boolean;
  /** Whole-universe row count for the active filters. */
  total: number;
  hasMore: boolean;
  loadingMore: boolean;
  onLoadMore: () => void;
}): React.ReactElement {
  const router = useRouter();
  // Kite-style quick-action bar target — the symbol of the hovered row.
  const [hoverSym, setHoverSym] = useState<string | null>(null);
  // Infinite scroll — when the sentinel below the table enters the scroll
  // viewport, pull the next page. Depends on onLoadMore identity so a
  // filter change re-arms with the fresh closure.
  const sentinelRef = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    const el = sentinelRef.current;
    if (!el || !hasMore) return;
    const obs = new IntersectionObserver(
      (entries) => {
        if (entries.some((e) => e.isIntersecting)) onLoadMore();
      },
      { root: el.closest(".screener-results"), rootMargin: "400px" },
    );
    obs.observe(el);
    return () => obs.disconnect();
  }, [hasMore, onLoadMore]);
  return (
    <div
      className="screener-results quartr-no-scrollbar"
      style={{
        minHeight: 0,
        overflowY: "auto",
        overflowX: "auto",
        background: "var(--bg-base)",
        border: "none",
        borderRadius: "var(--radius-md)",
      }}
    >
      <table
        className="screener-table"
        style={{ width: "100%", borderCollapse: "collapse", fontFamily: "var(--font-ui)" }}
      >
        <thead style={{ position: "sticky", top: 0, background: "var(--bg-secondary)", zIndex: 1 }}>
          <tr>
            {STOCK_COLUMNS.map((c) => {
              const active = c.sortable && sort.key === c.id;
              return (
                <th
                  key={c.id}
                  onClick={() => c.sortable && onSort(c.id)}
                  style={{
                    ...th,
                    textAlign: c.align,
                    color: active ? "var(--text-primary)" : "var(--text-tertiary)",
                    cursor: c.sortable ? "pointer" : "default",
                  }}
                  onMouseEnter={(e) => {
                    if (c.sortable && !active) e.currentTarget.style.color = "var(--text-secondary)";
                  }}
                  onMouseLeave={(e) => {
                    if (c.sortable && !active) e.currentTarget.style.color = "var(--text-tertiary)";
                  }}
                >
                  <span
                    style={{
                      display: "inline-flex",
                      alignItems: "center",
                      gap: 5,
                      flexDirection: c.align === "right" ? "row-reverse" : "row",
                    }}
                  >
                    {c.sortable && (
                      <span
                        style={{
                          display: "inline-flex",
                          alignItems: "center",
                          lineHeight: 0,
                          opacity: active ? 1 : 0.45,
                          transition: "opacity 0.15s var(--ease-quartr)",
                        }}
                      >
                        {!active ? (
                          <ChevronsUpDown size={13} strokeWidth={2.5} aria-hidden="true" />
                        ) : sort.dir < 0 ? (
                          <ChevronDown size={13} strokeWidth={2.75} aria-hidden="true" />
                        ) : (
                          <ChevronUp size={13} strokeWidth={2.75} aria-hidden="true" />
                        )}
                      </span>
                    )}
                    {c.label}
                  </span>
                </th>
              );
            })}
          </tr>
        </thead>
        <tbody>
          {loading ? (
            <SkeletonRows cols={STOCK_COLUMNS.length} />
          ) : error ? (
            <tr>
              <td
                colSpan={STOCK_COLUMNS.length}
                style={{ padding: "44px 18px", textAlign: "center" }}
              >
                <div style={{ color: "var(--color-loss)", fontSize: 13, marginBottom: 6 }}>
                  {error}
                </div>
                <div style={{ color: "var(--text-tertiary)", fontSize: 12 }}>
                  Check your connection and try again.
                </div>
              </td>
            </tr>
          ) : rows.length === 0 ? (
            <tr>
              <td
                colSpan={STOCK_COLUMNS.length}
                style={{ padding: "44px 18px", textAlign: "center" }}
              >
                <div style={{ color: "var(--text-secondary)", fontSize: 13, marginBottom: 8 }}>
                  Nothing matches your filters.
                </div>
                {hasFilters && (
                  <button
                    type="button"
                    onClick={onResetFilters}
                    style={{
                      background: "transparent",
                      border: "1px solid var(--glass-border)",
                      borderRadius: "var(--radius-pill)",
                      padding: "5px 14px",
                      color: "var(--text-secondary)",
                      fontFamily: "var(--font-ui)",
                      fontSize: 12,
                      cursor: "pointer",
                    }}
                  >
                    Clear filters
                  </button>
                )}
              </td>
            </tr>
          ) : (
            rows.map((row) => (
              <tr
                key={row.symbol}
                onClick={() => router.push(`/stock/${encodeURIComponent(row.symbol)}`)}
                style={{
                  background: "transparent",
                  cursor: "pointer",
                  transition: "background-color 0.15s var(--ease-quartr)",
                }}
                onMouseEnter={(e) => {
                  e.currentTarget.style.background = "var(--bg-secondary)";
                  setHoverSym(row.symbol);
                  // Warm the stock-page route (RSC payload + the Recharts chart
                  // bundle) on hover so the click→chart transition is instant
                  // instead of cold-loading the whole page + chart lib.
                  router.prefetch(`/stock/${encodeURIComponent(row.symbol)}`);
                }}
                onMouseLeave={(e) => {
                  e.currentTarget.style.background = "transparent";
                  setHoverSym((s) => (s === row.symbol ? null : s));
                }}
              >
                {STOCK_COLUMNS.map((c) => (
                  <td
                    key={c.id}
                    style={{
                      ...td,
                      textAlign: c.align,
                      color: "var(--text-primary)",
                      fontFamily: c.align === "right" ? "var(--font-mono)" : "var(--font-ui)",
                    }}
                  >
                    {c.id === "market_cap_cr" ? (
                      // Kite's actual hover behaviour: on the hovered row
                      // the MKT CAP value is hidden and the quick-action
                      // bar takes its place, right-aligned on the value's
                      // own axis. Name/number overlap is impossible at any
                      // window width because bar and value never coexist.
                      // `visibility` (not display) keeps the cell width —
                      // zero layout shift.
                      <div style={{ position: "relative" }}>
                        <span
                          style={{
                            visibility:
                              hoverSym === row.symbol ? "hidden" : "visible",
                          }}
                        >
                          {renderStockCell(row, c, sectorLabel)}
                        </span>
                        {hoverSym === row.symbol && (
                          <StockHoverActions
                            symbol={row.symbol}
                            name={row.name}
                            logoUrl={row.logo_url}
                            className="absolute"
                            style={{
                              right: 0,
                              top: "50%",
                              marginTop: -14,
                              zIndex: 5,
                            }}
                          />
                        )}
                      </div>
                    ) : (
                      renderStockCell(row, c, sectorLabel)
                    )}
                  </td>
                ))}
              </tr>
            ))
          )}
        </tbody>
      </table>

      {/* Infinite-scroll sentinel + progress footer. The observer pulls the
          next page ~400px before this becomes visible, so scrolling feels
          continuous; the footer is the honest "how much of the market am I
          looking at" indicator. */}
      {!loading && !error && rows.length > 0 && (
        <div
          ref={sentinelRef}
          data-testid="screener-load-more"
          style={{
            padding: "14px 16px",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            gap: 8,
            fontSize: 11.5,
            color: "var(--text-tertiary)",
            fontFamily: "var(--font-ui)",
          }}
        >
          {loadingMore ? (
            <>Loading more…</>
          ) : hasMore ? (
            <button
              type="button"
              onClick={onLoadMore}
              style={{
                background: "transparent",
                border: "1px solid var(--glass-border)",
                borderRadius: "var(--radius-pill)",
                padding: "6px 14px",
                fontSize: 11.5,
                color: "var(--text-secondary)",
                fontFamily: "var(--font-ui)",
                cursor: "pointer",
              }}
            >
              Load more ({rows.length.toLocaleString("en-IN")} of{" "}
              {total.toLocaleString("en-IN")})
            </button>
          ) : (
            <>All {total.toLocaleString("en-IN")} shown</>
          )}
        </div>
      )}

      {!loading && !error && note && (
        <div
          style={{
            padding: "12px 16px 18px",
            fontSize: 11,
            lineHeight: 1.5,
            color: "var(--text-tertiary)",
            fontFamily: "var(--font-ui)",
          }}
        >
          {note}
        </div>
      )}
    </div>
  );
}

function renderStockCell(
  row: ScreenerStock,
  c: StockColumn,
  sectorLabel: (key: string) => string,
): React.ReactNode {
  switch (c.id) {
    case "symbol":
      return (
        <div style={{ display: "flex", alignItems: "center", gap: 12, minWidth: 0 }}>
          <CompanyLogo
            logoUrl={row.logo_url}
            name={row.name}
            symbol={row.symbol}
            hue={sectorHue(row.sector)}
            size={34}
          />
          <div style={{ display: "flex", flexDirection: "column", gap: 2, minWidth: 0 }}>
            <span
              style={{
                fontWeight: "var(--weight-medium)" as React.CSSProperties["fontWeight"],
                whiteSpace: "nowrap",
              }}
            >
              {row.symbol}
            </span>
            <span
              style={{
                fontSize: 10.5,
                color: "var(--text-tertiary)",
                whiteSpace: "nowrap",
                overflow: "hidden",
                textOverflow: "ellipsis",
                maxWidth: 220,
              }}
            >
              {sectorLabel(row.sector)}
            </span>
          </div>
        </div>
      );
    case "market_cap_cr":
      return fmtCr(row.market_cap_cr);
    case "price":
      return fmtINR(row.price);
    case "change_pct":
      return <SignedPct v={row.change_pct} />;
    case "pe":
      return fmtNum1(row.pe);
    case "one_year_pct":
      return <SignedPct v={row.one_year_pct} />;
    default:
      return "—";
  }
}

/** Signed, profit/loss-coloured percent (em-dash when null). Used for the
 *  Change and 1-Y Return columns. */
function SignedPct({ v }: { v: number | null | undefined }): React.ReactNode {
  if (v == null || !Number.isFinite(v)) return "—";
  const pos = v >= 0;
  return (
    <span
      style={{
        color: pos ? "var(--color-profit)" : "var(--color-loss)",
        fontVariantNumeric: "tabular-nums",
      }}
    >
      {pos ? "+" : ""}
      {v.toFixed(2)}%
    </span>
  );
}

function SkeletonRows({ cols }: { cols: number }): React.ReactElement {
  return (
    <>
      {Array.from({ length: 10 }).map((_, i) => (
        <tr key={i}>
          {Array.from({ length: cols }).map((__, j) => (
            <td key={j} style={{ ...td }}>
              <div
                className="screener-skeleton"
                style={{
                  height: j === 0 ? 34 : 12,
                  width: j === 0 ? "70%" : "55%",
                  marginLeft: j === 0 ? 0 : "auto",
                  borderRadius: "var(--radius-sm)",
                  background: "var(--bg-secondary)",
                }}
              />
            </td>
          ))}
        </tr>
      ))}
    </>
  );
}

// ─────────────────────────────────────────────────────────
// MOCK screen view (ETFs / Indices / Funds) — preserved behaviour
// ─────────────────────────────────────────────────────────
function MockScreenView({
  screenId,
  onSwitchScreen,
  mobileFiltersOpen,
  setMobileFiltersOpen,
}: {
  screenId: ScreenId;
  onSwitchScreen: (id: ScreenId) => void;
  mobileFiltersOpen: boolean;
  setMobileFiltersOpen: (fn: (o: boolean) => boolean) => void;
}): React.ReactElement {
  const screen = MOCK_SCREENS[screenId] ?? MOCK_SCREENS.etfs!;
  const [filters, setFilters] = useState<MockFilters>(() => cloneFilters(screen.filterShape));
  const [activePreset, setActivePreset] = useState<string | null>("all");
  const [sortKey, setSortKey] = useState(screen.defaultSort);
  const [sortDir, setSortDir] = useState<1 | -1>(-1);

  // Reset filter/sort when the screen changes.
  useEffect(() => {
    setFilters(cloneFilters(screen.filterShape));
    setActivePreset("all");
    setSortKey(screen.defaultSort);
    setSortDir(-1);
  }, [screen]);

  const setFilter = (key: string, value: string) => {
    setFilters((prev) => ({ ...prev, [key]: value }));
    setActivePreset(null);
  };
  const toggleListItem = (key: string, value: string) => {
    setFilters((prev) => {
      const arr = fa(prev, key);
      return {
        ...prev,
        [key]: arr.includes(value) ? arr.filter((v) => v !== value) : [...arr, value],
      };
    });
    setActivePreset(null);
  };
  const reset = () => {
    setFilters(cloneFilters(screen.filterShape));
    setActivePreset("all");
  };
  const applyPreset = (p: MockPreset) => {
    const merged: MockFilters = { ...cloneFilters(screen.filterShape) };
    for (const [k, v] of Object.entries(p.set())) {
      if (v !== undefined) merged[k] = v;
    }
    setFilters(merged);
    setActivePreset(p.id);
  };

  const results = useMemo(() => {
    const rows = screen.universe.filter((r) => screen.filterFn(r, filters));
    return rows.slice().sort((a, b) => {
      const av = a[sortKey];
      const bv = b[sortKey];
      if (typeof av === "string") return (av || "").localeCompare(String(bv ?? "")) * sortDir;
      return ((num(av) ?? 0) - (num(bv) ?? 0)) * sortDir;
    });
  }, [screen, filters, sortKey, sortDir]);

  const toggleSort = (key: string) => {
    if (sortKey === key) setSortDir((d) => (d === 1 ? -1 : 1));
    else {
      setSortKey(key);
      setSortDir(-1);
    }
  };

  const activeFilterCount = countMockFilters(filters);

  return (
    <>
      <TitleRow
        screenId={screenId}
        onSwitchScreen={onSwitchScreen}
        activeFilterCount={activeFilterCount}
        mobileFiltersOpen={mobileFiltersOpen}
        setMobileFiltersOpen={setMobileFiltersOpen}
        subhead={
          <>
            <span
              style={{
                color: "var(--text-primary)",
                fontWeight: "var(--weight-medium)" as React.CSSProperties["fontWeight"],
              }}
            >
              {results.length}
            </span>{" "}
            of {screen.universe.length} {screen.label.toLowerCase()} match
            {activeFilterCount > 0 &&
              ` · ${activeFilterCount} filter${activeFilterCount === 1 ? "" : "s"} active`}
          </>
        }
      />

      <div
        className="screener-presets"
        style={{ padding: "0 32px 14px", display: "flex", flexWrap: "wrap", gap: 6, flexShrink: 0 }}
      >
        {screen.presets.map((p) => {
          const active = activePreset === p.id;
          return (
            <button
              key={p.id}
              onClick={() => applyPreset(p)}
              style={{
                padding: "6px 12px",
                background: active ? "var(--text-primary)" : "transparent",
                border: `1px solid ${active ? "var(--text-primary)" : "var(--glass-border)"}`,
                borderRadius: "var(--radius-pill)",
                color: active ? "var(--bg-primary)" : "var(--text-secondary)",
                fontFamily: "var(--font-ui)",
                fontSize: 12,
                fontWeight: "var(--weight-medium)" as React.CSSProperties["fontWeight"],
                cursor: "pointer",
                transition: "all 0.2s var(--ease-quartr)",
              }}
            >
              {p.label}
            </button>
          );
        })}
      </div>

      <div
        className="screener-body"
        style={{
          flex: 1,
          minHeight: 0,
          display: "grid",
          gridTemplateColumns: "260px minmax(0, 1fr)",
          gap: 16,
          padding: "0 32px 24px",
        }}
      >
        {mobileFiltersOpen && (
          <div
            className="screener-filter-backdrop"
            onClick={() => setMobileFiltersOpen(() => false)}
            aria-hidden="true"
          />
        )}
        <MockFilterRail
          screenId={screenId}
          filters={filters}
          setFilter={setFilter}
          toggleListItem={toggleListItem}
          reset={reset}
          mobileOpen={mobileFiltersOpen}
          onMobileClose={() => setMobileFiltersOpen(() => false)}
          resultCount={results.length}
        />
        <MockResultsTable
          columns={screen.columns}
          rows={results}
          sortKey={sortKey}
          sortDir={sortDir}
          onSort={toggleSort}
        />
      </div>
    </>
  );
}

function cloneFilters(shape: MockFilters): MockFilters {
  const out: MockFilters = {};
  for (const [k, v] of Object.entries(shape)) out[k] = Array.isArray(v) ? [...v] : v;
  return out;
}

function MockFilterRail({
  screenId,
  filters,
  setFilter,
  toggleListItem,
  reset,
  mobileOpen,
  onMobileClose,
  resultCount,
}: {
  screenId: ScreenId;
  filters: MockFilters;
  setFilter: (key: string, value: string) => void;
  toggleListItem: (key: string, value: string) => void;
  reset: () => void;
  mobileOpen: boolean;
  onMobileClose: () => void;
  resultCount: number;
}): React.ReactElement {
  const [mounted, setMounted] = useState(false);
  useEffect(() => setMounted(true), []);

  const headerEl = (
    <div
      className="screener-filter-header"
      style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}
    >
      <div
        style={{
          fontFamily: "var(--font-display)",
          fontWeight: "var(--weight-display)" as React.CSSProperties["fontWeight"],
          fontSize: 15,
          color: "var(--text-primary)",
          letterSpacing: "-0.01em",
        }}
      >
        Filters
      </div>
      <div style={{ display: "inline-flex", alignItems: "center", gap: 10 }}>
        <button
          onClick={reset}
          style={{
            background: "transparent",
            border: "none",
            padding: 0,
            cursor: "pointer",
            fontFamily: "var(--font-ui)",
            fontSize: 12,
            color: "var(--text-tertiary)",
          }}
        >
          Reset
        </button>
        <button
          type="button"
          className="screener-filter-close"
          onClick={onMobileClose}
          aria-label="Hide filters"
          style={{
            display: "none",
            width: 28,
            height: 28,
            alignItems: "center",
            justifyContent: "center",
            background: "transparent",
            border: "none",
            borderRadius: "var(--radius-sm)",
            color: "var(--text-secondary)",
            cursor: "pointer",
          }}
        >
          <X size={16} strokeWidth={2} aria-hidden="true" />
        </button>
      </div>
    </div>
  );

  const catFor: Record<string, string[]> = {
    etfs: ETF_CATEGORIES,
    indices: INDEX_CATEGORIES,
    funds: FUND_CATEGORIES,
  };

  const groupsEl = (
    <>
      <FilterGroup label="Category">
        <ChipMulti
          options={catFor[screenId] ?? []}
          selected={(filters.categories as string[]) ?? []}
          onToggle={(v) => toggleListItem("categories", v)}
        />
      </FilterGroup>

      {screenId === "etfs" && (
        <>
          <FilterGroup label="AUM (₹ Cr)">
            <Row label="Min AUM">
              <NumInput value={String(filters.aum_min)} onChange={(v) => setFilter("aum_min", v)} placeholder="—" />
            </Row>
          </FilterGroup>
          <FilterGroup label="Cost">
            <Row label="Max expense %">
              <NumInput value={String(filters.exp_max)} onChange={(v) => setFilter("exp_max", v)} placeholder="—" />
            </Row>
          </FilterGroup>
          <FilterGroup label="Performance">
            <Row label="Min 1-Y return %">
              <NumInput value={String(filters.ret_min)} onChange={(v) => setFilter("ret_min", v)} placeholder="—" />
            </Row>
          </FilterGroup>
        </>
      )}

      {screenId === "indices" && (
        <>
          <FilterGroup label="Today">
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 6 }}>
              <NumInput value={String(filters.day_min)} onChange={(v) => setFilter("day_min", v)} placeholder="Min %" />
              <NumInput value={String(filters.day_max)} onChange={(v) => setFilter("day_max", v)} placeholder="Max %" />
            </div>
          </FilterGroup>
          <FilterGroup label="Year-to-date">
            <Row label="Min YTD %">
              <NumInput value={String(filters.ytd_min)} onChange={(v) => setFilter("ytd_min", v)} placeholder="—" />
            </Row>
          </FilterGroup>
        </>
      )}

      {screenId === "funds" && (
        <>
          <FilterGroup label="AUM (₹ Cr)">
            <Row label="Min AUM">
              <NumInput value={String(filters.aum_min)} onChange={(v) => setFilter("aum_min", v)} placeholder="—" />
            </Row>
          </FilterGroup>
          <FilterGroup label="Cost">
            <Row label="Max expense %">
              <NumInput value={String(filters.exp_max)} onChange={(v) => setFilter("exp_max", v)} placeholder="—" />
            </Row>
          </FilterGroup>
          <FilterGroup label="Performance">
            <Row label="Min 3-Y CAGR %">
              <NumInput value={String(filters.three_y_min)} onChange={(v) => setFilter("three_y_min", v)} placeholder="—" />
            </Row>
            <Row label="Min 5-Y CAGR %">
              <NumInput value={String(filters.five_y_min)} onChange={(v) => setFilter("five_y_min", v)} placeholder="—" />
            </Row>
          </FilterGroup>
        </>
      )}
    </>
  );

  if (mobileOpen && mounted) {
    return createPortal(
      <div className="screener-sheet-overlay" role="dialog" aria-modal="true" aria-label="Filters">
        <div className="screener-filter-backdrop" onClick={onMobileClose} aria-hidden="true" />
        <aside className="screener-filter-sheet">
          <div className="screener-sheet-handle" aria-hidden="true" />
          {headerEl}
          <div className="screener-sheet-body quartr-no-scrollbar">{groupsEl}</div>
          <button type="button" className="screener-sheet-apply" onClick={onMobileClose}>
            Show {resultCount} {resultCount === 1 ? "result" : "results"}
          </button>
        </aside>
      </div>,
      document.body,
    );
  }

  return (
    <aside
      id="screener-filter-rail"
      className="screener-filter-rail quartr-no-scrollbar"
      style={{
        minHeight: 0,
        overflowY: "auto",
        background: "var(--bg-primary)",
        border: "none",
        borderRadius: "var(--radius-md)",
        padding: 16,
        display: "flex",
        flexDirection: "column",
        gap: 13,
      }}
    >
      {headerEl}
      {groupsEl}
    </aside>
  );
}

// Monogram glyph for the mock screens (no real logos for ETFs/indices/funds).
function BrandGlyph({
  label,
  hueKey,
}: {
  label: string;
  hueKey: string | null | undefined;
}): React.ReactElement {
  const initial = (label || "").trim()[0]?.toUpperCase() ?? "•";
  const hue = sectorHue(hueKey);
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
        border: "none",
        color: hue,
        fontFamily: "var(--font-ui)",
        fontSize: 14,
        fontWeight: "var(--weight-medium)" as React.CSSProperties["fontWeight"],
        letterSpacing: "-0.02em",
      }}
    >
      {initial}
    </div>
  );
}

function MockResultsTable({
  columns,
  rows,
  sortKey,
  sortDir,
  onSort,
}: {
  columns: MockColumn[];
  rows: MockRow[];
  sortKey: string;
  sortDir: 1 | -1;
  onSort: (key: string) => void;
}): React.ReactElement {
  return (
    <div
      className="screener-results quartr-no-scrollbar"
      style={{
        minHeight: 0,
        overflowY: "auto",
        overflowX: "auto",
        background: "var(--bg-base)",
        border: "none",
        borderRadius: "var(--radius-md)",
      }}
    >
      <table
        className="screener-table"
        style={{ width: "100%", borderCollapse: "collapse", fontFamily: "var(--font-ui)" }}
      >
        <thead style={{ position: "sticky", top: 0, background: "var(--bg-secondary)", zIndex: 1 }}>
          <tr>
            {columns.map((c) => {
              const active = sortKey === c.id;
              return (
                <th
                  key={c.id}
                  onClick={() => onSort(c.id)}
                  style={{
                    ...th,
                    textAlign: c.align,
                    color: active ? "var(--text-primary)" : "var(--text-tertiary)",
                    cursor: "pointer",
                  }}
                >
                  <span
                    style={{
                      display: "inline-flex",
                      alignItems: "center",
                      gap: 5,
                      flexDirection: c.align === "right" ? "row-reverse" : "row",
                    }}
                  >
                    <span
                      style={{
                        display: "inline-flex",
                        alignItems: "center",
                        lineHeight: 0,
                        opacity: active ? 1 : 0.45,
                      }}
                    >
                      {!active ? (
                        <ChevronsUpDown size={13} strokeWidth={2.5} aria-hidden="true" />
                      ) : sortDir < 0 ? (
                        <ChevronDown size={13} strokeWidth={2.75} aria-hidden="true" />
                      ) : (
                        <ChevronUp size={13} strokeWidth={2.75} aria-hidden="true" />
                      )}
                    </span>
                    {c.label}
                  </span>
                </th>
              );
            })}
          </tr>
        </thead>
        <tbody>
          {rows.length === 0 ? (
            <tr>
              <td
                colSpan={columns.length}
                style={{ padding: "40px 18px", textAlign: "center", color: "var(--text-secondary)", fontSize: 13 }}
              >
                Nothing matches your filters.
              </td>
            </tr>
          ) : (
            rows.map((row) => (
              <tr
                key={String(row.ticker ?? row.name)}
                style={{ background: "transparent", transition: "background-color 0.15s var(--ease-quartr)" }}
                onMouseEnter={(e) => {
                  e.currentTarget.style.background = "var(--bg-secondary)";
                }}
                onMouseLeave={(e) => {
                  e.currentTarget.style.background = "transparent";
                }}
              >
                {columns.map((c) => (
                  <td
                    key={c.id}
                    style={{
                      ...td,
                      textAlign: c.align,
                      color: mockCellColor(row, c),
                      fontFamily: c.align === "right" ? "var(--font-mono)" : "var(--font-ui)",
                    }}
                  >
                    {renderMockCell(row, c)}
                  </td>
                ))}
              </tr>
            ))
          )}
        </tbody>
      </table>
    </div>
  );
}

function mockCellColor(row: MockRow, c: MockColumn): string {
  if (c.type === "pct") {
    const v = row[c.id];
    if (v == null) return "var(--text-primary)";
    return num(v) >= 0 ? "var(--color-profit)" : "var(--color-loss)";
  }
  if (c.type === "text") return "var(--text-secondary)";
  return "var(--text-primary)";
}

function renderMockCell(row: MockRow, c: MockColumn): React.ReactNode {
  const v = row[c.id];
  if (c.type === "ticker") {
    return (
      <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
        <BrandGlyph label={String(row.ticker)} hueKey={String(row.sector ?? row.category ?? "")} />
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <span style={{ fontWeight: "var(--weight-medium)" as React.CSSProperties["fontWeight"] }}>
            {String(row.ticker)}
          </span>
          {row.exch != null && (
            <span
              style={{
                fontSize: 9,
                color: "var(--text-tertiary)",
                fontFamily: "var(--font-mono)",
                letterSpacing: "0.06em",
              }}
            >
              {String(row.exch)}
            </span>
          )}
        </div>
      </div>
    );
  }
  if (c.type === "ticker_plain") {
    return (
      <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
        <BrandGlyph label={String(row.ticker)} hueKey={String(row.category ?? "")} />
        <span style={{ fontWeight: "var(--weight-medium)" as React.CSSProperties["fontWeight"] }}>
          {String(row.ticker)}
        </span>
      </div>
    );
  }
  if (c.type === "fund") {
    return (
      <div style={{ display: "flex", alignItems: "center", gap: 12, minWidth: 0 }}>
        <BrandGlyph label={String(row.name)} hueKey={String(row.category ?? "")} />
        <div style={{ display: "flex", flexDirection: "column", gap: 2, minWidth: 0 }}>
          <span
            style={{
              fontWeight: "var(--weight-medium)" as React.CSSProperties["fontWeight"],
              whiteSpace: "nowrap",
              overflow: "hidden",
              textOverflow: "ellipsis",
            }}
          >
            {String(row.name)}
          </span>
          <span
            style={{
              fontSize: 10.5,
              color: "var(--text-tertiary)",
              fontFamily: "var(--font-mono)",
              letterSpacing: "0.06em",
            }}
          >
            {String(row.ticker)}
          </span>
        </div>
      </div>
    );
  }
  if (c.type === "text") return <span>{v == null ? "—" : String(v)}</span>;
  if (c.type === "pct") {
    if (v == null) return "—";
    const n = num(v);
    return `${n >= 0 ? "+" : ""}${n.toFixed(2)}%`;
  }
  if (c.fmt) return v == null ? "—" : c.fmt(num(v));
  return v == null ? "—" : String(v);
}

function countMockFilters(f: MockFilters): number {
  let n = 0;
  for (const v of Object.values(f)) {
    if (Array.isArray(v)) {
      if (v.length > 0) n++;
    } else if (v !== "" && v != null) n++;
  }
  if (f.day_min !== "" && f.day_max !== "") n--;
  return Math.max(0, n);
}

// ── Filter primitives (shared) ────────────────────────────
function FilterGroup({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}): React.ReactElement {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
      <div
        style={{
          fontSize: 10,
          color: "var(--text-tertiary)",
          fontWeight: "var(--weight-medium)" as React.CSSProperties["fontWeight"],
          letterSpacing: "0.08em",
          textTransform: "uppercase",
        }}
      >
        {label}
      </div>
      {children}
    </div>
  );
}

function Row({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}): React.ReactElement {
  return (
    <div style={{ display: "grid", gridTemplateColumns: "1fr 80px", alignItems: "center", gap: 10 }}>
      <span style={{ fontSize: 12, color: "var(--text-secondary)" }}>{label}</span>
      {children}
    </div>
  );
}

function ChipButton({
  active,
  onClick,
  children,
}: {
  active: boolean;
  onClick: () => void;
  children: React.ReactNode;
}): React.ReactElement {
  return (
    <button
      onClick={onClick}
      style={{
        padding: "5px 11px",
        background: active ? "var(--text-primary)" : "var(--bg-base)",
        border: `1px solid ${active ? "var(--text-primary)" : "var(--glass-border)"}`,
        borderRadius: "var(--radius-pill)",
        color: active ? "var(--bg-primary)" : "var(--text-secondary)",
        fontFamily: "var(--font-ui)",
        fontSize: 11.5,
        fontWeight: "var(--weight-medium)" as React.CSSProperties["fontWeight"],
        cursor: "pointer",
        transition: "all 0.2s var(--ease-quartr)",
      }}
      onMouseEnter={(e) => {
        if (active) return;
        e.currentTarget.style.borderColor = "var(--glass-border-focus)";
        e.currentTarget.style.color = "var(--text-primary)";
        e.currentTarget.style.background = "var(--bg-elevated)";
      }}
      onMouseLeave={(e) => {
        if (active) return;
        e.currentTarget.style.borderColor = "var(--glass-border)";
        e.currentTarget.style.color = "var(--text-secondary)";
        e.currentTarget.style.background = "var(--bg-base)";
      }}
    >
      {children}
    </button>
  );
}

function ChipMulti({
  options,
  selected,
  onToggle,
}: {
  options: string[];
  selected: string[];
  onToggle: (v: string) => void;
}): React.ReactElement {
  return (
    <div style={{ display: "flex", flexWrap: "wrap", gap: 5 }}>
      {options.map((o) => (
        <ChipButton key={o} active={selected.includes(o)} onClick={() => onToggle(o)}>
          {o}
        </ChipButton>
      ))}
    </div>
  );
}

function NumInput({
  value,
  onChange,
  placeholder,
}: {
  value: string;
  onChange: (v: string) => void;
  placeholder?: string;
}): React.ReactElement {
  return (
    <input
      type="text"
      inputMode="decimal"
      value={value}
      onChange={(e) => {
        const v = e.target.value;
        if (v === "" || /^-?\d*\.?\d*$/.test(v)) onChange(v);
      }}
      placeholder={placeholder}
      style={{
        width: "100%",
        padding: "6px 10px",
        background: "var(--bg-base)",
        border: "1px solid var(--glass-border)",
        borderRadius: "var(--radius-sm)",
        color: "var(--text-primary)",
        fontFamily: "var(--font-mono)",
        fontSize: 12,
        outline: "none",
        textAlign: "right",
      }}
      onFocus={(e) => {
        e.currentTarget.style.borderColor = "var(--glass-border-focus)";
      }}
      onBlur={(e) => {
        e.currentTarget.style.borderColor = "var(--glass-border)";
      }}
    />
  );
}

const tierBtnStyle: React.CSSProperties = {
  flex: 1,
  padding: "5px 0",
  background: "transparent",
  border: "1px solid var(--glass-border)",
  borderRadius: "var(--radius-sm)",
  color: "var(--text-tertiary)",
  fontFamily: "var(--font-ui)",
  fontSize: 10.5,
  fontWeight: "var(--weight-medium)" as React.CSSProperties["fontWeight"],
  cursor: "pointer",
  transition: "all 0.2s var(--ease-quartr)",
};

const th: React.CSSProperties = {
  padding: "13px 16px",
  fontSize: 10,
  letterSpacing: "0.1em",
  textTransform: "uppercase",
  fontWeight: "var(--weight-display)" as React.CSSProperties["fontWeight"],
  borderBottom: "1.5px solid var(--glass-border)",
  whiteSpace: "nowrap",
  userSelect: "none",
  fontFamily: "var(--font-ui)",
  transition: "color 0.15s var(--ease-quartr)",
};

const td: React.CSSProperties = {
  padding: "14px 16px",
  fontSize: 12.5,
  borderBottom: "1px solid var(--glass-border)",
  whiteSpace: "nowrap",
};
