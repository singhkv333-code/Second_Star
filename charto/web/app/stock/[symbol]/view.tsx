"use client";

import { useEffect, useState } from "react";
import { StockDetailPage } from "@/components/StockDetailPage";

/**
 * Client wrapper that mounts Pivot's stock detail page — StockDetailPage is
 * copied here unchanged — under charto's own chrome instead of Pivot's
 * AppShell. charto has no sidebar to keep: the only navigation that makes
 * sense from a company page is back to the chart for that symbol.
 *
 * Theme: Pivot applies its dark palette by toggling `.dark` on <html>, and
 * that lived in AppShell. This page runs on a DIFFERENT ORIGIN from the chart
 * (:5175 vs :5173), so it cannot read charto's stored choice — the chart
 * therefore sends it in the link as `?theme=`, and the choice is remembered
 * here so a reload or a peer click keeps it.
 */
const CHART = "http://localhost:5173/index.html";
const KEY = "charto_theme";

export function StockSymbolView({ symbol }: { symbol: string }): React.ReactElement {
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
          <StockDetailPage symbol={symbol} />
        </div>
      </div>
    </div>
  );
}
