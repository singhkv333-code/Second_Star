"use client";

/**
 * AgentsSummaryHeader — three-up header that sits above the agent catalog
 * grid on the Active Agents page. Composed of:
 *
 *   1. ActiveStrategiesCard — count of workflows with status === "active"
 *   2. CumulativePnlCard    — sum of all daily P&L from the heatmap series
 *   3. PnlHeatmap           — GitHub-style contribution heatmap of daily P&L
 *
 * Backend doesn't yet expose per-day P&L, so the heatmap series is generated
 * deterministically from the date — mirroring the seeded `Sparkline` pattern
 * used by AgentsTab / WorkflowDraftCard. Weekends are holiday-blank, weekdays
 * carry a small positive bias so the page reads as a healthy account.
 */

import { Fragment, useMemo } from "react";
import { cn } from "@/lib/utils";
import type { WorkflowStatus, WorkflowSummary } from "@/lib/types";

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
// Deterministic daily P&L series
// ---------------------------------------------------------------------------

/** Stable per-date pseudo-random in [-1, 1]. */
function daySeed(d: Date): number {
  const k =
    d.getUTCFullYear() * 10000 +
    (d.getUTCMonth() + 1) * 100 +
    d.getUTCDate();
  // Two sine harmonics — same family as the Sparkline seed math, gives a
  // pleasing non-repeating series without any RNG.
  const a = Math.sin(k * 0.0173) * 43758.5453;
  const b = Math.sin(k * 0.0913) * 12193.7777;
  const mix = (a + b) - Math.floor(a + b);  // fractional part in [0, 1)
  return mix * 2 - 1;                       // map to [-1, 1)
}

type DayCell = {
  date: Date;
  pnl: number;                              // ₹ (positive = profit)
  trades: number;                           // simulated trade count
  isMarketDay: boolean;
};

function buildSeries(today: Date): DayCell[] {
  // Column 0..WEEKS-1 left-to-right; row 0..MARKET_DAYS-1 Mon-Fri top-to-bottom.
  // Cells are stored column-major so `cells[col * MARKET_DAYS + row]` gives
  // the cell at (col, row) — the same indexing the renderer uses.
  //
  // Anchor: the Monday of the rightmost column is the Monday of today's week.
  // The leftmost column starts (WEEKS-1) weeks earlier. Future weekdays in
  // the current week (e.g. Thu/Fri if today is Wed) render as empty.

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

      // Future weekdays: blank (will render in the "empty" colour).
      const isPast = d.getTime() <= todayCopy.getTime();
      if (!isPast) {
        cells.push({ date: d, pnl: 0, trades: 0, isMarketDay: false });
        continue;
      }

      const r = daySeed(d);
      const pnl = Math.round((r * 4200 + 600) * (1 - 0.35 * Math.abs(r)));
      const trades = Math.max(0, Math.round(2 + r * 3 + 1.5));
      cells.push({ date: d, pnl, trades, isMarketDay: true });
    }
  }
  return cells;
}

// ---------------------------------------------------------------------------
// Color binning — GitHub-style 5-step ramp, profit vs loss
// ---------------------------------------------------------------------------

type Bin = -3 | -2 | -1 | 0 | 1 | 2 | 3;

function binFor(pnl: number, isMarketDay: boolean): Bin {
  if (!isMarketDay) return 0;
  const a = Math.abs(pnl);
  if (a < 200) return 0;
  let mag: 1 | 2 | 3;
  if (a < 1500) mag = 1;
  else if (a < 3500) mag = 2;
  else mag = 3;
  return (pnl >= 0 ? mag : (-mag as -1 | -2 | -3)) as Bin;
}

