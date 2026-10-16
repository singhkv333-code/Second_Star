/** A table of companies with their end-of-day numbers, each name a link to
 *  its stock page. Used by the directory, the sectors and the daily lists. */

import Link from "next/link";
import type { MarketRow } from "@/lib/seo/data";
import { crore, inr, num, pct } from "@/lib/seo/format";
import { stockPath } from "@/lib/seo/site";
import styles from "./public.module.css";

export type Column = "change" | "ret_1y" | "mcap" | "pe" | "roe" | "dist_high" | "dist_low";

const HEAD: Record<Column, string> = {
  change: "1 day", ret_1y: "1 year", mcap: "Market cap", pe: "P/E", roe: "ROE",
  dist_high: "From 52w high", dist_low: "From 52w low",
};

function Signed({ v, digits = 1 }: { v: number | null; digits?: number }): React.ReactElement {
  if (v == null) return <>—</>;
  return <span className={v >= 0 ? styles.up : styles.down}>{pct(v, digits, true)}</span>;
}

function cell(r: MarketRow, c: Column): React.ReactNode {
  switch (c) {
    case "change": return <Signed v={r.change} digits={2} />;
    case "ret_1y": return <Signed v={r.ret_1y} />;
    case "mcap": return crore(r.mcap_cr);
    case "pe": return num(r.pe, 1);
    case "roe": return pct(r.roe);
    case "dist_high": return <Signed v={r.dist_high} />;
    case "dist_low": return <Signed v={r.dist_low} />;
  }
}

export function CompanyTable({
  rows, columns, caption, numbered = true,
}: {
  rows: MarketRow[];
  columns: Column[];
  caption?: string;
  numbered?: boolean;
}): React.ReactElement {
  return (
    <div className={styles.tableWrap}>
      <table className={styles.table}>
        {caption && <caption>{caption}</caption>}
        <thead>
          <tr>
            <th scope="col">Company</th>
            <th scope="col">Price</th>
            {columns.map((c) => <th key={c} scope="col">{HEAD[c]}</th>)}
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={r.symbol}>
              <td>
                {numbered && <span aria-hidden style={{ color: "#9aa1a8", marginRight: 8 }}>{i + 1}</span>}
                <Link href={stockPath(r.symbol)}>{r.name}</Link>
                <span className={styles.name}>{r.symbol}</span>
              </td>
              <td>{inr(r.price)}</td>
              {columns.map((c) => <td key={c}>{cell(r, c)}</td>)}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** "1,23,456" companies, by sector, with medians: the numbers a sector page
 *  leads with. */
export function median(xs: (number | null)[]): number | null {
  const v = xs.filter((x): x is number => x != null && Number.isFinite(x)).sort((a, b) => a - b);
  if (!v.length) return null;
  const mid = Math.floor(v.length / 2);
  return v.length % 2 ? v[mid]! : (v[mid - 1]! + v[mid]!) / 2;
}
