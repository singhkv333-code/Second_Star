"use client";

/**
 * Solvency and Value — the four classic scores, and the ratios they are made of.
 *
 * Two halves of one argument. On the left, four models sit in the four
 * quadrants of a single crosshair: solvency above value on one side, the
 * distress odds beside the return on the other. On the right, the five ratios
 * those models are built from, drawn as one closed shape — because the scores
 * are summaries and the shape is the reason they came out the way they did.
 *
 * The crosshair is two lines. Four cards with four borders was the alternative
 * and it is what every screener already does; a card per number turns a
 * related set into four unrelated ones, and the borders end up carrying more
 * ink than the numbers. Here the only rules on the panel are the two axes that
 * mean something, and the captions ride on their ends.
 *
 * Colour is carried by the verdict, never by the number. A green 10.92 and a
 * red 2.31 read as a scoreboard; the number is the fact and the verdict is the
 * reading of it, so the ink stays ink and the one coloured word does the work.
 */

import * as React from "react";

import type { CompanyScores, ScoreAxis, ScoreQuadrant } from "@/lib/api";
import { PanelHead } from "./chrome";

const BAND_COLOR: Record<string, string> = {
  good: "var(--color-profit)",
  watch: "var(--color-warn)",
  risk: "var(--color-loss)",
};

/** ₹ with Indian grouping and no decimals — a fair-value number is never
 *  precise to the paisa, and two decimals on a four-digit figure only makes
 *  the digit that matters smaller. */
function rupees(v: number): string {
  return `₹${Math.round(v).toLocaleString("en-IN")}`;
}

function formatValue(q: ScoreQuadrant): string {
  if (q.value === null) return "—";
  if (q.format === "rupees") return rupees(q.value);
  // U+2212. A hyphen set at 46px sits too high and too short to read as a
  // sign — at this size the difference between -4.67 and −4.67 is the
  // difference between a typo and a negative number.
  const minus = (t: string) => t.replace(/^-/, "\u2212");
  if (q.format === "pct") return minus(`${q.value.toFixed(1)}%`);
  return minus(q.value.toFixed(2));
}

/** The one line under each number. Every model says something different, so
 *  none of them says "N/A" or repeats its own name: Graham is only meaningful
 *  against the live price, Ohlson's log-odds are only meaningful as odds, and
 *  DuPont is only meaningful as its three legs multiplied out. */
function subline(q: ScoreQuadrant, price: number | null): string | null {
  if (q.value === null) return q.unavailable_reason;
  if (q.key === "graham" && price) {
    const gap = (price - q.value) / q.value * 100;
    return gap >= 0 ? `${gap.toFixed(0)}% above market price` : `${Math.abs(gap).toFixed(0)}% below market price`;
  }
  if (q.key === "ohlson" && q.probability_pct !== undefined) {
    return `${q.probability_pct.toFixed(q.probability_pct < 1 ? 2 : 1)}% distress odds`;
  }
  if (q.key === "dupont" && q.margin_pct !== undefined) {
    return `${q.margin_pct.toFixed(1)}% × ${q.asset_turnover?.toFixed(2)} × ${q.equity_multiplier?.toFixed(2)}`;
  }
  return q.verdict ?? null;
}

export function SolvencyValuePanel({
  data,
  price,
}: {
  data: CompanyScores;
  price: number | null;
}): React.ReactElement | null {
  if (!data.available || data.quadrants.length < 4) return null;
  const [tl, tr, bl, br] = data.quadrants as [ScoreQuadrant, ScoreQuadrant, ScoreQuadrant, ScoreQuadrant];

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 18 }}>
      <PanelHead
        title="Solvency and Value"
        right={
          <span style={{
            fontFamily: "var(--font-ui)", fontSize: 13, fontWeight: 500,
            color: "var(--text-primary)", whiteSpace: "nowrap",
          }}>
            {data.period} · {data.basis === "standalone" ? "Standalone" : "Consolidated"}
          </span>
        }
      />

      {/* Matrix and radar are one row on a laptop and stack on a phone. The
          radar is given the narrower column: it is a shape, and a shape reads
          at any size, while four numbers and their verdicts need the width. */}
      <div className="grid grid-cols-1 lg:grid-cols-[1.3fr_1fr]" style={{ gap: 36, alignItems: "center" }}>
        <Matrix tl={tl} tr={tr} bl={bl} br={br} price={price} />
        <Radar axes={data.radar} />
      </div>
    </div>
  );
}

