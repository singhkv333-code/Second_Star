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
import { ArrowDown, ArrowUp, ArrowUpRight, Calendar, ShieldAlert, TriangleAlert, X } from "lucide-react";
import { cn } from "@/lib/utils";
import { Dialog, DialogClose, DialogContent, DialogTitle, DialogTrigger } from "@/components/ui/dialog";
import { getStockQuote, type StockQuote } from "@/lib/api";
import { isError } from "@/lib/types";
import { BacktestEquityChart } from "@/components/chart/BacktestEquityChart";

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
    // Risk-adjusted metrics + benchmark (added 2026-05-29; optional so older
    // payloads still render). Sharpe/Sortino annualized; benchmark net of costs.
    sharpe?: number | null;
    sortino?: number | null;
    benchmark_return_pct?: number | null;
    // Statistical-rigor battery (added 2026-06-01; optional — older payloads
    // and short backtests omit them; never break if absent).
    forward_stats?: {
      observed_sharpe: number | null;
      skew: number | null;
      kurtosis: number | null;
      n_obs: number;
      num_trials: number;
      psr: number | null;
      min_trl: number | null;
      deflated_sharpe: number | null;
    } | null;
    monte_carlo?: {
      n_sims: number;
      block_size: number;
      dd_median_pct: number | null;
      dd_p95_severity_pct: number | null;
      dd_worst_pct: number | null;
      terminal_median_pct: number | null;
      terminal_p05_pct: number | null;
      prob_loss: number | null;
      prob_dd_worse_than_tol: number | null;
      drawdown_tolerance_pct: number | null;
    } | null;
    sub_periods?: {
      n_periods: number;
      period_returns_pct: number[];
      positive_period_frac: number | null;
      best_period_return_pct: number | null;
      worst_period_return_pct: number | null;
      concentration: number | null;
    } | null;
    trust_verdict?: {
      verdict: "insufficient_data" | "no_edge" | "unproven" | "promising";
      label: string;
      confidence: number;
      rationale: string;
      flags: string[];
    } | null;
  };
  bench_buy_hold_return_pct: number | null;
  // Methodology block — window / after-costs / daily-bar basis / survivorship
  // caveat. Present on backtests run after the 2026-05-29 transparency change.
  methodology?: {
    window: string;
    costs: string;
    basis: string;
    caveat: string;
  } | null;
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
        <div className="mt-1 flex items-baseline gap-3 flex-wrap">
          <p
            className={cn(
              "text-[26px] leading-none font-semibold tabular-nums tracking-tight",
              positive
                ? "text-emerald-600 dark:text-emerald-400"
                : "text-rose-600 dark:text-rose-400",
            )}
          >
            {fmtPct(metrics.total_return_pct)}
          </p>
          {metrics.trust_verdict && (
            <span
              className={cn(
                "inline-flex items-center rounded-full px-2 py-0.5 text-[10.5px] font-medium leading-none",
                verdictClasses(metrics.trust_verdict.verdict).pill,
              )}
            >
              {metrics.trust_verdict.label} · {metrics.trust_verdict.confidence}%
            </span>
          )}
        </div>
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

  // Benchmark can be null (e.g. DSL backtest when primary bars are missing) —
  // coerce to 0 for the delta math + label so the card never renders NaN.
  const benchPct = bench_buy_hold_return_pct ?? 0;
  const positive = metrics.total_return_pct >= 0;
  const beatsBench = metrics.total_return_pct > benchPct;
  const benchDelta = metrics.total_return_pct - benchPct;
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
          <div data-testid="equity-chart">
            <BacktestEquityChart
              equity={equity_curve}
              baseline={metrics.starting_capital}
              benchmark={price_curve.length ? price_curve : null}
              signals={signals}
              height={220}
            />
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
            value={fmtPct(benchPct)}
          />
          {(metrics.sharpe != null || metrics.sortino != null) && (
            <>
              <RoomyStat
                label="Sharpe"
                value={metrics.sharpe != null ? metrics.sharpe.toFixed(2) : "—"}
              />
              <RoomyStat
                label="Sortino"
                value={metrics.sortino != null ? metrics.sortino.toFixed(2) : "—"}
              />
            </>
          )}
        </div>
      </div>

      {/* Trust panel — statistical-rigor verdict. Shown only when the
          backend attaches trust_verdict to metrics (post-2026-06-01 runs).
          Placed high in the detail view — "should I trust this?" before
          the P&L breakdown. */}
      <TrustPanel metrics={metrics} />

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

      {/* Methodology strip — window, after-costs basis, survivorship caveat.
          Surfaced so the user knows results are net of realistic costs on
          daily bars, not idealized. Present only on post-2026-05-29 runs. */}
      {payload.methodology && (
        <div
          className="border-t border-border/50 px-10 py-3 text-[10.5px] leading-snug text-muted-foreground/70"
          data-testid="backtest-methodology"
        >
          <span className="tabular-nums">{payload.methodology.window}</span>
          <span className="mx-1.5 text-muted-foreground/40">·</span>
          {payload.methodology.costs}
          <span className="mx-1.5 text-muted-foreground/40">·</span>
          {payload.methodology.basis}
        </div>
      )}

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
          {payload.methodology?.caveat ?? "Past performance does not guarantee future results."}
        </p>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Trust panel helpers
