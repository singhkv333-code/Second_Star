"use client";

/**
 * Solvency and Value — the four classic scores, and the ratios each is made of.
 *
 * Two halves of one argument. On the left, four models sit in the four
 * quadrants of a single crosshair: solvency above value on one side, the
 * distress odds beside the return on the other. On the right, the inputs of
 * ONE of them at a time — point at a quadrant to see what that score is built
 * from, click to keep it there.
 *
 * The radar belongs to a score, not to the panel. A single shared radar could
 * only ever be one model's inputs wearing a neutral label: Altman's five
 * weighted ratios do not explain a Graham number, and Graham's own tests — a
 * P/E under 15, a P/B under 1.5, current assets twice current liabilities —
 * have nothing to do with a distress model. Selecting a score answers "and
 * what is that number made of", which is the question a single number always
 * provokes.
 *
 * The crosshair is two lines. Four cards with four borders was the alternative
 * and it is what every screener already does; a card per number turns a
 * related set into four unrelated ones, and the borders end up carrying more
 * ink than the numbers. The quadrant under the pointer takes a wash instead —
 * the shape it fills IS the quadrant, so the affordance costs no new geometry.
 * Held and hovered wear the same wash: the radar's caption already names the
 * score it is drawing, so the matrix does not need a second state to say it.
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
  // U+2212. A hyphen set at 38px sits too high and too short to read as a
  // sign — at this size the difference between -6.30 and −6.30 is the
  // difference between a typo and a negative number.
  const minus = (t: string) => t.replace(/^-/, "−");
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
    return gap >= 0
      ? `${gap.toFixed(0)}% above market price`
      : `${Math.abs(gap).toFixed(0)}% below market price`;
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
  const quadrants = data.quadrants;
  // Selection opens on the first score that actually computed. Defaulting to
  // index 0 would open on an empty frame for a company whose Altman inputs are
  // missing, which reads as a broken control rather than as missing data.
  const firstKey = (quadrants.find((q) => q.value !== null) ?? quadrants[0])?.key ?? "";
  const [selected, setSelected] = React.useState<string>(firstKey);
  const [hovered, setHovered] = React.useState<string | null>(null);

  // A new symbol is a new set of scores; keeping the old selection would point
  // at a key this company may not have at all.
  React.useEffect(() => {
    setSelected(firstKey);
    setHovered(null);
  }, [data.symbol, firstKey]);

  if (!data.available || quadrants.length < 4) return null;
  const [tl, tr, bl, br] = quadrants as [ScoreQuadrant, ScoreQuadrant, ScoreQuadrant, ScoreQuadrant];

  // Hover PREVIEWS, selection HOLDS. Pointing at a score should answer the
  // question immediately — a reader who has to click to find out what a number
  // is made of mostly does not — and clicking keeps that answer up while they
  // read the spokes.
  const activeKey = hovered ?? selected;
  const active = quadrants.find((q) => q.key === activeKey) ?? quadrants[0];

  const cell = (q: ScoreQuadrant, side: "left" | "right") => (
    <Cell
      q={q}
      price={price}
      side={side}
      state={q.key === selected ? "selected" : q.key === hovered ? "hovered" : "idle"}
      onSelect={() => setSelected(q.key)}
      onHover={(on) => setHovered(on ? q.key : null)}
    />
  );

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
      <style>{
        "@keyframes sv-radar-in{from{opacity:0;transform:translateY(5px)}to{opacity:1;transform:none}}"
      }</style>

      <PanelHead
        title="Solvency and Value"
        right={
          <span style={{
            fontFamily: "var(--font-ui)", fontSize: 12.5, fontWeight: 500,
            color: "var(--text-primary)", whiteSpace: "nowrap",
          }}>
            {data.period} · {data.basis === "standalone" ? "Standalone" : "Consolidated"}
          </span>
        }
      />

      <div className="grid grid-cols-1 lg:grid-cols-[1.35fr_1fr]" style={{ gap: 24, alignItems: "center" }}>
        <div style={{ position: "relative" }} onMouseLeave={() => setHovered(null)}>
          {/* The two axes. Absolute rather than borders on the cells, so the
              crosshair is exactly two lines that cross — put them on the cells
              and you get four L-shapes that only look like a crosshair where
              they happen to meet. */}
          <div aria-hidden style={{
            position: "absolute", left: "50%", top: 22, bottom: 22, width: 1,
            background: "var(--glass-border)",
          }} />
          <div aria-hidden style={{
            position: "absolute", top: "50%", left: 0, right: 0, height: 1,
            background: "var(--glass-border)",
          }} />

          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr" }}>
            <Caption text={tl.caption} side="left" />
            <Caption text={tr.caption} side="right" />

            {cell(tl, "left")}
            {cell(tr, "right")}
            {cell(bl, "left")}
            {cell(br, "right")}

            <Caption text={bl.caption} side="left" />
            <Caption text={br.caption} side="right" />
          </div>
        </div>

        {/* The caption sits IN the chart, not above it. The radar's top band is
            empty by construction — the first spoke label starts 60px down —
            and a line of text stacked above the frame only pushed the shape
            further from the numbers it explains. */}
        <div style={{ position: "relative" }}>
          <div style={{
            position: "absolute", top: 4, left: 0, right: 0, textAlign: "center",
            fontFamily: "var(--font-ui)", fontSize: 10.5, fontWeight: 650,
            letterSpacing: "0.09em", textTransform: "uppercase",
            color: "var(--pivot-blue)", pointerEvents: "none", zIndex: 1,
          }}>
            {active?.label ?? ""}
          </div>
          {/* Keyed on the active score so the shape re-enters rather than
              jumping: a polygon cannot tween between two different sets of
              spokes — Graham has six and Altman five — and a hard swap on
              hover reads as a glitch rather than as an answer. */}
          <div key={activeKey} style={{ width: "100%", animation: "sv-radar-in 180ms ease-out" }}>
            <Radar axes={active?.radar?.length ? active.radar : data.radar} />
          </div>
        </div>
      </div>
    </div>
  );
}

