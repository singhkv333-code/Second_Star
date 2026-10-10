"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { ArrowLeft, ArrowRight, Minus, Plus } from "lucide-react";
import { submitBugReport } from "@/lib/api";
import { isError } from "@/lib/types";
import "./affiliate.css";

/**
 * /affiliate — the partner programs, as a page of its own (reached from the
 * account menu, outside the app shell, like /pricing).
 *
 * Two programs only, for now: Referral (anyone with an account) and
 * Ambassador (people with an audience). Every term the page states lives in
 * PROGRAMS, so the numbers are edited in one place. Applications go to the
 * existing /feedback inbox — there is no partner backend yet, so the page
 * promises a reply, not a dashboard.
 */

type ProgramId = "referral" | "ambassador";

const PROGRAMS: Record<ProgramId, {
  name: string; rate: number; headline: string; line: string; terms: string[];
}> = {
  referral: {
    name: "Referral",
    rate: 0.25,
    headline: "25%",
    line: "of every payment a friend makes, for life.",
    terms: ["Anyone with a Pivot account", "90-day link window", "Paid monthly"],
  },
  ambassador: {
    name: "Ambassador",
    rate: 0.4,
    headline: "40%",
    line: "for the first year, then 25% for life.",
    terms: ["Creators, educators, trading communities", "Pro+ on us", "First look at new tools"],
  },
};

// Plan prices as /pricing states them (₹ per month, billed monthly).
const PLANS = [
  { id: "pro", name: "Pro", price: 499 },
  { id: "proplus", name: "Pro+", price: 999 },
] as const;

const STEPS: { title: string; art: string }[] = [
  {
    title: "Share your link",
    art: String.raw`  *
   \.
    '~~~~~~~~~~~~~.
                  |`,
  },
  {
    title: "They trade on Pivot",
    art: String.raw`     /\
    |  |
    |  |
   /|/\|\
     **
     *`,
  },
  {
    title: "You get paid, monthly",
    art: String.raw`   \  |  /
 -- ( * ) --
   /  |  \
     ' '`,
  },
];

const FAQ: { q: string; a: string }[] = [
  { q: "Which program is for me?", a: "Referral is for anyone who uses Pivot. Ambassador is for people who teach or lead a trading audience." },
  { q: "How is a referral counted?", a: "Someone signs up within 90 days of opening your link. They stay yours for as long as they pay." },
  { q: "When am I paid?", a: "Monthly, in INR, for payments that cleared the month before." },
  { q: "Can I be in both?", a: "An ambassador earns the ambassador rate on everyone they bring. One program per person." },
];

const HERO_ART = String.raw`        .            *
     _______
  ,-'       '-.        .
 /    .---.    \
=====(     )=========
 \    '---'    /
  '-._______.-'     *`;

const TNT_ART = String.raw`         *
        /
   ____/
  |    |
 _|____|_
| |  |  | |
| |  |  | |
|_|__|__|_|`;

const inr = (n: number): string => "₹" + Math.round(n).toLocaleString("en-IN");

