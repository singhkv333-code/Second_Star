"use client";

/**
 * The four statements, whole.
 *
 * The panel on the stock page shows four rows of the balance sheet and two of
 * the quarterly P&L. That was never a data problem: `mc.statement_lines` holds
 * a hundred and twenty line items per company across twenty-three periods,
 * sectioned as MC files them, and only the balance sheet was ever read out of
 * it. This page reads all four.
 *
 * One table renders every statement because MC publishes all four in the same
 * shape — a line item, a section, a value per period. That includes the ratio
 * sheet, which is why "a ratio area with a lot of ratios" costs a tab here
 * rather than a subsystem: thirty-eight ratios under Per Share, Profitability,
 * Liquidity, Coverage and Valuation, filed, not derived.
 *
 * Nothing on this page is computed. A number that disagrees with the summary
 * panel would be worse than a number that is missing, and the only way to be
 * sure they agree is for both to quote the same store.
 */

import * as React from "react";
import Link from "next/link";
import { ArrowLeft } from "lucide-react";

import {
  getStatement,
  type StatementResponse,
  type StatementType,
  type BalanceSheetRow,
} from "@/lib/api";
import { isError } from "@/lib/types";
import { CompanyLogo } from "@/components/CompanyLogo";
import { useCompanyLogos } from "@/hooks/useCompanyLogos";
import { EmptyNote, PanelSkeleton, usePhone } from "./chrome";

const TABS: { value: StatementType; label: string }[] = [
  { value: "profit_loss", label: "Profit & Loss" },
  { value: "balance_sheet", label: "Balance Sheet" },
  { value: "cash_flow", label: "Cash Flow" },
  { value: "ratios", label: "Ratios" },
];

type Basis = "consolidated" | "standalone";

export function StatementsPage({
  symbol,
  initialTab = "profit_loss",
}: {
  symbol: string;
  initialTab?: StatementType;
}): React.ReactElement {
  const [tab, setTab] = React.useState<StatementType>(initialTab);
  const [basis, setBasis] = React.useState<Basis>("consolidated");
  const [years, setYears] = React.useState(10);
  // Keyed by tab+basis+years so switching back to a tab already fetched is
  // instant and does not re-flash a skeleton over numbers already read.
  const [cache, setCache] = React.useState<Record<string, StatementResponse>>({});
  const [dead, setDead] = React.useState(false);

  const key = `${tab}:${basis}:${years}`;
  const data = cache[key];

  React.useEffect(() => {
    if (data) return;
    let cancelled = false;
    getStatement(symbol, tab, basis, years)
      .then((r) => {
        if (cancelled) return;
        if (isError(r)) { setDead(true); return; }
        setCache((prev) => ({ ...prev, [key]: r.data }));
      })
      .catch(() => { if (!cancelled) setDead(true); });
    return () => { cancelled = true; };
  }, [symbol, tab, basis, years, key, data]);

  const logos = useCompanyLogos([symbol]);
  const name = data?.company?.name ?? symbol;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 18, paddingBottom: 40 }}>
      {/* ── heading ─────────────────────────────────────────────────────── */}
      <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
        <Link
          href={`/stock/${encodeURIComponent(symbol)}`}
          style={{
            display: "inline-flex", alignItems: "center", gap: 6,
            fontFamily: "var(--font-ui)", fontSize: "var(--sd-f125)",
            color: "var(--text-secondary)", textDecoration: "none", width: "fit-content",
          }}
        >
          <ArrowLeft size={14} aria-hidden="true" />
          {symbol}
        </Link>

        <div style={{ display: "flex", alignItems: "center", gap: 12, minWidth: 0 }}>
          <CompanyLogo
            logoUrl={logos[symbol.toUpperCase()] ?? null}
            name={name}
            symbol={symbol}
            size={38}
          />
          <div style={{ minWidth: 0 }}>
            <h1 style={{
              margin: 0, fontFamily: "var(--font-ui)", fontSize: 26, fontWeight: 600,
              letterSpacing: "-0.025em", color: "var(--text-primary)",
              overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
            }}>
              {name}
            </h1>
            <div style={{ fontSize: "var(--sd-f125)", color: "var(--text-secondary)" }}>
              Financial statements
            </div>
          </div>
        </div>
      </div>

      {/* ── statement tabs ──────────────────────────────────────────────── */}
      <div style={{ borderBottom: "1px solid var(--glass-border)", overflowX: "auto" }}>
        <div role="tablist" style={{ display: "flex", gap: 0, width: "max-content", minWidth: "100%" }}>
          {TABS.map((t) => {
            const on = t.value === tab;
            return (
              <button
                key={t.value}
                type="button"
                role="tab"
                aria-selected={on}
                onClick={() => setTab(t.value)}
                style={{
                  padding: "8px 16px", border: "none", background: "transparent",
                  cursor: "pointer", whiteSpace: "nowrap",
                  fontFamily: "var(--font-ui)", fontSize: "var(--sd-f13)",
                  fontWeight: on ? 600 : 400,
                  color: on ? "var(--text-primary)" : "var(--text-secondary)",
                  borderBottom: `2px solid ${on ? "var(--text-primary)" : "transparent"}`,
                  marginBottom: -1, transition: "color 150ms, border-color 150ms",
                }}
              >
                {t.label}
              </button>
            );
          })}
        </div>
      </div>

      {/* ── basis + span ────────────────────────────────────────────────── */}
      <div style={{
        display: "flex", alignItems: "flex-end", justifyContent: "space-between",
        gap: 14, flexWrap: "wrap",
        borderBottom: "1px solid var(--glass-border)",
      }}>
        <Choice
          value={basis}
          options={[
            { value: "consolidated", label: "Consolidated" },
            { value: "standalone", label: "Standalone" },
          ]}
          onChange={(v) => setBasis(v as Basis)}
        />
        <Choice
          align="right"
          value={String(years)}
          options={[
            { value: "5", label: "5 years" },
            { value: "10", label: "10 years" },
            { value: "25", label: "All" },
          ]}
          onChange={(v) => setYears(Number(v))}
        />
      </div>

      {/* ── the grid ────────────────────────────────────────────────────── */}
      {dead ? (
        <EmptyNote>These statements could not be loaded.</EmptyNote>
      ) : !data ? (
        <PanelSkeleton rows={12} />
      ) : !data.available || !data.rows.length ? (
        <EmptyNote>
          No {TABS.find((t) => t.value === tab)?.label.toLowerCase()} has been
          filed for this company on a {basis} basis.
        </EmptyNote>
      ) : (
        <StatementGrid data={data} />
      )}
    </div>
  );
}

