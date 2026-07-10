"use client";

/**
 * StrategyCleanCard — one strategy's detail, rendered as a calm, premium
 * summary card: the strategy name → a risk chip → what you'd own → a single
 * hero outcome number scaled to the ticket → a clean worst/best/min metric
 * row → the one risk → the link into the full statistical analysis.
 *
 * It is the SUMMARY surface; "See the full analysis" opens <StrategyDeepDive/>.
 *
 * DESIGN LAW: one hero number, generous whitespace, borderless aligned
 * metrics (no filled grey boxes), sentence-case micro-labels, tabular
 * numerals, colour reserved for real P&L (green/red) and the risk dot — never
 * a pastel card tint. Every figure is quoted from the expression's real fields
 * (or the block is gated out entirely). No fabricated numbers.
 */

import * as React from "react";
import { ChevronDown, AlertTriangle, Wallet } from "lucide-react";
import type { ExpressionDetail } from "@/lib/types";
import { exprName, exprMinAmount } from "@/components/views/ExpressionHero";

const FONT = "var(--font-display)";

// ── ₹ formatting (en-IN) ────────────────────────────────────────────────────
function inr(v: number): string {
  const r = Math.round(v);
  const sign = r < 0 ? "−" : "";
  return `${sign}₹${Math.abs(r).toLocaleString("en-IN")}`;
}

// The three tiers, expressed as a plain risk level + a matching text colour.
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

// "You'd own" — a plain sentence about the holdings, in the mockup's shape.
function ownSentence(e: ExpressionDetail): string {
  if (e.option_legs && e.option_legs.length > 0) {
    return (
      e.option_legs_note ??
      "An options structure — the exact strikes are set when you deploy. Close it any time."
    );
  }
  const m = e.members ?? [];
  if (m.length === 0) return "Not specified for this strategy.";
  if (m.length <= 3) return `${m.length} real companies — ${m.join(", ")}. Sell anytime.`;
  const shown = m.slice(0, 3).join(", ");
  return `${m.length} real companies — ${shown} and ${m.length - 3} more. Sell anytime.`;
}

// Median / best / worst outcome as fractions-of-amount %, honest sources only.
// Prefer the real Monte-Carlo spread; fall back to the backtested headline +
// worst single drop. Returns null when there is nothing real to show.
function outcomeSpread(
  e: ExpressionDetail,
): { median: number; best: number | null; worst: number | null } | null {
  const mc = e.monte_carlo;
  // Central figure is the MEAN (strategy_total_pct) — the average across all
  // occurrences, our headline measure (not the median). best/worst stay the
  // best/worst SEEN occurrence from the ≥2-occurrence distribution.
  const mean = e.strategy_total_pct;
  if (mc && (mc.terminal_pct?.length ?? 0) >= 2) {
    return { median: mean ?? mc.median, best: mc.p95, worst: mc.p05 };
  }
  if (mean != null) {
    return { median: mean, best: null, worst: e.worst_drop_pct };
  }
  return null;
}

