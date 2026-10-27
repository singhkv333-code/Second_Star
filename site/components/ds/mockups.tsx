"use client";

/**
 * Pivot design system — device mockups.
 *
 * MacWindow: a macOS app-window frame (traffic lights, mono title,
 * optional URL pill) for landing-page product shots — the Linear-style
 * "hero window". Tone follows the surface: `paper` for light shots,
 * `ink` for the dark sections (forces the .dark token set inside, so
 * any DS component dropped in just works).
 */

import * as React from "react";
import { cn } from "@/lib/utils";

export function MacWindow({
  title = "Pivot",
  url,
  tone = "paper",
  className,
  style,
  children,
}: {
  /** Window title, set in the mono face. */
  title?: string;
  /** Optional centered URL pill (browser-chrome reading). */
  url?: string;
  tone?: "paper" | "ink";
  className?: string;
  style?: React.CSSProperties;
  children: React.ReactNode;
}) {
  const ink = tone === "ink";
  return (
    <div
      className={cn(ink && "dark", className)}
      style={{
        borderRadius: 14,
        border: "1px solid var(--glass-border-hover)",
        background: ink ? "#0d0d0e" : "var(--bg-base)",
        boxShadow:
          "0 1px 2px rgba(0,0,0,0.06), 0 12px 24px -8px rgba(0,0,0,0.14), 0 36px 80px -24px rgba(0,0,0,0.28)",
        overflow: "hidden",
        ...style,
      }}
    >
      {/* Title bar */}
      <div
        className="relative flex items-center"
        style={{
          height: 38,
          padding: "0 14px",
          borderBottom: "1px solid var(--glass-border)",
          background: ink ? "rgba(255,255,255,0.025)" : "var(--bg-secondary)",
        }}
      >
        <div className="flex items-center gap-[7px]" aria-hidden>
          {["#ff5f57", "#febc2e", "#28c840"].map((c) => (
            <span
              key={c}
              style={{
                width: 11,
                height: 11,
                borderRadius: "50%",
                background: c,
                boxShadow: "inset 0 0 0 0.5px rgba(0,0,0,0.15)",
              }}
            />
          ))}
        </div>
        <div
          className="pointer-events-none absolute inset-x-0 flex justify-center"
          aria-hidden={!url && !title}
        >
          {url ? (
            <span
              style={{
                fontFamily: "var(--font-mono)",
                fontSize: 11,
                color: "var(--text-tertiary)",
                background: "var(--surface-active)",
                border: "1px solid var(--glass-border)",
                borderRadius: "var(--radius-pill)",
                padding: "4px 14px",
                letterSpacing: "0.02em",
              }}
            >
              {url}
            </span>
          ) : (
            <span
              style={{
                fontFamily: "var(--font-mono)",
                fontSize: 11,
                fontWeight: 500,
                letterSpacing: "0.08em",
                textTransform: "uppercase",
                color: "var(--text-tertiary)",
              }}
            >
              {title}
            </span>
          )}
        </div>
      </div>
      {/* Window body */}
      <div style={{ background: "var(--bg-base)" }}>{children}</div>
    </div>
  );
}
