"use client";

/**
 * HoldingsTable — the open-positions blotter for the Paper Trading tab.
 * Quartr-styled semantic <table>: bold symbol + sector badge, right-aligned
 * tabular-nums money columns, colored Unrealized / Day P&L, a "stale" marker
 * on rows whose mark is behind. Reads getPaperHoldings().
 */

import * as React from "react";
import { useEffect, useState } from "react";

import { getPaperHoldings, type PaperHolding } from "@/lib/api";
import { isError } from "@/lib/types";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import {
  inr,
  pct,
  pnlColor,
  qty,
  relativeTime,
  signedInr,
} from "@/components/paper/format";

type S<T> =
  | { k: "loading" }
  | { k: "ok"; d: T }
  | { k: "err" }
  | { k: "empty" };

const DASH = "—";

/** Column geometry shared by the header + body so they stay aligned. */
const COLS = "minmax(160px,1.6fr) repeat(6, minmax(96px,1fr))";

function HeaderCell({
  label,
  right,
  semantic,
}: {
  label: string;
  right?: boolean;
  semantic?: boolean;
}): React.ReactElement {
  return (
    <div
      className="q-uppercase-label"
      role={semantic ? "columnheader" : undefined}
      style={{ textAlign: right ? "right" : "left" }}
    >
      {label}
    </div>
  );
}

function StaleDot({
  markedAt,
}: {
  markedAt: string | null;
}): React.ReactElement {
  return (
    <TooltipProvider delayDuration={150}>
      <Tooltip>
        <TooltipTrigger asChild>
          <span
            aria-label="Stale price"
            style={{
              display: "inline-block",
              width: 6,
              height: 6,
              borderRadius: "var(--radius-pill)",
              background: "var(--color-warn)",
              flex: "0 0 auto",
            }}
          />
        </TooltipTrigger>
        <TooltipContent>
          <span style={{ fontFamily: "var(--font-ui)" }}>
            Stale price · marked {relativeTime(markedAt)}
          </span>
        </TooltipContent>
      </Tooltip>
    </TooltipProvider>
  );
}

function PnlCell({
  value,
  sub,
}: {
  value: number;
  sub?: string;
}): React.ReactElement {
  return (
    <div
      role="cell"
      className="flex flex-col items-end tabular-nums"
      style={{ gap: 2, color: pnlColor(value) }}
    >
      <span style={{ fontSize: 13 }}>{signedInr(value)}</span>
      {sub !== undefined ? (
        <span style={{ fontSize: 11, opacity: 0.85 }}>{sub}</span>
      ) : null}
    </div>
  );
}

function NumCell({ text }: { text: string }): React.ReactElement {
  return (
    <div
      role="cell"
      className="tabular-nums"
      style={{
        textAlign: "right",
        fontSize: 13,
        color: "var(--text-secondary)",
      }}
    >
      {text}
    </div>
  );
}

function HoldingRow({ h }: { h: PaperHolding }): React.ReactElement {
  return (
    <div
      role="row"
      className="items-center"
      style={{
        display: "grid",
        gridTemplateColumns: COLS,
        columnGap: 12,
        padding: "11px 16px",
        borderTop: "1px solid var(--glass-border)",
        transition: "background-color 0.35s var(--ease-quartr)",
      }}
      onMouseEnter={(e) => {
        e.currentTarget.style.backgroundColor = "var(--surface-hover)";
      }}
      onMouseLeave={(e) => {
        e.currentTarget.style.backgroundColor = "transparent";
      }}
    >
      {/* Symbol + sector */}
      <div role="cell" className="flex items-center" style={{ gap: 8, minWidth: 0 }}>
        {h.stale ? <StaleDot markedAt={h.last_mark_at} /> : null}
        <div className="flex flex-col" style={{ gap: 3, minWidth: 0 }}>
          <span
            className="q-display"
            style={{
              color: "var(--text-primary)",
              fontWeight: 600,
              fontSize: 14,
              lineHeight: 1.1,
              whiteSpace: "nowrap",
              overflow: "hidden",
              textOverflow: "ellipsis",
            }}
          >
            {h.symbol}
          </span>
          {h.sector ? (
            <Badge
              variant="secondary"
              style={{
                alignSelf: "flex-start",
                fontSize: 10,
                lineHeight: 1.2,
                padding: "1px 6px",
                fontWeight: 500,
                maxWidth: "100%",
                whiteSpace: "nowrap",
                overflow: "hidden",
                textOverflow: "ellipsis",
                display: "inline-block",
              }}
            >
              {h.sector}
            </Badge>
          ) : null}
        </div>
      </div>

      <NumCell text={qty(h.quantity)} />
      <NumCell text={inr(h.avg_cost)} />
      <NumCell text={h.last_price === null ? DASH : inr(h.last_price)} />
      <NumCell text={inr(h.market_value)} />
      <PnlCell value={h.unrealized_pnl} sub={pct(h.unrealized_pct)} />
      <PnlCell value={h.day_pnl} />
    </div>
  );
}

