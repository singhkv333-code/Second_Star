"use client";

/**
 * ActiveAgentsRail — right-side "Active Agents" panel shown on the dashboard.
 *
 * Fetches GET /api/workflows (active + paused) and for each fetches
 * GET /api/workflows/{id}/runs?limit=1 to derive status pill.
 *
 * Status derivation:
 *   RUNNING  — workflow active + last run status is "running" or "awaiting_approval"
 *   BLOCKED  — workflow active + last run status is "failed"
 *   IDLE     — workflow active, no in-flight run
 */

import { useEffect, useState } from "react";
import { formatDistanceToNow, parseISO } from "date-fns";
import { Bot, Play, RefreshCw } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { getWorkflow, listRuns, listWorkflows } from "@/lib/api";
import { isError } from "@/lib/types";
import type { Workflow, WorkflowSummary } from "@/lib/types";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

type AgentStatus = "RUNNING" | "BLOCKED" | "IDLE";

type AgentCard = {
  workflow: WorkflowSummary;
  agentStatus: AgentStatus;
  seq: number;
  category: string;
};

type RailState =
  | { kind: "loading" }
  | { kind: "error"; message: string }
  | { kind: "ok"; cards: AgentCard[] };

type ActiveAgentsRailProps = {
  onOpenWorkflow: (workflow: Workflow) => void;
};

// ---------------------------------------------------------------------------
// Category derivation (from step_type prefix patterns)
// ---------------------------------------------------------------------------

/** Derive a display category from a workflow summary (no steps available).
 *  Falls back to "AGENT" since WorkflowSummary doesn't include steps. */
function deriveCategory(name: string): string {
  const n = name.toLowerCase();
  if (n.includes("cash") || n.includes("sweep") || n.includes("fund")) return "CASH";
  if (n.includes("research") || n.includes("report") || n.includes("analyse") || n.includes("analyze")) return "RESEARCH";
  if (n.includes("risk") || n.includes("hedge")) return "RISK";
  if (n.includes("income") || n.includes("dividend")) return "INCOME";
  return "AGENT";
}

/** Derive a human-readable category pill label. */
function categoryLabel(cat: string): string {
  const MAP: Record<string, string> = {
    CASH: "Fund Management",
    RESEARCH: "Research",
    RISK: "Risk",
    INCOME: "Income",
    AGENT: "Strategy",
  };
  return MAP[cat] ?? "Strategy";
}

/** Category footer-pill color (a CSS color used for the 1px border,
 *  the 5px dot, and the label text). Mirrors Quartr's CATEGORY_COLOR
 *  map but extended for pivot's category set. */
function categoryHex(cat: string): string {
  const MAP: Record<string, string> = {
    CASH: "#60a5fa",      // blue
    RESEARCH: "#a78bfa",  // violet
    RISK: "var(--color-loss)",
    INCOME: "var(--color-profit)",
    AGENT: "var(--text-secondary)",
  };
  return MAP[cat] ?? "var(--text-secondary)";
}

/** Status pill color — Quartr's STATUS_COLOR mapping. */
function statusHex(status: AgentStatus): string {
  if (status === "RUNNING") return "var(--color-profit)";
  if (status === "BLOCKED") return "var(--color-loss)";
  return "var(--text-tertiary)";
}

// ---------------------------------------------------------------------------
// ActiveAgentsRail
// ---------------------------------------------------------------------------

