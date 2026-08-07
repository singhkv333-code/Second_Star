"use client";

/**
 * Annual report — facts pulled out of the filed PDF, each still attached to
 * the page it came from.
 *
 * This is the section the rest of the page is scaffolding for. Every other
 * finance site shows you a number; none of them shows you the page of the
 * annual report it was read off. We hold `page`, `quote`, `grounding` and a
 * unit verdict for every fact, so the design rule here is simple: never show
 * a value without its provenance within one click.
 *
 * Two honesty affordances that are not decoration:
 *
 *   · `unit_agrees` is a STRING verdict, and "DISAGREE model=crore
 *     deterministic=million" is a real value in this data. Two independent
 *     readings of the unit disagreed — the exact failure that produced a
 *     10,000x error elsewhere in this codebase. Those rows get a warning chip.
 *     Hiding them would be the worse choice: a wrong number shown confidently
 *     is more expensive than a right number shown with a caveat.
 *   · `grounding` distinguishes a value lifted verbatim from one normalised
 *     on the way out. Only the exceptions are marked, so the badge means
 *     something when it appears.
 */

import * as React from "react";

import type { AnnualReportResponse, FilingFact } from "@/lib/api";
import { Chip, EmptyNote, PanelHead } from "./chrome";
import { DASH, num } from "./FinTable";

function unitVerdict(v: string | null): "ok" | "warn" | "none" {
  if (!v) return "none";
  const s = v.toLowerCase();
  if (s.startsWith("disagree")) return "warn";
  if (s === "agree") return "ok";
  return "none";
}

export function AnnualReportPanel({
  data,
}: {
  data: AnnualReportResponse;
}): React.ReactElement {
  const [task, setTask] = React.useState(data.tasks[0]?.task ?? "");
  const active = data.tasks.find((t) => t.task === task) ?? data.tasks[0];

  // One document per period; the page chips link into the newest by default
  // because that is the report most facts were read from.
  const docByPeriod = React.useMemo(() => {
    const m = new Map<string, string>();
    for (const d of data.documents) if (d.period && d.url) m.set(d.period, d.url);
    return m;
  }, [data.documents]);
  const primaryDoc = data.documents[0];

  if (!data.tasks.length) {
    return <EmptyNote>No annual-report facts extracted for this company yet.</EmptyNote>;
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
      <PanelHead
        title="From the annual report"
        sub={
          primaryDoc ? (
            <>
              {primaryDoc.period} report
              {primaryDoc.pages ? ` · ${primaryDoc.pages} pages` : ""} ·{" "}
              {data.tasks.reduce((n, t) => n + t.count, 0)} facts, each traceable to a page
            </>
          ) : undefined
        }
        right={
          primaryDoc?.url ? (
            <Chip as="a" href={primaryDoc.url} tone="accent">
              Open PDF ↗
            </Chip>
          ) : null
        }
      />

      {/* Task rail. Horizontally scrollable rather than wrapped to three rows —
          sixteen tasks wrapped pushes the content below the fold. */}
      <div style={{ overflowX: "auto", paddingBottom: 2 }}>
        <div style={{ display: "flex", gap: 6, width: "max-content" }}>
          {data.tasks.map((t) => {
            const on = t.task === active?.task;
            return (
              <button
                key={t.task}
                type="button"
                onClick={() => setTask(t.task)}
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
                {t.label}
                <span style={{ opacity: 0.6, marginLeft: 6, fontVariantNumeric: "tabular-nums" }}>
                  {t.count}
                </span>
              </button>
            );
          })}
        </div>
      </div>

      {active?.groups.map((g) => (
        <FactGroup key={g.grp} grp={g.grp} facts={g.facts} docByPeriod={docByPeriod}
                   fallbackUrl={primaryDoc?.url ?? null} />
      ))}

      {data.truncated ? (
        <div style={{ fontSize: 11, color: "var(--text-tertiary)" }}>
          Showing the first 1,200 facts for this company; the report holds more.
        </div>
      ) : null}
    </div>
  );
}

