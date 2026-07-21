"use client";

/**
 * StrategyDeepDive — the "See the full analysis" surface, opened as an inline
 * accordion below a strategy card on the View detail page.
 *
 * It is deliberately SPARE: exactly three evidence cards, side by side, and
 * nothing else — no header, no calculator, no stat wall, no episode list. Each
 * card is real and computed; none is fabricated, and any card whose data is
 * genuinely unavailable is dropped rather than faked:
 *
 *   1  What you'd hold        — the weighted basket (or the option structure).
 *   2  What the simulations say — a distilled Monte-Carlo read: the middle
 *                                outcome, the loss-to-best range, the odds of a
 *                                loss.
 *   3  Reward for the risk taken — return earned per unit of worst-case risk.
 *
 * DESIGN LAW: rounded, border-only, ≥13px, tabular numerals, calm color
 * (green/red reserved for real P&L), plain language, light + dark via tokens.
 */

import * as React from "react";
import type { ExpressionDetail, Holding } from "@/lib/types";
import { isError } from "@/lib/types";
import type { MonteCarlo } from "./charts/MonteCarloDistribution";
import { Num, Stat } from "./Stat";
import { useTokenColors } from "./use-token-color";
import { CompanyLogo } from "@/components/CompanyLogo";
import { fetchSecurityMeta } from "@/lib/api";
import type { SecurityMeta } from "@/lib/api";

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
        padding: 20,
        display: "flex",
        flexDirection: "column",
        gap: 16,
        minWidth: 0,
      }}
    >
      <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
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

/** Plain, scheme-aware read of how the basket is weighted (no jargon up front). */
function holdingsSubtitle(scheme: string | null | undefined): string {
  switch (scheme) {
    case "min_variance":
      return "More money goes into the steadier companies, so the whole bundle swings less. (This is called minimum-variance weighting.)";
    case "risk_parity":
      return "Each name is sized so it adds a similar amount of risk — no single stock dominates. (This is called risk-parity weighting.)";
    case "mcap":
      return "Bigger companies get a bigger share, matching their market size. (This is called market-cap weighting.)";
    case "factor":
      return "More money goes into the names with the strongest recent momentum. (This is called momentum weighting.)";
    case "curated":
      return "Each name is given a set share, chosen by hand to reflect the role it plays in the basket.";
    default:
      return "Equal money in each name — the simple, even split, with how each did on average per occurrence.";
  }
}

/** Plain read of the reward:risk ratio, in the mockup's voice. */
function rewardVerdict(r: number | null | undefined): string {
  if (r == null || Number.isNaN(r))
    return "Not enough history yet to judge the reward for the risk taken.";
  if (r >= 1.5) return "A strong payoff for the risk taken — well rewarded.";
  if (r >= 0.8) return "A fair payoff for the risk taken.";
  if (r >= 0.3) return "A modest payoff for the risk — steady, not spectacular.";
  if (r >= 0)
    return "A thin payoff for the risk taken — the reward is small next to the worst drop.";
  return "The risk taken has not been rewarded historically.";
}

