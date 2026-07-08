"use client";

/**
 * DeployConfirmModal — the confirmation step between pressing "Deploy" and a
 * real broker order.
 *
 * It shows the strategy's affordable entry basket as ONE LOT: every company at
 * its minimum whole-share count. The combined basket = 1 lot. The user can:
 *   • buy N lots (the "Lots" stepper scales every company together), and
 *   • fine-tune any single company's share count up or down ("as available").
 *
 * Confirm places the adjusted basket through the user's CONNECTED broker via
 * placeExpression(). If no broker is connected the backend returns a 409 and we
 * show the "connect your broker" message right here — no order, no fake success.
 */

import * as React from "react";
import { Loader2, Minus, Plus, X, CheckCircle2 } from "lucide-react";
import type { ExpressionDetail } from "@/lib/types";
import { isError } from "@/lib/types";
import { placeExpression } from "@/lib/api";
import type { ViewPlaceResponse } from "@/lib/api";
import { placeableEntryLegs, tierLabel } from "./view-format";

const FONT = "var(--font-display)";

const inr = (n: number): string =>
  `₹${Math.round(n).toLocaleString("en-IN")}`;

export function DeployConfirmModal({
  expression,
  onClose,
  onPlaced,
}: {
  expression: ExpressionDetail;
  onClose: () => void;
  onPlaced: (result: ViewPlaceResponse) => void;
}): React.ReactElement {
  const baseLegs = React.useMemo(
    () => placeableEntryLegs(expression.entry),
    [expression.entry],
  );

  // Lot multiplier (coarse) + per-company absolute overrides (fine). Effective
  // qty = override ?? base.shares * lots. Changing lots resets the fine-tuning.
  const [lots, setLots] = React.useState(1);
  const [overrides, setOverrides] = React.useState<Record<string, number>>({});

  const effQty = React.useCallback(
    (symbol: string, baseShares: number): number =>
      overrides[symbol] ?? baseShares * lots,
    [overrides, lots],
  );

  const [placing, setPlacing] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);
  const [placed, setPlaced] = React.useState<ViewPlaceResponse | null>(null);

  // Esc closes.
  React.useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  function setLotsSafe(next: number) {
    setLots(Math.max(1, Math.trunc(next)));
    setOverrides({}); // re-derive every row from base × lots
    setError(null);
  }

  function bumpCompany(symbol: string, baseShares: number, delta: number) {
    setOverrides((prev) => {
      const cur = prev[symbol] ?? baseShares * lots;
      return { ...prev, [symbol]: Math.max(0, cur + delta) };
    });
    setError(null);
  }

  const oneLotTotal = baseLegs.reduce((s, l) => s + l.shares * l.price, 0);
  const total = baseLegs.reduce(
    (s, l) => s + effQty(l.symbol, l.shares) * l.price,
    0,
  );
  const nActive = baseLegs.filter((l) => effQty(l.symbol, l.shares) > 0).length;

  async function handleConfirm() {
    if (placing || placed) return;
    setError(null);
    const legs = baseLegs
      .map((l) => ({ symbol: l.symbol, quantity: effQty(l.symbol, l.shares) }))
      .filter((l) => l.quantity > 0);
    if (legs.length === 0) {
      setError("Add at least one share to place this basket.");
      return;
    }
    setPlacing(true);
    const res = await placeExpression(expression.id, { legs });
    setPlacing(false);
    if (isError(res)) {
      setError(res.error.message);
      return;
    }
    setPlaced(res.data);
    onPlaced(res.data);
  }

  const title = expression.strategy_name ?? expression.plain_label ?? tierLabel(expression.tier);

  return (
    <>
      {/* Scrim */}
      <div
        onClick={onClose}
        style={{
          position: "fixed",
          inset: 0,
          zIndex: 400,
          background: "rgba(0,0,0,0.42)",
          backdropFilter: "blur(2px)",
        }}
      />
      {/* Dialog */}
      <div
        role="dialog"
        aria-modal="true"
        aria-label={`Deploy ${title}`}
        style={{
          position: "fixed",
          left: "50%",
          top: "50%",
          transform: "translate(-50%, -50%)",
          width: "min(560px, calc(100vw - 24px))",
          maxHeight: "calc(100vh - 48px)",
          display: "flex",
          flexDirection: "column",
          zIndex: 401,
          background: "var(--bg-primary)",
          border: "1px solid var(--glass-border)",
          borderRadius: "var(--radius-lg)",
          boxShadow: "0 16px 56px rgba(0,0,0,0.32)",
          fontFamily: FONT,
          overflow: "hidden",
          animation: "deploy-modal-in 0.2s var(--ease-quartr, ease-out)",
        }}
      >
        <style>{`@keyframes deploy-modal-in { from { transform: translate(-50%, -46%); opacity: 0; } to { transform: translate(-50%, -50%); opacity: 1; } }`}</style>

        {/* Header */}
        <div
          style={{
            display: "flex",
            alignItems: "flex-start",
            justifyContent: "space-between",
            gap: 12,
            padding: "18px 20px 14px",
            borderBottom: "1px solid var(--glass-border)",
          }}
        >
          <div>
            <div
              style={{
                fontSize: 12,
                fontWeight: 500,
                letterSpacing: "0.04em",
                textTransform: "uppercase",
                color: "var(--text-tertiary)",
                marginBottom: 4,
              }}
            >
              Confirm & place
            </div>
            <h2
              style={{
                fontSize: 18,
                fontWeight: 600,
                letterSpacing: "-0.01em",
                color: "var(--text-primary)",
                margin: 0,
                lineHeight: 1.3,
              }}
            >
              {title}
            </h2>
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close"
            style={{
              display: "inline-flex",
              alignItems: "center",
              justifyContent: "center",
              width: 30,
              height: 30,
              borderRadius: "var(--radius-md)",
              border: "1px solid var(--glass-border)",
              background: "transparent",
              color: "var(--text-secondary)",
              cursor: "pointer",
              flexShrink: 0,
            }}
          >
            <X size={16} aria-hidden />
          </button>
        </div>

        {/* Lots control */}
        <div
          style={{
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            gap: 12,
            padding: "14px 20px",
            borderBottom: "1px solid var(--glass-border)",
          }}
        >
          <div>
            <div style={{ fontSize: 14, fontWeight: 600, color: "var(--text-primary)" }}>
              Lots
            </div>
            <div style={{ fontSize: 12.5, color: "var(--text-tertiary)", lineHeight: 1.4 }}>
              1 lot = the full basket below ({inr(oneLotTotal)})
            </div>
          </div>
          <Stepper
            value={lots}
            onDec={() => setLotsSafe(lots - 1)}
            onInc={() => setLotsSafe(lots + 1)}
            decDisabled={lots <= 1 || placing || !!placed}
            incDisabled={placing || !!placed}
          />
        </div>

        {/* Company rows */}
        <div style={{ overflowY: "auto", padding: "6px 8px", flex: "1 1 auto" }}>
          {baseLegs.map((l) => {
            const qty = effQty(l.symbol, l.shares);
            return (
              <div
                key={l.symbol}
                style={{
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "space-between",
                  gap: 10,
                  padding: "10px 12px",
                  borderRadius: "var(--radius-md)",
                }}
              >
                <div style={{ minWidth: 0 }}>
                  <div
                    style={{
                      fontSize: 14.5,
                      fontWeight: 600,
                      color: qty > 0 ? "var(--text-primary)" : "var(--text-tertiary)",
                      whiteSpace: "nowrap",
                      overflow: "hidden",
                      textOverflow: "ellipsis",
                    }}
                  >
                    {l.symbol}
                    {l.role === "core" && (
                      <span style={{ color: "var(--text-tertiary)", fontWeight: 500 }}>
                        {" "}· ETF core
                      </span>
                    )}
                  </div>
                  <div style={{ fontSize: 12.5, color: "var(--text-tertiary)" }}>
                    {l.price > 0 ? `${inr(l.price)}/sh` : "priced at deploy"}
                    {qty > 0 ? ` · ${inr(qty * l.price)}` : ""}
                  </div>
                </div>
                <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
                  <Stepper
                    value={qty}
                    onDec={() => bumpCompany(l.symbol, l.shares, -1)}
                    onInc={() => bumpCompany(l.symbol, l.shares, +1)}
                    decDisabled={qty <= 0 || placing || !!placed}
                    incDisabled={placing || !!placed}
                    compact
                  />
                </div>
              </div>
            );
          })}
        </div>

        {/* Footer — total + error + actions */}
        <div
          style={{
            borderTop: "1px solid var(--glass-border)",
            padding: "14px 20px 16px",
            display: "flex",
            flexDirection: "column",
            gap: 12,
          }}
        >
          <div style={{ display: "flex", alignItems: "baseline", justifyContent: "space-between" }}>
            <span style={{ fontSize: 13, color: "var(--text-secondary)" }}>
              {nActive} {nActive === 1 ? "company" : "companies"} · {lots}{" "}
              {lots === 1 ? "lot" : "lots"}
            </span>
            <span style={{ fontSize: 18, fontWeight: 700, color: "var(--text-primary)" }}>
              {inr(total)}
            </span>
          </div>

          {error && (
            <div
              role="alert"
              style={{
                fontSize: 13,
                lineHeight: 1.5,
                color: "var(--color-loss)",
                background: "color-mix(in srgb, var(--color-loss) 8%, transparent)",
                border: "1px solid color-mix(in srgb, var(--color-loss) 30%, transparent)",
                borderRadius: "var(--radius-md)",
                padding: "9px 12px",
              }}
            >
              {error}
            </div>
          )}

          <div style={{ display: "flex", gap: 10 }}>
            <button
              type="button"
              onClick={onClose}
              disabled={placing}
              style={{
                flex: "0 0 auto",
                fontFamily: FONT,
                fontSize: 15,
                fontWeight: 600,
                color: "var(--text-secondary)",
                background: "transparent",
                border: "1px solid var(--glass-border)",
                borderRadius: "var(--radius-md)",
                padding: "11px 18px",
                cursor: placing ? "default" : "pointer",
              }}
            >
              {placed ? "Close" : "Cancel"}
            </button>
            <button
              type="button"
              onClick={handleConfirm}
              disabled={placing || !!placed || total <= 0}
              style={{
                flex: "1 1 auto",
                display: "inline-flex",
                alignItems: "center",
                justifyContent: "center",
                gap: 8,
                fontFamily: FONT,
                fontSize: 15,
                fontWeight: 600,
                color: "hsl(var(--primary-foreground))",
                background: "hsl(var(--primary))",
                border: "1px solid hsl(var(--primary))",
                borderRadius: "var(--radius-md)",
                padding: "11px 18px",
                cursor: placing || placed || total <= 0 ? "default" : "pointer",
                opacity: placing || placed || total <= 0 ? 0.7 : 1,
              }}
            >
              {placed ? (
                <>
                  <CheckCircle2 size={15} aria-hidden />
                  {placed.routed_to === "paper" ? "Filled (paper)" : "Order placed"}
                </>
              ) : placing ? (
                <>
                  <Loader2 size={15} className="animate-spin" aria-hidden />
                  Placing…
                </>
              ) : (
                `Place ${inr(total)}`
              )}
            </button>
          </div>
        </div>
      </div>
    </>
  );
}

