"use client";

/**
 * AgentsSummaryHeader — three-up header that sits above the agent catalog
 * grid on the Active Agents page. Composed of:
 *
 *   1. ActiveStrategiesCard — real active-workflow count + per-agent returns
 *   2. TradesCard           — real 6-month closed-trade win/loss scorecard
 *   3. PnlHeatmap           — GitHub-style heatmap of REAL daily P&L
 *
 * Everything is driven by `GET /api/workflows/summary`. No data is
 * fabricated: when the series / scorecard / returns are empty we render an
 * honest empty state rather than a seeded sparkline or invented bars.
 */

import { Fragment, useMemo } from "react";
import { cn } from "@/lib/utils";
import { Skeleton } from "@/components/ui/skeleton";
import type {
  DailyPnlPoint,
  StrategyReturn,
  WorkflowTrades6mo,
  WorkflowsSummary,
} from "@/lib/agentsApi";

// ---------------------------------------------------------------------------
// Heatmap geometry
// ---------------------------------------------------------------------------

const WEEKS = 26;                  // ~6 months
const MARKET_DAYS = 5;             // Mon..Fri only — Sat/Sun rows are dropped
const LEGEND_SWATCH = 10;          // px, legend swatch edge

const MONTHS = [
  "Jan", "Feb", "Mar", "Apr", "May", "Jun",
  "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
];

// ---------------------------------------------------------------------------
// Real daily P&L series → heatmap cell grid
//
// The backend returns a sparse `daily_pnl: [{date, pnl}]` array (NAV deltas
// on snapshotted days). We project those onto a Mon..Fri × 26-week calendar
// grid anchored to today's week. Days with no snapshot are blank (not zero
// "loss" cells) so the grid never implies data we don't have.
// ---------------------------------------------------------------------------

type DayCell = {
  date: Date;
  pnl: number;                              // ₹ (positive = profit)
  hasData: boolean;                         // true only when the backend had a point
};

