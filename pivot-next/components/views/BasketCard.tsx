"use client";

/**
 * BasketCard — one curated basket, as a real holdings table.
 *
 * Replaces the old strategy summary card for baskets. The centre of the card is
 * a screener-language table (uppercase micro-header, hairline rows, right-
 * aligned tabular numerals, live price per name) because a basket IS a table of
 * positions — its shape should read like the rest of the product's tables
 * rather than like prose.
 *
 * Quantities are the truth. The reader buys as many of each name as they like;
 * editing one never moves another's quantity, and the weight column is a
 * readout of what each holding costs as a share of the basket. The basket's
 * cost is therefore an output, shown in the table's own footer.
 *
 * DESIGN LAW: hairlines not boxes, tabular numerals, colour reserved for real
 * P&L. Every price is a live quote and every return is the pack's own figure —
 * where a price is missing the row says so rather than guessing.
 */

import * as React from "react";
import { RotateCcw, X, Plus } from "lucide-react";
import type { ExpressionDetail } from "@/lib/types";
import { CompanyLogo } from "@/components/CompanyLogo";
import { useCompanyLogos } from "@/hooks/useCompanyLogos";
import { useLiveQuote } from "@/hooks/useLiveQuote";
import { exprName } from "@/components/views/ExpressionHero";
import {
  editedTotalPct,
  isEdited,
  recommendedLegs,
  removeLeg,
  resolveBasket,
  restoreLeg,
  setLegShares,
  type PriceMap,
  type ResolvedLeg,
  type BasketEdit,
} from "@/components/views/basket";

const FONT = "var(--font-display)";

function inr(v: number): string {
  const r = Math.round(v);
  const sign = r < 0 ? "−" : "";
  return `${sign}₹${Math.abs(r).toLocaleString("en-IN")}`;
}

/** Price with paise — a quote is a quote, don't round it into a different number. */
function inrPrice(v: number): string {
  return `₹${v.toLocaleString("en-IN", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })}`;
}

function riskLevel(tier: ExpressionDetail["tier"]): { label: string; color: string } {
  switch (tier) {
    case "conservative":
      return { label: "Low", color: "var(--color-profit)" };
    case "balanced":
      return { label: "Medium", color: "var(--color-warning, #b45309)" };
    default:
      return { label: "High", color: "var(--color-loss)" };
  }
}

// Screener table language, tightened for a card-width table.
const TH: React.CSSProperties = {
  padding: "9px 6px",
  fontSize: 10,
  letterSpacing: "0.09em",
  textTransform: "uppercase",
  fontWeight: 550,
  color: "var(--text-tertiary)",
  borderBottom: "1.5px solid var(--glass-border)",
  whiteSpace: "nowrap",
  fontFamily: FONT,
};

const TD: React.CSSProperties = {
  padding: "9px 6px",
  fontSize: 12.5,
  borderBottom: "1px solid var(--glass-border)",
  whiteSpace: "nowrap",
  fontFamily: FONT,
  color: "var(--text-primary)",
};

const NUM: React.CSSProperties = {
  ...TD,
  textAlign: "right",
  fontVariantNumeric: "tabular-nums",
};

