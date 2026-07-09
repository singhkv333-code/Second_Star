"use client";

/**
 * BenchmarkComparison — the "How this strategy behaves" section on a View
 * detail, rebuilt as a balanced, responsive GRID of varied, real visuals for
 * the CURRENTLY SELECTED strategy (it re-renders whenever the tier changes).
 *
 * Only the strategy's OWN return is ever shown — no benchmark figures,
 * comparisons, or "beat" framing appear anywhere in this section.
 *
 * Grid cells (each a rounded, border-only card; omitted honestly when its data
 * is genuinely unavailable — never fabricated). Cells 1-3 + 5-6 sit in the
 * balanced auto-fit grid; cell 4 is full-width in its own row below (its
 * episode list runs much taller than its neighbours):
 *   1. Allocation & position   → <AllocationPie/>           (weights + long/short; 2+ holdings only)
 *   2. How each holding did     → <ReturnsHeatmap/>          (per-name return + tag; 2+ holdings only)
 *   3. What the simulations say → <MonteCarloDistribution/>  (outcome spread)
 *   4. When it happened before  → <EventReturns/>            (date → return, N of M) — full-width row
 *   5. Reward for the risk taken→ cross-strategy reward:risk bars
 *   6. How well it lined up      → PER-STRATEGY historical alignment + hold window
 *
 * DESIGN LAW (v2): ROUNDED, BORDER-ONLY (no grey fills), plain language (no
 * jargon: CAAR/t/p/MinTRL/DSR/Sharpe/beta — translated to words), >= 13px,
 * aligned/symmetrical, calm/professional, light + dark.
 */

import * as React from "react";
import type { ExpressionDetail, ViewDetail } from "@/lib/types";
import { Num } from "@/components/views/Stat";
import { Hairline } from "@/components/views/ViewSurface";
import { AllocationPie } from "@/components/views/charts/AllocationPie";
import { ReturnsHeatmap } from "@/components/views/charts/ReturnsHeatmap";
import { MonteCarloDistribution } from "@/components/views/charts/MonteCarloDistribution";
import { EventReturns } from "@/components/views/charts/EventReturns";
import { ConfidenceMeter } from "@/components/views/ConfidenceMeter";
import { tierLabel, fmtRatio } from "@/components/views/view-format";

const FONT = "var(--font-display)";

/** One rounded, border-only grid cell with an aligned title + optional subtitle. */
function SubCard({
  title,
  subtitle,
  children,
}: {
  title: string;
  subtitle?: string;
  children: React.ReactNode;
}): React.ReactElement {
  return (
    <section
      style={{
        display: "flex",
        flexDirection: "column",
        gap: 14,
        border: "1px solid var(--glass-border)",
        borderRadius: "var(--radius-lg)",
        background: "var(--bg-base)",
        padding: 20,
        minWidth: 0,
      }}
    >
      <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
        <h3
          style={{
            fontFamily: FONT,
            fontSize: 15,
            fontWeight: 600,
            color: "var(--text-primary)",
            lineHeight: 1.3,
            margin: 0,
          }}
        >
          {title}
        </h3>
        {subtitle && (
          <span
            style={{
              fontFamily: FONT,
              fontSize: 13,
              fontWeight: 400,
              color: "var(--text-tertiary)",
              lineHeight: 1.4,
            }}
          >
            {subtitle}
          </span>
        )}
      </div>
      {children}
    </section>
  );
}

/** One tidy left-anchored reward:risk bar in the cross-strategy comparison. */
function RatioRow({
  label,
  ratio,
  maxAbs,
  selected,
}: {
  label: string;
  ratio: number | null;
  maxAbs: number;
  selected: boolean;
}): React.ReactElement {
  const has = ratio !== null && ratio !== undefined && !Number.isNaN(ratio);
  const pos = (ratio ?? 0) >= 0;
  const color = !has
    ? "var(--text-tertiary)"
    : pos
      ? "var(--color-profit)"
      : "var(--color-loss)";
  const widthPct = has ? Math.max((Math.abs(ratio!) / maxAbs) * 100, 3) : 0;
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
      {/* Full strategy name on its own line — never truncated. */}
      <span
        style={{
          display: "flex",
          alignItems: "center",
          gap: 7,
          fontFamily: FONT,
          fontSize: 13,
          fontWeight: selected ? 600 : 500,
          color: selected ? "var(--text-primary)" : "var(--text-secondary)",
        }}
      >
        <span
          aria-hidden
          style={{
            width: 6,
            height: 6,
            flexShrink: 0,
            borderRadius: "var(--radius-pill)",
            background: selected ? "var(--pivot-blue)" : "transparent",
          }}
        />
        {label}
      </span>
      <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
        <div
          style={{
            flex: 1,
            minWidth: 0,
            height: 10,
            borderRadius: "var(--radius-pill)",
            background: "var(--glass-border)",
            overflow: "hidden",
          }}
        >
          <div
            style={{
              width: `${widthPct}%`,
              height: "100%",
              background: color,
              borderRadius: "var(--radius-pill)",
            }}
          />
        </div>
        <span style={{ width: 44, flexShrink: 0, textAlign: "right" }}>
          <Num size="md" weight={600} color={color}>
            {has ? `${fmtRatio(ratio, 1)}×` : "—"}
          </Num>
        </span>
      </div>
    </div>
  );
}

