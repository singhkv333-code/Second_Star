"use client";

/**
 * /_demo/widgets — non-routed sandbox used to capture screenshots of
 * Pivot's chat widgets with deterministic mock data. Not linked from
 * anywhere; render-only, no API dependencies.
 */

import { StockSnapshotCard } from "@/components/chat/StockSnapshotCard";
import { WorkflowDraftCard, type WorkflowDraft } from "@/components/chat/WorkflowDraftCard";

const MOCK_DRAFT: WorkflowDraft = {
  name: "RELIANCE Weekday Dip-Buy",
  description:
    "Every Friday at 3:55 PM IST, if RELIANCE is down 1% or more intraday, market-buy ₹10,000 worth of shares.",
  rationale:
    "Combines a fixed-day schedule with a dip filter so capital deploys only on local pullbacks, not on every Friday.",
  warnings: ["Live trading is disabled — orders register only."],
  _render_hint: "workflow_draft_card",
  steps: [
    {
      step_type: "schedule_trigger",
      label: "Every Friday, 3:55 PM IST",
      config: { cron: "55 15 * * 5", timezone: "Asia/Kolkata" },
    },
    {
      step_type: "fetch_quote",
      label: "Pull RELIANCE intraday quote",
      config: { symbol: "RELIANCE", exchange: "NSE" },
    },
    {
      step_type: "condition",
      label: "Day change ≤ −1%",
      config: { field: "change_pct", op: "lte", value: -1 },
    },
    {
      step_type: "register_order",
      label: "Register buy ₹10,000 (CNC)",
      config: {
        symbol: "RELIANCE",
        side: "buy",
        amount_inr: 10000,
        product: "CNC",
      },
    },
  ],
};

export default function DemoWidgets(): React.ReactElement {
  return (
    <div
      style={{
        minHeight: "100vh",
        background: "var(--bg-base)",
        padding: 56,
        display: "flex",
        flexDirection: "column",
        gap: 40,
      }}
    >
      <h1
        style={{
          fontFamily: "var(--font-experiment)",
          fontSize: 32,
          color: "var(--text-primary)",
          letterSpacing: "-0.02em",
          margin: 0,
        }}
      >
        Widget sandbox
      </h1>

      <div style={{ display: "flex", gap: 28, alignItems: "flex-start", flexWrap: "wrap" }}>
        {/* StockSnapshotCard — fetches its own data; symbol drives it. */}
        <div style={{ width: 520 }}>
          <StockSnapshotCard symbol="RELIANCE" exchange="NSE" />
        </div>

        {/* WorkflowDraftCard — mocked draft, no save/run side effects. */}
        <div style={{ width: 600 }}>
          <WorkflowDraftCard
            draft={MOCK_DRAFT}
            onOpenEditor={() => undefined}
          />
        </div>
      </div>
    </div>
  );
}
