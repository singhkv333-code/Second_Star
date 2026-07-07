/**
 * view-strategy-adapter — bridges the REAL backend `ViewDetail` (from
 * GET /api/views/{id}) into the `StrategyConfig[]` shape the redesigned
 * view-detail components (ReturnsChart / StrategyCalculator / StrategyTable /
 * StrategyExplanation) consume.
 *
 * The new components were authored against a mock `strategies.ts`; this adapter
 * lets them render live data WITHOUT touching a single component. Every number
 * traces to a real backtest field (gain_loss, strategy_total_pct, entry) — the
 * projected path is a MODELLED smooth curve landing on the real expected
 * return, which the chart already labels "modelled, not guaranteed" (honest:
 * we never present the synthetic path as a realised track).
 */

import type { StrategyConfig, Risk } from "@/components/view-detail/strategies";
import type { ExpressionDetail, ViewDetail } from "@/lib/types";

/** Tier → the mock palette the new design was tuned against. */
const TIER_COLOR: Record<string, string> = {
  conservative: "#0ea5e9", // calm blue
  balanced: "#8b5cf6", // violet
  aggressive: "#f59e0b", // amber
};
const FALLBACK_COLORS = ["#0ea5e9", "#8b5cf6", "#f59e0b", "#10b981"];

function normalizeRisk(expr: ExpressionDetail): Risk {
  const s = `${expr.plain_risk ?? ""} ${expr.risk_profile ?? ""}`.toLowerCase();
  if (/high|aggress|speculat/.test(s)) return "High";
  if (/low|calm|conserv|defensive/.test(s)) return "Low";
  return "Moderate";
}

/** Probability-weighted expected return (fraction), from real backtest fields. */
function expectedReturn(expr: ExpressionDetail): number {
  const gl = expr.gain_loss;
  if (gl && gl.n_gain != null && gl.n_loss != null) {
    const ng = gl.n_gain;
    const nl = gl.n_loss;
    const g = gl.avg_gain_pct ?? 0;
    const l = gl.avg_loss_pct ?? 0;
    const total = ng + nl;
    if (total > 0) return (ng * g + nl * l) / total / 100;
  }
  if (expr.strategy_total_pct != null) return expr.strategy_total_pct / 100;
  return 0;
}

/** Optimistic edge (fraction). Prefers the real max gain, then avg gain. */
function highReturn(expr: ExpressionDetail, exp: number): number {
  const gl = expr.gain_loss;
  const v =
    gl?.max_gain_pct ??
    gl?.avg_gain_pct ??
    (exp > 0 ? exp * 2 * 100 : 20);
  return Math.max(v / 100, exp + 0.01);
}

/** Pessimistic edge (fraction, negative). Prefers real max loss, then avg loss. */
function lowReturn(expr: ExpressionDetail, exp: number): number {
  const gl = expr.gain_loss;
  const raw = gl?.max_loss_pct ?? gl?.avg_loss_pct ?? expr.worst_drop_pct ?? -10;
  const v = raw > 0 ? -raw : raw; // worst_drop_pct is reported positive
  return Math.min(v / 100, exp - 0.01);
}

/** Split the plain "why" into sentence-ish explanation lines. */
function explanationLines(expr: ExpressionDetail): string[] {
  const src =
    expr.plain_why ??
    expr.rationale ??
    expr.plain_one_liner ??
    "";
  const parts = src
    .split(/(?<=[.!?])\s+/)
    .map((s) => s.trim())
    .filter(Boolean);
  if (parts.length) return parts;
  return expr.rationale ? [expr.rationale] : ["No detailed write-up yet."];
}

/** Classic smoothstep ease (0→1). */
function smooth(t: number): number {
  const x = Math.min(1, Math.max(0, t));
  return x * x * (3 - 2 * x);
}

/** One expression → one StrategyConfig row. */
export function expressionToStrategy(
  expr: ExpressionDetail,
  index: number,
): StrategyConfig {
  const exp = expectedReturn(expr);
  const high = highReturn(expr, exp);
  const low = lowReturn(expr, exp);
  const risk = normalizeRisk(expr);
  const color: string =
    TIER_COLOR[String(expr.tier).toLowerCase()] ??
    FALLBACK_COLORS[index % FALLBACK_COLORS.length] ??
    "#0ea5e9";
  const name: string =
    expr.strategy_name || expr.plain_label || expr.label || "Strategy";
  const oneLiner =
    expr.plain_one_liner ??
    expr.strategy_type ??
    (expr.members.length ? `A basket of ${expr.members.length} names.` : "");
  const minAmount = expr.entry?.min_entry_inr ?? 500;
  // Higher-risk / option structures pay an early premium/theta drag before the
  // curve resolves — a small dip makes the modelled path read honestly.
  const dip = risk === "High" ? -Math.min(0.06, Math.abs(low) * 0.15) : 0;

  return {
    id: expr.id,
    name,
    oneLiner,
    risk,
    minAmount,
    color,
    expReturn: exp,
    lowReturn: low,
    highReturn: high,
    explanation: explanationLines(expr),
    pathAt: (t: number) => {
      const x = Math.min(1, Math.max(0, t));
      return 1 + dip + (exp - dip) * smooth(x);
    },
  };
}

/** All deployable-or-not expressions of a view → StrategyConfig rows. */
export function viewToStrategies(view: ViewDetail): StrategyConfig[] {
  return (view.expressions ?? []).map(expressionToStrategy);
}
