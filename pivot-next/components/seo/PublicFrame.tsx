/**
 * The chrome around every public page: a header a signed-out reader can use
 * (search, the directory, sign in) and a footer that links the whole
 * public graph together, so a crawler that lands anywhere reaches everything.
 */

import Link from "next/link";
import { BRAND, HOME_URL } from "@/lib/seo/site";
import { StockSearch } from "./StockSearch";
import styles from "./public.module.css";

const POPULAR: [string, string][] = [
  ["RELIANCE", "Reliance"], ["HDFCBANK", "HDFC Bank"], ["TCS", "TCS"], ["INFY", "Infosys"],
  ["ICICIBANK", "ICICI Bank"], ["SBIN", "SBI"], ["BHARTIARTL", "Airtel"], ["ITC", "ITC"],
  ["TMPV", "Tata Motors PV"], ["M&M", "Mahindra & Mahindra"], ["BAJFINANCE", "Bajaj Finance"], ["LT", "L&T"], ["ADANIENT", "Adani Enterprises"], ["HAL", "HAL"],
];

export function PublicFrame({
  children,
  signupNext,
}: {
  children: React.ReactNode;
  /** Where signing up should land: the chart for this stock, usually. */
  signupNext?: string;
}): React.ReactElement {
  const next = signupNext ? `?next=${encodeURIComponent(signupNext)}` : "";
  return (
    <div className={styles.page}>
      <a className={styles.skip} href="#content">Skip to content</a>
      <header className={styles.header}>
        <div className={`${styles.wrap} ${styles.headerRow}`}>
          <a href={HOME_URL} className={styles.brand} aria-label={`${BRAND} home`}>{BRAND}.</a>
          <nav className={styles.nav} aria-label="Explore">
            <Link href="/stocks">Stocks</Link>
            <Link href="/sectors">Sectors</Link>
            <Link href="/stocks/top-gainers">Top gainers</Link>
            <Link href="/stocks/52-week-high">52-week highs</Link>
          </nav>
          <div className={styles.headerEnd}>
            <StockSearch />
            <Link className={styles.signin} href={`/login${next}`}>Sign in</Link>
            <Link className={styles.cta} href={`/signup${next}`}>Start free</Link>
          </div>
        </div>
      </header>
      <main id="content" className={`${styles.wrap} ${styles.main}`} tabIndex={-1}>
        {children}
      </main>
      <footer className={styles.footer}>
        <div className={styles.wrap}>
          <nav aria-label="Popular stocks">
            {POPULAR.map(([sym, label]) => (
              <Link key={sym} href={`/stock/${encodeURIComponent(sym)}`}>{label} share price</Link>
            ))}
          </nav>
          <nav aria-label="Pivot">
            <Link href="/stocks">All NSE stocks</Link>
            <Link href="/sectors">Sectors</Link>
            <Link href="/stocks/top-gainers">Top gainers</Link>
            <Link href="/stocks/top-losers">Top losers</Link>
            <Link href="/stocks/52-week-high">52-week highs</Link>
            <Link href="/stocks/52-week-low">52-week lows</Link>
            <Link href="/pricing">Pricing</Link>
            <a href={`${HOME_URL}/about`}>About</a>
            <a href={`${HOME_URL}/terms`}>Terms</a>
            <a href={`${HOME_URL}/privacy`}>Privacy</a>
            <a href={`${HOME_URL}/contact`}>Contact</a>
          </nav>
          <p>
            {BRAND} is a research and charting platform, not a broker or a SEBI-registered investment adviser. Prices are
            end-of-day NSE closes unless marked live; fundamentals come from company filings. Nothing here is a
            recommendation to buy or sell a security. This is analysis, not financial advice.
          </p>
        </div>
      </footer>
    </div>
  );
}
