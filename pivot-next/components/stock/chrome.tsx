"use client";

/**
 * The small shared parts of the deep sections: a panel heading, a segmented
 * control, a chip, and the skeleton.
 *
 * These exist so the five panels cannot drift apart. They are deliberately
 * thin — the existing page already owns the visual language (12px radius,
 * `--glass-border` hairlines, 14/600 section labels), and this file restates
 * it once rather than five times.
 */

import * as React from "react";

/** True below the page's phone breakpoint — the same 639.98px the page layout
 *  itself switches on, so a panel and the layout around it never disagree
 *  about which device they are on.
 *
 *  Only for the things CSS cannot reach: a canvas chart's axis font, how many
 *  sessions of history are worth drawing at 350px, whether a row is one line
 *  or two. Everything that is only a size or a gap belongs in a media query.
 *
 *  Starts false so the server render and the first client render agree; the
 *  effect corrects it before paint. */
/** The space between one section of the stock page and the next.
 *
 *  Sections used to set their own top margin — 24 on Performance, 32 on
 *  Technical Analysis, 26 on Pattern Edge, 36 on Key Metrics, 28 on Financial
 *  Performance — so the page's rhythm changed every time it changed subject,
 *  and at the low end a heading sat closer to the chart above it than to its
 *  own content. One value, and a roomier one. */
export const SECTION_GAP = 56;

export function usePhone(): boolean {
  const [phone, setPhone] = React.useState(false);
  React.useEffect(() => {
    const mq = window.matchMedia("(max-width: 639.98px)");
    const sync = (): void => setPhone(mq.matches);
    sync();
    mq.addEventListener("change", sync);
    return () => mq.removeEventListener("change", sync);
  }, []);
  return phone;
}

export function PanelHead({
  title,
  sub,
  right,
}: {
  title: string;
  sub?: React.ReactNode;
  right?: React.ReactNode;
}): React.ReactElement {
  return (
    <div
      style={{
        display: "flex",
        alignItems: "flex-end",
        justifyContent: "space-between",
        gap: 14,
        flexWrap: "wrap",
      }}
    >
      <div>
        {/* This IS the section heading now — there is no eyebrow above it and
            no wrapper title above that, so it carries the whole level.
            21/600 is not a taste call: it is the size "Financial Performance"
            is set in one section up. Two headings at the same level of a
            document that disagree on size read as two different pages that
            happen to be stacked — which is why "Performance" and "Key Metrics",
            left behind at 14, came up with the rest of them. */}
        <h3
          className="m-0"
          style={{
            fontFamily: "var(--font-ui)",
            fontSize: 21,
            fontWeight: 600,
            letterSpacing: "-0.022em",
            color: "var(--text-primary)",
          }}
        >
          {title}
        </h3>
        {sub ? (
          <div style={{ fontSize: 12, color: "var(--text-tertiary)", marginTop: 3 }}>
            {sub}
          </div>
        ) : null}
      </div>
      {right}
    </div>
  );
}

/** A choice between a few named views.
 *
 *  It used to be a pill inside a bordered, filled track — a control that drew
 *  four things (outer border, inner fill, active fill, active shadow) to say
 *  one thing. On a page whose own rule is that a card earns its border by
 *  being the interaction, a segmented track around two words is furniture.
 *
 *  So the box is gone and the type carries the state: the live option is ink
 *  at 600, the rest are tertiary. `underline` adds the 2px rule the Financial
 *  Performance tabs already use, for the places this reads as a tab strip
 *  rather than as a setting sitting beside a heading.
 */
export function Segmented({
  value,
  options,
  onChange,
  underline = false,
}: {
  value: string;
  options: { value: string; label: string }[];
  onChange: (v: string) => void;
  underline?: boolean;
}): React.ReactElement {
  return (
    <div
      role="tablist"
      style={{
        display: "inline-flex",
        flexWrap: "wrap",
        alignItems: "center",
        gap: underline ? 0 : 16,
        // The tab strip sits ON the hairline it shares with the panel below,
        // the way the Financial Performance tabs do.
        marginBottom: underline ? -1 : 0,
      }}
    >
      {options.map((o) => {
        const on = o.value === value;
        return (
          <button
            key={o.value}
            type="button"
            role="tab"
            aria-selected={on}
            onClick={() => onChange(o.value)}
            style={{
              padding: underline ? "6px 14px" : 0,
              border: "none",
              background: "transparent",
              cursor: "pointer",
              fontFamily: "var(--font-ui)",
              fontSize: 12.5,
              fontWeight: on ? 600 : 400,
              color: on
                ? underline ? "var(--pivot-blue, #1b7cc7)" : "var(--text-primary)"
                : "var(--text-secondary)",
              borderBottom: underline
                ? `2px solid ${on ? "var(--pivot-blue, #1b7cc7)" : "transparent"}`
                : "none",
              transition: "color 150ms ease, border-color 150ms ease",
            }}
          >
            {o.label}
          </button>
        );
      })}
    </div>
  );
}

export type ChipTone = "neutral" | "accent" | "warn";

export function Chip({
  children,
  tone = "neutral",
  title,
  onClick,
  as = "span",
  href,
}: {
  children: React.ReactNode;
  tone?: ChipTone;
  title?: string;
  onClick?: () => void;
  as?: "span" | "a" | "button";
  href?: string;
}): React.ReactElement {
  const style: React.CSSProperties = {
    display: "inline-flex",
    alignItems: "center",
    gap: 4,
    padding: "1.5px 7px",
    borderRadius: "var(--radius-xs)",
    fontFamily: "var(--font-ui)",
    fontSize: 11,
    fontWeight: 550,
    lineHeight: 1.55,
    whiteSpace: "nowrap",
    textDecoration: "none",
    border: "1px solid",
    cursor: onClick || href ? "pointer" : "default",
    ...(tone === "accent"
      ? { color: "var(--pivot-blue)", borderColor: "var(--accent-border)", background: "var(--accent-wash)" }
      : tone === "warn"
        ? { color: "var(--color-warn)", borderColor: "color-mix(in srgb, var(--color-warn) 40%, transparent)", background: "color-mix(in srgb, var(--color-warn) 10%, transparent)" }
        : { color: "var(--text-tertiary)", borderColor: "var(--glass-border)", background: "var(--bg-secondary)" }),
  };
  if (as === "a" && href) {
    return (
      <a href={href} target="_blank" rel="noreferrer" title={title} style={style}>
        {children}
      </a>
    );
  }
  if (as === "button" || onClick) {
    return (
      <button type="button" onClick={onClick} title={title} style={style}>
        {children}
      </button>
    );
  }
  return <span title={title} style={style}>{children}</span>;
}

/** Loading state for a panel. Deliberately shaped like the content it
 *  replaces — bars where rows will be — so the layout does not jump when the
 *  data lands. */
export function PanelSkeleton({ rows = 6 }: { rows?: number }): React.ReactElement {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 8 }} aria-busy="true">
      {Array.from({ length: rows }).map((_, i) => (
        <div
          key={i}
          style={{
            height: i === 0 ? 30 : 20,
            borderRadius: "var(--radius-xs)",
            background: "var(--bg-elevated)",
            opacity: 0.55,
          }}
        />
      ))}
    </div>
  );
}

export function EmptyNote({ children }: { children: React.ReactNode }): React.ReactElement {
  return (
    <div
      style={{
        padding: "18px 16px",
        border: "1px dashed var(--glass-border)",
        borderRadius: "var(--radius-md)",
        color: "var(--text-tertiary)",
        fontSize: 13,
        fontFamily: "var(--font-ui)",
      }}
    >
      {children}
    </div>
  );
}