export function ActiveAgentsRail({
  onOpenWorkflow,
}: ActiveAgentsRailProps): React.ReactElement {
  const [state, setState] = useState<RailState>({ kind: "loading" });

  const load = (): void => {
    setState({ kind: "loading" });

    listWorkflows({ status: ["active", "paused"], limit: 10 })
      .then(async (result) => {
        if (isError(result)) {
          setState({ kind: "error", message: result.error.message });
          return;
        }

        const workflows = result.data.items;
        if (workflows.length === 0) {
          setState({ kind: "ok", cards: [] });
          return;
        }

        // Fetch last run for each workflow to derive status
        const cards = await Promise.all(
          workflows.map(async (wf, idx): Promise<AgentCard> => {
            let agentStatus: AgentStatus = "IDLE";
            try {
              const runsResult = await listRuns(wf.id, { limit: 1 });
              if (!isError(runsResult) && runsResult.data.items.length > 0) {
                const lastRun = runsResult.data.items[0]!;
                if (lastRun.status === "running" || lastRun.status === "awaiting_approval") {
                  agentStatus = "RUNNING";
                } else if (lastRun.status === "failed") {
                  agentStatus = "BLOCKED";
                }
              }
            } catch {
              // Ignore — IDLE fallback
            }

            const cat = deriveCategory(wf.name);
            return { workflow: wf, agentStatus, seq: idx + 1, category: cat };
          }),
        );

        setState({ kind: "ok", cards });
      })
      .catch((err: unknown) => {
        const msg = err instanceof Error ? err.message : "Network error";
        setState({ kind: "error", message: msg });
      });
  };

  useEffect(() => {
    load();
  }, []);

  return (
    <aside
      className="flex flex-col"
      aria-label="Active Agents"
      data-testid="active-agents-rail"
      style={{ gap: 14 }}
    >
      {/* Heading — matches frontend-quartr/.../ActiveAgentsRail.jsx
          font-display weight-display 18px tracking -0.02em, no refresh
          control. */}
      <div
        className="flex items-center"
        style={{ marginBottom: 4 }}
      >
        <h2
          style={{
            fontFamily: "var(--font-display)",
            fontWeight: "var(--weight-display)" as unknown as number,
            fontSize: 18,
            letterSpacing: "-0.02em",
            color: "var(--text-primary)",
            margin: 0,
          }}
        >
          Active Agents
        </h2>
      </div>

      {state.kind === "loading" && <AgentRailSkeleton />}

      {state.kind === "error" && (
        <div
          role="alert"
          className="rounded-xl border bg-card px-4 py-4 text-center"
          data-testid="rail-error"
        >
          <p className="text-xs text-muted-foreground">{state.message}</p>
          <Button
            variant="ghost"
            size="sm"
            className="mt-2 h-6 text-xs"
            onClick={load}
          >
            Retry
          </Button>
        </div>
      )}

      {state.kind === "ok" && state.cards.length === 0 && (
        <div
          className="rounded-xl border bg-card px-4 py-6 text-center"
          data-testid="rail-empty"
        >
          <Bot className="mx-auto mb-2 h-6 w-6 text-muted-foreground" aria-hidden={true} />
          <p className="text-xs text-muted-foreground">No active agents yet.</p>
        </div>
      )}

      {state.kind === "ok" &&
        state.cards.map((card) => (
          <AgentCardItem
            key={card.workflow.id}
            card={card}
            onOpen={onOpenWorkflow}
          />
        ))}
    </aside>
  );
}

// ---------------------------------------------------------------------------
// AgentCardItem
// ---------------------------------------------------------------------------

