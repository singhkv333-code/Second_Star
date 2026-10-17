"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { ArrowLeft, ArrowRight, Instagram } from "lucide-react";
import { HalftonePlanet, PixelGrid } from "./Pixels";
import { LandingLogo } from "./LandingLogo";
import { AFFILIATE_JOIN_HREF, AFFILIATE_SETTINGS_HREF, REFERRAL_RATE_PERCENT, inr } from "./referrals";
import { readChartoToken } from "@/lib/charto-auth";
import "./affiliate.css";

/**
 * /affiliate — Pivot's referral programme. Every "Sign up" goes to the
 * product (AFFILIATE_JOIN_HREF), whose popup hands over the referral link;
 * referrals are then followed in Settings → Affiliate.
 */

// Plan prices as /pricing states them (₹ per month, billed monthly).
const PLANS = [
  { id: "pro", name: "Pro", price: 499 },
  { id: "proplus", name: "Pro+", price: 999 },
] as const;

const STEPS: { title: string; pattern: "share" | "trade" | "paid" }[] = [
  { title: "Share your link", pattern: "share" },
  { title: "They join and subscribe", pattern: "trade" },
  { title: "You earn on eligible payments", pattern: "paid" },
];

type ReferralTerms = {
  version: string; effective_date: string; rate_bps: number; duration_days: number;
  attribution_days: number; touch: "last"; eligibility: string;
  revenue_basis: string; refund_policy: string; payout_schedule: string;
  payout_method: string; payout_min_paise: number; changes: string;
};

function faqFor(rate: number, terms: ReferralTerms | null): { q: string; a: string }[] {
  return [
    { q: "How do I join?", a: "Select Sign up. Pivot opens the affiliate popup; accept the terms, sign up, and Settings → Affiliate opens with your link ready to copy. If you are not signed in, you will sign in first." },
    { q: "How does a referral count?", a: terms
      ? `A new Pivot account is credited to the most recent referral link its owner visited within ${terms.attribution_days} days. Existing accounts and self-referrals do not qualify.`
      : "A new Pivot account created after visiting your link is credited to you. If someone visits more than one referral link, the most recent visit counts. Existing accounts and self-referrals do not qualify." },
    { q: "What do I earn?", a: terms
      ? `${rate}% of ${terms.revenue_basis} for ${terms.duration_days} days after the referred signup. Refunded or disputed payments are reversed.`
      : `${rate}% of eligible subscription payments made by the people you refer. Refunded or disputed payments are reversed.` },
    { q: "Where do I see my referrals?", a: "In Settings → Affiliate: your link, signups, paying customers, commissions by status and your payout history." },
    { q: "When are payouts made?", a: terms
      ? `${terms.payout_schedule} Minimum ${inr(terms.payout_min_paise / 100)}. ${terms.payout_method}`
      : "Each commission is reviewed by the Pivot team before payout, and every transfer is recorded in your payout history." },
    { q: "Do I need an audience?", a: "No. The programme is open to every Pivot user." },
  ];
}

const LOCAL_DEMO = process.env.NODE_ENV === "development" && process.env.NEXT_PUBLIC_PIVOT_REFERRALS_DEMO === "1";

function useAffiliateScroll(): void {
  useEffect(() => {
    const html = document.documentElement;
    const body = document.body;
    const prev: [string, string, string, string] = [html.style.overflow, body.style.overflow, html.style.height, body.style.height];
    html.style.overflow = "auto";
    body.style.overflow = "auto";
    html.style.height = "auto";
    body.style.height = "auto";
    return () => { html.style.overflow = prev[0]; body.style.overflow = prev[1]; html.style.height = prev[2]; body.style.height = prev[3]; };
  }, []);
}

/** The landing page's topbar (site PivotLanding → Nav), always on its
 *  dark variant here, with "Back to Pivot" in place of "Log in". */
