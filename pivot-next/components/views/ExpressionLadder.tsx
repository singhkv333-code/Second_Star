"use client";

/**
 * ExpressionLadder — three IDENTICAL square columns
 * (Conservative / Balanced / Aggressive).
 *
 * DESIGN LAW: the columns are equal size. There is NO negative margin, NO fill,
 * NO outline that resizes the recommended column — the recommendation is marked
 * with a plain inline 'Recommended' tag INSIDE the equal-size card. Sections
 * separate via hairline + whitespace, never nested filled boxes.
 *
 * The "Compare tiers" action and its rationale are owned by the parent
 * (ViewDetailPage). The dead onCompare/compareRationale block has been removed.
 */

import * as React from "react";
import type { ExpressionDetail, ExpressionTier } from "@/lib/types";
import { ExpressionCard } from "./ExpressionCard";
import { ViewSurface } from "./ViewSurface";
import { tierLabel } from "./view-format";

const TIERS: ExpressionTier[] = ["conservative", "balanced", "aggressive"];

const FONT = "var(--font-display)";

// ---------------------------------------------------------------------------
// Sort within a tier — backend already exposes one expression per tier in the
// common case; if several share a tier, surface the strongest (higher episodes
// beaten, then higher total return) first.
// ---------------------------------------------------------------------------

function tierStrength(e: ExpressionDetail): number {
  const beat = e.pct_episodes_beat ?? 0;
  const ret = e.strategy_total_pct ?? 0;
  return beat * 1000 + ret;
}

// ---------------------------------------------------------------------------
// Empty-tier card — SQUARE, border-only, equal size to a filled column.
// ---------------------------------------------------------------------------

function EmptyTierCard({ tier }: { tier: ExpressionTier }): React.ReactElement {
  return (
    <ViewSurface style={{ height: "100%" }}>
      <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
        <span
          style={{
            fontFamily: FONT,
            fontSize: 13,
            fontWeight: 500,
            color: "var(--text-tertiary)",
          }}
        >
          {tierLabel(tier)}
        </span>
        <p
          style={{
            fontFamily: FONT,
            fontSize: 15,
            fontWeight: 400,
            color: "var(--text-secondary)",
            lineHeight: 1.5,
            margin: 0,
          }}
        >
          No finished expression in this tier yet.
        </p>
      </div>
    </ViewSurface>
  );
}

// ---------------------------------------------------------------------------
// Main ExpressionLadder
// ---------------------------------------------------------------------------

export interface ExpressionLadderProps {
  expressions: ExpressionDetail[];
  recommendedTier?: ExpressionTier | null;
  onOpenWorkflowById: (id: string) => void;
}

export function ExpressionLadder({
  expressions,
  recommendedTier,
  onOpenWorkflowById,
}: ExpressionLadderProps): React.ReactElement {
  if (!expressions || expressions.length === 0) {
    return (
      <p
        style={{
          fontFamily: FONT,
          fontSize: 15,
          color: "var(--text-tertiary)",
          margin: 0,
        }}
      >
        No expressions published yet.
      </p>
    );
  }

  const byTier = TIERS.reduce<Record<ExpressionTier, ExpressionDetail[]>>(
    (acc, t) => {
      acc[t] = expressions
        .filter((e) => e.tier === t)
        .sort((a, b) => tierStrength(b) - tierStrength(a));
      return acc;
    },
    { conservative: [], balanced: [], aggressive: [] },
  );

  return (
    <div
      className="grid grid-cols-1 lg:grid-cols-3"
      style={{ gap: 20, alignItems: "stretch" }}
    >
      {TIERS.map((tier) => {
        const top = byTier[tier][0] ?? null;
        return (
          <div key={tier} style={{ height: "100%" }}>
            {top ? (
              <ExpressionCard
                expression={top}
                recommended={recommendedTier === tier}
                onOpenWorkflowById={onOpenWorkflowById}
              />
            ) : (
              <EmptyTierCard tier={tier} />
            )}
          </div>
        );
      })}
    </div>
  );
}
