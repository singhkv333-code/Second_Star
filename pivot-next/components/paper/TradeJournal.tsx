"use client";

import * as React from "react";
import { useEffect, useState } from "react";
import { getPaperFills, type PaperFillRow } from "@/lib/api";
import { isError } from "@/lib/types";
import {
  inr,
  signedInr,
  qty,
  pnlColor,
  relativeTime,
} from "@/components/paper/format";
import { Skeleton } from "@/components/ui/skeleton";

type S<T> =
  | { k: "loading" }
  | { k: "ok"; d: T }
  | { k: "err" }
  | { k: "empty" };

/** Grid template shared by the header + every body row so columns stay aligned. */
const COLS =
  "110px minmax(96px, 1.4fr) 64px minmax(64px, 0.9fr) minmax(96px, 1.1fr) minmax(96px, 1.1fr) minmax(88px, 1fr) minmax(96px, 1.1fr)";

function fillTime(f: PaperFillRow): number {
  if (!f.filled_at) return 0;
  const t = Date.parse(f.filled_at);
  return Number.isNaN(t) ? 0 : t;
}

function SideBadge({ side }: { side: string }): React.ReactElement {
  const isBuy = side.toUpperCase() === "BUY";
  const color = isBuy ? "var(--color-profit)" : "var(--color-loss)";
  return (
    <span
      className="q-mono"
      style={{
        display: "inline-flex",
        alignItems: "center",
        justifyContent: "center",
        fontSize: 10,
        fontWeight: 600,
        letterSpacing: "0.04em",
        padding: "2px 8px",
        borderRadius: "var(--radius-pill)",
        color,
        background: "color-mix(in srgb, " + color + " 12%, transparent)",
        border: "1px solid color-mix(in srgb, " + color + " 32%, transparent)",
        whiteSpace: "nowrap",
      }}
    >
      {side.toUpperCase()}
    </span>
  );
}

function HeaderRow({ semantic }: { semantic?: boolean }): React.ReactElement {
  const ch = semantic ? "columnheader" : undefined;
  return (
    <div
      className="grid items-center"
      role={semantic ? "row" : undefined}
      style={{
        gridTemplateColumns: COLS,
        gap: 12,
        padding: "10px 16px",
        background: "var(--bg-secondary)",
        borderBottom: "1px solid var(--glass-border)",
        position: "sticky",
        top: 0,
        zIndex: 1,
      }}
    >
      <span className="q-uppercase-label" role={ch}>Time</span>
      <span className="q-uppercase-label" role={ch}>Symbol</span>
      <span className="q-uppercase-label" role={ch}>Side</span>
      <span className="q-uppercase-label" role={ch} style={{ textAlign: "right" }}>
        Qty
      </span>
      <span className="q-uppercase-label" role={ch} style={{ textAlign: "right" }}>
        Fill Price
      </span>
      <span className="q-uppercase-label" role={ch} style={{ textAlign: "right" }}>
        Value
      </span>
      <span className="q-uppercase-label" role={ch} style={{ textAlign: "right" }}>
        Charges
      </span>
      <span className="q-uppercase-label" role={ch} style={{ textAlign: "right" }}>
        Realized
      </span>
    </div>
  );
}

function FillRow({ f }: { f: PaperFillRow }): React.ReactElement {
  const realized = f.realized_pnl;
  return (
    <div
      className="grid items-center"
      role="row"
      style={{
        gridTemplateColumns: COLS,
        gap: 12,
        padding: "11px 16px",
        borderBottom: "1px solid var(--glass-border)",
        transition: "background-color 0.2s var(--ease-quartr)",
      }}
      onMouseEnter={(e) => {
        e.currentTarget.style.backgroundColor = "var(--surface-hover)";
      }}
      onMouseLeave={(e) => {
        e.currentTarget.style.backgroundColor = "transparent";
      }}
    >
      <span
        role="cell"
        style={{
          fontSize: 13,
          color: "var(--text-tertiary)",
          whiteSpace: "nowrap",
          overflow: "hidden",
          textOverflow: "ellipsis",
        }}
      >
        {relativeTime(f.filled_at)}
      </span>
      <span
        role="cell"
        className="q-display"
        style={{
          fontSize: 13.5,
          color: "var(--text-primary)",
          whiteSpace: "nowrap",
          overflow: "hidden",
          textOverflow: "ellipsis",
        }}
        title={f.symbol}
      >
        {f.symbol}
      </span>
      <span role="cell">
        <SideBadge side={f.side} />
      </span>
      <span
        role="cell"
        className="tabular-nums"
        style={{
          fontSize: 13,
          color: "var(--text-secondary)",
          textAlign: "right",
        }}
      >
        {qty(f.quantity)}
      </span>
      <span
        role="cell"
        className="tabular-nums"
        style={{
          fontSize: 13,
          color: "var(--text-primary)",
          textAlign: "right",
        }}
      >
        {inr(f.fill_price)}
      </span>
      <span
        role="cell"
        className="tabular-nums"
        style={{
          fontSize: 13,
          color: "var(--text-primary)",
          textAlign: "right",
        }}
      >
        {inr(f.gross_value)}
      </span>
      <span
        role="cell"
        className="tabular-nums"
        style={{
          fontSize: 13,
          color: "var(--text-tertiary)",
          textAlign: "right",
        }}
      >
        {inr(f.charges)}
      </span>
      <span
        role="cell"
        className="tabular-nums"
        style={{
          fontSize: 13,
          fontWeight: realized !== null ? 550 : 400,
          color: realized !== null ? pnlColor(realized) : "var(--text-disabled)",
          textAlign: "right",
        }}
      >
        {realized !== null ? signedInr(realized) : "—"}
      </span>
    </div>
  );
}