// ---------------------------------------------------------------------------

type TrustVerdict = "insufficient_data" | "no_edge" | "unproven" | "promising";

function verdictClasses(verdict: TrustVerdict): { pill: string; badge: string; text: string } {
  switch (verdict) {
    case "promising":
      return {
        pill: "bg-emerald-100 text-emerald-700 dark:bg-emerald-500/15 dark:text-emerald-300",
        badge: "bg-emerald-50 text-emerald-800 dark:bg-emerald-500/15 dark:text-emerald-300",
        text: "text-emerald-700 dark:text-emerald-300",
      };
    case "unproven":
      return {
        pill: "bg-amber-100 text-amber-700 dark:bg-amber-500/15 dark:text-amber-300",
        badge: "bg-amber-50 text-amber-800 dark:bg-amber-500/15 dark:text-amber-300",
        text: "text-amber-700 dark:text-amber-300",
      };
    case "no_edge":
      return {
        pill: "bg-rose-100 text-rose-700 dark:bg-rose-500/15 dark:text-rose-300",
        badge: "bg-rose-50 text-rose-800 dark:bg-rose-500/15 dark:text-rose-300",
        text: "text-rose-700 dark:text-rose-300",
      };
    case "insufficient_data":
    default:
      return {
        pill: "bg-zinc-100 text-zinc-600 dark:bg-zinc-700/40 dark:text-zinc-400",
        badge: "bg-zinc-50 text-zinc-700 dark:bg-zinc-700/40 dark:text-zinc-400",
        text: "text-zinc-600 dark:text-zinc-400",
      };
  }
}

const FLAG_LABELS: Record<string, string> = {
  selection_bias: "Many variants tried",
  return_concentrated: "Return concentrated",
  drawdown_risk: "Deep drawdown risk",
  loss_likely: "Loss likely",
};

function flagChipClass(flag: string): string {
  if (flag === "drawdown_risk" || flag === "loss_likely") {
    return "bg-rose-100 text-rose-700 dark:bg-rose-500/15 dark:text-rose-300";
  }
  return "bg-amber-100 text-amber-700 dark:bg-amber-500/15 dark:text-amber-300";
}

function fmtProbPct(n: number | null | undefined): string {
  if (n == null) return "—";
  return `${Math.round(n * 100)}%`;
}

