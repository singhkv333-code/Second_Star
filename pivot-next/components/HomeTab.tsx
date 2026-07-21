"use client";

/**
 * HomeTab — the landing surface for Pivot.
 *
 * A calm, premium "market at a glance" dashboard laid out as a fit-to-screen
 * BENTO grid. Every card's internal spacing is vh-clamped so the whole board
 * fits without any scrolling on realistic desktop viewports, no matter how
 * much a given browser's chrome eats into the window (a raw "1920x1080"
 * check isn't enough — OS scaling, tab/bookmarks bars, etc. all shrink the
 * usable height differently). It gathers the six things a user most often
 * wants on arrival into one scannable board, each cell a doorway into the
 * deeper tab:
 *
 *   ┌──────── indices (NIFTY / SENSEX / BANK NIFTY / MIDCAP) ────────┐
 *   ├───── Portfolio ─────┬──── Watchlist ────┬──── Chat prompts ────┤
 *   ├──── Prebuilt strategies ────┴──────── Not sure? (Views) ───────┤
 *   └────────────────────────────────────────────────────────────────┘
 *
 * DESIGN: borders-only cards on the paper surface, radius tokens, theme-aware
 * via globals.css custom properties (light + dark both hold). Each card's
 * body — and the page as a whole — falls back to scrolling (hidden
 * scrollbar) if a viewport is too short even after clamping bottoms out;
 * silently clipping content out of reach is treated as a worse failure than
 * an occasional scroll. No fabricated numbers — every figure is a real tool
 * value or an honest empty state.
 */

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useId, useMemo, useRef, useState } from "react";
import {
  Activity,
  ArrowRight,
  ArrowUpRight,
  BarChart2,
  Bell,
  CandlestickChart,
  Layers,
  LineChart,
  MessageSquare,
  PieChart,
  Repeat,
  Scale,
  Sparkles,
  Telescope,
  TrendingUp,
  Wallet,
} from "lucide-react";
import {
  getMarketIndices,
  getMe,
  getPortfolioHoldings,
  getPortfolioSummary,
  getSparkline,
  getStockQuote,
  type Holding,
  type IndexQuote,
  type PortfolioSummary,
} from "@/lib/api";
import { isError } from "@/lib/types";
import type { Workflow } from "@/lib/types";
import { useTradingMode } from "@/lib/trading-mode";
import { useWatchlists, setActiveWatchlist, type Watchlist } from "@/lib/watchlists";
import { useCompanyLogos } from "@/hooks/useCompanyLogos";
import { Panel } from "@/components/ds/surfaces";
import { CompanyLogo } from "@/components/CompanyLogo";
import { ViewCard } from "@/components/views/ViewCard";
import { Skeleton } from "@/components/ui/skeleton";
import type { ViewSummary } from "@/lib/types";
import packSummariesRaw from "@/components/views/pack/viewpack01.summaries.json";
import pack2SummariesRaw from "@/components/views/pack/viewpack02.summaries.json";

const PACK_SUMMARIES = [
  ...(packSummariesRaw as unknown as ViewSummary[]),
  ...(pack2SummariesRaw as unknown as ViewSummary[]),
];

/**
 * Only live opinions belong on Home. `coming_soon` teasers (and anything still
 * developing) have no detail record behind them, so opening one from Home would
 * dead-end. `status` can't tell them apart — every packed view carries "open" —
 * so the lifecycle flags are the discriminator.
 */
const ACTIVE_SUMMARIES = PACK_SUMMARIES.filter(
  (v) => v.coming_soon !== true && !v.is_developing,
);

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

export type HomeTabProps = {
  /** Switch the shell to another top-level tab. */
  onGoTab: (tab: "chat" | "portfolio" | "screener" | "views" | "agents") => void;
  /** Drop a prompt into the chat composer and auto-submit it. */
  onSendPrompt: (prompt: string) => void;
  /**
   * Open a prebuilt agent: jump to the Agents tab and pop the side editor on
   * it. `matchName` is the seeded workflow's name — the shell looks it up in
   * the user's own workflows and opens the REAL agent when present, falling
   * back to `draft` (used verbatim for the options strategy, which has no
   * seeded workflow of its own).
   */
  onOpenAgent: (spec: { matchName: string; draft: Workflow }) => void;
  /**
   * Open the Agents tab on its "Strategies" surface (the Options strategies
   * list). Used by the F&O prebuilt tile, which has no agent of its own.
   */
  onOpenStrategies: () => void;
};

// ---------------------------------------------------------------------------
// Formatters
// ---------------------------------------------------------------------------

const INR = new Intl.NumberFormat("en-IN", {
  style: "currency",
  currency: "INR",
  maximumFractionDigits: 0,
});
const NUM = new Intl.NumberFormat("en-IN", { maximumFractionDigits: 2 });

function fmtINR(n: number): string {
  return INR.format(n);
}
function fmtNum(n: number): string {
  return NUM.format(n);
}
function fmtSignedPct(n: number): string {
  return `${n >= 0 ? "+" : ""}${n.toFixed(2)}%`;
}

function greetingForHour(): string {
  const h = new Date().getHours();
  if (h < 12) return "Good morning";
  if (h < 17) return "Good afternoon";
  return "Good evening";
}

function firstName(name: string | null | undefined, email: string | null | undefined): string {
  const trimmed = (name || "").trim();
  if (trimmed) return trimmed.split(/\s+/)[0]!;
  const e = (email || "").trim();
  if (e) {
    const local = e.split("@")[0]!;
    if (/^demo[_\d]/i.test(local)) return "there";
    return local;
  }
  return "there";
}

const TODAY_FMT = new Intl.DateTimeFormat("en-IN", {
  weekday: "long",
  day: "numeric",
  month: "long",
});

// NOTE: the home board no longer classifies "monitor vs laptop" from physical
// pixel counts — that guess mis-sized Retina laptops (small screen, huge pixel
// count) and 1920 monitors, causing overflow and wrong prompt/strategy counts.
// The rich-vs-compact decision is now made from actual available space: the
// chat prompts measure their card height (ChatPromptsCard / promptsThatFit),
// and the Views + strategies compactions key off `@media (max-height)` in
// globals.css — both scaling-honest, unlike a device guess.

/** Rough NSE session check in IST — Mon–Fri, 09:15–15:30. Presentational only
 *  (a calm status chip); never gates any data or action. */
function marketStatus(): { open: boolean; label: string } {
  const now = new Date();
  // Convert to IST regardless of the viewer's timezone.
  const ist = new Date(now.toLocaleString("en-US", { timeZone: "Asia/Kolkata" }));
  const day = ist.getDay(); // 0 Sun … 6 Sat
  const mins = ist.getHours() * 60 + ist.getMinutes();
  const open = day >= 1 && day <= 5 && mins >= 555 && mins <= 930;
  return { open, label: open ? "Markets open" : "Markets closed" };
}

// ---------------------------------------------------------------------------
// AreaSpark — a compact line + soft area-fill sparkline (signed colour). A
// chart element, not a card tint: the faint gradient lives under the line only.
// ---------------------------------------------------------------------------

