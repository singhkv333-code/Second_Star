"use client";

/**
 * ExpressionHero — the two-column hero of the View detail page, wired to REAL
 * expression data (the /view-detail mock design, productionised):
 *
 *   LEFT  — ExpressionReturnsChart: every expression's real equity curve drawn
 *           as one thin line (selected = bolder), plus the REAL benchmark
 *           series from the backend curve as a dashed baseline. All series are
 *           rescaled live to the ₹ amount typed into the ticket.
 *   RIGHT — ExpressionTicket: the sticky "trade ticket". One ₹ amount input,
 *           quick-add pills, one selectable row per expression showing what the
 *           amount projects to (average past occurrence, worst/best honest
 *           range), and the REAL Deploy CTA (deployExpression → workflow).
 *
 * DESIGN LAW: border-only, no pastel fills, hairlines, color is for data.
 * NEVER fabricate — projections come from strategy_total_pct / worst_drop_pct /
 * episode extremes; options tiers with no backtest show "Priced at deploy".
 */

import * as React from "react";
import { ArrowRight, Info, Loader2 } from "lucide-react";
import {
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { ExpressionDetail } from "@/lib/types";
import { useTokenColors } from "@/components/views/use-token-color";
import { tierLabel } from "@/components/views/view-format";

const FONT = "var(--font-display)";

// Per-expression line colors, assigned by position in the expressions array so
// the chart, ticket and legend always agree. Tiers arrive ordered
// conservative → balanced → aggressive, matching blue → violet → amber.
const EXPR_COLORS = ["#0ea5e9", "#8b5cf6", "#f59e0b", "#10b981", "#f43f5e"];

export function exprColor(index: number): string {
  return EXPR_COLORS[index % EXPR_COLORS.length]!;
}

export function exprName(e: ExpressionDetail): string {
  return e.strategy_name ?? e.plain_label ?? tierLabel(e.tier);
}

// Compact legend label — the full strategy_name ("Own a bundle of 7 AI/IT
// companies", "High-payoff bet on Infosys") is great in tables/cards but wraps
// the inline legend onto two lines. Distil each of the three known name shapes
// down to a short, one-line form while keeping the theme/entity. Falls back to
// the full name if the shape is unexpected.
export function exprShortName(e: ExpressionDetail): string {
  const full = e.strategy_name ?? e.plain_label ?? "";
  // Conservative: "Own a bundle of N <theme> companies/firms" → "<theme> bundle"
  const bundle = /bundle of\s+\d+\s+(.+?)\s+(?:companies|firms|stocks)\b/i.exec(full);
  if (bundle) {
    const theme = bundle[1]!.split(/\s+&\s+/)[0]!.trim(); // "oil & defence" → "oil"
    return `${theme.charAt(0).toUpperCase()}${theme.slice(1)} bundle`;
  }
  // Balanced: "Own the bundle, cushioned against market falls" → "Cushioned bundle"
  if (/cushioned/i.test(full)) return "Cushioned bundle";
  // Aggressive: "High-payoff bet on <entity> [rising]" → "<entity> bet"
  const bet = /bet on\s+(.+?)(?:\s+rising)?\s*$/i.exec(full);
  if (bet) return `${bet[1]!.trim()} bet`;
  return exprName(e);
}

// Minimum ₹ to deploy a row. The expression payload carries no per-instrument
// minimum, so we use the real structural floor: options trade in fixed lots →
// a genuine minimum ticket (a lone lottery call is cheaper than a defined-risk
// spread); a basket/ETF has almost none. Heuristic, not a live quote.
export function exprMinAmount(e: ExpressionDetail): number {
  const kind = (e.expression_kind ?? "").toLowerCase();
  const type = (e.strategy_type ?? "").toLowerCase();
  const isOption =
    kind.includes("option") ||
    type.includes("option") ||
    type.includes("spread") ||
    (e.option_legs?.length ?? 0) > 0 ||
    e.option_model != null;
  if (isOption) return e.tier === "aggressive" ? 1000 : 5000;
  return 500;
}

// ── ₹ formatting (en-IN, mirrors components/view-detail/strategies.ts) ──────

function inr(v: number): string {
  const r = Math.round(v);
  const sign = r < 0 ? "−" : "";
  return `${sign}₹${Math.abs(r).toLocaleString("en-IN")}`;
}

function inrCompact(v: number): string {
  const a = Math.abs(v);
  const sign = v < 0 ? "−" : "";
  if (a >= 1e7) return `${sign}₹${(a / 1e7).toFixed(a >= 1e8 ? 0 : 1)}Cr`;
  if (a >= 1e5) return `${sign}₹${(a / 1e5).toFixed(a >= 1e6 ? 0 : 1)}L`;
  if (a >= 1e3) return `${sign}₹${Math.round(a / 1e3)}k`;
  return `${sign}₹${Math.round(a)}`;
}

function fmtYTick(v: number): string {
  const a = Math.abs(v);
  if (a >= 1e6) return `₹${(v / 1e5).toFixed(1)}L`;
  if (a >= 1e4) return `₹${Math.round(v / 1000)}k`;
  return `₹${Math.round(v).toLocaleString("en-IN")}`;
}

function fmtSignedPct(frac: number): string {
  const s = frac > 0 ? "+" : frac < 0 ? "−" : "";
  return `${s}${Math.abs(frac * 100).toFixed(1)}%`;
}

// ── shared projection math (real numbers only, no invention) ────────────────

/** Curve is usable when it has >= 2 points and a positive starting value. */
function usableCurve(e: ExpressionDetail): boolean {
  return (
    Array.isArray(e.equity_curve) &&
    e.equity_curve.length >= 2 &&
    (e.equity_curve[0]?.strategy ?? 0) > 0
  );
}

/** End-to-end return fraction of the expression's own curve (null if none). */
function curveReturn(e: ExpressionDetail): number | null {
  if (!usableCurve(e) || !e.equity_curve) return null;
  const first = e.equity_curve[0]!.strategy;
  const last = e.equity_curve[e.equity_curve.length - 1]!.strategy;
  return last / first - 1;
}

// ── the chart ────────────────────────────────────────────────────────────────

const BENCH_KEY = "bench";
const exprKey = (id: string): string => `e_${id}`;

type Row = Record<string, number>;

export function ExpressionReturnsChart({
  expressions,
  selectedId,
  amount,
  benchmarkLabel,
  caption,
}: {
  expressions: ExpressionDetail[];
  selectedId: string | null;
  amount: number;
  benchmarkLabel: string | null;
  /** Honest context line rendered in the hairline footer. */
  caption: React.ReactNode;
}): React.ReactElement {
  const c = useTokenColors({
    profit: "--color-profit",
    loss: "--color-loss",
    tertiary: "--text-tertiary",
    secondary: "--text-secondary",
    border: "--glass-border",
    borderFocus: "--glass-border-focus",
    bg: "--bg-base",
    ink: "--text-primary",
  });

  const drawable = expressions.filter(usableCurve);
  // The dashed baseline comes from the SELECTED expression's real benchmark
  // series (each curve carries its own aligned benchmark), falling back to the
  // first drawable one.
  const benchSource =
    drawable.find((e) => e.id === selectedId) ?? drawable[0] ?? null;

  const rows = React.useMemo((): Row[] => {
    const byIdx = new Map<number, Row>();
    for (const e of drawable) {
      if (!e.equity_curve) continue;
      const first = e.equity_curve[0]!.strategy;
      const scale = amount / first;
      e.equity_curve.forEach((p, i) => {
        const row = byIdx.get(i) ?? { day: i };
        row[exprKey(e.id)] = p.strategy * scale;
        byIdx.set(i, row);
      });
    }
    if (benchSource?.equity_curve && (benchSource.equity_curve[0]?.benchmark ?? 0) > 0) {
      const firstB = benchSource.equity_curve[0]!.benchmark;
      const scaleB = amount / firstB;
      benchSource.equity_curve.forEach((p, i) => {
        const row = byIdx.get(i) ?? { day: i };
        row[BENCH_KEY] = p.benchmark * scaleB;
        byIdx.set(i, row);
      });
    }
    return Array.from(byIdx.values()).sort(
      (a, b) => (a.day ?? 0) - (b.day ?? 0),
    );
  }, [drawable, benchSource, amount]);

  const labelByKey = React.useMemo(() => {
    const m = new Map<string, string>();
    for (const e of drawable) m.set(exprKey(e.id), exprName(e));
    m.set(BENCH_KEY, benchmarkLabel ?? "Benchmark");
    return m;
  }, [drawable, benchmarkLabel]);

  const endRow = rows[rows.length - 1];
  const benchEnd = (endRow?.[BENCH_KEY] as number | undefined) ?? null;

  // Honest empty state — a developing view has no return path to draw.
  if (drawable.length === 0) {
    return (
      <div
        style={{
          height: 320,
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          borderTop: `1px solid ${c.border}`,
          borderBottom: `1px solid ${c.border}`,
        }}
      >
        <span style={{ fontFamily: FONT, fontSize: 14, color: c.tertiary }}>
          No return path yet — this view is still developing.
        </span>
      </div>
    );
  }

  return (
    // Kalshi-clean: the chart floats on the page — no card box.
    <div style={{ display: "flex", flexDirection: "column", gap: 14, minWidth: 0 }}>
      {/* title + live legend */}
      <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
        <span
          style={{
            fontFamily: FONT,
            fontSize: 16,
            fontWeight: 600,
            letterSpacing: "-0.01em",
            color: "var(--text-primary)",
          }}
        >
          What {inr(amount)} could have become
        </span>
        <div style={{ display: "flex", flexWrap: "wrap", gap: "6px 18px" }}>
          {benchEnd != null && (
            <LegendItem
              color={c.tertiary}
              dashed
              label={benchmarkLabel ?? "Benchmark"}
              value={inr(benchEnd)}
              valueColor={c.secondary}
              secondary={c.secondary}
            />
          )}
          {drawable.map((e) => {
            const ret = curveReturn(e) ?? 0;
            const idx = expressions.indexOf(e);
            return (
              <LegendItem
                key={e.id}
                color={exprColor(idx)}
                label={exprShortName(e)}
                value={fmtSignedPct(ret)}
                valueColor={ret >= 0 ? c.profit : c.loss}
                secondary={c.secondary}
              />
            );
          })}
        </div>
      </div>

      <div style={{ width: "100%", height: 320 }}>
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={rows} margin={{ top: 8, right: 8, bottom: 0, left: 4 }}>
            <CartesianGrid
              stroke={c.border}
              strokeDasharray="2 5"
              horizontal
              vertical={false}
            />
            <XAxis
              dataKey="day"
              type="number"
              domain={[0, "dataMax"]}
              tick={{ fontSize: 12, fill: c.tertiary, fontFamily: FONT }}
              tickLine={false}
              axisLine={false}
              tickCount={5}
              tickFormatter={(v: number) => `${Math.round(v)}d`}
            />
            <YAxis
              orientation="left"
              tick={{ fontSize: 12, fill: c.tertiary, fontFamily: FONT }}
              tickFormatter={fmtYTick}
              tickLine={false}
              axisLine={false}
              width={56}
              domain={["auto", "auto"]}
            />
            <Tooltip
              cursor={{ stroke: c.borderFocus, strokeWidth: 1 }}
              contentStyle={{
                borderRadius: 12,
                border: `1px solid ${c.border}`,
                background: c.bg,
                fontFamily: FONT,
                fontSize: 13,
                fontVariantNumeric: "tabular-nums",
                color: c.ink,
              }}
              labelFormatter={(v) => `Day ${v} in market`}
              formatter={(value: number, name: string) => [
                inr(value),
                labelByKey.get(name) ?? name,
              ]}
            />
            <Line
              type="monotone"
              dataKey={BENCH_KEY}
              stroke={c.tertiary}
              strokeWidth={1.5}
              strokeDasharray="5 5"
              dot={false}
              isAnimationActive={false}
              connectNulls
            />
            {drawable.map((e) => {
              const idx = expressions.indexOf(e);
              const active = selectedId === e.id;
              return (
                <Line
                  key={e.id}
                  type="monotone"
                  dataKey={exprKey(e.id)}
                  stroke={exprColor(idx)}
                  strokeWidth={active ? 2.5 : 1.6}
                  strokeOpacity={selectedId && !active ? 0.45 : 1}
                  dot={false}
                  isAnimationActive={false}
                  connectNulls
                />
              );
            })}
          </LineChart>
        </ResponsiveContainer>
      </div>

      {/* hairline footer: honest context caption */}
      <div
        style={{
          borderTop: `1px solid ${c.border}`,
          paddingTop: 12,
          fontFamily: FONT,
          fontSize: 13,
          lineHeight: 1.5,
          color: "var(--text-tertiary)",
        }}
      >
        {caption}
      </div>
    </div>
  );
}

function LegendItem({
  color,
  dashed,
  label,
  value,
  valueColor,
  secondary,
}: {
  color: string;
  dashed?: boolean;
  label: string;
  value: string;
  valueColor: string;
  secondary: string;
}): React.ReactElement {
  return (
    <span
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: 8,
        fontFamily: FONT,
        fontSize: 13,
        whiteSpace: "nowrap",
      }}
    >
      <span
        aria-hidden
        style={{
          width: 16,
          height: 0,
          borderTop: dashed ? `2px dashed ${color}` : `2.5px solid ${color}`,
          borderRadius: 2,
        }}
      />
      <span style={{ color: secondary, fontWeight: 500 }}>{label}</span>
      <span
        style={{
          color: valueColor,
          fontWeight: 700,
          fontVariantNumeric: "tabular-nums",
        }}
      >
        {value}
      </span>
    </span>
  );
}