export function StrategyDeepDive({
  expression,
}: {
  expression: ExpressionDetail;
  /** Retained for API compatibility with the page; unused in the spare layout. */
  viewTitle?: string | null;
  onBack?: () => void;
  showBackLink?: boolean;
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
  const allHoldings = e.holdings ?? [];
  const longHoldings = allHoldings.filter((h) => h.position !== "short");
  const shortHolding = allHoldings.find((h) => h.position === "short");

  // Batch-resolve display metadata (logo, real name, asset class) for any
  // symbol that doesn't already carry it from the backend payload.
  const [metaMap, setMetaMap] = React.useState<Record<string, SecurityMeta>>({});
  React.useEffect(() => {
    if (allHoldings.length === 0) return;
    // Symbols that are missing at least one metadata field
    const missing = allHoldings
      .filter((h) => h.logo_url === undefined && h.asset_class === undefined)
      .map((h) => h.symbol);
    // Pre-seed with whatever the payload already has
    const seed: Record<string, SecurityMeta> = {};
    for (const h of allHoldings) {
      if (h.asset_class !== undefined) {
        seed[h.symbol] = {
          symbol: h.symbol,
          name: h.name,
          logo_url: h.logo_url ?? null,
          asset_class: h.asset_class ?? null,
          currency: h.currency ?? null,
        };
      }
    }
    if (Object.keys(seed).length > 0) setMetaMap(seed);
    if (missing.length === 0) return;
    let cancelled = false;
    fetchSecurityMeta(missing).then((res) => {
      if (cancelled) return;
      if (isError(res)) return; // graceful: fall back to bare name + monogram
      const map: Record<string, SecurityMeta> = { ...seed };
      for (const m of res.data) {
        map[m.symbol] = m;
      }
      setMetaMap(map);
    });
    return () => { cancelled = true; };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [e.id]);

  const mc = e.monte_carlo ?? null;
  const hasMc = !!mc && (mc.terminal_pct?.length ?? 0) >= 5;
  const ratio = e.risk_return_ratio ?? null;
  const hasRatio = ratio != null && !Number.isNaN(ratio);

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
      {/* The three evidence cards, side by side — collapse to a stack only when
          the row genuinely runs out of width. */}
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fit, minmax(min(100%, 300px), 1fr))",
          gap: 16,
          alignItems: "stretch",
        }}
      >
        {/* 1 · What you'd hold (basket) / The options structure */}
        {isOption ? (
          <Section title="The options structure" subtitle={om!.assumptions}>
            <OptionStructure om={om!} />
          </Section>
        ) : longHoldings.length > 0 ? (
          <Section title="What you'd hold" subtitle={holdingsSubtitle(e.weight_scheme)}>
            <HoldingsWeights holdings={longHoldings} shortHolding={shortHolding} c={c} metaMap={metaMap} />
          </Section>
        ) : null}

        {/* 2 · What the simulations say (distilled Monte-Carlo read) */}
        {hasMc && (
          <Section
            title="What the simulations say"
            subtitle={`We re-ran this on reshuffled history ${mc!.n_sims.toLocaleString(
              "en-IN",
            )} times to see the spread of outcomes — not one guess.`}
          >
            <SimSummary mc={mc as unknown as MonteCarlo} mean={e.strategy_total_pct} c={c} />
          </Section>
        )}

        {/* 3 · Reward for the risk taken */}
        {hasRatio && (
          <Section
            title="Reward for the risk taken"
            subtitle="For every ₹1 you risked losing at the worst point, you earned this much back. Higher is better."
          >
            <RewardRisk
              ratio={ratio!}
              reward={e.strategy_total_pct}
              worstDrop={e.worst_drop_pct}
              c={c}
            />
          </Section>
        )}
      </div>

      <p style={{ margin: 0, fontFamily: FONT, fontSize: 13, lineHeight: 1.5, color: "var(--text-tertiary)" }}>
        Pivot arms the trigger and prepares the orders — you review and place every order yourself. This is analysis, not financial advice.
      </p>
    </div>
  );
}

/** Distilled Monte-Carlo: middle outcome, the loss→best track, odds of a loss. */
function SimSummary({
  mc,
  mean,
  c,
}: {
  mc: MonteCarlo;
  /** The MEAN return (headline measure); falls back to the median if absent. */
  mean?: number | null;
  c: Record<string, string>;
}): React.ReactElement {
  const worst = mc.p05;
  const best = mc.p95;
  const median = typeof mean === "number" ? mean : mc.median;
  const lossPct = mc.prob_loss * 100;
  const range = best - worst;
  const markerPos = range > 0 ? Math.min(100, Math.max(0, ((median - worst) / range) * 100)) : 50;
  const medianColor = median >= 0 ? c.profit : c.loss;
  // The middle-50% band (interquartile range) — the "usual" outcome most runs
  // clustered in, a calmer read than the 5%-tail extremes. Shown only when the
  // API supplies both quartiles (never recomputed, never faked).
  const hasIqr =
    typeof mc.p25 === "number" &&
    typeof mc.p75 === "number" &&
    !Number.isNaN(mc.p25) &&
    !Number.isNaN(mc.p75);

  return (
    <div style={{ display: "flex", flex: 1, flexDirection: "column", justifyContent: "space-between", gap: 18 }}>
      {/* the middle outcome, headline */}
      <div style={{ display: "flex", alignItems: "baseline", gap: 10, flexWrap: "wrap" }}>
        <span
          style={{
            fontFamily: FONT,
            fontVariantNumeric: "tabular-nums",
            fontSize: 34,
            fontWeight: 600,
            letterSpacing: "-0.02em",
            lineHeight: 1,
            color: medianColor,
          }}
        >
          {pct(median, 0)}
        </span>
        <span style={{ fontFamily: FONT, fontSize: 13, color: "var(--text-tertiary)" }}>
          average outcome
        </span>
      </div>

      {/* the middle-50% band — where the bulk of runs actually landed */}
      {hasIqr && (
        <div
          style={{
            display: "flex",
            alignItems: "baseline",
            justifyContent: "space-between",
            gap: 12,
            padding: "12px 0",
            borderTop: "1px solid var(--glass-border)",
            borderBottom: "1px solid var(--glass-border)",
          }}
        >
          <span style={{ fontFamily: FONT, fontSize: 13, color: "var(--text-tertiary)", lineHeight: 1.4 }}>
            Half of all runs landed between
          </span>
          <span
            style={{
              fontFamily: FONT,
              fontVariantNumeric: "tabular-nums",
              fontSize: 15,
              fontWeight: 600,
              color: "var(--text-secondary)",
              whiteSpace: "nowrap",
            }}
          >
            {pct(mc.p25!, 0)} &nbsp;to&nbsp; {pct(mc.p75!, 0)}
          </span>
        </div>
      )}

      {/* worst → best track, with the middle outcome marked */}
      <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
        <div
          style={{
            position: "relative",
            height: 6,
            borderRadius: "var(--radius-pill)",
            background: "color-mix(in srgb, var(--pivot-blue) 22%, var(--glass-border))",
          }}
        >
          <span
            aria-hidden
            style={{
              position: "absolute",
              top: -3,
              left: `${markerPos}%`,
              transform: "translateX(-50%)",
              width: 3,
              height: 12,
              borderRadius: 2,
              background: "var(--pivot-blue)",
            }}
          />
        </div>
        <div
          style={{
            display: "flex",
            justifyContent: "space-between",
            gap: 8,
            fontFamily: FONT,
            fontVariantNumeric: "tabular-nums",
            fontSize: 13,
          }}
        >
          <span style={{ color: c.loss }}>worst 5% · {pct(worst, 0)}</span>
          <span style={{ color: "var(--text-tertiary)" }}>
            chance of a loss · {lossPct < 0.1 ? "under 0.1" : lossPct.toFixed(0)}%
          </span>
          <span style={{ color: c.profit }}>best 5% · {pct(best, 0)}</span>
        </div>
      </div>
    </div>
  );
}

