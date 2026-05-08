"use client";

/**
 * AgentsTab — Quartr-style agent catalog card grid.
 *
 * Per docs/UI_TABS_V1.md §1 + Day 7 redesign (image 4).
 * Lists GET /api/workflows with filter chips. Clicking a card opens AgentPanel.
 *
 * Card design: file-folder style with:
 *   - Header bar: FILE NNN / QUANT|INCOME|etc + RISK pill
 *   - Serif title ending with a period
 *   - KEY:VALUE rows: METHOD / UNIVERSE / CADENCE
 *   - Footer: VIEW AGENT link + CAGR placeholder
 */

import { useEffect, useState } from "react";
import { formatDistanceToNow, parseISO } from "date-fns";
import {
  AlertCircle,
  Bot,
  Play,
  RefreshCw,
  Sparkles,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { createWorkflow, getWorkflow, listWorkflows } from "@/lib/api";
import { isError } from "@/lib/types";
import type { Workflow, WorkflowStatus, WorkflowSummary } from "@/lib/types";
import { DEMO_WORKFLOW } from "@/components/agent-panel/demo-workflow";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export type AgentsTabProps = {
  onOpenWorkflow: (workflow: Workflow) => void;
};

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

// ---------------------------------------------------------------------------
// Category derivation (from name/description)
// ---------------------------------------------------------------------------

type Category = "QUANT" | "INCOME" | "TACTICAL" | "EVENT" | "PASSIVE";
type RiskLevel = "HIGH RISK" | "MEDIUM RISK" | "LOW RISK";

function deriveCategory(wf: WorkflowSummary): Category {
  const text = `${wf.name} ${wf.description ?? ""}`.toLowerCase();
  if (text.includes("dividend") || text.includes("income") || text.includes("yield")) return "INCOME";
  if (text.includes("event") || text.includes("earnings") || text.includes("ipo")) return "EVENT";
  if (text.includes("passive") || text.includes("index") || text.includes("etf")) return "PASSIVE";
  if (text.includes("tactical") || text.includes("swing") || text.includes("breakout")) return "TACTICAL";
  return "QUANT";
}

/** Derive risk: without full step data, use description heuristics. */
function deriveRisk(wf: WorkflowSummary): RiskLevel {
  const text = `${wf.name} ${wf.description ?? ""}`.toLowerCase();
  if (text.includes("approval") || text.includes("review")) return "MEDIUM RISK";
  if (text.includes("notify") || text.includes("fetch") || text.includes("monitor") || text.includes("watch")) return "LOW RISK";
  if (text.includes("buy") || text.includes("sell") || text.includes("order") || text.includes("trade")) return "HIGH RISK";
  return "MEDIUM RISK";
}

/** Derive METHOD from description (truncated) or name. */
function deriveMethod(wf: WorkflowSummary): string {
  if (wf.description) return wf.description.slice(0, 60) + (wf.description.length > 60 ? "…" : "");
  return wf.name;
}

/** Derive UNIVERSE heuristic from name/description. Pulls the most
 *  plausible NSE ticker out of the workflow's free text. Tokens that
 *  are common English words (cadence/action verbs) are excluded so
 *  short tickers like TCS / RIL aren't shadowed by words like
 *  MONTHLY or WEEKLY. */
function deriveUniverse(wf: WorkflowSummary): string {
  const text = `${wf.name} ${wf.description ?? ""}`.toUpperCase();
  const nseMatch = text.match(/\b([A-Z]{2,12})\b/g);
  const knownIndices = ["NIFTY", "SENSEX", "BANKNIFTY"];
  // English-words / domain verbs that match the ticker regex but are
  // not real tickers. Add to this list rather than tightening the
  // length filter — TCS / SBI / RIL are 3-letter tickers we DO want.
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

/** Derive CADENCE from next_run_at / last_run_at availability. */
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

export function AgentsTab({ onOpenWorkflow }: AgentsTabProps): React.ReactElement {
  const [filter, setFilter] = useState<Filter>("all");
  const [state, setState] = useState<FetchState>({ kind: "loading" });
  const [openingId, setOpeningId] = useState<string | null>(null);
  const [seeding, setSeeding] = useState(false);
  const [seedError, setSeedError] = useState<string | null>(null);

  const load = (f: Filter): void => {
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
  };

  useEffect(() => {
    load(filter);
  }, [filter]);

  const seedDemoAgent = (): void => {
    setSeeding(true);
    setSeedError(null);
    createWorkflow({
      name: DEMO_WORKFLOW.name,
      description: DEMO_WORKFLOW.description ?? undefined,
      single_instance: DEMO_WORKFLOW.single_instance,
      steps: DEMO_WORKFLOW.steps.map((s) => ({
        step_type: s.step_type,
        label: s.label,
        config: s.config,
      })),
    })
      .then((result) => {
        if (isError(result)) {
          setSeedError(result.error.message);
          return;
        }
        load(filter);
      })
      .catch((err: unknown) => {
        setSeedError(err instanceof Error ? err.message : "Network error");
      })
      .finally(() => setSeeding(false));
  };

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

  return (
    <div className="flex flex-col" style={{ gap: 18 }} data-testid="agents-tab">
      {/* Page heading — Quartr serif */}
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

      {/* Filter chips — Quartr pills */}
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

      {/* States */}
      {state.kind === "loading" && <AgentsGridSkeleton />}

      {state.kind === "error" && (
        <div
          role="alert"
          className="flex flex-col items-center justify-center py-12 text-center"
          data-testid="agents-error"
        >
          <AlertCircle className="mb-3 h-6 w-6 text-destructive" aria-hidden="true" />
          <p className="text-sm font-medium">Couldn&apos;t load agents</p>
          <p className="mt-1 text-xs text-muted-foreground">{state.message}</p>
          <Button
            variant="outline"
            size="sm"
            className="mt-4"
            onClick={() => load(filter)}
          >
            <RefreshCw className="mr-1.5 h-3.5 w-3.5" aria-hidden="true" />
            Retry
          </Button>
        </div>
      )}

      {state.kind === "ok" && state.items.length === 0 && (
        <div
          className="flex flex-col items-center justify-center py-12 text-center"
          data-testid="agents-empty"
        >
          <Bot className="mb-3 h-8 w-8 text-muted-foreground" aria-hidden="true" />
          <p className="text-sm font-medium">No agents yet</p>
          <p className="mt-1 text-xs text-muted-foreground">
            Start a chat to propose one, or try the example below.
          </p>
          <Button
            variant="outline"
            size="sm"
            className="mt-4 gap-1.5"
            onClick={seedDemoAgent}
            disabled={seeding}
            data-testid="create-example-agent-btn"
          >
            <Sparkles className="h-3.5 w-3.5" aria-hidden="true" />
            {seeding ? "Creating..." : "Create example agent"}
          </Button>
          {seedError && (
            <p
              className="mt-2 text-xs text-destructive"
              role="alert"
              data-testid="seed-error"
            >
              {seedError}
            </p>
          )}
        </div>
      )}

      {state.kind === "ok" && state.items.length > 0 && (
        <div
          className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4"
          data-testid="agents-list"
          role="list"
          style={{ gap: 14 }}
        >
          {state.items.map((wf, idx) => (
            // h-full makes the cell stretch to the tallest row, so the
            // card inside always reaches full height and the footer
            // (VIEW AGENT) stays bottom-aligned across rows.
            <div key={wf.id} role="listitem" className="h-full">
              <AgentFileCard
                workflow={wf}
                seq={idx + 1}
                isOpening={openingId === wf.id}
                onSelect={() => handleSelect(wf.id)}
              />
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// AgentFileCard — file-folder style
// ---------------------------------------------------------------------------

/** Map pivot's RiskLevel to a CSS color value (Quartr's source uses
 *  --color-loss for the risk strip, regardless of level). */
function riskHex(risk: RiskLevel): string {
  if (risk === "HIGH RISK") return "var(--color-loss)";
  if (risk === "MEDIUM RISK") return "var(--color-warn)";
  return "var(--text-tertiary)";
}

function AgentFileCard({
  workflow,
  seq,
  isOpening,
  onSelect,
}: {
  workflow: WorkflowSummary;
  seq: number;
  isOpening: boolean;
  onSelect: () => void;
}): React.ReactElement {
  const category = deriveCategory(workflow);
  const risk = deriveRisk(workflow);
  const method = deriveMethod(workflow);
  const universe = deriveUniverse(workflow);
  const cadence = deriveCadence(workflow);
  const titleText = workflow.name.endsWith(".") ? workflow.name : `${workflow.name}.`;

  return (
    <div
      data-testid={`agent-card-${workflow.id}`}
      className="flex h-full flex-col"
      style={{
        background: "var(--bg-primary)",
        border: "1px solid var(--glass-border)",
        borderRadius: 0,
        fontFamily: "var(--font-ui)",
        position: "relative",
        opacity: isOpening ? 0.6 : 1,
        color: "var(--text-primary)",
        transition: "border-color 0.25s var(--ease-quartr), opacity 0.2s var(--ease-quartr)",
      }}
      onMouseEnter={(e) => { e.currentTarget.style.borderColor = "var(--glass-border-hover)"; }}
      onMouseLeave={(e) => { e.currentTarget.style.borderColor = "var(--glass-border)"; }}
    >
      {/* Top bar — FILE NNN / FAMILY · RISK */}
      <div
        className="flex items-center justify-between"
        style={{
          padding: "12px 16px",
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
          FILE {String(seq).padStart(3, "0")} / {category}
        </span>
        <span
          style={{
            fontFamily: "var(--font-ui)",
            fontSize: 10,
            letterSpacing: "0.18em",
            fontWeight: 600,
            color: riskHex(risk),
          }}
        >
          {risk}
        </span>
      </div>

      {/* Body — flex column. The slack goes BETWEEN the sparkline and
          the KV grid so the KV labels (METHOD/UNIVERSE/...) line up
          across cards in the same row, no matter how short the title
          or description is. */}
      <div className="flex flex-col" style={{ padding: 16, flex: 1 }}>
        <h3
          className="m-0"
          style={{
            fontFamily: "var(--font-ui)",
            fontSize: 26,
            letterSpacing: "-0.025em",
            lineHeight: 1.05,
            fontWeight: 600,
            color: "var(--text-primary)",
          }}
        >
          {titleText}
        </h3>

        <Sparkline seed={workflow.id} positive={true} />

        {/* Spacer — soaks up the height difference between cards so
            the KV grid below is bottom-aligned to the footer. */}
        <div style={{ flex: 1 }} aria-hidden={true} />

        <div
          style={{
            display: "grid",
            gridTemplateColumns: "auto 1fr",
            rowGap: 6,
            columnGap: 14,
            marginTop: 4,
            fontSize: 12,
            fontFamily: "var(--font-ui)",
          }}
        >
          <span style={{ color: "var(--text-tertiary)", letterSpacing: "0.06em" }}>METHOD</span>
          <span style={{ color: "var(--text-secondary)" }}>{method}</span>
          <span style={{ color: "var(--text-tertiary)", letterSpacing: "0.06em" }}>UNIVERSE</span>
          <span style={{ color: "var(--text-secondary)" }}>{universe}</span>
          <span style={{ color: "var(--text-tertiary)", letterSpacing: "0.06em" }}>CADENCE</span>
          <span style={{ color: "var(--text-secondary)" }}>{cadence}</span>
        </div>
      </div>

      {/* Footer button — full-width clickable strip */}
      <button
        type="button"
        onClick={onSelect}
        disabled={isOpening}
        aria-label={`View agent: ${workflow.name}`}
        data-testid={`agent-row-${workflow.id}`}
        className="flex items-center justify-between"
        style={{
          padding: "10px 16px",
          background: "var(--bg-elevated)",
          color: "var(--text-primary)",
          border: "none",
          borderTop: "1px solid var(--glass-border)",
          textAlign: "left",
          cursor: isOpening ? "wait" : "pointer",
          opacity: isOpening ? 0.6 : 1,
          transition: "background-color 0.25s var(--ease-quartr)",
        }}
        onMouseEnter={(e) => { if (!isOpening) e.currentTarget.style.background = "var(--bg-secondary)"; }}
        onMouseLeave={(e) => { if (!isOpening) e.currentTarget.style.background = "var(--bg-elevated)"; }}
      >
        <span
          className="inline-flex items-center"
          style={{
            gap: 6,
            fontFamily: "var(--font-ui)",
            fontSize: 10,
            fontWeight: 600,
            letterSpacing: "0.1em",
            color: "var(--text-secondary)",
          }}
        >
          <Play size={10} fill="currentColor" strokeWidth={0} aria-hidden="true" />
          VIEW AGENT
        </span>
      </button>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Sparkline — deterministic seeded series, mirrors FQ's StrategyList::Sparkline
// ---------------------------------------------------------------------------

function Sparkline({ seed, positive }: { seed: string; positive: boolean }): React.ReactElement {
  const POINTS = 40;
  const W = 280;
  const H = 56;
  const numericSeed = String(seed || "x")
    .split("")
    .reduce((a, c) => a + c.charCodeAt(0), 0);

  let v = 50;
  const series: number[] = [];
  for (let i = 0; i < POINTS; i++) {
    const r = Math.sin((i + numericSeed) * 0.41) + Math.cos((i + numericSeed) * 0.19);
    v += (positive ? 0.45 : -0.4) + r * 1.4;
    series.push(v);
  }

  const min = Math.min(...series);
  const max = Math.max(...series);
  const span = Math.max(1, max - min);

  const xAt = (i: number): number => (i / (POINTS - 1)) * W;
  const yAt = (val: number): number => H - ((val - min) / span) * (H - 6) - 3;

  const linePath = series
    .map((val, i) => `${i === 0 ? "M" : "L"} ${xAt(i).toFixed(1)} ${yAt(val).toFixed(1)}`)
    .join(" ");
  const areaPath = `${linePath} L ${W} ${H} L 0 ${H} Z`;
  const gradId = `spark-${numericSeed}`;
  const stroke = positive ? "var(--color-profit)" : "var(--color-loss)";
  const fillTop = positive ? "rgba(16, 185, 129, 0.22)" : "rgba(239, 68, 68, 0.22)";
  const fillBot = positive ? "rgba(16, 185, 129, 0)" : "rgba(239, 68, 68, 0)";

  return (
    <svg
      viewBox={`0 0 ${W} ${H}`}
      width="100%"
      height={H}
      preserveAspectRatio="none"
      style={{ display: "block", marginTop: 14 }}
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
        vectorEffect="non-scaling-stroke"
      />
    </svg>
  );
}

// ---------------------------------------------------------------------------
// Skeleton
// ---------------------------------------------------------------------------

function AgentsGridSkeleton(): React.ReactElement {
  return (
    <div
      className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3"
      data-testid="agents-loading"
    >
      {Array.from({ length: 6 }).map((_, i) => (
        <Skeleton key={i} className="h-52 w-full rounded-xl" />
      ))}
    </div>
  );
}
