"use client";

/**
 * ExpressionCard — one expression column in the ladder.
 *
 * DESIGN LAW (see ViewSurface.tsx): SQUARE container, BORDERS-ONLY, no fills,
 * no DS <Panel>/<MonoTag>. We render the plain_* fields the backend serves —
 * NEVER the raw label/rationale/risk_profile/capital_intensity or any enum.
 *
 * Layout (top → bottom, separated by hairlines + whitespace):
 *   header   — tier word · Recommended tag · trust dot + plain badge
 *   title    — plain_label (18/600, 2-line clamp) + plain_one_liner (15)
 *   basket   — members as a plain comma list (or an honest "still refining")
 *   capital  — "Roughly how much it needs: " + capital_label (LABEL only)
 *   metrics  — FIXED 3-metric grid: Total return · vs Nifty · Worst drop
 *   why/risk — one line plain_why + one line plain_risk
 *   CTA      — "Deploy" (solid ink, square)
 *
 * Deploy flow (register-not-execute) preserved verbatim:
 *   workflow_id → onOpenWorkflowById(workflow_id)
 *   null → deployExpression(id) → onOpenWorkflowById(result.workflow_id)
 */

import * as React from "react";
import { Loader2 } from "lucide-react";
import type { ExpressionDetail } from "@/lib/types";
import { isError } from "@/lib/types";
import { deployExpression } from "@/lib/api";
import { ViewSurface, Hairline } from "./ViewSurface";
import { Num } from "./Stat";
import {
  tierLabel,
  capitalLabel,
  fmtPct,
  signColor,
  verdictColor,
  verdictLabel,
} from "./view-format";

const FONT = "var(--font-display)";

// ---------------------------------------------------------------------------
// Small text helpers (calm, >=13px)
// ---------------------------------------------------------------------------

function Label({
  children,
  color = "var(--text-tertiary)",
}: {
  children: React.ReactNode;
  color?: string;
}): React.ReactElement {
  return (
    <span
      style={{
        fontFamily: FONT,
        fontSize: 13,
        fontWeight: 500,
        color,
        lineHeight: 1.3,
      }}
    >
      {children}
    </span>
  );
}

function Line({
  children,
  color = "var(--text-secondary)",
  size = 15,
  clamp,
}: {
  children: React.ReactNode;
  color?: string;
  size?: number;
  clamp?: number;
}): React.ReactElement {
  const clampStyle: React.CSSProperties = clamp
    ? {
        overflow: "hidden",
        display: "-webkit-box",
        WebkitLineClamp: clamp,
        WebkitBoxOrient: "vertical",
      }
    : {};
  return (
    <p
      style={{
        fontFamily: FONT,
        fontSize: size,
        fontWeight: 400,
        color,
        lineHeight: 1.45,
        margin: 0,
        ...clampStyle,
      }}
    >
      {children}
    </p>
  );
}

// One fixed metric tile (label on top, value below).
function Metric({
  label,
  value,
  color,
}: {
  label: string;
  value: string;
  color: string;
}): React.ReactElement {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 4, minWidth: 0 }}>
      <Label>{label}</Label>
      <Num size="value" color={color}>
        {value}
      </Num>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main ExpressionCard
// ---------------------------------------------------------------------------

export interface ExpressionCardProps {
  expression: ExpressionDetail;
  recommended?: boolean;
  onOpenWorkflowById: (id: string) => void;
}

