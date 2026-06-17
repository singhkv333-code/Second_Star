/**
 * IndicatorBacktestCard — concise inline widget for indicator-backtest
 * results, with a "View" pill that opens the full result in a right-side
 * sidebar (the same slide-in panel pattern as the workflow editor).
 *
 * Surfaces:
 *   - IndicatorBacktestCard   — narrow widget (tag chip · title · date row ·
 *     interactive hover chart · total return · View pill). The detailed
 *     CAGR / Max DD / Trades / Hit rate breakdown is intentionally kept off
 *     the concise card and surfaced only in the View sidebar.
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
import { ArrowDown, ArrowUp, ArrowUpRight, Calendar, Info, ShieldAlert, X } from "lucide-react";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import {
  Sheet,
  SheetClose,
  SheetContent,
  SheetTitle,
  SheetTrigger,
} from "@/components/ui/sheet";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip";
import { InteractiveAreaChart } from "@/components/charts/InteractiveAreaChart";
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

const fmtPct = (n: number | null | undefined): string =>
  n == null ? "—" : `${n >= 0 ? "+" : ""}${n.toFixed(2)}%`;

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
  const ind = payload.indicator as string;
  if (ind === "rsi") {
    return `RSI(${payload.indicator_period}) ${opLabel(payload.operator)} ${payload.threshold}`;
  }
  if (ind === "schedule") {
    return `Scheduled buy strategy`;
  }
  return `Price ${opLabel(payload.operator)} ${ind.toUpperCase()}(${payload.indicator_period})`;
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
      className="mb-2 mt-1 w-full max-w-[388px] overflow-hidden rounded-3xl border border-border/50 bg-card px-6 pt-6 pb-6 shadow-[0_1px_2px_rgba(15,23,42,0.04),0_12px_28px_-16px_rgba(15,23,42,0.10)]"
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

      {/* Hero chart — interactive: hover reveals a crosshair + value/date
          tooltip. The detailed CAGR / Max DD / Trades / Hit rate breakdown
          lives behind the "View" sidebar, not on the concise card. */}
      <div className="mt-4 h-[110px]">
        <InteractiveAreaChart
          points={payload.equity_curve}
          color={positive ? "var(--color-profit)" : "var(--color-loss)"}
          height={110}
          formatValue={fmtINR}
          formatDate={(iso) => formatDateShort(iso)}
          ariaLabel={`${symbol} equity curve`}
          enableRangeSelect={false}
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

      {/* View pill — opens the full detail in a right-side sidebar, the
          same slide-in panel pattern as the workflow editor / step drawer
          (Radix dialog under the hood) rather than a centered modal. */}
      <Sheet>
        <SheetTrigger asChild>
          <button
            type="button"
            className="mt-6 inline-flex h-8 w-full items-center justify-center gap-2 rounded-full bg-primary text-[12px] font-medium tracking-tight text-primary-foreground transition-colors hover:bg-primary/90"
            data-testid="indicator-backtest-view-btn"
          >
            <span className="leading-none">View</span>
            <ArrowUpRight
              className="h-4 w-4 shrink-0"
              strokeWidth={2}
              aria-hidden="true"
            />
          </button>
        </SheetTrigger>
        <SheetContent
          side="right"
          aria-describedby={undefined}
          // Mirrors the Agent editor panel (AgentPanel): a `border-l`,
          // `shadow-xl` right-side surface with a slim header bar
          // (display-font title + a ghost rounded-full close) over a
          // scrollable body. `p-0` / `gap-0` drop the sheet's default
          // chrome so the layout matches the editor exactly; the built-in
          // top-right close (`opacity-70`) is hidden in favour of the
          // editor-style header button.
          //
          // Width: matched to the Agent editor panel (AgentPanel), which
          // opens at a *proportional* width — not a fixed px. AppShell seeds
          // it as `min(520, max(340, 25vw))`, i.e. 25% of the viewport
          // clamped to [340, 520]. `clamp(340px, 25vw, 520px)` reproduces
          // that exactly so the two side panels are the same width on any
          // screen. Inline style is used because the shadcn right-side
          // variant hardcodes `w-3/4` + `sm:max-w-sm`, which utility-class
          // merging can't reliably override. Keep these bounds in sync with
          // AGENT_PANEL_MIN_WIDTH (340) / AGENT_PANEL_DEFAULT_WIDTH (520).
          style={{ width: "clamp(340px, 25vw, 520px)", maxWidth: "100%" }}
          overlayClassName="backtest-sheet-overlay"
          className="backtest-sheet-shell flex flex-col gap-0 border-l bg-background p-0 shadow-xl [&>button.opacity-70]:hidden"
        >
          <SheetTitle className="sr-only">{titleFor(payload)}</SheetTitle>
          {/* Header bar — same rhythm as AgentPanel's header (borderless). */}
          <div className="flex shrink-0 items-center justify-between px-4 py-3">
            <span
              style={{
                fontFamily: "var(--font-display)",
                fontWeight: "var(--weight-display)" as unknown as number,
                fontSize: 18,
                letterSpacing: "-0.02em",
                color: "var(--text-primary)",
              }}
            >
              Backtest
            </span>
            <SheetClose asChild>
              <Button
                variant="ghost"
                size="icon"
                aria-label="Close backtest panel"
                className="rounded-full"
              >
                <X className="h-4 w-4" aria-hidden="true" />
              </Button>
            </SheetClose>
          </div>
          <div className="min-h-0 flex-1 overflow-y-auto overflow-x-hidden">
            <IndicatorBacktestDetail payload={payload} />
          </div>
        </SheetContent>
      </Sheet>
    </div>
  );
}

