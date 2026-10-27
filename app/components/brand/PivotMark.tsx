import * as React from "react";

/**
 * PivotMark — the Pivot brand glyph as a crisp, resolution-independent inline
 * SVG. Replaces the legacy 2000×2000 raster logos (`/pivot-icon.png` and
 * `/pivot-light.png`), which blurred whenever they were scaled down to the
 * ~20px topbar size.
 *
 * The mark is four vertical bars forming an ascending chart: three bars that
 * step up the diagonal, then a tall final bar — a "momentum / breakout"
 * silhouette. Geometry is taken faithfully from the original glyph:
 *
 *   • 648 × 576 tight bounding box (aspect ≈ 1.125)
 *   • four columns on a 180px pitch — each bar 108 wide, 72 gap (a clean
 *     0.6 / 0.4 rhythm, exactly as measured from the source PNG)
 *   • bars 1→3 climb the diagonal; the leftmost bar and the tall final bar
 *     both land on the shared baseline (y = 576)
 *
 * It paints with `currentColor`, so it inherits the surrounding text colour
 * and needs no light/dark asset swap — in dark mode it renders white, in
 * light mode black, automatically. `size` sets the rendered height in px;
 * width follows the aspect ratio.
 */
export interface PivotMarkProps {
  /** Rendered height in px. Width follows the 1.125 aspect ratio. Default 20. */
  size?: number;
  /** Corner radius in glyph units (viewBox is 648×576). Default 8 (subtle). */
  radius?: number;
  /**
   * Bar width in glyph units (viewBox is 648×576). Default 108, the faithful
   * source geometry. Bars widen/narrow around their column centres (180px
   * pitch), so the overall footprint stays put. The wordmark lockup
   * (PivotLogo) uses a heavier 132 so the bars hold their own next to a
   * bold wordmark.
   */
  barWidth?: number;
  className?: string;
  style?: React.CSSProperties;
  /**
   * Accessible label. When provided the SVG is exposed as an image with this
   * name; when omitted it is treated as decorative (aria-hidden).
   */
  title?: string;
}

export function PivotMark({
  size = 20,
  radius = 8,
  barWidth = 108,
  className,
  style,
  title,
}: PivotMarkProps): React.ReactElement {
  /* Column centres sit on the 180px pitch (54, 234, 414, 594); each bar is
     laid out symmetrically around its centre so barWidth changes don't
     shift the silhouette. The viewBox hugs the outer bar edges, so at the
     default 108 it is the original 0 0 648 576. */
  const x = (center: number) => center - barWidth / 2;
  const vbX = x(54);
  const vbWidth = 540 + barWidth;
  const width = (size * vbWidth) / 576;
  return (
    <svg
      viewBox={`${vbX} 0 ${vbWidth} 576`}
      width={width}
      height={size}
      fill="currentColor"
      className={className}
      style={{ display: "block", ...style }}
      role={title ? "img" : "presentation"}
      aria-label={title}
      aria-hidden={title ? undefined : true}
      focusable={false}
    >
      {title ? <title>{title}</title> : null}
      {/* Bar 1 — leftmost, sits on the baseline */}
      <rect x={x(54)} y={360} width={barWidth} height={216} rx={radius} />
      {/* Bar 2 — steps up */}
      <rect x={x(234)} y={180} width={barWidth} height={180} rx={radius} />
      {/* Bar 3 — steps up to the top */}
      <rect x={x(414)} y={0} width={barWidth} height={180} rx={radius} />
      {/* Bar 4 — tall final bar, full height to the baseline */}
      <rect x={x(594)} y={0} width={barWidth} height={576} rx={radius} />
    </svg>
  );
}

export default PivotMark;
