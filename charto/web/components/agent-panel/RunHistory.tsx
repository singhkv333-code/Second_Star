"use client";

/**
 * RunHistory — paginated list of runs for a workflow.
 *
 * Per docs/ARCHITECTURE.md §11 and docs/API_CONTRACT.md §6.1.
 * Clicking a row opens RunView (passed back via onSelectRun).
 */

import { useEffect, useState } from "react";
import { formatDistanceToNow } from "date-fns";
import {
  AlertCircle,
  ChevronDown,
  Clock,
  RefreshCw,
  Zap,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";
import { listRuns } from "@/lib/api";
import { isError } from "@/lib/types";
import type { RunStatus, RunSummary, TriggeredBy } from "@/lib/types";

export type RunHistoryProps = {
  workflowId: string;
  /** Called when the user clicks a row. Parent renders RunView for the selected run. */
  onSelectRun: (runId: string) => void;
};

type FetchState =
  | { kind: "loading" }
  | { kind: "error"; message: string }
  | { kind: "ok"; items: RunSummary[]; nextCursor: string | null };

export function RunHistory({
  workflowId,
  onSelectRun,
}: RunHistoryProps): React.ReactElement {
  const [state, setState] = useState<FetchState>({ kind: "loading" });
  const [loadingMore, setLoadingMore] = useState(false);

  const load = (cursor?: string): void => {
    if (!cursor) setState({ kind: "loading" });
    listRuns(workflowId, { limit: 20, cursor })
      .then((result) => {
        if (isError(result)) {
          setState({ kind: "error", message: result.error.message });
          return;
        }
        setState((prev) => {
          const existing =
            cursor && prev.kind === "ok" ? prev.items : [];
          return {
            kind: "ok",
            items: [...existing, ...result.data.items],
            nextCursor: result.data.next_cursor,
          };
        });
      })
      .catch((err: unknown) => {
        const msg = err instanceof Error ? err.message : "Network error";
        setState({ kind: "error", message: msg });
      })
      .finally(() => setLoadingMore(false));
  };

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [workflowId]);

  const handleLoadMore = (): void => {
    if (state.kind !== "ok" || !state.nextCursor) return;
    setLoadingMore(true);
    load(state.nextCursor);
  };

  // --- loading ---
  if (state.kind === "loading") {
    return (
      <div className="space-y-2 px-6 py-5" data-testid="run-history-loading">
        {Array.from({ length: 5 }).map((_, i) => (
          <Skeleton key={i} className="h-14 w-full rounded-xl" />
        ))}
      </div>
    );
  }

  // --- error ---
  if (state.kind === "error") {
    return (
      <div
        role="alert"
        className="flex flex-col items-center justify-center px-8 py-12 text-center"
        data-testid="run-history-error"
      >
        <AlertCircle className="mb-3 h-6 w-6 text-destructive" aria-hidden="true" />
        <p className="text-sm font-medium">Couldn&apos;t load run history</p>
        <p className="mt-1 max-w-sm text-xs text-muted-foreground">{state.message}</p>
        <Button
          variant="outline"
          size="sm"
          className="mt-4"
          onClick={() => load()}
        >
          <RefreshCw className="mr-1.5 h-3.5 w-3.5" aria-hidden="true" />
          Retry
        </Button>
      </div>
    );
  }

  // --- empty ---
  if (state.items.length === 0) {
    return (
      <div
        className="flex flex-col items-center justify-center px-8 py-12 text-center"
        data-testid="run-history-empty"
      >
        <Clock className="mb-3 h-6 w-6 text-muted-foreground" aria-hidden="true" />
        <p className="text-sm font-medium">No runs yet</p>
        <p className="mt-1 text-xs text-muted-foreground">
          Click &quot;Run now&quot; to check conditions immediately — the action
          only fires if they&apos;re currently met.
        </p>
      </div>
    );
  }

  // --- list ---
  return (
    <div className="flex flex-col" data-testid="run-history">
      <ol className="divide-y">
        {state.items.map((run) => (
          <li key={run.id}>
            <RunHistoryRow run={run} onSelect={() => onSelectRun(run.id)} />
          </li>
        ))}
      </ol>

      {state.nextCursor && (
        <div className="px-6 py-4">
          <Button
            variant="outline"
            size="sm"
            className="w-full"
            disabled={loadingMore}
            onClick={handleLoadMore}
            data-testid="load-more-button"
          >
            {loadingMore ? (
              <RefreshCw className="mr-1.5 h-3.5 w-3.5 animate-spin" aria-hidden="true" />
            ) : (
              <ChevronDown className="mr-1.5 h-3.5 w-3.5" aria-hidden="true" />
            )}
            Load more
          </Button>
        </div>
      )}
    </div>
  );
}

