/**
 * IndicatorBacktestCard — concise inline widget for indicator-backtest
 * results, with a "View" pill that opens the full result in a modal.
 *
 * Surfaces:
 *   - IndicatorBacktestCard   — narrow widget (tag chip · title · date row ·
 *     hero sparkline · CAGR / Max DD / Trades / Hit rate · total return ·
 *     View pill). Inspired by the Composer / "Hedgefundie's Excellent
 *     Adventure" tile reference.
 *   - IndicatorBacktestDetail — full breakdown: header with vs-B&H delta,
 *     hero sparkline with buy/sell dot strip, Net P&L row, two stat grid
 *     rows, price + indicator thumbnails, explanation, disclaimer. This
 *     is what the modal mounts.
 *
 * Rendered when chat returns raw_data._render_hint === "indicator_backtest_chart".
 * Type scale matches LogicCardChip / StockSnapshotCard exactly.
 */
"use client";

import * as React from "react";
import { ArrowDown, ArrowUp, ArrowUpRight, Calendar, ShieldAlert, X } from "lucide-react";
import { cn } from "@/lib/utils";
import { Dialog, DialogClose, DialogContent, DialogTitle, DialogTrigger } from "@/components/ui/dialog";
import { getStockQuote, type StockQuote } from "@/lib/api";
import { isError } from "@/lib/types";

// Detail surface — kept in sync with the concise card's design language:
// rounded-3xl, generous padding, soft shadow, no dense dividers, large
// hero sparkline. The modal mounts this directly via DialogContent.

export type IndicatorBacktestPayload = {
  symbol: string;
  // Legacy backtester emits "rsi" | "sma" | "ema". The DSL-tree
  // backtester emits "compound" (the tree doesn't map to one
  // indicator key); the card falls back to ``tree_summary`` for the
  // title in that case.
  indicator: string;
  indicator_period: number;
  operator: string;
  threshold: number;
  period_label: string;
  price_curve: Array<{ t: string; v: number }>;
  equity_curve: Array<{ t: string; v: number }>;
  indicator_curve: Array<{ t: string; v: number }>;
  signals: Array<{ t: string; side: "buy" | "sell"; price: number; indicator_value: number | null }>;
  metrics: {
    total_return_pct: number;
    cagr_pct: number;
    max_drawdown_pct: number;
    hit_rate_pct: number;
    n_trades: number;
    n_wins: number;
    starting_capital: number;
    ending_value: number;
  };
  bench_buy_hold_return_pct: number | null;
  // ── DSL-tree extras (present only on responses from the
  // backtest_dsl_tree chat tool). When ``tree_summary`` is set the
  // card uses it as the condition label instead of the indicator/
  // operator/threshold tuple, which is meaningless for compound trees.
  tree_summary?: string | null;
  trades?: Array<{
    trade_id: number;
    entry_date: string;
    entry_price: number;
    exit_date: string | null;
    exit_price: number | null;
    quantity: number;
    net_pnl: number;
    return_pct: number;
    exit_reason: string;
  }>;
  diagnostics?: {
    bars_evaluated: number;
    fire_bars: number;
    unknown_value_bars: number;
  };
};

type Props = { payload: IndicatorBacktestPayload };

// ---------------------------------------------------------------------------
// Formatters
// ---------------------------------------------------------------------------

const fmtINR = (n: number): string =>
  new Intl.NumberFormat("en-IN", {
    style: "currency",
    currency: "INR",
    maximumFractionDigits: 0,
  }).format(n);

const fmtPct = (n: number): string => `${n >= 0 ? "+" : ""}${n.toFixed(2)}%`;

// "RELIANCE" → "Reliance".  Title-cases each whitespace-separated token so
// multi-word tickers like "HDFC BANK" become "Hdfc Bank" rather than
// "Hdfc bank".  Used by the concise widget to match the LogicCardChip title
// rhythm.
const toCapitalized = (s: string): string =>
  s
    .toLowerCase()
    .split(/\s+/)
    .map((w) => (w.length > 0 ? w[0]!.toUpperCase() + w.slice(1) : w))
    .join(" ");

const opLabel = (op: string): string => {
  switch (op) {
    case "<":
      return "drops below";
    case ">":
      return "rises above";
    case "crosses_below":
      return "crosses below";
    case "crosses_above":
      return "crosses above";
    default:
      return op;
  }
};

