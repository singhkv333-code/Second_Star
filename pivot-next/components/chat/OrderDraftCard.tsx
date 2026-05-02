"use client";

/**
 * OrderDraftCard — inline chat card for place-order drafts.
 *
 * Rendered when the chatbot returns a draft with _render_hint === "order_draft_card".
 * Sibling to WorkflowDraftCard — same card-frame, different layout.
 *
 * Layout (image 2 spec):
 *   Status pill row: BUY|SELL · MARKET|LIMIT · ORDER-DRAFT-NNNN · est. fill < 1s
 *   3-column body:
 *     INSTRUMENT  | QUANTITY | ESTIMATED COST
 *     name+symbol | big qty  | big total
 *     meta        | last + % | fees + cash pct
 *     [sparkline] |          | [Confirm & place →]
 */

import { useEffect, useState } from "react";
import { ArrowRight, Loader2, Minus } from "lucide-react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { getSparkline, type SparklinePoint } from "@/lib/api";

// ---------------------------------------------------------------------------
// Public types
// ---------------------------------------------------------------------------

export type OrderDraftData = {
  /** e.g. "ORDER-DRAFT-1234" */
  draft_id?: string;
  symbol: string;
  exchange?: string;
  company_name?: string;
  sector?: string;
  side: "buy" | "sell";
  order_type: "market" | "limit";
  quantity: number;
  /** Last traded price in INR */
  last_price: number;
  /** Change pct for color */
  change_pct?: number;
  /** Estimated total cost in INR */
  estimated_cost: number;
  /** Fees in INR */
  fees?: number;
  /** Cash used as % of available buying power */
  cash_pct?: number;
  _render_hint: "order_draft_card";
};

export type OrderDraftCardProps = {
  order: OrderDraftData;
  /** Called when user confirms placement. */
  onConfirm?: (order: OrderDraftData) => void;
};

// ---------------------------------------------------------------------------
// Formatters
// ---------------------------------------------------------------------------

const INR = new Intl.NumberFormat("en-IN", {
  style: "currency",
  currency: "INR",
  maximumFractionDigits: 2,
});

function fmtINR(n: number): string {
  return INR.format(n);
}

function fmtNum(n: number, digits = 2): string {
  return new Intl.NumberFormat("en-IN", {
    maximumFractionDigits: digits,
    minimumFractionDigits: digits,
  }).format(n);
}

// ---------------------------------------------------------------------------
// OrderDraftCard
// ---------------------------------------------------------------------------

