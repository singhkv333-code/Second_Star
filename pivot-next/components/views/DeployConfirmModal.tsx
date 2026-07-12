"use client";

/**
 * DeployConfirmModal — the "Deploy = execute now" confirmation.
 *
 * Deploy no longer arms a workflow/agent. It places the strategy's basket into
 * the paper book (or the connected broker for a live account) immediately. This
 * modal is the confirm gate: on open it fetches the exact per-leg whole-share /
 * unit breakdown at the calculator amount (`previewPlaceBasket`, no writes),
 * shows it, and only on Confirm does it place (`placeBasket`). When the strategy
 * can't be placed — an option/hedge/pair tier, or no live prices (market
 * closed) — it shows the backend's exact reason and offers no place action.
 */

import * as React from "react";
import { AlertCircle, CheckCircle2, Loader2 } from "lucide-react";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
} from "@/components/ui/dialog";
import {
  previewPlaceBasket,
  placeBasket,
  type BasketPreviewResponse,
  type BasketPlaceResponse,
  type BasketFillLeg,
} from "@/lib/api";
import { isError } from "@/lib/types";
import type { ExpressionDetail } from "@/lib/types";
import { exprName } from "@/components/views/ExpressionHero";
import { CompanyLogo } from "@/components/CompanyLogo";

const FONT = "var(--font-display)";

function inr(v: number): string {
  return `₹${Math.round(v).toLocaleString("en-IN")}`;
}

/** Whole shares for Indian equity; fractional units for US/crypto. */
function fmtQty(leg: { quantity: number; asset_class: string }): string {
  const n = leg.quantity;
  if (leg.asset_class === "crypto") return n.toFixed(n < 1 ? 6 : 4);
  if (leg.asset_class === "us_equity" || leg.asset_class === "us_etf")
    return n.toFixed(n < 1 ? 4 : 2);
  return String(Math.round(n));
}

function unitLabel(assetClass: string): string {
  return assetClass === "crypto" ? "units" : "sh";
}

const SKIP_REASON: Record<string, string> = {
  no_price: "no live price",
  price_unavailable: "no live price",
  slice_too_small: "amount too small for one unit",
  short_unsupported: "short leg — not brokered",
  insufficient_buying_power: "not enough buying power",
  market_closed: "market closed",
  rejected: "rejected by the book",
};

/** A leg's skip status → a short human reason (falls back to the raw status). */
export function skipReasonText(status: string): string {
  return SKIP_REASON[status] ?? status.replace(/_/g, " ");
}

