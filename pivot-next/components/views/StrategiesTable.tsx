"use client";

/**
 * StrategiesTable — the real, roomy strategies TABLE on a View detail page
 * (replaces the old stacked expression cards).
 *
 * Columns: Name | Type | Risk | Max drop | Avg profit | (View details)
 * "Avg profit" is the AVERAGE over the event's past occurrences (mean per
 * occurrence) — never compounded across occurrences. Benchmark-relative
 * numbers (vs Nifty / excess return) are never rendered here — only the
 * strategy's own return.
 * Rows: the view's expressions. Numeric columns are right-aligned + tabular.
 * Each row has a "View details" button that expands an in-table panel showing
 * the strategy's plain_why, plain_risk, what-you'd-hold (basket members or the
 * honest option-legs note), capital intensity, and a Deploy CTA.
 *
 * Option / derivative expressions have no offline historical backtest (there
 * is no offline option chain) — those rows render "Priced at deploy" instead
 * of a fabricated max-drop number or a trust word (see isNotBacktested()).
 *
 * DESIGN LAW (v2): ROUNDED (outer card var(--radius-lg); chips/buttons
 * var(--radius-md)), BORDER-ONLY (no grey fills), plain language (no jargon),
 * >= 13px text, aligned/symmetrical, comfortable padding.
 */

import * as React from "react";
import { ChevronDown, Loader2 } from "lucide-react";
import type { ExpressionDetail, EntryBlock } from "@/lib/types";
import { Num } from "@/components/views/Stat";
import {
  tierLabel,
  fmtPct,
  signColor,
  capitalLabel,
} from "@/components/views/view-format";

/** Format INR with Indian grouping (no paise). */
function fmtInr(n: number): string {
  return "₹" + Math.round(n).toLocaleString("en-IN");
}

/** One-line entry ticket text for a row. */
function entryTicket(entry: EntryBlock): string | null {
  if (entry.basis === "lite_basket" || entry.basis === "etf_substitute") {
    if (entry.min_entry_inr != null) return `From ${fmtInr(entry.min_entry_inr)}`;
  }
  if (entry.basis === "option_premium") {
    if (entry.min_entry_inr != null) return `≈${fmtInr(entry.min_entry_inr)}/lot`;
  }
  if (entry.basis === "priced_at_deploy") return "Priced at deploy";
  if (entry.basis === "margin_required") return "Needs margin";
  return null;
}

const FONT = "var(--font-display)";

// Grid template shared by the header row and every data row so columns align.
const GRID =
  "minmax(150px, 1.5fr) minmax(110px, 1.1fr) 96px 92px 92px 116px";

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

// A small muted, border-only, rounded chip used in place of a fabricated
// number for tiers that have no historical backtest.
function PricedAtDeployChip(): React.ReactElement {
  return (
    <span
      style={{
        display: "inline-flex",
        alignItems: "center",
        fontFamily: FONT,
        fontSize: 13,
        fontWeight: 500,
        color: "var(--text-tertiary)",
        border: "1px solid var(--glass-border)",
        borderRadius: "var(--radius-md)",
        padding: "3px 9px",
        whiteSpace: "nowrap",
      }}
    >
      Priced at deploy
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
}: {
  onClick: () => void;
  busy: boolean;
  disabled: boolean;
}): React.ReactElement {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled || busy}
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
        cursor: disabled || busy ? "default" : "pointer",
        opacity: disabled ? 0.55 : 1,
        alignSelf: "flex-start",
        transition: "opacity 180ms var(--ease-quartr)",
      }}
    >
      {busy && <Loader2 size={14} className="animate-spin" aria-hidden />}
      {busy ? "Arming…" : "Deploy this strategy"}
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
        <HeaderCell>Type</HeaderCell>
        <HeaderCell>Risk</HeaderCell>
        <HeaderCell align="right">Max drop</HeaderCell>
        <HeaderCell align="right">Avg profit</HeaderCell>
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
                {expr.entry && (() => {
                  const ticket = entryTicket(expr.entry);
                  return ticket ? (
                    <span
                      style={{
                        fontFamily: FONT,
                        fontSize: 12,
                        fontWeight: 400,
                        color: "var(--text-tertiary)",
                        fontVariantNumeric: "tabular-nums",
                      }}
                    >
                      {ticket}
                    </span>
                  ) : null;
                })()}
              </div>
              <span
                style={{
                  fontFamily: FONT,
                  fontSize: 13,
                  fontWeight: 400,
                  color: "var(--text-secondary)",
                  lineHeight: 1.35,
                }}
              >
                {type}
              </span>
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
              <span style={{ textAlign: "right" }}>
                {notBacktested ? (
                  <PricedAtDeployChip />
                ) : (
                  <Num size="md" weight={600} color="var(--color-loss)">
                    {fmtPct(expr.worst_drop_pct)}
                  </Num>
                )}
              </span>
              <span style={{ textAlign: "right" }}>
                <Num
                  size="md"
                  weight={600}
                  color={signColor(expr.strategy_total_pct)}
                >
                  {fmtPct(expr.strategy_total_pct)}
                </Num>
              </span>
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
                  disabled={
                    !expr.is_deployable && !expr.workflow_id
                  }
                />
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
          Avg profit is the average each time this has happened before — not
          added up across occurrences.
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
