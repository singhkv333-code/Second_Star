"use client";

import { useEffect, useRef, useState } from "react";
import { BarChart2, ChevronRight, LogOut, User } from "lucide-react";
import { StockDetailPage } from "@/components/StockDetailPage";
import { StockAskBar } from "@/components/stock/StockAskBar";
import { PivotLockup } from "@/components/brand/PivotLockup";
import { logoutCharto, useChartoUser } from "@/lib/charto-auth";

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

function venueFor(symbol: string): string {
  const normalized = symbol.toUpperCase();
  if (/USDT$/.test(normalized)) return "BYBIT";
  if (/-USD$/.test(normalized)) return "COINBASE";
  if (["GOLD", "GOLDM", "SILVER", "SILVERM", "CRUDEOIL", "NATURALGAS", "COPPER", "ZINC", "ALUMINIUM"].includes(normalized)) return "MCX";
  if (["USDINR", "EURINR", "GBPINR", "JPYINR"].includes(normalized)) return "NSE CDS";
  if (["SENSEX", "BANKEX"].includes(normalized)) return "BSE";
  return "NSE";
}

/** The account control, mirroring the chart's own `#acctBtn`.
 *
 *  It reads charto's session, not Pivot's: the two apps issue separate tokens
 *  and `/auth/me` answers in charto's shape (`{ user }`), so Pivot's `getMe()`
 *  found nobody here even for someone signed in on the chart, and its
 *  `logoutUser()` cleared a token charto had not issued. See lib/charto-auth.
 *
 *  Signed out is a first-class state, as it is on the chart: charto works
 *  without an account and this page is deliberately ungated, so the control
 *  says who you are not and offers the way in rather than pretending. */
function StockAccountMenu({ symbol }: { symbol: string }): React.ReactElement {
  const [open, setOpen] = useState(false);
  const user = useChartoUser();
  const ref = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!open) return;
    const close = (event: MouseEvent): void => {
      if (ref.current && !ref.current.contains(event.target as Node)) setOpen(false);
    };
    const closeOnEscape = (event: KeyboardEvent): void => {
      if (event.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", close);
    document.addEventListener("keydown", closeOnEscape);
    return () => {
      document.removeEventListener("mousedown", close);
      document.removeEventListener("keydown", closeOnEscape);
    };
  }, [open]);

  const displayName = user?.name?.trim() || user?.email || "";
  const initial = displayName.trim()[0]?.toUpperCase() || "";
  const label = user ? `Account \u2014 ${displayName}` : "Sign in to Charto";

  const signOut = async (): Promise<void> => {
    await logoutCharto();
    // Signing out changes WHOSE work this page is showing, and the components
    // holding it are already mounted — the same reason the chart reloads.
    window.location.reload();
  };

  return (
    <div ref={ref} className="relative shrink-0">
      <button
        type="button"
        aria-label={label}
        title={label}
        aria-expanded={open}
        onClick={() => setOpen((value) => !value)}
        className={
          user
            ? "flex h-9 w-9 items-center justify-center rounded-full bg-[#1597b6] text-[14px] font-semibold text-white shadow-sm transition hover:bg-[#1186a2]"
            : "flex h-9 w-9 items-center justify-center rounded-full border border-border bg-muted text-muted-foreground transition hover:bg-muted/70"
        }
      >
        {user ? initial : <User size={16} aria-hidden="true" />}
      </button>
      {open && (
        <div className="absolute right-0 top-[calc(100%+8px)] z-50 w-64 overflow-hidden rounded-xl border border-border bg-background p-1.5 shadow-xl">
          <div className="border-b border-border/70 px-3 py-2.5">
            <div className="truncate text-[13px] font-semibold text-foreground">
              {user ? displayName : "Not signed in"}
            </div>
            <div className="mt-0.5 truncate text-[11px] text-muted-foreground">
              {user
                ? (user.name?.trim() ? user.email : "Signed in to Charto")
                : "Working in this browser"}
            </div>
          </div>
          {user ? (
            <button
              type="button"
              onClick={() => void signOut()}
              className="mt-1 flex w-full items-center gap-2 rounded-lg px-3 py-2 text-left text-[13px] text-foreground transition hover:bg-muted"
            >
              <LogOut size={15} aria-hidden="true" />
              Log out
            </button>
          ) : (
            // Charto's sign-in dialog lives on the chart, so this hands the
            // visitor to it rather than to Pivot's /login, which authenticates
            // against a different backend entirely.
            <a
              href={`${CHART}?symbol=${encodeURIComponent(symbol)}`}
              className="mt-1 flex w-full items-center gap-2 rounded-lg px-3 py-2 text-left text-[13px] text-foreground transition hover:bg-muted"
            >
              <User size={15} aria-hidden="true" />
              Sign in on the chart
            </a>
          )}
        </div>
      )}
    </div>
  );
}

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

  const chart = (path: string): string => `${CHART}${path}`;
  const chartHref = chart(`?symbol=${encodeURIComponent(symbol)}`);

  // globals.css locks the DOCUMENT scroll (`html, body { overflow: hidden }`)
  // because Pivot scrolls inside AppShell's main pane. Dropping AppShell
  // dropped that pane, so the page could not scroll at all below the fold and
  // lost its gutters. This is AppShell's own children container, verbatim.
  return (
    <div className="flex h-screen min-h-0 flex-col bg-background">
      {/* Tighter gaps on a phone. At 390px the row was exactly full and the
          only child that can give — the symbol pill, which carries min-w-0 —
          was the one paying for it: 360ONE rendered as "3…", the one thing on
          the bar that says which company this is. */}
      <div className="flex h-[56px] shrink-0 items-center gap-2 border-b border-border bg-background px-3 sm:gap-3 sm:px-5">
        <a href={chart("")} aria-label="Back to chart" className="flex shrink-0 items-center text-foreground">
          <PivotLockup fontSize={20} />
        </a>
        <div className="h-6 w-px bg-border" aria-hidden="true" />
        <a
          href={chartHref}
          className="flex min-w-0 items-baseline gap-1.5 rounded-full bg-muted px-3 py-2 text-[12px] font-semibold text-foreground transition hover:bg-muted/80"
        >
          <span className="truncate">{symbol}</span>
          <span className="hidden text-[10px] font-medium text-muted-foreground min-[390px]:inline">{venueFor(symbol)}</span>
        </a>
        <div className="flex-1" />
        <a
          href={chartHref}
          className="inline-flex h-9 shrink-0 items-center gap-2 rounded-lg border border-[#1597b6]/30 bg-[#1597b6]/10 px-3 text-[13px] font-semibold text-[#087f9c] transition hover:bg-[#1597b6]/15 dark:text-[#58c7df]"
        >
          <BarChart2 size={16} aria-hidden="true" />
          {/* The label goes before the symbol does. A chart button that is
              only an icon is still a chart button; a company page that will
              not say which company is not a company page. */}
          <span className="hidden min-[400px]:inline">Launch chart</span>
          <ChevronRight className="hidden sm:block" size={14} aria-hidden="true" />
        </a>
        <StockAccountMenu symbol={symbol} />
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
      {/* The ask bar belongs to the ROUTE, not to StockDetailPage: that
          component draws two layouts (desktop and phone) and the bar should be
          one instance present whichever is drawn. It floats over the content,
          so the spacer reserves the height it would otherwise cover at the end
          of the scroll — plus the phone's home-indicator inset, which the bar
          itself also clears. */}
      <div
        aria-hidden
        style={{ height: "calc(96px + env(safe-area-inset-bottom, 0px))" }}
      />
      <StockAskBar symbol={symbol} />
    </CompanyChrome>
  );
}
