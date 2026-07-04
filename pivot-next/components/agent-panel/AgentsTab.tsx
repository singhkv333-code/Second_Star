"use client";

/**
 * AgentsTab — agent catalog grid.
 *
 * Three surfaces behind a top toggle:
 *   • Equity agents (workflows) — real summary header + per-agent cards whose
 *     sparkline/return/run-stats come from GET /api/workflows/{id}/performance
 *     (lazy-loaded per card). Delete via DELETE /api/workflows/{id}.
 *   • Strategies — the user's own equity/ETF baskets (built here via the
 *     EquityBasketBuilder, GET /strategies/baskets) PLUS their registered F&O
 *     strategies (GET /users/option-strategies), together in one surface.
 *   • My Views — the user's deployed view positions (the same ledger the
 *     Views tab opens), from GET /api/views/positions.
 *
 * All numbers are real. When an agent has no run/NAV history the card shows
 * "No runs yet" instead of a fabricated sparkline; empty sections show honest
 * empty states.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import {
  AlertCircle,
  Bot,
  Layers,
  MessageSquarePlus,
  MoreVertical,
  RefreshCw,
  Trash2,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { cn } from "@/lib/utils";
import {
  getWorkflow,
  listWorkflows,
  backtestDraftWorkflow,
  type BacktestDraftResponse,
} from "@/lib/api";
import {
  deleteWorkflow,
  getWorkflowPerformance,
  getWorkflowsSummary,
  listRegisteredOptionStrategies,
  withdrawRegisteredOptionStrategy,
  type RegisteredOptionStrategy,
  type WorkflowPerformance,
  type WorkflowsSummary,
} from "@/lib/agentsApi";
import { isError } from "@/lib/types";
import type { Workflow, WorkflowStatus, WorkflowSummary } from "@/lib/types";
import { AgentsSummaryHeader } from "./AgentsSummaryHeader";
import { EquityBasketsSection } from "./EquityBasketsSection";
import { MyViews } from "@/components/views/MyViews";

const BRAND_GREEN = "#4CAF50";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export type AgentsTabProps = {
  onOpenWorkflow: (workflow: Workflow) => void;
  /**
   * "Edit with chat" — hand the agent off to the chat surface with a
   * pre-written amendment prompt. The FULL workflow (incl. steps) is passed
   * so the chat surface can target this EXACT agent for amendment.
   */
  onEditWithChat?: (workflow: Workflow) => void;
  /** "Browse views" from the Views surface — jump to the Views tab. */
  onBrowseViews?: () => void;
};

type Surface = "equity" | "strategies" | "views";
type Filter = "all" | WorkflowStatus;

const FILTERS: { value: Filter; label: string }[] = [
  { value: "all", label: "All" },
  { value: "active", label: "Active" },
  { value: "paused", label: "Paused" },
  { value: "draft", label: "Draft" },
];

type FetchState =
  | { kind: "loading" }
  | { kind: "error"; message: string }
  | { kind: "ok"; items: WorkflowSummary[] };

type OptionsState =
  | { kind: "loading" }
  | { kind: "error"; message: string }
  | { kind: "ok"; items: RegisteredOptionStrategy[] };

// ---------------------------------------------------------------------------
// Category derivation
// ---------------------------------------------------------------------------

type Category = "STRATEGY" | "INCOME" | "RISK" | "RESEARCH" | "CASH";

function deriveCategory(wf: WorkflowSummary): Category {
  const text = `${wf.name} ${wf.description ?? ""}`.toLowerCase();
  if (text.includes("cash") || text.includes("sweep") || text.includes("fund")) return "CASH";
  if (text.includes("research") || text.includes("report") || text.includes("analyse") || text.includes("analyze")) return "RESEARCH";
  if (text.includes("risk") || text.includes("hedge")) return "RISK";
  if (text.includes("income") || text.includes("dividend") || text.includes("yield")) return "INCOME";
  return "STRATEGY";
}

function categoryLabel(cat: Category): string {
  const MAP: Record<Category, string> = {
    STRATEGY: "Agent",
    INCOME: "Income",
    RISK: "Risk",
    RESEARCH: "Research",
    CASH: "Fund Management",
  };
  return MAP[cat];
}

function categoryChipClass(cat: Category): string {
  switch (cat) {
    case "CASH":
      return "bg-sky-100 text-sky-700 dark:bg-sky-500/15 dark:text-sky-300";
    case "RESEARCH":
      return "bg-violet-100 text-violet-700 dark:bg-violet-500/15 dark:text-violet-300";
    case "RISK":
      return "bg-rose-100 text-rose-700 dark:bg-rose-500/15 dark:text-rose-300";
    case "INCOME":
      return "bg-emerald-100 text-emerald-700 dark:bg-emerald-500/15 dark:text-emerald-300";
    default:
      return "bg-[#1b7cc7]/10 text-[#1b7cc7] dark:bg-[#1b7cc7]/20 dark:text-[#60b3e8]";
  }
}