function dateKey(d: Date): string {
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${y}-${m}-${day}`;
}

function buildSeries(today: Date, daily: DailyPnlPoint[]): DayCell[] {
  const byDate = new Map<string, number>();
  for (const p of daily) {
    // A date may appear once; if duplicated, sum so totals stay faithful.
    byDate.set(p.date, (byDate.get(p.date) ?? 0) + p.pnl);
  }

  const todayCopy = new Date(today);
  todayCopy.setHours(0, 0, 0, 0);

  const todayDow = (todayCopy.getDay() + 6) % 7;   // Mon=0..Sun=6
  const thisMonday = new Date(todayCopy);
  thisMonday.setDate(thisMonday.getDate() - todayDow);

  const startMonday = new Date(thisMonday);
  startMonday.setDate(startMonday.getDate() - (WEEKS - 1) * 7);

  const cells: DayCell[] = [];
  for (let col = 0; col < WEEKS; col++) {
    for (let row = 0; row < MARKET_DAYS; row++) {
      const d = new Date(startMonday);
      d.setDate(d.getDate() + col * 7 + row);
      const key = dateKey(d);
      const has = byDate.has(key);
      cells.push({ date: d, pnl: has ? byDate.get(key)! : 0, hasData: has });
    }
  }
  return cells;
}

// ---------------------------------------------------------------------------
// Color binning — GitHub-style ramp, profit vs loss. Cells with no real data
// fall to the muted "empty" colour (bin 0) and are NOT painted as losses.
// ---------------------------------------------------------------------------

type Bin = -3 | -2 | -1 | 0 | 1 | 2 | 3;

function binFor(pnl: number, hasData: boolean): Bin {
  if (!hasData) return 0;
  const a = Math.abs(pnl);
  if (a < 200) return 0;
  let mag: 1 | 2 | 3;
  if (a < 1500) mag = 1;
  else if (a < 3500) mag = 2;
  else mag = 3;
  return (pnl >= 0 ? mag : (-mag as -1 | -2 | -3)) as Bin;
}

function cellColor(bin: Bin): { light: string; dark: string } {
  switch (bin) {
    case 3:  return { light: "#216e39", dark: "#39d353" };
    case 2:  return { light: "#30a14e", dark: "#26a641" };
    case 1:  return { light: "#40c463", dark: "#006d32" };
    case -1: return { light: "#fda4af", dark: "#7f1d1d" };
    case -2: return { light: "#f87171", dark: "#b91c1c" };
    case -3: return { light: "#dc2626", dark: "#ef4444" };
    default: return { light: "#ebedf0", dark: "#161b22" };
  }
}

// ---------------------------------------------------------------------------
// Header
// ---------------------------------------------------------------------------

export function AgentsSummaryHeader({
  summary,
  isLoading,
}: {
  summary: WorkflowsSummary | null;
  isLoading: boolean;
}): React.ReactElement {
  const daily = summary?.daily_pnl;
  const cells = useMemo(() => {
    const t = new Date();
    t.setHours(0, 0, 0, 0);
    return buildSeries(t, daily ?? []);
  }, [daily]);

  const hasDailyData = (daily?.length ?? 0) > 0;
  const totalPnl = summary?.total_pnl ?? 0;
  const trades = summary?.trades_6mo ?? null;
  const strategyReturns = summary?.strategy_returns ?? [];
  const activeCount = summary?.active_count ?? 0;

  return (
    <div
      className="grid grid-cols-1 gap-3 lg:grid-cols-[1fr_1fr_2fr]"
      data-testid="agents-summary-header"
    >
      <StatCard
        label="Active strategies"
        chip={isLoading ? "—" : activeCount.toLocaleString("en-IN")}
        bottom={
          <StrategyReturnRows
            returns={strategyReturns}
            isLoading={isLoading}
          />
        }
      />
      <StatCard
        label="Closed trades · 6 mo"
        chip={isLoading ? "—" : (trades?.total ?? 0).toLocaleString("en-IN")}
        bottom={<WinLossBreakdown trades={trades} isLoading={isLoading} />}
      />
      <PnlHeatmap
        cells={cells}
        totalPnl={totalPnl}
        hasData={hasDailyData}
        isLoading={isLoading}
      />
    </div>
  );
}

// ---------------------------------------------------------------------------
// StatCard
// ---------------------------------------------------------------------------

type Tone = "neutral" | "profit" | "loss";

function toneColor(tone: Tone): string {
  switch (tone) {
    case "profit": return "var(--color-profit)";
    case "loss":   return "var(--color-loss)";
    default:       return "var(--text-tertiary)";
  }
}

function StatCard({
  label,
  chip,
  chipTone = "neutral",
  bottom,
}: {
  label: string;
  chip: string;
  chipTone?: Tone;
  bottom: React.ReactNode;
}): React.ReactElement {
  return (
    <div
      className={cn(
        "agents-stat-card flex flex-col justify-between gap-3 rounded-xl border border-border/50 bg-card px-3.5 py-3",
        "shadow-[0_1px_2px_rgba(15,23,42,0.04),0_6px_16px_-12px_rgba(15,23,42,0.08)]",
      )}
    >
      <div className="flex items-center justify-between gap-2">
        <span
          style={{
            fontFamily: "var(--font-ui)",
            fontSize: 12,
            fontWeight: 500,
            color: "var(--text-secondary)",
            letterSpacing: "-0.01em",
          }}
        >
          {label}
        </span>
        <ValueChip text={chip} tone={chipTone} />
      </div>
      {bottom}
    </div>
  );
}

function ValueChip({
  text,
  tone,
}: {
  text: string;
  tone: Tone;
}): React.ReactElement {
  const color = tone === "neutral" ? "var(--text-primary)" : toneColor(tone);
  return (
    <span
      className="tabular-nums shrink-0 inline-flex items-center rounded-xl bg-muted px-3 py-1.5"
      style={{
        fontFamily: "var(--font-ui)",
        fontWeight: 700,
        fontSize: 22,
        letterSpacing: "-0.02em",
        lineHeight: 1.1,
        color,
      }}
    >
      {text}
    </span>
  );
}

// ---------------------------------------------------------------------------
// StrategyReturnRows — leaderboard bars driven by the REAL per-active-agent
// return_pct from the summary. Agents whose return is unknown (null, i.e. no
// forward-test idea/cache yet) render with an em-dash instead of a bar, so we
// never invent a number. Agents WITH a real return are ranked + bar-filled.
// ---------------------------------------------------------------------------

function StrategyReturnRows({
  returns,
  isLoading,
}: {
  returns: StrategyReturn[];
  isLoading: boolean;
}): React.ReactElement {
  const MAX_VISIBLE = 4;

  const gridStyle: React.CSSProperties = {
    display: "grid",
    gridTemplateColumns: "minmax(0, 130px) 1fr minmax(0, 46px)",
    rowGap: 8,
    columnGap: 12,
    alignItems: "center",
    width: "100%",
  };

  if (isLoading) {
    return (
      <div style={gridStyle} aria-hidden={true}>
        {Array.from({ length: 3 }).map((_, i) => (
          <Fragment key={i}>
            <span style={{ height: 11, background: "var(--bg-elevated)", borderRadius: 4, opacity: 0.7 }} />
            <span style={{ height: 8, background: "var(--bg-elevated)", borderRadius: 9999, opacity: 0.7 }} />
            <span style={{ height: 11, background: "var(--bg-elevated)", borderRadius: 4, opacity: 0.7 }} />
          </Fragment>
        ))}
      </div>
    );
  }

  if (returns.length === 0) {
    return (
      <div
        className="flex items-center"
        style={{
          height: 56,
          fontFamily: "var(--font-ui)",
          fontSize: 11.5,
          color: "var(--text-tertiary)",
        }}
      >
        No active strategies yet
      </div>
    );
  }

  // Sort: agents with a real return first (return-descending), then unknowns.
  const sorted = [...returns].sort((a, b) => {
    const av = a.return_pct;
    const bv = b.return_pct;
    if (av === null && bv === null) return 0;
    if (av === null) return 1;
    if (bv === null) return -1;
    return bv - av;
  });

  const visible = sorted.slice(0, MAX_VISIBLE);
  const remaining = sorted.length - visible.length;

  const known = visible.filter((x) => x.return_pct !== null) as Array<
    StrategyReturn & { return_pct: number }
  >;
  const maxAbs = Math.max(1, ...known.map((x) => Math.abs(x.return_pct)));
  const positiveCount = known.filter((x) => x.return_pct >= 0).length;
  const negativeCount = known.length - positiveCount;

  let posSeen = 0;
  let negSeen = 0;

  return (
    <div style={gridStyle} aria-label={`${returns.length} active strategies`}>
      {visible.map((row) => {
        const ret = row.return_pct;

        if (ret === null) {
          // Honest "no return data yet" — no bar, no invented value.
          return (
            <Fragment key={row.workflow_id}>
              <span
                className="truncate"
                title={row.name}
                style={{
                  fontFamily: "var(--font-ui)",
                  fontSize: 12,
                  fontWeight: 500,
                  color: "var(--text-secondary)",
                  letterSpacing: "-0.01em",
                }}
              >
                {row.name}
              </span>
              <div
                style={{
                  height: 9,
                  background: "var(--bg-elevated)",
                  borderRadius: 9999,
                  opacity: 0.5,
                }}
                aria-hidden={true}
              />
              <span
                className="tabular-nums text-right"
                style={{
                  fontFamily: "var(--font-ui)",
                  fontSize: 12,
                  fontWeight: 500,
                  color: "var(--text-tertiary)",
                }}
                title="No forward-test return yet"
              >
                —
              </span>
            </Fragment>
          );
        }

        const isUp = ret >= 0;
        const halfWidthPct = (Math.abs(ret) / maxAbs) * 50;

        let blueTier: number;
        if (isUp) {
          blueTier = positiveCount > 1 ? 1 - (posSeen / (positiveCount - 1)) * 0.45 : 1;
          posSeen += 1;
        } else {
          const reversedRank = negativeCount - 1 - negSeen;
          blueTier = negativeCount > 1 ? 1 - (reversedRank / (negativeCount - 1)) * 0.45 : 1;
          negSeen += 1;
        }
        const fillColor = `color-mix(in srgb, var(--pivot-blue) ${Math.round(
          blueTier * 100,
        )}%, var(--bg-elevated))`;

        const text = `${isUp ? "+" : "−"}${Math.abs(ret).toFixed(1)}%`;

        return (
          <Fragment key={row.workflow_id}>
            <span
              className="truncate"
              title={row.name}
              style={{
                fontFamily: "var(--font-ui)",
                fontSize: 12,
                fontWeight: 500,
                color: "var(--text-secondary)",
                letterSpacing: "-0.01em",
              }}
            >
              {row.name}
            </span>
            <div
              style={{
                height: 9,
                background: "var(--bg-elevated)",
                borderRadius: 9999,
                overflow: "hidden",
                position: "relative",
              }}
            >
              <div
                aria-hidden={true}
                style={{
                  position: "absolute",
                  left: "50%",
                  top: 0,
                  bottom: 0,
                  width: 1,
                  background: "var(--glass-border-hover)",
                  transform: "translateX(-0.5px)",
                  opacity: 0.55,
                  zIndex: 1,
                }}
              />
              <div
                style={{
                  position: "absolute",
                  top: 0,
                  bottom: 0,
                  ...(isUp ? { left: "50%" } : { right: "50%" }),
                  width: `${halfWidthPct}%`,
                  background: fillColor,
                  transition:
                    "width 0.4s var(--ease-quartr), background 0.3s var(--ease-quartr)",
                  zIndex: 2,
                }}
              />
            </div>
            <span
              className="tabular-nums text-right"
              style={{
                fontFamily: "var(--font-ui)",
                fontSize: 12,
                fontWeight: 600,
                color: "var(--pivot-blue)",
                letterSpacing: "-0.01em",
              }}
            >
              {text}
            </span>
          </Fragment>
        );
      })}
      {remaining > 0 && (
        <span
          style={{
            gridColumn: "1 / -1",
            fontFamily: "var(--font-ui)",
            fontSize: 11,
            fontWeight: 500,
            color: "var(--text-tertiary)",
            letterSpacing: "-0.01em",
            marginTop: 2,
          }}
        >
          +{remaining} more strateg{remaining === 1 ? "y" : "ies"}
        </span>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// WinLossBreakdown — real 6-month closed-trade scorecard. Empty when the user
// has no decided trades (no paper fills with a realized P&L yet).
// ---------------------------------------------------------------------------

function WinLossBreakdown({
  trades,
  isLoading,
}: {
  trades: WorkflowTrades6mo | null;
  isLoading: boolean;
}): React.ReactElement {
  const profit = "var(--color-profit)";
  const loss = "var(--color-loss)";

  if (isLoading) {
    return (
      <div className="flex flex-col gap-2" aria-hidden={true}>
        <div className="flex items-baseline justify-between gap-2">
          <span style={{ height: 13, width: 64, background: "var(--bg-elevated)", borderRadius: 4, display: "inline-block", opacity: 0.7 }} />
          <span style={{ height: 13, width: 64, background: "var(--bg-elevated)", borderRadius: 4, display: "inline-block", opacity: 0.7 }} />
        </div>
        <div style={{ height: 8, background: "var(--bg-elevated)", borderRadius: 9999, opacity: 0.7 }} />
      </div>
    );
  }

  const wins = trades?.wins ?? 0;
  const losses = trades?.losses ?? 0;
  const decided = wins + losses;

  if (decided === 0) {
    return (
      <div
        className="flex items-center"
        style={{
          height: 40,
          fontFamily: "var(--font-ui)",
          fontSize: 11.5,
          color: "var(--text-tertiary)",
        }}
      >
        No closed trades in the last 6 months
      </div>
    );
  }

  const winPct = (wins / decided) * 100;
  const lossPct = 100 - winPct;

  return (
    <div className="flex flex-col gap-2">
      <div className="flex items-baseline justify-between gap-2 tabular-nums">
        <span
          style={{
            fontFamily: "var(--font-ui)",
            fontWeight: 600,
            fontSize: 13,
            color: profit,
            letterSpacing: "-0.01em",
          }}
        >
          {wins.toLocaleString("en-IN")} wins
          <span style={{ marginLeft: 4, fontWeight: 500, fontSize: 11, color: "var(--text-tertiary)" }}>
            {winPct.toFixed(0)}%
          </span>
        </span>
        <span
          style={{
            fontFamily: "var(--font-ui)",
            fontWeight: 600,
            fontSize: 13,
            color: loss,
            letterSpacing: "-0.01em",
          }}
        >
          <span style={{ marginRight: 4, fontWeight: 500, fontSize: 11, color: "var(--text-tertiary)" }}>
            {lossPct.toFixed(0)}%
          </span>
          {losses.toLocaleString("en-IN")} losses
        </span>
      </div>
      <div
        className="flex w-full overflow-hidden rounded-full"
        style={{ height: 8, background: "var(--bg-elevated)" }}
        role="img"
        aria-label={`${wins} winning trades, ${losses} losing trades`}
      >
        <span aria-hidden={true} style={{ width: `${winPct}%`, background: profit, transition: "width 0.4s var(--ease-quartr)" }} />
        <span aria-hidden={true} style={{ width: `${lossPct}%`, background: loss, transition: "width 0.4s var(--ease-quartr)" }} />
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// PnlHeatmap — GitHub-style square calendar of REAL daily P&L
// ---------------------------------------------------------------------------

function PnlHeatmap({
  cells,
  totalPnl,
  hasData,
  isLoading,
}: {
  cells: DayCell[];
  totalPnl: number;
  hasData: boolean;
  isLoading: boolean;
}): React.ReactElement {
  const totalTone: Tone = !hasData ? "neutral" : totalPnl >= 0 ? "profit" : "loss";

  const monthLabels = useMemo(() => {
    const labels: { col: number; text: string }[] = [];
    let lastMonth = -1;
    for (let col = 0; col < WEEKS; col++) {
      const cell = cells[col * MARKET_DAYS];
      if (!cell) continue;
      const m = cell.date.getMonth();
      if (m !== lastMonth && (col === 0 || cell.date.getDate() <= 7)) {
        labels.push({ col, text: MONTHS[m]! });
        lastMonth = m;
      }
    }
    return labels;
  }, [cells]);

  const gridStyle: React.CSSProperties = {
    display: "grid",
    gridTemplateColumns: `auto repeat(${WEEKS}, minmax(0, 1fr))`,
    gridTemplateRows: `auto repeat(${MARKET_DAYS}, auto)`,
    columnGap: 2,
    rowGap: 2,
    width: "100%",
    maxWidth: 460,
  };

  const labelStyle: React.CSSProperties = {
    fontFamily: "var(--font-ui)",
    fontSize: 9.5,
    color: "var(--text-tertiary)",
    lineHeight: 1,
  };

  return (
    <div
      className={cn(
        "agents-stat-card flex flex-col gap-2.5 rounded-xl border border-border/50 bg-card px-3.5 py-3",
        "shadow-[0_1px_2px_rgba(15,23,42,0.04),0_6px_16px_-12px_rgba(15,23,42,0.08)]",
      )}
      data-testid="pnl-heatmap"
    >
      <div className="flex items-center justify-between gap-2">
        <span
          style={{
            fontFamily: "var(--font-ui)",
            fontSize: 12,
            fontWeight: 500,
            color: "var(--text-secondary)",
            letterSpacing: "-0.01em",
          }}
        >
          Daily P&L · last 6 months
        </span>
        <ValueChip text={isLoading || !hasData ? "—" : formatInr(totalPnl, true)} tone={totalTone} />
      </div>

      {isLoading ? (
        <div
          className="flex items-center justify-center"
          style={{ minHeight: 96 }}
          data-testid="pnl-heatmap-loading"
        >
          <Skeleton className="h-full w-full rounded-md" style={{ minHeight: 96 }} />
        </div>
      ) : (
        <div style={gridStyle} role="img" aria-label="Daily profit and loss heatmap, last six months">
          {monthLabels.map((m) => (
            <span
              key={`${m.col}-${m.text}`}
              style={{ ...labelStyle, gridColumn: m.col + 2, gridRow: 1, alignSelf: "end", paddingBottom: 2 }}
            >
              {m.text}
            </span>
          ))}

          {(["Mon", null, "Wed", null, "Fri"] as const).map((label, i) =>
            label ? (
              <span
                key={`rl-${i}`}
                style={{ ...labelStyle, gridColumn: 1, gridRow: i + 2, alignSelf: "center", paddingRight: 4 }}
              >
                {label}
              </span>
            ) : null,
          )}

          {cells.map((cell, i) => {
            const col = Math.floor(i / MARKET_DAYS);
            const row = i % MARKET_DAYS;
            const bin = binFor(cell.pnl, cell.hasData);
            const colors = cellColor(bin);
            const dateLabel = cell.date.toLocaleDateString("en-IN", {
              day: "numeric",
              month: "short",
              year: "numeric",
            });
            const pnlLabel = cell.hasData ? formatInr(cell.pnl, true) : "—";
            return (
              <div
                key={i}
                className="heatmap-cell"
                title={`${dateLabel} — ${pnlLabel}`}
                style={{
                  gridColumn: col + 2,
                  gridRow: row + 2,
                  aspectRatio: "1 / 1",
                  borderRadius: 3,
                  ...({
                    "--cell-light": colors.light,
                    "--cell-dark": colors.dark,
                  } as React.CSSProperties),
                }}
              />
            );
          })}
        </div>
      )}

      <div className="flex items-center justify-end gap-1.5 text-[9.5px] text-muted-foreground">
        <span>Loss</span>
        <LegendSwatches bins={[-3, -2, -1, 0, 1, 2, 3]} />
        <span>Profit</span>
      </div>
    </div>
  );
}

function LegendSwatches({ bins }: { bins: Bin[] }): React.ReactElement {
  return (
    <span className="inline-flex items-center gap-[3px]">
      {bins.map((b) => {
        const colors = cellColor(b);
        return (
          <span
            key={b}
            className="heatmap-cell"
            style={{
              display: "inline-block",
              width: LEGEND_SWATCH,
              height: LEGEND_SWATCH,
              borderRadius: 2,
              ...({
                "--cell-light": colors.light,
                "--cell-dark": colors.dark,
              } as React.CSSProperties),
            }}
            aria-hidden={true}
          />
        );
      })}
    </span>
  );
}

// ---------------------------------------------------------------------------
// Formatting
// ---------------------------------------------------------------------------

function formatInr(amount: number, signed: boolean): string {
  const abs = Math.abs(Math.round(amount));
  const formatted = abs.toLocaleString("en-IN", { maximumFractionDigits: 0 });
  const sign = !signed ? "" : amount > 0 ? "+" : amount < 0 ? "−" : "";
  return `${sign}₹${formatted}`;
}