export function ExpressionCard({
  expression,
  recommended = false,
  onOpenWorkflowById,
}: ExpressionCardProps): React.ReactElement {
  const [deploying, setDeploying] = React.useState(false);
  const [deployError, setDeployError] = React.useState<string | null>(null);

  const verdict = expression.scores?.backtest?.trust_verdict ?? null;
  const trustWord = expression.trust_badge ?? verdictLabel(verdict);
  const trustColor = verdictColor(verdict);

  const members = expression.members ?? [];
  const hasBasket = members.length > 0;

  async function handleDeploy() {
    if (deploying) return;
    setDeployError(null);
    if (expression.workflow_id) {
      onOpenWorkflowById(expression.workflow_id);
      return;
    }
    setDeploying(true);
    const res = await deployExpression(expression.id);
    setDeploying(false);
    if (isError(res)) {
      setDeployError(res.error.message);
      return;
    }
    onOpenWorkflowById(res.data.workflow_id);
  }

  const deployDisabled =
    deploying || (!expression.is_deployable && !expression.workflow_id);

  return (
    <ViewSurface
      interactive
      style={{
        height: "100%",
        display: "flex",
        flexDirection: "column",
        gap: 16,
        ...(recommended
          ? { borderColor: "var(--glass-border-focus)" }
          : {}),
      }}
    >
      {/* ── HEADER ── */}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          gap: 8,
          flexWrap: "wrap",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <Label color="var(--text-secondary)">
            {tierLabel(expression.tier)}
          </Label>
          {recommended && (
            <span
              style={{
                fontFamily: FONT,
                fontSize: 13,
                fontWeight: 500,
                color: "var(--pivot-blue)",
                border: "1px solid var(--glass-border-focus)",
                borderRadius: "var(--radius-pill)",
                padding: "1px 7px",
                lineHeight: 1.4,
              }}
            >
              Recommended
            </span>
          )}
        </div>
        <span
          style={{ display: "inline-flex", alignItems: "center", gap: 6 }}
        >
          <span
            aria-hidden
            style={{
              width: 7,
              height: 7,
              borderRadius: "50%",
              background: trustColor,
              flexShrink: 0,
            }}
          />
          <Label color={trustColor}>{trustWord}</Label>
        </span>
      </div>

      {/* ── TITLE ── */}
      <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
        <h3
          style={{
            fontFamily: FONT,
            fontSize: 18,
            fontWeight: 600,
            lineHeight: 1.3,
            letterSpacing: "-0.01em",
            color: "var(--text-primary)",
            margin: 0,
            overflow: "hidden",
            display: "-webkit-box",
            WebkitLineClamp: 2,
            WebkitBoxOrient: "vertical",
            minHeight: `calc(18px * 1.3 * 2)`,
          }}
        >
          {expression.plain_label ?? tierLabel(expression.tier)}
        </h3>
        {expression.plain_one_liner && (
          <Line clamp={3}>{expression.plain_one_liner}</Line>
        )}
      </div>

      <Hairline />

      {/* ── BASKET ── */}
      <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
        <Label>What you&apos;d hold</Label>
        {hasBasket ? (
          <Line color="var(--text-primary)">{members.join(", ")}</Line>
        ) : (
          <Line color="var(--text-tertiary)">
            Still being refined — no finished basket to show yet.
          </Line>
        )}
      </div>

      {/* ── CAPITAL ── */}
      <Line color="var(--text-secondary)" size={13}>
        Roughly how much it needs:{" "}
        <span style={{ color: "var(--text-primary)", fontWeight: 500 }}>
          {capitalLabel(expression.capital_label)}
        </span>
      </Line>

      <Hairline />

      {/* ── FIXED 3-METRIC GRID ── */}
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(3, minmax(0, 1fr))",
          gap: 12,
        }}
      >
        <Metric
          label="Total return"
          value={fmtPct(expression.strategy_total_pct)}
          color={signColor(expression.strategy_total_pct)}
        />
        <Metric
          label="vs Nifty"
          value={fmtPct(expression.nifty_total_pct)}
          color="var(--text-secondary)"
        />
        <Metric
          label="Worst drop"
          value={fmtPct(expression.worst_drop_pct)}
          color={signColor(expression.worst_drop_pct)}
        />
      </div>

      {/* ── WHY / RISK ── */}
      {(expression.plain_why || expression.plain_risk) && (
        <>
          <Hairline />
          <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
            {expression.plain_why && (
              <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
                <Label>Why</Label>
                <Line size={13} clamp={2}>
                  {expression.plain_why}
                </Line>
              </div>
            )}
            {expression.plain_risk && (
              <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
                <Label>Risk</Label>
                <Line size={13} clamp={2}>
                  {expression.plain_risk}
                </Line>
              </div>
            )}
          </div>
        </>
      )}

      {/* ── CTA ── pushed to the bottom so columns line up ── */}
      <div
        style={{
          marginTop: "auto",
          display: "flex",
          flexDirection: "column",
          gap: 8,
          paddingTop: 4,
        }}
      >
        <button
          type="button"
          onClick={handleDeploy}
          disabled={deployDisabled}
          aria-label="Deploy this expression"
          style={{
            display: "inline-flex",
            alignItems: "center",
            justifyContent: "center",
            gap: 8,
            width: "100%",
            fontFamily: FONT,
            fontSize: 15,
            fontWeight: 600,
            color: "hsl(var(--primary-foreground))",
            background: "hsl(var(--primary))",
            border: "1px solid hsl(var(--primary))",
            borderRadius: "var(--radius-md)",
            padding: "10px 16px",
            cursor: deployDisabled ? "default" : "pointer",
            opacity: deployDisabled ? 0.6 : 1,
            transition: "opacity 180ms var(--ease-quartr)",
          }}
        >
          {deploying ? (
            <>
              <Loader2 size={14} className="animate-spin" aria-hidden />
              Arming…
            </>
          ) : (
            "Deploy"
          )}
        </button>
        {deployError && (
          <Line color="var(--color-loss)" size={13} clamp={2}>
            {deployError}
          </Line>
        )}
      </div>
    </ViewSurface>
  );
}
