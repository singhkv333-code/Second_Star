"use client";

/**
 * RiskStrip — a horizontal strip of plain-English risk Stat tiles (no chart).
 * De-jargoned per the design law: no "DD", no "Monte-Carlo", no "P(loss)".
 *   Worst drop        ← max drawdown (keep the minus sign)
 *   Worst-case drop   ← the modelled p95 drawdown (Monte-Carlo, label hidden)
 *   Chance of a loss  ← prob-loss
 *   Win rate          ← win rate
 * Drawdowns render amber, loss-chance renders red, win-rate ink. Null per-tile
 * → muted "—" (never blank). All values >= 13px (Stat floor).
 */

import { Stat, StatStrip } from "../Stat";

export function RiskStrip({
  maxDdPct,
  mcDdP95Pct,
  mcProbLoss,
  winRate,
}: {
  maxDdPct?: number | null;
  mcDdP95Pct?: number | null;
  mcProbLoss?: number | null;
  winRate?: number | null;
}): React.ReactElement {
  const ddStr = (v: number | null | undefined): string =>
    v == null ? "—" : `−${Math.abs(v).toFixed(1)}%`;

  return (
    <StatStrip>
      <Stat
        label="Worst drop"
        value={ddStr(maxDdPct)}
        valueColor={maxDdPct == null ? "var(--text-tertiary)" : "var(--color-warn)"}
        valueSize="value"
      />
      {mcDdP95Pct != null && (
        <Stat
          label="Worst-case drop"
          value={ddStr(mcDdP95Pct)}
          valueColor="var(--color-warn)"
          valueSize="value"
        />
      )}
      <Stat
        label="Chance of a loss"
        value={mcProbLoss == null ? "—" : `${(mcProbLoss * 100).toFixed(0)}%`}
        valueColor={mcProbLoss == null ? "var(--text-tertiary)" : "var(--color-loss)"}
        valueSize="value"
      />
      <Stat
        label="Win rate"
        value={winRate == null ? "—" : `${(winRate * 100).toFixed(0)}%`}
        valueColor={winRate == null ? "var(--text-tertiary)" : "var(--text-primary)"}
        valueSize="value"
      />
    </StatStrip>
  );
}
