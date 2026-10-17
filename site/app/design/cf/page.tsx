"use client";

import { useEffect } from "react";
import { FeaturesSection } from "@/components/landing/features/FeaturesSection";

/**
 * Scratch route for reviewing the landing page's features section on its own,
 * without scrolling past a full-viewport hero and a GSAP film every time.
 * Same wrapper the real page uses (`.pivot-landing` carries the palette
 * tokens), and the reveal state is forced on so the section reads as it does
 * once scrolled into view.
 */
export default function CfPreviewPage(): React.ReactElement {
  useEffect(() => {
    document.documentElement.classList.add("pivot-landing-active");
    // `?y=1800` parks the document at a given offset so one story can be
    // reviewed at full resolution instead of inside a 5,000px contact sheet.
    const y = Number(new URLSearchParams(location.search).get("y") || 0);
    if (y > 0) window.scrollTo({ top: y, behavior: "auto" });
    return () => document.documentElement.classList.remove("pivot-landing-active");
  }, []);

  return (
    <main className="pivot-landing">
      {/* Settled state, not the entrance: this route exists to review the
          composition, and a half-faded screenshot is a review of the tween.
          The real page hands `[data-reveal]` to GSAP (`landing-scroll`), which
          is not mounted here — so the blocks would sit at the stylesheet's
          resting opacity 0 forever without this. */}
      <style>{`[data-reveal]{opacity:1!important;transform:none!important}.cf-band{transition:none!important}`}</style>
      <FeaturesSection />
    </main>
  );
}
