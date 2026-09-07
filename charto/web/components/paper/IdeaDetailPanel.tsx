"use client";

/**
 * IdeaDetailPanel — the drill-in scorecard for ONE forward-test idea.
 *
 * Fetches getPaperIdeaDetail(ideaId) into its own 4-state S<T> machine and
 * renders, top to bottom:
 *  - a header (label, origin/status Badge, the verdict chip),
 *  - a KPI row (cum return, forward Sharpe, alpha, PSR, Max DD, maturity vs
 *    MinTRL, DSR, cohort trial count),
 *  - THE DUAL DECAY CHART: the forward idea-NAV curve (solid Area) overlaid on
 *    the backtest equity baseline (dashed grey Line), BOTH rebased to 100 at
 *    their first point so two differently-scaled, differently-dated series read
 *    on a single index axis ("days since each series' start"),
 *  - the gates table (semantic role="table" grid: label / forward / backtest /
 *    pass-✓✗).
 *
 * Mirrors the Quartr idioms of EquityCurveChart / KpiStatCards / HoldingsTable.
 */

import { useEffect, useId, useState } from "react";
import {
  Area,
  ComposedChart,
  CartesianGrid,
  Line,
  ResponsiveContainer,
  Tooltip as RTooltip,
  XAxis,
  YAxis,
} from "recharts";

import {
  getPaperIdeaDetail,
  type IdeaGate,
  type PaperIdeaDetail,
} from "@/lib/api";
import { isError } from "@/lib/types";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { dateShort, pct, pnlColor } from "@/components/paper/format";

type S<T> =
  | { k: "loading" }
  | { k: "ok"; d: T }
  | { k: "err" }
  | { k: "empty" };

const DASH = "—";
const CHART_HEIGHT = 260;

/** Verdict → { Badge variant, humanized label }. */
const VERDICT_MAP: Record<
  string,
  { variant: "success" | "warning" | "destructive" | "muted"; label: string }
> = {
  on_track: { variant: "success", label: "On track" },
  decayed: { variant: "warning", label: "Decayed" },
  execution_problem: { variant: "destructive", label: "Execution problem" },
  insufficient_data: { variant: "muted", label: "Insufficient data" },
};

/** A number → fixed-2 string with a null/NaN → em-dash guard (Sharpe/PSR/DSR
 * have no shared formatter). */
function fixed2(n: number | null | undefined): string {
  if (n === null || n === undefined || Number.isNaN(n)) return DASH;
  return n.toFixed(2);
}

/** The verdict chip (falls back to a muted badge of the raw key). */
function VerdictChip({ verdict }: { verdict: string | null }): React.ReactElement {
  if (!verdict) {
    return (
      <Badge variant="muted" style={{ fontSize: 11 }}>
        {DASH}
      </Badge>
    );
  }
  const m = VERDICT_MAP[verdict];
  return (
    <Badge variant={m?.variant ?? "muted"} style={{ fontSize: 11 }}>
      {m?.label ?? verdict}
    </Badge>
  );
}

function CardShell({ children }: { children: React.ReactNode }): React.ReactElement {
  return (
    <div
      className="flex flex-col"
      style={{
        gap: 14,
        padding: "14px 16px",
        background: "var(--bg-primary)",
        border: "1px solid var(--glass-border)",
        borderRadius: "var(--radius-md)",
      }}
    >
      {children}
    </div>
  );
}

/** One compact KPI tile: uppercase label + value (colored optionally). */
function Kpi({
  label,
  value,
  color,
}: {
  label: string;
  value: string;
  color?: string;
}): React.ReactElement {
  return (
    <div
      className="flex flex-col"
      style={{
        gap: 6,
        padding: "12px 14px",
        background: "var(--bg-primary)",
        border: "1px solid var(--glass-border)",
        borderRadius: "var(--radius-md)",
      }}
    >
      <span className="q-uppercase-label">{label}</span>
      <span
        className="q-display tabular-nums"
        style={{ fontSize: 18, lineHeight: 1.1, color: color ?? "var(--text-primary)" }}
      >
        {value}
      </span>
    </div>
  );
}

