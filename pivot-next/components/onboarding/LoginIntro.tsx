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
/** White cover fade (app appears inside the pill) — late in the zoom. */
const T_REVEAL_START = 3.18;
const T_REVEAL_END = 3.42;

// Heavy / light extremes of the wordmark.
const HEAVY = { wght: 900, wdth: 150, strokeEm: 0.26, trackEm: 0.04 };
const LIGHT = { wght: 100, wdth: 100, strokeEm: 0, trackEm: 0.13 };
/** Word scale settles down slightly as the weight thins (optical match). */
const THIN_SCALE = 0.93;
/** Zoom magnification — enough that the pill's corners exit any viewport. */
const ZOOM_SCALE = 64;

// ── Easings ─────────────────────────────────────────────────────────
const easeOutCubic = (x: number): number => 1 - Math.pow(1 - x, 3);
const easeInOutCubic = (x: number): number =>
  x < 0.5 ? 4 * x * x * x : 1 - Math.pow(-2 * x + 2, 3) / 2;
const easeInOutQuint = (x: number): number =>
  x < 0.5 ? 16 * x * x * x * x * x : 1 - Math.pow(-2 * x + 2, 5) / 2;
const easeInExpo = (x: number): number =>
  x === 0 ? 0 : Math.pow(2, 10 * x - 10);

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
  const ringBase = useRef<{ w: number; h: number; bw: number } | null>(null);
  const doneFired = useRef(false);

  /** Render the animation state for time t. Pure w.r.t. DOM refs. */
  const applyFrame = useCallback(
    (t: number): void => {
      const word = wordRef.current;
      const ring = ringRef.current;
      const cover = coverRef.current;
      if (!word || !ring || !cover) return;

      // ── blocks ↔ text crossfade (carve entry) ──
      const xfade = easeOutCubic(seg(t, T_CARVE_START, T_CARVE_START + 0.17));
      for (const b of blockRefs.current) {
        if (b) b.style.opacity = String(1 - xfade);
      }
      word.style.opacity = String(xfade);

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

      // ── zoom: swap the O glyph for a crisp border-pill ring, grow it ──
      const zoom = easeInExpo(seg(t, T_MERGE_END, T_ZOOM_END));
      const inZoom = t >= T_MERGE_END;
      if (inZoom && !ringBase.current) {
        const oEl = letterRefs.current[O_INDEX];
        if (oEl) {
          const r = oEl.getBoundingClientRect();
          // Hairline-O stroke ≈ 9.5% of the glyph box height (Anybody @100).
          ringBase.current = {
            w: r.width,
            h: r.height * 0.72, // cap-height box, not the full line box
            bw: Math.max(2.5, r.height * 0.72 * 0.095),
          };
        }
      }
      if (inZoom && ringBase.current) {
        word.style.opacity = "0";
        const { w, h, bw } = ringBase.current;
        const s = lerp(1, ZOOM_SCALE, zoom);
        ring.style.opacity = "1";
        ring.style.width = `${(w * s).toFixed(1)}px`;
        ring.style.height = `${(h * s).toFixed(1)}px`;
        ring.style.borderWidth = `${(bw * s).toFixed(1)}px`;
        ring.style.borderRadius = `${((h * s) / 2).toFixed(1)}px`;
      } else {
        ring.style.opacity = "0";
      }

      // ── reveal: white cover fades — the app appears inside the pill ──
      cover.style.opacity = String(1 - seg(t, T_REVEAL_START, T_REVEAL_END));

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
            color: "#0b0b0c",
            WebkitTextStrokeColor: "#0b0b0c",
            fontVariationSettings: '"wght" 900, "wdth" 150',
            letterSpacing: `${HEAVY.trackEm}em`,
            opacity: 0,
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