export function AffiliatePage(): React.ReactElement {
  // The root layout locks html/body scrolling for the app shell; this is a
  // long page, so release it while mounted (the /waitlist page does the same).
  useEffect(() => {
    const html = document.documentElement;
    const body = document.body;
    const prev: [string, string] = [html.style.overflow, body.style.overflow];
    html.style.overflow = "auto";
    body.style.overflow = "auto";
    return () => { html.style.overflow = prev[0]; body.style.overflow = prev[1]; };
  }, []);

  const [program, setProgram] = useState<ProgramId>("referral");
  const apply = (p: ProgramId): void => {
    setProgram(p);
    document.getElementById("apply")?.scrollIntoView({ behavior: "smooth", block: "start" });
  };

  return (
    <div className="aff">
      <header className="aff-nav aff-shell">
        <Link href="/" className="aff-brand">Pivot.</Link>
        <Link href="/" className="aff-back"><ArrowLeft aria-hidden="true" /> Back to Pivot</Link>
      </header>

      <main>
        <section className="aff-shell">
          <div className="aff-hero">
            <img className="aff-hero-art" src="/affiliate/vesuvius.jpg" alt="" aria-hidden="true" />
            <div className="aff-hero-shade" aria-hidden="true" />
            <div className="aff-grain" aria-hidden="true" />
            <div className="aff-hero-copy">
              <pre className="aff-ascii aff-hero-ascii" aria-hidden="true">{HERO_ART}</pre>
              <h1>Light the fuse.</h1>
              <p>Bring traders to Pivot and earn on every payment they make.</p>
              <div className="aff-actions">
                <button type="button" className="aff-btn aff-btn-solid" onClick={() => apply("referral")}>
                  Join referral <ArrowRight aria-hidden="true" />
                </button>
                <button type="button" className="aff-btn aff-btn-line" onClick={() => apply("ambassador")}>
                  Become an ambassador
                </button>
              </div>
            </div>
            <Calculator program={program} onProgram={setProgram} />
            <span className="aff-credit">Joseph Wright of Derby, Vesuvius in Eruption, c. 1776–80</span>
          </div>
        </section>

        <section className="aff-shell aff-programs" aria-label="Programs">
          <ProgramCard id="referral" variant="sand" onApply={apply} />
          <ProgramCard id="ambassador" variant="planets" onApply={apply} />
        </section>

        <section className="aff-shell aff-steps" aria-label="How it works">
          {STEPS.map((s, i) => (
            <article key={s.title}>
              <span className="aff-step-n">0{i + 1}</span>
              <pre className="aff-ascii" aria-hidden="true">{s.art}</pre>
              <h3>{s.title}</h3>
            </article>
          ))}
        </section>

        <section className="aff-shell aff-faq">
          <h2>Questions</h2>
          <div>{FAQ.map((f) => <FaqItem key={f.q} {...f} />)}</div>
        </section>

        <section className="aff-shell" id="apply">
          <div className="aff-apply">
            <div className="aff-mesh" aria-hidden="true"><i /><i /><i /></div>
            <div className="aff-grain" aria-hidden="true" />
            <div className="aff-apply-side">
              <pre className="aff-ascii aff-tnt" aria-hidden="true">{TNT_ART}</pre>
              <h2>Ready when you are.</h2>
            </div>
            <ApplyForm program={program} onProgram={setProgram} />
          </div>
        </section>
      </main>

      <footer className="aff-shell aff-foot">
        <span className="aff-brand">Pivot.</span>
        <span>Data and analysis, not financial advice.</span>
      </footer>
    </div>
  );
}

function Calculator({ program, onProgram }: {
  program: ProgramId; onProgram: (p: ProgramId) => void;
}): React.ReactElement {
  const [people, setPeople] = useState(40);
  const [plan, setPlan] = useState<(typeof PLANS)[number]["id"]>("pro");
  const price = PLANS.find((p) => p.id === plan)!.price;
  const monthly = people * price * PROGRAMS[program].rate;
  return (
    <div className="aff-calc">
      <div className="aff-seg" role="tablist" aria-label="Program">
        {(Object.keys(PROGRAMS) as ProgramId[]).map((id) => (
          <button key={id} type="button" role="tab" aria-selected={program === id} onClick={() => onProgram(id)}>
            {PROGRAMS[id].name}
          </button>
        ))}
      </div>
      <label className="aff-calc-row" htmlFor="aff-people">
        <span>Traders you bring</span><strong>{people}</strong>
      </label>
      <input id="aff-people" type="range" min={1} max={500} value={people}
        onChange={(e) => setPeople(Number(e.target.value))}
        style={{ "--fill": `${((people - 1) / 499) * 100}%` } as React.CSSProperties} />
      <div className="aff-calc-row">
        <span>On</span>
        <div className="aff-seg aff-seg-small" role="tablist" aria-label="Plan">
          {PLANS.map((p) => (
            <button key={p.id} type="button" role="tab" aria-selected={plan === p.id} onClick={() => setPlan(p.id)}>
              {p.name} {inr(p.price)}
            </button>
          ))}
        </div>
      </div>
      <div className="aff-calc-out">
        <strong>{inr(monthly)}</strong>
        <span>a month · {inr(monthly * 12)} a year</span>
      </div>
    </div>
  );
}