export function DeployConfirmModal({
  expr,
  amount,
  onClose,
  onPlaced,
}: {
  /** The expression to deploy; null closes the modal. */
  expr: ExpressionDetail | null;
  amount: number;
  onClose: () => void;
  /** Fired after a successful placement (parent shows a toast / navigates). */
  onPlaced: (res: BasketPlaceResponse) => void;
}): React.ReactElement {
  const [loading, setLoading] = React.useState(false);
  const [preview, setPreview] = React.useState<BasketPreviewResponse | null>(
    null,
  );
  const [reason, setReason] = React.useState<string | null>(null);
  const [placing, setPlacing] = React.useState(false);

  const open = expr !== null;

  // Fetch the breakdown whenever the modal opens (or the amount changes).
  React.useEffect(() => {
    if (expr === null) {
      setPreview(null);
      setReason(null);
      setPlacing(false);
      return;
    }
    let cancelled = false;
    setLoading(true);
    setPreview(null);
    setReason(null);
    previewPlaceBasket(expr.id, amount).then((res) => {
      if (cancelled) return;
      setLoading(false);
      if (isError(res)) {
        setReason(res.error.message);
        return;
      }
      setPreview(res.data);
      if (!res.data.placeable) setReason(res.data.reason);
    });
    return () => {
      cancelled = true;
    };
  }, [expr, amount]);

  async function handleConfirm(): Promise<void> {
    if (expr === null || placing) return;
    setPlacing(true);
    setReason(null);
    const res = await placeBasket(expr.id, amount);
    setPlacing(false);
    if (isError(res)) {
      setReason(res.error.message);
      return;
    }
    onPlaced(res.data);
    onClose();
  }

  const canPlace = preview !== null && preview.placeable && !placing;
  const routed = preview?.routed_to === "broker" ? "your broker" : "paper book";

  return (
    <Dialog open={open} onOpenChange={(o) => !o && onClose()}>
      <DialogContent
        className="sm:max-w-[440px]"
        style={{ fontFamily: FONT }}
      >
        <DialogHeader>
          <DialogTitle style={{ fontSize: 18 }}>
            {reason && !preview?.placeable
              ? "Can’t deploy this strategy"
              : "Place into your paper book?"}
          </DialogTitle>
          {expr && (
            <DialogDescription>
              {exprName(expr)} · {inr(amount)}
            </DialogDescription>
          )}
        </DialogHeader>

        {/* Loading the breakdown */}
        {loading && (
          <div
            style={{
              display: "flex",
              alignItems: "center",
              gap: 10,
              padding: "18px 4px",
              color: "var(--text-tertiary)",
              fontSize: 14,
            }}
          >
            <Loader2 size={16} className="animate-spin" aria-hidden />
            Sizing the basket at the current price…
          </div>
        )}

        {/* Blocked: an exact reason (option tier, no prices, or an error) */}
        {!loading && reason && !preview?.placeable && (
          <div
            role="alert"
            style={{
              display: "flex",
              gap: 10,
              padding: "14px 16px",
              borderRadius: "var(--radius-md)",
              background: "color-mix(in srgb, var(--color-loss) 8%, transparent)",
              border:
                "1px solid color-mix(in srgb, var(--color-loss) 30%, transparent)",
              color: "var(--text-primary)",
              fontSize: 13.5,
              lineHeight: 1.5,
            }}
          >
            <AlertCircle
              size={17}
              style={{ color: "var(--color-loss)", flexShrink: 0, marginTop: 1 }}
              aria-hidden
            />
            <span>{reason}</span>
          </div>
        )}

        {/* The placeable breakdown */}
        {!loading && preview?.placeable && (
          <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
            <div
              style={{
                display: "flex",
                flexDirection: "column",
                border: "1px solid var(--glass-border)",
                borderRadius: "var(--radius-md)",
                overflow: "hidden",
              }}
            >
              {preview.legs.map((leg, i) => (
                <LegRow key={leg.symbol} leg={leg} first={i === 0} />
              ))}
            </div>

            {preview.skipped.length > 0 && (
              <div
                style={{
                  fontSize: 12.5,
                  color: "var(--text-tertiary)",
                  lineHeight: 1.5,
                }}
              >
                Skipped:{" "}
                {preview.skipped
                  .map((s) => `${s.symbol} (${skipReasonText(s.status)})`)
                  .join(", ")}
                .
              </div>
            )}

            <div
              style={{
                fontSize: 12.5,
                color: "var(--text-tertiary)",
                lineHeight: 1.5,
              }}
            >
              Routes to your {routed}. This is analysis, not financial advice.
            </div>
          </div>
        )}

        {/* Actions */}
        <div
          style={{
            display: "flex",
            justifyContent: "flex-end",
            gap: 10,
            marginTop: 6,
          }}
        >
          <button
            type="button"
            onClick={onClose}
            style={{
              fontFamily: FONT,
              fontSize: 14,
              fontWeight: 500,
              padding: "8px 16px",
              borderRadius: "var(--radius-md)",
              border: "1px solid var(--glass-border)",
              background: "transparent",
              color: "var(--text-primary)",
              cursor: "pointer",
            }}
          >
            {preview?.placeable ? "Cancel" : "Close"}
          </button>
          {preview?.placeable && (
            <button
              type="button"
              onClick={handleConfirm}
              disabled={!canPlace}
              style={{
                display: "inline-flex",
                alignItems: "center",
                gap: 8,
                fontFamily: FONT,
                fontSize: 14,
                fontWeight: 600,
                padding: "8px 18px",
                borderRadius: "var(--radius-md)",
                border: "1px solid hsl(var(--primary))",
                background: "hsl(var(--primary))",
                color: "hsl(var(--primary-foreground))",
                cursor: canPlace ? "pointer" : "default",
                opacity: canPlace ? 1 : 0.6,
              }}
            >
              {placing ? (
                <>
                  <Loader2 size={15} className="animate-spin" aria-hidden />
                  Placing…
                </>
              ) : (
                <>
                  <CheckCircle2 size={15} aria-hidden />
                  Confirm &amp; place
                </>
              )}
            </button>
          )}
        </div>
      </DialogContent>
    </Dialog>
  );
}

function LegRow({
  leg,
  first,
}: {
  leg: BasketFillLeg;
  first: boolean;
}): React.ReactElement {
  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        justifyContent: "space-between",
        gap: 10,
        padding: "10px 14px",
        borderTop: first ? "none" : "1px solid var(--glass-border)",
        fontSize: 13.5,
      }}
    >
      <span style={{ display: "flex", alignItems: "center", gap: 10, minWidth: 0 }}>
        <CompanyLogo
          logoUrl={leg.logo_url}
          name={leg.name ?? leg.symbol}
          symbol={leg.symbol}
          size={26}
        />
        <span
          style={{
            display: "flex",
            flexDirection: "column",
            minWidth: 0,
            lineHeight: 1.25,
          }}
        >
          <span
            style={{
              fontWeight: 600,
              color: "var(--text-primary)",
              whiteSpace: "nowrap",
              overflow: "hidden",
              textOverflow: "ellipsis",
            }}
          >
            {leg.name ?? leg.symbol}
          </span>
          <span style={{ fontSize: 11.5, color: "var(--text-tertiary)" }}>
            {leg.symbol}
          </span>
        </span>
      </span>
      <span
        style={{
          fontVariantNumeric: "tabular-nums",
          color: "var(--text-secondary)",
          whiteSpace: "nowrap",
          flexShrink: 0,
        }}
      >
        {fmtQty(leg)} {unitLabel(leg.asset_class)}
        <span style={{ color: "var(--text-tertiary)" }}>
          {"  ·  "}
          {inr(leg.slice_inr)}
        </span>
      </span>
    </div>
  );
}