const conditionFor = (payload: IndicatorBacktestPayload): string => {
  // DSL-tree backtests carry the readback in ``tree_summary``.
  // It's the natural-language form of the whole tree (multi-condition,
  // multi-symbol, aggregator-aware) so we prefer it over the
  // single-indicator template.
  if (payload.tree_summary && payload.tree_summary.length > 0) {
    return payload.tree_summary;
  }
  return payload.indicator === "rsi"
    ? `RSI(${payload.indicator_period}) ${opLabel(payload.operator)} ${payload.threshold}`
    : `Price ${opLabel(payload.operator)} ${payload.indicator.toUpperCase()}(${payload.indicator_period})`;
};

const titleFor = (payload: IndicatorBacktestPayload): string =>
  `${payload.symbol} · ${conditionFor(payload)}`;

// ---------------------------------------------------------------------------
// IndicatorBacktestCard — concise widget. Matches the composer tile
// reference: tag chip → title → date row → sparkline → 2×2 stat block →
// accent total return → pill View button.
// ---------------------------------------------------------------------------

type QuoteState =
  | { kind: "loading" }
  | { kind: "ok"; quote: StockQuote }
  | { kind: "hidden" };

export function IndicatorBacktestCard({ payload }: Props): React.ReactElement {
  const { symbol, metrics, period_label } = payload;

  const positive = metrics.total_return_pct >= 0;
  const conditionLabel = conditionFor(payload);

  // Pull the company name the same way LogicCardChip does — best effort,
  // falls back to the title-cased ticker if the quote endpoint is offline
  // or returns no name (also keeps the unit test that renders without a
  // network deterministic).
  const [quoteState, setQuoteState] = React.useState<QuoteState>({ kind: "loading" });
  React.useEffect(() => {
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

  const companyName =
    quoteState.kind === "ok" && quoteState.quote.name
      ? quoteState.quote.name
      : toCapitalized(symbol);

  return (
    <div
      className="my-2 w-full max-w-[440px] overflow-hidden rounded-3xl border border-border/50 bg-card px-6 pt-6 pb-6 shadow-[0_1px_2px_rgba(15,23,42,0.04),0_12px_28px_-16px_rgba(15,23,42,0.10)]"
      data-testid="indicator-backtest-card"
      role="region"
      aria-label={`Indicator backtest ${symbol}`}
    >
      {/* Tag chip */}
      <div className="flex">
        <span className="inline-flex items-center rounded-md bg-sky-100 px-2.5 py-0.5 text-[11px] font-medium tracking-tight text-sky-700 dark:bg-sky-500/15 dark:text-sky-300">
          Indicator Backtest
        </span>
      </div>

      {/* Title — matches the LogicCardChip SnapshotHeader rhythm: full
          company name, then mono ticker line, then plain-sans condition. */}
      <h3 className="mt-3 truncate text-[20px] leading-[1.2] font-semibold tracking-tight text-foreground">
        {companyName}
      </h3>
      {companyName !== symbol && (
        <p className="mt-0.5 font-mono text-[10.5px] uppercase tracking-wider text-muted-foreground">
          {symbol}
        </p>
      )}
      <p
        className={cn(
          "mt-1.5 text-[12px] tracking-tight text-muted-foreground",
          // DSL trees can be long ("RSI(14) of TCS < 30 AND price of
          // NIFTY > 22000 …") — let them wrap. Single-indicator
          // condition strings stay on one line as before.
          payload.tree_summary ? "leading-snug" : "truncate",
        )}
        title={conditionLabel}
      >
        {conditionLabel}
      </p>

      {/* Date / period row — calendar + period_label */}
      <div className="mt-3 flex items-center gap-1.5 text-[12px] text-muted-foreground">
        <Calendar
          className="h-3.5 w-3.5 shrink-0 text-muted-foreground/70"
          aria-hidden="true"
        />
        <span className="tabular-nums">{period_label}</span>
      </div>

      {/* Hero sparkline — full-bleed, no axes, no signal overlay. */}
      <div className="mt-4 h-[110px]">
        <SparkAreaChart points={payload.equity_curve} positive={positive} />
      </div>

      {/* 2×2 stat block — CAGR · Max DD on top, Trades · Hit rate below. */}
      <div className="mt-5 grid grid-cols-2 gap-y-4 gap-x-6">
        <ConciseStat label="CAGR" value={fmtPct(metrics.cagr_pct)} />
        <ConciseStat
          label="Max DD"
          value={`${metrics.max_drawdown_pct.toFixed(1)}%`}
        />
        <ConciseStat label="Trades" value={String(metrics.n_trades)} />
        <ConciseStat
          label="Hit rate"
          value={`${metrics.hit_rate_pct.toFixed(0)}%`}
        />
      </div>

      {/* Accent total return */}
      <div className="mt-5">
        <p className="text-[11px] uppercase tracking-wider text-muted-foreground">
          Strategy total return
        </p>
        <p
          className={cn(
            "mt-1 text-[26px] leading-none font-semibold tabular-nums tracking-tight",
            positive
              ? "text-emerald-600 dark:text-emerald-400"
              : "text-rose-600 dark:text-rose-400",
          )}
        >
          {fmtPct(metrics.total_return_pct)}
        </p>
      </div>

      {/* View pill — opens the full detail in a modal. */}
      <Dialog>
        <DialogTrigger asChild>
          <button
            type="button"
            className="mt-6 inline-flex h-10 w-full items-center justify-center gap-1.5 rounded-full bg-primary text-[13px] font-medium tracking-tight text-primary-foreground transition-colors hover:bg-primary/90"
            data-testid="indicator-backtest-view-btn"
          >
            <span className="leading-none">View</span>
            <ArrowUpRight
              className="h-4 w-4 shrink-0"
              strokeWidth={2}
              aria-hidden="true"
            />
          </button>
        </DialogTrigger>
        <DialogContent
          aria-describedby={undefined}
          className="max-w-[1080px] gap-0 overflow-hidden rounded-2xl border border-border/60 bg-card p-0 shadow-[0_1px_2px_rgba(15,23,42,0.04),0_24px_64px_-20px_rgba(15,23,42,0.22)] [&>button.opacity-70]:hidden"
        >
          <DialogTitle className="sr-only">{titleFor(payload)}</DialogTitle>
          {/* Lucide X close — replaces shadcn's default. Sits in the
              top-right corner with a soft pill so it stays legible
              against the hero. The shadcn default close is hidden via
              the [&>button.opacity-70]:hidden selector on the parent
              (targets only the auto-injected close, not this one). */}
          <DialogClose
            className="absolute right-4 top-4 z-20 inline-flex h-9 w-9 items-center justify-center rounded-full border border-border/50 bg-background/90 text-muted-foreground shadow-sm backdrop-blur transition-colors hover:bg-muted hover:text-foreground focus:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
            aria-label="Close"
          >
            <X className="h-4 w-4" strokeWidth={2} aria-hidden="true" />
          </DialogClose>
          <IndicatorBacktestDetail payload={payload} />
        </DialogContent>
      </Dialog>
    </div>
  );
}

// ---------------------------------------------------------------------------
// IndicatorBacktestDetail — full result surface, mounted inside the modal.
// Reads as an expansion of IndicatorBacktestCard: same tag chip, company
// name + mono ticker + condition + period header, roomy padding and a
// taller hero sparkline. Stat blocks use RoomyStat instead of divided
// cells so the surface stays calm at modal scale.
// ---------------------------------------------------------------------------

export function IndicatorBacktestDetail({ payload }: Props): React.ReactElement {
  const {
    symbol,
    indicator,
    indicator_period,
    threshold,
    period_label,
    price_curve,
    equity_curve,
    indicator_curve,
    signals,
    metrics,
    bench_buy_hold_return_pct,
  } = payload;

  const positive = metrics.total_return_pct >= 0;
  const beatsBench = metrics.total_return_pct > bench_buy_hold_return_pct;
  const benchDelta = metrics.total_return_pct - bench_buy_hold_return_pct;
  const benchEqual = Math.abs(benchDelta) < 0.005;
  const netPnl = metrics.ending_value - metrics.starting_capital;
  const conditionLabel = conditionFor(payload);

  const buys = signals.filter((s) => s.side === "buy");
  const sells = signals.filter((s) => s.side === "sell");

  // Same company-name resolution as the concise card so the modal header
  // reads "Reliance" rather than "RELIANCE" when the quote endpoint is
  // available. Falls back to title-cased ticker.
  const [quoteState, setQuoteState] = React.useState<QuoteState>({ kind: "loading" });
  React.useEffect(() => {
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

  const companyName =
    quoteState.kind === "ok" && quoteState.quote.name
      ? quoteState.quote.name
      : toCapitalized(symbol);

  const signalPositions = React.useMemo(() => {
    if (equity_curve.length < 2) return [] as Array<{ x: number; side: "buy" | "sell" }>;
    const t0 = new Date(equity_curve[0]!.t).getTime();
    const tN = new Date(equity_curve[equity_curve.length - 1]!.t).getTime();
    const span = tN - t0 || 1;
    return signals.map((s) => ({
      x: Math.min(1, Math.max(0, (new Date(s.t).getTime() - t0) / span)),
      side: s.side,
    }));
  }, [equity_curve, signals]);

  const benchSign = benchEqual ? "±" : benchDelta > 0 ? "+" : "";
  const benchTone = benchEqual
    ? "text-muted-foreground"
    : beatsBench
      ? "text-emerald-700 dark:text-emerald-300"
      : "text-rose-700 dark:text-rose-300";
  const benchPillBg = benchEqual
    ? "bg-muted"
    : beatsBench
      ? "bg-emerald-50 dark:bg-emerald-500/10"
      : "bg-rose-50 dark:bg-rose-500/10";

  return (
    <div
      className="relative w-full bg-card"
      data-testid="indicator-backtest-detail"
      role="region"
      aria-label={`Indicator backtest ${symbol}`}
    >
      {/* HERO — generous, landscape. Identity left, return right. */}
      <div className="flex items-start justify-between gap-12 px-10 pt-9 pb-7 pr-20">
        <div className="min-w-0 pr-4">
          {/* Tag + period chips — give the modal the same context as the
              concise card it expanded from. */}
          <div className="flex flex-wrap items-center gap-2">
            <span className="inline-flex items-center rounded-md bg-sky-100 px-2.5 py-0.5 text-[11px] font-medium tracking-tight text-sky-700 dark:bg-sky-500/15 dark:text-sky-300">
              Indicator Backtest
            </span>
            <span className="inline-flex items-center gap-1 rounded-md bg-muted px-2 py-0.5 text-[11px] font-medium tabular-nums text-muted-foreground">
              <Calendar className="h-3 w-3 shrink-0" aria-hidden="true" />
              {period_label}
            </span>
          </div>
          <div className="mt-3 flex items-baseline gap-3">
            <h3 className="truncate text-[26px] leading-[1.1] font-semibold tracking-tight text-foreground">
              {companyName}
            </h3>
            {companyName !== symbol && (
              <span className="font-mono text-[11px] uppercase tracking-wider text-muted-foreground/70">
                {symbol}
              </span>
            )}
          </div>
          <p className="mt-2 text-[13px] tracking-tight text-foreground/80">
            {conditionLabel}
          </p>
        </div>

        <div className="shrink-0 text-right">
          <p className="text-[10.5px] uppercase tracking-[0.08em] text-muted-foreground">
            Strategy return
          </p>
          <p
            className={cn(
              "mt-1 text-[36px] leading-none font-semibold tabular-nums tracking-tight",
              positive
                ? "text-emerald-600 dark:text-emerald-400"
                : "text-rose-600 dark:text-rose-400",
            )}
          >
            {fmtPct(metrics.total_return_pct)}
          </p>
          <span
            className={cn(
              "mt-2.5 inline-flex items-center gap-1 rounded-full px-2.5 py-1 text-[11.5px] font-medium tabular-nums",
              benchPillBg,
              benchTone,
            )}
          >
            {!benchEqual &&
              (beatsBench ? (
                <ArrowUp
                  className="h-3 w-3 shrink-0"
                  strokeWidth={2.5}
                  aria-hidden="true"
                />
              ) : (
                <ArrowDown
                  className="h-3 w-3 shrink-0"
                  strokeWidth={2.5}
                  aria-hidden="true"
                />
              ))}
            {Math.abs(benchDelta).toFixed(2)}%{" "}
            <span className="ml-0.5 font-normal text-muted-foreground">vs buy &amp; hold</span>
          </span>
        </div>
      </div>

      {/* Two-column body: equity curve left, 4×2 stat grid right. */}
      <div className="grid grid-cols-[1.35fr_1fr] gap-x-12 border-t border-border/50 px-10 py-8">
        {/* Left: equity curve */}
        <div className="min-w-0">
          <div className="relative h-[220px]" data-testid="equity-chart">
            <SparkAreaChart points={equity_curve} positive={positive} mode="step" />
            <SignalDotStrip signals={signalPositions} />
          </div>
          <div className="mt-4 flex items-center justify-between gap-2 text-[11.5px] text-muted-foreground">
            <span className="inline-flex items-center gap-1.5 tabular-nums">
              <span className="h-1.5 w-1.5 rounded-full bg-emerald-500" aria-hidden="true" />
              {buys.length} buy{buys.length === 1 ? "" : "s"} <span className="text-muted-foreground/50">(top)</span>
              <span className="mx-1.5 text-muted-foreground/40">·</span>
              <span className="h-1.5 w-1.5 rounded-full bg-rose-500" aria-hidden="true" />
              {sells.length} sell{sells.length === 1 ? "" : "s"} <span className="text-muted-foreground/50">(bottom)</span>
            </span>
            <span className="tabular-nums">
              {fmtINR(metrics.starting_capital)}{" "}
              <span className="text-muted-foreground/50">→</span>{" "}
              <span className="text-foreground/90">{fmtINR(metrics.ending_value)}</span>
            </span>
          </div>
        </div>

        {/* Right: 2×4 stat grid — wide cells so currency strings never
            truncate. Pairs related stats horizontally (CAGR/Max DD,
            Trades/Hit rate, Start/End value, Wins/Buy-&-hold). */}
        <div className="grid grid-cols-2 gap-x-8 gap-y-5 self-center">
          <RoomyStat label="CAGR" value={fmtPct(metrics.cagr_pct)} />
          <RoomyStat
            label="Max DD"
            value={`${metrics.max_drawdown_pct.toFixed(1)}%`}
          />
          <RoomyStat label="Trades" value={String(metrics.n_trades)} />
          <RoomyStat
            label="Hit rate"
            value={`${metrics.hit_rate_pct.toFixed(0)}%`}
          />
          <RoomyStat label="Start" value={fmtINR(metrics.starting_capital)} />
          <RoomyStat label="End value" value={fmtINR(metrics.ending_value)} />
          <RoomyStat label="Wins" value={`${metrics.n_wins}/${metrics.n_trades}`} />
          <RoomyStat
            label={`${symbol} buy & hold`}
            value={fmtPct(bench_buy_hold_return_pct)}
          />
        </div>
      </div>

      {/* Net P&L | Price thumb | Indicator thumb — equal-weight 3-col so
          the P&L number gets its own column instead of floating in dead
          space, and the two thumbs share alignment with it. */}
      <div className="grid grid-cols-3 items-center gap-x-8 border-t border-border/50 px-10 py-6">
        <div className="flex flex-col gap-1">
          <span className="text-[11px] uppercase tracking-[0.08em] text-muted-foreground">
            Net P&amp;L
          </span>
          <span
            className={cn(
              "text-[20px] font-semibold tabular-nums tracking-tight",
              netPnl >= 0
                ? "text-emerald-600 dark:text-emerald-400"
                : "text-rose-600 dark:text-rose-400",
            )}
          >
            {netPnl >= 0 ? "+" : ""}
            {fmtINR(netPnl)}
          </span>
        </div>
        <InlineThumb
          label="Price"
          testId="price-chart"
          points={price_curve}
          color="var(--price-line)"
        />
        <InlineThumb
          label={`${indicator.toUpperCase()}(${indicator_period})`}
          testId="indicator-chart"
          points={indicator_curve}
          color="rgb(99 102 241)"
          referenceY={indicator === "rsi" ? threshold : undefined}
        />
      </div>

      {/* Insight + disclaimer. Footer reads the strategy in plain
          English instead of repeating numbers from the stat grid.
          Disclaimer drops one notch in weight so it stays a footnote. */}
      <div className="flex items-center justify-between gap-6 border-t border-border/50 px-10 py-4">
        <p className="text-[12.5px] leading-snug text-foreground/70">
          {benchEqual
            ? `Strategy matched buy-and-hold (${metrics.hit_rate_pct.toFixed(0)}% hit rate across ${metrics.n_trades} trade${metrics.n_trades === 1 ? "" : "s"}).`
            : beatsBench
              ? `Strategy beat buy-and-hold by ${benchDelta.toFixed(2)}% with a ${metrics.hit_rate_pct.toFixed(0)}% hit rate across ${metrics.n_trades} trade${metrics.n_trades === 1 ? "" : "s"}.`
              : `Despite a ${metrics.hit_rate_pct.toFixed(0)}% hit rate across ${metrics.n_trades} trade${metrics.n_trades === 1 ? "" : "s"}, strategy underperformed buy-and-hold by ${Math.abs(benchDelta).toFixed(2)}%.`}
        </p>
        <p className="inline-flex shrink-0 items-center gap-1.5 text-[10.5px] leading-snug text-muted-foreground/55">
          <ShieldAlert
            className="h-3 w-3 shrink-0 text-muted-foreground/40"
            aria-hidden="true"
          />
          Past performance does not guarantee future results.
        </p>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// SparkAreaChart — full-bleed area-fill primitive (same as
// StockSnapshotCard's SparkAreaChart, with viewBox H driven by the parent
// container).
// ---------------------------------------------------------------------------

// Catmull-Rom → cubic Bezier path generator.  Produces a smooth open curve
// through every (x,y) without overshooting too much; tension 0.5 keeps the
// curve close to the original points while sanding off staircase corners
// that the raw equity series tends to produce (long flat holds punctuated
// by trade-day jumps).
function smoothPath(xs: number[], ys: number[]): string {
  if (xs.length < 2) return "";
  if (xs.length === 2) return `M ${xs[0]},${ys[0]} L ${xs[1]},${ys[1]}`;

  const segs: string[] = [`M ${xs[0]},${ys[0]}`];
  for (let i = 0; i < xs.length - 1; i++) {
    const p0x = xs[i === 0 ? i : i - 1]!;
    const p0y = ys[i === 0 ? i : i - 1]!;
    const p1x = xs[i]!;
    const p1y = ys[i]!;
    const p2x = xs[i + 1]!;
    const p2y = ys[i + 1]!;
    const p3x = xs[i + 2 < xs.length ? i + 2 : i + 1]!;
    const p3y = ys[i + 2 < ys.length ? i + 2 : i + 1]!;

    const c1x = p1x + (p2x - p0x) / 6;
    const c1y = p1y + (p2y - p0y) / 6;
    const c2x = p2x - (p3x - p1x) / 6;
    const c2y = p2y - (p3y - p1y) / 6;

    segs.push(`C ${c1x},${c1y} ${c2x},${c2y} ${p2x},${p2y}`);
  }
  return segs.join(" ");
}

// Stepped path — used for the equity curve specifically. A backtest sits
// flat between trades and jumps on trade days; smoothing across those
// plateaus implies continuous gain that did not happen. Horizontal hold,
// vertical step at each new point.
function stepPath(xs: number[], ys: number[]): string {
  if (xs.length < 2) return "";
  const segs: string[] = [`M ${xs[0]},${ys[0]}`];
  for (let i = 1; i < xs.length; i++) {
    segs.push(`H ${xs[i]} V ${ys[i]}`);
  }
  return segs.join(" ");
}

function SparkAreaChart({
  points,
  positive,
  mode = "smooth",
}: {
  points: Array<{ t: string; v: number }>;
  positive: boolean;
  mode?: "smooth" | "step";
}): React.ReactElement {
  if (points.length < 2) return <></>;

  const values = points.map((p) => p.v);
  const min = Math.min(...values);
  const max = Math.max(...values);
  const range = max - min || 1;

  const W = 400;
  const H = 110;
  const PADDING_X = 2;
  const PADDING_Y = 10;

  const normalize = (v: number): number =>
    H - PADDING_Y - ((v - min) / range) * (H - PADDING_Y * 2);

  const xs = points.map((_, i) => PADDING_X + (i / (points.length - 1)) * (W - PADDING_X * 2));
  const ys = values.map(normalize);

  const linePath = mode === "step" ? stepPath(xs, ys) : smoothPath(xs, ys);
  const areaPath = `${linePath} L ${xs[xs.length - 1]},${H} L ${xs[0]},${H} Z`;

  const color = positive ? "#10b981" : "#f43f5e";
  const gradId = `ibk-eq-${positive ? "up" : "dn"}`;

  return (
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
      <path d={areaPath} fill={`url(#${gradId})`} />
      <path
        d={linePath}
        fill="none"
        stroke={color}
        strokeWidth={1.5}
        strokeLinejoin="round"
        strokeLinecap="round"
      />
    </svg>
  );
}

function SignalDotStrip({
  signals,
}: {
  signals: Array<{ x: number; side: "buy" | "sell" }>;
}): React.ReactElement | null {
  if (signals.length === 0) return null;
  // Buys ride a strip near the top of the chart, sells near the bottom.
  // Stacking opposite sides on opposite edges removes the overlap problem
  // where same-day buy/sell pairs collapsed into a single red dot.
  const buys = signals.filter((s) => s.side === "buy");
  const sells = signals.filter((s) => s.side === "sell");
  return (
    <>
      <div className="pointer-events-none absolute inset-x-0 top-1 h-1.5">
        {buys.map((s, i) => (
          <span
            key={`b-${i}`}
            className="absolute top-0 h-1.5 w-1.5 -translate-x-1/2 rounded-full bg-emerald-500 ring-1 ring-card"
            style={{ left: `${s.x * 100}%` }}
            aria-hidden="true"
          />
        ))}
      </div>
      <div className="pointer-events-none absolute inset-x-0 bottom-1 h-1.5">
        {sells.map((s, i) => (
          <span
            key={`s-${i}`}
            className="absolute top-0 h-1.5 w-1.5 -translate-x-1/2 rounded-full bg-rose-500 ring-1 ring-card"
            style={{ left: `${s.x * 100}%` }}
            aria-hidden="true"
          />
        ))}
      </div>
    </>
  );
}

// ---------------------------------------------------------------------------
// ConciseStat — bigger, no-divider stat used in the concise widget. Label
// caps + value at 16px to feel premium next to the small body type.
// ---------------------------------------------------------------------------

function ConciseStat({
  label,
  value,
}: {
  label: string;
  value: string;
}): React.ReactElement {
  return (
    <div className="flex flex-col gap-0.5">
      <span className="text-[10.5px] tracking-wide text-muted-foreground">
        {label}
      </span>
      <span className="text-[16px] font-semibold tabular-nums tracking-tight text-foreground">
        {value}
      </span>
    </div>
  );
}

// ---------------------------------------------------------------------------
// RoomyStat + ThumbTile — primitives used by the detail view. RoomyStat
// mirrors the concise card's ConciseStat at the same scale so the modal
// reads as an expansion of the card, not a separate surface.
// ---------------------------------------------------------------------------

function RoomyStat({
  label,
  value,
}: {
  label: string;
  value: string;
}): React.ReactElement {
  return (
    <div className="flex min-w-0 flex-col gap-1">
      <span className="text-[11px] tracking-tight text-muted-foreground">
        {label}
      </span>
      <span className="text-[17px] font-semibold tabular-nums tracking-tight text-foreground">
        {value}
      </span>
    </div>
  );
}

function InlineThumb({
  label,
  testId,
  points,
  color,
  referenceY,
}: {
  label: string;
  testId?: string;
  points: Array<{ t: string; v: number }>;
  color: string;
  referenceY?: number;
}): React.ReactElement {
  return (
    <div className="flex flex-col gap-1.5" data-testid={testId}>
      <span className="text-[11px] tracking-tight text-muted-foreground">
        {label}
      </span>
      <div className="h-10">
        <ThumbLine points={points} color={color} referenceY={referenceY} />
      </div>
    </div>
  );
}

function ThumbLine({
  points,
  color,
  referenceY,
}: {
  points: Array<{ t: string; v: number }>;
  color: string;
  referenceY?: number;
}): React.ReactElement {
  if (points.length < 2) return <></>;

  const values = points.map((p) => p.v);
  const min = referenceY != null ? Math.min(referenceY, ...values) : Math.min(...values);
  const max = referenceY != null ? Math.max(referenceY, ...values) : Math.max(...values);
  const range = max - min || 1;

  const W = 200;
  const H = 32;
  const PY = 3;

  const normalize = (v: number): number =>
    H - PY - ((v - min) / range) * (H - PY * 2);

  const xs = points.map((_, i) => (i / (points.length - 1)) * W);
  const ys = values.map(normalize);
  const linePath = smoothPath(xs, ys);

  const refY = referenceY != null ? normalize(referenceY) : null;

  return (
    <svg
      viewBox={`0 0 ${W} ${H}`}
      className="h-full w-full"
      aria-hidden={true}
      preserveAspectRatio="none"
    >
      {refY != null && (
        <line
          x1={0}
          x2={W}
          y1={refY}
          y2={refY}
          stroke="rgb(244 63 94)"
          strokeWidth={0.75}
          strokeDasharray="3 3"
          opacity={0.7}
        />
      )}
      <path
        d={linePath}
        fill="none"
        stroke={color}
        strokeWidth={1.25}
        strokeLinejoin="round"
        strokeLinecap="round"
      />
    </svg>
  );
}

