import Link from "next/link";
import { COMPANY_NAME, COMPANY_PAGES, CONTACT_EMAIL, UPDATED_DATE, type CompanyPageKey } from "./content";
import styles from "./CompanyPage.module.css";

const LINKS: { key: CompanyPageKey; label: string }[] = [
  { key: "about", label: "About us" },
  { key: "terms", label: "Terms & conditions" },
  { key: "privacy", label: "Privacy policy" },
];

export function CompanyPage({ page }: { page: CompanyPageKey }): React.ReactElement {
  const content = COMPANY_PAGES[page];
  return (
    <div className={styles.page}>
      <a className={styles.skip} href="#company-content">Skip to content</a>
      <header className={styles.header}>
        <Link href="/" className={styles.brand} aria-label="Pivot home">Pivot.</Link>
        <Link href="/">Back to Pivot</Link>
      </header>
      <main id="company-content" className={styles.main} tabIndex={-1}>
        <h1>{content.title}</h1>
        {page !== "about" && <p className={styles.updated}>Last updated: <time dateTime="2026-10-11">{UPDATED_DATE}</time></p>}
        <p>{content.introduction}</p>
        {content.sections.map(section => (
          <section key={section.id} id={section.id} aria-labelledby={`${section.id}-title`}>
            <h2 id={`${section.id}-title`}>{section.title}</h2>
            {section.paragraphs.map(paragraph => <p key={paragraph}>{paragraph}</p>)}
            {page === "privacy" && section.id === "providers" && <p>You can also read the <a href="https://razorpay.com/privacy-policy/">Razorpay privacy policy</a> and the <a href="https://posthog.com/privacy">PostHog privacy policy</a>.</p>}
          </section>
        ))}
        <section aria-labelledby="contact-title">
          <h2 id="contact-title">Contact</h2>
          <p>For questions about Pivot or these pages, contact {COMPANY_NAME} at <a href={`mailto:${CONTACT_EMAIL}`}>{CONTACT_EMAIL}</a>.</p>
        </section>
      </main>
      <footer className={styles.footer}>
        <nav aria-label="Company and legal pages">
          {LINKS.map(({key, label}) => <Link key={key} href={`/${key}`} aria-current={page === key ? "page" : undefined}>{label}</Link>)}
        </nav>
        <p>© 2026 {COMPANY_NAME}</p>
      </footer>
    </div>
  );
}
