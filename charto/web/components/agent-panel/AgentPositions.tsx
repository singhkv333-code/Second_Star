"use client";

/**
 * AgentPositions — the positions THIS agent's own trades opened, with the
 * returns on them since the trades took place.
 *
 * Attribution is agent-scoped: the backend FIFO-replays only the fills tagged
 * to this workflow's ForwardIdea (GET /api/workflows/{id}/positions), so this
 * is not the account-wide book — it's what this one agent is holding.
 * Same load/error/empty/list shape as RunHistory.
 */

import { useEffect, useState } from "react";
import { AlertCircle, PackageOpen, RefreshCw } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";
import { getAgentPositions } from "@/lib/api";
import { isError } from "@/lib/types";
import type { AgentPosition, AgentPositions as AgentPositionsData } from "@/lib/types";

export type AgentPositionsProps = {
  workflowId: string;
};

type FetchState =
  | { kind: "loading" }
  | { kind: "error"; message: string }
  | { kind: "ok"; data: AgentPositionsData };

function inr(n: number, max = 2): string {
  const sign = n < 0 ? "-" : "";
  return `${sign}₹${Math.abs(n).toLocaleString("en-IN", {
    maximumFractionDigits: max,
  })}`;
}

function signedInr(n: number): string {
  const sign = n > 0 ? "+" : n < 0 ? "-" : "";
  return `${sign}₹${Math.abs(n).toLocaleString("en-IN", {
    maximumFractionDigits: 0,
  })}`;
}

