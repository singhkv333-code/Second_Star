"use client";

/**
 * Quarters — `quarterly_metrics`, rendered as it is stored.
 *
 * The table holds ~50 precomputed columns per quarter: margins, YoY, QoQ and
 * TTM are all already there. Nothing here recomputes them. Deriving revenue
 * growth on the client would give the page a second, quietly different answer
 * from the chat side and the screener, which both read the stored column.
 *
 * The column set is chosen by FILL, not by what would look complete. Measured
 * across recent quarters: net profit 100%, EPS TTM 97%, revenue 94%, YoY 88%,
 * EBITDA 64%, operating margin 59%, NPA 1%. So revenue/PAT/EPS lead, the
 * bank-only ratios appear as a column group only for companies that report
 * them, and every cell can be an em-dash without the row looking broken.
 */

import type { ColumnDef } from "@tanstack/react-table";
import * as React from "react";
import { Area, AreaChart, Bar, BarChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

import type { QuarterRow, QuartersResponse } from "@/lib/api";
import { DASH, FinTable, num, toneOf } from "./FinTable";
import { PanelHead, Segmented } from "./chrome";

/** Crore figures run to five digits; a raw `1,04,232` column is unreadable at
 *  a glance, so scale to thousands of crore past 10k and keep the unit in the
 *  header rather than repeating it in every cell. */
function cr(v: number | null): string {
  if (v === null || v === undefined) return DASH;
  if (Math.abs(v) >= 10000) return num(v / 1000, { dp: 1 }) + "k";
  return num(v, { dp: 0 });
}

function Pct({ v, bps = false }: { v: number | null; bps?: boolean }): React.ReactElement {
  if (v === null || v === undefined) return <span style={{ color: "var(--text-tertiary)" }}>{DASH}</span>;
  return (
    <span style={{ color: toneOf(v) }}>
      {num(v, { dp: bps ? 0 : 1, signed: true })}
      {bps ? " bps" : "%"}
    </span>
  );
}

export function QuartersPanel({
  data,
  basis,
  onBasisChange,
}: {
  data: QuartersResponse;
  basis: "consolidated" | "standalone";
  onBasisChange: (b: "consolidated" | "standalone") => void;
}): React.ReactElement {
  const rows = data.quarters;

  // A column that is empty for THIS company is not shown for this company.
  //
  // The obvious version of this table shows a fixed set of columns and lets
  // the sparse ones fill with em-dashes — and then TCS renders EBITDA and
  // operating margin as two columns of nothing, because it files neither.
  // Dead columns are worse than missing ones: they take horizontal space on a
  // table that already scrolls, and they read as data the page failed to load.
  //
  // So the column set is decided per company by what it actually reports. Same
  // rule that decides the tabs, one level down. A lender's NPA and ROA appear
  // by exactly the same test rather than by a special case.
  const has = React.useCallback(
    (k: keyof QuarterRow) => rows.some((r) => r[k] !== null && r[k] !== undefined),
    [rows],
  );

  const columns = React.useMemo<ColumnDef<QuarterRow, unknown>[]>(() => {
    const base: ColumnDef<QuarterRow, unknown>[] = [
      {
        id: "period",
        header: "Quarter",
        accessorFn: (r) => r.period_label ?? r.period_end,
        cell: (c) => (
          <span style={{ fontWeight: 550 }}>{String(c.getValue() ?? DASH)}</span>
        ),
      },
      { id: "revenue", header: "Revenue ₹cr", accessorFn: (r) => r.revenue,
        cell: (c) => cr(c.getValue() as number | null), meta: { numeric: true } },
      { id: "rev_yoy", header: "Rev YoY", accessorFn: (r) => r.revenue_yoy_pct,
        cell: (c) => <Pct v={c.getValue() as number | null} />, meta: { numeric: true } },
      { id: "rev_qoq", header: "Rev QoQ", accessorFn: (r) => r.revenue_qoq_pct,
        cell: (c) => <Pct v={c.getValue() as number | null} />, meta: { numeric: true } },
      ...(has("ebitda") ? [{ id: "ebitda", header: "EBITDA ₹cr", accessorFn: (r: QuarterRow) => r.ebitda,
        cell: (c) => cr(c.getValue() as number | null), meta: { numeric: true } } as ColumnDef<QuarterRow, unknown>] : []),
      { id: "pat", header: "PAT ₹cr", accessorFn: (r) => r.net_profit,
        cell: (c) => cr(c.getValue() as number | null), meta: { numeric: true } },
      { id: "pat_yoy", header: "PAT YoY", accessorFn: (r) => r.net_profit_yoy_pct,
        cell: (c) => <Pct v={c.getValue() as number | null} />, meta: { numeric: true } },
      ...(has("operating_margin_pct") ? [{ id: "opm", header: "OPM",
        accessorFn: (r: QuarterRow) => r.operating_margin_pct,
        cell: (c) => {
          const v = c.getValue() as number | null;
          return v === null ? DASH : num(v, { dp: 1, pct: true });
        }, meta: { numeric: true } } as ColumnDef<QuarterRow, unknown>] : []),
      ...(has("net_margin_pct") ? [{ id: "npm", header: "Net margin",
        accessorFn: (r: QuarterRow) => r.net_margin_pct,
        cell: (c) => {
          const v = c.getValue() as number | null;
          return v === null ? DASH : num(v, { dp: 1, pct: true });
        }, meta: { numeric: true } } as ColumnDef<QuarterRow, unknown>] : []),
      { id: "eps", header: "EPS ₹", accessorFn: (r) => r.eps_basic,
        cell: (c) => num(c.getValue() as number | null, { dp: 2 }), meta: { numeric: true } },
      { id: "eps_ttm", header: "EPS TTM ₹", accessorFn: (r) => r.eps_ttm,
        cell: (c) => num(c.getValue() as number | null, { dp: 2 }), meta: { numeric: true } },
    ];
    if (has("gross_npa_pct") || has("roa_pct")) {
      base.push(
        { id: "gnpa", header: "Gross NPA", accessorFn: (r) => r.gross_npa_pct,
          cell: (c) => {
            const v = c.getValue() as number | null;
            return v === null ? DASH : num(v, { dp: 2, pct: true });
          }, meta: { numeric: true } },
        { id: "roa", header: "ROA", accessorFn: (r) => r.roa_pct,
          cell: (c) => {
            const v = c.getValue() as number | null;
            return v === null ? DASH : num(v, { dp: 2, pct: true });
          }, meta: { numeric: true } },
      );
    }
    return base;
  }, [has]);

  // Charts read oldest → newest; the table reads newest first. Same data, and
  // both orders are the natural one for their own shape.
  const chron = React.useMemo(() => [...rows].reverse(), [rows]);
  const latest = rows[0];

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 18 }}>
      <PanelHead
        title="Quarterly results"
        right={
          data.bases_available.length > 1 ? (
            <Segmented
              value={basis}
              options={data.bases_available.map((b) => ({
                value: b,
                label: b === "consolidated" ? "Consolidated" : "Standalone",
              }))}
              onChange={(v) => onBasisChange(v as "consolidated" | "standalone")}
            />
          ) : null
        }
      />

      {latest ? (
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "repeat(auto-fit, minmax(150px, 1fr))",
            gap: 10,
          }}
        >
          <Stat label="Revenue" value={`₹${cr(latest.revenue)} cr`} delta={latest.revenue_yoy_pct} deltaLabel="YoY" />
          <Stat label="Net profit" value={`₹${cr(latest.net_profit)} cr`} delta={latest.net_profit_yoy_pct} deltaLabel="YoY" />
          <Stat label="EPS (TTM)" value={latest.eps_ttm !== null ? `₹${num(latest.eps_ttm, { dp: 2 })}` : DASH} delta={latest.np_ttm_yoy_pct} deltaLabel="PAT TTM YoY" />
          {/* Whichever margin this company reports. A tile hard-wired to
              operating margin reads "—" forever for the many companies that
              only file a net margin, which wastes the most prominent row on
              the panel. */}
          {latest.operating_margin_pct !== null ? (
            <Stat
              label="Operating margin"
              value={num(latest.operating_margin_pct, { dp: 1, pct: true })}
              delta={latest.operating_margin_yoy_bps}
              deltaLabel="YoY bps"
              bps
            />
          ) : (
            <Stat
              label="Net margin"
              value={latest.net_margin_pct !== null ? num(latest.net_margin_pct, { dp: 1, pct: true }) : DASH}
              delta={latest.net_margin_yoy_bps}
              deltaLabel="YoY bps"
              bps
            />
          )}
        </div>
      ) : null}

      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(260px, 1fr))", gap: 12 }}>
        <MiniChart title="Revenue by quarter, ₹cr">
          <BarChart data={chron}>
            <XAxis dataKey="period_label" hide />
            <YAxis hide />
            <Tooltip content={<MiniTip unit="₹cr" field="revenue" />} cursor={{ fill: "var(--bg-elevated)" }} />
            <Bar dataKey="revenue" fill="var(--pivot-blue)" radius={[2, 2, 0, 0]} />
          </BarChart>
        </MiniChart>
        <MiniChart title="Net profit by quarter, ₹cr">
          <BarChart data={chron}>
            <XAxis dataKey="period_label" hide />
            <YAxis hide />
            <Tooltip content={<MiniTip unit="₹cr" field="net_profit" />} cursor={{ fill: "var(--bg-elevated)" }} />
            <Bar dataKey="net_profit" fill="var(--color-profit)" radius={[2, 2, 0, 0]} />
          </BarChart>
        </MiniChart>
        <MiniChart title="Revenue TTM, ₹cr">
          <AreaChart data={chron}>
            <XAxis dataKey="period_label" hide />
            <YAxis hide domain={["dataMin", "dataMax"]} />
            <Tooltip content={<MiniTip unit="₹cr" field="rev_ttm" />} />
            <Area
              dataKey="rev_ttm"
              stroke="var(--pivot-blue)"
              strokeWidth={1.5}
              fill="var(--accent-wash)"
              connectNulls
            />
          </AreaChart>
        </MiniChart>
      </div>

      <FinTable data={rows} columns={columns} maxHeight={430} />
    </div>
  );
}