// ── the crosshair ──────────────────────────────────────────────────────────

type CellState = "idle" | "hovered" | "selected";

/** An axis caption: the name of the dimension, riding on the end of the axis
 *  its quadrant sits against. */
function Caption({ text, side }: { text: string; side: "left" | "right" }): React.ReactElement {
  return (
    <div style={{
      fontFamily: "var(--font-ui)", fontSize: 10.5, fontWeight: 650,
      letterSpacing: "0.09em", textTransform: "uppercase",
      color: "var(--text-primary)",
      paddingLeft: side === "right" ? 20 : 0,
      paddingRight: side === "left" ? 20 : 0,
      height: 22, lineHeight: "22px",
    }}>
      {text}
    </div>
  );
}

function Cell({
  q, price, side, state, onSelect, onHover,
}: {
  q: ScoreQuadrant; price: number | null; side: "left" | "right";
  state: CellState; onSelect: () => void; onHover: (on: boolean) => void;
}): React.ReactElement {
  const line = subline(q, price);
  // Three cases, in order. A model with a band is coloured by its band.
  // Graham has no band of its own — it is a number, and the reading is whether
  // the market is above or below it, so the comparison colours the line.
  // Anything else (DuPont's three legs, an unavailable reason) is not a
  // verdict at all and stays ink.
  let color = "var(--text-primary)";
  if (q.band) color = BAND_COLOR[q.band] ?? color;
  else if (q.key === "graham" && q.value !== null && price) {
    color = price > q.value ? "var(--color-warn)" : "var(--color-profit)";
  }

  const selected = state === "selected";   // ARIA only — see the wash below
  return (
    <div
      role="button"
      tabIndex={0}
      aria-pressed={selected}
      aria-label={`${q.label}: show what it is made of`}
      onClick={onSelect}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onSelect(); }
      }}
      onMouseEnter={() => onHover(true)}
      onFocus={() => onHover(true)}
      onBlur={() => onHover(false)}
      style={{
        position: "relative",
        // Vertical padding is the SAME on both rows. It used to be 4/14 on the
        // top row and 14/4 on the bottom so the content cleared the axis, and
        // that asymmetry went straight into the wash: two rectangles of
        // different heights, neither lining up with the other.
        padding: "14px 0",
        minWidth: 0,
        cursor: "pointer",
        outline: "none",
      }}
    >
      {/* The wash is its own layer, inset off the crosshair rather than
          bounded by the cell. Painted as the cell's background it ran up to
          both axes and covered the lines it was supposed to sit inside — the
          crosshair looked broken wherever the pointer was. */}
      <span
        aria-hidden
        style={{
          position: "absolute",
          top: 2, bottom: 2,
          left: side === "left" ? -10 : 8,
          right: side === "left" ? 8 : -10,
          borderRadius: 12,
          background: state === "idle" ? "transparent" : "var(--surface-hover)",
          transition: "background 140ms ease",
          pointerEvents: "none",
        }}
      />
      <div style={{
        position: "relative",
        paddingLeft: side === "right" ? 20 : 0,
        paddingRight: side === "left" ? 20 : 0,
      }}>
        <div style={{
          fontFamily: "var(--font-ui)", fontSize: 13, fontWeight: 600,
          color: "var(--text-primary)", letterSpacing: "-0.01em",
        }}>
          {q.label}
        </div>
        <div style={{
          fontFamily: "var(--font-ui)", fontSize: "clamp(27px, 2.7vw, 36px)", fontWeight: 600,
          letterSpacing: "-0.03em", lineHeight: 1.1, marginTop: 4,
          color: "var(--text-primary)", fontVariantNumeric: "tabular-nums",
        }}>
          {formatValue(q)}
        </div>
        {line ? (
          <div style={{
            fontFamily: "var(--font-ui)", fontSize: 13.5, fontWeight: 500,
            marginTop: 6, color, letterSpacing: "-0.005em",
          }}>
            {line}
          </div>
        ) : null}
      </div>
    </div>
  );
}

