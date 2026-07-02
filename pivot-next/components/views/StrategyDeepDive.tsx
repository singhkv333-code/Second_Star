"use client";

/**
 * StrategyDeepDive — the "I want the evidence" page.
 *
 * The gallery card + view detail are the calm, layman path (belief → stance →
 * strategies). This is the OTHER audience: the speculative, half-baked-finance
 * enthusiast who wants to interrogate a single strategy. Everything here is REAL
 * and computed — nothing is fabricated:
 *
 *   1  Calculator      — put in ₹X, see the outcome range (StrategyCalculator).
 *   2  Where it may go  — Monte-Carlo terminal-outcome distribution.
 *   3  Every occurrence — the real event-study, per-window, vs the index.
 *   4  Structure        — option legs + greeks (option tier) OR the weighted
 *                         holdings (basket/hedge tier), with the REAL scheme.
 *   5  The honest stats — typical return, hit-rate, worst drop, reward:risk,
 *                         trust verdict — the numbers, stated plainly.
 *
 * DESIGN LAW: rounded, border-only, ≥13px, tabular numerals, calm color
 * (green/red reserved for real P&L), light + dark via tokens.
 */

import * as React from "react";
import { ArrowLeft } from "lucide-react";
import {
  Bar,
  BarChart,
  Cell,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { ExpressionDetail, ForwardModel, EntryBlock } from "@/lib/types";
import { MonteCarloDistribution, type MonteCarlo } from "./charts/MonteCarloDistribution";
import { StrategyCalculator } from "./StrategyCalculator";
import { Num, Stat } from "./Stat";
import { tierLabel } from "./view-format";
import { useTokenColors } from "./use-token-color";

const FONT = "var(--font-display)";

function pct(v: number | null | undefined, dp = 1): string {
  if (v == null) return "—";
  const s = v > 0 ? "+" : v < 0 ? "−" : "";
  return `${s}${Math.abs(v).toFixed(dp)}%`;
}

function Section({
  title,
  subtitle,
  children,
}: {
  title: string;
  subtitle?: string;
  children: React.ReactNode;
}): React.ReactElement {
  return (
    <section
      style={{
        border: "1px solid var(--glass-border)",
        borderRadius: "var(--radius-lg)",
        background: "var(--bg-base)",
        padding: 18,
        display: "flex",
        flexDirection: "column",
        gap: 14,
      }}
    >
      <div style={{ display: "flex", flexDirection: "column", gap: 3 }}>
        <h3
          style={{
            margin: 0,
            fontFamily: FONT,
            fontSize: 16,
            fontWeight: 600,
            color: "var(--text-primary)",
          }}
        >
          {title}
        </h3>
        {subtitle && (
          <span style={{ fontFamily: FONT, fontSize: 13, color: "var(--text-tertiary)", lineHeight: 1.45 }}>
            {subtitle}
          </span>
        )}
      </div>
      {children}
    </section>
  );
}

// ---------------------------------------------------------------------------
// ForwardModelSection — "If it goes your way" (modeled, never historical).
// Renders only when forward_model is present. All numbers are computed by the
// backend scenario model; labeled "modeled" throughout so they are never
// confused with a track record.
// ---------------------------------------------------------------------------

function ForwardModelSection({ fm }: { fm: ForwardModel }): React.ReactElement {
  const net = fm.expected_net_pct;
  const sign = net > 0 ? "+" : net < 0 ? "−" : "";
  const absNet = Math.abs(net).toFixed(1);
  const band = fm.band_pct;
  const pYesPct = Math.round(fm.p_yes * 100);

  // Horizontal percentile band: p05 | p25 | p50 | p75
  // Normalize to a 0-100 scale for the visual bar.
  const vals = [band.p05, band.p25, band.p50, band.p75];
  const minV = Math.min(...vals);
  const maxV = Math.max(...vals);
  const span = maxV - minV || 1;
  const toPos = (v: number): string => `${((v - minV) / span) * 100}%`;

  const [open, setOpen] = React.useState(false);

  return (
    <Section
      title="If it goes your way"
      subtitle="A scenario model — not a track record. Labeled 'modeled' throughout."
    >
      {/* Lead: p50 expected net */}
      <div style={{ display: "flex", alignItems: "baseline", gap: 8 }}>
        <span
          style={{
            fontFamily: FONT,
            fontSize: 28,
            fontWeight: 700,
            letterSpacing: "-0.02em",
            fontVariantNumeric: "tabular-nums",
            color: net >= 0 ? "var(--color-profit)" : "var(--color-loss)",
          }}
        >
          {sign}{absNet}%
        </span>
        <span style={{ fontFamily: FONT, fontSize: 13, color: "var(--text-tertiary)" }}>
          modeled, net of costs
        </span>
      </div>

      {/* Probability line */}
      <p style={{ margin: 0, fontFamily: FONT, fontSize: 13, color: "var(--text-secondary)", lineHeight: 1.5 }}>
        <strong style={{ fontVariantNumeric: "tabular-nums" }}>{pYesPct}%</strong>
        {" priced in — "}
        {fm.p_source}
      </p>

      {/* Percentile band */}
      <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
        <span style={{ fontFamily: FONT, fontSize: 12, color: "var(--text-tertiary)" }}>
          Scenario range (p05 → p75, net)
        </span>
        <div style={{ position: "relative", height: 20, background: "color-mix(in srgb, var(--text-tertiary) 10%, transparent)", borderRadius: 4 }}>
          {/* p05 → p75 fill */}
          <div
            style={{
              position: "absolute",
              left: toPos(band.p05),
              width: `${((band.p75 - band.p05) / span) * 100}%`,
              height: "100%",
              background: "color-mix(in srgb, var(--pivot-blue) 25%, transparent)",
              borderRadius: 4,
            }}
          />
          {/* p25 → p75 fill (darker) */}
          <div
            style={{
              position: "absolute",
              left: toPos(band.p25),
              width: `${((band.p75 - band.p25) / span) * 100}%`,
              height: "100%",
              background: "color-mix(in srgb, var(--pivot-blue) 40%, transparent)",
              borderRadius: 4,
            }}
          />
          {/* p50 marker */}
          <div
            style={{
              position: "absolute",
              left: toPos(band.p50),
              width: 2,
              height: "100%",
              background: "var(--pivot-blue)",
              borderRadius: 1,
              transform: "translateX(-1px)",
            }}
          />
        </div>
        <div
          style={{
            display: "flex",
            justifyContent: "space-between",
            fontFamily: FONT,
            fontSize: 12,
            fontVariantNumeric: "tabular-nums",
            color: "var(--text-tertiary)",
          }}
        >
          <span>{pct(band.p05)} p05</span>
          <span>{pct(band.p25)} p25</span>
          <span>{pct(band.p50)} p50</span>
          <span>{pct(band.p75)} p75</span>
        </div>
      </div>

      {/* Assumptions — collapsible quiet list */}
      <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
        <button
          type="button"
          onClick={() => setOpen((v) => !v)}
          style={{
            alignSelf: "flex-start",
            fontFamily: FONT,
            fontSize: 12,
            fontWeight: 500,
            color: "var(--text-tertiary)",
            background: "transparent",
            border: "none",
            cursor: "pointer",
            padding: 0,
            textDecoration: "underline",
            textDecorationStyle: "dotted",
          }}
        >
          How this was modeled {open ? "▲" : "▼"}
        </button>
        {open && (
          <ul
            style={{
              margin: 0,
              paddingLeft: 18,
              display: "flex",
              flexDirection: "column",
              gap: 4,
            }}
          >
            {fm.assumptions.map((a, i) => (
              <li
                key={i}
                style={{ fontFamily: FONT, fontSize: 12, color: "var(--text-tertiary)", lineHeight: 1.5 }}
              >
                {a}
              </li>
            ))}
          </ul>
        )}
      </div>
    </Section>
  );
}

// ---------------------------------------------------------------------------
// EntrySection — "Getting in small" — the smallest honest entry ticket.
// ---------------------------------------------------------------------------

function EntrySection({ entry }: { entry: EntryBlock }): React.ReactElement {
  const fmtInr = (n: number): string => "₹" + Math.round(n).toLocaleString("en-IN");

  return (
    <Section
      title="Getting in small"
      subtitle="The cheapest honest way to enter this strategy today."
    >
      {/* Headline: min_entry_inr */}
      {entry.min_entry_inr != null && (
        <div style={{ display: "flex", alignItems: "baseline", gap: 8 }}>
          <span
            style={{
              fontFamily: FONT,
              fontSize: 24,
              fontWeight: 700,
              letterSpacing: "-0.01em",
              fontVariantNumeric: "tabular-nums",
              color: "var(--text-primary)",
            }}
          >
            {fmtInr(entry.min_entry_inr)}
          </span>
          <span style={{ fontFamily: FONT, fontSize: 13, color: "var(--text-tertiary)" }}>
            {entry.basis === "option_premium" ? "per lot (upfront premium)" : "to enter"}
          </span>
        </div>
      )}

      {/* lite_basket / etf_core_plus_names: list the legs. A core leg is ETF
          units; a satellite leg is one whole share of the strategy's own name. */}
      {(entry.basis === "lite_basket" || entry.basis === "etf_core_plus_names") &&
        entry.legs &&
        entry.legs.length > 0 && (
          <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
            {entry.legs.map((leg, i) => (
              <div
                key={i}
                style={{
                  fontFamily: FONT,
                  fontSize: 13,
                  fontVariantNumeric: "tabular-nums",
                  color: "var(--text-primary)",
                }}
              >
                {leg.units ?? leg.shares} × {leg.symbol} @ {fmtInr(leg.price)}
                {leg.role === "core" && leg.tracks && (
                  <span style={{ color: "var(--text-tertiary)" }}>
                    {" "}
                    — the core; tracks {leg.tracks}
                  </span>
                )}
                {leg.role === "satellite" && (
                  <span style={{ color: "var(--text-tertiary)" }}>
                    {" "}
                    — the strategy's own pick
                  </span>
                )}
              </div>
            ))}
          </div>
        )}

      {/* dropped names */}
      {(entry.dropped ?? []).length > 0 && (
        <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
          <span style={{ fontFamily: FONT, fontSize: 12, color: "var(--text-tertiary)" }}>
            Not included at this ticket size:
          </span>
          {(entry.dropped ?? []).map((d, i) => (
            <span
              key={i}
              style={{ fontFamily: FONT, fontSize: 12, color: "var(--text-tertiary)", lineHeight: 1.4 }}
            >
              {d.symbol} — {d.reason}
            </span>
          ))}
        </div>
      )}

      {/* etf_substitute: show the ETF line */}
      {entry.basis === "etf_substitute" && entry.etf && (
        <div
          style={{
            fontFamily: FONT,
            fontSize: 13,
            fontVariantNumeric: "tabular-nums",
            color: "var(--text-primary)",
          }}
        >
          {entry.etf.units} × {entry.etf.symbol} @ {fmtInr(entry.etf.price)}
          <span style={{ color: "var(--text-tertiary)" }}> — tracks {entry.etf.tracks}</span>
        </div>
      )}

      {/* etf_alternative (offered alongside lite_basket) */}
      {entry.etf_alternative && (
        <div style={{ display: "flex", flexDirection: "column", gap: 3 }}>
          <span style={{ fontFamily: FONT, fontSize: 12, color: "var(--text-tertiary)" }}>
            Or simpler:
          </span>
          <div
            style={{
              fontFamily: FONT,
              fontSize: 13,
              fontVariantNumeric: "tabular-nums",
              color: "var(--text-primary)",
            }}
          >
            {entry.etf_alternative.units} × {entry.etf_alternative.symbol} @ {fmtInr(entry.etf_alternative.price)}
            <span style={{ color: "var(--text-tertiary)" }}> — tracks {entry.etf_alternative.tracks}</span>
          </div>
        </div>
      )}

      {/* small_ticket: the budget-sized far-OTM long single — a DIFFERENT
          structure, framed as the longshot it is, never as the same trade. */}
      {entry.small_ticket && (
        <div
          style={{
            display: "flex",
            flexDirection: "column",
            gap: 4,
            padding: "10px 12px",
            border: "1px solid var(--glass-border)",
            borderRadius: "var(--radius-md)",
          }}
        >
          <span
            style={{
              fontFamily: FONT,
              fontSize: 13,
              fontWeight: 600,
              color: "var(--text-primary)",
              fontVariantNumeric: "tabular-nums",
            }}
          >
            Longshot ticket: ≈{fmtInr(entry.small_ticket.est_premium_per_lot_inr)}
            {" — "}
            {entry.small_ticket.structure === "long_strangle"
              ? "a far-out-of-the-money strangle"
              : entry.small_ticket.structure === "long_put"
                ? "a single far-out-of-the-money put"
                : "a single far-out-of-the-money call"}
            {entry.small_ticket.underlying
              ? ` on ${entry.small_ticket.underlying}`
              : ""}
          </span>
          {entry.small_ticket.pop_pct != null && (
            <span
              style={{ fontFamily: FONT, fontSize: 12, color: "var(--text-tertiary)" }}
            >
              Modelled odds of finishing profitable: ~
              {entry.small_ticket.pop_pct}% — most of these expire worthless;
              the premium is the most you can lose.
            </span>
          )}
          {entry.small_ticket.note && (
            <span
              style={{
                fontFamily: FONT,
                fontSize: 12,
                color: "var(--text-tertiary)",
                lineHeight: 1.45,
              }}
            >
              {entry.small_ticket.note}
            </span>
          )}
        </div>
      )}

      {/* option_alternates: the same structure on cheaper same-theme lots */}
      {(entry.option_alternates ?? []).length > 0 && (
        <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
          <span style={{ fontFamily: FONT, fontSize: 12, color: "var(--text-tertiary)" }}>
            Same structure, cheaper lot:
          </span>
          {(entry.option_alternates ?? []).map((a, i) => (
            <span
              key={i}
              style={{
                fontFamily: FONT,
                fontSize: 13,
                fontVariantNumeric: "tabular-nums",
                color: "var(--text-primary)",
              }}
            >
              {a.label} — ≈{fmtInr(a.est_premium_per_lot_inr)}/lot
              {a.pop_pct != null && (
                <span style={{ color: "var(--text-tertiary)" }}>
                  {" "}
                  (modelled odds ~{a.pop_pct}%)
                </span>
              )}
            </span>
          ))}
        </div>
      )}

      {/* note (always present) */}
      <p
        style={{
          margin: 0,
          fontFamily: FONT,
          fontSize: 13,
          color: "var(--text-secondary)",
          lineHeight: 1.55,
        }}
      >
        {entry.note}
      </p>
    </Section>
  );
}

// ---------------------------------------------------------------------------
// WeakTrackingWarning — amber callout for a "Heads-up: this bundle..." warning.
// ---------------------------------------------------------------------------

function WeakTrackingWarning({ text }: { text: string }): React.ReactElement {
  return (
    <div
      style={{
        display: "flex",
        gap: 10,
        padding: "12px 14px",
        border: "1px solid color-mix(in srgb, var(--color-warn) 45%, transparent)",
        borderRadius: "var(--radius-lg)",
        background: "color-mix(in srgb, var(--color-warn) 8%, var(--bg-base))",
      }}
    >
      <span
        style={{
          fontFamily: FONT,
          fontSize: 13,
          color: "var(--text-secondary)",
          lineHeight: 1.55,
        }}
      >
        {text}
      </span>
    </div>
  );
}

export function StrategyDeepDive({
  expression,
  viewTitle,
  onBack,
}: {
  expression: ExpressionDetail;
  viewTitle?: string | null;
  onBack: () => void;
}): React.ReactElement {
  const c = useTokenColors({
    profit: "--color-profit",
    loss: "--color-loss",
    ink: "--text-primary",
    tertiary: "--text-tertiary",
    border: "--glass-border",
    borderFocus: "--glass-border-focus",
  });

  const e = expression;
  const om = e.option_model ?? null;
  const isOption = !!om;
  const isStraddle = om?.structure === "long_straddle";
  const longHoldings = (e.holdings ?? []).filter((h) => h.position !== "short");
  const shortHolding = (e.holdings ?? []).find((h) => h.position === "short");
  const name = e.strategy_name ?? e.plain_label ?? tierLabel(e.tier);

  // evidence_basis logic:
  // - "shock_no_analogs" with no episodes → skip the track record section
  // - "rolling_windows" → add caption to track record section
  const evidenceBasis = e.evidence_basis ?? null;
  const hasNoAnalogs = evidenceBasis === "shock_no_analogs";
  const hasEpisodes = (e.episodes?.length ?? 0) >= 2;
  const showEpisodes = hasEpisodes && !hasNoAnalogs;

  // Weak-tracking warnings (lines starting "Heads-up:")
  const weakWarning = (e.warnings ?? []).find((w) =>
    w.toLowerCase().startsWith("heads-up:")
  ) ?? null;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 18 }}>
      {/* header */}
      <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
        <button
          onClick={onBack}
          style={{
            display: "inline-flex",
            alignItems: "center",
            gap: 6,
            alignSelf: "flex-start",
            fontFamily: FONT,
            fontSize: 14,
            fontWeight: 500,
            color: "var(--text-secondary)",
            background: "transparent",
            border: "none",
            cursor: "pointer",
            padding: 0,
          }}
        >
          <ArrowLeft size={15} /> Back to the view
        </button>
        <div style={{ display: "flex", flexDirection: "column", gap: 5 }}>
          {viewTitle && (
            <span style={{ fontFamily: FONT, fontSize: 13, fontWeight: 500, color: "var(--text-tertiary)" }}>
              {viewTitle}
            </span>
          )}
          <h1
            style={{
              margin: 0,
              fontFamily: FONT,
              fontSize: 26,
              fontWeight: 600,
              letterSpacing: "-0.02em",
              color: "var(--text-primary)",
            }}
          >
            {name}
          </h1>
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
            <TierChip tier={tierLabel(e.tier)} />
            {e.strategy_type && (
              <span style={{ fontFamily: FONT, fontSize: 13, color: "var(--text-secondary)" }}>
                {e.strategy_type}
              </span>
            )}
            {e.weight_scheme && e.weight_scheme !== "equal" && (
              <span style={{ fontFamily: FONT, fontSize: 13, color: "var(--text-tertiary)" }}>
                · {schemeLabel(e.weight_scheme)}-weighted
              </span>
            )}
          </div>
          {e.plain_why && (
            <p style={{ margin: "4px 0 0", fontFamily: FONT, fontSize: 14, lineHeight: 1.55, color: "var(--text-secondary)", maxWidth: 640 }}>
              {e.plain_why}
            </p>
          )}
        </div>
      </div>

      {/* Weak-tracking callout — near the top so it's seen before the stats */}
      {weakWarning && <WeakTrackingWarning text={weakWarning} />}

      {/* 1 · calculator */}
      <StrategyCalculator expression={e} />

      {/* 2 · Monte Carlo — where it may go (gate on the same data-sufficiency the
          chart uses, so we never render an empty titled card). */}
      {e.monte_carlo && (e.monte_carlo.terminal_pct?.length ?? 0) >= 5 && (
        <Section
          title="Where it could go"
          subtitle="Re-running the strategy on thousands of resampled histories — the spread of terminal outcomes, not a single guess."
        >
          <MonteCarloDistribution
            mc={e.monte_carlo as unknown as MonteCarlo}
            underlyingSymbol={e.underlying_symbol}
            height={168}
          />
        </Section>
      )}

      {/* 2.5 · Forward model — "If it goes your way" (modeled, not historical) */}
      {e.forward_model && <ForwardModelSection fm={e.forward_model} />}

      {/* 3 · every past occurrence — the event study.
          Skipped entirely for shock_no_analogs (no comparable history exists). */}
      {showEpisodes && (
        <Section
          title="Every past occurrence"
          subtitle={`How the strategy did each of the ${e.episodes!.length} times, versus buying the index over the same window.`}
        >
          <EpisodeBars episodes={e.episodes!} c={c} />
          {evidenceBasis === "rolling_windows" && (
            <span style={{ fontFamily: FONT, fontSize: 12, color: "var(--text-tertiary)", lineHeight: 1.4 }}>
              Based on rolling windows of history — not distinct events.
            </span>
          )}
        </Section>
      )}

      {/* 4 · structure */}
      {isOption ? (
        <Section
          title="The options structure"
          subtitle={om!.assumptions}
        >
          <OptionStructure om={om!} isStraddle={isStraddle} />
        </Section>
      ) : longHoldings.length > 0 ? (
        <Section
          title="What you'd hold"
          subtitle={
            e.weight_scheme && e.weight_scheme !== "equal"
              ? `Sized by ${schemeLabel(e.weight_scheme)} — not equal-weight. Bigger bars carry more of your capital.`
              : "The names in the basket and how each did, on average, per occurrence."
          }
        >
          <HoldingsWeights holdings={longHoldings} shortHolding={shortHolding} c={c} />
        </Section>
      ) : null}

      {/* 5 · the honest stats */}
      <Section title="The honest stats" subtitle="Every number here is computed from real prices — nothing is fabricated.">
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "repeat(auto-fit, minmax(130px, 1fr))",
            gap: "16px 20px",
          }}
        >
          {isOption ? (
            <>
              <Stat
                label="Max profit"
                value={
                  <span style={{ color: "var(--color-profit)" }}>
                    {om!.max_profit_uncapped ? "Uncapped" : pct(om!.max_profit_pct, 0)}
                  </span>
                }
                sub={om!.max_profit_uncapped ? "moves with the underlying" : "of capital, capped"}
              />
              <Stat label="Max loss" value={<span style={{ color: "var(--color-loss)" }}>{pct(om!.max_loss_pct, 0)}</span>} sub="the premium paid" />
              <Stat label="Chance of profit" value={`${om!.pop_pct.toFixed(0)}%`} sub="lognormal, at expiry" />
              <Stat label="Breakeven move" value={isStraddle ? `±${pct(om!.breakeven_move_pct)}` : pct(om!.breakeven_move_pct)} sub="underlying must move" />
              <Stat label="Modelled vol" value={`${om!.vol_used_pct.toFixed(0)}%`} sub="realised, annualised" />
            </>
          ) : (
            <>
              <Stat
                label="Typical return"
                value={<span style={{ color: (e.strategy_total_pct ?? 0) >= 0 ? "var(--color-profit)" : "var(--color-loss)" }}>{pct(e.strategy_total_pct)}</span>}
                sub="mean per occurrence"
              />
              <Stat
                label="Ended positive"
                value={e.pct_positive != null ? `${e.pct_positive.toFixed(0)}%` : "—"}
                sub={e.n_positive != null && e.n_episodes ? `${e.n_positive} of ${e.n_episodes}` : undefined}
              />
              {e.gain_loss?.avg_gain_pct != null && (
                <Stat
                  label="Avg gain"
                  value={<span style={{ color: "var(--color-profit)" }}>{pct(e.gain_loss.avg_gain_pct)}</span>}
                  sub={e.gain_loss.n_gain != null ? `across ${e.gain_loss.n_gain} winning runs` : undefined}
                />
              )}
              {e.gain_loss?.avg_loss_pct != null && (
                <Stat
                  label="Avg loss"
                  value={<span style={{ color: "var(--color-loss)" }}>{pct(e.gain_loss.avg_loss_pct)}</span>}
                  sub={e.gain_loss.n_loss != null ? `across ${e.gain_loss.n_loss} losing runs` : undefined}
                />
              )}
              {e.gain_loss?.max_gain_pct != null && (
                <Stat
                  label="Max gain"
                  value={<span style={{ color: "var(--color-profit)" }}>{pct(e.gain_loss.max_gain_pct)}</span>}
                  sub="best single occurrence"
                />
              )}
              {e.gain_loss?.max_loss_pct != null && (
                <Stat
                  label="Max loss"
                  value={<span style={{ color: e.gain_loss.max_loss_pct < 0 ? "var(--color-loss)" : "var(--color-profit)" }}>{pct(e.gain_loss.max_loss_pct)}</span>}
                  sub="worst single occurrence"
                />
              )}
              <Stat label="Worst drop" value={<span style={{ color: "var(--color-loss)" }}>{pct(e.worst_drop_pct)}</span>} sub="deepest slide inside a run" />
              <Stat label="Reward : risk" value={e.risk_return_ratio != null ? e.risk_return_ratio.toFixed(1) : "—"} sub="return ÷ worst drop" />
              <Stat label="Track record" value={<span style={{ fontSize: 15 }}>{e.trust_badge ?? "—"}</span>} valueSize="md" sub={e.n_episodes ? `${e.n_episodes} occurrences` : undefined} />
            </>
          )}
        </div>
      </Section>

      {/* 6 · Entry block — "Getting in small" */}
      {e.entry && <EntrySection entry={e.entry} />}

      <p style={{ margin: 0, fontFamily: FONT, fontSize: 13, lineHeight: 1.5, color: "var(--text-tertiary)" }}>
        Pivot arms the trigger and prepares the orders — you review and place every order yourself. This is analysis, not financial advice.
      </p>
    </div>
  );
}