export function OrderDraftCard({
  order,
  onConfirm,
}: OrderDraftCardProps): React.ReactElement {
  const draftId = order.draft_id ?? `ORDER-DRAFT-${String(Math.floor(Math.random() * 9999)).padStart(4, "0")}`;
  const positive = (order.change_pct ?? 0) >= 0;
  const isBuy = order.side === "buy";

  return (
    <div
      className="w-full max-w-lg rounded-xl border bg-card shadow-sm overflow-hidden"
      data-testid="order-draft-card"
      role="region"
      aria-label={`Order draft: ${order.side.toUpperCase()} ${order.quantity} ${order.symbol}`}
    >
      {/* Status pill row */}
      <div className="flex items-center justify-between gap-2 border-b bg-muted/30 px-4 py-2">
        <div className="flex items-center gap-1.5">
          <span
            className={cn(
              "inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[10px] font-semibold uppercase",
              isBuy
                ? "bg-emerald-50 text-emerald-700 dark:bg-emerald-950/50 dark:text-emerald-400"
                : "bg-rose-50 text-rose-700 dark:bg-rose-950/50 dark:text-rose-400",
            )}
          >
            <span
              className={cn(
                "h-1.5 w-1.5 rounded-full",
                isBuy ? "bg-emerald-500" : "bg-rose-500",
              )}
              aria-hidden={true}
            />
            {order.side.toUpperCase()} · {order.order_type.toUpperCase()}
          </span>
          <span className="text-[10px] font-mono text-muted-foreground">{draftId}</span>
        </div>
        <span className="text-[10px] text-muted-foreground">est. fill &lt; 1s</span>
      </div>

      {/* Body: 3 columns */}
      <div className="grid grid-cols-3 divide-x">
        {/* INSTRUMENT */}
        <div className="flex flex-col gap-2 p-3.5">
          <span className="text-[9px] font-semibold uppercase tracking-wider text-muted-foreground">
            Instrument
          </span>
          <div>
            <p className="font-serif text-sm font-semibold leading-tight text-foreground">
              {order.company_name ?? order.symbol}
            </p>
            <span className="mt-0.5 inline-block rounded-md bg-muted px-1 py-0.5 font-mono text-[9px] font-semibold text-foreground">
              {order.symbol} · {order.exchange ?? "NSE"}
            </span>
            {order.sector && (
              <p className="mt-0.5 text-[10px] text-muted-foreground line-clamp-1">
                {order.sector}
              </p>
            )}
          </div>
          <MiniSparkline symbol={order.symbol} positive={positive} />
        </div>

        {/* QUANTITY */}
        <div className="flex flex-col gap-2 p-3.5">
          <span className="text-[9px] font-semibold uppercase tracking-wider text-muted-foreground">
            Quantity
          </span>
          <p className="font-serif text-2xl font-semibold tabular-nums text-foreground">
            {fmtNum(order.quantity, 0)}
          </p>
          <div>
            <p className="text-[11px] font-medium text-muted-foreground">
              LAST PRICE {fmtINR(order.last_price)}
            </p>
            {order.change_pct !== undefined && (
              <p
                className={cn(
                  "text-[11px] font-semibold tabular-nums",
                  positive ? "text-emerald-600 dark:text-emerald-400" : "text-rose-600 dark:text-rose-400",
                )}
              >
                {positive ? "+" : ""}{fmtNum(order.change_pct)}%
              </p>
            )}
          </div>
        </div>

        {/* ESTIMATED COST */}
        <div className="flex flex-col gap-2 p-3.5">
          <span className="text-[9px] font-semibold uppercase tracking-wider text-muted-foreground">
            Est. Cost
          </span>
          <p className="font-serif text-xl font-semibold tabular-nums text-foreground">
            {fmtINR(order.estimated_cost)}
          </p>
          <div className="space-y-0.5">
            {order.fees !== undefined && (
              <p className="text-[10px] text-muted-foreground">
                +{fmtINR(order.fees)} fees
              </p>
            )}
            {order.cash_pct !== undefined && (
              <p className="text-[10px] text-muted-foreground">
                uses {fmtNum(order.cash_pct, 1)}% of cash
              </p>
            )}
          </div>
          <Button
            size="sm"
            onClick={() => onConfirm?.(order)}
            className={cn(
              "mt-auto w-full h-7 rounded-full text-[11px]",
              isBuy
                ? "bg-emerald-600 hover:bg-emerald-700 text-white"
                : "bg-rose-600 hover:bg-rose-700 text-white",
            )}
            data-testid="confirm-order-btn"
          >
            Confirm &amp; place
            <ArrowRight className="ml-1 h-3 w-3" aria-hidden={true} />
          </Button>
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Mini sparkline (1M, hides silently on 404)
// ---------------------------------------------------------------------------

type SparkState =
  | { kind: "loading" }
  | { kind: "ok"; points: SparklinePoint[] }
  | { kind: "hidden" };

function MiniSparkline({
  symbol,
  positive,
}: {
  symbol: string;
  positive: boolean;
}): React.ReactElement {
  const [state, setState] = useState<SparkState>({ kind: "loading" });

  useEffect(() => {
    getSparkline(symbol, "1M")
      .then((result) => {
        if ("error" in result) {
          setState({ kind: "hidden" });
        } else {
          setState({ kind: "ok", points: result.data.points });
        }
      })
      .catch(() => setState({ kind: "hidden" }));
  }, [symbol]);

  if (state.kind === "loading") {
    return (
      <div className="flex h-8 items-center">
        <Loader2 className="h-3 w-3 animate-spin text-muted-foreground" aria-hidden={true} />
      </div>
    );
  }

  if (state.kind === "hidden" || state.points.length === 0) {
    return (
      <div className="flex h-8 items-center">
        <Minus className="h-3 w-3 text-muted-foreground/30" aria-hidden={true} />
      </div>
    );
  }

  const values = state.points.map((p) => p.v);
  const min = Math.min(...values);
  const max = Math.max(...values);
  const r = max - min || 1;
  const W = 80;
  const H = 24;
  const pts = values
    .map((v, i) => {
      const x = (i / (values.length - 1)) * W;
      const y = H - ((v - min) / r) * H;
      return `${x},${y}`;
    })
    .join(" ");

  const color = positive ? "#10b981" : "#f43f5e";

  return (
    <svg
      viewBox={`0 0 ${W} ${H}`}
      className="h-8 w-full"
      aria-hidden={true}
      preserveAspectRatio="none"
    >
      <polyline
        points={pts}
        fill="none"
        stroke={color}
        strokeWidth={1.5}
        strokeLinejoin="round"
        strokeLinecap="round"
      />
    </svg>
  );
}
