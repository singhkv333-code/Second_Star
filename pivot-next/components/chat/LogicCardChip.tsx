"use client";

/**
 * LogicCardChip — generic inline chat card for any tool that emits a
 * LogicCard (orders, GTT, SL, OCO, dip-buy, basket, squareoff, SIP
 * create, etc.). Backend tags `raw_data._render_hint = "logic_card"`
 * when one of those tools fires; ChatDemo dispatches here.
 *
 * Design language: smartwatch / Apple-Wallet style — pill card, single
 * display-size hero number with an eyebrow label above, a horizontal
 * stat strip below (thin caps label + bold tabular value), and a
 * compact filled CTA. No row dividers, generous padding, hairline
 * border. Inspired by the watch-tile reference grid.
 */

import { useEffect, useRef, useState } from "react";
import { Check, Loader2, ShieldAlert, TrendingDown, TrendingUp } from "lucide-react";
import { format, parseISO } from "date-fns";
import { cn } from "@/lib/utils";
import {
  getSparkline,
  getStockQuote,
  registerOrder,
  type OrderRegisterRequest,
  type RegisteredOrder,
  type StockQuote,
} from "@/lib/api";
import { isError } from "@/lib/types";

// ---------------------------------------------------------------------------
// Types — must match the dict shape built in backend/agents/tool_executor.py
// ---------------------------------------------------------------------------

export type LogicCardDetail = { label: string; value: string };

export type LogicCardRegisterPayload =
  | {
      symbol: string;
      exchange?: string;
      transaction_type: "BUY" | "SELL";
      order_type: "MARKET" | "LIMIT" | "GTT" | "SL" | "OCO";
      quantity: number;
      price?: number | null;
      trigger_price?: number | null;
      product?: string;
    }
  | {
      basket: true;
      legs: Array<{
        symbol: string;
        exchange?: string;
        transaction_type: "BUY" | "SELL";
        order_type: string;
        quantity: number;
        price?: number | null;
        product?: string;
      }>;
    };

export type LogicCard = {
  type: string;
  action: string;
  symbol: string;
  details: LogicCardDetail[];
  explanation: string;
  disclaimer: string;
  requires_confirmation: boolean;
  register_payload?: LogicCardRegisterPayload;
};

const AMOUNT_LABELS = ["amount", "total", "estimated total", "estimated cost"];

const lower = (s: string): string => s.trim().toLowerCase();

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

// Split details into a hero (the headline amount) + the rest, which become
// the horizontal stat strip cells.
function pickHero(details: LogicCardDetail[]): {
  hero: LogicCardDetail | null;
  strip: LogicCardDetail[];
} {
  const hero =
    details.find((d) => AMOUNT_LABELS.includes(lower(d.label))) ?? null;
  const strip = details.filter((d) => d !== hero);
  return { hero, strip };
}

// Cap the strip cell count so it stays single-row and legible. Excess
// details fold below.
const STRIP_CELLS = 3;

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

type ConfirmState =
  | { kind: "idle" }
  | { kind: "submitting" }
  | { kind: "done"; registered: RegisteredOrder[] }
  | { kind: "error"; message: string };

