"use client";

/**
 * IpoApplicationCard — inline chat card rendered when the chatbot's
 * `get_ipo_details` tool returns `_render_hint: "ipo_application_card"`.
 *
 * Implements:
 *  - Editable controls: category, quantity_lots, bid_price_mode, bid_price, upi_id
 *  - Live amount recompute (client-side preview matching server logic)
 *  - Full validation matrix per the P0 contract
 *  - State machine: idle -> saving -> registered(id) -> withdrawn; closed read-only
 *  - KYC block omitted (replaced by one disclaimer line)
 *  - No reminders CTA (P2 — render a disabled ghost link)
 *  - P1 OFFICIAL block: subscription per-category + Refresh, RHP link,
 *    allotment/registrar line, listing date, oversubscription note,
 *    GMP chip (only when payload.gmp is present)
 *
 * Modelled on WorkflowDraftCard.tsx patterns.
 */

import { useState } from "react";
import {
  AlertCircle,
  ArrowUpRight,
  BellRing,
  CheckCircle2,
  ChevronDown,
  ExternalLink,
  Loader2,
  RefreshCw,
  ShieldAlert,
  Undo2,
} from "lucide-react";
import { cn } from "@/lib/utils";
import {
  getIpoSubscription,
  registerIpoApplication,
  withdrawIpoApplication,
} from "@/lib/api";
import { isError } from "@/lib/types";
import type {
  IpoApplicationPayload,
  IpoCategory,
  IpoBidPriceMode,
  IpoApplication,
  IpoSubscription,
} from "@/lib/types";

// ---------------------------------------------------------------------------
// Public props
// ---------------------------------------------------------------------------

export type IpoApplicationCardProps = {
  payload: IpoApplicationPayload;
  /** Called when the user taps "Set up reminders for open day". Receives
   *  the IPO symbol so ChatDemo can forward it into the chat pipeline.
   *  Only rendered when `payload.automatable` is true. */
  onSetupReminders?: (symbol: string) => void;
  /** Extra classes for the root surface. Used when the card is hosted in a
   *  side panel (IpoApplicationPanel) to release its inline max-width. */
  className?: string;
  /** Visual variant.
   *  - "inline" (default): standalone chat card with border + shadow.
   *  - "panel": full-bleed for the IpoApplicationPanel drawer — no border,
   *    no shadow, no rounding; the panel owns the surface. */
  variant?: "inline" | "panel";
};

// ---------------------------------------------------------------------------
// Internal state machine
// ---------------------------------------------------------------------------

type CardState =
  | { kind: "idle" }
  | { kind: "saving" }
  | { kind: "registered"; application: IpoApplication; duplicate?: boolean }
  | { kind: "withdrawing" }
  | { kind: "withdrawn" }
  | { kind: "error"; message: string };

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/** UPI ID regex per contract: ^[\w.\-]{2,256}@[a-zA-Z]{2,64}$ */
const UPI_REGEX = /^[\w.\-]{2,256}@[a-zA-Z]{2,64}$/;

function maskUpiId(upiId: string): string {
  const atIdx = upiId.indexOf("@");
  if (atIdx <= 0) return upiId;
  const handle = upiId.slice(0, atIdx);
  const domain = upiId.slice(atIdx);
  if (handle.length <= 3) return upiId;
  return `${handle.slice(0, 2)}${"*".repeat(Math.min(handle.length - 2, 6))}${domain}`;
}

function formatIndianCurrency(amount: number): string {
  return new Intl.NumberFormat("en-IN", {
    style: "currency",
    currency: "INR",
    maximumFractionDigits: 0,
  }).format(amount);
}

const CATEGORY_LABELS: Record<IpoCategory, string> = {
  retail: "Retail (HNI < ₹2L)",
  snii: "sNII (₹2L–₹10L)",
  bnii: "bNII (> ₹10L)",
  shareholder: "Shareholder",
  employee: "Employee",
};

// ---------------------------------------------------------------------------
// Validation
// ---------------------------------------------------------------------------

type ValidationResult =
  | { ok: true }
  | { ok: false; reason: string };

