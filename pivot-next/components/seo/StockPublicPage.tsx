/**
 * The public stock page: what a signed-out reader, a search engine and an
 * answer engine all see at /stock/{symbol}.
 *
 * Server-rendered from one snapshot (lib/seo/data.ts). Every number on it is
 * a field of that snapshot or of the daily candles; every sentence is
 * written from those fields (lib/seo/stockCopy.ts); a section whose data is
 * missing is not drawn. Signed-in readers get the full research page instead
 * (app/stock/[symbol]/view.tsx).
 */

import Link from "next/link";
import type { Bar, HistoryPoint, MarketRow, StockSnapshot } from "@/lib/seo/data";
import { crore, day, fiscalQuarter, fiscalYear, inr, num, pct, times } from "@/lib/seo/format";
import { type Faq, type PriceStats, lead, summary } from "@/lib/seo/stockCopy";
import { letterOf, sectorPath, stockPath } from "@/lib/seo/site";
import { LivePrice } from "./LivePrice";
import { PriceChart } from "./PriceChart";
import styles from "./public.module.css";

type Props = { s: StockSnapshot; bars: Bar[]; stats: PriceStats | null; faqs: Faq[] };

function Signed({ v, digits = 1 }: { v: number | null | undefined; digits?: number }): React.ReactElement {
  if (v == null) return <>—</>;
  return <span className={v >= 0 ? styles.up : styles.down}>{pct(v, digits, true)}</span>;
}

function Stat({ label, value, note }: { label: string; value: string; note?: string | null }): React.ReactElement | null {
  if (value === "—") return null;
  return (
    <div className={styles.stat}>
      <dt>{label}</dt>
      <dd>{value}{note ? <small>{note}</small> : null}</dd>
    </div>
  );
}

function keyStats(s: StockSnapshot, p: PriceStats | null): React.ReactElement {
  const m = s.market;
  const L = s.latest;
  const fy = (k: string): string | null => (L[k]?.period_end ? fiscalYear(L[k]!.period_end) : null);
  const lender = s.sector === "banks" || s.sector === "financials";
  const q = s.quarters?.quarters?.[0];
  return (
    <dl className={styles.stats}>
      <Stat label="Market cap" value={crore(m?.mcap_cr)} note={m?.rank ? `Rank ${m.rank} on NSE` : null} />
      <Stat label="P/E (trailing)" value={times(m?.pe)} />
      <Stat label="Price to book" value={times(L.price_to_book?.value, 2)} note={fy("price_to_book")} />
      <Stat label="EPS (TTM)" value={inr(q?.eps_ttm ?? null)} note={q ? `to ${fiscalQuarter(q.period_end)}` : null} />
      <Stat label="ROE" value={pct(L.roe?.value)} note={fy("roe")} />
      <Stat label="ROCE" value={pct(L.roce?.value)} note={fy("roce")} />
      {!lender && <Stat label="Debt to equity" value={num(L.debt_to_equity?.value)} note={fy("debt_to_equity")} />}
      <Stat label="Net profit margin" value={pct(L.net_profit_margin?.value)} note={fy("net_profit_margin")} />
      {lender && <Stat label="Net interest margin" value={pct(L.net_interest_margin?.value, 2)} note={fy("net_interest_margin")} />}
      {lender && <Stat label="Gross NPA" value={pct(L.gross_npa_pct?.value, 2)} note={fy("gross_npa_pct")} />}
      {lender && <Stat label="Net NPA" value={pct(L.net_npa_pct?.value, 2)} note={fy("net_npa_pct")} />}
      {lender && <Stat label="CASA ratio" value={pct(L.casa_pct?.value)} note={fy("casa_pct")} />}
      <Stat label="Book value / share" value={inr(L.book_value_per_share?.value ?? null)} note={fy("book_value_per_share")} />
      <Stat label="Dividend payout" value={pct(L.dividend_payout?.value)} note={fy("dividend_payout")} />
      <Stat label="52-week high" value={inr(p?.high52 ?? null)} note={p ? day(p.high52Day) : null} />
      <Stat label="52-week low" value={inr(p?.low52 ?? null)} note={p ? day(p.low52Day) : null} />
      <Stat label="1-year return" value={pct(m?.ret_1y, 1, true)} />
      <Stat label="RSI (14-day)" value={num(m?.rsi, 1)} />
    </dl>
  );
}