function FactGroup({
  grp, facts, docByPeriod, fallbackUrl,
}: {
  grp: string;
  facts: FilingFact[];
  docByPeriod: Map<string, string>;
  fallbackUrl: string | null;
}): React.ReactElement {
  const [open, setOpen] = React.useState<number | null>(null);
  // Facts with a numeric value get a right-aligned figure column; a group of
  // pure prose (key audit matters, strategy) has nothing to align, so it reads
  // as a list instead of a table with one empty column.
  const numeric = facts.some((f) => f.value_crore !== null || f.value_text);

  // A warning on every single row is not a warning.
  //
  // Unit disagreement is a property of the TABLE the figures were read from —
  // one header, one unit, one verdict — so when it fires it usually fires on
  // all of them at once. Sixteen identical amber chips down a column teach a
  // reader to filter the colour out, which costs the badge its meaning on the
  // one table where only some rows disagree. So a unanimous verdict is stated
  // once, on the group; per-row chips are kept for the mixed case, where the
  // row is genuinely the thing in doubt.
  const warnRows = facts.filter((f) => unitVerdict(f.unit_agrees) === "warn");
  const groupWideWarn = warnRows.length > 1 && warnRows.length === facts.length;

  return (
    <div
      style={{
        border: "1px solid var(--glass-border)",
        borderRadius: "var(--radius-md)",
        background: "var(--bg-primary)",
        overflow: "hidden",
      }}
    >
      <div
        style={{
          padding: "9px 14px",
          borderBottom: "1px solid var(--glass-border)",
          background: "var(--bg-secondary)",
          fontFamily: "var(--font-ui)",
          fontSize: 12,
          fontWeight: 600,
          color: "var(--text-secondary)",
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          gap: 10,
        }}
      >
        <span>{grp}</span>
        {groupWideWarn ? (
          <Chip tone="warn" title={warnRows[0]?.unit_agrees ?? ""}>
            unit unconfirmed for this table
          </Chip>
        ) : null}
      </div>

      <div>
        {facts.map((f, i) => {
          const verdict = unitVerdict(f.unit_agrees);
          const url = (f.period && docByPeriod.get(f.period)) || fallbackUrl;
          const isOpen = open === i;
          return (
            <div
              key={i}
              style={{
                borderBottom: i === facts.length - 1 ? "none" : "1px solid var(--glass-border)",
              }}
            >
              <div
                style={{
                  display: "grid",
                  gridTemplateColumns: numeric ? "1fr auto auto" : "1fr auto",
                  gap: 12,
                  alignItems: "center",
                  padding: "9px 14px",
                }}
              >
                <div style={{ minWidth: 0 }}>
                  <div
                    style={{
                      fontSize: 13,
                      color: "var(--text-primary)",
                      overflow: "hidden",
                      textOverflow: "ellipsis",
                      whiteSpace: "nowrap",
                    }}
                    title={f.label ?? ""}
                  >
                    {f.label ?? DASH}
                  </div>
                  <div style={{ display: "flex", gap: 5, marginTop: 3, flexWrap: "wrap" }}>
                    {f.period ? <Chip>{f.period}</Chip> : null}
                    {f.basis ? <Chip>{f.basis}</Chip> : null}
                    {verdict === "warn" && !groupWideWarn ? (
                      <Chip tone="warn" title={f.unit_agrees ?? ""}>
                        unit unconfirmed
                      </Chip>
                    ) : null}
                    {f.grounding && f.grounding !== "exact" ? (
                      <Chip title="Value normalised from the source text rather than lifted verbatim">
                        {f.grounding}
                      </Chip>
                    ) : null}
                  </div>
                </div>

                {numeric ? (
                  <div
                    style={{
                      textAlign: "right",
                      fontVariantNumeric: "tabular-nums",
                      fontSize: 13,
                      fontWeight: 550,
                      color: "var(--text-primary)",
                      whiteSpace: "nowrap",
                    }}
                  >
                    {f.value_crore !== null
                      ? `₹${num(f.value_crore, { dp: 0 })} cr`
                      : (f.value_text ?? DASH)}
                    {f.unit_text && f.value_crore === null ? (
                      <span style={{ color: "var(--text-tertiary)", fontWeight: 400 }}>
                        {" "}{f.unit_text}
                      </span>
                    ) : null}
                  </div>
                ) : null}

                {/* The page chip is the whole promise of this section: one
                    click from a figure to the page it was read off. */}
                {f.page ? (
                  <Chip
                    as={url ? "a" : "span"}
                    href={url ? `${url}#page=${f.page}` : undefined}
                    tone="accent"
                    title={f.quote ? "Open the report at this page" : undefined}
                    onClick={!url ? () => setOpen(isOpen ? null : i) : undefined}
                  >
                    p.{f.page}
                  </Chip>
                ) : (
                  <span style={{ width: 1 }} />
                )}
              </div>

              {f.quote ? (
                <button
                  type="button"
                  onClick={() => setOpen(isOpen ? null : i)}
                  style={{
                    display: "block",
                    width: "100%",
                    textAlign: "left",
                    border: "none",
                    background: "transparent",
                    cursor: "pointer",
                    padding: "0 14px 9px",
                    font: "inherit",
                  }}
                >
                  <span
                    style={{
                      fontSize: 12,
                      color: "var(--text-tertiary)",
                      display: "block",
                      overflow: isOpen ? "visible" : "hidden",
                      textOverflow: "ellipsis",
                      whiteSpace: isOpen ? "normal" : "nowrap",
                      fontStyle: "italic",
                    }}
                  >
                    “{f.quote}”
                  </span>
                </button>
              ) : null}
            </div>
          );
        })}
      </div>
    </div>
  );
}