export function BasketCard({
  expression,
  amount,
  edit,
  onEdit,
  prices,
  onPrice,
}: {
  expression: ExpressionDetail;
  /** The ticket ₹ amount. Seeds opening quantities; never rescales an edited one. */
  amount: number;
  edit?: BasketEdit;
  onEdit: (next: BasketEdit) => void;
  prices?: PriceMap;
  onPrice: (key: string, price: number) => void;
}): React.ReactElement {
  const e = expression;
  const ticket = Number.isFinite(amount) ? amount : 100_000;
  const base = recommendedLegs(e);
  const { legs, totalCost, fullyPriced } = resolveBasket(e, edit, prices, ticket);
  const dropped = base.filter((b) => !legs.some((l) => l.key === b.key));
  const edited = isEdited(edit);
  const risk = riskLevel(e.tier);

  // Keyed on the joined symbols, not the array: `base` is rebuilt every render,
  // so depending on its identity would hand the hook a new array each time.
  const symbolKey = base
    .map((l) => l.symbol)
    .filter((s): s is string => s != null)
    .join(",");
  const logoSymbols = React.useMemo(
    () => (symbolKey ? symbolKey.split(",") : []),
    [symbolKey],
  );
  const logos = useCompanyLogos(logoSymbols);

  const [draft, setDraft] = React.useState<{ key: string; value: string } | null>(null);

  const commit = (key: string, raw: string): void => {
    const n = Number(raw);
    if (raw.trim() !== "" && Number.isFinite(n)) onEdit(setLegShares(edit, key, n));
    setDraft(null);
  };

  // Outcome scales to what the basket ACTUALLY costs — quantities are the
  // truth, and the ticket amount above only seeded them.
  const basis = totalCost != null && totalCost > 0 ? totalCost : ticket;
  const medianPct = editedTotalPct(e, edit, prices, ticket);
  const worstPct = e.monte_carlo?.p05 ?? e.worst_drop_pct ?? null;
  const bestPct = e.monte_carlo?.p95 ?? null;

  return (
    <div
      className="vwd-card"
      style={{
        height: "100%",
        border: "1px solid var(--glass-border)",
        borderRadius: "var(--radius-lg)",
        background: "var(--bg-base)",
      }}
    >
      <style>{`
        .vwd-brow .vwd-brow-x { opacity: 0; transition: opacity 140ms var(--ease-quartr); }
        .vwd-brow:hover .vwd-brow-x,
        .vwd-brow:focus-within .vwd-brow-x { opacity: 1; }
        @media (hover: none) { .vwd-brow .vwd-brow-x { opacity: 1; } }
        .vwd-brow { transition: background-color 150ms var(--ease-quartr); }
        .vwd-brow:hover { background: var(--bg-secondary); }
        .vwd-qty:focus-visible { outline: none; box-shadow: 0 0 0 3px color-mix(in srgb, var(--pivot-blue) 20%, transparent); }
        .vwd-basket-reset:hover { color: var(--text-secondary) !important; }
        .vwd-leg-back:hover { color: var(--text-secondary) !important; }
      `}</style>

      {/* ── header: name · risk · what it's for ── */}
      <div className="vwd-card-top">
        <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
          <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", gap: 12 }}>
            <h3
              style={{
                margin: 0,
                fontFamily: FONT,
                fontSize: 18,
                fontWeight: 600,
                letterSpacing: "-0.02em",
                lineHeight: 1.25,
                color: "var(--text-primary)",
              }}
            >
              {exprName(e)}
            </h3>
            <span
              style={{
                display: "inline-flex",
                alignItems: "center",
                gap: 6,
                flexShrink: 0,
                marginTop: 2,
                padding: "3px 9px",
                borderRadius: 999,
                border: "1px solid var(--glass-border)",
                fontFamily: FONT,
                fontSize: 11.5,
                fontWeight: 600,
                color: "var(--text-secondary)",
                whiteSpace: "nowrap",
              }}
            >
              <span
                aria-hidden
                style={{ width: 6, height: 6, borderRadius: 999, background: risk.color }}
              />
              {risk.label} risk
            </span>
          </div>

          {(e.plain_why ?? e.plain_one_liner) && (
            <span
              style={{
                fontFamily: FONT,
                fontSize: 13.5,
                lineHeight: 1.5,
                color: "var(--text-secondary)",
              }}
            >
              {e.plain_why ?? e.plain_one_liner}
            </span>
          )}
        </div>
      </div>

      <div className="vwd-card-rest">
        {/* ── the basket, as a table ── */}
        <div style={{ display: "flex", flexDirection: "column", gap: 8, minWidth: 0 }}>
          <div style={{ display: "flex", alignItems: "baseline", justifyContent: "space-between", gap: 10 }}>
            <span
              style={{
                fontFamily: FONT,
                fontSize: 10,
                fontWeight: 550,
                letterSpacing: "0.09em",
                textTransform: "uppercase",
                color: "var(--text-tertiary)",
              }}
            >
              {legs.length} holdings
            </span>
            {edited && (
              <button
                type="button"
                className="vwd-basket-reset"
                onClick={() => onEdit({ removed: [], shares: {} })}
                style={{
                  display: "inline-flex",
                  alignItems: "center",
                  gap: 5,
                  fontFamily: FONT,
                  fontSize: 12,
                  fontWeight: 500,
                  color: "var(--text-tertiary)",
                  background: "transparent",
                  border: "none",
                  padding: 0,
                  cursor: "pointer",
                  transition: "color 160ms var(--ease-quartr)",
                }}
              >
                <RotateCcw size={11} strokeWidth={2} aria-hidden />
                Reset
              </button>
            )}
          </div>

          <div style={{ overflowX: "auto" }} className="quartr-no-scrollbar">
            <table
              style={{
                width: "100%",
                borderCollapse: "collapse",
                fontFamily: FONT,
                minWidth: 420,
              }}
            >
              <thead>
                <tr>
                  <th style={{ ...TH, textAlign: "left" }}>Company</th>
                  <th style={{ ...TH, textAlign: "right" }}>Price</th>
                  <th style={{ ...TH, textAlign: "right" }}>Qty</th>
                  <th style={{ ...TH, textAlign: "right" }}>Value</th>
                  <th style={{ ...TH, textAlign: "right" }}>Wt</th>
                  <th style={{ ...TH, width: 24 }} aria-label="Remove" />
                </tr>
              </thead>
              <tbody>
                {legs.map((l) => (
                  <HoldingRow
                    key={l.key}
                    leg={l}
                    logoUrl={l.symbol ? logos[l.symbol.toUpperCase()] : null}
                    canRemove={legs.length > 1}
                    draftValue={draft?.key === l.key ? draft.value : null}
                    onDraft={(v) => setDraft({ key: l.key, value: v })}
                    onCancelDraft={() => setDraft(null)}
                    onCommit={(raw) => commit(l.key, raw)}
                    onRemove={() => onEdit(removeLeg(edit, l.key))}
                    onPrice={onPrice}
                  />
                ))}
              </tbody>
              <tfoot>
                <tr>
                  <td
                    colSpan={3}
                    style={{
                      ...TD,
                      borderBottom: "none",
                      paddingTop: 11,
                      color: "var(--text-tertiary)",
                      fontSize: 11.5,
                    }}
                  >
                    Basket cost{!fullyPriced && totalCost != null ? " (so far)" : ""}
                  </td>
                  <td
                    style={{
                      ...NUM,
                      borderBottom: "none",
                      paddingTop: 11,
                      fontWeight: 600,
                    }}
                  >
                    {totalCost != null ? inr(totalCost) : "—"}
                  </td>
                  <td style={{ ...NUM, borderBottom: "none", paddingTop: 11, fontWeight: 600 }}>
                    100%
                  </td>
                  <td style={{ ...TD, borderBottom: "none" }} />
                </tr>
              </tfoot>
            </table>
          </div>

          {/* dropped names stay one click from coming back */}
          {dropped.length > 0 && (
            <div style={{ display: "flex", flexWrap: "wrap", alignItems: "center", gap: "6px 10px" }}>
              <span style={{ fontFamily: FONT, fontSize: 11.5, color: "var(--text-tertiary)" }}>
                Removed:
              </span>
              {dropped.map((d) => (
                <button
                  key={d.key}
                  type="button"
                  className="vwd-leg-back"
                  onClick={() => onEdit(restoreLeg(edit, d.key))}
                  style={{
                    display: "inline-flex",
                    alignItems: "center",
                    gap: 3,
                    fontFamily: FONT,
                    fontSize: 11.5,
                    fontWeight: 500,
                    color: "var(--text-tertiary)",
                    background: "transparent",
                    border: "none",
                    padding: 0,
                    cursor: "pointer",
                    textDecoration: "underline",
                    textUnderlineOffset: 2,
                    transition: "color 160ms var(--ease-quartr)",
                  }}
                >
                  <Plus size={10} strokeWidth={2.5} aria-hidden />
                  {d.name}
                </button>
              ))}
            </div>
          )}
        </div>

        {/* ── what it did: one hero outcome, then the honest range ── */}
        {medianPct != null && (
          <div
            style={{
              display: "flex",
              flexDirection: "column",
              gap: 14,
              paddingTop: 16,
              borderTop: "1px solid var(--glass-border)",
            }}
          >
            <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
              <span style={{ fontFamily: FONT, fontSize: 12.5, color: "var(--text-tertiary)" }}>
                Typical outcome on {inr(basis)}
              </span>
              <div style={{ display: "flex", alignItems: "baseline", gap: 9, flexWrap: "wrap" }}>
                <span
                  style={{
                    fontFamily: "var(--font-serif)",
                    fontVariantNumeric: "tabular-nums",
                    fontSize: 34,
                    fontWeight: 600,
                    letterSpacing: "-0.02em",
                    lineHeight: 1,
                    color: "var(--text-primary)",
                  }}
                >
                  {medianPct >= 0 ? "+" : "−"}
                  {inr(Math.abs((basis * medianPct) / 100)).replace("−", "")}
                </span>
                <span
                  style={{
                    fontFamily: FONT,
                    fontVariantNumeric: "tabular-nums",
                    fontSize: 13,
                    fontWeight: 600,
                    color: medianPct >= 0 ? "var(--color-profit)" : "var(--color-loss)",
                  }}
                >
                  {medianPct >= 0 ? "+" : ""}
                  {medianPct.toFixed(1)}%
                </span>
              </div>
            </div>

            <div style={{ display: "flex", flexWrap: "wrap", gap: "12px 28px" }}>
              {worstPct != null && (
                <Stat label="Worst seen" value={inr((basis * worstPct) / 100)} tone="loss" />
              )}
              {bestPct != null && (
                <Stat label="Best seen" value={inr((basis * bestPct) / 100)} tone="profit" />
              )}
            </div>

            {edited && (
              <span
                style={{
                  fontFamily: FONT,
                  fontSize: 11.5,
                  lineHeight: 1.5,
                  color: "var(--text-tertiary)",
                }}
              >
                Worst and best are the recommended basket&apos;s own history. Your version
                isn&apos;t backtested — the figure above is your weights applied to each
                name&apos;s real return.
              </span>
            )}
          </div>
        )}

        {/* ── footer: the disclaimer ── */}
        <div
          style={{
            marginTop: "auto",
            paddingTop: 16,
            borderTop: "1px solid var(--glass-border)",
          }}
        >
          <span style={{ fontFamily: FONT, fontSize: 12, lineHeight: 1.5, color: "var(--text-tertiary)" }}>
            You review and place every order yourself. This is analysis, not financial advice.
          </span>
        </div>
      </div>
    </div>
  );
}

