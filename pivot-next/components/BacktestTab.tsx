"use client";

/**
 * BacktestTab — Phase 2 strategy backtester surface.
 *
 * Left: DSL input + field chip picker + date range + rebalance + benchmark.
 * Right: equity curve (Recharts), drawdown chart, metrics row, rebalance log.
 *
 * Uses GET /api/backtest/expr/fields for field chips.
 * Submits to POST /api/backtest/expr/run.
 * URL state: ?expr=&start=&end=&rebalance= via hash fragment so runs are shareable.
 */

import { useCallback, useEffect, useState } from "react";
import {
  CartesianGrid,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
  Area,
  AreaChart,
} from "recharts";
import { AlertCircle, RefreshCw, Zap } from "lucide-react";
import { format, parseISO } from "date-fns";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Accordion,
  AccordionContent,
  AccordionItem,
  AccordionTrigger,
} from "@/components/ui/accordion";
import { cn } from "@/lib/utils";
import {
  getBacktestFields,
  runBacktest,
  type BacktestField,
  type BacktestResult,
} from "@/lib/api";
import { isError } from "@/lib/types";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

type RunState =
  | { kind: "idle" }
  | { kind: "running" }
  | { kind: "error"; message: string }
  | { kind: "done"; result: BacktestResult };

type FieldsState =
  | { kind: "loading" }
  | { kind: "error" }
  | { kind: "ok"; fields: BacktestField[] };

const REBALANCE_OPTIONS = [
  { value: "D", label: "Daily" },
  { value: "W", label: "Weekly" },
  { value: "M", label: "Monthly" },
  { value: "Q", label: "Quarterly" },
] as const;

// ---------------------------------------------------------------------------
// URL state helpers
// ---------------------------------------------------------------------------

function readUrlState(): { expr: string; start: string; end: string; rebalance: string } {
  if (typeof window === "undefined") {
    return { expr: "", start: "2018-01-01", end: "2024-12-31", rebalance: "Q" };
  }
  const hash = window.location.hash;
  const match = hash.match(/\?(.+)$/);
  if (!match) return { expr: "", start: "2018-01-01", end: "2024-12-31", rebalance: "Q" };
  const params = new URLSearchParams(match[1]);
  return {
    expr: params.get("expr") ?? "",
    start: params.get("start") ?? "2018-01-01",
    end: params.get("end") ?? "2024-12-31",
    rebalance: params.get("rebalance") ?? "Q",
  };
}

function writeUrlState(expr: string, start: string, end: string, rebalance: string): void {
  if (typeof window === "undefined") return;
  const params = new URLSearchParams();
  if (expr) params.set("expr", expr);
  params.set("start", start);
  params.set("end", end);
  params.set("rebalance", rebalance);
  const base = window.location.hash.split("?")[0];
  window.history.replaceState(null, "", `${base}?${params.toString()}`);
}

// ---------------------------------------------------------------------------
// BacktestTab
// ---------------------------------------------------------------------------