function AffNav(): React.ReactElement {
  const [scrolled, setScrolled] = useState(false);
  useEffect(() => {
    const onScroll = (): void => {
      const y = document.body.scrollTop || document.documentElement.scrollTop || window.scrollY || 0;
      setScrolled((was) => (was ? y > 4 : y > 32));
    };
    onScroll();
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => window.removeEventListener("scroll", onScroll);
  }, []);
  return (
    <header className={scrolled ? "aff-nav scrolled" : "aff-nav"}>
      <div className="aff-nav-inner">
        <Link className="aff-brand" href="/" aria-label="Pivot home"><LandingLogo className="aff-brand-logo" /></Link>
        <Link className="aff-nav-cta" href="/"><ArrowLeft size={14} aria-hidden="true" /> Back to Pivot</Link>
      </div>
    </header>
  );
}

/** The landing page's footer (PivotLanding → Footer): the dithered artwork,
 *  three sweeping hairlines, the gradient wordmark, links and socials. */
function AffFooter(): React.ReactElement {
  return (
    <div className="aff-closing">
      <div className="aff-closing-art" aria-hidden="true" />
      <footer className="aff-footer">
        <div className="aff-footer-lines" aria-hidden="true"><i /><i /><i /></div>
        <div className="aff-footer-inner">
          <div className="aff-footer-wordmark"><LandingLogo className="aff-footer-logo" gradient={["#d5d7d9", "#75797c"]} /></div>
          <nav className="aff-footer-links" aria-label="Company and legal">
            <a href="/about">About us</a>
            <a href="/terms">Terms & conditions</a>
            <a href="/privacy">Privacy policy</a>
            <a href="/pricing">Pricing</a>
          </nav>
          <div className="aff-footer-social">
            <a href="https://www.instagram.com/investwithpivot/" target="_blank" rel="noopener noreferrer" aria-label="Pivot on Instagram (opens in a new tab)"><Instagram size={15} /></a>
            <a href="https://x.com/investwithpivot" target="_blank" rel="noopener noreferrer" aria-label="Pivot on X (opens in a new tab)"><XGlyph /></a>
          </div>
        </div>
      </footer>
    </div>
  );
}

/** X's wordmark glyph — lucide has no current-brand X mark. */
function XGlyph(): React.ReactElement {
  return <svg viewBox="0 0 24 24" width="14" height="14" fill="currentColor" aria-hidden="true" focusable="false">
    <path d="M18.244 2.25h3.308l-7.227 8.26 8.502 11.24H16.17l-5.214-6.817L4.99 21.75H1.68l7.73-8.835L1.254 2.25H8.08l4.713 6.231zm-1.161 17.52h1.833L7.084 4.126H5.117z"/>
  </svg>;
}
/** Every "Sign up" on the page: the product, which opens the join popup
 *  (a signed-out visitor signs in first and lands on it). */
function SignUp(): React.ReactElement {
  return (
    <a className="aff-btn aff-btn-solid" href={AFFILIATE_JOIN_HREF}>
      Sign up <ArrowRight aria-hidden="true" />
    </a>
  );
}