// ── the ticket ───────────────────────────────────────────────────────────────

const DEFAULT_AMT = 100_000;
const MIN_AMT = 100;
const MAX_AMT = 5_000_000;
const QUICK_ADDS = [10_000, 25_000, 50_000] as const;

export function ExpressionTicket({
  expressions,
  selectedId,
  onSelect,
  amount,
  onAmount,
  onDeploy,
  deployingId,
  deployError,
}: {
  expressions: ExpressionDetail[];
  selectedId: string | null;
  onSelect: (id: string) => void;
  amount: number;
  onAmount: (v: number) => void;
  onDeploy: (expr: ExpressionDetail) => void;
  deployingId: string | null;
  deployError: string | null;
}): React.ReactElement | null {
  const c = useTokenColors({
    loss: "--color-loss",
    tertiary: "--text-tertiary",
    border: "--glass-border",
    bg: "--bg-base",
    accent: "--text-primary",
  });

  const clamp = (v: number): number =>
    Math.max(MIN_AMT, Math.min(MAX_AMT, Math.round(v)));

  if (expressions.length === 0) return null;

  const selected =
    expressions.find((e) => e.id === selectedId) ?? expressions[0]!;
  const selPct = selected.strategy_total_pct;
  const selProjected = selPct != null ? amount * (1 + selPct / 100) : null;
  const busy = deployingId === selected.id;
  const deployable = selected.is_deployable || selected.workflow_id != null;

  // Snug width for the amount input: tabular digits are 1ch each; commas are
  // narrower, so trim ~0.5ch per comma to keep ₹ + number tight (no gap).
  const amtStr = amount.toLocaleString("en-IN");
  const amtInputWidth = `calc(${amtStr.length}ch - ${
    (amtStr.match(/,/g) || []).length * 0.5
  }ch)`;

  return (
    <div
      style={{
        border: `1px solid ${c.border}`,
        borderRadius: "var(--radius-lg)",
        background: c.bg,
        padding: 20,
        display: "flex",
        flexDirection: "column",
        gap: 16,
      }}
    >
      <style>{`
        .vwd-quick { transition: background-color 160ms var(--ease-quartr), opacity 160ms var(--ease-quartr); }
        .vwd-quick:hover { opacity: 0.88; }
        .vwd-quick:focus-visible { outline: none; box-shadow: 0 0 0 3px color-mix(in srgb, var(--pivot-blue) 22%, transparent); }
        .vwd-reset { transition: color 160ms var(--ease-quartr); }
        .vwd-reset:hover { color: var(--text-secondary) !important; }
        .vwd-row:not([aria-pressed="true"]):hover { border-color: var(--glass-border-hover) !important; background: var(--glass-bg-hover) !important; }
        .vwd-row:focus-visible { outline: none; box-shadow: 0 0 0 3px color-mix(in srgb, var(--pivot-blue) 20%, transparent); }
        .vwd-deploy { transition: opacity 160ms var(--ease-quartr), transform 160ms var(--ease-quartr); }
        .vwd-deploy:hover { opacity: 0.9; }
        .vwd-deploy:active { transform: translateY(1px); }
      `}</style>

      <div style={{ display: "flex", flexDirection: "column", gap: 3 }}>
        <span
          style={{
            fontFamily: FONT,
            fontSize: 16,
            fontWeight: 600,
            letterSpacing: "-0.01em",
            color: "var(--text-primary)",
          }}
        >
          Strategy calculator
        </span>
        <span
          style={{
            fontFamily: FONT,
            fontSize: 13,
            color: "var(--text-tertiary)",
            lineHeight: 1.45,
          }}
        >
          Enter an amount to see what each strategy averaged in past
          occurrences.
        </span>
      </div>

      {/* per-strategy rows — a clean, borderless radio list. Selection is the
          filled dot alone; the right rail carries the minimum ticket. */}
      <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
        {expressions.map((e) => {
          const isSel = selected.id === e.id;
          const kind = e.strategy_type ?? tierLabel(e.tier);
          return (
            <button
              key={e.id}
              onClick={() => onSelect(e.id)}
              aria-pressed={isSel}
              className="vwd-row"
              style={{
                textAlign: "left",
                display: "flex",
                alignItems: "center",
                gap: 12,
                padding: "10px 8px",
                borderRadius: "var(--radius-md)",
                cursor: "pointer",
                border: "none",
                background: "transparent",
                transition: "background-color 160ms var(--ease-quartr)",
              }}
            >
              {/* radio indicator — shadcn RadioGroupItem: 16px circle, shadow-xs.
                  Selected = bold near-black ring + a white gap + an 8px filled
                  --primary dot. Unselected = a light --input ring. border-box so
                  the 1.5px selected border never shifts the row. */}
              <span
                aria-hidden
                style={{
                  flexShrink: 0,
                  width: 16,
                  height: 16,
                  boxSizing: "border-box",
                  borderRadius: 999,
                  display: "grid",
                  placeItems: "center",
                  background: "hsl(var(--background))",
                  border: isSel
                    ? "1.5px solid hsl(var(--primary))"
                    : "1px solid hsl(var(--input))",
                  boxShadow: "0 1px 2px 0 rgb(0 0 0 / 0.05)",
                }}
              >
                {isSel && (
                  <span
                    style={{
                      width: 8,
                      height: 8,
                      borderRadius: 999,
                      background: "hsl(var(--primary))",
                    }}
                  />
                )}
              </span>

              {/* name + type */}
              <span
                style={{
                  display: "flex",
                  flexDirection: "column",
                  gap: 2,
                  minWidth: 0,
                  flex: 1,
                }}
              >
                <span
                  style={{
                    fontFamily: FONT,
                    fontSize: 14,
                    fontWeight: 600,
                    color: "var(--text-primary)",
                    overflow: "hidden",
                    textOverflow: "ellipsis",
                    whiteSpace: "nowrap",
                  }}
                >
                  {exprShortName(e)}
                </span>
                <span
                  style={{
                    fontFamily: FONT,
                    fontSize: 12,
                    color: "var(--text-tertiary)",
                    whiteSpace: "nowrap",
                    overflow: "hidden",
                    textOverflow: "ellipsis",
                  }}
                >
                  {kind}
                </span>
              </span>

              {/* right rail: minimum ticket */}
              <span
                style={{
                  fontFamily: FONT,
                  fontVariantNumeric: "tabular-nums",
                  fontSize: 12,
                  color: "var(--text-tertiary)",
                  whiteSpace: "nowrap",
                  flexShrink: 0,
                }}
              >
                min {inrCompact(exprMinAmount(e))}
              </span>
            </button>
          );
        })}
      </div>

      {/* amount input — below the strategy selection */}
      <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
        <label
          style={{
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            gap: 10,
            border: `1px solid ${c.border}`,
            borderRadius: "var(--radius-md)",
            padding: "12px 16px",
          }}
        >
          <span
            style={{
              fontFamily: FONT,
              fontSize: 15,
              fontWeight: 500,
              color: "var(--text-tertiary)",
            }}
          >
            Amount
          </span>
          <span
            style={{
              display: "inline-flex",
              alignItems: "baseline",
              gap: 3,
            }}
          >
            <span
              style={{
                fontFamily: FONT,
                fontSize: 18,
                fontWeight: 700,
                color: "var(--text-primary)",
              }}
            >
              ₹
            </span>
            <input
              aria-label="Amount to deploy"
              value={amtStr}
              onChange={(e) => {
                const raw = Number(e.target.value.replace(/[^0-9]/g, ""));
                if (Number.isFinite(raw)) onAmount(clamp(raw || MIN_AMT));
              }}
              inputMode="numeric"
              style={{
                fontFamily: FONT,
                fontVariantNumeric: "tabular-nums",
                fontSize: 18,
                fontWeight: 700,
                letterSpacing: "-0.01em",
                color: "var(--text-primary)",
                background: "transparent",
                border: "none",
                outline: "none",
                textAlign: "right",
                width: amtInputWidth,
                padding: 0,
              }}
            />
          </span>
        </label>
        <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
          {QUICK_ADDS.map((q) => (
            <button
              key={q}
              onClick={() => onAmount(clamp(amount + q))}
              className="vwd-quick"
              style={{
                fontFamily: FONT,
                fontVariantNumeric: "tabular-nums",
                fontSize: 13,
                fontWeight: 600,
                letterSpacing: "-0.01em",
                color: "#0a0a0a",
                background: "#ffffff",
                border: "none",
                borderRadius: "var(--radius-pill, 999px)",
                padding: "7px 15px",
                cursor: "pointer",
              }}
            >
              +{inrCompact(q)}
            </button>
          ))}
          <button
            onClick={() => onAmount(DEFAULT_AMT)}
            className="vwd-reset"
            style={{
              marginLeft: "auto",
              fontFamily: FONT,
              fontSize: 13,
              fontWeight: 500,
              color: "var(--text-tertiary)",
              background: "transparent",
              border: "none",
              padding: "7px 4px",
              cursor: "pointer",
            }}
          >
            Reset
          </button>
        </div>
      </div>

      {/* trade ticket footer: selected summary + REAL deploy */}
      <div
        style={{
          display: "flex",
          flexDirection: "column",
          gap: 12,
          borderTop: `1px solid ${c.border}`,
          paddingTop: 16,
        }}
      >

        {/* the hero number: what the SELECTED strategy projects to */}
        {selProjected != null ? (
          <div
            style={{
              display: "flex",
              alignItems: "center",
              justifyContent: "space-between",
              gap: 12,
            }}
          >
            <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
              <span
                style={{
                  fontFamily: FONT,
                  fontSize: 13,
                  color: "var(--text-tertiary)",
                }}
              >
                Projected value
              </span>
              <span
                style={{
                  fontFamily: FONT,
                  fontSize: 12,
                  color: "var(--text-tertiary)",
                }}
              >
                on {inr(amount)}
              </span>
            </div>
            <span
              style={{
                fontFamily: "var(--font-serif)",
                fontVariantNumeric: "tabular-nums",
                fontSize: 32,
                fontWeight: 600,
                letterSpacing: "-0.01em",
                color: "var(--text-primary)",
                lineHeight: 1.05,
                whiteSpace: "nowrap",
              }}
            >
              {inr(selProjected)}
            </span>
          </div>
        ) : (
          <span
            style={{
              fontFamily: FONT,
              fontSize: 12.5,
              lineHeight: 1.45,
              color: "var(--text-tertiary)",
            }}
          >
            Priced at deploy — the payoff is set by the live option chain when
            you arm it.
          </span>
        )}

        <button
          type="button"
          onClick={() => onDeploy(selected)}
          disabled={!deployable || busy}
          aria-label={`Deploy ${exprName(selected)}`}
          className="vwd-deploy"
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
            padding: "8px 16px",
            cursor: deployable && !busy ? "pointer" : "default",
            opacity: deployable ? 1 : 0.5,
          }}
        >
          {busy ? (
            <>
              <Loader2 size={15} className="animate-spin" aria-hidden />
              Arming…
            </>
          ) : (
            <>
              Deploy this strategy
              <ArrowRight size={16} strokeWidth={2} aria-hidden />
            </>
          )}
        </button>
        {deployError && (
          <span
            role="alert"
            style={{
              fontFamily: FONT,
              fontSize: 13,
              color: c.loss,
              lineHeight: 1.45,
            }}
          >
            {deployError}
          </span>
        )}
      </div>

      <DisclosureNote>
        Projections are the average past occurrence with an honest worst→best
        range — not a promise. Pivot arms the trigger; you place every order in
        your broker. This is analysis, not financial advice.
      </DisclosureNote>
    </div>
  );
}