export function StrategyCleanCard({
  expression,
  viewTitle,
  amount,
  onSeeAnalysis,
  analysisOpen = false,
}: {
  expression: ExpressionDetail;
  /** The view's question, shown as the eyebrow above the strategy name. */
  viewTitle?: string | null;
  /** The ₹ amount from the shared ticket, so "If you put in ₹X" tracks it. */
  amount: number;
  /** Opens the inline full-analysis (StrategyDeepDive) accordion. */
  onSeeAnalysis?: () => void;
  analysisOpen?: boolean;
}): React.ReactElement {
  const e = expression;
  const amt = Number.isFinite(amount) ? amount : 100_000;
  const spread = outcomeSpread(e);
  const minAmt = exprMinAmount(e);
  const [analysisHover, setAnalysisHover] = React.useState(false);

  const medianValue = spread ? (amt * spread.median) / 100 : 0;

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
      {/* ── TOP REGION (subgrid row 1): header + facts. Its height is shared
           across sibling cards so every outcome number below aligns. ── */}
      <div className="vwd-card-top">
        {/* ── header: (question) · name · risk chip ── */}
        <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
          {viewTitle && (
            <span
              style={{
                fontFamily: FONT,
                fontSize: 13.5,
                lineHeight: 1.4,
                color: "var(--text-tertiary)",
              }}
            >
              {viewTitle}
            </span>
          )}
          <h3
            style={{
              margin: 0,
              fontFamily: FONT,
              fontSize: 18,
              fontWeight: 600,
              letterSpacing: "-0.02em",
              lineHeight: 1.25,
              // Reserve two title lines so the risk line below sits at the same
              // Y across all three cards, even when a title wraps.
              minHeight: "calc(2 * 1.25em)",
              color: "var(--text-primary)",
            }}
          >
            {exprName(e)}
          </h3>
          <span
            style={{
              fontFamily: FONT,
              fontSize: 13,
              fontWeight: 600,
              color: "var(--text-tertiary)",
            }}
          >
            Risk: <span style={{ color: riskLevel(e.tier).color }}>{riskLevel(e.tier).label}</span>
          </span>
        </div>

        {/* ── you'd own ── */}
        <LabeledBlock label="You'd own" icon={<Wallet size={13} strokeWidth={2} />} caps={false}>
          <span style={{ fontFamily: FONT, fontSize: 14.5, lineHeight: 1.55, color: "var(--text-secondary)" }}>
            {ownSentence(e)}
          </span>
        </LabeledBlock>
      </div>
      {/* end TOP REGION */}

      {/* ── REST REGION (subgrid row 2): outcome → metrics → risk → CTA. The
           1fr track equalizes the card bodies; the footer pins to the floor. ── */}
      <div className="vwd-card-rest">
        {spread ? (
          <div style={{ display: "flex", flexDirection: "column", gap: 18 }}>
            {/* hero outcome, scaled to the ticket amount */}
            <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
              <span style={{ fontFamily: FONT, fontSize: 13, color: "var(--text-tertiary)" }}>
                If you put in {inr(amt)}, you&apos;d typically end with
              </span>
              <div style={{ display: "flex", alignItems: "baseline", gap: 8, flexWrap: "wrap" }}>
                <span
                  style={{
                    fontFamily: "var(--font-serif)",
                    fontVariantNumeric: "tabular-nums",
                    fontSize: 36,
                    fontWeight: 600,
                    letterSpacing: "-0.02em",
                    lineHeight: 1,
                    color: "var(--text-primary)",
                  }}
                >
                  {medianValue >= 0 ? "+" : "−"}
                  {inr(Math.abs(medianValue)).replace("−", "")}
                </span>
                <span style={{ fontFamily: FONT, fontSize: 13, color: "var(--text-tertiary)" }}>
                  on average
                </span>
              </div>
            </div>

            {/* clean, borderless metric row — worst · best · minimum */}
            <MetricRow
              cells={[
                spread.worst != null
                  ? { label: "Worst seen", value: inr((amt * spread.worst) / 100), tone: "loss" as const }
                  : null,
                spread.best != null
                  ? { label: "Best seen", value: inr((amt * spread.best) / 100), tone: "profit" as const }
                  : null,
                { label: "Minimum", value: inr(minAmt), tone: "neutral" as const },
              ]}
            />
          </div>
        ) : (
          <LabeledBlock label="Priced at deploy">
            <span style={{ fontFamily: FONT, fontSize: 14, lineHeight: 1.55, color: "var(--text-secondary)" }}>
              An options structure with no historical backtest — its payoff is priced from the
              live option chain when you deploy. Minimum {inr(minAmt)} to start.
            </span>
          </LabeledBlock>
        )}

        {/* ── footer: the one risk · the analysis link · the disclaimer ── */}
        <div
          style={{
            marginTop: "auto",
            display: "flex",
            flexDirection: "column",
            gap: 14,
            paddingTop: 18,
            borderTop: "1px solid var(--glass-border)",
          }}
        >
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 12 }}>
            {onSeeAnalysis ? (
              <button
                type="button"
                onClick={onSeeAnalysis}
                aria-expanded={analysisOpen}
                onMouseEnter={() => setAnalysisHover(true)}
                onMouseLeave={() => setAnalysisHover(false)}
                onFocus={() => setAnalysisHover(true)}
                onBlur={() => setAnalysisHover(false)}
                style={{
                  display: "inline-flex",
                  alignItems: "center",
                  justifyContent: "center",
                  gap: 8,
                  fontFamily: FONT,
                  fontSize: 14,
                  fontWeight: 600,
                  letterSpacing: "-0.01em",
                  color: "hsl(var(--primary-foreground))",
                  background: "hsl(var(--primary))",
                  border: "1px solid hsl(var(--primary))",
                  borderRadius: "var(--radius-md)",
                  padding: "8px 16px",
                  cursor: "pointer",
                  opacity: analysisHover ? 0.9 : 1,
                  transition: "opacity 160ms var(--ease-quartr)",
                }}
              >
                {analysisOpen ? "Hide the full analysis" : "See the full analysis"}
                <ChevronDown
                  size={14}
                  strokeWidth={2}
                  aria-hidden
                  style={{
                    transform: analysisOpen
                      ? "rotate(180deg)"
                      : analysisHover
                        ? "translateY(1px)"
                        : "none",
                    transition: "transform 240ms var(--ease-quartr)",
                  }}
                />
              </button>
            ) : (
              <span />
            )}
            {e.plain_risk && <RiskNote>{e.plain_risk}</RiskNote>}
          </div>

          <span style={{ fontFamily: FONT, fontSize: 12, lineHeight: 1.5, color: "var(--text-tertiary)" }}>
            You review and place every order yourself. This is analysis, not financial advice.
          </span>
        </div>
      </div>
      {/* end REST REGION */}
    </div>
  );
}