// ── the crosshair ──────────────────────────────────────────────────────────

function Matrix({
  tl, tr, bl, br, price,
}: {
  tl: ScoreQuadrant; tr: ScoreQuadrant; bl: ScoreQuadrant; br: ScoreQuadrant;
  price: number | null;
}): React.ReactElement {
  return (
    <div style={{ position: "relative" }}>
      {/* The two axes. Absolute rather than borders on the cells, so the
          crosshair is exactly two lines that cross — put them on the cells and
          you get four L-shapes that only look like a crosshair where they
          happen to meet. */}
      <div aria-hidden style={{
        position: "absolute", left: "50%", top: 26, bottom: 26, width: 1,
        background: "var(--glass-border)",
      }} />
      <div aria-hidden style={{
        position: "absolute", top: "50%", left: 0, right: 0, height: 1,
        background: "var(--glass-border)",
      }} />

      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr" }}>
        <Caption text={tl.caption} side="left" />
        <Caption text={tr.caption} side="right" />

        <Cell q={tl} price={price} side="left" row="top" />
        <Cell q={tr} price={price} side="right" row="top" />
        <Cell q={bl} price={price} side="left" row="bottom" />
        <Cell q={br} price={price} side="right" row="bottom" />

        <Caption text={bl.caption} side="left" />
        <Caption text={br.caption} side="right" />
      </div>
    </div>
  );
}

/** An axis caption: the name of the dimension, riding on the end of the axis
 *  its quadrant sits against. */
function Caption({ text, side }: { text: string; side: "left" | "right" }): React.ReactElement {
  return (
    <div style={{
      fontFamily: "var(--font-ui)", fontSize: 11, fontWeight: 650,
      letterSpacing: "0.09em", textTransform: "uppercase",
      color: "var(--text-primary)",
      padding: side === "left"
        ? "0 clamp(14px, 2.4vw, 34px) 0 0"
        : "0 0 0 clamp(14px, 2.4vw, 34px)",
      height: 26, lineHeight: "26px",
    }}>
      {text}
    </div>
  );
}

function Cell({
  q, price, side, row,
}: {
  q: ScoreQuadrant; price: number | null; side: "left" | "right"; row: "top" | "bottom";
}): React.ReactElement {
  const line = subline(q, price);
  // Three cases, in order. A model with a band is coloured by its band.
  // Graham has no band of its own — it is a number, and the reading is
  // whether the market is above or below it, so the comparison colours the
  // line. Anything else (DuPont's three legs, an unavailable reason) is not a
  // verdict at all and stays ink.
  let color = "var(--text-primary)";
  if (q.band) color = BAND_COLOR[q.band] ?? color;
  else if (q.key === "graham" && q.value !== null && price) {
    color = price > q.value ? "var(--color-warn)" : "var(--color-profit)";
  }
  return (
    <div style={{
      // The padding, not a border, is what separates the quadrants: each cell
      // is pushed off the axis it touches and left flush with the outer edge,
      // which is how the crosshair stays legible with nothing drawn around it.
      padding: side === "left"
        ? "22px clamp(14px, 2.4vw, 34px) 22px 0"
        : "22px 0 22px clamp(14px, 2.4vw, 34px)",
      paddingTop: row === "top" ? 6 : 26,
      paddingBottom: row === "top" ? 26 : 6,
      minWidth: 0,
    }}>
      <div style={{
        fontFamily: "var(--font-ui)", fontSize: 13.5, fontWeight: 600,
        color: "var(--text-primary)", letterSpacing: "-0.01em",
      }}>
        {q.label}
      </div>
      <div style={{
        fontFamily: "var(--font-ui)", fontSize: "clamp(31px, 3.6vw, 46px)", fontWeight: 600,
        letterSpacing: "-0.03em", lineHeight: 1.08, marginTop: 6,
        color: "var(--text-primary)", fontVariantNumeric: "tabular-nums",
      }}>
        {formatValue(q)}
      </div>
      {line ? (
        <div style={{
          fontFamily: "var(--font-ui)", fontSize: 14, fontWeight: 500,
          marginTop: 8, color, letterSpacing: "-0.005em",
        }}>
          {line}
        </div>
      ) : null}
    </div>
  );
}

