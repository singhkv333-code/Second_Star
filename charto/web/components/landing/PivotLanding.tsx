"use client";

import { useEffect, useId, useState } from "react";
import {
  ArrowDown, ArrowRight, ArrowUp, Check, Instagram,
} from "lucide-react";
import { ProductFilm } from "@/components/landing/film/film-demo";
import { FeaturesSection } from "@/components/landing/features/FeaturesSection";
import { useLandingScroll } from "@/components/landing/landing-scroll";

const examples = [
  "Mark the major support and resistance levels",
  "Why did volume spike on this candle?",
  "Compare this breakout with the previous one",
  "What invalidates this setup?",
];

const faqs = [
  ["What is Pivot?", "Pivot is an analyst standing at your chart. You ask in plain English: mark the levels, why did it move, screen for this setup, watch it while I'm away. The chart becomes the answer, annotated, with the evidence behind every mark."],
  ["Does Pivot place trades for me?", "No, and that is deliberate. Pivot sizes a position, plans the entry, stop and target, and arms the alert that tells you when your condition is met. You place the order in your own broker. Nothing is ever routed for you."],
  ["Can I backtest my ideas?", "Yes. Describe a rule in plain English and Pivot runs it on historical bars, then reports win-rate, drawdown and CAGR, plus how much of the result survives once the number of variants you tried is accounted for."],
  ["What can I ask it to watch?", "Anything the chart can measure: a close through a level, a volume leg that makes a break real, an indicator crossing, or several conditions at once. Alerts are checked server-side on bar close, so they hold while the tab is shut."],
  ["What does it know besides price?", "Fundamentals and statements for the name on screen, delivery and futures positioning, bulk and block deals, peers to compare it against, and the news around a move, all beside the chart rather than in another tab."],
  ["Does it remember my work?", "Yes. Trades keep the entry, stop and target as you planned them, the note you wrote against the bar you were watching, and the chart as it looked when you took them. You can ask what you said about a chart weeks later."],
] as const;

function PivotGlyph({ className }: { className?: string }): React.ReactElement {
  const maskId = `pl-mark-${useId().replace(/[^a-zA-Z0-9_-]/g, "")}`;
  return (
    <svg className={className} viewBox="144 68 652 652" aria-hidden="true" focusable="false">
      <mask id={maskId} maskUnits="userSpaceOnUse" x="144" y="68" width="652" height="652">
        <circle cx="469.5" cy="393.5" r="326" fill="#fff" />
        <rect x="604.5" y="68" width="37" height="652" fill="#000" />
        <path d="M711.4 86.1 739.4 110.4 227.6 700.9 199.6 676.6Z" fill="#000" />
      </mask>
      <rect x="144" y="68" width="652" height="652" fill="currentColor" mask={`url(#${maskId})`} />
    </svg>
  );
}

function Brand() {
  return <a className="pl-brand" href="#top" aria-label="Pivot home"><PivotGlyph className="pl-brand-glyph" /><span>Pivot<i className="pl-brand-dot">.</i></span></a>;
}

function Nav() {
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
  return <header className={`pl-nav ${isDark ? "dark" : "light"}${scrolled ? " scrolled" : ""}`}><div className="pl-nav-inner"><Brand /><a className="pl-nav-cta" href="#waitlist">Join waitlist <ArrowRight size={14}/></a></div></header>;
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
      <h1>Talk to the<span className="pl-mobile-break"><br /></span> Charts</h1>
      {/* Charto's composer shape, SHOWN rather than offered: the questions
          type themselves and nothing here takes input. The real thing is
          behind the waitlist, so a live box that answered with two canned
          strings would be promising a product this page cannot hand over.
          Decorative end to end — hence aria-hidden and no focusable child. */}
      <div className="pl-hero-prompt" aria-hidden="true">
        <p className="pl-prompt-line">{typedExample}|</p>
        <div className="pl-prompt-row">
          <span className="pl-prompt-context">NIFTY<i>1D</i></span>
          <span className="pl-prompt-send"><ArrowUp size={16}/></span>
        </div>
      </div>
      <div className="pl-actions"><a href="#waitlist">Get early access <ArrowRight size={15}/></a><a href="#demo">See how it works <ArrowDown size={14}/></a></div>
    </div>
  </section>;
}

/**
 * The product film — a looping, scripted walkthrough of the real thing, sitting
 * directly under the hero the way TradingView puts its product shot there. The
 * choreography lives in `film/film-demo.tsx`; this is only its frame on the
 * page. It owns `id="demo"` — the hero's "See how it works" lands here. The
 * static terminal mock that used to follow it is gone: two chart-and-chat
 * demos back to back said the same thing twice, and only one of them moved.
 */
