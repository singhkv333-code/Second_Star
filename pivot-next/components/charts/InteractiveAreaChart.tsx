"use client";

/**
 * InteractiveAreaChart — Groww-style smooth area chart with hover
 * crosshair, point dot, floating tooltip, AND click-and-drag range
 * selection (matching the ChartCard in StockDetailPage).
 *
 * Visual language matches the existing SparkAreaChart / PerformanceSvg
 * (smooth Catmull-Rom curve, gradient area fill, non-scaling stroke)
 * but adds the interactive layer the hand-rolled SVGs were missing:
 *
 *   • Vertical crosshair line that snaps to the nearest data point
 *   • Filled circle marker on the curve at the hovered point
 *   • Floating tooltip card with the formatted value + date
 *   • Drag-to-select range: floating returns pill + area shading
 */

import * as React from "react";

export type ChartPoint = { t: string; v: number };

type Props = {
  points: ChartPoint[];
  /** Line + gradient color. Use a CSS var (var(--color-profit)) or a hex. */
  color?: string;
  /** Container height in px. */
  height?: number;
  /** Format the tooltip value. Defaults to `n.toLocaleString()`. */
  formatValue?: (v: number) => string;
  /** Format the tooltip date label. Defaults to the raw `t` string. */
  formatDate?: (iso: string) => string;
  /** Render the curve as a smooth Catmull-Rom spline (default true). */
  smooth?: boolean;
  /** Show 4 horizontal gridlines + Y-axis labels on the right edge. */
  showGrid?: boolean;
  /**
   * Optional horizontal reference line value (e.g. RSI overbought
   * threshold). Drawn as a dashed line in `--color-loss` so it reads
   * as a "trigger" marker. Also included in the auto-domain so the
   * line is always visible inside the curve's value range.
   */
  referenceY?: number;
  /**
   * Enable click-and-drag range selection. When true, dragging shows a
   * floating returns pill (Δ + %) and shades the area under the curve
   * for the selected range. Default: true.
   */
  enableRangeSelect?: boolean;
  /** Optional accessibility label. */
  ariaLabel?: string;
  /** Test id forwarded to the outer container. */
  "data-testid"?: string;
};

// ─────────────────────────────────────────────────────────────────────────
// Path helpers — Catmull-Rom → cubic Bezier and a plain L-segment fallback.
// ─────────────────────────────────────────────────────────────────────────

function smoothPath(xs: number[], ys: number[]): string {
  if (xs.length < 2) return "";
  if (xs.length === 2) return `M ${xs[0]},${ys[0]} L ${xs[1]},${ys[1]}`;
  const segs: string[] = [`M ${xs[0]},${ys[0]}`];
  for (let i = 0; i < xs.length - 1; i++) {
    const p0x = xs[i === 0 ? i : i - 1]!;
    const p0y = ys[i === 0 ? i : i - 1]!;
    const p1x = xs[i]!;
    const p1y = ys[i]!;
    const p2x = xs[i + 1]!;
    const p2y = ys[i + 1]!;
    const p3x = xs[i + 2 < xs.length ? i + 2 : i + 1]!;
    const p3y = ys[i + 2 < ys.length ? i + 2 : i + 1]!;
    const c1x = p1x + (p2x - p0x) / 6;
    const c1y = p1y + (p2y - p0y) / 6;
    const c2x = p2x - (p3x - p1x) / 6;
    const c2y = p2y - (p3y - p1y) / 6;
    segs.push(`C ${c1x},${c1y} ${c2x},${c2y} ${p2x},${p2y}`);
  }
  return segs.join(" ");
}

function linearPath(xs: number[], ys: number[]): string {
  if (xs.length < 2) return "";
  return xs.map((x, i) => `${i === 0 ? "M" : "L"} ${x},${ys[i]}`).join(" ");
}

// ─────────────────────────────────────────────────────────────────────────
// Local formatters — match StockDetailPage's fmtDelta / fmtPct exactly.
// Self-contained so no cross-import is needed.
// ─────────────────────────────────────────────────────────────────────────

const INR_FMT = new Intl.NumberFormat("en-IN", {
  style: "currency",
  currency: "INR",
  maximumFractionDigits: 2,
});

function localFmtDelta(n: number): string {
  const s = n >= 0 ? "+" : "−";
  return `${s}${INR_FMT.format(Math.abs(n))}`;
}

