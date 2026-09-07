"use client";

import * as React from "react";
import { useEffect, useState } from "react";
import { getPaperOpenOrders, type PaperOpenOrder } from "@/lib/api";
import { isError } from "@/lib/types";
import { inr, qty, relativeTime } from "@/components/paper/format";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";

type S<T> =
  | { k: "loading" }
  | { k: "ok"; d: T }
  | { k: "err" }
  | { k: "empty" };

const PROFIT = "var(--color-profit)";
const LOSS = "var(--color-loss)";

/** A resting BUY/SELL pill, tinted with the matching semantic color. */
function SideBadge({ side }: { side: string }): React.ReactElement {
  const isBuy = side.toUpperCase() === "BUY";
  const color = isBuy ? PROFIT : LOSS;
  return (
    <span
      className="q-uppercase-label"
      style={{
        display: "inline-flex",
        alignItems: "center",
        padding: "2px 8px",
        borderRadius: "var(--radius-pill)",
        background: `color-mix(in srgb, ${color} 12%, transparent)`,
        color,
        letterSpacing: "0.04em",
        fontWeight: 600,
        lineHeight: 1.4,
      }}
    >
      {isBuy ? "BUY" : "SELL"}
    </span>
  );
}

/** Price cell: limit_price as ₹ when set, else "trig ₹…" for SL/GTT, else —. */
function priceLabel(o: PaperOpenOrder): string {
  if (o.limit_price !== null) return inr(o.limit_price);
  if (o.trigger_price !== null) return "trig " + inr(o.trigger_price);
  return "—";
}

const COL =
  "grid items-center" as const;
const GRID_COLS =
  "minmax(96px,1.4fr) 64px 80px minmax(56px,0.8fr) minmax(96px,1.1fr) minmax(96px,1.1fr) minmax(72px,0.9fr)";

function HeaderRow({ semantic }: { semantic?: boolean }): React.ReactElement {
  const ch = semantic ? "columnheader" : undefined;
  return (
    <div
      className={COL}
      role={semantic ? "row" : undefined}
      style={{
        gridTemplateColumns: GRID_COLS,
        gap: 12,
        padding: "10px 16px",
        borderBottom: "1px solid var(--glass-border)",
        background: "var(--bg-secondary)",
        position: "sticky",
        top: 0,
        zIndex: 1,
      }}
    >
      <div className="q-uppercase-label" role={ch}>Symbol</div>
      <div className="q-uppercase-label" role={ch}>Side</div>
      <div className="q-uppercase-label" role={ch}>Type</div>
      <div className="q-uppercase-label" role={ch} style={{ textAlign: "right" }}>
        Qty
      </div>
      <div className="q-uppercase-label" role={ch} style={{ textAlign: "right" }}>
        Price
      </div>
      <div className="q-uppercase-label" role={ch} style={{ textAlign: "right" }}>
        Reserved
      </div>
      <div className="q-uppercase-label" role={ch} style={{ textAlign: "right" }}>
        Placed
      </div>
    </div>
  );
}

function OrderRow({ o }: { o: PaperOpenOrder }): React.ReactElement {
  return (
    <div
      className={COL}
      role="row"
      style={{
        gridTemplateColumns: GRID_COLS,
        gap: 12,
        padding: "12px 16px",
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
      <div
        role="cell"
        className="q-display"
        style={{
          color: "var(--text-primary)",
          overflow: "hidden",
          textOverflow: "ellipsis",
          whiteSpace: "nowrap",
        }}
        title={o.symbol}
      >
        {o.symbol}
      </div>
      <div role="cell">
        <SideBadge side={o.side} />
      </div>
      <div role="cell">
        <Badge
          variant="secondary"
          className="q-mono"
          style={{ fontSize: 11, fontWeight: 500 }}
        >
          {o.order_type}
        </Badge>
      </div>
      <div
        role="cell"
        className="tabular-nums"
        style={{ textAlign: "right", color: "var(--text-primary)" }}
      >
        {qty(o.quantity)}
      </div>
      <div
        role="cell"
        className="tabular-nums"
        style={{
          textAlign: "right",
          color:
            o.limit_price === null && o.trigger_price !== null
              ? "var(--text-secondary)"
              : "var(--text-primary)",
        }}
      >
        {priceLabel(o)}
      </div>
      <div
        role="cell"
        className="tabular-nums"
        style={{ textAlign: "right", color: "var(--text-secondary)" }}
      >
        {inr(o.reserved_cash)}
      </div>
      <div
        role="cell"
        className="tabular-nums"
        style={{ textAlign: "right", color: "var(--text-tertiary)" }}
      >
        {relativeTime(o.created_at)}
      </div>
    </div>
  );
}

function SkeletonRow(): React.ReactElement {
  return (
    <div
      className={COL}
      style={{
        gridTemplateColumns: GRID_COLS,
        gap: 12,
        padding: "12px 16px",
        borderBottom: "1px solid var(--glass-border)",
      }}
    >
      <Skeleton style={{ height: 14, width: "70%" }} />
      <Skeleton style={{ height: 18, width: 40, borderRadius: 999 }} />
      <Skeleton style={{ height: 18, width: 48, borderRadius: 999 }} />
      <Skeleton style={{ height: 14, width: "60%", marginLeft: "auto" }} />
      <Skeleton style={{ height: 14, width: "80%", marginLeft: "auto" }} />
      <Skeleton style={{ height: 14, width: "80%", marginLeft: "auto" }} />
      <Skeleton style={{ height: 14, width: "60%", marginLeft: "auto" }} />
    </div>
  );
}

function Shell({
  children,
}: {
  children: React.ReactNode;
}): React.ReactElement {
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

export function OpenOrdersBlotter(): React.ReactElement {
  const [s, setS] = useState<S<PaperOpenOrder[]>>({ k: "loading" });

  useEffect(() => {
    let on = true;
    getPaperOpenOrders()
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

  if (s.k === "loading") {
    return (
      <Shell>
        <HeaderRow />
        <div>
          {[0, 1, 2, 3].map((i) => (
            <SkeletonRow key={i} />
          ))}
        </div>
      </Shell>
    );
  }

  if (s.k === "err") {
    return (
      <Shell>
        <HeaderRow />
        <div
          style={{
            padding: "28px 16px",
            textAlign: "center",
            color: "var(--text-tertiary)",
            fontSize: 13,
          }}
        >
          Couldn&rsquo;t load resting orders.
        </div>
      </Shell>
    );
  }

  if (s.k === "empty") {
    return (
      <Shell>
        <HeaderRow />
        <div
          className="flex flex-col items-center justify-center"
          style={{
            gap: 6,
            padding: "40px 16px",
            textAlign: "center",
          }}
        >
          <div
            className="q-display"
            style={{ color: "var(--text-secondary)", fontSize: 14 }}
          >
            No resting orders.
          </div>
          <div style={{ color: "var(--text-tertiary)", fontSize: 12.5 }}>
            LIMIT, SL and GTT orders waiting to fill will appear here.
          </div>
        </div>
      </Shell>
    );
  }

  return (
    <Shell>
      <div
        role="table"
        aria-label="Resting orders"
        style={{ maxHeight: 420, overflowY: "auto" }}
      >
        <HeaderRow semantic />
        <div role="rowgroup">
          {s.d.map((o) => (
            <OrderRow key={o.id} o={o} />
          ))}
        </div>
      </div>
    </Shell>
  );
}