// ── the radar ──────────────────────────────────────────────────────────────
// Hand-drawn SVG rather than a chart library: five points and two rings do not
// need one, and the labels have to sit outside the shape at a readable size —
// which is the part every default radar gets wrong.

const R = 108;          // polygon radius at 100
const PAD = 96;         // room for the labels outside it — sized for the
                        // longest one ('Profitability'), not for the numbers
const SIZE = (R + PAD) * 2;
const C = SIZE / 2;

function point(i: number, n: number, r: number): [number, number] {
  const a = -Math.PI / 2 + (i * 2 * Math.PI) / n;
  return [C + Math.cos(a) * r, C + Math.sin(a) * r];
}

function Radar({ axes }: { axes: ScoreAxis[] }): React.ReactElement | null {
  const n = axes.length;
  if (!n) return null;

  const poly = axes
    .map((a, i) => point(i, n, (Math.max(a.scaled ?? 0, 0) / 100) * R))
    .map(([x, y]) => `${x.toFixed(1)},${y.toFixed(1)}`)
    .join(" ");

  return (
    <div style={{ display: "flex", justifyContent: "center" }}>
      <svg viewBox={`0 0 ${SIZE} ${SIZE}`} width="100%" style={{ maxWidth: SIZE + 60 }} role="img"
           aria-label="Ratio profile">
        {/* Two rings, not five. The rings are a sense of scale, not a
            measuring grid — the numbers are printed at the ends of the spokes,
            so a reader who wants the value reads it rather than counting
            rings. */}
        {[0.5, 1].map((f) => (
          <polygon
            key={f}
            points={axes.map((_, i) => point(i, n, R * f)).map(([x, y]) => `${x.toFixed(1)},${y.toFixed(1)}`).join(" ")}
            fill="none"
            stroke="var(--glass-border)"
            strokeWidth={1}
          />
        ))}
        {axes.map((_, i) => {
          const [x, y] = point(i, n, R);
          return <line key={i} x1={C} y1={C} x2={x} y2={y} stroke="var(--glass-border)" strokeWidth={1} />;
        })}

        <polygon points={poly} fill="var(--accent-wash)" stroke="var(--pivot-blue)" strokeWidth={2}
                 strokeLinejoin="round" />
        {axes.map((a, i) => {
          const [x, y] = point(i, n, (Math.max(a.scaled ?? 0, 0) / 100) * R);
          return <circle key={a.key} cx={x} cy={y} r={3.5} fill="var(--pivot-blue)" />;
        })}

        {/* Labels at the ends of the spokes: the ratio's name, and under it
            the ratio itself at a size worth reading. Anchored by which side of
            the circle the spoke points to, so nothing overlaps the shape. */}
        {axes.map((a, i) => {
          const [x, y] = point(i, n, R + 26);
          const cos = Math.cos(-Math.PI / 2 + (i * 2 * Math.PI) / n);
          const anchor = cos > 0.3 ? "start" : cos < -0.3 ? "end" : "middle";
          return (
            <g key={a.key}>
              <text
                x={x} y={y - 4} textAnchor={anchor}
                style={{
                  fontFamily: "var(--font-ui)", fontSize: 13, fontWeight: 500,
                  fill: "var(--text-primary)", letterSpacing: "0.01em",
                }}
              >
                {a.label}
              </text>
              <text
                x={x} y={y + 17} textAnchor={anchor}
                style={{
                  fontFamily: "var(--font-ui)", fontSize: 19, fontWeight: 600,
                  fill: "var(--text-primary)", letterSpacing: "-0.02em",
                }}
              >
                {a.display}
              </text>
            </g>
          );
        })}
      </svg>
    </div>
  );
}