function Stat({
  label,
  value,
  tone,
}: {
  label: string;
  value: string;
  tone: "profit" | "loss" | "neutral";
}): React.ReactElement {
  const color =
    tone === "profit"
      ? "var(--color-profit)"
      : tone === "loss"
        ? "var(--color-loss)"
        : "var(--text-primary)";
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 3 }}>
      <span style={{ fontFamily: FONT, fontSize: 11.5, color: "var(--text-tertiary)" }}>{label}</span>
      <span
        style={{
          fontFamily: FONT,
          fontVariantNumeric: "tabular-nums",
          fontSize: 14.5,
          fontWeight: 600,
          letterSpacing: "-0.01em",
          color,
        }}
      >
        {value}
      </span>
    </div>
  );
}

/**
 * One holding row. A row component, not a loop body, because each name needs
 * its own live quote and hooks can't be called per-iteration — the underlying
 * quote socket is shared and subscribe-counted. Each row reports its price
 * upward so the card can price the basket as a whole.
 */
function HoldingRow({
  leg,
  logoUrl,
  canRemove,
  draftValue,
  onDraft,
  onCancelDraft,
  onCommit,
  onRemove,
  onPrice,
}: {
  leg: ResolvedLeg;
  logoUrl: string | null | undefined;
  canRemove: boolean;
  draftValue: string | null;
  onDraft: (v: string) => void;
  onCancelDraft: () => void;
  onCommit: (raw: string) => void;
  onRemove: () => void;
  onPrice: (key: string, price: number) => void;
}): React.ReactElement {
  const { ltp } = useLiveQuote(leg.symbol);

  // Report the price up so the card can compute cost shares. The parent ignores
  // a repeat of the same value, so this settles after one pass.
  React.useEffect(() => {
    if (ltp != null && ltp > 0) onPrice(leg.key, ltp);
  }, [ltp, leg.key, onPrice]);

  const shown = draftValue ?? (leg.shares != null ? String(leg.shares) : "");

  return (
    <tr className="vwd-brow">
      <td style={{ ...TD, textAlign: "left", width: "100%" }}>
        <div style={{ display: "flex", alignItems: "center", gap: 8, minWidth: 0 }}>
          <CompanyLogo
            logoUrl={logoUrl}
            name={leg.name}
            symbol={leg.symbol ?? leg.name}
            size={24}
          />
          <span
            style={{
              fontWeight: 500,
              overflow: "hidden",
              textOverflow: "ellipsis",
              whiteSpace: "nowrap",
            }}
          >
            {leg.name}
          </span>
        </div>
      </td>

      <td style={NUM}>
        {leg.price != null ? (
          inrPrice(leg.price)
        ) : (
          <span style={{ color: "var(--text-tertiary)" }}>—</span>
        )}
      </td>

      <td style={{ ...NUM, padding: "6px 6px" }}>
        <input
          className="vwd-qty"
          aria-label={`${leg.name} shares`}
          inputMode="numeric"
          disabled={leg.price == null}
          value={shown}
          onChange={(ev) => onDraft(ev.target.value.replace(/[^0-9]/g, ""))}
          onBlur={(ev) => onCommit(ev.target.value)}
          onKeyDown={(ev) => {
            if (ev.key === "Enter") (ev.target as HTMLInputElement).blur();
            if (ev.key === "Escape") onCancelDraft();
          }}
          style={{
            fontFamily: FONT,
            fontVariantNumeric: "tabular-nums",
            fontSize: 12.5,
            fontWeight: 600,
            color: "var(--text-primary)",
            background: "transparent",
            border: "1px solid var(--glass-border)",
            borderRadius: "var(--radius-sm, 6px)",
            outline: "none",
            textAlign: "right",
            width: "5ch",
            padding: "4px 6px",
            opacity: leg.price == null ? 0.45 : 1,
          }}
        />
      </td>

      <td style={NUM}>
        {leg.cost != null ? (
          inr(leg.cost)
        ) : (
          <span style={{ color: "var(--text-tertiary)" }}>No price</span>
        )}
      </td>

      <td style={{ ...NUM, color: "var(--text-secondary)", fontWeight: 600 }}>{leg.livePct}%</td>

      <td style={{ ...TD, padding: "9px 0 9px 2px" }}>
        <button
          type="button"
          className="vwd-brow-x"
          aria-label={`Remove ${leg.name} from the basket`}
          disabled={!canRemove}
          onClick={onRemove}
          style={{
            display: "inline-flex",
            padding: 2,
            color: "var(--text-tertiary)",
            background: "transparent",
            border: "none",
            borderRadius: 4,
            cursor: canRemove ? "pointer" : "default",
          }}
        >
          <X size={13} strokeWidth={2} aria-hidden />
        </button>
      </td>
    </tr>
  );
}

export default BasketCard;
