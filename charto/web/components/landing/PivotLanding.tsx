"use client";

import { useEffect, useState } from "react";
import {
  ArrowUp, Instagram,
} from "lucide-react";
import { Patterns } from "@/components/landing/v3/Patterns";
import { Workspace } from "@/components/landing/v3/Workspace";
import { Figures } from "@/components/landing/v3/Figures";
import { Honest } from "@/components/landing/v3/Honest";
import { Gallery } from "@/components/landing/v3/Gallery";
import { Tour } from "@/components/landing/v3/Tour";
import { PivotLogo } from "@/components/landing/PivotLogo";
import { logoSrc } from "@/components/landing/v3/logos";
import { Avatar, AvatarFallback, AvatarImage } from "@/components/ui/avatar";
import { useLandingScroll } from "@/components/landing/landing-scroll";
import "@/components/landing/v3/paper.css";

/** The pivotnow.in paper ground — cream, dot grain, dithered space objects
 *  (v3/paper.css). False restores the white page; nothing else changes. */
const PAPER = true;

const examples = [
  "Mark the major support and resistance levels",
  "Why did volume spike on this candle?",
  "Compare this breakout with the previous one",
  "What invalidates this setup?",
];

/** Marks stacked in the hero composer, one per market Pivot reads. */
const MARKS = ["NIFTY", "RELIANCE", "HDFCBANK", "GOLD", "BTC", "ETH"] as const;

const faqs = [
  ["What is Pivot?", "Pivot is a complete workspace for charting, market research, strategy testing and paper trading. Ask a question in plain English, inspect the evidence on the chart, and turn an idea into rules you can test."],
  ["Who is Pivot for?", "Both active traders and long-term investors. Traders can investigate setups, set alerts and test rules; investors can examine companies, filings, fundamentals and comparisons in the same workspace."],
  ["What can I ask Pivot to do on a chart?", "Explain a move, mark levels and patterns, compare setups, add indicators, check multiple timeframes at once, mark earnings and their implications on the chart, or watch for conditions. You can inspect the market data behind its drawings."],
  ["Does Pivot place real trades?", "No. Strategies can be armed and followed in a simulated paper book. Pivot does not send live orders to a broker or hold your funds."],
  ["Which markets does Pivot cover?", "Indian equities and indices, NSE options and MCX commodities, with crypto available on charts."],
  ["Is Pivot giving me investment advice?", "No. Pivot provides market data, analysis and testing frameworks. It does not give personalised buy, sell or hold recommendations. This is analysis, not financial advice."],
  ["How do I get access, and what does it cost?", <>You can start for free. Premium features are available on paid plans. <a href="/pricing">Check out the pricing.</a></>],
] as const;

function Brand({ href }: { href: string }) {
  return <a className="pl-brand" href={href} aria-label="Pivot home"><PivotLogo className="pl-brand-logo" /></a>;
}

/** The landing topbar. Off the landing page (`home="/"`) the wordmark links
 *  home; with no `[data-nav-theme='dark']` section under it, it stays light. */
