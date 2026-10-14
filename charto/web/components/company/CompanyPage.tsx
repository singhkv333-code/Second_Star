import { SiteChrome } from "@/components/site/SiteChrome";
import { COMPANY_NAME, COMPANY_PAGES, CONTACT_EMAIL, UPDATED_DATE, type CompanyPageKey } from "./content";
import styles from "./CompanyPage.module.css";

export function CompanyPage({ page }: { page: CompanyPageKey }): React.ReactElement {
  const content = COMPANY_PAGES[page];
  return (
    <SiteChrome current={page} skipTo="company-content">
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
    </SiteChrome>
  );
}