/** GitHub-faithful palette — graded greens for profit, graded reds for loss.
 *  Empty/no-trade days fall back to the muted surface so the grid still has
 *  rhythm but doesn't shout. */
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
  workflows,
  isLoading,
}: {
  workflows: WorkflowSummary[];
  isLoading: boolean;
}): React.ReactElement {
  // Deterministic series — re-anchored at midnight so the layout doesn't
  // shift on every render. useMemo with no deps is fine because the series
  // is purely date-derived.
  const cells = useMemo(() => {
    const t = new Date();
    t.setHours(0, 0, 0, 0);
    return buildSeries(t);
  }, []);

  const totalPnl = cells.reduce((sum, c) => sum + c.pnl, 0);
  const totalTrades = cells.reduce((sum, c) => sum + c.trades, 0);

  // Roster composition — read directly off workflow.status from the backend.
  const activeCount = workflows.filter((w) => w.status === "active").length;

  // Wins / losses are derived from the same seeded P&L series — every market
  // day with positive P&L counts as a "win" and contributes its trades to
  // the wins bucket; same for losses. Means the Trades-card breakdown
  // agrees exactly with what the heatmap is colouring.
  const winTrades = cells.reduce((s, c) => s + (c.pnl > 0 ? c.trades : 0), 0);
  const lossTrades = cells.reduce((s, c) => s + (c.pnl < 0 ? c.trades : 0), 0);

  return (
    <div
      className="grid grid-cols-1 gap-3 lg:grid-cols-[1fr_1fr_2fr]"
      data-testid="agents-summary-header"
    >
      <StatCard
        label="Active strategies"
        chip={isLoading ? "—" : activeCount.toLocaleString("en-IN")}
        bottom={<StrategyReturnRows workflows={workflows} isLoading={isLoading} />}
      />
      <StatCard
        label="Trades · 6 mo"
        chip={totalTrades.toLocaleString("en-IN")}
        bottom={<WinLossBreakdown wins={winTrades} losses={lossTrades} />}
      />
      <PnlHeatmap cells={cells} totalPnl={totalPnl} />
    </div>
  );
}

// ---------------------------------------------------------------------------
// StatCard — header (label + colored sublabel) + chip on the right, with a
// thin-bar mini histogram across the bottom. Mirrors the reference card.
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

// ValueChip — the rounded grey pill in the reference. Bold black on a
// muted surface; tone variants swap the text color to profit/loss so the
// P&L card lights up.
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

