"use client";

import { useEffect, useState } from "react";
import { ArrowUpRight, Newspaper, RefreshCw } from "lucide-react";
import { formatDistanceToNowStrict } from "date-fns";
import { getNews, type NewsItem } from "@/lib/api";
import { isError } from "@/lib/types";

type NewsState =
  | { kind: "loading" }
  | { kind: "error" }
  | { kind: "ready"; items: NewsItem[] };

export function StockNewsSection({
  symbol,
  companyName,
  exchange,
}: {
  symbol: string;
  companyName: string;
  exchange: "NSE" | "BSE";
}): React.ReactElement {
  const [attempt, setAttempt] = useState(0);
  const [state, setState] = useState<NewsState>({ kind: "loading" });

  useEffect(() => {
    let cancelled = false;
    setState({ kind: "loading" });

    getNews(symbol, exchange, 8, companyName)
      .then((result) => {
        if (cancelled) return;
        setState(isError(result)
          ? { kind: "error" }
          : { kind: "ready", items: result.data.items });
      })
      .catch(() => {
        if (!cancelled) setState({ kind: "error" });
      });

    return () => { cancelled = true; };
  }, [symbol, companyName, exchange, attempt]);

  return (
    <section id="stock-news" className="stock-news" aria-labelledby="stock-news-title">
      <header className="stock-news__header">
        <h2 id="stock-news-title">News</h2>
      </header>

      {state.kind === "loading" && <NewsSkeleton />}

      {state.kind === "error" && (
        <div className="stock-news__message" role="alert">
          <Newspaper size={19} aria-hidden="true" />
          <div>
            <strong>News is temporarily unavailable</strong>
            <span>The price and company data above are unaffected.</span>
          </div>
          <button type="button" onClick={() => setAttempt((n) => n + 1)}>
            <RefreshCw size={13} aria-hidden="true" /> Retry
          </button>
        </div>
      )}

      {state.kind === "ready" && state.items.length === 0 && (
        <div className="stock-news__message">
          <Newspaper size={19} aria-hidden="true" />
          <div>
            <strong>No recent coverage found</strong>
            <span>There are no current headlines for this security.</span>
          </div>
        </div>
      )}

      {state.kind === "ready" && state.items.length > 0 && (
        <ul className="stock-news__list">
          {state.items.map((item, index) => (
            <li key={`${item.url ?? item.title}-${index}`}>
              <NewsRow item={item} />
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

function NewsRow({ item }: { item: NewsItem }): React.ReactElement {
  const time = relativeTime(item.published_at);
  const content = (
    <>
      {item.thumbnail ? (
        // Source URLs are supplied by the news relay and can span arbitrary
        // publisher hosts, so next/image's static host allowlist is unsuitable.
        // eslint-disable-next-line @next/next/no-img-element
        <img
          className={`stock-news__thumb${item.thumbnail_kind === "publisher" ? " stock-news__thumb--publisher" : ""}`}
          src={item.thumbnail}
          alt=""
          loading="lazy"
        />
      ) : (
        <span className="stock-news__thumb stock-news__thumb--empty" aria-hidden="true">
          <Newspaper size={20} />
        </span>
      )}
      <div className="stock-news__copy">
        <div className="stock-news__meta">
          <span>{item.publisher || "Publisher unavailable"}</span>
          {time && <><i aria-hidden="true" /> <time dateTime={item.published_at ?? undefined}>{time}</time></>}
        </div>
        <h3>{item.title}</h3>
        {item.summary && <p>{item.summary}</p>}
      </div>
      {item.url && <ArrowUpRight className="stock-news__arrow" size={16} aria-hidden="true" />}
    </>
  );

  return item.url ? (
    <a className="stock-news__item" href={item.url} target="_blank" rel="noopener noreferrer">
      {content}
    </a>
  ) : (
    <article className="stock-news__item stock-news__item--static">{content}</article>
  );
}

function relativeTime(value: string | null): string | null {
  if (!value) return null;
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return null;
  return formatDistanceToNowStrict(date, { addSuffix: true });
}

function NewsSkeleton(): React.ReactElement {
  return (
    <div className="stock-news__list" aria-label="Loading company news">
      {[0, 1, 2].map((index) => (
        <div className="stock-news__item stock-news__skeleton" key={index}>
          <b />
          <div className="stock-news__copy">
            <span />
            <strong />
            <em />
          </div>
        </div>
      ))}
    </div>
  );
}