export function TradeJournal(): React.ReactElement {
  const [s, setS] = useState<S<PaperFillRow[]>>({ k: "loading" });

  useEffect(() => {
    let on = true;
    getPaperFills(100)
      .then((r) => {
        if (!on) return;
        if (isError(r)) {
          setS({ k: "err" });
          return;
        }
        const rows = [...r.data].sort((a, b) => fillTime(b) - fillTime(a));
        setS(rows.length === 0 ? { k: "empty" } : { k: "ok", d: rows });
      })
      .catch(() => {
        if (on) setS({ k: "err" });
      });
    return () => {
      on = false;
    };
  }, []);

  return (
    <div
      className="flex flex-col"
      style={{
        background: "var(--bg-primary)",
        border: "1px solid var(--glass-border)",
        borderRadius: "var(--radius-md)",
        overflow: "hidden",
        transition: "border-color 0.35s var(--ease-quartr)",
      }}
      onMouseEnter={(e) => {
        e.currentTarget.style.borderColor = "var(--glass-border-hover)";
      }}
      onMouseLeave={(e) => {
        e.currentTarget.style.borderColor = "var(--glass-border)";
      }}
    >
      <div
        className="flex items-center"
        style={{
          gap: 8,
          padding: "14px 16px",
          borderBottom: "1px solid var(--glass-border)",
        }}
      >
        <span
          className="q-display"
          style={{ fontSize: 15, color: "var(--text-primary)" }}
        >
          Trade Journal
        </span>
        {s.k === "ok" && (
          <span
            className="q-mono tabular-nums"
            style={{ fontSize: 11, color: "var(--text-tertiary)" }}
          >
            {s.d.length} fill{s.d.length === 1 ? "" : "s"}
          </span>
        )}
      </div>

      {s.k === "loading" && (
        <div>
          <HeaderRow />
          <div className="flex flex-col">
            {Array.from({ length: 8 }).map((_, i) => (
              <div
                key={i}
                className="grid items-center"
                style={{
                  gridTemplateColumns: COLS,
                  gap: 12,
                  padding: "11px 16px",
                  borderBottom: "1px solid var(--glass-border)",
                }}
              >
                <Skeleton style={{ height: 12, width: 64 }} />
                <Skeleton style={{ height: 12, width: 88 }} />
                <Skeleton style={{ height: 16, width: 42, borderRadius: 999 }} />
                <Skeleton style={{ height: 12, width: 40, marginLeft: "auto" }} />
                <Skeleton style={{ height: 12, width: 72, marginLeft: "auto" }} />
                <Skeleton style={{ height: 12, width: 80, marginLeft: "auto" }} />
                <Skeleton style={{ height: 12, width: 56, marginLeft: "auto" }} />
                <Skeleton style={{ height: 12, width: 72, marginLeft: "auto" }} />
              </div>
            ))}
          </div>
        </div>
      )}

      {s.k === "err" && (
        <div
          style={{
            padding: "28px 16px",
            textAlign: "center",
            fontSize: 13,
            color: "var(--text-tertiary)",
          }}
        >
          Couldn&apos;t load your trades. Please try again.
        </div>
      )}

      {s.k === "empty" && (
        <div
          className="flex flex-col items-center justify-center"
          style={{
            gap: 6,
            padding: "44px 16px",
            textAlign: "center",
          }}
        >
          <span
            className="q-display"
            style={{ fontSize: 14, color: "var(--text-secondary)" }}
          >
            No trades yet
          </span>
          <span style={{ fontSize: 13, color: "var(--text-tertiary)" }}>
            Your fills will appear here.
          </span>
        </div>
      )}

      {s.k === "ok" && (
        <div
          role="table"
          aria-label="Trade journal"
          className="flex flex-col"
          style={{ minHeight: 0, maxHeight: 460, overflowY: "auto" }}
        >
          <HeaderRow semantic />
          <div role="rowgroup" className="flex flex-col">
            {s.d.map((f) => (
              <FillRow key={f.id} f={f} />
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
