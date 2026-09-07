"use client";

/**
 * IpoStepRow — renders trigger.ipo_open and action.arm_ipo_intent workflow
 * step rows inside WorkflowDraftCard and AgentPanel step lists.
 *
 * Mirrors NewsStepRow.tsx patterns: read-only, inline style tokens, no new
 * dependencies. Editing config is deferred to P3.
 *
 * Supported step types:
 *   - trigger.ipo_open      { symbol: string }
 *   - action.arm_ipo_intent { ipo_symbol: string, quantity_lots: number,
 *                             category: string, bid_price_mode: string,
 *                             bid_price?: number | null }
 */

import { FileCheck, Rocket } from "lucide-react";

// ---------------------------------------------------------------------------
// Config shapes (subset we render)
// ---------------------------------------------------------------------------

type IpoOpenConfig = {
  symbol?: string;
  [extra: string]: unknown;
};

type ArmIpoIntentConfig = {
  ipo_symbol?: string;
  quantity_lots?: number;
  category?: string;
  bid_price_mode?: string;
  bid_price?: number | null;
  [extra: string]: unknown;
};

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

export type IpoStepRowProps = {
  step: {
    step_type: "trigger.ipo_open" | "action.arm_ipo_intent";
    config: IpoOpenConfig | ArmIpoIntentConfig;
    label?: string | null;
  };
};

// ---------------------------------------------------------------------------
// Category labels (mirrors backend CATEGORY_LABELS in IpoApplicationCard)
// ---------------------------------------------------------------------------

const CATEGORY_LABELS: Record<string, string> = {
  retail: "Retail",
  snii: "sNII",
  bnii: "bNII",
  shareholder: "Shareholder",
  employee: "Employee",
};

// ---------------------------------------------------------------------------
// IpoStepRow
// ---------------------------------------------------------------------------

export function IpoStepRow({ step }: IpoStepRowProps): React.ReactElement {
  if (step.step_type === "trigger.ipo_open") {
    return <IpoOpenRow config={step.config as IpoOpenConfig} />;
  }
  return <ArmIntentRow config={step.config as ArmIpoIntentConfig} />;
}

// ---------------------------------------------------------------------------
// trigger.ipo_open row
// ---------------------------------------------------------------------------

function IpoOpenRow({ config }: { config: IpoOpenConfig }): React.ReactElement {
  const symbol = config.symbol ?? "—";

  return (
    <div
      data-testid="ipo-step-row-trigger"
      style={{
        display: "flex",
        flexDirection: "column",
        gap: 8,
        padding: "12px 14px",
        background: "var(--bg-primary)",
        border: "1px solid var(--glass-border)",
        borderRadius: "var(--radius-md)",
        fontFamily: "var(--font-ui)",
      }}
    >
      {/* Header */}
      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
        <span
          aria-hidden="true"
          style={{
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            width: 28,
            height: 28,
            borderRadius: 6,
            background: "rgba(139, 92, 246, 0.10)",
            color: "#8b5cf6",
            flexShrink: 0,
          }}
        >
          <Rocket size={14} strokeWidth={1.75} aria-hidden />
        </span>
        <span
          style={{
            fontSize: 12.5,
            fontWeight: 600,
            color: "var(--text-primary)",
            flex: 1,
            letterSpacing: "-0.01em",
          }}
        >
          On IPO open
        </span>
        <span
          style={{
            fontSize: 10,
            fontWeight: 500,
            padding: "2px 7px",
            borderRadius: "var(--radius-pill)",
            background: "rgba(139, 92, 246, 0.08)",
            color: "#8b5cf6",
            border: "1px solid rgba(139, 92, 246, 0.18)",
            flexShrink: 0,
          }}
        >
          trigger
        </span>
      </div>

      {/* Symbol chip */}
      <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
        <span
          style={{
            fontSize: 12,
            fontWeight: 600,
            padding: "3px 10px",
            borderRadius: "var(--radius-pill)",
            background: "var(--bg-elevated)",
            border: "1px solid var(--glass-border)",
            color: "var(--text-primary)",
            letterSpacing: "0.04em",
            textTransform: "uppercase",
          }}
        >
          {symbol}
        </span>
        <span
          style={{
            fontSize: 11.5,
            color: "var(--text-secondary)",
          }}
        >
          subscription window opens
        </span>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// action.arm_ipo_intent row
// ---------------------------------------------------------------------------

function ArmIntentRow({
  config,
}: {
  config: ArmIpoIntentConfig;
}): React.ReactElement {
  const symbol = config.ipo_symbol ?? "—";
  const lots = config.quantity_lots ?? "—";
  const category = config.category
    ? (CATEGORY_LABELS[config.category] ?? config.category)
    : "—";
  const mode = config.bid_price_mode ?? "—";
  const price = config.bid_price != null ? `₹${config.bid_price}` : null;

  return (
    <div
      data-testid="ipo-step-row-action"
      style={{
        display: "flex",
        flexDirection: "column",
        gap: 8,
        padding: "12px 14px",
        background: "var(--bg-primary)",
        border: "1px solid var(--glass-border)",
        borderRadius: "var(--radius-md)",
        fontFamily: "var(--font-ui)",
      }}
    >
      {/* Header */}
      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
        <span
          aria-hidden="true"
          style={{
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            width: 28,
            height: 28,
            borderRadius: 6,
            background: "rgba(16, 185, 129, 0.10)",
            color: "#10b981",
            flexShrink: 0,
          }}
        >
          <FileCheck size={14} strokeWidth={1.75} aria-hidden />
        </span>
        <span
          style={{
            fontSize: 12.5,
            fontWeight: 600,
            color: "var(--text-primary)",
            flex: 1,
            letterSpacing: "-0.01em",
          }}
        >
          Arm IPO intent
        </span>
        <span
          style={{
            fontSize: 10,
            fontWeight: 500,
            padding: "2px 7px",
            borderRadius: "var(--radius-pill)",
            background: "rgba(16, 185, 129, 0.08)",
            color: "#10b981",
            border: "1px solid rgba(16, 185, 129, 0.18)",
            flexShrink: 0,
          }}
        >
          no broker call
        </span>
      </div>

      {/* Param chips row */}
      <div style={{ display: "flex", flexWrap: "wrap", gap: 5 }}>
        <ParamChip label="symbol" value={String(symbol).toUpperCase()} />
        <ParamChip label="lots" value={String(lots)} />
        <ParamChip label="category" value={category} />
        <ParamChip label="bid" value={mode === "cutoff" ? "cutoff" : (price ?? mode)} />
      </div>

      {/* Disclaimer nudge */}
      <p
        style={{
          margin: 0,
          fontSize: 11,
          color: "var(--text-tertiary)",
          fontStyle: "italic",
          lineHeight: 1.4,
        }}
      >
        Records intent only — you must place the bid and approve the UPI mandate yourself.
      </p>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Small helper
// ---------------------------------------------------------------------------

function ParamChip({
  label,
  value,
}: {
  label: string;
  value: string;
}): React.ReactElement {
  return (
    <span
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: 4,
        fontSize: 11,
        fontWeight: 500,
        padding: "2px 8px",
        borderRadius: "var(--radius-pill)",
        background: "var(--bg-elevated)",
        border: "1px solid var(--glass-border)",
        color: "var(--text-secondary)",
      }}
    >
      <span style={{ color: "var(--text-tertiary)", fontWeight: 400 }}>{label}:</span>
      {value}
    </span>
  );
}
