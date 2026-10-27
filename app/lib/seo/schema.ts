/**
 * schema.org JSON-LD for the public pages.
 *
 * Search engines and answer engines both read this block to know WHAT a page
 * is about (a company, with this ticker, on this exchange) before they read
 * what it says. Every value is taken from the same snapshot the page
 * renders, so the markup can never claim something the page does not show.
 */

import type { MarketRow, StockSnapshot } from "./data";
import type { Faq } from "./stockCopy";
import { BRAND, CONTACT_EMAIL, HOME_URL, LEGAL_NAME, SITE_URL, absoluteUrl, sectorPath, stockPath } from "./site";

type Json = Record<string, unknown>;

export const ORG_ID = `${HOME_URL}/#organization`;
export const WEBSITE_ID = `${SITE_URL}/#website`;

export function organization(): Json {
  return {
    "@type": "Organization",
    "@id": ORG_ID,
    name: BRAND,
    legalName: LEGAL_NAME,
    url: HOME_URL,
    logo: `${HOME_URL}/pivot-icon.png`,
    email: CONTACT_EMAIL,
    areaServed: "IN",
    description:
      "Pivot is an AI stock research platform for Indian markets: charts you can talk to, company fundamentals, " +
      "and strategies you can backtest on NSE and BSE stocks, indices, MCX commodities and crypto.",
  };
}

export function website(): Json {
  return {
    "@type": "WebSite",
    "@id": WEBSITE_ID,
    url: SITE_URL,
    name: `${BRAND} — Indian stock research`,
    inLanguage: "en-IN",
    publisher: { "@id": ORG_ID },
  };
}

export function breadcrumbs(items: { name: string; path: string }[]): Json {
  return {
    "@type": "BreadcrumbList",
    itemListElement: items.map((it, i) => ({
      "@type": "ListItem",
      position: i + 1,
      name: it.name,
      item: absoluteUrl(it.path),
    })),
  };
}

export function faqPage(faqs: Faq[]): Json {
  return {
    "@type": "FAQPage",
    mainEntity: faqs.map((f) => ({
      "@type": "Question",
      name: f.q,
      acceptedAnswer: { "@type": "Answer", text: f.a },
    })),
  };
}

export function stockGraph(s: StockSnapshot, faqs: Faq[], description: string, modified: string | null): Json {
  const url = absoluteUrl(stockPath(s.symbol));
  const corp: Json = {
    "@type": "Corporation",
    "@id": `${url}#company`,
    name: s.name,
    tickerSymbol: `NSE:${s.symbol}`,
    ...(s.industry ? { industry: s.industry } : {}),
    ...(s.website ? { url: s.website, sameAs: [s.website] } : {}),
  };
  const crumbs = [
    { name: "Stocks", path: "/stocks" },
    ...(s.sector && s.sector_label ? [{ name: s.sector_label, path: sectorPath(s.sector) }] : []),
    { name: s.name, path: stockPath(s.symbol) },
  ];
  return {
    "@context": "https://schema.org",
    "@graph": [
      organization(),
      website(),
      {
        "@type": "WebPage",
        "@id": url,
        url,
        name: `${s.name} share price, chart and fundamentals`,
        description,
        inLanguage: "en-IN",
        isPartOf: { "@id": WEBSITE_ID },
        about: { "@id": `${url}#company` },
        ...(modified ? { dateModified: modified } : {}),
        primaryImageOfPage: absoluteUrl(`${stockPath(s.symbol)}/opengraph-image`),
      },
      corp,
      breadcrumbs(crumbs),
      ...(faqs.length ? [faqPage(faqs)] : []),
    ],
  };
}

/** A list page (directory, sector, gainers): an ItemList of its companies. */
export function listGraph(name: string, path: string, rows: MarketRow[], crumbs: { name: string; path: string }[]): Json {
  return {
    "@context": "https://schema.org",
    "@graph": [
      organization(),
      website(),
      {
        "@type": "CollectionPage",
        "@id": absoluteUrl(path),
        url: absoluteUrl(path),
        name,
        inLanguage: "en-IN",
        isPartOf: { "@id": WEBSITE_ID },
        mainEntity: {
          "@type": "ItemList",
          numberOfItems: rows.length,
          itemListElement: rows.slice(0, 100).map((r, i) => ({
            "@type": "ListItem",
            position: i + 1,
            name: r.name,
            url: absoluteUrl(stockPath(r.symbol)),
          })),
        },
      },
      breadcrumbs(crumbs),
    ],
  };
}

/** `<script type="application/ld+json">` content, with `<` escaped so a
 *  company name can never close the script tag. */
export function ldJson(data: Json): string {
  return JSON.stringify(data).replace(/</g, "\\u003c");
}
