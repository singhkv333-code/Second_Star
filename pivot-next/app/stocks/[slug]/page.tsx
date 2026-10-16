import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";
import { CompanyTable } from "@/components/seo/CompanyTable";
import { PublicFrame } from "@/components/seo/PublicFrame";
import styles from "@/components/seo/public.module.css";
import { getCompanies, listed } from "@/lib/seo/data";
import { day } from "@/lib/seo/format";
import { LISTS, listBySlug } from "@/lib/seo/lists";
import { ldJson, listGraph } from "@/lib/seo/schema";
import { LETTERS, absoluteUrl, letterOf, stockPath } from "@/lib/seo/site";

/** `/stocks/a` … `/stocks/z`, `/stocks/0-9`: the directory by first letter.
 *  `/stocks/top-gainers` and the other named lists (lib/seo/lists.ts). */

export const revalidate = 900;

type Params = Promise<{ slug: string }>;

export async function generateMetadata({ params }: { params: Params }): Promise<Metadata> {
  const { slug } = await params;
  const list = listBySlug(slug);
  if (list) {
    const all = await getCompanies();
    return {
      title: `${list.title(day(all?.as_of?.eod_day))} | Pivot`,
      description: `${list.intro} Updated every trading day with price, market cap and valuation.`,
      robots: { index: true, follow: true },
    alternates: { canonical: absoluteUrl(`/stocks/${slug}`) },
    };
  }
  if (!LETTERS.includes(slug)) return {};
  const L = slug.toUpperCase();
  return {
    title: `NSE Stocks Starting With ${L}: Share Prices & Fundamentals | Pivot`,
    description: `Every NSE-listed company whose name starts with ${L}, with share price, market cap and a link to its chart, financials and shareholding.`,
    robots: { index: true, follow: true },
    alternates: { canonical: absoluteUrl(`/stocks/${slug}`) },
  };
}

export default async function Page({ params }: { params: Params }) {
  const { slug } = await params;
  const list = listBySlug(slug);
  if (!list && !LETTERS.includes(slug)) notFound();
  const all = await getCompanies();
  const rows = listed(all);
  const eod = day(all?.as_of?.eod_day);

  if (list) {
    const picked = list.pick(rows);
    return (
      <PublicFrame>
        <script type="application/ld+json" dangerouslySetInnerHTML={{ __html: ldJson(listGraph(list.h1, `/stocks/${slug}`, picked, [{ name: "Stocks", path: "/stocks" }, { name: list.h1, path: `/stocks/${slug}` }])) }} />
        <ol className={styles.crumbs} aria-label="Breadcrumb">
          <li><Link href="/stocks">Stocks</Link></li>
          <li aria-current="page">{list.h1}</li>
        </ol>
        <h1 className={styles.h1}>{list.h1}</h1>
        <p className={styles.lead} style={{ marginTop: 12 }}>{list.intro} As of the {eod} close.</p>
        {picked.length ? (
          <CompanyTable rows={picked} columns={list.columns} caption={`End of day ${eod} · ${picked.length} companies`} />
        ) : (
          <p className={styles.muted}>No company meets this list&apos;s rule at the {eod} close.</p>
        )}
        <section className={styles.section} aria-labelledby="more">
          <h2 id="more" className={styles.h2}>More lists</h2>
          <ul className={styles.chips}>
            {LISTS.map((l) => (
              <li key={l.slug}><Link href={`/stocks/${l.slug}`} aria-current={l.slug === slug ? "page" : undefined}>{l.h1}</Link></li>
            ))}
          </ul>
          <p className={styles.note}>Rankings are arithmetic on end-of-day prices and reported numbers. This is analysis, not financial advice.</p>
        </section>
      </PublicFrame>
    );
  }

  const names = rows.filter((r) => letterOf(r.name) === slug).sort((a, b) => a.name.localeCompare(b.name));
  const L = slug.toUpperCase();
  return (
    <PublicFrame>
      <script type="application/ld+json" dangerouslySetInnerHTML={{ __html: ldJson(listGraph(`NSE stocks starting with ${L}`, `/stocks/${slug}`, names, [{ name: "Stocks", path: "/stocks" }, { name: L, path: `/stocks/${slug}` }])) }} />
      <ol className={styles.crumbs} aria-label="Breadcrumb">
        <li><Link href="/stocks">Stocks</Link></li>
        <li aria-current="page">{L}</li>
      </ol>
      <h1 className={styles.h1}>NSE stocks starting with {L}</h1>
      <p className={styles.lead} style={{ marginTop: 12 }}>{names.length} companies. Prices are the {eod} close.</p>
      <ul className={styles.chips} style={{ marginBottom: 28 }}>
        {LETTERS.map((l) => (
          <li key={l}><Link href={`/stocks/${l}`} aria-current={l === slug ? "page" : undefined}>{l.toUpperCase()}</Link></li>
        ))}
      </ul>
      <ul className={styles.cols}>
        {names.map((r) => (
          <li key={r.symbol}><Link href={stockPath(r.symbol)}>{r.name}</Link><span>{r.symbol}</span></li>
        ))}
      </ul>
    </PublicFrame>
  );
}