export function LogicCardChip({
  card,
  conversationId,
}: {
  card: LogicCard;
  /** Chat session id — sent with the order so a paper fill attributes to
      the right forward-test idea (Paper → Ideas). */
  conversationId?: string;
}): React.ReactElement {
  const [state, setState] = useState<ConfirmState>({ kind: "idle" });

  const canConfirm: boolean =
    card.requires_confirmation &&
    card.register_payload !== undefined &&
    state.kind === "idle";

  const handleConfirm = async (): Promise<void> => {
    if (!card.register_payload) return;
    setState({ kind: "submitting" });
    const result = await registerOrder({
      ...(card.register_payload as unknown as OrderRegisterRequest),
      ...(conversationId ? { conversation_id: conversationId } : {}),
    });
    if (isError(result)) {
      setState({
        kind: "error",
        message: result.error.message ?? "Failed to register order",
      });
      return;
    }
    const data = result.data;
    const registered =
      "registered" in data
        ? data.registered
        : ([data] as RegisteredOrder[]);
    setState({ kind: "done", registered });
  };

  const isSell = card.action === "SELL";
  const isBuy = card.action === "BUY";

  const accentText = isSell
    ? "text-rose-700 dark:text-rose-400"
    : isBuy
      ? "text-emerald-700 dark:text-emerald-400"
      : "text-foreground";

  const accentDot = isSell
    ? "bg-rose-500"
    : isBuy
      ? "bg-emerald-500"
      : "bg-foreground/60";

  const orderTypeLabel = card.type.replace(/_/g, " ");
  const { hero, strip } = pickHero(card.details);
  const visibleStrip = strip.slice(0, STRIP_CELLS);
  const overflowStrip = strip.slice(STRIP_CELLS);

  return (
    <div
      className="mb-2 mt-1 w-full max-w-[388px] overflow-hidden rounded-3xl border border-border/50 bg-card shadow-[0_1px_2px_rgba(15,23,42,0.04),0_12px_28px_-16px_rgba(15,23,42,0.10)]"
      data-testid="logic-card-chip"
      role="region"
      aria-label={`${card.action} ${card.symbol}`}
    >
      {/* Hero — StockSnapshot-style header + sparkline */}
      <SnapshotHeader symbol={card.symbol} action={card.action} />
      <span className="sr-only">{orderTypeLabel}</span>

      {/* Action eyebrow under the snapshot */}
      <div className="px-5 pt-1 pb-3">
        <span
          className={cn(
            "inline-flex items-center gap-1.5 q-uppercase-label !text-[10px] tabular-nums",
            accentText,
          )}
        >
          <span className={cn("h-1.5 w-1.5 rounded-full", accentDot)} aria-hidden="true" />
          {card.action}
        </span>
      </div>

      {/* Hero amount row — matches StockSnapshotCard StatCell typography */}
      {hero && (
        <div className="flex items-baseline justify-between px-5 pb-4">
          <span className="text-[9.5px] font-medium uppercase tracking-wider text-muted-foreground">
            {hero.label}
          </span>
          <span className="text-[12px] font-medium tabular-nums text-foreground">
            {hero.value}
          </span>
        </div>
      )}

      {/* Stat grid — hairline divider + StatCell typography to match StockSnapshotCard */}
      {visibleStrip.length > 0 && (
        <dl
          className={cn(
            "grid border-t border-border/60",
            visibleStrip.length === 1 && "grid-cols-1",
            visibleStrip.length === 2 && "grid-cols-2",
            visibleStrip.length >= 3 && "grid-cols-3",
          )}
          data-testid="logic-card-details"
        >
          {visibleStrip.map((d, i) => (
            <div
              key={i}
              className={cn(
                "flex flex-col gap-0.5 px-3 py-2.5 min-w-0",
                i !== visibleStrip.length - 1 && "border-r border-border/60",
              )}
            >
              <dt className="text-[9.5px] font-medium uppercase tracking-wider text-muted-foreground truncate">
                {d.label}
              </dt>
              <dd className="text-[12px] font-medium tabular-nums text-foreground truncate">
                {d.value}
              </dd>
            </div>
          ))}
        </dl>
      )}

      {/* Overflow rows — stacked below, same StatCell type scale. */}
      {overflowStrip.length > 0 && (
        <dl
          className="border-t border-border/60 px-5 py-2.5 space-y-1.5"
          data-testid="logic-card-details-overflow"
        >
          {overflowStrip.map((d, i) => (
            <div key={i} className="flex items-baseline justify-between gap-4">
              <dt className="text-[9.5px] font-medium uppercase tracking-wider text-muted-foreground">
                {d.label}
              </dt>
              <dd className="text-[12px] font-medium text-foreground tabular-nums text-right">
                {d.value}
              </dd>
            </div>
          ))}
        </dl>
      )}

      {/* Explanation */}
      {card.explanation && (
        <p className="border-t border-border/40 px-5 py-3 text-[12px] leading-relaxed text-muted-foreground">
          {card.explanation}
        </p>
      )}

      {/* CTA */}
      <div className="border-t border-border/40 px-4 py-3">
        {state.kind === "done" ? (
          <div
            className="flex h-8 w-full items-center justify-center gap-1.5 text-[12px] font-medium tracking-tight text-foreground"
            data-testid="logic-card-placed"
          >
            <AnimatedCheck />
            Placed
          </div>
        ) : (
          <button
            type="button"
            className={cn(
              "flex h-8 w-full items-center justify-center gap-2 rounded-full text-[12px] font-medium tracking-tight transition-colors",
              "bg-primary text-primary-foreground hover:bg-primary/90",
              "disabled:cursor-not-allowed disabled:opacity-60",
            )}
            onClick={() => void handleConfirm()}
            disabled={!canConfirm || state.kind === "submitting"}
            data-testid="logic-card-confirm-btn"
          >
            {state.kind === "submitting" ? (
              <>
                <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
                Registering…
              </>
            ) : (
              <>
                <Check
                  className="h-4 w-4"
                  aria-hidden="true"
                  strokeWidth={2.5}
                />
                Confirm &amp; register
              </>
            )}
          </button>
        )}
        {state.kind === "error" && (
          <p
            role="alert"
            data-testid="logic-card-error"
            className="mt-2 rounded-md bg-destructive/10 px-2.5 py-1.5 text-[11.5px] text-destructive"
          >
            {state.message}
          </p>
        )}
        {state.kind === "done" && (
          <span className="sr-only" data-testid="logic-card-confirmed">
            <ConfirmedSummary registered={state.registered} />
          </span>
        )}
      </div>

      {/* DISCLAIMER — matches WorkflowDraftCard: amber-tinted hairline footer
          to signal "advisory", consistent across all chat surfaces. */}
      {card.disclaimer && (
        <div className="flex items-center gap-1.5 border-t border-border/40 bg-amber-50/40 px-6 py-2.5 dark:bg-amber-500/[0.04]">
          <ShieldAlert
            className="h-3 w-3 shrink-0 text-amber-600/80 dark:text-amber-400/80"
            aria-hidden="true"
          />
          <p className="text-[11px] leading-snug text-amber-700/90 dark:text-amber-300/90">
            {card.disclaimer}
          </p>
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// SymbolSparkline — 1M area-fill chart for the order's symbol. Mirrors the
// SparkAreaChart from StockSnapshotCard (same /api/markets/sparkline source,
// same period-direction colour rule).
// ---------------------------------------------------------------------------

type SparkState =
  | { kind: "loading" }
  | { kind: "ok"; points: { t: string; v: number }[] }
  | { kind: "hidden" };

// ---------------------------------------------------------------------------
// SnapshotHeader — exact StockSnapshotCard top section: eyebrow exchange ·
// sector chip, large company name, ticker mono label, right-aligned LTP
// with change %, timestamp, and an embedded 1Y sparkline below.
// ---------------------------------------------------------------------------

type QuoteState =
  | { kind: "loading" }
  | { kind: "ok"; quote: StockQuote }
  | { kind: "hidden" };

function SnapshotHeader({
  symbol,
  action,
}: {
  symbol: string;
  action: string;
}): React.ReactElement {
  const [quoteState, setQuoteState] = useState<QuoteState>({ kind: "loading" });

  useEffect(() => {
    let cancelled = false;
    setQuoteState({ kind: "loading" });
    getStockQuote(symbol, "NSE")
      .then((result) => {
        if (cancelled) return;
        if (isError(result)) {
          setQuoteState({ kind: "hidden" });
        } else {
          setQuoteState({ kind: "ok", quote: result.data });
        }
      })
      .catch(() => {
        if (!cancelled) setQuoteState({ kind: "hidden" });
      });
    return () => {
      cancelled = true;
    };
  }, [symbol]);

  // Prefer "today's tick" colour for the price change pill, but colour the
  // sparkline by the period direction (matches StockSnapshotCard).
  const quote = quoteState.kind === "ok" ? quoteState.quote : null;
  const positive = quote ? quote.change >= 0 : action !== "SELL";
  const timeStr = new Date().toLocaleTimeString("en-IN", {
    hour: "2-digit",
    minute: "2-digit",
    timeZone: "Asia/Kolkata",
    hour12: false,
  });

  return (
    <div data-testid="logic-card-snapshot">
      <div className="flex items-start justify-between gap-4 px-5 pt-4 pb-3">
        <div className="min-w-0">
          <div className="flex items-center gap-1.5">
            <span className="text-[10px] font-medium tracking-[0.08em] uppercase text-muted-foreground/80">
              {quote ? `${quote.exchange} · ${quote.sector ?? "Equity"}` : "NSE"}
            </span>
          </div>
          <h3 className="mt-1.5 truncate text-[17px] leading-tight font-semibold tracking-tight text-foreground">
            {quote?.name || symbol}
          </h3>
          {quote && quote.name && quote.name !== symbol && (
            <p className="mt-0.5 font-mono text-[10.5px] uppercase tracking-wider text-muted-foreground">
              {symbol}
            </p>
          )}
        </div>

        {quote && (
          <div className="text-right shrink-0">
            <p className="text-[20px] leading-none font-semibold tabular-nums text-foreground tracking-tight">
              {fmtINR(quote.ltp)}
            </p>
            <div className="mt-1.5 flex items-center justify-end gap-1">
              {positive ? (
                <TrendingUp
                  className="h-3.5 w-3.5 text-emerald-600 dark:text-emerald-400"
                  aria-hidden={true}
                />
              ) : (
                <TrendingDown
                  className="h-3.5 w-3.5 text-rose-600 dark:text-rose-400"
                  aria-hidden={true}
                />
              )}
              <span
                className={cn(
                  "text-[11.5px] font-medium tabular-nums",
                  positive
                    ? "text-emerald-600 dark:text-emerald-400"
                    : "text-rose-600 dark:text-rose-400",
                )}
              >
                {positive ? "+" : ""}
                {fmtINR(quote.change)} ({positive ? "+" : ""}
                {fmtNum(quote.change_pct)}%)
              </span>
            </div>
            <p className="mt-0.5 text-[10px] text-muted-foreground/80 tabular-nums">
              {timeStr} IST
            </p>
          </div>
        )}
      </div>

      {/* Sparkline */}
      <div className="px-5 pb-1">
        <SymbolSparkline symbol={symbol} className="h-[80px] w-full" />
      </div>
    </div>
  );
}

function SymbolSparkline({
  symbol,
  className,
}: {
  symbol: string;
  className?: string;
}): React.ReactElement {
  const [spark, setSpark] = useState<SparkState>({ kind: "loading" });
  // Scrub-tooltip state: index of the point under the cursor, or null when
  // not hovering. Declared before the early returns to keep hook order stable.
  const containerRef = useRef<HTMLDivElement>(null);
  const [hoverIdx, setHoverIdx] = useState<number | null>(null);

  useEffect(() => {
    let cancelled = false;
    setSpark({ kind: "loading" });
    getSparkline(symbol, "1Y")
      .then((result) => {
        if (cancelled) return;
        if (isError(result)) {
          setSpark({ kind: "hidden" });
        } else if (!Array.isArray(result.data.points)) {
          // Defensive: a malformed/unexpected payload (e.g. a proxy error
          // body) must hide the sparkline, not crash the whole chat tree
          // on `spark.points.length` below.
          setSpark({ kind: "hidden" });
        } else {
          setSpark({ kind: "ok", points: result.data.points });
        }
      })
      .catch(() => {
        if (!cancelled) setSpark({ kind: "hidden" });
      });
    return () => {
      cancelled = true;
    };
  }, [symbol]);

  if (spark.kind === "hidden" || (spark.kind === "ok" && spark.points.length === 0)) {
    return <></>;
  }

  if (spark.kind === "loading") {
    return <div className={className} aria-hidden="true" />;
  }

  const points = spark.points;
  const first = points[0]?.v ?? 0;
  const last = points[points.length - 1]?.v ?? 0;
  const positive = last >= first;

  const values = points.map((p) => p.v);
  const min = Math.min(...values);
  const max = Math.max(...values);
  const range = max - min || 1;

  const W = 400;
  const H = 80;
  const PADDING_X = 2;
  const PADDING_Y = 6;

  const normalize = (v: number): number =>
    H - PADDING_Y - ((v - min) / range) * (H - PADDING_Y * 2);

  const xs = points.map((_, i) =>
    PADDING_X + (i / (points.length - 1)) * (W - PADDING_X * 2),
  );
  const ys = values.map(normalize);

  const linePoints = xs.map((x, i) => `${x},${ys[i]}`).join(" ");
  const areaPoints = [
    `${xs[0]},${H}`,
    ...xs.map((x, i) => `${x},${ys[i]}`),
    `${xs[xs.length - 1]},${H}`,
  ].join(" ");

  const color = positive ? "#10b981" : "#ef4444";
  const gradId = `logic-spark-${positive ? "up" : "dn"}-${symbol.replace(/[^a-z0-9]/gi, "")}`;

  // ── Scrub interaction ──────────────────────────────────────────────────
  // Map the cursor's horizontal position to the nearest data point and show
  // a Groww-style floating tooltip (date sub-row + bold price), plus a
  // vertical crosshair and a dot pinned to the line. Positions are expressed
  // as percentages of the viewBox so they track the line under the SVG's
  // non-uniform (preserveAspectRatio="none") scaling.
  const handleMove = (e: React.PointerEvent<HTMLDivElement>): void => {
    const el = containerRef.current;
    if (!el || points.length < 2) return;
    const rect = el.getBoundingClientRect();
    if (rect.width === 0) return;
    const frac = (e.clientX - rect.left) / rect.width;
    const idx = Math.round(frac * (points.length - 1));
    setHoverIdx(Math.max(0, Math.min(points.length - 1, idx)));
  };

  const hx = hoverIdx !== null ? (xs[hoverIdx]! / W) * 100 : 0;
  const hy = hoverIdx !== null ? (ys[hoverIdx]! / H) * 100 : 0;
  const hPoint = hoverIdx !== null ? points[hoverIdx]! : null;
  const hFrac = hoverIdx !== null ? hoverIdx / (points.length - 1) : 0;
  const active = hPoint !== null;
  // Keep the tooltip inside the chart's horizontal bounds: left-align near
  // the start, right-align near the end, centre otherwise.
  const tooltipAlign =
    hFrac < 0.18
      ? "translateX(0)"
      : hFrac > 0.82
        ? "translateX(-100%)"
        : "translateX(-50%)";
  // Flip the tooltip below the point when the line sits high in the chart, so
  // it never escapes the top edge into the header above.
  const tooltipVert =
    hy < 50 ? "translateY(8px)" : "translateY(calc(-100% - 8px))";

  return (
    <div
      ref={containerRef}
      className={cn("relative cursor-crosshair touch-none", className)}
      data-testid="logic-card-sparkline"
      onPointerMove={handleMove}
      onPointerLeave={() => setHoverIdx(null)}
    >
      <svg
        viewBox={`0 0 ${W} ${H}`}
        className="h-full w-full"
        aria-hidden={true}
        preserveAspectRatio="none"
      >
        <defs>
          <linearGradient id={gradId} x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor={color} stopOpacity={0.18} />
            <stop offset="100%" stopColor={color} stopOpacity={0} />
          </linearGradient>
        </defs>
        <polygon points={areaPoints} fill={`url(#${gradId})`} />
        <polyline
          points={linePoints}
          fill="none"
          stroke={color}
          strokeWidth={1.5}
          strokeLinejoin="round"
          strokeLinecap="round"
        />
      </svg>

      {active && hPoint && (
        <>
          {/* Vertical crosshair */}
          <span
            aria-hidden="true"
            className="pointer-events-none absolute top-0 bottom-0 w-px bg-foreground/15"
            style={{ left: `${hx}%` }}
          />
          {/* Dot pinned to the line */}
          <span
            aria-hidden="true"
            className="pointer-events-none absolute h-2 w-2 -translate-x-1/2 -translate-y-1/2 rounded-full border-2 border-card"
            style={{ left: `${hx}%`, top: `${hy}%`, backgroundColor: color }}
          />
          {/* Floating tooltip — date + price */}
          <div
            role="status"
            aria-live="polite"
            data-testid="logic-card-sparkline-tooltip"
            className="pointer-events-none absolute z-10 whitespace-nowrap rounded-lg border border-border/60 bg-popover px-2 py-1 shadow-md"
            style={{ left: `${hx}%`, top: `${hy}%`, transform: `${tooltipAlign} ${tooltipVert}` }}
          >
            <div className="text-[9px] leading-tight text-muted-foreground tabular-nums">
              {fmtSparkDate(hPoint.t)}
            </div>
            <div className="text-[11px] font-semibold leading-tight tabular-nums text-foreground">
              {fmtINR(hPoint.v)}
            </div>
          </div>
        </>
      )}
    </div>
  );
}

// Format a sparkline point's timestamp for the scrub tooltip. Points carry an
// ISO date string (daily granularity for the 1Y series); fall back to the raw
// value if it doesn't parse.
function fmtSparkDate(t: string): string {
  try {
    return format(parseISO(t), "d MMM yyyy");
  } catch {
    return t;
  }
}

function ConfirmedSummary({
  registered,
}: {
  registered: RegisteredOrder[];
}): React.ReactElement {
  if (registered.length === 1) {
    const row = registered[0];
    if (!row) return <></>;
    return (
      <>
        Registered #{row.id} · {row.symbol} {row.transaction_type}{" "}
        {row.quantity} at {row.placed_at}
      </>
    );
  }
  return (
    <>
      Registered {registered.length} legs:
      {registered.map((r, i) => (
        <span key={r.id}>
          {i === 0 ? " " : ", "}#{r.id} {r.symbol} {r.transaction_type} {r.quantity}
        </span>
      ))}
    </>
  );
}

/**
 * AnimatedCheck — playful one-shot tick draw that mounts when the order
 * is placed. The path's stroke-dasharray + stroke-dashoffset animates
 * from fully-hidden to fully-drawn over 400ms; CSS-only, no JS timers.
 */
function AnimatedCheck(): React.ReactElement {
  return (
    <svg
      width="16"
      height="16"
      viewBox="0 0 16 16"
      fill="none"
      aria-hidden="true"
      className="shrink-0"
    >
      <path
        d="M3.25 8.5L6.5 11.5L12.75 4.75"
        stroke="currentColor"
        strokeWidth="2.25"
        strokeLinecap="round"
        strokeLinejoin="round"
        style={{
          strokeDasharray: 16,
          strokeDashoffset: 16,
          animation: "logicCardTick 380ms cubic-bezier(0.65, 0, 0.35, 1) forwards",
        }}
      />
      <style>{`
        @keyframes logicCardTick {
          to { stroke-dashoffset: 0; }
        }
      `}</style>
    </svg>
  );
}
