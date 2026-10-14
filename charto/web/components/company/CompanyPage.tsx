import { SiteChrome } from "@/components/site/SiteChrome";
import {
  ADDRESS, COMPANY_PAGES, CONTACT_EMAIL, GRIEVANCE_OFFICER, LEGAL_NAME, UPDATED,
  type CompanyPageKey,
} from "./content";
import styles from "./CompanyPage.module.css";

/* Mentions of the contact address become links wherever they appear. */
function withMail(text: string): React.ReactNode {
  const parts = text.split(CONTACT_EMAIL);
  return parts.flatMap((part, i) => i === 0 ? [part] : [
    <a key={i} href={`mailto:${CONTACT_EMAIL}`}>{CONTACT_EMAIL}</a>, part,
  ]);
}

/* "Account: your name…" reads faster with its label set in bold. */
function labelled(item: string): React.ReactNode {
  const m = /^([A-Z][^:.]{1,40}):\s(.*)$/s.exec(item);
  return m ? <><strong>{m[1]}:</strong> {withMail(m[2] ?? "")}</> : withMail(item);
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
  const legal = page === "terms" || page === "privacy" || page === "refunds";
  return (
    <SiteChrome current={page} skipTo="company-content">
      <main id="company-content" className={styles.main} tabIndex={-1}>
        <h1>{content.title}</h1>
        {legal && <p className={styles.updated}>Last updated <time dateTime={UPDATED.iso}>{UPDATED.label}</time></p>}
        <p className={styles.lead}>{content.introduction}</p>
        {content.sections.map(section => (
          <section key={section.id} id={section.id} aria-labelledby={`${section.id}-title`}>
            <h2 id={`${section.id}-title`}>{section.title}</h2>
            {section.paragraphs.map(paragraph => <p key={paragraph}>{withMail(paragraph)}</p>)}
            {section.list && <ul>{section.list.map(item => <li key={item}>{labelled(item)}</li>)}</ul>}
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
    </SiteChrome>
  );
}