export function AffiliatePage(): React.ReactElement {
  useEffect(() => {
    if (window.location.hash === "#referral-admin") window.location.replace("/affiliate/admin");
    if (window.location.hash === "#referral-account") window.location.replace(AFFILIATE_SETTINGS_HREF);
  }, []);
  // The published terms refine the copy when they load; the page never waits
  // on them, and the published rate stands if they are unavailable.
  const [terms, setTerms] = useState<ReferralTerms | null>(null);
  useEffect(() => {
    fetch("/referrals/terms", { cache: "no-store" })
      .then(async (r) => { if (!r.ok) throw new Error("terms unavailable"); return r.json() as Promise<ReferralTerms>; })
      .then(setTerms)
      .catch(() => undefined);
  }, []);
  // The root layout locks html/body scrolling for the app shell; this is a
  // long page, so release it while mounted (the /waitlist page does the same).
  useAffiliateScroll();
  const rate = terms ? terms.rate_bps / 100 : REFERRAL_RATE_PERCENT;

  return (
    <div className="aff">
      <AffNav />

      <main className="aff-main">
        <section className="aff-shell">
          <div className="aff-hero">
            <img className="aff-hero-art" src="/affiliate/vesuvius.jpg" alt="" aria-hidden="true" />
            <div className="aff-hero-shade" aria-hidden="true" />
            <div className="aff-grain" aria-hidden="true" />
            <div className="aff-hero-copy">
              <h1>Light the fuse.</h1>
              <p>Share Pivot with people who will use it, and earn {rate}% of every eligible subscription payment they make.</p>
              <div className="aff-actions">
                <SignUp />
                {LOCAL_DEMO && <Link className="aff-btn aff-btn-line" href="/affiliate/admin">Preview admin</Link>}
              </div>
            </div>
            <Calculator rate={rate} />
          </div>
        </section>

        <section className="aff-shell aff-programs" aria-label="Earn with Pivot">
          <ProgramCard rate={rate} terms={terms} />
        </section>

        <section className="aff-shell aff-steps" aria-label="How referrals work">
          {STEPS.map((s) => (
            <article key={s.title}>
              <PixelGrid pattern={s.pattern} />
              <h3>{s.title}</h3>
            </article>
          ))}
        </section>

        <section className="aff-shell aff-faq">
          <h2>Questions</h2>
          <FaqList items={faqFor(rate, terms)} />
        </section>

        <section className="aff-shell">
          <div className="aff-apply">
            <div className="aff-mesh" aria-hidden="true"><i /><i /><i /></div>
            <div className="aff-grain" aria-hidden="true" />
            <div className="aff-apply-side">
              <HalftonePlanet />
              <h2>Ready when you are.</h2>
            </div>
            <div className="aff-apply-cta">
              <p>Sign up and Pivot hands you your link. Every referral, commission and payout is in Settings → Affiliate.</p>
              <SignUp />
            </div>
          </div>
        </section>

        {terms && <ReferralTermsPanel terms={terms} />}
      </main>

      <AffFooter />
    </div>
  );
}

/** /affiliate/admin — the referral review desk. A referrer's own view lives
 *  in the product, under Settings → Affiliate. */
export function AffiliateDashboard(): React.ReactElement {
  useAffiliateScroll();
  return <div className="aff aff-dashboard">
    <AffNav />
    <main className="aff-main">
      <nav className="aff-shell aff-dashboard-nav" aria-label="Referral views">
        <Link href="/affiliate">Earn with Pivot</Link>
        <Link href="/affiliate/admin" aria-current="page">{LOCAL_DEMO ? "Admin preview" : "Referral review"}</Link>
      </nav>
      <ReferralAdmin demo={LOCAL_DEMO} />
    </main>
    <AffFooter />
  </div>;
}

type ReviewRow = { payment_id: string; subscriber_id: number; referrer_id: number; earned_paise: number; reversed_paise: number; status: string };
type SignupRow = {
  subscriber_id: number; subscriber_email: string; referrer_id: number; referrer_email: string;
  code: string | null; signed_up: number; status: string; paid_periods: number; earned_paise: number;
};
const DEMO_SIGNUPS: SignupRow[] = [
  { subscriber_id: 104, subscriber_email: "trader@example.com", referrer_id: 10, referrer_email: "affiliate@example.com", code: "demo-link", signed_up: 1792022400, status: "pending", paid_periods: 1, earned_paise: 12475 },
  { subscriber_id: 107, subscriber_email: "investor@example.com", referrer_id: 10, referrer_email: "affiliate@example.com", code: "demo-link", signed_up: 1792108800, status: "pending", paid_periods: 0, earned_paise: 0 },
];
const day = (t: number): string => new Date(t * 1000).toLocaleDateString("en-IN", { day: "numeric", month: "short", year: "numeric" });
const DEMO_REVIEW: ReviewRow[] = [
  { payment_id: "pay_demo_pending", subscriber_id: 104, referrer_id: 10, earned_paise: 12475, reversed_paise: 0, status: "pending" },
  { payment_id: "pay_demo_approved", subscriber_id: 105, referrer_id: 10, earned_paise: 12475, reversed_paise: 0, status: "approved" },
  { payment_id: "pay_demo_exception", subscriber_id: 106, referrer_id: 10, earned_paise: 12475, reversed_paise: 0, status: "exception" },
];

