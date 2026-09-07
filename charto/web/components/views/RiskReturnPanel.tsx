"use client";

/**
 * RiskReturnPanel — the per-kind HEADLINE viz dispatcher for one expression.
 *
 * Switches on expression_kind and renders the most honest visual for that
 * structure (all data is real, computed client-side — never fabricated):
 *   option_strategy → <PayoffDiagram>   (structural expiry payoff)
 *   basket          → <AllocationDonut> + <ReturnDistribution>
 *   pair            → long/short leg rows + <BenchmarkCompare>
 *   (fallback)      → <AllocationDonut> if weighted, else <BenchmarkCompare>
 *
 * The QuantConnect-style metric strip (Return/Excess/PSR/DSR…), the confidence
 * meters, the trust ladder and the risk strip all live in ExpressionCard — this
 * component owns ONLY the headline viz. Returns null when a kind has nothing to
 * draw (the card gates the zone on `hasHeadlineViz`).
 *
 * All numerals are Inter-tabular via the chart components (no JetBrains-Mono numeral var).
 */

import * as React from "react";
import type {
  BacktestScores,
  ExpressionInstrument,
  ExpressionKind,
  ExpressionStructure,
} from "@/lib/types";
import { PayoffDiagram } from "./charts/PayoffDiagram";
import { AllocationDonut } from "./charts/AllocationDonut";
import { ReturnDistribution } from "./charts/ReturnDistribution";
import { type Leg } from "./charts/payoff-math";
import { Num } from "./Stat";
import { fmtPct, signColor } from "./view-format";

// ---------------------------------------------------------------------------
// Kind classification (ExpressionKind is an open string — branch defensively)
// ---------------------------------------------------------------------------

export type VizKind = "option" | "basket" | "pair" | "fallback";

export function classifyKind(kind: ExpressionKind | null | undefined): VizKind {
  const k = (kind ?? "").toLowerCase();
  if (k.includes("option")) return "option";
  if (k.includes("basket")) return "basket";
  if (k.includes("pair")) return "pair";
  return "fallback";
}

function hasWeights(structure: ExpressionStructure): boolean {
  return (
    !!structure.weights && Object.keys(structure.weights).length > 0
  );
}

function hasLegs(structure: ExpressionStructure): boolean {
  return Array.isArray(structure.legs) && structure.legs.length > 0;
}

function hasBenchmark(scores: BacktestScores | null): boolean {
  if (!scores) return false;
  const bench =
    scores.nifty_same_window_pct ?? scores.nifty_buy_hold_total_pct ?? null;
  return (
    scores.total_return_pct !== null &&
    scores.total_return_pct !== undefined &&
    bench !== null
  );
}

/**
 * Whether a kind+structure+scores combination has ANY headline viz to draw.
 * ExpressionCard uses this to gate the viz zone (and its Hairline).
 */
export function hasHeadlineViz(
  kind: ExpressionKind | null | undefined,
  structure: ExpressionStructure,
  scores: BacktestScores | null,
): boolean {
  switch (classifyKind(kind)) {
    case "option":
      return hasLegs(structure);
    case "basket":
      return hasWeights(structure);
    case "pair":
      return true;
    default:
      return hasWeights(structure) || hasBenchmark(scores);
  }
}

// ---------------------------------------------------------------------------
// Option leg derivation: structure.legs (unknown[]) + strikes + underlying
// ---------------------------------------------------------------------------

type RawLeg = {
  role?: unknown;
  side?: unknown;
  strike?: unknown;
  strike_offset?: unknown;
  qty_lots?: unknown;
};