function HeaderBar({ semantic }: { semantic?: boolean }): React.ReactElement {
  return (
    <div
      role={semantic ? "row" : undefined}
      style={{
        display: "grid",
        gridTemplateColumns: COLS,
        columnGap: 12,
        padding: "10px 16px",
        background: "var(--bg-secondary)",
        borderBottom: "1px solid var(--glass-border)",
        position: "sticky",
        top: 0,
        zIndex: 1,
      }}
    >
      <HeaderCell label="Symbol" semantic={semantic} />
      <HeaderCell label="Qty" right semantic={semantic} />
      <HeaderCell label="Avg Cost" right semantic={semantic} />
      <HeaderCell label="LTP" right semantic={semantic} />
      <HeaderCell label="Mkt Value" right semantic={semantic} />
      <HeaderCell label="Unrealized" right semantic={semantic} />
      <HeaderCell label="Day P&L" right semantic={semantic} />
    </div>
  );
}

function Shell({ children }: { children: React.ReactNode }): React.ReactElement {
  return (
    <div
      style={{
        background: "var(--bg-primary)",
        border: "1px solid var(--glass-border)",
        borderRadius: "var(--radius-md)",
        overflow: "hidden",
      }}
    >
      {children}
    </div>
  );
}

function LoadingRows(): React.ReactElement {
  return (
    <Shell>
      <HeaderBar />
      <div>
        {Array.from({ length: 5 }).map((_, i) => (
          <div
            key={i}
            style={{
              display: "grid",
              gridTemplateColumns: COLS,
              columnGap: 12,
              padding: "13px 16px",
              borderTop: "1px solid var(--glass-border)",
              alignItems: "center",
            }}
          >
            <div className="flex flex-col" style={{ gap: 6 }}>
              <Skeleton style={{ height: 12, width: "55%" }} />
              <Skeleton style={{ height: 10, width: "38%" }} />
            </div>
            {Array.from({ length: 6 }).map((__, j) => (
              <div key={j} className="flex justify-end">
                <Skeleton style={{ height: 12, width: "70%" }} />
              </div>
            ))}
          </div>
        ))}
      </div>
    </Shell>
  );
}

function ErrorState(): React.ReactElement {
  return (
    <Shell>
      <div
        style={{
          padding: "28px 16px",
          textAlign: "center",
          fontSize: 13,
          color: "var(--text-tertiary)",
          fontFamily: "var(--font-ui)",
        }}
      >
        Couldn&apos;t load holdings. Try again in a moment.
      </div>
    </Shell>
  );
}

function EmptyState(): React.ReactElement {
  return (
    <Shell>
      <div
        className="flex flex-col items-center justify-center"
        style={{
          padding: "40px 16px",
          gap: 6,
          textAlign: "center",
        }}
      >
        <span
          className="q-display"
          style={{ color: "var(--text-secondary)", fontSize: 14 }}
        >
          No open positions yet.
        </span>
        <span style={{ color: "var(--text-tertiary)", fontSize: 12 }}>
          Filled buy orders will show up here.
        </span>
      </div>
    </Shell>
  );
}

export function HoldingsTable(): React.ReactElement {
  const [s, setS] = useState<S<PaperHolding[]>>({ k: "loading" });

  useEffect(() => {
    let on = true;
    getPaperHoldings()
      .then((r) => {
        if (!on) return;
        if (isError(r)) {
          setS({ k: "err" });
          return;
        }
        const d = r.data;
        setS(d.length === 0 ? { k: "empty" } : { k: "ok", d });
      })
      .catch(() => {
        if (on) setS({ k: "err" });
      });
    return () => {
      on = false;
    };
  }, []);

  if (s.k === "loading") return <LoadingRows />;
  if (s.k === "err") return <ErrorState />;
  if (s.k === "empty") return <EmptyState />;

  const rows = s.d;

  return (
    <Shell>
      {/* The role="table" element is the scroll container, so the sticky
          header (a direct child row) stays pinned while the body rowgroup
          scrolls. maxHeight + overflowY clips reliably (unlike a Radix Root
          given only max-height). */}
      <div
        role="table"
        aria-label="Open positions"
        style={{ maxHeight: 440, overflowY: "auto" }}
      >
        <HeaderBar semantic />
        <div role="rowgroup">
          {rows.map((h) => (
            <HoldingRow key={h.symbol} h={h} />
          ))}
        </div>
      </div>
    </Shell>
  );
}
