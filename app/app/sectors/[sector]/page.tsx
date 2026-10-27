import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";
import { CompanyTable, median } from "@/components/seo/CompanyTable";
import { PublicFrame } from "@/components/seo/PublicFrame";
import styles from "@/components/seo/public.module.css";
import { getCompanies, listed } from "@/lib/seo/data";
import { crore, day, num, pct } from "@/lib/seo/format";
import { ldJson, listGraph } from "@/lib/seo/schema";
import { absoluteUrl, sectorKey, sectorPath } from "@/lib/seo/site";

export const revalidate = 900;

// No pages at build time; each is rendered on its first request and then
// served from cache, regenerated in the background every `revalidate`
// seconds (incremental static regeneration).
export function generateStaticParams(): never[] {
  return [];
}

type Params = Promise<{ sector: string }>;

async function load(slug: string) {
  const all = await getCompanies();
  const key = sectorKey(slug);
  const label = all?.sectors?.[key];
  if (!label) return null;
  const rows = listed(all).filter((r) => r.sector === key);
  return { all, key, label, rows };
}

export async function generateMetadata({ params }: { params: Params }): Promise<Metadata> {
  const got = await load((await params).sector);
  if (!got) return {};
  return {
    title: `${got.label} Stocks in India: Share Prices, P/E & ROE of ${got.rows.length} NSE Companies | Pivot`,
    description:
      `All ${got.rows.length} NSE-listed ${got.label} companies ranked by market cap, with share price, one-day and ` +
      `one-year returns, P/E and ROE, and the sector's medians.`,
    robots: { index: true, follow: true },
    alternates: { canonical: absoluteUrl(sectorPath(got.key)) },
  };
}

export default async function Page({ params }: { params: Params }) {
  const got = await load((await params).sector);
  if (!got) notFound();
  const { all, key, label, rows } = got;
  const eod = day(all?.as_of?.eod_day);
  const total = rows.reduce((a, r) => a + (r.mcap_cr ?? 0), 0);
  const pe = median(rows.map((r) => r.pe));
  const roe = median(rows.map((r) => r.roe));
  const ret = median(rows.map((r) => r.ret_1y));
  const top3 = rows.slice(0, 3);
  const best = [...rows].filter((r) => r.ret_1y != null && r.rank <= 1000).sort((a, b) => b.ret_1y! - a.ret_1y!)[0];
  const others = Object.entries(all?.sectors ?? {}).filter(([k]) => k !== key);

  return (
    <PublicFrame>
      <script type="application/ld+json" dangerouslySetInnerHTML={{ __html: ldJson(listGraph(`${label} stocks`, sectorPath(key), rows, [{ name: "Stocks", path: "/stocks" }, { name: "Sectors", path: "/sectors" }, { name: label, path: sectorPath(key) }])) }} />
      <ol className={styles.crumbs} aria-label="Breadcrumb">
        <li><Link href="/stocks">Stocks</Link></li>
        <li><Link href="/sectors">Sectors</Link></li>
        <li aria-current="page">{label}</li>
      </ol>
      <h1 className={styles.h1}>{label} stocks</h1>
      <div className={styles.prose} style={{ marginTop: 12 }}>
        <p className={styles.lead}>
          {rows.length} NSE-listed {label} companies with a combined market capitalisation of {crore(total, true)} at the {eod} close.
          {top3.length > 0 && <> The largest are {top3.map((r) => r.name).join(", ")}.</>}
        </p>
        <p>
          {pe != null && <>The sector&apos;s median P/E is {num(pe, 1)}</>}
          {roe != null && <>{pe != null ? " and its" : "Its"} median return on equity {pct(roe)}</>}
          {(pe != null || roe != null) && ". "}
          {ret != null && <>The median company has returned {pct(ret, 1, true)} over the past year</>}
          {best && <>; among the 1,000 largest listed companies, {best.name} has done best in the sector at {pct(best.ret_1y, 1, true)}</>}
          {ret != null && "."}
        </p>
      </div>
      <section className={styles.section} aria-labelledby="all">
        <h2 id="all" className={styles.h2}>All {label} stocks by market cap</h2>
        <CompanyTable rows={rows} columns={["change", "ret_1y", "mcap", "pe", "roe"]} caption={`End of day ${eod}`} />
      </section>
      <section className={styles.section} aria-labelledby="other">
        <h2 id="other" className={styles.h2}>Other sectors</h2>
        <ul className={styles.chips}>
          {others.map(([k, l]) => <li key={k}><Link href={sectorPath(k)}>{l}</Link></li>)}
        </ul>
        <p className={styles.note}>Sector assignment follows each company&apos;s reported industry. This is analysis, not financial advice.</p>
      </section>
    </PublicFrame>
  );
}
