"use client";

/**
 * The financial table primitive — TanStack Table v8, dressed in our own tokens.
 *
 * Headless is the point. Every styled table library ships its own colour and
 * spacing system, which then has to be fought back into line with
 * `--glass-border` and `--font-ui`; TanStack ships only the row model, so the
 * markup below is ours and matches the cards it sits inside.
 *
 * Two behaviours a financial table needs that a generic one does not:
 *
 *   · the label column PINS. Twenty quarters or twenty-two fiscal years scroll
 *     horizontally, and a number you cannot name is not information. It stays
 *     put via `position: sticky` on the first cell of every row.
 *   · numbers are `tabular-nums` and right-aligned, so digits stack in columns
 *     and a reader can compare magnitudes down the page without reading them.
 *
 * Nulls render as an em-dash, never as 0 or a blank. Blank reads as a rendering
 * bug and 0 is a claim we have not got — a lot of these columns are genuinely
 * sparse (operating margin is filled for ~59% of recent quarters).
 */

import {
  type ColumnDef,
  flexRender,
  getCoreRowModel,
  useReactTable,
} from "@tanstack/react-table";
import * as React from "react";

export type FinTableProps<T> = {
  data: T[];
  columns: ColumnDef<T, unknown>[];
  /** Rendered above the scroll area, inside the same border. */
  caption?: React.ReactNode;
  /** Height at which the body starts scrolling vertically. Omit for auto. */
  maxHeight?: number;
  emptyMessage?: string;
};

export function FinTable<T>({
  data,
  columns,
  caption,
  maxHeight,
  emptyMessage = "No rows.",
}: FinTableProps<T>): React.ReactElement {
  const table = useReactTable({
    data,
    columns,
    getCoreRowModel: getCoreRowModel(),
  });

  return (
    <div
      style={{
        border: "1px solid var(--glass-border)",
        borderRadius: "var(--radius-md)",
        background: "var(--bg-primary)",
        overflow: "hidden",
      }}
    >
      {caption ? (
        <div
          style={{
            padding: "10px 14px",
            borderBottom: "1px solid var(--glass-border)",
            fontSize: 12,
            color: "var(--text-tertiary)",
            fontFamily: "var(--font-ui)",
          }}
        >
          {caption}
        </div>
      ) : null}

      <div style={{ overflow: "auto", maxHeight }}>
        <table
          style={{
            borderCollapse: "separate",
            borderSpacing: 0,
            width: "100%",
            fontFamily: "var(--font-ui)",
            fontSize: 13,
          }}
        >
          <thead>
            {table.getHeaderGroups().map((hg) => (
              <tr key={hg.id}>
                {hg.headers.map((h, i) => {
                  const meta = (h.column.columnDef.meta ?? {}) as {
                    numeric?: boolean;
                  };
                  return (
                    <th
                      key={h.id}
                      style={{
                        // The header row and the pinned column both stick, so
                        // the top-left cell needs the higher z-index or the
                        // label column paints over its own heading.
                        position: "sticky",
                        top: 0,
                        left: i === 0 ? 0 : undefined,
                        zIndex: i === 0 ? 3 : 2,
                        background: "var(--bg-elevated)",
                        textAlign: meta.numeric ? "right" : "left",
                        padding: "8px 14px",
                        fontSize: 11,
                        fontWeight: 600,
                        letterSpacing: "0.02em",
                        color: "var(--text-tertiary)",
                        whiteSpace: "nowrap",
                        borderBottom: "1px solid var(--glass-border)",
                      }}
                    >
                      {h.isPlaceholder
                        ? null
                        : flexRender(h.column.columnDef.header, h.getContext())}
                    </th>
                  );
                })}
              </tr>
            ))}
          </thead>
          <tbody>
            {table.getRowModel().rows.length === 0 ? (
              <tr>
                <td
                  colSpan={columns.length}
                  style={{
                    padding: "22px 14px",
                    color: "var(--text-tertiary)",
                    fontSize: 13,
                  }}
                >
                  {emptyMessage}
                </td>
              </tr>
            ) : (
              table.getRowModel().rows.map((row) => (
                <tr key={row.id}>
                  {row.getVisibleCells().map((cell, i) => {
                    const meta = (cell.column.columnDef.meta ?? {}) as {
                      numeric?: boolean;
                    };
                    return (
                      <td
                        key={cell.id}
                        style={{
                          position: i === 0 ? "sticky" : undefined,
                          left: i === 0 ? 0 : undefined,
                          zIndex: i === 0 ? 1 : undefined,
                          background: "var(--bg-primary)",
                          textAlign: meta.numeric ? "right" : "left",
                          padding: "8px 14px",
                          whiteSpace: "nowrap",
                          color: "var(--text-primary)",
                          fontVariantNumeric: meta.numeric ? "tabular-nums" : undefined,
                          borderBottom: "1px solid var(--glass-border)",
                        }}
                      >
                        {flexRender(cell.column.columnDef.cell, cell.getContext())}
                      </td>
                    );
                  })}
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}

/** An absent value is an em-dash. Not 0 (a number we do not have), not blank
 *  (indistinguishable from a broken cell). Used by every column below. */
export const DASH = "—";

export function num(
  v: number | null | undefined,
  opts: { dp?: number; pct?: boolean; signed?: boolean } = {},
): string {
  if (v === null || v === undefined || Number.isNaN(v)) return DASH;
  const { dp = 0, pct = false, signed = false } = opts;
  const body = Math.abs(v).toLocaleString("en-IN", {
    minimumFractionDigits: dp,
    maximumFractionDigits: dp,
  });
  const sign = v < 0 ? "−" : signed ? "+" : "";
  return `${sign}${body}${pct ? "%" : ""}`;
}

/** Colour by direction, using the same profit/loss tokens the price strip uses
 *  so a positive quarter and a positive day read as the same green. */
export function toneOf(v: number | null | undefined): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "var(--text-tertiary)";
  if (v > 0) return "var(--color-profit)";
  if (v < 0) return "var(--color-loss)";
  return "var(--text-secondary)";
}
