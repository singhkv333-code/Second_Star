"use client";

/**
 * SimulatedIpoAllocations — Paper Trading dashboard section.
 *
 * Shows the user's simulated IPO allotment records.  The "Simulated" framing
 * is unmissable: a section-level badge, a per-row badge on every allotted row,
 * and a footer note.  No real funds, no real allotments — purely a labelled
 * ledger for forward-testing IPO exposure.
 *
 * Reads getPaperIpoAllocations() into a 4-state machine and renders a compact
 * table: symbol + name, type chip, lots / qty, ≈ amount, allotment status
 * badge, allotment date.  Follows the same fetch + render skeleton pattern as
 * HoldingsTable and IdeaScorecards.
 */

import * as React from "react";
import { useEffect, useState } from "react";

import { getPaperIpoAllocations } from "@/lib/api";
import { isError, type PaperIpoAllocation } from "@/lib/types";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { dateShort, inr, pnlColor, qty, signedInr } from "@/components/paper/format";

// ---------------------------------------------------------------------------
// 4-state machine
// ---------------------------------------------------------------------------

type S<T> =
  | { k: "loading" }
  | { k: "ok"; d: T }
  | { k: "err" }
  | { k: "empty" };

// ---------------------------------------------------------------------------
// Column geometry — shared by header + body rows
// ---------------------------------------------------------------------------

/** symbol col wider; rest share space. Last col = listing P&L (P3.1). */
const COLS =
  "minmax(140px,1.8fr) 80px minmax(80px,1fr) minmax(96px,1fr) minmax(120px,1.2fr) minmax(96px,1fr) minmax(120px,1.2fr)";

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

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

function HeaderCell({
  label,
  right,
}: {
  label: string;
  right?: boolean;
}): React.ReactElement {
  return (
    <div
      className="q-uppercase-label"
      style={{ textAlign: right ? "right" : "left" }}
    >
      {label}
    </div>
  );
}

function HeaderBar(): React.ReactElement {
  return (
    <div
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
      <HeaderCell label="IPO" />
      <HeaderCell label="Type" />
      <HeaderCell label="Lots / Qty" right />
      <HeaderCell label="≈ Amount" right />
      <HeaderCell label="Status" right />
      <HeaderCell label="Allotment date" right />
      <HeaderCell label="Listing P&L" right />
    </div>
  );
}

function AllotmentBadge({
  status,
  quantityAllotted,
}: {
  status: PaperIpoAllocation["allotment_status"];
  quantityAllotted: number;
}): React.ReactElement {
  if (status === "allotted") {
    return (
      <Badge
        variant="success"
        style={{ fontSize: 10, whiteSpace: "nowrap" }}
      >
        Allotted {qty(quantityAllotted)}
      </Badge>
    );
  }
  if (status === "not_allotted") {
    return (
      <Badge variant="destructive" style={{ fontSize: 10 }}>
        Not allotted
      </Badge>
    );
  }
  return (
    <Badge variant="muted" style={{ fontSize: 10 }}>
      Pending
    </Badge>
  );
}

/**
 * Listing credit state for allotted rows (P3.1).
 *
 * - book_credited + simulated_pnl: show signed P&L + listing price vs issue price.
 * - book_credited + book_note (no pnl): show the note (e.g. insufficient buying power).
 * - allotted + !book_credited: "Awaiting listing {date}".
 * - all other statuses: empty cell.
 */