export function BacktestTab(): React.ReactElement {
  const initial = readUrlState();
  const [expr, setExpr] = useState(initial.expr);
  const [start, setStart] = useState(initial.start);
  const [end, setEnd] = useState(initial.end);
  const [rebalance, setRebalance] = useState(initial.rebalance);
  const [runState, setRunState] = useState<RunState>({ kind: "idle" });
  const [fieldsState, setFieldsState] = useState<FieldsState>({ kind: "loading" });
  const [logScale, setLogScale] = useState(false);

  // Load fields for chip picker
  useEffect(() => {
    getBacktestFields()
      .then((result) => {
        if (isError(result)) {
          setFieldsState({ kind: "error" });
          return;
        }
        const all: BacktestField[] = [
          ...result.data.base_fields,
          ...result.data.computed_fields,
        ];
        setFieldsState({ kind: "ok", fields: all });
      })
      .catch(() => setFieldsState({ kind: "error" }));
  }, []);

  const handleRun = useCallback(async (): Promise<void> => {
    if (!expr.trim()) return;
    if (runState.kind === "running") return;

    writeUrlState(expr, start, end, rebalance);
    setRunState({ kind: "running" });

    const result = await runBacktest({
      expression: expr.trim(),
      start,
      end,
      rebalance,
      auto_map_symbols: true,
    });

    if (isError(result)) {
      setRunState({ kind: "error", message: result.error.message });
    } else {
      setRunState({ kind: "done", result: result.data });
    }
  }, [expr, start, end, rebalance, runState.kind]);

  const handleKeyDown = (e: React.KeyboardEvent): void => {
    if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
      e.preventDefault();
      void handleRun();
    }
  };

  const appendField = (fieldName: string): void => {
    setExpr((prev) => {
      const trimmed = prev.trim();
      if (!trimmed) return fieldName;
      return `${trimmed} AND ${fieldName}`;
    });
  };

  return (
    <div className="flex flex-col gap-6" data-testid="backtest-tab">
      <div className="flex items-start justify-between gap-2">
        <div>
          <h1 className="font-serif text-2xl font-semibold tracking-tight text-foreground">
            Strategy Backtester
          </h1>
          <p className="mt-1 text-xs text-muted-foreground">
            Write a fundamentals filter, pick a date range and rebalance cadence, run.
          </p>
        </div>
      </div>

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-[1fr_1.6fr]">
        {/* ── Left: Builder ── */}
        <div className="flex flex-col gap-4">
          {/* DSL input */}
          <div className="rounded-xl border bg-card p-4 shadow-sm">
            <label htmlFor="bt-expr" className="mb-2 block text-xs font-semibold uppercase tracking-wider text-muted-foreground">
              Expression
            </label>
            <textarea
              id="bt-expr"
              value={expr}
              onChange={(e) => setExpr(e.target.value)}
              onKeyDown={handleKeyDown}
              placeholder="pe_ratio < 15 AND roe > 18"
              rows={4}
              className={cn(
                "w-full resize-none rounded-lg border bg-background px-3 py-2 font-mono text-sm",
                "focus:outline-none focus:ring-2 focus:ring-ring",
                "placeholder:text-muted-foreground/50",
              )}
              aria-label="Backtest expression"
              data-testid="bt-expr-input"
            />
            <p className="mt-1 text-[10px] text-muted-foreground">
              Cmd+Enter to run · AND / OR / NOT · numeric comparisons · field{" "}
              <code className="font-mono">_ttm</code> suffix for trailing 12-month
            </p>
          </div>

          {/* Field chip picker */}
          <div className="rounded-xl border bg-card p-4 shadow-sm">
            <p className="mb-2 text-xs font-semibold uppercase tracking-wider text-muted-foreground">
              Field picker
            </p>
            {fieldsState.kind === "loading" && (
              <div className="flex flex-wrap gap-1.5">
                {Array.from({ length: 8 }).map((_, i) => (
                  <Skeleton key={i} className="h-6 w-20 rounded-full" />
                ))}
              </div>
            )}
            {fieldsState.kind === "error" && (
              <p className="text-xs text-muted-foreground">Couldn&apos;t load fields.</p>
            )}
            {fieldsState.kind === "ok" && (
              <div className="flex flex-wrap gap-1.5 max-h-40 overflow-y-auto">
                {fieldsState.fields.map((f) => (
                  <button
                    key={f.name}
                    type="button"
                    onClick={() => appendField(f.name)}
                    title={f.description ?? f.name}
                    className={cn(
                      "rounded-full border px-2.5 py-0.5 text-[11px] font-medium",
                      "bg-muted/40 text-foreground hover:bg-primary/10 hover:text-primary",
                      "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                      "transition-colors",
                    )}
                    data-testid={`field-chip-${f.name}`}
                  >
                    {f.name}
                  </button>
                ))}
              </div>
            )}
          </div>

          {/* Date range + rebalance */}
          <div className="rounded-xl border bg-card p-4 shadow-sm space-y-3">
            <p className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
              Parameters
            </p>
            <div className="grid grid-cols-2 gap-3">
              <div>
                <label htmlFor="bt-start" className="mb-1 block text-[11px] text-muted-foreground">
                  Start date
                </label>
                <Input
                  id="bt-start"
                  type="date"
                  value={start}
                  onChange={(e) => setStart(e.target.value)}
                  className="h-8 text-sm"
                  data-testid="bt-start-input"
                />
              </div>
              <div>
                <label htmlFor="bt-end" className="mb-1 block text-[11px] text-muted-foreground">
                  End date
                </label>
                <Input
                  id="bt-end"
                  type="date"
                  value={end}
                  onChange={(e) => setEnd(e.target.value)}
                  className="h-8 text-sm"
                  data-testid="bt-end-input"
                />
              </div>
            </div>

            <div>
              <p className="mb-1.5 text-[11px] text-muted-foreground">Rebalance frequency</p>
              <div className="flex gap-2 flex-wrap" role="group" aria-label="Rebalance frequency">
                {REBALANCE_OPTIONS.map((opt) => (
                  <button
                    key={opt.value}
                    type="button"
                    onClick={() => setRebalance(opt.value)}
                    aria-pressed={rebalance === opt.value}
                    className={cn(
                      "rounded-full border px-3 py-1 text-xs font-medium transition-colors",
                      rebalance === opt.value
                        ? "bg-primary text-primary-foreground border-primary"
                        : "bg-muted/40 text-foreground hover:bg-muted",
                    )}
                    data-testid={`rebalance-${opt.value}`}
                  >
                    {opt.label}
                  </button>
                ))}
              </div>
            </div>
          </div>

          <Button
            onClick={() => { void handleRun(); }}
            disabled={!expr.trim() || runState.kind === "running"}
            className="w-full"
            data-testid="bt-run-btn"
          >
            {runState.kind === "running" && (
              <RefreshCw className="mr-2 h-4 w-4 animate-spin" aria-hidden="true" />
            )}
            <Zap className="mr-2 h-4 w-4" aria-hidden="true" />
            Run backtest
          </Button>
        </div>

        {/* ── Right: Results ── */}
        <div>
          {runState.kind === "idle" && (
            <IdleResultsPlaceholder />
          )}
          {runState.kind === "running" && (
            <RunningResults />
          )}
          {runState.kind === "error" && (
            <ErrorResults
              message={runState.message}
              onRetry={() => { void handleRun(); }}
            />
          )}
          {runState.kind === "done" && (
            <BacktestResults
              result={runState.result}
              logScale={logScale}
              onToggleScale={() => setLogScale((v) => !v)}
            />
          )}
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Result states
// ---------------------------------------------------------------------------

function IdleResultsPlaceholder(): React.ReactElement {
  return (
    <div
      className="flex h-64 flex-col items-center justify-center rounded-xl border border-dashed bg-card/50 text-center"
      data-testid="bt-idle"
    >
      <Zap className="mb-3 h-8 w-8 text-muted-foreground/40" aria-hidden="true" />
      <p className="text-sm font-medium">Write an expression and run</p>
      <p className="mt-1 text-xs text-muted-foreground max-w-xs">
        Results will appear here — equity curve, drawdown, metrics, and position log.
      </p>
    </div>
  );
}

function RunningResults(): React.ReactElement {
  return (
    <div className="flex flex-col gap-4" data-testid="bt-running">
      <div className="rounded-xl border bg-card p-4 shadow-sm space-y-3">
        <Skeleton className="h-4 w-32" />
        <Skeleton className="h-48 w-full" />
      </div>
      <div className="grid grid-cols-4 gap-3">
        {Array.from({ length: 7 }).map((_, i) => (
          <Skeleton key={i} className="h-14 rounded-xl" />
        ))}
      </div>
    </div>
  );
}

function ErrorResults({ message, onRetry }: { message: string; onRetry: () => void }): React.ReactElement {
  return (
    <div
      role="alert"
      className="flex flex-col items-center justify-center rounded-xl border border-destructive/20 bg-destructive/5 p-8 text-center"
      data-testid="bt-error"
    >
      <AlertCircle className="mb-3 h-6 w-6 text-destructive" aria-hidden="true" />
      <p className="text-sm font-medium">Backtest failed</p>
      <p className="mt-1 text-xs text-muted-foreground max-w-xs">{message}</p>
      <Button variant="outline" size="sm" className="mt-4" onClick={onRetry}>
        <RefreshCw className="mr-1.5 h-3.5 w-3.5" aria-hidden="true" />
        Retry
      </Button>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Full results view
// ---------------------------------------------------------------------------

function BacktestResults({
  result,
  logScale,
  onToggleScale,
}: {
  result: BacktestResult;
  logScale: boolean;
  onToggleScale: () => void;
}): React.ReactElement {
  return (
    <div className="flex flex-col gap-5" data-testid="bt-results">
      {/* Warnings */}
      {result.warnings.length > 0 && (
        <div className="rounded-xl border border-warning/30 bg-warning/5 px-4 py-3">
          {result.warnings.map((w, i) => (
            <p key={i} className="text-xs text-warning">{w}</p>
          ))}
        </div>
      )}

      {/* Equity curve */}
      <EquityCurveChart result={result} logScale={logScale} onToggleScale={onToggleScale} />

      {/* Drawdown chart */}
      <DrawdownChart equityCurve={result.equity_curve} />

      {/* Metrics row */}
      <MetricsRow metrics={result.metrics} />

      {/* Position log + audit */}
      <RebalanceLog rebalances={result.rebalances} />
      <AuditAppendix audit={result.universe_audit} fields={result.leaf_fields} />
    </div>
  );
}

// ---------------------------------------------------------------------------
// Equity curve chart
// ---------------------------------------------------------------------------

const MONTH_FMT = (date: string): string => {
  try {
    return format(parseISO(date), "MMM yy");
  } catch {
    return date;
  }
};

function EquityCurveChart({
  result,
  logScale,
  onToggleScale,
}: {
  result: BacktestResult;
  logScale: boolean;
  onToggleScale: () => void;
}): React.ReactElement {
  // Merge equity_curve and benchmark_curve by date
  const dateMap = new Map<string, { strategy?: number; benchmark?: number }>();
  for (const pt of result.equity_curve) {
    dateMap.set(pt.date, { strategy: pt.value });
  }
  for (const pt of result.benchmark_curve) {
    const existing = dateMap.get(pt.date) ?? {};
    dateMap.set(pt.date, { ...existing, benchmark: pt.value });
  }
  const data = [...dateMap.entries()]
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([date, v]) => ({ date, strategy: v.strategy, benchmark: v.benchmark }));

  // Compute percent-from-start for tooltip
  const stratStart = data[0]?.strategy ?? 1;
  const benchStart = data[0]?.benchmark ?? 1;

  return (
    <div className="rounded-xl border bg-card p-4 shadow-sm" data-testid="equity-curve-chart">
      <div className="mb-3 flex items-center justify-between">
        <p className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
          Equity curve
        </p>
        <div className="flex items-center gap-3">
          <div className="flex items-center gap-2 text-[11px] text-muted-foreground">
            <span className="inline-block h-2 w-4 rounded-full bg-primary" />
            Strategy
            <span className="ml-2 inline-block h-2 w-4 rounded-full bg-muted-foreground/40" />
            Benchmark
          </div>
          <button
            type="button"
            onClick={onToggleScale}
            className="rounded border px-2 py-0.5 text-[10px] text-muted-foreground hover:bg-muted"
            aria-pressed={logScale}
          >
            {logScale ? "Linear" : "Log"}
          </button>
        </div>
      </div>
      <ResponsiveContainer width="100%" height={220}>
        <LineChart data={data} margin={{ top: 4, right: 8, bottom: 4, left: 0 }}>
          <CartesianGrid strokeDasharray="3 3" className="stroke-border/50" />
          <XAxis
            dataKey="date"
            tickFormatter={MONTH_FMT}
            tick={{ fontSize: 10 }}
            className="text-muted-foreground"
            minTickGap={40}
          />
          <YAxis
            scale={logScale ? "log" : "auto"}
            domain={logScale ? (["auto", "auto"] as [string, string]) : undefined}
            tick={{ fontSize: 10 }}
            tickFormatter={(v: number) => `${(v / 1_000_000).toFixed(1)}M`}
            width={52}
          />
          <Tooltip
            formatter={(value: number, name: string) => {
              const base = name === "strategy" ? stratStart : benchStart;
              const pct = ((value / base - 1) * 100).toFixed(1);
              return [`₹${(value / 1_000).toFixed(0)}K (+${pct}%)`, name === "strategy" ? "Strategy" : "Benchmark"];
            }}
            labelFormatter={MONTH_FMT}
            contentStyle={{ fontSize: 11 }}
          />
          <Line
            type="monotone"
            dataKey="strategy"
            stroke="hsl(var(--primary))"
            strokeWidth={2}
            dot={false}
          />
          <Line
            type="monotone"
            dataKey="benchmark"
            stroke="hsl(var(--muted-foreground))"
            strokeWidth={1.5}
            dot={false}
            opacity={0.6}
          />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Drawdown chart
// ---------------------------------------------------------------------------

function DrawdownChart({ equityCurve }: { equityCurve: Array<{ date: string; value: number }> }): React.ReactElement {
  // Compute drawdown at each point
  let peak = equityCurve[0]?.value ?? 0;
  const data = equityCurve.map((pt) => {
    if (pt.value > peak) peak = pt.value;
    const dd = peak > 0 ? ((pt.value - peak) / peak) * 100 : 0;
    return { date: pt.date, drawdown: dd };
  });

  return (
    <div className="rounded-xl border bg-card p-4 shadow-sm" data-testid="drawdown-chart">
      <p className="mb-3 text-xs font-semibold uppercase tracking-wider text-muted-foreground">
        Drawdown
      </p>
      <ResponsiveContainer width="100%" height={120}>
        <AreaChart data={data} margin={{ top: 4, right: 8, bottom: 4, left: 0 }}>
          <CartesianGrid strokeDasharray="3 3" className="stroke-border/50" />
          <XAxis dataKey="date" tickFormatter={MONTH_FMT} tick={{ fontSize: 10 }} minTickGap={40} />
          <YAxis tick={{ fontSize: 10 }} tickFormatter={(v: number) => `${v.toFixed(0)}%`} width={40} />
          <ReferenceLine y={0} stroke="hsl(var(--border))" />
          <Tooltip
            formatter={(v: number) => [`${v.toFixed(2)}%`, "Drawdown"]}
            labelFormatter={MONTH_FMT}
            contentStyle={{ fontSize: 11 }}
          />
          <Area
            type="monotone"
            dataKey="drawdown"
            stroke="hsl(var(--destructive))"
            fill="hsl(var(--destructive))"
            fillOpacity={0.15}
            strokeWidth={1.5}
            dot={false}
          />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Metrics row
// ---------------------------------------------------------------------------

type BacktestMetricsType = {
  cagr_pct: number;
  sharpe: number | null;
  max_drawdown_pct: number;
  calmar: number | null;
  turnover_pct: number | null;
  hit_rate_pct: number | null;
  n_unique_companies: number | null;
  total_return_pct: number;
};

function MetricsRow({ metrics }: { metrics: BacktestMetricsType }): React.ReactElement {
  const items: Array<{ label: string; value: string; positive?: boolean }> = [
    { label: "CAGR", value: `${metrics.cagr_pct >= 0 ? "+" : ""}${metrics.cagr_pct.toFixed(1)}%`, positive: metrics.cagr_pct >= 0 },
    { label: "Sharpe", value: metrics.sharpe !== null ? metrics.sharpe.toFixed(2) : "—" },
    { label: "Max DD", value: `${metrics.max_drawdown_pct.toFixed(1)}%`, positive: false },
    { label: "Calmar", value: metrics.calmar !== null ? metrics.calmar.toFixed(2) : "—" },
    { label: "Turnover", value: metrics.turnover_pct !== null ? `${metrics.turnover_pct.toFixed(0)}%` : "—" },
    { label: "Hit Rate", value: metrics.hit_rate_pct !== null ? `${metrics.hit_rate_pct.toFixed(1)}%` : "—" },
    { label: "# Companies", value: metrics.n_unique_companies !== null ? String(metrics.n_unique_companies) : "—" },
  ];

  return (
    <div
      className="grid grid-cols-4 gap-2 sm:grid-cols-7"
      data-testid="metrics-row"
    >
      {items.map((item) => (
        <div key={item.label} className="rounded-xl border bg-card p-3 text-center shadow-sm">
          <p className="text-[9px] font-semibold uppercase tracking-wider text-muted-foreground">
            {item.label}
          </p>
          <p
            className={cn(
              "mt-1 text-sm font-semibold tabular-nums",
              item.positive === true && "text-emerald-600 dark:text-emerald-400",
              item.positive === false && item.label === "Max DD" && "text-rose-600 dark:text-rose-400",
            )}
          >
            {item.value}
          </p>
        </div>
      ))}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Rebalance log (collapsible accordion)
// ---------------------------------------------------------------------------

type RebalanceType = {
  date: string;
  entered: Array<{ symbol: string; weight: number }>;
  exited: Array<{ symbol: string }>;
};

function RebalanceLog({ rebalances }: { rebalances: RebalanceType[] }): React.ReactElement {
  if (rebalances.length === 0) {
    return (
      <div className="rounded-xl border bg-card p-4 text-center text-xs text-muted-foreground">
        No rebalances recorded.
      </div>
    );
  }

  return (
    <Accordion type="single" collapsible className="rounded-xl border bg-card shadow-sm" data-testid="rebalance-log">
      <AccordionItem value="log" className="border-0">
        <AccordionTrigger className="px-4 py-3 text-xs font-semibold uppercase tracking-wider text-muted-foreground hover:no-underline">
          Position log · {rebalances.length} rebalances
        </AccordionTrigger>
        <AccordionContent>
          <div className="max-h-64 overflow-y-auto divide-y">
            {rebalances.slice(0, 50).map((r, i) => (
              <div key={i} className="px-4 py-3">
                <p className="mb-1 text-[11px] font-semibold text-foreground">
                  {r.date}
                </p>
                <div className="flex gap-4 flex-wrap">
                  {r.entered.length > 0 && (
                    <div>
                      <span className="text-[10px] font-medium text-emerald-600 uppercase">
                        Entered
                      </span>
                      <div className="mt-0.5 flex flex-wrap gap-1">
                        {r.entered.map((e) => (
                          <span key={e.symbol} className="rounded bg-emerald-50 dark:bg-emerald-900/20 px-1.5 py-0.5 text-[10px] font-medium text-emerald-700 dark:text-emerald-400">
                            {e.symbol} {(e.weight * 100).toFixed(1)}%
                          </span>
                        ))}
                      </div>
                    </div>
                  )}
                  {r.exited.length > 0 && (
                    <div>
                      <span className="text-[10px] font-medium text-rose-600 uppercase">
                        Exited
                      </span>
                      <div className="mt-0.5 flex flex-wrap gap-1">
                        {r.exited.map((e) => (
                          <span key={e.symbol} className="rounded bg-rose-50 dark:bg-rose-900/20 px-1.5 py-0.5 text-[10px] font-medium text-rose-700 dark:text-rose-400">
                            {e.symbol}
                          </span>
                        ))}
                      </div>
                    </div>
                  )}
                </div>
              </div>
            ))}
            {rebalances.length > 50 && (
              <p className="px-4 py-2 text-[10px] text-muted-foreground">
                Showing 50 of {rebalances.length} rebalances.
              </p>
            )}
          </div>
        </AccordionContent>
      </AccordionItem>
    </Accordion>
  );
}

// ---------------------------------------------------------------------------
// Audit appendix (first rebalance date universe)
// ---------------------------------------------------------------------------

function AuditAppendix({ audit, fields }: { audit: Record<string, unknown>[]; fields: string[] }): React.ReactElement {
  if (audit.length === 0) return <></>;

  return (
    <Accordion type="single" collapsible className="rounded-xl border bg-card shadow-sm" data-testid="audit-appendix">
      <AccordionItem value="audit" className="border-0">
        <AccordionTrigger className="px-4 py-3 text-xs font-semibold uppercase tracking-wider text-muted-foreground hover:no-underline">
          Audit appendix · {audit.length} companies at first rebalance
        </AccordionTrigger>
        <AccordionContent>
          <div className="overflow-x-auto">
            <table className="w-full text-[11px]">
              <thead>
                <tr className="border-b">
                  <th className="px-3 py-2 text-left font-semibold text-muted-foreground">Name</th>
                  {fields.slice(0, 6).map((f) => (
                    <th key={f} className="px-3 py-2 text-right font-semibold text-muted-foreground">
                      {f}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody className="divide-y">
                {audit.map((row, i) => (
                  <tr key={i} className="hover:bg-muted/30">
                    <td className="px-3 py-2 font-medium text-foreground">
                      {String(row.name ?? row.sc_id ?? "—")}
                    </td>
                    {fields.slice(0, 6).map((f) => {
                      const v = row[f];
                      return (
                        <td key={f} className="px-3 py-2 text-right tabular-nums text-muted-foreground">
                          {typeof v === "number" ? v.toFixed(2) : v !== undefined ? String(v) : "—"}
                        </td>
                      );
                    })}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </AccordionContent>
      </AccordionItem>
    </Accordion>
  );
}
