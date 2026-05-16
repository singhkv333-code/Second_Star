"use client";

/**
 * WaitlistNav — sticky full-width glass navbar with light↔dark crossfade
 * when scrolling into a dark section (CapabilityCanvas, BuildSecurities,
 * WaitlistFormBlock, WordmarkFooter). Each dark section carries
 * data-nav-theme="dark"; we detect by probing which section sits under
 * the navbar band.
 */

import { useEffect, useState } from "react";
import Link from "next/link";

export function WaitlistNav(): React.ReactElement {
  const [scrolled, setScrolled] = useState(false);
  const [isDark, setIsDark] = useState(false);
  // SSR-safe: start with the desktop value to match server render, then
  // narrow to the mobile value after mount based on the actual viewport.
  const [stripe, setStripe] = useState(16);

  useEffect(() => {
    const updateStripe = () => setStripe(window.innerWidth < 640 ? 10 : 16);
    updateStripe();
    window.addEventListener("resize", updateStripe);
    return () => window.removeEventListener("resize", updateStripe);
  }, []);

  useEffect(() => {
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
      const y =
        document.body.scrollTop ||
        document.documentElement.scrollTop ||
        window.scrollY ||
        0;
      // Hysteresis on the contraction so the bar doesn't oscillate
      // when the contraction shifts page height back across threshold.
      setScrolled((prev) => (prev ? y > 4 : y > 32));

      const probeY = 40;
      const anyDark = getDarkSections().some((el) => {
        const r = el.getBoundingClientRect();
        return r.top < probeY && r.bottom > probeY;
      });
      setIsDark(anyDark);
    };

    onScroll();
    window.addEventListener("scroll", onScroll, { passive: true });
    document.addEventListener("scroll", onScroll, { passive: true });
    document.body.addEventListener("scroll", onScroll, { passive: true });
    return () => {
      window.removeEventListener("scroll", onScroll);
      document.removeEventListener("scroll", onScroll);
      document.body.removeEventListener("scroll", onScroll);
    };
  }, []);

  const bg = isDark ? "rgba(13,13,14,1)" : "rgba(255,255,255,0.65)";
  const ink = isDark ? "#f0ede5" : "var(--text-primary)";
  const border = scrolled
    ? isDark
      ? "1px solid rgba(255,255,255,0.10)"
      : "1px solid rgba(15,18,22,0.08)"
    : "1px solid transparent";
  const pillBg = isDark ? "#f0ede5" : "#0d0d0e";
  const pillInk = isDark ? "#0d0d0e" : "#ffffff";

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
        paddingTop: scrolled ? 0 : stripe,
        paddingBottom: scrolled ? 0 : stripe,
        transition:
          "background-color 400ms ease, color 400ms ease, border-color 400ms ease, padding-top 320ms cubic-bezier(0.22,1,0.36,1), padding-bottom 320ms cubic-bezier(0.22,1,0.36,1), box-shadow 320ms cubic-bezier(0.22,1,0.36,1)",
      }}
    >
      <div className="mx-auto flex max-w-7xl items-center justify-between px-4 sm:px-6">
        <Link
          href="/"
          className="flex items-center text-[18px] sm:text-[22px]"
          style={{
            gap: 0,
            fontFamily: "var(--font-experiment)",
            fontWeight: 550,
            letterSpacing: "-0.02em",
            color: "inherit",
          }}
        >
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img
            src="/pivot-light.png"
            alt="Pivot"
            width={55}
            height={55}
            className="h-10 w-10 shrink-0 sm:h-[55px] sm:w-[55px]"
            style={{
              display: "block",
              objectFit: "contain",
              transition: "filter 400ms ease",
              filter: isDark ? "invert(1) brightness(1.6)" : "none",
            }}
          />
          <span style={{ marginLeft: -2 }}>pivot</span>
        </Link>

        <a
          href="#waitlist"
          className="inline-flex items-center rounded-full text-[12.5px] font-medium hover:opacity-90 sm:text-[13px]"
          style={{
            paddingLeft: 14,
            paddingRight: 14,
            paddingTop: 7,
            paddingBottom: 7,
            background: pillBg,
            color: pillInk,
            transition:
              "background-color 400ms ease, color 400ms ease, opacity 200ms ease",
          }}
        >
          <span className="sm:hidden">Join Waitlist</span>
          <span className="hidden sm:inline">Join the Waitlist</span>
        </a>
      </div>
    </header>
  );
}