export function Nav({ home = "#top" }: { home?: string } = {}) {
  const [scrolled, setScrolled] = useState(false);
  const [isDark, setIsDark] = useState(true);
  useEffect(() => {
    const darkSections = Array.from(document.querySelectorAll("[data-nav-theme='dark']"));
    const onScroll = () => {
      const y = document.body.scrollTop || document.documentElement.scrollTop || window.scrollY || 0;
      setScrolled((wasScrolled) => (wasScrolled ? y > 4 : y > 32));
      const probeY = 40;
      setIsDark(darkSections.some((section) => {
        const bounds = section.getBoundingClientRect();
        return bounds.top < probeY && bounds.bottom > probeY;
      }));
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
  return <header className={`pl-nav ${isDark ? "dark" : "light"}${scrolled ? " scrolled" : ""}`}><div className="pl-nav-inner"><Brand href={home} /><div className="pl-nav-links"><SignIn /></div></div></header>;

}

function Hero() {
  const [index, setIndex] = useState(0);
  const [typedExample, setTypedExample] = useState("");
  const [deleting, setDeleting] = useState(false);
  useEffect(() => {
    const example = examples[index] ?? examples[0]!;
    let delay = deleting ? 28 : 52;

    if (!deleting && typedExample === example) delay = 1700;
    if (deleting && typedExample.length === 0) delay = 320;

    const timer = window.setTimeout(() => {
      if (!deleting && typedExample === example) {
        setDeleting(true);
      } else if (deleting && typedExample.length === 0) {
        setDeleting(false);
        setIndex((current) => (current + 1) % examples.length);
      } else {
        setTypedExample((current) => deleting ? current.slice(0, -1) : example.slice(0, current.length + 1));
      }
    }, delay);

    return () => window.clearTimeout(timer);
  }, [deleting, index, typedExample]);
  return <section id="top" className="pl-hero" data-nav-theme="dark">
    <div className="pl-hero-art"/><div className="pl-hero-shade"/>
    <div className="pl-hero-content">
      <h1>Superintelligent Charts <em>for Trading</em></h1>
      {/* Charto's composer shape, SHOWN rather than offered: the questions
          type themselves and nothing here takes input. A live box that
          answered with two canned strings would promise answers this page
          cannot give; the real composer is one click away on the platform.
          Decorative end to end — hence aria-hidden and no focusable child. */}
      <div className="pl-hero-prompt" aria-hidden="true">
        <p className="pl-prompt-line">{typedExample}|</p>
        <div className="pl-prompt-row">
          {/* the markets one chat reads: indices, stocks, commodities, crypto */}
          <span className="pl-prompt-marks">
            {MARKS.map((s) => (
              <Avatar key={s} className="pl-av">
                <AvatarImage src={logoSrc(s)} alt="" />
                <AvatarFallback>{s.slice(0, 1)}</AvatarFallback>
              </Avatar>
            ))}
          </span>
          <span className="pl-prompt-send"><ArrowUp size={16}/></span>
        </div>
      </div>
      <div className="pl-actions"><a href="https://app.pivotnow.in/">Open Platform<ArrowUpRight /></a><a href="#demo">See how it works<ArrowDown /></a></div>
    </div>
  </section>;
}

/**
 * FAQ — the design is lifted from the production waitlist
 * (`pivot-next/components/waitlist/Sections.tsx` → FAQSection): white ground,
 * a serif heading parked in a 280px column beside the list, hairline rules
 * between rows, and the Aave-style two-bar glyph whose upright rotates 90°
 * so + becomes −. The answer opens on the grid-rows 0fr→1fr trick, which
 * animates to the content's natural height without measuring it in JS.
 * Copy stays charto's.
 */
function FAQ() {
  const [open, setOpen] = useState<number | null>(0);
  return <section id="faq" className="pl-faq2"><div className="pl-faq2-inner">
    <h2>FAQs</h2>
    <div className="pl-faq2-list">{faqs.map(([q, a], i) => {
      const isOpen = open === i;
      return <div className="pl-faq2-item" key={q}>
        <button type="button" onClick={() => setOpen(isOpen ? null : i)} aria-expanded={isOpen}>
          <span>{q}</span>
          <span className={`pl-faq2-glyph${isOpen ? " open" : ""}`} aria-hidden="true"><i/><i/></span>
        </button>
        <div className="pl-faq2-panel" style={{ gridTemplateRows: isOpen ? "1fr" : "0fr", opacity: isOpen ? 1 : 0 }}>
          <div><p>{a}</p></div>
        </div>
      </div>;
    })}</div>
  </div></section>;
}

function Footer() {
  return <>
    {/* CTA and footer are one surface: the artwork runs behind BOTH, so they
        share a wrapper and neither paints its own ground. */}
    <div className="pl-closing" data-nav-theme="dark">
    <div className="pl-closing-art" aria-hidden="true"/>
    {/* Closing CTA — the production waitlist's WaitlistFormBlock
        (`pivot-next/components/waitlist/Sections.tsx`): two serif lines, the
        second italic and dimmed. */}
    <section id="waitlist" className="pl-cta">
      <div className="pl-cta-inner" data-reveal>
        <h2>One workspace.</h2>
        <h2 className="pl-cta-italic">That&apos;s all you need.</h2>
      </div>
    </section>
    {/* Footer — the production waitlist's WordmarkFooter: near-black ground,
        three sweeping hairlines, and the oversized italic serif wordmark. No
        link columns: upstream carries none, only &nbsp; placeholders. The two
        socials survive as icon buttons. */}
    <footer className="pl-footer2">
      <div className="pl-footer2-lines" aria-hidden="true"><i/><i/><i/></div>
      <div className="pl-footer2-inner">
        <div className="pl-footer2-wordmark"><PivotLogo className="pl-footer2-logo" gradient={["#d5d7d9", "#75797c"]} /></div>
        <nav className="pl-footer2-links" aria-label="Company and legal">
          <a href="/about">About us</a>
          <a href="/terms">Terms & conditions</a>
          <a href="/privacy">Privacy policy</a>
          <a href="/pricing">Pricing</a>
          <a href="/refunds">Refunds & cancellation</a>
        </nav>
        <div className="pl-footer2-social">
          <a href="https://www.instagram.com/investwithpivot/" target="_blank" rel="noopener noreferrer" aria-label="Pivot on Instagram (opens in a new tab)"><Instagram size={15}/></a>
          <a href="https://x.com/investwithpivot" target="_blank" rel="noopener noreferrer" aria-label="Pivot on X (opens in a new tab)"><XGlyph/></a>
        </div>
      </div>
    </footer>
    </div>
  </>;
}

/** X's wordmark glyph — lucide has no current-brand X mark. */
function XGlyph(): React.ReactElement {
  return <svg viewBox="0 0 24 24" width="14" height="14" fill="currentColor" aria-hidden="true" focusable="false">
    <path d="M18.244 2.25h3.308l-7.227 8.26 8.502 11.24H16.17l-5.214-6.817L4.99 21.75H1.68l7.73-8.835L1.254 2.25H8.08l4.713 6.231zm-1.161 17.52h1.833L7.084 4.126H5.117z"/>
  </svg>;
}

export function PivotLanding(): React.ReactElement { useLandingScroll(); return <main className="pivot-landing" data-bg={PAPER ? "paper" : undefined}><Nav/><Hero/><Tour/><Figures/><Patterns/><Gallery/><Workspace/><Honest/><FAQ/><Footer/></main>; }

/* The hero's arrows say where each button goes: out to the platform (up and
   right), down the page to the demo. They move a step that way on hover. */
function ArrowUpRight() {
  return <svg className="pl-arrow pl-arrow-out" width="15" height="15" viewBox="0 0 16 16" fill="none" aria-hidden="true"><path d="M4.5 11.5 11.5 4.5M6 4.5h5.5V10" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" /></svg>;
}

function ArrowDown() {
  return <svg className="pl-arrow pl-arrow-down" width="15" height="15" viewBox="0 0 16 16" fill="none" aria-hidden="true"><path d="M8 3v10M3.8 8.8 8 13l4.2-4.2" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" /></svg>;
}

/* Sign in, as a small saucer over a night sky: it idles on a slow bob with its
   running lights cycling, and on hover it lifts, tilts and lowers a beam that
   draws a star up into it. All
   monochrome, all CSS, and still under prefers-reduced-motion. */
function SignIn() {
  return (
    <a className="pl-nav-cta pl-nav-login pl-ufo" href="https://app.pivotnow.in/login">
      <span className="pl-ufo-craft" aria-hidden="true">
        <span className="pl-ufo-beam" />
        <i className="pl-ufo-star" />
        <svg viewBox="0 0 28 18" width="24" height="16" fill="none">
          <path className="pl-ufo-dome" d="M8.6 8.2C8.9 4.9 11.2 2.6 14 2.6s5.1 2.3 5.4 5.6" fill="currentColor" fillOpacity=".18" stroke="currentColor" strokeWidth="1.4" />
          <ellipse cx="14" cy="10" rx="12" ry="3.9" fill="currentColor" />
          <ellipse cx="14" cy="8.9" rx="8.4" ry="1.4" fill="var(--ufo-ground)" fillOpacity=".55" />
          <circle className="pl-ufo-light" cx="7.4" cy="10.6" r="1" />
          <circle className="pl-ufo-light" cx="14" cy="11.4" r="1" />
          <circle className="pl-ufo-light" cx="20.6" cy="10.6" r="1" />
        </svg>
      </span>
      <span className="pl-ufo-label">Sign in</span>
    </a>
  );
}

