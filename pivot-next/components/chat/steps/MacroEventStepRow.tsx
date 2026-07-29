"use client";

/**
 * MacroEventStepRow — renders a `trigger.scheduled_macro` step prettily
 * inside WorkflowDraftCard and the AgentPanel step list.
 *
 * The macro trigger is calendar-armed AND outcome-verified: it fires on
 * a known-date central-bank / CPI release ONLY once the outcome is
 * confirmed against the official source (with a prediction-market
 * fallback). This row surfaces (a) which event, (b) what outcome fires
 * it, (c) the source of truth, and (d) the "verifies before firing"
 * affordance that distinguishes it from a naive news-keyword trigger.
 *
 * Read-only. Editing config fields lives in the AgentPanel drawer.
 */

import { CalendarCheck, ShieldCheck } from "lucide-react";

const ACCENT = "#d97706"; // amber — distinct from the news teal
const ACCENT_BG = "rgba(217, 119, 6, 0.10)";

type MacroKind = "rbi_mpc" | "us_fomc" | "india_cpi" | "us_cpi";

export type MacroStepConfig = {
  kind?: MacroKind | string;
  expected_outcome?: string;
  min_confidence?: number;
  allow_prediction_market_fallback?: boolean;
  comparison?: string | null;
  threshold?: number | null;
};

type MacroEventStepRowProps = {
  step: {
    step_type: "trigger.scheduled_macro";
    config: MacroStepConfig;
    label?: string | null;
  };
};

const KIND_LABEL: Record<string, string> = {
  rbi_mpc: "RBI MPC — repo-rate decision",
  us_fomc: "US Fed (FOMC) — rate decision",
  india_cpi: "India CPI — inflation print",
  us_cpi: "US CPI — inflation print",
};

const KIND_SOURCE: Record<string, string> = {
  rbi_mpc: "RBI Press Releases (official)",
  us_fomc: "Federal Reserve press releases (official)",
  india_cpi: "MOSPI via news feed",
  us_cpi: "BLS via news feed",
};

const RATE_OUTCOME: Record<string, string> = {
  cut: "a rate CUT",
  hold: "rates held UNCHANGED",
  hike: "a rate HIKE",
};

function describeOutcome(cfg: MacroStepConfig): string {
  const kind = String(cfg.kind ?? "");
  const outcome = String(cfg.expected_outcome ?? "");
  const isPrint = kind === "india_cpi" || kind === "us_cpi";
  if (isPrint) {
    const cmp = cfg.comparison ?? ">";
    const thr = cfg.threshold ?? null;
    if (thr === null) {
      return outcome === "met"
        ? "the print meets your threshold"
        : "the print misses your threshold";
    }
    const verb = outcome === "met" ? "Fires when" : "Fires unless";
    return `${verb} CPI is ${cmp} ${thr}%`;
  }
  return `Fires on ${RATE_OUTCOME[outcome] ?? outcome}`;
}

export function MacroEventStepRow({
  step,
}: MacroEventStepRowProps): React.ReactElement {
  const { config } = step;
  const kind = String(config.kind ?? "");
  const minConf = config.min_confidence ?? 0.85;
  const confPct = Math.round(minConf * 100);
  const kindLabel = KIND_LABEL[kind] ?? step.label ?? "Scheduled macro event";
  const source = KIND_SOURCE[kind] ?? "official source";
  const pmFallback = config.allow_prediction_market_fallback ?? true;

  return (
    <div
      data-testid="macro-event-step-row"
      style={{
        display: "flex",
        flexDirection: "column",
        gap: 10,
        padding: "12px 14px",
        background: "var(--bg-base)",
        border: "1px solid var(--glass-border)",
        borderRadius: "var(--radius-md)",
        fontFamily: "var(--font-ui)",
      }}
    >
      {/* 1. Header */}
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
            background: ACCENT_BG,
            color: ACCENT,
            flexShrink: 0,
          }}
        >
          <CalendarCheck size={14} strokeWidth={1.75} aria-hidden />
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
          {kindLabel}
        </span>
        <span
          style={{
            fontSize: 10,
            fontWeight: 500,
            padding: "2px 7px",
            // Rounded-rect (matches the "Agent" pill shape on the draft card),
            // no border — just the tinted fill.
            borderRadius: 6,
            background: ACCENT_BG,
            color: ACCENT,
            flexShrink: 0,
          }}
        >
          min_conf &ge; {confPct}%
        </span>
      </div>

      {/* 2. What fires it */}
      <p
        style={{
          margin: 0,
          fontSize: 12,
          color: "var(--text-secondary)",
          lineHeight: 1.45,
          fontWeight: 500,
        }}
      >
        {describeOutcome(config)}
      </p>

      {/* 3. Source of truth + verification affordance */}
      <div style={{ display: "flex", alignItems: "center", gap: 6, flexWrap: "wrap" }}>
        <span
          style={{
            display: "inline-flex",
            alignItems: "center",
            gap: 4,
            fontSize: 10.5,
            color: "var(--text-tertiary)",
          }}
        >
          <ShieldCheck size={12} strokeWidth={1.75} aria-hidden />
          Verified against: {source}
        </span>
      </div>

      {/* 4. Verify-before-fire note */}
      <p
        style={{
          margin: 0,
          fontSize: 10.5,
          color: "var(--text-tertiary)",
          lineHeight: 1.4,
        }}
      >
        Confirms the actual outcome before firing
        {pmFallback ? "; falls back to a prediction-market resolution if the official source is unclear." : "."}
      </p>
    </div>
  );
}