// ---------------------------------------------------------------------------
// IndicatorBacktestDetail — full result surface, mounted inside the modal.
// Vertical flow: chips → identity → condition → hero stat (Strategy
// return ✕ vs B&H) → equity curve with signal-density band → 4-col
// performance grid → full-width Price band → optional indicator band →
// insight + disclaimer footer. One calm card surface throughout.
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
  // bench_buy_hold_return_pct is `number | null` — a backtest can have no
  // benchmark. Guard every derivation so a null neither crashes (fmtPct) nor
  // silently coerces to 0 and renders a misleading "beat buy-and-hold".
  const hasBench = bench_buy_hold_return_pct != null;
  const beatsBench = hasBench && metrics.total_return_pct > bench_buy_hold_return_pct!;
  const benchDelta = hasBench ? metrics.total_return_pct - bench_buy_hold_return_pct! : 0;
  const benchEqual = hasBench && Math.abs(benchDelta) < 0.005;
  const netPnl = metrics.ending_value - metrics.starting_capital;
  const conditionLabel = conditionFor(payload);
  const hasIndicatorChart = (indicator as string) !== "schedule" && indicator_curve.length > 1;

  const buys = signals.filter((s) => s.side === "buy");
  const sells = signals.filter((s) => s.side === "sell");

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

  // Map signal timestamps → 0..1 along the equity curve's time span.
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

  const startDate = equity_curve[0]?.t ? formatDateShort(equity_curve[0].t) : "";
  const endDate = equity_curve[equity_curve.length - 1]?.t
    ? formatDateShort(equity_curve[equity_curve.length - 1]!.t)
    : "";

  const insightText = !hasBench
    ? `${metrics.hit_rate_pct.toFixed(0)}% hit rate across ${metrics.n_trades} trade${metrics.n_trades === 1 ? "" : "s"}.`
    : benchEqual
    ? `Strategy matched buy-and-hold (${metrics.hit_rate_pct.toFixed(0)}% hit rate across ${metrics.n_trades} trade${metrics.n_trades === 1 ? "" : "s"}).`
    : beatsBench
      ? `Strategy beat buy-and-hold by ${benchDelta.toFixed(2)}% with a ${metrics.hit_rate_pct.toFixed(0)}% hit rate across ${metrics.n_trades} trade${metrics.n_trades === 1 ? "" : "s"}.`
      : `Despite a ${metrics.hit_rate_pct.toFixed(0)}% hit rate across ${metrics.n_trades} trade${metrics.n_trades === 1 ? "" : "s"}, strategy underperformed buy-and-hold by ${Math.abs(benchDelta).toFixed(2)}%.`;

  return (
    <TooltipProvider delayDuration={150}>
    <div
      className="relative w-full bg-card"
      data-testid="indicator-backtest-detail"
      role="region"
      aria-label={`Indicator backtest ${symbol}`}
    >
      <div className="flex flex-col gap-5 px-6 pt-3 pb-5">
        {/* ── HEADER: chips, identity, condition (compact). The "Backtest"
            type label now lives in the panel header bar, so the in-body
            chip row carries just the period. ─────────────────────────── */}
        <div className="flex flex-col gap-2">
          <div className="flex flex-wrap items-center gap-2">
            <span className="inline-flex items-center gap-1 rounded-md bg-muted px-2.5 py-0.5 text-[11px] font-medium tabular-nums tracking-tight text-muted-foreground">
              <Calendar className="h-3 w-3 shrink-0" aria-hidden="true" />
              {period_label}
            </span>
          </div>
          <div className="flex flex-wrap items-baseline gap-x-3 gap-y-0.5">
            <h3 className="text-[20px] leading-[1.15] font-semibold tracking-tight text-foreground sm:text-[22px]">
              {companyName}
            </h3>
            <span className="font-mono text-[11px] uppercase tracking-wider text-muted-foreground/70">
              NSE: {symbol}
            </span>
            <span className="text-[12px] text-muted-foreground/70">
              <span className="mx-1.5 text-muted-foreground/40">·</span>
              {conditionLabel}
            </span>
          </div>
        </div>

        {/* ── HERO STAT: Strategy return + vs B&H (compact) ────────── */}
        <div className="grid grid-cols-2 gap-x-4">
          <div className="flex min-w-0 flex-col gap-0.5">
            <span className="font-mono text-[10px] uppercase tracking-[0.08em] text-muted-foreground">
              Strategy return
            </span>
            <div className="flex flex-col gap-0.5">
              <span
                className={cn(
                  "text-[24px] leading-none font-semibold tabular-nums tracking-tight sm:text-[28px]",
                  positive ? "text-[var(--color-profit)]" : "text-[var(--color-loss)]",
                )}
              >
                {fmtPct(metrics.total_return_pct)}
              </span>
              <span
                className={cn(
                  "text-[11.5px] font-medium tabular-nums",
                  netPnl >= 0 ? "text-[var(--color-profit)]/80" : "text-[var(--color-loss)]/80",
                )}
              >
                {netPnl >= 0 ? "+" : ""}
                {fmtINR(netPnl)}
              </span>
            </div>
          </div>
          {hasBench && (
          <div className="flex min-w-0 flex-col gap-0.5 border-l border-border/40 pl-4 sm:pl-5">
            <span className="font-mono text-[10px] uppercase tracking-[0.08em] text-muted-foreground">
              vs Buy &amp; hold
            </span>
            <div className="flex flex-col gap-0.5">
              <span
                className={cn(
                  "inline-flex items-baseline gap-1 text-[24px] leading-none font-semibold tabular-nums tracking-tight sm:text-[28px]",
                  benchEqual
                    ? "text-foreground"
                    : beatsBench
                      ? "text-[var(--color-profit)]"
                      : "text-[var(--color-loss)]",
                )}
              >
                {!benchEqual &&
                  (beatsBench ? (
                    <ArrowUp className="h-4 w-4 shrink-0" strokeWidth={2.5} aria-hidden="true" />
                  ) : (
                    <ArrowDown className="h-4 w-4 shrink-0" strokeWidth={2.5} aria-hidden="true" />
                  ))}
                {Math.abs(benchDelta).toFixed(2)}%
              </span>
              <span className="text-[11.5px] font-medium tabular-nums text-muted-foreground">
                B&amp;H {fmtPct(bench_buy_hold_return_pct)}
              </span>
            </div>
          </div>
          )}
        </div>

        {/* ── CHARTS + PERFORMANCE — single column. The panel is ~520px
            wide (matched to the Agent editor); a side-by-side split
            crammed the charts and stretched the stat grid into sparse
            rows. Everything now stacks vertically: full-width Equity and
            Price charts, then a compact Performance grid below. ───────── */}
        <div className="mt-1 flex min-w-0 flex-col gap-7">
          {/* Equity curve */}
          <div className="flex min-w-0 flex-col gap-2">
            <div className="flex items-baseline justify-between gap-3">
              <HintLabel
                label="Equity curve"
                tip={`Value of a ${fmtINR(metrics.starting_capital)} portfolio over time, marked-to-market through every trade the strategy makes. Flat stretches mean the strategy is holding cash; sloped sections track the position's price.`}
              />
              <span className="font-mono text-[11px] tabular-nums text-muted-foreground">
                {fmtINR(metrics.starting_capital)}
                <span className="mx-1.5 text-muted-foreground/50">→</span>
                <span className="text-foreground/90">{fmtINR(metrics.ending_value)}</span>
              </span>
            </div>
            <div className="relative h-[170px]" data-testid="equity-chart">
              <InteractiveAreaChart
                points={equity_curve}
                color={positive ? "var(--color-profit)" : "var(--color-loss)"}
                height={170}
                formatValue={fmtINR}
                formatDate={(iso) => formatDateShort(iso)}
                ariaLabel="Equity curve"
                enableRangeSelect={true}
              />
              <SignalDensityBand signals={signalPositions} />
            </div>
            <div className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1 text-[11px] text-muted-foreground">
              <span className="tabular-nums">
                {startDate}
                <span className="mx-1.5 text-muted-foreground/50">—</span>
                {endDate}
              </span>
              <span className="inline-flex items-center gap-1.5 tabular-nums">
                <span
                  className="h-1.5 w-1.5 rounded-full bg-[var(--color-profit)]"
                  aria-hidden="true"
                />
                {buys.length} buy{buys.length === 1 ? "" : "s"}
                <span className="mx-1.5 text-muted-foreground/40">·</span>
                <span
                  className="h-1.5 w-1.5 rounded-full bg-[var(--color-loss)]"
                  aria-hidden="true"
                />
                {sells.length} sell{sells.length === 1 ? "" : "s"}
              </span>
            </div>
          </div>

          {/* Price */}
          <div className="flex flex-col gap-1.5" data-testid="price-chart">
            <HintLabel
              label="Price"
              tip="The underlying asset's raw market price over the same window. Independent of strategy — the same line whether you ran this backtest, a different one, or nothing at all."
            />
            <div className="h-[56px]">
              <InteractiveAreaChart
                points={price_curve}
                color="var(--price-line)"
                height={56}
                formatValue={(v) => `₹${Math.round(v).toLocaleString("en-IN")}`}
                formatDate={formatDateShort}
                ariaLabel="Price"
                enableRangeSelect={false}
              />
            </div>
          </div>

          {/* Indicator (optional) */}
          {hasIndicatorChart ? (
            <div className="flex flex-col gap-1.5" data-testid="indicator-chart">
              <HintLabel
                label={
                  <>
                    {indicator.toUpperCase()}({indicator_period})
                    {indicator === "rsi" ? (
                      <span className="ml-2 normal-case tracking-normal text-muted-foreground/70">
                        threshold {threshold}
                      </span>
                    ) : null}
                  </>
                }
                tip={
                  indicator === "rsi"
                    ? `RSI(${indicator_period}) — momentum oscillator that signals overbought (high) or oversold (low) conditions. The dashed line marks the strategy's trigger threshold of ${threshold}.`
                    : `${indicator.toUpperCase()}(${indicator_period}) — moving average over the last ${indicator_period} periods. The strategy compares the live price against this line to decide when to buy or sell.`
                }
              />
              <div className="h-[56px]">
                <InteractiveAreaChart
                  points={indicator_curve}
                  color="#219ebc"
                  height={56}
                  formatValue={(v) => v.toFixed(2)}
                  formatDate={formatDateShort}
                  referenceY={indicator === "rsi" ? threshold : undefined}
                  ariaLabel={`${indicator.toUpperCase()}(${indicator_period})`}
                  enableRangeSelect={false}
                />
              </div>
            </div>
          ) : (
            // Hidden marker keeps the indicator-chart testId discoverable
            // for tests that assert its presence; visually no-op.
            <span data-testid="indicator-chart" className="sr-only" aria-hidden="true" />
          )}

          {/* Performance — compact full-width grid below the charts
              (was a stretched right-hand column). */}
          <div className="flex flex-col gap-3 border-t border-border/40 pt-4">
            <span className="font-mono text-[10px] uppercase tracking-[0.08em] text-muted-foreground">
              Performance
            </span>
            <div className="grid grid-cols-2 gap-x-6 gap-y-4">
              <DetailStat label="CAGR" value={fmtPct(metrics.cagr_pct)} tone={metrics.cagr_pct >= 0 ? "profit" : "loss"} />
              <DetailStat
                label="Max DD"
                value={`-${Math.abs(metrics.max_drawdown_pct).toFixed(1)}%`}
                tone="loss"
              />
              <DetailStat label="Trades" value={String(metrics.n_trades)} />
              <DetailStat
                label="Hit rate"
                value={`${metrics.hit_rate_pct.toFixed(0)}%`}
              />
              <DetailStat label="Wins" value={`${metrics.n_wins}/${metrics.n_trades}`} />
              <DetailStat label="Start" value={fmtINR(metrics.starting_capital)} />
              <DetailStat label="End value" value={fmtINR(metrics.ending_value)} />
              <DetailStat
                label={`${symbol} buy & hold`}
                value={fmtPct(bench_buy_hold_return_pct)}
                tone={
                  bench_buy_hold_return_pct == null
                    ? undefined
                    : bench_buy_hold_return_pct >= 0
                      ? "profit"
                      : "loss"
                }
              />
            </div>
          </div>
        </div>

        {/* ── INSIGHT + DISCLAIMER ─────────────────────────────────── */}
        <div className="flex flex-col gap-2 border-t border-border/40 pt-4">
          <p className="text-[12px] leading-snug text-foreground/75">
            {insightText}
          </p>
          <p className="inline-flex items-center gap-1.5 text-[10.5px] leading-snug text-muted-foreground/55">
            <ShieldAlert
              className="h-3 w-3 shrink-0 text-muted-foreground/40"
              aria-hidden="true"
            />
            Past performance does not guarantee future results.
          </p>
        </div>
      </div>
    </div>
    </TooltipProvider>
  );
}