/** The page's one toggle: an underline tab strip, the same control the
 *  statement tabs above and the Financial Performance panel use. Bare weighted
 *  text was a third way of saying "this one is live" on a page that already
 *  had two, and at two words it read as a sentence rather than a control. */
function Choice({
  value, options, onChange, align = "left",
}: {
  value: string;
  options: { value: string; label: string }[];
  onChange: (v: string) => void;
  /** A strip at the row's right edge pads its LAST tab flush instead of its
   *  first, so the labels line up with the panel edge either way. */
  align?: "left" | "right";
}): React.ReactElement {
  return (
    <div role="tablist" style={{ display: "inline-flex", alignItems: "flex-end" }}>
      {options.map((o, i) => {
        const on = o.value === value;
        const flush = align === "left" ? i === 0 : i === options.length - 1;
        return (
          <button
            key={o.value}
            type="button"
            role="tab"
            aria-selected={on}
            onClick={() => onChange(o.value)}
            style={{
              border: "none", background: "transparent", cursor: "pointer",
              padding: "6px 14px",
              paddingLeft: flush && align === "left" ? 0 : 14,
              paddingRight: flush && align === "right" ? 0 : 14,
              marginBottom: -1,
              borderBottom: `2px solid ${on ? "var(--text-primary)" : "transparent"}`,
              fontFamily: "var(--font-ui)", fontSize: "var(--sd-f125)",
              fontWeight: on ? 600 : 400,
              color: on ? "var(--text-primary)" : "var(--text-secondary)",
              transition: "color 150ms, border-color 150ms",
            }}
          >
            {o.label}
          </button>
        );
      })}
    </div>
  );
}

/**
 * One statement.
 *
 * The line-item column sticks while the years scroll, because a number in the
 * eighth column means nothing once its name has left the screen. Section
 * headers are rows rather than separate tables so the sticky column and the
 * scroll stay in one grid.
 *
 * Totals are picked out in weight. MC does not mark them, so they are found by
 * name — "Total …", and the handful of statement lines that are totals without
 * saying so. A reader scanning for the bottom line should not have to read
 * thirty rows at one weight to find it.
 */
