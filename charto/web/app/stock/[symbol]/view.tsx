"use client";

import { useEffect, useState } from "react";
import { StockDetailPage } from "@/components/StockDetailPage";

/**
 * Client wrapper that mounts the stock detail page under charto's own chrome
 * instead of Pivot's AppShell. The page's LAYOUT is Pivot's, tracked against
 * it; its DATA is charto's, served by dataserver.py's /api shim out of the
 * same store the chart reads, so the two can never quote different numbers
 * for one session. charto has no sidebar to keep: the only navigation that makes
 * sense from a company page is back to the chart for that symbol.
 *
 * Theme: Pivot applies its dark palette by toggling `.dark` on <html>, and
 * that lived in AppShell. The chart sends its choice in the link as `?theme=`
 * and it is remembered here so a reload or a peer click keeps it. (This page
 * used to run on a different ORIGIN from the chart and could not read the
 * stored choice at all; it is proxied onto the chart's origin now, so the
 * localStorage read below is a real fallback rather than a dead branch.)
 */
// Relative, because the chart and this page are now ONE origin — nginx (and
// serve.py in dev) proxy /stock/ here. An absolute localhost:5173 was a link
// back to whichever machine happened to be reading the page.
const CHART = "/index.html";
const KEY = "charto_theme";

/** The whole company surface under charto's chrome. `StockSymbolView` is the
 *  overview; the statements page is the same chrome around a different body,
 *  so the theme handshake below is written once rather than twice — two copies
 *  would drift, and a reader crossing between them would watch the page change
 *  palette mid-navigation. */
export function CompanyChrome({
  symbol, children,
}: {
  symbol: string;
  children: React.ReactNode;
}): React.ReactElement {
  const [dark, setDark] = useState(true);

  useEffect(() => {
    const url = new URLSearchParams(window.location.search).get("theme");
    let mode = url === "dark" || url === "light" ? url : null;
    if (!mode) {
      try {
        const saved = localStorage.getItem(KEY);
        mode = saved === "dark" || saved === "light" ? saved : null;
      } catch { /* private mode — fall through to the OS preference */ }
    }
    if (!mode) {
      mode = window.matchMedia("(prefers-color-scheme: dark)").matches
        ? "dark" : "light";
    }
    try { localStorage.setItem(KEY, mode); } catch { /* not fatal */ }
    setDark(mode === "dark");
  }, []);

  useEffect(() => {
    document.documentElement.classList.toggle("dark", dark);
  }, [dark]);

  const toggle = (): void => {
    const next = !dark;
    try { localStorage.setItem(KEY, next ? "dark" : "light"); } catch { /* ok */ }
    setDark(next);
  };
  const chart = (path: string): string => `${CHART}${path}`;

  // globals.css locks the DOCUMENT scroll (`html, body { overflow: hidden }`)
  // because Pivot scrolls inside AppShell's main pane. Dropping AppShell
  // dropped that pane, so the page could not scroll at all below the fold and
  // lost its gutters. This is AppShell's own children container, verbatim.
  return (
    <div className="flex h-screen min-h-0 flex-col bg-background">
      <div className="flex h-[52px] shrink-0 items-center gap-3 border-b border-border/40 px-6">
        <a href={chart("")} className="text-[15px] font-semibold tracking-tight">
          Charto<span style={{ color: "#2962ff" }}>.</span>
        </a>
        <div className="flex-1" />
        <button
          type="button"
          onClick={toggle}
          className="rounded-md border border-border/60 px-3 py-1.5 text-[13px] font-medium"
          title="Toggle theme"
        >
          Theme
        </button>
        <a
          href={chart(`?symbol=${encodeURIComponent(symbol)}`)}
          className="rounded-md px-3 py-1.5 text-[13px] font-medium text-white"
          style={{ background: "#2962ff" }}
        >
          Open chart →
        </a>
      </div>
      <div className="flex-1 min-h-0 overflow-y-auto px-3 pt-6 pb-8 sm:px-5 lg:px-8">
        {/* In Pivot the sidebar eats ~260px of a wide screen. Without it the
            page ran edge to edge on a 2000px display and read oversized, so
            the content keeps a comparable measure instead. */}
        <div className="mx-auto w-full max-w-[1600px]">
          {children}
        </div>
      </div>
    </div>
  );
}

export function StockSymbolView({ symbol }: { symbol: string }): React.ReactElement {
  return (
    <CompanyChrome symbol={symbol}>
      <StockDetailPage symbol={symbol} />
    </CompanyChrome>
  );
}