function RunHistoryRow({
  run,
  onSelect,
}: {
  run: RunSummary;
  onSelect: () => void;
}): React.ReactElement {
  const ago = formatDistanceToNow(new Date(run.started_at), {
    addSuffix: true,
  });
  const duration =
    run.finished_at
      ? Math.round(
          (new Date(run.finished_at).getTime() -
            new Date(run.started_at).getTime()) /
            1000,
        )
      : null;

  const title = RUN_TITLE_LABELS[run.triggered_by] ?? "Run";

  return (
    <button
      type="button"
      className={cn(
        "flex w-full items-center gap-3 px-6 py-3.5 text-left",
        "hover:bg-muted/50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring",
        "transition-colors",
      )}
      onClick={onSelect}
      data-testid={`run-row-${run.id}`}
      aria-label={`${title}, ${run.status}, ${ago}`}
    >
      <RunStatusDot status={run.status} />

      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <TriggerBadge triggeredBy={run.triggered_by} label={title} />
        </div>
        <div className="mt-0.5 flex items-center gap-3">
          <span className="text-[11px] text-muted-foreground">{ago}</span>
          {duration !== null && (
            <span className="text-[11px] text-muted-foreground">
              {duration}s
            </span>
          )}
          <span className="text-[11px] text-muted-foreground">
            {run.step_count} step{run.step_count !== 1 ? "s" : ""}
          </span>
        </div>
      </div>

      <RunStatusBadgeCompact status={run.status} />
    </button>
  );
}

function RunStatusDot({ status }: { status: RunStatus }): React.ReactElement {
  const cls = RUN_STATUS_DOT[status] ?? "bg-muted-foreground";
  return (
    <span
      aria-hidden="true"
      className={cn("h-2 w-2 shrink-0 rounded-full", cls, {
        "animate-pulse": status === "running",
      })}
    />
  );
}

function RunStatusBadgeCompact({
  status,
}: {
  status: RunStatus;
}): React.ReactElement {
  const { label, variant } = RUN_STATUS_META[status] ?? {
    label: status,
    variant: "muted" as const,
  };
  return (
    <Badge variant={variant} className="shrink-0 text-[10px]">
      {label}
    </Badge>
  );
}

function TriggerBadge({
  triggeredBy,
  label,
}: {
  triggeredBy: TriggeredBy;
  /** Human-readable run title; falls back to the trigger label. */
  label?: string;
}): React.ReactElement {
  const text = label ?? TRIGGER_LABELS[triggeredBy] ?? triggeredBy;
  return (
    <span className="inline-flex items-center gap-1.5 text-[13px] font-medium text-foreground">
      <Zap className="h-3.5 w-3.5 text-muted-foreground" aria-hidden="true" />
      {text}
    </span>
  );
}

// ---------------------------------------------------------------------------
// Style constants
// ---------------------------------------------------------------------------

const RUN_STATUS_DOT: Record<RunStatus, string> = {
  running: "bg-blue-500",
  succeeded: "bg-green-500",
  failed: "bg-red-500",
  cancelled: "bg-slate-400",
  awaiting_approval: "bg-amber-400",
};

type BadgeVariant = "default" | "success" | "warning" | "destructive" | "muted" | "outline" | "secondary" | "info";

const RUN_STATUS_META: Record<
  RunStatus,
  { label: string; variant: BadgeVariant }
> = {
  running: { label: "Running", variant: "info" },
  succeeded: { label: "Succeeded", variant: "success" },
  failed: { label: "Failed", variant: "destructive" },
  cancelled: { label: "Cancelled", variant: "muted" },
  awaiting_approval: { label: "Awaiting approval", variant: "warning" },
};

const TRIGGER_LABELS: Partial<Record<TriggeredBy, string>> = {
  schedule: "Scheduled",
  manual: "Manual",
  webhook: "Webhook",
  price_alert: "Price alert",
  indicator_alert: "Indicator alert",
  event_alert: "Event",
};

// Human-readable run titles — shown instead of the internal run id hash so a
// retail user reads "Scheduled run" / "Manual run" rather than "1159bbed".
const RUN_TITLE_LABELS: Partial<Record<TriggeredBy, string>> = {
  schedule: "Scheduled run",
  manual: "Manual run",
  webhook: "Webhook run",
  price_alert: "Price-alert run",
  indicator_alert: "Indicator-alert run",
  event_alert: "Event-triggered run",
};