/* ------------------------------------------------------------------ chart -- */

/** A chart row keyed by integer trading-day index; either series may be null
 * at a given index because the forward + backtest ranges differ in length. */
type DualPoint = {
  day: number;
  forward: number | null;
  backtest: number | null;
};

type DualChart = {
  points: DualPoint[];
  hasBacktest: boolean;
};

/** Rebase a series to 100 at its first non-zero point so two differently
 * scaled curves read on one axis. Returns null per point until a valid base. */
function rebase(values: number[]): (number | null)[] {
  const base = values.find((v) => v !== 0 && !Number.isNaN(v));
  if (base === undefined) return values.map(() => null);
  return values.map((v) => (Number.isNaN(v) ? null : (v / base) * 100));
}

function buildDualChart(d: PaperIdeaDetail): DualChart | null {
  const fwdRaw = d.forward_curve.map((p) => p.idea_nav);
  if (fwdRaw.length < 2) return null;
  const fwd = rebase(fwdRaw);

  const btRaw = d.backtest?.equity_curve.map((p) => p.equity) ?? [];
  const bt = btRaw.length >= 2 ? rebase(btRaw) : [];
  const hasBacktest = bt.length >= 2;

  const n = Math.max(fwd.length, bt.length);
  const points: DualPoint[] = [];
  for (let i = 0; i < n; i += 1) {
    points.push({
      day: i,
      forward: i < fwd.length ? fwd[i] ?? null : null,
      backtest: hasBacktest && i < bt.length ? bt[i] ?? null : null,
    });
  }
  return { points, hasBacktest };
}

type DualTip = { payload?: DualPoint };

function DualTooltip({
  active,
  payload,
}: {
  active?: boolean;
  payload?: DualTip[];
}): React.ReactElement | null {
  if (!active || !payload || payload.length === 0) return null;
  const p = payload[0]?.payload;
  if (!p) return null;
  return (
    <div
      style={{
        background: "var(--bg-elevated)",
        border: "1px solid var(--glass-border)",
        borderRadius: "var(--radius-sm)",
        padding: "8px 10px",
      }}
    >
      <div className="q-uppercase-label" style={{ marginBottom: 4 }}>
        Trading day {p.day}
      </div>
      <div
        className="tabular-nums q-mono"
        style={{ color: "var(--text-primary)", fontSize: 13 }}
      >
        Forward {p.forward === null ? DASH : p.forward.toFixed(1)}
      </div>
      <div
        className="tabular-nums q-mono"
        style={{ color: "var(--text-tertiary)", fontSize: 12, marginTop: 2 }}
      >
        Backtest {p.backtest === null ? DASH : p.backtest.toFixed(1)}
      </div>
    </div>
  );
}

