"use client";

/**
 * WaitlistNav — sticky full-width glass navbar. Same design + content
 * as before; adds a smooth light↔dark crossfade so the nav restyles
 * itself when scrolling into a dark section (CapabilityCanvas,
 * BuildSecurities, WaitlistFormBlock, WordmarkFooter).
 *
 * How the theme detection works:
 *   - Each dark section carries data-nav-theme="dark" on its outer
 *     element. A scroll listener checks whether any of them crosses
 *     the navbar's vertical band (top ~50px buffer). If yes, the nav
 *     gets `isDark = true` and all color tokens swap.
 *   - Backgrounds, text color, border color, and CTA pill all have a
 *     400ms transition declared, so the swap eases in lockstep.
 *
 * Contraction (aave.com mechanic):
 *   - At rest, header has 24px top + 16px bottom padding.
 *   - On scroll, the header is translateY(-24px) — the top stripe
 *     slides off-screen and the inner row sits flush at the top.
 *     A hairline divider fades in at the new bottom edge.
 *   - Logo and CTA pill keep their size (aave doesn't resize them).
 *   - Glass blur stays on through both states.
 */

import { useEffect, useState } from "react";

export function WaitlistNav(): React.ReactElement {
  const [scrolled, setScrolled] = useState(false);
  const [isDark, setIsDark] = useState(false);

  useEffect(() => {
    // Cached on first run so we're not requerying the DOM every scroll
    // tick. The waitlist page is static after mount, so this is safe.
    let darkSections: Element[] | null = null;
    const getDarkSections = (): Element[] => {
      if (darkSections === null) {
        darkSections = Array.from(
          document.querySelectorAll("[data-nav-theme='dark']"),
        );
      }
      return darkSections;
    };

    const onScroll = () => {
      // The waitlist page locks `html, body { height: 100% }` in globals.css
      // and only releases `overflow: hidden` — so the actual scroller is
      // `<body>` (not the document root), and real scroll events on
      // <body> do NOT bubble to window/document. We read scrollTop from
      // body first, then fall back for any other layout.
      const y =
        document.body.scrollTop ||
        document.documentElement.scrollTop ||
        window.scrollY ||
        0;
      // Hysteresis: contracting the navbar shrinks the page by 32px,
      // which can re-seat scrollTop right back across the trigger
      // threshold — causing the bar to oscillate (the "vibration" the
      // user sees near the boundary). Using two thresholds — enter at
      // 32, leave only below 4 — opens a dead band that absorbs the
      // layout shift and stops the feedback loop.
      setScrolled((prev) => (prev ? y > 4 : y > 32));

      // Probe at y=40 (roughly the vertical center of the navbar) —
      // ask which section is under that point. If it's a dark section,
      // flip to dark.
      const probeY = 40;
      const anyDark = getDarkSections().some((el) => {
        const r = el.getBoundingClientRect();
        return r.top < probeY && r.bottom > probeY;
      });
      setIsDark(anyDark);
    };

    onScroll();
    // Attach to body too — body-element scroll events don't bubble to
    // window/document, so listening only on those would miss every tick.
    window.addEventListener("scroll", onScroll, { passive: true });
    document.addEventListener("scroll", onScroll, { passive: true });
    document.body.addEventListener("scroll", onScroll, { passive: true });
    return () => {
      window.removeEventListener("scroll", onScroll);
      document.removeEventListener("scroll", onScroll);
      document.body.removeEventListener("scroll", onScroll);
    };
  }, []);

  // Theme-resolved color tokens. Both states keep the glass blur on —
  // only the tint, ink color, border, and pill colors swap.
  const bg = isDark ? "rgba(13,13,14,1)" : "rgba(255,255,255,0.65)";
  const ink = isDark ? "#f0ede5" : "var(--text-primary)";
  const border = scrolled
    ? isDark
      ? "1px solid rgba(255,255,255,0.10)"
      : "1px solid rgba(15,18,22,0.08)"
    : "1px solid transparent";
  const pillBg = isDark ? "#f0ede5" : "#0d0d0e";
  const pillInk = isDark ? "#0d0d0e" : "#ffffff";

  // aave.com mechanic: the header reserves a symmetric vertical
  // stripe at rest and collapses it on scroll. Top + bottom shrink
  // together so the bar stays visually centered through the
  // contraction (no top-heavy / bottom-heavy intermediate state).
  const STRIPE = 16;

  return (
    <header
      className="sticky top-0 z-50 w-full"
      style={{
        backgroundColor: bg,
        color: ink,
        backdropFilter: "saturate(180%) blur(18px)",
        WebkitBackdropFilter: "saturate(180%) blur(18px)",
        borderBottom: border,
        boxShadow: scrolled
          ? "0 6px 20px -10px rgba(0,0,0,0.10)"
          : "none",
        paddingTop: scrolled ? 0 : STRIPE,
        paddingBottom: scrolled ? 0 : STRIPE,
        transition:
          "background-color 400ms ease, color 400ms ease, border-color 400ms ease, padding-top 320ms cubic-bezier(0.22,1,0.36,1), padding-bottom 320ms cubic-bezier(0.22,1,0.36,1), box-shadow 320ms cubic-bezier(0.22,1,0.36,1)",
      }}
    >
      <div className="mx-auto flex max-w-7xl items-center justify-between px-6">
        {/* Brand — mirrors AppShell exactly. Wordmark inherits `color` */}
        <a
          href="/waitlist"
          className="flex items-center"
          style={{
            gap: 0,
            fontFamily: "var(--font-experiment)",
            fontWeight: 550,
            fontSize: 22,
            letterSpacing: "-0.02em",
            color: "inherit",
          }}
        >
          <img
            src="/pivot-light.png"
            alt="Pivot"
            width={55}
            height={55}
            className="shrink-0"
            style={{
              display: "block",
              objectFit: "contain",
              transition: "filter 400ms ease",
              // Invert the PNG when on a dark section so the dark glyph
              // becomes light. The brand wordmark is just text and rides
              // on the parent `color`, so it transitions for free.
              filter: isDark ? "invert(1) brightness(1.6)" : "none",
            }}
          />
          <span style={{ marginLeft: -2 }}>pivot</span>
        </a>

        <a
          href="#waitlist"
          className="inline-flex items-center rounded-full text-[13px] font-medium hover:opacity-90"
          style={{
            paddingLeft: 16,
            paddingRight: 16,
            paddingTop: 8,
            paddingBottom: 8,
            background: pillBg,
            color: pillInk,
            transition:
              "background-color 400ms ease, color 400ms ease, opacity 200ms ease",
          }}
        >
          Join the Waitlist
        </a>
      </div>
    </header>
  );
}
