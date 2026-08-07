"use client";

/**
 * Documents — everything the company has filed, newest first.
 *
 * Reverse chronology with a type filter, because that is the only order a
 * filings list is ever read in. The type counts come from the whole set, not
 * from the page being shown, so the filter tells you how much is behind it
 * before you click.
 *
 * BSE titles are boilerplate — "Announcement under Regulation 30 (LODR)-
 * Analyst / Investor Meet - Outcome" on almost every row — so the human-
 * readable type leads and the raw title is secondary. Sorting a list by a
 * column where every value is identical is how you get a list nobody scans.
 */

import * as React from "react";

import type { CompanyDocument, DocumentsResponse } from "@/lib/api";
import { Chip, EmptyNote, PanelHead } from "./chrome";

const TYPE_LABEL: Record<string, string> = {
  financial_result: "Results",
  annual_report: "Annual report",
  concall_av: "Earnings call",
  investor_presentation: "Investor deck",
};

const label = (t: string): string =>
  TYPE_LABEL[t] ?? t.replace(/_/g, " ").replace(/^\w/, (c) => c.toUpperCase());

function fmtDate(iso: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso.slice(0, 10);
  return d.toLocaleDateString("en-IN", { day: "2-digit", month: "short", year: "numeric" });
}

export function DocumentsPanel({
  data,
  filter,
  onFilterChange,
}: {
  data: DocumentsResponse;
  filter: string;
  onFilterChange: (t: string) => void;
}): React.ReactElement {
  if (!data.available && !data.types.length) {
    return <EmptyNote>No filed documents indexed for this company.</EmptyNote>;
  }

  const total = data.types.reduce((n, t) => n + t.n, 0);

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
      <PanelHead
        title="Filed documents"
        sub={`${total.toLocaleString("en-IN")} indexed · newest first`}
      />

      <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
        <FilterChip on={filter === ""} onClick={() => onFilterChange("")}>
          All <span style={{ opacity: 0.6 }}>{total}</span>
        </FilterChip>
        {data.types.map((t) => (
          <FilterChip
            key={t.doc_type}
            on={filter === t.doc_type}
            onClick={() => onFilterChange(t.doc_type)}
          >
            {label(t.doc_type)} <span style={{ opacity: 0.6 }}>{t.n}</span>
          </FilterChip>
        ))}
      </div>

      <div
        style={{
          border: "1px solid var(--glass-border)",
          borderRadius: "var(--radius-md)",
          background: "var(--bg-primary)",
          overflow: "hidden",
        }}
      >
        {data.documents.length === 0 ? (
          <div style={{ padding: "18px 16px", fontSize: 13, color: "var(--text-tertiary)" }}>
            Nothing filed under this type.
          </div>
        ) : (
          data.documents.map((d, i) => (
            <Row key={`${d.url ?? i}-${i}`} doc={d} last={i === data.documents.length - 1} />
          ))
        )}
      </div>
    </div>
  );
}

function Row({ doc, last }: { doc: CompanyDocument; last: boolean }): React.ReactElement {
  const body = (
    <div
      style={{
        display: "grid",
        gridTemplateColumns: "88px 128px 1fr auto",
        gap: 12,
        alignItems: "center",
        padding: "9px 14px",
        borderBottom: last ? "none" : "1px solid var(--glass-border)",
      }}
    >
      <span
        style={{
          fontSize: 12, color: "var(--text-tertiary)",
          fontVariantNumeric: "tabular-nums", whiteSpace: "nowrap",
        }}
      >
        {fmtDate(doc.doc_date)}
      </span>
      <span>
        <Chip tone={doc.doc_type === "annual_report" ? "accent" : "neutral"}>
          {label(doc.doc_type)}
        </Chip>
      </span>
      <span
        style={{
          fontSize: 12.5, color: "var(--text-secondary)",
          overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
        }}
        title={doc.title ?? ""}
      >
        {doc.subcategory || doc.category || doc.title || "—"}
      </span>
      <span style={{ fontSize: 11, color: "var(--text-tertiary)", whiteSpace: "nowrap" }}>
        {doc.fin_year ?? ""}{doc.quarter ? ` ${doc.quarter}` : ""}
        {doc.url ? <span style={{ color: "var(--pivot-blue)", marginLeft: 8 }}>↗</span> : null}
      </span>
    </div>
  );

  return doc.url ? (
    <a
      href={doc.url}
      target="_blank"
      rel="noreferrer"
      style={{ display: "block", textDecoration: "none", color: "inherit" }}
    >
      {body}
    </a>
  ) : (
    body
  );
}

function FilterChip({
  on, onClick, children,
}: {
  on: boolean; onClick: () => void; children: React.ReactNode;
}): React.ReactElement {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={on}
      style={{
        padding: "5px 11px",
        borderRadius: "var(--radius-pill)",
        border: "1px solid",
        borderColor: on ? "var(--accent-border)" : "var(--glass-border)",
        background: on ? "var(--accent-wash)" : "var(--bg-primary)",
        color: on ? "var(--pivot-blue)" : "var(--text-secondary)",
        fontFamily: "var(--font-ui)",
        fontSize: 12,
        fontWeight: on ? 600 : 500,
        cursor: "pointer",
        whiteSpace: "nowrap",
      }}
    >
      {children}
    </button>
  );
}
