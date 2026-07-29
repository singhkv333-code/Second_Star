"use client";

/**
 * AllocationPie — the basket-weights donut for a View expression. One slice per
 * holding; a clear LONG / SHORT legend (colour + label) so it is obvious what
 * position is held in each security:
 *   • basket  → all long, slices sized by real weight_pct
 *   • pair    → long names + a distinct red short-Nifty slice
 *   • option  → the long / short legs of the structure
 *
 * Long exposure is drawn in a calm blue opacity ramp; short exposure in the
 * loss red, so a short leg is unmistakable. The centre label is the strategy
 * name (e.g. "Bull call spread") or a plain "N holdings" / "N long · M short".
 *
 * DESIGN LAW: rounded, border-only empty state, every label >= 13px, tabular
 * numerals, light + dark via useTokenColors. We NEVER print a fabricated
 * weight: a "%" appears only when the API actually gave a weight_pct.
 */

import {
  Cell,
  Pie,
  PieChart,
  ResponsiveContainer,
} from "recharts";
import { useTokenColors } from "../use-token-color";

/** One slice of the allocation, as served on an expression's `holdings`. */
export type PieHolding = {
  name: string;
  symbol: string;
  position?: "long" | "short" | string | null;
  weight_pct?: number | null;
  return_pct?: number | null;
};

/** Calm blue opacity ramp for long slices (most → least weight). */
const LONG_RAMP = [1, 0.82, 0.66, 0.53, 0.42, 0.33, 0.26, 0.2];

function isShort(p?: string | null): boolean {
  return typeof p === "string" && p.toLowerCase() === "short";
}

/** Friendlier, jargon-free leg names: "Buy CE (atm)" → "Buy call (ATM)",
 *  "Sell CE (delta 0.25)" → "Sell call (OTM)". Plain stock names pass through. */
function legName(name: string): string {
  return name
    .replace(/\bCE\b/g, "call")
    .replace(/\bPE\b/g, "put")
    .replace(/\(\s*atm\s*\)/i, "(ATM)")
    .replace(/\(\s*delta[^)]*\)/i, "(OTM)");
}

