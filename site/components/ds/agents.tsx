"use client";

/**
 * Pivot design system — agents & workflows.
 *
 * The automation vocabulary: agent summary cards (status, trigger tag,
 * next-run, mini equity curve) and the numbered trigger→condition→
 * action rail used by workflow draft cards. Register-not-execute is a
 * design value here: drafts read as *proposals* (outline, quiet) and
 * only armed agents carry the live pulse.
 */

import * as React from "react";
import { cn } from "@/lib/utils";
import {
  type AgentState,
  Figure,
  MonoTag,
  StatusPill,
  Title,
} from "./primitives";
import { Panel, SparkLine } from "./surfaces";

/* ────────────────────────────────────────────────────────────────────
 * Workflow steps
 * ──────────────────────────────────────────────────────────────────── */

export type StepKind = "trigger" | "condition" | "action" | "notify";

const STEP_LABEL: Record<StepKind, string> = {
  trigger: "TRIGGER",
  condition: "CONDITION",
  action: "ACTION",
  notify: "NOTIFY",
};

/**
 * One row of a workflow draft: index bullet on a hairline rail, mono
 * kind label, and the human-readable step description.
 */
export function WorkflowStep({
  index,
  kind,
  last = false,
  className,
  children,
}: {
  index: number;
  kind: StepKind;
  /** Hides the connecting rail below the bullet. */
  last?: boolean;
  className?: string;
  children: React.ReactNode;
}) {
  return (
    <div className={cn("relative flex gap-3.5 pb-5", last && "pb-0", className)}>
      {!last && (
        <span
          aria-hidden
          className="absolute left-[11px] top-6 bottom-0 w-px"
          style={{ background: "var(--glass-border-hover)" }}
        />
      )}
      <span
        className="relative z-[1] grid h-[23px] w-[23px] shrink-0 place-items-center"
        style={{
          borderRadius: "50%",
          border: "1px solid var(--glass-border-focus)",
          background: "var(--bg-card)",
          fontFamily: "var(--font-mono)",
          fontSize: 10.5,
          fontWeight: 600,
          color: "var(--text-secondary)",
        }}
      >
        {index}
      </span>
      <div className="min-w-0 pt-0.5">
        <div
          style={{
            fontFamily: "var(--font-mono)",
            fontSize: 9.5,
            fontWeight: 600,
            letterSpacing: "0.12em",
            color: "var(--text-tertiary)",
            marginBottom: 3,
          }}
        >
          {STEP_LABEL[kind]}
        </div>
        <div
          style={{
            fontFamily: "var(--font-ui)",
            fontSize: 13.5,
            lineHeight: 1.55,
            color: "var(--text-primary)",
          }}
        >
          {children}
        </div>
      </div>
    </div>
  );
}

/* ────────────────────────────────────────────────────────────────────
 * Agent card
 * ──────────────────────────────────────────────────────────────────── */

/**
 * Agent summary card for rails/lists/dashboards. Quiet by default;
 * the status dot is the only live element. `equity` (normalised
 * series) renders a signed sparkline footer.
 */
export function AgentCard({
  name,
  tag,
  state,
  nextRun,
  lastRun,
  equity,
  footer,
  className,
  onClick,
}: {
  name: string;
  /** Trigger/category microlabel, e.g. "RSI < 30", "FRI 09:30". */
  tag: string;
  state: AgentState;
  nextRun?: string;
  lastRun?: string;
  /** Normalised series for the footer sparkline (signed coloring). */
  equity?: number[];
  /** Custom footer; overrides the equity sparkline row. */
  footer?: React.ReactNode;
  className?: string;
  onClick?: () => void;
}) {
  return (
    <Panel
      variant={state === "draft" ? "outline" : "paper"}
      pad={18}
      interactive={Boolean(onClick)}
      onClick={onClick}
      className={className}
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <Title size={15} style={{ marginBottom: 6 }}>
            {name}
          </Title>
          <MonoTag tone="fill">{tag}</MonoTag>
        </div>
        <StatusPill state={state} />
      </div>

      {(nextRun || lastRun) && (
        <div
          className="mt-4 flex items-center gap-4"
          style={{
            fontFamily: "var(--font-ui)",
            fontSize: 12,
            color: "var(--text-tertiary)",
          }}
        >
          {nextRun && (
            <span>
              next{" "}
              <span
                style={{
                  color: "var(--text-secondary)",
                  fontVariantNumeric: "tabular-nums",
                }}
              >
                {nextRun}
              </span>
            </span>
          )}
          {lastRun && (
            <span>
              last{" "}
              <span
                style={{
                  color: "var(--text-secondary)",
                  fontVariantNumeric: "tabular-nums",
                }}
              >
                {lastRun}
              </span>
            </span>
          )}
        </div>
      )}

      {footer ? footer : <AgentEquityFooter equity={equity} />}
    </Panel>
  );
}

/** Signed paper-P&L footer; null when no usable series. */
function AgentEquityFooter({ equity }: { equity?: number[] }) {
  const first = equity?.[0];
  const last = equity?.[equity.length - 1];
  if (!equity || equity.length < 2 || first === undefined || last === undefined || first === 0) {
    return null;
  }
  const pct = ((last - first) / first) * 100;
  return (
            <div
              className="mt-4 flex items-end justify-between gap-3 border-t pt-3"
              style={{ borderColor: "var(--glass-border)" }}
            >
              <div>
                <div
                  style={{
                    fontFamily: "var(--font-mono)",
                    fontSize: 9.5,
                    fontWeight: 500,
                    letterSpacing: "0.1em",
                    textTransform: "uppercase",
                    color: "var(--text-tertiary)",
                    marginBottom: 4,
                  }}
                >
                  Paper P&amp;L
                </div>
                <Figure size={15}>
                  {(pct >= 0 ? "+" : "") + pct.toFixed(1)}%
                </Figure>
              </div>
              <SparkLine data={equity} signed width={104} height={30} />
            </div>
  );
}