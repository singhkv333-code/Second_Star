"use client";

/**
 * The price at the top of a public stock page. The server renders the last
 * close (that is what is cached and indexed); in the browser this asks the
 * chart server's /quotes for the current price, which rides the one shared
 * market feed, so a reader costs no broker call. During market hours it
 * refreshes every 30 seconds while the tab is visible.
 */

import { useEffect, useState } from "react";
import { inr, pct } from "@/lib/seo/format";
import styles from "./public.module.css";

type Quote = { last: number; change: number; change_pct: number; as_of: number };

function marketOpen(now = new Date()): boolean {
  const ist = new Date(now.getTime() + (330 + now.getTimezoneOffset()) * 60_000);
  const d = ist.getDay();
  const m = ist.getHours() * 60 + ist.getMinutes();
  return d >= 1 && d <= 5 && m >= 9 * 60 + 15 && m <= 15 * 60 + 30;
}

export function LivePrice({
  symbol, close, change, asOf,
}: {
  symbol: string;
  close: number;
  change: number | null;
  asOf: string;
}): React.ReactElement {
  const [q, setQ] = useState<Quote | null>(null);

  useEffect(() => {
    let alive = true;
    const pull = (): void => {
      fetch(`/quotes?symbols=${encodeURIComponent(symbol)}`)
        .then((r) => (r.ok ? r.json() : null))
        .then((d: { quotes?: Quote[] } | null) => {
          const got = d?.quotes?.[0];
          if (alive && got && Number.isFinite(got.last) && got.last > 0) setQ(got);
        })
        .catch(() => {});
    };
    pull();
    const id = window.setInterval(() => {
      if (document.visibilityState === "visible" && marketOpen()) pull();
    }, 30_000);
    return () => { alive = false; window.clearInterval(id); };
  }, [symbol]);

  const price = q?.last ?? close;
  const chg = q ? q.change_pct : change;
  const stamp = q
    ? `${marketOpen() ? "Live" : "Last traded"} · ${new Date(q.as_of * 1000).toLocaleString("en-IN", {
        timeZone: "Asia/Kolkata", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit",
      })} IST`
    : `NSE close · ${asOf}`;

  return (
    <div className={styles.price}>
      <div className={styles.priceValue}>{inr(price)}</div>
      {chg != null && (
        <div className={`${styles.priceChange} ${chg >= 0 ? styles.up : styles.down}`}>
          {q ? `${q.change >= 0 ? "+" : "−"}${inr(Math.abs(q.change)).slice(1)} ` : ""}({pct(chg, 2, true)})
        </div>
      )}
      <div className={styles.priceAsOf}>{stamp}</div>
    </div>
  );
}