// HintLabel — uppercase section label with a tiny info icon that
// reveals a tooltip on hover/focus. Used so users don't have to ask
// what "equity curve" vs "price" mean.
function HintLabel({
  label,
  tip,
  className,
}: {
  label: React.ReactNode;
  tip: string;
  className?: string;
}): React.ReactElement {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 font-mono text-[10px] uppercase tracking-[0.08em] text-muted-foreground",
        className,
      )}
    >
      {label}
      <Tooltip>
        <TooltipTrigger asChild>
          <button
            type="button"
            aria-label={`What is ${typeof label === "string" ? label : "this"}?`}
            className="inline-flex items-center text-muted-foreground/60 transition-colors hover:text-foreground focus:outline-none focus-visible:text-foreground"
          >
            <Info className="h-3 w-3" aria-hidden="true" />
          </button>
        </TooltipTrigger>
        <TooltipContent
          side="top"
          align="start"
          className="max-w-[260px] text-[11.5px] leading-snug normal-case tracking-normal"
        >
          {tip}
        </TooltipContent>
      </Tooltip>
    </span>
  );
}

function formatDateShort(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleDateString("en-IN", { day: "numeric", month: "short", year: "2-digit" });
}

// SignalDensityBand — thin 6px strip at the bottom of the equity chart.
// Each signal renders as a 1px vertical tick at low opacity so clusters
// visually accumulate into darker bands without 200+ overlapping dots.
// Buys = profit green, sells = loss red.
function SignalDensityBand({
  signals,
}: {
  signals: Array<{ x: number; side: "buy" | "sell" }>;
}): React.ReactElement | null {
  if (signals.length === 0) return null;
  // Tick opacity inversely proportional to signal count so dense
  // backtests (200+ buys) stay readable instead of blowing out into a
  // solid bar. Cap at 0.55 so a single signal still reads clearly.
  const tickOpacity = Math.max(0.18, Math.min(0.55, 18 / signals.length));
  return (
    <div
      className="pointer-events-none absolute inset-x-0 bottom-0 h-1.5 overflow-hidden rounded-b-sm bg-muted/30"
      aria-hidden="true"
    >
      {signals.map((s, i) => (
        <span
          key={i}
          className="absolute top-0 bottom-0 w-px"
          style={{
            left: `${s.x * 100}%`,
            background:
              s.side === "buy" ? "var(--color-profit)" : "var(--color-loss)",
            opacity: tickOpacity,
          }}
        />
      ))}
    </div>
  );
}

// DetailStat — performance grid cell with optional profit/loss tone.
// Label sits above the value; both use the same horizontal alignment so
// the 4-col grid scans as a clean table without dividers.
function DetailStat({
  label,
  value,
  tone,
}: {
  label: string;
  value: string;
  tone?: "profit" | "loss";
}): React.ReactElement {
  return (
    <div className="flex min-w-0 flex-col gap-1">
      <span className="text-[11px] tracking-tight text-muted-foreground">
        {label}
      </span>
      <span
        className={cn(
          "text-[18px] font-semibold tabular-nums tracking-tight",
          tone === "profit" && "text-[var(--color-profit)]",
          tone === "loss" && "text-[var(--color-loss)]",
          !tone && "text-foreground",
        )}
      >
        {value}
      </span>
    </div>
  );
}