function Stepper({
  value,
  onDec,
  onInc,
  decDisabled,
  incDisabled,
  compact = false,
}: {
  value: number;
  onDec: () => void;
  onInc: () => void;
  decDisabled: boolean;
  incDisabled: boolean;
  compact?: boolean;
}): React.ReactElement {
  const size = compact ? 28 : 32;
  const btn = (
    disabled: boolean,
    onClick: () => void,
    label: string,
    icon: React.ReactNode,
  ): React.ReactElement => (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      aria-label={label}
      style={{
        display: "inline-flex",
        alignItems: "center",
        justifyContent: "center",
        width: size,
        height: size,
        borderRadius: "var(--radius-md)",
        border: "1px solid var(--glass-border)",
        background: "transparent",
        color: disabled ? "var(--text-tertiary)" : "var(--text-primary)",
        cursor: disabled ? "default" : "pointer",
        opacity: disabled ? 0.5 : 1,
      }}
    >
      {icon}
    </button>
  );
  return (
    <div style={{ display: "inline-flex", alignItems: "center", gap: 8 }}>
      {btn(decDisabled, onDec, "Decrease", <Minus size={14} aria-hidden />)}
      <span
        style={{
          minWidth: compact ? 26 : 30,
          textAlign: "center",
          fontSize: 15,
          fontWeight: 600,
          fontVariantNumeric: "tabular-nums",
          color: "var(--text-primary)",
        }}
      >
        {value}
      </span>
      {btn(incDisabled, onInc, "Increase", <Plus size={14} aria-hidden />)}
    </div>
  );
}