function ReferralAdmin({ demo }: { demo: boolean }): React.ReactElement | null {
  const [rows, setRows] = useState<ReviewRow[] | null>(demo ? DEMO_REVIEW : null);
  const [signups, setSignups] = useState<SignupRow[]>(demo ? DEMO_SIGNUPS : []);
  const [notice, setNotice] = useState("");
  const [reference, setReference] = useState("");
  const [reviewReason, setReviewReason] = useState("");
  const [selected, setSelected] = useState<string[]>([]);
  const [accessError, setAccessError] = useState("");
  const token = demo ? "local-demo" : readChartoToken();
  const headers: Record<string, string> = token ? { Authorization: `Bearer ${token}` } : {};
  const refresh = async (): Promise<void> => {
    if (demo) return;
    const response = await fetch("/admin/referrals", { headers, cache: "no-store" });
    if (response.ok) {
      const data = (await response.json()) as { review: ReviewRow[]; signups: SignupRow[] };
      setRows(data.review);
      setSignups(data.signups || []);
      setAccessError("");
    } else {
      setAccessError(response.status === 403 ? "This account does not have referral administrator access." : "Referral reviews are unavailable. Try again shortly.");
    }
  };
  useEffect(() => { if (token && !demo) void refresh(); }, [token, demo]);
  if (!token) return <section className="aff-shell aff-account aff-admin"><h2>Referral review</h2><p role="status">Sign in with an administrator account to view referral reviews.</p></section>;
  if (accessError) return <section className="aff-shell aff-account aff-admin"><h2>Referral review</h2><p role="alert">{accessError}</p></section>;
  if (rows === null) return <section className="aff-shell aff-account aff-admin"><h2>Referral review</h2><p role="status">Loading referral reviews…</p></section>;
  const approve = async (payment_id: string): Promise<void> => {
    if (demo) { setRows(rows.map((r) => r.payment_id === payment_id ? { ...r, status: "approved" } : r)); setNotice("Sample commission approved locally."); return; }
    const response = await fetch("/admin/referrals/approve", { method: "POST", headers: { ...headers, "Content-Type": "application/json" }, body: JSON.stringify({ payment_id }) });
    setNotice(response.ok ? "Commission approved." : "Approval failed. Review the commission status.");
    await refresh();
  };
  const payout = async (): Promise<void> => {
    const chosen = rows.filter((r) => selected.includes(r.payment_id));
    const first = chosen[0];
    if (!first || !reference.trim() || chosen.some((r) => r.referrer_id !== first.referrer_id)) {
      setNotice("Select approved commissions for one referrer and enter a transfer reference."); return;
    }
    if (demo) { setRows(rows.filter((r) => !selected.includes(r.payment_id))); setNotice("Sample payout recorded in this browser preview only."); setSelected([]); setReference(""); return; }
    const response = await fetch("/admin/referrals/payout", { method: "POST", headers: { ...headers, "Content-Type": "application/json" }, body: JSON.stringify({ referrer_id: first.referrer_id, payment_ids: selected, reference: reference.trim() }) });
    setNotice(response.ok ? "Payout recorded. Confirm the transfer in your payment records." : "Payout could not be recorded. Check eligibility and the minimum.");
    if (response.ok) { setSelected([]); setReference(""); }
    await refresh();
  };
  const resolve = async (payment_id: string, decision: "clear" | "reject"): Promise<void> => {
    if (reviewReason.trim().length < 8) { setNotice("Enter a review reason of at least eight characters."); return; }
    if (demo) { setRows(rows.map((r) => r.payment_id === payment_id ? { ...r, status: decision === "clear" ? "pending" : "rejected" } : r)); setNotice("Sample exception reviewed locally."); setReviewReason(""); return; }
    const response = await fetch("/admin/referrals/resolve", { method: "POST", headers: { ...headers, "Content-Type": "application/json" }, body: JSON.stringify({ payment_id, decision, reason: reviewReason.trim() }) });
    setNotice(response.ok ? "Exception reviewed. The decision is in the audit trail." : "This exception requires a separate payout recovery review.");
    if (response.ok) setReviewReason("");
    await refresh();
  };
  return <section className="aff-shell aff-account aff-admin" id="referral-admin" aria-label="Referral administration">
    <h2>Referral review</h2>
    {demo && <p className="aff-demo-note">Local preview · these controls change sample data in this browser only.</p>}
    <p>Commission is credited when a referred account is on an active paid plan for a new billing period, from Pivot&apos;s own billing records. Approve it, transfer the money, then record the transfer here.</p>
    {notice && <p role="status">{notice}</p>}
    <h3>Referred signups</h3>
    <div className="aff-admin-list">{signups.length ? signups.map((r) => <div key={r.subscriber_id}>
      <span>{r.subscriber_email || `Account ${r.subscriber_id}`}<small>Joined {day(r.signed_up)} through {r.referrer_email || `account ${r.referrer_id}`}{r.code ? ` · link ${r.code}` : ""}</small></span>
      <span>{r.paid_periods > 0 ? `Paid · ${r.paid_periods} period${r.paid_periods === 1 ? "" : "s"}` : "Not converted"}</span>
      <strong>{inr(r.earned_paise / 100)}</strong>
    </div>) : <p>No one has signed up through a referral link yet.</p>}</div>
    <h3>Commissions</h3>
    <div className="aff-account-link"><input aria-label="Exception review reason" placeholder="Reason for exception decision" value={reviewReason} onChange={(e) => setReviewReason(e.target.value)} /></div>
    <div className="aff-admin-list">{rows.length ? rows.map((r) => <div key={r.payment_id}>
      <input type="checkbox" aria-label={`Select ${r.payment_id}`} disabled={r.status !== "approved"} checked={selected.includes(r.payment_id)} onChange={(e) => setSelected(e.target.checked ? [...selected, r.payment_id] : selected.filter((id) => id !== r.payment_id))} />
      <span>{r.payment_id}<small>Referrer {r.referrer_id} · Customer {r.subscriber_id}</small></span>
      <span>{r.status}</span><strong>{inr((r.earned_paise-r.reversed_paise)/100)}</strong>
      {r.status === "pending" && <button type="button" className="aff-btn aff-btn-line" onClick={() => void approve(r.payment_id)}>Approve</button>}
      {r.status === "exception" && <><button type="button" className="aff-btn aff-btn-line" onClick={() => void resolve(r.payment_id, "clear")}>Clear</button><button type="button" className="aff-btn aff-btn-line" onClick={() => void resolve(r.payment_id, "reject")}>Reject</button></>}
    </div>) : <p>No commissions need review.</p>}</div>
    <div className="aff-account-link"><input aria-label="External transfer reference" placeholder="External transfer reference" value={reference} onChange={(e) => setReference(e.target.value)} /><button type="button" className="aff-btn aff-btn-solid" onClick={() => void payout()}>Record payout</button></div>
  </section>;
}