function ListingCreditCell({
  row,
}: {
  row: PaperIpoAllocation;
}): React.ReactElement {
  if (row.allotment_status !== "allotted") {
    return <div />;
  }

  if (row.book_credited && row.simulated_pnl != null) {
    const pnl = row.simulated_pnl;
    return (
      <div
        className="flex flex-col"
        style={{ gap: 2, alignItems: "flex-end", minWidth: 0 }}
      >
        <span
          className="tabular-nums"
          style={{ fontSize: 13, fontWeight: 600, color: pnlColor(pnl) }}
        >
          {signedInr(pnl)}
        </span>
        {row.listing_price != null ? (
          <span
            style={{
              fontSize: 11,
              color: "var(--text-tertiary)",
              whiteSpace: "nowrap",
            }}
          >
            {inr(row.listing_price, 2)} vs {inr(row.issue_price, 2)}
          </span>
        ) : null}
        <span
          style={{
            fontSize: 10,
            color: "var(--text-tertiary)",
            fontStyle: "italic",
          }}
        >
          In Holdings / NAV
        </span>
      </div>
    );
  }

  if (row.book_credited && row.book_note) {
    return (
      <div style={{ textAlign: "right", minWidth: 0 }}>
        <span
          style={{
            fontSize: 11,
            color: "var(--color-loss)",
            display: "block",
            overflow: "hidden",
            textOverflow: "ellipsis",
            whiteSpace: "nowrap",
          }}
          title={row.book_note}
        >
          {row.book_note}
        </span>
      </div>
    );
  }

  // allotted, not yet credited — awaiting listing date
  return (
    <div style={{ textAlign: "right", minWidth: 0 }}>
      <span
        style={{
          fontSize: 11,
          color: "var(--text-tertiary)",
          display: "block",
          whiteSpace: "nowrap",
        }}
      >
        Awaiting listing
        {row.listing_date ? (
          <>
            {" "}
            <span style={{ color: "var(--text-secondary)" }}>
              {dateShort(row.listing_date)}
            </span>
          </>
        ) : null}
      </span>
    </div>
  );
}

function AllocationRow({
  row,
}: {
  row: PaperIpoAllocation;
}): React.ReactElement {
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
      {/* Symbol + name */}
      <div className="flex flex-col" style={{ gap: 2, minWidth: 0 }}>
        <span
          className="q-display"
          style={{
            fontWeight: 600,
            fontSize: 14,
            color: "var(--text-primary)",
            lineHeight: 1.1,
            whiteSpace: "nowrap",
            overflow: "hidden",
            textOverflow: "ellipsis",
          }}
        >
          {row.ipo_symbol}
        </span>
        {row.ipo_name ? (
          <span
            style={{
              fontSize: 11,
              color: "var(--text-tertiary)",
              whiteSpace: "nowrap",
              overflow: "hidden",
              textOverflow: "ellipsis",
            }}
          >
            {row.ipo_name}
          </span>
        ) : null}
      </div>

      {/* Type chip */}
      <div>
        <Badge
          variant="secondary"
          style={{
            fontSize: 10,
            background: "var(--bg-secondary)",
            color: "var(--text-secondary)",
            textTransform: "uppercase",
            letterSpacing: "0.04em",
          }}
        >
          {row.ipo_type}
        </Badge>
      </div>

      {/* Lots / Qty — right aligned */}
      <div
        className="tabular-nums"
        style={{ textAlign: "right", fontSize: 13, color: "var(--text-secondary)" }}
      >
        {row.lots_applied}L&nbsp;/&nbsp;{qty(row.quantity_applied)}
      </div>

      {/* Amount estimate — right aligned */}
      <div
        className="tabular-nums"
        style={{ textAlign: "right", fontSize: 13, color: "var(--text-secondary)" }}
      >
        {inr(row.amount_applied, 0)}
      </div>

      {/* Allotment status — right aligned */}
      <div style={{ display: "flex", justifyContent: "flex-end" }}>
        <AllotmentBadge
          status={row.allotment_status}
          quantityAllotted={row.quantity_allotted}
        />
      </div>

      {/* Allotment date — right aligned */}
      <div
        style={{
          textAlign: "right",
          fontSize: 12,
          color: "var(--text-tertiary)",
        }}
      >
        {dateShort(row.allotment_date)}
      </div>

      {/* Listing P&L (P3.1) — credited / awaiting / note */}
      <div style={{ display: "flex", justifyContent: "flex-end" }}>
        <ListingCreditCell row={row} />
      </div>
    </div>
  );
}