function localFmtPct(n: number): string {
  const s = n >= 0 ? "+" : "";
  return `${s}${n.toFixed(2)}%`;
}

// Formats an ISO date string like "dd MMM, yyyy". Falls back to raw string.
function fmtDateShort(iso: string): string {
  try {
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return iso;
    return d.toLocaleDateString("en-IN", {
      day: "2-digit",
      month: "short",
      year: "numeric",
    });
  } catch {
    return iso;
  }
}

// ─────────────────────────────────────────────────────────────────────────
// Drag selection state
// ─────────────────────────────────────────────────────────────────────────

type DragState = {
  isDragging: boolean;
  startIdx: number;
  endIdx: number;
};

// ─────────────────────────────────────────────────────────────────────────
// Component
// ─────────────────────────────────────────────────────────────────────────

export function InteractiveAreaChart({
  points,
  color = "var(--color-profit)",
  height = 200,
  formatValue,
  formatDate,
  smooth = true,
  showGrid = false,
  referenceY,
  enableRangeSelect = true,
  ariaLabel,
  "data-testid": testId,
}: Props): React.ReactElement {
  const containerRef = React.useRef<HTMLDivElement>(null);
  const [hoverIdx, setHoverIdx] = React.useState<number | null>(null);
  const [drag, setDrag] = React.useState<DragState | null>(null);

  // Stable ids — React.useId so multiple charts on the same page
  // don't share gradient / clipPath definitions.
  const uid = React.useId().replace(/:/g, "");
  const gradId = `grad-${uid}`;
  const clipId = `sel-clip-${uid}`;

  // ── Escape key clears a finalised selection ──────────────────────────
  // NOTE: all hooks must be called unconditionally before any early return.
  React.useEffect(() => {
    if (!drag) return;
    const onKey = (e: KeyboardEvent): void => {
      if (e.key === "Escape") setDrag(null);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [drag]);

  // ── Pointer → nearest index helper (needs points.length) ────────────
  const clientXToIdx = React.useCallback(
    (clientX: number): number => {
      const rect = containerRef.current?.getBoundingClientRect();
      if (!rect || rect.width === 0) return 0;
      const x = clientX - rect.left;
      const frac = Math.max(0, Math.min(1, x / rect.width));
      return Math.round(frac * (points.length - 1));
    },
    [points.length],
  );

  // ── Hover handlers ────────────────────────────────────────────────────
  const handleMove = React.useCallback(
    (e: React.MouseEvent<HTMLDivElement>): void => {
      const rect = containerRef.current?.getBoundingClientRect();
      if (!rect || rect.width === 0) return;
      const x = e.clientX - rect.left;
      const frac = Math.max(0, Math.min(1, x / rect.width));
      const idx = Math.round(frac * (points.length - 1));
      setHoverIdx(idx);
      // Also update endIdx live while dragging
      if (enableRangeSelect) {
        setDrag((prev) =>
          prev?.isDragging ? { ...prev, endIdx: idx } : prev,
        );
      }
    },
    [points.length, enableRangeSelect],
  );

  const handleLeave = React.useCallback((): void => {
    setHoverIdx(null);
    // Cancel an in-progress drag; preserve a finalised selection.
    if (enableRangeSelect) {
      setDrag((prev) => (prev?.isDragging ? null : prev));
    }
  }, [enableRangeSelect]);

  // ── Drag handlers ─────────────────────────────────────────────────────
  const handlePointerDown = React.useCallback(
    (e: React.PointerEvent<HTMLDivElement>): void => {
      if (!enableRangeSelect) return;
      if (e.button !== 0) return;
      const idx = clientXToIdx(e.clientX);
      setDrag({ isDragging: true, startIdx: idx, endIdx: idx });
      try {
        (e.currentTarget as HTMLDivElement).setPointerCapture(e.pointerId);
      } catch {
        // setPointerCapture may throw in some environments; safe to ignore.
      }
    },
    [enableRangeSelect, clientXToIdx],
  );

  const handlePointerMove = React.useCallback(
    (e: React.PointerEvent<HTMLDivElement>): void => {
      if (!enableRangeSelect) return;
      const idx = clientXToIdx(e.clientX);
      setDrag((prev) => (prev?.isDragging ? { ...prev, endIdx: idx } : prev));
    },
    [enableRangeSelect, clientXToIdx],
  );

  const handlePointerUp = React.useCallback(
    (e: React.PointerEvent<HTMLDivElement>): void => {
      if (!enableRangeSelect) return;
      const idx = clientXToIdx(e.clientX);
      setDrag((prev) => {
        if (!prev) return null;
        // Plain click (< 2 point spread): clear selection.
        if (Math.abs(idx - prev.startIdx) < 2) return null;
        return { ...prev, isDragging: false, endIdx: idx };
      });
    },
    [enableRangeSelect, clientXToIdx],
  );

  const handlePointerCancel = React.useCallback((): void => {
    if (enableRangeSelect) {
      setDrag((prev) => (prev?.isDragging ? null : prev));
    }
  }, [enableRangeSelect]);

  // ── Early return for insufficient data ──────────────────────────────
  // All hooks above; safe to return early after this point.
  if (points.length < 2) {
    return <div ref={containerRef} style={{ height }} aria-label={ariaLabel} />;
  }

  const W = 800;
  const H = height;
  const padX = 4;
  // Smaller charts (≤80px tall) deserve less vertical padding so the
  // curve doesn't shrink into a flat ribbon.
  const padY = H < 80 ? 4 : 10;
  const innerW = W - padX * 2;
  const innerH = H - padY * 2;

  const values = points.map((p) => p.v);
  const min = referenceY != null ? Math.min(referenceY, ...values) : Math.min(...values);
  const max = referenceY != null ? Math.max(referenceY, ...values) : Math.max(...values);
  const range = max - min || 1;

  const xAt = (i: number): number => padX + (i / (points.length - 1)) * innerW;
  const yAt = (v: number): number => padY + (1 - (v - min) / range) * innerH;

  const xs = points.map((_, i) => xAt(i));
  const ys = values.map(yAt);

  const linePath = smooth ? smoothPath(xs, ys) : linearPath(xs, ys);
  const areaPath = `${linePath} L ${xs[xs.length - 1]} ${H} L ${xs[0]} ${H} Z`;

  // ── Hover derived values ─────────────────────────────────────────────
  const activePoint = hoverIdx !== null ? points[hoverIdx]! : null;
  const activeXPct =
    hoverIdx !== null ? (xs[hoverIdx]! / W) * 100 : null;
  const activeYPct =
    hoverIdx !== null ? (ys[hoverIdx]! / H) * 100 : null;

  // Tooltip horizontal anchoring — keep it on-screen at the edges.
  const tooltipAnchor: React.CSSProperties =
    activeXPct === null
      ? {}
      : activeXPct < 8
        ? { left: 0, transform: "translateX(0)" }
        : activeXPct > 92
          ? { left: "100%", transform: "translateX(-100%)" }
          : { left: `${activeXPct}%`, transform: "translateX(-50%)" };

  // ── Selection derived values ─────────────────────────────────────────
  // Inline (no useMemo) since these are O(1) and hooks must all be above.
  let selectionInfo: {
    lo: number;
    hi: number;
    deltaAbs: number;
    deltaPct: number;
  } | null = null;

  if (drag && Math.abs(drag.endIdx - drag.startIdx) >= 2) {
    const lo = Math.min(drag.startIdx, drag.endIdx);
    const hi = Math.max(drag.startIdx, drag.endIdx);
    const loV = points[lo]?.v ?? 0;
    const hiV = points[hi]?.v ?? 0;
    if (loV !== 0) {
      const deltaAbs = hiV - loV;
      const deltaPct = (deltaAbs / loV) * 100;
      selectionInfo = { lo, hi, deltaAbs, deltaPct };
    }
  }

  // Pill horizontal center as % of container width, clamped to [6, 94].
  let pillLeftPct: number | null = null;
  if (selectionInfo !== null) {
    const { lo, hi } = selectionInfo;
    const loSvgX = xs[lo] ?? 0;
    const hiSvgX = xs[hi] ?? 0;
    const midSvgX = (loSvgX + hiSvgX) / 2;
    pillLeftPct = Math.max(6, Math.min(94, (midSvgX / W) * 100));
  }

  // SVG x-coordinates of the selection boundaries (in viewBox units).
  const selLoX = selectionInfo !== null ? (xs[selectionInfo.lo] ?? 0) : 0;
  const selHiX = selectionInfo !== null ? (xs[selectionInfo.hi] ?? 0) : 0;

  // Color by sign of the delta (green profit / red loss).
  const selColor =
    selectionInfo !== null && selectionInfo.deltaAbs < 0
      ? "var(--color-loss)"
      : "var(--color-profit)";

  // Labels for the lo/hi boundary points.
  const loLabel = selectionInfo !== null
    ? fmtDateShort(points[selectionInfo.lo]?.t ?? "")
    : "";
  const hiLabel = selectionInfo !== null
    ? fmtDateShort(points[selectionInfo.hi]?.t ?? "")
    : "";

  // Optional grid lines (4 evenly spaced)
  const gridYs = showGrid
    ? Array.from({ length: 4 }, (_, i) => padY + (i / 3) * innerH)
    : [];
  const gridLabels = showGrid
    ? Array.from({ length: 4 }, (_, i) => {
        const t = i / 3;
        const v = min + range * (1 - t);
        return { y: padY + t * innerH, v };
      })
    : [];

  return (
    <div
      ref={containerRef}
      className="relative w-full select-none"
      style={{
        height,
        cursor: enableRangeSelect ? "crosshair" : "default",
      }}
      onMouseMove={handleMove}
      onMouseLeave={handleLeave}
      onPointerDown={handlePointerDown}
      onPointerMove={handlePointerMove}
      onPointerUp={handlePointerUp}
      onPointerCancel={handlePointerCancel}
      role="img"
      aria-label={ariaLabel}
      data-testid={testId}
    >
      <svg
        viewBox={`0 0 ${W} ${H}`}
        width="100%"
        height={H}
        preserveAspectRatio="none"
        style={{ display: "block" }}
        aria-hidden="true"
      >
        <defs>
          <linearGradient id={gradId} x1="0" y1="0" x2="0" y2="1">
            {/* Softer gradient ramp (0.12 → 0) — matches Groww's
                clean look where the fill is a whisper of color, not
                a band. */}
            <stop offset="0%" stopColor={color} stopOpacity={0.12} />
            <stop offset="100%" stopColor={color} stopOpacity={0} />
          </linearGradient>

          {/* Clip path for selection shading — rect bounded by the
              lo/hi x-positions, full height. The area path drawn
              beneath the curve is naturally bounded above by the
              curve itself, so only the under-curve region is visible
              within the clip rectangle. */}
          {selectionInfo !== null && (
            <clipPath id={clipId}>
              <rect x={selLoX} y={0} width={selHiX - selLoX} height={H} />
            </clipPath>
          )}
        </defs>

        {showGrid &&
          gridYs.map((y, i) => (
            <line
              key={i}
              x1={0}
              x2={W}
              y1={y}
              y2={y}
              stroke="var(--glass-border)"
              strokeWidth={1}
              vectorEffect="non-scaling-stroke"
            />
          ))}

        {referenceY != null && (
          <line
            x1={0}
            x2={W}
            y1={yAt(referenceY)}
            y2={yAt(referenceY)}
            stroke="var(--color-loss)"
            strokeWidth={0.75}
            strokeDasharray="3 3"
            opacity={0.6}
            vectorEffect="non-scaling-stroke"
          />
        )}

        {/* Base area gradient fill */}
        <path d={areaPath} fill={`url(#${gradId})`} />

        {/* Selection area shading — same area path, clipped to
            [lo, hi] x-range, filled with profit/loss color at 0.18
            opacity. Rendered BEFORE the line so the line draws on top. */}
        {selectionInfo !== null && (
          <path
            d={areaPath}
            fill={selColor}
            fillOpacity={0.18}
            stroke="none"
            clipPath={`url(#${clipId})`}
          />
        )}

        {/* Main curve line */}
        <path
          d={linePath}
          fill="none"
          stroke={color}
          strokeWidth={1.25}
          strokeLinejoin="round"
          strokeLinecap="round"
          vectorEffect="non-scaling-stroke"
        />

        {/* Thin dashed boundary line at lo edge */}
        {selectionInfo !== null && (
          <line
            x1={selLoX}
            x2={selLoX}
            y1={0}
            y2={H}
            stroke={selColor}
            strokeWidth={0.75}
            strokeDasharray="3 3"
            strokeOpacity={0.5}
            vectorEffect="non-scaling-stroke"
          />
        )}

        {/* Thin dashed boundary line at hi edge */}
        {selectionInfo !== null && selLoX !== selHiX && (
          <line
            x1={selHiX}
            x2={selHiX}
            y1={0}
            y2={H}
            stroke={selColor}
            strokeWidth={0.75}
            strokeDasharray="3 3"
            strokeOpacity={0.5}
            vectorEffect="non-scaling-stroke"
          />
        )}
      </svg>

      {/* Y-axis labels on the right edge */}
      {showGrid &&
        gridLabels.map(({ y, v }, i) => (
          <span
            key={i}
            className="pointer-events-none absolute right-1 -translate-y-1/2 text-[10px] tabular-nums text-muted-foreground/70"
            style={{ top: `${(y / H) * 100}%` }}
          >
            {formatValue ? formatValue(v) : Math.round(v).toString()}
          </span>
        ))}

      {/* Hover crosshair (div, so the dash stays crisp).
          Hidden while actively dragging to reduce visual noise. */}
      {activeXPct !== null && !drag?.isDragging && (
        <div
          className="pointer-events-none absolute top-0 bottom-0 border-l border-dashed border-muted-foreground/40"
          style={{ left: `${activeXPct}%` }}
          aria-hidden="true"
        />
      )}

      {/* Hover dot (div, so circle stays circular regardless of SVG stretch).
          Hidden while dragging. */}
      {activeXPct !== null && activeYPct !== null && !drag?.isDragging && (
        <div
          className="pointer-events-none absolute h-[10px] w-[10px] -translate-x-1/2 -translate-y-1/2 rounded-full border-2 border-background shadow-[0_0_0_1px_rgba(0,0,0,0.06)]"
          style={{
            left: `${activeXPct}%`,
            top: `${activeYPct}%`,
            background: color,
          }}
          aria-hidden="true"
        />
      )}

      {/* Floating hover tooltip card.
          Hidden while dragging (the range pill takes over). */}
      {activePoint && !drag?.isDragging && (
        <div
          className="pointer-events-none absolute z-10 rounded-md border border-border/60 bg-popover px-2.5 py-1.5 text-[11px] shadow-lg"
          style={{
            top: 0,
            marginTop: -6,
            ...tooltipAnchor,
            transform: `${tooltipAnchor.transform ?? ""} translateY(-100%)`,
            whiteSpace: "nowrap",
          }}
          role="status"
          aria-live="polite"
        >
          <div className="font-semibold tabular-nums text-foreground">
            {formatValue ? formatValue(activePoint.v) : activePoint.v.toLocaleString()}
          </div>
          <div className="text-[10px] text-muted-foreground">
            {formatDate ? formatDate(activePoint.t) : activePoint.t}
          </div>
        </div>
      )}

      {/* ── Range-selection floating returns pill ──────────────────────
          Shown live during drag and after finalisation.
          Design matches StockDetailPage ChartCard exactly:
            • var(--bg-primary) background card
            • var(--glass-border) border
            • var(--radius-pill) rounded pill
            • var(--font-mono) mono font, 12px/600
            • profit/loss color by sign
            • sub-label: date range in var(--text-tertiary), var(--font-ui), 10px
            • 0 2px 8px rgba(0,0,0,0.18) box-shadow
          pointerEvents:none so it never blocks dragging. */}
      {selectionInfo !== null && pillLeftPct !== null && (
        <div
          aria-live="polite"
          aria-label="Selected range return"
          style={{
            position: "absolute",
            top: 6,
            left: `${pillLeftPct}%`,
            transform: "translateX(-50%)",
            pointerEvents: "none",
            zIndex: 10,
            display: "flex",
            flexDirection: "column",
            alignItems: "center",
            gap: 2,
          }}
        >
          {/* Main pill — Δ price + Δ % */}
          <div
            style={{
              display: "inline-flex",
              alignItems: "center",
              gap: 6,
              padding: "4px 10px",
              borderRadius: "var(--radius-pill)",
              background: "var(--bg-primary)",
              border: "1px solid var(--glass-border)",
              boxShadow: "0 2px 8px rgba(0,0,0,0.18)",
              fontFamily: "var(--font-mono)",
              fontSize: 12,
              fontWeight: 600,
              whiteSpace: "nowrap",
              color: selColor,
            }}
          >
            {localFmtDelta(selectionInfo.deltaAbs)}{" "}
            ({localFmtPct(selectionInfo.deltaPct)})
          </div>

          {/* Date range sub-label */}
          <div
            style={{
              fontSize: 10,
              fontFamily: "var(--font-ui)",
              color: "var(--text-tertiary)",
              whiteSpace: "nowrap",
            }}
          >
            {loLabel} – {hiLabel}
          </div>
        </div>
      )}
    </div>
  );
}
