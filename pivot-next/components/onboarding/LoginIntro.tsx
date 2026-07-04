"use client";

/**
 * LoginIntro — the post-login brand moment, a faithful rebuild of Nelson
 * Noa's "Onto" loader with the PIVOT wordmark. One continuous ~3.7s
 * sequence:
 *
 *   blocks   0.00–0.45s  five solid slabs, one per letter, holding still
 *   carve    0.45–1.30s  counters punch open — slabs become ultra-heavy type
 *                        (a fat text-stroke closing the counters, animated
 *                        to zero; crossfaded from literal <div> slabs)
 *   thin     1.30–2.15s  variable-font morph: wght 900→100, wdth 150→100,
 *                        tracking opens, the word settles slightly smaller
 *   merge    2.15–2.80s  letters slide into the O and are absorbed; only
 *                        the hairline O ring remains, centred
 *   zoom     2.80–3.70s  the O grows ~60× — its stroke floods the screen
 *                        (black field, white pill counter), the white cover
 *                        fades so the app shows through the pill, and the
 *                        rounded corners exit the viewport
 *
 * Timeline is a pure function of t (applyFrame), driven by rAF — so a
 * preview can freeze any instant (?t=1.8) to inspect frames. The zoom ring
 * animates width/height/border-width (not transform), so it stays crisp
 * at any scale instead of rasterising blurry.
 *
 * Set the word in the "Anybody" variable font (wght 100–900, wdth 50–150):
 * its heavy-extended state reads as slabs, its light O is a clean stadium
 * ring — the two extremes this animation needs. Load it wherever the
 * component is used (see app/anim-preview/page.tsx).
 */

import { useCallback, useEffect, useMemo, useRef } from "react";

const WORD = "PIVOT";
const O_INDEX = 3; // the letter everything merges into

// ── Timeline (seconds) ──────────────────────────────────────────────
const T_CARVE_START = 0.45; // blocks → text crossfade begins
const T_CARVE_END = 1.3; //   stroke fully open
const T_THIN_END = 2.15; //   hairline weight reached
const T_MERGE_END = 2.8; //   only the O remains
const T_ZOOM_END = 3.7; //    counter has swallowed the viewport
/** White cover fade (app appears inside the pill), as zoom progress 0..1 —
 *  timed to when the ring's black has already flooded the viewport. */
const REVEAL_Z_START = 0.6;
const REVEAL_Z_END = 0.75;

// Heavy / light extremes of the wordmark.
const HEAVY = { wght: 900, wdth: 150, strokeEm: 0.26, trackEm: 0.04 };
const LIGHT = { wght: 100, wdth: 100, strokeEm: 0, trackEm: 0.13 };
/** Word scale settles down slightly as the weight thins (optical match). */
const THIN_SCALE = 0.93;

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

interface LetterMeasure {
  /** translateX (px) that moves this letter's centre onto the word centre. */
  dx: number;
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

  // Measured at phase boundaries, not per frame.
  const mergeMeasure = useRef<LetterMeasure[] | null>(null);
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

      // ── blocks ↔ text crossfade (carve entry) ──
      // The slabs live INSIDE the letter spans (so they track cell layout),
      // so the text fades via ink alpha, not container opacity — container
      // opacity would take the slabs down with it.
      const xfade = easeOutCubic(seg(t, T_CARVE_START, T_CARVE_START + 0.17));
      for (const b of blockRefs.current) {
        if (b) b.style.opacity = String(1 - xfade);
      }
      const ink = `rgba(11, 11, 12, ${xfade.toFixed(3)})`;
      word.style.color = ink;
      word.style.webkitTextStrokeColor = ink;

      // ── carve: fat stroke → 0 (counters punch open) ──
      const carve = easeOutCubic(seg(t, T_CARVE_START, T_CARVE_END));
      const strokeEm = lerp(HEAVY.strokeEm, LIGHT.strokeEm, carve);

      // ── thin: variable-font morph heavy → hairline ──
      const thin = easeInOutCubic(seg(t, T_CARVE_END, T_THIN_END));
      const wght = lerp(HEAVY.wght, LIGHT.wght, thin);
      const wdth = lerp(HEAVY.wdth, LIGHT.wdth, thin);
      const track = lerp(HEAVY.trackEm, LIGHT.trackEm, thin);
      const scale = lerp(1, THIN_SCALE, thin);

      word.style.webkitTextStrokeWidth = `${strokeEm.toFixed(4)}em`;
      word.style.fontVariationSettings = `"wght" ${wght.toFixed(1)}, "wdth" ${wdth.toFixed(1)}`;
      word.style.letterSpacing = `${track.toFixed(4)}em`;
      word.style.transform = `scale(${scale.toFixed(4)})`;

