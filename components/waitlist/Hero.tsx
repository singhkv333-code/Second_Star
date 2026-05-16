"use client";

/**
 * Hero — the opening view of the waitlist page. Left column carries
 * the "One message. That's all investing takes." headline + copy +
 * dual CTAs; right column holds the animated phone mock on desktop
 * and a curved-edge prompt card on mobile.
 */

import { PhoneChat } from "@/components/waitlist/PhoneChat";
import { PromptCard } from "@/components/waitlist/PromptCard";

export function Hero(): React.ReactElement {
  return (
    <section className="relative overflow-hidden bg-white">
      <div className="relative mx-auto grid max-w-7xl grid-cols-1 items-center gap-10 px-5 pb-14 pt-8 sm:px-6 sm:pt-16 lg:grid-cols-[1.05fr_1fr] lg:gap-16 lg:pb-24 lg:pt-20">
        <div className="relative z-10 max-w-xl">
          <h1 className="font-serif text-[40px] leading-[1.04] tracking-[-0.03em] text-[#0d0d0e] sm:text-[56px] sm:tracking-[-0.04em] sm:leading-[1.02] lg:text-[72px]">
            One message.
            <br />
            <span className="italic text-[#8a8f96]">That&apos;s all</span>
            <br />
            investing takes.
          </h1>
          <p className="mt-5 max-w-md text-[14.5px] leading-[1.6] text-[#4d555c] sm:mt-7 sm:text-[15px] sm:leading-7">
            Tell Pivot what you want — buy, sell, set alerts, automate
            SIPs, or build strategies. It reads the market, plans the
            execution, and places the trade.
            <span className="hidden sm:inline">
              {" "}No manual monitoring, no charts, no placing orders.
            </span>
          </p>

          <div className="mt-7 flex flex-wrap items-center gap-3 sm:mt-9 sm:gap-4">
            <a
              href="#waitlist"
              className="inline-flex items-center gap-2 rounded-full bg-[#0d0d0e] px-5 py-3 text-[14px] font-medium text-white shadow-[0_10px_30px_-10px_rgba(0,0,0,0.4)] transition-opacity hover:opacity-90"
            >
              Join the Waitlist
              <ArrowRight />
            </a>
            <a
              href="#how-it-works"
              className="inline-flex items-center gap-1.5 py-3 text-[14px] font-medium text-[#0d0d0e] transition-colors hover:text-[#4d555c]"
            >
              See how it works
              <ArrowDown />
            </a>
          </div>
        </div>

        <div className="relative flex justify-center lg:justify-end">
          <div className="hidden lg:block">
            <PhoneChat />
          </div>
          <div className="block w-full lg:hidden">
            <PromptCard />
          </div>
        </div>
      </div>
    </section>
  );
}

function ArrowRight(): React.ReactElement {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M5 12h14" /><path d="m12 5 7 7-7 7" />
    </svg>
  );
}

function ArrowDown(): React.ReactElement {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M12 5v14" /><path d="m19 12-7 7-7-7" />
    </svg>
  );
}