function validate(
  payload: IpoApplicationPayload,
  category: IpoCategory,
  quantityLots: number,
  bidPriceMode: IpoBidPriceMode,
  bidPrice: string,
): ValidationResult {
  const { locked, validation, type: ipoType } = payload;
  const band = locked.price_band;
  const lotSize = locked.lot_size;

  // Cannot compute amounts — disable register
  if (!band || !lotSize) {
    return { ok: false, reason: "Price band not yet available — register once IPO details are confirmed." };
  }

  // Quantity must be an integer >= min_lots
  if (!Number.isInteger(quantityLots) || quantityLots < validation.min_lots) {
    return { ok: false, reason: `Minimum ${validation.min_lots} lot${validation.min_lots > 1 ? "s" : ""} required.` };
  }

  // Fixed mode: bid_price required and must be in band
  if (bidPriceMode === "fixed") {
    const price = parseFloat(bidPrice);
    if (isNaN(price)) {
      return { ok: false, reason: "Enter a bid price for fixed-price mode." };
    }
    if (price < band.min || price > band.max) {
      return {
        ok: false,
        reason: `Bid price must be between ${formatIndianCurrency(band.min)} and ${formatIndianCurrency(band.max)}.`,
      };
    }
  }

  // Amount estimate using cutoff (cap check uses band.max regardless of mode)
  const effectiveCapPrice = band.max;
  const amountAtCutoff = quantityLots * lotSize * effectiveCapPrice;

  // Retail cap (mainboard only; SME bypasses)
  if (
    category === "retail" &&
    ipoType === "mainboard" &&
    !validation.sme_bypasses_retail_cap &&
    amountAtCutoff > validation.retail_max_amount
  ) {
    return {
      ok: false,
      reason: `Retail applications capped at ₹2,00,000. Reduce lots or switch category.`,
    };
  }

  // UPI cap hard-block
  const effectivePrice = bidPriceMode === "cutoff" ? band.max : parseFloat(bidPrice) || band.max;
  const amountEstimate = quantityLots * lotSize * effectivePrice;
  if (amountEstimate > validation.upi_cap) {
    return {
      ok: false,
      reason: `Amount exceeds ₹5,00,000 UPI cap — use bank-ASBA via your broker app.`,
    };
  }

  return { ok: true };
}

// ---------------------------------------------------------------------------
// Amount preview (client-side; BE re-validates on submit)
// ---------------------------------------------------------------------------

function computeAmountPreview(
  payload: IpoApplicationPayload,
  quantityLots: number,
  bidPriceMode: IpoBidPriceMode,
  bidPrice: string,
): number | null {
  const band = payload.locked.price_band;
  const lotSize = payload.locked.lot_size;
  if (!band || !lotSize) return null;

  const effectivePrice =
    bidPriceMode === "cutoff"
      ? band.max
      : parseFloat(bidPrice) || band.max;

  if (isNaN(effectivePrice)) return null;
  return quantityLots * lotSize * effectivePrice;
}

// ---------------------------------------------------------------------------
// Status badge
// ---------------------------------------------------------------------------

function StatusBadge({ status }: { status: IpoApplicationPayload["status"] }): React.ReactElement {
  const map = {
    upcoming: { label: "Upcoming", cls: "text-blue-600 dark:text-blue-300" },
    open: { label: "Open", cls: "text-emerald-600 dark:text-emerald-300" },
    closed: { label: "Closed", cls: "text-slate-500 dark:text-slate-400" },
  };
  const { label, cls } = map[status];
  return (
    <span className={cn("shrink-0 text-[11.5px] font-medium tracking-tight", cls)}>
      {label}
    </span>
  );
}

// ---------------------------------------------------------------------------
// Subscription helpers
// ---------------------------------------------------------------------------

/**
 * Return the subscription value for the selected category, used to drive
 * the oversubscription warning at the lots stepper.
 */
function subscriptionForCategory(
  sub: IpoSubscription | null,
  category: IpoCategory,
): number | null {
  if (!sub) return null;
  switch (category) {
    case "retail": return sub.rii;
    case "snii":
    case "bnii": return sub.nii;
    case "employee": return sub.employee;
    case "shareholder": return sub.shareholder;
    default: return null;
  }
}

/**
 * Format the as_of ISO timestamp to a compact "HH:MM" string in the local timezone.
 */
function formatAsOf(asOf: string | undefined): string | null {
  if (!asOf) return null;
  try {
    const d = new Date(asOf);
    return d.toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit", hour12: false });
  } catch {
    return null;
  }
}

// ---------------------------------------------------------------------------
// Main card
// ---------------------------------------------------------------------------

