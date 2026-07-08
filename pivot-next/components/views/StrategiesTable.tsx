"use client";

/**
 * StrategiesTable — the real, roomy strategies TABLE on a View detail page
 * (replaces the old stacked expression cards).
 *
 * Columns: Name | Risk | Avg gain | Avg loss | Max gain | Max loss | (Details)
 * The four numeric columns are COMPARABLE — all from the same per-occurrence
 * return distribution: avg gain/loss are the means of the positive and
 * negative occurrences; max gain/loss the single best and worst. Benchmark-
 * relative numbers (vs Nifty / excess return) are never rendered here — only
 * the strategy's own outcomes. Strategy type + entry ticket live under the
 * name.
 * Rows: the view's expressions. Numeric columns are right-aligned + tabular.
 * Each row has a "Details" button that expands an in-table panel showing
 * the strategy's plain_why, plain_risk, what-you'd-hold (basket members or the
 * honest option-legs note), capital intensity, and a Deploy CTA.
 *
 * Option / derivative expressions have no offline historical backtest (there
 * is no offline option chain) — their max gain/loss come from the payoff
 * MODEL (asterisked, explained in the footer) and their averages honestly
 * stay "—" (see isNotBacktested()).
 *
 * DESIGN LAW (v2): ROUNDED (outer card var(--radius-lg); chips/buttons
 * var(--radius-md)), BORDER-ONLY (no grey fills), plain language (no jargon),
 * >= 13px text, aligned/symmetrical, comfortable padding.
 */

import * as React from "react";
import { ChevronDown, Loader2, CheckCircle2 } from "lucide-react";
import type { ExpressionDetail, EntryBlock } from "@/lib/types";
import type { ViewPlaceResponse } from "@/lib/api";
import { Num } from "@/components/views/Stat";
import {
  tierLabel,
  fmtPct,
  signColor,
  capitalLabel,
  isPlaceableBasket,
} from "@/components/views/view-format";

/** Format INR with Indian grouping (no paise). */
function fmtInr(n: number): string {
  return "₹" + Math.round(n).toLocaleString("en-IN");
}

/** One-line entry ticket text for a row. */
function entryTicket(entry: EntryBlock): string | null {
  if (
    entry.basis === "lite_basket" ||
    entry.basis === "etf_core_plus_names" ||
    entry.basis === "etf_substitute"
  ) {
    if (entry.min_entry_inr != null) return `From ${fmtInr(entry.min_entry_inr)}`;
  }
  if (entry.basis === "option_premium" || entry.basis === "priced_at_deploy") {
    const own =
      entry.min_entry_inr != null
        ? `≈${fmtInr(entry.min_entry_inr)}/lot`
        : "Priced at deploy";
    // The budget-sized far-OTM single is a DIFFERENT structure — say "longshot",
    // never present it as the same trade at a lower price.
    const small = entry.small_ticket
      ? ` · longshot from ${fmtInr(entry.small_ticket.est_premium_per_lot_inr)}`
      : "";
    return own + small;
  }
  if (entry.basis === "margin_required") return "Needs margin";
  return null;
}

const FONT = "var(--font-display)";

// Grid template shared by the header row and every data row so columns align.
const GRID =
  "minmax(170px, 1.6fr) 88px 84px 84px 84px 84px 96px";

function HeaderCell({
  children,
  align = "left",
}: {
  children: React.ReactNode;
  align?: "left" | "right";
}): React.ReactElement {
  return (
    <span
      style={{
        fontFamily: FONT,
        fontSize: 13,
        fontWeight: 500,
        color: "var(--text-tertiary)",
        lineHeight: 1.3,
        textAlign: align,
        whiteSpace: "nowrap",
      }}
    >
      {children}
    </span>
  );
}

// An expression is an options/derivative structure with no offline historical
// backtest when ANY of these hold — such rows must never show a fabricated
// max-drop number or a trust word derived from a backtest that doesn't exist.
function isNotBacktested(expr: ExpressionDetail): boolean {
  return (
    expr.curve_basis === "underlying" ||
    expr.expression_kind === "option_strategy" ||
    (expr.option_legs != null && expr.option_legs.length > 0) ||
    (expr.strategy_type ?? "").toLowerCase().includes("option")
  );
}

