import * as React from "react";

import { PivotMark } from "@/components/brand/PivotMark";

/**
 * PivotLogo — the full brand lockup: bar-chart mark + "Pivot" wordmark,
 * set like the ElevenLabs sidebar logo ("IIElevenLabs"): a heavy sans
 * wordmark whose capitals stand exactly as tall as the mark's bars, with
 * the "P" sitting directly adjacent to the final tall bar so the letters
 * read as a continuation of the chart.
 *
 * Alignment contract (why the numbers are what they are):
 *   • Inter's cap height is 0.727 em, and its caps sit optically centred
 *     in the em box — so with `line-height: 1` and flex `align-items:
 *     center`, bars sized to `0.727 × fontSize` share both the cap line
 *     and the baseline with the wordmark, no manual nudging.
 *   • The gap between the mark and the "P" equals the mark's own internal
 *     bar gap (72/576 of its height), so the "P" scans as the next bar
 *     in the series rather than a separate element.
 *
 * Paints with `currentColor` throughout — set `color` on a parent (or via
 * className) and both mark and wordmark follow, light or dark.
 */
export interface PivotLogoProps {
  /** Wordmark font size in px. The mark scales from it. Default 23. */
  fontSize?: number;
  className?: string;
  style?: React.CSSProperties;
  /** Accessible name for the mark. Default "Pivot". */
  title?: string;
}

/**
 * Mark height, in em. A touch over Inter's cap height (0.727 em): the bars
 * sit on the baseline and the tall final bar rises slightly proud of the
 * cap line, so the mark holds its own next to the bold wordmark instead of
 * reading undersized.
 */
const MARK_HEIGHT = 0.78;
/**
 * Bar width in mark glyph units (column pitch is 180). Heavier than the
 * source geometry's 108 so bar stems match the wordmark's stroke weight.
 */
const MARK_BAR_WIDTH = 132;
/**
 * Inter's baseline sits 0.848 em below the top of its `line-height: 1` box
 * (measured from rendered ink extents in-browser). A flex-centred mark of
 * height H em therefore needs a translateY of (0.848 − (1 + H) / 2) em to
 * plant the bar bottoms exactly on the wordmark's baseline.
 */
const BASELINE_OFFSET = 0.848;
/**
 * Mark→wordmark gap: the mark's internal bar gap (48/576 of its height)
 * minus the "P" glyph's left side bearing (~0.058 em at weight 700), so the
 * *ink* gap before the P equals the gap between bars and the P reads as
 * the next bar in the series.
 */
const WORDMARK_GAP = `${(MARK_HEIGHT * (48 / 576) - 0.058).toFixed(3)}em`;

export function PivotLogo({
  fontSize = 23,
  className,
  style,
  title = "Pivot",
}: PivotLogoProps): React.ReactElement {
  const markSize = fontSize * MARK_HEIGHT;
  const baselineNudge = `${(BASELINE_OFFSET - (1 + MARK_HEIGHT) / 2).toFixed(3)}em`;
  return (
    <span
      className={className}
      style={{
        display: "inline-flex",
        alignItems: "center",
        fontFamily: "var(--font-display)",
        fontWeight: 700,
        fontSize,
        lineHeight: 1,
        letterSpacing: "-0.035em",
        ...style,
      }}
    >
      <PivotMark
        size={markSize}
        barWidth={MARK_BAR_WIDTH}
        className="shrink-0"
        style={{ transform: `translateY(${baselineNudge})` }}
        title={title}
      />
      <span style={{ marginLeft: WORDMARK_GAP }}>Pivot</span>
    </span>
  );
}

export default PivotLogo;