function AgentCardItem({
  card,
  onOpen,
}: {
  card: AgentCard;
  onOpen: (workflow: Workflow) => void;
}): React.ReactElement {
  const { workflow, agentStatus, seq, category } = card;
  const [opening, setOpening] = useState(false);

  const handleOpen = async (): Promise<void> => {
    setOpening(true);
    try {
      const result = await getWorkflow(workflow.id);
      if (!isError(result)) {
        onOpen(result.data);
      }
    } catch {
      // Ignore
    } finally {
      setOpening(false);
    }
  };

  const lastRunAgo = workflow.last_run_at
    ? formatDistanceToNow(parseISO(workflow.last_run_at), { addSuffix: true })
    : null;

  const nextRun = workflow.next_run_at
    ? formatDistanceToNow(parseISO(workflow.next_run_at), { addSuffix: true })
    : null;

  const statusColor = statusHex(agentStatus);
  const catColor = categoryHex(category);
  const catLabel = categoryLabel(category);
  const titleText = workflow.name.endsWith(".") ? workflow.name : `${workflow.name}.`;
  const nextValue = nextRun ?? (workflow.next_run_at === null ? "On trigger" : "Manual");

  return (
    <div
      data-testid={`agent-card-${workflow.id}`}
      className="flex flex-col text-[var(--text-primary)]"
      style={{
        background: "var(--bg-primary)",
        border: "1px solid var(--glass-border)",
        borderRadius: 0,
        transition: "border-color 0.25s var(--ease-quartr)",
      }}
      onMouseEnter={(e) => { e.currentTarget.style.borderColor = "var(--glass-border-hover)"; }}
      onMouseLeave={(e) => { e.currentTarget.style.borderColor = "var(--glass-border)"; }}
    >
      {/* Top strip — AGENT NN / CATEGORY · STATUS pill */}
      <div
        className="flex items-center justify-between"
        style={{
          padding: "10px 14px",
          borderBottom: "2px solid var(--glass-border-hover)",
        }}
      >
        <span
          style={{
            fontFamily: "var(--font-ui)",
            fontSize: 10,
            letterSpacing: "0.18em",
            fontWeight: 600,
            color: "var(--text-secondary)",
          }}
        >
          AGENT {String(seq).padStart(2, "0")} / {category}
        </span>
        <StatusPill status={agentStatus} />
      </div>

      {/* Body */}
      <div style={{ padding: "14px 14px 12px", flex: 1 }}>
        <h3
          className="m-0"
          style={{
            fontFamily: "var(--font-ui)",
            fontSize: 17,
            letterSpacing: "-0.02em",
            lineHeight: 1.15,
            fontWeight: 600,
            color: "var(--text-primary)",
          }}
        >
          {titleText}
        </h3>

        {workflow.description && (
          <p
            className="line-clamp-2"
            style={{
              margin: "8px 0 12px",
              fontSize: 12.5,
              lineHeight: 1.5,
              color: "var(--text-secondary)",
              fontFamily: "var(--font-ui)",
            }}
          >
            {workflow.description}
          </p>
        )}

        <div
          style={{
            display: "grid",
            gridTemplateColumns: "auto 1fr",
            rowGap: 4,
            columnGap: 12,
            fontSize: 11,
            fontFamily: "var(--font-ui)",
          }}
        >
          <span style={{ color: "var(--text-tertiary)", letterSpacing: "0.06em" }}>MODEL</span>
          <span style={{ color: "var(--text-secondary)" }}>Pivot Engine</span>
          <span style={{ color: "var(--text-tertiary)", letterSpacing: "0.06em" }}>LAST</span>
          <span style={{ color: "var(--text-secondary)" }}>{lastRunAgo ?? "Never"}</span>
          <span style={{ color: "var(--text-tertiary)", letterSpacing: "0.06em" }}>NEXT</span>
          <span style={{ color: "var(--text-secondary)" }}>{nextValue}</span>
        </div>
      </div>

      {/* Footer — VIEW AGENT button + category pill */}
      <div
        className="flex items-center justify-between"
        style={{
          padding: "9px 14px",
          background: "var(--bg-elevated)",
          borderTop: "1px solid var(--glass-border)",
        }}
      >
        <button
          type="button"
          onClick={handleOpen}
          disabled={opening}
          aria-label={`View agent: ${workflow.name}`}
          data-testid={`view-agent-${workflow.id}`}
          className="inline-flex items-center"
          style={{
            background: "transparent",
            border: "none",
            padding: 0,
            color: "var(--text-secondary)",
            fontFamily: "var(--font-ui)",
            fontSize: 10,
            fontWeight: 600,
            letterSpacing: "0.12em",
            cursor: opening ? "not-allowed" : "pointer",
            opacity: opening ? 0.5 : 1,
            transition: "color 0.2s var(--ease-quartr)",
            gap: 6,
          }}
          onMouseEnter={(e) => { e.currentTarget.style.color = "var(--text-primary)"; }}
          onMouseLeave={(e) => { e.currentTarget.style.color = "var(--text-secondary)"; }}
        >
          <Play size={9} fill="currentColor" strokeWidth={0} aria-hidden={true} />
          VIEW AGENT
        </button>

        <span
          className="inline-flex items-center"
          style={{
            gap: 5,
            padding: "3px 9px",
            borderRadius: "var(--radius-pill)",
            background: "var(--surface-active)",
            border: `1px solid ${catColor}`,
            fontFamily: "var(--font-ui)",
            fontSize: 10.5,
            fontWeight: 500,
            color: catColor,
            whiteSpace: "nowrap",
          }}
        >
          <span
            aria-hidden={true}
            style={{
              width: 5,
              height: 5,
              borderRadius: "50%",
              background: catColor,
            }}
          />
          {catLabel}
        </span>
      </div>
    </div>
  );
}


// ---------------------------------------------------------------------------
// Status pill
// ---------------------------------------------------------------------------

function StatusPill({ status }: { status: AgentStatus }): React.ReactElement {
  // Quartr's status indicator is a colored dot + uppercase label, no
  // pill background — the color *is* the signal. Running gets the
  // pulse-quartr animation defined in globals.css.
  const color = statusHex(status);
  const pulse = status === "RUNNING";
  return (
    <span
      className="inline-flex items-center"
      style={{
        gap: 5,
        fontFamily: "var(--font-ui)",
        fontSize: 10,
        letterSpacing: "0.18em",
        fontWeight: 600,
        color,
      }}
    >
      <span
        aria-hidden={true}
        style={{
          width: 6,
          height: 6,
          borderRadius: "50%",
          background: color,
          animation: pulse ? "pulse-quartr 1.6s ease-in-out infinite" : "none",
        }}
      />
      {status}
    </span>
  );
}

// ---------------------------------------------------------------------------
// Skeleton
// ---------------------------------------------------------------------------

function AgentRailSkeleton(): React.ReactElement {
  return (
    <div className="space-y-2.5" data-testid="rail-loading">
      {Array.from({ length: 3 }).map((_, i) => (
        <Skeleton key={i} className="h-40 w-full rounded-xl" />
      ))}
    </div>
  );
}