// ── the radar ──────────────────────────────────────────────────────────────
// Hand-drawn SVG rather than a chart library: a handful of points and two rings
// do not need one, and the labels have to sit outside the shape at a readable
// size — which is the part every default radar gets wrong.

const R = 78;           // polygon radius at 100
const PAD = 92;         // room for the labels outside it. Sized for the widest
                        // thing a spoke can carry, which is not a label but a
                        // value: Graham's six spokes put "7.96x P/B" out at
                        // 30 degrees, where a pad cut for "Efficiency" clips it.
const SIZE = (R + PAD) * 2;
const C = SIZE / 2;

function point(i: number, n: number, r: number): [number, number] {
  const a = -Math.PI / 2 + (i * 2 * Math.PI) / n;
  return [C + Math.cos(a) * r, C + Math.sin(a) * r];
}

function ring(axes: ScoreAxis[], f: number): string {
  return axes
    .map((_, i) => point(i, axes.length, R * f))
    .map(([x, y]) => `${x.toFixed(1)},${y.toFixed(1)}`)
    .join(" ");
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
      <svg viewBox={`0 0 ${SIZE} ${SIZE}`} width="100%" style={{ maxWidth: SIZE + 40 }} role="img"
           aria-label="Ratio profile">
        {/* Two rings, not five. The rings are a sense of scale, not a measuring
            grid — the number is printed at the end of every spoke, so a reader
            who wants the value reads it rather than counting rings. */}
        {[0.5, 1].map((f) => (
          <polygon key={f} points={ring(axes, f)} fill="none"
                   stroke="var(--glass-border)" strokeWidth={1} />
        ))}
        {axes.map((a, i) => {
          const [x, y] = point(i, n, R);
          return <line key={a.key} x1={C} y1={C} x2={x} y2={y}
                       stroke="var(--glass-border)" strokeWidth={1} />;
        })}

        <polygon points={poly} fill="var(--accent-wash)" stroke="var(--pivot-blue)"
                 strokeWidth={2} strokeLinejoin="round" />
        {axes.map((a, i) => {
          const [x, y] = point(i, n, (Math.max(a.scaled ?? 0, 0) / 100) * R);
          return <circle key={a.key} cx={x} cy={y} r={3} fill="var(--pivot-blue)" />;
        })}

        {/* Labels at the ends of the spokes: the ratio's name, and under it the
            ratio itself at a size worth reading. Anchored by which side of the
            circle the spoke points to, so nothing overlaps the shape. The
            definition rides in a title, where it costs no ink. */}
        {axes.map((a, i) => {
          const [x, y] = point(i, n, R + 23);
          const cos = Math.cos(-Math.PI / 2 + (i * 2 * Math.PI) / n);
          const anchor = cos > 0.3 ? "start" : cos < -0.3 ? "end" : "middle";
          return (
            <g key={a.key}>
              <title>{a.detail}</title>
              <text x={x} y={y - 4} textAnchor={anchor} style={{
                fontFamily: "var(--font-ui)", fontSize: 12, fontWeight: 500,
                fill: "var(--text-primary)", letterSpacing: "0.01em",
              }}>
                {a.label}
              </text>
              <text x={x} y={y + 15} textAnchor={anchor} style={{
                fontFamily: "var(--font-ui)", fontSize: 16, fontWeight: 600,
                fill: "var(--text-primary)", letterSpacing: "-0.02em",
              }}>
                {a.display}
              </text>
            </g>
          );
        })}
      </svg>
    </div>
  );
}
