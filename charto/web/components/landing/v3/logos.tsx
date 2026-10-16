/**
 * logos — the instrument marks Pivot itself shows (`/symbols` → logos:
 * SharePerks tiles for listed companies, logo.dev for crypto, the app's own
 * `preview/assets/instruments` for indices and commodities), copied in and
 * bundled with `new URL(…, import.meta.url)` so they ship with the page
 * rather than from a root path. A symbol without one gets the monogram the
 * app falls back to.
 */
import * as React from "react";

const SRC: Record<string, string> = {
  BHARTIARTL: new URL("./logos/BHARTIARTL.svg", import.meta.url).pathname,
  BTC: new URL("./logos/BTCUSDT.png", import.meta.url).pathname,
  ETH: new URL("./logos/ETHUSDT.png", import.meta.url).pathname,
  HDFCBANK: new URL("./logos/HDFCBANK.svg", import.meta.url).pathname,
  ICICIBANK: new URL("./logos/ICICIBANK.svg", import.meta.url).pathname,
  INFY: new URL("./logos/INFY.svg", import.meta.url).pathname,
  ITC: new URL("./logos/ITC.svg", import.meta.url).pathname,
  KALYANKJIL: new URL("./logos/KALYANKJIL.svg", import.meta.url).pathname,
  OLAELEC: new URL("./logos/OLAELEC.svg", import.meta.url).pathname,
  RELIANCE: new URL("./logos/RELIANCE.svg", import.meta.url).pathname,
  SBIN: new URL("./logos/SBIN.svg", import.meta.url).pathname,
  TCS: new URL("./logos/TCS.svg", import.meta.url).pathname,
  CRUDEOIL: new URL("./logos/crudeoil.svg", import.meta.url).pathname,
  GOLD: new URL("./logos/gold.svg", import.meta.url).pathname,
  NIFTY: new URL("./logos/nifty-50.svg", import.meta.url).pathname,
  BANKNIFTY: new URL("./logos/nifty-bank.svg", import.meta.url).pathname,
  SENSEX: new URL("./logos/sensex.svg", import.meta.url).pathname,
  SILVER: new URL("./logos/silver.svg", import.meta.url).pathname,
};

/** The mark's file, for pages that redraw it (the ASCII legend samples it). */
export const logoSrc = (s: string): string | undefined => SRC[s];

export function Logo({ s, size = 18, className }: { s: string; size?: number; className?: string }) {
  const src = SRC[s];
  const style = { width: size, height: size } as React.CSSProperties;
  if (!src) return <span className={`lg lg-mono ${className ?? ""}`} style={{ ...style, fontSize: size * 0.48 }} aria-hidden="true">{s.slice(0, 1)}</span>;
  // eslint-disable-next-line @next/next/no-img-element
  return <img className={`lg${s === "NIFTY" ? " lg-nifty" : ""} ${className ?? ""}`} src={src} alt="" style={style} />;
}