function deriveUniverse(wf: WorkflowSummary): string {
  const text = `${wf.name} ${wf.description ?? ""}`.toUpperCase();
  const nseMatch = text.match(/\b([A-Z]{2,12})\b/g);
  const knownIndices = ["NIFTY", "SENSEX", "BANKNIFTY"];
  const NON_TICKER_WORDS = new Set([
    "EVERY", "WEEKDAY", "WEEKLY", "MONTHLY", "DAILY", "MONDAY", "TUESDAY",
    "WEDNESDAY", "THURSDAY", "FRIDAY", "SATURDAY", "SUNDAY",
    "MARKET", "BUYING", "POWER", "EMAIL", "PRICE", "FETCH", "NOTIFY",
    "MORNING", "AFTERNOON", "EVENING", "NIGHT",
    "AT", "ON", "IF", "THE", "AND", "FOR", "BUY", "SELL", "SIP",
    "LIMIT", "ORDER", "QUANTITY", "AMOUNT",
    "AM", "PM", "IST", "UTC",
  ]);
  for (const m of nseMatch ?? []) {
    if (knownIndices.includes(m)) return "NIFTY 50";
    if (m.length >= 3 && m.length <= 10 && !NON_TICKER_WORDS.has(m)) {
      return m;
    }
  }
  return "NSE 500";
}

function deriveCadence(wf: WorkflowSummary): string {
  if (wf.next_run_at) return "scheduled";
  if (wf.last_run_at) return "manual";
  const text = `${wf.name} ${wf.description ?? ""}`.toLowerCase();
  if (text.includes("weekday") || text.includes("daily")) return "weekday";
  if (text.includes("weekly") || text.includes("monday")) return "weekly";
  if (text.includes("monthly")) return "monthly";
  if (text.includes("real-time") || text.includes("price") || text.includes("indicator")) return "real-time";
  return "manual";
}

// ---------------------------------------------------------------------------
// AgentsTab
// ---------------------------------------------------------------------------

