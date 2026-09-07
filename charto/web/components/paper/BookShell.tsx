"use client";

/**
 * BookShell — the frame the two book pages share.
 *
 * Portfolio and Strategies are one surface split in two: the holdings, and the
 * rules that produced them. So they get one header with a link between them
 * rather than two pages that each look like the whole app, and a way back to
 * the chart — which is where both of them came from and where they are edited.
 *
 * It owns no data. Each tab still fetches its own, exactly as it does inside
 * Pivot's shell, which is what lets those two components stay unedited.
 */

import Link from "next/link";
import { useEffect, useState } from "react";

import { getStoredToken } from "@/components/AppBootstrap";

const TABS = [
  { href: "/paper", label: "Portfolio" },
  { href: "/strategies", label: "Strategies" },
] as const;

export function BookShell({
  active,
  children,
}: {
  active: "/paper" | "/strategies";
  children: React.ReactNode;
}): React.ReactElement {
  // Client-only: `getStoredToken` reads localStorage, and rendering the
  // signed-out state during SSR would flash it at every signed-in visitor.
  const [signedIn, setSignedIn] = useState<boolean | null>(null);
  useEffect(() => setSignedIn(Boolean(getStoredToken())), []);

  return (
    <div
      className="flex min-h-full flex-col"
      style={{ gap: 18, padding: "20px 24px 32px", background: "var(--bg-base)" }}
    >
      <header
        className="flex flex-wrap items-center"
        style={{ gap: 12, paddingBottom: 4 }}
      >
        <nav className="flex" style={{ gap: 2 }}>
          {TABS.map((t) => (
            <Link
              key={t.href}
              href={t.href}
              className="q-display"
              style={{
                fontSize: 13,
                padding: "5px 13px",
                borderRadius: "var(--radius-xs)",
                textDecoration: "none",
                color:
                  t.href === active
                    ? "var(--text-primary)"
                    : "var(--text-secondary)",
                background:
                  t.href === active ? "var(--bg-secondary)" : "transparent",
                border:
                  t.href === active
                    ? "1px solid var(--glass-border)"
                    : "1px solid transparent",
              }}
            >
              {t.label}
            </Link>
          ))}
        </nav>
        <span style={{ flex: 1 }} />
        <span
          style={{
            fontFamily: "var(--font-ui)",
            fontSize: 12,
            color: "var(--text-secondary)",
          }}
        >
          Simulated · no order reaches a broker
        </span>
        <a
          href="/"
          className="q-display"
          style={{
            fontSize: 12.5,
            padding: "5px 12px",
            borderRadius: "var(--radius-xs)",
            border: "1px solid var(--glass-border)",
            color: "var(--text-secondary)",
            textDecoration: "none",
          }}
        >
          Back to the chart
        </a>
      </header>

      {signedIn === null ? null : signedIn ? (
        children
      ) : (
        <div
          style={{
            background: "var(--bg-secondary)",
            border: "1px solid var(--glass-border)",
            borderRadius: "var(--radius-sm)",
            padding: "20px 22px",
            maxWidth: "62ch",
          }}
        >
          <p
            style={{
              margin: 0,
              fontFamily: "var(--font-ui)",
              fontSize: 13.5,
              color: "var(--text-secondary)",
            }}
          >
            A paper book belongs to an account, not to a browser tab. Sign in on
            the chart and this page will find it.{" "}
            <a href="/" style={{ color: "var(--text-primary)" }}>
              Open the chart
            </a>
            .
          </p>
        </div>
      )}
    </div>
  );
}

/** Send a question to the chart's composer. The pages are a different app on
 *  the same origin, so a query parameter is the whole interface — chat.js
 *  reads `ask`, drops it in the box and waits. */
export function askOnChart(prompt: string, symbol?: string): void {
  const p = new URLSearchParams();
  if (symbol) p.set("symbol", symbol);
  p.set("ask", prompt);
  window.location.href = `/?${p.toString()}`;
}
