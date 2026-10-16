import type { Metadata } from "next";
import Link from "next/link";
import { CompanyTable } from "@/components/seo/CompanyTable";
import { PublicFrame } from "@/components/seo/PublicFrame";
import styles from "@/components/seo/public.module.css";
import { getCompanies, listed } from "@/lib/seo/data";
import { day, num } from "@/lib/seo/format";
import { LISTS } from "@/lib/seo/lists";
import { ldJson, listGraph } from "@/lib/seo/schema";
import { LETTERS, absoluteUrl, letterOf, sectorPath } from "@/lib/seo/site";

// Rendered per request from the cached company list, never at build time:
// a deploy must not depend on the API answering during `next build`.
export const dynamic = "force-dynamic";

export const metadata: Metadata = {
  title: "All NSE Stocks: Share Prices, Charts & Fundamentals A–Z | Pivot",
  description:
    "Every NSE-listed company Pivot covers, with today's share price, market cap, P/E and ROE. Browse by name, by sector, " +
    "or by today's top gainers, losers and 52-week highs.",
  robots: { index: true, follow: true },
  alternates: { canonical: absoluteUrl("/stocks") },
};

export default async function Page() {
  const all = await getCompanies();
  const rows = listed(all);
  const counts = new Map<string, number>();
  for (const r of rows) counts.set(letterOf(r.name), (counts.get(letterOf(r.name)) ?? 0) + 1);
  const sectors = Object.entries(all?.sectors ?? {})
    .map(([key, label]) => ({ key, label, n: rows.filter((r) => r.sector === key).length }))
    .filter((s) => s.n > 0)
    .sort((a, b) => b.n - a.n);
  const top = rows.slice(0, 100);

  return (
    <PublicFrame>
      <script type="application/ld+json" dangerouslySetInnerHTML={{ __html: ldJson(listGraph("All NSE stocks", "/stocks", top, [{ name: "Stocks", path: "/stocks" }])) }} />
      <h1 className={styles.h1}>All NSE stocks</h1>
      <p className={styles.lead} style={{ marginTop: 12 }}>
        {num(rows.length, 0)} NSE-listed companies with their share price, chart, financials, quarterly results and
        shareholding, free and without signing in. Prices are end-of-day closes to {day(all?.as_of?.eod_day)}.
      </p>

      <section className={styles.section} aria-labelledby="az">
        <h2 id="az" className={styles.h2}>Browse A–Z</h2>
        <ul className={styles.chips}>
          {LETTERS.filter((l) => counts.get(l)).map((l) => (
            <li key={l}><Link href={`/stocks/${l}`}>{l.toUpperCase()} <span className={styles.muted}>{counts.get(l)}</span></Link></li>
          ))}
        </ul>
      </section>

      <section className={styles.section} aria-labelledby="lists">
        <h2 id="lists" className={styles.h2}>Today&apos;s lists</h2>
        <ul className={styles.chips}>
          {LISTS.map((l) => <li key={l.slug}><Link href={`/stocks/${l.slug}`}>{l.h1}</Link></li>)}
        </ul>
      </section>

      <section className={styles.section} aria-labelledby="sectors">
        <h2 id="sectors" className={styles.h2}>By sector</h2>
        <ul className={styles.chips}>
          {sectors.map((s) => (
            <li key={s.key}><Link href={sectorPath(s.key)}>{s.label} <span className={styles.muted}>{s.n}</span></Link></li>
          ))}
        </ul>
      </section>

      <section className={styles.section} aria-labelledby="largest">
        <h2 id="largest" className={styles.h2}>The 100 largest companies</h2>
        <CompanyTable rows={top} columns={["change", "ret_1y", "mcap", "pe", "roe"]} caption={`End of day ${day(all?.as_of?.eod_day)}`} />
      </section>
    </PublicFrame>
  );
}