function Returns({ m }: { m: MarketRow }): React.ReactElement {
  return (
    <div className={styles.tableWrap}>
      <table className={styles.table}>
        <thead>
          <tr><th scope="col">Period</th><th scope="col">1 day</th><th scope="col">1 week</th><th scope="col">1 month</th><th scope="col">3 months</th><th scope="col">1 year</th></tr>
        </thead>
        <tbody>
          <tr>
            <th scope="row">Price change</th>
            <td><Signed v={m.change} digits={2} /></td>
            <td><Signed v={m.ret_1w} /></td>
            <td><Signed v={m.ret_1m} /></td>
            <td><Signed v={m.ret_3m} /></td>
            <td><Signed v={m.ret_1y} /></td>
          </tr>
        </tbody>
      </table>
    </div>
  );
}

function Quarterly({ s }: { s: StockSnapshot }): React.ReactElement | null {
  const qs = (s.quarters?.quarters ?? []).slice(0, 8).reverse();
  if (!qs.length) return null;
  const rows: [string, (q: (typeof qs)[number]) => React.ReactNode][] = [
    ["Revenue", (q) => crore(q.revenue)],
    ["Revenue YoY", (q) => <Signed v={q.revenue_yoy_pct} />],
    ["Net profit", (q) => crore(q.net_profit)],
    ["Net profit YoY", (q) => <Signed v={q.net_profit_yoy_pct} />],
    ["Net margin", (q) => pct(q.net_margin_pct)],
    ["EPS", (q) => inr(q.eps_basic)],
  ];
  return (
    <section className={styles.section} aria-labelledby="quarterly">
      <h2 id="quarterly" className={styles.h2}>{s.name} quarterly results</h2>
      <div className={styles.tableWrap}>
        <table className={styles.table}>
          <caption>₹ crore unless stated · {s.quarters?.basis} · newest on the right</caption>
          <thead>
            <tr><th scope="col">Quarter</th>{qs.map((q) => <th key={q.period_end} scope="col">{fiscalQuarter(q.period_end)}</th>)}</tr>
          </thead>
          <tbody>
            {rows.map(([label, cell]) => (
              <tr key={label}><th scope="row">{label}</th>{qs.map((q) => <td key={q.period_end}>{cell(q)}</td>)}</tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function Annual({ s }: { s: StockSnapshot }): React.ReactElement | null {
  const H = s.history;
  const lines: [string, string, (v: number) => string][] = [
    ["revenue", "Revenue", (v) => crore(v)],
    ["operating_profit", "Operating profit", (v) => crore(v)],
    ["net_profit", "Net profit", (v) => crore(v)],
    ["eps_basic", "EPS (₹)", (v) => inr(v)],
    ["cash_from_ops", "Cash from operations", (v) => crore(v)],
    ["total_debt", "Total debt", (v) => crore(v)],
    ["total_equity", "Shareholders' equity", (v) => crore(v)],
  ];
  const present = lines.filter(([k]) => H[k]?.length);
  if (!present.length) return null;
  const years = [...new Set(present.flatMap(([k]) => (H[k] ?? []).map((p) => p.period_end).filter(Boolean)))]
    .sort() as string[];
  const cols = years.slice(-6);
  const at = (pts: HistoryPoint[] | undefined, y: string): number | null => pts?.find((p) => p.period_end === y)?.value ?? null;
  return (
    <section className={styles.section} aria-labelledby="annual">
      <h2 id="annual" className={styles.h2}>{s.name} financials</h2>
      <div className={styles.tableWrap}>
        <table className={styles.table}>
          <caption>Annual, ₹ crore unless stated · fiscal years ending March</caption>
          <thead><tr><th scope="col">Item</th>{cols.map((y) => <th key={y} scope="col">{fiscalYear(y)}</th>)}</tr></thead>
          <tbody>
            {present.map(([k, label, f]) => (
              <tr key={k}>
                <th scope="row">{label}</th>
                {cols.map((y) => { const v = at(H[k], y); return <td key={y}>{v == null ? "—" : f(v)}</td>; })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function Ownership({ s }: { s: StockSnapshot }): React.ReactElement | null {
  const sh = s.shareholding;
  if (!sh?.groups?.length) return null;
  const qs = sh.quarters.slice(-6);
  const labels = sh.groups.map((g) => g.label);
  return (
    <section className={styles.section} aria-labelledby="shareholding">
      <h2 id="shareholding" className={styles.h2}>{s.name} shareholding pattern</h2>
      <div className={styles.tableWrap}>
        <table className={styles.table}>
          <caption>% of shares held, by quarter end</caption>
          <thead><tr><th scope="col">Holder</th>{qs.map((q) => <th key={q.quarter} scope="col">{day(q.quarter)}</th>)}</tr></thead>
          <tbody>
            {labels.map((label) => (
              <tr key={label}>
                <th scope="row">{label}</th>
                {qs.map((q) => { const v = q[label]; return <td key={q.quarter}>{typeof v === "number" ? pct(v, 2) : "—"}</td>; })}
              </tr>
            ))}
            {sh.pledge_pct != null && (
              <tr><th scope="row">Promoter shares pledged</th>{qs.map((q) => <td key={q.quarter}>{q.pledge_pct == null ? "—" : pct(q.pledge_pct, 2)}</td>)}</tr>
            )}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function Peers({ s }: { s: StockSnapshot }): React.ReactElement | null {
  if (!s.peers.length || !s.market) return null;
  const rows = [...s.peers, s.market].sort((a, b) => a.rank - b.rank);
  return (
    <section className={styles.section} aria-labelledby="peers">
      <h2 id="peers" className={styles.h2}>{s.name} vs peers</h2>
      <p className={styles.muted}>The listed {s.sector_label} companies nearest to {s.name} by market capitalisation.</p>
      <div className={styles.tableWrap}>
        <table className={styles.table}>
          <thead>
            <tr><th scope="col">Company</th><th scope="col">Price</th><th scope="col">1 day</th><th scope="col">1 year</th><th scope="col">Market cap</th><th scope="col">P/E</th><th scope="col">ROE</th></tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.symbol} className={r.symbol === s.symbol ? styles.current : undefined}>
                <td>
                  {r.symbol === s.symbol ? <strong>{r.symbol}</strong> : <Link href={stockPath(r.symbol)}>{r.symbol}</Link>}
                  <span className={styles.name}>{r.name}</span>
                </td>
                <td>{inr(r.price)}</td>
                <td><Signed v={r.change} digits={2} /></td>
                <td><Signed v={r.ret_1y} /></td>
                <td>{crore(r.mcap_cr)}</td>
                <td>{num(r.pe, 1)}</td>
                <td>{pct(r.roe)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

export function StockPublicPage({ s, bars, stats, faqs }: Props): React.ReactElement {
  const m = s.market;
  const chartHref = `/chart-app/index.html?symbol=${encodeURIComponent(s.symbol)}`;
  const askHref = `/signup?next=${encodeURIComponent(`/?symbol=${s.symbol}`)}`;
  const asOf = day(stats?.lastDay ?? s.as_of?.eod_day);
  const close = stats?.last ?? m?.price ?? null;
  const prompts = [
    `Why has ${s.symbol} moved over the last three months?`,
    `Backtest a 50-day moving-average crossover on ${s.symbol} over five years`,
    `Compare ${s.symbol} with ${s.peers[0]?.symbol ?? "its peers"} on margins and valuation`,
  ];

  return (
    <article>
      <ol className={styles.crumbs} aria-label="Breadcrumb">
        <li><Link href="/stocks">Stocks</Link></li>
        {s.sector && s.sector_label && <li><Link href={sectorPath(s.sector)}>{s.sector_label}</Link></li>}
        <li aria-current="page">{s.name}</li>
      </ol>

      <div className={styles.top}>
        <div>
          <h1 className={styles.h1}>{s.name} share price</h1>
          <p className={styles.sub}>
            <span>NSE: {s.symbol}</span>
            {s.sector && s.sector_label && <Link href={sectorPath(s.sector)}>{s.sector_label}</Link>}
            {s.industry && <span>{s.industry}</span>}
          </p>
        </div>
        {close != null && <LivePrice symbol={s.symbol} close={close} change={m?.change ?? null} asOf={asOf} />}
      </div>

      <div className={styles.actions}>
        <Link className={styles.cta} href={askHref}>Ask Pivot about {s.symbol}</Link>
        <a className={styles.ctaGhost} href={chartHref}>Open the interactive chart</a>
      </div>

      <PriceChart bars={bars} name={s.name} />

      <section className={styles.section} aria-labelledby="overview">
        <h2 id="overview" className={styles.h2}>{s.name} at a glance</h2>
        <div className={styles.prose}>
          <p className={styles.lead}>{lead(s, stats).join(" ")}</p>
          {summary(s, stats).length > 0 && <p>{summary(s, stats).join(" ")}</p>}
        </div>
      </section>

      <section className={styles.section} aria-labelledby="numbers">
        <h2 id="numbers" className={styles.h2}>Key numbers</h2>
        {keyStats(s, stats)}
        {m && (<><h3 className={styles.h3}>Returns to {day(s.as_of?.eod_day)}</h3><Returns m={m} /></>)}
      </section>

      <Quarterly s={s} />
      <Annual s={s} />
      <Ownership s={s} />
      <Peers s={s} />

      <section className={styles.callout} aria-label="Ask Pivot">
        <div>
          <h2>Ask anything about {s.name}</h2>
          <p>
            Pivot answers on the live chart with evidence you can check, and tests strategies against {s.symbol}&apos;s own
            history. Try: {prompts.map((p, i) => <span key={p}>{i ? " · " : ""}&ldquo;{p}&rdquo;</span>)}
          </p>
        </div>
        <Link className={styles.cta} href={askHref}>Start free</Link>
      </section>

      <section className={styles.section} aria-labelledby="faq">
        <h2 id="faq" className={styles.h2}>Frequently asked questions about {s.name}</h2>
        <div className={styles.faq}>
          {faqs.map((f) => (
            <details key={f.q}>
              <summary>{f.q}</summary>
              <p>{f.a}</p>
            </details>
          ))}
        </div>
      </section>

      <section className={styles.section} aria-labelledby="sources">
        <h2 id="sources" className={styles.h2}>Sources and method</h2>
        <p className={styles.muted}>
          Prices: NSE daily closes to {asOf}{s.as_of?.eod_day ? ` (returns and ratios at the ${day(s.as_of.eod_day)} close)` : ""}.
          Fundamentals: company filings via Moneycontrol, {s.quarters?.basis ?? "consolidated"} where available.
          Shareholding: exchange shareholding-pattern filings. P/E is the last close over trailing twelve-month EPS.
          Market-cap rank is among the {num(s.universe_size, 0)} NSE companies Pivot tracks; SEBI&apos;s bands call the top
          100 large-cap and the next 150 mid-cap. Figures marked &ldquo;—&rdquo; are not available rather than zero.
          This is analysis, not financial advice.
        </p>
        <p className={styles.note}>
          More: <Link href={s.sector ? sectorPath(s.sector) : "/sectors"}>all {s.sector_label ?? ""} stocks</Link> ·{" "}
          <Link href={`/stocks/${letterOf(s.name)}`}>companies starting with {letterOf(s.name).toUpperCase()}</Link> ·{" "}
          <Link href="/stocks">all NSE stocks</Link> · <Link href={`${stockPath(s.symbol)}.md`}>this page as text</Link>
        </p>
      </section>
    </article>
  );
}