/** Reward:risk as a single scaled bar with a plain verdict beneath, plus the
 *  two real numbers the ratio is built from (typical gain ÷ worst drop). */
function RewardRisk({
  ratio,
  reward,
  worstDrop,
  c,
}: {
  ratio: number;
  reward?: number | null;
  worstDrop?: number | null;
  c: Record<string, string>;
}): React.ReactElement {
  const pos = ratio >= 0;
  const color = pos ? c.profit : c.loss;
  // A reward:risk of 1.0 fills the bar; above that it caps. Below, it scales
  // linearly (0.3× ≈ a third of the way), matching the "steady, not
  // spectacular" read.
  const widthPct = Math.max(3, Math.min(100, Math.abs(ratio) * 100));
  // The ratio is avg-return ÷ worst-drawdown; when the API supplies both, show
  // the actual numerator and denominator so the "×" isn't a black box.
  const hasParts =
    typeof reward === "number" &&
    !Number.isNaN(reward) &&
    typeof worstDrop === "number" &&
    !Number.isNaN(worstDrop);

  return (
    <div style={{ display: "flex", flex: 1, flexDirection: "column", justifyContent: "space-between", gap: 16 }}>
      <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 14 }}>
          <div
            style={{
              flex: 1,
              minWidth: 0,
              height: 12,
              borderRadius: "var(--radius-pill)",
              background: "var(--glass-border)",
              overflow: "hidden",
            }}
          >
            <div
              style={{
                width: `${widthPct}%`,
                height: "100%",
                background: color,
                borderRadius: "var(--radius-pill)",
              }}
            />
          </div>
          <Num size="lg" weight={600} color={color} style={{ flexShrink: 0 }}>
            {ratio.toFixed(1)}×
          </Num>
        </div>
        <span style={{ fontFamily: FONT, fontSize: 13, lineHeight: 1.5, color: "var(--text-tertiary)" }}>
          {rewardVerdict(ratio)}
        </span>
      </div>

      {/* What the ratio is made of — the two real numbers behind it. */}
      {hasParts && (
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "1fr 1fr",
            gap: 12,
            paddingTop: 16,
            borderTop: "1px solid var(--glass-border)",
          }}
        >
          <RewardPart label="Typical gain" value={pct(reward, 1)} color={reward! >= 0 ? c.profit : c.loss} />
          <RewardPart label="Worst drop" value={pct(worstDrop, 1)} color={c.loss} />
        </div>
      )}
    </div>
  );
}

/** One labelled number in the reward:risk decomposition. */
function RewardPart({
  label,
  value,
  color,
}: {
  label: string;
  value: string;
  color?: string;
}): React.ReactElement {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
      <span style={{ fontFamily: FONT, fontSize: 13, color: "var(--text-tertiary)" }}>{label}</span>
      <span
        style={{
          fontFamily: FONT,
          fontVariantNumeric: "tabular-nums",
          fontSize: 20,
          fontWeight: 600,
          color,
        }}
      >
        {value}
      </span>
    </div>
  );
}