/**
 * LabeledBlock — a small sentence-case micro-label above a block of content.
 * The card's cohesive label system (used for "You'd own" and the fallback).
 */
function LabeledBlock({
  label,
  children,
  icon,
  caps = true,
}: {
  label: string;
  children: React.ReactNode;
  icon?: React.ReactNode;
  caps?: boolean;
}): React.ReactElement {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 6, minWidth: 0 }}>
      <span
        style={{
          display: "inline-flex",
          alignItems: "center",
          gap: 5,
          fontFamily: FONT,
          fontSize: caps ? 11.5 : 12.5,
          fontWeight: 600,
          letterSpacing: caps ? "0.04em" : "0.01em",
          textTransform: caps ? "uppercase" : "none",
          color: "var(--text-tertiary)",
        }}
      >
        {icon != null && (
          <span style={{ display: "inline-flex", flexShrink: 0 }} aria-hidden>
            {icon}
          </span>
        )}
        {label}
      </span>
      {children}
    </div>
  );
}

type MetricTone = "profit" | "loss" | "neutral";
type MetricCell = { label: string; value: string; tone: MetricTone };

function metricColor(tone: MetricTone): string {
  if (tone === "profit") return "var(--color-profit)";
  if (tone === "loss") return "var(--color-loss)";
  return "var(--text-primary)";
}

/**
 * MetricRow — the worst / best / minimum figures as a clean, borderless,
 * left-aligned row split from the hero by a single hairline. No filled box —
 * the numbers do the work.
 */
function MetricRow({ cells }: { cells: (MetricCell | null)[] }): React.ReactElement {
  const present = cells.filter((c): c is MetricCell => c != null);
  return (
    <div
      style={{
        display: "flex",
        flexWrap: "wrap",
        gap: "14px 32px",
        paddingTop: 16,
        borderTop: "1px solid var(--glass-border)",
      }}
    >
      {present.map((c) => (
        <div key={c.label} style={{ display: "flex", flexDirection: "column", gap: 3 }}>
          <span style={{ fontFamily: FONT, fontSize: 12, color: "var(--text-tertiary)" }}>{c.label}</span>
          <span
            style={{
              fontFamily: FONT,
              fontVariantNumeric: "tabular-nums",
              fontSize: 15,
              fontWeight: 600,
              letterSpacing: "-0.01em",
              color: metricColor(c.tone),
            }}
          >
            {c.value}
          </span>
        </div>
      ))}
    </div>
  );
}

/**
 * "If you're wrong" — the single biggest risk, revealed in a calm floating
 * card on hover / keyboard focus. Anchored to the right so it never overflows
 * the card's edge.
 */
function RiskNote({ children }: { children: React.ReactNode }): React.ReactElement {
  const [open, setOpen] = React.useState(false);

  return (
    <span
      style={{ position: "relative", display: "inline-flex", width: "fit-content" }}
      onMouseEnter={() => setOpen(true)}
      onMouseLeave={() => setOpen(false)}
    >
      <button
        type="button"
        aria-expanded={open}
        aria-label="If you're wrong"
        onFocus={() => setOpen(true)}
        onBlur={() => setOpen(false)}
        style={{
          display: "inline-flex",
          alignItems: "center",
          gap: 5,
          padding: 0,
          border: "none",
          background: "none",
          cursor: "help",
          fontFamily: FONT,
          fontSize: 12.5,
          fontWeight: 500,
          lineHeight: 1.5,
          color: "var(--text-tertiary)",
        }}
      >
        <AlertTriangle size={13} strokeWidth={2} aria-hidden />
        If you&apos;re wrong
      </button>

      {open && (
        <span
          role="note"
          style={{
            position: "absolute",
            bottom: "calc(100% + 8px)",
            right: 0,
            zIndex: 20,
            width: 300,
            maxWidth: "min(300px, calc(100vw - 32px))",
            padding: "12px 14px",
            fontFamily: FONT,
            fontSize: 12,
            lineHeight: 1.55,
            color: "var(--text-secondary)",
            background: "var(--glass-bg-hover)",
            border: "none",
            borderRadius: 6,
            boxShadow: "none",
          }}
        >
          {children}
        </span>
      )}
    </span>
  );
}

export default StrategyCleanCard;