// StrategyReturnRows — leaderboard-style horizontal bars, one per strategy:
//
//   Momentum         ████████████████████████████  +8.2%
//   Sector rotation  ███████████████████░░░░░░░░░  +5.6%
//   Pairs trade      ███████████░░░░░░░░░░░░░░░░░  +3.1%
//   Mean reversion   ████░░░░░░░░░░░░░░░░░░░░░░░░  −1.4%
//
// Bar fill width scales with |return| / max(|returns|), so the biggest
// performer always pegs at 100%. Fill color intensifies with return rank
// (top performer = deep brand-green, smaller positives lighten via
// color-mix toward the track); negatives go to brand-red regardless of
// magnitude. Names are shortened to fit the left column; returns sit
// right-aligned in the brand colour. Sorted return-descending so winners
// are at the top. Cap at 4 rows with a subdued "+N more strategies" tail.
//
// Returns are seeded per workflow id (deterministic) until a backend
// return field lands — same approach as the heatmap.
function StrategyReturnRows({
  workflows,
  isLoading,
}: {
  workflows: WorkflowSummary[];
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
            <span
              style={{
                height: 11,
                background: "var(--bg-elevated)",
                borderRadius: 4,
                opacity: 0.7,
              }}
            />
            <span
              style={{
                height: 8,
                background: "var(--bg-elevated)",
                borderRadius: 9999,
                opacity: 0.7,
              }}
            />
            <span
              style={{
                height: 11,
                background: "var(--bg-elevated)",
                borderRadius: 4,
                opacity: 0.7,
              }}
            />
          </Fragment>
        ))}
      </div>
    );
  }

  if (workflows.length === 0) {
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
        No strategies yet
      </div>
    );
  }

  // Compute returns, then sort return-descending so the leaderboard reads
  // top-down (winners → losers).
  const withReturns = workflows.map((wf) => ({
    wf,
    ret: seededReturnPct(wf.id),
  }));
  withReturns.sort((a, b) => b.ret - a.ret);

  const visible = withReturns.slice(0, MAX_VISIBLE);
  const remaining = withReturns.length - visible.length;

  // Each half of the track represents the full magnitude range — the bar
  // for the biggest |return| pegs at 50% (one full side), everything else
  // scales relative to it. Diverging center → right for gains, center →
  // left for losses.
  const maxAbs = Math.max(1, ...visible.map((x) => Math.abs(x.ret)));

  // Cobalt-blue rank shading on both sides — each sign has its own rank:
  //   • Positives ranked best→worst: biggest gain darkest
  //   • Negatives ranked worst→least-bad: biggest loss darkest
  // Smaller magnitudes lighten by blending with the track, so the strip
  // reads as a single blue diverging gradient. Direction (left vs right
  // of center) and the +/− label carry the sign.
  const positiveCount = visible.filter((x) => x.ret >= 0).length;
  const negativeCount = visible.length - positiveCount;

  return (
    <div style={gridStyle} aria-label={`${workflows.length} strategies`}>
      {visible.map(({ wf, ret }, i) => {
        const isUp = ret >= 0;
        const halfWidthPct = (Math.abs(ret) / maxAbs) * 50;

        let blueTier: number;
        if (isUp) {
          blueTier = positiveCount > 1
            ? 1 - (i / (positiveCount - 1)) * 0.45
            : 1;
        } else {
          // Sort is return-descending, so the biggest-magnitude loss sits
          // at the LAST position. Reverse the rank within the negatives
          // block so that most-negative → darkest blue.
          const negIdx = i - positiveCount;
          const reversedRank = negativeCount - 1 - negIdx;
          blueTier = negativeCount > 1
            ? 1 - (reversedRank / (negativeCount - 1)) * 0.45
            : 1;
        }
        const fillColor = `color-mix(in srgb, var(--pivot-blue) ${Math.round(
          blueTier * 100,
        )}%, var(--bg-elevated))`;

        const text = `${isUp ? "+" : "−"}${Math.abs(ret).toFixed(1)}%`;
        const labelColor = "var(--pivot-blue)";

        return (
          <Fragment key={wf.id}>
            <span
              className="truncate"
              title={wf.name}
              style={{
                fontFamily: "var(--font-ui)",
                fontSize: 12,
                fontWeight: 500,
                color: "var(--text-secondary)",
                letterSpacing: "-0.01em",
              }}
            >
              {wf.name}
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
              {/* Subtle center divider so the divergent baseline reads as
                  intentional rather than accidental. */}
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
              {/* Bar fill — grows outward from the center. Width is half
                  the full magnitude range so positives can reach the right
                  edge and negatives the left edge. */}
              <div
                style={{
                  position: "absolute",
                  top: 0,
                  bottom: 0,
                  ...(isUp
                    ? { left: "50%" }
                    : { right: "50%" }),
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
                color: labelColor,
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

/** Seeded return percentage in roughly [-6, +12] from a workflow id. Same
 *  pattern as the heatmap's daySeed — deterministic so the value doesn't
 *  flicker between renders. */
function seededReturnPct(id: string): number {
  let h = 0;
  for (let i = 0; i < id.length; i++) {
    h = ((h << 5) - h + id.charCodeAt(i)) | 0;
  }
  const a = Math.sin(h * 0.0173) * 43758.5453;
  const b = Math.sin(h * 0.0913) * 12193.7777;
  const frac = (a + b) - Math.floor(a + b);   // [0, 1)
  return frac * 18 - 6;                       // [-6, +12)
}


// WinLossBreakdown — the actually useful sub-stat for a trade-count card:
// what fraction of those trades won vs lost. Renders as:
//   row 1: green "W wins · NN%"  ─── spacer ───  red "L losses · NN%"
//   row 2: a single horizontal bar split green/red by win/loss share
// This communicates win-rate at a glance and clearly differentiates the
// Trades card from the heatmap (which is time-keyed) and the Active-strategies
// card (which is decorative).
function WinLossBreakdown({
  wins,
  losses,
}: {
  wins: number;
  losses: number;
}): React.ReactElement {
  const total = Math.max(1, wins + losses);
  const winPct = (wins / total) * 100;
  const lossPct = 100 - winPct;
  const profit = "var(--color-profit)";
  const loss = "var(--color-loss)";

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
          <span
            style={{
              marginLeft: 4,
              fontWeight: 500,
              fontSize: 11,
              color: "var(--text-tertiary)",
            }}
          >
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
          <span
            style={{
              marginRight: 4,
              fontWeight: 500,
              fontSize: 11,
              color: "var(--text-tertiary)",
            }}
          >
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
        <span
          aria-hidden={true}
          style={{
            width: `${winPct}%`,
            background: profit,
            transition: "width 0.4s var(--ease-quartr)",
          }}
        />
        <span
          aria-hidden={true}
          style={{
            width: `${lossPct}%`,
            background: loss,
            transition: "width 0.4s var(--ease-quartr)",
          }}
        />
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// PnlHeatmap — GitHub-style square calendar
// ---------------------------------------------------------------------------

function PnlHeatmap({
  cells,
  totalPnl,
}: {
  cells: DayCell[];
  totalPnl: number;
}): React.ReactElement {
  const totalTone: Tone = totalPnl >= 0 ? "profit" : "loss";

  // Month labels — find the column where each new month first appears, so
  // the labels line up across the top of the grid.
  const monthLabels = useMemo(() => {
    const labels: { col: number; text: string }[] = [];
    let lastMonth = -1;
    for (let col = 0; col < WEEKS; col++) {
      // First weekday of this column (Monday).
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

  // One grid drives the whole heatmap:
  //   Column 1            → row labels (Mon / Wed / Fri)
  //   Columns 2..WEEKS+1  → one heat column per week, equal-fr sized so the
  //                         grid stretches to fill the card width
  //   Row 1               → month labels
  //   Rows 2..MARKET_DAYS+1 → one row per weekday (Mon..Fri)
  // Explicit `gridColumn` / `gridRow` on every cell — no auto-flow — so the
  // row-label column and the heat columns share row heights exactly.
  const gridStyle: React.CSSProperties = {
    display: "grid",
    gridTemplateColumns: `auto repeat(${WEEKS}, minmax(0, 1fr))`,
    gridTemplateRows: `auto repeat(${MARKET_DAYS}, auto)`,
    columnGap: 2,
    rowGap: 2,
    width: "100%",
    // Cap how wide the heatmap can grow on wide cards — without this the
    // 1fr columns blow up to ~22px squares and the strip towers over the
    // sibling cards. Left-aligned (default) so its position matches what
    // the user had before.
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
        <ValueChip text={formatInr(totalPnl, true)} tone={totalTone} />
      </div>

      <div style={gridStyle} role="img" aria-label="Daily profit and loss heatmap, last six months">
        {/* Month labels in row 1 (cols 2..) */}
        {monthLabels.map((m) => (
          <span
            key={`${m.col}-${m.text}`}
            style={{
              ...labelStyle,
              gridColumn: m.col + 2,
              gridRow: 1,
              alignSelf: "end",
              paddingBottom: 2,
            }}
          >
            {m.text}
          </span>
        ))}

        {/* Row labels in column 1 (rows 2..) — only Mon / Wed / Fri are
            shown; empty rows leave gridColumn 1 slot blank but reserved. */}
        {(["Mon", null, "Wed", null, "Fri"] as const).map((label, i) =>
          label ? (
            <span
              key={`rl-${i}`}
              style={{
                ...labelStyle,
                gridColumn: 1,
                gridRow: i + 2,
                alignSelf: "center",
                paddingRight: 4,
              }}
            >
              {label}
            </span>
          ) : null,
        )}

        {/* Cells — explicit (col, row) placement, column-major data layout. */}
        {cells.map((cell, i) => {
          const col = Math.floor(i / MARKET_DAYS);
          const row = i % MARKET_DAYS;
          const bin = binFor(cell.pnl, cell.isMarketDay);
          const colors = cellColor(bin);
          const dateLabel = cell.date.toLocaleDateString("en-IN", {
            day: "numeric",
            month: "short",
            year: "numeric",
          });
          const pnlLabel = cell.isMarketDay
            ? `${formatInr(cell.pnl, true)} · ${cell.trades} trade${cell.trades === 1 ? "" : "s"}`
            : "—";
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
                // CSS vars switched on .dark via globals.css .heatmap-cell rule.
                // eslint-disable-next-line @typescript-eslint/consistent-type-assertions
                ...({
                  "--cell-light": colors.light,
                  "--cell-dark": colors.dark,
                } as React.CSSProperties),
              }}
            />
          );
        })}
      </div>

      {/* Legend — loss ramp on the left, profit ramp on the right */}
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
              // eslint-disable-next-line @typescript-eslint/consistent-type-assertions
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