/** Option legs + net greeks. */
function OptionStructure({
  om,
}: {
  om: NonNullable<ExpressionDetail["option_model"]>;
}): React.ReactElement {
  const g = om.net_greeks;
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
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

/** Maps asset_class → a short, muted badge label. */
function assetBadgeLabel(assetClass: string | null | undefined): string | null {
  switch (assetClass) {
    case "in_equity": return "IN";
    case "in_etf": return "ETF";
    case "us_equity": return "US";
    case "us_etf": return "US ETF";
    case "crypto": return "Crypto";
    default: return null;
  }
}

/** Inline badge chip — muted, no fill, border only. */
function AssetBadge({ label }: { label: string }): React.ReactElement {
  return (
    <span
      style={{
        fontFamily: FONT,
        fontSize: 10,
        fontWeight: 600,
        letterSpacing: "0.03em",
        color: "var(--text-tertiary)",
        border: "1px solid var(--glass-border)",
        borderRadius: 4,
        padding: "1px 5px",
        flexShrink: 0,
        lineHeight: 1.6,
        whiteSpace: "nowrap",
      }}
    >
      {label}
    </span>
  );
}

/** Short-leg row rendered below the long holdings, separated by a dashed divider. */
function ShortHoldingRow({
  holding,
  metaMap,
}: {
  holding: Holding;
  metaMap: Record<string, SecurityMeta>;
}): React.ReactElement {
  const meta = metaMap[holding.symbol];
  const name = meta?.name ?? holding.name;
  const logoUrl = meta?.logo_url ?? holding.logo_url ?? null;
  const badgeLabel = assetBadgeLabel(meta?.asset_class ?? holding.asset_class ?? null);
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 10, borderTop: "1px dashed var(--glass-border)", paddingTop: 10 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 7, width: 148, flexShrink: 0, minWidth: 0 }}>
        <CompanyLogo logoUrl={logoUrl} name={name} symbol={holding.symbol} size={20} />
        <span
          style={{
            fontFamily: FONT,
            fontSize: 13,
            color: "var(--text-secondary)",
            overflow: "hidden",
            textOverflow: "ellipsis",
            whiteSpace: "nowrap",
            flex: 1,
            minWidth: 0,
          }}
          title={name}
        >
          {name}
        </span>
        {badgeLabel && <AssetBadge label={badgeLabel} />}
      </div>
      <span style={{ flex: 1, fontFamily: FONT, fontSize: 13, color: "var(--text-tertiary)" }}>
        short leg — hedges out the market
      </span>
    </div>
  );
}

/** Weighted holdings as bars (bar length = weight), with per-name avg return. */
function HoldingsWeights({
  holdings,
  shortHolding,
  c,
  metaMap,
}: {
  holdings: NonNullable<ExpressionDetail["holdings"]>;
  shortHolding?: ExpressionDetail["holdings"][number];
  c: Record<string, string>;
  metaMap: Record<string, SecurityMeta>;
}): React.ReactElement {
  const maxW = Math.max(...holdings.map((h) => h.weight_pct ?? 0), 1);

  function resolvedName(h: Holding): string {
    return metaMap[h.symbol]?.name ?? h.name;
  }
  function resolvedLogo(h: Holding): string | null {
    return metaMap[h.symbol]?.logo_url ?? h.logo_url ?? null;
  }
  function resolvedAssetClass(h: Holding): string | null {
    return metaMap[h.symbol]?.asset_class ?? h.asset_class ?? null;
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
      {holdings.map((h, i) => {
        const w = h.weight_pct ?? 0;
        const ret = h.return_pct;
        const name = resolvedName(h);
        const logoUrl = resolvedLogo(h);
        const badgeLabel = assetBadgeLabel(resolvedAssetClass(h));
        return (
          <div key={i} style={{ display: "flex", alignItems: "center", gap: 10 }}>
            {/* Logo + name + badge stacked in a fixed-width name cell */}
            <div style={{ display: "flex", alignItems: "center", gap: 7, width: 148, flexShrink: 0, minWidth: 0 }}>
              <CompanyLogo
                logoUrl={logoUrl}
                name={name}
                symbol={h.symbol}
                size={20}
              />
              <span
                style={{
                  fontFamily: FONT,
                  fontSize: 13,
                  color: "var(--text-primary)",
                  overflow: "hidden",
                  textOverflow: "ellipsis",
                  whiteSpace: "nowrap",
                  flex: 1,
                  minWidth: 0,
                }}
                title={name}
              >
                {name}
              </span>
              {badgeLabel && <AssetBadge label={badgeLabel} />}
            </div>
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
        <ShortHoldingRow holding={shortHolding} metaMap={metaMap} />
      )}
    </div>
  );
}

export default StrategyDeepDive;
