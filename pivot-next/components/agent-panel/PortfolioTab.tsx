"use client";

/**
 * PortfolioTab — read-only holdings + P&L view.
 *
 * Per docs/UI_TABS_V1.md §3. Reads from the legacy /portfolio/{summary,holdings}
 * endpoints (NOT under /api/*). Top metric strip + sortable holdings table.
 *
 * The "performance chart" promised in §3 is intentionally NOT included here:
 * the backend has no historical-portfolio-value endpoint yet, and faking
 * the data violates ARCHITECTURE.md §5.2 ("never fake data"). When that
 * endpoint lands, drop a small SVG line chart into the placeholder slot.
 */

import { useEffect, useMemo, useState } from "react";
import {
  AlertCircle,
  ArrowDown,
  ArrowUp,
  ArrowUpDown,
  RefreshCw,
  Wallet,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";
import {
  getPortfolioHoldings,
  getPortfolioSummary,
  type Holding,
  type PortfolioSummary,
} from "@/lib/api";
import { isError } from "@/lib/types";

type SortKey =
  | "tradingsymbol"
  | "quantity"
  | "average_price"
  | "last_price"
  | "pnl"
  | "day_change_percentage"
  | "value";

type SortDir = "asc" | "desc";

type FetchState =
  | { kind: "loading" }
  | { kind: "error"; message: string }
  | { kind: "ok"; summary: PortfolioSummary; holdings: Holding[] };

const INR = new Intl.NumberFormat("en-IN", {
  style: "currency",
  currency: "INR",
  maximumFractionDigits: 0,
});

const INR_SIGNED = new Intl.NumberFormat("en-IN", {
  style: "currency",
  currency: "INR",
  maximumFractionDigits: 0,
  signDisplay: "always",
});

function formatPct(n: number): string {
  const sign = n > 0 ? "+" : "";
  return `${sign}${n.toFixed(2)}%`;
}

function holdingValue(h: Holding): number {
  return h.last_price * h.quantity;
}

export function PortfolioTab(): React.ReactElement {
  const [state, setState] = useState<FetchState>({ kind: "loading" });
  const [sort, setSort] = useState<{ key: SortKey; dir: SortDir }>({
    key: "value",
    dir: "desc",
  });

  const load = (): void => {
    setState({ kind: "loading" });
    Promise.all([getPortfolioSummary(), getPortfolioHoldings()])
      .then(([sumRes, holdRes]) => {
        if (isError(sumRes)) {
          setState({ kind: "error", message: sumRes.error.message });
          return;
        }
        if (isError(holdRes)) {
          setState({ kind: "error", message: holdRes.error.message });
          return;
        }
        setState({
          kind: "ok",
          summary: sumRes.data,
          holdings: holdRes.data ?? [],
        });
      })
      .catch((err: unknown) => {
        const msg = err instanceof Error ? err.message : "Network error";
        setState({ kind: "error", message: msg });
      });
  };

  useEffect(() => {
    load();
  }, []);

  const sortedHoldings = useMemo(() => {
    if (state.kind !== "ok") return [];
    const items = [...state.holdings];
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
  }, [state, sort]);

  const cycleSort = (key: SortKey): void => {
    setSort((s) =>
      s.key === key
        ? { key, dir: s.dir === "asc" ? "desc" : "asc" }
        : { key, dir: key === "tradingsymbol" ? "asc" : "desc" },
    );
  };

  return (
    <div className="flex flex-col gap-6" data-testid="portfolio-tab">
      {state.kind === "loading" && <PortfolioLoading />}

      {state.kind === "error" && (
        <div
          role="alert"
          className="flex flex-col items-center justify-center py-12 text-center"
          data-testid="portfolio-error"
        >
          <AlertCircle className="mb-3 h-6 w-6 text-destructive" aria-hidden="true" />
          <p className="text-sm font-medium">Couldn&apos;t load portfolio</p>
          <p className="mt-1 text-xs text-muted-foreground">{state.message}</p>
          <Button variant="outline" size="sm" className="mt-4" onClick={load}>
            <RefreshCw className="mr-1.5 h-3.5 w-3.5" aria-hidden="true" />
            Retry
          </Button>
        </div>
      )}

      {state.kind === "ok" && (
        <>
          <MetricStrip summary={state.summary} />
          {state.holdings.length === 0 ? (
            <div
              className="flex flex-col items-center justify-center py-12 text-center rounded-xl border bg-card"
              data-testid="portfolio-empty"
            >
              <Wallet className="mb-3 h-8 w-8 text-muted-foreground" aria-hidden="true" />
              <p className="text-sm font-medium">No holdings yet</p>
              <p className="mt-1 text-xs text-muted-foreground">
                When you place your first trade, your positions will show here.
              </p>
            </div>
          ) : (
            <HoldingsTable
              holdings={sortedHoldings}
              sort={sort}
              onSort={cycleSort}
            />
          )}
          <PerformancePlaceholder />
        </>
      )}
    </div>
  );
}

// ── Metric strip ─────────────────────────────────────────────────────

function MetricStrip({ summary }: { summary: PortfolioSummary }): React.ReactElement {
  return (
    <div
      className="grid grid-cols-1 sm:grid-cols-3 gap-3 rounded-xl border bg-card p-5"
      data-testid="portfolio-metrics"
    >
      <Metric label="Portfolio value" value={INR.format(summary.total_value)} />
      <Metric
        label="Day P&L"
        value={INR_SIGNED.format(summary.day_pnl)}
        accent={summary.day_pnl >= 0 ? "up" : "down"}
      />
      <Metric
        label="Total P&L"
        value={INR_SIGNED.format(summary.total_pnl)}
        sub={formatPct(summary.total_pnl_pct)}
        accent={summary.total_pnl >= 0 ? "up" : "down"}
      />
    </div>
  );
}

function Metric({
  label,
  value,
  sub,
  accent,
}: {
  label: string;
  value: string;
  sub?: string;
  accent?: "up" | "down";
}): React.ReactElement {
  return (
    <div className="flex flex-col gap-1">
      <span className="text-xs uppercase tracking-wide text-muted-foreground">
        {label}
      </span>
      <span
        className={cn(
          "text-2xl font-semibold tabular-nums",
          accent === "up" && "text-emerald-600 dark:text-emerald-400",
          accent === "down" && "text-rose-600 dark:text-rose-400",
        )}
      >
        {value}
      </span>
      {sub && (
        <span
          className={cn(
            "text-xs tabular-nums",
            accent === "up" && "text-emerald-600/80 dark:text-emerald-400/80",
            accent === "down" && "text-rose-600/80 dark:text-rose-400/80",
          )}
        >
          {sub}
        </span>
      )}
    </div>
  );
}

// ── Holdings table ───────────────────────────────────────────────────

const COLUMNS: { key: SortKey; label: string; align: "left" | "right" }[] = [
  { key: "tradingsymbol", label: "Symbol", align: "left" },
  { key: "quantity", label: "Qty", align: "right" },
  { key: "average_price", label: "Avg", align: "right" },
  { key: "last_price", label: "LTP", align: "right" },
  { key: "pnl", label: "P&L", align: "right" },
  { key: "day_change_percentage", label: "Day %", align: "right" },
  { key: "value", label: "Value", align: "right" },
];

function HoldingsTable({
  holdings,
  sort,
  onSort,
}: {
  holdings: Holding[];
  sort: { key: SortKey; dir: SortDir };
  onSort: (key: SortKey) => void;
}): React.ReactElement {
  return (
    <div className="rounded-xl border bg-card shadow-sm overflow-hidden">
      <table className="w-full text-sm" data-testid="holdings-table">
        <thead className="bg-muted/30 text-xs uppercase tracking-wide text-muted-foreground">
          <tr>
            {COLUMNS.map((col) => {
              const active = sort.key === col.key;
              const Icon = !active
                ? ArrowUpDown
                : sort.dir === "asc"
                ? ArrowUp
                : ArrowDown;
              return (
                <th
                  key={col.key}
                  scope="col"
                  className={cn(
                    "px-4 py-3 font-medium",
                    col.align === "right" && "text-right",
                  )}
                >
                  <button
                    type="button"
                    onClick={() => onSort(col.key)}
                    className={cn(
                      "inline-flex items-center gap-1 hover:text-foreground transition-colors",
                      col.align === "right" && "ml-auto",
                      active && "text-foreground",
                    )}
                    aria-label={`Sort by ${col.label}`}
                    data-testid={`sort-${col.key}`}
                  >
                    {col.label}
                    <Icon className="h-3 w-3" aria-hidden="true" />
                  </button>
                </th>
              );
            })}
          </tr>
        </thead>
        <tbody className="divide-y">
          {holdings.map((h) => (
            <tr
              key={`${h.exchange}:${h.tradingsymbol}`}
              className="hover:bg-muted/20 transition-colors"
              data-testid={`holding-${h.tradingsymbol}`}
            >
              <td className="px-4 py-3 font-medium">{h.tradingsymbol}</td>
              <td className="px-4 py-3 text-right tabular-nums">{h.quantity}</td>
              <td className="px-4 py-3 text-right tabular-nums text-muted-foreground">
                {INR.format(h.average_price)}
              </td>
              <td className="px-4 py-3 text-right tabular-nums">
                {INR.format(h.last_price)}
              </td>
              <td
                className={cn(
                  "px-4 py-3 text-right tabular-nums",
                  h.pnl >= 0
                    ? "text-emerald-600 dark:text-emerald-400"
                    : "text-rose-600 dark:text-rose-400",
                )}
              >
                {INR_SIGNED.format(h.pnl)}
              </td>
              <td
                className={cn(
                  "px-4 py-3 text-right tabular-nums",
                  h.day_change_percentage >= 0
                    ? "text-emerald-600 dark:text-emerald-400"
                    : "text-rose-600 dark:text-rose-400",
                )}
              >
                {formatPct(h.day_change_percentage)}
              </td>
              <td className="px-4 py-3 text-right tabular-nums font-medium">
                {INR.format(holdingValue(h))}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// ── Performance chart placeholder ────────────────────────────────────
// TODO(day8-be): Replace with a Recharts LineChart wired to:
//   GET /api/portfolio/performance?period=1Y → { equity_curve: [{date, value}] }
//   Overlay: GET /api/quotes/index/^NSEI/history?period=1Y → benchmark series.
// Both en-route. Until they ship this placeholder is intentional (no fake data).

function PerformancePlaceholder(): React.ReactElement {
  return (
    <div
      className="rounded-xl border border-dashed bg-card/50 p-6 text-center"
      data-testid="performance-placeholder"
    >
      <p className="text-sm font-medium">Performance</p>
      <p className="mt-1 text-xs text-muted-foreground">
        Performance chart arriving in v1.1 — wired to{" "}
        <code className="font-mono text-[10px]">/api/portfolio/performance</code>.
      </p>
    </div>
  );
}

function PortfolioLoading(): React.ReactElement {
  return (
    <div className="flex flex-col gap-6" data-testid="portfolio-loading">
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-3 rounded-xl border bg-card p-5">
        {[0, 1, 2].map((i) => (
          <div key={i} className="flex flex-col gap-2">
            <Skeleton className="h-3 w-24" />
            <Skeleton className="h-7 w-32" />
          </div>
        ))}
      </div>
      <div className="rounded-xl border bg-card overflow-hidden">
        <Skeleton className="h-9 w-full" />
        {[0, 1, 2, 3, 4].map((i) => (
          <Skeleton key={i} className="h-12 w-full mt-px" />
        ))}
      </div>
    </div>
  );
}
