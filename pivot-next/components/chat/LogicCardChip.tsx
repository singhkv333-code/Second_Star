"use client";

/**
 * LogicCardChip — generic inline chat card for any tool that emits a
 * LogicCard (orders, GTT, SL, OCO, dip-buy, basket, squareoff, SIP
 * create, etc.). Backend tags `raw_data._render_hint = "logic_card"`
 * when one of those tools fires; ChatDemo dispatches here.
 *
 * The card mirrors the public.com checklist UX:
 *   - header pill + symbol + action
 *   - human-readable details list
 *   - explanation paragraph
 *   - "Confirm & register" primary CTA → POST /orders/register
 *     (writes a TradeLog row with source="chat-confirm")
 *   - disclaimer footer
 *
 * After a successful register call, the card swaps into a confirmed
 * state showing the registered order id(s).
 */

import { useState } from "react";
import { Check, Loader2, ShieldAlert, ShoppingCart } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import {
  registerOrder,
  type OrderRegisterRequest,
  type RegisteredOrder,
} from "@/lib/api";
import { isError } from "@/lib/types";

// ---------------------------------------------------------------------------
// Types — must match the dict shape built in backend/agents/tool_executor.py
// ---------------------------------------------------------------------------

export type LogicCardDetail = { label: string; value: string };

export type LogicCardRegisterPayload =
  | {
      symbol: string;
      exchange?: string;
      transaction_type: "BUY" | "SELL";
      order_type: "MARKET" | "LIMIT" | "GTT" | "SL" | "OCO";
      quantity: number;
      price?: number | null;
      trigger_price?: number | null;
      product?: string;
    }
  | {
      basket: true;
      legs: Array<{
        symbol: string;
        exchange?: string;
        transaction_type: "BUY" | "SELL";
        order_type: string;
        quantity: number;
        price?: number | null;
        product?: string;
      }>;
    };

export type LogicCard = {
  type: string;
  action: string;
  symbol: string;
  details: LogicCardDetail[];
  explanation: string;
  disclaimer: string;
  requires_confirmation: boolean;
  /** Present for tools that resolve into a /orders/register intent. */
  register_payload?: LogicCardRegisterPayload;
};

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

type ConfirmState =
  | { kind: "idle" }
  | { kind: "submitting" }
  | { kind: "done"; registered: RegisteredOrder[] }
  | { kind: "error"; message: string };