function Calculator({ rate }: { rate: number }): React.ReactElement {
  const [people, setPeople] = useState(40);
  const [plan, setPlan] = useState<(typeof PLANS)[number]["id"]>("pro");
  const price = PLANS.find((p) => p.id === plan)!.price;
  const monthly = people * price * (rate / 100);
  // The track's filled share is set after mount: a custom property in an
  // inline style serialises differently on the server and the client, and
  // React reports the mismatch on every load.
  const range = useRef<HTMLInputElement>(null);
  useEffect(() => {
    range.current?.style.setProperty("--fill", `${((people - 1) / 499) * 100}%`);
  }, [people]);
  return (
    <div className="aff-calc">
      <strong className="aff-calc-title">What you could earn at {rate}%</strong>
      <label className="aff-calc-row" htmlFor="aff-people">
        <span>Traders you bring</span><strong>{people}</strong>
      </label>
      <input ref={range} id="aff-people" type="range" min={1} max={500} value={people}
        onChange={(e) => setPeople(Number(e.target.value))} />
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
        <span>per month if all {people} subscribe and remain paid</span>
      </div>
      <p className="aff-calc-note">An illustration, not a guarantee. Commission is paid on eligible payments only.</p>
    </div>
  );
}

function ProgramCard({ rate, terms }: { rate: number; terms: ReferralTerms | null }): React.ReactElement {
  return (
    <article className="aff-card aff-card-sand">
      <div className="aff-mesh" aria-hidden="true"><i /><i /><i /></div>
      <div className="aff-grain" aria-hidden="true" />
      <div className="aff-card-body">
        <h2>Earn with Pivot</h2>
        <div className="aff-card-rate">
          <strong>{rate}%</strong>
          <span>{terms ? `commission on ${terms.revenue_basis}.` : "commission on eligible Pivot subscriptions."}</span>
        </div>
        <ul>
          <li>{terms ? terms.eligibility : "Open to every Pivot user"}</li>
          <li>Your link and referrals in Settings → Affiliate</li>
          <li>{terms ? `Reviewed payouts · minimum ${inr(terms.payout_min_paise / 100)}` : "Every commission reviewed before payout"}</li>
        </ul>
        <SignUp />
      </div>
    </article>
  );
}