function TierChip({ tier }: { tier: string }): React.ReactElement {
  return (
    <span
      style={{
        fontFamily: FONT,
        fontSize: 12.5,
        fontWeight: 600,
        color: "var(--pivot-blue)",
        background: "color-mix(in srgb, var(--pivot-blue) 12%, var(--bg-base))",
        border: "1px solid color-mix(in srgb, var(--pivot-blue) 30%, transparent)",
        borderRadius: "var(--radius-pill, 999px)",
        padding: "3px 10px",
      }}
    >
      {tier}
    </span>
  );
}

function schemeLabel(s: string): string {
  return (
    {
      min_variance: "minimum-variance",
      risk_parity: "risk-parity",
      factor: "momentum-factor",
      mcap: "market-cap",
      equal: "equal",
    }[s] ?? s
  );
}

/** Per-episode return bars vs the index over the same window. */
function EpisodeBars({
  episodes,
  c,
}: {
  episodes: NonNullable<ExpressionDetail["episodes"]>;
  c: Record<string, string>;
}): React.ReactElement {
  const data = episodes.map((ep, i) => ({
    i,
    label: ep.label ?? `#${i + 1}`,
    date: ep.date ?? null,
    ret: ep.return_pct ?? 0,
    bench: ep.benchmark_pct ?? null,
  }));
  return (
    <div style={{ height: 180 }}>
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={data} margin={{ top: 12, right: 8, bottom: 4, left: 0 }}>
          <XAxis dataKey="i" hide />
          <YAxis
            tickFormatter={(v: number) => `${v > 0 ? "+" : ""}${v}%`}
            tick={{ fontFamily: FONT, fontSize: 13, fill: c.tertiary }}
            axisLine={false}
            tickLine={false}
            width={46}
          />
          <ReferenceLine y={0} stroke={c.borderFocus} />
          <Tooltip
            cursor={{ fill: "color-mix(in srgb, var(--text-tertiary) 8%, transparent)" }}
            content={({ active, payload }) => {
              if (!active || !payload?.length) return null;
              const d = payload[0]!.payload as (typeof data)[number];
              return (
                <div
                  style={{
                    background: "var(--bg-base)",
                    border: `1px solid ${c.border}`,
                    borderRadius: 8,
                    padding: "6px 10px",
                    fontFamily: FONT,
                    fontVariantNumeric: "tabular-nums",
                    fontSize: 13,
                  }}
                >
                  <div style={{ fontWeight: 700, color: "var(--text-primary)" }}>
                    {d.label}
                    {d.date ? ` · ${d.date}` : ""}
                  </div>
                  <div style={{ color: d.ret >= 0 ? c.profit : c.loss }}>
                    strategy {pct(d.ret)}
                  </div>
                  {d.bench != null && (
                    <div style={{ color: "var(--text-tertiary)" }}>index {pct(d.bench)}</div>
                  )}
                </div>
              );
            }}
          />
          <Bar dataKey="ret" radius={[2, 2, 0, 0]} isAnimationActive={false}>
            {data.map((d, i) => (
              <Cell key={i} fill={d.ret >= 0 ? c.profit : c.loss} fillOpacity={0.82} />
            ))}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}

/** Option legs + net greeks. Handles both standard spreads and long_straddle
 *  (two BUY legs: CE + PE at ATM). Pass isStraddle=true to label it correctly. */
function OptionStructure({
  om,
  isStraddle = false,
}: {
  om: NonNullable<ExpressionDetail["option_model"]>;
  isStraddle?: boolean;
}): React.ReactElement {
  const g = om.net_greeks;
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
      {isStraddle && (
        <span
          style={{
            fontFamily: FONT,
            fontSize: 13,
            fontWeight: 600,
            color: "var(--pivot-blue)",
            background: "color-mix(in srgb, var(--pivot-blue) 8%, transparent)",
            border: "1px solid color-mix(in srgb, var(--pivot-blue) 25%, transparent)",
            borderRadius: "var(--radius-md)",
            padding: "4px 10px",
            alignSelf: "flex-start",
          }}
        >
          Both directions (straddle) — profits from a large move either way
        </span>
      )}
      <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
        {om.legs.map((leg, i) => (
          <div
            key={i}
            style={{
              display: "flex",
              alignItems: "center",
              gap: 10,
              fontFamily: FONT,
              fontSize: 14,
              color: "var(--text-primary)",
            }}
          >
            <span
              style={{
                fontSize: 12.5,
                fontWeight: 700,
                color: leg.action === "BUY" ? "var(--pivot-blue)" : "var(--color-warn)",
                background:
                  leg.action === "BUY"
                    ? "color-mix(in srgb, var(--pivot-blue) 12%, var(--bg-base))"
                    : "color-mix(in srgb, var(--color-warn) 12%, var(--bg-base))",
                borderRadius: 6,
                padding: "2px 8px",
                minWidth: 46,
                textAlign: "center",
              }}
            >
              {leg.action}
            </span>
            <span style={{ fontWeight: 600 }}>
              {leg.option_type} {leg.strike_label}
            </span>
            {om.underlying_label && (
              <span style={{ color: "var(--text-tertiary)" }}>on {om.underlying_label}</span>
            )}
          </div>
        ))}
      </div>
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fit, minmax(90px, 1fr))",
          gap: "12px 16px",
          borderTop: "1px solid var(--glass-border)",
          paddingTop: 12,
        }}
      >
        <Stat label="Net delta" value={<Num size="md">{g.delta.toFixed(2)}</Num>} valueSize="md" />
        <Stat label="Net gamma" value={<Num size="md">{g.gamma.toFixed(3)}</Num>} valueSize="md" />
        <Stat label="Net vega" value={<Num size="md">{g.vega.toFixed(2)}</Num>} valueSize="md" />
        <Stat label="Net theta" value={<Num size="md">{g.theta.toFixed(2)}</Num>} valueSize="md" sub="per day" />
      </div>
    </div>
  );
}

