import * as React from "react";

/**
 * PivotLockup — the circular disc mark beside a serif "Pivot", which is the
 * brand as the chart app draws it (`.pivot-lockup` in charto/preview) and as
 * the browser tab, the sign-in wall and the favicon all already show it.
 *
 * This exists because the company page was wearing a DIFFERENT lockup — the
 * bar-chart mark and an Inter wordmark of `PivotLogo` — so a reader crossing
 * from the chart to a company page met a second logo for the same product.
 * One mark, or it is not a mark.
 *
 * Measurements are the chart app's own, kept in the same relative units so
 * the two render identically at any size:
 *   • mark 0.86em, so it sits between the cap line and baseline of the serif
 *   • gap 0.28em
 *   • letter-spacing -0.03em, weight 550 (--weight-display), line-height 1
 *
 * Everything paints in `currentColor` except the full stop, which carries the
 * accent — set `color` on a parent and the lockup follows into either theme.
 */

/**
 * The disc alone. A filled circle with two cuts taken out of it: a narrow
 * vertical slot near the right edge, and a diagonal slash from top-right to
 * bottom-left, leaving a wedge and a sliver.
 *
 * The SAME geometry as app/icon.svg (the favicon) and the mask the chart app
 * applies to assets/pivot-mark.png. Drawn rather than masked from that PNG
 * because this one is asked for at 15px inside the ask bar's pill and at 20px
 * in the header, and a raster has one size.
 *
 * The mask needs an id unique per instance — two of these on one page sharing
 * an id would both resolve to whichever mounted first, which is a full disc
 * with no cuts. useId, so it survives hydration matching too.
 */
export function PivotDisc({
  size = 15,
  className,
}: {
  size?: number | string;
  className?: string;
}): React.ReactElement {
  const id = React.useId();
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 652 652"
      className={className}
      aria-hidden="true"
      focusable="false"
      style={{ display: "block", flex: "none" }}
    >
      <mask id={id}>
        <circle cx="326" cy="326" r="326" fill="#fff" />
        <rect x="460.5" y="0" width="37" height="652" fill="#000" />
        <path d="M567.4 18.1 595.4 42.4 83.6 632.9 55.6 608.6Z" fill="#000" />
      </mask>
      <circle cx="326" cy="326" r="326" fill="currentColor" mask={`url(#${id})`} />
    </svg>
  );
}

export interface PivotLockupProps {
  /** Wordmark size in px. The mark scales from it. Default 20. */
  fontSize?: number;
  className?: string;
  style?: React.CSSProperties;
  /** Accessible name. The mark is decorative; this names the whole lockup. */
  title?: string;
}

export function PivotLockup({
  fontSize = 20,
  className,
  style,
  title = "Pivot",
}: PivotLockupProps): React.ReactElement {
  return (
    <span
      className={className}
      aria-label={title}
      role="img"
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: "0.28em",
        fontFamily: "var(--font-serif)",
        fontWeight: 550,
        fontSize,
        lineHeight: 1,
        letterSpacing: "-0.03em",
        ...style,
      }}
    >
      <PivotDisc size="0.86em" />
      {/* aria-hidden on the text: the lockup already carries its name, and
          without this a screen reader reads "Pivot" twice. */}
      <span aria-hidden="true">
        Pivot<span style={{ color: "var(--pivot-blue, #219ebc)" }}>.</span>
      </span>
    </span>
  );
}
