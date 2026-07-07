"use client";

/**
 * StrategiesEditorial — the strategies section on the View detail page, as a
 * row of clean cards (one <StrategyCleanCard/> per expression) sitting side by
 * side: conservative · balanced · aggressive. No table, no single selected
 * panel — every strategy is shown in full at once.
 *
 * Each card's "See the full analysis" opens that strategy's <StrategyDeepDive/>
 * accordion below the row (the parent tracks which one is open). DESIGN LAW:
 * rounded, border-only, no pastel fills, color is for data, plain language.
 */

import * as React from "react";
import type { ExpressionDetail } from "@/lib/types";
import { StrategyCleanCard } from "@/components/views/StrategyCleanCard";

export function StrategiesEditorial({
  expressions,
  amount,
  openAnalysisId = null,
  onToggleAnalysis,
}: {
  expressions: ExpressionDetail[];
  /** The shared ticket ₹ amount, so each card's "If you put in ₹X" tracks it. */
  amount: number;
  /** Which strategy's full-analysis accordion is currently open (or null). */
  openAnalysisId?: string | null;
  /** Toggle the full-analysis accordion for a given strategy. */
  onToggleAnalysis?: (id: string) => void;
}): React.ReactElement | null {
  if (expressions.length === 0) return null;

  return (
    <div className="vwd-strats">
      <style>{`
        .vwd-strats {
          display: grid;
          grid-template-columns: repeat(${expressions.length}, minmax(0, 1fr));
          /* Two shared row tracks: the top region (header + facts) sizes to the
             tallest card, so every outcome box below it starts at the same Y;
             the rest region (1fr) equalizes the card bodies. */
          grid-template-rows: auto 1fr;
          column-gap: 20px;
          row-gap: 16px;
          align-items: stretch;
        }
        /* Each card is a subgrid spanning both tracks, so its two regions line
           up with every sibling card's regions. */
        .vwd-strats > .vwd-card {
          grid-row: 1 / -1;
          display: grid;
          grid-template-rows: subgrid;
          row-gap: 20px;
        }
        .vwd-card-top,
        .vwd-card-rest {
          display: flex;
          flex-direction: column;
          gap: 20px;
          min-height: 0;
        }
        .vwd-card-top  { padding: 28px 28px 0; }
        .vwd-card-rest { padding: 0 28px 28px; }

        /* When the grid wraps, drop the subgrid and fall back to plain stacked
           cards (row-alignment across columns no longer applies). */
        @media (max-width: 1100px) {
          .vwd-strats { grid-template-columns: 1fr 1fr; grid-template-rows: none; }
          .vwd-strats > .vwd-card { grid-row: auto; display: flex; flex-direction: column; }
        }
        @media (max-width: 720px) { .vwd-strats { grid-template-columns: 1fr; } }
      `}</style>

      {expressions.map((e) => (
        <StrategyCleanCard
          key={e.id}
          expression={e}
          amount={amount}
          onSeeAnalysis={onToggleAnalysis ? () => onToggleAnalysis(e.id) : undefined}
          analysisOpen={openAnalysisId === e.id}
        />
      ))}
    </div>
  );
}

export default StrategiesEditorial;