function deriveOptionLegs(structure: ExpressionStructure): {
  legs: Leg[];
  underlyingRef: number | null;
  normalized: boolean;
} {
  const underlyingRef =
    typeof structure.underlying === "number" ? structure.underlying : null;

  const strikesArr: number[] = Array.isArray(structure.strikes)
    ? (structure.strikes as unknown[]).filter(
        (s): s is number => typeof s === "number" && Number.isFinite(s),
      )
    : [];

  const defaultQty =
    typeof structure.qty_lots === "number" ? structure.qty_lots : undefined;

  const rawLegs: RawLeg[] = Array.isArray(structure.legs)
    ? (structure.legs as RawLeg[])
    : [];

  const legs: Leg[] = [];
  let normalized = false;
  rawLegs.forEach((rl, i) => {
    const role = typeof rl.role === "string" ? rl.role : "call";
    const roleL = role.toLowerCase();
    const side: "buy" | "sell" =
      rl.side === "sell" || rl.side === "buy"
        ? rl.side
        : roleL.includes("short") || roleL.includes("sell")
          ? "sell"
          : "buy";

    let strike: number | null = null;
    if (typeof rl.strike === "number" && Number.isFinite(rl.strike)) {
      strike = rl.strike;
    } else if (typeof strikesArr[i] === "number") {
      strike = strikesArr[i]!;
    } else if (
      typeof rl.strike_offset === "number" &&
      underlyingRef !== null
    ) {
      strike = underlyingRef * (1 + rl.strike_offset);
    } else if (typeof rl.strike_offset === "number") {
      // No absolute underlying price (the underlying is an index NAME, not a
      // number) — draw the structural payoff on a spot-normalized axis where
      // spot = 100 and each strike sits at its % offset from spot.
      strike = 100 * (1 + rl.strike_offset);
      normalized = true;
    }

    if (strike === null || !Number.isFinite(strike) || strike <= 0) return;
    legs.push({
      role,
      side,
      strike,
      qtyLots: typeof rl.qty_lots === "number" ? rl.qty_lots : defaultQty,
    });
  });

  return { legs, underlyingRef: normalized ? 100 : underlyingRef, normalized };
}

// ---------------------------------------------------------------------------
// Pair long/short leg rows
// ---------------------------------------------------------------------------

function symbolOf(inst: string | ExpressionInstrument): string {
  return typeof inst === "string" ? inst : (inst.symbol ?? "");
}

function roleOf(inst: string | ExpressionInstrument): string {
  return typeof inst === "string" ? "" : (inst.role ?? "").toLowerCase();
}

function PairLegs({
  instruments,
}: {
  instruments: (string | ExpressionInstrument)[];
}): React.ReactElement {
  const longs = instruments
    .filter((i) => roleOf(i) === "long")
    .map(symbolOf)
    .filter(Boolean);
  const shorts = instruments
    .filter((i) => roleOf(i) === "short")
    .map(symbolOf)
    .filter(Boolean);

  // No explicit roles → show a flat instrument list so we never render blank.
  const haveRoles = longs.length > 0 || shorts.length > 0;
  const flat = instruments.map(symbolOf).filter(Boolean);

  const LegRow = ({
    side,
    color,
    syms,
  }: {
    side: string;
    color: string;
    syms: string[];
  }) => (
    <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
      <span
        style={{
          width: 56,
          flexShrink: 0,
          fontFamily: "var(--font-display)",
          fontSize: 13,
          fontWeight: 500,
          color,
        }}
      >
        {side}
      </span>
      <Num size="md" color="var(--text-primary)">
        {syms.length > 0 ? syms.join(" · ") : "—"}
      </Num>
    </div>
  );

  if (!haveRoles) {
    return (
      <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
        <LegRow side="Legs" color="var(--text-tertiary)" syms={flat} />
      </div>
    );
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
      <LegRow side="Long" color="var(--color-profit)" syms={longs} />
      <LegRow side="Short" color="var(--color-loss)" syms={shorts} />
    </div>
  );
}

// ---------------------------------------------------------------------------
// CompareBars — a local square Strategy-vs-benchmark leaf for the pair /
// fallback cases (the full-detail BenchmarkCompare with the tier toggle is the
// detail-page primary; here we only have a single expression's numbers). Two
// SQUARE horizontal bars + an excess line, all labels >= 13px.
// ---------------------------------------------------------------------------