function AreaSpark({
  data,
  up,
  width = 100,
  height = 34,
}: {
  data: number[];
  up: boolean;
  width?: number;
  height?: number;
}): React.ReactElement | null {
  const gid = useId().replace(/:/g, "");
  if (!data || data.length < 2) return null;
  const min = Math.min(...data);
  const max = Math.max(...data);
  const span = max - min || 1;
  const stepX = width / (data.length - 1);
  const y = (v: number): number => height - 2 - ((v - min) / span) * (height - 4);
  const line = data.map((v, i) => `${(i * stepX).toFixed(1)},${y(v).toFixed(2)}`);
  const linePath = `M ${line.join(" L ")}`;
  const areaPath = `${linePath} L ${width.toFixed(1)},${height} L 0,${height} Z`;
  const color = up ? "var(--color-profit)" : "var(--color-loss)";
  return (
    <svg
      width={width}
      height={height}
      viewBox={`0 0 ${width} ${height}`}
      preserveAspectRatio="none"
      aria-hidden
      style={{ display: "block", width: "100%", height }}
    >
      <defs>
        <linearGradient id={`sp-${gid}`} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor={color} stopOpacity={0.16} />
          <stop offset="100%" stopColor={color} stopOpacity={0} />
        </linearGradient>
      </defs>
      <path d={areaPath} fill={`url(#sp-${gid})`} stroke="none" />
      <path
        d={linePath}
        fill="none"
        stroke={color}
        strokeWidth={1.5}
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

/** Fetch a 1-month close series for a symbol, returned as plain numbers (or
 *  null on any failure so a card degrades to no-spark rather than erroring). */
async function fetchSpark(symbol: string): Promise<number[] | null> {
  const r = await getSparkline(symbol, "1M").catch(() => null);
  if (!r || isError(r) || !r.data.points || r.data.points.length < 2) return null;
  return r.data.points.map((p) => p.v);
}

// ---------------------------------------------------------------------------
// Static content — strategy + chat prompt seeds
// ---------------------------------------------------------------------------

type StrategyIcon = React.ComponentType<{ size?: number; strokeWidth?: number; style?: React.CSSProperties }>;

type StrategyTile =
  | {
      /** Opens the Agents tab with the side editor on the seeded agent. */
      kind: "agent";
      title: string;
      subtitle: string;
      tag: string;
      Icon: StrategyIcon;
      /** Name of the seeded workflow to open in the editor (see onOpenAgent). */
      matchName: string;
      /** Full workflow used verbatim when no seeded agent matches `matchName`. */
      draft: Workflow;
    }
  | {
      /** Opens the Agents tab on its "Strategies" (Options strategies) surface. */
      kind: "option";
      title: string;
      subtitle: string;
      tag: string;
      Icon: StrategyIcon;
    };

// Fixed epoch for the local-draft timestamps — these are never persisted, so
// the exact value is immaterial; a constant keeps them render-stable.
const DRAFT_TS = "2026-01-01T00:00:00Z";

/** Build a well-formed local-draft Workflow from a name/description + steps.
 *  `id` is a "local-…" sentinel so the shell treats it as an unsaved draft and
 *  binds the editor to it. */
function makeDraft(
  slug: string,
  name: string,
  description: string,
  steps: { step_type: string; label: string; config: Record<string, unknown> }[],
): Workflow {
  return {
    id: `local-${slug}`,
    name,
    description,
    status: "draft",
    version: 1,
    single_instance: true,
    created_at: DRAFT_TS,
    updated_at: DRAFT_TS,
    activated_at: null,
    last_run_at: null,
    next_run_at: null,
    steps: steps.map((s, i) => ({
      id: `local-${slug}-${i}`,
      step_index: i,
      step_type: s.step_type,
      label: s.label,
      config: s.config,
    })),
  };
}

// The prebuilt strategies mirror the three demo agents seeded on registration
// (see backend/services/demo_seeder.py) plus one options strategy. The three
// AGENT tiles open the Agents tab with the side editor on that agent — the
// shell looks the agent up by `matchName` and opens the user's REAL seeded
// workflow when present, else the `draft` here. The OPTION tile opens the
// full-screen NIFTY option-chain / strategy builder instead.
const PREBUILT_STRATEGIES: StrategyTile[] = [
  {
    kind: "agent",
    title: "RELIANCE 3:15 PM buy",
    subtitle: "Weekday buy when buying power is high",
    tag: "Automation",
    matchName: "RELIANCE 3:15 PM weekday buy",
    Icon: TrendingUp,
    draft: makeDraft(
      "reliance-315",
      "RELIANCE 3:15 PM weekday buy",
      "Every weekday at 3:15 PM IST, buy 10 RELIANCE if buying power > ₹50,000.",
      [
        { step_type: "trigger.schedule", label: "Every weekday at 3:15 PM IST", config: { cron: "15 15 * * 1-5", timezone: "Asia/Kolkata" } },
        { step_type: "fetch.portfolio", label: "Get portfolio", config: {} },
        { step_type: "condition.numeric", label: "Buying power > ₹50,000", config: { left: { ref: "portfolio.cash" }, operator: ">", right: 50000 } },
        { step_type: "action.place_order", label: "Buy 10 RELIANCE", config: { symbol: "RELIANCE", side: "buy", quantity: 10, order_type: "market", requires_approval: false } },
        { step_type: "notify.message", label: "Email confirmation", config: { channel: "email", template: "order_confirmation", vars: { symbol: "RELIANCE" } } },
      ],
    ),
  },
  {
    kind: "agent",
    title: "INFY weekly dip-buy",
    subtitle: "Buy the Monday dip on a limit",
    tag: "Automation",
    matchName: "INFY weekly dip-buy",
    Icon: LineChart,
    draft: makeDraft(
      "infy-dip",
      "INFY weekly dip-buy",
      "Every Monday morning, buy 5 INFY at limit if price < ₹1,400.",
      [
        { step_type: "trigger.schedule", label: "Every Monday at 9:30 AM IST", config: { cron: "30 9 * * 1", timezone: "Asia/Kolkata" } },
        { step_type: "action.place_order", label: "Buy 5 INFY at ₹1,400", config: { symbol: "INFY", side: "buy", quantity: 5, order_type: "limit", limit_price: 1400, requires_approval: false } },
      ],
    ),
  },
  {
    kind: "agent",
    title: "TCS monthly SIP",
    subtitle: "Rupee-cost-average every month",
    tag: "Automation",
    matchName: "TCS monthly SIP",
    Icon: Repeat,
    draft: makeDraft(
      "tcs-sip",
      "TCS monthly SIP",
      "On the 1st of every month, buy 2 TCS at market.",
      [
        { step_type: "trigger.schedule", label: "1st of every month at 9:30 AM IST", config: { cron: "30 9 1 * *", timezone: "Asia/Kolkata" } },
        { step_type: "action.place_order", label: "Buy 2 TCS", config: { symbol: "TCS", side: "buy", quantity: 2, order_type: "market", requires_approval: false } },
      ],
    ),
  },
  {
    kind: "option",
    title: "NIFTY Bull Call Spread",
    subtitle: "Defined-risk bullish options play",
    tag: "F&O",
    Icon: Layers,
  },
];

type ChatPrompt = {
  label: string;
  Icon: React.ComponentType<{ size?: number; strokeWidth?: number; style?: React.CSSProperties }>;
};

// Six seeds. ChatPromptsCard measures the card's real height and shows as
// many as fit without cramping (a tall monitor takes all six; a short laptop
// or a small-screen MacBook trims to what stays readable) — see PROMPT_ROW_MIN
// and the ResizeObserver there. Measured space, not a physical-pixel device
// guess, which used to mis-size Retina laptops and 1920 monitors.
const CHAT_PROMPTS: ChatPrompt[] = [
  { label: "Give me a market pulse for today.", Icon: Activity },
  { label: "Analyse TCS — technicals, fundamentals and a view.", Icon: BarChart2 },
  { label: "What are the top movers in NIFTY 50 today?", Icon: TrendingUp },
  { label: "Compare INFY vs TCS over the last 6 months.", Icon: Scale },
  { label: "Show me the NIFTY option chain with max pain and PCR.", Icon: CandlestickChart },
  { label: "Alert me when RELIANCE crosses ₹1,500.", Icon: Bell },
];

// ---------------------------------------------------------------------------
// HomeTab
// ---------------------------------------------------------------------------

export function HomeTab({ onGoTab, onSendPrompt, onOpenAgent, onOpenStrategies }: HomeTabProps): React.ReactElement {
  const [greetName, setGreetName] = useState<string | null>(null);
  const router = useRouter();

  useEffect(() => {
    getMe().then((r) => {
      if (isError(r)) {
        setGreetName("there");
        return;
      }
      setGreetName(firstName(r.data.full_name, r.data.email));
    }).catch(() => setGreetName("there"));
  }, []);

  const greeting = greetingForHour();
  const today = useMemo(() => TODAY_FMT.format(new Date()), []);

  return (
    <div
      // Always overflow-y-auto, even at lg+: the vh-clamped bento grid fits
      // without scrolling on any realistic viewport, but on a pathologically
      // short one (heavy browser chrome eating into a small laptop screen)
      // this is the last-resort fallback — silently clipping row 2 out of
      // reach (the old lg:overflow-hidden) is worse than an occasional
      // scroll.
      className="mx-auto flex h-full min-h-0 w-full flex-col overflow-y-auto"
      style={{ maxWidth: 1760, gap: "clamp(8px, 1.4vh, 14px)" }}
      data-testid="home-tab"
    >
      {/* ── Greeting + indices (fixed header band) ───────────────────── */}
      <div className="flex shrink-0 flex-col" style={{ gap: "clamp(8px, 1.4vh, 14px)" }}>
        <div className="flex flex-wrap items-baseline justify-between" style={{ gap: 8 }}>
          {greetName === null ? (
            <Skeleton style={{ height: 32, width: "min(300px, 60vw)" }} />
          ) : (
            <h1
              data-testid="home-greeting"
              style={{
                margin: 0,
                fontFamily: "var(--font-experiment)",
                fontWeight: "var(--weight-display)" as unknown as number,
                fontSize: "clamp(22px, 2.6vw, 32px)",
                letterSpacing: "-0.035em",
                lineHeight: 1.05,
                color: "var(--text-primary)",
              }}
            >
              {greeting}, {greetName}
            </h1>
          )}
          <div className="flex items-center" style={{ gap: 12 }}>
            <MarketStatusChip />
            <p
              style={{
                margin: 0,
                fontFamily: "var(--font-ui)",
                fontSize: 12.5,
                color: "var(--text-tertiary)",
                letterSpacing: "-0.005em",
              }}
            >
              {today}
            </p>
          </div>
        </div>

        <IndicesStrip
          onSelect={(idx) => router.push(`/stock/${encodeURIComponent(idx.symbol)}`)}
        />
      </div>

      {/* ── Bento grid (fills remaining height at lg+) ───────────────── */}
      <div
        className="grid grid-cols-1 lg:min-h-0 lg:flex-1 lg:grid-cols-6 lg:grid-rows-[1.05fr_1fr]"
        style={{ gap: "clamp(10px, 1.5vh, 16px)", gridAutoRows: "minmax(160px, auto)" }}
      >
        {/* Row 1 */}
        <div className="lg:col-span-2 lg:col-start-1 lg:row-start-1 min-h-0">
          <PortfolioSummaryCard onGoTab={onGoTab} />
        </div>
        <div className="lg:col-span-2 lg:col-start-3 lg:row-start-1 min-h-0">
          <WatchlistCard onGoTab={onGoTab} />
        </div>
        <div className="lg:col-span-2 lg:col-start-5 lg:row-start-1 min-h-0">
          <ChatPromptsCard onGoTab={onGoTab} onSend={onSendPrompt} />
        </div>

        {/* Row 2 */}
        <div className="lg:col-span-3 lg:col-start-1 lg:row-start-2 min-h-0">
          <StrategiesCard onOpenAgent={onOpenAgent} onOpenStrategies={onOpenStrategies} />
        </div>
        <div className="lg:col-span-3 lg:col-start-4 lg:row-start-2 min-h-0">
          <ViewsCard onGoTab={onGoTab} />
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Market status chip
// ---------------------------------------------------------------------------

function MarketStatusChip(): React.ReactElement {
  const [status, setStatus] = useState<{ open: boolean; label: string } | null>(null);
  // Compute on the client only (SSR has no reliable IST clock) and refresh
  // every minute so the chip flips at the open/close boundary.
  useEffect(() => {
    setStatus(marketStatus());
    const id = setInterval(() => setStatus(marketStatus()), 60_000);
    return () => clearInterval(id);
  }, []);
  if (!status) return <span style={{ width: 96, height: 22 }} aria-hidden />;
  const color = status.open ? "var(--color-profit)" : "var(--text-tertiary)";
  return (
    <span
      className="inline-flex items-center"
      style={{
        fontFamily: "var(--font-ui)",
        fontSize: 11.5,
        fontWeight: 600,
        letterSpacing: "-0.005em",
        color,
        whiteSpace: "nowrap",
      }}
    >
      {status.label}
    </span>
  );
}

// ---------------------------------------------------------------------------
// Card shell — a Panel that flexes to fill its bento cell, with a header row
// and a body that scrolls inside itself as a fallback.
// ---------------------------------------------------------------------------

function CardShell({
  Icon,
  title,
  badge,
  actionLabel,
  onAction,
  action,
  children,
  bodyClassName,
  scroll = true,
  clip = true,
}: {
  Icon: React.ComponentType<{ size?: number; strokeWidth?: number; style?: React.CSSProperties }>;
  title: string;
  badge?: React.ReactNode;
  actionLabel?: string;
  onAction?: () => void;
  /** Custom header-right control; overrides the actionLabel button when set. */
  action?: React.ReactNode;
  children: React.ReactNode;
  bodyClassName?: string;
  scroll?: boolean;
  /** When false (non-scroll cards only), the body never clips — lets a row's
   *  hover highlight sit flush to the edge without being cut. */
  clip?: boolean;
}): React.ReactElement {
  return (
    <Panel
      pad={16}
      className="flex h-full min-h-0 flex-col"
      style={{
        gap: "clamp(8px, 1.1vh, 12px)",
        padding: "clamp(10px, 1.6vh, 16px)",
        background: "var(--bg-base)",
        boxShadow: "var(--shadow-card)",
      }}
    >
      <div className="flex shrink-0 items-center justify-between" style={{ gap: 10 }}>
        <div className="inline-flex min-w-0 items-center" style={{ gap: 8 }}>
          <Icon size={15} strokeWidth={1.9} style={{ color: "var(--text-tertiary)", flexShrink: 0 }} />
          <span
            style={{
              fontFamily: "var(--font-display)",
              fontWeight: "var(--weight-display)" as unknown as number,
              fontSize: 15.5,
              letterSpacing: "-0.02em",
              color: "var(--text-primary)",
              whiteSpace: "nowrap",
            }}
          >
            {title}
          </span>
          {badge}
        </div>
        {action ? action : actionLabel && onAction && (
          <button
            type="button"
            onClick={onAction}
            className="inline-flex shrink-0 items-center"
            style={{
              gap: 4,
              padding: "4px 8px",
              background: "transparent",
              border: "none",
              borderRadius: "var(--radius-sm)",
              color: "var(--text-secondary)",
              fontFamily: "var(--font-ui)",
              fontSize: 12,
              fontWeight: 500,
              cursor: "pointer",
              transition: "color 0.2s var(--ease-quartr)",
            }}
            onMouseEnter={(e) => { e.currentTarget.style.color = "var(--text-primary)"; }}
            onMouseLeave={(e) => { e.currentTarget.style.color = "var(--text-secondary)"; }}
          >
            {actionLabel}
            <ArrowRight size={12} strokeWidth={2} aria-hidden />
          </button>
        )}
      </div>
      <div
        className={`min-h-0 flex-1 ${scroll ? "overflow-y-auto quartr-no-scrollbar" : clip ? "overflow-hidden" : "overflow-visible"} ${bodyClassName ?? ""}`}
      >
        {children}
      </div>
    </Panel>
  );
}

// ---------------------------------------------------------------------------
// Indices strip
// ---------------------------------------------------------------------------

type IndicesState =
  | { kind: "loading" }
  | { kind: "ok"; items: IndexQuote[] }
  | { kind: "empty" };

function IndicesStrip({
  onSelect,
}: {
  onSelect: (idx: IndexQuote) => void;
}): React.ReactElement {
  const [state, setState] = useState<IndicesState>({ kind: "loading" });
  const [sparks, setSparks] = useState<Record<string, number[]>>({});

  useEffect(() => {
    let alive = true;
    getMarketIndices()
      .then((r) => {
        if (!alive) return;
        if (isError(r) || r.data.items.length === 0) {
          setState({ kind: "empty" });
          return;
        }
        const items = r.data.items.slice(0, 4);
        setState({ kind: "ok", items });
        // Fire off the trend series in parallel; each fills in as it lands.
        items.forEach((idx) => {
          void fetchSpark(idx.symbol).then((series) => {
            if (alive && series) setSparks((m) => ({ ...m, [idx.symbol]: series }));
          });
        });
      })
      .catch(() => alive && setState({ kind: "empty" }));
    return () => { alive = false; };
  }, []);

  if (state.kind === "empty") return <></>;

  return (
    <div
      className="grid grid-cols-2 lg:grid-cols-4"
      style={{ gap: "clamp(8px, 1.1vh, 12px)" }}
      data-testid="home-indices"
    >
      {state.kind === "loading"
        ? Array.from({ length: 4 }).map((_, i) => (
            <Panel key={i} pad={15} style={{ background: "var(--bg-base)", boxShadow: "var(--shadow-card)" }}>
              <Skeleton style={{ height: 12, width: "60%", marginBottom: 10 }} />
              <Skeleton style={{ height: 22, width: "80%", marginBottom: 10 }} />
              <Skeleton style={{ height: 30, width: "100%" }} />
            </Panel>
          ))
        : state.items.map((idx) => (
            <IndexCard key={idx.symbol} idx={idx} spark={sparks[idx.symbol]} onSelect={onSelect} />
          ))}
    </div>
  );
}

// Publishable logo.dev token (pk_…) — safe to expose in the frontend; it is the
// same token the backend embeds in company logo_url values. logo.dev serves
// logos BY DOMAIN, so the exchange mark comes from nseindia.com / bseindia.com.
const LOGODEV_TOKEN = "pk_X3WtLGU0RTuTq-o9GTLEsg";

/** Small exchange logo for an index — SENSEX is BSE, the NIFTY family is NSE.
 *  Pulls the exchange mark from logo.dev by domain; on any load failure it
 *  falls back to a monochrome NSE/BSE text badge. */
function IndexEmblem({ name }: { name: string }): React.ReactElement {
  const [errored, setErrored] = useState(false);
  const bse = /sensex/i.test(name);
  const domain = bse ? "bseindia.com" : "nseindia.com";
  const code = bse ? "BSE" : "NSE";
  const box: React.CSSProperties = {
    width: 26,
    height: 26,
    flexShrink: 0,
  };
  if (errored) {
    return (
      <span
        aria-hidden
        className="inline-flex items-center justify-center"
        style={{
          ...box,
          fontFamily: "var(--font-ui)",
          fontSize: 8.5,
          fontWeight: 700,
          letterSpacing: "0.04em",
          color: "var(--text-secondary)",
        }}
      >
        {code}
      </span>
    );
  }
  return (
    // eslint-disable-next-line @next/next/no-img-element
    <img
      src={`https://img.logo.dev/${domain}?token=${LOGODEV_TOKEN}&size=128&format=png`}
      alt={`${code} logo`}
      width={26}
      height={26}
      className="shrink-0 object-contain"
      style={box}
      onError={() => setErrored(true)}
      loading="lazy"
    />
  );
}

function IndexCard({
  idx,
  spark,
  onSelect,
}: {
  idx: IndexQuote;
  spark?: number[];
  onSelect: (idx: IndexQuote) => void;
}): React.ReactElement {
  const up = idx.change >= 0;
  const color = up ? "var(--color-profit)" : "var(--color-loss)";
  return (
    <div
      role="button"
      tabIndex={0}
      aria-label={`Open ${idx.name}`}
      className="home-index-card"
      onClick={() => onSelect(idx)}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          onSelect(idx);
        }
      }}
      style={{ cursor: "pointer" }}
    >
    <Panel
      pad={12}
      className="flex flex-col"
      style={{
        gap: "clamp(5px, 0.8vh, 8px)",
        padding: "clamp(8px, 1.1vh, 12px)",
        overflow: "hidden",
        background: "var(--bg-base)",
        boxShadow: "var(--shadow-card)",
      }}
    >
      <div className="flex items-center justify-between" style={{ gap: 8 }}>
        <div className="flex min-w-0 items-center" style={{ gap: 8 }}>
          <IndexEmblem name={idx.name} />
          <span
            style={{
              fontFamily: "var(--font-ui)",
              fontSize: 10.5,
              fontWeight: 600,
              letterSpacing: "0.02em",
              color: "var(--text-tertiary)",
              textTransform: "uppercase",
              whiteSpace: "nowrap",
              overflow: "hidden",
              textOverflow: "ellipsis",
            }}
            title={idx.name}
          >
            {idx.name}
          </span>
        </div>
        <span
          className="tabular-nums inline-flex shrink-0 items-center"
          style={{
            gap: 3,
            fontFamily: "var(--font-display)",
            fontSize: 11,
            fontWeight: 600,
            color,
          }}
        >
          <ArrowUpRight size={11} strokeWidth={2.6} style={up ? undefined : { transform: "rotate(90deg)" }} />
          {fmtSignedPct(idx.change_pct)}
        </span>
      </div>
      <div className="flex items-end justify-between" style={{ gap: 10 }}>
        <div className="flex flex-col" style={{ gap: 1 }}>
          <span
            className="tabular-nums"
            style={{
              fontFamily: "var(--font-display)",
              fontWeight: "var(--weight-display)" as unknown as number,
              fontSize: 18,
              letterSpacing: "-0.03em",
              color: "var(--text-primary)",
              lineHeight: 1.05,
            }}
          >
            {fmtNum(idx.value)}
          </span>
          <span
            className="tabular-nums"
            style={{ fontFamily: "var(--font-display)", fontSize: 11, color }}
          >
            {up ? "+" : "−"}{fmtNum(Math.abs(idx.change))}
          </span>
        </div>
        <div style={{ width: 84, flexShrink: 0 }}>
          {spark ? (
            <AreaSpark data={spark} up={up} width={84} height={26} />
          ) : (
            <div style={{ height: 26 }} />
          )}
        </div>
      </div>
    </Panel>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Portfolio summary card
// ---------------------------------------------------------------------------

type PortfolioState =
  | { kind: "loading" }
  | { kind: "ok"; summary: PortfolioSummary; holdings: Holding[] }
  | { kind: "empty" };

function PortfolioSummaryCard({
  onGoTab,
}: {
  onGoTab: HomeTabProps["onGoTab"];
}): React.ReactElement {
  const mode = useTradingMode();
  const [state, setState] = useState<PortfolioState>({ kind: "loading" });

  useEffect(() => {
    let alive = true;
    setState({ kind: "loading" });
    Promise.all([getPortfolioSummary(), getPortfolioHoldings()])
      .then(([summaryRes, holdingsRes]) => {
        if (!alive) return;
        setState(
          isError(summaryRes)
            ? { kind: "empty" }
            : { kind: "ok", summary: summaryRes.data, holdings: isError(holdingsRes) ? [] : holdingsRes.data },
        );
      })
      .catch(() => alive && setState({ kind: "empty" }));
    return () => { alive = false; };
    // Re-fetch when the trading mode flips (real ↔ paper).
  }, [mode]);

  // Best/worst holding by OVERALL return since entry (not today's move, which
  // is 0 when the market is closed) — null when there are no holdings, or a
  // single tile when only one holding exists (gainer === loser).
  const movers = useMemo(() => {
    if (state.kind !== "ok" || state.holdings.length === 0) return null;
    const sorted = [...state.holdings].sort(
      (a, b) => overallReturnPct(b) - overallReturnPct(a),
    );
    const gainer = sorted[0]!;
    const loser = sorted[sorted.length - 1]!;
    return gainer.tradingsymbol === loser.tradingsymbol ? { gainer, loser: null } : { gainer, loser };
  }, [state]);

  const moverSymbols = useMemo(
    () => [movers?.gainer.tradingsymbol, movers?.loser?.tradingsymbol].filter((s): s is string => !!s),
    [movers],
  );
  const moverLogos = useCompanyLogos(moverSymbols);

  const badge =
    mode === "paper" ? (
      <span
        style={{
          fontFamily: "var(--font-ui)",
          fontSize: 10,
          fontWeight: 700,
          letterSpacing: "0.04em",
          textTransform: "uppercase",
          color: "#d97706",
          border: "1px solid #d9770655",
          borderRadius: "var(--radius-pill)",
          padding: "1px 6px",
        }}
      >
        Paper
      </span>
    ) : undefined;

  return (
    <CardShell
      Icon={Wallet}
      title="Portfolio"
      badge={badge}
      actionLabel="Open"
      onAction={() => onGoTab("portfolio")}
      // Scrolls (hidden scrollbar) as a last-resort fallback rather than
      // clipping: on a viewport too short even after the vh-clamped spacing
      // and the movers-row hide kick in, silently cutting off the invested/
      // P&L figures is worse than an occasional scroll.
      bodyClassName="flex flex-col"
    >
      {state.kind === "loading" ? (
        <div className="grid grid-cols-2" style={{ gap: 16 }}>
          {Array.from({ length: 4 }).map((_, i) => (
            <div key={i}>
              <Skeleton style={{ height: 11, width: "70%", marginBottom: 9 }} />
              <Skeleton style={{ height: 20, width: "85%" }} />
            </div>
          ))}
        </div>
      ) : state.kind === "empty" ? (
        <EmptyHint
          icon={PieChart}
          // Paper trading never touches a broker, so prompting to connect one
          // is asking for something the mode does not use.
          text={
            mode === "paper"
              ? "No paper positions yet. Your simulated holdings and P&L will show here."
              : "Connect a broker to see your holdings and P&L here."
          }
          cta="Go to Portfolio"
          onClick={() => onGoTab("portfolio")}
        />
      ) : (
        <div className="flex h-full flex-col justify-between" style={{ gap: "clamp(8px, 1.6vh, 14px)" }}>
          {/* Hero — total value + a live day-change pill */}
          <div className="flex flex-col" style={{ gap: "clamp(4px, 0.9vh, 8px)" }}>
            <span
              style={{
                fontFamily: "var(--font-ui)",
                fontSize: 10.5,
                fontWeight: 500,
                letterSpacing: "0.02em",
                color: "var(--metric-label)",
              }}
            >
              Total value
            </span>
            <div className="flex flex-wrap items-center" style={{ gap: 10 }}>
              <span
                className="tabular-nums"
                style={{
                  fontFamily: "var(--font-display)",
                  fontWeight: "var(--weight-display)" as unknown as number,
                  fontSize: "clamp(22px, 3vh, 30px)",
                  letterSpacing: "-0.03em",
                  color: "var(--text-primary)",
                  lineHeight: 1,
                }}
              >
                {fmtINR(state.summary.total_value)}
              </span>
              <ChangePill amount={state.summary.day_pnl} suffix="today" />
            </div>
          </div>
          <InvestedBar summary={state.summary} />
          <div className="grid grid-cols-2" style={{ gap: "clamp(10px, 1.6vh, 16px)" }}>
            <Stat label="Invested" value={fmtINR(state.summary.invested_value)} />
            <SignedStat
              label="Total P&L"
              amount={state.summary.total_pnl}
              pct={state.summary.total_pnl_pct}
            />
          </div>
          {movers && (
            <div
              className="home-portfolio-movers grid grid-cols-2"
              style={{
                gap: "clamp(10px, 1.6vh, 16px)",
                paddingTop: "clamp(6px, 1.3vh, 12px)",
                borderTop: "1px solid var(--glass-border)",
              }}
            >
              <MoverTile
                label={movers.loser ? "Top gainer" : "Your holding"}
                holding={movers.gainer}
                logoUrl={moverLogos[movers.gainer.tradingsymbol.toUpperCase()] ?? null}
              />
              {movers.loser && (
                <MoverTile
                  label="Top loser"
                  holding={movers.loser}
                  logoUrl={moverLogos[movers.loser.tradingsymbol.toUpperCase()] ?? null}
                />
              )}
            </div>
          )}
          <div
            className="flex items-center"
            style={{
              gap: 8,
              paddingTop: "clamp(6px, 1.3vh, 12px)",
              borderTop: "1px solid var(--glass-border)",
              fontFamily: "var(--font-ui)",
              fontSize: 11.5,
              color: "var(--text-tertiary)",
            }}
          >
            <PieChart size={13} strokeWidth={1.8} />
            {state.summary.num_holdings > 0
              ? `${state.summary.num_holdings} holding${state.summary.num_holdings === 1 ? "" : "s"} · tap Open for the full breakdown`
              : "No holdings yet · tap Open to get started"}
          </div>
        </div>
      )}
    </CardShell>
  );
}

function Stat({
  label,
  value,
  emphasis,
}: {
  label: string;
  value: string;
  emphasis?: boolean;
}): React.ReactElement {
  return (
    <div className="flex flex-col" style={{ gap: 5 }}>
      <span
        style={{
          fontFamily: "var(--font-ui)",
          fontSize: 10.5,
          fontWeight: 500,
          letterSpacing: "0.02em",
          color: "var(--metric-label)",
        }}
      >
        {label}
      </span>
      <span
        className="tabular-nums"
        style={{
          fontFamily: "var(--font-display)",
          fontWeight: "var(--weight-display)" as unknown as number,
          fontSize: emphasis ? 22 : 17,
          letterSpacing: "-0.025em",
          color: "var(--text-primary)",
        }}
      >
        {value}
      </span>
    </div>
  );
}

function SignedStat({
  label,
  amount,
  pct,
}: {
  label: string;
  amount: number;
  pct?: number;
}): React.ReactElement {
  const pos = amount >= 0;
  const color = pos ? "var(--color-profit)" : "var(--color-loss)";
  return (
    <div className="flex flex-col" style={{ gap: 5 }}>
      <span
        style={{
          fontFamily: "var(--font-ui)",
          fontSize: 10.5,
          fontWeight: 500,
          letterSpacing: "0.02em",
          color: "var(--metric-label)",
        }}
      >
        {label}
      </span>
      <span
        className="tabular-nums"
        style={{
          fontFamily: "var(--font-display)",
          fontSize: 17,
          letterSpacing: "-0.025em",
          color,
          whiteSpace: "nowrap",
        }}
      >
        {pos ? "+" : "−"}{fmtINR(Math.abs(amount)).replace(/^[-−]/, "")}
        {pct !== undefined && (
          <span style={{ fontSize: 11.5, opacity: 0.85, marginLeft: 5 }}>({fmtSignedPct(pct)})</span>
        )}
      </span>
    </div>
  );
}

/** Overall return since entry for a holding: unrealised P&L over cost basis.
 *  Reads the actual P&L (not last−avg) so it's right even for unmarked paper
 *  lots. 0 when there's no cost basis. */
function overallReturnPct(h: Holding): number {
  // |cost basis| so a profitable SHORT (negative quantity) reads as a positive
  // return, not a sign-flipped one.
  const cost = Math.abs(h.average_price * h.quantity);
  return cost > 0 ? (h.pnl / cost) * 100 : 0;
}

/** Best/worst holding by OVERALL return since entry — logo, symbol, and the
 *  signed return, matching the watchlist row's visual grammar at a smaller
 *  scale. */
function MoverTile({
  label,
  holding,
  logoUrl,
}: {
  label: string;
  holding: Holding;
  logoUrl: string | null;
}): React.ReactElement {
  const ret = overallReturnPct(holding);
  const pos = ret >= 0;
  const color = pos ? "var(--color-profit)" : "var(--color-loss)";
  return (
    <Link
      href={`/stock/${encodeURIComponent(holding.tradingsymbol)}`}
      className="flex min-w-0 flex-col"
      style={{ gap: 6, textDecoration: "none" }}
    >
      <span
        style={{
          fontFamily: "var(--font-ui)",
          fontSize: 10.5,
          fontWeight: 500,
          letterSpacing: "0.02em",
          color: "var(--metric-label)",
        }}
      >
        {label}
      </span>
      <div className="flex min-w-0 items-center" style={{ gap: 8 }}>
        <CompanyLogo logoUrl={logoUrl} name={holding.tradingsymbol} symbol={holding.tradingsymbol} size={22} />
        <div className="flex min-w-0 flex-col">
          <span
            style={{
              fontFamily: "var(--font-ui)",
              fontSize: 12,
              fontWeight: 600,
              color: "var(--text-primary)",
              letterSpacing: "-0.01em",
              whiteSpace: "nowrap",
              overflow: "hidden",
              textOverflow: "ellipsis",
            }}
          >
            {holding.tradingsymbol}
          </span>
          <span
            className="tabular-nums"
            style={{ fontFamily: "var(--font-display)", fontSize: 11.5, fontWeight: 600, color }}
          >
            {fmtSignedPct(ret)}
          </span>
        </div>
      </div>
    </Link>
  );
}

/** A compact signed pill: coloured text + arrow on a faint outline. Reads as a
 *  status chip, not a filled colour block (kept within the borders-only house
 *  style — the tint is a 10%-alpha wash of the signed colour, not a pastel). */
function ChangePill({ amount, suffix }: { amount: number; suffix?: string }): React.ReactElement {
  const pos = amount >= 0;
  const color = pos ? "var(--color-profit)" : "var(--color-loss)";
  return (
    <span
      className="tabular-nums inline-flex items-center"
      style={{
        gap: 4,
        color,
        fontFamily: "var(--font-display)",
        fontSize: 12,
        fontWeight: 600,
        lineHeight: 1,
      }}
    >
      <ArrowUpRight size={12} strokeWidth={2.6} style={pos ? undefined : { transform: "rotate(90deg)" }} />
      {pos ? "+" : "−"}{fmtINR(Math.abs(amount)).replace(/^[-−]/, "")}
      {suffix && <span style={{ opacity: 0.75, fontWeight: 500 }}>{suffix}</span>}
    </span>
  );
}

/** A slim invested→current-holdings bar with the unrealised gain/loss, filling
 *  the portfolio card's middle band with something meaningful rather than a
 *  void. The gain is the REAL unrealised P&L (`total_pnl`) — NOT
 *  `total_value − invested`, which wrongly folds in uninvested cash (total_value
 *  = holdings mark + cash), so a down book was showing a fake positive number.
 *  Current holdings value = invested + total_pnl, so the bar reflects cost vs
 *  what those holdings are worth now, consistent with the Total P&L below. */
function InvestedBar({ summary }: { summary: PortfolioSummary }): React.ReactElement {
  const invested = Math.max(summary.invested_value, 0);
  const gain = summary.total_pnl;
  const value = Math.max(invested + gain, 0);
  const pos = gain >= 0;
  // Fraction of the bar that is "principal"; the remainder is gain (or the
  // whole bar shrinks toward the value on a loss). Clamp to [0,1].
  const base = pos ? (value > 0 ? invested / value : 1) : (invested > 0 ? value / invested : 1);
  const basePct = Math.max(0, Math.min(1, base)) * 100;
  const gainColor = pos ? "var(--color-profit)" : "var(--color-loss)";
  return (
    <div className="flex flex-col" style={{ gap: "clamp(4px, 0.9vh, 7px)" }}>
      <div className="flex items-center justify-between" style={{ fontFamily: "var(--font-ui)", fontSize: 10.5 }}>
        <span style={{ color: "var(--metric-label)", fontWeight: 500 }}>Unrealised</span>
        <span className="tabular-nums" style={{ color: gainColor, fontFamily: "var(--font-display)", fontWeight: 600 }}>
          {pos ? "+" : "−"}{fmtINR(Math.abs(gain)).replace(/^[-−]/, "")}
        </span>
      </div>
      <div
        style={{
          position: "relative",
          height: 6,
          borderRadius: 999,
          overflow: "hidden",
          background: "var(--surface-active)",
        }}
      >
        <div
          style={{
            position: "absolute",
            inset: 0,
            width: `${basePct}%`,
            background: "var(--text-tertiary)",
            opacity: 0.55,
          }}
        />
        <div
          style={{
            position: "absolute",
            top: 0,
            bottom: 0,
            left: `${basePct}%`,
            right: 0,
            background: gainColor,
            opacity: 0.9,
          }}
        />
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Watchlist card
// ---------------------------------------------------------------------------

type WlRow = {
  symbol: string;
  name: string | null;
  sector: string | null;
  logoUrl: string | null;
  ltp: number | null;
  changePct: number | null;
  spark: number[] | null;
};

function WatchlistCard({
  onGoTab,
}: {
  onGoTab: HomeTabProps["onGoTab"];
}): React.ReactElement {
  const wl = useWatchlists();
  const active = wl.lists.find((l) => l.id === wl.activeId);
  const tickers = useMemo(() => (active?.tickers ?? []).slice(0, 7), [active]);
  const tickersKey = tickers.join(",");
  const [rows, setRows] = useState<WlRow[] | null>(null);

  useEffect(() => {
    if (tickers.length === 0) {
      setRows([]);
      return;
    }
    let alive = true;
    setRows(null);
    Promise.all(
      tickers.map(async (sym): Promise<WlRow> => {
        const [r, spark] = await Promise.all([
          getStockQuote(sym).catch(() => null),
          fetchSpark(sym),
        ]);
        if (!r || isError(r))
          return { symbol: sym, name: null, sector: null, logoUrl: null, ltp: null, changePct: null, spark };
        return {
          symbol: sym,
          name: r.data.name,
          sector: r.data.sector,
          logoUrl: r.data.logo_url ?? null,
          ltp: r.data.ltp,
          changePct: r.data.change_pct,
          spark,
        };
      }),
    ).then((res) => { if (alive) setRows(res); });
    return () => { alive = false; };
  }, [tickersKey]); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <CardShell
      Icon={BarChart2}
      title="Watchlist"
      action={<WatchlistSwitcher lists={wl.lists} activeId={wl.activeId} />}
    >
      {rows === null ? (
        <div className="flex flex-col" style={{ gap: 14 }}>
          {tickers.map((t) => (
            <div key={t} className="flex items-center justify-between">
              <Skeleton style={{ height: 13, width: 70 }} />
              <Skeleton style={{ height: 13, width: 56 }} />
            </div>
          ))}
        </div>
      ) : rows.length === 0 ? (
        <EmptyHint
          icon={BarChart2}
          text="Your watchlist is empty. Add tickers from the Screener."
          cta="Open Screener"
          onClick={() => onGoTab("screener")}
        />
      ) : (
        <ul className="home-watchlist-list flex flex-col" style={{ margin: 0, padding: 0, listStyle: "none" }}>
          {rows.map((row, i) => (
            <WatchlistRow key={row.symbol} row={row} last={i === rows.length - 1} />
          ))}
        </ul>
      )}
    </CardShell>
  );
}

/** Numbered 1–5 slot switch — the exact borderless segmented control the
 *  Screener's watchlist strip uses: active slot is a solid ink pill, the rest
 *  are plain numerals (dimmed further when empty). */
function WatchlistSwitcher({
  lists,
  activeId,
}: {
  lists: Watchlist[];
  activeId: number;
}): React.ReactElement {
  return (
    <div className="inline-flex shrink-0 items-center" style={{ gap: 4 }} role="group" aria-label="Switch watchlist">
      {lists.map((w) => {
        const isActive = w.id === activeId;
        const count = w.tickers.length;
        return (
          <button
            key={w.id}
            type="button"
            onClick={() => setActiveWatchlist(w.id)}
            title={count ? `${count} stock${count === 1 ? "" : "s"}` : "Empty"}
            style={{
              padding: "2px 8px",
              border: "none",
              borderRadius: "var(--radius-sm)",
              fontFamily: "var(--font-ui)",
              fontSize: 11.5,
              fontWeight: 500,
              fontVariantNumeric: "tabular-nums",
              cursor: "pointer",
              background: isActive ? "var(--text-primary)" : "transparent",
              color: isActive
                ? "var(--bg-primary)"
                : count
                  ? "var(--text-secondary)"
                  : "var(--text-tertiary)",
              transition: "color 0.2s var(--ease-quartr), background-color 0.2s var(--ease-quartr)",
            }}
            onMouseEnter={(e) => {
              if (!isActive) e.currentTarget.style.color = "var(--text-primary)";
            }}
            onMouseLeave={(e) => {
              if (!isActive)
                e.currentTarget.style.color = count ? "var(--text-secondary)" : "var(--text-tertiary)";
            }}
          >
            {w.id}
          </button>
        );
      })}
    </div>
  );
}

function WatchlistRow({ row, last }: { row: WlRow; last: boolean }): React.ReactElement {
  const has = row.changePct !== null;
  const pos = (row.changePct ?? 0) >= 0;
  const color = pos ? "var(--color-profit)" : "var(--color-loss)";
  return (
    <li
      style={{
        borderBottom: last ? "none" : "1px solid var(--glass-border)",
      }}
    >
      <Link
        href={`/stock/${encodeURIComponent(row.symbol)}`}
        className="flex items-center"
        style={{
          gap: 10,
          padding: "clamp(4px, 0.9vh, 7px) 0",
          textDecoration: "none",
        }}
      >
      <CompanyLogo logoUrl={row.logoUrl} name={row.name ?? row.symbol} symbol={row.symbol} size={30} />
      <div className="flex min-w-0 flex-col" style={{ flex: "1 1 auto" }}>
        <span
          style={{
            fontFamily: "var(--font-ui)",
            fontSize: 12.5,
            fontWeight: 600,
            color: "var(--text-primary)",
            letterSpacing: "-0.01em",
          }}
        >
          {row.symbol}
        </span>
        {(row.sector || row.name) && (
          <span
            style={{
              fontFamily: "var(--font-ui)",
              fontSize: 10.5,
              color: "var(--text-tertiary)",
              whiteSpace: "nowrap",
              overflow: "hidden",
              textOverflow: "ellipsis",
              maxWidth: 130,
            }}
          >
            {row.sector ?? row.name}
          </span>
        )}
      </div>
      {row.spark && (
        <div style={{ width: 58, flexShrink: 0, opacity: 0.9 }} aria-hidden>
          <AreaSpark data={row.spark} up={pos} width={58} height={22} />
        </div>
      )}
      <div className="flex flex-col items-end tabular-nums" style={{ flexShrink: 0 }}>
        <span style={{ fontFamily: "var(--font-display)", fontSize: 12.5, color: "var(--text-primary)" }}>
          {row.ltp !== null ? fmtNum(row.ltp) : "—"}
        </span>
        {has ? (
          <span style={{ fontFamily: "var(--font-display)", fontSize: 11, color }}>
            {fmtSignedPct(row.changePct!)}
          </span>
        ) : (
          <span style={{ fontFamily: "var(--font-ui)", fontSize: 10.5, color: "var(--text-tertiary)" }}>
            n/a
          </span>
        )}
      </div>
      </Link>
    </li>
  );
}

// ---------------------------------------------------------------------------
// Chat prompts card
// ---------------------------------------------------------------------------

// Smallest height (px) a single prompt row can take before its one line of
// text + icon start to look cramped against the row border. The fit maths
// below never lets a visible row fall below this, so rows always read cleanly.
const PROMPT_ROW_MIN = 38;
// Inter-row gap in px — the resolved midpoint of the clamp() on the list below.
// Used only to size the fit; the real gap is still the clamp.
const PROMPT_ROW_GAP = 8;

/** How many prompt rows fit in `height` px without any row dropping below
 *  PROMPT_ROW_MIN. n flex rows share `height` with (n-1) gaps between them, so
 *  the tallest n that keeps every row ≥ min is floor((height+gap)/(min+gap)).
 *  Clamped to [3, total] — never blank, never more than we have. */
function promptsThatFit(height: number, total: number): number {
  if (height <= 0) return total;
  const n = Math.floor((height + PROMPT_ROW_GAP) / (PROMPT_ROW_MIN + PROMPT_ROW_GAP));
  return Math.max(3, Math.min(total, n));
}

function ChatPromptsCard({
  onGoTab,
  onSend,
}: {
  onGoTab: HomeTabProps["onGoTab"];
  onSend: (prompt: string) => void;
}): React.ReactElement {
  const listRef = useRef<HTMLDivElement | null>(null);
  // How many prompts to render. Measured from the card's real height rather
  // than guessed from the device: the tall monitor holds all six, a laptop or
  // small MacBook trims to what stays readable — no overflow on either, and no
  // dependence on physical-pixel counts (which mis-sized both). Starts at four
  // (always safe — never overflows on first paint) and the observer corrects
  // it up or down once the card has a measured height.
  const [visible, setVisible] = useState(4);
  useEffect(() => {
    const el = listRef.current;
    if (!el) return;
    // Only the lg+ bento fixes the card to a viewport-derived height where the
    // fit maths is stable; below lg the board is a single scrolling column, so
    // the card sizes to its own content and there's room for all six.
    const lg = window.matchMedia("(min-width: 1024px)");
    const measure = (): void => {
      setVisible(lg.matches ? promptsThatFit(el.clientHeight, CHAT_PROMPTS.length) : CHAT_PROMPTS.length);
    };
    measure();
    // The list is flex-1, so at lg its height tracks the card (which the bento
    // grid sizes from the viewport) independently of how many rows we render —
    // the measurement is stable, not circular, and re-fires on any resize/zoom.
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    lg.addEventListener("change", measure);
    return () => {
      ro.disconnect();
      lg.removeEventListener("change", measure);
    };
  }, []);

  return (
    <CardShell
      Icon={MessageSquare}
      title="Chat with Pivot"
      actionLabel="Open"
      onAction={() => onGoTab("chat")}
      scroll={false}
      clip={false}
      bodyClassName="flex flex-col"
    >
      {/* No inner scroll — each prompt grows to an equal share of the card
          height (flex-1), so they read as one evenly-spaced stack that fills
          the card rather than floating apart. `visible` (measured above) keeps
          six on a tall monitor and trims on shorter screens so no row is
          cramped. The small vertical padding keeps the top/bottom rows' hover
          highlight visible. */}
      <div
        ref={listRef}
        className="home-chat-prompts flex flex-1 flex-col"
        style={{ gap: "clamp(6px, 1.1vh, 10px)", paddingBlock: 2 }}
      >
        {CHAT_PROMPTS.slice(0, visible).map((p) => (
          <PromptRow key={p.label} label={p.label} Icon={p.Icon} onClick={() => onSend(p.label)} />
        ))}
      </div>
    </CardShell>
  );
}

function PromptRow({
  label,
  Icon,
  onClick,
}: {
  label: string;
  Icon: React.ComponentType<{ size?: number; strokeWidth?: number; style?: React.CSSProperties }>;
  onClick: () => void;
}): React.ReactElement {
  return (
    <button
      type="button"
      onClick={onClick}
      className="inline-flex w-full flex-1 items-center"
      style={{
        gap: 9,
        minHeight: 0,
        padding: "clamp(5px, 1vh, 8px) 11px",
        background: "var(--bg-base)",
        border: "1px solid var(--glass-border)",
        borderRadius: "var(--radius-md)",
        color: "var(--text-secondary)",
        fontFamily: "var(--font-ui)",
        fontSize: 12.5,
        fontWeight: "var(--weight-medium)" as unknown as number,
        cursor: "pointer",
        textAlign: "left",
        transition:
          "color 0.25s var(--ease-quartr), background-color 0.25s var(--ease-quartr), border-color 0.25s var(--ease-quartr)",
      }}
      onMouseEnter={(e) => {
        e.currentTarget.style.color = "var(--text-primary)";
        e.currentTarget.style.borderColor = "var(--glass-border-hover)";
        e.currentTarget.style.background = "var(--bg-elevated)";
      }}
      onMouseLeave={(e) => {
        e.currentTarget.style.color = "var(--text-secondary)";
        e.currentTarget.style.borderColor = "var(--glass-border)";
        e.currentTarget.style.background = "var(--bg-base)";
      }}
    >
      <Icon size={13} strokeWidth={1.8} style={{ color: "var(--text-tertiary)", flexShrink: 0 }} />
      <span
        style={{ whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}
      >
        {label}
      </span>
    </button>
  );
}

// ---------------------------------------------------------------------------
// Prebuilt strategies card
// ---------------------------------------------------------------------------

function StrategiesCard({
  onOpenAgent,
  onOpenStrategies,
}: {
  onOpenAgent: HomeTabProps["onOpenAgent"];
  onOpenStrategies: HomeTabProps["onOpenStrategies"];
}): React.ReactElement {
  return (
    <CardShell Icon={Sparkles} title="Prebuilt strategies" bodyClassName="min-h-0">
      {/* vh-clamped gap/padding/icon sizing on the tiles below keeps all four
          tiles fitting without a scrollbar on any realistic viewport; the
          card still scrolls (hidden scrollbar) as a last-resort fallback
          rather than ever clipping a tile out of reach. */}
      <div
        className="home-strategies-grid grid h-full grid-cols-1 sm:grid-cols-2"
        style={{ gap: "clamp(6px, 1vh, 12px)", gridAutoRows: "1fr" }}
      >
        {PREBUILT_STRATEGIES.map((s) => (
          <StrategyCard key={s.title} tile={s} onOpenAgent={onOpenAgent} onOpenStrategies={onOpenStrategies} />
        ))}
      </div>
    </CardShell>
  );
}

function StrategyCard({
  tile,
  onOpenAgent,
  onOpenStrategies,
}: {
  tile: StrategyTile;
  onOpenAgent: HomeTabProps["onOpenAgent"];
  onOpenStrategies: HomeTabProps["onOpenStrategies"];
}): React.ReactElement {
  const { Icon } = tile;
  const open = (): void => {
    if (tile.kind === "option") {
      // The F&O tile has no agent of its own — jump to the Agents tab's
      // "Strategies" surface, which lists the user's options strategies.
      onOpenStrategies();
      return;
    }
    onOpenAgent({ matchName: tile.matchName, draft: tile.draft });
  };
  return (
    <div
      role="button"
      tabIndex={0}
      onClick={open}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          open();
        }
      }}
      className="home-strat home-strat-tile group flex h-full items-center transition-all duration-200 hover:-translate-y-0.5"
      style={{
        gap: 12,
        padding: "clamp(8px, 1.4vh, 13px) 14px",
        borderRadius: "var(--radius-md)",
        border: "1px solid var(--glass-border)",
        background: "var(--bg-base)",
        boxShadow: "var(--shadow-card)",
        cursor: "pointer",
        transitionTimingFunction: "var(--ease-quartr)",
      }}
      onMouseEnter={(e) => {
        e.currentTarget.style.borderColor = "var(--glass-border-hover)";
        e.currentTarget.style.boxShadow = "var(--shadow-card-hover)";
      }}
      onMouseLeave={(e) => {
        e.currentTarget.style.borderColor = "var(--glass-border)";
        e.currentTarget.style.boxShadow = "var(--shadow-card)";
      }}
    >
      <div
        className="home-strat-icon flex shrink-0 items-center justify-center"
        style={{
          width: "clamp(26px, 3.6vh, 34px)",
          height: "clamp(26px, 3.6vh, 34px)",
          borderRadius: "var(--radius-sm)",
          background: "var(--bg-base)",
          border: "1px solid var(--glass-border)",
          color: "var(--text-secondary)",
        }}
      >
        <Icon size={16} strokeWidth={1.8} />
      </div>
      <div className="flex min-w-0 flex-1 flex-col" style={{ gap: 3 }}>
        <span
          style={{
            fontFamily: "var(--font-ui)",
            fontSize: 13,
            fontWeight: 600,
            color: "var(--text-primary)",
            letterSpacing: "-0.01em",
            whiteSpace: "nowrap",
          }}
        >
          {tile.title}
        </span>
        <span
          style={{
            fontFamily: "var(--font-ui)",
            fontSize: 11.5,
            color: "var(--text-tertiary)",
            lineHeight: 1.35,
          }}
        >
          {tile.subtitle}
        </span>
      </div>
      <ArrowRight
        size={15}
        strokeWidth={2}
        className="home-strat-arrow shrink-0"
        style={{ color: "var(--text-tertiary)" }}
        aria-hidden
      />
    </div>
  );
}

// ---------------------------------------------------------------------------
// Views card ("Not sure what to trade?")
// ---------------------------------------------------------------------------

function ViewsCard({
  onGoTab,
}: {
  onGoTab: HomeTabProps["onGoTab"];
}): React.ReactElement {
  // Pick 2 opinions at random each visit so the block isn't always the same.
  // Seed with a stable slice for SSR, then shuffle on mount (client-only) to
  // avoid a hydration mismatch from Math.random during render.
  const [picks, setPicks] = useState(() => ACTIVE_SUMMARIES.slice(0, 2));
  useEffect(() => {
    setPicks(
      [...ACTIVE_SUMMARIES].sort(() => Math.random() - 0.5).slice(0, 2),
    );
  }, []);
  return (
    <CardShell
      Icon={Telescope}
      title="Not sure what to trade?"
      actionLabel="Browse opinions"
      onAction={() => onGoTab("views")}
      // Never scroll — every teaser card's full content (question, timeline,
      // return, Yes/No) must be visible at once. The vh-clamped ViewCard
      // sizing (see .home-views-grid rules in globals.css) shrinks the cards
      // to fit whatever height the row-2 cell has.
      scroll={false}
    >
      {/* The real View-Markets ViewCard — question · timeline · honest best-run
          return · Yes/No stance buttons. Two across, since the Home cell is
          ~half the board width (the Views tab gives each card a full third). */}
      <div className="home-views-grid grid grid-cols-1 sm:grid-cols-2" style={{ gap: 12, height: "100%" }}>
        {picks.map((v) => (
          <ViewCard key={v.id} view={v} onOpen={() => onGoTab("views")} sans />
        ))}
      </div>
    </CardShell>
  );
}

// ---------------------------------------------------------------------------
// Empty hint
// ---------------------------------------------------------------------------

function EmptyHint({
  icon: Icon,
  text,
  cta,
  onClick,
}: {
  icon: React.ComponentType<{ size?: number; strokeWidth?: number; style?: React.CSSProperties }>;
  text: string;
  cta: string;
  onClick: () => void;
}): React.ReactElement {
  return (
    <div className="flex h-full flex-col items-start justify-center" style={{ gap: 12 }}>
      <div className="inline-flex items-center" style={{ gap: 9 }}>
        <Icon size={17} strokeWidth={1.7} style={{ color: "var(--text-tertiary)" }} />
        <span style={{ fontFamily: "var(--font-ui)", fontSize: 12.5, color: "var(--text-secondary)", lineHeight: 1.45 }}>
          {text}
        </span>
      </div>
      <button
        type="button"
        onClick={onClick}
        className="inline-flex items-center"
        style={{
          gap: 6,
          padding: "7px 13px",
          background: "var(--text-primary)",
          color: "var(--bg-base)",
          border: "none",
          borderRadius: "var(--radius-pill)",
          fontFamily: "var(--font-ui)",
          fontSize: 12.5,
          fontWeight: 600,
          cursor: "pointer",
        }}
      >
        {cta} <ArrowRight size={13} strokeWidth={2.2} />
      </button>
    </div>
  );
}
