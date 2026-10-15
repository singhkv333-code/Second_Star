"use client";

/**
 * The public pages' frame — /pricing, /about, /terms, /privacy: the landing
 * page's topbar, the page, and the four company links, on a white ground.
 * The page scrolls as one document (like the landing page), so the topbar
 * sees the scroll and picks up its frosted bar.
 */

import * as React from "react";
import { Nav } from "@/components/landing/PivotLanding";
import "./site.css";

export type SitePage = "about" | "terms" | "privacy" | "pricing" | "refunds" | "contact";

const LINKS: { key: SitePage; label: string }[] = [
  { key: "about", label: "About us" },
  { key: "terms", label: "Terms & conditions" },
  { key: "privacy", label: "Privacy policy" },
  { key: "pricing", label: "Pricing" },
  { key: "refunds", label: "Refunds & cancellation" },
  { key: "contact", label: "Contact" },
];

export function SiteChrome({
  current, className, skipTo, children,
}: { current: SitePage; className?: string; skipTo?: string; children: React.ReactNode }): React.ReactElement {
  React.useEffect(() => {
    const html = document.documentElement;
    html.classList.add("pivot-landing-active");
    return () => html.classList.remove("pivot-landing-active");
  }, []);

  return (
    <div className={`pivot-landing site${className ? ` ${className}` : ""}`}>
      {skipTo ? <a className="site-skip" href={`#${skipTo}`}>Skip to content</a> : null}
      <Nav home="/" />
      {children}
      <footer className="site-footer">
        <nav aria-label="Company and legal">
          {LINKS.map(({ key, label }) => (
            <a key={key} href={`/${key}`} aria-current={current === key ? "page" : undefined}>{label}</a>
          ))}
        </nav>
      </footer>
    </div>
  );
}