function CompareBarRow({
  label,
  pct,
  fill,
  scale,
}: {
  label: string;
  pct: number;
  fill: string;
  scale: number;
}): React.ReactElement {
  const magnitude = scale > 0 ? Math.min(100, (Math.abs(pct) / scale) * 100) : 0;
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
      <span
        style={{
          width: 84,
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
      <div style={{ flex: 1, minWidth: 0 }}>
        <div
          style={{
            height: 20,
            width: `${magnitude}%`,
            minWidth: 2,
            background: fill,
            borderRadius: "var(--radius-sm)",
            transition: "width 320ms var(--ease-quartr)",
          }}
        />
      </div>
      <span style={{ width: 76, flexShrink: 0, textAlign: "right" }}>
        <Num size="lg" color={signColor(pct)}>
          {fmtPct(pct)}
        </Num>
      </span>
    </div>
  );
}

function CompareBars({
  strategyPct,
  benchmarkPct,
  excessPct,
  benchmarkLabel,
}: {
  strategyPct: number;
  benchmarkPct: number | null;
  excessPct?: number | null;
  benchmarkLabel: string;
}): React.ReactElement {
  const scale = Math.max(
    Math.abs(strategyPct),
    benchmarkPct != null ? Math.abs(benchmarkPct) : 0,
    1,
  );
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
      <CompareBarRow
        label="Strategy"
        pct={strategyPct}
        fill="var(--pivot-blue)"
        scale={scale}
      />
      {benchmarkPct != null && (
        <CompareBarRow
          label={benchmarkLabel}
          pct={benchmarkPct}
          fill="var(--text-tertiary)"
          scale={scale}
        />
      )}
      {excessPct != null && (
        <span
          style={{
            paddingLeft: 96,
            fontFamily: "var(--font-display)",
            fontSize: 13,
            color: "var(--text-tertiary)",
          }}
        >
          Excess vs {benchmarkLabel}{" "}
          <Num size="md" color={signColor(excessPct)}>
            {fmtPct(excessPct)}
          </Num>
        </span>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main dispatcher
// ---------------------------------------------------------------------------

export interface RiskReturnPanelProps {
  kind: ExpressionKind;
  structure: ExpressionStructure;
  scores: BacktestScores | null;
  instruments: (string | ExpressionInstrument)[];
  benchmarkLabel?: string;
}

export function RiskReturnPanel({
  kind,
  structure,
  scores,
  instruments,
  benchmarkLabel = "NIFTY",
}: RiskReturnPanelProps): React.ReactElement | null {
  const benchPct = scores
    ? (scores.nifty_same_window_pct ?? scores.nifty_buy_hold_total_pct ?? null)
    : null;

  switch (classifyKind(kind)) {
    case "option": {
      const { legs, underlyingRef, normalized } = deriveOptionLegs(structure);
      return (
        <PayoffDiagram
          legs={legs}
          underlyingRef={underlyingRef}
          label={
            normalized
              ? "Payoff at expiry · spot = 100 (normalized)"
              : undefined
          }
        />
      );
    }

    case "basket": {
      const weights = structure.weights ?? {};
      const cap =
        typeof structure.single_name_cap === "number"
          ? structure.single_name_cap
          : undefined;
      const purity =
        typeof structure.basket_purity === "number"
          ? structure.basket_purity
          : undefined;
      const subs =
        Array.isArray(scores?.sub_period_returns_pct) &&
        scores!.sub_period_returns_pct!.length > 0
          ? scores!.sub_period_returns_pct!
          : null;
      return (
        <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
          <AllocationDonut weights={weights} cap={cap} purity={purity} />
          {subs && (
            <ReturnDistribution
              values={subs}
              posFrac={scores?.sub_period_pos_frac}
            />
          )}
        </div>
      );
    }

    case "pair": {
      return (
        <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
          <PairLegs instruments={instruments} />
          {hasBenchmark(scores) && (
            <CompareBars
              strategyPct={scores!.total_return_pct!}
              benchmarkPct={benchPct}
              excessPct={scores!.excess_return_pct}
              benchmarkLabel={benchmarkLabel}
            />
          )}
        </div>
      );
    }

    default: {
      const weights = structure.weights ?? {};
      if (Object.keys(weights).length > 0) {
        const cap =
          typeof structure.single_name_cap === "number"
            ? structure.single_name_cap
            : undefined;
        const purity =
          typeof structure.basket_purity === "number"
            ? structure.basket_purity
            : undefined;
        return (
          <AllocationDonut weights={weights} cap={cap} purity={purity} />
        );
      }
      if (hasBenchmark(scores)) {
        return (
          <CompareBars
            strategyPct={scores!.total_return_pct!}
            benchmarkPct={benchPct}
            excessPct={scores!.excess_return_pct}
            benchmarkLabel={benchmarkLabel}
          />
        );
      }
      return null;
    }
  }
}
