"use client";

/**
 * Ownership and profile.
 *
 * Small on purpose. The underlying table carries thirty-odd columns but only a
 * handful are populated across the universe — institutions and insiders sit at
 * ~96%, employee count at 5%. So the layout is a short definition list that
 * simply omits what is missing, rather than a grid of tiles half of which
 * would read "—" on most companies.
 *
 * The two holding percentages come from the source as FRACTIONS (0.17857),
 * not percents. Rendering them raw was the obvious trap: "0.18% held by
 * institutions" for TCS is off by two orders of magnitude and looks plausible.
 */

import * as React from "react";

import type { OwnershipResponse } from "@/lib/api";
import { EmptyNote, PanelHead } from "./chrome";
import { num } from "./FinTable";

/** yfinance exchange codes, which are not names anyone uses. Leaving the raw
 *  code in place put "Listed on NSI" on the page — correct in the source,
 *  meaningless on screen. Unmapped codes pass through rather than being
 *  blanked: an unfamiliar code still tells the reader more than nothing. */
const EXCHANGE: Record<string, string> = {
  NSI: "NSE", BSE: "BSE", BOM: "BSE", NSE: "NSE",
};

/** Source stores a fraction of shares outstanding; the page shows a percent. */
function heldPct(v: number | null | undefined): string | null {
  if (v === null || v === undefined) return null;
  return num(v * 100, { dp: 2, pct: true });
}

export function OwnershipPanel({ data }: { data: OwnershipResponse }): React.ReactElement {
  if (!data.available) {
    return <EmptyNote>No ownership profile available for this company.</EmptyNote>;
  }

  const inst = heldPct(data.held_percent_institutions);
  const insiders = heldPct(data.held_percent_insiders);

  const facts: { k: string; v: string | null }[] = [
    { k: "Held by institutions", v: inst },
    { k: "Held by insiders", v: insiders },
    {
      k: "Institutions on the register",
      v: data.institutions_count !== null && data.institutions_count !== undefined
        ? num(data.institutions_count) : null,
    },
    { k: "Institutional float", v: heldPct(data.institutions_float_percent) },
    {
      k: "Employees",
      v: data.full_time_employees ? num(data.full_time_employees) : null,
    },
    { k: "Sector", v: data.sector ?? null },
    { k: "Industry", v: data.industry ?? null },
    {
      k: "Headquarters",
      v: [data.city, data.state, data.country].filter(Boolean).join(", ") || null,
    },
    { k: "Listed on", v: data.exchange ? (EXCHANGE[data.exchange] ?? data.exchange) : null },
  ].filter((f) => f.v !== null);

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
      <PanelHead
        title="Ownership & profile"
        right={
          data.website ? (
            <a
              href={data.website}
              target="_blank"
              rel="noreferrer"
              style={{ fontSize: 12, color: "var(--pivot-blue)", textDecoration: "none" }}
            >
              {data.website.replace(/^https?:\/\/(www\.)?/, "")} ↗
            </a>
          ) : null
        }
      />

      {inst || insiders ? (
        <div
          style={{
            border: "1px solid var(--glass-border)",
            borderRadius: "var(--radius-md)",
            background: "var(--bg-primary)",
            padding: "14px 16px",
            display: "flex",
            flexDirection: "column",
            gap: 10,
          }}
        >
          {[
            { label: "Institutions", pct: (data.held_percent_institutions ?? 0) * 100, tone: "var(--pivot-blue)" },
            { label: "Insiders / promoters", pct: (data.held_percent_insiders ?? 0) * 100, tone: "#7C9885" },
          ]
            .filter((b) => b.pct > 0)
            .map((b) => (
              <div key={b.label}>
                <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 4 }}>
                  <span style={{ fontSize: 12, color: "var(--text-secondary)" }}>{b.label}</span>
                  <span
                    style={{
                      fontSize: 12, fontWeight: 600,
                      fontVariantNumeric: "tabular-nums", color: "var(--text-primary)",
                    }}
                  >
                    {num(b.pct, { dp: 2, pct: true })}
                  </span>
                </div>
                <div style={{ height: 5, borderRadius: 3, background: "var(--bg-elevated)" }}>
                  <div
                    style={{
                      width: `${Math.min(100, b.pct)}%`, height: "100%",
                      borderRadius: 3, background: b.tone,
                    }}
                  />
                </div>
              </div>
            ))}
        </div>
      ) : null}

      <dl
        style={{
          margin: 0,
          display: "grid",
          gridTemplateColumns: "repeat(auto-fit, minmax(210px, 1fr))",
          gap: "0 22px",
          border: "1px solid var(--glass-border)",
          borderRadius: "var(--radius-md)",
          background: "var(--bg-primary)",
          padding: "6px 16px 12px",
        }}
      >
        {facts.map((f) => (
          <div
            key={f.k}
            style={{
              display: "flex",
              justifyContent: "space-between",
              gap: 12,
              padding: "8px 0",
              borderBottom: "1px solid var(--glass-border)",
            }}
          >
            <dt style={{ fontSize: 12, color: "var(--text-tertiary)" }}>{f.k}</dt>
            <dd
              style={{
                margin: 0, fontSize: 12, fontWeight: 550,
                color: "var(--text-primary)", textAlign: "right",
                fontVariantNumeric: "tabular-nums",
              }}
            >
              {f.v}
            </dd>
          </div>
        ))}
      </dl>

      {data.long_business_summary ? (
        <Summary text={data.long_business_summary} />
      ) : null}
    </div>
  );
}

/** Business summaries run to several hundred words. Clamped to four lines with
 *  a real toggle rather than a fade — a fade tells you there is more but not
 *  how to get it. */
function Summary({ text }: { text: string }): React.ReactElement {
  const [open, setOpen] = React.useState(false);
  return (
    <div
      style={{
        border: "1px solid var(--glass-border)",
        borderRadius: "var(--radius-md)",
        background: "var(--bg-primary)",
        padding: "13px 16px",
      }}
    >
      <div style={{ fontSize: 11, color: "var(--text-tertiary)", marginBottom: 6 }}>
        Business
      </div>
      <p
        style={{
          margin: 0,
          fontSize: 13,
          lineHeight: 1.62,
          color: "var(--text-secondary)",
          display: open ? "block" : "-webkit-box",
          WebkitLineClamp: open ? undefined : 4,
          WebkitBoxOrient: "vertical",
          overflow: open ? undefined : "hidden",
        }}
      >
        {text}
      </p>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        style={{
          marginTop: 7, border: "none", background: "transparent", padding: 0,
          color: "var(--pivot-blue)", fontFamily: "var(--font-ui)", fontSize: 12,
          cursor: "pointer",
        }}
      >
        {open ? "Show less" : "Show more"}
      </button>
    </div>
  );
}