export function IpoApplicationCard({ payload, onSetupReminders, className, variant = "inline" }: IpoApplicationCardProps): React.ReactElement {
  const isReadOnly = payload.status === "closed";
  const isPanel = variant === "panel";

  // Editable state — initialised from payload.editable
  const [category, setCategory] = useState<IpoCategory>(payload.editable.category);
  const [quantityLots, setQuantityLots] = useState<number>(payload.editable.quantity_lots);
  const [bidPriceMode, setBidPriceMode] = useState<IpoBidPriceMode>(payload.editable.bid_price_mode);
  const [bidPrice, setBidPrice] = useState<string>(
    payload.editable.bid_price !== null ? String(payload.editable.bid_price) : "",
  );
  const [upiId, setUpiId] = useState<string>(payload.editable.upi_id ?? "");

  // P1 — live subscription data (refreshable by user)
  const [subscription, setSubscription] = useState<IpoSubscription | null>(
    payload.locked.subscription,
  );
  const [subRefreshing, setSubRefreshing] = useState(false);
  const [subRefreshError, setSubRefreshError] = useState<string | null>(null);

  const [cardState, setCardState] = useState<CardState>({ kind: "idle" });

  async function handleRefreshSubscription(): Promise<void> {
    if (subRefreshing || payload.status !== "open") return;
    setSubRefreshing(true);
    setSubRefreshError(null);
    try {
      const result = await getIpoSubscription(payload.symbol);
      if (isError(result)) {
        // Keep prior value, show a subtle note
        setSubRefreshError(result.error.message ?? "Could not refresh subscription data.");
      } else {
        setSubscription(result.data.subscription);
      }
    } finally {
      setSubRefreshing(false);
    }
  }

  const amountPreview = computeAmountPreview(payload, quantityLots, bidPriceMode, bidPrice);
  const validationResult = validate(payload, category, quantityLots, bidPriceMode, bidPrice);

  // P1 — oversubscription note: map category → its subscription value
  const categorySubValue = subscriptionForCategory(subscription, category);
  const isOversubscribed = categorySubValue !== null && categorySubValue > 1;

  const upiValid = upiId.length > 0 && UPI_REGEX.test(upiId);
  const upiFormatNote = upiId.length > 0 && !upiValid
    ? "UPI ID format looks off — expected handle@bank (e.g. name@upi)"
    : null;

  // Cut-off allowed rule: retail or employee, and NOT sme
  const cutoffAllowed = payload.validation.cutoff_allowed;

  // When switching to a category that disallows cutoff, force fixed mode
  function handleCategoryChange(newCat: IpoCategory): void {
    setCategory(newCat);
    const catAllowsCutoff = newCat === "retail" || newCat === "employee";
    if (!catAllowsCutoff && bidPriceMode === "cutoff") {
      setBidPriceMode("fixed");
    }
  }

  const isBusy = cardState.kind === "saving" || cardState.kind === "withdrawing";
  const isRegistered = cardState.kind === "registered";
  const isWithdrawn = cardState.kind === "withdrawn";

  async function handleRegister(): Promise<void> {
    if (!validationResult.ok || isReadOnly || isBusy) return;
    setCardState({ kind: "saving" });

    const maskedUpi = upiId ? maskUpiId(upiId) : undefined;
    const body = {
      ipo_symbol: payload.symbol,
      category,
      quantity_lots: quantityLots,
      bid_price_mode: bidPriceMode,
      ...(bidPriceMode === "fixed" && bidPrice ? { bid_price: parseFloat(bidPrice) } : {}),
      ...(maskedUpi ? { upi_id_masked: maskedUpi } : {}),
      conversation_id: payload.conversation_id ?? undefined,
    };

    const result = await registerIpoApplication(body);
    if (isError(result)) {
      setCardState({ kind: "error", message: result.error.message ?? "Registration failed — try again." });
      return;
    }
    setCardState({
      kind: "registered",
      application: result.data.application,
      duplicate: result.data.duplicate,
    });
  }

  async function handleWithdraw(): Promise<void> {
    if (cardState.kind !== "registered") return;
    const appId = cardState.application.id;
    setCardState({ kind: "withdrawing" });

    const result = await withdrawIpoApplication(appId);
    if (isError(result)) {
      // Restore registered state on failure
      setCardState({
        kind: "error",
        message: result.error.message ?? "Withdrawal failed — try again.",
      });
      return;
    }
    setCardState({ kind: "withdrawn" });
  }

  return (
    <div
      data-testid="ipo-application-card"
      role="region"
      aria-label={`IPO application: ${payload.name}`}
      className={cn(
        "w-full transition-all duration-500 ease-out",
        isPanel
          ? "bg-transparent"
          : "my-2 max-w-[440px] overflow-hidden rounded-3xl border border-border/50 bg-card shadow-[0_1px_2px_rgba(15,23,42,0.04),0_12px_28px_-16px_rgba(15,23,42,0.10)]",
        className,
      )}
      style={{
        animation: "draftCardIn-quartr 360ms cubic-bezier(0.22, 1, 0.36, 1) both",
      }}
    >
      {/* Header */}
      <div className="flex flex-col gap-3.5 px-5 pt-4 pb-0">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <h3 className="text-[16px] leading-[1.25] font-semibold tracking-tight text-foreground">
              {payload.name}
            </h3>
            <p className="mt-1 flex items-center gap-1.5 text-[11.5px] text-muted-foreground">
              <span className="font-medium text-foreground/70">{payload.symbol}</span>
              <span className="text-muted-foreground/40">·</span>
              <span className="capitalize">{payload.type} IPO</span>
            </p>
          </div>
          <StatusBadge status={payload.status} />
        </div>

        {/* Spec sheet — hairline-divided fact rows (premium fintech read) */}
        <div className="border-t border-border/40">
          <SpecRow
            label="Price band"
            value={
              payload.locked.price_band
                ? payload.locked.price_band.is_fixed
                  ? formatIndianCurrency(payload.locked.price_band.max)
                  : `${formatIndianCurrency(payload.locked.price_band.min)} – ${formatIndianCurrency(payload.locked.price_band.max)}`
                : "TBA"
            }
            muted={!payload.locked.price_band}
          />
          <SpecRow
            label="Lot size"
            value={payload.locked.lot_size !== null ? `${payload.locked.lot_size} shares` : "TBA"}
            muted={payload.locked.lot_size === null}
          />
          <SpecRow
            label="Issue dates"
            value={`${formatDate(payload.locked.open_date)} – ${formatDate(payload.locked.close_date)}`}
          />
          <SpecRow label="Issue size" value={payload.locked.issue_size} />
          {payload.locked.listing_date ? (
            <SpecRow label="Listing" value={formatDate(payload.locked.listing_date)} last />
          ) : null}
        </div>

        {/* P1 — Subscription block (structured per-category) */}
        <SubscriptionBlock
          subscription={subscription}
          isOpen={payload.status === "open"}
          refreshing={subRefreshing}
          refreshError={subRefreshError}
          onRefresh={() => void handleRefreshSubscription()}
        />

        {/* P1 — RHP, allotment/registrar, GMP */}
        <OfficialLinksBlock payload={payload} />
      </div>

      {/* Registered state — show confirmation, offer withdraw */}
      {isRegistered && (
        <RegisteredConfirmation
          application={cardState.application}
          duplicate={cardState.duplicate}
          symbol={payload.symbol}
          status={payload.status}
          onWithdraw={() => void handleWithdraw()}
          isWithdrawing={false}
        />
      )}

      {/* Withdrawing state */}
      {cardState.kind === "withdrawing" && (
        <div className="flex items-center gap-2 px-5 py-4 text-[12.5px] text-muted-foreground">
          <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
          Withdrawing…
        </div>
      )}

      {/* Withdrawn state */}
      {isWithdrawn && (
        <div className="px-5 py-4 text-[12.5px] text-muted-foreground">
          Intent withdrawn. You can re-register before the issue closes.
        </div>
      )}

      {/* Editable form — hidden once registered/withdrawing/withdrawn */}
      {!isRegistered && cardState.kind !== "withdrawing" && !isWithdrawn && (
        <EditableForm
          payload={payload}
          category={category}
          quantityLots={quantityLots}
          bidPriceMode={bidPriceMode}
          bidPrice={bidPrice}
          upiId={upiId}
          amountPreview={amountPreview}
          validationResult={validationResult}
          upiFormatNote={upiFormatNote}
          upiValid={upiValid}
          cutoffAllowed={cutoffAllowed}
          isReadOnly={isReadOnly}
          isSaving={cardState.kind === "saving"}
          saveError={cardState.kind === "error" ? cardState.message : null}
          isOversubscribed={isOversubscribed}
          categorySubValue={categorySubValue}
          onCategoryChange={handleCategoryChange}
          onQuantityChange={setQuantityLots}
          onBidPriceModeChange={setBidPriceMode}
          onBidPriceChange={setBidPrice}
          onUpiIdChange={setUpiId}
          onRegister={() => void handleRegister()}
          onSetupReminders={
            payload.automatable && onSetupReminders
              ? () => onSetupReminders(payload.symbol)
              : undefined
          }
        />
      )}

      {/* Disclaimer footer */}
      <div
        className={cn(
          "mx-5 mt-3.5 mb-5 flex items-start gap-2 rounded-xl bg-amber-50/60 px-3 py-2.5 dark:bg-amber-500/[0.06]",
        )}
      >
        <ShieldAlert
          className="mt-px h-3.5 w-3.5 shrink-0 text-amber-600/80 dark:text-amber-400/80"
          aria-hidden="true"
        />
        <p className="text-[10.5px] leading-relaxed text-amber-700/90 dark:text-amber-300/90">
          {payload.disclaimer}
        </p>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// P1 sub-components
// ---------------------------------------------------------------------------

/**
 * SubscriptionBlock — renders the per-category subscription data with
 * an as-of timestamp and a Refresh button (only when status=="open").
 */
function SubscriptionBlock({
  subscription,
  isOpen,
  refreshing,
  refreshError,
  onRefresh,
}: {
  subscription: IpoSubscription | null;
  isOpen: boolean;
  refreshing: boolean;
  refreshError: string | null;
  onRefresh: () => void;
}): React.ReactElement | null {
  const asOfLabel = subscription?.as_of ? formatAsOf(subscription.as_of) : null;
  const cats = subscription
    ? ([
        ["RII", subscription.rii],
        ["NII", subscription.nii],
        ["QIB", subscription.qib],
        ["EMP", subscription.employee],
        ["SH", subscription.shareholder],
      ] as const).filter(([, v]) => v !== null)
    : [];

  return (
    <div className="flex flex-col gap-2 border-t border-border/40 pt-3">
      <div className="flex items-center justify-between gap-2">
        <span className="text-[11px] font-medium uppercase tracking-[0.08em] text-muted-foreground/80">
          Subscription
        </span>
        <div className="flex items-center gap-2">
          {asOfLabel && (
            <span className="text-[10px] tabular-nums text-muted-foreground/55">as of {asOfLabel}</span>
          )}
          {isOpen && (
            <button
              type="button"
              onClick={onRefresh}
              disabled={refreshing}
              aria-label="Refresh subscription data"
              title="Refresh subscription from NSE"
              className={cn(
                "shrink-0 rounded-full p-1 text-muted-foreground/70 transition-colors",
                "hover:bg-muted hover:text-foreground",
                "disabled:cursor-not-allowed disabled:opacity-50",
              )}
            >
              <RefreshCw
                className={cn("h-3 w-3", refreshing && "animate-spin")}
                aria-hidden="true"
              />
            </button>
          )}
        </div>
      </div>

      {cats.length > 0 ? (
        <div className="flex flex-wrap gap-x-3 gap-y-1 text-[12px] tabular-nums">
          {cats.map(([label, value]) => (
            <span key={label} className="text-muted-foreground">
              {label}{" "}
              <span className="font-medium text-foreground">{(value as number).toFixed(1)}×</span>
            </span>
          ))}
        </div>
      ) : (
        <span className="text-[12px] text-muted-foreground">Not available yet</span>
      )}

      {refreshError && (
        <span className="text-[10px] text-amber-600 dark:text-amber-400">{refreshError}</span>
      )}
    </div>
  );
}

/**
 * OfficialLinksBlock — RHP prospectus link, allotment/registrar line,
 * and GMP chip (only if payload.gmp is present — absent in v1).
 */
function OfficialLinksBlock({
  payload,
}: {
  payload: IpoApplicationPayload & { gmp?: { value: number; disclaimer: string } };
}): React.ReactElement | null {
  const { locked } = payload;
  const hasRhp = Boolean(locked.rhp_url);
  const hasAllotment = Boolean(locked.allotment_deeplink);
  const gmp = payload.gmp;

  return (
    <div className="flex flex-col gap-2">
      <div className="flex flex-wrap gap-2">
        {hasRhp && (
          <a
            href={locked.rhp_url!}
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex items-center gap-1.5 rounded-full border border-border/60 px-3 py-1.5 text-[11px] font-medium text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
          >
            <ExternalLink className="h-3 w-3 shrink-0" aria-hidden="true" />
            Prospectus (RHP)
          </a>
        )}
        {hasAllotment && (
          <a
            href={locked.allotment_deeplink!}
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex items-center gap-1.5 rounded-full border border-border/60 px-3 py-1.5 text-[11px] font-medium text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
          >
            <ExternalLink className="h-3 w-3 shrink-0" aria-hidden="true" />
            Allotment{locked.registrar ? ` · ${locked.registrar}` : ""}
          </a>
        )}
      </div>
      {!hasAllotment && (
        <p className="text-[11px] text-muted-foreground/70">
          Allotment: check with your broker / registrar
        </p>
      )}

      {/* GMP chip — only rendered when payload.gmp is present (v1: always absent) */}
      {gmp && (
        <div className="flex items-start gap-1.5 rounded-lg border border-amber-400/30 bg-amber-50/40 px-2.5 py-2 dark:bg-amber-500/[0.06]">
          <AlertCircle
            className="mt-px h-3 w-3 shrink-0 text-amber-600/80 dark:text-amber-400/80"
            aria-hidden="true"
          />
          <div className="flex flex-col gap-0.5">
            <span className="text-[11px] font-medium text-foreground">
              GMP ≈ {gmp.value > 0 ? "+" : ""}{formatIndianCurrency(gmp.value)}
            </span>
            <span className="text-[10px] leading-snug text-amber-700/80 dark:text-amber-300/80">
              {gmp.disclaimer}
            </span>
          </div>
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// EditableForm sub-component
// ---------------------------------------------------------------------------

function EditableForm({
  payload,
  category,
  quantityLots,
  bidPriceMode,
  bidPrice,
  upiId,
  amountPreview,
  validationResult,
  upiFormatNote,
  upiValid,
  cutoffAllowed,
  isReadOnly,
  isSaving,
  saveError,
  isOversubscribed,
  categorySubValue,
  onCategoryChange,
  onQuantityChange,
  onBidPriceModeChange,
  onBidPriceChange,
  onUpiIdChange,
  onRegister,
  onSetupReminders,
}: {
  payload: IpoApplicationPayload;
  category: IpoCategory;
  quantityLots: number;
  bidPriceMode: IpoBidPriceMode;
  bidPrice: string;
  upiId: string;
  amountPreview: number | null;
  validationResult: ValidationResult;
  upiFormatNote: string | null;
  upiValid: boolean;
  cutoffAllowed: boolean;
  isReadOnly: boolean;
  isSaving: boolean;
  saveError: string | null;
  /** True when the selected category's subscription > 1× (oversubscribed). */
  isOversubscribed: boolean;
  /** The raw subscription multiplier for the selected category, for messaging. */
  categorySubValue: number | null;
  onCategoryChange: (c: IpoCategory) => void;
  onQuantityChange: (q: number) => void;
  onBidPriceModeChange: (m: IpoBidPriceMode) => void;
  onBidPriceChange: (p: string) => void;
  onUpiIdChange: (id: string) => void;
  onRegister: () => void;
  /** When defined (payload.automatable is true), renders the active
   *  "Set up reminders for open day" CTA in place of the disabled ghost. */
  onSetupReminders?: () => void;
}): React.ReactElement {
  const { validation } = payload;

  // Whether the category allows cutoff
  const catAllowsCutoff = category === "retail" || category === "employee";

  const canRegister =
    !isReadOnly &&
    !isSaving &&
    validationResult.ok &&
    amountPreview !== null;

  return (
    <div className="flex flex-col gap-3.5 px-5 pt-4">
      {/* Category */}
      <FormRow label="Category">
        <div className="relative">
          <select
            value={category}
            onChange={(e) => onCategoryChange(e.target.value as IpoCategory)}
            disabled={isReadOnly || isSaving}
            className={cn(
              "w-full appearance-none rounded-lg border border-border/60 bg-background px-3 py-2 pr-9 text-[12.5px] font-medium text-foreground",
              "transition-colors focus:border-foreground/30 focus:outline-none focus:ring-2 focus:ring-ring/30",
              "disabled:cursor-not-allowed disabled:opacity-60",
            )}
          >
            {validation.category_options.map((opt) => (
              <option key={opt} value={opt}>
                {CATEGORY_LABELS[opt]}
              </option>
            ))}
          </select>
          <ChevronDown
            className="pointer-events-none absolute right-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground/60"
            aria-hidden="true"
          />
        </div>
      </FormRow>

      {/* Quantity */}
      <FormRow label="Lots">
        <div className="flex flex-col gap-2">
          <div className="flex items-center justify-between gap-2">
            {/* Premium stepper — single rounded shell with split controls. */}
            <div className="inline-flex items-center rounded-lg border border-border/60 bg-background">
              <button
                type="button"
                aria-label="Decrease lots"
                disabled={isReadOnly || isSaving || quantityLots <= validation.min_lots}
                onClick={() => onQuantityChange(Math.max(validation.min_lots, quantityLots - 1))}
                className={cn(
                  "flex h-7 w-7 items-center justify-center rounded-l-lg text-[15px] text-muted-foreground",
                  "transition-colors hover:bg-muted hover:text-foreground",
                  "disabled:cursor-not-allowed disabled:opacity-30",
                )}
              >
                −
              </button>
              <input
                type="number"
                min={validation.min_lots}
                value={quantityLots}
                disabled={isReadOnly || isSaving}
                onChange={(e) => {
                  const v = parseInt(e.target.value, 10);
                  if (!isNaN(v)) onQuantityChange(v);
                }}
                className={cn(
                  "w-10 border-x border-border/60 bg-transparent py-1.5 text-center text-[12.5px] font-semibold tabular-nums text-foreground",
                  "focus:outline-none",
                  "disabled:cursor-not-allowed disabled:opacity-60",
                  "[appearance:textfield] [&::-webkit-inner-spin-button]:appearance-none [&::-webkit-outer-spin-button]:appearance-none",
                )}
              />
              <button
                type="button"
                aria-label="Increase lots"
                disabled={isReadOnly || isSaving}
                onClick={() => onQuantityChange(quantityLots + 1)}
                className={cn(
                  "flex h-7 w-7 items-center justify-center rounded-r-lg text-[15px] text-muted-foreground",
                  "transition-colors hover:bg-muted hover:text-foreground",
                  "disabled:cursor-not-allowed disabled:opacity-30",
                )}
              >
                +
              </button>
            </div>
            {payload.locked.lot_size !== null && (
              <span className="text-[11px] text-muted-foreground">
                {(quantityLots * payload.locked.lot_size).toLocaleString("en-IN")} shares
              </span>
            )}
          </div>
          {/* P1 — oversubscription note */}
          {isOversubscribed && categorySubValue !== null && (
            <p
              role="note"
              className="flex items-start gap-1.5 rounded-lg bg-amber-50/70 px-2.5 py-2 text-[10.5px] leading-snug text-amber-700/90 dark:bg-amber-500/[0.06] dark:text-amber-300/90"
            >
              <AlertCircle className="mt-px h-3 w-3 shrink-0" aria-hidden="true" />
              {categorySubValue.toFixed(1)}× oversubscribed — allotment is by lottery; extra lots won&apos;t raise your odds.
            </p>
          )}
        </div>
      </FormRow>

      {/* Bid price mode — segmented control (matches the app's chart-mode toggle) */}
      <FormRow label="Bid at">
        <div className="inline-flex w-full overflow-hidden rounded-lg border border-border/60">
          <SegmentButton
            active={bidPriceMode === "cutoff"}
            disabled={isReadOnly || isSaving || !catAllowsCutoff || !cutoffAllowed}
            title={!catAllowsCutoff ? "Cut-off only for retail/employee" : undefined}
            onClick={() => onBidPriceModeChange("cutoff")}
          >
            Cut-off price
          </SegmentButton>
          <SegmentButton
            active={bidPriceMode === "fixed"}
            disabled={isReadOnly || isSaving}
            onClick={() => onBidPriceModeChange("fixed")}
          >
            Fixed price
          </SegmentButton>
        </div>
      </FormRow>

      {/* Bid price input — only when fixed */}
      {bidPriceMode === "fixed" && (
        <FormRow label="Bid price (₹)">
          <input
            type="number"
            placeholder={
              payload.locked.price_band
                ? `${payload.locked.price_band.min}–${payload.locked.price_band.max}`
                : "Enter price"
            }
            value={bidPrice}
            disabled={isReadOnly || isSaving}
            onChange={(e) => onBidPriceChange(e.target.value)}
            className={cn(
              "w-full rounded-lg border border-border/60 bg-background px-3 py-2 text-[12.5px] font-medium tabular-nums text-foreground",
              "transition-colors focus:border-foreground/30 focus:outline-none focus:ring-2 focus:ring-ring/30",
              "disabled:cursor-not-allowed disabled:opacity-60",
            )}
          />
          {payload.locked.price_band && (
            <p className="mt-1.5 text-[10.5px] text-muted-foreground">
              Band: {formatIndianCurrency(payload.locked.price_band.min)} – {formatIndianCurrency(payload.locked.price_band.max)}
            </p>
          )}
        </FormRow>
      )}

      {/* UPI ID */}
      <FormRow label="UPI ID">
        <div className="flex flex-col gap-1.5">
          <input
            type="text"
            placeholder="yourname@upi"
            value={upiId}
            disabled={isReadOnly || isSaving}
            onChange={(e) => onUpiIdChange(e.target.value)}
            className={cn(
              "w-full rounded-lg border bg-background px-3 py-2 text-[12.5px] text-foreground",
              "transition-colors focus:outline-none focus:ring-2 focus:ring-ring/30",
              "disabled:cursor-not-allowed disabled:opacity-60",
              upiFormatNote
                ? "border-amber-400/60 focus:border-amber-500/50 dark:border-amber-500/40"
                : "border-border/60 focus:border-foreground/30",
            )}
          />
          {upiFormatNote && (
            <p className="text-[10.5px] text-amber-600 dark:text-amber-400" role="alert">
              {upiFormatNote}
            </p>
          )}
        </div>
      </FormRow>

      {/* Amount summary — the hero number, premium emphasis */}
      {amountPreview !== null && (
        <div className="flex items-center justify-between border-t border-border/40 pt-3.5">
          <span className="text-[12px] font-medium text-muted-foreground">
            Total at {bidPriceMode === "cutoff" ? "cut-off" : "bid"}
          </span>
          <span className="text-[16px] font-semibold tabular-nums tracking-tight text-foreground">
            {formatIndianCurrency(amountPreview)}
          </span>
        </div>
      )}

      {/* Validation error or amount-not-computable notice */}
      {!validationResult.ok && payload.locked.price_band && payload.locked.lot_size && (
        <div
          role="alert"
          className="flex items-start gap-1.5 rounded-xl bg-destructive/10 px-3 py-2.5 text-[11.5px] text-destructive"
        >
          <AlertCircle className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden="true" />
          <span>{validationResult.reason}</span>
        </div>
      )}

      {/* CTA */}
      {isReadOnly ? (
        <p className="text-center text-[11.5px] text-muted-foreground">
          This IPO is closed — registration is no longer available.
        </p>
      ) : (
        <button
          type="button"
          onClick={onRegister}
          disabled={!canRegister}
          data-testid="ipo-register-button"
          className={cn(
            "inline-flex h-8 w-full items-center justify-center gap-2 rounded-full bg-primary text-[12px] font-medium tracking-tight text-primary-foreground transition-all",
            "hover:bg-primary/90 active:scale-[0.98]",
            "disabled:cursor-not-allowed disabled:opacity-40",
          )}
        >
          {isSaving ? (
            <>
              <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />
              <span>Registering…</span>
            </>
          ) : (
            <>
              <span>Register intent</span>
              <ArrowUpRight className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
            </>
          )}
        </button>
      )}

      {/* Reminders CTA — active when automatable (P2), disabled ghost otherwise */}
      <div className="flex justify-center">
        {onSetupReminders ? (
          <button
            type="button"
            onClick={onSetupReminders}
            data-testid="ipo-setup-reminders-button"
            className={cn(
              "inline-flex items-center gap-1.5 rounded-full px-3 py-1.5 text-[11.5px] font-medium",
              "text-muted-foreground transition-colors hover:bg-muted hover:text-foreground",
            )}
          >
            <BellRing className="h-3 w-3 shrink-0" aria-hidden="true" />
            Set up reminders for open day
          </button>
        ) : (
          <button
            type="button"
            disabled
            aria-disabled="true"
            title="Event-triggered reminders are coming in a future update"
            className="text-[11px] text-muted-foreground/40 cursor-not-allowed"
          >
            Set up reminders (coming soon)
          </button>
        )}
      </div>

      {/* Error message */}
      {saveError && (
        <p
          role="alert"
          data-testid="ipo-register-error"
          className="rounded-xl bg-destructive/10 px-3 py-2.5 text-[11.5px] text-destructive"
          style={{
            animation: "draftCardIn-quartr 240ms cubic-bezier(0.22, 1, 0.36, 1) both",
          }}
        >
          {saveError}
        </p>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Registered confirmation
// ---------------------------------------------------------------------------

function RegisteredConfirmation({
  application,
  duplicate,
  symbol,
  status,
  onWithdraw,
  isWithdrawing,
}: {
  application: IpoApplication;
  duplicate?: boolean;
  symbol: string;
  status: IpoApplicationPayload["status"];
  onWithdraw: () => void;
  isWithdrawing: boolean;
}): React.ReactElement {
  const canWithdraw = status === "open" || status === "upcoming";

  return (
    <div
      className="flex flex-col gap-3 px-5 py-4"
      data-testid="ipo-registered-confirmation"
      style={{
        animation: "draftCardIn-quartr 360ms cubic-bezier(0.22, 1, 0.36, 1) both",
      }}
    >
      <div className="flex items-center gap-2">
        <CheckCircle2
          className="h-5 w-5 text-emerald-500"
          strokeWidth={2.25}
          aria-hidden="true"
        />
        <div>
          <p className="text-[13px] font-semibold tracking-tight text-foreground">
            Intent registered
          </p>
          <p className="text-[11.5px] text-muted-foreground">
            {symbol} · {CATEGORY_LABELS[application.category]} · {application.quantity_lots} lot{application.quantity_lots !== 1 ? "s" : ""}
            {application.amount_estimate
              ? ` · ≈ ${formatIndianCurrency(application.amount_estimate)} estimated`
              : ""}
          </p>
        </div>
      </div>

      {duplicate && (
        <div
          role="status"
          className="rounded-lg bg-amber-50 px-3 py-2 text-[11px] text-amber-700 dark:bg-amber-500/10 dark:text-amber-300"
        >
          You had an earlier application for this IPO — this replaces it.
        </div>
      )}

      <p className="text-[11px] text-muted-foreground">
        Your intent is logged. You must{" "}
        <strong>place and approve the mandate in your broker or UPI app by 5 PM on close day</strong>
        {" "}— Pivot cannot submit or fund this bid.
      </p>

      {canWithdraw && (
        <button
          type="button"
          onClick={onWithdraw}
          disabled={isWithdrawing}
          data-testid="ipo-withdraw-button"
          className={cn(
            "inline-flex items-center gap-1.5 self-start rounded-md px-3 py-1.5 text-[11.5px] font-medium text-muted-foreground",
            "border border-border/60 transition-colors hover:bg-muted hover:text-foreground",
            "disabled:cursor-not-allowed disabled:opacity-60",
          )}
        >
          {isWithdrawing ? (
            <Loader2 className="h-3 w-3 animate-spin" aria-hidden="true" />
          ) : (
            <Undo2 className="h-3 w-3" aria-hidden="true" />
          )}
          Withdraw intent
        </button>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Small helpers
// ---------------------------------------------------------------------------

function FormRow({ label, children }: { label: string; children: React.ReactNode }): React.ReactElement {
  return (
    <div className="flex flex-col gap-1.5">
      <label className="text-[11px] font-medium uppercase tracking-[0.08em] text-muted-foreground/80">
        {label}
      </label>
      {children}
    </div>
  );
}

/** One segment of the "Bid at" segmented control. Mirrors the app's chart-mode
 *  toggle: active segment is a solid dark fill, inactive are quiet text. */
function SegmentButton({
  active,
  disabled,
  title,
  onClick,
  children,
}: {
  active: boolean;
  disabled?: boolean;
  title?: string;
  onClick: () => void;
  children: React.ReactNode;
}): React.ReactElement {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      title={title}
      className={cn(
        "flex-1 px-3 py-1.5 text-[12px] font-medium transition-colors",
        active
          ? "bg-foreground text-background"
          : "text-muted-foreground hover:text-foreground",
        "disabled:cursor-not-allowed disabled:opacity-40 disabled:hover:text-muted-foreground",
      )}
    >
      {children}
    </button>
  );
}

/** A single hairline-divided fact row in the spec sheet (label left, value right). */
function SpecRow({
  label,
  value,
  muted = false,
  last = false,
}: {
  label: string;
  value: string;
  muted?: boolean;
  last?: boolean;
}): React.ReactElement {
  return (
    <div
      className={cn(
        "flex items-center justify-between gap-3 py-2.5",
        !last && "border-b border-border/40",
      )}
    >
      <span className="text-[11.5px] text-muted-foreground">{label}</span>
      <span
        className={cn(
          "text-[12.5px] font-medium tabular-nums tracking-tight",
          muted ? "text-muted-foreground" : "text-foreground",
        )}
      >
        {value}
      </span>
    </div>
  );
}

function formatDate(dateStr: string): string {
  try {
    const d = new Date(dateStr + "T00:00:00");
    return d.toLocaleDateString("en-IN", { day: "numeric", month: "short" });
  } catch {
    return dateStr;
  }
}