function DualDecayChart({ d }: { d: PaperIdeaDetail }): React.ReactElement {
  const gradientId = `idea-fill-${useId().replace(/:/g, "")}`;
  const chart = buildDualChart(d);

  return (
    <CardShell>
      <div className="flex items-center justify-between" style={{ gap: 16 }}>
        <div className="q-uppercase-label">Forward vs backtest (rebased to 100)</div>
      </div>

      {chart === null ? (
        <div
          style={{
            color: "var(--text-tertiary)",
            fontSize: 13,
            padding: "40px 16px",
            textAlign: "center",
          }}
        >
          The decay curve appears after the first two forward snapshots.
        </div>
      ) : (
        <>
          <div
            role="img"
            aria-label={`Forward NAV vs backtest baseline, both rebased to 100 at inception.${
              chart.hasBacktest ? "" : " No backtest baseline to compare against."
            }`}
            style={{ width: "100%", height: CHART_HEIGHT }}
          >
            <ResponsiveContainer width="100%" height="100%">
              <ComposedChart
                data={chart.points}
                margin={{ top: 8, right: 8, bottom: 4, left: 0 }}
              >
                <defs>
                  <linearGradient id={gradientId} x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0%" stopColor="var(--price-line)" stopOpacity={0.22} />
                    <stop offset="100%" stopColor="var(--price-line)" stopOpacity={0} />
                  </linearGradient>
                </defs>
                <CartesianGrid
                  stroke="var(--glass-border)"
                  strokeOpacity={0.6}
                  vertical={false}
                />
                <XAxis
                  dataKey="day"
                  type="number"
                  tick={{ fill: "var(--text-tertiary)", fontSize: 11 }}
                  axisLine={{ stroke: "var(--glass-border)" }}
                  tickLine={false}
                  minTickGap={28}
                  label={{
                    value: "Days since inception",
                    position: "insideBottom",
                    offset: -2,
                    fill: "var(--text-tertiary)",
                    fontSize: 10,
                  }}
                />
                <YAxis
                  tickFormatter={(v: number) => v.toFixed(0)}
                  tick={{ fill: "var(--text-tertiary)", fontSize: 11 }}
                  axisLine={false}
                  tickLine={false}
                  width={44}
                  domain={["auto", "auto"]}
                />
                <RTooltip
                  content={<DualTooltip />}
                  cursor={{ stroke: "var(--glass-border-hover)", strokeWidth: 1 }}
                />
                {chart.hasBacktest ? (
                  <Line
                    type="monotone"
                    dataKey="backtest"
                    stroke="var(--text-tertiary)"
                    strokeWidth={1.5}
                    strokeDasharray="4 3"
                    dot={false}
                    activeDot={false}
                    connectNulls
                    isAnimationActive={false}
                  />
                ) : null}
                <Area
                  type="monotone"
                  dataKey="forward"
                  stroke="var(--price-line)"
                  strokeWidth={2}
                  fill={`url(#${gradientId})`}
                  dot={false}
                  activeDot={{ r: 3, fill: "var(--price-line)", strokeWidth: 0 }}
                  connectNulls
                  isAnimationActive={false}
                />
              </ComposedChart>
            </ResponsiveContainer>
          </div>
          {chart.hasBacktest ? null : (
            <div style={{ color: "var(--text-tertiary)", fontSize: 12 }}>
              No backtest baseline to compare against.
            </div>
          )}
        </>
      )}
    </CardShell>
  );
}

/* ------------------------------------------------------------------ gates -- */

const GATE_COLS = "minmax(140px,1.6fr) repeat(2, minmax(90px,1fr)) minmax(64px,0.6fr)";

function GateHeaderCell({
  label,
  right,
}: {
  label: string;
  right?: boolean;
}): React.ReactElement {
  return (
    <div
      className="q-uppercase-label"
      role="columnheader"
      style={{ textAlign: right ? "right" : "left" }}
    >
      {label}
    </div>
  );
}

/** A pass/fail glyph: ✓ profit / ✗ loss / — when the gate has no baseline. */
function PassGlyph({ value }: { value: boolean | null }): React.ReactElement {
  if (value === null) {
    return <span style={{ color: "var(--text-tertiary)" }}>{DASH}</span>;
  }
  return (
    <span
      aria-label={value ? "pass" : "fail"}
      style={{
        color: value ? "var(--color-profit)" : "var(--color-loss)",
        fontWeight: 600,
      }}
    >
      {value ? "✓" : "✗"}
    </span>
  );
}

