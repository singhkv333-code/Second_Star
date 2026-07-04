"use client";

/**
 * LoginIntro — the post-login brand moment (Onto-style loader, PIVOT
 * wordmark). One continuous ~3.95s sequence:
 *
 *   rise     0.00–1.05s  five EQUAL thick slabs grow from the ground up,
 *                        staggered left → right
 *   settle   1.05–1.35s  the equal grid glides onto the letters' real
 *                        cells (PIVOT's I is narrower than its slot)
 *   carve    1.30–2.15s  slabs crossfade into ultra-heavy type whose
 *                        counters punch open (a fat text-stroke animating
 *                        to zero). The word STAYS bold — no thin-down.
 *   merge    2.25–2.95s  the bold letters slide into the O and are
 *                        absorbed; only the heavy O remains
 *   zoom     2.95–3.95s  the O grows — its stroke floods the screen
 *                        (black field, white pill counter), the white
 *                        cover fades so the app shows through the pill,
 *                        and the rounded corners exit the viewport
 *
 * Timeline is a pure function of t (applyFrame), driven by rAF — so a
 * preview can freeze any instant (?t=1.8) to inspect frames. The zoom ring
 * animates width/height/border (not transform), so it stays crisp at any
 * scale; its start state is measured from the real bold O's ink (canvas
 * metrics + a midline pixel scan for the stroke) so the glyph → ring
 * hand-off is seamless.
 *
 * Set in the "Anybody" variable font at wght 900 / wdth 150 throughout —
 * load it wherever the component is used (see app/anim-preview/page.tsx).
 */

import { useCallback, useEffect, useMemo, useRef } from "react";

const WORD = "PIVOT";
const O_INDEX = 3; // the letter everything merges into

// ── Timeline (seconds) ──────────────────────────────────────────────
const RISE_STAGGER = 0.13; //  per-block delay, left → right
const RISE_DURATION = 0.58; // each block's ground-up growth
const T_SETTLE_START = 1.05; // equal grid → real letter cells
const T_SETTLE_END = 1.35;
const T_XFADE_START = 1.3; //  slabs ↔ text crossfade
const T_XFADE_END = 1.8;
const T_CARVE_START = 1.45; // fat stroke → 0 (counters punch open)
const T_CARVE_END = 2.6;
const T_MELT_START = 1.2; // gooey blur bell over the slab→letter forge
const T_MELT_END = 2.5;
/** Peak gooey blur, as a fraction of the font size. */
const MELT_BLUR_EM = 0.055;
const T_MERGE_START = 2.7; // bold letters converge on the O
const T_MERGE_END = 3.4;
const T_ZOOM_END = 4.4; //    counter has swallowed the viewport
/** White cover fade (app appears inside the pill), as zoom progress 0..1 —
 *  timed to when the ring's black has already flooded the viewport. */
const REVEAL_Z_START = 0.6;
const REVEAL_Z_END = 0.75;

/** The wordmark's fixed variable-font state — bold from start to finish. */
const WGHT = 900;
const WDTH = 150;
const STROKE_EM = 0.26; // carve-phase text-stroke (closes the counters)
const TRACK_EM = 0.04;
/** Slab gap on each side of an equal-grid slot, as a fraction of slot width. */
const SLOT_GAP = 0.045;

// ── Easings ─────────────────────────────────────────────────────────
const easeOutCubic = (x: number): number => 1 - Math.pow(1 - x, 3);
const easeInOutCubic = (x: number): number =>
  x < 0.5 ? 4 * x * x * x : 1 - Math.pow(-2 * x + 2, 3) / 2;
const easeInOutQuint = (x: number): number =>
  x < 0.5 ? 16 * x * x * x * x * x : 1 - Math.pow(-2 * x + 2, 5) / 2;
const easeInQuart = (x: number): number => x * x * x * x;

/** Progress of t through [a, b], clamped to 0..1. */
const seg = (t: number, a: number, b: number): number =>
  Math.max(0, Math.min(1, (t - a) / (b - a)));
const lerp = (a: number, b: number, p: number): number => a + (b - a) * p;

interface SlabGeom {
  /** Equal-grid slot (left/width, px relative to the word box). */
  grid: { left: number; width: number };
  /** The letter's real cell (measured from its span). */
  cell: { left: number; width: number };
}