function StatementGrid({ data }: { data: StatementResponse }): React.ReactElement {
  const phone = usePhone();
  const { periods, rows, unit, statement } = data;
  // Ratio values are already ×/%/₹ per share — an "Rs. Cr." unit note over
  // that column would be wrong, and MC sends one anyway.
  const showUnit = statement !== "ratios" && unit;

  const nameW = phone ? 150 : 260;

  return (
    <div>
      <div
        className="stock-bleed"
        style={{
          border: "1px solid var(--glass-border)",
          borderRadius: "var(--radius-md)",
          background: "var(--bg-primary)",
          overflow: "hidden",
        }}
      >
        <div style={{ overflowX: "auto", WebkitOverflowScrolling: "touch" }}>
        <table style={{
          // `separate` so the sticky header and sticky label column keep their
          // own borders — a collapsed table drops the shared edge as soon as a
          // cell is lifted out of flow, and the header rule disappears on
          // scroll. This is FinTable's model, applied to a hand-rolled grid.
          borderCollapse: "separate", borderSpacing: 0,
          width: "max-content", minWidth: "100%",
          fontFamily: "var(--font-ui)", fontSize: 13,
        }}>
          <thead>
            <tr>
              <th style={{
                position: "sticky", left: 0, top: 0, zIndex: 3,
                background: "var(--bg-elevated)",
                minWidth: nameW, maxWidth: nameW, width: nameW,
                padding: "8px 14px", textAlign: "left",
                fontSize: 11, fontWeight: 600, letterSpacing: "0.02em",
                color: "var(--text-tertiary)", whiteSpace: "nowrap",
                borderBottom: "1px solid var(--glass-border)",
                borderRight: "1px solid var(--glass-border)",
              }}>
                {showUnit ? `₹ ${unit?.replace(/^Rs\.\s*/, "")}` : "Line item"}
              </th>
              {periods.map((p, i) => (
                <th key={p} style={{
                  position: "sticky", top: 0, zIndex: 2,
                  background: "var(--bg-elevated)",
                  padding: "8px 14px", textAlign: "right", whiteSpace: "nowrap",
                  fontSize: 11, fontWeight: 600, letterSpacing: "0.02em",
                  // The most recent period is the one being read; the rest are
                  // the context it is read against.
                  color: i === 0 ? "var(--text-primary)" : "var(--text-tertiary)",
                  borderBottom: "1px solid var(--glass-border)",
                }}>
                  {p}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((r, idx) => (
              <Row
                key={`${r.line_item}-${idx}`}
                row={r}
                periods={periods}
                nameW={nameW}
                showSection={r.section !== null && r.section !== rows[idx - 1]?.section}
                // A ratio sheet has no totals — every row is a measure, and
                // "Net Profit/Share" matching a rule written for "Net Profit"
                // put a per-share ratio in bold as if it were a bottom line.
                marksTotals={statement !== "ratios"}
              />
            ))}
          </tbody>
        </table>
        </div>
      </div>
    </div>
  );
}

/** Lines that are a total without the word. */
const TOTAL_RE = /^(total|net profit|profit\/loss for the period|profit\/loss before tax|gross profit)/i;

function Row({
  row, periods, nameW, showSection, marksTotals,
}: {
  row: BalanceSheetRow;
  periods: string[];
  nameW: number;
  showSection: boolean;
  marksTotals: boolean;
}): React.ReactElement {
  const isTotal = marksTotals && TOTAL_RE.test(row.line_item.trim());
  return (
    <>
      {showSection && row.section ? (
        <tr>
          <td
            colSpan={periods.length + 1}
            style={{
              padding: "8px 14px",
              fontSize: 11, fontWeight: 600, letterSpacing: "0.02em",
              color: "var(--text-tertiary)",
              background: "var(--bg-elevated)",
              borderBottom: "1px solid var(--glass-border)",
            }}
          >
            {row.section}
          </td>
        </tr>
      ) : null}
      <tr>
        <th
          scope="row"
          style={{
            position: "sticky", left: 0, zIndex: 1,
            background: "var(--bg-primary)",
            minWidth: nameW, maxWidth: nameW, width: nameW,
            padding: "8px 14px", textAlign: "left",
            fontSize: 13,
            fontWeight: isTotal ? 600 : 400,
            color: "var(--text-primary)",
            borderBottom: "1px solid var(--glass-border)",
            borderRight: "1px solid var(--glass-border)",
            // Long MC line items wrap inside the fixed column rather than
            // widening it and pushing every year off the screen.
            whiteSpace: "normal", lineHeight: 1.35,
          }}
          title={row.line_item}
        >
          {row.line_item}
        </th>
        {periods.map((p, i) => {
          // The filed text is preferred over the number: MC's own formatting
          // carries the sign convention and the decimals it reported at, and
          // reformatting a float loses both.
          const text = row.value_texts[p];
          const val = row.values[p];
          const shown = text ?? (val === null || val === undefined ? null : String(val));
          return (
            <td key={p} style={{
              padding: "8px 14px", textAlign: "right", whiteSpace: "nowrap",
              fontFamily: "var(--font-mono)", fontSize: 13,
              fontVariantNumeric: "tabular-nums",
              fontWeight: isTotal || i === 0 ? 600 : 400,
              borderBottom: "1px solid var(--glass-border)",
              color: shown === null
                ? "var(--text-tertiary)"
                : i === 0 ? "var(--text-primary)" : "var(--text-secondary)",
            }}>
              {shown ?? "—"}
            </td>
          );
        })}
      </tr>
    </>
  );
}