type TrustPanelMetrics = {
  trust_verdict?: {
    verdict: TrustVerdict;
    label: string;
    confidence: number;
    rationale: string;
    flags: string[];
  } | null;
  forward_stats?: {
    n_obs: number;
    num_trials: number;
    psr: number | null;
    min_trl: number | null;
    deflated_sharpe: number | null;
  } | null;
  monte_carlo?: {
    dd_p95_severity_pct: number | null;
    prob_loss: number | null;
  } | null;
  sub_periods?: {
    concentration: number | null;
  } | null;
};

function TrustPanel({ metrics }: { metrics: TrustPanelMetrics }): React.ReactElement | null {
  const tv = metrics.trust_verdict;
  if (!tv) return null;

  const fs = metrics.forward_stats;
  const mc = metrics.monte_carlo;
  const sp = metrics.sub_periods;

  const cls = verdictClasses(tv.verdict);

  const trlMet =
    fs?.min_trl != null && fs.min_trl <= fs.n_obs;
  const trlSuffix =
    fs?.min_trl != null
      ? trlMet
        ? " ✓"
        : " ✗"
      : "";

  return (
    <div
      className="border-t border-border/50 px-10 py-6"
      data-testid="backtest-trust-panel"
      aria-label="Statistical trust panel"
    >
      {/* Verdict header */}
      <div className="flex flex-wrap items-start gap-3">
        <span
          className={cn(
            "inline-flex items-center gap-1.5 rounded-md px-3 py-1.5 text-[12px] font-semibold tracking-tight",
            cls.badge,
          )}
        >
          <TriangleAlert className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
          {tv.label}
          <span className="font-normal opacity-75">· {tv.confidence}% confidence</span>
        </span>
      </div>

      {/* Rationale */}
      <p className="mt-2.5 text-[12.5px] leading-snug text-muted-foreground">
        {tv.rationale}
      </p>

      {/* Rigor stat row */}
      {fs && (
        <div className="mt-4 grid grid-cols-3 gap-x-8 gap-y-4 sm:grid-cols-6">
          <RoomyStat
            label="PSR"
            value={fmtProbPct(fs.psr)}
          />
          <div className="flex min-w-0 flex-col gap-1">
            <span className="text-[11px] tracking-tight text-muted-foreground">
              Deflated Sharpe
            </span>
            <span className="text-[17px] font-semibold tabular-nums tracking-tight text-foreground">
              {fmtProbPct(fs.deflated_sharpe)}
            </span>
            <span className="text-[10px] text-muted-foreground/70 tabular-nums">
              {fs.num_trials} trial{fs.num_trials === 1 ? "" : "s"}
            </span>
          </div>
          <div className="flex min-w-0 flex-col gap-1">
            <span className="text-[11px] tracking-tight text-muted-foreground">
              Min track record
            </span>
            <span className={cn(
              "text-[17px] font-semibold tabular-nums tracking-tight",
              trlMet ? "text-emerald-600 dark:text-emerald-400" : "text-foreground",
            )}>
              {fs.min_trl != null ? `${fs.min_trl} obs${trlSuffix}` : "—"}
            </span>
            <span className="text-[10px] text-muted-foreground/70 tabular-nums">
              {fs.n_obs} observed
            </span>
          </div>
          <RoomyStat
            label="5%-worst DD"
            value={mc?.dd_p95_severity_pct != null ? `${mc.dd_p95_severity_pct.toFixed(1)}%` : "—"}
          />
          <RoomyStat
            label="P(loss)"
            value={fmtProbPct(mc?.prob_loss)}
          />
          <RoomyStat
            label="Concentration"
            value={fmtProbPct(sp?.concentration)}
          />
        </div>
      )}

      {/* Flag chips */}
      {tv.flags.length > 0 && (
        <div className="mt-3 flex flex-wrap gap-1.5">
          {tv.flags.map((flag) => (
            <span
              key={flag}
              className={cn(
                "inline-flex items-center rounded-md px-2 py-0.5 text-[10.5px] font-medium",
                flagChipClass(flag),
              )}
            >
              {FLAG_LABELS[flag] ?? flag}
            </span>
          ))}
        </div>
      )}
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