export function AgentPositions({
  workflowId,
}: AgentPositionsProps): React.ReactElement {
  const [state, setState] = useState<FetchState>({ kind: "loading" });

  const load = (): void => {
    setState({ kind: "loading" });
    getAgentPositions(workflowId)
      .then((result) => {
        if (isError(result)) {
          setState({ kind: "error", message: result.error.message });
          return;
        }
        setState({ kind: "ok", data: result.data });
      })
      .catch((err: unknown) => {
        const msg = err instanceof Error ? err.message : "Network error";
        setState({ kind: "error", message: msg });
      });
  };

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [workflowId]);

  // --- loading ---
  if (state.kind === "loading") {
    return (
      <div className="space-y-2 px-6 py-5" data-testid="agent-positions-loading">
        <Skeleton className="h-16 w-full rounded-xl" />
        {Array.from({ length: 4 }).map((_, i) => (
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
        data-testid="agent-positions-error"
      >
        <AlertCircle className="mb-3 h-6 w-6 text-destructive" aria-hidden="true" />
        <p className="text-sm font-medium">Couldn&apos;t load positions</p>
        <p className="mt-1 max-w-sm text-xs text-muted-foreground">{state.message}</p>
        <Button variant="outline" size="sm" className="mt-4" onClick={load}>
          <RefreshCw className="mr-1.5 h-3.5 w-3.5" aria-hidden="true" />
          Retry
        </Button>
      </div>
    );
  }

  const { data } = state;

  // --- empty ---
  if (data.positions.length === 0) {
    return (
      <div
        className="flex flex-col items-center justify-center px-8 py-12 text-center"
        data-testid="agent-positions-empty"
      >
        <PackageOpen className="mb-3 h-6 w-6 text-muted-foreground" aria-hidden="true" />
        <p className="text-sm font-medium">No open positions yet</p>
        <p className="mt-1 max-w-sm text-xs text-muted-foreground">
          {data.has_data
            ? "This agent has traded, but every position it opened is now closed."
            : "This agent hasn't placed any trades yet. When it fires and buys, the positions it holds will show up here."}
        </p>
        {data.has_data && data.realized_pnl !== 0 && (
          <p className="mt-3 text-xs text-muted-foreground">
            Booked P&amp;L so far:{" "}
            <span
              className={cn(
                "font-medium",
                data.realized_pnl >= 0 ? "text-emerald-600" : "text-red-600",
              )}
            >
              {signedInr(data.realized_pnl)}
            </span>
          </p>
        )}
      </div>
    );
  }

  const unrealPos = data.unrealized_pnl >= 0;

  // --- list ---
  return (
    <div className="flex flex-col" data-testid="agent-positions">
      {/* Summary header — this agent's rolled-up book. */}
      <div className="border-b px-6 py-4">
        <div className="grid grid-cols-2 gap-x-6 gap-y-3">
          <div>
            <p className="text-[11px] uppercase tracking-wide text-muted-foreground">
              Invested
            </p>
            <p className="mt-0.5 text-[15px] font-semibold tabular-nums">
              {inr(data.invested, 0)}
            </p>
          </div>
          <div>
            <p className="text-[11px] uppercase tracking-wide text-muted-foreground">
              Current value
            </p>
            <p className="mt-0.5 text-[15px] font-semibold tabular-nums">
              {inr(data.market_value, 0)}
            </p>
          </div>
          <div>
            <p className="text-[11px] uppercase tracking-wide text-muted-foreground">
              Unrealized P&amp;L
            </p>
            <p
              className={cn(
                "mt-0.5 text-[15px] font-semibold tabular-nums",
                unrealPos ? "text-emerald-600" : "text-red-600",
              )}
            >
              {signedInr(data.unrealized_pnl)}
              {data.unrealized_pnl_pct !== null && (
                <span className="ml-1 text-[12px] font-normal">
                  ({unrealPos ? "+" : ""}
                  {data.unrealized_pnl_pct.toFixed(2)}%)
                </span>
              )}
            </p>
          </div>
          <div>
            <p className="text-[11px] uppercase tracking-wide text-muted-foreground">
              Realized P&amp;L
            </p>
            <p
              className={cn(
                "mt-0.5 text-[15px] font-semibold tabular-nums",
                data.realized_pnl >= 0 ? "text-emerald-600" : "text-red-600",
              )}
            >
              {signedInr(data.realized_pnl)}
            </p>
          </div>
        </div>
      </div>

      <ol className="divide-y">
        {data.positions.map((p) => (
          <li key={p.symbol}>
            <PositionRow position={p} />
          </li>
        ))}
      </ol>

      <p className="px-6 py-3 text-[11px] leading-relaxed text-muted-foreground">
        Positions this agent&apos;s trades opened, marked at the latest price.
        Returns are since each fill. This is analysis, not financial advice.
      </p>
    </div>
  );
}

function PositionRow({
  position,
}: {
  position: AgentPosition;
}): React.ReactElement {
  const pos = position.unrealized_pnl >= 0;
  return (
    <div
      className="flex items-center gap-3 px-6 py-3.5"
      data-testid={`position-row-${position.symbol}`}
    >
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <span className="text-[13px] font-semibold text-foreground">
            {position.symbol}
          </span>
          <span className="text-[11px] text-muted-foreground">
            {position.quantity} qty
          </span>
        </div>
        <div className="mt-0.5 flex items-center gap-3 text-[11px] text-muted-foreground tabular-nums">
          <span>Avg {inr(position.avg_cost)}</span>
          <span>
            LTP {position.last_price !== null ? inr(position.last_price) : "—"}
          </span>
        </div>
      </div>

      <div className="shrink-0 text-right tabular-nums">
        <div
          className={cn(
            "text-[13px] font-semibold",
            pos ? "text-emerald-600" : "text-red-600",
          )}
        >
          {signedInr(position.unrealized_pnl)}
        </div>
        <div className="mt-0.5 flex items-center justify-end gap-2 text-[11px]">
          {position.unrealized_pnl_pct !== null && (
            <span className={pos ? "text-emerald-600" : "text-red-600"}>
              {pos ? "+" : ""}
              {position.unrealized_pnl_pct.toFixed(2)}%
            </span>
          )}
          <span className="text-muted-foreground">
            {inr(position.market_value, 0)}
          </span>
        </div>
      </div>
    </div>
  );
}