function GateRow({ g }: { g: IdeaGate }): React.ReactElement {
  return (
    <div
      role="row"
      className="items-center"
      style={{
        display: "grid",
        gridTemplateColumns: GATE_COLS,
        columnGap: 12,
        padding: "11px 16px",
        borderTop: "1px solid var(--glass-border)",
      }}
    >
      <div
        role="cell"
        className="q-display"
        style={{
          color: "var(--text-primary)",
          fontSize: 13,
          fontWeight: 500,
          minWidth: 0,
          overflow: "hidden",
          textOverflow: "ellipsis",
          whiteSpace: "nowrap",
        }}
      >
        {g.label}
      </div>
      <div
        role="cell"
        className="tabular-nums"
        style={{ textAlign: "right", fontSize: 13, color: "var(--text-secondary)" }}
      >
        {fixed2(g.forward)}
      </div>
      <div
        role="cell"
        className="tabular-nums"
        style={{ textAlign: "right", fontSize: 13, color: "var(--text-secondary)" }}
      >
        {fixed2(g.backtest)}
      </div>
      <div
        role="cell"
        className="tabular-nums"
        style={{ textAlign: "center", fontSize: 14 }}
      >
        <PassGlyph value={g.pass} />
      </div>
    </div>
  );
}

function GatesTable({ gates }: { gates: IdeaGate[] }): React.ReactElement {
  return (
    <div
      style={{
        background: "var(--bg-primary)",
        border: "1px solid var(--glass-border)",
        borderRadius: "var(--radius-md)",
        overflow: "hidden",
      }}
    >
      <div
        role="table"
        aria-label="Backtest vs forward gates"
        style={{ maxHeight: 320, overflowY: "auto" }}
      >
        <div
          role="row"
          style={{
            display: "grid",
            gridTemplateColumns: GATE_COLS,
            columnGap: 12,
            padding: "10px 16px",
            background: "var(--bg-secondary)",
            borderBottom: "1px solid var(--glass-border)",
            position: "sticky",
            top: 0,
            zIndex: 1,
          }}
        >
          <GateHeaderCell label="Gate" />
          <GateHeaderCell label="Forward" right />
          <GateHeaderCell label="Backtest" right />
          <GateHeaderCell label="Pass" />
        </div>
        <div role="rowgroup">
          {gates.length === 0 ? (
            <div
              style={{
                padding: "24px 16px",
                textAlign: "center",
                fontSize: 13,
                color: "var(--text-tertiary)",
              }}
            >
              No gates evaluated yet.
            </div>
          ) : (
            gates.map((g) => <GateRow key={g.label} g={g} />)
          )}
        </div>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ panel -- */

export interface IdeaDetailPanelProps {
  ideaId: string;
  onClose: () => void;
}

export function IdeaDetailPanel({
  ideaId,
  onClose,
}: IdeaDetailPanelProps): React.ReactElement {
  const [s, setS] = useState<S<PaperIdeaDetail>>({ k: "loading" });

  useEffect(() => {
    let on = true;
    setS({ k: "loading" });
    getPaperIdeaDetail(ideaId)
      .then((r) => {
        if (!on) return;
        if (isError(r)) {
          setS({ k: "err" });
          return;
        }
        setS({ k: "ok", d: r.data });
      })
      .catch(() => {
        if (on) setS({ k: "err" });
      });
    return () => {
      on = false;
    };
  }, [ideaId]);

  if (s.k === "loading") {
    return (
      <div className="flex flex-col" style={{ gap: 14 }}>
        <Skeleton style={{ height: 28, width: 240 }} />
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "repeat(auto-fit, minmax(150px, 1fr))",
            gap: 12,
          }}
        >
          {Array.from({ length: 6 }).map((_, i) => (
            <Skeleton key={i} style={{ height: 70, borderRadius: "var(--radius-md)" }} />
          ))}
        </div>
        <Skeleton style={{ height: CHART_HEIGHT + 60, borderRadius: "var(--radius-md)" }} />
        <Skeleton style={{ height: 180, borderRadius: "var(--radius-md)" }} />
      </div>
    );
  }

  if (s.k === "err") {
    return (
      <CardShell>
        <div className="q-uppercase-label">Idea scorecard</div>
        <div
          style={{
            color: "var(--text-tertiary)",
            fontSize: 13,
            padding: "32px 0",
            textAlign: "center",
          }}
        >
          Couldn&rsquo;t load this idea&rsquo;s scorecard.
        </div>
        <button
          type="button"
          onClick={onClose}
          style={{
            alignSelf: "center",
            fontFamily: "var(--font-ui)",
            fontSize: 13,
            color: "var(--text-secondary)",
            background: "transparent",
            border: "1px solid var(--glass-border)",
            borderRadius: "var(--radius-sm)",
            padding: "6px 14px",
            cursor: "pointer",
          }}
        >
          Close
        </button>
      </CardShell>
    );
  }

  if (s.k === "empty") {
    return (
      <CardShell>
        <div className="q-uppercase-label">Idea scorecard</div>
        <div
          style={{
            color: "var(--text-tertiary)",
            fontSize: 13,
            padding: "40px 16px",
            textAlign: "center",
          }}
        >
          Nothing to show for this idea yet.
        </div>
      </CardShell>
    );
  }

  const d = s.d;
  const psrPct =
    d.psr === null || Number.isNaN(d.psr) ? DASH : pct(d.psr * 100).replace("+", "");
  // "Maturity" = calendar days since inception (matches the list card).
  const maturityDays = d.maturity_days === null ? DASH : `${d.maturity_days}d`;
  // n_obs and MinTRL are both in OBSERVATIONS — compare them directly.
  const obsVsMinTrl = `${d.n_obs ?? DASH} / MinTRL ${
    d.mintrl === null || Number.isNaN(d.mintrl) ? DASH : d.mintrl.toFixed(0)
  }`;

  return (
    <div className="flex flex-col" style={{ gap: 14 }}>
      {/* Header */}
      <div className="flex flex-col" style={{ gap: 8 }}>
        <div className="flex items-center justify-between" style={{ gap: 12 }}>
          <span
            className="q-display"
            style={{
              fontSize: 22,
              lineHeight: 1.15,
              color: "var(--text-primary)",
              fontWeight: 600,
              minWidth: 0,
              overflow: "hidden",
              textOverflow: "ellipsis",
              whiteSpace: "nowrap",
            }}
          >
            {d.label}
          </span>
          <VerdictChip verdict={d.verdict} />
        </div>
        <div className="flex items-center" style={{ gap: 8, flexWrap: "wrap" }}>
          <Badge
            variant="secondary"
            style={{ fontSize: 10, background: "var(--bg-secondary)", color: "var(--text-secondary)" }}
          >
            {d.origin_kind}
          </Badge>
          <Badge
            variant="secondary"
            style={{ fontSize: 10, background: "var(--bg-secondary)", color: "var(--text-secondary)" }}
          >
            {d.status}
          </Badge>
          {d.promotion_ready ? (
            <Badge variant="success" style={{ fontSize: 10 }}>
              promotion ready
            </Badge>
          ) : null}
          <span style={{ fontSize: 12, color: "var(--text-tertiary)" }}>
            since {dateShort(d.inception_date)}
          </span>
        </div>
      </div>

      {/* KPI row */}
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fit, minmax(150px, 1fr))",
          gap: 12,
        }}
      >
        <Kpi label="Cum Return" value={pct(d.cum_return_pct)} color={pnlColor(d.cum_return_pct)} />
        <Kpi label="Fwd Sharpe" value={fixed2(d.sharpe)} />
        <Kpi label="Alpha" value={pct(d.alpha)} color={pnlColor(d.alpha)} />
        <Kpi label="PSR" value={`${fixed2(d.psr)} (${psrPct})`} />
        <Kpi label="Max DD" value={pct(d.max_drawdown_pct)} color={pnlColor(d.max_drawdown_pct)} />
        <Kpi label="Maturity" value={maturityDays} />
        <Kpi label="Obs vs MinTRL" value={obsVsMinTrl} />
        <Kpi label="DSR" value={fixed2(d.dsr)} />
        <Kpi label="Cohort Trials" value={`${d.cohort_trial_count}`} />
      </div>

      {/* Dual decay chart */}
      <DualDecayChart d={d} />

      {/* Gates */}
      <GatesTable gates={d.gates} />
    </div>
  );
}