function LoadingRows(): React.ReactElement {
  return (
    <Shell>
      <HeaderBar />
      <div>
        {Array.from({ length: 3 }).map((_, i) => (
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
            <div className="flex flex-col" style={{ gap: 5 }}>
              <Skeleton style={{ height: 12, width: "50%" }} />
              <Skeleton style={{ height: 10, width: "70%" }} />
            </div>
            {Array.from({ length: 6 }).map((__, j) => (
              <div key={j} className="flex justify-end">
                <Skeleton style={{ height: 12, width: "60%" }} />
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
        Couldn&apos;t load simulated IPO allocations. Try again in a moment.
      </div>
    </Shell>
  );
}

function EmptyState(): React.ReactElement {
  return (
    <Shell>
      <div
        className="flex flex-col items-center justify-center"
        style={{ padding: "40px 16px", gap: 6, textAlign: "center" }}
      >
        <span
          className="q-display"
          style={{ color: "var(--text-secondary)", fontSize: 14 }}
        >
          No simulated IPO applications yet.
        </span>
        <span
          style={{ fontSize: 12, color: "var(--text-tertiary)", maxWidth: 380 }}
        >
          Apply to an IPO in paper mode to forward-test allotment outcomes
          without real funds.
        </span>
      </div>
    </Shell>
  );
}

// ---------------------------------------------------------------------------
// Section header
// ---------------------------------------------------------------------------

function SectionHeader(): React.ReactElement {
  return (
    <div className="flex items-center" style={{ gap: 10, marginBottom: 10 }}>
      <span
        className="q-display"
        style={{ fontSize: 15, fontWeight: 600, color: "var(--text-primary)" }}
      >
        IPO applications
      </span>
      {/* Unmissable "Simulated" label at the section level */}
      <Badge
        variant="warning"
        style={{
          fontSize: 10,
          fontWeight: 700,
          letterSpacing: "0.05em",
          textTransform: "uppercase",
        }}
      >
        Simulated
      </Badge>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Public export
// ---------------------------------------------------------------------------

export function SimulatedIpoAllocations(): React.ReactElement {
  const [s, setS] = useState<S<PaperIpoAllocation[]>>({ k: "loading" });

  useEffect(() => {
    let on = true;
    getPaperIpoAllocations()
      .then((r) => {
        if (!on) return;
        if (isError(r)) {
          setS({ k: "err" });
          return;
        }
        const d = r.data;
        setS(!Array.isArray(d) || d.length === 0 ? { k: "empty" } : { k: "ok", d });
      })
      .catch(() => {
        if (on) setS({ k: "err" });
      });
    return () => {
      on = false;
    };
  }, []);

  let body: React.ReactElement;

  if (s.k === "loading") {
    body = <LoadingRows />;
  } else if (s.k === "err") {
    body = <ErrorState />;
  } else if (s.k === "empty") {
    body = <EmptyState />;
  } else {
    body = (
      <Shell>
        <div
          role="table"
          aria-label="Simulated IPO allocations"
          style={{ maxHeight: 380, overflowY: "auto" }}
        >
          <HeaderBar />
          <div role="rowgroup">
            {s.d.map((row) => (
              <AllocationRow key={row.id} row={row} />
            ))}
          </div>
        </div>
        {/* Footer disclaimer — "simulated" is always visible */}
        <div
          style={{
            padding: "8px 16px",
            borderTop: "1px solid var(--glass-border)",
            fontSize: 11,
            color: "var(--text-tertiary)",
            fontFamily: "var(--font-ui)",
          }}
        >
          All allocations are <strong>simulated</strong> — deterministic lottery
          model, no real ASBA/UPI call, no real funds moved. Allotted shares
          are credited to the paper book at issue price; listing P&amp;L
          tracks live via mark-to-market.
        </div>
      </Shell>
    );
  }

  return (
    <div>
      <SectionHeader />
      {body}
    </div>
  );
}
