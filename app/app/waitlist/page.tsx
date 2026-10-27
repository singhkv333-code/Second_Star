"use client";

/**
 * Waitlist landing page. Public route — bypasses the AppBootstrap auth
 * gate via the PUBLIC_ROUTES list in components/AppBootstrap.tsx.
 *
 * The root layout locks `html, body { overflow: hidden }` so the
 * authenticated app can pin its topbar/sidebar. This page is a long
 * scroll, so we temporarily release that lock on mount and restore it
 * on unmount.
 */

import { useEffect } from "react";
import { Hero } from "@/components/waitlist/Hero";
import { WaitlistNav } from "@/components/waitlist/WaitlistNav";
import { CapabilityCanvas } from "@/components/waitlist/CapabilityCanvas";
import {
  BuildSecuritiesSection,
  EventTriggersSection,
  FAQSection,
  HowItWorksSection,
  WaitlistFormBlock,
  WordmarkFooter,
} from "@/components/waitlist/Sections";

export default function WaitlistPage(): React.ReactElement {
  useEffect(() => {
    const html = document.documentElement;
    const body = document.body;
    const prevHtml = html.style.overflow;
    const prevBody = body.style.overflow;
    html.style.overflow = "auto";
    body.style.overflow = "auto";
    return () => {
      html.style.overflow = prevHtml;
      body.style.overflow = prevBody;
    };
  }, []);

  return (
    <main className="min-h-screen bg-white text-[#0d0d0e]">
      <WaitlistNav />
      <Hero />
      <HowItWorksSection />
      <section id="capabilities">
        <CapabilityCanvas />
      </section>
      <BuildSecuritiesSection />
      <EventTriggersSection />
      <FAQSection />
      <section id="waitlist">
        <WaitlistFormBlock />
      </section>
      <WordmarkFooter />
    </main>
  );
}