function Stat({
  label, value, delta, deltaLabel, bps = false,
}: {
  label: string; value: string; delta: number | null; deltaLabel: string; bps?: boolean;
}): React.ReactElement {
  return (
    <div
      style={{
        border: "1px solid var(--glass-border)",
        borderRadius: "var(--radius-sm)",
        background: "var(--bg-primary)",
        padding: "11px 13px",
      }}
    >
      <div style={{ fontSize: 11, color: "var(--text-tertiary)", marginBottom: 4 }}>{label}</div>
      <div style={{ fontSize: 17, fontWeight: 600, letterSpacing: "-0.01em", fontVariantNumeric: "tabular-nums" }}>
        {value}
      </div>
      <div style={{ fontSize: 11, marginTop: 3, fontVariantNumeric: "tabular-nums" }}>
        <Pct v={delta} bps={bps} />
        <span style={{ color: "var(--text-tertiary)" }}> {deltaLabel}</span>
      </div>
    </div>
  );
}

function MiniChart({ title, children }: { title: string; children: React.ReactElement }): React.ReactElement {
  return (
    <div
      style={{
        border: "1px solid var(--glass-border)",
        borderRadius: "var(--radius-sm)",
        background: "var(--bg-primary)",
        padding: "11px 13px 6px",
      }}
    >
      <div style={{ fontSize: 11, color: "var(--text-tertiary)", marginBottom: 6 }}>{title}</div>
      <div style={{ height: 92 }}>
        <ResponsiveContainer width="100%" height="100%">
          {children}
        </ResponsiveContainer>
      </div>
    </div>
  );
}

/** Recharts' default tooltip carries its own white card and shadow. This one
 *  is the product's surface, so a hover does not reveal a second design. */
function MiniTip({
  active, payload, unit, field,
}: {
  active?: boolean;
  payload?: { payload: QuarterRow }[];
  unit: string;
  field: keyof QuarterRow;
}): React.ReactElement | null {
  const first = active ? payload?.[0] : undefined;
  if (!first) return null;
  const row = first.payload;
  const v = row[field] as number | null;
  return (
    <div
      style={{
        background: "var(--bg-primary)",
        border: "1px solid var(--glass-border)",
        borderRadius: 8,
        padding: "6px 9px",
        fontSize: 12,
        fontFamily: "var(--font-ui)",
        color: "var(--text-primary)",
      }}
    >
      <div style={{ color: "var(--text-tertiary)", fontSize: 11 }}>
        {row.period_label ?? row.period_end}
      </div>
      <div style={{ fontVariantNumeric: "tabular-nums" }}>
        {v === null ? DASH : `${num(v, { dp: 0 })} ${unit}`}
      </div>
    </div>
  );
}