export function AllocationPie({
  holdings,
  strategyName,
  height = 172,
}: {
  holdings?: PieHolding[] | null;
  strategyName?: string | null;
  height?: number;
}): React.ReactElement {
  const c = useTokenColors({
    blue: "--pivot-blue",
    loss: "--color-loss",
    ink: "--text-primary",
    secondary: "--text-secondary",
    tertiary: "--text-tertiary",
    border: "--glass-border",
    bgBase: "--bg-base",
  });

  const safe = (Array.isArray(holdings) ? holdings : []).filter(
    (h) => h && (h.name || h.symbol),
  );

  if (safe.length === 0) {
    return (
      <div
        style={{
          height,
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          borderRadius: "var(--radius-lg)",
          border: `1px solid ${c.border}`,
          background: c.bgBase,
          fontFamily: "var(--font-display)",
          fontSize: 13,
          color: c.tertiary,
        }}
      >
        Allocation not available yet
      </div>
    );
  }

  // Slice angles: real weights when present, otherwise equal notional legs.
  const hasWeights = safe.every(
    (h) => typeof h.weight_pct === "number" && (h.weight_pct as number) > 0,
  );

  let longIdx = -1;
  const data = safe.map((h) => {
    const short = isShort(h.position);
    if (!short) longIdx += 1;
    return {
      key: `${h.symbol || h.name}-${h.position ?? "long"}`,
      name: h.name || h.symbol,
      symbol: h.symbol,
      short,
      weight: hasWeights ? (h.weight_pct as number) : 1,
      hasRealWeight: typeof h.weight_pct === "number" && h.weight_pct > 0,
      realWeight: h.weight_pct ?? null,
      fill: short ? c.loss : c.blue,
      opacity: short ? 1 : LONG_RAMP[Math.min(Math.max(longIdx, 0), LONG_RAMP.length - 1)],
    };
  });

  const nLong = data.filter((d) => !d.short).length;
  const nShort = data.filter((d) => d.short).length;

  const centerTop =
    strategyName ||
    (nShort > 0 ? `${nLong} long · ${nShort} short` : `${nLong} holdings`);

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 18 }}>
        {/* Donut + centre label */}
        <div
          style={{
            position: "relative",
            width: height,
            height,
            flexShrink: 0,
          }}
        >
          <ResponsiveContainer width="100%" height="100%">
            <PieChart>
              <Pie
                data={data}
                dataKey="weight"
                nameKey="name"
                innerRadius={height * 0.33}
                outerRadius={height * 0.47}
                paddingAngle={2}
                strokeWidth={0}
                isAnimationActive={false}
              >
                {data.map((d) => (
                  <Cell key={d.key} fill={d.fill} fillOpacity={d.opacity} />
                ))}
              </Pie>
            </PieChart>
          </ResponsiveContainer>
          <div
            style={{
              position: "absolute",
              inset: 0,
              display: "flex",
              flexDirection: "column",
              alignItems: "center",
              justifyContent: "center",
              padding: "0 14px",
              textAlign: "center",
              pointerEvents: "none",
            }}
          >
            <span
              style={{
                fontFamily: "var(--font-display)",
                fontSize: 14,
                fontWeight: 600,
                lineHeight: 1.25,
                color: c.ink,
                letterSpacing: "-0.01em",
              }}
            >
              {centerTop}
            </span>
          </div>
        </div>

        {/* Legend — name + LONG/SHORT tag + (real) weight */}
        <div
          style={{
            display: "flex",
            flexDirection: "column",
            gap: 8,
            minWidth: 0,
            flex: 1,
          }}
        >
          {data.map((d) => (
            <div
              key={d.key}
              style={{ display: "flex", alignItems: "center", gap: 9 }}
            >
              <span
                aria-hidden
                style={{
                  width: 11,
                  height: 11,
                  borderRadius: "var(--radius-xs)",
                  flexShrink: 0,
                  background: d.fill,
                  opacity: d.opacity,
                }}
              />
              <span
                title={`${d.name} (${d.symbol})`}
                style={{
                  fontFamily: "var(--font-display)",
                  fontSize: 13,
                  fontWeight: 500,
                  color: c.ink,
                  lineHeight: 1.3,
                  minWidth: 0,
                  flexShrink: 1,
                  whiteSpace: "nowrap",
                  overflow: "hidden",
                  textOverflow: "ellipsis",
                }}
              >
                {legName(d.name)}
              </span>
              {/* Only tag positions when the basket actually MIXES long & short —
                  on an all-long basket the repeated "LONG" is noise and it crowds
                  out the names. When there is a short leg, tag every row so the
                  short is unmistakable. */}
              {nShort > 0 && (
                <span
                  style={{
                    fontFamily: "var(--font-display)",
                    fontSize: 13,
                    fontWeight: 600,
                    letterSpacing: "0.04em",
                    color: d.short ? c.loss : c.tertiary,
                    flexShrink: 0,
                  }}
                >
                  {d.short ? "SHORT" : "LONG"}
                </span>
              )}
              <span
                style={{
                  marginLeft: "auto",
                  fontFamily: "var(--font-display)",
                  fontVariantNumeric: "tabular-nums",
                  fontSize: 13,
                  fontWeight: 600,
                  color: c.secondary,
                  flexShrink: 0,
                }}
              >
                {d.hasRealWeight ? `${Math.round(d.realWeight as number)}%` : ""}
              </span>
            </div>
          ))}
        </div>
      </div>

      {!hasWeights && (
        <span
          style={{
            fontFamily: "var(--font-display)",
            fontSize: 13,
            lineHeight: 1.5,
            color: c.tertiary,
          }}
        >
          Legs shown at equal size — this structure isn&rsquo;t weighted by a
          fixed percentage.
        </span>
      )}
    </div>
  );
}

export default AllocationPie;
