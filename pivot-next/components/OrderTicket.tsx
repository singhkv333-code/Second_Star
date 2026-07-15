"use client";

/**
 * OrderTicket — the Kite-style order window, rebuilt for Pivot.
 *
 * A bottom sheet that slides up when any Buy/Sell button dispatches
 * `pivot:open-order-ticket` (mirror of the option-chain global-host
 * pattern). Two tabs:
 *
 *   Quick   — the fewest fields that can place a trade: quantity +
 *             product; fires a MARKET order at the live price.
 *   Regular — quantity + price with Market/Limit, plus FUNCTIONAL
 *             GTT stop-loss / target exits (% from entry): both set →
 *             a true OCO bracket (paper: shared gtt_oco_group the
 *             evaluator cancels-on-fill; live: Kite two-leg GTT).
 *
 * Header: blue (buy) / red (sell), NSE/BSE exchange radios fed by
 * GET /markets/quote/{symbol}?exchange plus live NSE ticks, and the
 * side toggle. Submit → POST /orders/register (register-not-execute):
 * paper mode fills the simulated book; live mode routes via the
 * connected broker and the backend 409s honestly when none.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Info, RotateCw } from "lucide-react";
import { toast } from "sonner";
import {
  registerOrder,
  getPaperSummary,
  getStockQuote,
  getAccountMode,
  type RegisteredOrder,
} from "@/lib/api";
import { isError } from "@/lib/types";
import { subscribe, unsubscribe, type LiveTick } from "@/lib/liveQuoteManager";

// ---------------------------------------------------------------------------
// Public API — dispatch helper + event contract
// ---------------------------------------------------------------------------

export type OrderTicketOpenDetail = {
  symbol: string;
  side?: "BUY" | "SELL";
  name?: string;
};

const OPEN_EVENT = "pivot:open-order-ticket";

/** Open the global order ticket from anywhere (buttons, hover bars, cards). */
export function openOrderTicket(detail: OrderTicketOpenDetail): void {
  window.dispatchEvent(new CustomEvent(OPEN_EVENT, { detail }));
}

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const BUY_COLOR = "#4184f3";
const SELL_COLOR = "#eb5b3c";

type Tab = "Quick" | "Regular";
type OrderType = "MARKET" | "LIMIT";
type Product = "MIS" | "CNC";

function fmtInr(v: number | null | undefined): string {
  if (v === null || v === undefined || !Number.isFinite(v)) return "—";
  return `₹${v.toLocaleString("en-IN", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })}`;
}

// ---------------------------------------------------------------------------
// Host — mount ONCE in AppShell
// ---------------------------------------------------------------------------

export function OrderTicketHost(): React.ReactElement | null {
  const [detail, setDetail] = useState<OrderTicketOpenDetail | null>(null);

  useEffect(() => {
    const onOpen = (e: Event): void => {
      const d = (e as CustomEvent).detail as OrderTicketOpenDetail | undefined;
      if (d?.symbol) setDetail({ ...d, symbol: d.symbol.toUpperCase() });
    };
    window.addEventListener(OPEN_EVENT, onOpen);
    return () => window.removeEventListener(OPEN_EVENT, onOpen);
  }, []);

  if (!detail) return null;
  return (
    <OrderTicketSheet
      key={`${detail.symbol}-${detail.side ?? "BUY"}`}
      symbol={detail.symbol}
      initialSide={detail.side ?? "BUY"}
      onClose={() => setDetail(null)}
    />
  );
}

// ---------------------------------------------------------------------------
// The sheet
// ---------------------------------------------------------------------------

