"use client";

/**
 * Hero — the opening view of the waitlist page. Reproduces the
 * wireframe exactly: left column carries the "One message. That's all
 * investing takes." headline + supporting copy + dual CTAs; right
 * column holds the animated phone mock messaging Pivot.
 */

import { PhoneChat } from "@/components/waitlist/PhoneChat";

export function Hero(): React.ReactElement {
  return (
    <section className="relative overflow-hidden bg-white">
      {/* Hero grid */}
      <div className="relative mx-auto grid max-w-7xl grid-cols-1 items-center gap-10 px-6 pb-16 pt-10 sm:pt-16 lg:grid-cols-[1.05fr_1fr] lg:gap-16 lg:pb-20 lg:pt-20">
        {/* Left — copy + CTAs */}
        <div className="relative z-10 max-w-xl">
          <h1 className="font-serif text-[52px] leading-[1.02] tracking-[-0.04em] text-[#0d0d0e] sm:text-[72px]">
            One message.
            <br />
            <span className="italic text-[#8a8f96]">That&apos;s all</span>
            <br />
            investing takes.
          </h1>
          <p className="mt-7 max-w-md text-[15px] leading-7 text-[#4d555c]">
            Tell Pivot what you want — buy, sell, set alerts, automate
            SIPs, or build strategies. It reads the market, plans the
            execution, and places the trade. No manual monitoring,
            no charts, no placing orders.
          </p>

          <div className="mt-9 flex flex-wrap items-center gap-4">
            <a
              href="#waitlist"
              className="inline-flex items-center gap-2 rounded-full bg-[#0d0d0e] px-5 py-3 text-[14px] font-medium text-white shadow-[0_10px_30px_-10px_rgba(0,0,0,0.4)] transition-opacity hover:opacity-90"
            >
              Get early access
              <ArrowRight />
            </a>
            <a
              href="#capabilities"
              className="inline-flex items-center gap-1.5 text-[14px] font-medium text-[#0d0d0e] transition-colors hover:text-[#4d555c]"
            >
              See how it works
              <ArrowDown />
            </a>
          </div>
        </div>

        {/* Right — phone */}
        <div className="relative flex justify-center lg:justify-end">
          <PhoneChat />
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