export function LoginIntro({
  onDone,
  freezeAt,
}: {
  /** Fired once the counter has swallowed the viewport (overlay unmounts). */
  onDone?: () => void;
  /** Preview hook — render the exact frame at this time (s) and hold. */
  freezeAt?: number;
}): React.ReactElement {
  const rootRef = useRef<HTMLDivElement>(null);
  const coverRef = useRef<HTMLDivElement>(null);
  const wordRef = useRef<HTMLDivElement>(null);
  const ringRef = useRef<HTMLDivElement>(null);
  const blockRefs = useRef<(HTMLDivElement | null)[]>([]);
  const letterRefs = useRef<(HTMLSpanElement | null)[]>([]);

  // Measured once (font-ready / phase boundaries), not per frame.
  const slabGeom = useRef<SlabGeom[] | null>(null);
  const fontPxRef = useRef(0);
  const mergeDx = useRef<number[] | null>(null);
  const ringBase = useRef<{
    w: number;
    h: number;
    holeW: number;
    holeH: number;
    cx: number;
    cy: number;
  } | null>(null);
  const doneFired = useRef(false);

  /** Render the animation state for time t. Pure w.r.t. DOM refs. */
  const applyFrame = useCallback(
    (t: number): void => {
      const word = wordRef.current;
      const ring = ringRef.current;
      const cover = coverRef.current;
      if (!word || !ring || !cover) return;

      // ── slabs: staggered ground-up rise, then equal grid → real cells ──
      const xfade = easeOutCubic(seg(t, T_XFADE_START, T_XFADE_END));
      if (slabGeom.current) {
        const settle = easeInOutCubic(seg(t, T_SETTLE_START, T_SETTLE_END));
        blockRefs.current.forEach((b, i) => {
          if (!b) return;
          const g = slabGeom.current![i]!;
          const rise = easeOutCubic(
            seg(t, i * RISE_STAGGER, i * RISE_STAGGER + RISE_DURATION),
          );
          b.style.left = `${lerp(g.grid.left, g.cell.left, settle).toFixed(2)}px`;
          b.style.width = `${lerp(g.grid.width, g.cell.width, settle).toFixed(2)}px`;
          b.style.transform = `scaleY(${rise.toFixed(4)})`;
          b.style.opacity = String(1 - xfade);
        });
      }

      // ── carve: text fades in (ink alpha) while its fat stroke → 0 ──
      // Ink alpha, not container opacity — the slabs are siblings inside
      // the same word box and must fade on their own schedule.
      const ink = `rgba(11, 11, 12, ${xfade.toFixed(3)})`;
      word.style.color = ink;
      word.style.webkitTextStrokeColor = ink;
      const carve = easeOutCubic(seg(t, T_CARVE_START, T_CARVE_END));
      word.style.webkitTextStrokeWidth = `${lerp(STROKE_EM, 0, carve).toFixed(4)}em`;

      // ── melt: gooey blur bell over the forge window. The word layer is
      // black-on-white, so blur + high contrast fuses slab and glyph into
      // one blob that re-solidifies as the letterform — the slab reads as
      // MELTING into the letter rather than crossfading. Zero at both ends
      // (crisp slabs before, crisp type before the merge).
      const meltP = seg(t, T_MELT_START, T_MELT_END);
      const blurPx = MELT_BLUR_EM * fontPxRef.current * Math.sin(Math.PI * meltP);
      word.style.filter =
        blurPx > 0.2 ? `blur(${blurPx.toFixed(2)}px) contrast(28)` : "none";

      // ── merge: bold letters converge on the word centre; non-O absorbed ──
      const merge = easeInOutQuint(seg(t, T_MERGE_START, T_MERGE_END));
      if (merge > 0 && !mergeDx.current) {
        // Measure once, the frame merge begins.
        const wordRect = word.getBoundingClientRect();
        const cx = wordRect.left + wordRect.width / 2;
        mergeDx.current = letterRefs.current.map((el) => {
          if (!el) return 0;
          const r = el.getBoundingClientRect();
          return cx - (r.left + r.width / 2);
        });
      }
      if (mergeDx.current) {
        letterRefs.current.forEach((el, i) => {
          if (!el) return;
          el.style.transform = `translateX(${(mergeDx.current![i]! * merge).toFixed(2)}px)`;
          if (i !== O_INDEX) {
            // Absorbed into the O over the last stretch of the travel.
            el.style.opacity = String(1 - seg(merge, 0.55, 0.95));
          }
        });
      }

      // ── zoom: swap the O glyph for a crisp border-pill ring, grow it.
      // The source zooms INTO the O: the stroke's share of the screen grows
      // as it approaches, so the black floods the viewport while the pill
      // counter is still small — outer edge and hole run separate curves.
      const z = seg(t, T_MERGE_END, T_ZOOM_END);
      const inZoom = t >= T_MERGE_END;
      if (inZoom && !ringBase.current) {
        // Measure the bold O's actual INK (not its advance box, which
        // includes tracking and bearings): canvas metrics for the box, a
        // pixel scan of the rendered O's midline for the stroke width.
        const oEl = letterRefs.current[O_INDEX];
        const fontPx = parseFloat(getComputedStyle(word).fontSize);
        const cnv = document.createElement("canvas");
        const pad = Math.ceil(fontPx * 0.25);
        cnv.width = Math.ceil(fontPx * 2.2);
        cnv.height = Math.ceil(fontPx * 1.6);
        const ctx = cnv.getContext("2d", { willReadFrequently: true });
        if (oEl && ctx) {
          // wdth 150 maps to font-stretch "extra-expanded"; set it both in
          // the shorthand and via fontStretch (belt and suspenders — an
          // unsupported keyword would silently fall back to wdth 100).
          ctx.font = `${WGHT} extra-expanded ${fontPx}px "Anybody"`;
          try {
            (ctx as CanvasRenderingContext2D & { fontStretch?: string }).fontStretch =
              "extra-expanded";
          } catch {
            /* older engines: shorthand already carries it */
          }
          ctx.textBaseline = "alphabetic";
          const m = ctx.measureText("O");
          const inkW = m.actualBoundingBoxLeft + m.actualBoundingBoxRight;
          const inkH = m.actualBoundingBoxAscent + m.actualBoundingBoxDescent;
          ctx.fillStyle = "#000";
          ctx.fillText("O", pad, pad + m.actualBoundingBoxAscent);
          // The bold O's SIDE walls are thicker than its top/bottom walls,
          // so the counter needs two scans: the middle row for the side
          // stroke (→ hole width) and the middle column for the top stroke
          // (→ hole height). One stroke for both axes reads as a slit.
          const midY = Math.round(pad + inkH / 2);
          const row = ctx.getImageData(0, midY, cnv.width, 1).data;
          let runStart = -1;
          let sideStroke = Math.max(2, inkW * 0.3);
          for (let x = 0; x < cnv.width; x++) {
            const on = row[x * 4 + 3]! > 128;
            if (on && runStart < 0) runStart = x;
            if (!on && runStart >= 0) {
              sideStroke = Math.max(2, x - runStart);
              break;
            }
          }
          // Ink left = origin − actualBoundingBoxLeft (the metric is
          // positive toward the LEFT, so an inset glyph yields a negative).
          const midX = Math.round(pad - m.actualBoundingBoxLeft + inkW / 2);
          const col = ctx.getImageData(midX, 0, 1, cnv.height).data;
          runStart = -1;
          let capStroke = Math.max(2, inkH * 0.3);
          for (let y = 0; y < cnv.height; y++) {
            const on = col[y * 4 + 3]! > 128;
            if (on && runStart < 0) runStart = y;
            if (!on && runStart >= 0) {
              capStroke = Math.max(2, y - runStart);
              break;
            }
          }
          const r = oEl.getBoundingClientRect();
          // Ink centre in viewport coords — the span's box includes the
          // letter-spacing gap after the glyph, so its centre sits left of
          // the box centre.
          const cx =
            r.left + (m.actualBoundingBoxRight - m.actualBoundingBoxLeft) / 2;
          const cy = r.top + r.height / 2;
          ringBase.current = {
            w: inkW,
            h: inkH,
            holeW: Math.max(2, inkW - 2 * sideStroke),
            holeH: Math.max(2, inkH - 2 * capStroke),
            cx,
            cy,
          };
        }
      }
      if (inZoom && ringBase.current) {
        word.style.opacity = "0";
        const { w, h, holeW, holeH, cx, cy } = ringBase.current;
        ring.style.left = `${cx.toFixed(1)}px`;
        ring.style.top = `${cy.toFixed(1)}px`;
        const diag = Math.hypot(window.innerWidth, window.innerHeight);
        // Outer edge races ahead (floods the screen black by z≈0.55)…
        const outerP = easeInOutQuint(z);
        const W = lerp(w, diag * 2.3, outerP);
        const H = lerp(h, diag * 2.3 * (h / w), outerP);
        // …while the pill hole lags, then blows past the viewport at the end.
        // BOTH hole dimensions target beyond the viewport independently —
        // preserving the bold O's flat slot aspect to the end would leave
        // the hollow too short to ever clear the screen vertically.
        const holeP = easeInQuart(z);
        const hw = Math.min(lerp(holeW, diag * 1.35, holeP), W - 2);
        const hh = Math.min(lerp(holeH, diag * 1.35, holeP), H - 2);
        const bLR = (W - hw) / 2;
        const bTB = (H - hh) / 2;
        ring.style.opacity = "1";
        ring.style.width = `${W.toFixed(1)}px`;
        ring.style.height = `${H.toFixed(1)}px`;
        ring.style.borderWidth = `${bTB.toFixed(1)}px ${bLR.toFixed(1)}px`;
        // Elliptical radii per axis: inner edge = the oval counter
        // (rx hw/2, ry hh/2), outer edge = inner + border — which at the
        // hand-off equals the full ink box, i.e. the O's stadium silhouette.
        ring.style.borderRadius = `${(hw / 2 + bLR).toFixed(1)}px / ${(hh / 2 + bTB).toFixed(1)}px`;
      } else {
        ring.style.opacity = "0";
      }

      // ── reveal: white cover fades — the app appears inside the pill,
      // while everything around it is already the ring's black. ──
      cover.style.opacity = String(1 - seg(z, REVEAL_Z_START, REVEAL_Z_END));

      // ── done ──
      if (t >= T_ZOOM_END && !doneFired.current && freezeAt == null) {
        doneFired.current = true;
        onDone?.();
      }
    },
    [onDone, freezeAt],
  );

  useEffect(() => {
    const root = rootRef.current;
    if (!root) return;

    // Accessibility: skip the ride entirely under reduced motion.
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
      if (freezeAt == null) {
        doneFired.current = true;
        onDone?.();
      }
      return;
    }

    let raf = 0;
    let cancelled = false;

    // Wait until the variable font is genuinely AVAILABLE — measuring or
    // painting fallback glyphs would wreck both the slab geometry and the
    // carve. `fonts.load()` alone is not enough: if the Google Fonts
    // stylesheet hasn't registered the face yet, it resolves immediately
    // with no matches, so poll `fonts.check()` (bounded at 4s).
    const FONT_SPEC = '900 100px "Anybody"';
    const waitForFont = async (): Promise<void> => {
      const start = performance.now();
      while (
        !document.fonts.check(FONT_SPEC) &&
        performance.now() - start < 4000 &&
        !cancelled
      ) {
        try {
          await document.fonts.load(FONT_SPEC);
        } catch {
          /* keep polling */
        }
        if (document.fonts.check(FONT_SPEC)) return;
        await new Promise((r) => setTimeout(r, 60));
      }
    };

    void waitForFont().then(() => {
      if (cancelled) return;
      const word = wordRef.current;
      if (word) {
        fontPxRef.current = parseFloat(getComputedStyle(word).fontSize);
        // Slab geometry: equal-grid slots + each letter's real cell,
        // both relative to the word box (slabs are absolute children).
        const wordRect = word.getBoundingClientRect();
        // The equal grid spans the letter row, not the padded word box.
        const padX = 0.24 * fontPxRef.current;
        const slot = (wordRect.width - 2 * padX) / WORD.length;
        const gap = slot * SLOT_GAP;
        slabGeom.current = letterRefs.current.map((el, i) => {
          const r = el?.getBoundingClientRect();
          const gridSlot = { left: padX + i * slot + gap, width: slot - 2 * gap };
          return {
            grid: gridSlot,
            cell: r
              ? { left: r.left - wordRect.left, width: r.width }
              : gridSlot,
          };
        });
      }
      root.style.visibility = "visible";
      if (freezeAt != null) {
        // Frame-inspection mode: walk up to the freeze point so one-shot
        // phase measurements (merge dx, ring geometry) happen in order.
        for (let tt = 0; tt < freezeAt; tt += 1 / 60) applyFrame(tt);
        applyFrame(freezeAt);
        return;
      }
      const t0 = performance.now();
      const tick = (): void => {
        const t = (performance.now() - t0) / 1000;
        applyFrame(Math.min(t, T_ZOOM_END));
        if (t < T_ZOOM_END) raf = requestAnimationFrame(tick);
      };
      raf = requestAnimationFrame(tick);
    });

    return () => {
      cancelled = true;
      cancelAnimationFrame(raf);
    };
  }, [applyFrame, freezeAt, onDone]);

  const letters = useMemo(() => WORD.split(""), []);

  return (
    <div
      ref={rootRef}
      aria-hidden="true"
      data-testid="login-intro"
      style={{
        position: "fixed",
        inset: 0,
        zIndex: 200,
        visibility: "hidden", // shown once the font is ready + measured
        pointerEvents: "none",
      }}
    >
      {/* White cover — the animation's paper. Fades late in the zoom so the
          app appears inside the pill counter while the field around it is
          still the ring's black. */}
      <div
        ref={coverRef}
        style={{ position: "absolute", inset: 0, background: "#ffffff" }}
      />

      {/* Stage — everything centres on the viewport middle. */}
      <div
        style={{
          position: "absolute",
          inset: 0,
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
        }}
      >
        {/* The wordmark — bold throughout. Letters are per-span so merge can
            move them; slabs are absolute siblings inside the same box. */}
        <div
          ref={wordRef}
          style={{
            position: "relative",
            display: "flex",
            fontFamily: '"Anybody", sans-serif',
            fontSize: "clamp(64px, 11vw, 148px)",
            // Own white paint + breathing room: the melt filter's contrast
            // needs a solid background to threshold against, and the blur
            // must not clip at the box edge. Invisible against the cover.
            background: "#ffffff",
            padding: "0.18em 0.24em",
            lineHeight: 1,
            // Ink starts transparent — the slabs carry the opening phases;
            // applyFrame fades the glyphs in via color/stroke alpha.
            color: "transparent",
            WebkitTextStrokeColor: "transparent",
            fontVariationSettings: `"wght" ${WGHT}, "wdth" ${WDTH}`,
            letterSpacing: `${TRACK_EM}em`,
            whiteSpace: "pre",
          }}
        >
          {letters.map((ch, i) => (
            <span
              key={i}
              ref={(el) => {
                letterRefs.current[i] = el;
              }}
              style={{ position: "relative", display: "inline-block" }}
            >
              {ch}
            </span>
          ))}
          {/* Slabs — five EQUAL thick blocks over the cap-height band.
              They rise from the ground up (scaleY, origin bottom) with a
              left → right stagger, then settle onto the letters' real
              cells just before the carve crossfade. */}
          {letters.map((_, i) => (
            <div
              key={`slab-${i}`}
              ref={(el) => {
                blockRefs.current[i] = el;
              }}
              style={{
                position: "absolute",
                // Cap-height band, shifted by the word box's own padding.
                top: "0.31em",
                bottom: "0.315em",
                left: 0,
                width: 0,
                background: "#0b0b0c",
                borderRadius: "0.03em",
                transform: "scaleY(0)",
                transformOrigin: "bottom",
              }}
            />
          ))}
        </div>
      </div>

      {/* Zoom ring — the O, rebuilt as a border-pill so it scales crisp.
          Geometry (size + border) animates instead of transform: no raster
          blur at 60×. Positioned on the merged O's measured ink centre. */}
      <div
        ref={ringRef}
        style={{
          position: "absolute",
          left: "50%",
          top: "50%",
          transform: "translate(-50%, -50%)",
          borderStyle: "solid",
          borderColor: "#0b0b0c",
          opacity: 0,
          boxSizing: "border-box",
        }}
      />
    </div>
  );
}

export default LoginIntro;