function OrderTicketSheet({
  symbol,
  initialSide,
  onClose,
}: {
  symbol: string;
  initialSide: "BUY" | "SELL";
  onClose: () => void;
}): React.ReactElement {
  const [side, setSide] = useState<"BUY" | "SELL">(initialSide);
  const [tab, setTab] = useState<Tab>("Regular");
  const [exchange, setExchange] = useState<"NSE" | "BSE">("NSE");
  const [product, setProduct] = useState<Product>("CNC");
  const [orderType, setOrderType] = useState<OrderType>("LIMIT");
  const [qty, setQty] = useState<string>("1");
  const [price, setPrice] = useState<string>("");
  // GTT bracket exits (Regular tab) — % moves from the entry price.
  const [slOn, setSlOn] = useState(false);
  const [slPct, setSlPct] = useState<string>("5");
  const [tpOn, setTpOn] = useState(false);
  const [tpPct, setTpPct] = useState<string>("10");
  const [nseLtp, setNseLtp] = useState<number | null>(null);
  const [bseLtp, setBseLtp] = useState<number | null>(null);
  const [accountMode, setAccountMode] = useState<"paper" | "live" | null>(null);
  const [available, setAvailable] = useState<number | null>(null);
  const [pending, setPending] = useState(false);
  // Track whether the user touched the price field — snapshots/ticks seed
  // it only while untouched, so we never clobber typed input.
  const priceDirty = useRef(false);

  const accent = side === "BUY" ? BUY_COLOR : SELL_COLOR;
  const ltp = exchange === "BSE" ? bseLtp : nseLtp;
  // Quick is always a market order; Regular follows the radio.
  const effectiveType: OrderType = tab === "Quick" ? "MARKET" : orderType;
  const needsPrice = effectiveType === "LIMIT";

  const seedPrice = useCallback((value: number): void => {
    if (!priceDirty.current) setPrice(String(value));
  }, []);

  // ── LTP: REST snapshot for both exchanges + live NSE ticks over WS ─
  useEffect(() => {
    let cancelled = false;
    void getStockQuote(symbol, "NSE").then((r) => {
      if (cancelled || isError(r)) return;
      setNseLtp(r.data.ltp);
      seedPrice(r.data.ltp);
    });
    void getStockQuote(symbol, "BSE").then((r) => {
      if (cancelled || isError(r)) return;
      setBseLtp(r.data.ltp);
    });
    const listener = (tick: LiveTick): void => {
      setNseLtp(tick.ltp);
      seedPrice(tick.ltp);
    };
    subscribe(symbol, listener);
    return () => {
      cancelled = true;
      unsubscribe(symbol, listener);
    };
  }, [symbol, seedPrice]);

  // ── Available funds — authoritative backend mode decides. Paper mode
  //    shows the paper book's buying power; live mode has no margins API
  //    yet, so it stays an honest "—". ─────────────────────────────────
  const loadFunds = useCallback((): void => {
    void getAccountMode().then((r) => {
      if (isError(r)) return;
      const mode = r.data.mode === "paper" ? "paper" : "live";
      setAccountMode(mode);
      if (mode !== "paper") {
        setAvailable(null);
        return;
      }
      void getPaperSummary().then((s) => {
        if (!isError(s) && s.data.exists) {
          setAvailable(s.data.buying_power ?? s.data.cash_available ?? null);
        }
      });
    });
  }, []);
  useEffect(loadFunds, [loadFunds]);

  // ── Esc closes ────────────────────────────────────────────────────
  useEffect(() => {
    const onKey = (e: KeyboardEvent): void => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  // ── Derived ───────────────────────────────────────────────────────
  const qtyNum = Math.floor(Number(qty));
  const priceNum = Number(price);
  const slPctNum = Number(slPct);
  const tpPctNum = Number(tpPct);
  const qtyValid = Number.isFinite(qtyNum) && qtyNum >= 1;
  const priceValid = !needsPrice || (Number.isFinite(priceNum) && priceNum > 0);
  const gttActive = tab === "Regular";
  const slValid =
    !gttActive || !slOn || (Number.isFinite(slPctNum) && slPctNum > 0 && slPctNum < 90);
  const tpValid =
    !gttActive || !tpOn || (Number.isFinite(tpPctNum) && tpPctNum > 0 && tpPctNum < 900);
  const canSubmit = qtyValid && priceValid && slValid && tpValid && !pending;

  const required = useMemo(() => {
    const unit = needsPrice && priceNum > 0 ? priceNum : ltp;
    if (!qtyValid || unit === null || !Number.isFinite(unit)) return null;
    return qtyNum * unit;
  }, [needsPrice, priceNum, ltp, qtyValid, qtyNum]);

  // ── Submit ────────────────────────────────────────────────────────
  const submit = useCallback(async (): Promise<void> => {
    if (!canSubmit) return;
    setPending(true);
    const result = await registerOrder({
      symbol,
      exchange,
      transaction_type: side,
      order_type: effectiveType,
      quantity: qtyNum,
      price: needsPrice ? priceNum : null,
      trigger_price: null,
      product,
      gtt_stoploss_pct: gttActive && slOn ? slPctNum : null,
      gtt_target_pct: gttActive && tpOn ? tpPctNum : null,
    });
    setPending(false);
    if (isError(result)) {
      toast.error(result.error.message);
      return;
    }
    const order = result.data as RegisteredOrder;
    const status = order.status ?? "registered";
    const exitBits: string[] = [];
    if (order.exits?.stoploss) {
      exitBits.push(`SL @ ${fmtInr(order.exits.stoploss.trigger_price)}`);
    }
    if (order.exits?.target) {
      exitBits.push(`target @ ${fmtInr(order.exits.target.trigger_price)}`);
    }
    const exitsNote = exitBits.length ? ` · ${exitBits.join(" / ")} armed` : "";
    // The paper broker can 201 with status "rejected" (e.g. market closed) —
    // that's a failed order and must not read as a success.
    if (/reject/i.test(status)) {
      toast.error(`${side} ${qtyNum} ${symbol} — ${status}`);
    } else if (order.queued) {
      // Market was closed → queued as an after-market order (AMO). Say so
      // plainly and point to where it can be cancelled.
      toast(`${side} ${qtyNum} ${symbol} queued`, {
        description:
          "Market closed — this order will execute at the next open. Cancel it before then from Portfolio → Orders.",
        duration: 9000,
      });
    } else {
      toast.success(`${side} ${qtyNum} ${symbol} — ${status}${exitsNote}`);
    }
    if (order.exits_error) {
      toast.warning(`Exits not armed: ${order.exits_error}`);
    }
    onClose();
  }, [
    canSubmit, symbol, exchange, side, effectiveType, qtyNum, needsPrice,
    priceNum, product, gttActive, slOn, slPctNum, tpOn, tpPctNum, onClose,
  ]);

  // Enter submits (Kite behaviour), unless focus is on a button.
  useEffect(() => {
    const onKey = (e: KeyboardEvent): void => {
      if (e.key === "Enter" && !(e.target instanceof HTMLButtonElement)) {
        void submit();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [submit]);

  return (
    <>
      {/* Transparent click-away layer — Kite has no scrim; outside click closes. */}
      <div
        data-testid="order-ticket-backdrop"
        onClick={onClose}
        style={{ position: "fixed", inset: 0, zIndex: 340, background: "transparent" }}
      />
      <div
        role="dialog"
        aria-label={`${side === "BUY" ? "Buy" : "Sell"} ${symbol}`}
        data-testid="order-ticket"
        style={{
          position: "fixed",
          left: "50%",
          bottom: 16,
          transform: "translateX(-50%)",
          width: "min(780px, calc(100vw - 24px))",
          zIndex: 341,
          borderRadius: 6,
          overflow: "hidden",
          background: "var(--bg-primary)",
          border: "1px solid var(--glass-border)",
          boxShadow: "0 12px 48px rgba(0,0,0,0.28)",
          fontFamily: "var(--font-ui)",
          animation: "order-ticket-up 0.22s var(--ease-quartr, ease-out)",
        }}
      >
        <style>{`@keyframes order-ticket-up { from { transform: translate(-50%, 24px); opacity: 0; } to { transform: translate(-50%, 0); opacity: 1; } }`}</style>

        {/* ── Header ─────────────────────────────────────────────── */}
        <div style={{ background: accent, color: "#fff", padding: "14px 18px 12px" }}>
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
            <div style={{ fontSize: 16, fontWeight: 600, letterSpacing: "0.01em" }}>
              {symbol}
            </div>
            {/* Side toggle — Kite's little switch flips buy ↔ sell. */}
            <button
              type="button"
              role="switch"
              aria-checked={side === "SELL"}
              aria-label={side === "BUY" ? "Switch to sell" : "Switch to buy"}
              title={side === "BUY" ? "Switch to SELL" : "Switch to BUY"}
              data-testid="order-ticket-side-toggle"
              onClick={() => setSide((s) => (s === "BUY" ? "SELL" : "BUY"))}
              style={{
                width: 40,
                height: 20,
                borderRadius: 999,
                border: "none",
                cursor: "pointer",
                background: "rgba(255,255,255,0.35)",
                position: "relative",
                transition: "background 0.15s",
              }}
            >
              <span
                style={{
                  position: "absolute",
                  top: 2,
                  left: side === "BUY" ? 2 : 22,
                  width: 16,
                  height: 16,
                  borderRadius: "50%",
                  background: "#fff",
                  transition: "left 0.18s var(--ease-quartr, ease-out)",
                }}
              />
            </button>
          </div>
          <div style={{ display: "flex", gap: 18, marginTop: 8, fontSize: 12.5 }}>
            {(
              [
                ["NSE", nseLtp],
                ["BSE", bseLtp],
              ] as const
            ).map(([ex, exLtp]) => {
              const selected = exchange === ex;
              const selectable = exLtp !== null;
              return (
                <button
                  key={ex}
                  type="button"
                  role="radio"
                  aria-checked={selected}
                  disabled={!selectable}
                  title={selectable ? `Route order to ${ex}` : `No ${ex} quote available`}
                  data-testid={`order-ticket-exchange-${ex.toLowerCase()}`}
                  onClick={() => {
                    setExchange(ex);
                    if (!priceDirty.current && exLtp !== null) setPrice(String(exLtp));
                  }}
                  style={{
                    display: "inline-flex",
                    alignItems: "center",
                    gap: 6,
                    background: "transparent",
                    border: "none",
                    padding: 0,
                    color: "#fff",
                    fontFamily: "var(--font-ui)",
                    fontSize: 12.5,
                    opacity: selected ? 1 : 0.65,
                    cursor: selectable ? "pointer" : "not-allowed",
                  }}
                >
                  <span
                    aria-hidden="true"
                    style={{
                      width: 12,
                      height: 12,
                      borderRadius: "50%",
                      border: "2px solid #fff",
                      background: selected ? "#fff" : "transparent",
                      boxShadow: selected ? `inset 0 0 0 2.5px ${accent}` : undefined,
                    }}
                  />
                  {ex} {exLtp !== null ? fmtInr(exLtp) : "—"}
                </button>
              );
            })}
          </div>
        </div>

        {/* ── Tabs — Quick and Regular, both live ────────────────── */}
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: 4,
            padding: "0 10px",
            borderBottom: "1px solid var(--glass-border)",
          }}
        >
          {(["Quick", "Regular"] as const).map((t) => {
            const active = tab === t;
            return (
              <button
                key={t}
                type="button"
                onClick={() => setTab(t)}
                data-testid={`order-ticket-tab-${t.toLowerCase()}`}
                aria-pressed={active}
                style={{
                  padding: "11px 14px 9px",
                  background: "transparent",
                  border: "none",
                  borderBottom: active ? `2px solid ${accent}` : "2px solid transparent",
                  color: active ? accent : "var(--text-secondary)",
                  fontFamily: "var(--font-ui)",
                  fontSize: 13,
                  fontWeight: active ? 600 : 500,
                  cursor: "pointer",
                }}
              >
                {t}
              </button>
            );
          })}
        </div>

        {/* ── Body ───────────────────────────────────────────────── */}
        <div style={{ padding: "14px 18px 0" }}>
          {/* Product row — shared by both tabs. */}
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
            <div style={{ display: "flex", gap: 22 }}>
              <RadioOption
                checked={product === "MIS"}
                accent={accent}
                onSelect={() => setProduct("MIS")}
                label="Intraday"
                sub="MIS"
                testId="order-ticket-product-mis"
              />
              <RadioOption
                checked={product === "CNC"}
                accent={accent}
                onSelect={() => setProduct("CNC")}
                label="Longterm"
                sub="CNC"
                testId="order-ticket-product-cnc"
              />
            </div>
            {tab === "Regular" && (
              <span
                title="Order validity options — coming soon"
                style={{ fontSize: 12.5, color: "var(--text-disabled)", cursor: "not-allowed" }}
              >
                Advanced ⌄
              </span>
            )}
          </div>

          {tab === "Quick" ? (
            /* ── Quick: the fewest fields that place a trade ──────── */
            <div
              style={{
                display: "grid",
                gridTemplateColumns: "1fr 2fr",
                gap: 14,
                marginTop: 14,
                paddingBottom: 16,
                borderBottom: "1px solid var(--glass-border)",
              }}
            >
              <TicketField label="Qty.">
                <input
                  type="number"
                  min={1}
                  step={1}
                  value={qty}
                  onChange={(e) => setQty(e.target.value)}
                  data-testid="order-ticket-qty"
                  aria-label="Quantity"
                  style={inputStyle(true)}
                />
              </TicketField>
              <div
                style={{
                  alignSelf: "end",
                  paddingBottom: 10,
                  fontSize: 12.5,
                  color: "var(--text-tertiary)",
                }}
              >
                Places a market order at the live price
                {ltp !== null ? ` (${fmtInr(ltp)})` : ""}.
              </div>
            </div>
          ) : (
            /* ── Regular: Qty + Price, Market/Limit, GTT exits ───── */
            <>
              <div
                style={{
                  display: "grid",
                  gridTemplateColumns: "1fr 1fr",
                  gap: 14,
                  marginTop: 14,
                }}
              >
                <TicketField label="Qty.">
                  <input
                    type="number"
                    min={1}
                    step={1}
                    value={qty}
                    onChange={(e) => setQty(e.target.value)}
                    data-testid="order-ticket-qty"
                    aria-label="Quantity"
                    style={inputStyle(true)}
                  />
                </TicketField>
                <TicketField label="Price">
                  <input
                    type="number"
                    min={0}
                    step={0.05}
                    value={needsPrice ? price : ""}
                    placeholder={needsPrice ? "" : "—"}
                    disabled={!needsPrice}
                    onChange={(e) => {
                      priceDirty.current = true;
                      setPrice(e.target.value);
                    }}
                    data-testid="order-ticket-price"
                    aria-label="Price"
                    style={inputStyle(needsPrice)}
                  />
                </TicketField>
              </div>

              <div
                style={{
                  display: "grid",
                  gridTemplateColumns: "1fr 1fr",
                  gap: 14,
                  marginTop: 10,
                  paddingBottom: 16,
                  borderBottom: "1px solid var(--glass-border)",
                }}
              >
                <div />
                <div style={{ display: "flex", gap: 18, justifyContent: "center" }}>
                  <RadioOption
                    checked={orderType === "MARKET"}
                    accent={accent}
                    onSelect={() => setOrderType("MARKET")}
                    label="Market"
                    testId="order-ticket-type-market"
                  />
                  <RadioOption
                    checked={orderType === "LIMIT"}
                    accent={accent}
                    onSelect={() => setOrderType("LIMIT")}
                    label="Limit"
                    testId="order-ticket-type-limit"
                  />
                </div>
              </div>

              {/* GTT stoploss/target — FUNCTIONAL bracket exits. Both on →
                  a true OCO pair (one fills, the other cancels). */}
              <div
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: 16,
                  padding: "12px 0",
                  borderBottom: "1px solid var(--glass-border)",
                  color: "var(--text-secondary)",
                  fontSize: 12.5,
                }}
              >
                <span
                  title="Good-till-triggered exits, sized as a % move from your entry price"
                  style={{
                    fontSize: 10,
                    fontWeight: 700,
                    fontStyle: "italic",
                    border: "1px solid var(--glass-border)",
                    borderRadius: 8,
                    padding: "1px 7px",
                    color: "var(--text-tertiary)",
                  }}
                >
                  gtt
                </span>
                <GttExitControl
                  label="Stoploss"
                  on={slOn}
                  pct={slPct}
                  valid={slValid}
                  accent={accent}
                  onToggle={(next) => setSlOn(next)}
                  onPct={(v) => setSlPct(v)}
                  testId="order-ticket-gtt-sl"
                />
                <GttExitControl
                  label="Target"
                  on={tpOn}
                  pct={tpPct}
                  valid={tpValid}
                  accent={accent}
                  onToggle={(next) => setTpOn(next)}
                  onPct={(v) => setTpPct(v)}
                  testId="order-ticket-gtt-tp"
                />
                <Info
                  size={14}
                  strokeWidth={2}
                  aria-hidden="true"
                  style={{ marginLeft: "auto", color: "var(--text-disabled)" }}
                />
              </div>
            </>
          )}
        </div>

        {/* ── Footer ─────────────────────────────────────────────── */}
        <div
          style={{
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            padding: "12px 18px 14px",
          }}
        >
          <div style={{ fontSize: 12.5, color: "var(--text-secondary)", display: "flex", gap: 16, alignItems: "center" }}>
            <span data-testid="order-ticket-required">
              Required <span style={{ color: accent }}>{fmtInr(required)}</span>
            </span>
            <span data-testid="order-ticket-available" style={{ display: "inline-flex", alignItems: "center", gap: 5 }}>
              Available{" "}
              <span style={{ color: "var(--text-primary)" }}>
                {accountMode === "paper" ? fmtInr(available) : "—"}
              </span>
              <button
                type="button"
                onClick={loadFunds}
                aria-label="Refresh available funds"
                title="Refresh available funds"
                style={{
                  border: "none",
                  background: "transparent",
                  color: "var(--text-tertiary)",
                  cursor: "pointer",
                  display: "inline-flex",
                  padding: 1,
                }}
              >
                <RotateCw size={12} strokeWidth={2} aria-hidden="true" />
              </button>
            </span>
          </div>
          <div style={{ display: "flex", gap: 10 }}>
            <button
              type="button"
              onClick={() => void submit()}
              disabled={!canSubmit}
              data-testid="order-ticket-submit"
              style={{
                minWidth: 88,
                padding: "9px 22px",
                borderRadius: 4,
                border: "none",
                background: accent,
                color: "#fff",
                fontFamily: "var(--font-ui)",
                fontSize: 14,
                fontWeight: 600,
                cursor: canSubmit ? "pointer" : "not-allowed",
                opacity: canSubmit ? 1 : 0.6,
              }}
            >
              {pending ? "…" : side === "BUY" ? "Buy" : "Sell"}
            </button>
            <button
              type="button"
              onClick={onClose}
              data-testid="order-ticket-cancel"
              style={{
                minWidth: 88,
                padding: "9px 22px",
                borderRadius: 4,
                border: "1px solid var(--glass-border)",
                background: "var(--bg-primary)",
                color: "var(--text-secondary)",
                fontFamily: "var(--font-ui)",
                fontSize: 14,
                fontWeight: 500,
                cursor: "pointer",
              }}
            >
              Cancel
            </button>
          </div>
        </div>
      </div>
    </>
  );
}

// ---------------------------------------------------------------------------
// Small pieces
// ---------------------------------------------------------------------------

function inputStyle(enabled: boolean): React.CSSProperties {
  return {
    width: "100%",
    padding: "10px 12px",
    borderRadius: 4,
    border: "1px solid var(--glass-border)",
    background: enabled ? "var(--bg-primary)" : "var(--bg-elevated)",
    color: enabled ? "var(--text-primary)" : "var(--text-disabled)",
    fontFamily: "var(--font-ui)",
    fontSize: 14,
    outline: "none",
    cursor: enabled ? "text" : "not-allowed",
  };
}

function TicketField({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}): React.ReactElement {
  return (
    <div>
      <div
        style={{
          fontSize: 11.5,
          color: "var(--text-tertiary)",
          marginBottom: 5,
          fontFamily: "var(--font-ui)",
        }}
      >
        {label}
      </div>
      {children}
    </div>
  );
}

function GttExitControl({
  label,
  on,
  pct,
  valid,
  accent,
  onToggle,
  onPct,
  testId,
}: {
  label: string;
  on: boolean;
  pct: string;
  valid: boolean;
  accent: string;
  onToggle: (next: boolean) => void;
  onPct: (value: string) => void;
  testId: string;
}): React.ReactElement {
  return (
    <label
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: 6,
        cursor: "pointer",
        color: on ? "var(--text-primary)" : "var(--text-secondary)",
      }}
    >
      <input
        type="checkbox"
        checked={on}
        onChange={(e) => onToggle(e.target.checked)}
        aria-label={`${label} GTT`}
        data-testid={`${testId}-check`}
        style={{ accentColor: accent }}
      />
      {label}
      <input
        type="number"
        min={0}
        step={0.5}
        value={on ? pct : ""}
        placeholder={on ? "" : "—"}
        disabled={!on}
        onChange={(e) => onPct(e.target.value)}
        aria-label={`${label} percent`}
        data-testid={`${testId}-pct`}
        style={{
          width: 64,
          padding: "3px 6px",
          border: "none",
          borderBottom: `1px dashed ${on && !valid ? "#e5484d" : "var(--glass-border)"}`,
          background: "transparent",
          color: "var(--text-primary)",
          fontFamily: "var(--font-ui)",
          fontSize: 12.5,
          outline: "none",
          textAlign: "right",
        }}
      />
      %
    </label>
  );
}

function RadioOption({
  checked,
  accent,
  onSelect,
  label,
  sub,
  testId,
}: {
  checked: boolean;
  accent: string;
  onSelect: () => void;
  label: string;
  sub?: string;
  testId?: string;
}): React.ReactElement {
  return (
    <button
      type="button"
      role="radio"
      aria-checked={checked}
      onClick={onSelect}
      data-testid={testId}
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: 7,
        background: "transparent",
        border: "none",
        padding: 0,
        cursor: "pointer",
        fontFamily: "var(--font-ui)",
        fontSize: 13,
        color: checked ? "var(--text-primary)" : "var(--text-secondary)",
      }}
    >
      <span
        aria-hidden="true"
        style={{
          width: 14,
          height: 14,
          borderRadius: "50%",
          border: checked ? `4.5px solid ${accent}` : "2px solid var(--text-tertiary)",
          background: "var(--bg-primary)",
          boxSizing: "border-box",
          transition: "border 0.12s",
        }}
      />
      {label}
      {sub && (
        <span style={{ fontSize: 11, color: "var(--text-tertiary)" }}>{sub}</span>
      )}
    </button>
  );
}