/** Weighted holdings as bars (bar length = weight), with per-name avg return. */
function HoldingsWeights({
  holdings,
  shortHolding,
  c,
}: {
  holdings: NonNullable<ExpressionDetail["holdings"]>;
  shortHolding?: ExpressionDetail["holdings"][number];
  c: Record<string, string>;
}): React.ReactElement {
  const maxW = Math.max(...holdings.map((h) => h.weight_pct ?? 0), 1);
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
      {holdings.map((h, i) => {
        const w = h.weight_pct ?? 0;
        const ret = h.return_pct;
        return (
          <div key={i} style={{ display: "flex", alignItems: "center", gap: 12 }}>
            <span style={{ fontFamily: FONT, fontSize: 13.5, color: "var(--text-primary)", width: 130, flexShrink: 0, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
              {h.name}
            </span>
            <div style={{ flex: 1, height: 8, background: "color-mix(in srgb, var(--text-tertiary) 10%, transparent)", borderRadius: 4, overflow: "hidden" }}>
              <div style={{ width: `${Math.max(4, (w / maxW) * 100)}%`, height: "100%", background: c.profit, opacity: 0.55, borderRadius: 4 }} />
            </div>
            <Num size="md" style={{ width: 48, textAlign: "right", color: "var(--text-secondary)" }}>
              {w.toFixed(0)}%
            </Num>
            {ret != null && (
              <Num size="md" style={{ width: 56, textAlign: "right", color: ret >= 0 ? "var(--color-profit)" : "var(--color-loss)" }}>
                {pct(ret)}
              </Num>
            )}
          </div>
        );
      })}
      {shortHolding && (
        <div style={{ display: "flex", alignItems: "center", gap: 12, borderTop: "1px dashed var(--glass-border)", paddingTop: 10 }}>
          <span style={{ fontFamily: FONT, fontSize: 13.5, color: "var(--text-secondary)", width: 130, flexShrink: 0 }}>
            {shortHolding.name}
          </span>
          <span style={{ flex: 1, fontFamily: FONT, fontSize: 13, color: "var(--text-tertiary)" }}>
            short leg — hedges out the market
          </span>
        </div>
      )}
    </div>
  );
}

export default StrategyDeepDive;