// One right-aligned cell of the four comparable gain/loss columns. Modelled
// values (option payoff bounds, no history) carry an asterisk explained in
// the footer; an uncapped modelled max gain says so instead of a fake cap.
function GainLossCell({
  value,
  modelled,
  uncapped,
}: {
  value: number | null | undefined;
  modelled: boolean;
  uncapped?: boolean;
}): React.ReactElement {
  return (
    <span style={{ textAlign: "right" }}>
      {uncapped ? (
        <span
          style={{
            fontFamily: FONT,
            fontSize: 13,
            fontWeight: 500,
            color: "var(--color-profit)",
            whiteSpace: "nowrap",
          }}
        >
          Open-ended*
        </span>
      ) : value == null ? (
        <span
          style={{ fontFamily: FONT, fontSize: 13, color: "var(--text-tertiary)" }}
        >
          —
        </span>
      ) : (
        <Num size="md" weight={600} color={signColor(value)}>
          {fmtPct(value)}
          {modelled ? "*" : ""}
        </Num>
      )}
    </span>
  );
}

// A plain word "what you'd hold" line for the expanded panel.
function holdSentence(expr: ExpressionDetail): string {
  if (expr.option_legs && expr.option_legs.length > 0) {
    // Long straddle: both legs are BUY (CE + PE) — never assume one BUY + one SELL.
    if (expr.option_model?.structure === "long_straddle") {
      return "Both directions (straddle) — you profit from a large move either way. Exact strikes and premium are set at deploy.";
    }
    return (
      expr.option_legs_note ??
      "An options structure — the exact strikes are set when you deploy."
    );
  }
  const members = expr.members ?? [];
  if (members.length > 0) {
    return `Equal-weighted: ${members.join(", ")}.`;
  }
  return "—";
}

function DeployButton({
  onClick,
  busy,
  disabled,
  placeable,
  placed,
}: {
  onClick: () => void;
  busy: boolean;
  disabled: boolean;
  /** True when Deploy places a real share/ETF basket (vs arming a draft). */
  placeable: boolean;
  /** Set once the basket has been placed — swaps the CTA to a confirmation. */
  placed?: ViewPlaceResponse | null;
}): React.ReactElement {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled || busy || !!placed}
      style={{
        display: "inline-flex",
        alignItems: "center",
        justifyContent: "center",
        gap: 8,
        fontFamily: FONT,
        fontSize: 14,
        fontWeight: 600,
        color: "hsl(var(--primary-foreground))",
        background: "hsl(var(--primary))",
        border: "1px solid hsl(var(--primary))",
        borderRadius: "var(--radius-md)",
        padding: "9px 18px",
        cursor: disabled || busy || placed ? "default" : "pointer",
        opacity: disabled ? 0.55 : 1,
        alignSelf: "flex-start",
        transition: "opacity 180ms var(--ease-quartr)",
      }}
    >
      {placed ? (
        <>
          <CheckCircle2 size={14} aria-hidden />
          {placed.routed_to === "paper" ? "Filled (paper)" : "Order placed"}
        </>
      ) : busy ? (
        <>
          <Loader2 size={14} className="animate-spin" aria-hidden />
          {placeable ? "Placing…" : "Arming…"}
        </>
      ) : (
        "Deploy this strategy"
      )}
    </button>
  );
}

interface StrategiesTableProps {
  expressions: ExpressionDetail[];
  /** Currently-selected (chart-driving) expression id — highlighted. */
  selectedId?: string | null;
  onSelect?: (id: string) => void;
  onDeploy: (expr: ExpressionDetail) => void;
  deployingId?: string | null;
  deployError?: string | null;
  /** Broker/paper placements keyed by expression id (from ViewDetailPage). */
  placedById?: Record<string, ViewPlaceResponse>;
  /** Open the full statistical analysis page for a strategy. */
  onOpenDeepDive?: (expr: ExpressionDetail) => void;
}

