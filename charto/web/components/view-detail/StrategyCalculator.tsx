"use client";

/**
 * StrategyCalculator — the RIGHT column of the View-detail redesign (sticky).
 *
 * One ₹ amount input at the top (default ₹1,00,000) + one row per strategy:
 *   name · minimum required · what the entered amount projects to.
 * When the amount is below a strategy's minimum, that row greys out and shows
 * "Minimum ₹X" instead of a projection.
 *
 * All math lives in strategies.ts (`projectValue` / `meetsMinimum`) — this
 * component only formats. Never a single fabricated "you'll win ₹X": each row
 * shows the expected value AND the low→high range.
 */

import * as React from "react";
import { ArrowRight, ShieldCheck } from "lucide-react";
import { useTokenColors } from "@/components/views/use-token-color";
import {
  inr,
  inrCompact,
  meetsMinimum,
  projectValue,
  type StrategyConfig,
} from "./strategies";

const DEFAULT_AMT = 100_000;
const MIN_AMT = 100;
const MAX_AMT = 5_000_000;
const QUICK_ADDS = [10_000, 25_000, 50_000] as const;

export function StrategyCalculator({
  strategies,
  selectedId,
  onSelect,
  amount,
  onAmount,
  onDeploy,
}: {
  strategies: StrategyConfig[];
  selectedId: string;
  onSelect: (id: string) => void;
  /** Controlled amount (shared with the chart so both rescale together). */
  amount: number;
  onAmount: (v: number) => void;
  /** Fired when the user arms a deploy (register-not-execute). Optional so the
   *  standalone mock page renders without a backend. */
  onDeploy?: (strategyId: string, amount: number) => void;
}): React.ReactElement {
  const c = useTokenColors({
    blue: "--pivot-blue",
    profit: "--color-profit",
    loss: "--color-loss",
    ink: "--text-primary",
    secondary: "--text-secondary",
    tertiary: "--text-tertiary",
    border: "--glass-border",
    bg: "--bg-base",
  });

  const clamp = (v: number): number =>
    Math.max(MIN_AMT, Math.min(MAX_AMT, Math.round(v)));

  const selected =
    strategies.find((s) => s.id === selectedId) ?? strategies[0] ?? null;
  const selProj = selected ? projectValue(amount, selected) : null;
  const selOk = selected ? meetsMinimum(amount, selected) : false;

  // Register-not-execute: "arming" a draft. Resets whenever the picked
  // strategy changes so the confirmation never lingers on a stale choice.
  const [armed, setArmed] = React.useState(false);
  React.useEffect(() => setArmed(false), [selectedId, amount]);

  return (
    <div
      style={{
        border: `1px solid ${c.border}`,
        borderRadius: "var(--radius-lg)",
        background: c.bg,
        padding: 20,
        display: "flex",
        flexDirection: "column",
        gap: 16,
      }}
    >
      <style>{`
        .vd-quick:hover { border-color: var(--glass-border-focus) !important; }
        .vd-strat:not([aria-pressed="true"]):hover { border-color: var(--glass-border-hover) !important; }
        .vd-deploy { transition: opacity 160ms var(--ease-quartr), transform 160ms var(--ease-quartr); }
        .vd-deploy:hover { opacity: 0.9; }
        .vd-deploy:active { transform: translateY(1px); }
      `}</style>
      <div style={{ display: "flex", flexDirection: "column", gap: 3 }}>
        <span
          style={{
            fontFamily: "var(--font-display)",
            fontSize: 16,
            fontWeight: 600,
            letterSpacing: "-0.01em",
            color: "var(--text-primary)",
          }}
        >
          Strategy calculator
        </span>
        <span
          style={{
            fontFamily: "var(--font-display)",
            fontSize: 13,
            color: "var(--text-tertiary)",
            lineHeight: 1.45,
          }}
        >
          Enter an amount to see what each strategy could turn it into.
        </span>
      </div>

      {/* amount input */}
      <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
        <div
          style={{
            display: "flex",
            alignItems: "baseline",
            gap: 8,
            borderBottom: `1px solid ${c.border}`,
            paddingBottom: 8,
          }}
        >
          <span
            style={{
              fontFamily: "var(--font-display)",
              fontSize: 22,
              fontWeight: 700,
              color: "var(--text-tertiary)",
            }}
          >
            ₹
          </span>
          <input
            aria-label="Amount to invest"
            value={amount.toLocaleString("en-IN")}
            onChange={(e) => {
              const raw = Number(e.target.value.replace(/[^0-9]/g, ""));
              if (Number.isFinite(raw)) onAmount(clamp(raw || MIN_AMT));
            }}
            inputMode="numeric"
            style={{
              fontFamily: "var(--font-display)",
              fontVariantNumeric: "tabular-nums",
              fontSize: 28,
              fontWeight: 700,
              letterSpacing: "-0.02em",
              color: "var(--text-primary)",
              background: "transparent",
              border: "none",
              outline: "none",
              width: "100%",
              padding: 0,
            }}
          />
        </div>
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
          {QUICK_ADDS.map((q) => (
            <button
              key={q}
              onClick={() => onAmount(clamp(amount + q))}
              className="vd-quick"
              style={{
                fontFamily: "var(--font-display)",
                fontVariantNumeric: "tabular-nums",
                fontSize: 13,
                fontWeight: 600,
                color: "var(--text-secondary)",
                background: "transparent",
                border: `1px solid ${c.border}`,
                borderRadius: "var(--radius-pill, 999px)",
                padding: "6px 12px",
                cursor: "pointer",
                transition: "border-color 160ms var(--ease-quartr)",
              }}
            >
              +{inrCompact(q)}
            </button>
          ))}
          <button
            onClick={() => onAmount(DEFAULT_AMT)}
            style={{
              fontFamily: "var(--font-display)",
              fontSize: 13,
              fontWeight: 500,
              color: "var(--text-tertiary)",
              background: "transparent",
              border: "none",
              padding: "6px 6px",
              cursor: "pointer",
            }}
          >
            Reset
          </button>
        </div>
      </div>

      {/* per-strategy rows */}
      <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
        {strategies.map((s) => {
          const ok = meetsMinimum(amount, s);
          const proj = projectValue(amount, s);
          const selected = selectedId === s.id;
          const gain = proj.expected - amount;
          const gainColor = gain >= 0 ? c.profit : c.loss;

          return (
            <button
              key={s.id}
              onClick={() => onSelect(s.id)}
              aria-pressed={selected}
              className="vd-strat"
              style={{
                textAlign: "left",
                display: "flex",
                flexDirection: "column",
                gap: 6,
                padding: "12px 14px",
                borderRadius: "var(--radius-md)",
                cursor: "pointer",
                border: `1px solid ${selected ? s.color : c.border}`,
                background: "var(--bg-base)",
                opacity: ok ? 1 : 0.5,
                transition: "border-color 160ms var(--ease-quartr)",
              }}
            >
              <div
                style={{
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "space-between",
                  gap: 10,
                }}
              >
                <span
                  style={{
                    display: "inline-flex",
                    alignItems: "center",
                    gap: 8,
                    fontFamily: "var(--font-display)",
                    fontSize: 14,
                    fontWeight: 600,
                    color: "var(--text-primary)",
                  }}
                >
                  <span
                    aria-hidden
                    style={{
                      width: 10,
                      height: 10,
                      borderRadius: 3,
                      background: s.color,
                      flexShrink: 0,
                    }}
                  />
                  {s.name}
                </span>
                <span
                  style={{
                    fontFamily: "var(--font-display)",
                    fontVariantNumeric: "tabular-nums",
                    fontSize: 12,
                    color: "var(--text-tertiary)",
                    whiteSpace: "nowrap",
                  }}
                >
                  min {inrCompact(s.minAmount)}
                </span>
              </div>

              {ok ? (
                <>
                  <div
                    style={{
                      fontFamily: "var(--font-display)",
                      fontVariantNumeric: "tabular-nums",
                      fontSize: 15,
                      fontWeight: 600,
                      color: "var(--text-secondary)",
                    }}
                  >
                    {inr(amount)}{" "}
                    <span style={{ color: "var(--text-tertiary)" }}>→</span>{" "}
                    <span style={{ color: gainColor, fontWeight: 700 }}>
                      ~{inr(proj.expected)}
                    </span>
                  </div>
                  <div
                    style={{
                      fontFamily: "var(--font-display)",
                      fontVariantNumeric: "tabular-nums",
                      fontSize: 12,
                      color: "var(--text-tertiary)",
                    }}
                  >
                    range {inrCompact(proj.low)} → {inrCompact(proj.high)}
                  </div>
                </>
              ) : (
                <div
                  style={{
                    fontFamily: "var(--font-display)",
                    fontVariantNumeric: "tabular-nums",
                    fontSize: 13,
                    fontWeight: 600,
                    color: "var(--text-tertiary)",
                  }}
                >
                  Minimum {inr(s.minAmount)} to deploy
                </div>
              )}
            </button>
          );
        })}
      </div>

      {/* trade ticket: selected-strategy summary + deploy CTA */}
      {selected && (
        <div
          style={{
            display: "flex",
            flexDirection: "column",
            gap: 12,
            borderTop: `1px solid ${c.border}`,
            paddingTop: 16,
          }}
        >
          <div
            style={{
              display: "flex",
              alignItems: "baseline",
              justifyContent: "space-between",
              gap: 10,
            }}
          >
            <span
              style={{
                display: "inline-flex",
                alignItems: "center",
                gap: 8,
                fontFamily: "var(--font-display)",
                fontSize: 13,
                fontWeight: 600,
                color: "var(--text-secondary)",
                minWidth: 0,
              }}
            >
              <span
                aria-hidden
                style={{
                  width: 8,
                  height: 8,
                  borderRadius: 2,
                  background: selected.color,
                  flexShrink: 0,
                }}
              />
              <span
                style={{
                  overflow: "hidden",
                  textOverflow: "ellipsis",
                  whiteSpace: "nowrap",
                }}
              >
                {selected.name}
              </span>
            </span>
            {selOk && selProj && (
              <span
                style={{
                  fontFamily: "var(--font-display)",
                  fontVariantNumeric: "tabular-nums",
                  fontSize: 14,
                  fontWeight: 700,
                  color:
                    selProj.expected - amount >= 0 ? c.profit : c.loss,
                  whiteSpace: "nowrap",
                }}
              >
                ~{inr(selProj.expected)}
              </span>
            )}
          </div>

          {armed ? (
            <div
              style={{
                display: "flex",
                alignItems: "flex-start",
                gap: 10,
                borderRadius: "var(--radius-md)",
                border: `1px solid color-mix(in srgb, ${c.profit} 40%, transparent)`,
                padding: "12px 14px",
              }}
            >
              <ShieldCheck
                size={18}
                strokeWidth={1.9}
                color={c.profit}
                style={{ flexShrink: 0, marginTop: 1 }}
                aria-hidden
              />
              <span
                style={{
                  fontFamily: "var(--font-display)",
                  fontSize: 12.5,
                  lineHeight: 1.5,
                  color: "var(--text-secondary)",
                }}
              >
                Registered as a draft automation. Pivot never places the trade —
                open it to review and confirm in your broker.{" "}
                <button
                  onClick={() => setArmed(false)}
                  style={{
                    fontFamily: "var(--font-display)",
                    fontSize: 12.5,
                    fontWeight: 600,
                    color: c.blue,
                    background: "transparent",
                    border: "none",
                    padding: 0,
                    cursor: "pointer",
                  }}
                >
                  Undo
                </button>
              </span>
            </div>
          ) : (
            <button
              type="button"
              onClick={() => {
                setArmed(true);
                onDeploy?.(selected.id, amount);
              }}
              disabled={!selOk}
              aria-label={`Deploy ${selected.name}`}
              className="vd-deploy"
              style={{
                display: "inline-flex",
                alignItems: "center",
                justifyContent: "center",
                gap: 8,
                width: "100%",
                fontFamily: "var(--font-display)",
                fontSize: 15,
                fontWeight: 600,
                color: "hsl(var(--primary-foreground))",
                background: "hsl(var(--primary))",
                border: "1px solid hsl(var(--primary))",
                borderRadius: "var(--radius-md)",
                padding: "11px 16px",
                cursor: selOk ? "pointer" : "default",
                opacity: selOk ? 1 : 0.5,
              }}
            >
              {selOk ? "Deploy this expression" : "Amount below minimum"}
              {selOk && <ArrowRight size={16} strokeWidth={2} aria-hidden />}
            </button>
          )}
        </div>
      )}

      <p
        style={{
          margin: 0,
          fontFamily: "var(--font-display)",
          fontSize: 12,
          lineHeight: 1.5,
          color: "var(--text-tertiary)",
        }}
      >
        Projections are illustrative expected values with a low→high range, not a
        guaranteed payout. This is analysis, not financial advice.
      </p>
    </div>
  );
}

export default StrategyCalculator;