function ProgramCard({ id, variant, onApply }: {
  id: ProgramId; variant: "sand" | "planets"; onApply: (p: ProgramId) => void;
}): React.ReactElement {
  const p = PROGRAMS[id];
  return (
    <article className={`aff-card aff-card-${variant}`}>
      {variant === "planets" ? <img className="aff-card-art" src="/affiliate/planets.png" alt="" aria-hidden="true" /> : <div className="aff-mesh" aria-hidden="true"><i /><i /><i /></div>}
      <div className="aff-grain" aria-hidden="true" />
      <div className="aff-card-body">
        <h2>{p.name}</h2>
        <div className="aff-card-rate">
          <strong>{p.headline}</strong>
          <span>{p.line}</span>
        </div>
        <ul>{p.terms.map((t) => <li key={t}>{t}</li>)}</ul>
        <button type="button" className="aff-btn aff-btn-solid" onClick={() => onApply(id)}>
          {id === "referral" ? "Join referral" : "Apply"} <ArrowRight aria-hidden="true" />
        </button>
      </div>
    </article>
  );
}

function FaqItem({ q, a }: { q: string; a: string }): React.ReactElement {
  const [open, setOpen] = useState(false);
  return (
    <article className={open ? "aff-faq-item open" : "aff-faq-item"}>
      <button type="button" aria-expanded={open} onClick={() => setOpen(!open)}>
        <span>{q}</span>{open ? <Minus aria-hidden="true" /> : <Plus aria-hidden="true" />}
      </button>
      <div className="aff-faq-a"><div><p>{a}</p></div></div>
    </article>
  );
}

function ApplyForm({ program, onProgram }: {
  program: ProgramId; onProgram: (p: ProgramId) => void;
}): React.ReactElement {
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [where, setWhere] = useState("");
  const [state, setState] = useState<"idle" | "sending" | "sent" | "error">("idle");
  const valid = name.trim().length > 1 && /\S+@\S+\.\S+/.test(email);

  const submit = async (e: React.FormEvent): Promise<void> => {
    e.preventDefault();
    if (!valid || state === "sending") return;
    setState("sending");
    const r = await submitBugReport({
      category: "other",
      severity: "normal",
      title: `Partner application · ${PROGRAMS[program].name} · ${name.trim()}`.slice(0, 160),
      description: [
        `Program: ${PROGRAMS[program].name}`,
        `Name: ${name.trim()}`,
        `Email: ${email.trim()}`,
        `Where they share: ${where.trim() || "—"}`,
      ].join("\n"),
      email: email.trim(),
      context: { page: typeof window !== "undefined" ? window.location.href : "/affiliate" },
    });
    setState(isError(r) ? "error" : "sent");
  };

  if (state === "sent") {
    return (
      <div className="aff-form aff-sent" role="status">
        <strong>Received.</strong>
        <span>We will reply to {email.trim()}.</span>
      </div>
    );
  }
  return (
    <form className="aff-form" onSubmit={submit}>
      <div className="aff-seg" role="radiogroup" aria-label="Program">
        {(Object.keys(PROGRAMS) as ProgramId[]).map((id) => (
          <button key={id} type="button" role="radio" aria-checked={program === id} onClick={() => onProgram(id)}>
            {PROGRAMS[id].name}
          </button>
        ))}
      </div>
      <input aria-label="Name" placeholder="Name" value={name} onChange={(e) => setName(e.target.value)} autoComplete="name" />
      <input aria-label="Email" placeholder="Email" type="email" value={email} onChange={(e) => setEmail(e.target.value)} autoComplete="email" />
      {program === "ambassador" ? (
        <input aria-label="Where you share" placeholder="Your channel, page or community" value={where} onChange={(e) => setWhere(e.target.value)} />
      ) : null}
      <button type="submit" className="aff-btn aff-btn-solid" disabled={!valid || state === "sending"}>
        {state === "sending" ? "Sending…" : program === "ambassador" ? "Apply" : "Join referral"} <ArrowRight aria-hidden="true" />
      </button>
      {state === "error" ? <span className="aff-error" role="alert">That did not go through. Try again in a moment.</span> : null}
    </form>
  );
}