export function LogicCardChip({
  card,
}: {
  card: LogicCard;
}): React.ReactElement {
  const [state, setState] = useState<ConfirmState>({ kind: "idle" });

  const canConfirm =
    card.requires_confirmation &&
    card.register_payload !== undefined &&
    state.kind === "idle";

  const handleConfirm = async (): Promise<void> => {
    if (!card.register_payload) return;
    setState({ kind: "submitting" });
    const result = await registerOrder(
      card.register_payload as unknown as OrderRegisterRequest,
    );
    if (isError(result)) {
      setState({
        kind: "error",
        message: result.error.message ?? "Failed to register order",
      });
      return;
    }
    const data = result.data;
    const registered =
      "registered" in data
        ? data.registered
        : ([data] as RegisteredOrder[]);
    setState({ kind: "done", registered });
  };

  return (
    <div
      className={cn(
        "my-2 w-full max-w-md rounded-xl border bg-card shadow-sm overflow-hidden",
      )}
      data-testid="logic-card-chip"
      role="region"
      aria-label={`${card.action} ${card.symbol}`}
    >
      {/* Header */}
      <div className="flex items-start gap-3 px-4 pt-4 pb-3">
        <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-primary/10">
          <ShoppingCart
            className="h-4 w-4 text-primary"
            aria-hidden="true"
          />
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <Badge
              variant="outline"
              className="text-[10px] px-1.5 py-0 uppercase tracking-wide"
            >
              {card.type.replace(/_/g, " ")}
            </Badge>
            <Badge
              variant={card.action === "SELL" ? "destructive" : "default"}
              className="text-[10px] px-1.5 py-0"
            >
              {card.action}
            </Badge>
          </div>
          <h3 className="mt-1 text-sm font-semibold leading-snug text-foreground">
            {card.symbol}
          </h3>
        </div>
      </div>

      {/* Details */}
      {card.details.length > 0 && (
        <dl
          className="border-t px-4 py-2.5 grid grid-cols-2 gap-x-3 gap-y-1.5"
          data-testid="logic-card-details"
        >
          {card.details.map((d, i) => (
            <div key={i} className="contents">
              <dt className="text-[11px] text-muted-foreground">{d.label}</dt>
              <dd className="text-[11px] text-foreground text-right tabular-nums">
                {d.value}
              </dd>
            </div>
          ))}
        </dl>
      )}

      {/* Explanation */}
      {card.explanation && (
        <p className="border-t px-4 py-2.5 text-[11px] leading-relaxed text-muted-foreground">
          {card.explanation}
        </p>
      )}

      {/* CTA / state */}
      <div className="border-t px-4 py-3">
        {state.kind === "done" ? (
          <ConfirmedState registered={state.registered} />
        ) : (
          <Button
            size="sm"
            className="w-full justify-center"
            onClick={() => void handleConfirm()}
            disabled={!canConfirm || state.kind === "submitting"}
            data-testid="logic-card-confirm-btn"
          >
            {state.kind === "submitting" ? (
              <>
                <Loader2
                  className="mr-1.5 h-3.5 w-3.5 animate-spin"
                  aria-hidden="true"
                />
                Registering…
              </>
            ) : (
              <>
                <Check className="mr-1.5 h-3.5 w-3.5" aria-hidden="true" />
                Confirm &amp; register
              </>
            )}
          </Button>
        )}
        {state.kind === "error" && (
          <p
            role="alert"
            data-testid="logic-card-error"
            className="mt-2 rounded-md bg-destructive/10 px-2.5 py-1.5 text-[11px] text-destructive"
          >
            {state.message}
          </p>
        )}
      </div>

      {/* Disclaimer */}
      {card.disclaimer && (
        <div className="border-t bg-muted/30 px-4 py-2 flex items-start gap-1.5">
          <ShieldAlert
            className="mt-0.5 h-3 w-3 shrink-0 text-muted-foreground"
            aria-hidden="true"
          />
          <p className="text-[10px] leading-relaxed text-muted-foreground">
            {card.disclaimer}
          </p>
        </div>
      )}
    </div>
  );
}

function ConfirmedState({
  registered,
}: {
  registered: RegisteredOrder[];
}): React.ReactElement {
  if (registered.length === 1) {
    const row = registered[0];
    return (
      <div
        data-testid="logic-card-confirmed"
        className="flex items-start gap-2 rounded-md bg-emerald-500/10 px-3 py-2 text-emerald-700 dark:text-emerald-400"
      >
        <Check className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden="true" />
        <div className="min-w-0 flex-1 text-[11px]">
          <p className="font-medium">
            Registered #{row.id} · {row.symbol} {row.transaction_type}{" "}
            {row.quantity}
          </p>
          <p className="mt-0.5 text-emerald-700/70 dark:text-emerald-400/70">
            {row.placed_at}
          </p>
        </div>
      </div>
    );
  }
  return (
    <div
      data-testid="logic-card-confirmed"
      className="rounded-md bg-emerald-500/10 px-3 py-2 text-emerald-700 dark:text-emerald-400"
    >
      <p className="text-[11px] font-medium">
        Registered {registered.length} legs
      </p>
      <ul className="mt-1 space-y-0.5 text-[10px]">
        {registered.map((r) => (
          <li key={r.id}>
            #{r.id} · {r.symbol} {r.transaction_type} {r.quantity}
          </li>
        ))}
      </ul>
    </div>
  );
}
