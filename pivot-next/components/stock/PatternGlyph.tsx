"use client";

/**
 * A drawn sketch of each pattern, beside its name.
 *
 * There is no open-source SVG set for NAMED patterns — searching turns up
 * generic "candlestick chart" icons and nothing that distinguishes a bullish
 * harami from a bearish one. So these are drawn from canonical OHLC written
 * out below rather than sourced: it costs one table of numbers, and it buys
 * glyphs that share the page's own profit/loss colours, follow the theme, and
 * scale without a raster.
 *
 * The numbers are textbook shapes, not samples of real bars. A glyph's job is
 * to let someone who half-remembers the name recognise it — so the defining
 * feature of each pattern is exaggerated, and everything else is kept plain.
 *
 * Prices run 0–100 with 100 at the TOP, which is the way a chart reads and the
 * opposite of how SVG counts, so every y is flipped once at the point of use.
 */

import * as React from "react";

/** [open, high, low, close] on a 0–100 price scale. */
type Candle = [number, number, number, number];

const CANDLES: Record<string, Candle[]> = {
  // ── single ────────────────────────────────────────────────────────────
  doji:              [[50, 74, 26, 50]],
  long_legged_doji:  [[50, 88, 12, 50]],
  dragonfly_doji:    [[72, 76, 22, 72]],
  gravestone_doji:   [[28, 78, 24, 28]],
  spinning_top:      [[44, 78, 22, 56]],
  marubozu:          [[20, 80, 20, 80]],
  hammer:            [[62, 74, 22, 70]],
  hanging_man:       [[70, 74, 22, 62]],
  inverted_hammer:   [[32, 80, 26, 42]],
  shooting_star:     [[42, 80, 34, 32]],
  bullish_belt_hold: [[22, 74, 22, 68]],
  bearish_belt_hold: [[78, 78, 26, 32]],

  // ── two ───────────────────────────────────────────────────────────────
  bullish_engulfing: [[60, 66, 50, 52], [46, 76, 42, 72]],
  bearish_engulfing: [[40, 50, 34, 48], [54, 58, 24, 28]],
  bullish_harami:    [[74, 78, 32, 36], [46, 60, 42, 58]],
  bearish_harami:    [[32, 78, 28, 74], [58, 62, 46, 50]],
  bullish_kicker:    [[58, 62, 42, 44], [56, 82, 54, 78]],
  bearish_kicker:    [[42, 58, 38, 56], [44, 46, 18, 22]],
  dark_cloud_cover:  [[28, 66, 24, 62], [72, 76, 38, 42]],
  piercing_line:     [[72, 76, 34, 38], [28, 66, 24, 62]],
  tweezer_top:       [[38, 74, 34, 68], [68, 74, 38, 42]],
  tweezer_bottom:    [[62, 66, 26, 32], [32, 62, 26, 58]],

  // ── three ─────────────────────────────────────────────────────────────
  morning_star:            [[72, 76, 44, 46], [36, 42, 30, 40], [44, 76, 40, 72]],
  evening_star:            [[28, 56, 24, 54], [62, 72, 58, 66], [56, 60, 24, 28]],
  bullish_abandoned_baby:  [[68, 72, 48, 50], [32, 38, 26, 32], [44, 78, 42, 74]],
  bearish_abandoned_baby:  [[32, 54, 28, 52], [70, 76, 64, 70], [58, 60, 26, 30]],
  three_white_soldiers:    [[24, 44, 20, 42], [38, 60, 34, 58], [54, 78, 50, 76]],
  three_black_crows:       [[76, 80, 56, 58], [62, 66, 40, 42], [46, 50, 22, 24]],
  three_inside_up:         [[74, 78, 36, 40], [48, 60, 44, 58], [54, 82, 50, 78]],
  three_inside_down:       [[26, 64, 22, 60], [52, 56, 40, 44], [46, 50, 18, 22]],
  three_outside_up:        [[58, 62, 46, 48], [44, 72, 40, 68], [70, 86, 66, 82]],
  three_outside_down:      [[42, 54, 38, 52], [56, 60, 28, 32], [30, 34, 14, 18]],

  // ── five ──────────────────────────────────────────────────────────────
  rising_three_methods: [
    [22, 62, 20, 60], [56, 58, 46, 48], [52, 54, 42, 44], [48, 50, 38, 40], [44, 84, 42, 80],
  ],
  falling_three_methods: [
    [78, 80, 38, 40], [44, 54, 42, 52], [48, 58, 46, 56], [52, 62, 50, 60], [56, 58, 16, 20],
  ],
};

