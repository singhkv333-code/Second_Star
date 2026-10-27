/**
 * landing-scroll — the page's scroll layer, in one place.
 *
 * What this replaces: an IntersectionObserver that stamped `data-visible` on
 * `[data-reveal]`, with a CSS transition doing the fade. That worked, but it
 * could only ever do one thing — every block entered identically, alone, with
 * no way to say that three columns belong together or that the artwork behind
 * them sits further away. This module is the same contract (`[data-reveal]`
 * still marks what enters) driven by GSAP's ScrollTrigger, which the repo
 * already ships and the product film already uses.
 *
 * Two kinds of motion, and deliberately no third:
 *
 *   ENTRANCES — a block rises 26px and fades in, once, on the way past. Blocks
 *     that cross the line together are batched by ScrollTrigger and staggered
 *     in the order they sit on the page, which is what turns a row of three
 *     figures into one gesture instead of three separate arrivals.
 *
 *   DRIFT — the dithered artwork moves DOWN relative to its own section as the
 *     page scrolls, which is what something far away does when you move past
 *     it. Never the type, never a card, never a panel: everything that carries
 *     information stays exactly where it was laid out. The whole effect is
 *     ±22px over a 3,000px section, which reads as depth rather than as motion.
 *
 * What is NOT here, on purpose: no smooth-scroll hijack. ScrollSmoother is a
 * Club plugin and Lenis is a dependency, but the real objection is that both
 * take the scroll away from the reader — the wheel stops meaning what the
 * operating system says it means. A page whose argument is "this product is
 * calm and precise" should not fight the scrollbar.
 *
 * Reduced motion is a separate branch of the same `gsap.matchMedia`: no
 * entrances, no drift, everything already visible. `mm.revert()` on unmount
 * puts every property back and kills every trigger, so a route change cannot
 * leave a half-played tween behind.
 */
"use client";

import { useEffect } from "react";
import gsap from "gsap";
import { ScrollTrigger } from "gsap/ScrollTrigger";

gsap.registerPlugin(ScrollTrigger);

/** How far a block travels on its way in. Small enough to read as settling. */
const RISE = 26;

/**
 * Park a background layer on its own slow track. `from`/`to` are the ends of
 * the drift in pixels, applied to a custom property rather than to `transform`
 * because the layers being moved are pseudo-elements — the artwork is painted
 * by `::after` on the section and `::before` on two of the stories, and GSAP
 * cannot address those directly.
 */
function drift(scope: string, prop: string, span: number, trigger?: string) {
  gsap.utils.toArray<HTMLElement>(scope).forEach((el) => {
    gsap.fromTo(
      el,
      { [prop]: `${-span}px` },
      {
        [prop]: `${span}px`,
        ease: "none",
        scrollTrigger: {
          trigger: trigger ?? el,
          start: "top bottom",
          end: "bottom top",
          scrub: true,
        },
      },
    );
  });
}

export function useLandingScroll(): void {
  useEffect(() => {
    const mm = gsap.matchMedia();

    mm.add("(prefers-reduced-motion: no-preference)", () => {
      const blocks = gsap.utils.toArray<HTMLElement>("[data-reveal]");
      /* Plain `opacity`, not the autoAlpha shorthand: that one also sets
         `visibility: hidden`, which takes every block below the fold out of
         the accessibility tree and out of what a crawler renders. On a page
         whose whole job is to be read and found, that is a bad trade for the
         pointer events it would save on something already invisible. */
      gsap.set(blocks, { opacity: 0, y: RISE });

      /* `batch` groups whatever crosses the line in the same frame, so the
         stagger falls out of the layout instead of being hand-assigned: three
         figure columns arrive as one sweep, a lone heading arrives alone.
         `onEnterBack` exists for the reload-halfway-down case — those blocks
         are already past their trigger and would otherwise never be told to
         appear. */
      ScrollTrigger.batch(blocks, {
        start: "top 88%",
        onEnter: (batch) =>
          gsap.to(batch, {
            opacity: 1,
            y: 0,
            duration: 0.85,
            ease: "power2.out",
            stagger: 0.08,
            overwrite: true,
          }),
        onEnterBack: (batch) =>
          gsap.to(batch, {
            opacity: 1,
            y: 0,
            duration: 0.45,
            ease: "power2.out",
            overwrite: true,
          }),
      });

      /* The two full-bleed photographs, held back behind the page by a few per
         cent of their own height. They are already scaled past their frames in
         CSS, which is where the slack to move into comes from. */
      gsap.utils.toArray<HTMLElement>(".pl-hero-art, .pl-closing-art").forEach((art) => {
        gsap.fromTo(
          art,
          { yPercent: -3.5 },
          {
            yPercent: 3.5,
            ease: "none",
            scrollTrigger: {
              trigger: art.parentElement ?? art,
              start: "top bottom",
              end: "bottom top",
              scrub: true,
            },
          },
        );
      });

      /* The features section's own halftones: the plume and the horizon on the
         section, the planet on its story. Each on its own trigger, so a plate
         1,000px down the page drifts while it is being looked at rather than
         finishing before it arrives. */
      drift(".cf", "--cf-art-y", 22);
      drift(".cf-plate", "--cf-plate-y", 30);

      /* Web fonts land after hydration and move everything below them; without
         this the triggers are measured against a layout that no longer exists. */
      void document.fonts?.ready.then(() => ScrollTrigger.refresh());
    });

    mm.add("(prefers-reduced-motion: reduce)", () => {
      gsap.set("[data-reveal]", { opacity: 1, y: 0 });
    });

    return () => mm.revert();
  }, []);
}