      // ── merge: letters converge on the word centre; non-O absorbed ──
      const merge = easeInOutQuint(seg(t, T_THIN_END, T_MERGE_END));
      if (merge > 0 && !mergeMeasure.current) {
        // Measure once, at the light state, the frame merge begins.
        const wordRect = word.getBoundingClientRect();
        const cx = wordRect.left + wordRect.width / 2;
        mergeMeasure.current = letterRefs.current.map((el) => {
          if (!el) return { dx: 0 };
          const r = el.getBoundingClientRect();
          return { dx: cx - (r.left + r.width / 2) };
        });
      }
      if (mergeMeasure.current) {
        letterRefs.current.forEach((el, i) => {
          if (!el) return;
          const { dx } = mergeMeasure.current![i]!;
          el.style.transform = `translateX(${(dx * merge).toFixed(2)}px)`;
          if (i !== O_INDEX) {
            // Absorbed into the O over the last stretch of its travel.
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
        // Measure the O's actual INK (not its advance box, which includes
        // tracking and bearings) so the ring picks up seamlessly where the
        // glyph left off: canvas metrics for the box, a pixel scan of the
        // rendered O's midline for the true hairline stroke width.
        const oEl = letterRefs.current[O_INDEX];
        const fontPx =
          parseFloat(getComputedStyle(word).fontSize) * THIN_SCALE;
        const cnv = document.createElement("canvas");
        const pad = Math.ceil(fontPx * 0.25);
        cnv.width = Math.ceil(fontPx * 1.6);
        cnv.height = Math.ceil(fontPx * 1.6);
        const ctx = cnv.getContext("2d", { willReadFrequently: true });
        if (oEl && ctx) {
          ctx.font = `100 ${fontPx}px "Anybody"`;
          ctx.textBaseline = "alphabetic";
          const m = ctx.measureText("O");
          const inkW = m.actualBoundingBoxLeft + m.actualBoundingBoxRight;
          const inkH = m.actualBoundingBoxAscent + m.actualBoundingBoxDescent;
          ctx.fillStyle = "#000";
          ctx.fillText("O", pad, pad + m.actualBoundingBoxAscent);
          const midY = Math.round(pad + inkH / 2);
          const row = ctx.getImageData(0, midY, cnv.width, 1).data;
          let runStart = -1;
          let stroke = Math.max(2, inkH * 0.04);
          for (let x = 0; x < cnv.width; x++) {
            const on = row[x * 4 + 3]! > 128;
            if (on && runStart < 0) runStart = x;
            if (!on && runStart >= 0) {
              stroke = Math.max(2, x - runStart);
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
            holeW: inkW - 2 * stroke,
            holeH: inkH - 2 * stroke,
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
        const holeP = easeInQuart(z);
        const hw = Math.min(lerp(holeW, diag * 1.3, holeP), W - 2);
        const hh = Math.min(lerp(holeH, diag * 1.3 * (holeH / holeW), holeP), H - 2);
        const bLR = (W - hw) / 2;
        const bTB = (H - hh) / 2;
        ring.style.opacity = "1";
        ring.style.width = `${W.toFixed(1)}px`;
        ring.style.height = `${H.toFixed(1)}px`;
        ring.style.borderWidth = `${bTB.toFixed(1)}px ${bLR.toFixed(1)}px`;
        // Keep the INNER edge a true pill: outer radius = inner + border.
        ring.style.borderRadius = `${(hh / 2 + bLR).toFixed(1)}px / ${(hh / 2 + bTB).toFixed(1)}px`;
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

    // Wait for the variable font — measuring/painting fallback glyphs
    // would wreck both the slab positions and the morph.
    void document.fonts.load('900 100px "Anybody"').then(() => {
      if (cancelled) return;
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

  // The slabs sit exactly on the heavy-state letter boxes. Rather than
  // measuring async, both slabs and letters live in the same flex row with
  // identical sizing — each slab is a sibling overlay of its letter cell.
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
        visibility: "hidden", // shown once the font is ready
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
        {/* The wordmark. Letters are per-span so merge can move them. */}
        <div
          ref={wordRef}
          style={{
            position: "relative",
            display: "flex",
            fontFamily: '"Anybody", sans-serif',
            fontSize: "clamp(64px, 11vw, 148px)",
            lineHeight: 1,
            // Ink starts transparent — the slabs carry the block phase;
            // applyFrame fades the glyphs in via color/stroke alpha.
            color: "transparent",
            WebkitTextStrokeColor: "transparent",
            fontVariationSettings: '"wght" 900, "wdth" 150',
            letterSpacing: `${HEAVY.trackEm}em`,
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
              {/* Slab — covers this letter's cell during the block phase.
                  Inset horizontally by the tracking gap so slabs read as
                  separate rectangles; vertically to the cap-height box. */}
              <div
                ref={(el) => {
                  blockRefs.current[i] = el;
                }}
                style={{
                  position: "absolute",
                  left: "0.015em",
                  right: "0.055em",
                  top: "0.13em",
                  bottom: "0.135em",
                  background: "#0b0b0c",
                  borderRadius: "0.03em",
                  opacity: 1,
                }}
              />
            </span>
          ))}
        </div>
      </div>

      {/* Zoom ring — the O, rebuilt as a border-pill so it scales crisp.
          Geometry (size + border) animates instead of transform: no raster
          blur at 60×. Centred on the viewport like the merged O. */}
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