/**
 * A compact "ⓘ Agent disclosures" trigger that reveals the full disclaimer in a
 * floating card on hover or keyboard focus — keeps the hero calm while keeping
 * the required legal copy one gesture away. Accessible: focusable, ESC-free
 * (blur closes it), and the panel is announced as a note.
 */
function DisclosureNote({ children }: { children: React.ReactNode }) {
  const [open, setOpen] = React.useState(false);

  return (
    <span
      style={{ position: "relative", display: "inline-flex", width: "fit-content" }}
      onMouseEnter={() => setOpen(true)}
      onMouseLeave={() => setOpen(false)}
    >
      <button
        type="button"
        aria-expanded={open}
        aria-label="How to read this"
        onFocus={() => setOpen(true)}
        onBlur={() => setOpen(false)}
        style={{
          display: "inline-flex",
          alignItems: "center",
          gap: 5,
          padding: 0,
          border: "none",
          background: "none",
          cursor: "help",
          fontFamily: FONT,
          fontSize: 12,
          lineHeight: 1.5,
          color: "var(--text-tertiary)",
        }}
      >
        <Info size={13} strokeWidth={2} aria-hidden />
        How to read this
      </button>

      {open && (
        <span
          role="note"
          style={{
            position: "absolute",
            top: "calc(100% + 8px)",
            left: 0,
            zIndex: 20,
            width: 300,
            maxWidth: "min(300px, calc(100vw - 32px))",
            padding: "12px 14px",
            fontFamily: FONT,
            fontSize: 12,
            lineHeight: 1.55,
            color: "var(--text-secondary)",
            background: "var(--glass-bg-hover)",
            border: "none",
            borderRadius: 6,
            boxShadow: "none",
          }}
        >
          {children}
        </span>
      )}
    </span>
  );
}