/** Shape patterns as a price path on the same 0–100 scale. */
const PATHS: Record<string, [number, number][]> = {
  head_and_shoulders: [
    [0, 26], [12, 56], [22, 42], [34, 60], [50, 84], [66, 60], [78, 44], [88, 58], [100, 26],
  ],
  inverse_head_and_shoulders: [
    [0, 74], [12, 44], [22, 58], [34, 40], [50, 16], [66, 40], [78, 56], [88, 42], [100, 74],
  ],
  double_top: [
    [0, 22], [22, 74], [40, 46], [60, 74], [80, 40], [100, 24],
  ],
  double_bottom: [
    [0, 78], [22, 26], [40, 54], [60, 26], [80, 60], [100, 76],
  ],
  triple_top: [
    [0, 22], [14, 72], [28, 48], [46, 72], [60, 48], [76, 72], [90, 44], [100, 32],
  ],
  triple_bottom: [
    [0, 78], [14, 28], [28, 52], [46, 28], [60, 52], [76, 28], [90, 56], [100, 68],
  ],
  bull_flag: [
    [0, 16], [26, 76], [40, 62], [52, 70], [64, 56], [76, 64], [86, 52], [100, 92],
  ],
  bear_flag: [
    [0, 84], [26, 24], [40, 38], [52, 30], [64, 44], [76, 36], [86, 48], [100, 8],
  ],
  bull_pennant: [
    [0, 16], [26, 78], [42, 46], [56, 70], [68, 54], [78, 64], [86, 58], [100, 92],
  ],
  bear_pennant: [
    [0, 84], [26, 22], [42, 54], [56, 30], [68, 46], [78, 36], [86, 42], [100, 8],
  ],
};

const UP = "var(--color-profit)";
const DOWN = "var(--color-loss)";

/** Shape patterns that resolve DOWN. Everything else in `PATHS` resolves up. */
const BEARISH = new Set([
  "head_and_shoulders", "double_top", "triple_top", "bear_flag", "bear_pennant",
]);

export function hasGlyph(kind: string): boolean {
  return kind in CANDLES || kind in PATHS;
}

export function PatternGlyph({
  kind,
  width = 46,
  height = 26,
}: {
  kind: string;
  width?: number;
  height?: number;
}): React.ReactElement {
  const candles = CANDLES[kind];
  const path = PATHS[kind];

  // Price 0–100 → pixel y, flipped. 1.5px of air top and bottom keeps a wick
  // that reaches 0 or 100 from being clipped by the viewBox edge.
  const y = (v: number): number => height - 1.5 - (v / 100) * (height - 3);

  if (candles) {
    const slot = width / candles.length;
    const bodyW = Math.max(2.5, Math.min(6, slot * 0.52));
    return (
      <svg width={width} height={height} viewBox={`0 0 ${width} ${height}`} role="img" aria-hidden="true" style={{ display: "block", flexShrink: 0 }}>
        {candles.map(([o, h, l, c], i) => {
          const cx = slot * (i + 0.5);
          const up = c >= o;
          const tone = up ? UP : DOWN;
          const top = y(Math.max(o, c));
          const bot = y(Math.min(o, c));
          // A doji has no body at all; without a floor it renders as nothing.
          const bh = Math.max(1.2, bot - top);
          return (
            <g key={i}>
              <line x1={cx} x2={cx} y1={y(h)} y2={y(l)} stroke={tone} strokeWidth={1} />
              <rect
                x={cx - bodyW / 2}
                y={top}
                width={bodyW}
                height={bh}
                fill={up ? "none" : tone}
                stroke={tone}
                strokeWidth={1}
                rx={0.5}
              />
            </g>
          );
        })}
      </svg>
    );
  }

  if (path) {
    // Colour comes from what the pattern MEANS, not from where its path
    // happens to end. Head and shoulders returns to its own neckline, so
    // "ends higher than it started" painted a textbook bearish reversal green;
    // a double bottom ends slightly below its first peak and came out red.
    const tone = BEARISH.has(kind) ? DOWN : UP;
    const d = path
      .map(([px, pv], i) => `${i ? "L" : "M"}${((px / 100) * (width - 2) + 1).toFixed(1)},${y(pv).toFixed(1)}`)
      .join(" ");
    return (
      <svg width={width} height={height} viewBox={`0 0 ${width} ${height}`} role="img" aria-hidden="true" style={{ display: "block", flexShrink: 0 }}>
        <path d={d} fill="none" stroke={tone} strokeWidth={1.3} strokeLinejoin="round" strokeLinecap="round" />
      </svg>
    );
  }

  // An unmapped pattern reserves the same width so the column does not jog.
  return <span style={{ display: "block", width, height, flexShrink: 0 }} />;
}