function ReferralTermsPanel({ terms }: { terms: ReferralTerms }): React.ReactElement {
  return <section className="aff-shell" id="terms" aria-label="Referral programme terms">
    <div className="aff-apply aff-terms-panel">
      <div className="aff-mesh" aria-hidden="true"><i /><i /><i /></div>
      <div className="aff-grain" aria-hidden="true" />
      <div className="aff-apply-side"><HalftonePlanet /><h2>Programme terms.</h2><p>Version {terms.version} · effective {terms.effective_date}</p></div>
      <dl className="aff-form aff-terms-list">
        <div><dt>Eligibility</dt><dd>{terms.eligibility}</dd></div>
        <div><dt>Attribution</dt><dd>Last valid link visit within {terms.attribution_days} days; bound at new account signup. Self-referrals and existing accounts are excluded.</dd></div>
        <div><dt>Rate and duration</dt><dd>{terms.rate_bps / 100}% of {terms.revenue_basis} for {terms.duration_days} days after referred signup.</dd></div>
        <div><dt>Refunds and disputes</dt><dd>{terms.refund_policy}</dd></div>
        <div><dt>Payouts</dt><dd>{terms.payout_schedule} Minimum {inr(terms.payout_min_paise / 100)}. {terms.payout_method}</dd></div>
        <div><dt>Changes</dt><dd>{terms.changes}</dd></div>
      </dl>
    </div>
  </section>;
}

/** The landing page's FAQ behaviour (PivotLanding → FAQ): one answer open at
 *  a time, the two-bar glyph whose upright rotates flat so + becomes −, and
 *  the answer opening on grid rows 0fr → 1fr with a fade. */
function FaqList({ items }: { items: { q: string; a: string }[] }): React.ReactElement {
  const [open, setOpen] = useState<number | null>(0);
  return (
    <div className="aff-faq-list">{items.map(({ q, a }, i) => {
      const isOpen = open === i;
      return (
        <div className="aff-faq-item" key={q}>
          <button type="button" aria-expanded={isOpen} onClick={() => setOpen(isOpen ? null : i)}>
            <span>{q}</span>
            <span className={isOpen ? "aff-faq-glyph open" : "aff-faq-glyph"} aria-hidden="true"><i /><i /></span>
          </button>
          <div className="aff-faq-a" style={{ gridTemplateRows: isOpen ? "1fr" : "0fr", opacity: isOpen ? 1 : 0 }}>
            <div><p>{a}</p></div>
          </div>
        </div>
      );
    })}</div>
  );
}