function Film() {
  return <section id="demo" className="film-section" data-nav-theme="dark"><div className="film-shell">
    <div className="film-head" data-reveal>
      <h2>Trade with <em>conviction.</em></h2>
    </div>
    <ProductFilm/>
  </div></section>;
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
    <h2 data-reveal>FAQs</h2>
    {/* Every row is its own reveal: they cross the line together, so the
        scroll layer batches them and the list unrolls rather than blinking on
        as one block. */}
    <div className="pl-faq2-list">{faqs.map(([q, a], i) => {
      const isOpen = open === i;
      return <div className="pl-faq2-item" key={q} data-reveal>
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

/* The experience bands, in the order a person grows through them. `value` is
   what a backend would store; `label` and `note` are what the card shows. */
const experienceLevels = [
  { value: "beginner", label: "Beginner", note: "Less than a year" },
  { value: "intermediate", label: "Intermediate", note: "1 to 3 years" },
  { value: "experienced", label: "Experienced", note: "3+ years" },
  { value: "professional", label: "Professional", note: "I trade for a living" },
] as const;

function Footer() {
  const [email,setEmail]=useState("");
  const [name,setName]=useState("");
  const [experience,setExperience]=useState("");
  const [style,setStyle]=useState("");
  const [joined,setJoined]=useState(false);
  const [sending,setSending]=useState(false);
  const [error,setError]=useState("");
  const canSubmit = email.trim() !== "" && name.trim() !== "" && experience !== "";

  // Same-origin by design: on Vercel this reaches the server-only route that
  // writes to Azure PostgreSQL. Database credentials never enter this client
  // component or the browser bundle.
  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!canSubmit || sending) return;
    setSending(true);
    setError("");
    try {
      const res = await fetch("/api/waitlist", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: name.trim(), email: email.trim(), experience,
                               style: style.trim() }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        // The server's own message, which names the field at fault — better
        // than a generic failure line, and it is already user-facing prose.
        setError(typeof data.error === "string" ? data.error : "Something went wrong. Please try again.");
        return;
      }
      setJoined(true);
    } catch {
      setError("Could not reach the server. Please try again.");
    } finally {
      setSending(false);
    }
  }
  return <>
    {/* CTA and footer are one surface: the artwork runs behind BOTH, so they
        share a wrapper and neither paints its own ground. */}
    <div className="pl-closing" data-nav-theme="dark">
    <div className="pl-closing-art" aria-hidden="true"/>
    {/* Closing CTA — the production waitlist's WaitlistFormBlock
        (`pivot-next/components/waitlist/Sections.tsx`): two serif lines, the
        second italic and dimmed, over a pill form on a hairline border. */}
    <section id="waitlist" className="pl-cta">
      <div className="pl-cta-inner" data-reveal>
        <h2>One message.</h2>
        <h2 className="pl-cta-italic">That&apos;s all it takes.</h2>
        {joined
          ? <p className="pl-cta-joined" role="status"><Check/> You&apos;re on the list. We&apos;ll reach out soon.</p>
          : <form onSubmit={submit} noValidate>
              {/* Three fields do not fit the one-row pill the single email
                  input wore, so the form becomes a stacked card and keeps the
                  page's language instead: translucent fills, hairline borders,
                  the same blur the pill carried, and the accent only on the
                  chosen answer and the submit. */}
              <div className="pl-field">
                <label htmlFor="pl-name">Name</label>
                <input id="pl-name" name="name" type="text" required autoComplete="name"
                  value={name} onChange={e=>setName(e.target.value)} placeholder="Your name"/>
              </div>
              <div className="pl-field">
                <label htmlFor="pl-email">Email</label>
                <input id="pl-email" name="email" type="email" required autoComplete="email"
                  value={email} onChange={e=>setEmail(e.target.value)} placeholder="your@email.com"/>
              </div>
              {/* A radiogroup rather than a <select>: four options are worth
                  showing at once, and the native dropdown is the one control
                  on this page that cannot be themed to match it. */}
              <fieldset className="pl-field pl-field-choice">
                <legend>How would you describe your trading experience?</legend>
                <div className="pl-choices">
                  {experienceLevels.map(o => (
                    <label key={o.value} className={`pl-choice${experience===o.value?" is-on":""}`}>
                      <input type="radio" name="experience" value={o.value} required
                        checked={experience===o.value}
                        onChange={()=>setExperience(o.value)}/>
                      <span className="pl-choice-label">{o.label}</span>
                      <span className="pl-choice-note">{o.note}</span>
                    </label>
                  ))}
                </div>
              </fieldset>
              {/* The only optional question on the form, and the only one whose
                  answer cannot be enumerated — which is why it is here at all.
                  The four bands above say how LONG someone has traded; this
                  says how they trade, and those are different facts. Marked
                  optional in the label rather than left to be inferred from
                  the absence of an asterisk nothing else on this form uses. */}
              <div className="pl-field">
                <label htmlFor="pl-style">
                  Share your trading experience or style
                  <span className="pl-optional">Optional</span>
                </label>
                <textarea id="pl-style" name="style" rows={3} maxLength={2000}
                  value={style} onChange={e=>setStyle(e.target.value)}
                  placeholder="What you trade, how you decide, what you wish were easier."/>
              </div>
              {error && <p className="pl-form-error" role="alert">{error}</p>}
              <button type="submit" disabled={!canSubmit || sending}>
                {sending ? "Joining…" : "Join the Waitlist"}
              </button>
            </form>}
      </div>
    </section>
    {/* Footer — the production waitlist's WordmarkFooter: near-black ground,
        three sweeping hairlines, and the oversized italic serif wordmark. No
        link columns: upstream carries none, only &nbsp; placeholders. The two
        socials survive as icon buttons. */}
    <footer className="pl-footer2">
      <div className="pl-footer2-lines" aria-hidden="true"><i/><i/><i/></div>
      <div className="pl-footer2-inner">
        <div className="pl-footer2-wordmark"><span>Pivot<i className="pl-brand-dot">.</i></span></div>
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

export function PivotLanding(): React.ReactElement { useLandingScroll(); return <main className="pivot-landing"><Nav/><Hero/><Film/><FeaturesSection/><FAQ/><Footer/></main>; }
