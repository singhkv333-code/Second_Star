import type { Metadata } from "next";
import Link from "next/link";
import { median } from "@/components/seo/CompanyTable";
import { PublicFrame } from "@/components/seo/PublicFrame";
import styles from "@/components/seo/public.module.css";
import { getCompanies, listed } from "@/lib/seo/data";
import { crore, day, num, pct } from "@/lib/seo/format";
import { ldJson, breadcrumbs, organization, website } from "@/lib/seo/schema";
import { absoluteUrl, sectorPath } from "@/lib/seo/site";

// Rendered per request from the cached company list, never at build time:
// a deploy must not depend on the API answering during `next build`.
export const dynamic = "force-dynamic";

export const metadata: Metadata = {
  title: "Indian Stock Market Sectors: Performance, P/E & ROE by Sector | Pivot",
  description:
    "Every NSE sector with its company count, combined market cap, median P/E, median ROE and one-year return, " +
    "and the stocks in each.",
  robots: { index: true, follow: true },
  alternates: { canonical: absoluteUrl("/sectors") },
};

export default async function Page() {
  const all = await getCompanies();
  const rows = listed(all);
  const sectors = Object.entries(all?.sectors ?? {}).map(([key, label]) => {
    const in_ = rows.filter((r) => r.sector === key);
    return {
      key, label, n: in_.length,
      mcap: in_.reduce((a, r) => a + (r.mcap_cr ?? 0), 0),
      pe: median(in_.map((r) => r.pe)),
      roe: median(in_.map((r) => r.roe)),
      ret: median(in_.map((r) => r.ret_1y)),
      leader: in_[0],
    };
  }).filter((s) => s.n > 0).sort((a, b) => b.mcap - a.mcap);

  const graph = {
    "@context": "https://schema.org",
    "@graph": [organization(), website(), breadcrumbs([{ name: "Stocks", path: "/stocks" }, { name: "Sectors", path: "/sectors" }])],
  };

  return (
    <PublicFrame>
      <script type="application/ld+json" dangerouslySetInnerHTML={{ __html: ldJson(graph) }} />
      <ol className={styles.crumbs} aria-label="Breadcrumb">
        <li><Link href="/stocks">Stocks</Link></li>
        <li aria-current="page">Sectors</li>
      </ol>
      <h1 className={styles.h1}>NSE sectors</h1>
      <p className={styles.lead} style={{ marginTop: 12 }}>
        {sectors.length} sectors covering {num(rows.length, 0)} companies, largest first. Medians are across each
        sector&apos;s companies at the {day(all?.as_of?.eod_day)} close.
      </p>
      <div className={styles.tableWrap}>
        <table className={styles.table}>
          <thead>
            <tr><th scope="col">Sector</th><th scope="col">Companies</th><th scope="col">Market cap</th><th scope="col">Median P/E</th><th scope="col">Median ROE</th><th scope="col">Median 1-year return</th><th scope="col">Largest</th></tr>
          </thead>
          <tbody>
            {sectors.map((s) => (
              <tr key={s.key}>
                <td><Link href={sectorPath(s.key)}>{s.label}</Link></td>
                <td>{s.n}</td>
                <td>{crore(s.mcap, true)}</td>
                <td>{num(s.pe, 1)}</td>
                <td>{pct(s.roe)}</td>
                <td>{pct(s.ret, 1, true)}</td>
                <td>{s.leader?.name ?? "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </PublicFrame>
  );
}