/** Plain-words read of a 0-100 alignment score (no jargon, never a promise). */
function alignmentWords(score: number | null | undefined): string {
  if (score === null || score === undefined) {
    return "Not enough track record yet — judge this once more seasons play out.";
  }
  if (score >= 80) {
    return "This kind of strategy has lined up strongly with past monsoon seasons — but it is still judged on only a handful of episodes, so treat it as encouraging, not a promise.";
  }
  if (score >= 65) {
    return "This kind of strategy has lined up reasonably well with past seasons. Judged on only a handful of episodes, so treat it as encouraging rather than a promise.";
  }
  if (score >= 50) {
    return "A mixed match with past seasons — it has worked some years and not others. Treat it with caution.";
  }
  return "A weak match with past seasons — the history does not back this strongly yet.";
}

export function BenchmarkComparison({
  view,
  expr,
}: {
  view: ViewDetail;
  expr: ExpressionDetail | null;
}): React.ReactElement | null {
  if (!expr) return null;

  const holdings = expr.holdings ?? [];
  // A single-holding strategy's donut/heatmap just repeats the hero number —
  // only show them once there are at least two holdings to actually compare.
  const hasHoldings = holdings.length > 0 && holdings.length >= 2;
  // Per-holding returns exist only when at least one holding carries a number
  // (option legs carry null — we omit the heatmap honestly for those).
  const hasHoldingReturns =
    holdings.some(
      (h) => typeof h.return_pct === "number" && !Number.isNaN(h.return_pct),
    ) && holdings.length >= 2;

  const exprs = view.expressions ?? [];
  const ratioVals = exprs
    .map((e) => e.risk_return_ratio)
    .filter((r): r is number => typeof r === "number" && !Number.isNaN(r));
  const maxAbs = Math.max(...ratioVals.map((r) => Math.abs(r)), 1);
  const hasRatios = ratioVals.length > 0;

  const episodes = expr.episodes ?? [];
  const hasEpisodes = episodes.length > 0;

  const mc = expr.monte_carlo ?? null;
  const hasMc =
    !!mc && Array.isArray(mc.terminal_pct) && mc.terminal_pct.length >= 5;

  const align = expr.historical_alignment ?? null;
  const alignScore = align?.score ?? null;
  const alignLetter = align?.letter ?? null;

  const benchLabel = view.benchmark_label ?? "Nifty";
  const selName =
    expr.strategy_name ?? expr.plain_label ?? tierLabel(expr.tier);
  const isOption = expr.curve_basis === "underlying";

  // How many cards actually land in the grid (the "how well it lined up" card is
  // always present, +1). A single-holding strategy drops the donut+heatmap, which
  // can leave just ONE card — if we let auto-fit stretch it to a full-width 1fr it
  // reads as a half-empty box. So when only one card renders, cap it to a normal
  // card width and left-align it instead of stretching.
  const gridCardCount =
    (hasHoldings ? 1 : 0) +
    (hasHoldingReturns ? 1 : 0) +
    (hasMc ? 1 : 0) +
    (hasRatios ? 1 : 0) +
    1;

  return (
    <section style={{ display: "flex", flexDirection: "column", gap: 14 }}>
      <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
        <h2
          style={{
            fontFamily: FONT,
            fontSize: 18,
            fontWeight: 600,
            color: "var(--text-primary)",
            lineHeight: 1.3,
            letterSpacing: "-0.01em",
            margin: 0,
          }}
        >
          How this strategy behaves
        </h2>
        <span
          style={{
            fontFamily: FONT,
            fontSize: 13,
            fontWeight: 400,
            color: "var(--text-tertiary)",
            lineHeight: 1.4,
          }}
        >
          {selName}.
          {expr.exit_period && expr.exit_period.trim().length > 0
            ? ` Typical hold: ${expr.exit_period}.`
            : ""}
        </span>
      </div>

      {/* Balanced 2-up grid on desktop, single column on mobile. Cells stretch
          to equal height per row; each is omitted when its data is unavailable. */}
      <div
        style={{
          display: "grid",
          gridTemplateColumns:
            gridCardCount <= 1
              ? "minmax(min(100%, 340px), 560px)"
              : "repeat(auto-fit, minmax(min(100%, 340px), 1fr))",
          justifyContent: "start",
          gap: 16,
          alignItems: "stretch",
        }}
      >
        {/* 1 — Allocation & position */}
        {hasHoldings && (
          <SubCard
            title="Allocation & position"
            subtitle="What it holds, and whether each leg is long or short."
          >
            <AllocationPie holdings={holdings} strategyName={selName} />
          </SubCard>
        )}

        {/* 2 — How each holding did (one tidy treatment: the heatmap) */}
        {hasHoldingReturns && (
          <SubCard
            title="How each holding did"
            subtitle="Average return each time the event happened — greener is stronger."
          >
            <ReturnsHeatmap holdings={holdings} />
          </SubCard>
        )}

        {/* 3 — What the simulations say (the outcome spread) */}
        {hasMc && (
          <SubCard
            title="What the simulations say"
            subtitle="Re-running a single occurrence on resampled history, thousands of times."
          >
            <MonteCarloDistribution
              mc={mc}
              underlyingSymbol={expr.underlying_symbol}
            />
          </SubCard>
        )}

        {/* 5 — Reward for the risk taken (cross-strategy) */}
        {hasRatios && (
          <SubCard
            title="Reward for the risk taken"
            subtitle="Return earned per unit of risk — higher is better, across the strategies."
          >
            <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
              {exprs.map((e) => (
                <RatioRow
                  key={e.id}
                  label={e.strategy_name ?? e.plain_label ?? tierLabel(e.tier)}
                  ratio={e.risk_return_ratio}
                  maxAbs={maxAbs}
                  selected={expr.id === e.id}
                />
              ))}
            </div>
          </SubCard>
        )}

        {/* 6 — How well it lined up (PER-STRATEGY) + hold window */}
        <SubCard
          title="How well it lined up before"
          subtitle="How consistently this strategy has matched the belief in past data."
        >
          <ConfidenceMeter
            label="Historical alignment"
            score={alignScore}
            letter={alignLetter}
          />
          <span
            style={{
              fontFamily: FONT,
              fontSize: 13,
              fontWeight: 400,
              color: "var(--text-tertiary)",
              lineHeight: 1.55,
            }}
          >
            {alignmentWords(alignScore)}
          </span>

          {expr.exit_period && (
            <>
              <Hairline />
              <div style={{ display: "flex", flexDirection: "column", gap: 3 }}>
                <span
                  style={{
                    fontFamily: FONT,
                    fontSize: 13,
                    fontWeight: 500,
                    color: "var(--text-tertiary)",
                  }}
                >
                  How long it&rsquo;s held
                </span>
                <span
                  style={{
                    fontFamily: FONT,
                    fontSize: 14,
                    fontWeight: 400,
                    color: "var(--text-primary)",
                    lineHeight: 1.5,
                  }}
                >
                  {expr.exit_period}
                </span>
              </div>
            </>
          )}

          {isOption && (
            <span
              style={{
                fontFamily: FONT,
                fontSize: 13,
                fontWeight: 400,
                color: "var(--text-tertiary)",
                lineHeight: 1.5,
              }}
            >
              The simulations and holdings above reflect the underlying
              {expr.underlying_symbol ? ` (${expr.underlying_symbol})` : ""}, not
              the option position&rsquo;s own profit or loss.
            </span>
          )}
        </SubCard>
      </div>

      {/* 4 — When it happened before (self-titled bordered card). Rendered
          full-width in its own row rather than inside the auto-fit grid above:
          its episode list runs much taller than its neighbours, so sharing a
          grid row with them stretched every cell to match and left dead
          whitespace beside the shorter cards. */}
      {hasEpisodes && (
        <EventReturns
          episodes={episodes}
          positiveEpisodes={expr.positive_episodes}
          benchmarkLabel={benchLabel}
        />
      )}
    </section>
  );
}

export default BenchmarkComparison;
