import Link from "next/link";
import {
  ADDRESS, COMPANY_NAME, COMPANY_PAGES, CONTACT_EMAIL, GRIEVANCE_OFFICER, LEGAL_NAME, UPDATED,
  type CompanyPageKey,
} from "./content";
import styles from "./CompanyPage.module.css";

const LINKS: { key: CompanyPageKey; label: string }[] = [
  { key: "about", label: "About" },
  { key: "terms", label: "Terms of service" },
  { key: "privacy", label: "Privacy policy" },
  { key: "refunds", label: "Refunds and cancellation" },
  { key: "contact", label: "Contact" },
];

/* Mentions of the contact address become links wherever they appear. */
function withMail(text: string): React.ReactNode {
  const parts = text.split(CONTACT_EMAIL);
  return parts.flatMap((part, i) => i === 0 ? [part] : [
    <a key={i} href={`mailto:${CONTACT_EMAIL}`}>{CONTACT_EMAIL}</a>, part,
  ]);
}

/* A page's named phrases ("Refund Policy") become links to their pages. */
function withLinks(text: string, links: [string, string][] = []): React.ReactNode {
  const hit = links.find(([phrase]) => text.includes(phrase));
  if (!hit) return withMail(text);
  const [phrase, href] = hit;
  const rest = links.filter(l => l !== hit);
  return text.split(phrase).flatMap((part, i) => i === 0 ? [withLinks(part, rest)] : [
    <Link key={`${href}-${i}`} href={href}>{phrase}</Link>, withLinks(part, rest),
  ]);
}

/* "Account: your name…" reads faster with its label set in bold. */
function labelled(item: string, links?: [string, string][]): React.ReactNode {
  const m = /^([A-Z][^:.]{1,40}):\s(.*)$/s.exec(item);
  return m ? <><strong>{m[1]}:</strong> {withLinks(m[2] ?? "", links)}</> : withLinks(item, links);
}

const PROVIDER_POLICIES: [string, string][] = [
  ["Microsoft", "https://privacy.microsoft.com/privacystatement"],
  ["Vercel", "https://vercel.com/legal/privacy-policy"],
  ["Hostinger", "https://www.hostinger.com/legal/privacy-policy"],
  ["Razorpay", "https://razorpay.com/privacy/"],
  ["PostHog", "https://posthog.com/privacy"],
  ["Google", "https://policies.google.com/privacy"],
  ["Brave", "https://search.brave.com/help/privacy-policy"],
];

export function CompanyPage({ page }: { page: CompanyPageKey }): React.ReactElement {
  const content = COMPANY_PAGES[page];
  const legal = page !== "about" && page !== "contact";
  return (
    <div className={styles.page}>
      <a className={styles.skip} href="#company-content">Skip to content</a>
      <header className={styles.header}>
        <Link href="/" className={styles.brand} aria-label="Pivot home">Pivot.</Link>
        <Link href="/">Back to Pivot</Link>
      </header>
      <main id="company-content" className={styles.main} tabIndex={-1}>
        <h1>{content.title}</h1>
        {legal && <p className={styles.updated}>Last updated <time dateTime={UPDATED.iso}>{UPDATED.label}</time></p>}
        <p className={styles.lead}>{content.introduction}</p>
        {content.sections.map(section => (
          <section key={section.id} id={section.id} aria-labelledby={`${section.id}-title`}>
            <h2 id={`${section.id}-title`}>{section.title}</h2>
            {section.paragraphs.map(paragraph => <p key={paragraph}>{withLinks(paragraph, content.links)}</p>)}
            {section.list && <ul>{section.list.map(item => <li key={item}>{labelled(item, content.links)}</li>)}</ul>}
            {page === "privacy" && section.id === "share" && (
              <p>
                Their own privacy policies:{" "}
                {PROVIDER_POLICIES.map(([name, url], i) => (
                  <span key={name}>{i > 0 && ", "}<a href={url} rel="noreferrer">{name}</a></span>
                ))}.
              </p>
            )}
          </section>
        ))}
        {content.related && (
          <section aria-labelledby="related-title" className={styles.related}>
            <h2 id="related-title">Refunds and cancellation</h2>
            <ul>
              {content.related.map(([label, href]) => <li key={href}><Link href={href}>{label}</Link></li>)}
            </ul>
          </section>
        )}
        <section aria-labelledby="who-title" className={styles.who}>
          <h2 id="who-title">{page === "contact" ? "Company details" : "Contact"}</h2>
          <p>
            {LEGAL_NAME}{ADDRESS ? `, ${ADDRESS}` : ""}. Email <a href={`mailto:${CONTACT_EMAIL}`}>{CONTACT_EMAIL}</a>.
          </p>
          <p>
            {GRIEVANCE_OFFICER.title}{GRIEVANCE_OFFICER.name ? `: ${GRIEVANCE_OFFICER.name}` : ""},{" "}
            <a href={`mailto:${GRIEVANCE_OFFICER.email}?subject=Grievance`}>{GRIEVANCE_OFFICER.email}</a>.
            {" "}We acknowledge complaints within 48 hours and aim to resolve them within one month.
          </p>
        </section>
      </main>
      <footer className={styles.footer}>
        <nav aria-label="Company and legal pages">
          {LINKS.map(({ key, label }) => (
            <Link key={key} href={`/${key}`} aria-current={page === key ? "page" : undefined}>{label}</Link>
          ))}
        </nav>
        <p>© 2026 {COMPANY_NAME}. Pivot provides analysis and tools, not investment advice.</p>
      </footer>
    </div>
  );
}