export function StrategiesTable({
  expressions,
  selectedId,
  onSelect,
  onDeploy,
  deployingId,
  deployError,
  placedById,
  onOpenDeepDive,
}: StrategiesTableProps): React.ReactElement | null {
  const [openId, setOpenId] = React.useState<string | null>(null);

  const rows = Array.isArray(expressions) ? expressions : [];
  if (rows.length === 0) return null;

  // Hold / exit window for these strategies (view-level — every row shares it).
  // Surfaced as an always-visible caption so the holding period is clear without
  // expanding a row (the two numeric columns are max drop / avg profit).
  const holdWindow = rows
    .map((r) => r.exit_period)
    .find((p): p is string => typeof p === "string" && p.trim().length > 0);

  return (
    <div
      style={{
        border: "1px solid var(--glass-border)",
        borderRadius: "var(--radius-lg)",
        background: "var(--bg-base)",
        overflow: "hidden",
      }}
    >
      {/* Header */}
      <div
        style={{
          display: "grid",
          gridTemplateColumns: GRID,
          gap: 12,
          alignItems: "center",
          padding: "14px 18px",
          borderBottom: "1px solid var(--glass-border)",
        }}
      >
        <HeaderCell>Strategy</HeaderCell>
        <HeaderCell>Risk</HeaderCell>
        <HeaderCell align="right">Avg gain</HeaderCell>
        <HeaderCell align="right">Avg loss</HeaderCell>
        <HeaderCell align="right">Max gain</HeaderCell>
        <HeaderCell align="right">Max loss</HeaderCell>
        <HeaderCell align="right">{""}</HeaderCell>
      </div>

      {rows.map((expr, i) => {
        const open = openId === expr.id;
        const selected = selectedId === expr.id;
        const name = expr.strategy_name ?? expr.plain_label ?? tierLabel(expr.tier);
        const type = expr.strategy_type ?? "—";
        const notBacktested = isNotBacktested(expr);
        return (
          <div
            key={expr.id}
            style={{
              borderTop: i === 0 ? "none" : "1px solid var(--glass-border)",
              background: selected
                ? "color-mix(in srgb, var(--pivot-blue) 5%, transparent)"
                : "transparent",
            }}
          >
            {/* Data row — the whole row selects (drives the chart); the
                button toggles the detail panel. */}
            <div
              role={onSelect ? "button" : undefined}
              tabIndex={onSelect ? 0 : undefined}
              onClick={onSelect ? () => onSelect(expr.id) : undefined}
              onKeyDown={
                onSelect
                  ? (e) => {
                      if (e.key === "Enter" || e.key === " ") {
                        e.preventDefault();
                        onSelect(expr.id);
                      }
                    }
                  : undefined
              }
              style={{
                display: "grid",
                gridTemplateColumns: GRID,
                gap: 12,
                alignItems: "center",
                padding: "16px 18px",
                cursor: onSelect ? "pointer" : "default",
              }}
            >
              <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
                <span
                  style={{
                    fontFamily: FONT,
                    fontSize: 14,
                    fontWeight: 600,
                    color: "var(--text-primary)",
                    lineHeight: 1.35,
                  }}
                >
                  {name}
                </span>
                {(() => {
                  const ticket = expr.entry ? entryTicket(expr.entry) : null;
                  const sub = [type !== "—" ? type : null, ticket]
                    .filter(Boolean)
                    .join(" · ");
                  return sub ? (
                    <span
                      style={{
                        fontFamily: FONT,
                        fontSize: 12,
                        fontWeight: 400,
                        color: "var(--text-tertiary)",
                        fontVariantNumeric: "tabular-nums",
                      }}
                    >
                      {sub}
                    </span>
                  ) : null;
                })()}
              </div>
              <span
                style={{
                  fontFamily: FONT,
                  fontSize: 13,
                  fontWeight: 500,
                  color: "var(--text-secondary)",
                }}
              >
                {tierLabel(expr.tier)}
              </span>
              {(() => {
                const gl = expr.gain_loss ?? null;
                const modelled = gl?.basis === "modelled";
                return (
                  <>
                    <GainLossCell value={gl?.avg_gain_pct} modelled={modelled} />
                    <GainLossCell value={gl?.avg_loss_pct} modelled={modelled} />
                    <GainLossCell
                      value={gl?.max_gain_pct}
                      modelled={modelled}
                      uncapped={gl?.max_gain_uncapped}
                    />
                    <GainLossCell value={gl?.max_loss_pct} modelled={modelled} />
                  </>
                );
              })()}
              <span
                style={{ display: "flex", justifyContent: "flex-end" }}
              >
                <button
                  type="button"
                  onClick={(e) => {
                    e.stopPropagation();
                    setOpenId(open ? null : expr.id);
                  }}
                  aria-expanded={open}
                  style={{
                    display: "inline-flex",
                    alignItems: "center",
                    gap: 5,
                    fontFamily: FONT,
                    fontSize: 13,
                    fontWeight: 500,
                    color: "var(--text-secondary)",
                    background: "var(--bg-base)",
                    border: "1px solid var(--glass-border)",
                    borderRadius: "var(--radius-md)",
                    padding: "7px 11px",
                    cursor: "pointer",
                    whiteSpace: "nowrap",
                  }}
                >
                  Details
                  <ChevronDown
                    size={13}
                    aria-hidden
                    style={{
                      transform: open ? "rotate(180deg)" : "none",
                      transition: "transform 180ms var(--ease-quartr)",
                    }}
                  />
                </button>
              </span>
            </div>

            {/* Expanded detail panel */}
            {open && (
              <div
                style={{
                  padding: "4px 18px 20px",
                  display: "flex",
                  flexDirection: "column",
                  gap: 14,
                }}
              >
                {expr.plain_why && (
                  <DetailLine label="Why it works" value={expr.plain_why} />
                )}
                {expr.plain_risk && (
                  <DetailLine label="Main risk" value={expr.plain_risk} />
                )}
                <DetailLine
                  label="What you'd hold"
                  value={holdSentence(expr)}
                />
                <DetailLine
                  label="Hold / exit"
                  value={
                    expr.exit_period && expr.exit_period.trim().length > 0
                      ? expr.exit_period
                      : "Not specified for this strategy."
                  }
                />
                {notBacktested && (
                  <DetailLine
                    label="About this number"
                    value="This is an options structure with no historical backtest — its payoff (max loss, breakevens, probability) is priced from the live option chain when you deploy."
                  />
                )}
                <div
                  style={{
                    display: "flex",
                    flexWrap: "wrap",
                    gap: "8px 24px",
                  }}
                >
                  <MetaChip
                    label="Capital needed"
                    value={capitalLabel(expr.capital_label)}
                  />
                  <MetaChip
                    label="Track record"
                    value={
                      notBacktested
                        ? "Priced at deploy"
                        : expr.trust_badge ?? "Not enough data"
                    }
                  />
                </div>
                {onOpenDeepDive && (
                  <button
                    onClick={() => onOpenDeepDive(expr)}
                    style={{
                      alignSelf: "flex-start",
                      fontFamily: FONT,
                      fontSize: 13,
                      fontWeight: 600,
                      color: "var(--pivot-blue)",
                      background: "transparent",
                      border: "1px solid color-mix(in srgb, var(--pivot-blue) 40%, transparent)",
                      borderRadius: "var(--radius-md)",
                      padding: "7px 12px",
                      cursor: "pointer",
                    }}
                  >
                    See the full analysis →
                  </button>
                )}
                <DeployButton
                  onClick={() => onDeploy(expr)}
                  busy={deployingId === expr.id}
                  placeable={isPlaceableBasket(expr.entry)}
                  placed={placedById?.[expr.id] ?? null}
                  disabled={
                    !isPlaceableBasket(expr.entry) &&
                    !expr.is_deployable &&
                    !expr.workflow_id
                  }
                />
                {(() => {
                  const p = placedById?.[expr.id];
                  if (!p) return null;
                  const plural = p.count === 1 ? "" : "s";
                  return (
                    <span
                      style={{
                        fontFamily: FONT,
                        fontSize: 13,
                        color: "var(--text-secondary)",
                        lineHeight: 1.5,
                      }}
                    >
                      {p.routed_to === "paper"
                        ? `Filled ${p.count} order${plural} in your paper book.`
                        : `Placed ${p.count} order${plural} through your broker.`}
                    </span>
                  );
                })()}
                {deployError && deployingId === expr.id && (
                  <span
                    role="alert"
                    style={{
                      fontFamily: FONT,
                      fontSize: 13,
                      color: "var(--color-loss)",
                    }}
                  >
                    {deployError}
                  </span>
                )}
                <span
                  style={{
                    fontFamily: FONT,
                    fontSize: 13,
                    color: "var(--text-tertiary)",
                    lineHeight: 1.5,
                  }}
                >
                  Pivot arms the trigger and prepares the orders — you review
                  and place every order yourself. This is analysis, not
                  financial advice.
                </span>
              </div>
            )}
          </div>
        );
      })}

      {/* Always-visible footer: clarifies that the profit columns are the
          AVERAGE per occurrence (not compounded across occurrences), plus the
          hold/exit window — the time signal the numeric columns don't carry. */}
      <div
        style={{
          display: "flex",
          flexDirection: "column",
          gap: 4,
          padding: "13px 18px",
          borderTop: "1px solid var(--glass-border)",
        }}
      >
        <span
          style={{
            fontFamily: FONT,
            fontSize: 13,
            fontWeight: 400,
            color: "var(--text-tertiary)",
            lineHeight: 1.4,
          }}
        >
          Avg gain / avg loss are the average winning and losing outcomes
          across past occurrences; max gain / max loss are the single best and
          worst. Nothing is added up across occurrences. * marks modelled
          option payoff bounds — there is no history for an option structure,
          and the final pricing is set at deploy.
        </span>
        {holdWindow && (
          <span
            style={{
              display: "flex",
              alignItems: "baseline",
              flexWrap: "wrap",
              gap: "2px 8px",
            }}
          >
            <span
              style={{
                fontFamily: FONT,
                fontSize: 13,
                fontWeight: 600,
                color: "var(--text-secondary)",
              }}
            >
              Typical hold:
            </span>
            <span
              style={{
                fontFamily: FONT,
                fontSize: 13,
                fontWeight: 400,
                color: "var(--text-secondary)",
                lineHeight: 1.4,
              }}
            >
              {holdWindow}
            </span>
          </span>
        )}
      </div>
    </div>
  );
}

function DetailLine({
  label,
  value,
}: {
  label: string;
  value: React.ReactNode;
}): React.ReactElement {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
      <span
        style={{
          fontFamily: FONT,
          fontSize: 13,
          fontWeight: 500,
          color: "var(--text-tertiary)",
        }}
      >
        {label}
      </span>
      <span
        style={{
          fontFamily: FONT,
          fontSize: 14,
          fontWeight: 400,
          color: "var(--text-primary)",
          lineHeight: 1.55,
        }}
      >
        {value}
      </span>
    </div>
  );
}

function MetaChip({
  label,
  value,
}: {
  label: string;
  value: React.ReactNode;
}): React.ReactElement {
  return (
    <span style={{ display: "inline-flex", flexDirection: "column", gap: 2 }}>
      <span
        style={{
          fontFamily: FONT,
          fontSize: 13,
          fontWeight: 500,
          color: "var(--text-tertiary)",
        }}
      >
        {label}
      </span>
      <span
        style={{
          fontFamily: FONT,
          fontSize: 14,
          fontWeight: 600,
          color: "var(--text-primary)",
        }}
      >
        {value}
      </span>
    </span>
  );
}

export default StrategiesTable;
