"use client";

/**
 * BenchmarkCompare — the PRIMARY "Strategy vs Nifty" chart on a View detail.
 *
 * Takes the full expressions array and builds an INTERNAL tier toggle
 * (Conservative / Balanced / Aggressive) from whatever tiers are present. For
 * the selected tier it draws ONE clean grouped/paired HORIZONTAL bar:
 *   - Strategy bar  → var(--pivot-blue)
 *   - Nifty bar     → var(--text-tertiary) gray
 * SQUARE bars (radius 0), NO gridlines, NO track fills, NO <13px ticks — the
 * value is read off a direct end-label (>=13px, tabular). Each bar also shows
 * the grown-rupees story (₹1,00,000 → ₹X) via growthOfInvestment.
 *
 * If the selected tier has no finished number (e.g. a crude leg that never
 * cleared), we render an HONEST 'No finished basket yet' empty state — never a
 * fabricated bar.
 *
 * DESIGN LAW: square corners, borders-only (the colored bars are the one
 * allowed signal fill), every label/caption >= 13px, Inter-tabular numerals.
 */

import * as React from "react";
import type { ExpressionDetail, ExpressionTier } from "@/lib/types";
import { Num } from "../Stat";
import {
  fmtPct,
  growthOfInvestment,
  signColor,
  tierLabel,
  trustBadge,
  winRateLabel,
} from "../view-format";

const TIER_ORDER: ExpressionTier[] = ["conservative", "balanced", "aggressive"];

const LABEL_W = 84;
const PCT_W = 76;
const GAP = 12;

function pickByTier(
  expressions: ExpressionDetail[],
  tier: ExpressionTier,
): ExpressionDetail | undefined {
  return expressions.find((e) => e.tier === tier);
}

function hasNumber(e: ExpressionDetail | undefined): boolean {
  return !!e && e.strategy_total_pct !== null && e.strategy_total_pct !== undefined;
}

function CompareBar({
  label,
  pct,
  growthLabel,
  fill,
  scale,
}: {
  label: string;
  pct: number;
  growthLabel: string;
  fill: string;
  scale: number;
}): React.ReactElement {
  const magnitude = scale > 0 ? Math.min(100, (Math.abs(pct) / scale) * 100) : 0;
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
      <div style={{ display: "flex", alignItems: "center", gap: GAP }}>
        <span
          style={{
            width: LABEL_W,
            flexShrink: 0,
            fontFamily: "var(--font-display)",
            fontSize: 13,
            fontWeight: 500,
            color: "var(--text-tertiary)",
            whiteSpace: "nowrap",
            overflow: "hidden",
            textOverflow: "ellipsis",
          }}
        >
          {label}
        </span>
        <div
          style={{
            flex: 1,
            minWidth: 0,
            display: "flex",
            alignItems: "center",
            gap: 10,
          }}
        >
          <div style={{ flex: 1, minWidth: 0 }}>
            <div
              style={{
                height: 20,
                width: `${magnitude}%`,
                minWidth: 2,
                background: fill,
                borderRadius: "var(--radius-xs)",
                transition: "width 320ms var(--ease-quartr)",
              }}
            />
          </div>
          <span
            style={{
              width: PCT_W,
              flexShrink: 0,
              textAlign: "right",
            }}
          >
            <Num size="lg" color={signColor(pct)}>
              {fmtPct(pct)}
            </Num>
          </span>
        </div>
      </div>
      <span
        style={{
          paddingLeft: LABEL_W + GAP,
          fontFamily: "var(--font-display)",
          fontVariantNumeric: "tabular-nums",
          fontSize: 13,
          color: "var(--text-tertiary)",
          letterSpacing: "-0.01em",
        }}
      >
        {growthLabel}
      </span>
    </div>
  );
}

export function BenchmarkCompare({
  expressions,
  benchmarkLabel = "Nifty",
  investmentBase = 100000,
}: {
  expressions: ExpressionDetail[];
  benchmarkLabel?: string;
  investmentBase?: number;
}): React.ReactElement {
  const tiers = TIER_ORDER.filter((t) =>
    expressions.some((e) => e.tier === t),
  );

  // Default to the first tier that actually has a finished number, else first.
  const initialTier =
    tiers.find((t) => hasNumber(pickByTier(expressions, t))) ??
    tiers[0] ??
    "conservative";

  const [selected, setSelected] = React.useState<ExpressionTier>(initialTier);

  const expr = pickByTier(expressions, selected);

  const Toggle = (
    <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
      {tiers.map((t) => {
        const isSel = t === selected;
        return (
          <button
            key={t}
            type="button"
            onClick={() => setSelected(t)}
            className="rounded-md"
            style={{
              fontFamily: "var(--font-display)",
              fontSize: 13,
              fontWeight: isSel ? 600 : 500,
              padding: "7px 12px",
              minWidth: 116,
              textAlign: "center",
              borderRadius: "var(--radius-md)",
              border: `1px solid ${
                isSel ? "var(--text-primary)" : "var(--glass-border)"
              }`,
              background: "var(--bg-base)",
              color: isSel ? "var(--text-primary)" : "var(--text-tertiary)",
              cursor: "pointer",
              transition: "border-color 180ms var(--ease-quartr)",
            }}
          >
            {tierLabel(t)}
          </button>
        );
      })}
    </div>
  );

  // Honest empty state — selected tier has no finished basket number.
  if (!hasNumber(expr)) {
    return (
      <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
        {tiers.length > 1 && Toggle}
        <div
          className="rounded-lg"
          style={{
            border: "1px solid var(--glass-border)",
            borderRadius: "var(--radius-lg)",
            padding: "20px",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            background: "var(--bg-base)",
          }}
        >
          <span
            style={{
              fontFamily: "var(--font-display)",
              fontSize: 15,
              color: "var(--text-tertiary)",
            }}
          >
            No finished basket yet
          </span>
        </div>
      </div>
    );
  }

  const stratPct = expr!.strategy_total_pct!;
  const niftyPct = expr!.nifty_total_pct;
  const scale = Math.max(
    Math.abs(stratPct),
    niftyPct != null ? Math.abs(niftyPct) : 0,
    1,
  );

  const stratGrowth = growthOfInvestment(stratPct, investmentBase).label;
  const niftyGrowth =
    niftyPct != null ? growthOfInvestment(niftyPct, investmentBase).label : null;

  const caption = `Across all past episodes combined (not per year) · ${winRateLabel(
    expr!.pct_episodes_beat,
    expr!.n_episodes,
    benchmarkLabel,
  )} · ${expr!.trust_badge ?? trustBadge(null)}`;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
      {tiers.length > 1 && Toggle}

      <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
        <CompareBar
          label="Strategy"
          pct={stratPct}
          growthLabel={stratGrowth}
          fill="var(--pivot-blue)"
          scale={scale}
        />
        {niftyPct != null && niftyGrowth != null && (
          <CompareBar
            label={benchmarkLabel}
            pct={niftyPct}
            growthLabel={niftyGrowth}
            fill="var(--text-tertiary)"
            scale={scale}
          />
        )}
      </div>

      <span
        style={{
          fontFamily: "var(--font-display)",
          fontSize: 13,
          lineHeight: 1.5,
          color: "var(--text-tertiary)",
        }}
      >
        {caption}
      </span>
    </div>
  );
}