export function AgentsTab({
  onOpenWorkflow,
  onEditWithChat,
  onBrowseViews,
}: AgentsTabProps): React.ReactElement {
  const [surface, setSurface] = useState<Surface>("equity");
  const [filter, setFilter] = useState<Filter>("all");
  const [state, setState] = useState<FetchState>({ kind: "loading" });
  const [openingId, setOpeningId] = useState<string | null>(null);

  // Summary header — loaded once (independent of the status filter).
  const [summary, setSummary] = useState<WorkflowsSummary | null>(null);
  const [summaryLoading, setSummaryLoading] = useState(true);

  // Options strategies surface.
  const [optionsState, setOptionsState] = useState<OptionsState>({ kind: "loading" });
  const [optionsLoaded, setOptionsLoaded] = useState(false);

  // Delete-in-flight ids (both surfaces) so the kebab disables + the card dims.
  const [deletingId, setDeletingId] = useState<string | null>(null);

  const loadSummary = useCallback((): void => {
    setSummaryLoading(true);
    getWorkflowsSummary()
      .then((result) => {
        if (isError(result)) {
          setSummary(null);
          return;
        }
        setSummary(result.data);
      })
      .catch(() => setSummary(null))
      .finally(() => setSummaryLoading(false));
  }, []);

  const load = useCallback((f: Filter): void => {
    setState({ kind: "loading" });
    const statusParam = f === "all" ? ["active", "paused", "draft"] : [f];
    listWorkflows({ status: statusParam as WorkflowStatus[], limit: 50 })
      .then((result) => {
        if (isError(result)) {
          setState({ kind: "error", message: result.error.message });
          return;
        }
        setState({ kind: "ok", items: result.data.items });
      })
      .catch((err: unknown) => {
        const msg = err instanceof Error ? err.message : "Network error";
        setState({ kind: "error", message: msg });
      });
  }, []);

  const loadOptions = useCallback((): void => {
    setOptionsState({ kind: "loading" });
    listRegisteredOptionStrategies()
      .then((result) => {
        if (isError(result)) {
          setOptionsState({ kind: "error", message: result.error.message });
          return;
        }
        setOptionsState({ kind: "ok", items: result.data.strategies ?? [] });
      })
      .catch((err: unknown) => {
        const msg = err instanceof Error ? err.message : "Network error";
        setOptionsState({ kind: "error", message: msg });
      });
  }, []);

  useEffect(() => {
    loadSummary();
  }, [loadSummary]);

  useEffect(() => {
    load(filter);
  }, [filter, load]);

  // Lazy-load registered option strategies the first time the user opens the
  // Strategies surface (equity baskets load themselves inside their section).
  useEffect(() => {
    if (surface === "strategies" && !optionsLoaded) {
      setOptionsLoaded(true);
      loadOptions();
    }
  }, [surface, optionsLoaded, loadOptions]);

  const handleSelect = (id: string): void => {
    setOpeningId(id);
    getWorkflow(id)
      .then((result) => {
        if (isError(result)) return;
        onOpenWorkflow(result.data);
      })
      .catch(() => {})
      .finally(() => setOpeningId(null));
  };

  const handleEditWithChat = (wf: WorkflowSummary): void => {
    if (!onEditWithChat) return;
    // Fetch the full workflow (incl. steps) so the chat surface can target
    // THIS exact agent for amendment, not guess from the name.
    getWorkflow(wf.id)
      .then((result) => {
        if (isError(result)) return;
        onEditWithChat(result.data);
      })
      .catch(() => {});
  };

  const handleDeleteWorkflow = (id: string): void => {
    setDeletingId(id);
    deleteWorkflow(id)
      .then((result) => {
        if (isError(result)) return;
        // Optimistically drop from the grid, then refresh the summary counts.
        setState((prev) =>
          prev.kind === "ok"
            ? { kind: "ok", items: prev.items.filter((w) => w.id !== id) }
            : prev,
        );
        loadSummary();
      })
      .catch(() => {})
      .finally(() => setDeletingId((cur) => (cur === id ? null : cur)));
  };

  const handleWithdrawOption = (id: string): void => {
    setDeletingId(id);
    withdrawRegisteredOptionStrategy(id)
      .then((result) => {
        if (isError(result)) return;
        setOptionsState((prev) =>
          prev.kind === "ok"
            ? { kind: "ok", items: prev.items.filter((s) => s.id !== id) }
            : prev,
        );
      })
      .catch(() => {})
      .finally(() => setDeletingId((cur) => (cur === id ? null : cur)));
  };

  return (
    <div className="agents-tab flex flex-col" style={{ gap: 18 }} data-testid="agents-tab">
      {/* Page heading */}
      <h1
        className="q-serif"
        style={{
          fontSize: 22,
          letterSpacing: "-0.025em",
          color: "var(--text-primary)",
          margin: 0,
        }}
      >
        Active Agents
      </h1>

      {/* Equity / Options toggle */}
      <SurfaceToggle value={surface} onChange={setSurface} />

      {surface === "equity" ? (
        <>
          {/* Summary header — real data */}
          <AgentsSummaryHeader summary={summary} isLoading={summaryLoading} />

          {/* Status filter chips */}
          <div
            className="flex flex-wrap items-center"
            style={{ gap: 6 }}
            role="group"
            aria-label="Filter agents"
          >
            {FILTERS.map((f) => {
              const active = filter === f.value;
              return (
                <button
                  key={f.value}
                  type="button"
                  onClick={() => setFilter(f.value)}
                  aria-pressed={active}
                  data-testid={`filter-${f.value}`}
                  style={{
                    padding: "6px 12px",
                    background: active ? "var(--text-primary)" : "transparent",
                    border: `1px solid ${active ? "var(--text-primary)" : "var(--glass-border)"}`,
                    borderRadius: "var(--radius-pill)",
                    color: active ? "var(--bg-primary)" : "var(--text-secondary)",
                    fontFamily: "var(--font-ui)",
                    fontSize: 12,
                    fontWeight: 500,
                    cursor: "pointer",
                    transition: "all 0.2s var(--ease-quartr)",
                  }}
                >
                  {f.label}
                </button>
              );
            })}
          </div>

          {state.kind === "loading" && <AgentsGridSkeleton />}

          {state.kind === "error" && (
            <ErrorPanel message={state.message} onRetry={() => load(filter)} testId="agents-error" />
          )}

          {state.kind === "ok" && state.items.length === 0 && (
            <div
              className="flex flex-col items-center justify-center py-12 text-center"
              data-testid="agents-empty"
            >
              <Bot className="mb-3 h-8 w-8 text-muted-foreground" aria-hidden="true" />
              <p className="text-sm font-medium">No agents yet</p>
              <p className="mt-1 text-xs text-muted-foreground">
                Start a chat to propose one.
              </p>
            </div>
          )}

          {state.kind === "ok" && state.items.length > 0 && (
            <div
              className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4"
              data-testid="agents-list"
              role="list"
            >
              {state.items.map((wf) => (
                <div key={wf.id} role="listitem" className="h-full">
                  <AgentMiniCard
                    workflow={wf}
                    isOpening={openingId === wf.id}
                    isDeleting={deletingId === wf.id}
                    onSelect={() => handleSelect(wf.id)}
                    onEditWithChat={
                      onEditWithChat ? () => handleEditWithChat(wf) : undefined
                    }
                    onDelete={() => handleDeleteWorkflow(wf.id)}
                  />
                </div>
              ))}
            </div>
          )}
        </>
      ) : surface === "strategies" ? (
        // Strategies = equity/ETF baskets (the ones we build) + registered
        // option strategies, together in one place.
        <div className="flex flex-col" style={{ gap: 32 }}>
          <EquityBasketsSection />
          <OptionsStrategiesSection
            state={optionsState}
            deletingId={deletingId}
            onRetry={loadOptions}
            onWithdraw={handleWithdrawOption}
          />
        </div>
      ) : (
        // The user's deployed views — same ledger the Views tab opens.
        <MyViews embedded onBrowse={onBrowseViews} />
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// SurfaceToggle — Equity agents · Strategies · My Opinions
// ---------------------------------------------------------------------------

function SurfaceToggle({
  value,
  onChange,
}: {
  value: Surface;
  onChange: (s: Surface) => void;
}): React.ReactElement {
  const OPTIONS: { key: Surface; label: string }[] = [
    { key: "equity", label: "Equity agents" },
    { key: "strategies", label: "Strategies" },
    { key: "views", label: "My Opinions" },
  ];
  return (
    <div
      role="tablist"
      aria-label="Agent surface"
      data-testid="agents-surface-toggle"
      className="inline-flex items-center self-start rounded-full p-1"
      style={{ background: "var(--bg-elevated)", border: "1px solid var(--glass-border)" }}
    >
      {OPTIONS.map((o) => {
        const active = value === o.key;
        return (
          <button
            key={o.key}
            type="button"
            role="tab"
            aria-selected={active}
            data-testid={`surface-${o.key}`}
            onClick={() => onChange(o.key)}
            className="rounded-full transition-colors"
            style={{
              padding: "6px 16px",
              fontFamily: "var(--font-ui)",
              fontSize: 12.5,
              fontWeight: 600,
              letterSpacing: "-0.01em",
              cursor: "pointer",
              border: "none",
              background: active ? "var(--text-primary)" : "transparent",
              color: active ? "var(--bg-primary)" : "var(--text-secondary)",
            }}
          >
            {o.label}
          </button>
        );
      })}
    </div>
  );
}

// ---------------------------------------------------------------------------
// AgentMiniCard — equity workflow card, now driven by real performance data.
// ---------------------------------------------------------------------------

function AgentMiniCard({
  workflow,
  isOpening,
  isDeleting,
  onSelect,
  onEditWithChat,
  onDelete,
}: {
  workflow: WorkflowSummary;
  isOpening: boolean;
  isDeleting: boolean;
  onSelect: () => void;
  onEditWithChat?: () => void;
  onDelete: () => void;
}): React.ReactElement {
  const category = deriveCategory(workflow);
  const universe = deriveUniverse(workflow);
  const cadence = deriveCadence(workflow);
  const description = workflow.description ?? null;

  const [confirmOpen, setConfirmOpen] = useState(false);

  // Per-card performance — lazy-loaded once the card mounts.
  const [perf, setPerf] = useState<WorkflowPerformance | null>(null);
  const [perfLoading, setPerfLoading] = useState(true);
  const mountedRef = useRef(true);

  useEffect(() => {
    mountedRef.current = true;
    setPerfLoading(true);
    getWorkflowPerformance(workflow.id)
      .then((result) => {
        if (!mountedRef.current) return;
        if (isError(result)) {
          setPerf(null);
          return;
        }
        setPerf(result.data);
      })
      .catch(() => {
        if (mountedRef.current) setPerf(null);
      })
      .finally(() => {
        if (mountedRef.current) setPerfLoading(false);
      });
    return () => {
      mountedRef.current = false;
    };
  }, [workflow.id]);

  // Backtested-results fallback — only fetched once the live-performance check
  // above resolves AND turns out to have no real NAV chart. A workflow that's
  // been live long enough to have its own NAV history always shows THAT
  // (never overshadowed by a backtest); one that hasn't yet still gives the
  // user something real to look at instead of a bare "No runs yet" box.
  const perfHasChart = (perf?.has_data ?? false) && (perf?.series?.length ?? 0) >= 2;
  const [backtest, setBacktest] = useState<BacktestDraftResponse | null>(null);
  const [backtestLoading, setBacktestLoading] = useState(false);

  useEffect(() => {
    if (perfLoading || perfHasChart) return;
    let cancelled = false;
    setBacktestLoading(true);
    getWorkflow(workflow.id)
      .then((wfResult) => {
        if (cancelled) return null;
        if (isError(wfResult)) return null;
        return backtestDraftWorkflow({
          name: wfResult.data.name,
          description: wfResult.data.description,
          steps: wfResult.data.steps,
          period: "1y",
        });
      })
      .then((btResult) => {
        if (cancelled) return;
        setBacktest(btResult && !isError(btResult) ? btResult.data : null);
      })
      .catch(() => {
        if (!cancelled) setBacktest(null);
      })
      .finally(() => {
        if (!cancelled) setBacktestLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [workflow.id, perfLoading, perfHasChart]);

  const handleKey = (e: React.KeyboardEvent<HTMLDivElement>): void => {
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      onSelect();
    }
  };

  return (
    <div
      data-testid={`agent-card-${workflow.id}`}
      role="button"
      tabIndex={0}
      aria-label={`Open agent: ${workflow.name}`}
      onClick={onSelect}
      onKeyDown={handleKey}
      className={cn(
        "agents-mini-card group flex h-full cursor-pointer flex-col gap-4 rounded-2xl border border-border/50 bg-card px-5 py-5",
        "shadow-[0_1px_2px_rgba(15,23,42,0.04),0_8px_20px_-12px_rgba(15,23,42,0.08)]",
        "transition-colors hover:border-border focus:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2",
        (isOpening || isDeleting) && "opacity-70 pointer-events-none",
      )}
    >
      {/* Header: category chip + status pill + kebab */}
      <div className="flex items-center justify-between gap-3">
        <span
          className={cn(
            "inline-flex items-center rounded-md px-2.5 py-0.5 text-[11px] font-medium tracking-tight",
            categoryChipClass(category),
          )}
        >
          {categoryLabel(category)}
        </span>
        <div className="flex items-center gap-1.5">
          <WorkflowStatusPill status={workflow.status} />
          <CardKebab
            label={`Agent ${workflow.name} actions`}
            disabled={isDeleting}
            onDelete={() => setConfirmOpen(true)}
          />
        </div>
      </div>

      {/* Title */}
      <h3 className="m-0 line-clamp-2 text-[20px] leading-[1.2] font-semibold tracking-tight text-foreground">
        {workflow.name}
      </h3>

      {/* Performance — real NAV sparkline + return, or honest "No runs yet". */}
      <PerformanceBlock
        perf={perf}
        isLoading={perfLoading}
        backtest={backtest}
        backtestLoading={backtestLoading}
      />

      {/* KV rows */}
      <div className="mt-auto flex flex-col gap-1.5 border-t border-border/40 pt-3 text-[12px]">
        <KvRow label="Method" value={description ?? "Manual workflow"} />
        <KvRow label="Universe" value={universe} />
        <KvRow label="Cadence" value={cadence} />
      </div>

      {/* Edit with chat */}
      {onEditWithChat && (
        <button
          type="button"
          onClick={(e) => {
            e.stopPropagation();
            onEditWithChat();
          }}
          onKeyDown={(e) => e.stopPropagation()}
          data-testid={`agent-edit-with-chat-${workflow.id}`}
          aria-label={`Edit ${workflow.name} with chat`}
          className={cn(
            "inline-flex items-center justify-center gap-1.5 rounded-lg border border-border/60 bg-background/60 px-3 py-2 text-[12px] font-medium text-foreground/80",
            "transition-colors hover:border-border hover:bg-muted hover:text-foreground",
            "focus:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-1",
          )}
        >
          <MessageSquarePlus className="h-3.5 w-3.5" aria-hidden="true" />
          Edit with chat
        </button>
      )}

      {/* Hidden interaction sentinel — preserves the agent-row-{id} testid. */}
      <button
        type="button"
        onClick={(e) => {
          e.stopPropagation();
          onSelect();
        }}
        disabled={isOpening}
        aria-label={`View agent: ${workflow.name}`}
        data-testid={`agent-row-${workflow.id}`}
        className="sr-only"
      >
        View agent
      </button>

      <DeleteConfirmDialog
        open={confirmOpen}
        onOpenChange={setConfirmOpen}
        title={`Delete “${workflow.name}”?`}
        description="This permanently removes the agent and all its run history. Paper-trade fills already booked are kept. This can't be undone."
        confirmLabel="Delete agent"
        onConfirm={onDelete}
      />
    </div>
  );
}

// ---------------------------------------------------------------------------
// PerformanceBlock — real NAV sparkline + return / runs / success-rate row.
// has_data false → "No runs yet" (no fabricated chart).
// ---------------------------------------------------------------------------

// Two equal points → NavSparkline renders a flat line. Used ONLY as a
// visual placeholder (dashed + neutral-gray, never profit/loss colored) for
// cards with no live data and no eligible backtest — never mistaken for a
// real (even flat) result.
const FLAT_PLACEHOLDER_SERIES: { date: string; nav: number }[] = [
  { date: "", nav: 0 },
  { date: "", nav: 0 },
];

function PerformanceBlock({
  perf,
  isLoading,
  backtest,
  backtestLoading,
}: {
  perf: WorkflowPerformance | null;
  isLoading: boolean;
  backtest: BacktestDraftResponse | null;
  backtestLoading: boolean;
}): React.ReactElement {
  if (isLoading) {
    return <Skeleton className="h-[56px] w-full rounded-md" style={{ marginTop: 14 }} />;
  }

  const hasData = perf?.has_data ?? false;
  const series = perf?.series ?? [];

  // No real live NAV yet — fall back to a backtest of the SAME steps over
  // the last year, clearly labelled so it's never mistaken for live
  // performance. Ineligible shapes (event triggers, one-off manual orders,
  // etc.) or a still-loading fetch keep the honest "No runs yet" state.
  if (!hasData || series.length < 2) {
    if (backtestLoading) {
      return <Skeleton className="h-[56px] w-full rounded-md" style={{ marginTop: 14 }} />;
    }
    if (backtest?.eligible) {
      const btSeries = backtest.equity_curve.map((p) => ({ date: p.t, nav: p.v }));
      const btPositive = backtest.metrics.total_return_pct >= 0;
      return (
        <div className="flex flex-col" style={{ marginTop: 14, gap: 8 }}>
          <div className="flex items-center justify-between gap-2">
            <span
              className="inline-flex items-center rounded-md px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wide"
              style={{ background: "var(--bg-elevated)", color: "var(--text-tertiary)" }}
              title="Simulated over historical prices — not live trading results"
            >
              Backtested · 1y
            </span>
          </div>
          <NavSparkline series={btSeries} positive={btPositive} dashed />
          <div className="flex items-center justify-between gap-2 text-[11px]">
            <span
              className="tabular-nums"
              style={{ fontWeight: 600, color: btPositive ? "var(--color-profit)" : "var(--color-loss)" }}
            >
              {btPositive ? "+" : "−"}
              {Math.abs(backtest.metrics.total_return_pct).toFixed(1)}%
            </span>
            <span className="text-muted-foreground tabular-nums">
              {backtest.metrics.n_trades} simulated trade{backtest.metrics.n_trades === 1 ? "" : "s"}
            </span>
          </div>
        </div>
      );
    }
    return (
      <div className="flex flex-col" style={{ marginTop: 14, gap: 8 }} data-testid="agent-no-runs">
        <NavSparkline series={FLAT_PLACEHOLDER_SERIES} positive={false} dashed neutral />
        <div className="flex items-center justify-between gap-2 text-[11px]">
          <span className="text-muted-foreground">No runs yet</span>
          {perf && perf.run_count > 0 && series.length < 2 && (
            <span className="text-muted-foreground tabular-nums">
              {perf.run_count} run{perf.run_count === 1 ? "" : "s"} · no NAV history
            </span>
          )}
        </div>
      </div>
    );
  }

  const returnPct = perf?.return_pct ?? null;
  const positive = (returnPct ?? 0) >= 0;

  return (
    <div className="flex flex-col" style={{ marginTop: 14, gap: 8 }}>
      <NavSparkline series={series} positive={positive} />
      <div className="flex items-center justify-between gap-2 text-[11px]">
        <span className="tabular-nums" style={{ fontWeight: 600, color: positive ? "var(--color-profit)" : "var(--color-loss)" }}>
          {returnPct === null
            ? "—"
            : `${returnPct >= 0 ? "+" : "−"}${Math.abs(returnPct).toFixed(1)}%`}
        </span>
        <span className="text-muted-foreground tabular-nums">
          {perf?.run_count ?? 0} run{(perf?.run_count ?? 0) === 1 ? "" : "s"}
          {perf?.success_rate !== null && perf?.success_rate !== undefined && (
            <> · {perf.success_rate.toFixed(0)}% ok</>
          )}
        </span>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// NavSparkline — REAL series → path. No seeding, no synthesis.
// ---------------------------------------------------------------------------

function NavSparkline({
  series,
  positive,
  dashed = false,
  neutral = false,
}: {
  series: { date: string; nav: number }[];
  positive: boolean;
  /** Backtested (not live) results render with a dashed stroke so the two
   *  are never visually confused at a glance. */
  dashed?: boolean;
  /** No real/backtested data at all — a flat muted-gray placeholder line
   *  instead of the profit/loss green or red, so it's never mistaken for
   *  an actual (even zero) result. */
  neutral?: boolean;
}): React.ReactElement {
  const W = 280;
  const H = 56;
  const vals = series.map((p) => p.nav);
  const min = Math.min(...vals);
  const max = Math.max(...vals);
  const span = Math.max(1e-9, max - min);
  const n = vals.length;

  const xAt = (i: number): number => (n <= 1 ? 0 : (i / (n - 1)) * W);
  const yAt = (val: number): number => H - ((val - min) / span) * (H - 6) - 3;

  const linePath = vals
    .map((val, i) => `${i === 0 ? "M" : "L"} ${xAt(i).toFixed(1)} ${yAt(val).toFixed(1)}`)
    .join(" ");
  const areaPath = `${linePath} L ${W} ${H} L 0 ${H} Z`;
  const gradId = `nav-spark-${Math.round(min)}-${n}`;
  const stroke = neutral ? "var(--text-tertiary)" : positive ? "var(--color-profit)" : "var(--color-loss)";
  const fillTop = neutral ? "rgba(148, 163, 184, 0.16)" : positive ? "rgba(16, 185, 129, 0.22)" : "rgba(239, 68, 68, 0.22)";
  const fillBot = neutral ? "rgba(148, 163, 184, 0)" : positive ? "rgba(16, 185, 129, 0)" : "rgba(239, 68, 68, 0)";

  return (
    <svg
      viewBox={`0 0 ${W} ${H}`}
      width="100%"
      height={H}
      preserveAspectRatio="none"
      style={{ display: "block" }}
      role="img"
      aria-label="Agent NAV trend"
    >
      <defs>
        <linearGradient id={gradId} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor={fillTop} />
          <stop offset="100%" stopColor={fillBot} />
        </linearGradient>
      </defs>
      <path d={areaPath} fill={`url(#${gradId})`} />
      <path
        d={linePath}
        fill="none"
        stroke={stroke}
        strokeWidth="1.6"
        strokeLinecap="round"
        strokeLinejoin="round"
        strokeDasharray={dashed ? "4 3" : undefined}
        vectorEffect="non-scaling-stroke"
      />
    </svg>
  );
}

// ---------------------------------------------------------------------------
// Options strategies section
// ---------------------------------------------------------------------------

function OptionsStrategiesSection({
  state,
  deletingId,
  onRetry,
  onWithdraw,
}: {
  state: OptionsState;
  deletingId: string | null;
  onRetry: () => void;
  onWithdraw: (id: string) => void;
}): React.ReactElement {
  return (
    <div className="flex flex-col gap-4" data-testid="options-section">
      <h2
        className="q-serif m-0"
        style={{ fontSize: 16, letterSpacing: "-0.02em", color: "var(--text-primary)" }}
      >
        Options strategies
      </h2>

      {state.kind === "loading" && <AgentsGridSkeleton />}

      {state.kind === "error" && (
        <ErrorPanel message={state.message} onRetry={onRetry} testId="options-error" />
      )}

      {state.kind === "ok" && state.items.length === 0 && (
        <div
          className="flex flex-col items-center justify-center py-12 text-center"
          data-testid="options-empty"
        >
          <Layers className="mb-3 h-8 w-8 text-muted-foreground" aria-hidden="true" />
          <p className="text-sm font-medium">No options strategies yet</p>
          <p className="mt-1 text-xs text-muted-foreground">
            Build or register an F&amp;O strategy in chat and it&apos;ll appear here.
          </p>
        </div>
      )}

      {state.kind === "ok" && state.items.length > 0 && (
        <div
          className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4"
          data-testid="options-list"
          role="list"
        >
          {state.items.map((s) => (
            <div key={s.id} role="listitem" className="h-full">
              <OptionStrategyCard
                strategy={s}
                isDeleting={deletingId === s.id}
                onWithdraw={() => onWithdraw(s.id)}
              />
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function prettyTemplate(template: string): string {
  return template
    .split(/[_\s-]+/)
    .filter(Boolean)
    .map((w) => w.charAt(0).toUpperCase() + w.slice(1))
    .join(" ");
}

function OptionStatusPill({ status }: { status: string }): React.ReactElement {
  const s = status.toLowerCase();
  if (s === "active" || s === "registered" || s === "open") {
    return (
      <span
        className="inline-flex items-center gap-1.5 rounded-full border bg-transparent px-2.5 py-1 text-[11px] font-medium"
        style={{ borderColor: BRAND_GREEN, color: BRAND_GREEN }}
      >
        <span
          aria-hidden={true}
          className="h-1.5 w-1.5 rounded-full"
          style={{ background: BRAND_GREEN, animation: "pulse-quartr 1.6s ease-in-out infinite" }}
        />
        {status.charAt(0).toUpperCase() + status.slice(1)}
      </span>
    );
  }
  return (
    <span
      className="inline-flex items-center gap-1.5 rounded-full bg-muted px-2.5 py-1 text-[11px] font-medium text-muted-foreground"
    >
      <span aria-hidden={true} className="h-1.5 w-1.5 rounded-full bg-muted-foreground/60" />
      {status.charAt(0).toUpperCase() + status.slice(1)}
    </span>
  );
}

function formatInrCompact(amount: number | null): string {
  if (amount === null) return "—";
  const abs = Math.abs(Math.round(amount));
  const sign = amount < 0 ? "−" : "";
  return `${sign}₹${abs.toLocaleString("en-IN", { maximumFractionDigits: 0 })}`;
}

function OptionStrategyCard({
  strategy,
  isDeleting,
  onWithdraw,
}: {
  strategy: RegisteredOptionStrategy;
  isDeleting: boolean;
  onWithdraw: () => void;
}): React.ReactElement {
  const [confirmOpen, setConfirmOpen] = useState(false);
  const expiryLabel = (() => {
    const d = new Date(strategy.expiry);
    if (Number.isNaN(d.getTime())) return strategy.expiry;
    return d.toLocaleDateString("en-IN", { day: "numeric", month: "short", year: "numeric" });
  })();

  return (
    <div
      data-testid={`option-strategy-card-${strategy.id}`}
      className={cn(
        "group flex h-full flex-col gap-4 rounded-2xl border border-border/50 bg-card px-5 py-5",
        "shadow-[0_1px_2px_rgba(15,23,42,0.04),0_8px_20px_-12px_rgba(15,23,42,0.08)]",
        "transition-colors hover:border-border",
        isDeleting && "opacity-70 pointer-events-none",
      )}
    >
      {/* Header: template chip + status pill + kebab */}
      <div className="flex items-center justify-between gap-3">
        <span className="inline-flex items-center rounded-md bg-[#1b7cc7]/10 px-2.5 py-0.5 text-[11px] font-medium tracking-tight text-[#1b7cc7] dark:bg-[#1b7cc7]/20 dark:text-[#60b3e8]">
          {strategy.book === "paper" ? "Paper" : "Live"}
        </span>
        <div className="flex items-center gap-1.5">
          <OptionStatusPill status={strategy.status} />
          <CardKebab
            label={`Strategy ${strategy.underlying} actions`}
            disabled={isDeleting}
            onDelete={() => setConfirmOpen(true)}
            deleteLabel="Withdraw"
          />
        </div>
      </div>

      {/* Title */}
      <div className="flex flex-col gap-0.5">
        <h3 className="m-0 text-[20px] leading-[1.2] font-semibold tracking-tight text-foreground">
          {strategy.underlying}
        </h3>
        <span className="text-[12px] text-muted-foreground">
          {prettyTemplate(strategy.template)} · {strategy.qty_lots} lot{strategy.qty_lots === 1 ? "" : "s"}
        </span>
      </div>

      {/* Metrics grid — only real values; nulls render em-dash. */}
      <div className="grid grid-cols-2 gap-x-3 gap-y-2 text-[12px]">
        <Metric label="Net premium" value={formatInrCompact(strategy.net_premium)} />
        <Metric
          label="PoP"
          value={strategy.pop === null ? "—" : `${(strategy.pop * 100).toFixed(0)}%`}
        />
        <Metric label="Max profit" value={formatInrCompact(strategy.max_profit)} tone="profit" />
        <Metric label="Max loss" value={formatInrCompact(strategy.max_loss)} tone="loss" />
      </div>

      {/* Legs */}
      <div className="mt-auto flex flex-col gap-1.5 border-t border-border/40 pt-3 text-[12px]">
        <span className="text-muted-foreground/70">
          Expiry · {expiryLabel}
        </span>
        <div className="flex flex-wrap gap-1.5">
          {strategy.legs.length === 0 ? (
            <span className="text-muted-foreground/70">No legs recorded</span>
          ) : (
            strategy.legs.map((leg, i) => (
              <span
                key={`${leg.option_type}-${leg.strike}-${i}`}
                className={cn(
                  "inline-flex items-center rounded-md px-2 py-0.5 text-[11px] font-medium tabular-nums",
                  leg.side === "BUY"
                    ? "bg-emerald-100 text-emerald-700 dark:bg-emerald-500/15 dark:text-emerald-300"
                    : "bg-rose-100 text-rose-700 dark:bg-rose-500/15 dark:text-rose-300",
                )}
                title={leg.tradingsymbol ?? undefined}
              >
                {leg.side === "BUY" ? "+" : "−"}
                {leg.strike}
                {leg.option_type}
              </span>
            ))
          )}
        </div>
      </div>

      <DeleteConfirmDialog
        open={confirmOpen}
        onOpenChange={setConfirmOpen}
        title={`Withdraw ${strategy.underlying} ${prettyTemplate(strategy.template)}?`}
        description="This withdraws the registered strategy. It will no longer be tracked here."
        confirmLabel="Withdraw"
        onConfirm={onWithdraw}
      />
    </div>
  );
}

function Metric({
  label,
  value,
  tone = "neutral",
}: {
  label: string;
  value: string;
  tone?: "neutral" | "profit" | "loss";
}): React.ReactElement {
  const color =
    tone === "profit" ? "var(--color-profit)" : tone === "loss" ? "var(--color-loss)" : "var(--text-primary)";
  return (
    <div className="flex flex-col gap-0.5">
      <span className="text-muted-foreground/70">{label}</span>
      <span className="tabular-nums font-semibold" style={{ color }}>
        {value}
      </span>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Shared: kebab menu + delete confirm dialog + error panel
// ---------------------------------------------------------------------------

function CardKebab({
  label,
  disabled,
  onDelete,
  deleteLabel = "Delete",
}: {
  label: string;
  disabled: boolean;
  onDelete: () => void;
  deleteLabel?: string;
}): React.ReactElement {
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <button
          type="button"
          aria-label={label}
          disabled={disabled}
          onClick={(e) => e.stopPropagation()}
          onKeyDown={(e) => e.stopPropagation()}
          className={cn(
            "inline-flex h-7 w-7 items-center justify-center rounded-md text-muted-foreground",
            "transition-colors hover:bg-muted hover:text-foreground",
            "focus:outline-none focus-visible:ring-2 focus-visible:ring-ring",
            disabled && "opacity-50",
          )}
        >
          <MoreVertical className="h-4 w-4" aria-hidden="true" />
        </button>
      </DropdownMenuTrigger>
      <DropdownMenuContent
        align="end"
        onClick={(e) => e.stopPropagation()}
      >
        <DropdownMenuItem
          className="gap-2 text-destructive focus:text-destructive"
          onSelect={(e) => {
            e.preventDefault();
            onDelete();
          }}
        >
          <Trash2 className="h-4 w-4" aria-hidden="true" />
          {deleteLabel}
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

function DeleteConfirmDialog({
  open,
  onOpenChange,
  title,
  description,
  confirmLabel,
  onConfirm,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: string;
  description: string;
  confirmLabel: string;
  onConfirm: () => void;
}): React.ReactElement {
  return (
    <AlertDialog open={open} onOpenChange={onOpenChange}>
      <AlertDialogContent onClick={(e) => e.stopPropagation()}>
        <AlertDialogHeader>
          <AlertDialogTitle>{title}</AlertDialogTitle>
          <AlertDialogDescription>{description}</AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel>Cancel</AlertDialogCancel>
          <AlertDialogAction
            onClick={(e) => {
              e.stopPropagation();
              onConfirm();
            }}
            className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
          >
            {confirmLabel}
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}

function ErrorPanel({
  message,
  onRetry,
  testId,
}: {
  message: string;
  onRetry: () => void;
  testId: string;
}): React.ReactElement {
  return (
    <div
      role="alert"
      className="flex flex-col items-center justify-center py-12 text-center"
      data-testid={testId}
    >
      <AlertCircle className="mb-3 h-6 w-6 text-destructive" aria-hidden="true" />
      <p className="text-sm font-medium">Couldn&apos;t load</p>
      <p className="mt-1 text-xs text-muted-foreground">{message}</p>
      <Button variant="outline" size="sm" className="mt-4" onClick={onRetry}>
        <RefreshCw className="mr-1.5 h-3.5 w-3.5" aria-hidden="true" />
        Retry
      </Button>
    </div>
  );
}

function KvRow({
  label,
  value,
}: {
  label: string;
  value: string;
}): React.ReactElement {
  return (
    <div className="flex items-center justify-between gap-3">
      <span className="shrink-0 text-muted-foreground/70">{label}</span>
      <span className="min-w-0 truncate text-right text-foreground/85">{value}</span>
    </div>
  );
}

// ---------------------------------------------------------------------------
// WorkflowStatusPill
// ---------------------------------------------------------------------------

function WorkflowStatusPill({
  status,
}: {
  status: WorkflowStatus;
}): React.ReactElement {
  if (status === "active") {
    return (
      <span
        className="inline-flex items-center gap-1.5 rounded-full border bg-transparent px-2.5 py-1 text-[11px] font-medium"
        style={{ borderColor: BRAND_GREEN, color: BRAND_GREEN }}
      >
        <span
          aria-hidden={true}
          className="h-1.5 w-1.5 rounded-full"
          style={{ background: BRAND_GREEN, animation: "pulse-quartr 1.6s ease-in-out infinite" }}
        />
        Active
      </span>
    );
  }
  const palette: Record<
    Exclude<WorkflowStatus, "active">,
    { dot: string; label: string; bg: string; text: string }
  > = {
    paused: {
      dot: "bg-amber-500",
      label: "Paused",
      bg: "bg-amber-50 dark:bg-amber-500/10",
      text: "text-amber-700 dark:text-amber-300",
    },
    draft: {
      dot: "bg-muted-foreground/60",
      label: "Draft",
      bg: "bg-muted",
      text: "text-muted-foreground",
    },
    archived: {
      dot: "bg-muted-foreground/40",
      label: "Archived",
      bg: "bg-muted",
      text: "text-muted-foreground/80",
    },
  };
  const p = palette[status];
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-[11px] font-medium",
        p.bg,
        p.text,
      )}
    >
      <span aria-hidden={true} className={cn("h-1.5 w-1.5 rounded-full", p.dot)} />
      {p.label}
    </span>
  );
}

// ---------------------------------------------------------------------------
// Skeleton
// ---------------------------------------------------------------------------

function AgentsGridSkeleton(): React.ReactElement {
  return (
    <div
      className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4"
      data-testid="agents-loading"
    >
      {Array.from({ length: 6 }).map((_, i) => (
        <Skeleton key={i} className="h-64 w-full rounded-2xl" />
      ))}
    </div>
  );
}
